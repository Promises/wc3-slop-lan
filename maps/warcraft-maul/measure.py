"""Measurements, not checks: the damage towers really do, in a real game (measure/slop.toml).

The unit data's damage over cooldown misleads for the fastest towers - the engine cannot attack
faster than the attack animation lets it - so each tower is built for real, given a target with
life it cannot get through, and every hit it lands is summed over game time. The results go to
the run's log and to measure-<test>.csv in its artifacts. A test fails only when a tower never
hit, or could not be built.

Raw is the damage before the target's armor, as the map's damage triggers see it; dealt is what
the target lost. Spells (attack=false) are counted apart from attacks.
"""
import time

import maul
from builds import race_named
from maul import CREEPS, RED
from races import (ELEMENTALIST_BUILDER, ELEMENTS, Findings, SIPHON, UNCHARGED_RUNE, build_chain, build_in_red_lane,
                   give_gold, unit_at)
from slop import TestFailed, trace

# Other races' finishers, by the race that builds them and their name
FINISHERS = [('Human Town Hall', 'Dalaran Guard Tower'), ('Goblins', 'Goblin Blademaster'),
             ('Night Elf Ancient', 'Corrupted Illidan'), ('Giants Hall', 'Giant Revenant'),
             ('Gnoll Republic', 'Gnoll Assassin'), ('The Forsaken', 'Sylvanas Windrunner'),
             ('Dragons', 'Black Dragon'), ('Goblins', 'Tinker'), ('Goblins', 'Shredder'),
             ('Outland', 'Rend Blackhand'), ('Ice Troll Hut', 'Ice Troll King')]
# Far more life than any tower takes off in the window
TARGET_LIFE = 50_000_000
# How long the towers attack once every one of them has hit
MEASURE_SECONDS = 20
# Game time in the trace: ticks of 0.1 s
TICK = 0.1
# The Elementalists' level 2 rune (Siphon of two of an element) and level 3 (bought) by element
LEVEL_2 = {'Water': 'u034', 'Fire': 'u030', 'Nature': 'u02E', 'Air': 'u032', 'Death': 'u02A', 'Life': 'u02C'}
LEVEL_3 = {'Water': 'u035', 'Fire': 'u031', 'Nature': 'u02F', 'Air': 'u033', 'Death': 'u02B', 'Life': 'u02D'}
# The runes built for the Elementalist measurement: enough for a pair of most elements
RUNES = 14


def tick_of(line):
    return int(trace.LINE.match(line).group(2))


def place_target(game, spot, side, tower_range, air):
    """A target the tower reaches: next to it for a melee tower, a little further for the rest."""
    distance = 96 if tower_range <= 150 else 160
    kind = maul.AIR_TARGET if air else maul.GROUND_TARGET
    return game.create(CREEPS, kind, spot[0], spot[1] + side * distance, life=TARGET_LIFE, rooted=True)[0]['id']


def measure(game, towers, name):
    """Lets the towers attack their targets for MEASURE_SECONDS once each has hit, then sums every
    hit by tower. towers: {tower ref: label}. Returns the rows, and writes them to the log and a
    CSV in the run's artifacts."""
    before = len(game.events('hit'))

    def lines():
        return [line for line in game.events('hit')[before:]]

    def hit_by_all():
        sources = {trace.parse_fields(line).get('src') for line in lines()}
        return all(tower in sources for tower in towers)
    try:
        game.wait('every tower to hit', hit_by_all, 60)
    except TestFailed:
        pass
    start = len(game.events('hit'))
    time.sleep(MEASURE_SECONDS)
    window = game.events('hit')[start:]
    rows = []
    for tower, label in towers.items():
        own = [(tick_of(line), trace.parse_fields(line)) for line in window if f' src={tower} ' in line]
        attacks = [(t, f) for t, f in own if f.get('attack') == 'true']
        spells = [(t, f) for t, f in own if f.get('attack') != 'true']
        if own:
            first, last = own[0][0], own[-1][0]
            seconds = max((last - first) * TICK, TICK)
            # The hits after the first, over the time from the first to the last
            raw = sum(f.get('raw', 0) for _, f in attacks[1:]) / seconds if len(attacks) > 1 else 0
            dealt = sum(f.get('amount', 0) for _, f in own[1:]) / seconds
            rate = (len(attacks) - 1) / seconds if len(attacks) > 1 else 0
            spell = sum(f.get('amount', 0) for _, f in spells) / seconds
        else:
            raw = dealt = rate = spell = 0
        rows.append((label, round(raw), round(dealt), round(spell), round(rate, 2), len(attacks)))
    rows.sort(key=lambda row: -row[1])
    game.log(f'{"tower":38} {"raw dps":>9} {"dealt dps":>10} {"spell dps":>10} {"attacks/s":>10} {"hits":>6}')
    for label, raw, dealt, spell, rate, hits in rows:
        game.log(f'{label:38} {raw:>9} {dealt:>10} {spell:>10} {rate:>10} {hits:>6}')
    path = game.artifacts / f'measure-{name}.csv'
    path.write_text('tower,raw_dps,dealt_dps,spell_dps,attacks_per_s,attack_hits\n'
                    + ''.join(f'"{label}",{raw},{dealt},{spell},{rate},{hits}\n' for label, raw, dealt, spell, rate, hits in rows))
    return rows


