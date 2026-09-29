"""Tests that need three players, run with three-players/slop.toml: red and blue are the clients,
and teal is the harness's host, seated in a lane, which the map takes as one more defender. Teal
only ever acts through commands the tests send as it."""
import maul
from maul import BLUE, RED
from slop import TestFailed

TEAL = 2


def test_votekick_by_majority(game):
    """Red starts a votekick against blue and teal votes yes: 2 votes of 3 players is a majority,
    so blue is kicked. (The old rule wanted players / 2 + 1 = 2.5 votes, which the 2 who may vote
    never reach.)"""
    maul.start(game)
    before = len(game.events('votekick'))
    game.cmd(RED, '-votekick blue')
    game.cmd(TEAL, '-y')
    kicked = f' votekick p{BLUE} kicked'

    def counts():
        return [line for line in game.events('votekick')[before:] if 'needed=' in line]
    try:
        game.wait('blue to be kicked', lambda: any(kicked in line for line in game.events('votekick')[before:]), 20)
    except TestFailed:
        # With teal not a player (the host not seated in a lane) its vote never counts
        raise TestFailed(f'blue was not kicked; the votekick counted {counts()[-1] if counts() else "nothing"}')
    if 'needed=2 ' not in counts()[-1] or 'players=3' not in counts()[-1]:
        raise TestFailed(f'the votekick counted {counts()[-1]}; 2 of 3 players are needed')
