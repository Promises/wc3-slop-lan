# The map library

`library/slop.lua` is one Lua file that lets a test drive a running map and read it back.

- **How it gets in.** The harness writes it into a *copy* of the map when a run starts
  (`wc3-slop-lan inject`). It goes at the end of the map's script, or just before a final
  top-level `return` (TypeScript-to-Lua bundles end with one).
- **How it starts.** It wraps the map's `main`, so it starts right after the map's own start-up.
  Your builds never contain it.
- **Lua maps only.** A JASS map can't run it; the host still plays such a map and checks it for
  desyncs.

## What it does

- **Two ways in.** Both run a command line *as a player*, on every client alike, so the game
  stays in step:
  - **from the host's seat**: sync data under the prefix (`slop`), as `"<player> <line>"`. Only
    the seat is listened to. This is what `slop cmd` uses, and it needs an active host;
  - **from a file**: a client polls its own CustomMapData for command files and sends what it
    finds as its own player. This works with a hidden host, or with no host at all (a Battle.net
    game). `slop file` writes these files.
- **One way out: the trace.** Numbered chunks `slop-trace-p<slot>-NNNN.txt` in CustomMapData,
  flushed every second. Every file the library writes or reads carries the slot of the player
  that client plays, so two clients can share a data folder. The file names differ between
  clients by design; what's in the files doesn't. Categories marked urgent are flushed at once, because a
  desync drops the client within the second.

A trace line is:

```
<sequence> t<ticks> h<handles> <category> <text>
```

- `ticks` counts tenths of a second of game time.
- `handles` is roughly how many handles the client has made. It's a hint when hunting a desync,
  not proof of one. Local-only code (UI frames, effects only one player sees) makes and frees
  handles on one client only, so clients that are perfectly in step can differ here. The
  harness leaves it out when it compares clients; the host's per-tick checksum is what decides.

Every second the library writes a heartbeat. Here is one, with Warcraft Maul's hooks adding
`lives`, `wave`, `k` (kills) and `t` (towers):

```
812 t300 h53120 beat handles=53120 cmdpoll=61 lives=100 wave=2 p0(g=500 l=0 k=12 t=4) p1(g=250 l=0 k=9 t=3)
```

Each playing human (not the seat) gets a `p<slot>(...)` part, with gold (`g`) and lumber (`l`)
plus whatever the map's hooks add.

## Built-in commands

They start with a dot, so they can't clash with a map's own chat commands.

| | |
|---|---|
| `.units` | the player's units into the trace: `unit p0 id=1049 type=h000 at=512,-256 life=420 order=move`, then `unit p0 end` |
| `.order <unit> <order>` | an immediate order (`stop`, `holdposition`) to one of the player's own units, by handle id |
| `.order <unit> <order> <x> <y>` | a point order (`move`, `attack`, `patrol`) |
| `.order <unit> <order> <target>` | a target order at another unit, by handle id |
| `.build <builder> <type> <x> <y>` | a build order; the type as its four letters |
| `.gold <n>`, `.lumber <n>` | set the player's resources |
| `.end` | ends the game for everyone, to the score screen: every client runs it from the same sync event. The harness uses it to reuse the clients for the next test |

- **Results** go to the trace, in the category `order`, as `... issued` or `... rejected`.
- **Own units only:** a player can only order their own units, as with a real selection.
- **Handle ids** are the same on every client of one game, so an id from `.units` works for
  `.order`.
- **Anything else** goes to the map's hooks. If no hook takes the line, it is noted as
  `slop p0 unhandled`.

## Hooks for your map

A map adds its own commands and state through hooks, always behind a guard. The guard costs a
nil check in a normal game:

```lua
if Slop then
    Slop.onCommand(function(player, line)        -- player: 0-based slot
        if line:sub(1, 1) == '-' then
            MyCommands.run(Player(player), line)
            return true                           -- handled
        end
        return false                              -- let the next hook, or "unhandled", have it
    end)
    Slop.heartbeat(function() return 'wave=' .. CurrentWave end)
    Slop.playerFields(function(player) return 'score=' .. Score[player] end)
    Slop.urgent('wave', 'boss')                   -- flush these categories at once
end
```

Anywhere in the map's code, `Slop.note(category, text)` writes an event to the trace.

Register hooks during the map's start-up (in `main` or anything it calls). The library is
defined before any of it runs, and starts right after the map's own `main` body. In a w3ts map
that is before the `MAIN_AFTER` hooks run, which is fine: hooks are read when they're used, not
when the library starts.

### TypeScript

`library/slop.d.ts` declares the global `Slop`. Copy it into the map's sources:

```ts
if (Slop !== undefined) {
    Slop.onCommand((player, line) => commands.run(player, line));
}
```

The functions are plain table fields, so they are declared `this: void`: TSTL calls them with a
dot, without passing `self`.

[`maps/warcraft-maul`](../maps/warcraft-maul) is a full set. The hooks that example
relies on live in Warcraft Maul's `src/World/Game/SlopHooks.ts`. They:

- run chat commands as any player (`-gold 500`);
- run PlayerSync messages as any player (`@race-pick:I00W`): what the player's own UI would
  have sent, so tests can pick, build and vote without clicking;
- add the game's state to the heartbeat.

## The seat

An active host plays a seat: a player in a slot that no client controls. Its commands come from
that player. The library knows which slot it is:

```lua
if Slop and slot == Slop.seat then -- not a real player: give it no base, no lane, no score
```

With `seat = "auto"` the seat takes the first slot the map doesn't define, which most maps never
look at. A map that loops over every slot and sets up whatever is playing should skip it.
Warcraft Maul does this where it seats its defenders.

## Rules for anything that writes to the trace

- **Only simulation state.** Every client must write exactly the same lines while the games
  agree. No `GetLocalPlayer`, no wall clock, no camera or UI state. Anything local makes the
  traces differ by design, and hides the real divergence.
- **Hooks must be deterministic** for the same reason. A hook that fails is caught and noted
  (`slop ... failed: <error>`); it doesn't break the game.

## Config

The injector writes one line above the library:

```lua
SLOP_CONFIG = {seat = 17, prefix = "slop"}
```

The library also reads `files = false` (turns off the file channel), `trace` (the trace's file
name prefix) and `autostart = false` (the map calls `Slop.start()` itself).

## Testing it

`lua library/test.lua` runs the library outside the game, against stand-ins for the natives it
calls. It checks the channels, the built-ins, the hooks and the trace. The game runs Lua 5.3;
the library uses nothing newer.
