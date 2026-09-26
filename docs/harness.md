# The harness

Python 3.11+, no dependencies. It drives everything from a [slop.toml](config.md): it stages
the map (with the library in, for Lua maps), starts the web UI server and puts its page in the
game, launches the clients offline, starts the host, switches each client to LAN, joins them,
and waits until the game is on.

## The `slop` command

```
./slop [-c slop.toml] <command>

maps                   the maps under maps/, and which is the default
check [map]            is everything in place? (changes nothing)
up [map] [--build]     start a game and leave it running
test [map] [--build] [--fresh] [--only NAME ...]
                       run the map's tests, each on a fresh game
mcp [map]              serve the harness to an MCP client over stdio

status                 the host's view: phase, ticks, seat, each player
cmd <player> <line>    run a command line as a player (0-based slot) on every client
type <text>            type a chat line as the host's seat
ctl <line>             any host control command (docs/host.md)
file <client> <line>   a command through a client's file channel, as that client's own player
state [client]         the newest heartbeat, as JSON
trace [client] [n]     the last n trace lines
down                   stop it
```

- **Which map:** `[map]` is a folder under `maps/`. Left out, it's the default map from
  `configuration.toml`; `-c` takes any `slop.toml` instead (see [config.md](config.md)).
- **`--build`** runs the map's `map.build` first.
- **The running game:** `up` records it in `.slop/session.json` at the repo root, with the map
  it plays. The commands below `mcp` work on that game, whatever map it is, and `down` ends it.

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
  The second client has a user folder of its own (under `second_home`) whose `Maps/<map.folder>`
  links there.

## Writing tests

A tests file is plain Python: every `test_*` function is a test. `tests.file` may name one file or
a list, and a helper module beside them can be imported by all of them (`maps/warcraft-maul/maul.py`). Each one gets a fresh game
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
`library.inject = false`). Run one test with `./slop test <map> --only gold_reaches_everyone`.

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
| `upgrade(player, unit, type)` | upgrades a building |
| `create(player, type, x, y, count=1, life=None, frozen=False, rooted=False)` | makes units for a player (targets, creeps): `[{id, type, x, y}]`; life is set and the units frozen (paused) or rooted (can't move, still act) as they are made |
| `watch_casts(player)` | traces every spell that player's units cast from now on; `casts()` reads them: `[{src, srctype, ability, dst}]` |
| `watch(player)` | traces every hit on that player's units from now on; `hits()` reads them (with `raw`, the amount before armor) |
| `remove(player, unit)` | takes one of the player's units out of the game, without a death |
| `control(line)` | any host command; returns its one-line reply |

| reading | |
|---|---|
| `beat(client=0)` | the newest heartbeat: `beat['tick']`, the map's keys (`beat['wave']`), `beat.gold(slot)`, `beat.lumber(slot)`, `beat.player(slot, key)` |
| `trace(client=0)` | every trace line so far |
| `events(kind, client=0)` | trace lines of one category (`unit`, `order`, `slop`, or the map's own) |
| `inspect(unit, *abilities)` | a unit now: `type`, `owner`, `x`, `y`, `order`, `life`, `max_life`, `mana`, `damage_min`/`max`, `cd`, `range`, `armor`, `speed`, and each ability's level |
| `hits(client=0)` | the watched hits: `[{src, srctype, dst, amount, raw, atk, attack}]` |
| `units_later(...)`, `create_later(...)`, `inspect_later(...)`, `beat_after(client=0)` | the same requests without waiting: a `Pending` with `ready()` and `result()`, so a test can keep several going at once (Warcraft Maul's race tests build for two players side by side this way) |
| `finish(pending, timeout=15)` | waits for a `Pending` and returns its result, or fails |
| `wait_for(what, predicate, timeout=30, client=0)` | waits until `predicate(beat)` holds, or fails |
| `wait(what, condition, timeout=30)` | waits until `condition()` holds, or fails |
| `check_in_step()` | fails on a desync or differing traces (runs after every test anyway) |
| `screenshot(client=0)` | a PNG of that client's window, by window id (needs [yabai](https://github.com/koekeishiya/yabai) to find the window) |
| `library`, `seat`, `artifacts` | whether the map carries the library; the host's seat (or None); where this run's files go |

### The Windows build (Linux + Wine)

When the game binary is a `.exe`, the harness can't install its page (that build's menus come
from its packed data). Instead it keeps [slop-activator](../activator/) running (started with the
same launcher as the games, unless one runs already) and starts `harness/webui/bridge.py` for
each game: the bridge reads the menus' port and guid from the activator's instance file and plays
the page's part toward the web UI server, so everything else here works unchanged. Before each
LAN search the bridge asks the activator for a fresh switch. `PlayOffline` is not sent (it starts
a Battle.net sign-in on this build). One client for now. See [porting.md](porting.md#3-linux--wine).

### Players, clients and slots

- **A player** is a 0-based slot: 0 is red, 1 is blue. Named players take the map's human slots
  in order.
- **A client** is 0 or 1: the game playing the first or the second player's slot, in slot
  order. Each game has a user folder of its own (the second runs with its home at
  `second_home`), and the library names each game's files by the player it plays. While the game
  is in step, both clients hold the same trace, so client 0 is usually enough.
  (`screenshot(client)` goes by launch order instead, which usually matches.)

## MCP

`./slop mcp [map]` serves the harness over stdio, for an AI agent to play the map. For Claude
Code, in the map's repo `.mcp.json`:

```json
{
  "mcpServers": {
    "wc3-slop": {
      "command": "../wc3-slop-lan/slop",
      "args": ["mcp", "<map>"]
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
