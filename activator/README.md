# slop-activator

Keeps every Warcraft III on the machine - the **Windows build**, on Windows or under Wine - on its
real LAN provider, so the games can host and join LAN games (with each other, or with
wc3-slop-lan's host). Start it once and leave it open: it notices each game that starts, switches
it, and switches it again whenever the game falls back to its loopback provider.

It does what W3Champions' launcher does for its own games, the same way: with the game's threads
suspended it rewrites the provider id in one instruction (`mov ecx,'LOOP'` becomes `'TCPN'`), has
the game rebuild its provider, and puts the original bytes back at once. No debugger is attached
and no DLL is injected. Details: [docs/porting.md](../docs/porting.md#2-the-windows-build-slop-activator).

**Game builds:** 3.0.0.24268. Any other build is refused (a changed game needs its patterns
checked first); `--any-build` tries anyway, and the byte checks still stand.

## Use

```
slop-activator.exe            watch: switch every game that starts, until closed
slop-activator.exe --once     switch the games running now, then exit
```

Under Wine, run it in the game's prefix: `WINEPREFIX=~/Games/wc3 wine slop-activator.exe`.

For each game it writes `%TEMP%\slop-activator\<pid>.json` - the port and guid of the game's
menus, and how many times it has switched it - which is how wc3-slop-lan's harness
(`harness/webui/bridge.py`) drives a Windows game's menus. Dropping `<pid>.request` there asks for
a switch now (the harness does before each LAN search, so the search gets a fresh provider).

Sample log:

```
18:05:02 slop-activator: watching for Warcraft III (builds 3.0.0.24268)
18:05:07 game 1104: Warcraft III 3.0.0.24268
18:05:16 game 1104: menus on 127.0.0.1:33997, guid 12509788252692449178
18:05:16 game 1104: on TCPN (selector at 0x14118bcaa, back to LOOP again)
18:07:43 game 1104: back on LOOP
18:07:44 game 1104: on TCPN (selector at 0x14118bcaa, back to LOOP again)
```

## Build

It cross-compiles, so no Windows machine is needed:

```sh
rustup target add x86_64-pc-windows-gnu    # plus MinGW: brew install mingw-w64 / pacman -S mingw-w64-gcc
cargo build --release --target x86_64-pc-windows-gnu
cargo test                                 # the patterns and the websocket client, on any OS
```

**Tested:** under wine-staging 11.18 on Linux. Real Windows has not been tried yet.
