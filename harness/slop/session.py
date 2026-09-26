"""One game: clients on this machine joined to the host, ready to be driven and read back.

    session = Session(config.load(), 'try').start()
    session.cmd(0, '.gold 500')              # as red, through the host's seat
    session.wait_for('the gold', lambda beat: beat.gold(0) == 500)
    session.check_in_step()
    session.stop()

What works depends on the config: an active host can act (cmd, type); a hidden one only relays.
The map library (injected into the staged map) is what makes commands do anything and what
writes the trace everything is read back from; without it a session still checks the host saw
no desync. See docs/harness.md.
"""
import json
import os
import re
import pathlib
import shutil
import socket
import subprocess
import time
import uuid

from . import trace, webui
from . import config as config_module
from .config import REPO, Config

ACTIVATE = REPO / 'harness/activate.sh'
# The running game, whichever map it is: one at a time, since a session refuses to start while
# Warcraft III runs
STATE = REPO / '.slop' / 'session.json'


class TestFailed(AssertionError):
    pass


class NeedsLibrary(TestFailed):
    pass


def run_build(config: Config, log=print):
    if not config.build:
        raise RuntimeError('no map.build in the config')
    log(f'building: {config.build}')
    subprocess.run(config.build, shell=True, cwd=config.build_dir, check=True)


def ensure_host_binary(config: Config, log=print):
    """Builds the host when it is missing or older than its sources - which include the map
    library, built into the binary - and cargo is at hand; a no-op build takes a moment."""
    if shutil.which('cargo') is None:
        if not config.host_binary.exists():
            raise RuntimeError(f'no host at {config.host_binary}, and no cargo to build it')
        return
    if not config.host_binary.exists():
        log('building the host (cargo build, the first time takes a few minutes)')
    done = subprocess.run(['cargo', 'build', '--quiet'], cwd=REPO / 'host', capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f'building the host failed:\n{done.stderr.strip()[-2000:]}')


class Pending:
    """A request to the game whose answer is not in yet (from the *_later methods): ready() says
    whether it is, result() reads it. Lets a test keep several requests going at once - two
    players' builds, say - instead of waiting on each in turn."""

    def __init__(self, what, ready, result):
        self.what, self._ready, self._result = what, ready, result

    def ready(self):
        try:
            return bool(self._ready())
        except (OSError, ValueError, KeyError):
            return False

    def result(self):
        return self._result()


