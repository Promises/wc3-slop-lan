"""Tests that make sense for any map: it loads for everyone and stays in step."""
from slop import TestFailed


def test_plays_in_step(game):
    """Two clients play a minute without parting (the harness checks for desyncs after every test)."""
    game.wait('a minute of play', lambda: int(game.control('status').split('ticks=')[1].split()[0]) > 2000,
              timeout=90)


def test_library_heartbeat(game):
    """With the library in, every client writes the same heartbeat as the game goes on."""
    first = game.beat(0)
    if first is None:
        raise TestFailed('no heartbeat: is this a Lua map with library.inject on?')
    game.wait_for('ten seconds of game time', lambda beat: beat['tick'] >= first['tick'] + 100)
