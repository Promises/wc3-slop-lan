"""The two config files.

- configuration.toml (at the repo root, optional): how to run Warcraft III on this machine, and
  which map to use when none is named. Its defaults are configuration.example.toml's values.
- slop.toml, one per map, in maps/<name>/: what to play and how to test it.

A map is named by its folder under maps/ (`slop up warcraft-maul`), or given as a path to its
slop.toml (`slop -c path/to/slop.toml up`). Paths in both files are relative to the file they
are in, may start with ~, and may use ${VAR} or ${VAR:-default}. See docs/config.md.
"""
import os
import pathlib
import re
import tomllib
from dataclasses import dataclass, field

REPO = pathlib.Path(__file__).resolve().parents[2]
MAPS = REPO / 'maps'
CONFIG_NAME = 'slop.toml'
CONFIGURATION = REPO / 'configuration.toml'
CONFIGURATION_EXAMPLE = REPO / 'configuration.example.toml'
MAP_FILES = ('*.w3x', '*.w3m')


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    path: pathlib.Path
    # [map]
    map_file: pathlib.Path
    map_folder: str = 'slop'
    build: str | None = None
    build_dir: pathlib.Path | None = None
    # [host]
    host_mode: str = 'hidden'
    seat: str = 'auto'
    prefix: str = 'slop'
    control: int = 8778
    host_binary: pathlib.Path = REPO / 'host/target/debug/wc3-slop-lan'
    # [library]
    inject: bool = True
    # [clients]
    clients: int = 2
    names: list = field(default_factory=lambda: ['red', 'blue'])
    # [tests]
    tests_files: list = field(default_factory=list)
    # configuration.toml [game]
    configuration: pathlib.Path | None = None
    game: pathlib.Path | None = None
    webui_dir: pathlib.Path | None = None
    data: pathlib.Path | None = None
    second_home: pathlib.Path | None = None
    args: list = field(default_factory=list)
    launcher: list = field(default_factory=list)
    server: str = ''
    activator: pathlib.Path | None = None

    @property
    def root(self):
        return self.path.parent

    @property
    def name(self):
        """The map's name: its folder under maps/, or its slop.toml's path elsewhere."""
        return self.root.name if self.root.parent == MAPS else str(self.path)

    @property
    def active(self):
        return self.host_mode == 'active'

    @property
    def maps(self):
        return self.data / 'Maps'

    @property
    def windows_build(self):
        """The game is the Windows build (on Windows or under Wine): its menus cannot load our page,
        so slop-activator and the bridge stand in for it."""
        return self.game.suffix.lower() == '.exe'

    @property
    def activator_dir(self):
        """Where slop-activator writes its instance files: %TEMP%\\slop-activator of the user the
        data folder belongs to (<user>/Documents/Warcraft III -> <user>/AppData/Local/Temp)."""
        return self.data.parents[1] / 'AppData' / 'Local' / 'Temp' / 'slop-activator'

    @property
    def custom_map_data(self):
        return self.data / 'CustomMapData'

    def home_for(self, client):
        """The home folder a client runs with: yours for the first, second_home for the second."""
        return pathlib.Path.home() if client == 0 else self.second_home

    def data_for(self, client):
        """A client's user folder: `data`, moved under that client's home."""
        if client == 0:
            return self.data
        try:
            return self.second_home / self.data.relative_to(pathlib.Path.home())
        except ValueError:
            raise ConfigError(f'[game] data ({self.data}) is not under your home folder, so the second '
                              f'client\'s user folder cannot be found from second_home')

    @property
    def launch(self):
        """The command that starts one client."""
        return [*self.launcher, str(self.game), *self.args]

    @property
    def runs(self):
        """Where each run's artifacts go."""
        return self.root / '.slop'


def _expand(value):
    """${VAR} and ${VAR:-default}, then ~."""
    def replace(match):
        name, default = match.group(1), match.group(3)
        return os.environ.get(name) or (default if default is not None else '')
    return os.path.expanduser(re.sub(r'\$\{(\w+)(:-([^}]*))?\}', replace, value))


def available_maps():
    """The maps under maps/, by name."""
    return sorted(p.parent.name for p in MAPS.glob(f'*/{CONFIG_NAME}'))


def configuration():
    """configuration.toml over the example's values: (the merged table, the file or None)."""
    merged = tomllib.loads(CONFIGURATION_EXAMPLE.read_text())
    if not CONFIGURATION.is_file():
        return merged, None
    own = tomllib.loads(CONFIGURATION.read_text())
    for key, value in own.items():
        if key not in merged:
            raise ConfigError(f'{CONFIGURATION.name}: unknown key {key}')
        if isinstance(value, dict):
            for inner in value:
                if inner not in merged[key]:
                    raise ConfigError(f'{CONFIGURATION.name}: unknown key {key}.{inner}')
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged, CONFIGURATION


