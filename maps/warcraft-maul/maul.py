"""What the Warcraft Maul tests share: the map's own race data, and the steps every race test
takes (settings, picking, building, placing targets).

The race data comes from the map repo (scripts/race-data.js reads the map's object data), so the
tests check the game against what the map says, and follow it when the map changes. The map
repo is found beside this one, or at $WC3_MAUL.
"""
import functools
import json
import os
import pathlib
import re
import subprocess

from slop import TestFailed

MAUL = pathlib.Path(os.environ.get('WC3_MAUL') or pathlib.Path(__file__).resolve().parents[3] / 'wc3-ts-template')

RED = 0
BLUE = 1
# The first creep player (Navy): towers attack its units
CREEPS = 13
# Test targets: a ground creep (wave 4, Militia) and an air one (wave 5, Wind Rider)
GROUND_TARGET = 'hC16'
AIR_TARGET = 'nC17'
TARGET_LIFE = 5_000_000

@functools.lru_cache(maxsize=None)
def lane_areas():
    """The 13 lanes (the players' spawn areas) as the map defines them, red's first:
    [(min_x, min_y, max_x, max_y)]."""
    source = (MAUL / 'src/World/WarcraftMaulSettings.ts').read_text()
    block = re.search(r'PLAYER_AREAS: Rectangle\[\] = \[(.*?)\];', source, re.S).group(1)
    number = r'\s*(-?[\d.]+)\s*'
    return [tuple(float(v) for v in match)
            for match in re.findall(rf'new Rectangle\(\[{number},{number},{number},{number}\]\)', block)]


# A player's own lane holds several towers at once, far enough apart that none reaches another's
# target: near its corners when it is wide (red's, 2432 x 1536: 1184 or more between a tower and
# another's target), at its top and bottom when it is tall and narrow (blue's)
INSET_ACROSS, INSET_ALONG = 320, 256


def own_slots(player, area):
    min_x, min_y, max_x, max_y = area
    top, bottom = int(max_y - INSET_ALONG), int(min_y + INSET_ALONG)
    if max_x - min_x >= max_y - min_y:
        return [(player, player, int(x), y, side) for y, side in ((top, 1), (bottom, -1))
                for x in (min_x + INSET_ACROSS, max_x - INSET_ACROSS)]
    return [(player, player, int((min_x + max_x) / 2), y, side) for y, side in ((top, 1), (bottom, -1))]


def lane_slots(players=(RED,)):
    """Where the race tests put towers, each far from every other one's target: several in each
    playing player's own lane (the only place their homesick towers may stand), and one in the
    middle of every other lane, shared out so each player has about as many.
    [(player, lane, x, y, side)]: side is where its target goes, 1 above the tower, -1 below."""
    areas = lane_areas()
    slots = [slot for player in players for slot in own_slots(player, areas[player])]
    for lane, (min_x, min_y, max_x, max_y) in enumerate(areas):
        if lane not in players:
            player = min(players, key=lambda p: sum(1 for slot in slots if slot[0] == p))
            slots.append((player, lane, int((min_x + max_x) / 2), int((min_y + max_y) / 2), -1))
    return slots


def single_spot(distance=160):
    """Where a one-tower test builds, and where its targets go: red's first own slot, with the
    targets on its outer side."""
    _, _, x, y, side = lane_slots((RED,))[0]
    return (x, y), (x, y + side * distance)


def sell(game, player, towers, timeout=20):
    """Sells a player's towers the way a player does (the map's sell ability, A02D), so the map
    lets go of them as it does in a game, and waits until they are gone."""
    towers = set(towers)
    if not towers:
        return
    for tower in towers:
        game.order(player, tower, 'windwalk')
    game.wait(f'{len(towers)} tower(s) to be sold',
              lambda: not towers & {unit['id'] for unit in game.units(player)}, timeout)


@functools.lru_cache(maxsize=None)
def races(tier):
    """The races of a tier as the map defines them, with their towers."""
    done = subprocess.run(['node', 'scripts/race-data.js', tier], cwd=MAUL, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f'reading the race data failed: {done.stderr.strip()[-500:]}')
    return json.loads(done.stdout)


