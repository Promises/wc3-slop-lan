# By hand: two clients in a game on the host

This guide goes from nothing running to two Warcraft III clients playing a map on the host,
with commands sent into the game from outside. `./slop up` does all of this by itself; do it by
hand to see each piece work, or to find which one doesn't.

Every command below uses these variables:

```sh
SLOP=~/path/to/wc3-slop-lan
MAP=~/path/to/MyMap.w3x
GAME="/Applications/Warcraft III/_retail_/x86_64/Warcraft III.app/Contents/MacOS/Warcraft III"
MAPS="$HOME/Library/Application Support/Blizzard/Warcraft III/Maps"
BIN="$SLOP/host/target/debug/wc3-slop-lan"
WC3="$SLOP/harness/wc3.sh"
```

## 1. Build the host, stage the map

```sh
(cd "$SLOP/host" && cargo build)
mkdir -p "$MAPS/slop"
"$BIN" inject --map "$MAP" --out "$MAPS/slop/MyMap.w3x" --seat auto   # a Lua map, with the library
# or, for a JASS map or no library:  cp "$MAP" "$MAPS/slop/MyMap.w3x"
"$BIN" map "$MAPS/slop/MyMap.w3x" | head -3
```

`--seat auto` picks the first slot the map doesn't define. `map` prints it as `free slot`, and
the host must use the same one (step 5).

## 2. One data folder for both

Both clients use your normal data folder (`~/Library/Application Support/Blizzard/Warcraft III`),
so both see the staged map. The library keeps their files apart by naming each file after the
player that client plays: `slop-trace-p0-0001.txt`, `slop-beat-p1.txt`.

## 3. The page and its server

```sh
WEBUI="/Applications/Warcraft III/_retail_/webui"
[ -f "$WEBUI/index.html.before-slop" ] || cp "$WEBUI/index.html" "$WEBUI/index.html.before-slop"
cp "$SLOP/harness/webui/index.html" "$WEBUI/index.html"
```

The first line keeps the page that was there, once. If the page there is already ours, don't
keep it: remove the backup afterwards.

In a terminal of its own:

```sh
python3 "$SLOP/harness/webui/server.py" /tmp/webui.log 8777
```

Then clear the numbering, so the next game is instance 1:

```sh
"$WC3" reset
```

## 4. Two clients, and their instance numbers

An **instance number** is the order in which a game's page checked in with the server since
the last reset. A game that restarts gets a new number, so reset when you start over, and start
the clients one at a time so you know which is which.

```sh
"$GAME" -editor -launch -windowmode windowed -nowfpause > /dev/null 2>&1 &
"$WC3" who          # wait for number 1; the key is the game's web UI port
"$GAME" -editor -launch -windowmode windowed -nowfpause > /dev/null 2>&1 &
"$WC3" who          # wait for number 2
"$WC3" raw 1 PlayOffline '{}'
"$WC3" raw 2 PlayOffline '{}'
```

- `-editor` skips the Battle.net login.
- `-nowfpause` keeps an unfocused game running.
- After `PlayOffline` the screen stays on `LOGIN_DOORS`; that's expected.

**A client's pid** is the process listening on its instance's port:
`lsof -ti tcp:<port> -sTCP:LISTEN`. `activate.sh` looks it up for you.

## 5. The host

In a terminal of its own:

```sh
"$BIN" host --map "$MAPS/slop/MyMap.w3x" --name slop-manual --host active --seat auto red blue
# or, taking no slot:   "$BIN" host --map "$MAPS/slop/MyMap.w3x" --name slop-manual red blue
echo status | nc -w 2 127.0.0.1 8778
# Lobby ticks=0 up=3s seat=14 | p1 red conn=false ... | p2 blue conn=false ...
```

## 6. Switch each client to LAN, and join

One client at a time: activate it, then join it.

```sh
bash "$SLOP/harness/activate.sh" 1
# game 12345 switched to its LAN provider; LAN port :16001
"$WC3" raw 1 SendGameListing '{}'
"$WC3" lanjoin 1 slop-manual true

bash "$SLOP/harness/activate.sh" 2
"$WC3" raw 2 SendGameListing '{}'
"$WC3" lanjoin 2 slop-manual true
```

- **Keep the `true`** (keep the provider): without it the join undoes the switch.
- **Activate again before every join.** The switch only lasts until the provider changes.

The host starts the game once both are in: a 6 second countdown, then loading.
`echo status | nc -w 2 127.0.0.1 8778` shows `Playing` once it's on.

## 7. Drive it

```sh
ctl() { echo "$*" | nc -w 2 127.0.0.1 8778; }
ctl cmd 0 .gold 5000           # red gets 5000 gold (the library's built-in)
ctl cmd 0 .units               # red's units, into the trace
ctl type -help                 # the seat types "-help" (map chat triggers; unverified)
ctl chat hello                 # a line on everyone's screen
```

The traces are in `~/Library/Application Support/Blizzard/Warcraft III/CustomMapData/`, one per
player: `slop-trace-p0-*.txt` from red's game, `slop-trace-p1-*.txt` from blue's. If the clients
part ways, the host logs `DESYNC at tick N`.

With a hidden host, commands go through a client's file channel instead, as the player that
game plays: `"$WC3" cmd 1 p0 .gold 5000` (both games poll the same folder, so name the player).

## 8. Stop

```sh
pkill -f wc3-slop-lan
pkill -9 -f 'MacOS/Warcraft III'       # plain pkill leaves the game hanging
```

Stop the server with Ctrl-C. To put the game's own page back:
`mv "$WEBUI/index.html.before-slop" "$WEBUI/index.html"`.

## When it goes wrong

| symptom | why, and what to do |
|---|---|
| `activate.sh`: no instance N | the client hasn't checked in, or its number moved after a restart: `"$WC3" who`, or reset and relaunch |
| `activate.sh`: lldb can't attach | the debugger isn't allowed: `sudo DevToolsSecurity -enable` |
| `activate.sh` refuses the build | the game updated; the breakpoint address has to be found again for the new build |
| the join never finds the game | activate again (the switch was reset), check the name, and check `lsof -nP -iUDP:16000` shows the host |
| host: "does not have this map" | the client's copy differs, or its path does: both must see the *same staged file* |
| the client quits at the countdown | the slot layout doesn't match the map; `"$BIN" map` shows the layout it reads |
| `cmd` does nothing | the host is hidden, the map has no library (JASS, or not injected), or the game isn't `Playing` yet |
| a client is stuck after a game | `pkill -9` it and start again from step 4 |
