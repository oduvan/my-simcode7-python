"""SimCode city controller — adaptive Base-level climber.

Design in one paragraph
-----------------------
Everything is decided *statelessly* on `@on.idle`: the handler reads the live
world, scores every (source -> sink) haul it could do right now, and issues the
single best command.  There is no per-robot plan to go stale, so a hot-reload,
an expired robot or a surprise level-up can never strand anyone.  Coordination
comes from a small claim registry in `store` so N robots don't chase the same 10
ore.  A throttled planner runs alongside: it keeps a *wishlist* of buildings
derived from the live quest ladder (never hardcoded), places one site at a time
when the city can actually afford it, and keeps a Flying Station queueing
replacements for the fleet that constantly ages out.

Balance numbers are read from the world (`recipe`, `construction.required`,
`quest.required`, `unlocks`, `spot.remaining`, store capacities).  The only
hardcoded tables are the *shape* of the tech tree (which item comes from which
processor) and coarse cost hints used before a type has ever been built — the
hints are replaced by the real `construction.required` the first time a site of
that type is placed.
"""

from simcode import on, robots, buildings, world, store

# Bump when the shape of anything kept in `store` changes, so a hot-reload
# discards state written by an older strategy (CLAUDE.md gotcha #6).
VERSION = 7

# ---------------------------------------------------------------------------
# tech tree (structure, not balance): item -> (producer building, input items)
# ---------------------------------------------------------------------------
CHAIN = {
    "plate": ("smelter", ("ore",)),
    "wire": ("wire_mill", ("metal",)),
    "glass": ("glassworks", ("crystal",)),
    "coke": ("kiln", ("carbon",)),
    "part": ("assembler", ("plate", "wire")),
    "circuit": ("electronics_lab", ("wire", "glass")),
    "alloy": ("alloy_furnace", ("plate", "coke")),
    "module": ("module_assembler", ("part", "circuit")),
    "frame": ("frame_shop", ("alloy", "plate")),
}
PRODUCER = dict((v[0], k) for k, v in CHAIN.items())
RAWS = ("ore", "metal", "crystal", "carbon")
TIER = {"ore": 0, "metal": 0, "crystal": 0, "carbon": 0,
        "plate": 1, "wire": 1, "glass": 1, "coke": 1,
        "part": 2, "circuit": 2, "alloy": 2,
        "module": 3, "frame": 3}

# Coarse fallbacks, only used before a type has ever been placed.  Real values
# are learned from `construction.required` and cached in store["cost"].
COST_HINT = {
    "mining": {"ore": 15},
    "storage": {"ore": 8, "metal": 4},
    "flying_station": {"ore": 10, "metal": 5},
    "smelter": {"ore": 50, "metal": 30},
    "wire_mill": {"ore": 30, "metal": 50},
    "glassworks": {"ore": 50, "crystal": 30},
    "kiln": {"ore": 50, "carbon": 30},
    "assembler": {"plate": 40, "wire": 30},
    "electronics_lab": {"wire": 40, "glass": 30},
    "alloy_furnace": {"plate": 40, "coke": 30},
    "module_assembler": {"circuit": 25, "part": 35},
    "frame_shop": {"alloy": 35, "part": 25},
    "deep_mine": {"part": 30, "plate": 30},
    "warehouse": {"alloy": 25, "plate": 40},
    "charging_tower": {"circuit": 25, "wire": 40},
}
ROBOT_COST = {
    "builder": {"ore": 12, "metal": 6},
    "hauler": {"ore": 18, "metal": 10},
    "scout": {"ore": 10, "metal": 8},
    "mechanic": {"ore": 14, "metal": 10},
    "heavy_hauler": {"ore": 30, "metal": 18},
    "ranger": {"ore": 20, "metal": 16},
}
MINE_TYPES = ("mining", "deep_mine")
BANK_TYPES = ("storage", "warehouse")

# scoring
W_DIST = 0.45
P_SITE = 100.0
P_QUEST = 92.0
P_FLEET = 96.0         # a tiny fleet outranks the quest — but never a build site
P_STATION_LOW = 86.0
P_STATION = 52.0
P_STARVED = 88.0       # a quest-critical processor with an empty input store
P_PROC_WANT = 72.0
P_PROC_ANY = 44.0
P_BANK = 24.0
EX_CRITICAL = 130.0    # no mine at all yet — finding a spot is existential
EX_URGENT = 78.0       # a raw we need has no known live spot
EX_IDLE = 20.0         # nothing better to do
MARGIN = 12.0          # energy kept spare on every flight
MAX_FLEET = 44
MIN_FLEET = 7          # below this, growing the fleet beats building anything
CLAIM_TTL = 90

DIRS = [(1.0, 0.0), (0.92, 0.38), (0.71, 0.71), (0.38, 0.92),
        (0.0, 1.0), (-0.38, 0.92), (-0.71, 0.71), (-0.92, 0.38),
        (-1.0, 0.0), (-0.92, -0.38), (-0.71, -0.71), (-0.38, -0.92),
        (0.0, -1.0), (0.38, -0.92), (0.71, -0.71), (0.92, -0.38)]

_ECAP = [100.0]        # largest battery seen (module global; resets on reload)


