#!/usr/bin/env python3
"""Watches for Warcraft III LAN games and prints each one's game record as it appears.

Games registered on this machine only (the way W3Champions registers its own) never go out
on the network, so a packet capture and `dns-sd -B` both miss them; this browses through the
system's Bonjour library on every interface including the local-only one, and asks for each
game's record (DNS type 66, a protobuf GameInfo) the moment it appears.

    python3 diagnostics/bonjour-watch.py [seconds] [--subtype _w3xp27d8]

Records are also saved as captures/lan-records/<name>.bin for comparison.
"""
import base64
import ctypes
import os
import select
import sys
import time

lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
BrowseReply = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32,
                               ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p)
QueryReply = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32,
                              ctypes.c_char_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint16,
                              ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p)
lib.DNSServiceBrowse.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.c_uint32,
                                 ctypes.c_char_p, ctypes.c_char_p, BrowseReply, ctypes.c_void_p]
lib.DNSServiceQueryRecord.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_char_p, ctypes.c_uint16, ctypes.c_uint16, QueryReply, ctypes.c_void_p]
lib.DNSServiceRefSockFD.argtypes = [ctypes.c_void_p]
lib.DNSServiceProcessResult.argtypes = [ctypes.c_void_p]

ADD = 0x2
GAME_RECORD = 66
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'captures', 'lan-records')

refs = []          # every open DNSService operation, polled together
seen = set()


def varints(data, position):
    value = shift = 0
    while True:
        byte = data[position]
        position += 1
        value |= (byte & 0x7f) << shift
        shift += 7
        if not byte & 0x80:
            return value, position


def fields(data):
    position = 0
    while position < len(data):
        key, position = varints(data, position)
        if key & 7 == 0:
            value, position = varints(data, position)
        elif key & 7 == 2:
            length, position = varints(data, position)
            value = data[position:position + length]
            position += length
        else:
            return
        yield key >> 3, value


def show(name, rdata):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name.split('._')[0] + '.bin'), 'wb') as saved:
        saved.write(rdata)
    print('\n=== %s (%d bytes)' % (name, len(rdata)))
    for field, value in fields(rdata):
        if field == 3:
            entry = dict(fields(value))
            key = entry.get(1, b'').decode()
            text = entry.get(2, b'').decode(errors='replace')
            if key == 'game_data':
                blob = base64.b64decode(text)
                print('  %-17s %d bytes %s' % (key, len(blob), blob.hex()))
            else:
                print('  %-17s %s' % (key, text))
        else:
            print('  field %-11d %s' % (field, value if isinstance(value, int) else value.decode(errors='replace')))
    sys.stdout.flush()


@QueryReply
def on_record(ref, flags, interface, error, fullname, rrtype, rrclass, length, rdata, ttl, context):
    if error == 0 and flags & ADD and length:
        name = fullname.decode()
        data = ctypes.string_at(rdata, length)
        if (name, data) not in seen:
            seen.add((name, data))
            show(name, data)


@BrowseReply
def on_service(ref, flags, interface, error, name, regtype, domain, context):
    if error or not flags & ADD:
        return
    full = '%s.%s%s' % (name.decode(), regtype.decode(), domain.decode())
    print('found %s on interface %d' % (full, interface))
    query = ctypes.c_void_p()
    if lib.DNSServiceQueryRecord(ctypes.byref(query), 0, 0, full.encode(), GAME_RECORD, 1, on_record, None) == 0:
        refs.append(query)


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else 120
    subtype = sys.argv[sys.argv.index('--subtype') + 1] if '--subtype' in sys.argv else '_w3xp27d8'
    browse = ctypes.c_void_p()
    # Interface 0 is every interface, and includes services registered on this machine only
    error = lib.DNSServiceBrowse(ctypes.byref(browse), 0, 0, ('_blizzard._udp,' + subtype).encode(),
                                 b'local.', on_service, None)
    if error:
        sys.exit('browse failed: %d' % error)
    refs.append(browse)
    print('watching _blizzard._udp,%s for %ds' % (subtype, seconds))
    end = time.time() + seconds
    while time.time() < end:
        by_fd = {lib.DNSServiceRefSockFD(ref): ref for ref in refs}
        ready, _, _ = select.select(list(by_fd), [], [], 0.5)
        for fd in ready:
            lib.DNSServiceProcessResult(by_fd[fd])


main()
