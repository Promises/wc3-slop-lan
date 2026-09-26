# Porting: a new game version, Windows, Linux + Wine

Worked out and verified on **macOS with Warcraft III 3.0.0.24268** (the x86_64 build, under
Rosetta on Apple Silicon), and for the Windows build on **Linux + Wine** (sections 2 and 3).
This page is what is tied to a build and platform, how each piece was found, and how to find it
again somewhere else.

- **Verified** marks what was proven on 3.0.0.24268 / macOS.
- **Lead** marks a reasonable starting point that nobody has tried yet.

Work through it in order: each step depends on the one before it.

## What is specific to a build or a platform

| piece | where | build-specific | platform-specific |
|---|---|---|---|
| LAN activation, macOS (breakpoint address, UUID, bytes) | `harness/activate.sh` | **yes** | **yes**: lldb/Rosetta, the register used |
| LAN activation, Windows build (selector and factory patterns, known builds) | `activator/src/patterns.rs` | **yes** | Windows or Wine |
| The menus without our page (Windows build) | `activator/`, `harness/webui/bridge.py` | the message names, maybe | Windows or Wine |
| Map checksum (xoro + `war3map.w3l`) | `host/src/map.rs` | maybe | no |
| Discovery (product `PX3W`, version 10200, port 16000) | `host/src/discovery.rs` | the version, maybe | no |
| The join burst (0x59 skins/profile messages) | `host/src/game.rs` | maybe (Flo tracks it) | no |
| Web UI message names (`PlayOffline`, `SendGameListing`, `InitializeLocalNetProvider`, `ScoreScreenClose`) | `harness/webui/index.html` | maybe | no |
| Install paths, the game binary, the webui folder, launch arguments | `configuration.toml` `[game]` | no | **yes** |
| The data folder (Maps, CustomMapData) | `configuration.toml` `[game]` (`data`) | no | **yes** |
| Process tools (`pgrep`, `lsof`, `kill`, `screencapture`, `yabai`) | `harness/slop/*.py`, `harness/*.sh` | no | **yes** |
| Writing the library into a map (StormLib paths) | `host/src/inject.rs` | no | Windows: not implemented |
| The map library itself | `library/slop.lua` | only if natives change | no |

## 1. A new game version

### 1.1 The game binary

**macOS:** `/Applications/Warcraft III/_retail_/x86_64/Warcraft III.app/Contents/MacOS/Warcraft III`.
- Get its UUID with `dwarfdump --uuid <binary>`.
- The `__TEXT` segment starts at `0x100000000` with file offset 0 (`otool -l <binary>`), so
  file offset = address − `0x100000000`. Check that this still holds.
- If Blizzard ships a native arm64 build, everything in 1.2 changes: the constant is built with
  `movz`/`movk`, and the argument is in `w0`/`x0` instead of `edi`/`rdi`.

### 1.2 The provider switch (activation): find the new breakpoint

**What we know (verified).** The game's handler for the web UI message
`InitializeLocalNetProvider` loads the provider id `'LOOP'` (`0x4c4f4f50`) as the first argument
and calls the function that selects the provider. Changing that argument to `'TCPN'`
(`0x5443504e`) just before the call gives a real LAN provider. On 3.0.0.24268 that is
`mov edi,'LOOP'` at `0x100d3c69d` (`bf 50 4f 4f 4c`), followed by `call` at `0x100d3c6a2`, where
the breakpoint sits.

**Candidates.** On x86_64 search the binary for `bf 50 4f 4f 4c` (`mov edi, 'LOOP'`). On
3.0.0.24268 there are **five** such sites, so the pattern alone doesn't pick one. This script
lists them:

```python
data = open(BINARY, 'rb').read()
import re
print([hex(m.start() + 0x100000000) for m in re.finditer(re.escape(b'\xbf\x50\x4f\x4f\x4c'), data)])
```

