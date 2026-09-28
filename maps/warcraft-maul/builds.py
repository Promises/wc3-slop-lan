"""Designs from the Maze Designer (warcraftmaul.com/maze-designer), built in a real game.

Every maul-build/1 file in builds/ is a test. Red picks the design's race(s) and builds its
towers in red's own lane where the design puts them - the designer's canonical lane turned into
red's lane the way the map turns lanes (maul.lane_point) - then upgrades them along their
chains, all with the design's budget as gold. It passes when:

- the map takes every tower (its anti-block refuses one that would close the way);
- each stands as the form its chain ends as;
- what was spent is what the design costs.

The game is in Debug mode at difficulty 400, so no wave comes, and the ones sent are the
hardest. These are the builds for the wave 34 performance test."""
import json
import pathlib
import re
import time

import maul
from maul import RED
from races import Findings
from slop import TestFailed, trace

BUILDS = pathlib.Path(__file__).resolve().parent / 'builds'
BUILD_FORMAT = 'maul-build/1'
# The builds are for the performance test: the hardest creeps, 4x hp and armour and random abilities
DIFFICULTY = 400
TIERS = ('Beginner', 'Intermediate', 'Advanced', 'Other', 'Secondary')
# How long the towers may take to stand, and each round of upgrades
BUILD_SECONDS, UPGRADE_SECONDS = 150, 90


def race_named(name):
    for tier in TIERS:
        for race in maul.races(tier):
            if race['name'] == name:
                return race
    raise TestFailed(f'no race called {name!r} in the map')


def standing_on(units, spot, builders):
    """The building on a tower spot, if any (the builders made for it aside)."""
    return next((unit for unit in units if abs(unit['x'] - spot[0]) <= 32 and abs(unit['y'] - spot[1]) <= 32
                 and unit['type'] not in builders), None)


class Job:
    """One design to build: whose it is, in which lane, and what it takes."""

    def __init__(self, build, player, lane):
        self.build, self.player, self.lane = build, player, lane
        self.name = build['name']
        self.races = [race_named(name) for name in build['races']]
        self.by_id, self.builder_of = {}, {}
        for race in self.races:
            for tower in race['towers']:
                self.by_id.setdefault(tower['id'], tower)
                self.builder_of.setdefault(tower['id'], race['builder'])
        self.builders = {race['builder'] for race in self.races}
        self.towers = build['towers']
        unknown = sorted({i for tower in self.towers for i in tower['chain'] if i not in self.by_id})
        if unknown:
            raise TestFailed(f'{self.name}: towers not of {" or ".join(build["races"])}: {", ".join(unknown)}')
        self.cost = sum(self.by_id[i]['gold'] for tower in self.towers for i in tower['chain'])
        self.spots = [maul.lane_point(lane, tower['at']) for tower in self.towers]
        self.uses_food = any(self.by_id[i]['food'] for tower in self.towers for i in tower['chain'])

    def at(self, units, spot):
        return standing_on(units, spot, self.builders)

    def where(self):
        return f"{maul.COLOURS[self.lane].lower()}'s lane"


