#!/usr/bin/env python3
"""Reads a Bonjour capture and prints every Warcraft III game advertisement in it.

A LAN game is a `_blizzard._udp` service registered under a version subtype, with the game
itself in an extra DNS record of type 66 holding a protobuf GameInfo. This prints, for each
advertised game, the subtype pointers seen, the SRV port, and the GameInfo entries - enough to
compare with what a host of our own would publish.

    python3 diagnostics/mdns-decode.py captures/mdns.pcap
"""
import base64
import collections
import struct
import sys

TYPES = {1: 'A', 12: 'PTR', 16: 'TXT', 28: 'AAAA', 33: 'SRV', 47: 'NSEC', 66: 'GAMEINFO'}


def frames_from_pcapng(data):
    """(link type, frame) for every packet of a pcapng file, which macOS writes for -i any."""
    position, links, endian = 0, [], '<'
    while position + 12 <= len(data):
        kind, = struct.unpack(endian + 'I', data[position:position + 4])
        if kind == 0x0A0D0D0A:
            endian = '<' if data[position + 8:position + 12] == b'\x4d\x3c\x2b\x1a' else '>'
            links = []
        length, = struct.unpack(endian + 'I', data[position + 4:position + 8])
        if length < 12:
            return
        body = data[position + 8:position + length - 4]
        if kind == 1:
            links.append(struct.unpack(endian + 'H', body[:2])[0])
        elif kind == 6:
            interface, _, _, captured, _ = struct.unpack(endian + 'IIIII', body[:20])
            link = links[interface] if interface < len(links) else 1
            yield link, body[20:20 + captured]
        position += length


def records_from_pcap(path):
    raw = open(path, 'rb').read()
    if raw[:4] == b'\x0a\x0d\x0d\x0a':
        for link, frame in frames_from_pcapng(raw):
            payload = udp_payload(link, frame)
            if payload is not None:
                yield payload
        return
    with open(path, 'rb') as capture:
        header = capture.read(24)
        magic, = struct.unpack('<I', header[:4])
        endian = '<' if magic in (0xa1b2c3d4, 0xa1b23c4d) else '>'
        link, = struct.unpack(endian + 'I', header[20:24])
        while True:
            record = capture.read(16)
            if len(record) < 16:
                return
            _, _, saved, _ = struct.unpack(endian + 'IIII', record)
            payload = udp_payload(link, capture.read(saved))
            if payload is not None:
                yield payload


def udp_payload(link, frame):
    """The mDNS payload of one captured frame, or None."""
    if link == 258:                                       # macOS pktap: its own header first
        length, = struct.unpack('<I', frame[:4])
        link, = struct.unpack('<I', frame[8:12])
        frame = frame[length:]
    frame = frame[{0: 4, 1: 14, 108: 4}.get(link, 4):]
    if not frame:
        return None
    version = frame[0] >> 4
    if version == 4:
        start = (frame[0] & 0x0f) * 4
        if frame[9] != 17:
            return None
    elif version == 6:
        start = 40
        if frame[6] != 17:
            return None
    else:
        return None
    udp = frame[start:]
    if len(udp) < 8 or 5353 not in struct.unpack('>HH', udp[:4]):
        return None
    return udp[8:]


def read_name(message, position):
    labels = []
    jumped = False
    end = position
    for _ in range(64):
        length = message[position]
        if length == 0:
            position += 1
            break
        if length & 0xC0 == 0xC0:
            pointer = struct.unpack('>H', message[position:position + 2])[0] & 0x3FFF
            if not jumped:
                end = position + 2
            jumped = True
            position = pointer
            continue
        labels.append(message[position + 1:position + 1 + length].decode('utf-8', 'replace'))
        position += 1 + length
    return '.'.join(labels), (end if jumped else position)


def parse(message):
    """Answers and additional records of one DNS message, as (name, type, rdata start, data)."""
    if len(message) < 12:
        return []
    _, flags, qd, an, ns, ar = struct.unpack('>HHHHHH', message[:12])
    position = 12
    for _ in range(qd):
        _, position = read_name(message, position)
        position += 4
    out = []
    for _ in range(an + ns + ar):
        name, position = read_name(message, position)
        kind, _, _, length = struct.unpack('>HHIH', message[position:position + 10])
        position += 10
        out.append((name, kind, position, message[position:position + length]))
        position += length
    return out, message


def protobuf_fields(data):
    """(field, value) pairs of a protobuf message; nested messages stay bytes."""
    position = 0
    while position < len(data):
        key = 0
        shift = 0
        while True:
            byte = data[position]
            position += 1
            key |= (byte & 0x7f) << shift
            shift += 7
            if not byte & 0x80:
                break
        field, wire = key >> 3, key & 7
        if wire == 0:
            value = 0
            shift = 0
            while True:
                byte = data[position]
                position += 1
                value |= (byte & 0x7f) << shift
                shift += 7
                if not byte & 0x80:
                    break
            yield field, value
        elif wire == 2:
            length = 0
            shift = 0
            while True:
                byte = data[position]
                position += 1
                length |= (byte & 0x7f) << shift
                shift += 7
                if not byte & 0x80:
                    break
            yield field, data[position:position + length]
            position += length
        else:
            return


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    subtypes = collections.defaultdict(set)
    games = {}
    ports = {}
    for message in records_from_pcap(sys.argv[1]):
        parsed = parse(message)
        if not parsed:
            continue
        records, raw = parsed
        for name, kind, start, data in records:
            if '_blizzard' not in name:
                continue
            if kind == 12:
                target, _ = read_name(raw, start)
                if '._sub.' in name:
                    subtypes[target].add(name.split('._sub.')[0])
            elif kind == 33:
                ports[name] = struct.unpack('>H', data[4:6])[0]
            elif kind == 66:
                games[name] = data

    if not games:
        print('No game records (type 66) in the capture.')
    for name, data in games.items():
        print('=== %s' % name)
        print('  subtypes : %s' % (', '.join(sorted(subtypes.get(name, []))) or 'none seen'))
        print('  srv port : %s' % ports.get(name, '?'))
        for field, value in protobuf_fields(data):
            if field == 3:
                entry = dict(protobuf_fields(value))
                key = entry.get(1, b'').decode('utf-8', 'replace')
                text = entry.get(2, b'').decode('utf-8', 'replace')
                if key == 'game_data':
                    blob = base64.b64decode(text)
                    print('  %-17s %d bytes: %s' % (key, len(blob), blob.hex()))
                else:
                    print('  %-17s %s' % (key, text))
            else:
                print('  field %-11d %s' % (field, value if isinstance(value, int) else value.decode('utf-8', 'replace')))
    for target, names in subtypes.items():
        if target not in games:
            print('subtype pointer without a record: %s -> %s' % (', '.join(sorted(names)), target))


main()
