#!/usr/bin/env python3
"""Reads a loopback capture and prints what was said to the game inside it.

Two conversations matter and they are nothing alike. W3Champions drives the menus over the
same web UI socket our page uses, from its own process, so only a packet capture sees it. Its
in-game half is the game protocol itself: their client (Flo) emulates a LAN host, which is how
a "[W3C]" line appears in a running match - a ChatFromHost packet, not anything on the socket.

This reassembles every TCP stream and reads both: WebSocket frames where a web UI socket was
opened (client frames are masked, the game's are not), and W3GS packets everywhere else.

    python3 diagnostics/tcp-decode.py captures/w3c-tcp.pcap [--all]

Without --all, only the first 200 bytes of each message are shown.
"""
import collections
import struct
import sys

LINK_TYPES = {0: 4, 1: 14, 108: 4, 113: 16}       # link type -> header length

# The game's own protocol, named as Flo's crates/w3gs names them
W3GS_MAGIC = 0xF7
PACKETS = {
    0x01: 'PingFromHost', 0x04: 'SlotInfoJoin', 0x05: 'RejectJoin', 0x06: 'PlayerInfo',
    0x07: 'PlayerLeft', 0x08: 'PlayerLoaded', 0x09: 'SlotInfo', 0x0A: 'CountDownStart',
    0x0B: 'CountDownEnd', 0x0C: 'IncomingAction', 0x0D: 'Desync', 0x0F: 'ChatFromHost',
    0x10: 'StartLag', 0x11: 'StopLag', 0x14: 'GameOver', 0x1B: 'LeaveAck',
    0x1C: 'PlayerKicked', 0x1E: 'ReqJoin', 0x21: 'LeaveReq', 0x23: 'GameLoadedSelf',
    0x26: 'OutgoingAction', 0x27: 'OutgoingKeepAlive', 0x28: 'ChatToHost', 0x29: 'DropReq',
    0x2F: 'SearchGame', 0x30: 'GameInfo', 0x31: 'CreateGame', 0x32: 'RefreshGame',
    0x33: 'DecreateGame', 0x34: 'ChatFromOthers', 0x35: 'PingFromOthers', 0x36: 'PongToOthers',
    0x37: 'ClientInfo', 0x3B: 'PeerSet', 0x3D: 'MapCheck', 0x3F: 'StartDownload',
    0x42: 'MapSize', 0x43: 'MapPart', 0x44: 'MapPartOK', 0x45: 'MapPartError',
    0x46: 'PongToHost', 0x48: 'IncomingAction2', 0x59: 'ProtoBuf',
}
# The chatter of a running game, which would bury everything else
DULL = {'PingFromHost', 'PongToHost', 'OutgoingKeepAlive', 'IncomingAction', 'IncomingAction2',
        'OutgoingAction', 'PingFromOthers', 'PongToOthers'}


def w3gs(data):
    """The game protocol packets in one direction of a stream, as (name, payload)."""
    position = 0
    while position + 4 <= len(data):
        if data[position] != W3GS_MAGIC:
            position += 1
            continue
        kind = data[position + 1]
        length, = struct.unpack('<H', data[position + 2:position + 4])
        if length < 4 or position + length > len(data):
            position += 1
            continue
        yield PACKETS.get(kind, 'unknown 0x%02X' % kind), data[position + 4:position + length]
        position += length


def readable(payload):
    """The printable text in a packet, which for chat is the message itself."""
    text = ''.join(chr(b) if 32 <= b < 127 else ' ' for b in payload)
    return ' '.join(part for part in text.split('  ') if len(part.strip()) > 2).strip()


