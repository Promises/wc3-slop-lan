"""Tests that make sense for any map: it loads for everyone and stays in step."""
from slop import NeedsLibrary, TestFailed


def test_plays_in_step(game):
    """Two clients play a minute without parting (the harness checks for desyncs after every test)."""
    game.wait('a minute of play', lambda: int(game.control('status').split('ticks=')[1].split()[0]) > 2000,
              timeout=90)


def test_library_heartbeat(game):
    """With the library in, every client writes the same heartbeat as the game goes on."""
    if not game.library:
        raise NeedsLibrary('the map carries no library (a JASS map, or library.inject is off)')
    first = game.beat(0)
    if first is None:
        raise TestFailed('the library is in, but no heartbeat was written')
    game.wait_for('ten seconds of game time', lambda beat: beat['tick'] >= first['tick'] + 100)
