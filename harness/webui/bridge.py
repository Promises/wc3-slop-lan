#!/usr/bin/env python3
"""The menu page's part, for a game whose menus cannot load our page.

The Windows build of Warcraft III (on Windows, or under Wine) serves its menus from its own
packed data, so harness/webui/index.html cannot be put in their place. The menus still talk to
their page over a websocket on the game's local port, and anything local can talk on it too. So
this plays the page's part from outside: it checks in with the web UI server under the menus'
port, reports the screen they are on, and carries out what the harness asks for by sending the
messages the page would.

The menus' port and guid come from slop-activator (activator/), which finds them in the game's
memory while it keeps the game on its LAN provider, and writes them to
%TEMP%\\slop-activator\\<pid>.json. One bridge per game; it exits when the game's menus go.

    python3 harness/webui/bridge.py <instance file> [server]

Orders it takes (the ones the harness sends): raw {message, payload}, lanjoin {gameName,
keepProvider}, leave. Others are logged and skipped - the page's host, join and eval are for
Battle.net lobbies and for poking at the page itself.
"""
import base64
import json
import os
import pathlib
import socket
import struct
import sys
import time
import urllib.request

SETTLE = 15.0     # how long slop-activator may take to switch the provider when asked


class Menus:
    """The menus' websocket: what the page sends as {"type":"webui", ...}, and what comes back."""

    def __init__(self, port, guid):
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f'GET /webui-socket/{guid} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n'
                           f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n'
                           f'Origin: http://127.0.0.1:{port}\r\n\r\n').encode())
        reply = b''
        while b'\r\n\r\n' not in reply:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('the menus closed the connection')
            reply += chunk
        if not reply.startswith(b'HTTP/1.1 101'):
            raise ConnectionError(f'the menus refused the socket: {reply.splitlines()[0]!r}')
        self.buffer = reply.split(b'\r\n\r\n', 1)[1]

    def send(self, message, payload=None):
        data = json.dumps({'type': 'webui', 'message': message, 'payload': payload or {}}).encode()
        self._frame(0x1, data)

    def _frame(self, opcode, data):
        if len(data) < 126:
            head = bytes([0x80 | opcode, 0x80 | len(data)])
        elif len(data) < 65536:
            head = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack('>H', len(data))
        else:
            head = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack('>Q', len(data))
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def receive(self, timeout):
        """Messages from the game within `timeout` seconds; raises when the menus are gone."""
        self.sock.settimeout(timeout)
        try:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError('the menus closed the socket')
            self.buffer += chunk
        except socket.timeout:
            pass
        out = []
        while len(self.buffer) >= 2:
            length, at = self.buffer[1] & 0x7f, 2
            if length == 126:
                length, at = struct.unpack('>H', self.buffer[2:4])[0], 4
            elif length == 127:
                length, at = struct.unpack('>Q', self.buffer[2:10])[0], 10
            if len(self.buffer) < at + length:
                break
            opcode, data = self.buffer[0] & 0x0f, self.buffer[at:at + length]
            self.buffer = self.buffer[at + length:]
            if opcode == 0x8:
                raise ConnectionError('the menus closed the socket')
            if opcode == 0x9:
                self._frame(0xA, data)
            elif opcode == 0x1:
                try:
                    out.append(json.loads(data))
                except ValueError:
                    pass
        return out


class Server:
    """The web UI server, spoken to the way the page does."""

    def __init__(self, url, me):
        self.url, self.me = url.rstrip('/'), me

    def get(self, path):
        with urllib.request.urlopen(f'{self.url}{path}?id={self.me}', timeout=5) as response:
            return json.loads(response.read() or b'null')

    def post(self, path, body):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        request = urllib.request.Request(f'{self.url}{path}' + (f'?id={self.me}' if path != '/' else ''), data=data,
                                         method='POST')
        try:
            urllib.request.urlopen(request, timeout=5).close()
        except OSError:
            pass

    def record(self, text):
        self.post('/', f'[{self.me}] {text}')


def instance(path, timeout=180):
    """The activator's instance file: the game's pid, and its menus' port and guid."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            return json.loads(pathlib.Path(path).read_text())
        except (OSError, ValueError):
            time.sleep(0.5)
    raise SystemExit(f'no instance file at {path}')


def main():
    path = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 else os.environ.get('WC3_SERVER', 'http://127.0.0.1:8777')
    found = instance(path)
    menus = Menus(found['port'], found['guid'])
    server = Server(url, str(found['port']))
    screen, lobby = 'UNKNOWN', {}
    lan = {'wanted': None, 'asked': 0.0}
    hello = server.get('/hello')
    server.record(f'bridge for game {found["pid"]}: instance {hello["number"]}')

    def activations():
        return instance(path, timeout=5).get('activations', 0)

    def lanjoin(command):
        # A fresh TCPN provider for each search: asked of slop-activator, whose switch rebuilds the
        # provider, and waited for. (Not InitializeNetProvider, as the page sends: on the Windows
        # build that starts a Battle.net sign-in.)
        if not command.get('keepProvider'):
            before = activations()
            request = pathlib.Path(path).with_suffix('.request')
            request.write_text('switch')
            end = time.time() + SETTLE
            while activations() == before and time.time() < end:
                handle(menus.receive(0.25))
            if activations() == before:
                request.unlink(missing_ok=True)
                server.record('slop-activator did not switch the provider in time; searching anyway')
        menus.send('SendGameListing')
        lan['wanted'], lan['asked'] = command['gameName'], 0.0
        server.record(f'looking for LAN game {command["gameName"]}')

    def run(command):
        verb = command.get('verb')
        if verb == 'raw':
            menus.send(command['message'], command.get('payload'))
        elif verb == 'lanjoin':
            lanjoin(command)
        elif verb == 'leave':
            menus.send('LeaveGame')
        else:
            server.record(f'bridge: no {verb} here (it is for the page)')

    def handle(messages):
        nonlocal screen, lobby
        for message in messages:
            kind, payload = message.get('messageType'), message.get('payload') or {}
            server.record(f'<- {kind} {json.dumps(payload)[:300]}')
            if kind == 'SetGlueScreen':
                screen = payload.get('screen', screen)
                server.post('/state', {'screen': screen, 'lobby': lobby})
            elif kind == 'GameLobbySetup':
                lobby = payload
            elif kind == 'GameList' and lan['wanted']:
                for game in payload.get('games') or []:
                    if game.get('name') == lan['wanted']:
                        server.record(f'joining LAN game {game["name"]} (list id {game["id"]})')
                        menus.send('JoinGame', {'gameId': game['id'], 'password': '', 'mapFile': game.get('mapFile')})
                        lan['wanted'] = None
                        break

    last_poll = 0.0
    try:
        while True:
            handle(menus.receive(0.25))
            now = time.time()
            if lan['wanted'] and now - lan['asked'] > 2:
                menus.send('GetGameList')
                lan['asked'] = now
            if now - last_poll > 0.5:
                last_poll = now
                try:
                    work = server.get('/poll')
                except OSError:
                    continue
                for command in work or []:
                    run(command)
    except (ConnectionError, OSError) as error:
        server.record(f'bridge for game {found["pid"]}: the menus are gone ({error})')


if __name__ == '__main__':
    main()