class Session:
    def __init__(self, config: Config, name='game', log=print):
        self.config = config
        self.name = name
        self.log = log
        self.game_name = 'slop-' + uuid.uuid4().hex[:8]
        # Each client has its own user folder (two games on one break real maps); the library
        # names its files by player, so a client's files are found in whichever folder has them
        self.folders = [config.data_for(i) / 'CustomMapData' for i in range(config.clients)]
        # The slot each client plays, in slot order: client 0 plays the first of them
        self.client_slots = []
        self.artifacts = config.runs / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
        self.server = webui.Server(config.server)
        self.started_server = False
        self.client_pids = []
        # Every game this session started, from the moment it started: stop() kills them all,
        # also one that never checked in
        self.launched = []
        self.host_pid = None
        self.library = False
        self.seat = None
        self.host_log = None
        self.saved = False

    # --- lifecycle -------------------------------------------------------------------------------

    @property
    def staged_map(self):
        return self.config.maps / self.config.map_folder / self.config.map_file.name

    def start(self, timeout=240):
        """Launches the clients and plays the first game on them."""
        config = self.config
        running = subprocess.run(['pgrep', '-f', str(config.game)], capture_output=True, text=True).stdout.split()
        if running:
            raise RuntimeError(f'Warcraft III is already running (pid {", ".join(running)}): close it, '
                               'or `slop down` if a session was left up')
        if not config.map_file.is_file():
            raise RuntimeError(f'no map at {config.map_file}' + (' (build it: slop up --build)' if config.build else ''))
        self.artifacts.mkdir(parents=True, exist_ok=True)
        ensure_host_binary(config, self.log)
        self._stage()

        self.started_server = self.server.ensure(self.artifacts / 'webui.log')
        webui.install_page(config.webui_dir, config.server)
        self.server.reset()
        for index in range(config.clients):
            home = config.home_for(index)
            home.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, HOME=str(home), CFFIXED_USER_HOME=str(home))
            self.launched.append(subprocess.Popen(config.launch, env=env, stdout=subprocess.DEVNULL,
                                                  stderr=subprocess.DEVNULL, start_new_session=True).pid)
            self._wait(lambda: self.server.checked_in() >= index + 1, 90, f'client {index + 1} to start')
            # Offline: LAN needs no Battle.net, and a sign-in can sit in the login queue for minutes
            self.server.command(index + 1, 'raw', message='PlayOffline', payload={})
            self.client_pids.append(self._client_pid(index + 1))
        self.log(f'{config.clients} client(s) up, offline')
        self._play(timeout)
        return self

    def next_game(self, name, timeout=240):
        """Ends this game and plays a new one on the same clients, which saves launching them
        again. Raises when a client does not make it back to the menus; stop() and start() a
        fresh session then."""
        self.end_game()
        self.name = name
        self.game_name = 'slop-' + uuid.uuid4().hex[:8]
        self.artifacts = self.config.runs / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self._play(timeout)
        return self

    def end_game(self, timeout=45):
        """Ends the game and brings every client back to the menus, keeping its artifacts. With
        the library the game ends through `.end`, the same moment on every client; without it
        the host is stopped and the clients are dropped from the game. Either way each client
        lands on the score screen, which is part of the menus, and is sent on from there."""
        self._collect()
        ended = False
        if self.library:
            try:
                if self.seat is not None:
                    self.cmd(0, '.end')
                else:
                    self.file_command(0, '.end')
                ended = True
            except (TestFailed, OSError):
                pass
        if ended:
            try:
                self._wait(lambda: self._screens() == {'SCORE_SCREEN'}, timeout / 2, 'the score screen')
            except TestFailed:
                ended = False
        self._stop_host()
        if not ended:
            self._wait(lambda: 'GAME_LOBBY' not in self._screens(), timeout, 'the clients to leave the game')
        # What the score screen's close button sends. Whether the menus then report another
        # screen is not known; a client can join the next game from the score screen as well
        # Closing the score screen of a client that played offline asks it to log in (the menus'
        # "logged out during game" path sends StartLogin), so it is sent offline again after
        for number in range(1, self.config.clients + 1):
            self.server.command(number, 'raw', message='ScoreScreenClose', payload={})
            self.server.command(number, 'raw', message='PlayOffline', payload={})
        try:
            self._wait(lambda: not self._screens() & {'SCORE_SCREEN', 'GAME_LOBBY'}, 10, 'the menus')
        except TestFailed:
            pass
        self.log(f'game over; clients on {", ".join(sorted(map(str, self._screens())))}')

    def _play(self, timeout):
        """Hosts a game and joins every client to it."""
        config = self.config
        for folder in self.folders:
            trace.clear(folder)
        self._start_host()
        for number in range(1, config.clients + 1):
            subprocess.run(['bash', str(ACTIVATE), str(number), str(self.client_pids[number - 1])],
                           check=True, capture_output=True, timeout=90,
                           env=dict(os.environ, WC3_SERVER=config.server, WC3_GAME=str(config.game)))
            self.server.command(number, 'raw', message='SendGameListing', payload={})
            self.server.command(number, 'lanjoin', gameName=self.game_name, keepProvider=True)
        self._wait(lambda: self.control('status').startswith('Playing'), timeout, 'the game to start')
        self.client_slots = sorted(int(pid) - 1 for pid in re.findall(r'\| p(\d+) ', self.control('status')))
        if self.library:
            self._wait(lambda: all(self.beat(i) for i in range(config.clients)), 30,
                       "the library's first heartbeat on every client")
        self.log(f'game {self.game_name} is on ({"active host, seat " + str(self.seat) if self.seat is not None else "hidden host"}'
                 f'{", library in" if self.library else ", no library"})')
        self.save()

    def _screens(self):
        """The menu screen each client's page last reported."""
        return {v['state'].get('screen') for v in self.server.instances().values()}

    def _stage(self):
        """The map where the game looks, with the library in when the config asks for it."""
        config = self.config
        folder = self.staged_map.parent
        folder.mkdir(parents=True, exist_ok=True)
        # The second client's user folder sees the same map: its Maps/<folder> links to this one
        for client in range(1, config.clients):
            linked = config.data_for(client) / 'Maps' / config.map_folder
            if linked.is_symlink() and linked.resolve() == folder.resolve():
                continue
            if linked.exists() or linked.is_symlink():
                raise RuntimeError(f'{linked} exists and is not a link to {folder}; move it away')
            linked.parent.mkdir(parents=True, exist_ok=True)
            linked.symlink_to(folder)
        seat = config.seat if config.active else None
        if config.inject:
            command = [str(config.host_binary), 'inject', '--map', str(config.map_file), '--out', str(self.staged_map),
                       '--prefix', config.prefix]
            if seat is not None:
                command += ['--seat', seat]
            done = subprocess.run(command, capture_output=True, text=True)
            if done.returncode == 0:
                self.library = True
            else:
                self.log(f'no library: {done.stderr.strip().splitlines()[0] if done.stderr.strip() else "inject failed"}')
        if not self.library:
            shutil.copyfile(config.map_file, self.staged_map)

    def _start_host(self):
        config = self.config
        command = [str(config.host_binary), 'host', '--map', str(self.staged_map), '--name', self.game_name,
                   '--control', str(config.control), '--prefix', config.prefix, '--host', config.host_mode]
        if config.active:
            command += ['--seat', config.seat]
        command += config.names[:config.clients]
        self.host_log = open(self.artifacts / 'host.log', 'w')
        self.host_pid = subprocess.Popen(command, cwd=REPO, stdout=self.host_log, stderr=subprocess.STDOUT,
                                         start_new_session=True).pid
        self._wait(lambda: self.control('status').startswith('Lobby'), 20, 'the host to come up')
        status = self.control('status')
        if 'seat=' in status:
            self.seat = int(status.split('seat=')[1].split()[0])

    def _collect(self):
        """Copies each client's trace into this game's artifacts."""
        for slot in self.client_slots:
            target = self.artifacts / f'trace-p{slot}'
            target.mkdir(parents=True, exist_ok=True)
            for chunk in trace.chunks(self.folders, slot):
                shutil.copy(chunk, target / chunk.name)

    def _stop_host(self):
        if self.host_pid:
            subprocess.run(['kill', '-9', str(self.host_pid)], capture_output=True)
            self.host_pid = None
        if self.host_log:
            self.host_log.close()
            self.host_log = None

    def stop(self):
        self._collect()
        self._stop_host()
        for pid in set(self.client_pids) | set(self.launched):
            subprocess.run(['kill', '-9', str(pid)], capture_output=True)
        if self.started_server:
            self.server.stop()
            subprocess.run(['pkill', '-f', str(webui.SERVER)], capture_output=True)
        if self.saved:
            STATE.unlink(missing_ok=True)
        self.log(f'stopped; artifacts in {self.artifacts}')

    def save(self):
        """What `slop down` and later commands need to find this session again."""
        state = dict(name=self.name, game_name=self.game_name, artifacts=str(self.artifacts), host_pid=self.host_pid,
                     client_pids=self.client_pids, started_server=self.started_server, library=self.library,
                     seat=self.seat, client_slots=self.client_slots, launched=self.launched)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(dict(state, config=str(self.config.path)), indent=1))
        self.saved = True

    @classmethod
    def resume(cls, log=print):
        """The session `slop up` left running, whichever map it plays."""
        if not STATE.exists():
            raise RuntimeError('no game is up (slop up starts one)')
        state = json.loads(STATE.read_text())
        config = config_module.load(state['config'])
        session = cls(config, state['name'], log)
        session.game_name, session.artifacts = state['game_name'], pathlib.Path(state['artifacts'])
        session.host_pid, session.client_pids = state['host_pid'], state['client_pids']
        session.started_server, session.library, session.seat = state['started_server'], state['library'], state['seat']
        session.client_slots = state.get('client_slots', [])
        session.launched = state.get('launched', [])
        session.saved = True
        return session

    # --- acting ------------------------------------------------------------------------------------

    def control(self, line):
        """One command to the host; its one-line reply."""
        with socket.create_connection(('127.0.0.1', self.config.control), timeout=5) as sock:
            sock.sendall((line + '\n').encode())
            return sock.makefile().readline().strip()

    def cmd(self, player, line):
        """Runs a command line as a player (0-based slot) on every client: the library's built-ins
        (.units, .order, .build, .gold, .lumber) or whatever the map's hooks take."""
        self._need_library()
        reply = self.control(f'cmd {player} {line}')
        if not reply.startswith('queued'):
            raise TestFailed(f'the host refused "{line}": {reply}')

    def type(self, text):
        """Types a chat line as the seat's player, firing the map's chat triggers; no library
        needed. Unverified in a real game - see docs/host.md."""
        reply = self.control(f'type {text}')
        if not reply.startswith('queued'):
            raise TestFailed(f'the host refused to type "{text}": {reply}')

    def chat(self, text):
        """A line everyone sees on screen; the map does not."""
        return self.control(f'chat {text}')

    def file_command(self, client, line):
        """Runs a command as that client's own player through the file channel: no host seat
        needed, only the library."""
        self._need_library()
        slot = self.client_slots[client]
        folder = trace.polling_folder(self.folders, slot)
        names = folder and trace.write_command(folder, slot, line, int(time.time() * 1000) % 10**9)
        if names is None:
            raise TestFailed(f'client {client} is not polling for commands')

    def units(self, player, client=0, timeout=15):
        """A player's units, listed by the library: [{id, type, x, y, life, order}]."""
        return self.finish(self.units_later(player, client), timeout)

    def units_later(self, player, client=0):
        """units() without waiting: a Pending whose result() is the list."""
        before = len(self.events('unit', client))
        self.cmd(player, '.units')

        def lines():
            return self.events('unit', client)[before:]

        return Pending(f"player {player}'s unit list",
                       lambda: any(line.endswith(f'p{player} end') for line in lines()),
                       lambda: [unit for unit in map(trace.parse_unit, lines()) if unit and unit['player'] == player])

    def order(self, player, unit, name, *args):
        """order(0, id, 'move', x, y), order(0, id, 'stop'), order(0, id, 'attack', target_id)."""
        self.cmd(player, ' '.join(['.order', str(unit), name, *map(str, args)]))

    def build(self, player, builder, unit_type, x, y):
        self.cmd(player, f'.build {builder} {unit_type} {x} {y}')

    def upgrade(self, player, unit, unit_type):
        self.cmd(player, f'.upgrade {unit} {unit_type}')

    def create(self, player, unit_type, x, y, count=1, life=None, frozen=False, rooted=False, timeout=15):
        """Creates units for a player (a creep player, say) and returns them: [{id, type, x, y}].
        Life and freezing are set as they are made, before anything can shoot at them."""
        return self.finish(self.create_later(player, unit_type, x, y, count, life, frozen, rooted), timeout)

    def create_later(self, player, unit_type, x, y, count=1, life=None, frozen=False, rooted=False):
        """create() without waiting: a Pending whose result() is the units made."""
        before = len(self.events('created'))
        options = (f' life={life}' if life else '') + (' frozen' if frozen else '') + (' rooted' if rooted else '')
        self.cmd(player, f'.create {unit_type} {x} {y} {count}{options}')
        return Pending(f'{count} {unit_type} to be created',
                       lambda: len(self._new_created(before, player)) >= count,
                       lambda: self._new_created(before, player)[:count])

    def _new_created(self, before, player):
        return [c for c in map(trace.parse_created, self.events('created')[before:]) if c and c['player'] == player]

    def inspect(self, unit, *abilities, timeout=15):
        """A unit right now, any owner: type, owner, life, max_life, mana, damage_min/max, cd, range,
        armor, speed, and the level of each ability or buff asked for (0 without it)."""
        return self.finish(self.inspect_later(unit, *abilities), timeout)

    def inspect_later(self, unit, *abilities):
        """inspect() without waiting: a Pending whose result() is the unit's fields."""
        before = len(self.events('inspect'))
        self.cmd(0, ' '.join(['.inspect', str(unit), *abilities]))

        def found():
            return next((line for line in self.events('inspect')[before:] if f'id={unit} ' in line), None)

        return Pending(f'unit {unit} to be inspected', lambda: found() is not None, lambda: trace.parse_fields(found()))

    def beat_after(self, client=0):
        """A Pending for the first heartbeat written after this call: game state as of now, not a
        second ago."""
        first = self.beat(client)
        start = first['tick'] if first else -1

        def newer():
            beat = self.beat(client)
            return beat is not None and beat['tick'] > start

        return Pending('a fresh heartbeat', newer, lambda: self.beat(client))

    def finish(self, pending, timeout=15):
        """Waits for a Pending and returns its result; fails the test when it does not come."""
        self._wait(pending.ready, timeout, pending.what)
        return pending.result()

    def remove(self, player, unit):
        """Takes one of the player's units out of the game, with no death."""
        self.cmd(player, f'.remove {unit}')

    def watch(self, player):
        """Traces every hit on the player's units from now on; read them back with hits()."""
        self.cmd(player, '.watch')

    def watch_casts(self, player):
        """Traces every spell the player's units cast from now on; read them back with casts()."""
        self.cmd(player, '.casts')

    def casts(self, client=0):
        """Every traced cast: [{src, srctype, ability, dst}]."""
        return [trace.parse_fields(line) for line in self.events('cast', client)]

    def hits(self, client=0):
        """Every traced hit: [{src, srctype, dst, amount, raw, atk, attack}]; raw is the amount
        before the target's armor."""
        return [trace.parse_fields(line) for line in self.events('hit', client)]

    # --- reading -----------------------------------------------------------------------------------

    def trace(self, client=0):
        return trace.read(self.folders, self.client_slots[client])

    def beat(self, client=0):
        """The newest heartbeat of a client: game state as of the last whole second."""
        return trace.last_beat(self.trace(client))

    def events(self, kind, client=0):
        """Traced lines of one category: slop, unit, order, and whatever the map notes."""
        return trace.of_kind(self.trace(client), kind)

    def wait_for(self, what, predicate, timeout=30, client=0):
        """Waits until predicate(newest beat) holds; fails the test when it never does."""
        self._need_library()
        return self._wait(lambda: (lambda b: b is not None and predicate(b))(self.beat(client)), timeout, what)

    def wait(self, what, condition, timeout=30):
        return self._wait(condition, timeout, what)

    # --- the checks every test gets ------------------------------------------------------------------

    def check_in_step(self):
        """Fails when the host saw a desync, or when the clients' traces differ (handle ids aside)."""
        log = (self.artifacts / 'host.log').read_text(errors='replace')
        if 'DESYNC' in log:
            raise TestFailed(f'the host saw {log.count("DESYNC")} desync(s); see {self.artifacts / "host.log"}')
        if not self.library or self.config.clients < 2:
            return
        # Handle ids are left out: local-only code (UI frames, effects one player sees) makes and
        # frees handles on one client and not the other, which is no divergence - the host's
        # checksum comparison above agrees with that
        traces = [[trace.without_handles(line) for line in self.trace(i)] for i in range(self.config.clients)]
        for index in range(min(len(t) for t in traces)):
            if len({t[index] for t in traces}) > 1:
                raise TestFailed(f'the clients parted at trace line {index + 1}: '
                                 + ' | '.join(t[index][:80] for t in traces))

    def screenshot(self, client=0):
        """One client's window as a PNG file, captured by window id (needs yabai to find it)."""
        pid = self.client_pids[client]
        windows = json.loads(subprocess.run(['yabai', '-m', 'query', '--windows'], capture_output=True, text=True).stdout or '[]')
        window = next((w for w in windows if w.get('pid') == pid), None)
        if window is None:
            raise TestFailed(f'no window found for client {client} (is yabai installed?)')
        path = self.artifacts / f'screenshot-client{client + 1}-{int(time.time())}.png'
        if subprocess.run(['screencapture', '-x', '-o', '-l', str(window['id']), str(path)]).returncode:
            raise TestFailed('could not capture the window')
        return path

    # --- plumbing ----------------------------------------------------------------------------------

    def _need_library(self):
        if not self.library:
            raise NeedsLibrary('this needs the map library, which is only in Lua maps with library.inject on')

    def _wait(self, condition, timeout, what):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if condition():
                    return True
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(1)
        raise TestFailed(f'timed out after {timeout}s waiting for {what}')

    def _client_pid(self, number):
        port = self.server.port_of(number)
        out = subprocess.run(['lsof', '-ti', f'tcp:{port}', '-sTCP:LISTEN'], capture_output=True, text=True).stdout
        return int(out.split()[0])
