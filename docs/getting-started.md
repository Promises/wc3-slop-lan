# Getting started

## 1. What you need

- **macOS**, with Warcraft III installed in `/Applications/Warcraft III`, at version 3.0.0.24268.
  Other builds are refused, because activation depends on the exact game binary; see
  [protocol.md](protocol.md).
- **Python 3.11 or later.** The harness uses only the standard library.
- **Rust** ([rustup.rs](https://rustup.rs)) and Xcode's command line tools, to build the host.
  StormLib is compiled from C along the way.
- **The debugger allowed to attach to the game.** It is `lldb`, from Xcode's command line tools.
  If activation reports that it can't attach, `sudo DevToolsSecurity -enable` is the usual fix.

Nothing needs Battle.net. The clients start with `-editor`, which skips the login, and go offline.

## 2. Check

```sh
./slop -c examples/any-map/slop.toml check
```

`check` lists what's there and what's missing, and changes nothing. With
`SLOP_MAP=/path/to/map.w3x` set, it also reads the map: its human slots, and where an active
host's seat would go.

## 3. A first run

```sh
SLOP_MAP=/path/to/map.w3x ./slop -c examples/any-map/slop.toml test
```

For each test this:

1. stages the map;
2. starts the web UI server;
3. puts the harness's page into the game;
4. launches two clients and takes them offline;
5. starts the host;
6. switches both clients to LAN and joins them;
7. waits for the game to start;
8. runs the test;
9. checks for desyncs;
10. stops everything it started.

Each client takes about a minute to come up. Artifacts go to `examples/any-map/.slop/<time>-<test>/`:
the host's log, and each client's trace when the map is a Lua map.

> The harness refuses to start while any Warcraft III is running. Close it first. It never
> kills a game it didn't start.

## 4. Your own map

Copy `examples/any-map/slop.toml` next to your map project and edit it (see
[config.md](config.md)):

- `map.file`: your build output; `map.build`: how to build it (`./slop test --build`).
- `host.mode = "active"`, if tests should send commands through the host's seat. The seat is a
  player in a slot the map doesn't define (`seat = "auto"`). If the map's code loops over every
  slot, make it skip `Slop.seat` (see [library.md](library.md)).
- `tests.file`: your tests; see [harness.md](harness.md).

Then `./slop test`, or `./slop up` to play along by hand.

## 5. Going further

- Give your map hooks, so tests can use its own commands and read its own state:
  [library.md](library.md), and `examples/warcraft-maul` for a full set.
- Let an AI agent play: `./slop mcp`, see [harness.md](harness.md#mcp).
