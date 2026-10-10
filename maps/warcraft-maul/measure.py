"""Measurements, not checks: the damage towers really do, in a real game (measure/slop.toml).

The unit data's damage over cooldown misleads for the fastest towers - the engine cannot attack
faster than the attack animation lets it - so each tower is built for real, given a target with
life it cannot get through, and every hit it lands is summed over game time. The results go to
the run's log and to measure-<test>.csv in its artifacts. A test fails only when a tower never
hit, or could not be built.

Raw is the damage before the target's armor, as the map's damage triggers see it; dealt is what
the target lost. Spells (attack=false) are counted apart from attacks.
"""
import math
import time

import maul
from builds import race_named
from maul import BLUE, CREEPS, RED
from races import (COIN, DEPLETED_ROCK, ELEMENTALIST_BUILDER, ELEMENTS, Findings, KOINZ, LANE_SPACING, LOOT_BOXER,
                   LOOTBAG, ROCKS, SIPHON, STICK, UNCHARGED_RUNE, build_chain, build_in_red_lane, give_gold,
                   red_lane_spots, unit_at)
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


# The combinations measured, each from two charged runes (the first siphons the second): what it
# becomes, and what it is bought up to, if anything
COMBINATIONS = [('Sandstorm', 'Nature', 'Air', 'u024', 'u03D'), ('Undead', 'Life', 'Death', 'n026', None),
                ('Sapling', 'Life', 'Nature', 'u021', None), ('Air Rune', 'Air', 'Air', LEVEL_2['Air'], LEVEL_3['Air'])]
SANDSTORM_TARGETS = 5
# Rounds played before measuring: a Sapling becomes a Tree on the 6th; Undead grows each one
ROUNDS = 6
TREE = 'u036'
# Undead's upgrade to Level 2 (A0E6, from "Charge Gold and Lumber"): its order, found with the
# library's .orders (852630)
UNDEAD_UPGRADE_ORDERS = ('neutralspell',)
UNDEAD_2 = 'u038'


def assign_runes(offers, needs):
    """Runes for each need (a list of elements, one per rune), the scarcest elements first:
    {need index: [spot, ...]} for the needs that could be met."""
    free = set(offers)
    counts = {}
    for elements in offers.values():
        for element in elements:
            counts[element] = counts.get(element, 0) + 1
    met = {}
    for index, elements in sorted(enumerate(needs), key=lambda item: min(counts.get(e, 0) for e in item[1])):
        chosen = []
        for element in elements:
            spot = next((s for s in sorted(free) if element in offers[s]), None)
            if spot is None:
                break
            chosen.append(spot)
            free.discard(spot)
        if len(chosen) == len(elements):
            met[index] = chosen
        else:
            free.update(chosen)
    return met


def play_round(game):
    """One wave in Debug mode, started and killed off, so every end-of-round tower acts once."""
    maul.start_wave(game)
    game.wait_for('the wave to spawn', lambda b: (b.get('creeps') or 0) > 0, 30)
    time.sleep(6)
    game.cmd(RED, '-killall')
    game.wait_for('the wave to end', lambda b: b.get('spawning') is False, 60)


