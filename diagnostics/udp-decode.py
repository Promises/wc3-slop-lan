#!/usr/bin/env python3
"""Prints the LAN discovery packets in a UDP capture: the game's SearchGame and whatever
answers it (GameInfo, CreateGame, RefreshGame), byte for byte and field by field.

    python3 diagnostics/udp-decode.py captures/lan-udp.pcap
"""
import struct
import sys

NAMES = {0x2F: 'SearchGame', 0x30: 'GameInfo', 0x31: 'CreateGame', 0x32: 'RefreshGame',
         0x33: 'DecreateGame'}


def udp_packets(path):
    data = open(path, 'rb').read()
    if data[:4] == b'\x0a\x0d\x0d\x0a':
        sys.exit('pcapng: capture on one interface (-i lo0) so tcpdump writes plain pcap')
    endian = '<' if struct.unpack('<I', data[:4])[0] in (0xa1b2c3d4, 0xa1b23c4d) else '>'
    link, = struct.unpack(endian + 'I', data[20:24])
    position = 24
    while position + 16 <= len(data):
        seconds, micros, saved, _ = struct.unpack(endian + 'IIII', data[position:position + 16])
        frame = data[position + 16:position + 16 + saved]
        position += 16 + saved
        frame = frame[{0: 4, 1: 14, 108: 4}.get(link, 4):]
        if len(frame) < 28 or frame[0] >> 4 != 4 or frame[9] != 17:
            continue
        start = (frame[0] & 0x0f) * 4
        source = '.'.join(map(str, frame[12:16]))
        target = '.'.join(map(str, frame[16:20]))
        sport, dport = struct.unpack('>HH', frame[start:start + 4])
        yield seconds + micros / 1e6, '%s:%d' % (source, sport), '%s:%d' % (target, dport), frame[start + 8:]


def cstring(data, position):
    end = data.index(b'\x00', position)
    return data[position:end], end + 1


def describe(payload):
    kind = payload[1]
    body = payload[4:]
    if kind == 0x2F and len(body) >= 12:
        product, version, counter = struct.unpack('<4sII', body[:12])
        return 'product %r version %d (0x%x) counter %d' % (product, version, version, counter)
    if kind in (0x30, 0x31, 0x32) and len(body) >= 12:
        product, version, counter = struct.unpack('<4sII', body[:12])
        text = 'product %r version %d (0x%x) host counter %d' % (product, version, version, counter)
        if kind == 0x30:
            try:
                key, = struct.unpack('<I', body[12:16])
                name, position = cstring(body, 16)
                password, position = cstring(body, position)
                stat, position = cstring(body, position)
                tail = body[position:]
                fields = struct.unpack('<IIIII', tail[:20]) if len(tail) >= 22 else ()
                port = struct.unpack('<H', tail[20:22])[0] if len(tail) >= 22 else None
                text += '\n      entry key %d name %r password %r' % (key, name, password)
                text += '\n      stat string %d bytes' % len(stat)
                text += '\n      slots/type/unknown/open/uptime %s port %s' % (fields, port)
            except ValueError:
                pass
        return text
    return ''


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    start = None
    for moment, source, target, payload in udp_packets(sys.argv[1]):
        start = start or moment
        if len(payload) < 4 or payload[0] != 0xF7:
            print('%7.3f %s -> %s  not W3GS, %d bytes' % (moment - start, source, target, len(payload)))
            continue
        name = NAMES.get(payload[1], '0x%02X' % payload[1])
        print('%7.3f %s -> %s  %s (%d bytes)' % (moment - start, source, target, name, len(payload)))
        detail = describe(payload)
        if detail:
            print('      ' + detail)
        print('      ' + payload.hex())


main()
