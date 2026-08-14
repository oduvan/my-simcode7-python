"""SimCode city controller — a MINIMAL starting point.

This starter does nothing. That is deliberate, and it is the safest possible
starting state: your robots stay parked at the Base and wait for orders.

WHY PARKED, AND NOT EXPLORING

A robot wears out two ways, and only one of them is avoidable:

  * ENERGY — flying drains the battery. Run it to zero mid-flight and the robot is
    destroyed along with whatever it was carrying. You can avoid this by charging
    in time (`r.charge()` on a charging pad).
  * LIFESPAN — every robot also has a maximum CUMULATIVE flight distance
    (`r.life_remaining` / `r.life_max`). Fly far enough, over any span of time, and
    it simply expires. Nothing prevents this. The only answer is to build
    replacements before the fleet ages out.

A robot that never flies spends neither. So a parked city is stable: leave it for a
day and it is exactly as you left it, ready for your code. A starter that flew
around would look busier, but it would burn lifespan for nothing and eventually
leave you with no robots at all — and a city with no robots cannot act, because
every recovery path is a robot command. That is a real dead end, not a setback.

So: nothing here moves until you make it move.

WHAT TO DO NEXT

`@on.idle` fires whenever a robot is free and needs its next order — and it keeps
firing every few ticks while the robot stays idle, so a robot is never stranded.
That is your main loop. Decide what the robot should do, and issue ONE command.

The commands are on the robot handle:

    r.move_to(x, y)     fly in a straight line to a float position (reveals the map)
    r.pick_up(item, n)  take from the building on the robot's cell
    r.drop(item, n)     release into the building on the robot's cell
    r.charge()          refill the battery on a charging pad

and on the world:

    world.build("mining", x, y)   place a self-building construction site

A first step that is genuinely useful: find a resource spot near the Base, put a
mine on it, and haul what it produces to the Base to raise your level. Read
CLAUDE.md for the whole game and the full API — the goal, the buildings, the
supply chain, and what each event carries.

One thing worth knowing before you write the loop: the Base ladder is generated
from your world's seed, so what a level asks for differs between cities. Read
`buildings.base.quest.required` and react to it rather than hardcoding items.
"""

from simcode import on, robots


@on.idle
def act(e):
    """Called whenever a robot is free. Right now it does nothing on purpose.

    `e.robot_id` is the robot that needs an order; `robots[e.robot_id]` is its
    handle. Issuing no command leaves the robot parked — safe, and costing nothing.

    Replace this with your strategy. For example, to send a robot to a resource
    spot and mine it:

        r = robots[e.robot_id]
        spot = r.nearest(kind="ore_spot")
        if spot and r.cell != tuple(spot):
            r.move_to(*spot)
        elif spot:
            world.build("mining", *spot)

    (That needs `world` imported too — `from simcode import on, robots, world`.)
    Mind the battery once you start flying: budget the WHOLE round trip, out and
    back to a charging pad, not just the outbound leg.
    """
    return