**Find the right one.** It is the site that runs when the page sends
`InitializeLocalNetProvider`:
1. Start the game with the page installed and the web UI server running.
2. Attach lldb, and set a breakpoint on each candidate + 5 (the instruction after the `mov`):
   `breakpoint set --shlib "Warcraft III" --address <candidate + 5>`.
3. `continue`, then send the message: `harness/wc3.sh raw 1 InitializeLocalNetProvider '{}'`.
4. The breakpoint that hits is the site. Check that `register read rdi` shows `0x4c4f4f50`.

**Let W3Champions tell you.** W3Champions ships a fix for every patch, and its client (its Flo
client, with a native helper, `native_tcpn`) carries the new address. On 3.0.0.24268 the address was
found by reading their binary: they attach `/usr/libexec/rosetta/debugserver`, set a one-shot
breakpoint, rewrite `rdi` and detach, which is exactly what `activate.sh` does. Useful leads:
- `strings` / a disassembler on the W3Champions helper, looking for a 64-bit constant near
  `0x100d3c6a2`;
- watching the helper with `sudo dtruss -f`, or reading what it sends to debugserver
  (`diagnostics/capture-tcp.sh` records loopback traffic).

**Then update `activate.sh`:** `UUID`, `SITE` (the `call`), and the byte check (the file offset
of the `mov`, and ten bytes from there). Test it:
- `activate.sh` should print `switched to its LAN provider; LAN port :16000` (or 16001...).
- `lsof -a -nP -p <pid> -iUDP` should show the game holding a UDP port from 16000 up.

**Worth doing once it works:** find the site by pattern plus a check at runtime instead of by a
fixed address. Try each candidate, keep the one whose `rdi` reads `'LOOP'` when the page asks,
and cache it per UUID. Then a patch that doesn't move the code shape needs no edit.

### 1.3 Discovery

With a client activated, listen where it searches:

```sh
python3 diagnostics/udp-listen.py 16000 60      # then open the game list / run the join step
```

The client's `SearchGame` shows the product (`PX3W`) and the protocol version (10200 on
3.0.0.24268). If the version changed, update `PROTOCOL_VERSION` in `host/src/discovery.rs`. The
host echoes the searcher's own product and version back, so usually nothing breaks, but the
announce path (used when port 16000 is taken) sends the constant.

To see a real host's answer, capture one: start W3Champions and a game, then run
`sudo bash diagnostics/capture-tcp.sh` (loopback) or a UDP capture, and
`python3 diagnostics/udp-decode.py <pcap>`. Compare it field by field with
`Listing::game_info` in `discovery.rs`.

### 1.4 The map check

If clients find the game but won't join (the host logs `does not have this map ... reports 0 bytes`),
the checksum or the path is off.
- `wc3-slop-lan host ... --break-map-check path|size|crc|xoro|sha1` breaks one field on purpose.
  That's how we learned the client compares path, sha1 and xoro, echoes the size, and ignores
  crc32.
- `--xoro <hex>` sends a candidate checksum without rebuilding.
- The xoro algorithm is Flo's with `war3map.w3l` added after `war3map.w3q`. We found it by
  disassembling the game's checksum routine (`0x100763470` on 3.0.0.24268). It is the function
  that opens those map files by name one after another. Search the binary for the string
  `war3map.w3q` and follow its references.
- If the client joins but quits at the countdown, the **slot layout byte** is wrong.
  `wc3-slop-lan map` shows what the host reads from the map's flags.

### 1.5 The join burst

Flo's crates follow each patch. Bump the `rev` in `host/Cargo.toml` to W3Champions' newest
commit, `cargo build`, and read what changed in `crates/w3gs` and in
`crates/client/src/lan/game/lobby.rs`. The host's `welcome()` in `game.rs` mirrors that lobby
code. The protobuf messages (0x59 skins and profiles) are where a new patch most often adds
something.

### 1.6 The web UI page

The page drives the menus with message names from the game's own `GlueManager.js`. To check
them on a new build, have a running client's page search its bundle. This is how
`ScoreScreenClose` was found:

