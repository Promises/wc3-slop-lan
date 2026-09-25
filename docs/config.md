# slop.toml

The harness reads everything from one file, so a run is `./slop up` or `./slop test`.

**Which file.** `-c <file>`, else `$SLOP_CONFIG`, else `./slop.toml`.

**Paths.** Relative paths are resolved from the file's own folder. They may start with `~`, and
may use `${VAR}` or `${VAR:-default}`.

**Unknown keys** are errors, so a typo can't be silently ignored.

```toml
[map]
file = "dist/bin/map.w3x"      # required: the map to play; a build output is fine
folder = "slop"                # staged as Maps/<folder>/<file name>
build = "npm run build:dev"    # optional: run first with `slop up --build` / `slop test --build`
build_dir = "."                # where build runs

[host]
mode = "active"                # "active": the host plays a seat and can act; "hidden" (default): no slot
seat = "auto"                  # the active host's slot, 0-based, or "auto": the first slot the map leaves undefined
prefix = "slop"                # the sync prefix between host and library
control = 8778                 # the host's control port
# binary = "host/target/debug/wc3-slop-lan"

[library]
inject = true                  # put slop.lua into the staged copy (Lua maps; JASS maps run without)

[clients]
count = 2                      # 1 or 2
names = ["red", "blue"]        # player names, one per client
alt_home = "~/BattleNet-alt"   # the second client's data folder

[tests]
file = "tests.py"

[game]
server = "http://127.0.0.1:8777"   # the web UI server; the page expects this port
# binary = "/Applications/Warcraft III/_retail_/x86_64/Warcraft III.app/Contents/MacOS/Warcraft III"
# webui = "/Applications/Warcraft III/_retail_/webui"
```

## Choosing the host mode

| | hidden | active |
|---|---|---|
| slots | none | one, the seat |
| the map plays | exactly as on Battle.net | with one extra player that no client controls |
| `slop cmd` | no | yes (needs the library) |
| `slop type` | no | yes (no library needed) |
| file channel (`slop file`) | yes (needs the library) | yes |
| desync check | yes | yes |

- **Hidden** is right when the map mustn't see anything unusual, for example a test that only
  checks a game plays out in step.
- **Active** is what makes the game drivable. The seat goes in a slot the map doesn't define
  (`seat = "auto"`), which most maps never look at. A map that loops over all 24 slots should
  skip `Slop.seat`; see [library.md](library.md#the-seat).

## Examples

- **`examples/any-map/slop.toml`**: a hidden host, the map from `$SLOP_MAP`.
- **`examples/warcraft-maul/slop.toml`**: an active host at a fixed seat, a build step, and
  paths into a neighbouring repo through `${WC3_MAUL:-...}`.