def sync(game, player, message):
    """A PlayerSync message as a player: what their own UI would have sent."""
    game.cmd(player, '@' + message)


def start(game, attempts=12):
    """Debug mode (no wave progression) at normal difficulty, set with the map's settings command
    as a host bot would, so nothing but the test itself happens in the lanes.

    The map takes the settings only from the host it detected, and only once it waits for them,
    which is a few seconds into the game - a command before that is dropped. So it is sent until
    the round exists (the heartbeat's wave turns 1)."""
    for _ in range(attempts):
        game.cmd(RED, '-s debug 100')
        try:
            game.wait('the settings to apply', lambda: (fresh_beat(game).get('wave') or 0) >= 1, 5)
            return
        except TestFailed:
            continue
    raise TestFailed('the game never took the settings (is red the detected host?)')


def start_wave(game):
    """Starts the current wave now (in Debug mode none comes until a player asks)."""
    game.cmd(RED, '-start')
    game.wait('the wave to start', lambda: fresh_beat(game).get('spawning') is True, timeout=30)


def pick(game, player, item, timeout=30):
    """Picks a race by its item; returns its builder as {id (a ref), type}."""
    before = len(game.events('race'))
    known = {unit['id'] for unit in game.units(player)}
    sync(game, player, f'race-pick:{item}')

    def builder_type():
        for line in game.events('race')[before:]:
            match = re.search(rf'p{player} got builder (\w+)', line)
            if match:
                return match.group(1)
            if re.search(rf'p{player} (refused|cannot afford|already has)', line):
                raise TestFailed(f'the pick of {item} was refused: {line}')
        return None

    game.wait(f'a builder from {item}', lambda: builder_type() is not None, timeout)
    kind = builder_type()
    found = [unit for unit in game.units(player) if unit['type'] == kind and unit['id'] not in known]
    if not found:
        raise TestFailed(f'the pick of {item} traced a {kind}, but the player has no new one')
    return {'id': found[0]['id'], 'type': kind}


def refusal(game, player, item, timeout=20):
    """Picks a race that should be refused; returns the refusal's trace line."""
    before = len(game.events('race'))
    sync(game, player, f'race-pick:{item}')
    found = []

    def refused():
        for line in game.events('race')[before:]:
            if re.search(rf'p{player} got builder', line):
                raise TestFailed(f'{item} was picked, but should have been refused')
            if re.search(rf'p{player} (refused|cannot afford|already has)', line):
                found.append(line)
                return True
        return False

    game.wait(f'{item} to be refused', refused, timeout)
    return found[0]


def towers(game, player=RED, timeout=15):
    """The player's towers as the map sees them: {id: {type, class}}."""
    before = len(game.events('tower'))
    game.cmd(player, '.towers')
    game.wait('the tower list', lambda: any(line.endswith(f'p{player} end') for line in game.events('tower')[before:]),
              timeout)
    out = {}
    for line in game.events('tower')[before:]:
        match = re.search(r'p\d+ id=(\d+) type=(\S+) class=(\S+)', line)
        if match:
            out[int(match.group(1))] = {'type': match.group(2), 'class': match.group(3)}
    return out


def fresh_beat(game, client=0, timeout=10):
    """A heartbeat written after this call: game state as of now, not a second ago."""
    return game.finish(game.beat_after(client), timeout)


def upgrade_chains(race):
    """Every way to build a tower: a base tower and the upgrades up to one of its last forms.
    [[base, upgrade, ...], ...] - so every tower and upgrade gets built at least once."""
    by_id = {t['id']: t for t in race['towers']}
    upgraded = {u for t in race['towers'] for u in t['upgradesTo']}

    def chains(tower_id):
        tower = by_id.get(tower_id)
        if tower is None or not tower['upgradesTo']:
            return [[tower_id]]
        return [[tower_id, *rest] for upgrade in tower['upgradesTo'] for rest in chains(upgrade)]

    return [chain for base in race['towers'] if base['id'] not in upgraded for chain in chains(base['id'])]