def build_designs(game, jobs, findings):
    """Builds several designs at once - (build, player, lane) each - every step for all of them
    together: races and gold, then every base tower, then each round of upgrades. Returns, per
    design, [(tower, spot, unit or None)]."""
    jobs = [Job(*job) for job in jobs]
    players = sorted({job.player for job in jobs})
    for job in jobs:
        if job.cost > job.build['budget']:
            findings.append(f'{job.name}: costs {job.cost}, over its own budget of {job.build["budget"]}')

    maul.start(game, DIFFICULTY)
    gold = {}
    for player in players:
        mine = [job for job in jobs if job.player == player]
        for race in {race['name']: race for job in mine for race in job.races}.values():
            # A pick costs a lumber (a second race's is the lumber a player gets at wave 15); a
            # player may have the race already
            game.cmd(player, '.lumber 1')
            maul.fresh_beat(game)
            try:
                maul.pick(game, player, race['item'])
            except TestFailed as error:
                if 'already has' not in str(error):
                    raise
        if any(job.uses_food for job in mine):
            # Farms and animals go up side by side, so the animals are not left waiting on the
            # farms' food (the designer checked that the farms make enough)
            game.cmd(player, '.foodcap 300')
        gold[player] = sum(max(job.build['budget'], job.cost) for job in mine)
        game.cmd(player, f'.gold {gold[player]}')
    game.wait_for('the gold to be set', lambda beat: all(beat.gold(p) == gold[p] for p in players))

    # Every base tower of every design at once, each by a builder of its own made on its spot
    pending = [(job, game.create_later(job.player, job.builder_of[tower['chain'][0]], *spot))
               for job in jobs for tower, spot in zip(job.towers, job.spots)]
    builders = [(job.player, game.finish(p, 30)[0]['id']) for job, p in pending]
    orders_before = len(game.events('order'))
    for (player, builder), (job, tower, spot) in zip(
            builders, [(job, tower, spot) for job in jobs for tower, spot in zip(job.towers, job.spots)]):
        game.build(player, builder, tower['chain'][0], *spot)
    total = sum(len(job.towers) for job in jobs)
    orders = []

    def answered():
        orders[:] = [line for line in game.events('order')[orders_before:] if ' build ' in line]
        return len(orders) >= total
    game.wait(f'{total} build orders', answered, 90)
    for player in players:
        theirs = [line for line in orders if line.startswith(f'order p{player} ') or f' order p{player} ' in line]
        planned = [(job, tower) for job in jobs if job.player == player for tower in job.towers]
        for line, (job, tower) in zip(theirs, planned):
            if line.endswith('rejected'):
                findings.append(f'{job.name}: the order to build {tower["chain"][0]} at corner {tower["at"]} was refused')

    # Standing when the map has set it up as a tower (on construction finished)
    units = {}

    def look():
        for player in players:
            units[player] = game.units(player)

    def standing():
        look()
        registered = {player: maul.towers(game, player) for player in players}
        return all((unit := job.at(units[job.player], spot)) and unit['type'] == tower['chain'][0]
                   and unit['id'] in registered[job.player]
                   for job in jobs for tower, spot in zip(job.towers, job.spots))
    try:
        game.wait(f'{total} towers to stand', standing, BUILD_SECONDS)
    except TestFailed as error:
        findings.append(f'stopped: {error}')
    for player, builder in builders:
        game.remove(player, builder)

    # The upgrades, one step of every chain at a time
    for step in range(1, max(len(tower['chain']) for job in jobs for tower in job.towers)):
        todo = [(job, tower, spot) for job in jobs for tower, spot in zip(job.towers, job.spots)
                if len(tower['chain']) > step]
        for job, tower, spot in todo:
            unit = job.at(units[job.player], spot)
            if unit:
                game.upgrade(job.player, unit['id'], tower['chain'][step])

        def upgraded():
            look()
            return all((unit := job.at(units[job.player], spot)) and unit['type'] == tower['chain'][step]
                       for job, tower, spot in todo)
        try:
            game.wait(f'{len(todo)} towers to upgrade to their form {step + 1}', upgraded, UPGRADE_SECONDS)
        except TestFailed as error:
            findings.append(f'stopped: {error}')

    time.sleep(2)
    look()
    results = []
    for job in jobs:
        placed = [(tower, spot, job.at(units[job.player], spot)) for tower, spot in zip(job.towers, job.spots)]
        for tower, spot, unit in placed:
            want = tower['chain'][-1]
            findings.check(unit is not None and unit['type'] == want,
                           f'{job.name}: corner {tower["at"]} (world {spot[0]},{spot[1]}) is '
                           f'{"empty" if unit is None else "a " + unit["type"]}, the design has {want} there')
        game.log(f'{job.name}: {sum(1 for _, _, unit in placed if unit)} of {len(job.towers)} towers stand in '
                 f'{job.where()}')
        results.append(placed)
    beat = maul.fresh_beat(game)
    for player in players:
        cost = sum(job.cost for job in jobs if job.player == player)
        spent = gold[player] - beat.gold(player)
        findings.check(spent == cost, f'p{player} spent {spent} gold on their designs, which cost {cost}')
    return results


def build_design(game, build, findings, player=RED, lane=None):
    """Builds one design for a player, in their own lane unless another is named; returns
    [(tower, spot, unit or None)]."""
    return build_designs(game, [(build, player, player if lane is None else lane)], findings)[0]


def make_build_test(path):
    build = json.loads(path.read_text())
    if build.get('format') != BUILD_FORMAT:
        raise ValueError(f'{path.name}: format is not {BUILD_FORMAT}')

    def test(game):
        findings = Findings()
        build_design(game, build, findings)
        try:
            game.log(f'screenshot: {game.screenshot(0)}')
        except TestFailed as error:
            game.log(f'no screenshot: {error}')
        findings.raise_if_any(build['name'])

    test.__doc__ = (f'{build["name"]} ({path.name}): {len(build["towers"])} towers of {" and ".join(build["races"])}, '
                    f'built in red\'s lane for its budget of {build["budget"]}.')
    return test


for _path in sorted(BUILDS.glob('*.maul-build.json')):
    _name = re.sub(r'\W+', '_', _path.name[:-len('.maul-build.json')].lower()).strip('_')
    globals()['test_build_' + _name] = make_build_test(_path)
