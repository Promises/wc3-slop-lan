# How it works underneath

The game protocol (W3GS) is Flo's, from W3Champions' open client
([BogdanW3/W3C-Flo](https://github.com/BogdanW3/W3C-Flo)). The host's join burst mirrors
Flo's `crates/client/src/lan/game/lobby.rs`. What follows is what differed, or wasn't written
down, for Warcraft III 3.0.0.24268 on macOS. Captures and decoders for all of it are in
`diagnostics/`.

## Activation: switching a client to LAN

A stock client runs its local provider (`LOOP`) and never searches for LAN games. W3Champions
switches it to the real LAN provider (`TCPN`) with a debugger, and `harness/activate.sh` does
the same:

1. Attach Rosetta's debugserver (through `lldb`) to the running game.
2. Set a one-shot breakpoint at `0x100d3c6a2`, in the game's `InitializeLocalNetProvider`
   handler, just after `mov edi,'LOOP'` (bytes `bf 50 4f 4f 4c e8 09 2b 4f 00` at `0x100d3c69d`).
3. Have the page ask the game to initialise its provider (`InitializeLocalNetProvider` over
   the web UI socket), so the handler runs.
4. At the breakpoint, change `rdi` from `'LOOP'` (`0x4c4f4f50`) to `'TCPN'` (`0x5443504e`).
5. Detach.

- **Nothing is written into the game.** The script checks the binary's UUID
  (`C26F6D81-E702-3F44-B57E-91DD7C438B22`) and those bytes first, and refuses anything else.
- **The switch doesn't last.** It holds until the provider changes again, for example on
  leaving a LAN lobby, so activate before every join.
- **Joining keeps it.** The page's `lanjoin` with `keepProvider` joins without re-initialising
  the provider.

## Discovery

- **Search and answer.** A client on TCPN takes the first free UDP port from 16000 and
  broadcasts `SearchGame` (`f7 2f`, product `PX3W`, version 10200) to port 16000. Whoever holds
  that port answers with `GameInfo` (`f7 30`).
- **What GameInfo holds.** The laid-out settings (map path, xoro checksum, sha1, host name), 24
  slots, flags `0x100000`, the open slots, the uptime and the host's TCP port. The answer goes
  to the client's own port on loopback.
- **When the port is taken.** If something else holds 16000 (W3Champions does, when it runs),
  the host instead announces its game every second to ports 16000–16003.
- **No Bonjour.** Flo's public source publishes a Bonjour record, but the game doesn't list
  those; that record is only W3Champions' own "Local Network Probe".

## The map check

The client compares the map's path, sha1 and xoro checksum, and answers with its own file size.
It doesn't compare the crc32, or the size the host sends.

- **The xoro checksum.** It is Flo's algorithm with one more file. The game's routine
  (`0x100763470`) also folds in `war3map.w3l`, Reforged's lighting file, after `war3map.w3q`.
  Maps without that file get Flo's value.
- **The slot layout byte.** It sits in every slot table: 1 = custom forces, 2 = fixed player
  settings. Warcraft Maul needs 3. Flo's enum can't express both together, so the host writes
  the byte directly. With the wrong byte the client quits at the countdown.

## Joining and playing

**The join burst**, in order:
1. `SlotInfoJoin`, then `SlotInfo`;
2. a `PlayerInfo` for each other player, the seat included;
3. `PlayerSkins` (0x59, type 4) for each other player;
4. `PlayerProfile` (0x59, type 5) for everyone;
5. `MapCheck`.

The client echoes both 0x59 messages and answers the map check with `MapSize`.

**Starting.** Once everyone is in: `CountDownStart`, 6 seconds, `CountDownEnd`. Each client
sends `GameLoadedSelf` when it has loaded. The host then sends it `PlayerLoaded` for the seat,
and sends every client `PlayerLoaded` for the one that just finished.

**The game.** Every 30 ms the host sends each client an `IncomingAction`: the tick's time and
every action collected since the last tick, the seat's included. Each client answers every tick
with `OutgoingKeepAlive`, a checksum of its game state; the host compares these (see
[host.md](host.md)).

**Chat.** `ChatToHost` is relayed to the players it names, as `ChatFromHost`. Chat itself fires
no trigger. When a player types a line in a map with chat events, their client *also* sends a
MapTriggerChatCommand action (0x60: two ids, then the text), and that action is what fires
`EVENT_PLAYER_CHAT`.

## Sync data, the library's way in

`BlzSendSyncData(prefix, data)` becomes action `0x77`: prefix, `\0`, data, `\0`, a u32 zero.

- **From the seat.** The host puts exactly that into the stream as the seat's action; every
  client fires the map's sync event for it, as that player. This is how `cmd` reaches the
  library.
- **Filed under a client's own player,** the same action is dropped by that client and applied
  by the others, which is a desync.

## The web UI page

The game's menus are a web page it serves itself from `<install>/_retail_/webui/index.html`,
talking to the game over a websocket (`ws://127.0.0.1:<port>/webui-socket/<guid>`).

- **What our page is.** `harness/webui/index.html` is the page's skeleton: it loads the game's
  own `GlueManager.js`, which draws the menus. On top is a script that keeps hold of the
  socket, because a second connection of our own is accepted but never answered.
- **How it's driven.** The script checks in with `harness/webui/server.py` and runs what it's
  told: go offline (`PlayOffline`), list games, `lanjoin`, host and join Battle.net lobbies, or
  any raw message.
- **Instances.** The server numbers games in the order they check in. An instance's id is its
  game's web UI port, which is how `activate.sh` finds the game's process.

## The file channel

A preload file (`Preloader(name)`) runs as script. A command file sets an ability tooltip
(`ANcl`), and the library reads it straight back. Two details decide whether this works at all:

- **Its own preload pass.** The read must sit inside `PreloadStart`/`PreloadEnd`, or it does
  nothing once the game is on.
- **Missing names are remembered.** A name asked for while the file doesn't exist is treated as
  missing for good. So the library asks for a new name every poll, publishes the number in
  `slop-beat-p<slot>.txt`, and the writer fills a few names just ahead of it
  (`slop-cmd-p<slot>-NNNN.txt`).
