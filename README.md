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
- **A harness** (`harness/`, Python, no dependencies). Configured by a `slop.toml` per map, it
  builds, stages, launches, joins, drives and checks. You can use it from the command line (`./slop`),
  from Python tests, or as an MCP server for an AI agent.

It works on any map, at three levels:

| map | what you get |
|---|---|
| any map, host hidden | two clients play it on the host; every tick is checked for desyncs |
| a Lua map | plus the trace, and commands through the library: `.units`, `.order`, `.build`, `.gold`, `.lumber` |
| a Lua map with hooks | plus the map's own commands and state: see [Warcraft Maul](maps/warcraft-maul) |

## Requirements

- **macOS** with **Warcraft III 3.0.0.24268** in `/Applications/Warcraft III`, installed
  through Battle.net. Tested on Apple Silicon, where the game runs under Rosetta. Other game
  builds are refused; see *Limits*.
- **Xcode command line tools** (`xcode-select --install`): `lldb`, which switches the clients to
  LAN, and the compilers for the host.
- **CMake** (`brew install cmake`), to build StormLib inside the host.
- **Rust** ([rustup.rs](https://rustup.rs)), to build the host.
- **Python 3.11+.** The harness uses only the standard library.

Battle.net doesn't have to run, and no login is needed: the clients start offline.

**Linux + Wine** (the Windows build, one client for now) works too: wine-staging 11.6+ rather
than Proton, and [slop-activator](activator/) in place of `lldb` - download it from the releases
or build it here. Setup and pitfalls: [docs/porting.md](docs/porting.md#3-linux--wine). Real
Windows is untested.

## Install

```sh
git clone git@github.com:Promises/wc3-slop-lan.git
cd wc3-slop-lan
(cd host && cargo build)     # optional: the first `./slop up` or `./slop test` builds it too
```

## Setup

```sh
./slop check                 # what is missing, if anything; changes nothing
```

- **The game somewhere else?** Copy `configuration.example.toml` to `configuration.toml` and fix
  the paths under `[game]`. A standard install needs no `configuration.toml`.
- **Debugger permission:** if the first run reports that `lldb` can't attach to the game, run
  `sudo DevToolsSecurity -enable` once.
- **Close Warcraft III and W3Champions first.** The harness won't start while the game is
  running, and W3Champions replaces the game's menu page with its own.
- **The menu page:** the first run puts the harness's page into the game's `webui` folder. The
  page that was there is kept as `index.html.before-slop`.

## Quick start

```sh
./slop up                    # the default map, (2)Hammerfall_S3, on two clients; left running
./slop status                # the host's view: Playing, both players in
./slop file 0 .gold 5000     # red gets 5000 gold, on every client
./slop state                 # the newest heartbeat: gold, lumber, the map's own keys
./slop down                  # stop it

./slop test                  # or: the map's tests, each on a fresh game
```

- **Maps** live in `maps/<name>/`: a `slop.toml`, `tests.py`, and the map file. `./slop maps`
  lists them; `./slop up warcraft-maul` picks one. To test your own map, add a folder like
  `maps/any-map` with your map in it.
- **This machine's settings** (the default map, how to run the game) go in
  `configuration.toml`.

See [docs/getting-started.md](docs/getting-started.md) and [docs/config.md](docs/config.md).

## Docs

| | |
|---|---|
| [getting-started.md](docs/getting-started.md) | set up, check, first run |
| [config.md](docs/config.md) | maps, every `slop.toml` and `configuration.toml` key |
| [harness.md](docs/harness.md) | the `slop` command, writing tests, the Python API, the MCP server |
| [library.md](docs/library.md) | what the map library does, its commands, hooks for your map, the trace |
| [host.md](docs/host.md) | the host binary: active or hidden, control commands, `inject`, `map` |
| [manual.md](docs/manual.md) | every step by hand, no harness: useful to see each piece work |
| [protocol.md](docs/protocol.md) | how it works underneath: discovery, map check, activation |
| [porting.md](docs/porting.md) | how to get it working on a new game version, Windows, or Linux + Wine |

## Layout

```
slop                 the harness's command line
configuration.example.toml  this machine's settings: copy to configuration.toml to change them
host/                the host (Rust): hosting, injecting the library, reading maps
library/             slop.lua, its TypeScript declarations, and an offline test (lua library/test.lua)
harness/             the Python harness (slop/), the web UI page and its server (webui/),
                     activate.sh (switches a client to LAN), wc3.sh (drive the menus by hand)
maps/any-map/        the default map: (2)Hammerfall_S3 with a hidden host, the least any map needs
maps/warcraft-maul/  a full example: active host, library, the map's own hooks, its build elsewhere
diagnostics/         capture and decode tools used to work out the protocol
docs/
```

## Limits

- **macOS only, and one game build.** A stock client only finds LAN games after a debugger
  switches its network provider (the same trick W3Champions uses). `harness/activate.sh` does
  that at a fixed address in 3.0.0.24268, and refuses any other build. See
  [porting.md](docs/porting.md) for how to find it again, and how Windows and Wine differ.
- **The library needs a Lua map.** JASS maps still get hosting and the desync check.
- **Two clients at most,** for now.
- **The map must already be on disk.** The host does not send it to clients.

Built on W3Champions' open Flo crates ([BogdanW3/W3C-Flo](https://github.com/BogdanW3/W3C-Flo)).
The host's seat is modelled on gHost++'s fake player.
