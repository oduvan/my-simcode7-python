# SimCode city — robot controller (Python)

This repo is the **brain of one SimCode city**. `main.py` is a single Python program
that controls *all* the robots in your city in the **Robot City Builder** game. You don't
click to place buildings — **code is the only way to influence the world**. Push to this
repo's default branch and the platform hot-reloads your code into the running city; the
robots immediately act on the new program and you watch the city evolve at your city's
live page.

> This is a **user code repo**, not the platform. You only write the controller. The
> `simcode` client library, the world, the rules, and the robots all come from the platform.

## ⚡ Test locally BEFORE you push (do this every iteration)

Pushing to see the result is slow. You can run your `main.py` against the **real game
engine** on your own machine — it's the *exact* engine the server runs (downloaded on
demand, **not** a re-implementation) — so checking "does this actually work if I push
it *now*?" takes seconds.

**Step 1 — in a new environment, run the one-command setup. This is the FIRST thing to
run; nothing else is needed before it.**

```bash
./setup.sh                               # installs the test tooling + warms the engine cache
```

`./setup.sh` installs the local test tooling (the `simcode` client library + the
`robocity-sim` CLI) if it isn't installed yet, then runs a 1-tick local test that
downloads and caches the game engine — so a real run right afterwards is instant. It is
**idempotent and fast to re-run**: start every session with it without wondering whether
it already ran; when everything is in place it skips the install and returns in a moment.
If setup can't complete, it stops and names what was missing. It is **local testing only**
— it never deploys anything to your city (deploying is commit + push + resync, below).

> If pip puts `robocity-sim` somewhere that isn't on your `PATH` (typically `~/.local/bin`),
> setup.sh says so and prints the one `export PATH=…` line to paste into the shell you're in
> right now. It also adds that same line **once** to the shell startup files you already have
> (`~/.profile`, `~/.bashrc`, `~/.zshrc`) so a *new* shell needs no step at all — it names
> every file it changed, and those files are the only thing it writes outside this repo.

**Step 2 — after every edit, run the local check:**

```bash
robocity-sim run main.py                 # run your controller vs the REAL engine
robocity-sim run main.py --ticks 300     # simulate more ticks
robocity-sim run main.py --json          # machine-readable summary
```

