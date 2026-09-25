# The host

`host/`, Rust, built on W3Champions' Flo crates. `cargo build` in `host/`; the harness builds it
on first use.

```
wc3-slop-lan host --map <file> [options] <player name>...
wc3-slop-lan inject --map <file> --out <file> [--seat <slot|auto>] [--prefix <prefix>]
wc3-slop-lan map <file>
```

## host

Hosts one LAN game for clients on this machine.

- **Players.** Each named player takes the next human slot the map defines. Computer slots stay
  as the map has them.
- **Teams.** From the map's forces when it has custom forces; otherwise one team per player, as
  in melee.
- **Start.** The game starts once every named player is in and has confirmed the map: a
  6 second countdown, then loading.

| option | |
|---|---|
| `--host active\|hidden` | *hidden* (default) takes no slot and only relays. *Active* plays a seat of its own, the way gHost++'s fake player does, and can act in the game |
| `--seat <slot\|auto>` | the active host's slot (0-based); `auto` is the first slot the map leaves undefined |
| `--seat-name <name>` | the seat's player name, `HOST` by default |
| `--prefix <prefix>` | the sync prefix the map library listens on, `slop` by default |
| `--map-path <path>` | the map as the game names it; defaults to `maps\...` for a file under a `Maps` folder |
| `--name <game>` | the game name clients join; defaults to the map's file name |
| `--control <port>` | the control port, 8778 by default |
| `--break-map-check <field>`, `--xoro <hex>` | diagnostics: break one map check field on purpose, or send another checksum |

**Why a seat.**
- A client drops actions filed under its own player that it didn't send itself.
- The engine ignores actions from observers.

So the host can only act as a real player that no client controls. gHost++'s other trick, a
"virtual host" with no slot, is deleted before the countdown ends and can't act in the game at
all.

### What it checks

- **Desyncs.** Every tick each client answers with a checksum of its game state; the host
  compares them and logs `DESYNC at tick N: p1=... p2=...` when they differ.
- **Stalls.** It holds the game while a client is more than 50 ticks behind, as gHost++ does,
  and logs that it's waiting. It sends no lag screen.

### Control

Send one line to `127.0.0.1:<control port>`; you get one line back. `echo status | nc -w 2 127.0.0.1 8778`.

| command | |
|---|---|
| `status` | `Playing ticks=1234 up=80s seat=17 \| p1 red conn=true map=true loaded=true left=false \| ...` |
| `cmd <map player> <line>` | the library runs the line as that player (0-based slot) on every client. Active host only |
| `type <text>` | a MapTriggerChatCommand action (0x60) from the seat: the map's chat triggers fire as if the seat's player typed it. Active host only, no library needed. **Unverified:** clients put two ids in front of the text, and the host sends zeros there |
| `chat <text>` | a chat line everyone sees, from the seat (or the first player). Display only |
| `say <player id> <text>` | a line only that player sees |
| `sync <player id> <prefix> <data>` | any SyncData action, as that player |
| `raw <player id> <hex>` | any action bytes, as that player |

- **Player ids** (`say`, `sync`, `raw`) are the protocol's: slot + 1.
- **Map players** (`cmd`) are 0-based slots.
- **Acting as a client's own player** (`sync`, `raw`) desyncs that client, which drops the
  action while the others apply it. Use the seat.

## inject

```sh
wc3-slop-lan inject --map MyMap.w3x --out staged.w3x --seat auto
```

Copies a Lua map with the library in its script, after a config line naming the seat. The
library is built into the binary, so nothing else is needed.

- **Running it again** on the output replaces the block instead of adding a second one.
- **JASS maps** are refused: the library is Lua.
- **Checksums:** the copy has different checksums from the original, so host the copy, and put
  that same copy in every client's Maps folder.

## map

What the host reads from a map:
- its path in the game, size and checksums;
- the slot layout byte;
- its players and forces;
- the free slot `--seat auto` would take;
- whether it carries the library;
- its files.
