#!/usr/bin/env python3
"""Drives the running games from one place.

Every instance's web UI page (harness/webui/index.html) checks in here and then asks for work, so
games can be started whenever and told what to do afterwards: host a lobby, join one, start it,
or send any message the menus themselves would send. Each instance also reports where it is and
posts its socket traffic, which is written to the log file.

    python3 harness/webui/server.py [logfile] [port]

  For the pages:
    GET  /hello?id=<port>   register, and hear which instance number this is
    GET  /poll?id=<port>    the commands waiting for this instance
    POST /state?id=<port>   where this instance is now, as JSON
    POST /                  one line of socket traffic, appended to the log and kept
                            in memory for /log (an "= value" line is an eval answer)

  For us:
    GET  /instances                     what is connected and where it is
    GET  /log?id=<id|number>&n=<lines>  the last messages that instance saw, newest last
    POST /command                       {"to": 1|2|"all"|"<id>", "verb": ..., ...}
                                        verbs: host, join, start, leave, raw, eval,
                                        reload, trace
    POST /reset                         forget the instances and any queued work

  A host command's game name and password are remembered, so a later join can leave them out
  and still find the right lobby.
"""
import json
import re
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

LOG = sys.argv[1] if len(sys.argv) > 1 else 'webui-trace.log'
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8777

lock = threading.Lock()
instances = {}          # id -> {"number": 1, "state": {...}, "seen": epoch, "queue": [...]}
hosted = {}             # the game name and password of the last host command


def register(who):
    with lock:
        entry = instances.get(who)
        if entry is None:
            entry = {'number': len(instances) + 1, 'state': {}, 'queue': [], 'log': deque(maxlen=400)}
            instances[who] = entry
            print(f'instance {entry["number"]} is {who}')
        entry['seen'] = time.time()
        return entry


def targets(to):
    if to in (None, 'all'):
        return list(instances.values())
    for who, entry in instances.items():
        if who == str(to) or entry['number'] == to or str(entry['number']) == str(to):
            return [entry]
    return []


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def reply(self, code=204, body=None):
        self.send_response(code)
        self.send_header('Access-Control-Allow-Origin', '*')
        payload = json.dumps(body).encode() if body is not None else b''
        self.send_header('Content-Length', str(len(payload)))
        if body is not None:
            self.send_header('Content-Type', 'application/json')
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_GET(self):
        url = urlparse(self.path)
        who = (parse_qs(url.query).get('id') or ['?'])[0]
        if url.path == '/hello':
            entry = register(who)
            self.reply(200, {'number': entry['number']})
        elif url.path == '/poll':
            entry = register(who)
            with lock:
                work, entry['queue'] = entry['queue'], []
            self.reply(200, work)
        elif url.path == '/log':
            wanted = (parse_qs(url.query).get('n') or ['30'])[0]
            entries = targets(who) or []
            with lock:
                lines = [line for entry in entries for line in list(entry['log'])[-int(wanted):]]
            self.reply(200, lines)
        elif url.path == '/instances':
            with lock:
                self.reply(200, {who: {'number': e['number'], 'state': e['state'],
                                       'waiting': len(e['queue']),
                                       'seen': round(time.time() - e['seen'], 1)}
                                 for who, e in instances.items()})
        else:
            self.reply(404)

    def do_POST(self):
        url = urlparse(self.path)
        who = (parse_qs(url.query).get('id') or ['?'])[0]
        raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        if url.path == '/state':
            entry = register(who)
            try:
                entry['state'] = json.loads(raw or b'{}')
            except ValueError:
                pass
            self.reply()
        elif url.path == '/command':
            try:
                command = json.loads(raw or b'{}')
            except ValueError:
                return self.reply(400, {'error': 'not JSON'})
            chosen = targets(command.pop('to', 'all'))
            with lock:
                # What was hosted last, so a join can be told nothing but who should join
                if command.get('verb') == 'host' and command.get('gameName'):
                    hosted.update({'gameName': command['gameName'],
                                   'password': command.get('password', '')})
                elif command.get('verb') == 'join' and not command.get('gameName'):
                    command.update(hosted)
                for entry in chosen:
                    entry['queue'].append(command)
            print(f'{command.get("verb")} -> {[e["number"] for e in chosen] or "nobody"}')
            self.reply(200, {'sent': [e['number'] for e in chosen]})
        elif url.path == '/reset':
            with lock:
                instances.clear()
            print('instances cleared')
            self.reply()
        else:
            line = raw.decode('utf-8', 'replace').rstrip()
            with open(LOG, 'a') as log:
                log.write(line + '\n')
            # also kept in memory, so a message can be sent and its answer read back at once
            tag = re.match(r'\[(\d+)\]\s*(.*)', line)
            if tag:
                with lock:
                    entry = instances.get(tag.group(1))
                    if entry is not None:
                        entry['log'].append(tag.group(2))
            self.reply()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *args):
        pass


def main():
    print(f'writing {LOG}, listening on 127.0.0.1:{PORT}')
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
