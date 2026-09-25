#!/usr/bin/env python3
"""Listens where Warcraft III looks for LAN games and prints what arrives.

The game finds LAN games the classic way, over UDP: it asks, and a host answers with a
GameInfo packet. On this build the game's own port is 16000 (W3Champions takes 16000 when it
runs first, and the game moves to 16001). Holding the port shows the game's questions.

    python3 diagnostics/udp-listen.py [port] [seconds]
"""
import socket
import struct
import sys
import time

NAMES = {0x2F: 'SearchGame', 0x30: 'GameInfo', 0x31: 'CreateGame', 0x32: 'RefreshGame',
         0x33: 'DecreateGame'}

port = int(sys.argv[1]) if len(sys.argv) > 1 else 16000
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 120
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.bind(('', port))
sock.settimeout(1)
print('listening on UDP %d for %ds' % (port, seconds), flush=True)
end = time.time() + seconds
while time.time() < end:
    try:
        data, sender = sock.recvfrom(65535)
    except socket.timeout:
        continue
    kind = NAMES.get(data[1], '0x%02X' % data[1]) if len(data) > 1 and data[0] == 0xF7 else 'not W3GS'
    print('%s from %s:%d  %d bytes  %s' % (kind, sender[0], sender[1], len(data), data.hex()), flush=True)
