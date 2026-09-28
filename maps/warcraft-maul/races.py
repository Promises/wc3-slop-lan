"""The Beginner races, and the secondary race, checked against what the map says they are.

One test per Beginner race (made from the map's own race data, so a new or changed tower is
tested without touching this file):

- picking the race gives its builder and takes the pick's lumber;
- every tower and every upgrade builds, at the gold the map advertises;
- farms (High Elf Farm) give the food they advertise;
- every form of a tower that attacks - base forms as well as upgrades - hits a target of its
  own, with the attack type it advertises, for the damage its attack says (before armor);
- every aura reaches what it says it does - the tower itself, a tower of the race next to it,
  or the target - and nothing in another lane;
- towers with a behaviour class have the right one (the Barrelmaster once had the Kodo's).

Red and blue both pick the race and build, at the same time, so a race takes about half as
long; everything checked is still that one race. The towers are looked at in rounds, spread
over all 13 lanes so that none reaches another's target: one in the middle of each lane, and
several in each player's own (homesick towers may only stand there, and towers with auras never
do - they get a lane to themselves). A tower keeps its lane while it upgrades to its next form,
and is sold when it has no form left to look at. The game is in Debug mode, so no wave comes.

A failing race lists everything that is wrong with it, not just the first thing.

The secondary race (Shrine of Buffs, the only one so far) is only for a player who has a race,
and costs a pick's lumber like any other.
"""
import math
import re
import time

import maul
from maul import BLUE, CREEPS, RED
from slop import TestFailed, trace

# Behaviour classes a tower must have, or must not have, whatever the registration lists say.
# (KodoBeast is registered on no tower for now: the Chaos Kodo Beast has no Devour to cast.)
MUST_HAVE_CLASS = {}
MUST_NOT_HAVE_CLASS = {'oC58': 'KodoBeast'}

# What a tower's behaviour class makes it do while it attacks, as the harness sees it (from the
# classes' code in the map repo, src/World/Entity/Tower/Races):
# - the tower casts a spell itself (its class orders it to on each attack);
CASTS_ITSELF = {'Berserker', 'FleshGolem', 'DraeneiSeer', 'SalamanderLord', 'Akama', 'SirGalahad', 'Kael'}
# - a dummy casts this ability for it, on every attack or on a clock (Ogre Magi, every ~10 s; the
#   High Priest's monsoon, whenever it is off cooldown);
DUMMY_CASTS = {'OgreWarrior': 'A029', 'OgreMagi': 'A036', 'ForestTrollHighPriest': 'A03P'}
# - a dummy casts this ability by chance (per attack), so it is only judged after enough attacks;
DUMMY_CASTS_BY_CHANCE = {'WarchiefThrall': ('A03J', 0.05), 'Magtheridon': ('A0DT', 0.15)}
# - it makes units of this type (serpent wards);
MAKES_UNITS = {'Rokhan': 'o00H'}
# - it attacks random enemies in range: it gets this many targets in an arc in front of it, and
#   must spread its hits over them (one it stayed on, or the nearest one, would get nearly all)
RANDOM_TARGET, RANDOM_TARGETS = {'VenomTower'}, 8
# Not checked while attacking: kills (RockGiant, SeaGiant), time (AncientGolem), a wave running
# (IronGolemStatue, CorruptedAncientProtector) or research (CorruptedTreeofLife) - separate tests

# Base abilities that raise a tower's own damage (critical strikes, bash, damage auras), so its
# hits may go over what its attack says
RAISES_DAMAGE = {'AOcr', 'ACct', 'AHbh', 'Aakb', 'ACac', 'AEar'}
# Behaviour classes whose damage grows while they stand
GROWS = {'AncientGolem'}
# The map's homesick ability (AntiBlock): a tower with it may only stand in its owner's lane
HOMESICK = 'A0CR'

# A target stands this far from its tower (closer to one whose enemy aura is smaller than that);
# a tower of the race goes this far from one with a friendly aura, to see that it reaches
TARGET_DISTANCE = 160
NEIGHBOUR_DISTANCE = 256
# Gold for building everything a race has
GOLD = 1_000_000
# A round's attacks: at least this long, until each tower hit this often, at most this long
ROUND_AT_LEAST, WANTED_HITS, ROUND_SECONDS = 12, 3, 25


class Findings(list):
    def append(self, text):
        # A tower that stays wrong over several rounds is one problem
        if text not in self:
            super().append(text)

    def check(self, ok, text):
        if not ok:
            self.append(text)
        return ok

    def raise_if_any(self, what):
        if self:
            raise TestFailed(f'{what}: {len(self)} problem(s)\n  - ' + '\n  - '.join(self))


def red_units(game):
    return game.units(RED)


def towers_by_id(race):
    return {t['id']: t for t in race['towers']}


def named(data):
    return f'{data["id"]} {data["name"]}'


# Both players build at once: a build step is a generator that yields whenever it waits on the
# game, and together() runs one step of each player side by side, so their waits overlap. Each
# player's gold and food are their own, so the checks do not mix.

def wait(pending, timeout=15):
    """Waits for a Pending (see Session.*_later), yielding between looks; fails the test when it
    does not come."""
    end = time.time() + timeout
    while not pending.ready():
        if time.time() > end:
            raise TestFailed(f'timed out after {timeout}s waiting for {pending.what}')
        yield
    return pending.result()


def poll(ask, timeout):
    """Asks again and again (ask() is a generator, such as one of wait()) until the answer is
    something, yielding between tries; that, or None when time runs out."""
    end = time.time() + timeout
    while True:
        value = yield from ask()
        if value or time.time() > end:
            return value or None
        yield


def together(*steps):
    """Runs generators side by side until all are done; returns their values, in order. When one
    fails, the others are closed (so their clean-up runs) before the failure goes on."""
    values = [None] * len(steps)
    running = dict(enumerate(steps))
    try:
        while running:
            for index, step in list(running.items()):
                try:
                    next(step)
                except StopIteration as done:
                    values[index] = done.value
                    del running[index]
            if running:
                time.sleep(0.2)
    finally:
        for step in running.values():
            step.close()
    return values


