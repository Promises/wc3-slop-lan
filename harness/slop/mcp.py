"""An MCP server over the harness: play a map with real clients through tools.

One session lives across calls: start it, act (commands as any player, typed chat, unit orders),
read the game back (heartbeat, traced events, units, screenshots), check the clients are in
step, stop it. Also runs the config's tests. Speaks MCP over stdio (JSON-RPC 2.0, a message per
line) with no dependencies: `slop mcp -c <config>`. See docs/harness.md.
"""
import base64
import json
import sys
import traceback

from . import runner
from .config import Config
from .session import Session, TestFailed

PROTOCOL = '2025-06-18'


def log(text):
    # stdout carries the protocol; anything else goes to stderr
    print(text, file=sys.stderr, flush=True)


class Tools:
    def __init__(self, config: Config):
        self.config = config
        self.session = None

    def need_game(self):
        if self.session is None:
            raise TestFailed('no game running: call game_start first')
        return self.session

    def game_start(self):
        if self.session is not None:
            self.session.stop()
            self.session = None
        session = Session(self.config, 'mcp', log=log)
        try:
            session.start()
        except Exception:
            session.stop()
            raise
        self.session = session
        return (f'game {session.game_name} is on with {self.config.clients} client(s); '
                f'host {"seat " + str(session.seat) if session.seat is not None else "hidden"}, '
                f'library {"in" if session.library else "absent"}; artifacts in {session.artifacts}')

    def game_stop(self):
        game = self.need_game()
        game.stop()
        self.session = None
        return f'stopped; artifacts kept in {game.artifacts}'

    def status(self):
        return self.need_game().control('status')

    def cmd(self, player, line):
        self.need_game().cmd(int(player), line)
        return f'ran as player {player}: {line}'

    def type(self, text):
        self.need_game().type(text)
        return f'typed as the seat: {text}'

    def chat(self, text):
        return self.need_game().chat(text)

    def units(self, player, client=0):
        return self.need_game().units(int(player), int(client))

    def order(self, player, unit, name, args=()):
        self.need_game().order(int(player), int(unit), name, *args)
        return f'player {player} ordered {unit} to {name} {" ".join(map(str, args))}'.strip()

    def build(self, player, builder, unit_type, x, y):
        self.need_game().build(int(player), int(builder), unit_type, x, y)
        return f'player {player} ordered {builder} to build {unit_type} at {x},{y}'

    def state(self, client=0):
        return self.need_game().beat(int(client))

    def events(self, kind, client=0, last=20):
        return self.need_game().events(kind, int(client))[-int(last):]

    def trace_tail(self, client=0, lines=30):
        return self.need_game().trace(int(client))[-int(lines):]

    def check_in_step(self):
        self.need_game().check_in_step()
        return 'in step: the host saw no desync and the traces agree'

    def screenshot(self, client=0):
        path = self.need_game().screenshot(int(client))
        return {'image': base64.b64encode(path.read_bytes()).decode(), 'path': str(path)}

    def run_tests(self, names=()):
        if self.session is not None:
            return 'stop the running game first: the tests start games of their own'
        lines = []
        ok = runner.run(self.config, names, log=lines.append)
        return ('\n'.join(lines))[-6000:] + ('' if ok else '\n(failures above)')


