# Porting: a new game version, Windows, Linux + Wine

Everything here was worked out and verified on **macOS with Warcraft III 3.0.0.24268** (the
x86_64 build, running under Rosetta on Apple Silicon). This page is a research plan: what is
tied to that build and platform, how each piece was found, and how to find it again somewhere
else.

- **Verified** marks what was proven on 3.0.0.24268 / macOS.
- **Lead** marks a reasonable starting point that nobody has tried yet.

Work through it in order: each step depends on the one before it.

## What is specific to a build or a platform

| piece | where | build-specific | platform-specific |
|---|---|---|---|
| LAN activation (breakpoint address, UUID, bytes) | `harness/activate.sh` | **yes** | **yes**: lldb/Rosetta, the register used |
| Map checksum (xoro + `war3map.w3l`) | `host/src/map.rs` | maybe | no |
| Discovery (product `PX3W`, version 10200, port 16000) | `host/src/discovery.rs` | the version, maybe | no |
| The join burst (0x59 skins/profile messages) | `host/src/game.rs` | maybe (Flo tracks it) | no |
| Web UI message names (`PlayOffline`, `SendGameListing`, `InitializeLocalNetProvider`, `ScoreScreenClose`) | `harness/webui/index.html` | maybe | no |
| Install paths, the game binary, the webui folder | `slop.toml` `[game]` | no | **yes** |
| A second data folder (`HOME`, `CFFIXED_USER_HOME`) | `harness/slop/session.py` | no | **yes** |
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
./slop -c examples/any-map/slop.toml check
SLOP_MAP=<a melee map> ./slop -c examples/any-map/slop.toml test      # hidden host, the least moving parts
./slop -c examples/warcraft-maul/slop.toml test                        # active host, library, hooks
```

## 2. Windows

**Status: not attempted.** The host and the library are portable; activation, data folders and
process handling are not.

### 2.1 Game, paths, launching

- **Game:** `C:\Program Files (x86)\Warcraft III\_retail_\x86_64\Warcraft III.exe` (check your
  install). The page goes into `_retail_\webui\`. Set both in `slop.toml`'s `[game]`.
- **Launching:** the flags `-launch`, `-windowmode windowed` and `-nowfpause` are the same.
  Check that `-editor` still skips the Battle.net login when the game is started directly
  (lead).
- **Data folder:** the default is `Documents\Warcraft III` (CustomMapData, Maps, logs).
  `HOME`/`CFFIXED_USER_HOME` mean nothing on Windows, and two clients still need two data
  folders. Leads:
  - look for a command-line option that moves the user folder (`strings` on the exe, look for
    `-` options near "Documents");
  - a second Windows user started with `runas`;
  - a junction swapped between launches (fragile).

  Check how W3Champions starts two games, if it does.

### 2.2 Activation

- **The register.** x64 Windows passes the first argument in `ecx`/`rcx`, not `edi`/`rdi`. The
  site to look for is `b9 50 4f 4f 4c` (`mov ecx, 'LOOP'`) followed by a `call`, and the
  breakpoint rewrites `rcx`.
- **Finding it:** as in 1.2, with **x64dbg** (breakpoints on each candidate, then trigger from
  the page) or **cdb** from the Windows SDK. cdb is the scriptable one, the counterpart of
  `lldb --batch`:
  ```
  cdb -p <pid> -c "bp /1 <module>+<rva> \"r rcx=0x5443504e; qd\"; g"
  ```
  Use module-relative addresses (`Warcraft III+0x...`) because of ASLR; `lm` lists the base.
- **Watching W3Champions:** find out how W3Champions does it on Windows, with Process Monitor
  or API Monitor on its helper (`DebugActiveProcess`, `WriteProcessMemory`,
  `SetThreadContext`).
- **The script:** port `activate.sh` to Python (ctypes: `DebugActiveProcess`,
  `WaitForDebugEvent`, a hardware breakpoint via `SetThreadContext` Dr0–Dr3, rewrite `Rcx`,
  `DebugActiveProcessStop`), or wrap cdb.
- **Antivirus** may flag a process that debugs another. That is expected; W3Champions has the
  same problem.

### 2.3 The host

- `cargo build` works on Windows as-is for hosting.
- `inject` refuses on Windows. `replace_file` in `host/src/inject.rs` needs the UTF-16 path that
  StormLib's Windows API takes; the `stormlib` crate's `Archive::open` shows how
  (`widestring`).
- **Discovery:** the host answers on loopback to the searcher's port. Check that Windows
  delivers the client's broadcast to a socket bound to `0.0.0.0:16000`, with
  `diagnostics/udp-listen.py`.

### 2.4 The harness

| on macOS | on Windows |
|---|---|
| `pgrep -f`, `kill -9` | `psutil`, or `tasklist` / `taskkill /F` |
| `lsof -ti tcp:<port> -sTCP:LISTEN` | `netstat -ano`, or `psutil.net_connections()` |
| `harness/activate.sh`, `harness/wc3.sh` (bash) | Python equivalents (wc3.sh is thin: POSTs to the server) |
| `screencapture -l <window id>` + yabai | `PrintWindow`, or a capture library, by window handle |
| `HOME=` for the second client | whatever 2.1 finds |

`harness/slop/session.py` holds almost all of this, in `start`, `_client_pid` and `stop`.
Isolating it behind a small platform module is the clean way in.

## 3. Linux + Wine (or Proton)

**Status: not attempted.** The game is the Windows build, so section 2's findings apply inside
Wine, while the host and harness run natively on Linux.

### 3.1 Clients

- **Data folders come for free.** Each Wine prefix has its own
  `drive_c/users/<user>/Documents/Warcraft III`, so two prefixes are two clients:
  ```sh
  WINEPREFIX=~/wc3-a wine "C:/Program Files (x86)/Warcraft III/_retail_/x86_64/Warcraft III.exe" -launch -windowmode windowed -nowfpause
  WINEPREFIX=~/wc3-b wine ...
  ```
- **Sharing the map:** link each prefix's `Maps/<folder>` to one folder, as the harness does for
  `alt_home`.
- **The install:** can be shared (one prefix with the game, symlinked into the other) or copied.
  Battle.net under Wine is the usual way to install it (Lutris has scripts).
- **Launching directly:** check that `-editor` skips the login when the exe is started directly
  under Wine (lead).

### 3.2 Activation

The game code is Windows x64 code in a Linux process (the Wine preloader), so:
- **The register is `rcx`** (Windows calling convention), and the site is the `mov ecx,'LOOP'`
  pattern from 2.2.
- **Debugger:** `winedbg --gdb <pid>` gives a gdb that understands the PE modules. Plain `gdb -p`
  also works if you compute the module base yourself: `Warcraft III.exe`'s mapping in
  `/proc/<pid>/maps`, plus the RVA. The breakpoint script is then the gdb version of
  `activate.sh`: `tbreak *<addr>`, `continue`, `set $rcx = 0x5443504e`, `detach`.
- **ptrace:** it needs `kernel.yama.ptrace_scope` 0 or 1 (with the same user), or root.

### 3.3 Network

- **Loopback:** Wine processes share the Linux host's loopback, so the native host,
  `server.py` on `127.0.0.1:8777` and the page's `fetch` all reach each other (lead: check the
  page's requests aren't blocked by the embedded browser's policy under Wine).
- **Discovery:** the client's broadcast to `255.255.255.255:16000` may leave by the default
  interface rather than `lo`. The host binds `0.0.0.0:16000`, so it should still hear it; check
  with `diagnostics/udp-listen.py`. If the searches never arrive, the host's fallback (announcing
  to ports 16000–16003 on loopback) doesn't depend on broadcasts.

### 3.4 The harness

As on Windows for activation. The rest is closer to macOS: `pgrep`/`kill` work (on the Wine
processes); `lsof` works on the ports; and the second client is `WINEPREFIX` instead of `HOME`.
Screenshots: `xdotool search --pid` for the window, then `import -window <id>` (ImageMagick).

## Keeping it honest

When something works on a new version or platform, record it here: the build number, the
addresses, and which leads panned out. Keep a capture (`diagnostics/`) of a working join. The
next port starts from the last known-good one, the way this one started from W3Champions' Flo.
