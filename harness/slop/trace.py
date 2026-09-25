"""Reading back what the map library writes: the lockstep trace and its heartbeat.

A trace line is `<sequence> t<ticks> h<handles> <category> <text>`; the heartbeat is the
category `beat`, with `key=value` pairs and a `p<slot>(key=value ...)` part per player.
"""
import pathlib
import re

TRACE = 'slop-trace'
BEAT_FILE = 'slop-beat.txt'
COMMAND_FILE = 'slop-cmd'
LINE = re.compile(r'(\d+) t(\d+) h(\d+) (\S+) ?(.*)')


def data_folder(home):
    """Where a client with that home folder keeps CustomMapData."""
    return pathlib.Path(home) / 'Library/Application Support/Blizzard/Warcraft III/CustomMapData'


def clear(folder):
    """Removes what an earlier game left: trace chunks, command files, the beat file."""
    folder = pathlib.Path(folder)
    for stale in [*folder.glob(TRACE + '-*.txt'), *folder.glob(COMMAND_FILE + '-*.txt'), folder / BEAT_FILE]:
        stale.unlink(missing_ok=True)


def read(folder):
    """Every line a client has written, oldest first."""
    lines = []
    for chunk in sorted(pathlib.Path(folder).glob(TRACE + '-*.txt')):
        lines += re.findall(r'Preload\( "(.*)" \)', chunk.read_text(errors='replace'))
    lines.sort(key=lambda line: int(line.split(' ', 1)[0]) if line.split(' ', 1)[0].isdigit() else 0)
    return lines


def of_kind(lines, kind):
    return [line for line in lines if (match := LINE.match(line)) and match.group(4) == kind]


class Beat(dict):
    """One heartbeat: tick, handles and the map's own keys; players by slot."""

    @property
    def players(self):
        return self['players']

    def player(self, slot, key, default=None):
        return self.players.get(slot, {}).get(key, default)

    def gold(self, slot):
        return self.player(slot, 'g')

    def lumber(self, slot):
        return self.player(slot, 'l')


def _value(text):
    if re.fullmatch(r'-?\d+', text):
        return int(text)
    if text in ('true', 'false'):
        return text == 'true'
    return text


def parse_beat(line):
    match = LINE.match(line)
    if not match or match.group(4) != 'beat':
        return None
    body = match.group(5)
    beat = Beat(players={}, line=int(match.group(1)), tick=int(match.group(2)))
    for slot, fields in re.findall(r'p(\d+)\(([^)]*)\)', body):
        beat['players'][int(slot)] = {k: _value(v) for k, v in re.findall(r'(\w+)=(\S+)', fields)}
    for key, value in re.findall(r'(?:^|\s)(\w+)=([^\s(]+)(?=\s|$)', re.sub(r'p\d+\([^)]*\)', '', body)):
        beat[key] = _value(value)
    return beat


def last_beat(lines):
    for line in reversed(lines):
        beat = parse_beat(line)
        if beat:
            return beat
    return None


UNIT = re.compile(r'p(\d+) id=(\d+) type=(\S+) at=(-?\d+),(-?\d+) life=(-?\d+) order=(\S*)')


def parse_unit(line):
    match = UNIT.search(line)
    if not match:
        return None
    return dict(player=int(match.group(1)), id=int(match.group(2)), type=match.group(3), x=int(match.group(4)),
                y=int(match.group(5)), life=int(match.group(6)), order=match.group(7))


def write_command(folder, line, command_id):
    """Drops a command for the file channel: a preload file that sets the carrier tooltip, in the
    few names just ahead of the one the game is polling (a name polled while missing is never
    read again). Returns the names written, or None when no game is polling."""
    folder = pathlib.Path(folder)
    beat = folder / BEAT_FILE
    polling = 0
    if beat.exists():
        for found in re.finditer(r'cmdpoll=(\d+)', beat.read_text(errors='replace')):
            polling = max(polling, int(found.group(1)))
    if polling == 0:
        return None
    ahead, spread = 4, 8
    payload = f'CMD:{command_id}:{line}'.replace('"', "'").encode()
    body = (b'function PreloadFiles takes nothing returns nothing\n\r\n'
            b'\tcall PreloadStart()\r\n'
            b'\tcall Preload( "")\ncall BlzSetAbilityTooltip(\'ANcl\', "' + payload + b'", 0)\n//" )\r\n'
            b'\tcall PreloadEnd( 0.0 )\r\n\nendfunction\n\n\r\n')
    names = range(polling + ahead, polling + ahead + spread)
    for number in names:
        (folder / f'{COMMAND_FILE}-{number:04d}.txt').write_bytes(body)
    for old in folder.glob(COMMAND_FILE + '-*.txt'):
        if int(old.stem.rsplit('-', 1)[-1]) < polling:
            old.unlink(missing_ok=True)
    return list(names)