```sh
harness/wc3.sh eval 1 'fetch("GlueManager.js").then(function(r){return r.text()}).then(function(t){post("/","["+me+"] = "+(t.match(/[A-Za-z_]*(Score|Leave|Offline|Listing|NetProvider)[A-Za-z_]*/g)||[]).join(" ").slice(0,7000))}); "asked"'
harness/wc3.sh log 1 3 wide
```

`harness/wc3.sh trace 1 all` records every message the menus send and receive. Click through
something by hand and read the log to learn its messages.

### 1.7 Everything together

```sh
./slop check
./slop test any-map          # hidden host, a melee map: the least moving parts
./slop test warcraft-maul    # active host, library, hooks
```

## 2. The Windows build: slop-activator

**Status:** verified on Linux + Wine (section 3) with 3.0.0.24268; real Windows is untested,
but it runs the same `.exe` against the same Windows calls.

The Windows build differs from the macOS one in two ways that decide everything here:

- **Its code is encrypted on disk.** `.text` has 8.00 bits of entropy per byte; the loader
  (`war3_loader.dll`) decrypts it page by page as the game runs, and pages not yet run are mapped
  with no access. Search the *running* game, never the file.
- **Its menus come from its packed data**, not a `webui` folder: our page cannot be put there.

### 2.1 Activation: the provider selector

In the running game (3.0.0.24268) the handler behind `InitializeLocalNetProvider` is

```
sub rsp,0x58; call ..; mov ecx,'LOOP'; call ..; call ..; lea rcx,..; mov qword [rsp+0x28],4
```

and the provider factory it calls takes `BNET`, `LOOP` and `TCPN` (class `NetProviderLTCP`).
W3Champions' launcher rewrites exactly this `mov ecx,'LOOP'` (its pattern, an `iced-x86` check of
the instruction and its `POOL`→`NPCT` operands are in its binary), and so does
`activator/` (**verified**):

1. Find the selector by pattern once its page is decrypted (the handler must have run once;
   the activator asks the menus for it).
2. Suspend the game's threads, make sure none is on that instruction, write `TCPN` over `LOOP`,
   ask for `InitializeLocalNetProvider` (the game builds a TCPN provider and opens its LAN
   socket), write `LOOP` back at once, flush the instruction cache, resume.

**Keep the patch in for a moment only.** Left in place, the loader notices the changed code and
shuts the game down within about a minute (exception `0xC00000E5` from `war3_loader.dll`,
**verified**). Restored after the rebuild, the game ran on with its TCPN provider through several
LAN games (**verified**). No debugger is attached, and no DLL is injected.

A new build: extend `KNOWN_BUILDS` and the patterns in `activator/src/patterns.rs` once they are
checked; the activator refuses unknown builds and a pattern that matches more than one place.

### 2.2 The menus without our page

The game serves its menus at `http://127.0.0.1:<port>/webui/index.html?guid=<guid>` and talks to
them over `ws://127.0.0.1:<port>/webui-socket/<guid>`. Anything local may connect and send what
the page would, `{"type":"webui","message":...,"payload":{...}}`; the game's messages reach every
connected socket (**verified**). The activator reads port and guid from the game's memory (the
game writes that URL there) and publishes them in `%TEMP%\slop-activator\<pid>.json`;
`harness/webui/bridge.py` then plays the page's part toward the harness (check-in, screens,
`raw`, `lanjoin`, `leave`).

Two things differ from the macOS page's flow:

- **No `PlayOffline`.** This build reports `enableOfflineMode: false` in its `FeatureFlags`;
  `PlayOffline` starts a Battle.net sign-in ("Login Queue") that pulls the client out of the LAN
  game at loading. The local provider works from the login screen without it (**verified**).
- **No `InitializeNetProvider` before a search** (it too starts a sign-in). A fresh provider for
  each search is asked of the activator instead: the bridge drops `<pid>.request`, the
  activator switches again (its switch rebuilds the provider) and counts it in the instance
  file, and the bridge searches once the count has gone up.

`-editor` does not skip the login screen on this build, and is not needed.