def packets(path):
    """Every TCP payload in the capture, as (stream key, sequence, bytes)."""
    with open(path, 'rb') as capture:
        header = capture.read(24)
        if len(header) < 24:
            return
        magic, = struct.unpack('<I', header[:4])
        endian = '<' if magic in (0xa1b2c3d4, 0xa1b23c4d) else '>'
        link, = struct.unpack(endian + 'I', header[20:24])
        offset = LINK_TYPES.get(link, 4)
        while True:
            record = capture.read(16)
            if len(record) < 16:
                return
            _, _, saved, _ = struct.unpack(endian + 'IIII', record)
            data = capture.read(saved)
            if len(data) < saved:
                return
            frame = data[offset:]
            if len(frame) < 20 or (frame[0] >> 4) != 4:
                continue
            header_length = (frame[0] & 0x0f) * 4
            if frame[9] != 6:                      # not TCP
                continue
            source = '.'.join(str(b) for b in frame[12:16])
            destination = '.'.join(str(b) for b in frame[16:20])
            tcp = frame[header_length:]
            if len(tcp) < 20:
                continue
            source_port, destination_port, sequence = struct.unpack('>HHI', tcp[:8])
            data_offset = (tcp[12] >> 4) * 4
            payload = tcp[data_offset:]
            if payload:
                yield ((source, source_port, destination, destination_port), sequence, payload)


def streams(path):
    """Each stream's bytes in sequence order."""
    chunks = collections.defaultdict(dict)
    for key, sequence, payload in packets(path):
        chunks[key].setdefault(sequence, payload)
    return {key: b''.join(parts[s] for s in sorted(parts)) for key, parts in chunks.items()}


def frames(data):
    """The WebSocket messages in one direction of a stream."""
    # Skip the HTTP handshake if this end sent one
    start = data.find(b'\r\n\r\n')
    position = start + 4 if start != -1 else 0
    while position + 2 <= len(data):
        first, second = data[position], data[position + 1]
        opcode = first & 0x0f
        masked = second & 0x80
        length = second & 0x7f
        position += 2
        if length == 126:
            if position + 2 > len(data):
                return
            length, = struct.unpack('>H', data[position:position + 2])
            position += 2
        elif length == 127:
            if position + 8 > len(data):
                return
            length, = struct.unpack('>Q', data[position:position + 8])
            position += 8
        mask = b''
        if masked:
            mask = data[position:position + 4]
            position += 4
        body = data[position:position + length]
        position += length
        if len(body) < length:
            return
        if mask:
            body = bytes(b ^ mask[i % 4] for i, b in enumerate(body))
        if opcode in (1, 2) and body:
            yield body


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    width = None if '--all' in sys.argv else 200

    everything = streams(path)
    sockets = {key: data for key, data in everything.items() if b'/webui-socket/' in data}
    print('%d TCP streams in the capture, %d opened a web UI socket' % (len(everything), len(sockets)))
    if not sockets:
        print('None of them opened a web UI socket; only the game protocol below, if any.')

    for key, data in sorted(sockets.items(), key=lambda item: item[0][1]):
        source, source_port, destination, destination_port = key
        print('\n=== %s:%d -> %s:%d (client side) ===' % key)
        request = data[:data.find(b'\r\n\r\n')].decode('utf-8', 'replace')
        print('   ' + request.splitlines()[0] if request else '')
        for message in frames(data):
            text = message.decode('utf-8', 'replace')
            print('  -> ' + (text if width is None else text[:width]))
        back = everything.get((destination, destination_port, source, source_port))
        if back:
            print('--- and what the game answered ---')
            for message in frames(back):
                text = message.decode('utf-8', 'replace')
                print('  <- ' + (text if width is None else text[:width]))

    # The other conversation: the game protocol, where a running match is actually driven
    print('\n\n=== the game protocol on loopback ===')
    seen = False
    for key, data in sorted(everything.items(), key=lambda item: item[0][1]):
        if key in sockets:
            continue
        packets = [(name, payload) for name, payload in w3gs(data)]
        interesting = [(name, payload) for name, payload in packets if name not in DULL]
        if not packets:
            continue
        seen = True
        counts = collections.Counter(name for name, _ in packets)
        print('\n%s:%d -> %s:%d   %d packets: %s' % (
            key[0], key[1], key[2], key[3], len(packets),
            ', '.join('%s x%d' % (name, n) for name, n in counts.most_common(8))))
        for name, payload in interesting[:40]:
            text = readable(payload)
            print('   %-18s %s' % (name, text if width is None else text[:width]))
    if not seen:
        print('No game protocol traffic on loopback: the match was not hosted locally.')


main()