# ---------------------------------------------------------------------------
# tiny helpers
# ---------------------------------------------------------------------------
def _dist(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return (dx * dx + dy * dy) ** 0.5


def _at(r, b):
    """Is the robot standing anywhere on this building's footprint?"""
    c = r.cell
    p = b.position
    if c is None or p is None:
        return False
    w, h = b.footprint
    return p[0] <= c[0] < p[0] + w and p[1] <= c[1] < p[1] + h


def _sget(key, default):
    v = store.get(key, None)
    return default if v is None else v


def _sput(key, value):
    store[key] = value


def _reset_if_stale():
    if _sget("v", None) != VERSION:
        _sput("v", VERSION)
        _sput("cl", {})
        _sput("nb", {})
        _sput("ei", 0)
        _sput("pt", -999)
        _sput("prt", -999)
        _sput("lt", -999)


class Snap:
    """One categorised pass over the buildings for this dispatch."""

    def __init__(self):
        self.base = None
        self.sites = []
        self.mines = []
        self.banks = []
        self.stations = []
        self.procs = []
        self.decom = []
        self.pads = []
        self.occupied = set()
        self.all = buildings.all()
        for b in self.all:
            p = b.position
            if p is not None:
                w, h = b.footprint
                for dx in range(w):
                    for dy in range(h):
                        self.occupied.add((p[0] + dx, p[1] + dy))
            t = b.type
            st = b.status
            if t == "base":
                self.base = b
                self.pads.append(b)
                continue
            if st == "constructing":
                self.sites.append(b)
                continue
            if st == "decommissioning":
                self.decom.append(b)
                continue
            if t in MINE_TYPES:
                self.mines.append(b)
            elif t in BANK_TYPES:
                self.banks.append(b)
            elif t == "flying_station":
                self.stations.append(b)
                self.pads.append(b)
            elif t == "charging_tower":
                self.pads.append(b)
            elif t in PRODUCER:
                self.procs.append(b)

    def near_pad(self, pos):
        best, bd = None, 1e9
        for p in self.pads:
            d = _dist(pos, p.position)
            if d < bd:
                bd, best = d, p
        return best, bd

    def stock(self):
        """Everything a robot could go and fetch right now."""
        out = {}
        for b in self.mines + self.banks:
            for it, n in b.storage.items.items():
                out[it] = out.get(it, 0) + n
        for b in self.procs:
            o = b.output
            if o:
                for it, n in o.items.items():
                    out[it] = out.get(it, 0) + n
        for b in self.decom:
            rc = b.recoverable
            if rc:
                for it, n in rc.items.items():
                    out[it] = out.get(it, 0) + n
        return out

    def mine_sites(self):
        return [b for b in self.sites if b.type in MINE_TYPES]


# ---------------------------------------------------------------------------
# claims — keep N robots off the same 10 ore
# ---------------------------------------------------------------------------
def _claims(tick):
    cl = _sget("cl", {})
    out = {}
    for rid, c in cl.items():
        if not isinstance(c, list) or len(c) < 6:
            continue
        if tick - c[4] > CLAIM_TTL:
            continue
        if rid not in robots:
            continue
        out[rid] = c
    return out


def _claim(rid, kind, src_id, item, amt, sink_id, tick):
    cl = dict(_sget("cl", {}))
    cl[rid] = [kind, src_id, item, int(amt), tick, sink_id]
    _sput("cl", cl)


def _claimed(cl, me, ident, item, slot):
    n = 0
    for rid, c in cl.items():
        if rid == me:
            continue
        if c[0] == "h" and c[slot] == ident and c[2] == item:
            n += c[3]
    return n


def _explorers(cl, me):
    return sum(1 for rid, c in cl.items() if rid != me and c[0] == "x")


# ---------------------------------------------------------------------------
# what the ladder wants
# ---------------------------------------------------------------------------
def _quest_need(base):
    q = base.quest
    if not q:
        return {}
    req = q.required or {}
    prog = q.progress or {}
    out = {}
    for it, n in req.items():
        left = int(n) - int(prog.get(it, 0))
        if left > 0:
            out[it] = left
    return out


def _quest_frac(base):
    q = base.quest
    if not q or not q.required:
        return 1.0
    req = q.required or {}
    prog = q.progress or {}
    tot = sum(int(n) for n in req.values()) or 1
    got = sum(min(int(n), int(prog.get(it, 0))) for it, n in req.items())
    return got / float(tot)


def _want(base, snap):
    """Items worth producing/hauling: the quest tree, the preview of the next
    level, and whatever our processors already make."""
    seeds = set()
    q = base.quest
    if q and q.required:
        seeds.update(q.required.keys())
    nq = base.next_quest
    if nq and nq.required:
        seeds.update(nq.required.keys())
    for b in snap.procs:
        seeds.add(PRODUCER.get(b.type))
    for b in snap.sites:
        seeds.add(PRODUCER.get(b.type))
    want = set(x for x in seeds if x)
    frontier = list(want)
    while frontier:
        it = frontier.pop()
        ent = CHAIN.get(it)
        if not ent:
            continue
        for src in ent[1]:
            if src not in want:
                want.add(src)
                frontier.append(src)
    want.add("ore")
    want.add("metal")
    return want


def _cost_of(btype):
    learned = _sget("cost", {})
    c = learned.get(btype)
    if c:
        return c
    return COST_HINT.get(btype, {})


def _learn_costs(snap):
    learned = dict(_sget("cost", {}))
    changed = False
    for b in snap.sites:
        req = b.construction.required or {}
        if req and learned.get(b.type) != req:
            learned[b.type] = dict(req)
            changed = True
    if changed:
        _sput("cost", learned)


def _live_spots(snap):
    """Discovered, undepleted resource spots with nothing built on them."""
    out = []
    for c in world.spots():
        sp = c.spot
        if not sp or (sp.remaining or 0) <= 0:
            continue
        if c.position in snap.occupied:
            continue
        out.append((c.position, sp.resource, sp.remaining or 0))
    return out


def base_unlocks(snap):
    u = snap.base.unlocks if snap.base else None
    if not u:
        return ["mining", "storage", "flying_station", "builder"]
    return list(u)


def _mine_counts(snap, spots_by_cell):
    have = {}
    for m in snap.mines:
        sp = m.spot
        if sp and (sp.remaining or 0) > 0:
            have[sp.resource] = have.get(sp.resource, 0) + 1
    for b in snap.mine_sites():
        res = spots_by_cell.get(b.position)
        if res:
            have[res] = have.get(res, 0) + 1
    return have


def _target_mines(res, lvl, want):
    if res not in want:
        return 0
    if res == "ore":
        base_n = 3
    elif res == "metal":
        base_n = 2
    else:
        base_n = 1
    # Rung N wants ~1.5x rung N-1, and spots are finite, so extraction has to
    # keep widening or the whole chain thins out from the bottom.
    return min(10, base_n + max(0, (lvl - 1) // 2))


def _direct_items(base):
    """Items the Base is asking for now, plus the previewed next level."""
    direct = set()
    q = base.quest
    if q and q.required:
        direct.update(q.required.keys())
    nq = base.next_quest
    if nq and nq.required:
        direct.update(nq.required.keys())
    return direct


def _glut_cap(base):
    """How much of one item is worth banking, sized to the current rung.

    Enough to cover the next quest comfortably; past that a pile is just metal
    and robot-hours the gating chain never sees."""
    q = base.quest if base else None
    biggest = max([int(n) for n in (q.required or {}).values()] or [200]) if q else 200
    return max(400, 2 * biggest)


def _quest_tree(base):
    """The items the Base asks for, plus everything they are made from."""
    tree = set(_direct_items(base))
    frontier = list(tree)
    while frontier:
        it = frontier.pop()
        ent = CHAIN.get(it)
        if not ent:
            continue
        for src in ent[1]:
            if src not in tree:
                tree.add(src)
                frontier.append(src)
    return tree


def _producer_cap(item, base, lvl, tree):
    """Factories are the throughput ceiling on product quests, so scale copies
    with the level (the ladder grows ~1.5x per rung).

    Copies also absorb wear: T2/T3 processors decay to a halt, and a world
    whose ladder never unlocks `mechanic` has no repair at all — width is the
    only way to keep output up.  An item feeding two branches (wire goes to
    both `part` and `circuit`) needs proportionally more."""
    if item not in tree and item not in _direct_items(base):
        return min(3, 1 + lvl // 3)
    users = sum(1 for x in tree
                if item in (CHAIN.get(x) or (None, ()))[1])
    return max(2, min(12, 2 + lvl + 2 * users))


def _wishlist(snap, want, spots, lvl, stock):
    """Ordered list of (building_type, resource_or_None) we would like next."""
    unl = set(base_unlocks(snap))
    wish = []
    spots_by_cell = dict((p, res) for p, res, _rem in spots)
    have_mine = _mine_counts(snap, spots_by_cell)
    # While the Base still wants plain raws, extractors + logistics beat
    # factories: the chain can wait until the level is nearly cleared.
    q = snap.base.quest
    raw_only = bool(q and q.required and
                    all(it in RAWS for it in (q.required or {})))
    # Factories are pointless without robots to feed them, and a raws-only rung
    # is cleared by extractors alone — so gate the chain on both.
    chain_ok = ((not raw_only) or _quest_frac(snap.base) >= 0.6) \
        and len(robots) >= MIN_FLEET

    def add(t, res=None, mult=1.0):
        """mult = how many times the cost must be in stock before placing it,
        so speculative breadth never eats materials the quest needs."""
        if t in unl:
            wish.append((t, res, mult))

    if have_mine.get("ore", 0) < 1:
        add("mining", "ore")
    if have_mine.get("metal", 0) < 1:
        add("mining", "metal")
    if not any(b.type == "flying_station" for b in snap.all):
        add("flying_station")
    for res in ("ore", "metal"):
        if have_mine.get(res, 0) < 2 and _target_mines(res, lvl, want) >= 2:
            add("mining", res)

    tree = _quest_tree(snap.base)
    bank_free = sum(b.storage.free for b in snap.banks)
    # Only the genuine emergencies come before the chain: a bank about to jam
    # (a full store freezes robots holding undroppable cargo, gotcha #7) and a
    # raw that has actually run out.
    if bank_free < 400:
        add("warehouse")
        add("storage")
    # A single bank is a single point of failure: when it fills there is no
    # drop target left, and robots holding undroppable cargo never re-enter
    # task selection (gotcha #7).  A spare Storage is 8 ore — buy the insurance.
    if lvl >= 3 and len(snap.banks) < 2:
        add("storage")
    for res in RAWS:
        if res in tree and stock.get(res, 0) < 100 and have_mine.get(res, 0) < 12:
            add("mining", res)

    if chain_ok:
        need = _quest_need(snap.base)
        # Build the chain the Base is waiting on hardest first, cheapest tier
        # up, so 100 coke is not queued behind a processor nobody needs yet.
        by_tier = sorted((x for x in want if x in CHAIN),
                         key=lambda i: (TIER.get(i, 9), -need.get(i, 0)))
        for item in by_tier:
            ptype, inputs = CHAIN[item]
            n = sum(1 for b in snap.procs if b.type == ptype)
            n += sum(1 for b in snap.sites if b.type == ptype)
            if n >= _producer_cap(item, snap.base, lvl, tree):
                continue
            blocked = False
            for src in inputs:
                if src in RAWS and have_mine.get(src, 0) < 1:
                    add("mining", src)
                    blocked = True
            if not blocked:
                # A factory for what the Base is waiting on may be placed
                # part-funded: its inputs are exactly the goods nothing else
                # consumes, and a site outranks every other sink, so it fills
                # itself instead of losing the race to a 15-ore mine.
                add(ptype, None, 0.25 if item in tree else 1.0)

    # Comfort measures rank BELOW the chain.  They are cheap, and the planner
    # places the first affordable wish it finds, so anything cheap listed above
    # a factory wins the build slot every single pass and the quest stalls
    # while the city digs its ninth mine.
    for res in RAWS:
        if res in tree and stock.get(res, 0) < 250 and have_mine.get(res, 0) < 12:
            add("mining", res)
    if bank_free < 1200:
        add("warehouse")
        add("storage")
    for res in RAWS:
        if have_mine.get(res, 0) < _target_mines(res, lvl, want):
            add("mining", res)

    # Count sites too: a type whose site is already in flight is NOT missing.
    # Without this the planner re-queues it every pass while it builds, and a
    # cheap building (a Station is 10 ore) wins the build slot every time.
    def planned(t):
        return (sum(1 for b in snap.all if b.type == t and b.status != "decommissioning"))

    # A Station is also a charging pad, and at 10 ore it is by far the cheapest
    # one (a Charging Tower costs 40 wire, which the chain always wants more).
    # Only the two nearest the Base are ever stocked, so extras out at the
    # frontier hoard nothing — they just stop robots flying home to charge.
    if planned("flying_station") < min(6, 2 + len(snap.mines) // 5):
        add("flying_station")
    # Towers save charging flights but cost wire, which is usually the very
    # thing the quest chain is short of — so only out of genuine surplus.
    if lvl >= 4 and len(snap.pads) + planned("charging_tower") \
            < 2 + len(snap.mines) // 3:
        add("charging_tower", None, 3.0)

    # Breadth.  The ladder is generated from the world seed, so the next rung
    # can ask for anything — a city that already runs one of every unlocked
    # processor starts each new quest with stock instead of a build queue.
    if chain_ok:
        for item in sorted(CHAIN, key=lambda i: TIER.get(i, 9)):
            ptype, inputs = CHAIN[item]
            if any(b.type == ptype for b in snap.procs + snap.sites):
                continue
            short = [s for s in inputs
                     if s in RAWS and have_mine.get(s, 0) < 1]
            for s in short:
                add("mining", s, 2.0)
            if not short:
                add(ptype, None, 2.5)
    return wish


def _reserve(snap):
    """Raw nothing but a construction site may spend (CLAUDE.md gotcha #8).

    Losing the ability to fund a mine is the one unrecoverable state in this
    game: mines cost a raw, spots are finite, and every other way of getting
    that raw back runs through a mine.  So one mine's worth is always ring-
    fenced — more while the extraction base is still thin."""
    res = {}
    mine = _cost_of("mining")
    for it, n in mine.items():
        res[it] = res.get(it, 0) + int(n)
    if len(snap.mines) < 2:
        for it, n in mine.items():
            res[it] = res.get(it, 0) + int(n)
    if not snap.stations:
        for it, n in _cost_of("flying_station").items():
            res[it] = res.get(it, 0) + int(n)
    return dict((k, v) for k, v in res.items() if k in RAWS)


def _spendable(stock, reserve):
    """How much of each ring-fenced raw ordinary traffic may still move."""
    return dict((it, stock.get(it, 0) - n) for it, n in reserve.items())


# ---------------------------------------------------------------------------
# planner — place sites only when the city can actually fund them
# ---------------------------------------------------------------------------
def _site_cells(snap, w, h, near, btype, nb, taken):
    """Nearest free w x h patch that hasn't already been refused for this type.

    Refused cells matter: without skipping them the search is deterministic, so
    one `cell_occupied` would make that building type unbuildable forever."""
    ring = []
    for rad in range(1, 22):
        for dx in range(-rad, rad + 1):
            for dy in range(-rad, rad + 1):
                if max(abs(dx), abs(dy)) != rad:
                    continue
                x, y = int(near[0]) + dx, int(near[1]) + dy
                ok = True
                for ax in range(w):
                    for ay in range(h):
                        c = (x + ax, y + ay)
                        if c in snap.occupied or c in taken:
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    continue
                if nb.get(_nb_key(btype, (x, y))):
                    continue
                ring.append(((dx * dx + dy * dy) ** 0.5, (x, y)))
        if ring:
            ring.sort()
            return ring[0][1]
    return None


def _nb_key(t, pos):
    return "%s@%s,%s" % (t, pos[0], pos[1])


def _income(snap):
    """Items the city can still obtain without building anything new."""
    out = set()
    for m in snap.mines:
        sp = m.spot
        if sp and (sp.remaining or 0) > 0:
            out.add(sp.resource)
    for b in snap.procs:
        out.add(PRODUCER.get(b.type))
    return out


def _unblock(snap, stock):
    """Break a raw deadlock by tearing a building down for its materials.

    A site short of a raw the city has none of and no way to mine is the one
    genuinely unrecoverable state (gotcha #8) — but a building's build cost AND
    its contents come back as a recoverable store, and a Station's store is
    otherwise a one-way door.  So spend a building to buy the way out."""
    if snap.decom:
        return False                       # already reclaiming something
    short = {}
    for b in snap.sites:
        req = b.construction.required or {}
        dlv = b.construction.delivered or {}
        for it, n in req.items():
            left = int(n) - int(dlv.get(it, 0))
            if left > 0:
                short[it] = short.get(it, 0) + left
    if not snap.mines and not snap.mine_sites():
        for it, n in _cost_of("mining").items():
            short[it] = max(short.get(it, 0), int(n))
    if not short:
        return False
    income = _income(snap)
    dead = [it for it, n in short.items()
            if stock.get(it, 0) < n and it not in income]
    if not dead:
        return False
    victim, score = None, -1.0
    for b in snap.stations + snap.procs + snap.banks[1:]:
        got = 0
        for it in dead:
            got += b.storage[it] + int(_cost_of(b.type).get(it, 0))
        if got > score:
            score, victim = got, b
    if victim is None or score <= 0:
        return False
    victim.destroy()
    return True


def _plan(snap, wish, spots, lvl, tick):
    if tick - _sget("pt", -999) < 6:
        return
    _sput("pt", tick)
    _learn_costs(snap)

    for b in snap.mines:
        sp = b.spot
        if sp and (sp.remaining or 0) <= 0 and b.storage.total == 0:
            b.destroy()
            return

    # A halted T2/T3 processor with nobody able to repair it is dead weight
    # blocking a cell.  Recycle it: the build cost comes back and the planner
    # puts up a fresh one at full condition.
    can_repair = ("mechanic" in base_unlocks(snap)
                  or any(r.type == "mechanic" for r in robots.all()))
    if not can_repair:
        for b in snap.procs:
            if b.condition is not None and b.condition <= 0 \
                    and (b.output is None or b.output.total == 0):
                b.destroy()
                return

    stock0 = snap.stock()
    if _unblock(snap, stock0):
        return

    # Recycle surplus Flying Stations.  A station's store is a one-way door —
    # you cannot pick up from it — so every extra one parks raws where nothing
    # can reach them.  Tearing it down returns the build cost AND the contents.
    cap_st = min(6, 2 + len(snap.mines) // 5)
    if len(snap.stations) > cap_st and not snap.decom:
        keep = set(b.id for b in sorted(
            snap.stations,
            key=lambda b: _dist(b.position, snap.base.position))[:2])
        for b in snap.stations:
            if b.id in keep:
                continue
            if b.production.active or (b.production.queued or 0):
                continue
            # Only scrap one that is redundant AS A PAD — another pad already
            # covers its patch.  A lone station out on the frontier is earning
            # its keep even with an empty store.
            near = [p for p in snap.pads
                    if p.id != b.id and _dist(p.position, b.position) < 10]
            if not near:
                continue
            b.destroy()
            return

    if len(snap.sites) >= 1 + min(3, len(robots) // 4):
        return
    if not wish:
        return
    # A part-funded factory site outbids every other sink, so keep at most two
    # in flight or they starve the very chain they are meant to widen.
    busy_proc = sum(1 for b in snap.sites if b.type in PRODUCER)
    stock = snap.stock()
    nb = _sget("nb", {})
    base_pos = snap.base.position if snap.base else (0, 0)

    spot_cells = set(s[0] for s in spots)
    for t, res, mult in wish:
        if nb.get(t) == "level_required":
            continue
        if t in PRODUCER and busy_proc >= 2:
            continue
        cost = _cost_of(t)
        if any(stock.get(it, 0) < int(n) * mult for it, n in cost.items()):
            continue
        if t in MINE_TYPES:
            cand = [s for s in spots if s[1] == res]
            if not cand:
                continue
            cand.sort(key=lambda s: _dist(s[0], base_pos) - 0.008 * s[2])
            for pos, _r, _rem in cand[:5]:
                if nb.get(_nb_key(t, pos)):
                    continue
                world.build(t, pos[0], pos[1])
                return
            continue
        w, h = (2, 2) if t in ("storage", "warehouse") else (1, 1)
        anchor = base_pos
        if t in ("flying_station", "charging_tower"):
            # Put the new pad where the fleet is furthest from one: at the mine
            # with the longest run home to charge.
            far, fd = None, -1.0
            for m in snap.mines:
                _p, d = snap.near_pad(m.position)
                if d > fd:
                    fd, far = d, m.position
            if far is not None and fd > 16:
                anchor = (int(round(far[0])), int(round(far[1])))
            pos = _site_cells(snap, w, h, anchor, t, nb, spot_cells)
            if pos is None:
                continue
            world.build(t, pos[0], pos[1])
            return
        # A T1 processor eats raws and emits fewer units than it consumes
        # (2 metal -> 1 wire), so siting it AT its mine roughly halves the
        # tonnage that has to cross the map.  T2/T3 run on goods and stay near
        # the Base, which is where their output is going anyway.
        item = PRODUCER.get(t)
        inputs = (CHAIN.get(item) or (None, ()))[1]
        if item and inputs and all(s in RAWS for s in inputs):
            src_mines = [m for m in snap.mines
                         if m.spot and m.spot.resource in inputs
                         and (m.spot.remaining or 0) > 0]
            if src_mines:
                near = min(src_mines,
                           key=lambda m: _dist(m.position, base_pos))
                if _dist(near.position, base_pos) > 12:
                    anchor = near.position
        pos = _site_cells(snap, w, h, anchor, t, nb, spot_cells)
        if pos is None:
            continue
        world.build(t, pos[0], pos[1])
        return


# ---------------------------------------------------------------------------
# fleet production
# ---------------------------------------------------------------------------
def _fleet_target(snap, lvl):
    return max(5, min(MAX_FLEET, 5 + 2 * len(snap.mines) + len(snap.procs)))


def _fleet_now():
    """Headcount discounting robots about to age out.

    Every robot expires on cumulative flight distance, so counting live bodies
    alone makes replacement a sawtooth: the fleet coasts down until it trips a
    threshold, and throughput sags the whole way."""
    n = 0
    for r in robots.all():
        lm = r.life_max or 0
        lr = r.life_remaining
        if lm and lr is not None and lr < 0.15 * lm:
            continue
        n += 1
    return n


def _pick_robot_type(snap, unl):
    have = {}
    for r in robots.all():
        have[r.type] = have.get(r.type, 0) + 1
    wearing = [b for b in snap.procs if b.condition is not None]
    if "mechanic" in unl and wearing:
        if have.get("mechanic", 0) < min(3, 1 + len(wearing) // 4):
            return "mechanic"
    if "scout" in unl and have.get("scout", 0) + have.get("ranger", 0) < 2:
        return "ranger" if "ranger" in unl else "scout"
    if "heavy_hauler" in unl:
        return "heavy_hauler"
    if "hauler" in unl:
        return "hauler"
    return "builder"


def _produce(snap, lvl, tick):
    if not snap.stations or tick - _sget("prt", -999) < 8:
        return
    _sput("prt", tick)
    if not snap.mines:
        return          # no raw income yet — do not burn the seed capital
    unl = set(base_unlocks(snap))
    want_type = _pick_robot_type(snap, unl)
    # A missing mechanic is worth a slot even at full headcount: without one a
    # worn T2/T3 processor slows, then halts, and the chain dies with it.
    if len(robots) >= _fleet_target(snap, lvl) + 8:
        return          # hard headcount ceiling, whatever the age mix says
    if _fleet_now() >= _fleet_target(snap, lvl) and want_type != "mechanic":
        return
    cost = ROBOT_COST.get(want_type, {"ore": 12, "metal": 6})
    for st in snap.stations:
        if st.production.active or (st.production.queued or 0):
            continue
        sto = st.storage
        if all(sto[it] >= n for it, n in cost.items()):
            st.build_robot(want_type, 1)
            return


# ---------------------------------------------------------------------------
# sinks & sources
# ---------------------------------------------------------------------------
def _sinks(snap, want, lvl, cl, me, stock):
    """[(priority, building, item, need)] — everything that wants something."""
    out = []
    for b in snap.sites:
        req = b.construction.required or {}
        dlv = b.construction.delivered or {}
        for it, n in req.items():
            need = int(n) - int(dlv.get(it, 0)) - _claimed(cl, me, b.id, it, 5)
            if need > 0:
                out.append((P_SITE, b, it, need))
    if snap.base:
        # Deliver against the LAGGING requirement first.  A rung often asks for
        # an item and something built from it (600 part + 1800 plate): if plate
        # always outranks everything, every plate goes straight to the Base,
        # the assemblers never get fed, and `part` simply never moves.  Letting
        # the item that is already ahead drop below processor-feeding priority
        # sends its surplus into the chain that is behind instead.
        q = snap.base.quest
        req = (q.required or {}) if q else {}
        prog = (q.progress or {}) if q else {}
        fracs = {}
        for it, n in req.items():
            if int(n) > 0:
                fracs[it] = min(1.0, int(prog.get(it, 0)) / float(int(n)))
        behind = min(fracs.values()) if fracs else 0.0
        for it, need in _quest_need(snap.base).items():
            need -= _claimed(cl, me, snap.base.id, it, 5)
            if need <= 0:
                continue
            ahead = fracs.get(it, 0.0) > behind + 0.15
            out.append((P_QUEST - 30.0 if ahead else P_QUEST,
                        snap.base, it, need))
    target = _fleet_target(snap, lvl)
    fleet = _fleet_now()
    # Robots ARE throughput, and they expire continuously — a fleet allowed to
    # decay drags every other number down with it.  Replacement therefore has
    # to outrank feeding a starved factory (P_STARVED), or the chain eats the
    # raws the Station needed and the city quietly shrinks to a handful.
    if fleet < MIN_FLEET or fleet < target * 0.5:
        p_st = P_FLEET
    elif fleet < target * 0.92:
        p_st = P_STARVED + 1.0
    else:
        p_st = P_STATION
    gap = max(0, target - fleet)
    # Stock for the class we actually intend to build.  A flat cap can sit
    # BELOW that class's cost (a hauler is 18 ore, the old cap was 14), and
    # then the station never accumulates enough and the fleet quietly stops
    # being replaced while robots keep expiring.
    rcost = ROBOT_COST.get(_pick_robot_type(snap, set(base_unlocks(snap))),
                           ROBOT_COST["builder"])
    # Only the couple of stations nearest the Base are robot factories; the
    # rest are just charging pads and must not hoard raws.
    prim = sorted(snap.stations,
                  key=lambda b: _dist(b.position, snap.base.position))[:2]
    for st in prim:
        sto = st.storage
        if sto.free <= 0:
            continue
        for it, per in rcost.items():
            cap = per * min(4, max(2, gap))
            need = min(cap - sto[it], sto.free) - _claimed(cl, me, st.id, it, 5)
            if need > 0:
                out.append((p_st, st, it, need))
    direct = _direct_items(snap.base) if snap.base else set()
    tree = _quest_tree(snap.base) if snap.base else set()
    qneed = _quest_need(snap.base) if snap.base else {}
    for b in snap.procs:
        rec = b.recipe
        inp = b.input
        if not rec or inp is None or inp.free <= 0:
            continue
        ins = rec.inputs or {}
        tot = sum(int(v) for v in ins.values()) or 1
        outp = PRODUCER.get(b.type)
        in_tree = outp in tree
        base_prio = P_PROC_WANT if in_tree else P_PROC_ANY
        # A quest item we already have far more of than the rung asks for does
        # not need more feeding — those trips belong to whichever item is
        # actually gating the level (233 alloy banked while circuits stall).
        if outp in direct and stock.get(outp, 0) >= qneed.get(outp, 0) + 80:
            base_prio = min(base_prio, P_PROC_ANY)
        # Same for a feeder that has run away from its consumers: 1593 wire
        # banked against a rung that cannot use it is metal and haulage spent
        # on nothing.  Keep a buffer sized to the rung, not an unbounded pile.
        elif outp not in direct and stock.get(outp, 0) > _glut_cap(snap.base):
            base_prio = min(base_prio, P_BANK)
        if b.condition is not None and b.condition <= 0:
            base_prio = 10.0          # halted: repairing it comes first
        for it, amt in ins.items():
            # Never let a factory nobody is waiting on eat a scarce input that
            # the quest chain needs (circuits swallowing the wire that `part`
            # was waiting for is how a level silently stops progressing).
            if not in_tree and it in tree and stock.get(it, 0) < 150:
                continue
            share = int(inp.capacity * (int(amt) / tot) * 0.9)
            need = min(share - inp[it], inp.free)
            need -= _claimed(cl, me, b.id, it, 5)
            if need <= 0:
                continue
            prio = base_prio
            # A factory the Base is waiting on must never sit input-starved.
            if base_prio > 10.0 and outp in direct and inp[it] <= 0.25 * share:
                prio = P_STARVED
            out.append((prio, b, it, need))
    # A bank takes anything.  It needs one entry PER ITEM as well as the
    # catch-all, because a fetch has to name the item it is going for — without
    # these a processor whose output store fills up simply stalls forever.
    present = set()
    for b in snap.mines:
        present.update(b.storage.items.keys())
    for b in snap.procs:
        o = b.output
        if o:
            present.update(o.items.keys())
    for b in snap.decom:
        rc = b.recoverable
        if rc:
            present.update(rc.items.keys())
    # Banking a raw the city already has mountains of just burns robot trips
    # (and lifespan), so stop pulling those once the pile is deep enough.
    gcap = _glut_cap(snap.base)
    glut = set(it for it in present
               if stock.get(it, 0) > (300 + 200 * lvl if it in RAWS else gcap))
    for b in snap.banks:
        free = b.storage.free
        if free <= 0:
            continue
        out.append((P_BANK, b, None, free))
        for it in present:
            if it not in glut:
                out.append((P_BANK, b, it, free))
    return out


def _sources(snap):
    """item -> [(building, available, urgency_bonus)] for everything in stock.

    Indexed by item so a haul search only walks the buildings that actually
    hold what a sink is asking for."""
    raw = []
    for b in snap.mines:
        sto = b.storage
        if sto.total <= 0:
            continue
        full = sto.capacity and sto.total >= 0.55 * sto.capacity
        raw.append((b, sto, 22.0 if full else 6.0))
    for b in snap.procs:
        o = b.output
        if o is None or o.total <= 0:
            continue
        full = o.capacity and o.total >= 0.6 * o.capacity
        raw.append((b, o, 26.0 if full else 8.0))
    for b in snap.decom:
        rc = b.recoverable
        if rc is not None and rc.total > 0:
            raw.append((b, rc, 30.0))
    for b in snap.banks:
        if b.storage.total > 0:
            raw.append((b, b.storage, 0.0))
    by_item = {}
    for b, sto, bonus in raw:
        for it, n in sto.items.items():
            if n > 0:
                by_item.setdefault(it, []).append((b, n, bonus))
    return by_item


# ---------------------------------------------------------------------------
# movement / energy
# ---------------------------------------------------------------------------
def _reach(r, dest, snap):
    _p, back = snap.near_pad(dest)
    return (r.energy or 0) >= _dist(r.position, dest) + back + MARGIN


def _go_charge(r, snap):
    pad, _d = snap.near_pad(r.position)
    if pad is None:
        return False
    if _at(r, pad):
        if (r.energy or 0) >= _ECAP[0] - 1.0:
            return False
        r.charge()
    else:
        r.move_to(pad.position[0], pad.position[1])
    return True


def _step(r, dest, snap):
    if _reach(r, dest, snap):
        r.move_to(dest[0], dest[1])
        return True
    return _go_charge(r, snap)


# ---------------------------------------------------------------------------
# the idle brain
# ---------------------------------------------------------------------------
def _deliver(r, snap, sinks):
    """Carrying something — put it in the best place that will take it."""
    inv = r.inventory
    best = None
    for prio, b, item, need in sinks:
        if item is None:
            if inv.total <= 0:
                continue
            it2, amt = None, min(inv.total, need)
        else:
            if inv[item] <= 0:
                continue
            it2, amt = item, min(inv[item], need)
        if amt <= 0:
            continue
        score = prio - W_DIST * _dist(r.position, b.position)
        if best is None or score > best[0]:
            best = (score, b, it2)
    if best is None:
        return False
    _s, b, item = best
    if _at(r, b):
        if b.type in PRODUCER or b.type == "flying_station":
            r.drop(item)
        else:
            r.drop()
        return True
    return _step(r, b.position, snap)


def _best_fetch(r, snap, sinks, sources, spendable, cl):
    free = r.inventory.free
    if free <= 0:
        return None
    best = None
    for prio, sink, item, need in sinks:
        if item is None:
            continue
        # Everything except a construction site is a one-way door for raws
        # (you cannot pick up from the Base or a Station), so those sinks only
        # ever get what is not ring-fenced for the next mine.
        room = None
        if sink.status != "constructing" and item in spendable:
            room = spendable[item]
            if room <= 0:
                continue
        cands = sources.get(item)
        if not cands:
            continue
        if len(cands) > 8:
            cands = sorted(cands,
                           key=lambda c: _dist(r.position, c[0].position))[:8]
        for src, avail, bonus in cands:
            if src.id == sink.id:
                continue
            if src.type in BANK_TYPES and sink.type in BANK_TYPES:
                continue
            avail -= _claimed(cl, r.id, src.id, item, 1)
            if avail <= 0:
                continue
            amt = min(free, avail)
            if room is not None:
                amt = min(amt, room)
            if src.type in BANK_TYPES:
                amt = min(amt, need)
            if amt <= 0:
                continue
            cost = _dist(r.position, src.position) + _dist(src.position, sink.position)
            score = prio + bonus - W_DIST * cost
            if best is None or score > best[0]:
                best = (score, src, sink, item, amt)
    return best


def _topup(r, snap, sinks, spendable, cl):
    """Already standing on a store with spare capacity — fill up before flying."""
    here = r.here.building
    if here is None or here.status == "constructing":
        return False
    if here.type in MINE_TYPES or here.type in BANK_TYPES:
        sto = here.storage
    elif here.type in PRODUCER:
        sto = here.output
    elif here.status == "decommissioning":
        sto = here.recoverable
    else:
        return False
    if sto is None or sto.total <= 0:
        return False
    free = r.inventory.free
    if free <= 0:
        return False
    best = None
    for prio, sink, item, need in sinks:
        if item is None or sink.id == here.id or prio < P_PROC_ANY:
            continue
        avail = sto[item]
        if sink.status != "constructing" and item in spendable:
            avail = min(avail, spendable[item])
        avail -= _claimed(cl, r.id, here.id, item, 1)
        avail = min(avail, free, need)
        if avail <= 0:
            continue
        score = prio - W_DIST * _dist(here.position, sink.position)
        if best is None or score > best[0]:
            best = (score, item, avail)
    if best is None:
        return False
    _s, item, amt = best
    r.pick_up(item, int(amt))
    return True


def _explore(r, snap, tick):
    i = int(_sget("ei", 0))
    _sput("ei", i + 1)
    bp = snap.base.position if snap.base else (0, 0)
    dx, dy = DIRS[i % len(DIRS)]
    ring = i // len(DIRS)
    base_r = 15.0 + 6.0 * (ring % 8)
    for scale in (1.0, 0.75, 0.5, 0.32, 0.2):
        rad = base_r * scale
        tgt = (bp[0] + dx * rad, bp[1] + dy * rad)
        if _reach(r, tgt, snap):
            _claim(r.id, "x", "", "", 0, "", tick)
            r.move_to(tgt[0], tgt[1])
            return True
    return _go_charge(r, snap)


def _explore_value(snap, spots, want, lvl):
    """How badly do we need new ground right now, and how many may go?"""
    known = set(s[1] for s in spots)
    for m in snap.mines:
        sp = m.spot
        if sp and (sp.remaining or 0) > 0:
            known.add(sp.resource)
    missing = [x for x in RAWS if x in want and x not in known]
    if not missing:
        # Exploring costs lifespan, and lifespan is the fleet's real currency.
        # With the map already yielding spots, one or two scouts is plenty.
        return EX_IDLE, (1 if len(robots) < 20 else 2)
    if not snap.mines and not snap.mine_sites():
        return EX_CRITICAL, len(robots)
    return EX_URGENT, max(1, min(3, len(robots) // 2))


def _mechanic_job(r, snap):
    worn = [b for b in snap.procs
            if b.condition is not None and b.condition < 65]
    if not worn:
        return False
    inv = r.inventory
    if inv.total > 0 and inv["metal"] <= 0:
        return False
    worn.sort(key=lambda b: (b.condition, _dist(r.position, b.position)))
    tgt = worn[0]
    if inv["metal"] > 0:
        if _at(r, tgt):
            r.repair()
            return True
        return _step(r, tgt.position, snap)
    best = None
    for b in snap.banks + snap.mines:
        n = b.storage["metal"]
        if n <= 0:
            continue
        d = _dist(r.position, b.position) + _dist(b.position, tgt.position)
        if best is None or d < best[0]:
            best = (d, b, n)
    if best is None:
        return False
    _d0, src, n = best
    if _at(r, src):
        r.pick_up("metal", int(min(inv.free, n)))
        return True
    return _step(r, src.position, snap)


def _status(r, snap, lvl, tick):
    if tick - _sget("lt", -999) < 150:
        return
    _sput("lt", tick)
    st = snap.stock()
    kinds = {}
    for b in snap.all:
        kinds[b.type] = kinds.get(b.type, 0) + 1
    q = snap.base.quest
    worn = [int(b.condition) for b in snap.procs if b.condition is not None]
    types = {}
    for x in robots.all():
        types[x.type] = types.get(x.type, 0) + 1
    kinds["fleet"] = types
    r.log("L%d t%d robots=%d/%d q=%s/%s stock=%s bld=%s sites=%d worn=%s unl=%s"
          % (lvl, tick, len(robots), _fleet_target(snap, lvl),
             (q.progress if q else {}), (q.required if q else {}),
             st, kinds, len(snap.sites),
             sorted(worn)[:6], sorted(base_unlocks(snap))))


def _act(e):
    rid = e.robot_id
    if rid is None or rid not in robots:
        return
    r = robots[rid]
    if r.position is None:
        return
    if (r.energy or 0) > _ECAP[0]:
        _ECAP[0] = r.energy
    _reset_if_stale()
    snap = Snap()
    if snap.base is None:
        r.move_to(r.position[0], r.position[1])
        return

    tick = world.tick or 0
    lvl = snap.base.level or 1
    want = _want(snap.base, snap)
    spots = _live_spots(snap)
    stock = snap.stock()
    wish = _wishlist(snap, want, spots, lvl, stock)
    _plan(snap, wish, spots, lvl, tick)
    _produce(snap, lvl, tick)
    _status(r, snap, lvl, tick)

    cl = _claims(tick)
    spendable = _spendable(stock, _reserve(snap))
    inv = r.inventory

    # 1. never die mid-flight
    pad, dpad = snap.near_pad(r.position)
    if pad is not None and (r.energy or 0) < dpad + 8.0:
        if _at(r, pad):
            r.charge()
        else:
            r.move_to(pad.position[0], pad.position[1])
        return

    if r.type == "mechanic" and _mechanic_job(r, snap):
        return

    sinks = _sinks(snap, want, lvl, cl, rid, stock)

    # 2. top up where we stand, then deliver what we hold
    if inv.total > 0:
        if inv.free > 0 and _topup(r, snap, sinks, spendable, cl):
            return
        if _deliver(r, snap, sinks):
            return

    # 3. best haul in the city vs. going to look for new ground
    haul = None
    if inv.total == 0:
        haul = _best_fetch(r, snap, sinks, _sources(snap), spendable, cl)
    ex_score, ex_cap = _explore_value(snap, spots, want, lvl)
    if _explorers(cl, rid) >= ex_cap:
        ex_score = -1e9

    if haul is not None and haul[0] >= ex_score:
        _s, src, sink, item, amt = haul
        _claim(rid, "h", src.id, item, amt, sink.id, tick)
        if _at(r, src):
            r.pick_up(item, int(amt))
        else:
            _step(r, src.position, snap)
        return
    if ex_score > -1e8 and _explore(r, snap, tick):
        return

    # 4. genuinely nothing to do — top the battery off, else go look around
    if (r.energy or 0) < _ECAP[0] * 0.9 and _go_charge(r, snap):
        return
    _explore(r, snap, tick)


@on.idle
def idle(e):
    try:
        _act(e)
    except Exception as ex:                                   # never strand a robot
        try:
            r = robots[e.robot_id]
            r.log("ERR idle %s: %r" % (e.robot_id, ex))
            if not _go_charge(r, Snap()):
                p = r.position or (0.0, 0.0)
                r.move_to(p[0] + 1.0, p[1])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# bookkeeping events
# ---------------------------------------------------------------------------
@on.blocked
def blocked(e):
    reason = e.get("reason", "")
    btype = e.get("type", None)
    pos = e.get("pos", None)
    if btype and pos:
        nb = dict(_sget("nb", {}))
        if len(nb) > 300:
            nb = {}
        if reason == "level_required":
            nb[btype] = reason           # the whole type is gated, not the cell
        else:
            nb[_nb_key(btype, (int(pos[0]), int(pos[1])))] = reason
        _sput("nb", nb)


@on.base_level_up
def leveled(e):
    _sput("nb", {})          # level_required blocks may have lifted
    _sput("pt", -999)
    _sput("lt", -999)
    for r in robots.all():
        r.log("LEVEL UP -> %s quest=%s unlocks=%s"
              % (e.get("level", "?"), e.get("quest", {}), e.get("unlocks", [])))
        break


@on.quest_updated
def quest(e):
    _sput("pt", -999)
    for r in robots.all():
        r.log("QUEST L%s %s" % (e.get("level", "?"), e.get("requirements", {})))
        break


@on.construction_complete
def built(e):
    _sput("pt", -999)
    _sput("prt", -999)


@on.spot_depleted
def depleted(e):
    _sput("pt", -999)


@on.robot_expired
def expired(e):
    cl = dict(_sget("cl", {}))
    if e.robot_id in cl:
        del cl[e.robot_id]
        _sput("cl", cl)
    _sput("prt", -999)


@on.robot_destroyed
def destroyed(e):
    expired(e)


@on.building_stopped
def stopped(e):
    _sput("prt", -999)
