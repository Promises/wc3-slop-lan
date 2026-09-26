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
