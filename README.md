# wc3-slop-lan

*SLOP: Self-hosted LAN Offline Play.*

Automated testing for Warcraft III maps, with real game clients on your own machine. No mouse,
keyboard or window focus is needed, so the machine stays usable while tests run.

- **A LAN host** (`host/`, Rust). Two local clients join it like any LAN game. It relays the
  match, checks every tick that the clients agree (desyncs), and can play a seat of its own to
  send actions into the game.
- **A map library** (`library/slop.lua`). It is written into a *copy* of a Lua map when a run
  starts; no build of the map has to contain it. It runs commands as any player (unit orders,
  resources, and whatever the map adds through hooks) and writes a lockstep trace that tests
  read the game back from.
- **A harness** (`harness/`, Python, no dependencies). Configured by one `slop.toml`, it builds,
  stages, launches, joins, drives and checks. You can use it from the command line (`./slop`),
  from Python tests, or as an MCP server for an AI agent.

It works on any map, at three levels:

| map | what you get |
|---|---|
| any map, host hidden | two clients play it on the host; every tick is checked for desyncs |
| a Lua map | plus the trace, and commands through the library: `.units`, `.order`, `.build`, `.gold`, `.lumber` |
| a Lua map with hooks | plus the map's own commands and state: see [Warcraft Maul](examples/warcraft-maul) |

## Quick start

```sh
./slop -c examples/any-map/slop.toml check          # what is missing, if anything
SLOP_MAP=~/maps/MyMap.w3x ./slop -c examples/any-map/slop.toml test
```

Or write your own `slop.toml` (see [docs/config.md](docs/config.md)) and run:

```sh
./slop up                    # start a game and leave it running
./slop cmd 0 .gold 5000      # red gets 5000 gold, on every client
./slop state                 # the newest heartbeat: gold, lumber, the map's own keys
./slop down
```

**Needs:**
- macOS, with Warcraft III 3.0.0.24268 (see *Limits*);
- Python 3.11+;
- Rust, to build the host (the first run builds it);
- a second data folder for the second client (`~/BattleNet-alt` by default; the harness links
  the map into it itself).

## Docs

| | |
|---|---|
| [getting-started.md](docs/getting-started.md) | set up, check, first run |
| [config.md](docs/config.md) | every `slop.toml` key |
| [harness.md](docs/harness.md) | the `slop` command, writing tests, the Python API, the MCP server |
| [library.md](docs/library.md) | what the map library does, its commands, hooks for your map, the trace |
| [host.md](docs/host.md) | the host binary: active or hidden, control commands, `inject`, `map` |
| [manual.md](docs/manual.md) | every step by hand, no harness: useful to see each piece work |
| [protocol.md](docs/protocol.md) | how it works underneath: discovery, map check, activation |

## Layout

```
slop                 the harness's command line
host/                the host (Rust): hosting, injecting the library, reading maps
library/             slop.lua, its TypeScript declarations, and an offline test (lua library/test.lua)
harness/             the Python harness (slop/), the web UI page and its server (webui/),
                     activate.sh (switches a client to LAN), wc3.sh (drive the menus by hand)
examples/any-map/    the least any map needs
examples/warcraft-maul/  a full example: active host, library, and the map's own hooks
diagnostics/         capture and decode tools used to work out the protocol
docs/
```

## Limits

- **macOS only, and one game build.** A stock client only finds LAN games after a debugger
  switches its network provider (the same trick W3Champions uses). `harness/activate.sh` does
  that at a fixed address in 3.0.0.24268, and refuses any other build.
- **The library needs a Lua map.** JASS maps still get hosting and the desync check.
- **Two clients at most,** one per data folder.
- **The map must already be on disk.** The host does not send it to clients.

Built on W3Champions' open Flo crates ([BogdanW3/W3C-Flo](https://github.com/BogdanW3/W3C-Flo)).
The host's seat is modelled on gHost++'s fake player.
