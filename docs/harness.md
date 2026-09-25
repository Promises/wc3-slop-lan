# The harness

Python 3.11+, no dependencies. It drives everything from a [slop.toml](config.md): it stages
the map (with the library in, for Lua maps), starts the web UI server and puts its page in the
game, launches the clients offline, starts the host, switches each client to LAN, joins them,
and waits until the game is on.

## The `slop` command

```
./slop [-c slop.toml] <command>

check                  is everything in place for this config? (changes nothing)
up [--build]           start a game and leave it running
down                   stop it
status                 the host's view: phase, ticks, seat, each player
cmd <player> <line>    run a command line as a player (0-based slot) on every client
type <text>            type a chat line as the host's seat
ctl <line>             any host control command (docs/host.md)
file <client> <line>   a command through a client's file channel, as that client's own player
state [client]         the newest heartbeat, as JSON
trace [client] [n]     the last n trace lines
test [--build] [--fresh] [name]  run the config's tests, each on a fresh game
mcp                    serve the harness to an MCP client over stdio
```

`--build` runs the config's `map.build` first. `up` records the session in `.slop/session.json`
next to the config. The other commands find the running game through that file, and `down`
removes it.

```sh
./slop up
./slop cmd 0 .units          # red's units go into the trace...
./slop trace 0 5             # ...and show up here
./slop cmd 0 .order 1049 move 512 -256
./slop down
```

### What runs where

- **`slop` never kills a game it didn't start.** It refuses to start while Warcraft III is
  running.
- **What it stops:** its own clients and host, and the web UI server if it started one.
- **What it leaves:** its page stays in the game's webui folder. The page that was there first
  is kept as `index.html.before-slop` (`harness/slop/webui.py`).
- **The staged map** goes to `~/Library/Application Support/Blizzard/Warcraft III/Maps/<map.folder>/`.
  The second client's Maps folder gets a link to that folder.

## Writing tests

The tests file is plain Python: every `test_*` function is a test. Each one gets a fresh game
(a `Session`) as its argument and fails by raising. After each test the harness also fails it
if the host saw a desync, or if the clients' traces differ (handle ids aside, see
[library.md](library.md#what-it-does)).

```python
from slop import TestFailed

def test_gold_reaches_everyone(game):
    """A command runs as the player on every client."""
    game.cmd(0, '.gold 1717')
    game.wait_for('red to have the gold', lambda beat: beat.gold(0) == 1717)
    if game.beat(1).gold(0) != 1717:
        raise TestFailed('the second client disagrees')
```

A test that uses the library is **skipped**, not failed, when the map has none (a JASS map, or
`library.inject = false`). Run one test with `./slop test gold_reaches_everyone`.

**Games are fresh, clients are reused.** Launching two clients takes about a minute, so the
clients stay up between tests:
1. The game is ended: `.end` with the library, otherwise the host drops it.
2. The clients land on the score screen, and are sent on from there.
3. The next game is hosted and joined on the same clients.

A client that doesn't get back to the menus is replaced by starting everything over, so a stuck
client costs time, not a test. `--fresh` launches the clients again for every test instead.

### The session

| acting | |
|---|---|
| `cmd(player, line)` | a command line as that player, through the host's seat: the [built-ins](library.md#built-in-commands) or the map's hooks. Needs an active host and the library |
| `type(text)` | a chat line typed by the seat's player; the map's chat triggers fire. No library needed. Unverified in a real game, see [host.md](host.md) |
| `chat(text)` | a line shown to everyone; the map never sees it |
| `file_command(client, line)` | a command as that client's own player, through its file channel. Works with a hidden host |
| `units(player, client=0)` | the player's units: `[{id, type, x, y, life, order}]` |
| `order(player, unit, name, *args)` | `order(0, id, 'move', x, y)`, `order(0, id, 'stop')`, `order(0, id, 'attack', target)` |
| `build(player, builder, type, x, y)` | a build order; the type is its four letters |
| `control(line)` | any host command; returns its one-line reply |

| reading | |
|---|---|
| `beat(client=0)` | the newest heartbeat: `beat['tick']`, the map's keys (`beat['wave']`), `beat.gold(slot)`, `beat.lumber(slot)`, `beat.player(slot, key)` |
| `trace(client=0)` | every trace line so far |
| `events(kind, client=0)` | trace lines of one category (`unit`, `order`, `slop`, or the map's own) |
| `wait_for(what, predicate, timeout=30, client=0)` | waits until `predicate(beat)` holds, or fails |
| `wait(what, condition, timeout=30)` | waits until `condition()` holds, or fails |
| `check_in_step()` | fails on a desync or differing traces (runs after every test anyway) |
| `screenshot(client=0)` | a PNG of that client's window, by window id (needs [yabai](https://github.com/koekeishiya/yabai) to find the window) |
| `library`, `seat`, `artifacts` | whether the map carries the library; the host's seat (or None); where this run's files go |

### Players, clients and slots

- **A player** is a 0-based slot: 0 is red, 1 is blue. Named players take the map's human slots
  in order.
- **A client** is 0 or 1: which game's files to read. While the game is in step, both clients
  hold the same trace, so client 0 is usually enough.

## MCP

`./slop -c <config> mcp` serves the harness over stdio, for an AI agent to play the map. For
Claude Code, in the map's repo `.mcp.json`:

```json
{
  "mcpServers": {
    "wc3-slop": {
      "command": "../wc3-slop-lan/slop",
      "args": ["-c", "path/to/slop.toml", "mcp"]
    }
  }
}
```

**Tools:**

| group | tools |
|---|---|
| running a game | `game_start`, `game_stop`, `status` |
| acting | `cmd`, `type`, `chat`, `units`, `order`, `build` |
| reading | `state`, `events`, `trace_tail`, `check_in_step`, `screenshot` |
| tests | `run_tests` |

One game lives across calls. `game_start` takes a minute or two.
