# My SimCode City (Python)

This repo controls a city in **SimCode — Robot City Builder**. `main.py` is one Python
program that drives the whole robot fleet; **push to the default branch and the platform
hot-reloads** it into your live city.

**The goal:** robots start empty. Pick up materials from the starting **Storage**, build
**mines** on the four raw resources (ore, metal, crystal, carbon), then stand up a **factory
chain** — smelters/mills/labs that autonomously refine raws → basics → parts → advanced goods —
and haul the **products** to the **Base** to complete its **quest**. Each quest cleared **levels
the Base up** (your score) and **unlocks the next tier** of buildings + robot types; the quest is
product-based (L1→L2 raws, then part → module → module+frame). Build a **Flying Station** to
recharge robots and manufacture more of them — robots come in **level-gated types**
(`build_robot(type)`) and cost **raw ore + metal**. Two ongoing pressures make it a *living
economy*: every robot **expires** after flying a fixed distance (so keep building replacements),
and **T2/T3 processors wear down** and need a **mechanic** to `repair` them. **This starter does
none of that** — it only keeps the robots alive and explores the map; building and maintaining the
whole economy is your job.

> **Balance lives in the config, not in these docs.** The exact numbers (cargo, speed, lifespan,
> costs, recipe amounts, store caps, quest quantities, wear/repair rates, energy, start capital)
> are **tuned per city and change over time**, so don't copy magnitudes out of the docs. Read them
> live — from handles like `building.recipe`, `buildings.base.unlocks`, `r.life_remaining`,
> `building.storage.capacity` — or from the MCP tool **`get_world_config`** for the full picture.
> `CLAUDE.md` describes the mechanics + API; the config is the source of truth for numbers.

- **Edit `main.py`** to change how your robots behave (pick up, place mines, haul to the Base,
  charge, build robots at a Flying Station).
- **Push** → your city updates in real time at its live page.
- No setup, no manifest, no dependencies to install — the `simcode` client library is provided by the
  platform at runtime.

New here? Open **[`CLAUDE.md`](CLAUDE.md)** — it explains the game, the full client library (events +
commands + read model), the rules, and the sandbox constraints. It's written so
[Claude Code](https://claude.com/claude-code) can help you write better robot code.

```
main.py        # your controller (the only thing that runs)
setup.sh       # one-command setup for local testing (run this first in a new environment)
lib/           # optional helper modules main.py imports
issues/        # optional — commit a bug/idea folder here and it posts to the forum
CLAUDE.md      # the client library + game reference
```

> **Hit a bug?** Small stuff → ask your assistant to file it via the MCP forum tools. Something
> **complicated** (needs a repro + logs), or MCP not working? Commit an `issues/<name>/` folder
> (a `README.md` write-up + any evidence files); the next push turns it into a forum post. Always
> **commit to the default branch — no feature branches, no PRs** (the city only deploys from the
> default branch).

## Test it locally before you push

You can run your `main.py` against the **real game engine** on your machine — the exact
engine the server runs, downloaded on demand — so you can check "does this actually work
if I push it now?" in seconds:

```bash
./setup.sh                  # one command: installs the test tooling + warms the engine cache
robocity-sim run main.py    # run vs the real engine
```

`./setup.sh` is the **only** setup step, and the first thing to run in a fresh environment.
It is idempotent — re-run it any time; when everything is already installed it finishes
immediately, and if something is missing it says so. The engine is downloaded + cached on
first use (no build step, no token), which `setup.sh` does for you, so later runs are
instant. Read the summary — `handler errors` must be **0**. See [`CLAUDE.md`](CLAUDE.md)
for full usage and options (`--ticks`, `--seed`, `--json`).

After you push a change that affects the running code, **resync the city** (the platform's
MCP `resync` tool) so it deploys immediately instead of waiting for a push notification.

Have fun — the map is the same for everyone, so it's all about your code.
