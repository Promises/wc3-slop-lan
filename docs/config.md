# Configuration

Two kinds of file, so a run is `./slop up` rather than a long command line:

| file | says | where | |
|---|---|---|---|
| `configuration.toml` | the default map, and how to run Warcraft III on this machine | the repo root | optional: the defaults fit a standard macOS install |
| `slop.toml` | what to play and how to test it | one per map, in `maps/<name>/` | one per map |

- **Paths** in both are resolved from the file's own folder. They may start with `~`, and may
  use `${VAR}` or `${VAR:-default}`.
- **Unknown keys** are errors, so a typo can't be silently ignored.

## Maps

A map is a folder under `maps/`:

```
maps/
  any-map/          slop.toml, tests.py, (2)Hammerfall_S3.w3x
  warcraft-maul/    slop.toml, tests.py (its map is loaded from the map's own repo)
```

**Picking a map for a command:**
- by folder name: `./slop up warcraft-maul`;
- leave it out for the default map: `./slop up`;
- or give any `slop.toml`, anywhere: `./slop -c path/to/slop.toml up`.

`./slop maps` lists the maps, and shows which one is the default. To add a map, make a folder
beside these with a `slop.toml`, and the map file too unless `map.file` points elsewhere.

## slop.toml

```toml
[map]
file = "dist/bin/map.w3x"      # the map to play; left out: the one .w3x/.w3m next to this file
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

[tests]
file = "tests.py"              # or a list: ["tests.py", "races.py"]
```

**`map.file`** is the only part a map usually needs, and only when the map isn't in its
folder. A build output elsewhere is the typical case: Warcraft Maul's points at the map repo's
`dist/bin/map.w3x`.

## configuration.toml

Settings for this machine, the same for every map: which map to use when none is named, and
how to run the game. Copy `configuration.example.toml` to `configuration.toml` and change what
differs; git ignores `configuration.toml`. Without one, the example's values are used, and
`./slop check` says which is in use.

```toml
map = "any-map"                    # the default map: a folder under maps/

[game]
binary = "/Applications/Warcraft III/_retail_/x86_64/Warcraft III.app/Contents/MacOS/Warcraft III"
webui = "/Applications/Warcraft III/_retail_/webui"        # the harness puts its page here (empty for the Windows build)
data = "~/Library/Application Support/Blizzard/Warcraft III"  # holds Maps and CustomMapData
second_home = ".slop/client-2"     # the second client's home: its own user folder, Maps linked to yours
args = ["-editor", "-launch", "-windowmode", "windowed", "-nowfpause"]
launcher = []                      # put in front of the binary, e.g. ["env", "WINEPREFIX=...", "wine"]
activator = ""                     # the Windows build: slop-activator.exe (empty: the one built in activator/)
server = "http://127.0.0.1:8777"   # the web UI server; written into the page when it is installed
```

A `configuration.toml` only needs the keys it changes. For example, this is a whole one:

```toml
map = "warcraft-maul"
```

**How `[game]` is used:**
- The harness launches each client with `launcher + binary + args`.
- It stages maps under `data/Maps`, and reads traces from `data/CustomMapData`.
- Activation (`harness/activate.sh`) gets the binary from the harness. Run by hand, it assumes
  the standard macOS install unless `WC3_GAME` says otherwise.
- `configuration.example.toml` has Windows and Wine lines, commented out; see
  [porting.md](porting.md).

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

## The shipped maps

- **`maps/any-map`**: a hidden host, and `(2)Hammerfall_S3.w3x` right there, so it needs no
  `map.file`. Hammerfall is a W3Champions ladder map (Season 9), included as a small, ordinary
  melee map to try things on.
- **`maps/warcraft-maul`**: an active host at a fixed seat, a build step, and `map.file` into
  the neighbouring map repo through `${WC3_MAUL:-...}`.
