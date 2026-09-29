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
from races import (DEPLETED_ROCK, ELEMENTALIST_BUILDER, ELEMENTS, Findings, SIPHON, UNCHARGED_RUNE, build_chain,
                   build_in_red_lane, give_gold, red_lane_spots, unit_at)
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
    placed = build_in_red_lane(game, ELEMENTALIST_BUILDER, [(f'rune {n + 1}', UNCHARGED_RUNE) for n in range(16)],
                               findings)
    spots = [spot for _, spot in placed]
    game.wait('the runes to stand', lambda: all(unit_at(game, RED, spot, (UNCHARGED_RUNE,)) for spot in spots), 90)
    offers = {}
    for spot in spots:
        levels = game.inspect(unit_at(game, RED, spot, (UNCHARGED_RUNE,))['id'], *ELEMENTS)
        offers[spot] = [ELEMENTS[a][0] for a in ELEMENTS if levels.get(a, 0) > 0]
    needs = [['Life', 'Death'], ['Death', 'Death']]
    met = assign_runes(offers, needs)
    if len(met) < 2:
        raise TestFailed(f'the runes rolled no Undead and Death pair: {offers}')
    by_name = {name: (order, element) for name, order, element in ELEMENTS.values()}
    charged = {spot: element for i, two in met.items() for spot, element in zip(two, needs[i])}
    for spot, element in charged.items():
        game.order(RED, unit_at(game, RED, spot, (UNCHARGED_RUNE,))['id'], by_name[element][0])
    game.wait('the runes to take their element', lambda: all(unit_at(game, RED, spot, (by_name[element][1],))
                                                             for spot, element in charged.items()), 20)
    undead_spot, death_spot = met[0][0], met[1][0]
    for i, (source_spot, target_spot) in met.items():
        source = unit_at(game, RED, source_spot, (by_name[needs[i][0]][1],))
        target = unit_at(game, RED, target_spot, (by_name[needs[i][1]][1],))
        game.order(RED, source['id'], SIPHON, target['id'])
    game.wait('Undead and a Death Rune L2', lambda: unit_at(game, RED, undead_spot, (UNDEAD,))
              and unit_at(game, RED, death_spot, (LEVEL_2['Death'],)), 20)
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
    # 5% of its base (2,050) a wave
    findings.check(all(b - a >= 100 for a, b in zip(grown, grown[1:])), f'it did not gain about 5% a wave: {grown}')

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
    lines = [line for line in game.events('book', client) if f' book p{player} loaded ' in line]
    return int(lines[-1].rsplit(' ', 1)[1]) if lines else None


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
                                  'string.find(BlzFrameGetText(F("recipeBookPrimals")), "Thunderhead", 1, true) ~= nil)'),
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