### 2.3 Running it on Windows (untested)

- `slop-activator.exe`: from a release, or `cargo build --release --target
  x86_64-pc-windows-gnu` in `activator/` (it cross-compiles from macOS or Linux).
- **The harness** is written for Unix (`pgrep`, `kill`, `setsid`); it has not been run on
  Windows. The activator alone is enough for LAN play between Windows games and a host.
- **The host**: `cargo build` works; `inject` refuses on Windows (StormLib's UTF-16 paths,
  `host/src/inject.rs`).
- **A second client** needs a user folder of its own (a second Windows user, or a redirected
  Documents folder); two games on one break real maps.

## 3. Linux + Wine

**Status: verified** on Arch Linux, KDE Plasma (Wayland, Xwayland), a GTX 960M with
`nvidia-580xx`, wine-staging 11.18, Warcraft III 3.0.0.24268: `slop up` with Warcraft Maul, one
client, the library's heartbeat and commands (2026-09-26).

### 3.1 The prefix

- **wine-staging 11.6 or newer, not Proton.** Since 3.0 the login fails under Proton 11 ("Please
  check your VPN"): the new `ClientSdk.dll` hands `CertCreateCertificateChainEngine` an 88-byte
  config that Proton's Wine rejects; upstream Wine fixed it in 11.6.
- One 64-bit prefix (`WINEARCH=win64`, `winecfg -v win10`), `winetricks -q dxvk arial`, `nvapi`
  and `nvapi64` disabled. Run everything with `prime-run` on hybrid-graphics laptops.
- **Battle.net needs its browser's hardware acceleration off** or it crashes right after login:
  `"HardwareAcceleration": "false"` under `Client` in
  `drive_c/users/<user>/AppData/Roaming/Battle.net/Battle.net.config`.
- Install Warcraft III through Battle.net and start it from there once; after that the harness
  starts `Warcraft III.exe` itself (`-launch -windowmode windowed -nowfpause`).
- The system clock must be right (`timedatectl set-ntp true`); W3Champions checks it.

### 3.2 configuration.toml

```toml
[game]
binary = "~/Games/wc3/drive_c/Program Files (x86)/Warcraft III/_retail_/x86_64/Warcraft III.exe"
webui = ""
data = "~/Games/wc3/drive_c/users/<user>/Documents/Warcraft III"
args = ["-launch", "-windowmode", "windowed", "-nowfpause"]
launcher = ["env", "WINEPREFIX=/home/<user>/Games/wc3", "prime-run", "wine"]
```

The harness sees a Windows build (the binary ends in `.exe`), starts `slop-activator.exe` with
the same launcher, and a bridge per game. Run `slop` from the desktop session: a game started
without `DISPLAY`/`WAYLAND_DISPLAY`/`XAUTHORITY` exits at once. One client for now (a second needs
a second prefix; the harness refuses two).

### 3.3 Network

- Wine shares the Linux loopback: host, web UI server, bridge and games all reach each other.
- The game takes UDP 16000 first; the host then announces to the clients' ports instead
  ("UDP 16000 is taken ... announcing to the clients' ports", **verified**).

### 3.4 Pitfalls

- Wine links the prefix's `Documents` to your real `~/Documents`: the harness keeps configured
  paths as written (not resolved through links), since the activator's folder is found from the
  data folder's place in the prefix.
- Over SSH, `pkill -f <pattern>` kills its own shell when the command line holds the pattern;
  send scripts through `bash -s` on stdin.
- A process only just started under Wine may not give an exit code yet; the activator checks
  the process list before calling a game gone.
- W3Champions under Wine: sign-in often crashes in Wine's `ole32`, and launcher 1.6.8+ refuses
  to start games (its job-object check); neither matters for slop-lan.

## Keeping it honest

When something works on a new version or platform, record it here: the build number, the
addresses, and which leads panned out. Keep a capture (`diagnostics/`) of a working join. The
next port starts from the last known-good one, the way this one started from W3Champions' Flo.