def test_measure_finishers(game):
    """Other races' finishers, each on a lane slot of its own with a target it cannot kill."""
    findings = Findings()
    maul.start(game)
    give_gold(game, (RED,))
    game.watch(CREEPS)
    slots = maul.lane_slots((RED,))
    towers = {}
    for (race_name, tower_name), (_, lane, x, y, side) in zip(FINISHERS, slots):
        race = race_named(race_name)
        tower = next(t for t in race['towers'] if t['name'].split('] - ')[-1] == tower_name)
        chain = next(c for c in maul.upgrade_chains(race) if tower['id'] in c)
        chain = chain[:chain.index(tower['id']) + 1]
        built = build_chain(game, race, chain, (x, y), findings)
        if built is None:
            continue
        attack = tower['attacks']
        air_only = 'ground' not in attack['targets']
        place_target(game, (x, y), side, attack['range'], air_only)
        towers[built] = f'{tower_name} ({tower["id"]}, {tower.get("gold")}g)'
    rows = measure(game, towers, 'finishers')
    for label, raw, dealt, spell, rate, hits in rows:
        findings.check(hits > 0 or spell > 0, f'{label} never hit')
    findings.raise_if_any('measuring the finishers')


def test_measure_elementalist_runes(game):
    """The Elementalists' level 3 runes (a pair of runes of an element, siphoned into its level 2,
    then bought up), and a level 1 rune of each element that had no pair."""
    findings = Findings()
    maul.start(game)
    maul.pick(game, RED, 'I024')
    give_gold(game, (RED,))
    game.watch(CREEPS)
    placed = build_in_red_lane(game, ELEMENTALIST_BUILDER, [(f'rune {n + 1}', UNCHARGED_RUNE) for n in range(RUNES)],
                               findings)
    spots = [spot for _, spot in placed]
    game.wait('the runes to stand', lambda: all(unit_at(game, RED, spot, (UNCHARGED_RUNE,)) for spot in spots), 90)

    # What each rune can become, then pairs for as many elements as the rolls allow, Life first
    offers = {}
    for spot in spots:
        rune = unit_at(game, RED, spot, (UNCHARGED_RUNE,))
        levels = game.inspect(rune['id'], *ELEMENTS)
        offers[spot] = [ELEMENTS[a][0] for a in ELEMENTS if levels.get(a, 0) > 0]
    by_name = {name: (order, element) for name, order, element in ELEMENTS.values()}
    free, pairs, singles = set(spots), {}, {}
    for element in ('Life', 'Water', 'Nature', 'Air', 'Death', 'Fire'):
        able = [spot for spot in spots if spot in free and element in offers[spot]]
        if len(able) >= 2:
            pairs[element] = able[:2]
            free -= set(able[:2])
    for element in ('Life', 'Water', 'Nature', 'Air', 'Death', 'Fire'):
        able = [spot for spot in spots if spot in free and element in offers[spot]]
        if element not in pairs and able:
            singles[element] = able[0]
            free.discard(able[0])
    game.log(f'rune rolls: pairs {sorted(pairs)}, singles {sorted(singles)}')

    # Charge them
    charged = {spot: element for element, two in pairs.items() for spot in two}
    charged.update({spot: element for element, spot in singles.items()})
    for spot, element in charged.items():
        rune = unit_at(game, RED, spot, (UNCHARGED_RUNE,))
        game.order(RED, rune['id'], by_name[element][0])
    game.wait('the runes to take their element', lambda: all(unit_at(game, RED, spot, (by_name[element][1],))
                                                             for spot, element in charged.items()), 20)
    # Each pair: one siphons the other into the level 2 rune, which is bought up to level 3
    for element, (source_spot, target_spot) in pairs.items():
        source = unit_at(game, RED, source_spot, (by_name[element][1],))
        target = unit_at(game, RED, target_spot, (by_name[element][1],))
        game.order(RED, source['id'], SIPHON, target['id'])
    game.wait('the level 2 runes', lambda: all(unit_at(game, RED, two[0], (LEVEL_2[element],))
                                              for element, two in pairs.items()), 20)
    for element, two in pairs.items():
        level_2 = unit_at(game, RED, two[0], (LEVEL_2[element],))
        game.upgrade(RED, level_2['id'], LEVEL_3[element])
    game.wait('the level 3 runes', lambda: all(unit_at(game, RED, two[0], (LEVEL_3[element],))
                                              for element, two in pairs.items()), 90)

    # Targets, and the measurement
    towers = {}
    for element, two in pairs.items():
        tower = unit_at(game, RED, two[0], (LEVEL_3[element],))
        towers[tower['id']] = f'{element} Rune L3 ({LEVEL_3[element]})'
    for element, spot in singles.items():
        tower = unit_at(game, RED, spot, (by_name[element][1],))
        towers[tower['id']] = f'{element} Rune L1 ({by_name[element][1]})'
    ranges = {tower_id: game.inspect(tower_id).get('range', 160) for tower_id in towers}
    for tower_id in towers:
        unit = next(u for u in game.units(RED) if u['id'] == tower_id)
        place_target(game, (unit['x'], unit['y']), 1, ranges[tower_id], False)
    rows = measure(game, towers, 'elementalist-runes')
    for label, raw, dealt, spell, rate, hits in rows:
        findings.check(hits > 0, f'{label} never hit')
    findings.raise_if_any('measuring the Elementalist runes')