def build_step(game, race, player, chain, step, spot, tower, findings, state):
    """Builds one form of a chain for a player: its base on the spot (with a builder made for
    it), or an upgrade of the tower standing there. Waits until it is finished, and checks the
    gold it cost and the food it gave. A generator (see together()); its value is the tower, or
    None when the form never appeared.

    state['beat', player] is the player's last heartbeat after a build, which is the next build's
    'before' as long as nothing else changed their gold in between (the round clears it)."""
    by_id = towers_by_id(race)
    unit_type = chain[step]
    data = by_id[unit_type]
    fed = state.setdefault('food raised', set())
    if data['food'] and player not in fed:
        # Farms come first (and are checked before this); the towers that eat food get room for all
        game.cmd(player, '.foodcap 300')
        fed.add(player)
        state.pop(('beat', player), None)
    before = state.get(('beat', player)) or (yield from wait(game.beat_after()))
    builder = None
    if step == 0:
        builder = (yield from wait(game.create_later(player, race['builder'], spot[0], spot[1] - 256)))[0]['id']
        game.build(player, builder, unit_type, *spot)
    else:
        game.upgrade(player, tower, unit_type)
    try:
        def standing():
            units = yield from wait(game.units_later(player))
            return next((unit for unit in units if unit['type'] == unit_type
                         and abs(unit['x'] - spot[0]) <= 64 and abs(unit['y'] - spot[1]) <= 64), None)

        found = yield from poll(standing, 25)
        if found is None:
            # Its gold may be spent all the same: the next build needs a heartbeat of its own
            state.pop(('beat', player), None)
            findings.append(f'{named(data)} was not {"built" if step == 0 else "upgraded to"} at {spot}')
            return None
        tower = found['id']

        def finished():
            now = yield from wait(game.inspect_later(tower))
            return now.get('life', 0) >= now.get('max_life', 1)

        if not (yield from poll(finished, 60)):
            state.pop(('beat', player), None)
            findings.append(f'{named(data)} at {spot} was never finished')
            return None
    finally:
        if builder is not None:
            game.remove(player, builder)
    after = yield from wait(game.beat_after())
    state['beat', player] = after
    spent = before.gold(player) - after.gold(player)
    findings.check(spent == data['gold'], f'{named(data)} cost {spent} gold, the map says {data["gold"]}')
    # An upgrade replaces the food of the form it came from
    food_expected = data['foodMade'] - (by_id[chain[step - 1]]['foodMade'] if step else 0)
    if food_expected:
        gained = after.player(player, 'fc') - before.player(player, 'fc')
        findings.check(gained == food_expected, f'{named(data)} gave {gained} food, the map says {food_expected}')
    return tower


def give_gold(game, players):
    """Enough gold for everything, and a wait until the game has it: a command lands a sync round
    trip later, and the first build's cost is counted from after it."""
    for player in players:
        game.cmd(player, f'.gold {GOLD}')
    game.wait_for('the gold to be set', lambda beat: all(beat.gold(player) == GOLD for player in players))


def build_chain(game, race, chain, spot, findings, player=RED):
    """Builds a chain's base tower on the spot and upgrades it along the chain; returns the
    finished tower, or None when a step failed."""
    def steps():
        tower, state = None, {}
        for step in range(len(chain)):
            tower = yield from build_step(game, race, player, chain, step, spot, tower, findings, state)
            if tower is None:
                return None
        return tower

    return together(steps())[0]


# Auras -------------------------------------------------------------------------------------

def reaches_enemies(aura):
    return bool({'enemy', 'enemies'} & set(aura['targets']))


def reaches_itself(aura):
    return not reaches_enemies(aura) and 'self' in aura['targets']


def reaches_friends(aura):
    return not reaches_enemies(aura) and aura['area'] > 0 and bool({'friend', 'allies', 'player'} & set(aura['targets']))


def neighbour_type(race):
    """The tower put next to a friendly aura: the race's cheapest base tower that attacks, with
    no aura of its own and not homesick."""
    upgraded = {u for t in race['towers'] for u in t['upgradesTo']}
    candidates = [t for t in race['towers'] if t['id'] not in upgraded and t['attacks'] and not t['auras']
                  and HOMESICK not in t['abilities']]
    return min(candidates, key=lambda t: t['gold'])['id'] if candidates else None


# Lanes and rounds ----------------------------------------------------------------------------

class Slot:
    """A place for one of a player's towers (see maul.lane_slots), and the chain it works through."""

    def __init__(self, player, lane, x, y, side):
        self.player, self.lane, self.spot, self.side = player, lane, (x, y), side
        self.clear()

    def clear(self):
        self.chain, self.step, self.tower, self.done = None, -1, None, False

    @property
    def form(self):
        return self.chain[self.step]

    def target_spot(self, distance):
        return self.spot[0], self.spot[1] + self.side * distance

    def arc_spots(self, distance, count):
        """`count` spots `distance` from the tower, in a half circle on its targets' side."""
        angles = [math.pi * (i + 1) / (count + 1) for i in range(count)]
        return [(round(self.spot[0] + distance * math.cos(a)), round(self.spot[1] + self.side * distance * math.sin(a)))
                for a in angles]


def homesick(race, chain):
    by_id = towers_by_id(race)
    return any(HOMESICK in by_id[t]['abilities'] for t in chain)


def has_auras(race, chain):
    by_id = towers_by_id(race)
    return any(by_id[t]['auras'] for t in chain)


def fits(race, chain, slot):
    """Homesick towers stand only in their owner's lane, towers with auras only in a lane of their
    own."""
    if slot.lane == slot.player:
        return not has_auras(race, chain)
    return not homesick(race, chain)


def advance(game, race, slot, looked, findings, state):
    """Builds the slot's chain on to its next form worth a look (one that attacks or has an aura
    and was not looked at yet), or to its end. A generator (see together()); its value is True
    when the tower stands at a form to look at."""
    by_id = towers_by_id(race)
    while slot.step < len(slot.chain) - 1:
        tower = yield from build_step(game, race, slot.player, slot.chain, slot.step + 1, slot.spot, slot.tower,
                                      findings, state)
        if tower is None:
            return False
        slot.step, slot.tower = slot.step + 1, tower
        data = by_id[slot.form]
        if data['id'] not in looked and (data['attacks'] or data['auras']):
            looked.add(data['id'])
            return True
    return False