PLAYER = {'type': 'integer', 'description': 'map player, as a 0-based slot (0 = red)'}
CLIENT = {'type': 'integer', 'description': 'which client to read, 0 or 1 (both run the same game)', 'default': 0}
SPEC = {
    'game_start': ('Launch the clients and the host, join them, wait until the game is on. Takes a minute or two; '
                   'replaces a running game.', {}, []),
    'game_stop': ('Stop the game; its artifacts (host log, traces) are kept.', {}, []),
    'status': ("The host's view: phase, ticks, seat, each player's state.", {}, []),
    'cmd': ('Run a command line as a player on every client: the library built-ins (.units, .order, .build, '
            '.gold, .lumber) or whatever the map hooks (for Warcraft Maul, chat commands like "-gold 500" and '
            '"@<sync message>").', {'player': PLAYER, 'line': {'type': 'string'}}, ['player', 'line']),
    'type': ("Type a chat line as the host's seat, firing the map's chat triggers (no library needed; unverified).",
             {'text': {'type': 'string'}}, ['text']),
    'chat': ('Show a chat line to everyone (display only; the map never sees it).', {'text': {'type': 'string'}}, ['text']),
    'units': ("List a player's units: id, type, position, life, current order.",
              {'player': PLAYER, 'client': CLIENT}, ['player']),
    'order': ("Order one of a player's units: immediate (\"stop\"), point (\"move\" x y) or target (a unit id).",
              {'player': PLAYER, 'unit': {'type': 'integer'}, 'name': {'type': 'string'},
               'args': {'type': 'array', 'items': {'type': 'number'}, 'default': []}}, ['player', 'unit', 'name']),
    'build': ("Order a player's builder to build a unit type (four letters) at x, y.",
              {'player': PLAYER, 'builder': {'type': 'integer'}, 'unit_type': {'type': 'string'},
               'x': {'type': 'number'}, 'y': {'type': 'number'}}, ['player', 'builder', 'unit_type', 'x', 'y']),
    'state': ("The newest heartbeat: the map's own keys, and gold/lumber (plus the map's fields) per player.",
              {'client': CLIENT}, []),
    'events': ('Traced lines of one category: slop, unit, order, beat, and whatever the map notes.',
               {'kind': {'type': 'string'}, 'client': CLIENT, 'last': {'type': 'integer', 'default': 20}}, ['kind']),
    'trace_tail': ("The last lines of a client's trace.",
                   {'client': CLIENT, 'lines': {'type': 'integer', 'default': 30}}, []),
    'check_in_step': ("Fail if the host saw a desync or the clients' traces differ.", {}, []),
    'screenshot': ("Capture one client's game window.", {'client': CLIENT}, []),
    'run_tests': ("Run the config's tests, all or the named ones.",
                  {'names': {'type': 'array', 'items': {'type': 'string'}, 'default': []}}, []),
}


def call(tools, name, arguments):
    try:
        result = getattr(tools, name)(**arguments)
    except TestFailed as failure:
        return {'content': [{'type': 'text', 'text': str(failure)}], 'isError': True}
    except Exception:
        return {'content': [{'type': 'text', 'text': traceback.format_exc()[-3000:]}], 'isError': True}
    if isinstance(result, dict) and 'image' in result:
        return {'content': [{'type': 'image', 'data': result['image'], 'mimeType': 'image/png'},
                            {'type': 'text', 'text': result['path']}]}
    text = result if isinstance(result, str) else json.dumps(result, indent=1)
    return {'content': [{'type': 'text', 'text': text}]}


def serve(config: Config):
    tools = Tools(config)
    for raw in sys.stdin:
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        method, ident = message.get('method'), message.get('id')
        if ident is None:
            continue                      # notifications need no answer
        error = None
        if method == 'initialize':
            result = {'protocolVersion': message.get('params', {}).get('protocolVersion', PROTOCOL),
                      'capabilities': {'tools': {}}, 'serverInfo': {'name': 'wc3-slop', 'version': '0.1.0'}}
        elif method == 'tools/list':
            result = {'tools': [{'name': name, 'description': description,
                                 'inputSchema': {'type': 'object', 'properties': properties, 'required': required}}
                                for name, (description, properties, required) in SPEC.items()]}
        elif method == 'tools/call':
            params = message.get('params', {})
            if params.get('name') in SPEC:
                result = call(tools, params['name'], params.get('arguments') or {})
            else:
                error = {'code': -32602, 'message': 'unknown tool'}
        elif method == 'ping':
            result = {}
        else:
            error = {'code': -32601, 'message': f'no method {method}'}
        reply = {'jsonrpc': '2.0', 'id': ident}
        reply.update({'error': error} if error else {'result': result})
        print(json.dumps(reply), flush=True)
    if tools.session is not None:
        tools.session.stop()