def test_measure_elementalist_combinations(game):
    """Sandstorm L2 (on 5 targets), Undead after ROUNDS rounds (and its upgrade, if an order takes
    it), a Tree grown from a Sapling over ROUNDS rounds, and Air Rune L3."""
    findings = Findings()
    maul.start(game)
    maul.pick(game, RED, 'I024')
    give_gold(game, (RED,))
    game.cmd(RED, '-lives 1000000')
    game.watch(CREEPS)
    needs = [[first, second] for _, first, second, _, _ in COMBINATIONS]
    placed = build_in_red_lane(game, ELEMENTALIST_BUILDER, [(f'rune {n + 1}', UNCHARGED_RUNE) for n in range(20)],
                               findings)
    spots = [spot for _, spot in placed]
    game.wait('the runes to stand', lambda: all(unit_at(game, RED, spot, (UNCHARGED_RUNE,)) for spot in spots), 90)
    offers = {}
    for spot in spots:
        rune = unit_at(game, RED, spot, (UNCHARGED_RUNE,))
        levels = game.inspect(rune['id'], *ELEMENTS)
        offers[spot] = [ELEMENTS[a][0] for a in ELEMENTS if levels.get(a, 0) > 0]
    met = assign_runes(offers, needs)
    game.log('made: ' + ', '.join(COMBINATIONS[i][0] for i in sorted(met)) + '; not rolled: '
             + (', '.join(c[0] for i, c in enumerate(COMBINATIONS) if i not in met) or 'none'))
    by_name = {name: (order, element) for name, order, element in ELEMENTS.values()}

    # Charge, siphon, buy up
    charged = {spot: element for i, two in met.items() for spot, element in zip(two, needs[i])}
    for spot, element in charged.items():
        game.order(RED, unit_at(game, RED, spot, (UNCHARGED_RUNE,))['id'], by_name[element][0])
    game.wait('the runes to take their element', lambda: all(unit_at(game, RED, spot, (by_name[element][1],))
                                                             for spot, element in charged.items()), 20)
    for i, (source_spot, target_spot) in met.items():
        source = unit_at(game, RED, source_spot, (by_name[needs[i][0]][1],))
        target = unit_at(game, RED, target_spot, (by_name[needs[i][1]][1],))
        game.order(RED, source['id'], SIPHON, target['id'])
    game.wait('the fusions', lambda: all(unit_at(game, RED, two[0], (COMBINATIONS[i][3],)) for i, two in met.items()), 20)
    for i, two in met.items():
        bought = COMBINATIONS[i][4]
        if bought:
            game.upgrade(RED, unit_at(game, RED, two[0], (COMBINATIONS[i][3],))['id'], bought)
    game.wait('the bought upgrades', lambda: all(unit_at(game, RED, two[0], (COMBINATIONS[i][4],))
                                                 for i, two in met.items() if COMBINATIONS[i][4]), 90)

    # Undead's own upgrade, if one of the orders takes it
    undead_index = next((i for i in met if COMBINATIONS[i][0] == 'Undead'), None)
    undead_type = 'n026'
    if undead_index is not None:
        spot = met[undead_index][0]
        for order in UNDEAD_UPGRADE_ORDERS:
            before = len(game.events('order'))
            game.order(RED, unit_at(game, RED, spot, (undead_type,))['id'], order)
            game.wait(f'the {order} order', lambda: any(order in line for line in game.events('order')[before:]), 10)
            time.sleep(2)
            if unit_at(game, RED, spot, (UNDEAD_2,)):
                undead_type = UNDEAD_2
                game.log(f'Undead upgraded with the order {order!r}')
                break
        else:
            game.log(f'no order of {UNDEAD_UPGRADE_ORDERS} upgraded the Undead; it stays at level 1')

    # Rounds: the Sapling grows into a Tree, the Undead gains damage each one
    for round_number in range(1, ROUNDS + 1):
        play_round(game)
        if undead_index is not None:
            undead = unit_at(game, RED, met[undead_index][0], (undead_type,))
            if undead:
                now = game.inspect(undead['id'])
                game.log(f'round {round_number}: {undead_type} damage {now.get("damage_min")}-{now.get("damage_max")}')

    # Targets, and the measurement
    towers = {}
    final = {i: (TREE if COMBINATIONS[i][0] == 'Sapling' else undead_type if COMBINATIONS[i][0] == 'Undead'
                 else COMBINATIONS[i][4] or COMBINATIONS[i][3]) for i in met}
    for i, two in met.items():
        tower = unit_at(game, RED, two[0], (final[i],))
        if tower is None:
            findings.append(f'{COMBINATIONS[i][0]} is not a {final[i]} after {ROUNDS} rounds')
            continue
        towers[tower['id']] = f'{COMBINATIONS[i][0]} ({final[i]})'
        attack_range = game.inspect(tower['id']).get('range', 160)
        count = SANDSTORM_TARGETS if COMBINATIONS[i][0] == 'Sandstorm' else 1
        for n in range(count):
            place_target(game, (tower['x'] + 48 * (n - count // 2), tower['y']), 1, attack_range, False)
    rows = measure(game, towers, 'elementalist-combinations')
    for label, raw, dealt, spell, rate, hits in rows:
        findings.check(hits > 0, f'{label} never hit')
    findings.raise_if_any('measuring the Elementalist combinations')


# The Lich (the Elementalist redesign's first Primal): Undead L2 + Death Rune L3, for 250 gold; it
# gains LICH_GROWTH a wave
LICH, LICH_FEE, LICH_GROWTH, DEATH_3, UNDEAD = 'uP01', 250, 100, LEVEL_3['Death'], 'n026'
UNDEAD_UPGRADE = 'neutralspell'


def damage(game, unit_id):
    return game.inspect(unit_id).get('damage_min', 0)


def forget_recipes(game):
    """Empties red's recipe book (the debug command -forget), so a Primal is revealed before it is made."""
    before = len(game.events('book'))
    game.cmd(RED, '-forget')
    game.wait('the recipe book to empty', lambda: any(' book p0 forgot' in line for line in game.events('book')[before:]), 10)


def reveal(game, source, target, result, findings):
    """The first Siphon on an undiscovered Primal's pair: it shows the Primal, and spends nothing."""
    gold = maul.fresh_beat(game).gold(RED)
    before = len(game.events('siphon'))
    game.order(RED, source, SIPHON, target)
    try:
        game.wait(f'{result} to be revealed',
                  lambda: any(f' siphon p0 revealed {result}' in line for line in game.events('siphon')[before:]), 10)
    except TestFailed as error:
        findings.append(f'revealing {result}: {error}')
        return
    time.sleep(2)
    findings.check(not any(' siphon p0 made' in line for line in game.events('siphon')[before:]), f'revealing {result} made it')
    findings.check(maul.fresh_beat(game).gold(RED) == gold, f'revealing {result} spent gold')
    units = {u['id'] for u in game.units(RED)}
    findings.check(source in units and target in units, f'revealing {result} used up a tower')


def test_lich(game):
    """The Lich prototype: Undead L2 gains 15 a round; Siphon onto a Death Rune L3 is refused below
    its 250 gold fee; with the gold it makes a Lich that keeps all of Undead's damage, and the Lich
    then gains at least LICH_GROWTH a round. Then its damage per second."""
    findings = Findings()
    maul.start(game)
    maul.pick(game, RED, 'I024')
    give_gold(game, (RED,))
    forget_recipes(game)
    game.cmd(RED, '-lives 1000000')
    game.watch(CREEPS)
    # Undead and a Death Rune L2 put down as built: rolling them from runes needs three runes to
    # offer Death, which 16 runes miss about one time in 16 (making a fusion from runes is
    # test_thunderhead's)
    undead_spot, death_spot = spread_spots(2, 900)
    place_tower(game, UNDEAD, undead_spot)
    place_tower(game, LEVEL_2['Death'], death_spot)
    game.upgrade(RED, unit_at(game, RED, death_spot, (LEVEL_2['Death'],))['id'], DEATH_3)
    game.order(RED, unit_at(game, RED, undead_spot, (UNDEAD,))['id'], UNDEAD_UPGRADE)
    game.wait('Undead L2 and Death Rune L3', lambda: unit_at(game, RED, undead_spot, (UNDEAD_2,))
              and unit_at(game, RED, death_spot, (DEATH_3,)), 60)

    # Undead L2 grows 15 a round
    undead = unit_at(game, RED, undead_spot, (UNDEAD_2,))['id']
    grown = [damage(game, undead)]
    for _ in range(3):
        play_round(game)
        grown.append(damage(game, undead))
    game.log(f'Undead L2 damage by round: {grown}')
    findings.check(all(b - a >= 15 for a, b in zip(grown, grown[1:])), f'Undead L2 did not gain 15 a round: {grown}')

    # Short of the fee: the first Siphon reveals the Lich, the next is refused
    game.cmd(RED, '.gold 100')
    game.wait_for('the gold to be 100', lambda beat: beat.gold(RED) == 100)
    reveal(game, undead, unit_at(game, RED, death_spot, (DEATH_3,))['id'], LICH, findings)
    before = len(game.events('siphon'))
    game.order(RED, undead, SIPHON, unit_at(game, RED, death_spot, (DEATH_3,))['id'])
    try:
        game.wait('the fusion to be refused', lambda: any(' siphon p0 refused' in line for line in game.events('siphon')[before:]), 10)
    except TestFailed as error:
        findings.append(f'with 100 gold: {error}')
    findings.check(unit_at(game, RED, undead_spot, (UNDEAD_2,)) is not None, 'the Undead fused for 100 gold')

    # With the gold: a Lich that keeps the Undead's damage
    game.cmd(RED, '.gold 1000')
    game.wait_for('the gold to be 1000', lambda beat: beat.gold(RED) == 1000)
    kept = damage(game, undead)
    game.order(RED, undead, SIPHON, unit_at(game, RED, death_spot, (DEATH_3,))['id'])
    game.wait('the Lich', lambda: unit_at(game, RED, undead_spot, (LICH,)), 15)
    lich = unit_at(game, RED, undead_spot, (LICH,))['id']
    findings.check(unit_at(game, RED, death_spot, (DEPLETED_ROCK,)) is not None, 'the Death Rune L3 did not turn to rock')
    findings.check(maul.fresh_beat(game).gold(RED) == 1000 - LICH_FEE, f'the fusion did not take {LICH_FEE} gold')
    made = damage(game, lich)
    findings.check(made >= kept, f'the Lich has {made} damage, the Undead had {kept}')
    lich_grown = [made]
    for _ in range(2):
        play_round(game)
        lich_grown.append(damage(game, lich))
    game.log(f'Undead L2 {kept} became a Lich with {made}; by round: {lich_grown}')
    findings.check(all(b - a >= LICH_GROWTH for a, b in zip(lich_grown, lich_grown[1:])),
                   f'the Lich did not gain {LICH_GROWTH} a round: {lich_grown}')

    unit = next(u for u in game.units(RED) if u['id'] == lich)
    place_target(game, (unit['x'], unit['y']), 1, 600, False)
    rows = measure(game, {lich: f'Lich ({LICH}) after 5 rounds'}, 'lich')
    findings.check(rows and rows[0][5] > 0, 'the Lich never hit')
    findings.raise_if_any('the Lich')


# Thunderhead: Air Rune L3 + Water Rune L3, 600 gold, air only
THUNDERHEAD, THUNDERHEAD_FEE = 'uP02', 600


def rune_pairs(game, needs, count):
    """Builds `count` runes and makes a pair of runes for each need ([element, element]) the rolls
    allow, charged: {need index: (source spot, target spot)}."""
    findings = Findings()
    placed = build_in_red_lane(game, ELEMENTALIST_BUILDER, [(f'rune {n + 1}', UNCHARGED_RUNE) for n in range(count)],
                               findings)
    findings.raise_if_any('building the runes')
    spots = [spot for _, spot in placed]
    game.wait('the runes to stand', lambda: all(unit_at(game, RED, spot, (UNCHARGED_RUNE,)) for spot in spots), 120)
    offers = {}
    for spot in spots:
        levels = game.inspect(unit_at(game, RED, spot, (UNCHARGED_RUNE,))['id'], *ELEMENTS)
        offers[spot] = [ELEMENTS[a][0] for a in ELEMENTS if levels.get(a, 0) > 0]
    met = assign_runes(offers, needs)
    by_name = {name: (order, element) for name, order, element in ELEMENTS.values()}
    charged = {spot: element for i, two in met.items() for spot, element in zip(two, needs[i])}
    for spot, element in charged.items():
        game.order(RED, unit_at(game, RED, spot, (UNCHARGED_RUNE,))['id'], by_name[element][0])
    game.wait('the runes to take their element', lambda: all(unit_at(game, RED, spot, (by_name[element][1],))
                                                             for spot, element in charged.items()), 20)
    return met


def level_3(game, element, pair):
    """A pair of charged runes of an element siphoned into its Level 2 and bought up to Level 3, on
    the pair's first spot."""
    rune = {name: element_type for name, _, element_type in ELEMENTS.values()}[element]
    source, target = (unit_at(game, RED, spot, (rune,)) for spot in pair)
    game.order(RED, source['id'], SIPHON, target['id'])
    game.wait(f'the {element} Rune L2', lambda: unit_at(game, RED, pair[0], (LEVEL_2[element],)), 20)
    game.upgrade(RED, unit_at(game, RED, pair[0], (LEVEL_2[element],))['id'], LEVEL_3[element])
    game.wait(f'the {element} Rune L3', lambda: unit_at(game, RED, pair[0], (LEVEL_3[element],)), 60)
    return unit_at(game, RED, pair[0], (LEVEL_3[element],))


def test_thunderhead(game):
    """Thunderhead: Air Rune L3 siphons a Water Rune L3 - refused below its 600 gold fee, made with
    it; it attacks air and never ground. Then its damage per second against air."""
    findings = Findings()
    maul.start(game)
    maul.pick(game, RED, 'I024')
    give_gold(game, (RED,))
    forget_recipes(game)
    game.watch(CREEPS)
    needs = [['Air', 'Air'], ['Water', 'Water']]
    met = rune_pairs(game, needs, 16)
    if 0 not in met or 1 not in met:
        raise TestFailed('the runes rolled no two Air and two Water')
    air, water = level_3(game, 'Air', met[0]), level_3(game, 'Water', met[1])

    def fuse(source, target):
        game.order(RED, source['id'], SIPHON, target['id'])

    def refused(before):
        return any(' siphon p0 refused' in line for line in game.events('siphon')[before:])

    game.cmd(RED, '.gold 100')
    game.wait_for('the gold to be 100', lambda beat: beat.gold(RED) == 100)
    reveal(game, air['id'], water['id'], THUNDERHEAD, findings)
    before = len(game.events('siphon'))
    fuse(air, water)
    try:
        game.wait('the fusion to be refused', lambda: refused(before), 10)
    except TestFailed as error:
        findings.append(f'with 100 gold: {error}')

    game.cmd(RED, '.gold 10000')
    game.wait_for('the gold to be 10000', lambda beat: beat.gold(RED) == 10000)
    fuse(air, water)
    game.wait('the Thunderhead', lambda: unit_at(game, RED, met[0][0], (THUNDERHEAD,)), 15)
    findings.check(maul.fresh_beat(game).gold(RED) == 10000 - THUNDERHEAD_FEE, f'the fusion did not take {THUNDERHEAD_FEE} gold')
    thunderhead = unit_at(game, RED, met[0][0], (THUNDERHEAD,))

    # Air only: a ground target next to it is never hit
    ground = place_target(game, (thunderhead['x'], thunderhead['y']), -1, 128, False)
    place_target(game, (thunderhead['x'], thunderhead['y']), 1, 1000, True)
    before = len(game.events('hit'))
    rows = measure(game, {thunderhead['id']: f'Thunderhead ({THUNDERHEAD})'}, 'thunderhead')
    # Its own hits: other towers nearby may hit ground
    hits_on_ground = [line for line in game.events('hit')[before:]
                      if f' src={thunderhead["id"]} ' in line and f' dst={ground} ' in line]
    findings.check(not hits_on_ground, f'Thunderhead hit a ground target {len(hits_on_ground)} times')
    findings.check(rows and rows[0][5] > 0, 'Thunderhead never hit')
    findings.raise_if_any('Thunderhead')


# Ascension: a Primal at Attunement 15 ascends for a fee; Surge buys Attunement, 60 a level, 10 at most
EYE_OF_THE_STORM, EYE_FEE, ASCEND, SURGE, SURGE_PRICE, SURGE_LIMIT = 'uA02', 500, 'avatar', 'berserk', 60, 10
# The Eye's base damage before Attunement (its object data); Ascended growth is 5% of it a wave
EYE_BASE = 1576


def make_thunderhead(game):
    """A Thunderhead from rune pairs, with the gold for it, revealed first from an empty book; its spot."""
    forget_recipes(game)
    met = rune_pairs(game, [['Air', 'Air'], ['Water', 'Water']], 16)
    if 0 not in met or 1 not in met:
        raise TestFailed('the runes rolled no two Air and two Water')
    air, water = level_3(game, 'Air', met[0]), level_3(game, 'Water', met[1])
    findings = Findings()
    reveal(game, air['id'], water['id'], THUNDERHEAD, findings)
    findings.raise_if_any('revealing the Thunderhead')
    game.order(RED, air['id'], SIPHON, water['id'])
    game.wait('the Thunderhead', lambda: unit_at(game, RED, met[0][0], (THUNDERHEAD,)), 15)
    return met[0][0]


def test_ascension(game):
    """Surge and Ascend on a Thunderhead: Surge buys 10 levels of Attunement for 60 each and no more;
    Ascend is refused below Attunement 15; five waves later it ascends to the Eye of the Storm for
    500, which then gains about 5% of its base damage a wave. Then its damage per second."""
    findings = Findings()
    maul.start(game)
    maul.pick(game, RED, 'I024')
    give_gold(game, (RED,))
    game.cmd(RED, '-lives 1000000')
    game.watch(CREEPS)
    spot = make_thunderhead(game)

    def notes(kind, since):
        return [line.split(f' {kind} ', 1)[1] for line in game.events(kind)[since:]]

    # Surge: ten levels, then no more
    game.cmd(RED, '.gold 10000')
    game.wait_for('the gold to be 10000', lambda beat: beat.gold(RED) == 10000)
    before = len(game.events('surge'))
    for n in range(SURGE_LIMIT + 1):
        game.order(RED, unit_at(game, RED, spot, (THUNDERHEAD,))['id'], SURGE)
        game.wait(f'surge {n + 1}', lambda: len(game.events('surge')) > before + n, 10)
    surges = notes('surge', before)
    game.log('surge: ' + ' | '.join(surges))
    findings.check(sum(1 for n in surges if n.startswith('p0 attunement=')) == SURGE_LIMIT,
                   f'Surge did not buy {SURGE_LIMIT} levels: {surges}')
    findings.check(surges[-1].startswith('p0 refused'), f'an 11th Surge was not refused: {surges[-1]}')
    findings.check(maul.fresh_beat(game).gold(RED) == 10000 - SURGE_LIMIT * SURGE_PRICE,
                   f'Surge did not take {SURGE_PRICE} a level')

    # Ascend: refused at Attunement 10
    before = len(game.events('ascend'))
    game.order(RED, unit_at(game, RED, spot, (THUNDERHEAD,))['id'], ASCEND)
    try:
        game.wait('the ascension to be refused', lambda: any('refused' in n for n in notes('ascend', before)), 10)
    except TestFailed as error:
        findings.append(f'at Attunement 10: {error}')

    # Five waves to Attunement 15, then it ascends
    for _ in range(5):
        play_round(game)
    gold = maul.fresh_beat(game).gold(RED)
    before = len(game.events('ascend'))
    game.order(RED, unit_at(game, RED, spot, (THUNDERHEAD,))['id'], ASCEND)
    try:
        game.wait('the Eye of the Storm', lambda: unit_at(game, RED, spot, (EYE_OF_THE_STORM,)), 15)
    except TestFailed as error:
        findings.append(f'{error}: {notes("ascend", before)}')
        findings.raise_if_any('Ascension')
    findings.check(maul.fresh_beat(game).gold(RED) == gold - EYE_FEE, f'Ascension did not take {EYE_FEE} gold')
    eye = unit_at(game, RED, spot, (EYE_OF_THE_STORM,))['id']
    grown = [damage(game, eye)]
    for _ in range(2):
        play_round(game)
        grown.append(damage(game, eye))
    game.log(f'Eye of the Storm damage by wave: {grown}')
    # 5% of its base a wave, give or take the rounding of the total
    gain = EYE_BASE * 0.05
    findings.check(all(abs(b - a - gain) <= 1 for a, b in zip(grown, grown[1:])),
                   f'it did not gain 5% of its base ({gain:.0f}) a wave: {grown}')

    unit = next(u for u in game.units(RED) if u['id'] == eye)
    place_target(game, (unit['x'], unit['y']), 1, 1100, True)
    rows = measure(game, {eye: f'Eye of the Storm ({EYE_OF_THE_STORM}) 2 waves after'}, 'ascension')
    findings.check(rows and rows[0][5] > 0, 'the Eye of the Storm never hit')
    findings.raise_if_any('Ascension')



# What the game lets a map change on a unit while it plays: the question for fusions generated
# from their ingredients, rather than one unit type each. A plain ranged tower (an Arrow Tower:
# missile, pierce, range 900, 1 s) is changed one property at a time with the library's .lua,
# and what it then does is watched: each probe notes the value read back and what its hits show.
# The tower is kept in a Lua global (T) so each line stays well under a sync message's 255 bytes.
PROBE_TOWER, AIR_ONLY_TOWER, WEAPONLESS, MULTI_ATTACK = 'h042', 'h00L', 'hC66', 'A0E4'
PROBE_SECONDS = 6


def test_runtime_fields(game):
    """Which unit, weapon and ability properties take effect when changed during a game."""
    maul.start(game)
    give_gold(game, (RED,))
    game.watch(CREEPS)
    spot = red_lane_spots()[5]
    tower = game.create(RED, PROBE_TOWER, *spot)[0]['id']
    dalaran = game.create(RED, AIR_ONLY_TOWER, spot[0] + 1500, spot[1])[0]['id']
    near = place_target(game, spot, 1, 900, False)
    report = []

    def lua(code):
        if len(code) > 200:
            raise TestFailed(f'a .lua line of {len(code)} characters: {code}')
        before = len(game.events('lua'))
        game.cmd(RED, '.lua ' + code)
        game.wait('the line to run', lambda: any(' lua ok' in line or ' lua error' in line for line in game.events('lua')[before:]), 15)
        return [line.split(' lua ', 1)[1] for line in game.events('lua')[before:]][-1]

    def watch(seconds=PROBE_SECONDS):
        """The tower's hits over the next seconds: [(tick, fields)]."""
        start = len(game.events('hit'))
        time.sleep(seconds)
        return [(tick_of(line), trace.parse_fields(line)) for line in game.events('hit')[start:] if f' src={tower} ' in line]

    def summary(hits):
        attacks = [t for t, f in hits if f.get('attack') == 'true']
        rate = (len(attacks) - 1) / max((attacks[-1] - attacks[0]) * TICK, TICK) if len(attacks) > 1 else 0
        targets = {}
        for t, f in hits:
            targets.setdefault(t, set()).add(f.get('dst'))
        return (f'{len(hits)} hits, {rate:.2f} attacks/s, raw {sorted({f.get("raw") for _, f in hits})[:3]}, '
                f'atk {sorted({str(f.get("atk")) for _, f in hits})}, most targets in a tick {max(map(len, targets.values()), default=0)}')

    def probe(name, *lines, evidence=None):
        read = [lua(line) for line in lines][-1]
        seen = (evidence or (lambda: summary(watch())))()
        report.append((name, read, seen))
        game.log(f'{name}: read back {read}; {seen}')

    lua(f'_G.T = unit({tower}) _G.D = unit({dalaran}) return T ~= nil and D ~= nil')
    report.append(('(before)', '', summary(watch())))

    probe('base damage 500', 'BlzSetUnitBaseDamage(T, 500, 0) return BlzGetUnitBaseDamage(T, 0)')
    probe('cooldown 0.3', 'BlzSetUnitAttackCooldown(T, 0.3, 0) return BlzGetUnitAttackCooldown(T, 0)')
    probe('attack type 5 (weapon field)', 'BlzSetUnitWeaponIntegerField(T, UNIT_WEAPON_IF_ATTACK_ATTACK_TYPE, 0, 5) '
          'return BlzGetUnitWeaponIntegerField(T, UNIT_WEAPON_IF_ATTACK_ATTACK_TYPE, 0)')
    probe('projectile art (Farseer)', 'local f = UNIT_WEAPON_SF_ATTACK_PROJECTILE_ART '
          'BlzSetUnitWeaponStringField(T, f, 0, "Abilities\\\\Weapons\\\\FarseerMissile\\\\FarseerMissile.mdl") '
          'return BlzGetUnitWeaponStringField(T, f, 0)')
    probe('projectile speed 300', 'local f = UNIT_WEAPON_RF_ATTACK_PROJECTILE_SPEED BlzSetUnitWeaponRealField(T, f, 0, 300) '
          'return BlzGetUnitWeaponRealField(T, f, 0)')
    probe(f'skin ({AIR_ONLY_TOWER})', f'BlzSetUnitSkin(T, FourCC("{AIR_ONLY_TOWER}")) '
          'local i = GetUnitTypeId(T) return BlzGetUnitSkin(T) .. " type " .. i')
    probe('name, scale 1.6, colour red', 'BlzSetUnitName(T, "Probe") SetUnitScale(T, 1.6, 1.6, 1.6) '
          'SetUnitVertexColor(T, 255, 60, 60, 255) return GetUnitName(T)')
    try:
        game.log(f'the look: {game.screenshot()}')
    except TestFailed as problem:
        game.log(f'no screenshot: {problem}')

    # Range: the near target goes, one stands beyond 900; the range and acquisition are raised
    game.remove(CREEPS, near)
    far = game.create(CREEPS, maul.GROUND_TARGET, spot[0], spot[1] + 1400, life=TARGET_LIFE, rooted=True)[0]['id']
    probe('range as built, target 1,400 away', 'return BlzGetUnitWeaponRealField(T, UNIT_WEAPON_RF_ATTACK_RANGE, 0)')
    probe('range 1,800 (index 0) + acquisition', 'local f = UNIT_WEAPON_RF_ATTACK_RANGE BlzSetUnitWeaponRealField(T, f, 0, 1800) '
          'SetUnitAcquireRange(T, 1800) return BlzGetUnitWeaponRealField(T, f, 0)')
    probe('range 1,800 (index 1)', 'local f = UNIT_WEAPON_RF_ATTACK_RANGE BlzSetUnitWeaponRealField(T, f, 1, 1800) '
          'return BlzGetUnitWeaponRealField(T, f, 1) .. " / 0: " .. BlzGetUnitWeaponRealField(T, f, 0)')
    game.remove(CREEPS, far)

    # Targets allowed: the Dalaran's value (air only) onto the tower, with a ground and an air target
    ground = place_target(game, spot, 1, 900, False)
    air = place_target(game, spot, -1, 900, True)

    def by_kind():
        hits = watch()
        return (f'{sum(1 for _, f in hits if f.get("dst") == ground)} on ground, '
                f'{sum(1 for _, f in hits if f.get("dst") == air)} on air')
    probe('both targets, allowed as built', 'return BlzGetUnitWeaponIntegerField(T, UNIT_WEAPON_IF_ATTACK_TARGETS_ALLOWED, 0)',
          evidence=by_kind)
    probe('allowed: the Dalaran\'s (air only)', '_G.A = BlzGetUnitWeaponIntegerField(T, UNIT_WEAPON_IF_ATTACK_TARGETS_ALLOWED, 0) return A',
          'local f = UNIT_WEAPON_IF_ATTACK_TARGETS_ALLOWED BlzSetUnitWeaponIntegerField(T, f, 0, BlzGetUnitWeaponIntegerField(D, f, 0)) '
          'return BlzGetUnitWeaponIntegerField(T, f, 0)', evidence=by_kind)
    probe('allowed: back', 'local f = UNIT_WEAPON_IF_ATTACK_TARGETS_ALLOWED BlzSetUnitWeaponIntegerField(T, f, 0, A) '
          'return BlzGetUnitWeaponIntegerField(T, f, 0)', evidence=by_kind)

    # An ability added, and one of its fields changed: Sandstorm's Multi-Attack (Barrage)
    for dx in (-144, -96, -48, 48, 96, 144):
        game.create(CREEPS, maul.GROUND_TARGET, spot[0] + dx, spot[1] + 160, life=TARGET_LIFE, rooted=True)
    probe('seven targets, no Multi-Attack', f'return GetUnitAbilityLevel(T, FourCC("{MULTI_ATTACK}"))')
    probe('Multi-Attack added', f'return UnitAddAbility(T, FourCC("{MULTI_ATTACK}"))')
    probe('its targets field -> 2', f'_G.M = BlzGetUnitAbility(T, FourCC("{MULTI_ATTACK}")) return M ~= nil',
          'local f = ABILITY_ILF_MAXIMUM_NUMBER_OF_TARGETS_EFK3 BlzSetAbilityIntegerLevelField(M, f, 0, 2) '
          'return BlzGetAbilityIntegerLevelField(M, f, 0)')
    probe('ability tooltip on this unit\'s ability', f'local a = FourCC("{MULTI_ATTACK}") BlzSetAbilityTooltip(a, "Probe tip", 0) '
          'return BlzGetAbilityTooltip(a, 0)', evidence=lambda: '')
    probe('attacks off', 'BlzSetUnitWeaponBooleanField(T, UNIT_WEAPON_BF_ATTACKS_ENABLED, 0, false) '
          'return BlzGetUnitWeaponBooleanField(T, UNIT_WEAPON_BF_ATTACKS_ENABLED, 0)')

    # A tower with no weapon in its data (the Human Tower shell): switching one on
    shell_spot = red_lane_spots()[9]
    shell = game.create(RED, WEAPONLESS, *shell_spot)[0]['id']
    place_target(game, shell_spot, 1, 900, False)

    def shell_hits():
        start = len(game.events('hit'))
        time.sleep(PROBE_SECONDS)
        return f"{sum(1 for line in game.events('hit')[start:] if f' src={shell} ' in line)} hits"
    probe('weaponless: attacks on, damage, cooldown, range', f'_G.S = unit({shell}) BlzSetUnitWeaponBooleanField(S, UNIT_WEAPON_BF_ATTACKS_ENABLED, 0, true) '
          'return BlzGetUnitWeaponBooleanField(S, UNIT_WEAPON_BF_ATTACKS_ENABLED, 0)',
          'BlzSetUnitBaseDamage(S, 100, 0) BlzSetUnitAttackCooldown(S, 1, 0) '
          'BlzSetUnitWeaponRealField(S, UNIT_WEAPON_RF_ATTACK_RANGE, 0, 900) return BlzGetUnitBaseDamage(S, 0)',
          evidence=shell_hits)

    path = game.artifacts / 'runtime-fields.txt'
    path.write_text(''.join(f'{name}\n  read back: {read}\n  seen: {seen}\n' for name, read, seen in report))
    game.log(f'report: {path}')


# The recipe book and its save: a Primal revealed in one game is known in the next


def save_files(game, client):
    return sorted((game.folders[client] / 'WarcraftMaul').glob('save-*.txt'))


def loaded(game, player, client, since=0):
    """The 'save' trace line of a player's save being applied on a client, or None."""
    return next((line for line in game.events('save', client)[since:] if f' save p{player} loaded ' in line
                 or f' save p{player} rejected ' in line), None)


def book_size(game, player, client):
    """How many results a player's book held as their save loaded on a client, or None."""
    fields = book_loaded(game, player, client)
    return fields['known'] if fields else None


def book_loaded(game, player, client):
    """A player's book as their save loaded on a client: {known, given, ascended}, or None.
    The line reads "book p0 loaded 27 given=8 ascended=6"."""
    lines = [line for line in game.events('book', client) if f' book p{player} loaded ' in line]
    if not lines:
        return None
    words = lines[-1].split(f' book p{player} loaded ', 1)[1].split()
    fields = dict(word.split('=', 1) for word in words[1:])
    return {'known': int(words[0]), 'given': int(fields.get('given', 0)), 'ascended': int(fields.get('ascended', 0))}


def lua_true(game, code):
    """Whether a line of Lua returned true. A check of local things (frames) is written as "not the
    player this is about, or the check", so both clients note the same line when it holds and the
    clients' traces stay in step."""
    before = len(game.events('lua'))
    game.cmd(RED, '.lua ' + code)
    game.wait('the Lua line', lambda: len(game.events('lua')) > before, 10)
    return game.events('lua')[before].split(' lua ', 1)[1] == 'ok true'


def elementalist_game(game, name):
    """A new game with red on the Elementalist."""
    game.next_game(name)
    maul.start(game)
    maul.pick(game, RED, 'I024')
    give_gold(game, (RED,))


def test_recipe_discovery(game):
    """Discovery and the save: with no save file, red's book starts empty; Siphon on a pair with no
    recipe says so and hints; the first Siphon on Air L3 + Water L3 reveals the Thunderhead for free,
    and red's save file is written. The next game loads the save on both clients, and one Siphon
    makes the Thunderhead. A save edited by hand is rejected, and the book starts empty."""
    findings = Findings()
    for client in range(len(game.folders)):
        for path in save_files(game, client):
            path.unlink()
    # A game whose start finds no save file: the first game a player ever plays
    elementalist_game(game, 'recipe-discovery-first')
    game.wait('the saves to load', lambda: loaded(game, 0, 0) and loaded(game, 1, 0), 20)
    # The save format keeps to 32-bit integer operations; which width the game's Lua has, for the log
    before = len(game.events('lua'))
    game.cmd(RED, '.lua return math.maxinteger')
    game.wait('the Lua integer width', lambda: len(game.events('lua')) > before, 10)
    game.log(f'Lua math.maxinteger: {game.events("lua")[-1].split(" lua ", 1)[1]}')
    findings.check(book_size(game, 0, 0) == 0, f'red\'s book did not start empty: {book_size(game, 0, 0)}')
    game.watch(CREEPS)
    met = rune_pairs(game, [['Air', 'Air'], ['Water', 'Water']], 16)
    if 0 not in met or 1 not in met:
        raise TestFailed('the runes rolled no two Air and two Water')
    air, water = level_3(game, 'Air', met[0]), level_3(game, 'Water', met[1])

    # No recipe: Air L3 onto an uncharged rune; Air L3 goes into the undiscovered Thunderhead
    spare = next((u for u in game.units(RED) if u['type'] == UNCHARGED_RUNE), None)
    if spare is None:
        findings.append('no uncharged rune left to miss with')
    else:
        before = len(game.events('siphon'))
        game.order(RED, air['id'], SIPHON, spare['id'])
        try:
            game.wait('the miss', lambda: any(' siphon p0 miss ' in line for line in game.events('siphon')[before:]), 10)
            miss = next(line for line in game.events('siphon')[before:] if ' siphon p0 miss ' in line)
            findings.check(f'hint={LEVEL_3["Air"]}' in miss, f'the miss gave no hint about the Air Rune L3: {miss}')
        except TestFailed as error:
            findings.append(f'Siphon onto an uncharged rune: {error}')

    reveal(game, air['id'], water['id'], THUNDERHEAD, findings)
    # The Level 2 runes made on the way are discovered too
    discovered = [line.rsplit(' ', 1)[1] for line in game.events('book') if ' book p0 discovered ' in line]
    findings.check(THUNDERHEAD in discovered, f'the reveal did not put the Thunderhead in the book: {discovered}')
    try:
        game.wait('red\'s save file', lambda: save_files(game, 0), 10)
        text = save_files(game, 0)[0].read_text(errors='replace')
        game.log(f'save file {save_files(game, 0)[0].name}: {len(text)} characters')
        findings.check('BlzSetAbilityTooltip' in text and THUNDERHEAD not in text, 'the save file is not scrambled save text')
    except TestFailed as error:
        findings.append(str(error))
    findings.check(not save_files(game, 1), 'blue changed nothing, yet has a save file')

    # The next game: the save loads on both clients, and one Siphon makes the Thunderhead
    elementalist_game(game, 'recipe-discovery-loaded')
    game.wait('the saves to load', lambda: loaded(game, 0, 0) and loaded(game, 0, 1), 20)
    for client in range(2):
        line = loaded(game, 0, client)
        findings.check(' loaded ' in line and 'sections=1' in line, f'client {client + 1}: red\'s save did not load: {line}')
        findings.check(book_size(game, 0, client) == len(discovered),
                       f'client {client + 1}: red\'s book holds {book_size(game, 0, client)}, not {len(discovered)} ({discovered})')
    # The recipe book: its button on red's action bar only, and -book opens it on red's screen only
    lua_true(game, '_G.F = function(name) return BlzGetFrameByName(name, 0) end return true')
    findings.check(lua_true(game, 'return GetLocalPlayer() ~= Player(0) or BlzFrameIsVisible(F("recipeBookButton5Well"))'),
                   'red, an Elementalist, has no recipe book button')
    findings.check(lua_true(game, 'return GetLocalPlayer() ~= Player(1) or not BlzFrameIsVisible(F("recipeBookButton5Well"))'),
                   'blue, not an Elementalist, has a recipe book button')
    game.cmd(RED, '-book')
    time.sleep(1)
    findings.check(lua_true(game, 'return GetLocalPlayer() ~= Player(0) or (BlzFrameIsVisible(F("recipeBookPanel")) and '
                                  'string.find(BlzFrameGetText(F("recipeBookName1")), "Thunderhead", 1, true) ~= nil)'),
                   'red\'s -book did not open a book that lists the Thunderhead')
    findings.check(lua_true(game, 'return GetLocalPlayer() ~= Player(1) or not BlzFrameIsVisible(F("recipeBookPanel"))'),
                   'red\'s -book opened the book on blue\'s screen')
    try:
        game.log(f'the book on red\'s screen: {game.screenshot(0)}')
    except TestFailed as problem:
        game.log(f'no screenshot: {problem}')
    game.cmd(RED, '-book')
    time.sleep(1)
    findings.check(lua_true(game, 'return not BlzFrameIsVisible(F("recipeBookPanel"))'), 'a second -book did not close the book')

    game.watch(CREEPS)
    met = rune_pairs(game, [['Air', 'Air'], ['Water', 'Water']], 16)
    if 0 in met and 1 in met:
        air, water = level_3(game, 'Air', met[0]), level_3(game, 'Water', met[1])
        before = len(game.events('siphon'))
        game.order(RED, air['id'], SIPHON, water['id'])
        try:
            game.wait('the Thunderhead in one Siphon', lambda: unit_at(game, RED, met[0][0], (THUNDERHEAD,)), 15)
        except TestFailed as error:
            findings.append(f'with the Thunderhead known: {error}')
        findings.check(not any(' revealed ' in line for line in game.events('siphon')[before:]), 'a known Primal was revealed again')
    else:
        findings.append('the second game\'s runes rolled no two Air and two Water')

    # A save edited by hand: rejected, the book starts empty
    if not save_files(game, 0):
        findings.append('red has no save file to edit')
        findings.raise_if_any('the recipe book')
    path = save_files(game, 0)[0]
    text = path.read_text(errors='replace')
    start = text.index('BlzSetAbilityTooltip(') + len("BlzSetAbilityTooltip('Amls', \"") + 3
    path.write_text(text[:start] + ('A' if text[start] != 'A' else 'B') + text[start + 1:])
    elementalist_game(game, 'recipe-discovery-edited')
    game.wait('the saves to load', lambda: loaded(game, 0, 0), 20)
    line = loaded(game, 0, 0)
    findings.check(' rejected signature' in line, f'an edited save was not rejected: {line}')
    findings.check(book_size(game, 0, 0) == 0, f'after an edited save red\'s book holds {book_size(game, 0, 0)}')
    findings.raise_if_any('the recipe book')


# The other Primals, made from ingredients put down by the map's .tower hook: fusion, fee and what
# each is for, then Surge, five waves and the ascension of all four
# (name, source, target, Primal, fee, class, Ascended, fee, class)
PRIMALS = [
    ('Haboob', 'u03D', 'u033', 'uP03', 400, 'Haboob', 'uA03', 500, 'EndlessStorm'),
    ('Heart of Life', 'u02D', 'u02D', 'uP04', 500, 'AttunedTower', 'uA04', 600, 'AvatarOfLife'),
    ('World Tree', 'u036', 'u02F', 'uP05', 400, 'AttunedTower', 'uA05', 450, 'Nordrassil'),
    ('Inferno', 'u039', 'u031', 'uP06', 350, 'Inferno', 'uA06', 400, 'Firelord'),
]
HABOOB_TARGETS = 8
# More targets than it may hit, so its cap shows
HABOOB_RING = 12
INFERNO_DPS, FIRELORD_DPS = 400, 900
# The map's boss waves are 35-37, the Hero armoured ones (waves 9, 14, 19... are champions)
BOSS_WAVE = 35


# The recipe book's fusion table, cell by cell as the panel lays them out (row by row, j >= i, the
# Runes in the order Life, Death, Fire, Water, Nature, Air), and the ledger's Primals and Ascended
BOOK_CELLS = ['u02C', 'n026', 'u01D', 'u01F', 'u021', 'u022', 'u02A', 'n028', 'n030', 'u01E', 'u020', 'u030', 'u028',
              'u025', 'u027', 'u034', 'u023', 'u026', 'u02E', 'u024', 'u032']
BOOK_PRIMALS = ['uP01', 'uP02', 'uP03', 'uP04', 'uP05', 'uP06']
BOOK_ASCENDED = ['uA01', 'uA02', 'uA03', 'uA04', 'uA05', 'uA06']


def book_notes(game, player, kind, since=0):
    """The ids of a player's 'book' notes of one kind (discovered, took, ascended)."""
    marker = f' book p{player} {kind} '
    return [line.split(marker, 1)[1].split()[0] for line in game.events('book')[since:] if marker in line]


def learn(game, player, ids):
    """Puts results in a player's book as found, and Ascended as made (the debug command -learn)."""
    before = len(game.events('book'))
    game.cmd(player, '-learn ' + ' '.join(ids))
    game.wait(f'{len(ids)} entries learnt', lambda: len(book_notes(game, player, 'discovered', before))
              + len(book_notes(game, player, 'ascended', before)) >= len(ids), 10)


def click_book(game, player, frame, kind=None):
    """A click on one of the book's buttons, by the player whose client it is on (BlzFrameClick: a
    real, synced frame click). With a kind, waits for that note and returns its id; without, waits
    a moment and returns any note the click made, which should be none."""
    before = len(game.events('book'))
    game.cmd(player, f'.lua if GetLocalPlayer() == Player({player}) then '
                     f'BlzFrameClick(BlzGetFrameByName("{frame}", 0)) end')
    if kind is None:
        time.sleep(2)
        return [line for line in game.events('book')[before:] if f' book p{player} ' in line]
    game.wait(f'the {frame} click', lambda: book_notes(game, player, kind, before), 10)
    return book_notes(game, player, kind, before)[0]


def look_at_book(game, what):
    try:
        game.log(f'the book, {what}: {game.screenshot(0)}')
    except TestFailed as problem:
        game.log(f'no screenshot of the book, {what}: {problem}')


def test_recipe_book(game):
    """The recipe book as the Fusion Table, in its four states (screenshots of red's): a new player
    (1 of 27), midgame (10 of 27, the Lich King made), the table full (the free Primal offered and
    taken), complete. A click on a sealed cell does nothing; a Primal can be taken free only once
    every first fusion is found, and only one; blue's click changes blue's book only; the clients
    stay in step. The next game loads the free Primal as given and the Ascended made; -forget
    empties them too."""
    findings = Findings()
    maul.start(game)
    for player in (RED, BLUE):
        maul.pick(game, player, 'I024')
        before = len(game.events('book'))
        game.cmd(player, '-forget')
        game.wait('the book to empty', lambda: any(f' book p{player} forgot' in line
                                                   for line in game.events('book')[before:]), 10)
    lua_true(game, '_G.F = function(name) return BlzGetFrameByName(name, 0) end return true')

    def text_has(frame, text, player=RED):
        return lua_true(game, f'return GetLocalPlayer() ~= Player({player}) or '
                              f'string.find(BlzFrameGetText(F("{frame}")), "{text}", 1, true) ~= nil')

    # A new player: one find; a sealed cell is only a hint
    learn(game, RED, ['n026'])
    for player in (RED, BLUE):
        game.cmd(player, '-book')
    time.sleep(1)
    findings.check(lua_true(game, 'return BlzFrameIsVisible(F("recipeBookPanel"))'), 'the book did not open on both screens')
    findings.check(text_has('recipeBookTally', 'ffffffff1|r'), 'red\'s tally is not 1 of 27')
    findings.check(text_has('recipeBookReveal2', 'for one Primal, free'), 'the table does not say what filling it earns')
    findings.check(not click_book(game, RED, 'recipeBookCell15'), 'a click on a sealed cell did something')
    look_at_book(game, 'a new player')

    # Midgame: ten found, the Lich King made; no Primal free yet
    learn(game, RED, ['u02C', 'u01D', 'u02A', 'n028', 'u030', 'u02E', 'u034', 'uP01', 'uP06', 'uA01'])
    findings.check(not click_book(game, RED, 'recipeBookPrimal1'), 'a Primal was taken before the table was full')
    findings.check(text_has('recipeBookTally', 'ffffffff10|r'), 'red\'s tally is not 10 of 27')
    findings.check(text_has('recipeBookAscendedHeader', 'ffffffff1|r'), 'red\'s Ascended are not 1 of 6')
    look_at_book(game, 'midgame')

    # Blue fills the table and takes the Lich: blue's book alone
    learn(game, BLUE, BOOK_CELLS)
    took = click_book(game, BLUE, 'recipeBookPrimal0', 'took')
    findings.check(took == 'uP01', f'blue took {took}, not the Lich (uP01)')

    # Red's table full: every first fusion found, one Primal offered, taken once
    known = {'n026', 'u02C', 'u01D', 'u02A', 'n028', 'u030', 'u02E', 'u034'}
    learn(game, RED, [cell for cell in BOOK_CELLS if cell not in known])
    findings.check(text_has('recipeBookReveal1', 'Table full'), 'the table is not full')
    findings.check(text_has('recipeBookLedgerSubline', 'click a Primal to take it free'), 'no free Primal is offered')
    look_at_book(game, 'the table full')
    took = click_book(game, RED, 'recipeBookPrimal3', 'took')
    findings.check(took == 'uP04', f'the free Primal is {took}, not the Heart of Life (uP04)')
    findings.check(not click_book(game, RED, 'recipeBookPrimal1'), 'a second Primal was taken free')

    # Complete: every Primal and every Ascended
    learn(game, RED, ['uP02', 'uP03', 'uP05'] + BOOK_ASCENDED[1:])
    findings.check(text_has('recipeBookTally', 'ffffffff27|r'), 'red\'s tally is not 27 of 27')
    findings.check(lua_true(game, 'return GetLocalPlayer() ~= Player(0) or BlzFrameGetText(F("recipeBookReveal1")) == ""'),
                   'a complete book still shows a line under the table')
    look_at_book(game, 'complete')
    try:
        game.check_in_step()
    except TestFailed as error:
        findings.append(f'after the clicks: {error}')

    # The next game: the free Primal and the Ascended made come back; then -forget empties them
    elementalist_game(game, 'recipe-book-loaded')
    game.wait('the books to load', lambda: book_loaded(game, RED, 0) and book_loaded(game, RED, 1), 30)
    for client in range(2):
        book = book_loaded(game, RED, client)
        findings.check(book == {'known': 27, 'given': 1, 'ascended': 6},
                       f'client {client + 1}: red\'s book loaded as {book}, not 27 known, 1 given, 6 ascended')
    before = len(game.events('book'))
    game.cmd(RED, '-forget')
    game.wait('the book to empty', lambda: any(' book p0 forgot' in line for line in game.events('book')[before:]), 10)
    elementalist_game(game, 'recipe-book-forgotten')
    game.wait('the book to load', lambda: book_loaded(game, RED, 0), 30)
    book = book_loaded(game, RED, 0)
    findings.check(book == {'known': 0, 'given': 0, 'ascended': 0}, f'after -forget, red\'s book loaded as {book}')
    findings.raise_if_any('the recipe book')


def place_tower(game, unit_type, spot):
    """A tower put down for red as if built (the map's .tower hook); its id."""
    before = len(game.events('tower'))
    game.cmd(RED, f'.tower {unit_type} {spot[0]} {spot[1]}')
    game.wait(f'the {unit_type}', lambda: any(' made ' in line or ' could not ' in line
                                              for line in game.events('tower')[before:]), 10)
    line = next(line for line in game.events('tower')[before:] if ' made ' in line or ' could not ' in line)
    if ' could not ' in line:
        raise TestFailed(line)
    return trace.parse_fields(line)['id']


def tower_classes(game):
    """{tower id: class} of red's towers, as the map's .towers lists them."""
    before = len(game.events('tower'))
    game.cmd(RED, '.towers')
    game.wait('the tower list', lambda: any(line.endswith('p0 end') for line in game.events('tower')[before:]), 10)
    fields = [trace.parse_fields(line) for line in game.events('tower')[before:] if ' class=' in line and ' made ' not in line]
    return {f['id']: f['class'] for f in fields}


def spread_spots(count, distance, avoid=(), width=1):
    """Spots in red's lane at least `distance` apart and from the spots in `avoid`, each with the
    `width` spots to its right free for more towers."""
    spots = red_lane_spots()
    chosen = []
    for x, y in spots:
        if (all((x + n * LANE_SPACING, y) in spots for n in range(1, width + 1))
                and all(abs(x - cx) + abs(y - cy) >= distance for cx, cy in chosen + list(avoid))):
            chosen.append((x, y))
    if len(chosen) < count:
        raise TestFailed(f'red\'s lane has no {count} spots {distance} apart')
    return chosen[:count]


def ring(spot, radius, count):
    return [(round(spot[0] + radius * math.cos(2 * math.pi * i / count)), round(spot[1] + radius * math.sin(2 * math.pi * i / count)))
            for i in range(count)]


def watch_hits(game, seconds=MEASURE_SECONDS):
    """Every hit over the next seconds: [(tick, fields)]."""
    before = len(game.events('hit'))
    time.sleep(seconds)
    return [(tick_of(line), trace.parse_fields(line)) for line in game.events('hit')[before:]]


def per_second(hits, source, spells, field='raw'):
    """A tower's damage a second per target it hit: its attacks, or (spells) its other damage; `raw`
    is before the target's armor, `amount` what was dealt."""
    own = [(t, f) for t, f in hits if f.get('src') == source and (f.get('attack') != 'true') == spells]
    if len(own) < 2:
        return 0
    seconds = max((own[-1][0] - own[0][0]) * TICK, TICK)
    return sum(f.get(field, 0) for _, f in own) / seconds / max(len({f.get('dst') for _, f in own}), 1)


# One volley's hits land over a few ticks (missiles fly different distances); well under a cooldown
VOLLEY_TICKS = 3


def volley_targets(hits, source):
    """How many targets a tower hits in a volley, once all its targets stand: the most common count
    over the later half of its volleys (a volley ends where its hits pause for more than 2 ticks)."""
    volleys, current = [], []
    for t, f in hits:
        if f.get('src') != source:
            continue
        if current and t - current[-1][0] > 2:
            volleys.append(current)
            current = []
        current.append((t, f.get('dst')))
    volleys.append(current)
    counts = [len({dst for _, dst in volley}) for volley in volleys[len(volleys) // 2:] if volley]
    return max(set(counts), key=counts.count) if counts else 0


def per_hit(hits, source):
    """The mean damage dealt by a tower's attacks: a rate from a few slow hits is off by N/(N-1)."""
    dealt = [f.get('amount', 0) for _, f in hits if f.get('src') == source and f.get('attack') == 'true']
    return sum(dealt) / len(dealt) if dealt else 0


def most_in_a_tick(hits, source):
    """The most targets a tower hit in one volley (VOLLEY_TICKS): a Barrage's extra targets come as
    spell damage, and are counted."""
    own = [(t, f.get('dst')) for t, f in hits if f.get('src') == source]
    return max((len({dst for u, dst in own if t <= u < t + VOLLEY_TICKS}) for t, _ in own), default=0)


def test_primal_looks(game):
    """Screenshots of the Primals and their Ascended forms in red's lane, for looking at their
    models: all twelve from red's camera, then each one close; then red's recipe book. Nothing in
    it can fail."""
    maul.start(game)
    maul.pick(game, RED, 'I024')
    min_x, min_y, max_x, max_y = maul.lane_areas()[RED]
    towers = [f'uP0{n}' for n in range(1, 7)] + [f'uA0{n}' for n in range(1, 7)]
    step = (max_x - min_x) / 6
    placed = []
    for index, kind in enumerate(towers):
        row, column = divmod(index, 6)
        spot = (int(min_x + step * (column + 0.5)), int(min_y + (max_y - min_y) * (0.3 + 0.4 * row)))
        place_tower(game, kind, spot)
        placed.append((kind, spot))

    def look(x, y, distance, what):
        game.cmd(RED, f'.lua SetCameraPosition({x}, {y}) SetCameraField(CAMERA_FIELD_TARGET_DISTANCE, {distance}, 0)')
        time.sleep(2)
        try:
            game.log(f'{what}: {game.screenshot(0)}')
        except TestFailed as problem:
            game.log(f'no screenshot of {what}: {problem}')
    look(int((min_x + max_x) / 2), int((min_y + max_y) / 2), 2600, 'all twelve')
    for kind, (x, y) in placed:
        look(x, y, 1100, kind)
    # Imported icons: a Bubbles beside the first Primal, both selected, shows both in the
    # selection panel
    kind, (x, y) = placed[0]
    lich = unit_at(game, RED, (x, y), (kind,))['id']
    bubbles = place_tower(game, BUBBLES, (x, y - 256))
    game.cmd(RED, f'.lua if GetLocalPlayer() == Player(0) then ClearSelection() SelectUnit(unit({lich}), true) '
                  f'SelectUnit(unit({bubbles}), true) end')
    look(x, y, 1100, 'the Bubbles icon')
    game.cmd(RED, '.lua if GetLocalPlayer() == Player(0) then ClearSelection() end')
    # And the recipe book, open on red's screen
    game.cmd(RED, '-book')
    look(int((min_x + max_x) / 2), int((min_y + max_y) / 2), 2600, 'the recipe book')


def test_primals(game):
    """Haboob, Heart of Life, World Tree and Inferno: each ingredient pair reveals and then makes its
    Primal for its fee, with the class that gives it its behaviour; the Haboob hits 8 targets, the
    World Tree splashes, the Inferno burns 400 a second. After ten Surges and five waves each ascends
    for its fee: the Avatar of Life hits half again as hard in a boss wave, Nordrassil roots outside
    boss waves only, the Firelord burns hotter. Damage per second of all eight."""
    findings = Findings()
    maul.start(game)
    maul.pick(game, RED, 'I024')
    give_gold(game, (RED,))
    game.cmd(RED, '-lives 1000000')
    forget_recipes(game)
    game.watch(CREEPS)
    game.cmd(RED, '.gold 100000')
    game.wait_for('the gold', lambda beat: beat.gold(RED) == 100000)

    made, spots = {}, []
    for (name, source_type, target_type, primal, fee, _, _, _, _), asked in zip(PRIMALS, spread_spots(len(PRIMALS), 1000)):
        source = place_tower(game, source_type, asked)
        target = place_tower(game, target_type, (asked[0] + LANE_SPACING, asked[1]))
        # Where it stands: a building with a larger footprint (Purgatory L2) is moved to fit
        unit = next(u for u in game.units(RED) if u['id'] == source)
        spot = (unit['x'], unit['y'])
        spots.append(spot)
        reveal(game, source, target, primal, findings)
        gold = maul.fresh_beat(game).gold(RED)
        game.order(RED, source, SIPHON, target)
        try:
            game.wait(f'the {name}', lambda: unit_at(game, RED, spot, (primal,)), 15)
        except TestFailed as error:
            findings.append(f'{name}: {error}')
            continue
        findings.check(maul.fresh_beat(game).gold(RED) == gold - fee, f'the {name} did not take {fee} gold')
        made[name] = unit_at(game, RED, spot, (primal,))['id']
    classes = tower_classes(game)
    for name, _, _, _, _, cls, _, _, _ in PRIMALS:
        if name in made:
            findings.check(classes.get(made[name]) == cls, f'the {name} is a {classes.get(made[name])}, not a {cls}')
    findings.raise_if_any('making the Primals')

    def place_targets(towers):
        """Targets for each: the Haboob a ring of ground and air, the World Tree a clump, the Inferno
        two, the Heart of Life one; their ids. They are creeps, so a wave does not end while they stand."""
        placed = []

        def create(kind, x, y):
            placed.append(game.create(CREEPS, kind, x, y, life=TARGET_LIFE, rooted=True)[0]['id'])
        # The splash towers' clumps first: with range 800 they would otherwise take a neighbour's
        # lone target that stood first, and keep to it
        for name, tower in sorted(towers.items(), key=lambda item: item[0] not in ('World Tree', 'Nordrassil')):
            unit = next(u for u in game.units(RED) if u['id'] == tower)
            here = (unit['x'], unit['y'])
            if name in ('Haboob', 'Endless Storm'):
                for i, (x, y) in enumerate(ring(here, 320, HABOOB_RING)):
                    create(maul.AIR_TARGET if i % 2 else maul.GROUND_TARGET, x, y)
            elif name in ('World Tree', 'Nordrassil'):
                for dx in (-60, 0, 60):
                    create(maul.GROUND_TARGET, here[0] + dx, here[1] + 300)
            elif name in ('Inferno', 'Firelord'):
                for dy in (-200, 200):
                    create(maul.GROUND_TARGET, here[0], here[1] + dy)
            else:
                create(maul.GROUND_TARGET, here[0], here[1] + 96)
        return placed

    targets = place_targets(made)
    hits = watch_hits(game)
    rows = [(name, round(per_second(hits, tower, False)), round(per_second(hits, tower, True)), most_in_a_tick(hits, tower))
            for name, tower in made.items()]
    for row in rows:
        game.log(f'{row[0]:16} attack {row[1]:>7} a second a target, other {row[2]:>6}, most targets in a tick {row[3]}')
    by_name = {row[0]: row for row in rows}
    haboob_volley = volley_targets(hits, made['Haboob'])
    findings.check(haboob_volley == HABOOB_TARGETS, f'the Haboob hits {haboob_volley} targets a volley, not {HABOOB_TARGETS}')
    findings.check(by_name['World Tree'][3] >= 2, 'the World Tree did not splash')
    findings.check(abs(by_name['Inferno'][2] - INFERNO_DPS) <= INFERNO_DPS / 4,
                   f'the Inferno burns {by_name["Inferno"][2]} a second, not about {INFERNO_DPS}')
    findings.check(by_name['Heart of Life'][1] > 0, 'the Heart of Life never hit')

    # Ten Surges each, five waves, and they ascend
    for target in targets:
        game.remove(CREEPS, target)
    for name, tower in made.items():
        before = len(game.events('surge'))
        for n in range(SURGE_LIMIT):
            game.order(RED, tower, SURGE)
            game.wait(f'{name}\'s surge {n + 1}', lambda: len(game.events('surge')) > before + n, 10)
    for _ in range(5):
        play_round(game)
    ascended = {}
    for (name, _, _, _, _, _, result, fee, _), spot in zip(PRIMALS, spots):
        if name not in made:
            continue
        gold = maul.fresh_beat(game).gold(RED)
        game.order(RED, made[name], ASCEND)
        try:
            game.wait(f'the {name} to ascend', lambda: unit_at(game, RED, spot, (result,)), 15)
        except TestFailed as error:
            findings.append(f'{name}: {error}: {game.events("ascend")[-2:]}')
            continue
        findings.check(maul.fresh_beat(game).gold(RED) == gold - fee, f'the {name}\'s ascension did not take {fee} gold')
        ascended[name] = unit_at(game, RED, spot, (result,))['id']
    classes = tower_classes(game)
    for name, _, _, _, _, _, _, _, cls in PRIMALS:
        if name in ascended:
            findings.check(classes.get(ascended[name]) == cls, f'the ascended {name} is a {classes.get(ascended[name])}, not a {cls}')

    names = {'Haboob': 'Endless Storm', 'Heart of Life': 'Avatar of Life', 'World Tree': 'Nordrassil', 'Inferno': 'Firelord'}
    towers = {names[name]: tower for name, tower in ascended.items()}
    place_targets(towers)
    roots = len(game.events('root'))
    hits = watch_hits(game)
    rows = {name: (round(per_second(hits, tower, False)), round(per_second(hits, tower, True)), most_in_a_tick(hits, tower))
            for name, tower in towers.items()}
    for name, row in rows.items():
        game.log(f'{name:16} attack {row[0]:>7} a second a target, other {row[1]:>6}, most targets in a tick {row[2]}')
    if 'Endless Storm' in rows:
        storm_volley = volley_targets(hits, towers['Endless Storm'])
        findings.check(storm_volley == HABOOB_TARGETS, f'the Endless Storm hits {storm_volley} targets a volley, not {HABOOB_TARGETS}')
    if 'Firelord' in rows:
        findings.check(rows['Firelord'][1] >= FIRELORD_DPS, f'the Firelord burns {rows["Firelord"][1]} a second, under {FIRELORD_DPS}')
    if 'Nordrassil' in towers:
        findings.check(len(game.events('root')) > roots, 'Nordrassil rooted nothing')

    # A boss wave: the Avatar of Life hits half again as hard, and Nordrassil roots nothing
    if 'Avatar of Life' in towers:
        normal = round(per_hit(hits, towers['Avatar of Life']))
        game.cmd(RED, f'-wave {BOSS_WAVE}')
        time.sleep(1)
        roots = len(game.events('root'))
        boss_hits = watch_hits(game, 10)
        boss = round(per_hit(boss_hits, towers['Avatar of Life']))
        game.log(f'Avatar of Life: {normal} a hit, {boss} in a boss wave')
        findings.check(abs(boss - 1.5 * normal) <= 0.02 * normal, f'the Avatar of Life hits for {boss} in a boss wave, {normal} otherwise')
        if 'Nordrassil' in towers:
            findings.check(len(game.events('root')) == roots, 'Nordrassil rooted in a boss wave')
    findings.raise_if_any('the Primals')


# The midgame bridge (Elementalist Improvements): stronger burns, Plague's spreading disease, Mist's
# magic weakness, Tornado's Updraft, Blaze and Bubbles back to runes, Recharge for rocks
PURGATORY, PURGATORY_2, DECAY, PLAGUE, MIST, TORNADO, BLAZE, BUBBLES = 'n028', 'u039', 'u01E', 'u020', 'u028', 'u022', 'u027', 'u026'
# Expected damage a second: Purgatory 20 and 150 (immolation), Decay 15 (disease cloud), Plague 50
PURGATORY_DPS, PURGATORY_2_DPS, DECAY_DPS, PLAGUE_DPS = 20, 150, 15, 50
# How far from a dying creep Plague spreads (Plague.ts)
PLAGUE_SPREAD = 300
WATER_3, AIR_3 = 'u035', 'u033'
MIST_FACTOR, UPDRAFT_FACTOR = 1.1, 1.2
RECHARGE, RECHARGE_PRICE = 'rejuvination', 8


def attack_rate(hits, source):
    attacks = [t for t, f in hits if f.get('src') == source and f.get('attack') == 'true']
    return (len(attacks) - 1) / max((attacks[-1] - attacks[0]) * TICK, TICK) if len(attacks) > 1 else 0


def target_near(game, spot, dy=120):
    return game.create(CREEPS, maul.GROUND_TARGET, spot[0], spot[1] + dy, life=TARGET_LIFE, rooted=True)[0]['id']


def test_midgame(game):
    """The midgame bridge: Purgatory burns 20 and 150 a second, Decay 15; Mist makes a magic
    tower's hits on its target 10% bigger; Tornado's Updraft speeds a nearby tower's attacks by
    20%; Blaze and Bubbles turn back into Uncharged Runes; Recharge turns a Depleted Rock into an
    Uncharged Rune for 8 gold and is refused below; a creep dying near Plague infects the creeps
    around it."""
    findings = Findings()
    maul.start(game)
    maul.pick(game, RED, 'I024')
    give_gold(game, (RED,))
    game.cmd(RED, '-lives 1000000')
    game.watch(CREEPS)
    # The burns, Mist and Updraft each measure a tower on a target of its own: far enough apart
    # that no tower reaches another's target. Recharge, Blaze, Bubbles and Plague come after those
    # measurements and only need room (Bubbles two spots right of Blaze)
    spots = spread_spots(5, 900)
    taken = [(x + n * LANE_SPACING, y) for x, y in spots for n in (0, 1)]
    spots += spread_spots(3, 2 * LANE_SPACING, taken, width=2)
    targets = []

    # Burns: each alone with one target
    burners = {}
    for (kind, expected), spot in zip(((PURGATORY, PURGATORY_DPS), (PURGATORY_2, PURGATORY_2_DPS), (DECAY, DECAY_DPS)), spots):
        tower = place_tower(game, kind, spot)
        burners[kind] = (tower, target_near(game, spot), expected)
        targets.append(burners[kind][1])
    hits = watch_hits(game)
    for kind, (tower, target, expected) in burners.items():
        burned = [(t, f) for t, f in hits if f.get('dst') == target and f.get('attack') != 'true']
        seconds = max((burned[-1][0] - burned[0][0]) * TICK, TICK) if len(burned) > 1 else 0
        rate = round(sum(f.get('raw', 0) for _, f in burned) / seconds) if seconds else 0
        game.log(f'{kind}: burns {rate} a second (expected {expected})')
        findings.check(abs(rate - expected) <= expected * 0.25, f'{kind} burns {rate} a second, not about {expected}')

    # Mist: a Water Rune L3's hits on a target, then with Mist hitting the same target
    water = place_tower(game, WATER_3, spots[3])
    targets.append(target_near(game, spots[3]))
    before = per_hit(watch_hits(game, 8), water)
    place_tower(game, MIST, (spots[3][0] + LANE_SPACING, spots[3][1]))
    after = per_hit(watch_hits(game, 12), water)
    game.log(f'Water Rune L3: {before:.0f} a hit, {after:.0f} with Mist on its target')
    findings.check(abs(after - before * MIST_FACTOR) <= before * 0.03, f'with Mist the Water Rune hits for {after:.0f}, not {before * MIST_FACTOR:.0f}')

    # Updraft: an Air Rune L3's attack rate, then with a Tornado next to it
    air = place_tower(game, AIR_3, spots[4])
    targets.append(target_near(game, spots[4]))
    slow = attack_rate(watch_hits(game, 10), air)
    place_tower(game, TORNADO, (spots[4][0] + LANE_SPACING, spots[4][1]))
    fast = attack_rate(watch_hits(game, 10), air)
    game.log(f'Air Rune L3: {slow:.2f} attacks a second, {fast:.2f} beside a Tornado')
    findings.check(fast >= slow * (UPDRAFT_FACTOR - 0.08), f'Updraft did not speed the Air Rune up: {slow:.2f} -> {fast:.2f}')

    # Recharge: refused below 8 gold, then a rune for 8
    rock_spot = spots[5]
    rock = place_tower(game, DEPLETED_ROCK, rock_spot)
    game.cmd(RED, '.gold 5')
    game.wait_for('the gold to be 5', lambda beat: beat.gold(RED) == 5)
    before_notes = len(game.events('recharge'))
    game.order(RED, rock, RECHARGE)
    try:
        game.wait('the recharge to be refused', lambda: any(' refused ' in line for line in game.events('recharge')[before_notes:]), 10)
    except TestFailed as error:
        findings.append(f'Recharge with 5 gold: {error}')
    game.cmd(RED, '.gold 100')
    game.wait_for('the gold to be 100', lambda beat: beat.gold(RED) == 100)
    game.order(RED, rock, RECHARGE)
    try:
        game.wait('the rock to become a rune', lambda: unit_at(game, RED, rock_spot, (UNCHARGED_RUNE,)), 15)
        findings.check(maul.fresh_beat(game).gold(RED) == 100 - RECHARGE_PRICE, f'Recharge did not take {RECHARGE_PRICE} gold')
    except TestFailed as error:
        findings.append(f'Recharge with 100 gold: {error}')

    # Blaze and Bubbles back to runes: Blaze after its wave, Bubbles once its damage runs out
    blaze_spot, bubbles_spot = spots[6], (spots[6][0] + 2 * LANE_SPACING, spots[6][1])
    place_tower(game, BLAZE, blaze_spot)
    bubbles = place_tower(game, BUBBLES, bubbles_spot)
    game.cmd(RED, f'.lua BlzSetUnitBaseDamage(unit({bubbles}), 3, 0) return BlzGetUnitBaseDamage(unit({bubbles}), 0)')

    # Plague: a wave; a creep killed next to others infects them. The targets go first: they are
    # creeps, and a wave does not end while they stand
    for target in targets:
        game.remove(CREEPS, target)
    place_tower(game, PLAGUE, spots[7])
    plagued = len(game.events('plague'))
    maul.start_wave(game)
    game.wait_for('the wave to spawn', lambda b: (b.get('creeps') or 0) > 0, 30)
    # Two targets beside a wave creep in red's lane (Plague's reach), then the creep dies: wave
    # creeps walk too far apart to count on two of them being close
    min_x, min_y, max_x, max_y = maul.lane_areas()[RED]

    found = []

    def in_lane():
        found[:] = [u for u in game.units(CREEPS) if u['type'] != maul.GROUND_TARGET
                    and min_x <= u['x'] <= max_x and min_y <= u['y'] <= max_y]
        return found
    game.wait('a wave creep in red\'s lane', in_lane, 30)
    # Held still and kept from the lane's towers, so the targets stand beside it when it dies
    creep = found[0]
    game.cmd(RED, f'.lua PauseUnit(unit({creep["id"]}), true) SetUnitInvulnerable(unit({creep["id"]}), true)')
    creep = next((u for u in game.units(CREEPS) if u['id'] == creep['id']), creep)
    beside = [game.create(CREEPS, maul.GROUND_TARGET, creep['x'] + dx, creep['y'], life=TARGET_LIFE, rooted=True)[0]['id']
              for dx in (-100, 100)]
    # The game moves a unit off blocked ground, so count the targets that ended up in reach
    standing = [u for u in game.units(CREEPS) if u['id'] in beside]
    reach = [u for u in standing if math.hypot(u['x'] - creep['x'], u['y'] - creep['y']) < PLAGUE_SPREAD - 20]
    game.log(f'Plague: creep at ({creep["x"]:.0f}, {creep["y"]:.0f}), targets at '
             + ', '.join(f'({u["x"]:.0f}, {u["y"]:.0f})' for u in standing))
    game.cmd(CREEPS, f'.kill {creep["id"]}')
    try:
        # Its own note: the lane's towers keep killing other creeps, each death a note of its own
        mine = f' creep={creep["id"]} '

        def noted():
            return next((line for line in game.events('plague')[plagued:] if mine in line), None)
        game.wait('Plague to infect', noted, 10)
        infected = noted().split(' plague ', 1)[1]
        game.log(f'Plague: {infected}')
        findings.check(reach and int(infected.rsplit(' ', 1)[1]) >= len(reach),
                       f'Plague infected {infected}, {len(reach)} targets stood within {PLAGUE_SPREAD}')
    except TestFailed as error:
        findings.append(f'Plague: {error}')
    for target in beside:
        game.remove(CREEPS, target)
    time.sleep(6)
    game.cmd(RED, '-killall')
    game.wait_for('the wave to end', lambda b: b.get('spawning') is False, 60)
    for spot, name in ((blaze_spot, 'Blaze'), (bubbles_spot, 'Bubbles')):
        findings.check(unit_at(game, RED, spot, (UNCHARGED_RUNE,)) is not None, f'{name} did not turn back into an Uncharged Rune')
    findings.raise_if_any('the midgame bridge')


# Forest Trolls' High Priest: a dummy casts Monsoon (A03P, from ANmo) on its target's spot
MONSOON, MONSOON_DUMMY = 'A03P', 'u008'
# How long it lasts, as the High Priest's tooltip and dummy (13 s) have it
MONSOON_SECONDS = 12


def cast_monsoon(game, spot, life):
    """A Monsoon cast as the High Priest casts it: a dummy of red's with the ability, living `life`
    seconds, ordered onto the spot. The dummy's id."""
    dummy = game.create(RED, MONSOON_DUMMY, spot[0], spot[1] - 200)[0]['id']
    lua_value(game, f'UnitApplyTimedLife(unit({dummy}), FourCC("BTLF"), {life}) '
                    f'UnitAddAbility(unit({dummy}), FourCC("{MONSOON}")) '
                    f'return IssuePointOrder(unit({dummy}), "monsoon", {spot[0]}, {spot[1]})')
    return dummy


def monsoon_strikes(hits, target, dummies):
    """The ticks a target was struck by the given dummies' Monsoons."""
    return [tick for tick, f in hits if f.get('dst') == target and f.get('src') in dummies]


def test_monsoon_strikes_every_cast(game):
    """Monsoon strikes for its 12 s, not as long as its caster lives, and a unit struck by one
    Monsoon is struck by the next one too: one cast whose caster outlives it, then a second after
    the first caster is gone. Two Monsoons at once do not stack - a unit under one is not struck by
    the other (the game's Monsoon, measured 2026-10-09) - which is logged, not judged."""
    findings = Findings()
    maul.start(game)
    # Hits on creeps are only traced for a watched player
    game.watch(CREEPS)
    spot = red_lane_spots()[5]
    target = game.create(CREEPS, maul.GROUND_TARGET, spot[0], spot[1], life=TARGET_LIFE, rooted=True)[0]['id']

    # One Monsoon, its caster living well past it
    first = cast_monsoon(game, spot, 25)
    fields = lua_value(game, f'local a = BlzGetUnitAbility(unit({first}), FourCC("{MONSOON}")) '
                             f'return BlzGetAbilityRealLevelField(a, ABILITY_RLF_DURATION_NORMAL, 0) .. " s, area " .. '
                             f'BlzGetAbilityRealLevelField(a, ABILITY_RLF_AREA_OF_EFFECT, 0)')
    game.log(f'Monsoon at runtime: duration {fields}')
    struck = monsoon_strikes(watch_hits(game, 26), target, {first})
    span = (struck[-1] - struck[0]) * TICK if len(struck) > 1 else 0
    game.log(f'first Monsoon: {len(struck)} strikes over {span:.1f} s')
    findings.check(len(struck) > 0, 'the first Monsoon never struck')
    findings.check(span <= MONSOON_SECONDS + 1, f'the Monsoon struck for {span:.1f} s, past its {MONSOON_SECONDS} s')

    # A second, its caster living as the High Priest's does, after the first caster is gone
    second = cast_monsoon(game, spot, 13)
    struck = monsoon_strikes(watch_hits(game, 14), target, {second})
    game.log(f'second Monsoon: {len(struck)} strikes')
    findings.check(len(struck) > 0, 'a unit struck by one Monsoon was never struck by the next')

    # Two at once
    both = {cast_monsoon(game, spot, 13), cast_monsoon(game, spot, 13)}
    hits = watch_hits(game, 14)
    per_dummy = {dummy: len(monsoon_strikes(hits, target, {dummy})) for dummy in both}
    game.log(f'two Monsoons at once: {sorted(per_dummy.values())} strikes (they do not stack)')
    findings.raise_if_any('Monsoon')


# Human's Venom Tower: attacks a random creep in range, a new one each attack (RandomTargeting.ts)
VENOM = 'h045'


def test_venom_target_leaves_range(game):
    """A Venom Tower whose ordered creep walks out of its range goes on attacking: the creep its
    latest order went to (its 'target ... ordered' note) is moved far away, and the tower attacks
    another within a few seconds; three times. (The game drops the order by itself.)"""
    findings = Findings()
    maul.start(game)
    # Hits on creeps are only traced for a watched player
    game.watch(CREEPS)
    spot = red_lane_spots()[5]
    tower = place_tower(game, VENOM, spot)
    places = [(spot[0] - 150, spot[1] - 150), (spot[0] + 150, spot[1] - 150), (spot[0], spot[1] + 180)]
    targets = [game.create(CREEPS, maul.GROUND_TARGET, x, y, life=TARGET_LIFE, rooted=True)[0]['id'] for x, y in places]
    home = dict(zip(targets, places))

    def ordered_since(since):
        marker = f' target {tower} ordered '
        return [int(line.split(marker, 1)[1].split()[0]) for line in game.events('target')[since:] if marker in line]

    def attacks_since(since):
        return [trace.parse_fields(line) for line in game.events('hit')[since:]
                if f' src={tower} ' in line and 'attack=true' in line]

    game.wait('the tower to order itself onto a target', lambda: ordered_since(0), 15)
    for round_ in range(3):
        gone = ordered_since(0)[-1]
        x, y = home[gone]
        hits = len(game.events('hit'))
        game.cmd(RED, f'.lua SetUnitPosition(unit({gone}), {x + 3000}, {y})')
        try:
            game.wait(f'an attack on a target still in range after {gone} left',
                      lambda: any(f.get('dst') not in (gone, None) for f in attacks_since(hits)), 6)
        except TestFailed as error:
            findings.append(f'round {round_ + 1}: {error}')
        game.log(f'round {round_ + 1}: moved {gone}, the creep of its latest order, away')
        game.cmd(RED, f'.lua SetUnitPosition(unit({gone}), {x}, {y})')
        time.sleep(2)
    findings.raise_if_any('the Venom Tower')


# Loot Boxer's items, from races.py: a tier 9 box holds up to 6 mana and has a one-slot inventory
LOOT_BOX_TIER_9 = 'u04C'
ITEM_MANA = {STICK: 1, COIN: 2, KOINZ: 6}


def lua_value(game, code):
    """What a line of Lua returned, as text ('ok <value>' stripped)."""
    before = len(game.events('lua'))
    game.cmd(RED, '.lua ' + code)
    game.wait('the Lua line', lambda: len(game.events('lua')) > before, 10)
    result = game.events('lua')[before].split(' lua ', 1)[1]
    return result[3:] if result.startswith('ok ') else result


def test_loot_boxer_items(game):
    """Loot Boxer's items, used as a player would (UnitUseItem): Wooden Sticks, a Gold Coin and a
    Platinum Token give an opened-tier box 1, 2 and 6 mana when the box itself uses them, and
    nothing when the builder does; a stack of 9 Rocks used turns into a Lootbag (it never did: the
    game takes the used charge first), a bigger one keeps the rest, and a smaller one stays as it
    was."""
    findings = Findings()
    maul.start(game)
    builder = maul.pick(game, RED, LOOT_BOXER)['id']
    box = place_tower(game, LOOT_BOX_TIER_9, red_lane_spots()[5])

    def use(unit, item, charges=None):
        set_charges = f'SetItemCharges(it, {charges}) ' if charges is not None else ''
        lua_value(game, f'local it = UnitAddItemById(unit({unit}), FourCC("{item}")) {set_charges}'
                        f'return UnitUseItem(unit({unit}), it)')
        time.sleep(1.5)

    def box_mana():
        return float(lua_value(game, f'return GetUnitState(unit({box}), UNIT_STATE_MANA)'))

    def clear_box():
        lua_value(game, f'local it = UnitItemInSlot(unit({box}), 0) if it then RemoveItem(it) end return true')

    for item, mana in ITEM_MANA.items():
        clear_box()
        lua_value(game, f'SetUnitState(unit({box}), UNIT_STATE_MANA, 0) return true')
        use(box, item)
        got = box_mana()
        game.log(f'{item} used by the box: {got:.2f} mana')
        findings.check(abs(got - mana) < 0.2, f'{item} gave the box {got:.2f} mana, not {mana}')
    clear_box()
    lua_value(game, f'SetUnitState(unit({box}), UNIT_STATE_MANA, 0) return true')
    use(builder, COIN)
    findings.check(box_mana() < 0.2, 'a Gold Coin used by the builder gave the box mana')

    def rocks_and_bag():
        return lua_value(game, f'local r = GetItemOfTypeFromUnitBJ(unit({builder}), FourCC("{ROCKS}")) '
                               f'return (r and GetItemCharges(r) or -1) .. " " .. '
                               f'tostring(UnitHasItemOfTypeBJ(unit({builder}), FourCC("{LOOTBAG}")))')

    # A stack of 5 used stays 5; 9 make a Lootbag and are gone; 12 make one and leave 3
    for stack, expected in ((5, '5 false'), (9, '-1 true'), (12, '3 true')):
        lua_value(game, f'for i = 0, 5 do local it = UnitItemInSlot(unit({builder}), i) if it then RemoveItem(it) end end return true')
        use(builder, ROCKS, stack)
        state = rocks_and_bag()
        game.log(f'{stack} Rocks used: Rocks left and a Lootbag: {state}')
        findings.check(state == expected, f'{stack} Rocks used left "{state}" (Rocks, a Lootbag), not "{expected}"')
    findings.raise_if_any('Loot Boxer items')


def test_scoreboard_follows_the_wave(game):
    """In Sandbox mode the scoreboard's level, armour and creep type follow the current wave (they
    stayed wave 1's): screenshots of red's after -wave 35 (a boss wave) and -wave 12. The board's
    text cannot be read from the map, so the screenshots are the evidence."""
    maul.start(game)
    for wave in (35, 12):
        game.cmd(RED, f'-wave {wave}')
        time.sleep(2)
        try:
            game.log(f'the scoreboard on wave {wave}: {game.screenshot(0)}')
        except TestFailed as problem:
            game.log(f'no screenshot of the scoreboard on wave {wave}: {problem}')


# Human's Cold Tower: always attacks the creep in range furthest along the path (FrontmostTargeting.ts)
COLD_TOWER, HUMAN_TOWN_HALL = 'h04B', 'I006'
WAVE_CREEP_PLAYERS = (13, 14, 15, 16)
# South of red's path where it runs straight west, from the checkpoint at x -2432 to the one at
# -3584 and on to the teleporter at -4352, all at y 4736: there the creep furthest along is the
# one furthest west
COLD_TOWER_SPOT = (-3008, 4224)
PATH_Y, PATH_EAST = 4736, -2432
# How far a creep may stray from the path's line and still count as on it, and how far east of the
# westmost a pick may stand (creeps of one row walk side by side)
PATH_SLACK, ROW_SLACK = 160, 64


def front_picks(game, tower, since=0):
    """The Cold Tower's picks: [(picked, [(ref, x, y), ...] best first)]."""
    marker = f' target {tower} front '
    picks = []
    for line in game.events('target')[since:]:
        if marker not in line:
            continue
        head, ranked = line.split(marker, 1)[1].split(' of ', 1)
        candidates = []
        for entry in ranked.split():
            # The note lists the best few as ref@x,y; a line cut short leaves a stub
            ref, _, at = entry.partition('@')
            x, _, y = at.partition(',')
            if ref.isdigit() and x.lstrip('-').isdigit() and y.lstrip('-').isdigit():
                candidates.append((int(ref), int(x), int(y)))
        picks.append((int(head.split()[0]), candidates))
    return picks


def test_cold_tower_targets_the_frontmost(game):
    """The Cold Tower attacks the creep in range furthest along the path: with a wave walking red's
    straight leg west, every pick it makes among creeps on that leg is the westmost of them (give
    or take a row walking side by side), and its attacks follow its picks."""
    findings = Findings()
    maul.start(game)
    # Creeps only come to the lanes of players with a race
    maul.pick(game, RED, HUMAN_TOWN_HALL)
    # Hits are only traced on a watched player's units, and a wave's creeps belong to all four
    # creep players (Navy, Turquoise, Violet, Wheat)
    for creep_player in WAVE_CREEP_PLAYERS:
        game.watch(creep_player)
    game.cmd(RED, '-lives 1000000')
    tower = place_tower(game, COLD_TOWER, COLD_TOWER_SPOT)
    # A ground wave, made tougher so the creeps take a few hits and the front changes hands
    game.cmd(RED, '-diff 400')
    game.cmd(RED, '-wave 2')
    time.sleep(1)
    notes = len(game.events('target'))
    maul.start_wave(game)
    time.sleep(30)
    picks = front_picks(game, tower, notes)
    on_leg = [(picked, cands) for picked, cands in picks
              if len(cands) > 1 and any(ref == picked for ref, _, _ in cands)
              and all(abs(y - PATH_Y) <= PATH_SLACK and x <= PATH_EAST for _, x, y in cands)]
    wrong = [(picked, cands) for picked, cands in on_leg
             if next(x for ref, x, _ in cands if ref == picked) > min(x for _, x, _ in cands) + ROW_SLACK]
    game.log(f'{len(picks)} picks, {len(on_leg)} among creeps on the straight leg, {len(wrong)} not the westmost')
    for picked, cands in wrong[:3]:
        game.log(f'  picked {picked} of {cands}')
    findings.check(len(on_leg) >= 5, f'only {len(on_leg)} picks among creeps on the straight leg, too few to judge')
    findings.check(not wrong, f'{len(wrong)} of {len(on_leg)} picks were not the creep furthest along')
    # Its attacks go where it picked: the swing under way when it picks still lands on the old
    # target, so the picked creep takes one of the next two attacks
    attacks = [(tick_of(line), trace.parse_fields(line)) for line in game.events('hit')
               if f' src={tower} ' in line and 'attack=true' in line]
    pick_ticks = [(tick_of(line), int(line.split(f' target {tower} front ', 1)[1].split()[0]))
                  for line in game.events('target')[notes:] if f' target {tower} front ' in line]
    followed = sum(1 for tick, picked in pick_ticks
                   if picked in [f.get('dst') for t, f in attacks if t > tick][:2])
    game.log(f'attacks after a pick that landed on the picked creep: {followed} of {len(pick_ticks)}')
    findings.check(not pick_ticks or followed >= 0.7 * len(pick_ticks),
                   f'only {followed} of {len(pick_ticks)} picks were followed by an attack on the picked creep')
    findings.raise_if_any('the Cold Tower')


# Walkers for the frost tests: a creep player's units with endless life, walking red's straight leg
FROST_WALK = ((-1900, PATH_Y), (-4100, PATH_Y))


def walker_position(game, walker):
    """(tick, x, y) of a unit, read in game."""
    before = len(game.events('lua'))
    game.cmd(RED, f'.lua return GetUnitX(unit({walker})) .. " " .. GetUnitY(unit({walker}))')
    game.wait('a position', lambda: len(game.events('lua')) > before, 10)
    line = game.events('lua')[before]
    x, y = line.split(' lua ok ', 1)[1].split()
    return tick_of(line), float(x), float(y)


def frosted(game, walker):
    return lua_value(game, f'return GetUnitAbilityLevel(unit({walker}), FourCC("Bfro"))') not in ('0', 'nil')


def frost_walk(game, kinds, tower=True):
    """Walkers of these kinds side by side, ordered down the leg: [(speed, frosted at any look)].
    The first one's speed is measured once it carries the frost (with no tower, at the same point
    of its walk, about 1.5 s in), over 1.5 s of game time; the others are only looked at."""
    start, end = FROST_WALK
    walkers = [game.create(13, kind, start[0], start[1] + 96 * i, life=TARGET_LIFE)[0]['id']
               for i, kind in enumerate(kinds)]
    for i, walker in enumerate(walkers):
        lua_value(game, f'return IssuePointOrder(unit({walker}), "move", {end[0]}, {end[1] + 96 * i})')
    seen = {walker: False for walker in walkers}
    for _ in range(10 if tower else 0):
        time.sleep(0.3)
        for walker in walkers:
            seen[walker] = seen[walker] or frosted(game, walker)
        if seen[walkers[0]]:
            break
    if not tower:
        time.sleep(1.5)
    first = walker_position(game, walkers[0])
    time.sleep(1.5)
    last = walker_position(game, walkers[0])
    for walker in walkers:
        seen[walker] = seen[walker] or frosted(game, walker)
    speed = math.dist(first[1:], last[1:]) / max((last[0] - first[0]) * TICK, TICK)
    for walker in walkers:
        game.cmd(RED, f'.lua RemoveUnit(unit({walker}))')
    time.sleep(1)
    return [(speed if walker == walkers[0] else 0.0, seen[walker]) for walker in walkers]


def test_cold_tower_frost(game):
    """The Cold Tower's frost attack (AC01, a copy of Workers Union's A0EU that also reaches ground
    units): a footman and a gryphon of a creep player, with endless life, walk red's straight leg.
    With no tower they walk at full speed; past one Cold Tower both carry the frost buff (Bfro) and
    walk slower; past six they are slowed no more than by one (one buff, refreshed) and still walk.
    Also logs whether a second footman beside the first is slowed too."""
    findings = Findings()
    maul.start(game)
    for player in WAVE_CREEP_PLAYERS:
        game.watch(player)
    free = frost_walk(game, ['hfoo'], tower=False) + frost_walk(game, ['hgry'], tower=False)
    game.log(f'no tower: footman {free[0][0]:.0f}, gryphon {free[1][0]:.0f} a second')
    towers = [place_tower(game, COLD_TOWER, COLD_TOWER_SPOT)]
    findings.check(lua_value(game, f'return GetUnitAbilityLevel(unit({towers[0]}), FourCC("AC01"))') == '1',
                   'the Cold Tower has no AC01')
    one = frost_walk(game, ['hfoo']) + frost_walk(game, ['hgry'])
    game.log(f'one Cold Tower: footman {one[0][0]:.0f} (frost {one[0][1]}), gryphon {one[1][0]:.0f} (frost {one[1][1]})')
    for (speed, seen), (base, _), name in zip(one, free, ('footman', 'gryphon')):
        findings.check(seen, f'the {name} past a Cold Tower has no frost buff')
        findings.check(speed < 0.85 * base, f'the {name} walked {speed:.0f} past a Cold Tower, {base:.0f} free')
    towers += [place_tower(game, COLD_TOWER, (COLD_TOWER_SPOT[0] - 128 * i, COLD_TOWER_SPOT[1])) for i in range(1, 6)]
    six = frost_walk(game, ['hfoo']) + frost_walk(game, ['hgry'])
    game.log(f'six Cold Towers: footman {six[0][0]:.0f}, gryphon {six[1][0]:.0f}')
    for (speed, _), (single, _), (base, _), name in zip(six, one, free, ('footman', 'gryphon')):
        findings.check(speed >= 0.3 * base, f'six Cold Towers all but stopped the {name}: {speed:.0f} of {base:.0f}')
        findings.check(speed >= 0.85 * single, f'six Cold Towers slowed the {name} more than one: {speed:.0f}, {single:.0f}')
    for tower in towers[1:]:
        game.cmd(RED, f'.lua RemoveUnit(unit({tower}))')
    pair = frost_walk(game, ['hfoo', 'hfoo'])
    game.log(f'two footmen side by side past one Cold Tower: frost {pair[0][1]} and {pair[1][1]} '
             f'(the tower may switch between them, so this does not tell whether the frost splashes)')
    findings.raise_if_any('the Cold Tower\'s frost')


UNDEAD_ACOLYTE = 'h03I'


def test_undead_acolyte_frost(game):
    """Workers Union's Undead Acolyte attacks ground only, and its frost attack was A0EU, which
    reaches air only: it never slowed anything. It has AC01 now, which reaches ground: a footman
    walking past it carries the frost and walks slower."""
    findings = Findings()
    maul.start(game)
    for player in WAVE_CREEP_PLAYERS:
        game.watch(player)
    free = frost_walk(game, ['hfoo'], tower=False)[0][0]
    acolyte = place_tower(game, UNDEAD_ACOLYTE, COLD_TOWER_SPOT)
    abilities = lua_value(game, f'return GetUnitAbilityLevel(unit({acolyte}), FourCC("AC01")) .. " " .. '
                                f'GetUnitAbilityLevel(unit({acolyte}), FourCC("A0EU"))')
    findings.check(abilities == '1 0', f'the Acolyte\'s AC01 and A0EU levels are {abilities}, not 1 and 0')
    speed, seen = frost_walk(game, ['hfoo'])[0]
    game.log(f'footman: {free:.0f} free, {speed:.0f} past an Undead Acolyte (frost {seen})')
    findings.check(seen, 'the footman past an Undead Acolyte has no frost buff')
    findings.check(speed < 0.85 * free, f'the footman walked {speed:.0f} past an Undead Acolyte, {free:.0f} free')
    findings.raise_if_any('the Undead Acolyte\'s frost')


def test_race_picker_tabs(game):
    """Debug builds: the race picker's Dev tab sits right under the tiers while the Secondary tab is
    hidden (it used to keep Secondary's slot, leaving a gap), and under Secondary once that shows
    (after a first race). Screenshots of red's picker, before and after a pick; the tabs' places
    are also read from the frames."""
    findings = Findings()
    maul.start(game)
    lua_value(game, '_G.F = function(name) return BlzGetFrameByName(name, 0) end return true')

    def open_picker():
        lua_value(game, 'if GetLocalPlayer() == Player(0) then '
                        'BlzFrameClick(BlzGetFrameByName("raceSelectButton3", 0)) end return true')
        time.sleep(1.5)

    def shot(what):
        try:
            game.log(f'the race picker, {what}: {game.screenshot(0)}')
        except TestFailed as problem:
            game.log(f'no screenshot of the race picker, {what}: {problem}')
    shot('before a race')
    maul.pick(game, RED, HUMAN_TOWN_HALL)
    open_picker()
    shot('after a race')
    findings.raise_if_any('the race picker')



# The picks with a guide in the race picker (wc3-ts-template RaceGuides.ts): their tab, and their
# row's text, with which match of it to click (the Random tab and the Random pick share a name)
GUIDED_PICKS = (('Random', 'Random', 2), ('Random', 'Hardcore Random', 1), ('Random', 'Hybrid Random', 1),
                ('Beginner', 'Human Town Hall', 1), ('Beginner', 'Orc Stronghold [Geno]', 1),
                ('Beginner', 'High Elf Barracks [Geno]', 1), ('Beginner', 'Outland', 1), ('Beginner', 'High Elf Farm', 1),
                ('Beginner', 'Giants', 1), ('Beginner', 'Draenei Haven [Geno]', 1), ('Beginner', 'Corrupted Night Elves', 1),
                ('Beginner', 'Caerbannog', 1), ('Beginner', 'Critters [Geno]', 1), ('Beginner', 'Forest Troll Hut', 1),
                ('Advanced', 'The Elementalists', 1))


def test_race_guides(game):
    """Screenshots of red's race picker with each pick that has a guide shown in its information
    pane - the three random picks, the Beginner races and the Elementalists - for looking at whether
    each guide fits the pane and is not under the scoreboard (folded while the picker is open), and
    one once the picker is closed, the scoreboard back. A tab and a row are clicked by their text
    among the panel's buttons."""
    findings = Findings()
    maul.start(game)
    # The n-th of the panel's buttons whose text, without colour codes, is t, and a click on it. The
    # clicks are made on both clients, each in its own picker (tabs and rows only change what that
    # client shows), so both pickers are alike and every check notes the same line on both.
    # (One short line each: a command line is cut at chat length.)
    lua_value(game, '_G.plain = function(s) return (s:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")) end return true')
    lua_value(game, '_G.kid = function(i) return BlzFrameGetChild(BlzGetFrameByName("raceSelectPanel", 0), i) end return true')
    lua_value(game, '_G.nth = function(t, n) for i = 0, BlzFrameGetChildrenCount(BlzGetFrameByName("raceSelectPanel", 0)) - 1 do '
                    'local s = BlzFrameGetText(kid(i)) if s and plain(s) == t then n = n - 1 if n == 0 then return kid(i) end end end end return true')
    lua_value(game, '_G.clk = function(t, n) local c = nth(t, n) if c then BlzFrameClick(c) end return c ~= nil end return true')
    for tab, pick, match in GUIDED_PICKS:
        if not lua_true(game, f'return clk("{tab}", 1)'):
            findings.append(f'no {tab} tab')
            continue
        time.sleep(0.5)
        if not lua_true(game, f'return clk("{pick}", {match})'):
            # Further down the list: the scrollbar to its bottom (its top is its maximum)
            lua_true(game, 'BlzFrameSetValue(BlzGetFrameByName("raceSelectScroll", 0), 0) return true')
            time.sleep(0.5)
            if not lua_true(game, f'return clk("{pick}", {match})'):
                findings.append(f'no {pick} row in the {tab} tab')
                continue
        time.sleep(0.5)
        findings.check(lua_true(game, f'return plain(BlzFrameGetText(BlzGetFrameByName("raceSelectInfoName", 0))) == "{pick}"'),
                       f'clicked {pick}, the pane shows another pick')
        try:
            game.log(f'{pick}: {game.screenshot(0)}')
        except TestFailed as problem:
            game.log(f'no screenshot of {pick}: {problem}')
    # The scoreboard folds while the picker is open (it covers the pane) and comes back when it closes
    lua_true(game, 'BlzFrameClick(BlzGetFrameByName("raceSelectClose", 0)) return true')
    time.sleep(1)
    try:
        game.log(f'the picker closed, the scoreboard back: {game.screenshot(0)}')
    except TestFailed as problem:
        game.log(f'no screenshot of the closed picker: {problem}')
    findings.raise_if_any('race guides')


# A test maze in red's lane, between its first checkpoint (-2432, 4736) and its second
# (-3584, 4736): three walls of plain Towers across the lane, their gaps at alternate ends, so the
# creeps walk four passes. A Cold Tower stands in the middle wall and reaches all of them.
MAZE_WALL = 'hC66'
MAZE_CHECKPOINTS = ((-2432, 4736), (-3584, 4736))
MAZE_ROWS = list(range(4032, 5441, 128))
MAZE_WALLS = ((-2688, 'top'), (-3008, 'bottom'), (-3328, 'top'))
MAZE_COLD_TOWER = (-3008, 4736)


def maze_spots():
    """The walls' Tower spots: each wall the whole lane high but for two at its gap's end."""
    spots = []
    for x, gap in MAZE_WALLS:
        rows = MAZE_ROWS[:-2] if gap == 'top' else MAZE_ROWS[2:]
        spots += [(x, y) for y in rows]
    return spots


def path_distances(blocked_centres, area, goal):
    """Distance along the ground from every 64 cell of the area to the goal, around 128 x 128
    footprints at the given centres (8 directions, Dijkstra): {(i, j): distance}, and the cell of a point."""
    import heapq
    min_x, min_y, max_x, max_y = area
    columns, rows = int((max_x - min_x) // maul.GRID), int((max_y - min_y) // maul.GRID)
    centre = lambda i, j: (min_x + maul.GRID / 2 + maul.GRID * i, min_y + maul.GRID / 2 + maul.GRID * j)
    cell = lambda x, y: (int((x - min_x) // maul.GRID), int((y - min_y) // maul.GRID))
    blocked = {(i, j) for i in range(columns) for j in range(rows)
               if any(abs(centre(i, j)[0] - bx) < 64 and abs(centre(i, j)[1] - by) < 64 for bx, by in blocked_centres)}
    start = cell(*goal)
    distance = {start: 0.0}
    queue = [(0.0, start)]
    while queue:
        d, (i, j) = heapq.heappop(queue)
        if d > distance[(i, j)]:
            continue
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                n = (i + di, j + dj)
                if (di or dj) and 0 <= n[0] < columns and 0 <= n[1] < rows and n not in blocked:
                    # No corner cutting past a footprint
                    if di and dj and ((i + di, j) in blocked or (i, j + dj) in blocked):
                        continue
                    nd = d + maul.GRID * (1.414 if di and dj else 1)
                    if nd < distance.get(n, float('inf')):
                        distance[n] = nd
                        heapq.heappush(queue, (nd, n))
    return distance, cell


def test_cold_tower_frontmost_in_a_maze(game):
    """The Cold Tower attacks the creep furthest along the path even where the path winds back
    past it: a maze of four passes in red's lane, the tower in its middle wall, a wave walking it.
    Each pick it makes is held up against the distance left along the ground to the maze's exit
    (Dijkstra over the 64 grid around the towers): the picked creep is the one with the least, give
    or take two cells, among those the pick's note lists (its best eight)."""
    findings = Findings()
    maul.start(game)
    maul.pick(game, RED, HUMAN_TOWN_HALL)
    for player in WAVE_CREEP_PLAYERS:
        game.watch(player)
    game.cmd(RED, '-lives 1000000')
    walls = maze_spots()
    for spot in walls:
        if spot != MAZE_COLD_TOWER:
            place_tower(game, MAZE_WALL, spot)
    tower = place_tower(game, COLD_TOWER, MAZE_COLD_TOWER)
    # Hardly any damage, so the creeps live to walk the whole maze and the front changes hands as
    # each leader walks out of reach
    lua_value(game, f'BlzSetUnitBaseDamage(unit({tower}), 10, 0) return true')
    area = maul.lane_areas()[RED]
    distance, cell = path_distances(walls, area, MAZE_CHECKPOINTS[1])
    entry = distance[cell(*MAZE_CHECKPOINTS[0])]
    game.log(f'maze: {len(walls)} spots, {entry:.0f} along the ground from checkpoint to checkpoint '
             f'({MAZE_CHECKPOINTS[0][0] - MAZE_CHECKPOINTS[1][0]} straight)')
    findings.check(entry > 2 * (MAZE_CHECKPOINTS[0][0] - MAZE_CHECKPOINTS[1][0]), 'the maze does not wind')

    def left(x, y):
        """Distance left to the maze's exit: east of the first checkpoint a creep has yet to reach it."""
        if x > MAZE_CHECKPOINTS[0][0] + 32:
            return math.dist((x, y), MAZE_CHECKPOINTS[0]) + entry
        return distance.get(cell(x, y), float('inf'))

    game.cmd(RED, '-wave 2')
    time.sleep(1)
    notes = len(game.events('target'))
    seen_y = []
    maul.start_wave(game)
    for _ in range(45):
        time.sleep(1)
        seen_y += [p[1] for p in creep_positions(game).values()
                   if area[0] < p[0] < MAZE_CHECKPOINTS[0][0] and area[1] < p[1] < area[3]]
    game.log(f'creeps in the maze stood between y {min(seen_y, default=0):.0f} and {max(seen_y, default=0):.0f}')
    picks = front_picks(game, tower, notes)
    judged, wrong = 0, []
    for picked, cands in picks:
        if len(cands) < 2 or not any(ref == picked for ref, _, _ in cands):
            continue
        lefts = {ref: left(x, y) for ref, x, y in cands}
        if any(v == float('inf') for v in lefts.values()):
            continue
        judged += 1
        if lefts[picked] > min(lefts.values()) + 2 * maul.GRID:
            wrong.append((picked, {ref: round(v) for ref, v in lefts.items()}))
    game.log(f'{len(picks)} picks, {judged} judged, {len(wrong)} not the creep furthest along')
    for picked, lefts in wrong[:4]:
        game.log(f'  picked {picked}: distance left {lefts}')
    findings.check(judged >= 8, f'only {judged} picks to judge')
    findings.check(not wrong, f'{len(wrong)} of {judged} picks were not the creep furthest along the maze')
    findings.raise_if_any('the Cold Tower in a maze')


def creep_positions(game):
    """{id: (x, y)} of every wave creep."""
    return {u['id']: (u['x'], u['y']) for player in WAVE_CREEP_PLAYERS for u in game.units(player)}


ARROW_TOWER = 'h042'
ECHO_DAMAGE = 7


def test_damage_from_inside_a_damage_event(game):
    """Damage dealt from inside a damage event (the .dmgecho hook: an Arrow Tower's every hit deals
    7 chaos damage more from its on-hit event) is held back by the damage engine and dealt after
    the event. As Damage Engine 5.A has it, the handler then runs once on its own echo, whose echo
    is dealt with the handler frozen: two echoes for each attack - not none (lost), not more (the
    echo echoing itself, which once could hang the game) - and the game goes on. Each echo shows
    first as a 0-damage hit, the engine taking it off the game to deal it later. (The watcher's raw
    amount is kept by target, so nested hits muddle it: echoes are told by their amount.)"""
    findings = Findings()
    maul.start(game)
    for player in WAVE_CREEP_PLAYERS:
        game.watch(player)
    spot = red_lane_spots()[5]
    tower = place_tower(game, ARROW_TOWER, spot)
    game.create(CREEPS, maul.GROUND_TARGET, spot[0], spot[1] + 150, life=TARGET_LIFE, rooted=True)
    before = len(game.events('echo'))
    game.cmd(RED, f'.dmgecho {tower} {ECHO_DAMAGE}')
    game.wait('the echo hook', lambda: len(game.events('echo')) > before, 10)
    findings.check(' on ' in game.events('echo')[-1], f'the echo hook was refused: {game.events("echo")[-1]}')
    hits = [f for _, f in watch_hits(game, 10) if f.get('src') == tower]
    attacks = [f for f in hits if f.get('attack') == 'true']
    chaos = [f for f in hits if f.get('attack') != 'true' and f.get('atk') == 'chaos']
    echoes = [f for f in chaos if 0 < f.get('amount', 0) <= ECHO_DAMAGE]
    held = [f for f in chaos if f.get('amount', 0) == 0]
    others = [f for f in hits if f not in attacks and f not in echoes and f not in held]
    game.log(f'{len(attacks)} attacks, {len(echoes)} echoes, {len(held)} held back, {len(others)} other hits')
    findings.check(len(attacks) >= 5, f'only {len(attacks)} attacks to judge')
    # The last attack's echoes may fall outside the window
    findings.check(abs(len(echoes) - 2 * len(attacks)) <= 2,
                   f'{len(echoes)} echoes for {len(attacks)} attacks, two each expected: lost, or echoed on')
    findings.check(abs(len(held) - len(echoes)) <= 2, f'{len(held)} held back for {len(echoes)} echoes')
    findings.check(not others, f'hits from the tower that are neither its attacks nor echoes: {others[:3]}')
    # The game goes on: a fresh heartbeat
    maul.fresh_beat(game)
    findings.raise_if_any('damage from inside a damage event')


HYBRID_RANDOM, HYBRID_BUILDER = 'I00X', 'e00I'
# A free 2x2 of cells in red's maze (its area's corner is -4224, 3968; cells are 64): clear of
# both checkpoints (y 4736)
HYBRID_BUILD_CELLS = ((24, 5), (28, 5))


def test_hybrid_build_menu(game):
    """The hybrid build menu, on the right above the command card (screenshots): picking a tower
    closes it. With its Keep open button (HybridBuildPanel.KEEP_OPEN_BUTTON in the map, off for
    now) in the map, Keep open on leaves it open through a pick and the build, and off closes it
    on a pick again. Buttons are clicked in red's menu; a tower is placed with the message red's own
    click sends."""
    findings = Findings()
    maul.start(game)
    game.cmd(RED, '.gold 1000')
    game.wait_for('the gold to be set', lambda beat: beat.gold(RED) == 1000)
    before = len(game.events('hybrid'))
    maul.sync(game, RED, f'race-pick:{HYBRID_RANDOM}')
    game.wait('Hybrid Random to hand out towers', lambda: any(' hybrid p0 ' in line for line in game.events('hybrid')[before:]), 20)
    game.wait('the hybrid builder', lambda: any(u['type'] == HYBRID_BUILDER for u in game.units(RED)), 15)

    def shot(what):
        try:
            game.log(f'{what}: {game.screenshot(0)}')
        except TestFailed as problem:
            game.log(f'no screenshot, {what}: {problem}')

    def click(name):
        lua_true(game, f'if GetLocalPlayer() == Player(0) then BlzFrameClick(BlzGetFrameByName("{name}", 0)) end return true')
        time.sleep(0.7)

    # Frame visibility is local: "not red, or ..." notes the same line on both clients when it holds
    def menu_open():
        return lua_true(game, 'return GetLocalPlayer() ~= Player(0) or BlzFrameIsVisible(BlzGetFrameByName("hybridBuildPanel", 0))')

    def menu_closed():
        return lua_true(game, 'return GetLocalPlayer() ~= Player(0) or not BlzFrameIsVisible(BlzGetFrameByName("hybridBuildPanel", 0))')

    def build(cell):
        count = len(game.units(RED))
        maul.sync(game, RED, f'build:0:{cell[0]}:{cell[1]}:0:0')
        game.wait(f'a tower at cell {cell}', lambda: len(game.units(RED)) > count, 15)
        time.sleep(0.7)

    game.cmd(RED, '-build')
    time.sleep(1)
    findings.check(menu_open(), '-build did not open the menu')
    shot('the menu')

    if not lua_true(game, 'return BlzGetFrameByName("hybridBuildKeepOpen", 0) ~= nil'):
        game.log('no Keep open button in this build')
        click('hybridBuildTier0')
        findings.check(menu_closed(), 'picking a tower left the menu open')
        findings.raise_if_any('the hybrid build menu')
        return

    click('hybridBuildKeepOpen')
    click('hybridBuildTier0')
    findings.check(menu_open(), 'with Keep open on, picking a tower closed the menu')
    shot('Keep open on, a tower picked')
    try:
        build(HYBRID_BUILD_CELLS[0])
        findings.check(menu_open(), 'with Keep open on, building the tower closed the menu')
    except TestFailed as error:
        findings.append(f'with Keep open on: {error}')

    click('hybridBuildKeepOpen')
    click('hybridBuildTier0')
    findings.check(menu_closed(), 'with Keep open off, picking a tower left the menu open')
    shot('Keep open off, a tower picked')
    findings.raise_if_any('the hybrid build menu')


# Every Primal, beside the line red's wave walks between its two checkpoints (y 4736), the Heart
# of Life (melee) a cell from it
WAVE_PRIMALS = (('Lich', 'uP01', (-2688, 4864)), ('Thunderhead', 'uP02', (-2688, 4608)),
                ('Haboob', 'uP03', (-3008, 4864)), ('Heart of Life', 'uP04', (-3008, 4608)),
                ('World Tree', 'uP05', (-3328, 4864)), ('Inferno', 'uP06', (-3328, 4608)))
# Wave creeps' life while the Primals are watched, so no Primal kills the wave before the others reach it
WAVE_LIFE = 50_000_000


def test_primals_attack_a_wave(game):
    """Every Primal, just made, attacks a real ground wave (wave 2) walking past it - a player's
    Primal "did nothing", so this looks beyond the test targets the other Primal tests use - but
    the Thunderhead, which attacks air only and must leave it (test_thunderhead sees it strike air).
    The creeps' life is raised so that every Primal gets to reach them."""
    findings = Findings()
    maul.start(game)
    maul.pick(game, RED, HUMAN_TOWN_HALL)
    for player in WAVE_CREEP_PLAYERS:
        game.watch(player)
    towers = {name: place_tower(game, kind, spot) for name, kind, spot in WAVE_PRIMALS}
    # creeps(f): f on every wave creep, the four creep players' units (one short line each: a
    # command line is cut at chat length)
    lua_value(game, '_G.creeps = function(f) for p = 13, 16 do local g = CreateGroup() GroupEnumUnitsOfPlayer(g, Player(p), nil) '
                    'ForGroup(g, function() f(GetEnumUnit()) end) DestroyGroup(g) end end return true')
    raise_life = f'creeps(function(u) BlzSetUnitMaxHP(u, {WAVE_LIFE}) SetWidgetLife(u, {WAVE_LIFE}) end) return true'
    game.cmd(RED, '-wave 2')
    time.sleep(1)
    maul.start_wave(game)
    game.wait_for('the wave to spawn', lambda b: (b.get('creeps') or 0) > 0, 30)
    lua_value(game, raise_life)
    time.sleep(3)
    lua_value(game, raise_life)
    # Ground by most of the creep players' units (one flying unit comes with it)
    counts = lua_value(game, 'local all, fly = 0, 0 creeps(function(u) all = all + 1 '
                             'if IsUnitType(u, UNIT_TYPE_FLYING) then fly = fly + 1 end end) return fly .. "/" .. all')
    fly, total = (int(n) for n in counts.split('/'))
    findings.check(fly * 2 < total, f'wave 2 is not a ground wave: {counts} flying')
    hits = [f for _, f in watch_hits(game, 30)]
    for name, kind, spot in WAVE_PRIMALS:
        count = sum(1 for f in hits if f.get('src') == towers[name])
        game.log(f'{name:14} {count} hits')
        if name == 'Thunderhead':
            findings.check(count == 0, f'the Thunderhead hit a ground wave {count} times; it attacks air only')
        else:
            findings.check(count > 0, f'the {name} did not hit the wave')
    findings.raise_if_any('Primals against a wave')


# Towers with an autocast ability, and that ability (the map switches it on as a tower is set up,
# Autocast.ts): Arachnid's Parasite (its class casts it on attacks too) and the Chaos Warlock's
# Bloodlust. (The Dragons' Slow cannot be switched to autocast; their class casts it, see todo.md.)
AUTOCAST_TOWERS = (('Arachnid Parasite', 'o01A', 'A01I'), ('Chaos Warlock Bloodlust', 'o005', 'A004'))


def test_autocast_on_from_the_start(game):
    """A tower's autocast ability is on as it stands, with nobody switching it on: each of these
    towers, beside a target, casts its ability by itself (a player had to turn Arachnid's
    Parasitic Bite on for every tower)."""
    findings = Findings()
    maul.start(game)
    game.watch_casts(RED)
    spots = red_lane_spots()
    towers = {}
    for index, (name, kind, ability) in enumerate(AUTOCAST_TOWERS):
        spot = spots[5 + 6 * index]
        towers[name] = place_tower(game, kind, spot)
        game.create(CREEPS, maul.GROUND_TARGET, spot[0], spot[1] + 150, life=TARGET_LIFE, rooted=True)
    time.sleep(20)
    casts = game.casts()
    for name, kind, ability in AUTOCAST_TOWERS:
        count = sum(1 for c in casts if c.get('src') == towers[name] and c.get('ability') == ability)
        game.log(f'{name:24} {count} casts')
        findings.check(count > 0, f'the {name} never cast {ability}: its autocast is not on')
    findings.raise_if_any('autocast')


DORMANT_EGG, PHOENIX_EGG, AWAKEN, AWAKEN_PRICE = 'u01D', 'h006', 'neutralspell', 250
EGG_SPOTS = ((-2688, 4864), (-3008, 4864))


def test_dormant_egg_awaken(game):
    """The Dormant Phoenix Egg's Awaken: 250 gold at once, to skip the wait, and free once the egg
    has gained 5 mana, one a wave. A fresh egg awakens into a Phoenix Egg for 250; an egg at 4 mana
    reaches 5 at the end of a wave and awakens for nothing."""
    findings = Findings()
    maul.start(game)
    maul.pick(game, RED, HUMAN_TOWN_HALL)
    game.cmd(RED, '.gold 1000')
    game.wait_for('the gold to be set', lambda beat: beat.gold(RED) == 1000)
    eggs = [place_tower(game, DORMANT_EGG, spot) for spot in EGG_SPOTS]

    def awaken(egg, spot, price, what):
        before = maul.fresh_beat(game).gold(RED)
        game.order(RED, egg, AWAKEN)
        try:
            game.wait(f'{what} to awaken', lambda: unit_at(game, RED, spot, (PHOENIX_EGG,)), 10)
        except TestFailed as error:
            findings.append(f'{what}: {error}')
            return
        paid = before - maul.fresh_beat(game).gold(RED)
        game.log(f'{what} awakened for {paid}')
        findings.check(paid == price, f'{what} awakened for {paid} gold, not {price}')

    awaken(eggs[0], EGG_SPOTS[0], AWAKEN_PRICE, 'a fresh egg')
    # Four waves' mana, and the fifth from a wave's end
    lua_value(game, f'SetUnitState(unit({eggs[1]}), UNIT_STATE_MANA, 4) return true')
    game.cmd(RED, '-wave 1')
    time.sleep(1)
    maul.start_wave(game)
    game.wait_for('the wave to spawn', lambda b: (b.get('creeps') or 0) > 0, 30)
    time.sleep(3)
    lua_value(game, '_G.creeps = function(f) for p = 13, 16 do local g = CreateGroup() GroupEnumUnitsOfPlayer(g, Player(p), nil) '
                    'ForGroup(g, function() f(GetEnumUnit()) end) DestroyGroup(g) end end return true')
    lua_value(game, 'creeps(KillUnit) return true')
    game.wait_for('the wave to end', lambda b: not b.get('spawning') and (b.get('creeps') or 0) == 0, 60)
    time.sleep(2)
    mana = lua_value(game, f'return math.floor(GetUnitState(unit({eggs[1]}), UNIT_STATE_MANA))')
    findings.check(mana == '5', f'the egg has {mana} mana after the wave, not 5')
    awaken(eggs[1], EGG_SPOTS[1], 0, 'an egg at 5 mana')
    findings.raise_if_any('Awaken')


# A Primal and two Ascended (one whose power is a burn), and a phrase each one's own text has
POWER_TOWERS = (('Haboob', 'uP03', (-2688, 4864), 'Hits up to 8 targets'),
                ('Lich King', 'uA01', (-3008, 4864), 'The Lich, ascended'),
                ('Firelord', 'uA06', (-3328, 4864), 'The Inferno, ascended'))


def test_power_button(game):
    """A Primal's and an Ascended's Power says what the tower does and what it has gained, in the
    tooltip red sees with the tower selected (the game shows an ability's tooltip, not a tower's
    own copy, so the map sets red's view of it on selection): the tower's own description, its
    Attunement and its damage now - a burn for the Firelord - and after a wave Attunement 1, and an
    Ascended's growth counting the wave. A Haboob's multishot says 8 targets the same way, a
    Sandstorm selected after it 7 again, and a screenshot shows the Lich King's command card."""
    findings = Findings()
    maul.start(game)
    maul.pick(game, RED, HUMAN_TOWN_HALL)
    towers = {name: place_tower(game, kind, spot) for name, kind, spot, _ in POWER_TOWERS}

    def select(name):
        lua_true(game, f'if GetLocalPlayer() == Player(0) then ClearSelection() SelectUnit(unit({towers[name]}), true) end return true')
        time.sleep(1)

    # What red's screen shows; "not red, or ..." notes the same line on both clients when it holds
    def shows(ability, phrase):
        return lua_true(game, f'return GetLocalPlayer() ~= Player(0) or string.find(BlzGetAbilityExtendedTooltip('
                              f'FourCC("{ability}"), 0), "{phrase}", 1, true) ~= nil')

    for name, kind, spot, own in POWER_TOWERS:
        select(name)
        findings.check(shows('AP04', own), f'the {name}\'s Power tooltip lacks its description ("{own}")')
        findings.check(shows('AP04', 'Attunement: |cffffcc000|r / 15'), f'the {name}\'s Power tooltip does not say Attunement 0')
        now = 'Burns now' if name == 'Firelord' else 'Damage now'
        findings.check(shows('AP04', now), f'the {name}\'s Power tooltip does not say "{now}"')
        if name == 'Haboob':
            findings.check(shows('A0E4', 'hit 8 enemies'), 'the Haboob\'s multishot tooltip does not say 8')
    # A Sandstorm after the Haboob: the multishot's own tooltip again (7)
    towers['Sandstorm'] = place_tower(game, 'u024', (-3328, 4608))
    select('Haboob')
    select('Sandstorm')
    findings.check(shows('A0E4', 'hit 7 enemies'), 'a Sandstorm selected after the Haboob does not say 7')
    select('Lich King')
    try:
        game.log(f'the Lich King selected: {game.screenshot(0)}')
    except TestFailed as problem:
        game.log(f'no screenshot: {problem}')

    # A wave goes by: Attunement 1, and the Ascended grow by it
    game.cmd(RED, '-wave 1')
    time.sleep(1)
    maul.start_wave(game)
    game.wait_for('the wave to spawn', lambda b: (b.get('creeps') or 0) > 0, 30)
    time.sleep(3)
    lua_value(game, '_G.creeps = function(f) for p = 13, 16 do local g = CreateGroup() GroupEnumUnitsOfPlayer(g, Player(p), nil) '
                    'ForGroup(g, function() f(GetEnumUnit()) end) DestroyGroup(g) end end return true')
    lua_value(game, 'creeps(KillUnit) return true')
    game.wait_for('the wave to end', lambda b: not b.get('spawning') and (b.get('creeps') or 0) == 0, 60)
    time.sleep(2)
    # The Lich King stayed selected through the wave: its tooltip followed it
    findings.check(shows('AP04', 'Attunement: |cffffcc001|r / 15'), 'the selected Lich King\'s tooltip did not follow the wave')
    for name, kind, spot, own in POWER_TOWERS:
        select(name)
        findings.check(shows('AP04', 'Attunement: |cffffcc001|r / 15'), f'after a wave the {name}\'s Power tooltip does not say Attunement 1')
        if kind.startswith('uA'):
            findings.check(shows('AP04', 'Ascended for 1 wave'), f'after a wave the {name}\'s Power tooltip does not count it')
    findings.raise_if_any('the Power button')