def check_race(game, race, findings, players=(RED,)):
    """Builds every tower of a race the players already picked, round by round, and checks them.
    With two players both build, each in their own slots, at the same time."""
    give_gold(game, players)
    for player in players:
        game.watch_casts(player)
    game.watch(CREEPS)
    by_id = towers_by_id(race)
    # Farms first (the towers that eat food come after them), then the homesick ones, which
    # queue for their owner's lane
    pending = sorted(maul.upgrade_chains(race),
                     key=lambda chain: (not any(by_id[t]['foodMade'] for t in chain), not homesick(race, chain)))
    slots = [Slot(*slot) for slot in maul.lane_slots(players)]
    looked, state = set(), {}

    def build_for(player, looking):
        for slot in slots:
            if slot.player != player:
                continue
            if slot.chain is None:
                chain = next((c for c in pending if fits(race, c, slot)), None)
                if chain is None:
                    continue
                pending.remove(chain)
                slot.chain = chain
            if (yield from advance(game, race, slot, looked, findings, state)):
                looking.append(slot)
            else:
                slot.done = True

    while True:
        looking = []
        together(*(build_for(player, looking) for player in players))
        if not any(slot.tower is not None for slot in slots):
            break
        classes = check_classes(game, findings, players)
        if looking:
            look_at(game, race, looking, classes, findings, state)
        # Selling pays gold back: the next build needs a heartbeat of its own
        for player in players:
            state.pop(('beat', player), None)
        finished = [slot for slot in slots if slot.chain is not None
                    and (slot.done or slot.step == len(slot.chain) - 1)]
        for player in players:
            maul.sell(game, player, [s.tower for s in finished if s.player == player and s.tower is not None])
        for slot in finished:
            slot.clear()
    for chain in pending:
        findings.append(f'no lane could take {" > ".join(chain)}')


def by_form(events, tower, form):
    """The hits or casts of a tower while it was that form (a tower keeps its id as it upgrades)."""
    return [event for event in events if event['src'] == tower and event['srctype'] == form]