def find(explicit=None, map_name=None):
    """The slop.toml to use: the path given with -c, else the map named, else the default map
    from configuration.toml."""
    if explicit:
        path = pathlib.Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ConfigError(f'no config at {path}')
        return path
    name = map_name or configuration()[0]['map']
    path = MAPS / name / CONFIG_NAME
    if not path.is_file():
        known = ', '.join(available_maps()) or 'none'
        raise ConfigError(f'no map called {name!r} (maps/: {known})')
    return path


def _map_file(path, given):
    """map.file, or the one map file next to the slop.toml."""
    if given is not None:
        expanded = _expand(str(given))
        if not expanded:
            raise ConfigError(f'{path}: map.file is empty after expanding {given!r}')
        return (path.parent / expanded).resolve()
    found = sorted(f for pattern in MAP_FILES for f in path.parent.glob(pattern))
    if len(found) != 1:
        raise ConfigError(f'{path}: no map.file, and {len(found)} map files next to it '
                          f'({", ".join(f.name for f in found) or "none"}); put one there or set map.file')
    return found[0]


def load(explicit=None, map_name=None):
    path = find(explicit, map_name)
    raw = tomllib.loads(path.read_text())
    known = {'map': {'file', 'folder', 'build', 'build_dir'},
             'host': {'mode', 'seat', 'prefix', 'control', 'binary'},
             'library': {'inject'},
             'clients': {'count', 'names'},
             'tests': {'file'}}
    for section, values in raw.items():
        if section == 'game':
            raise ConfigError(f'{path.name}: [game] belongs in {CONFIGURATION.name} at the repo root '
                              f'(see {CONFIGURATION_EXAMPLE.name})')
        if section not in known or not isinstance(values, dict):
            raise ConfigError(f'{path.name}: unknown section [{section}]')
        for key in values:
            if key not in known[section]:
                raise ConfigError(f'{path.name}: unknown key {section}.{key}')

    def get(section, key, default=None):
        return raw.get(section, {}).get(key, default)

    def resolve(value):
        return (path.parent / _expand(value)).resolve() if value is not None else None

    config = Config(path=path, map_file=_map_file(path, get('map', 'file')))
    config.map_folder = get('map', 'folder', config.map_folder)
    config.build = get('map', 'build')
    config.build_dir = resolve(get('map', 'build_dir', '.'))
    config.host_mode = get('host', 'mode', config.host_mode)
    config.seat = str(get('host', 'seat', config.seat))
    config.prefix = get('host', 'prefix', config.prefix)
    config.control = int(get('host', 'control', config.control))
    if get('host', 'binary'):
        config.host_binary = resolve(get('host', 'binary'))
    config.inject = bool(get('library', 'inject', config.inject))
    config.clients = int(get('clients', 'count', config.clients))
    config.names = list(get('clients', 'names', config.names))
    files = get('tests', 'file', [])
    config.tests_files = [resolve(f) for f in ([files] if isinstance(files, str) else files)]
    _load_game(config)

    if config.host_mode not in ('active', 'hidden'):
        raise ConfigError(f'{path.name}: host.mode is "active" or "hidden", not {config.host_mode!r}')
    if config.seat != 'auto' and not config.seat.isdigit():
        raise ConfigError(f'{path.name}: host.seat is a slot number or "auto"')
    if not 1 <= config.clients <= 2:
        raise ConfigError(f'{path.name}: clients.count is 1 or 2')
    if config.windows_build and config.clients > 1:
        raise ConfigError(f'{path.name}: the Windows build runs one client for now (a second needs a Wine prefix '
                          f'or user of its own; see docs/porting.md): clients.count = 1')
    if len(config.names) < config.clients:
        raise ConfigError(f'{path.name}: clients.names needs a name per client')
    return config


def _load_game(config):
    table, source = configuration()
    game = table['game']
    base = (source or CONFIGURATION_EXAMPLE).parent

    def resolve(key):
        # Absolute, but symlinks kept: Wine links a prefix's Documents to the real one, and the
        # activator's folder is found from the data folder's place in the prefix
        return pathlib.Path(os.path.abspath(base / _expand(game[key])))

    config.configuration = source
    config.game, config.data = resolve('binary'), resolve('data')
    config.webui_dir = resolve('webui') if game['webui'] else None
    config.activator = resolve('activator') if game['activator'] else \
        REPO / 'activator/target/x86_64-pc-windows-gnu/release/slop-activator.exe'
    config.second_home = resolve('second_home')
    config.args = [_expand(str(a)) for a in game['args']]
    config.launcher = [_expand(str(a)) for a in game['launcher']]
    config.server = game['server'].rstrip('/')
