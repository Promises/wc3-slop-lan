"""slop.toml: what to play and how, so a run is `slop up` rather than a long command line.

Paths are relative to the config file, may start with ~, and may use ${VAR} or ${VAR:-default}.
Every key but map.file has a default; see examples/ and docs/config.md.
"""
import os
import pathlib
import re
import tomllib
from dataclasses import dataclass, field

REPO = pathlib.Path(__file__).resolve().parents[2]
CONFIG_NAME = 'slop.toml'


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
    tests_file: pathlib.Path | None = None
    # [game]
    server: str = 'http://127.0.0.1:8777'
    game: pathlib.Path = pathlib.Path('/Applications/Warcraft III/_retail_/x86_64/Warcraft III.app/Contents/MacOS/Warcraft III')
    webui_dir: pathlib.Path = pathlib.Path('/Applications/Warcraft III/_retail_/webui')

    @property
    def root(self):
        return self.path.parent

    @property
    def active(self):
        return self.host_mode == 'active'

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


def find(explicit=None):
    """The config to use: the one named, else $SLOP_CONFIG, else ./slop.toml."""
    chosen = explicit or os.environ.get('SLOP_CONFIG') or CONFIG_NAME
    path = pathlib.Path(chosen).expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f'no config at {path} (give one with -c, or see examples/)')
    return path


def load(explicit=None):
    path = find(explicit)
    raw = tomllib.loads(path.read_text())
    known = {'map': {'file', 'folder', 'build', 'build_dir'},
             'host': {'mode', 'seat', 'prefix', 'control', 'binary'},
             'library': {'inject'},
             'clients': {'count', 'names'},
             'tests': {'file'},
             'game': {'server', 'binary', 'webui'}}
    for section, values in raw.items():
        if section not in known or not isinstance(values, dict):
            raise ConfigError(f'{path.name}: unknown section [{section}]')
        for key in values:
            if key not in known[section]:
                raise ConfigError(f'{path.name}: unknown key {section}.{key}')

    def get(section, key, default=None):
        return raw.get(section, {}).get(key, default)

    def resolve(value):
        return (path.parent / _expand(value)).resolve() if value is not None else None

    if not _expand(str(get('map', 'file', ''))):
        raise ConfigError(f'{path.name}: map.file is required (empty after expanding {get("map", "file")!r}?)')
    config = Config(path=path, map_file=resolve(get('map', 'file')))
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
    config.tests_file = resolve(get('tests', 'file'))
    config.server = get('game', 'server', config.server)
    if get('game', 'binary'):
        config.game = resolve(get('game', 'binary'))
    if get('game', 'webui'):
        config.webui_dir = resolve(get('game', 'webui'))

    if config.host_mode not in ('active', 'hidden'):
        raise ConfigError(f'{path.name}: host.mode is "active" or "hidden", not {config.host_mode!r}')
    if config.seat != 'auto' and not config.seat.isdigit():
        raise ConfigError(f'{path.name}: host.seat is a slot number or "auto"')
    if not 1 <= config.clients <= 2:
        raise ConfigError(f'{path.name}: clients.count is 1 or 2')
    if len(config.names) < config.clients:
        raise ConfigError(f'{path.name}: clients.names needs a name per client')
    return config