def look_at(game, race, looking, classes, findings, state):
    """A round's look at the towers that just got to a new form: a target next to each (and a
    tower of the race next to a friendly aura), a while to attack, then what they did."""
    by_id = towers_by_id(race)
    neighbour = neighbour_type(race)
    # Casts are judged from here: a tower casts on its first attack, which can come as soon as its
    # target stands (while the other targets and neighbours are still being made), and a spell
    # with a cooldown does not come again in the round. Earlier rounds' casts are other towers'.
    casts_before = len(game.casts())
    targets, neighbours, spread = {}, {}, {}
    for slot in looking:
        data = by_id[slot.form]
        attack = data['attacks']
        enemy_areas = [aura['area'] for aura in data['auras'] if reaches_enemies(aura)]
        if attack or enemy_areas:
            distance = min([TARGET_DISTANCE, *(area * 3 // 4 for area in enemy_areas)])
            air_only = attack and 'air' in attack['targets'] and 'ground' not in attack['targets']
            kind = maul.AIR_TARGET if air_only else maul.GROUND_TARGET
            if attack and classes.get(slot.tower, {}).get('class') in RANDOM_TARGET:
                spread[slot] = [game.create(CREEPS, kind, *spot, life=maul.TARGET_LIFE, rooted=True)[0]['id']
                                for spot in slot.arc_spots(distance, RANDOM_TARGETS)]
                targets[slot] = spread[slot][0]
            else:
                targets[slot] = game.create(CREEPS, kind, *slot.target_spot(distance), life=maul.TARGET_LIFE,
                                            rooted=True)[0]['id']
        if neighbour and any(reaches_friends(aura) for aura in data['auras']):
            spot = (slot.spot[0] + NEIGHBOUR_DISTANCE, slot.spot[1])
            tower = together(build_step(game, race, slot.player, [neighbour], 0, spot, None, findings, state))[0]
            if tower is not None:
                neighbours[slot] = tower

    attackers = [slot for slot in looking if by_id[slot.form]['attacks']]
    by_key = {(slot.tower, slot.form): slot for slot in looking}
    started = time.time()
    while time.time() - started < ROUND_SECONDS:
        counts, spread_counts = {}, {}
        for hit in game.hits():
            key = (hit['src'], hit['srctype'])
            counts[key] = counts.get(key, 0) + 1
            if hit['dst'] in spread.get(by_key.get(key), ()):
                spread_counts[key] = spread_counts.get(key, 0) + 1
        # A tower that attacks at random also shoots the other towers' targets in its range: it
        # attacks until it hit its own ones often enough to see the spread
        if time.time() - started >= ROUND_AT_LEAST \
                and all(counts.get((s.tower, s.form), 0) >= WANTED_HITS for s in attackers) \
                and all(spread_counts.get((s.tower, s.form), 0) >= 3 * len(around) for s, around in spread.items()):
            break
        time.sleep(1)

    hits, casts = game.hits(), game.casts()
    for slot in attackers:
        cls = classes.get(slot.tower, {}).get('class', 'Tower')
        check_attack(game, by_id[slot.form], slot.tower, targets.get(slot), cls, hits, casts, findings)
    for slot in looking:
        check_auras(game, by_id, slot, targets, neighbours, looking, findings)
    check_behaviours(game, race, attackers, classes, casts[casts_before:], findings)
    for slot, around in spread.items():
        check_spread(by_id[slot.form], slot.tower, around, hits, findings)
    for target in {*targets.values(), *(t for around in spread.values() for t in around)}:
        game.remove(CREEPS, target)
    for slot, tower in neighbours.items():
        maul.sell(game, slot.player, [tower])


def check_attack(game, data, tower, target, cls, hits, casts, findings):
    """The tower hit its target with the attack type it advertises, for the damage it says."""
    attack = data['attacks']
    own = by_form(hits, tower, data['id'])
    if not own:
        now, victim = game.inspect(tower), game.inspect(target) if target else {}
        where = 'the target is gone'
        if 'x' in now and 'x' in victim:
            distance = ((now['x'] - victim['x']) ** 2 + (now['y'] - victim['y']) ** 2) ** 0.5
            where = f'target {distance:.0f} away at {victim["x"]},{victim["y"]} life {victim["life"]}'
        findings.append(f'{named(data)} never hit its target in {ROUND_SECONDS}s ({attack["type"]} {attack["weapon"]}, '
                        f'range {attack["range"]}, cooldown {attack["cooldown"]:.2f}): tower order={now.get("order")}, '
                        f'{where}')
        return
    attacks = [hit for hit in own if hit['attack'] == 'true']
    types = {hit['atk'] for hit in attacks}
    if attack['type'] not in types:
        spells = len(own) - len(attacks)
        # Damage that arrives as a spell from a tower that cast nothing comes from its attack:
        # an attack modifier such as Burning Oil hands the damage to burning ground. A tower
        # whose spell damage comes with casts of its own is casting instead of attacking
        # (the High Priest's monsoon cancelled every attack).
        casting = bool(by_form(casts, tower, data['id']))
        if types or casting or not spells:
            now = game.inspect(tower)
            findings.append(f'{named(data)} hit with {sorted(types) or "spells only"} ({spells} spell hits), the map '
                            f'says {attack["type"]} attacks: tower order={now.get("order")} cd={now.get("cd")} '
                            f'range={now.get("range")}')
        return
    raws = [hit['raw'] for hit in attacks if hit['atk'] == attack['type'] and 'raw' in hit]
    if not raws or attack['damageMax'] < attack['damageMin']:
        # A range the object data cannot mean (Ogre Magi's 1-0): its damage comes from elsewhere
        return
    says = f'its attack says {attack["damageMin"]}-{attack["damageMax"]}'
    findings.check(min(raws) >= attack['damageMin'] - 1,
                   f'{named(data)} hit for as little as {min(raws)} before armor, {says}')
    if not (set(data['abilityBases']) & RAISES_DAMAGE or cls in GROWS):
        findings.check(max(raws) <= attack['damageMax'] + 1,
                       f'{named(data)} hit for as much as {max(raws)} before armor, {says}')


def check_spread(data, tower, around, hits, findings):
    """A tower that attacks a random enemy spread its attacks over the targets around it."""
    on = {}
    for hit in by_form(hits, tower, data['id']):
        if hit['attack'] == 'true' and hit['dst'] in around:
            on[hit['dst']] = on.get(hit['dst'], 0) + 1
    attacks = sum(on.values())
    # 24 random picks of 8 miss 3 or more of them about once in 1400 rounds
    if attacks < 3 * len(around):
        findings.append(f'{named(data)} hit its {len(around)} targets only {attacks} times, too few to see a spread')
        return
    findings.check(len(on) > len(around) - 3 and max(on.values()) <= attacks // 2,
                   f'{named(data)} does not pick its targets at random: {attacks} attacks on '
                   f'{len(on)} of {len(around)} targets, at most {max(on.values())} on one')


def check_auras(game, by_id, slot, targets, neighbours, looking, findings):
    """Each aura of the tower reaches what it says - itself, the tower next to it, or its target -
    and not a tower or target in another lane."""
    data = by_id[slot.form]
    for aura in data['auras']:
        buff = aura['buff']
        name = f'{named(data)}: aura {aura["id"]} ({aura["base"]}, buff {buff}, area {aura["area"]})'
        # Towers in other lanes, none with an aura of the same buff
        elsewhere = [other for other in looking if other.lane != slot.lane
                     and all(a['buff'] != buff for a in by_id[other.form]['auras'])]
        if reaches_enemies(aura):
            target = targets.get(slot)
            if target is not None:
                findings.check(game.inspect(target, buff).get(buff, 0) > 0, f'{name} does not reach the target next to it')
            far = next((targets[o] for o in elsewhere if o in targets), None)
            if far is not None:
                findings.check(game.inspect(far, buff).get(buff, 0) == 0, f'{name} reaches a target in another lane')
            continue
        if reaches_itself(aura):
            findings.check(game.inspect(slot.tower, buff).get(buff, 0) > 0, f'{name} does not reach the tower itself')
        if reaches_friends(aura):
            if slot in neighbours:
                findings.check(game.inspect(neighbours[slot], buff).get(buff, 0) > 0,
                               f'{name} does not reach a tower {NEIGHBOUR_DISTANCE} away')
            far = next((o.tower for o in elsewhere), None)
            if far is not None:
                findings.check(game.inspect(far, buff).get(buff, 0) == 0, f'{name} reaches a tower in another lane')


def check_behaviours(game, race, slots, classes, casts, findings):
    """Each tower's behaviour class did what it does while it attacked, in the form it has now,
    judged by the casts of this round."""
    by_id = towers_by_id(race)
    hits = game.hits()
    units = [unit for player in {slot.player for slot in slots} for unit in game.units(player)]
    for slot in slots:
        cls = classes.get(slot.tower, {}).get('class', 'Tower')
        name = f'{named(by_id[slot.form])} ({cls})'
        attacks = len([hit for hit in by_form(hits, slot.tower, slot.form) if hit['attack'] == 'true'])
        if cls in CASTS_ITSELF:
            findings.check(bool(by_form(casts, slot.tower, slot.form)), f'{name} never cast its spell in {attacks} attacks')
        if cls in DUMMY_CASTS:
            ability = DUMMY_CASTS[cls]
            findings.check(any(c['ability'] == ability for c in casts), f'{name}: nothing cast {ability}')
        if cls in DUMMY_CASTS_BY_CHANCE:
            ability, chance = DUMMY_CASTS_BY_CHANCE[cls]
            # Judged only when missing it would be a 1-in-100 fluke
            if (1 - chance) ** attacks < 0.01:
                findings.check(any(c['ability'] == ability for c in casts),
                               f'{name}: nothing cast {ability} in {attacks} attacks at {chance:.0%} each')
            else:
                game.log(f'{name}: {attacks} attacks is too few to judge its {chance:.0%} {ability}')
        if cls in MAKES_UNITS:
            findings.check(any(u['type'] == MAKES_UNITS[cls] for u in units),
                           f'{name} made no {MAKES_UNITS[cls]}')


def check_classes(game, findings, players=(RED,)):
    """The towers' behaviour classes, checked against the must/must-not lists; returns them."""
    classes = {}
    for player in players:
        classes.update(maul.towers(game, player))
    for tower in classes.values():
        need = MUST_HAVE_CLASS.get(tower['type'])
        findings.check(need is None or tower['class'] == need,
                       f'{tower["type"]} has class {tower["class"]}, it needs {need}')
        banned = MUST_NOT_HAVE_CLASS.get(tower['type'])
        findings.check(banned is None or tower['class'] != banned,
                       f'{tower["type"]} has class {tower["class"]}, which belongs to another race')
    return classes


def make_race_test(race):
    def test(game):
        findings = Findings()
        maul.start(game)
        for player in (RED, BLUE):
            builder = maul.pick(game, player, race['item'])
            findings.check(builder['type'] == race['builder'],
                           f"p{player}'s pick gave a {builder['type']}, the race's builder is {race['builder']}")
            findings.check(maul.fresh_beat(game).lumber(player) == 0, f"p{player}'s pick did not take the starting lumber")
        try:
            check_race(game, race, findings, players=(RED, BLUE))
        except TestFailed as error:
            # What was found before the failure still counts
            findings.append(f'stopped: {error}')
        findings.raise_if_any(race['name'])

    test.__doc__ = (f'{race["name"]} ({race["item"]}): its builder, all {len(race["towers"])} towers at their '
                    'cost, every form that attacks hitting with its attack type and damage, and every aura.')
    return test


try:
    _beginner = maul.races('Beginner')
except (OSError, RuntimeError) as _error:
    # Without the map repo (or node) there are no race tests to make; say so as one failing test
    # rather than keeping the other tests in the suite from loading
    _reason = f'the race data could not be read from {maul.MAUL}: {_error}'

    def test_race_data(game):
        """The map's race data can be read (node scripts/race-data.js in the map repo)."""
        raise TestFailed(_reason)

    _beginner = []
for _race in _beginner:
    globals()['test_race_' + re.sub(r'\W+', '_', _race['name'].lower()).strip('_')] = make_race_test(_race)


def test_secondary_race_needs_a_first_race(game):
    """Shrine of Buffs cannot be the first pick: it is a secondary race."""
    maul.start(game)
    shrine = maul.races('Secondary')[0]
    line = maul.refusal(game, RED, shrine['item'])
    if 'no race yet for a secondary' not in line:
        raise TestFailed(f'refused for the wrong reason: {line}')


def test_secondary_race_after_the_first(game):
    """After a first race, Shrine of Buffs is picked for a pick's lumber, and its towers work."""
    findings = Findings()
    maul.start(game)
    shrine = maul.races('Secondary')[0]
    maul.pick(game, RED, maul.races('Beginner')[0]['item'])
    line = maul.refusal(game, RED, shrine['item'])
    findings.check('cannot afford' in line, f'with no lumber left the secondary was refused for: {line}')
    game.cmd(RED, '.lumber 1')
    maul.fresh_beat(game)
    builder = maul.pick(game, RED, shrine['item'])
    findings.check(builder['type'] == shrine['builder'],
                   f'the secondary pick gave a {builder["type"]}, not {shrine["builder"]}')
    findings.check(maul.fresh_beat(game).lumber(RED) == 0, 'the secondary pick did not take the lumber')
    try:
        check_race(game, shrine, findings)
    except TestFailed as error:
        findings.append(f'stopped: {error}')
    findings.raise_if_any('Shrine of Buffs')


# Loot Boxer -----------------------------------------------------------------------------------------

LOOT_BOXER = 'I02D'
LOOT_BOXER_BUILDER = 'u043'
# Its boxes, tier 1 to 9. A box of tier 1-3 turns into a random tower of its tier when it is
# built; one of tier 4 or more stands until it is opened (A0EX, a Channel with the order
# 'channel'), which costs all the mana the box holds (1 for tier 4, up to 6 for tier 9): it
# regains 0.01 a second, or more from the loot items made for it. The test fills it instead.
# Either way the Loot Boxer gets an item for a roll of 1-100.
LOOT_BOXES = ['u044', 'u045', 'u047', 'u046', 'u048', 'u049', 'u04A', 'u04B', 'u04C']
LOOT_BOX_OPENED = 4
# How many of each tier the test builds: 38 rolls in all (red's lane holds 39), most of them in
# the tiers whose loot is worth having
LOOT_BOX_COUNTS = {1: 6, 2: 3, 3: 3, 4: 3, 5: 3, 6: 5, 7: 5, 8: 5, 9: 5}
ROCKS, PREMIUM, LOOTBAG, STICK, COIN, KOINZ = 'I02F', 'I029', 'I02B', 'I028', 'I02A', 'I02C'
# Tiers 7-9, which give no Rocks: (item, up to roll) in order, the last one to 100
HIGH_TIER_LOOT = {7: ((STICK, 70), (LOOTBAG, 85), (COIN, 95), (KOINZ, 100)),
                  8: ((STICK, 65), (COIN, 80), (LOOTBAG, 92), (KOINZ, 100)),
                  9: ((STICK, 60), (COIN, 80), (LOOTBAG, 90), (KOINZ, 100))}


def loot_for(tier, roll):
    """The item a box of that tier (1-9) gives for a roll of 1-100, as the map's table has it
    (LootBoxerHandler.Loot): Rocks 95/90/85% for tiers 1-3, 80/70/60% for tiers 4-6, none for 7-9."""
    if tier <= 3:
        return ROCKS if roll <= 100 - 5 * tier else PREMIUM if roll <= 100 - 2 * tier else LOOTBAG
    if tier >= 7:
        return next(item for item, up_to in HIGH_TIER_LOOT[tier] if roll <= up_to)
    if roll <= 80 - 10 * (tier - 4):
        return ROCKS
    second, third, last = (PREMIUM, LOOTBAG, STICK) if tier <= 5 else (LOOTBAG, STICK, COIN)
    return second if roll <= 90 - 5 * (tier - 4) else third if roll <= 100 - 2 * (tier - 3) else last


def rock_charges(tier):
    """The charges Rocks from a box of tier 4-6 come with (the map rolls them between these)."""
    return 1, tier - 1
# Towers built in numbers (the Loot Boxer's boxes, the Elementalists' runes) stand this far apart,
# inside red's lane: 40 spots
LANE_SPACING = 256


def red_lane_spots():
    """Spots in red's lane, LANE_SPACING apart."""
    min_x, min_y, max_x, max_y = maul.lane_areas()[RED]
    return [(x, y) for y in range(int(min_y) + LANE_SPACING, int(max_y) - LANE_SPACING + 1, LANE_SPACING)
            for x in range(int(min_x) + LANE_SPACING, int(max_x) - LANE_SPACING + 1, LANE_SPACING)]


def build_in_red_lane(game, builder_type, plan, findings):
    """Orders a building for each (label, unit type) in the plan, each by a builder of its own at
    a spot of its own; a spot the order is refused at (one in red's lane refuses everything) is
    given up for the next free one. [(label, spot)] ordered."""
    spots = red_lane_spots()
    placed = []
    for label, unit_type in plan:
        while spots:
            spot = spots.pop(0)
            before = len(game.events('order'))
            builder = game.create(RED, builder_type, *spot)[0]['id']
            game.build(RED, builder, unit_type, *spot)
            lines = []

            def answered():
                lines[:] = [line for line in game.events('order')[before:] if ' build ' in line]
                return lines
            game.wait(f'the {label} order', answered, 15)
            if lines[0].endswith('issued'):
                placed.append((label, spot))
                break
            game.log(f'the {label} was refused at {spot}; trying the next spot')
            game.remove(RED, builder)
        else:
            findings.append(f'the {label} was refused at every spot in the lane')
    return placed


def test_loot_boxer(game):
    """Loot Boxer: every box turns into a tower of the pool (tiers 4-9 once opened), and gives
    the Loot Boxer an item of its tier's table for a roll of 1-100 (the map logs each roll)."""
    findings = Findings()
    maul.start(game)
    # Random-only: picked directly only because the game is in Debug mode (its Dev tab)
    boxer = maul.pick(game, RED, LOOT_BOXER)
    findings.check(boxer['type'] == LOOT_BOXER_BUILDER, f"the pick gave a {boxer['type']}, not {LOOT_BOXER_BUILDER}")
    game.cmd(RED, f'.gold {GOLD}')
    game.wait_for('the gold to be set', lambda beat: beat.gold(RED) == GOLD)

    def at(units, spot):
        return next((unit for unit in units if abs(unit['x'] - spot[0]) <= 64 and abs(unit['y'] - spot[1]) <= 64
                     and unit['type'] != LOOT_BOXER_BUILDER), None)

    plan = [tier for tier, count in LOOT_BOX_COUNTS.items() for _ in range(count)]
    before = len(game.events('lootbox'))
    placed = []
    try:
        placed = [(int(label.split()[1]), spot) for label, spot in
                  build_in_red_lane(game, LOOT_BOXER_BUILDER, [(f'tier {tier} box', LOOT_BOXES[tier - 1])
                                                              for tier in plan], findings)]
        # Tiers 1-3 turn into towers when built; the higher boxes are opened once they stand
        closed = [(tier, spot) for tier, spot in placed if tier >= LOOT_BOX_OPENED]
        boxes, opened = {}, set()
        orders_before = len(game.events('order'))

        def open_the_finished():
            # A box is filled with mana when first seen, and ordered open from the next look on:
            # in a test run, opening in the same tick as the mana was set was refused, and a tick
            # later it was not. A refused order is given again on the next look
            issued = {line.split()[-3] for line in game.events('order')[orders_before:] if line.endswith(' channel issued')}
            units = game.units(RED)
            for tier, spot in closed:
                if spot in opened:
                    continue
                if spot in boxes and str(boxes[spot]) in issued:
                    opened.add(spot)
                elif spot in boxes:
                    game.order(RED, boxes[spot], 'channel')
                else:
                    box = at(units, spot)
                    if box is not None and box['type'] == LOOT_BOXES[tier - 1]:
                        boxes[spot] = box['id']
                        game.cmd(RED, f'.mana {box["id"]} {tier - LOOT_BOX_OPENED + 1}')
            return len(opened) == len(closed)
        game.wait(f'{len(closed)} box(es) of tier {LOOT_BOX_OPENED}-9 to stand, and be opened', open_the_finished, 90)
        game.wait(f'{len(placed)} box(es) to give loot', lambda: len(game.events('lootbox')[before:]) >= len(placed), 30)
    except TestFailed as error:
        findings.append(f'stopped: {error}')

    loot = [trace.parse_fields(line) for line in game.events('lootbox')[before:]]
    by_tier = {}
    for entry in loot:
        by_tier.setdefault(entry.get('tier'), []).append(entry)
    for tier in LOOT_BOX_COUNTS:
        count = sum(1 for placed_tier, _ in placed if placed_tier == tier)
        findings.check(len(by_tier.get(tier, [])) == count,
                       f'{count} box(es) of tier {tier} gave {len(by_tier.get(tier, []))} item(s)')
    strays = sorted({str(tier) for tier in by_tier if tier not in LOOT_BOX_COUNTS})
    findings.check(not strays, f'loot was given for tier(s) {", ".join(strays)}, which no box has')
    for entry in loot:
        tier, roll, item, charges = entry.get('tier'), entry.get('roll'), entry.get('item'), entry.get('charges', 0)
        if not (isinstance(roll, int) and 1 <= roll <= 100 and tier in LOOT_BOX_COUNTS):
            findings.append(f'a tier {tier} box rolled {roll}, not 1-100')
            continue
        findings.check(item == loot_for(tier, roll), f'a tier {tier} box gave {item} for a roll of {roll}, '
                                                    f'the table says {loot_for(tier, roll)}')
        if item == ROCKS and tier >= LOOT_BOX_OPENED:
            low, high = rock_charges(tier)
            findings.check(low <= charges <= high, f'a tier {tier} box gave Rocks with {charges} charges, not {low}-{high}')
    items = {}
    for entry in loot:
        items.setdefault(entry.get('tier'), []).append(entry.get('item'))
    game.log('Loot Boxer loot: ' + '; '.join(f'tier {tier} ' + ' '.join(sorted(got)) for tier, got in sorted(items.items())))
    rolls = sorted(entry['roll'] for entry in loot if isinstance(entry.get('roll'), int))
    if rolls:
        game.log(f'Loot Boxer rolls ({len(rolls)}): {" ".join(map(str, rolls))}')
        # 38 rolls of 1-100 all on one side of 50 happen about once in 100 billion
        findings.check(rolls[0] <= 50 < rolls[-1], f'the rolls ran from {rolls[0]} to {rolls[-1]} only')

    # Every box is gone, and a tower of the pool stands in its place
    try:
        units = game.units(RED)
        for tier, spot in placed:
            unit = at(units, spot)
            findings.check(unit is not None and unit['type'] not in LOOT_BOXES,
                           f'the tier {tier} box at {spot} is '
                           f'{"gone, with nothing in its place" if unit is None else "still a " + unit["type"]}')
    except TestFailed as error:
        findings.append(f'the towers were not looked at: {error}')
    findings.raise_if_any('Loot Boxer')


# Elementalists -------------------------------------------------------------------------------------

ELEMENTALISTS = 'I024'
ELEMENTALIST_BUILDER = 'e00W'
UNCHARGED_RUNE = 'n00A'
# The charges an Uncharged Rune may get (GlobalSettings ELEMENTALIST_ABILITIES): each is a Channel
# that turns the rune into its element's rune (UnchargedRuneMorph)
ELEMENTS = {'A0BO': ('Water', 'absorb', 'n01R'), 'A0BS': ('Fire', 'acidbomb', 'n01S'),
            'A0C0': ('Nature', 'animatedead', 'n022'), 'A0C1': ('Air', 'antimagicshell', 'n023'),
            'A0C2': ('Death', 'avatar', 'n024'), 'A0C3': ('Life', 'banish', 'n025')}
# Each rune gets 2 of the 6 at random, so one has a given element 1 time in 3: 20 runes without
# it happen 3 times in 10,000
RUNES = 20


def test_elementalist_runes(game):
    """Elementalists: each Uncharged Rune gets two different charges, every element among them
    (Life included), and a charge turns the rune into its element's rune."""
    findings = Findings()
    maul.start(game)
    builder = maul.pick(game, RED, ELEMENTALISTS)
    findings.check(builder['type'] == ELEMENTALIST_BUILDER, f"the pick gave a {builder['type']}, not {ELEMENTALIST_BUILDER}")
    game.cmd(RED, f'.gold {GOLD}')
    game.wait_for('the gold to be set', lambda beat: beat.gold(RED) == GOLD)

    placed = build_in_red_lane(game, ELEMENTALIST_BUILDER, [(f'rune {n + 1}', UNCHARGED_RUNE) for n in range(RUNES)],
                               findings)
    runes = {}

    def standing():
        units = game.units(RED)
        for label, spot in placed:
            rune = next((u for u in units if u['type'] == UNCHARGED_RUNE and abs(u['x'] - spot[0]) <= 64
                         and abs(u['y'] - spot[1]) <= 64), None)
            if rune is not None:
                runes[label] = rune['id']
        return len(runes) == len(placed)
    try:
        game.wait(f'{len(placed)} Uncharged Runes to stand', standing, 60)
    except TestFailed as error:
        findings.append(f'stopped: {error}')

    charges = {}
    for label, rune in runes.items():
        levels = game.inspect(rune, *ELEMENTS)
        charges[label] = [ability for ability in ELEMENTS if levels.get(ability, 0) > 0]
        findings.check(len(charges[label]) == 2, f'{label} has {len(charges[label])} charges: {charges[label]}')
    counts = {ELEMENTS[ability][0]: sum(ability in got for got in charges.values()) for ability in ELEMENTS}
    game.log(f'charges over {len(charges)} runes: ' + ', '.join(f'{name} {count}' for name, count in counts.items()))
    for name, count in counts.items():
        findings.check(count > 0, f'no rune of {len(charges)} got the {name} charge')

    # One charge of each element that came up is used, and the rune must become that element's
    # (a new unit in its place: Tower.Upgrade replaces the rune)
    spots = dict(placed)
    used = set()
    for label, got in charges.items():
        ability = next((a for a in got if a not in used), None)
        if ability is None:
            continue
        used.add(ability)
        name, order, element = ELEMENTS[ability]
        spot = spots[label]
        game.order(RED, runes[label], order)
        try:
            game.wait(f'{label} to turn into the {name} rune ({element})',
                      lambda: any(u['type'] == element and abs(u['x'] - spot[0]) <= 64 and abs(u['y'] - spot[1]) <= 64
                                  for u in game.units(RED)), 15)
        except TestFailed as error:
            findings.append(f'{label}: {error}')
    findings.raise_if_any('Elementalists')


# Aviaries' Wyvern ------------------------------------------------------------------------------------

AVIARIES, AVIARIES_BUILDER, WYVERN = 'I003', 'eC10', 'oC60'
# The four creep players: the Wyvern's lightning must reach the units of each
CREEP_PLAYERS = (13, 14, 15, 16)
# Its lightning takes 15% of the life of every creep within 128 of it, on each of its attacks (every
# 2.5 s); its own attack is 1 damage or so, nothing next to that on a target of 5 million
WYVERN_REACH = 96


def wyvern_targets(game, spot):
    """A 5-million-life target of each creep player around the tower, within its lightning."""
    offsets = ((WYVERN_REACH, 0), (-WYVERN_REACH, 0), (0, WYVERN_REACH), (0, -WYVERN_REACH))
    return {player: game.create(player, maul.GROUND_TARGET, spot[0] + dx, spot[1] + dy, life=maul.TARGET_LIFE,
                                rooted=True)[0]['id'] for player, (dx, dy) in zip(CREEP_PLAYERS, offsets)}


def test_wyvern_lightning(game):
    """Aviaries' Wyvern: each attack strikes every creep next to it for 15% of its life, whichever
    creep player owns it (Navy's were left out once), and spares the boss waves (35-37; once it
    spared 34-35 instead)."""
    findings = Findings()
    maul.start(game)
    # Aviaries is switched off in the race picker; Debug mode lets it be picked from the Dev tab
    maul.pick(game, RED, AVIARIES)
    game.cmd(RED, f'.gold {GOLD}')
    game.wait_for('the gold to be set', lambda beat: beat.gold(RED) == GOLD)
    placed = build_in_red_lane(game, AVIARIES_BUILDER, [('Wyvern', WYVERN)], findings)
    findings.raise_if_any('building the Wyvern')
    spot = placed[0][1]

    def finished():
        tower = next((u for u in game.units(RED) if u['type'] == WYVERN and abs(u['x'] - spot[0]) <= 64
                      and abs(u['y'] - spot[1]) <= 64), None)
        return tower is not None and tower['life'] >= game.inspect(tower['id']).get('max_life', 1)
    game.wait('the Wyvern to stand', finished, 60)

    def lost(targets):
        """The share of its life each target lost, by creep player."""
        return {player: 1 - game.inspect(target).get('life', 0) / maul.TARGET_LIFE for player, target in targets.items()}

    # A normal wave: every creep player's target is struck
    targets = wyvern_targets(game, spot)
    time.sleep(12)
    shares = lost(targets)
    game.log('wave 1: ' + ', '.join(f'p{player} lost {share:.0%}' for player, share in shares.items()))
    for player, share in shares.items():
        findings.check(share >= 0.15, f'wave 1: the target of creep player {player} lost only {share:.1%}')
    for player, target in targets.items():
        game.remove(player, target)

    # A boss wave: none is struck, only the tower's own attack lands
    game.cmd(RED, '-wave 35')
    targets = wyvern_targets(game, spot)
    time.sleep(12)
    shares = lost(targets)
    game.log('wave 35: ' + ', '.join(f'p{player} lost {share:.0%}' for player, share in shares.items()))
    for player, share in shares.items():
        findings.check(share < 0.05, f'wave 35 (a boss): the target of creep player {player} lost {share:.1%}')
    findings.raise_if_any('Wyvern')


# Behaviours that need kills, time or a running wave, each on a game of its own ------------------

def race_named(name):
    return next(race for race in maul.races('Beginner') if race['name'] == name)


def one_tower(game, race_name, tower_id, *upgrades):
    """Picks the race for red and builds one tower on red's first slot, upgraded along the given
    types; returns (race, tower ref)."""
    findings = Findings()
    race = race_named(race_name)
    maul.start(game)
    maul.pick(game, RED, race['item'])
    give_gold(game, (RED,))
    tower = build_chain(game, race, [tower_id, *upgrades], maul.single_spot()[0], findings)
    findings.raise_if_any(f'building {tower_id}')
    return race, tower


def weak_targets(game, count, x, y):
    """Frozen ground creeps with 1 life, around a point."""
    return [unit['id'] for unit in game.create(CREEPS, maul.GROUND_TARGET, x, y, count, life=1, frozen=True)]


def test_giants_rock_giant_grows_after_40_kills(game):
    """Rock Giant: 'after 40 kills upgraded' - it becomes its next form on the 40th kill."""
    race, _ = one_tower(game, 'Giants Hall', 'hC53')
    weak_targets(game, 45, *maul.single_spot()[1])
    game.wait('the Rock Giant to upgrade after 40 kills',
              lambda: any(u['type'] == 'h00A' for u in red_units(game)), timeout=150)


def test_giants_sea_giant_swarms_on_kills(game):
    """Sea Giant: its kills send out carrion swarms (a dummy casts A03T)."""
    one_tower(game, 'Giants Hall', 'o00Y')
    game.watch_casts(RED)
    weak_targets(game, 3, *maul.single_spot()[1])
    game.wait('a swarm from a kill', lambda: any(c['ability'] == 'A03T' for c in game.casts()), timeout=40)


def test_giants_ancient_golem_grows_every_minute(game):
    """Ancient Golem: '+75 damage every minute' - its base damage grows by 75 within a minute."""
    _, tower = one_tower(game, 'Giants Hall', 'o00X')
    before = game.inspect(tower)['damage_min']
    game.wait('the Ancient Golem to grow', lambda: game.inspect(tower)['damage_min'] >= before + 75, timeout=75)
    after = game.inspect(tower)['damage_min']
    if after != before + 75:
        raise TestFailed(f'the Ancient Golem went from {before} to {after} damage, not +75')


def test_giants_iron_golem_statue_spikes_during_waves(game):
    """Iron Golem Statue: spikes every 5 s while a wave runs (dummies cast A030), and not before."""
    one_tower(game, 'Giants Hall', 'oC26')
    game.watch_casts(RED)
    time.sleep(8)
    if any(c['ability'] == 'A030' for c in game.casts()):
        raise TestFailed('the Iron Golem Statue spiked with no wave running')
    maul.start_wave(game)
    game.wait('spikes during the wave', lambda: any(c['ability'] == 'A030' for c in game.casts()), timeout=20)


def test_corrupted_ancient_protector_starfall_during_waves(game):
    """Corrupted Ancient Protector: starfall every 30 s while a wave runs (a dummy casts A010)."""
    one_tower(game, 'Corrupted Night Elves', 'n00L')
    game.watch_casts(RED)
    maul.start_wave(game)
    # Its clock ticks every 30 s from when it was built, and a tick only casts inside a wave: the
    # first tick after the wave starts is the one
    game.wait('a starfall during a wave', lambda: any(c['ability'] == 'A010' for c in game.casts()), timeout=100)
