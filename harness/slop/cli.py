"""slop: the harness from the command line.

A map is a folder under maps/ with a slop.toml; name it, or leave it out for the default map
(configuration.toml's `map`), or give any slop.toml with -c. See docs/harness.md.

  slop maps                   the maps there are, and which is the default
  slop check [map]            is everything in place? (changes nothing)
  slop up [map] [--build]     start a game and leave it running
  slop test [map] [--build] [--fresh] [--only NAME ...]
                              run the map's tests, each on a fresh game
  slop mcp [map]              serve the harness to an MCP client over stdio

The running game (from `slop up`), whichever map it is:
  slop status                 the host's view of it
  slop cmd <player> <line>    run a command line as a player (0-based slot) on every client
  slop type <text>            type a chat line as the host's seat
  slop ctl <line>             any host control command
  slop file <client> <line>   a command through a client's file channel, as its own player
  slop state [client]         the newest heartbeat
  slop trace [client] [n]     the last n trace lines
  slop down                   stop it
"""
import argparse
import json
import shutil
import subprocess
import sys

from . import config as config_module, mcp, runner
from .session import Session, TestFailed, ensure_host_binary, run_build


def check(config):
    """What is missing, in plain words; nothing is changed."""
    problems = []
    ok = lambda text: print(f'  ok    {text}')

    def bad(text):
        problems.append(text)
        print(f'  MISSING {text}')

    ok(f'map {config.name} ({config.path})')
    ok(f'configuration {config.configuration or "defaults (no configuration.toml; see configuration.example.toml)"}')
    (ok if config.map_file.is_file() else bad)(f'map file {config.map_file}'
                                               + ('' if config.map_file.is_file() or not config.build else f' (build: {config.build})'))
    (ok if config.game.exists() else bad)(f'game {config.game}')
    (ok if config.webui_dir.is_dir() else bad)(f"the game's webui folder {config.webui_dir}")
    (ok if config.data.is_dir() else bad)(f"the game's data folder {config.data}")
    (ok if config.host_binary.exists() else bad)(f'host {config.host_binary}' + ('' if config.host_binary.exists() else ' (slop up builds it; needs cargo)'))
    for tool in ('lldb', 'lsof', 'curl'):
        (ok if shutil.which(tool) else bad)(f'{tool} on PATH')
    if config.tests_file:
        (ok if config.tests_file.exists() else bad)(f'tests {config.tests_file}')
    if config.map_file.is_file() and config.host_binary.exists():
        facts = subprocess.run([str(config.host_binary), 'map', str(config.map_file)], capture_output=True, text=True)
        if facts.returncode:
            bad(f'a map the host can read: {facts.stderr.strip()}')
        else:
            humans = facts.stdout.count('kind=1')
            ok(f'the map has {humans} human slots; {config.clients} client(s) configured')
            free = next((l.split()[-1] for l in facts.stdout.splitlines() if l.startswith('free slot')), 'none')
            if config.active:
                ok(f'active host, seat {config.seat}' + (f' (auto resolves to {free})' if config.seat == 'auto' else ''))
            else:
                ok('hidden host: no seat, commands only through the file channel')
    print('ready' if not problems else f'{len(problems)} thing(s) missing')
    return not problems


def main(argv=None):
    parser = argparse.ArgumentParser(prog='slop', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-c', '--config', help='a slop.toml to use instead of a map from maps/')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('maps')
    for name in ('check', 'up', 'test', 'mcp'):
        sub = commands.add_parser(name)
        sub.add_argument('map', nargs='?', help='a folder under maps/ (default: configuration.toml\'s map)')
        if name in ('up', 'test'):
            sub.add_argument('--build', action='store_true', help="run the map's map.build first")
        if name == 'test':
            sub.add_argument('--fresh', action='store_true', help='launch the clients again for every test')
            sub.add_argument('--only', action='append', default=[], metavar='NAME', help='run only this test')
    commands.add_parser('down')
    commands.add_parser('status')
    sub = commands.add_parser('cmd')
    sub.add_argument('player', type=int)
    sub.add_argument('line', nargs='+')
    sub = commands.add_parser('type')
    sub.add_argument('text', nargs='+')
    sub = commands.add_parser('ctl')
    sub.add_argument('line', nargs='+')
    sub = commands.add_parser('file')
    sub.add_argument('client', type=int)
    sub.add_argument('line', nargs='+')
    sub = commands.add_parser('state')
    sub.add_argument('client', type=int, nargs='?', default=0)
    sub = commands.add_parser('trace')
    sub.add_argument('client', type=int, nargs='?', default=0)
    sub.add_argument('lines', type=int, nargs='?', default=30)
    args = parser.parse_args(argv)
    # Progress must show up as it happens, also when the output goes to a file or a pipe
    sys.stdout.reconfigure(line_buffering=True)

    try:
        if args.command == 'maps':
            default = config_module.configuration()[0]['map']
            for name in config_module.available_maps():
                print(f'{name}{"   (default)" if name == default else ""}')
            return 0
        if args.command in ('check', 'up', 'test', 'mcp'):
            config = config_module.load(args.config, args.map)
        elif args.config or getattr(args, 'map', None):
            sys.exit(f'slop {args.command} works on the running game; it takes no map')
    except config_module.ConfigError as problem:
        sys.exit(str(problem))

    try:
        if args.command == 'check':
            return 0 if check(config) else 1
        if args.command in ('up', 'test') and args.build:
            run_build(config)
        if args.command == 'up':
            session = Session(config, 'up')
            try:
                session.start()
            except BaseException:
                session.stop()
                raise
            act = 'slop cmd 0 .gold 5000' if session.seat is not None else 'slop file 0 .gold 5000'
            print(f'{config.name} is up, and left running: `slop status`, `{act}`, `slop state`; '
                  f'`slop down` when done')
            return 0
        if args.command == 'test':
            ensure_host_binary(config)
            return 0 if runner.run(config, args.only, reuse=not args.fresh) else 1
        if args.command == 'mcp':
            mcp.serve(config)
            return 0

        session = Session.resume()
        if args.command == 'down':
            session.stop()
        elif args.command == 'status':
            print(session.control('status'))
        elif args.command == 'cmd':
            session.cmd(args.player, ' '.join(args.line))
            print('queued')
        elif args.command == 'type':
            session.type(' '.join(args.text))
            print('queued')
        elif args.command == 'ctl':
            print(session.control(' '.join(args.line)))
        elif args.command == 'file':
            session.file_command(args.client, ' '.join(args.line))
            print('written')
        elif args.command == 'state':
            print(json.dumps(session.beat(args.client), indent=1))
        elif args.command == 'trace':
            print('\n'.join(session.trace(args.client)[-args.lines:]))
        return 0
    except (RuntimeError, TestFailed, OSError, subprocess.CalledProcessError, config_module.ConfigError) as problem:
        sys.exit(f'slop {args.command}: {problem}')