**Check your LIVE city after a push** — same tool, no token, no MCP (reads the server's
public REST API; the city is auto-detected from this repo's git remote):

```bash
robocity-sim inspect            # compact status of your live city
robocity-sim inspect --state    # full current world state
robocity-sim inspect --logs 100 # recent activity + your r.log() output
robocity-sim inspect --errors   # unhandled exceptions since your last push
```

**If your city looks "frozen" (nothing moving), run `inspect --errors` FIRST.** A raised
handler leaves that robot uncommanded, so a bug in your code looks like a stuck city — this
lists every unhandled exception since your last release, grouped by `type` + `file:line`,
with a sample traceback and the log lines leading up to it. `handler errors` in a local
`run` catches most of these before you push; `inspect --errors` catches what only happens live.

**Background (what `./setup.sh` does for you — you don't have to run these by hand).** The
tooling is one package, `pip install "git+https://github.com/oduvan/simcode-robocity-python-tools"`,
which provides both the `simcode` client library and the `robocity-sim` CLI. The **first run
downloads the engine** from the server (`GET /api/engine/lib`) and **caches** it under
`~/.cache/simcode/`, so later runs are instant — no build step, no
token. Your `main.py` runs **unchanged**. Read the summary: `handler errors` must be
**0**, `robots destroyed` should be **0**, and `buildings` / `map revealed` should grow
if the controller is actually doing something. The exit code is non-zero if any handler
raised, so you can gate a push on it. Only push after a local run looks right.

> **Check your code with `robocity-sim run main.py` — NOT `python main.py`.**
> Running the file directly only *imports* it — it registers your handlers and exits
> without ever running the engine, so you learn nothing about behaviour (and it can't
> talk to the live platform). `robocity-sim run` drives your handlers against the
> real engine tick by tick, so you verify **behaviour**, not just that it imports. It's
> the one reliable way to check a controller.

> **Platform note:** the engine library is a glibc-linked Linux/macOS build, so run local
> tests on a normal glibc host (system `python3` / a `python:3.x` image — **not**
> Alpine/musl). To use a locally-built engine instead of the download, point
> `SIMCODE_ENGINE_SO` at a `libengine.so`; point `SIMCODE_SERVER` at a different server.

## How it works (the model)

- **One script, whole fleet.** `main.py` controls every robot, addressed by **id**.
- **Event-driven, async.** You register handlers; the game dispatches events; you react by
  issuing **commands** (intents). Data in → intents out. You never hold a live game object.
- **State is read fresh** from the world on each event — `robots[...]`, `buildings`, `world`
  reflect the current tick when your handler runs.
- **Serial per robot.** Events for one robot arrive one at a time; a robot runs one command
  at a time (a new command replaces the active one).
- **No manifest, no config.** The repo is just this script (+ optional `lib/`). Language is
  chosen when the city is created; the entry is always `main.py`; the world and starting
  robots are defined by the game module.

## The game you're playing (Robot City Builder)

### ⚖️ Balance lives in the config — read it, don't hardcode

This doc describes **mechanics, roles, and the API** — deliberately **without balance numbers**
(cargo sizes, speeds, lifespans, costs, recipe amounts, store caps, quest quantities, wear/repair
rates, energy, start capital). Those are **not** fixed: the same module is **tuned per city** (#35)
and **rebalanced over time**, so any number written in a doc goes stale. **The config is the source
of truth; this doc is not.** Always derive balance from the live game:

- **At runtime, read what the game exposes** rather than using constants:
  - `building.recipe` — a built processor's `.inputs` / `.output` / `.out_amount` / `.ticks`.
  - `buildings.base.unlocks` — the building + robot types buildable at the current level.
  - `buildings.base.level` and `buildings.base.quest` (`.required` / `.progress`).
  - a robot's `.type`, `.life_remaining`, `.life_max`.
  - a store's capacity: `building.storage.capacity`, `r.inventory.capacity`, etc.

  Prefer these live handles over any hardcoded number.
- **The authoritative full balance for the city** is its **world config**, surfaced by the MCP
  tool **`get_world_config`**. It returns `robot_types` (cargo / speed / lifespan / cost /
  unlock-level per class), per-building `cost` / `build_ticks`, `tunables` (carry capacity, speeds,
  all store caps, mining, energy, start capital), the `unlocks` ladder, the `maintenance` dials
  (wear / repair rates), and the `quest` formula. When you (or an assistant) need an **exact**
  number, read it from there. Numbers can differ **per city** and **change over time** — so never
  copy a magnitude out of this doc; read it from the config.

Goal of the reference module: **raise the Base's level**. The Base sets a **quest** and each
quest cleared **levels the Base up** to a harder one — endlessly. Your **highest Base level is
your score.** Leveling is **product-based**, and each level **unlocks** more of the tech tree.

**The ladder is ENDLESS and GENERATED FROM YOUR WORLD'S SEED.** There is no fixed list of levels
to memorise: your city derives its own, so **another city's ladder is different from yours**, and
the same seed always reproduces the same one. Two consequences for your code:

- **Never hardcode what a level wants.** Read `buildings.base.quest.required` and react to it.
  A level may ask for raws, T1, T2 or T3 items depending on where you are and which world you got.
- **Never assume which level unlocks what.** Read `buildings.base.unlocks` — the type that arrived
  at level 3 in one city may arrive later or earlier in another.

Two things are guaranteed, so you can always make progress:

- **Every level is completable with what you already have.** A level never asks for something only
  a later level unlocks.
- **Every level is harder than the one below it** — measured in raw material, so a level wanting
  20 `module` counts as harder than one wanting 400 `ore`.

**Level 1 is always a raws-only bootstrap** (two of the four raws), so you can score by mining and
hauling before you have built any chain.

**The next level is a surprise.** Only the level you are working toward is published. The one above
appears as `buildings.base.next_quest` (`.level`, `.required`, `.unlocks`) once you pass ~75% of
the current level — use it to start stockpiling early. While it is absent, the next level is simply
not revealed yet; it never means you have run out of levels, because you cannot.

Building — or building a robot — of a **not-yet-unlocked** type is **rejected** with a
`level_required` reason.


Two new pressures make it a *living* economy — the fleet and the factories both **decay**, so you
never set-and-forget:

- **Robots expire (distance-based lifespan).** Every robot has a **max cumulative flight
  distance**; once it's flown that far it is **removed from the map** (`robot_expired`). This is
  *separate* from energy death (`robot_destroyed`, which charging avoids) — expiry is inevitable
  end-of-life. So the fleet is always aging out and you must **build replacements**. Higher robot
  types live longer (read `r.life_max` / the config for the actual figures).
- **T2/T3 processors wear out.** Producing batches drains a processor's **condition** (full →
  empty); productivity slows past the halfway mark and **stops when it hits empty**. A **mechanic**
  robot carrying **metal** flies to the worn building and runs `repair()` to restore it. (Mining
  and T1 processors never wear.)

**Robot types** — chosen at build time via `build_robot(type, n)`, unlocked by Base level. Each
class differs in **cargo / speed / lifespan / cost** — read the actual figures from
`get_world_config`'s `robot_types` (or a live robot's handles), not from here:

| Type | Unlock | Role |
| --- | --- | --- |
| **builder** | L1 | generalist — the starting fleet; places & supplies sites |
| **hauler** | L2 | logistics — big loads, slow |
| **scout** | L2 | exploration — fast, far, low cargo, cheap |
| **mechanic** | L2 | building maintenance (`repair`); carries metal |
| **heavy_hauler** | L4 | advanced logistics — largest loads |
| **ranger** | L4 | advanced explorer — fast and long-lived |

Robots cost **raw ore + metal** (per type — the amount is in the config), spent from a Flying
Station's own store.

> **This starter does NOT play the game — it does nothing at all.** Robots stay PARKED at the
> Base until your code moves them. That is deliberate: a parked robot spends no energy and no
> lifespan, so the city stays stable however long you leave it, and it can never age its fleet
> out into a dead end. Building the winning loop below is **your** job — that's the point of a
> starter. Grow `main.py` from the empty handler it ships with.

The loop you'll build toward:

```
pick up a kit from the starting Storage → fly to a resource spot →
  place a Mining site (world.build) + drop the kit to build it → the mine digs itself →
  build PROCESSORS (smelter/mill/lab…) → haul raws INTO them, pick FINISHED goods OUT →
  haul the tier the quest wants to the Base → Base LEVELS UP + unlocks the next tier → repeat
  (and: build a Flying Station, stock it with ORE + METAL, and build_robot(type,…) — every
   robot expires by distance, so keep making REPLACEMENTS or the fleet dies out;
   build mechanics to REPAIR worn T2/T3 processors; recharge on any pad to keep flying)
```

- **Robots start EMPTY.** There's no free kit — a robot carries nothing until it picks
  something up. Your capital is a **Storage building pre-placed next to the Base**, stocked
  with a starting supply of **ore + metal** (the amount is set by the config); robots `pick_up`
  from it to get building materials.
- **The world is endless & continuous.** Robots have **float** `(x, y)` positions and **fly**
  in straight lines from any point to any point, ignoring terrain and each other (no
  pathfinding, multiple robots may share a spot). They interact with a building by their
  **rounded cell** (`r.cell`). Flying **spends energy** (∝ distance); run the battery to zero
  **mid-flight and the robot is destroyed** — its cargo vanishes. Recharge by landing on a
  **charging pad** (the **Base**, a **Flying Station**, or a **Charging Tower**) and calling
  `r.charge()`.
- **Resources:** **four raws** — `ore`, `metal`, `crystal`, `carbon` — each found at finite
  **spots** (a spot yields one raw). A **Mining building mines autonomously** into its own
  storage — there is no `mine` command; a robot only **picks up** the output and **hauls** it.
  Everything above raw is made by a **processor** (below).

### The supply chain (the heart of the game)
Higher-tier goods come from **autonomous processor buildings**: each has an **input** store
(robots `drop` raws/goods in) and an **output** store (robots `pick_up` finished goods out), and
it converts input→output on its own over time. **Robots never process — they only haul.** The
tree branches raws → basics → intermediates → advanced:

| Tier | Processor | Refines (item flow) | Wears? |
| --- | --- | --- | --- |
| T1 | **smelter** | `ore → plate` | no |
| T1 | **wire_mill** | `metal → wire` | no |
| T1 | **glassworks** | `crystal → glass` | no |
| T1 | **kiln** | `carbon → coke` | no |
| T2 | **assembler** | `plate + wire → part` | **yes** |
| T2 | **electronics_lab** | `wire + glass → circuit` | **yes** |
| T2 | **alloy_furnace** | `plate + coke → alloy` | **yes** |
| T3 | **module_assembler** | `part + circuit → module` | **yes** |
| T3 | **frame_shop** | `alloy + plate → frame` | **yes** |

The table shows only the **item flow** (which inputs a processor turns into which output) — the
exact input/output **amounts**, batch **ticks**, and **build cost** are balance, so read them from
`building.recipe` and `get_world_config`, not here. Each batch makes its output over a few ticks;
input/output stores have a **fixed cap** (in the config), so a processor accumulates real stock
between hauls. When a processor finishes a batch it fires **`resource_produced`** (go haul the
output away); when it stalls it fires **`production_blocked`** with `reason` `output_full` (pick the
output up) or `input_short` (haul more input in). A building's cost is always paid in tiers **below**
its output, so the whole tree bootstraps from raws — no deadlock.

**Wear (T2/T3 only).** The T2/T3 processors have a **condition** meter that each batch drains
(from full toward empty). Above the halfway mark they run full speed; below it each batch takes
longer; at **empty they stop producing entirely**. Keep them serviced with a **mechanic** (see the
`repair` command). Mining and the T1 processors **never wear**, and the mechanic unlocks at L2
alongside T2, so nothing can decay before you can fix it. (Wear-per-batch and repair rates are
config `maintenance` dials — read them, don't assume.)

**Upgrade buildings** (built structures, not processors) sink advanced goods for better logistics:
- **deep_mine** (built from parts + plate) — a mine that extracts **faster into a larger buffer** than plain Mining.
- **warehouse** (built from alloy + plate, 2×2) — a general store like Storage but **much larger**.
- **charging_tower** (built from circuit + wire) — a remote **charging pad** out on the frontier.

(Their build costs are in the config — the point is only that they consume **advanced** goods.)

- **Buildings you don't build:** the **Base** (pre-placed, one) — the **quest hub** and a
  **charging pad**. `drop` goods on it to progress the current quest (its store is capped
  per-item at the requirement; excess stays on the robot); meet it and it **levels up**. You
  **cannot** `pick_up` from the Base, and it **cannot be destroyed**.
- **Buildings you build** with `world.build(type, x, y)` (all self-complete once supplied):
  - **mining** — placed on a live resource spot; auto-mines its raw into a small capped store.
  - **storage** (2×2) — a big buffer robots `pick_up` from and `drop` into.
  - **flying_station** — a **charging pad** *and* the **robot factory**: stock it (`drop`
    **ore + metal**), then call `station.build_robot(type, n)` to produce robots there.
  - all the **processors** and **upgrade buildings** above.
- **Everything except the Base is built autonomously:** place a site with
  `world.build(type, x, y)`, robots **`drop`** resources to fulfil the recipe, and the site
  **self-completes** once supplied — no connect step, no robot labor. You can also **tear a
  building down** with `world.destroy(x, y)` (or `b.destroy()`) to reclaim its materials.
- **Base building recipes:** Mining is **ore-only** (metal spots are precious, so raw extractors
  never cost metal); Storage and Flying Station cost **ore + metal**; a Flying Station spends **raw
  ore + metal** from its own store per robot it builds — the amount depends on the **type**. So
  growing the fleet is a raw-materials sink, not a product sink. (All the actual amounts live in the
  config — read `get_world_config` for exact build costs and per-type robot costs.)
- **Every build cost exceeds a robot's carry capacity, so building is a ≥2-trip haul.** No
  site can be funded by a single `pick_up` — construction sites **accumulate deliveries across
  trips**, so plan on flying a load in, `drop`-ing it, and returning for more.
- **Same map for everyone.** The module fixes the world seed, so *every* city of this type
  starts from the **identical canonical map** — the only variable is your code. It's a contest
  of whose program climbs to the highest Base level.

## Client library reference

```python
from simcode import on, robots, buildings, world, store
```

### Subscribe to events — `@on.<event>`
Each event carries `e.robot_id`. Common events and their extra fields:

| Event | Fields | Fires when |
| --- | --- | --- |
| **`idle`** | — | **a robot has no command and needs one** — after any command completes, or right after spawn. Re-fires every few ticks while it stays free (not every tick). **This is the main hook: handle it, decide, issue the next command.** |
| `spawn` | — | a robot enters the world (or your code reloads). |
| `arrived` | `position` | a `move_to` flight reached its target. |
| `blocked` | `reason` | a move/action couldn't complete (e.g. `no_station`). |
| `construction_started` | `building_id`, `type` | a `world.build(...)` placed a site. |
| `resource_delivered` | `building_id`, `item`, `amount` | a `drop` onto a site/store landed (one per item). |
| `construction_complete` | `building_id`, `type` | a site finished building (now active). |
| `spot_depleted` | `building_id` | a Mining building's resource spot ran out (no `robot_id`). |
| `storage_full` | `building_id` | a building's storage is full (no `robot_id`). |
| `resource_produced` | `building_id`, `item`, `amount` | a **processor** finished a batch — its output is ready to haul away (no `robot_id`). |
| `production_blocked` | `building_id`, `reason` | a processor stalled — `reason` is `output_full` (pick its output up) or `input_short` (haul more input in). Fires once per stall, no `robot_id`. |
| `building_destroyed` | `building_id` | a `world.destroy`'d building finished emptying its recoverable store and was removed (no `robot_id`). |
| `decommission_started` | `building_id` | a building entered `decommissioning` (its materials are now a recoverable store to haul away; no `robot_id`). |
| `maintenance_needed` | `building_id` | a T2/T3 processor's **condition dropped below the maintenance threshold** (around half) — send a mechanic to `repair` it before it slows/stops (no `robot_id`). |
| `building_stopped` | `building_id` | a T2/T3 processor's **condition hit empty** — it has **halted** production entirely until repaired (no `robot_id`). |
| `repair_complete` | `building_id` | a mechanic's `repair` process ended — the mechanic **ran out of metal** or the building reached **full condition** (no `robot_id`). |
| `inventory_full` | — | a robot can't carry more. |
| `robot_produced` | `robot_id` | a **Flying Station** finished a new robot. |
| `robot_destroyed` | `position`, `reason` | a robot ran out of energy **mid-flight** — gone, cargo lost. Avoidable by charging in time. |
| `robot_expired` | `position`, `reason` | a robot **exceeded its flight lifespan** (max cumulative distance) and was removed — cargo lost. **Separate** from `robot_destroyed`, and **inevitable** end-of-life: build replacements. |
| `charge_complete` | — | a robot on a charging pad finished charging (battery full). |
| `quest_updated` | `level`, `requirements{item:qty}` | the Base's current quest — at start and after each level-up. **Leveling is product-based**: L1 wants raw ore+metal, then L2+ wants products (part → module → module+frame). (`building_id`, no `robot_id`.) |
| `base_level_up` | `level`, `quest{item:qty}`, `unlocks` | the Base cleared its quest and **leveled up** — carries the next quest's item map **and the set of buildings/robot types now unlocked** at the new level (`building_id`, no `robot_id`). |
| `message` | `from`, `payload` | another robot messaged this one. |

The cleanest controller is built around **`idle`**: it fires exactly when a robot is free,
so you don't poll and you don't have to chain every completion event by hand. The starter is
essentially one `@on.idle` handler that reads the robot's live state and issues its next move.
The other events are there when you want their payload (e.g. `arrived.position`). Discovery
happens **by flying** — a robot reveals a radius (~5) around itself as it moves; to explore,
just `move_to` a point in the fog. There is no separate reveal command.

`subscribe(event, handler, once=False)` / `unsubscribe(...)` register at runtime too.

### Command a robot — `r = robots[id]`
A command tells one robot to do one thing. The robot can run **only one at a time** — issuing
a new command replaces the current one. Timed commands (`move_to`, `charge`) finish over
several ticks and fire a completion event; instant ones (`pick_up`, `drop`) resolve right
away. **Either way, when the robot is free again it fires `idle`** — so you rarely need the
specific completion events; just react to `idle`. Placing a building is a **world** command,
`world.build(...)`, not bound to a robot.

| Call | What it does | Completes with |
| --- | --- | --- |
| `r.move_to(x, y)` | **Fly** in a straight line to float `(x, y)`, ignoring terrain/other robots. Spends energy with distance; reveals the map (radius ~5) as it goes — this is how you explore. | `arrived` / `blocked` / `robot_destroyed` |
| `world.build(type, x, y)` | Place a self-building construction **site** at `(x, y)`. `type` is any buildable: `mining` (must be on a live resource spot), `storage`, `flying_station`, any **processor** (`smelter`/`wire_mill`/`glassworks`/`kiln`/`assembler`/`electronics_lab`/`alloy_furnace`/`module_assembler`/`frame_shop`), or an **upgrade** (`deep_mine`/`warehouse`/`charging_tower`). The Base isn't buildable. A type not yet unlocked at the current Base level is **rejected** (`blocked`, reason **`level_required`**). **Not** bound to a robot. | `construction_started` / `blocked` |
| `world.destroy(x, y)` | Tear down the building at `(x, y)`: it enters `decommissioning`, its **build cost + current contents** become a **recoverable** store, robots `pick_up` it empty, then it's removed. **Not** bound to a robot; the Base can't be destroyed. Also `b.destroy()` on a handle. | `decommission_started` → `building_destroyed` |
| `r.pick_up(item=None, amount=None)` | Grab resources from the building **on your cell** into your inventory — a Mining/Storage store, a **processor's output**, or a **decommissioning** building's recoverable store. **No args** = take everything that fits; **item only** = all of that item; **item + amount** = that amount (e.g. `r.pick_up("ore", 6)`). Instant. | resolves, then `idle` |
| `r.drop(item=None, amount=None)` | Release your inventory into the building/site **on your cell** — supply a build site, feed the Base/Storage, or load a **processor's input**. **No args** = drop everything; **item only** = all of that item; **item + amount** = that amount (e.g. `r.drop("metal", 3)`). Instant. | `resource_delivered` |
| `r.charge()` | Charge on the **charging pad on your cell** — a **Flying Station**, the **Base**, or a **Charging Tower**; holds the robot until the battery is full. | `charge_complete` / `blocked` (`no_station`) |
| `r.repair()` | **Mechanic only.** On a **worn T2/T3 processor** on your cell, start a **repair process** that drains the mechanic's **held metal** into the building's `condition` over time (the metal→condition rate is a config `maintenance` dial). Stops when the mechanic runs dry **or** the building hits full. Fetch metal first, fly to the building, then `repair()`. | `repair_complete` / `blocked` |
| `r.send(target_id, payload)` | Send a message to another robot. | the peer gets a `message` event |
| `r.cancel()` | Abort the current command; the robot goes free. | `idle` |
| `r.log("…")` | Write a line to the city log (for debugging your code). | — |

**Position-based:** `pick_up`, `drop`, `charge`, and `repair` act on whatever building/site is on
the robot's **current (rounded) cell** (`r.cell`). So to haul, `move_to` the mine, `pick_up`, then
`move_to` the Base and `drop`; to recharge, `move_to` a Flying Station then `charge()`; to service
a worn factory, load a mechanic with metal, `move_to` the processor, then `repair()`. On a
**processor** the direction implies the store: **`drop` loads its input**, **`pick_up` pulls its
output** — so you feed one and harvest the other at the same building. Mining, construction, and
processing are all **autonomous**, so there are no robot-driven mining, refining, build-wiring,
site-placing, or single-step-move commands — robots only fly, haul, charge, and (mechanics) repair.

### The Base — the quest hub — `b = buildings.base`
There's one Base; reach it via `buildings.base`. It **isn't built or commanded** — you feed it
and read its objective:
- **Feed it:** robots `drop(item, …)` (or bare `drop()` for all) on the Base's cell — deliver
  whatever the current quest asks for. Its store is the **quest accumulator**, capped per-item at
  the requirement (excess stays on the robot). You **cannot `pick_up` from the Base**, and it
  **can't be destroyed.** It also doubles as a **charging pad** (`r.charge()`).
- **Read the objective:** `buildings.base.level` (current level, starts at 1) and
  `buildings.base.quest` — `.required` and `.progress` (progress = min(delivered, required)).
  ⚠️ **These two are plain Python `dict`s (`{item: amount}`), NOT `Store` handles** like
  `r.inventory` / `building.storage`. So read them as dicts — `q = base.quest.required;
  q.get("ore", 0)`, `for item, need in q.items(): …` — and do **not** expect `Store` extras
  (`.items` here is the dict's own method, not a property; there's no `.total`, `.capacity`, or
  `Store`-style `in`). Don't write one accessor that assumes `.items` is a property for both, or it
  silently returns nothing for the quest maps. Leveling is **product-based**: L1 wants **raw ore+metal**, then every
  level after wants **products** (L2 part → L3 module → L4+ module+frame), so read the requirement
  each time rather than assuming ore/metal. Deliver it and the Base **levels up** to the next,
  harder quest.
- **Read the unlocks:** `buildings.base.unlocks` — the set of building + robot types **buildable
  at the current level**. Each level-up **unlocks the next tier**; trying to build (or
  `build_robot`) a still-locked type is rejected with `level_required`. Subscribe to
  `quest_updated` / `base_level_up` (which carries the new `unlocks`) to react.

### Grow the fleet — Flying Stations — `buildings.stations()`
Robots are built at a **Flying Station** (not the Base). Build one with
`world.build("flying_station", x, y)`, stock it, then command it:

| Call | What it does |
| --- | --- |
| `station.build_robot(type="builder", n=1)` | Queue `n` robots of `type` at **this** station. Each consumes that type's **raw ore + metal** cost (per-type amount in the config) from the station's own store and takes time; each finished one spawns **empty** at the station and fires `robot_produced` + its first `idle`. Waits if the store is short. A type not unlocked at the current Base level is rejected (`level_required`). |
| `station.cancel()` | Clear this station's production queue. |

Get a station handle from `buildings.stations()` (or `buildings.of_type("flying_station")`);
each exposes `.storage` (its production store — `drop` **ore + metal** here to fund robots) and
`.production` (`.active`, `.progress`, `.queued`). You **cannot `pick_up` from a station** (its
store is production-only). Robots cost **raw ore + metal** again, so fleet growth competes with
building for your raw supply — and only **builders** are available until you reach **L2** (which
unlocks hauler/scout/mechanic).

### Read the world (read-only handles)
You never hold a live object — these read **fresh** state each time your handler runs.

- **Robots:** `robots[id]`, `robots.all()`, `robots.of_type(t)`. A robot handle exposes
  `r.id`, `r.type`, `r.position` → **float** `(x, y)`, `r.cell` → the **rounded** `(x, y)` used
  for position-based actions, `r.facing`, `r.state`
  (`idle`/`moving`/`charging`/`hauling`/`blocked`), `r.command` (what it's doing),
  `r.energy` (battery, 0…cap), `r.inventory` (a **`Store`** item map: `r.inventory["ore"]`
  (missing → `0`), `.items`, `.free`, `.total`, `.capacity`, `.is_full`, `"ore" in r.inventory`),
  `r.life_remaining` / `r.life_max` (flight-distance lifespan — how much cumulative distance is
  left before this robot **expires**, and its type's max; watch `life_remaining` to retire/replace
  a robot proactively), `r.here` (`.terrain`, `.spot`, `.building` — what's on its cell),
  `r.nearest(kind=…|type=…)`, and `r.memory` (a per-robot dict you can write to).
- **Buildings:** `buildings[id]`, `buildings.all()`, `buildings.of_type(t)`, `buildings.base`,
  `buildings.stations()`. A building handle exposes `.type` (one of `base`, `mining`, `storage`,
  `flying_station`, the processors `smelter`/`wire_mill`/`glassworks`/`kiln`/`assembler`/
  `electronics_lab`/`alloy_furnace`/`module_assembler`/`frame_shop`, or the upgrades
  `deep_mine`/`warehouse`/`charging_tower`), `.position`, `.footprint` (w, h),
  `.status` (`constructing`/`active`/`decommissioning`), `.storage` (a **`Store`** item map:
  `b.storage["ore"]`, `.items`, `.free`, `.total`, `.capacity`; also `b.stored("ore")`),
  `.spot` (Mining: `.resource`, `.remaining` — the building auto-mines into its storage),
  `.level` + `.quest` + `.unlocks` (Base: `.quest.required` / `.quest.progress`, each a **plain
  `dict`** `{item: amount}` — NOT a `Store`, unlike `.storage`/`.inventory`; read with
  `q.get(item, 0)` / `q.items()`; `.unlocks` = types buildable at the current level),
  `.production` (Flying Station: `.active`, `.progress`, `.queued`), plus for a **processor**
  `.input` / `.output` (each a **`Store`**) and `.recipe` (`.inputs` map, `.output` item,
  `.out_amount`, `.ticks`), and for a **T2/T3 processor** `.condition` (a wear meter, full→empty —
  below the halfway mark it slows, at empty it stops; a mechanic's `repair` restores it), `.recoverable` (a **`Store`**
  while `decommissioning` — pick it empty to remove the building), `.construction` (while building:
  `.required`, `.delivered`, `.progress` — sites self-complete, so **no `connected` field**), and
  `.destroy()` to tear it down.
- **World:** `world.tick`; `world.size` (bounding box of the **discovered** region — a viewport
  hint, not a fixed extent), `world.origin`, `world.endless` (`True`); `world.spots()` — the
  resource spots you've **discovered so far** (each with `.position`, `.spot.resource`,
  `.spot.remaining`); `world.cells()` — the revealed tiles. The world is **endless**, generated
  lazily as robots fly into the fog. `world.build(type, x, y)` places a construction site.
- **`store`** — a city-wide dict for your own state that survives across events.

## Common gotchas

Real things that trip up controllers — **behaviour, not magnitudes** (read the numbers from the
config, per the balance rule above):

1. **A build cost can exceed a robot's inventory.** Funding a construction site can take
   **several trips** — sites accumulate deliveries across `drop`s. Don't assume one `pick_up`
   funds a site: read the cost from `building.recipe` and your cap from `r.inventory.capacity`,
   and keep hauling until the site completes.
2. **`drop()` deposits only what the target still needs or can hold — the excess stays on the
   robot.** True for build sites, processor inputs, and the Base. Over-pick, or drop mixed cargo
   into a store that only wants some of it, and you keep the leftovers — and can loop re-dropping
   into a full store (looks stuck). `resource_delivered` reports the amount actually **accepted** —
   trust that, not what you asked to drop.
3. **`world.build` / `world.destroy` failures are WORLD events with no `robot_id`.** They arrive
   as a **`blocked`** event (`@on.blocked`) carrying `reason`
   (`no_spot` / `cell_occupied` / `level_required` / `unknown_type` / `nothing_here` / …) plus the
   target `type` and `pos`, so you can tell **which** build failed. `world.build(...)` itself
   **returns nothing**, so watch `on.blocked` rather than checking a return value. (A real spot
   builds even **under fog** — the world is generated deterministically; a build fails only when
   there's genuinely no live spot there, the type isn't unlocked, the cell's taken, etc.)
4. **Guard energy for the whole ROUND TRIP, not just the way out.** A robot can reach a far target
   and then be too drained to get home, dying mid-flight. Before a long flight require
   `energy ≥ dist(here→dest) + dist(dest→nearest pad) + margin`. Pads are the **Base**, active
   **Flying Stations**, and **Charging Towers**. (The starter parks robots instead of flying
   them, so this is the first thing to get right once you DO start moving.)
5. **After the early levels the Base stops accepting raws — it wants PRODUCTS.** Read
   `buildings.base.quest`: once it asks for products, raws pile up in Storage and haulers can
   freeze holding undroppable cargo unless you've built the processor chain. Cap how much of each
   item you bank, **harvest processor outputs even before a downstream consumer exists** (a full
   output store stalls the processor), and always give an idle robot something to do.
6. **`store` (city-wide) and the world persist across code reloads; module globals reset.** A
   design change won't retroactively apply if an old decision is cached in `store` (or baked into a
   building / a robot's `memory`). Detect stale state on load and migrate or rebuild it.
7. **Full Storage can dead-lock the fleet — never park a robot holding cargo it can't drop.**
   Every `@on.idle` handler must issue a command; a robot left parked (charge/wait) while still
   holding undroppable cargo **never re-enters your task selection** and is stuck for good. Don't
   make Storage the only drop target: if it's full, fall through to the **next valid sink** —
   the **Base quest item → a processor input that needs it → any Storage with room**. Storage has
   a fixed cap, so cap how much of each item you bank, harvest processor outputs even without a
   downstream consumer, and add Storage/Warehouse capacity *before* you hit the ceiling. A couple
   of robots frozen on undroppable cargo can stall the **entire** city (no hauling, no base-feeding —
   the activity log simply goes **quiet**). (This is the full-Storage flavour of #5 above: same
   freeze, different trigger.)
8. **You can dig yourself into an unrecoverable raw shortage.** A Mining building costs a raw
   (ore); a spot is **finite** and eventually depletes (`spot_depleted`). If stored ore drops
   below one mine's cost *before* a replacement is up, you can build **neither a mine nor robots**
   (both cost ore) → a permanent, self-reinforcing deadlock (fleet decays, chain starves, no ore
   ever returns). Defend proactively: (1) **reserve** ~one mine's worth of each raw nothing else
   may spend; (2) **replace a mine on `spot.remaining` getting low, not on your stockpile getting
   low** — so you still have the raw to fund the replacement; (3) keep the fleet's **type mix
   bounded** (e.g. cap mechanics) so long-lived specialists don't crowd out the haulers you need to
   recover; (4) last-ditch, `world.destroy` a spent mine to reclaim its ore as a `.recoverable`
   store — but only helps if a robot then hauls that away.
9. **Local dev tips.** `print(...)` shows in `robocity-sim` stdout — and now so does `r.log(...)`.
   `store` values must be **JSON-serializable** (a `set` raises a clear error naming the key).
   `r.id` is a **string** like `"r1"`, not an int.

## Constraints — read before editing

- **Sandbox.** Your code runs in a restricted sandbox. You may `from simcode import …` and use
  a normal-Python subset, but **no file/network/process access, no arbitrary imports, no
  `eval`/`exec`/`open`**. Keep helpers in `lib/` and import them normally.
- **Handlers must be fast.** Each handler has a tight CPU/time budget per invocation. Do a
  little work and return; don't loop for long or block.
- **State:** module-level globals persist while the process runs but **reset on a code reload**.
  For state that must survive a push, use `store` (city-wide) or `r.memory` (per robot).
- **Determinism:** don't rely on wall-clock or randomness; the world is seeded and replayable.
- **The client library is provided** by the platform — do **not** `pip install simcode` or vendor it.
  (`./setup.sh` installs the *local test tooling*, which carries its own copy for offline runs;
  that's separate from what your city runs on the server.)
- **You cannot reset the world from code.** Resetting a city (wiping it back to tick 0) is a
  **destructive, owner-only action available ONLY in the web dashboard** (the Reset button) —
  there is no code-level or MCP reset. Your code influences the world only through robot/world commands.

## Working in this repo with Claude Code

- **Commit straight to the default branch — never a feature branch, never a PR.** The city only
  hot-reloads from this repo's **default branch** on push; work parked on another branch or an
  unmerged PR **never deploys**. So the loop is: edit `main.py` → run the local check → commit to
  the default branch → push → **resync**. Don't create branches, don't open PRs.
- **After the push completes, trigger a resync for this city** — with **the platform's MCP
  `resync` tool for your city**. It is the last step of the loop above, not an optional extra.
  Why: a push is normally delivered to the platform by a notification from GitHub, but that
  delivery is **not guaranteed**, and the automatic catch-up that recovers a missed one only runs
  **periodically** — so without a resync you can push and then wait, with no clear sign whether
  anything happened. A resync makes the new code take effect **immediately**. Do it only for
  changes that affect the **running code** (`main.py`, `lib/`) — a docs-only or `issues/`-only
  commit doesn't need one. (Resync just re-pulls this repo and reloads the code; it never resets
  your world.)
- The thing to improve is the **strategy** in `main.py` (and `lib/`). The world is fixed, so
  better code = a better city.
- **Iterate with the local check:** run `robocity-sim run main.py` after every edit (it
  runs your controller against the real engine — see the top of this file), then confirm on the
  live city + logs after a push (or via the platform's MCP tools).
- High-leverage improvements over the starter:
  - **Bootstrap + climb the chain.** Put mines on **all four raws** (crystal and carbon feed the
    glass/coke branches), then stand up the **processor chain** the current quest tier needs. The
    quest is **product-based and seed-generated** — read `buildings.base.quest.required` rather
    than assuming, since your world's ladder is its own. Past L1 it will want processed goods, so
    you must run the chains to level up. React to `resource_produced` to keep haulers pulling finished goods, and to
    `production_blocked` to unstall a processor (feed its input or clear its output). When a mine's
    spot runs dry (`spot_depleted`) build a **replacement** so the chain never starves.
  - **Replace the aging fleet.** Every robot **expires** by cumulative flight distance
    (`robot_expired`), so a fleet left un-replaced dies out. Watch `r.life_remaining` and keep a
    Flying Station stocked with **ore + metal** to `build_robot(type, …)` steadily — pick the
    **type** for the job (haulers for bulk logistics, scouts/rangers for far exploration, mechanics
    for maintenance). This is a steady-state cost, not a one-off.
  - **Keep the factories serviced.** T2/T3 processors **wear** (`condition` runs full→empty): watch for
    `maintenance_needed` (below the halfway mark) and `building_stopped` (halted at empty), and route a **mechanic**
    — load it with metal, fly it to the worn building, and `repair()` — before productivity
    collapses. At scale a single processor can't clear a big product quest without repair.
  - **Respect the unlock ladder.** Only what the current Base level **unlocks** is buildable
    (`buildings.base.unlocks`; a locked type → `level_required`). L1 gives basics + builder + T1;
    L2 unlocks T2 + hauler/scout/mechanic; L3 unlocks T3; L4 unlocks upgrades +
    heavy_hauler/ranger. Plan the order you climb.
  - Recharge robots **before** they run dry (energy-death is avoidable) — build **Flying Stations**
    / **Charging Towers** near your frontier as charging pads. Add **Storage** / **Warehouse** as
    buffers, and `world.destroy` a spent building to reclaim its materials. Watch the tension: a
    Flying Station's store pays for robots (raws), the Base's store pays for quests (products), and
    every tier competes for the same haulers — while the fleet and factories both keep decaying.
- **The game is purely event-driven — do NOT use an `on.tick` polling loop.** Build around
  **`on.idle`**: it fires precisely when a robot needs a command. The golden rule: **every
  handler must issue the robot's next command** (fly / haul / `world.build` / `charge`), so a
  robot is never left idle with no future event to wake it. (And since `idle` re-fires while a
  robot stays free, a robot is never permanently stuck.) If a code path would leave a robot
  with nothing to do, fly it into unexplored ground instead — flying reveals new map. That
  single discipline is what keeps the city growing without any polling.

## Report back — turn each session into a forum post

The platform has a small **community forum** (browse it at `/forum`; posts are written through the
platform's **MCP** tools). Treat reporting as part of the job: **when a working session wraps up —
or while you monitor the city grow — analyse what happened and file a forum post.** Good reports
are how the game gets better.

**First, interview your human — thoroughly.** Before posting, ask targeted questions and keep
asking follow-ups:
- What were they trying to achieve, and did it happen?
- What surprised or confused them (in the API, the docs, or the sim)?
- What was missing — and **what would they want next** (features, quests, mechanics)? Capture
  *ideas*, not just bugs.

**Then file a forum post per distinct issue or idea** via the MCP forum tools, in this format so a
post is easy to reproduce and close:
- **kind:** `bug`, `idea` (features/quests go here), or `question`.
- **city (required):** at least the **city slug** (plus its type / relevant state if useful).
- **for a `bug`:** what you did → **what you saw** → **what you expected to see** → repro steps →
  how often it happens.
- **for an `idea`/quest:** the proposal + why it matters + how it would play.

Never file a bare "it did not work" — always **slug + saw + expected + repro**. Report both real
bugs *and* future ideas: the forum is where the roadmap comes from.

**If this doc (or the game's docs) describes something that DOESN'T actually work, that IS a bug —
file it.** A documented event / command / read-model field / mechanic that misbehaves, or any
behaviour that doesn't match what's written here, is a **platform bug** — not your mistake, and not
something to silently work around. File a `bug` post so it gets fixed or the doc gets corrected.

- *Example.* This doc says the city-wide **`store` persists** across code reloads, container
  restarts, and resync — only a world **reset** clears it. So if you set `store["x"] = 1`, push a
  change (or resync), and `store` comes back **empty** with no reset, that contradicts the doc →
  file it: `create_forum_post(kind="bug", city="<slug>", title="store empty after resync",
  body="set store['x']=1 → pushed a no-op change → store came back {}; expected it to persist per
  CLAUDE.md")`.

**After you file, MONITOR for an answer — a post isn't done when you submit it.** Check back with
`get_forum_post(id)` (or `list_forum_posts`) for a maintainer **reply** or a `resolved` flag, and
relay the answer to your human. (Only the MCP forum tools can post/read — your robot code / the client library
cannot touch the forum.)

### When MCP isn't enough — file from the repo with an `issues/` folder
MCP is best for **quick ideas and small bugs**. For a **complicated bug** (you need to attach a
repro script, logs, screenshots — several files) **or when the MCP forum tools themselves aren't
working**, commit an issue folder to this repo instead:

```
issues/
  store-empty-after-resync/     # one folder per issue (kebab-case name)
    README.md                   # the write-up (required) — same slug + saw + expected + repro
    repro.py  logs.txt  ...     # any other files = evidence
```

On your next push the platform scans `issues/` and creates one forum post per folder. Conventions
(no frontmatter needed): the post **title** is the README's first `# heading` (else the folder
name); the **kind** is `bug` by default, or `idea`/`question` if you name the folder `idea-…` /
`question-…`; every non-README file becomes a linked **evidence** attachment. It's **one-way**
(repo → forum) and **idempotent** — the same folder won't double-post on later pushes. A push that
only touches `issues/` **does not restart your city**, so filing a bug never interrupts the robots.
