"""Warcraft Maul, played by real clients on the host.

Each test gets a fresh two-player game (red = 0, blue = 1). The map's hooks (SlopHooks.ts in the
map repo) take chat commands ("-gold 500") and PlayerSync messages ("@race-pick:I006") as any
player, and add lives, wave and creeps to the heartbeat, and kills and towers per player.
"""
import time

import maul
from maul import RED, sync
from slop import TestFailed

# Race shop items (Beginner races); a normal pick costs the starting lumber
HUMAN_TOWN_HALL = 'I006'
ORC_STRONGHOLD = 'I007'


def picked(game, player):
    return any(f'p{player} ' in line for line in game.events('pick'))


def test_idle_lockstep(game):
    """Two clients left alone for a while stay in lockstep."""
    start = game.beat(0)['tick']
    game.wait_for('20 seconds of game time', lambda b: b['tick'] >= start + 200, timeout=60)


def test_commands_reach_both_clients(game):
    """A map chat command sent as a player runs once, as that player, on every client."""
    game.cmd(0, '-gold 1717')
    game.cmd(1, '-gold 2525')
    game.wait_for('both gold changes', lambda b: b.gold(0) == 1717 and b.gold(1) == 2525)
    other = game.beat(1)
    if (other.gold(0), other.gold(1)) != (1717, 2525):
        raise TestFailed(f'the second client disagrees: {other.players}')


def test_races_and_first_wave(game):
    """Settings, a race pick each, and the first wave spawning - the start of every real game."""
    sync(game, 0, 'game-settings:0:0')
    sync(game, 0, f'race-pick:{HUMAN_TOWN_HALL}')
    sync(game, 1, f'race-pick:{ORC_STRONGHOLD}')
    for player in (0, 1):
        game.wait(f'player {player} to pick', lambda: picked(game, player))
    game.wait_for('the first wave to spawn', lambda b: b['wave'] >= 1 and b['creeps'] > 0, timeout=240)


def test_unit_orders(game):
    """A player's builder takes a move order through the library, the same on both clients."""
    sync(game, 0, 'game-settings:0:0')
    sync(game, 0, f'race-pick:{HUMAN_TOWN_HALL}')
    game.wait('red to pick', lambda: picked(game, 0))
    units = game.units(0)
    if not units:
        raise TestFailed('red owns no units after picking a race')
    builder = units[0]
    target = (builder['x'] + 384, builder['y'])
    game.order(0, builder['id'], 'move', *target)
    game.wait('the order to be issued', lambda: any('issued' in line for line in game.events('order')))

    def arrived(client):
        moved = next((u for u in game.units(0, client) if u['id'] == builder['id']), None)
        return moved is not None and abs(moved['x'] - target[0]) < 64 and abs(moved['y'] - target[1]) < 64

    game.wait('the builder to reach its target', lambda: arrived(0))
    if not arrived(1):
        raise TestFailed('the second client has the builder somewhere else')


def test_settings_command_and_debug_mode(game):
    """The host's settings command (-s debug 100) sets Debug mode: no wave counts down on its own,
    -start starts the current one, and when it is over the game stays on it, with no next wave on
    the way. A race is picked, since a wave only comes to lanes with one."""
    maul.start(game)
    maul.pick(game, RED, HUMAN_TOWN_HALL)
    first = maul.fresh_beat(game)
    time.sleep(5)
    later = maul.fresh_beat(game)
    if later.get('timer') != 0 or later.get('spawning') is not False:
        raise TestFailed(f'a wave is on its way in Debug mode: timer {first.get("timer")} then {later.get("timer")}')
    maul.start_wave(game)
    wave = later.get('wave')
    game.wait_for('creeps to come', lambda b: (b.get('creeps') or 0) > 0, timeout=30)
    game.wait_for('the wave to be over', lambda b: b.get('spawning') is False, timeout=300)
    time.sleep(5)
    after = maul.fresh_beat(game)
    if (after.get('wave'), after.get('timer'), after.get('spawning')) != (wave, 0, False):
        raise TestFailed(f'after the wave: wave {after.get("wave")} (was {wave}), timer {after.get("timer")}, '
                         f'spawning {after.get("spawning")}')


# The creep abilities (src/World/Entity/CreepAbilities in the map repo), by ability id
CREEP_ABILITIES = {'A069': 'Hardened Skin', 'A06A': 'Evasion', 'A06C': 'Armor Bonus', 'A08G': 'Cripple Aura',
                   'A00D': 'Spell Shield', 'A01S': 'Tornado Aura', 'A0B3': 'Vampiric Aura', 'A01E': 'Divine Shield',
                   'A01T': 'Walk It Off', 'A06D': 'Morning Person'}
# The players the waves spawn for (Navy, Turquoise, Violet, Wheat)
CREEP_PLAYERS = (13, 14, 15, 16)


def creeps(game):
    return [unit for player in CREEP_PLAYERS for unit in game.units(player)]


def test_creep_abilities_by_difficulty(game):
    """Each wave's creeps get one random creep ability per 100% of difficulty above 100% (none at
    100%, three at 400%), and a boss wave all ten. The difficulty is changed between waves with the
    dev build's -diff, and each wave is cleared away before the next."""
    maul.start(game)
    maul.pick(game, RED, HUMAN_TOWN_HALL)
    game.cmd(RED, '-lives 1000000')
    problems = []
    # Wave 2 is a ground wave (an air one never gets Divine Shield); 35 is the first boss
    for difficulty, wave, wanted in ((100, 2, 0), (200, 2, 1), (300, 2, 2), (400, 2, 3), (200, 35, len(CREEP_ABILITIES))):
        game.cmd(RED, f'-diff {difficulty}')
        game.cmd(RED, f'-wave {wave}')
        known = {unit['id'] for unit in creeps(game)}
        maul.start_wave(game)
        # Rows spawn half a second apart: wait until the count holds
        counts = []
        game.wait_for(f'wave {wave} to finish spawning',
                      lambda b: counts.append(b.get('creeps') or 0) or (len(counts) >= 4 and counts[-1] > 0
                                                                      and len(set(counts[-4:])) == 1), timeout=90)
        spawned = [unit for unit in creeps(game) if unit['id'] not in known]
        if not spawned:
            problems.append(f'{difficulty}%: wave {wave} spawned no creeps')
            continue
        # One set per wave: every creep has the same abilities, so a few are looked at
        sets = []
        for unit in spawned[:3]:
            levels = game.inspect(unit['id'], *CREEP_ABILITIES)
            sets.append(sorted(CREEP_ABILITIES[a] for a in CREEP_ABILITIES if levels.get(a, 0) > 0))
        game.log(f'{difficulty}% wave {wave}: {len(sets[0])} abilities: {", ".join(sets[0]) or "none"}')
        if any(got != sets[0] for got in sets):
            problems.append(f'{difficulty}% wave {wave}: the creeps differ: {sets}')
        if len(sets[0]) != wanted:
            problems.append(f'{difficulty}% wave {wave}: {len(sets[0])} abilities ({", ".join(sets[0]) or "none"}), '
                            f'wanted {wanted}')
        # The wave ends when its last creep dies
        game.cmd(RED, '-killall')
        game.wait_for(f'wave {wave} to be over', lambda b: b.get('spawning') is False, timeout=60)
    if problems:
        raise TestFailed(f'{len(problems)} problem(s)\n  - ' + '\n  - '.join(problems))
