# BML-lite: conditional, timed, phased missions, and mission directives

The BML layer describes command intent. It does not replace sensing, FoW, local target selection,
terrain movement, weapon physics, or DoctrineEngine. Entity-target missions continue to pursue
perceived Tracks rather than live enemy ground truth.

## 1. Existing conditional mission trigger

`conditions` are reactive triggers evaluated while an order is active. All conditions must be true.
When they become true, `on_true` replaces the active order. Supported paths include:

- `sim.time`
- `self.loss_ratio`, `self.strength_ratio`
- `self.personnel`, `self.initial_personnel`
- `self.equipment`, `self.initial_equipment`
- `self.state`
- `self.time_in_order`
- `self.capability.<CAPABILITY>`
- `self.distance_to_objective`, `self.at_objective`
- `self.enemy_count_near`: count of current actionable Tracks inside the configured radius, using estimated positions. The legacy name is retained, but it no longer reads live enemy positions or survival.

Conditions are validated when the BML is loaded, before any live order changes: `lhs` must be one
of the paths above, numeric paths need a finite numeric `rhs`, `self.capability.*`/`self.at_objective`
need `true`/`false` with `==`/`!=`, and `self.state` needs a state-name string. Coordinates must be
finite and inside the scenario world.

Operators: `<`, `<=`, `>`, `>=`, `==`, `!=`.
`on_false` is evaluated when an order completes, before that order's objective and elapsed-time
metadata are cleared. Unknown branch tasks, condition paths, directives, and out-of-world
coordinates are rejected while loading the BML, before existing orders are replaced.

Example: defend until 50% total formation loss, then withdraw to Rally Point Alpha.

```json
{
  "unit": "B-INF_PLT-1",
  "task": "HOLD",
  "conditions": [
    {"lhs": "self.loss_ratio", "op": ">=", "rhs": 0.50}
  ],
  "on_true": {
    "id": "RALLY-ALPHA",
    "task": "WITHDRAW",
    "destination": [800, 1200]
  }
}
```

The condition does not artificially cause the loss. It only observes normal runtime state.

## 2. Timed mission start

Use `start_at_s` (alias `not_before_s`) for absolute scenario time.

```json
{
  "unit": "B-INF_PLT-1",
  "task": "MOVE_TO",
  "destination": [1600, 1100],
  "start_at_s": 300
}
```

Until T+300 the formation does not execute the movement order. Local doctrine may still react to
contacts unless mission directives prohibit the relevant reaction.

## 3. Deadline / latest desired arrival

Use `deadline_s` (alias `complete_by_s`). A deadline never increases movement speed and never
teleports a unit. If the mission is unfinished at the deadline, the simulator logs
`ORDER_DEADLINE_MISSED`. Without `on_deadline`, the formation continues trying.

```json
{
  "id": "RALLY-BRAVO",
  "unit": "B-INF_COY-1",
  "task": "MOVE_TO",
  "destination": [1800, 1500],
  "deadline_s": 600,
  "on_deadline": {
    "id": "MISSED-BRAVO",
    "task": "HOLD"
  }
}
```

This makes schedule feasibility an outcome of mobility, terrain, interference, and combat rather
than a scripted speed bonus.

## 4. Phase-organized BML

`phases` organize mission sequences while compiling into the same normal per-unit Order queue.
There is no second combat/phase engine.

```json
{
  "side": "BLUE",
  "replace_existing_orders": true,
  "phases": [
    {
      "id": "PHASE_I_ASSEMBLY",
      "start_at_s": 0,
      "deadline_s": 300,
      "missions": [
        {"unit": "B-INF_PLT-1", "task": "MOVE_TO", "destination": [900, 1000]},
        {"unit": "B-INF_PLT-2", "task": "MOVE_TO", "destination": [950, 1050]}
      ]
    },
    {
      "id": "PHASE_II_ATTACK",
      "start_at_s": 300,
      "missions": [
        {"unit": "B-INF_PLT-1", "task": "ATTACK_POSITION", "destination": [1800, 1500]},
        {"unit": "B-INF_PLT-2", "task": "ATTACK_POSITION", "destination": [1850, 1550]}
      ]
    }
  ]
}
```

A unit proceeds through its own queued phase missions. `start_at_s` is the synchronization gate
when several formations must not begin the next phase before a common scenario time. Phases do not
currently impose a global "all units must finish Phase I" barrier; this avoids artificial forced
synchrony. A future explicit phase-trigger manager can be added if a research scenario requires it.

Phase-level `start_at_s`, `deadline_s`, and `directives` are inherited by missions unless a mission
overrides them.

For a scenario with load-time `aggregations`, the parent formation is created before BML is
loaded. Address the active parent ID in BML; inactive source children cannot be commanded until
they are deaggregated. Scenario-embedded commands for a load-time aggregate belong in that
`aggregations[]` entry's `orders`; embedded orders on children that become inactive are rejected.

## 5. Doctrine profile versus mission directive

These are deliberately separate:

- **Doctrine profile**: standing local tactical reaction policy assigned to a unit in the scenario.
- **Mission directive**: temporary command-specific override carried by the active BML mission.

Scenario example:

```json
{
  "id": "R-INF_PLT-1",
  "side": "RED",
  "type": "INF_PLT",
  "doctrine_profile": "STAND_FAST",
  "pos": [2400, 1800]
}
```

Profiles are defined in `config/doctrine_profiles.json`. Profiles are not implicitly tied to BLUE,
RED, faction, or branch, so two same-side/same-type units may select different profiles.

Mission-specific stand-fast example:

```json
{
  "unit": "R-INF_PLT-1",
  "task": "DEFEND_AREA",
  "center": [2400, 1800],
  "radius_m": 100,
  "directives": {
    "hold_at_all_costs": true,
    "allow_withdrawal": false,
    "allow_break_contact": false
  }
}
```

Supported local-reaction directives currently include:

- `hold_at_all_costs`
- `allow_withdrawal`
- `allow_break_contact`
- `allow_artillery_displacement`
- `allow_indirect_fire_dispersion`

These directives override normal local reaction policy, not physical reality. A unit with no usable
weapon can be ordered not to withdraw, but it does not magically regain firepower.

## 6. Design priority

Runtime precedence is intentionally:

1. physical/capability constraints,
2. explicit active mission directives,
3. selected doctrine profile,
4. historical generic engine defaults.

BML remains mission intent; DoctrineEngine remains local tactical reaction. New doctrine profiles or
mission directives should not require side-specific hard-coded branches in the simulation engine.

## Engagement-range mission directive (v48)

A mission can temporarily override the unit doctrine's positioning policy:

```json
{
  "unit": "B-INF_PLT-1",
  "task": "DESTROY_UNIT",
  "target": "R-INF_PLT-1",
  "directives": {
    "engagement_range_policy": "COMBINED_ARMS",
    "engagement_range_fraction": 0.90
  }
}
```

`STANDOFF` preserves the historical behavior: the formation stops when its longest relevant operational
weapon reaches the perceived target. `COMBINED_ARMS` permits long-range weapons to fire while the
formation continues closing until the shortest relevant operational weapon is inside its selected
fraction of maximum range. `BALANCED` uses the midpoint. This changes maneuver/positioning only; weapon
range, hit probability and target eligibility remain unchanged.


## Target identity vs. target location (v48.5)

Enemy unit identity does **not** reveal its location. `ATTACK_UNIT` / `DESTROY_UNIT` may prioritize a
known Track for the named target, but if no own/shared Track exists the formation does not read the
scenario Ground Truth position and does not move toward that enemy automatically. An optional explicit
planning/intelligence reference can be supplied by the BML itself:

```json
{
  "unit": "B-INF_PLT-1",
  "task": "DESTROY_UNIT",
  "target": "R-RIFLE_SQD-1",
  "target_position": [210, 400]
}
```

`target_position` is commander-provided information, not a live link to the target. Once a Track exists,
perceived Track / last-known Track position takes precedence.

For normal combat orders where enemy identity is unknown, prefer coordinate-based `ATTACK_POSITION`:

```json
{
  "unit": "B-INF_PLT-1",
  "task": "ATTACK_POSITION",
  "destination": [210, 400],
  "persistent": false
}
```

This order requires no enemy unit ID. The formation advances toward the commanded coordinate and may
engage any actionable hostile contact encountered through the normal Track/Belief pipeline. With
`persistent: false`, reaching/clearing the objective allows the next queued mission to begin; with the
default `true`, the formation remains at the objective searching/holding for further contacts.

## Mounted / Dismounted transport orders (v49)
Transport behavior is capability-driven. A carrier is any formation with equipment whose resolved
platform metadata has `passengers > 0`; vehicle names are not hard-coded.

### Organic mechanized infantry
```json
{"unit":"B-MECH-1","task":"DISMOUNT","destination":[420,310]}
{"unit":"B-MECH-1","task":"MOUNT"}
```
`DISMOUNT` may optionally include a destination. The carrier moves there, waits the configured
dismount time, then transfers all personnel elements marked `metadata.dismountable=true` to an
organic child infantry formation. Vehicle crews remain with the carrier. `MOUNT` rendezvous with
that child and transfers the surviving elements back.

### Boarding non-organic friendly infantry
```json
{"unit":"B-RIFLE-SQD-2","task":"BOARD","carrier":"B-BRADLEY-EMPTY-1"}
{"unit":"B-BRADLEY-EMPTY-1","task":"DISEMBARK","passengers":"ALL"}
```
The boarding unit retains its own identity and TO&E while embarked; it is removed from independent
movement/combat until disembarked. Boarding requires same side, sufficient free seats, an operable
carrier crew, physical rendezvous within `embark_radius_m`, and the loading delay. Seats are assigned
to individual vehicle slots inside an aggregate carrier formation and stored in runtime metadata.

`BOARD` uses the known friendly carrier position. It does not create any enemy Ground Truth knowledge.

## Timed execution applies to transport and combat orders

`start_at_s` is a common absolute scenario-time gate, not a MOVE-only option. For example:

```json
{
  "unit": "B-MECH-1",
  "task": "DISMOUNT",
  "start_at_s": 300
}
```

The unit may react under its local doctrine before T+300, but the scheduled DISMOUNT itself cannot
execute before simulation elapsed time reaches 300 seconds. The same rule applies to BOARD, MOUNT,
ATTACK_POSITION and other compiled orders.


## v49.2 terrain / elevation extension
- `LAKE` polygon: non-amphibious vehicles/equipment cannot enter. FOOT formations may swim at a severe speed penalty; on first water entry, crew-served/heavy machine guns and anti-armor weapons are dropped and logged (`SWIM_HEAVY_WEAPONS_DROPPED`).
- `BUILDING` polygon: FOOT formations may occupy only via explicit `ENTER_BUILDING` / `EXIT_BUILDING` boundary-crossing orders during runtime; ordinary movement routes around operational footprints. Engine-level occupancy is intentionally unlimited regardless of echelon/personnel count, leaving force-to-building constraints to higher-level BML/COA generation. Operational buildings hard-occlude visual/direct-fire rays unless elevation clears the roof. Occupants are harder to detect/hit from outside. Structure-capable direct weapons and indirect-fire impacts reduce structural integrity; collapse destroys occupants.
- `ELEVATION` polygon: authored contour/plateau elevation in metres above the 0 m map datum. Overlapping contours use the highest value. Grade affects path passability and movement speed. Observation/direct-fire ray height is compared with building roof height, so elevated observers may see/fire over lower obstacles.
- Editor tools: `L` Lake, `K` Building, `V` Elevation; comma/period changes current contour elevation by 10 m; semicolon/apostrophe changes current building height by 1 m.
- Elevation is currently vector/step-contour rather than a continuous DEM. It is deliberately isolated behind `TerrainModel.elevation_at()` for later bilinear/raster replacement.

### Attack a known building
```json
{"unit":"B-INF-PLT-1","task":"ATTACK_STRUCTURE","target_structure":"BLD1"}
```
Only direct weapons carrying the `STRUCTURE` target tag / `structure_capable` metadata participate. The order conveys knowledge of a static mapped structure, not an enemy-unit location. Artillery may include building IDs in the existing `STRIKE_INFRASTRUCTURE` target list.

## SECURE_AREA / bounded area defence (v49.4)
`SECURE_AREA` is a reactive bounded-defence mission. It occupies a center/radius (or polygon), engages perceived contacts, may manoeuvre/pursue only while the Track estimate remains inside the authorised pursuit boundary, and re-centers after contact. It never uses enemy Ground Truth.

```json
{
  "unit": "B-TANK-1",
  "task": "SECURE_AREA",
  "center": [300, 250],
  "radius_m": 180,
  "engagement_radius_m": 220,
  "pursuit_radius_m": 180,
  "return_to_center": true
}
```

`DEFEND_AREA` remains available. Set `pursue_within_area: true` to give it the same bounded pursuit behavior. Direct fire may reach a valid target outside the movement boundary if the unit already has a valid Track/LOS; the unit itself will not leave the pursuit boundary merely to chase it.

## BUILD_BARRICADE (v49.7)

A foot infantry/recon/special-operations formation may construct a preallocated 10 m HESCO MIL1-class barrier when its scenario metadata has `barricade_limit > 0`. The default is zero; this capacity represents barrier material/material-handling support allocated to the formation, not a permanently detached engineer crew.

```json
{
  "unit": "B-INF_PLT-1",
  "task": "BUILD_BARRICADE",
  "position": [420, 310],
  "heading_deg": 30
}
```

`heading_deg` is the outward/facing normal measured counter-clockwise from east. The protected side is the opposite side. Length is fixed at 10 m. Default construction time is 1200 s and may be overridden with `construction_time_s` for scenario calibration. The unit moves to the site, constructs the barrier, then consumes one of its allocated barrier sections.

## Explicit building entry / exit

Operational BUILDING footprints are hard movement obstacles for ordinary `MOVE_TO`, `ATTACK_POSITION`, pursuit, and retreat routing. A dismounted/FOOT formation crosses a building boundary only through an explicit building mission:

```json
{"unit":"B-INF_PLT-1","task":"ENTER_BUILDING","target_structure":"BLD1"}
```

`destination` may optionally identify a point inside the building; otherwise the building center is used.

```json
{"unit":"B-INF_PLT-1","task":"EXIT_BUILDING","target_structure":"BLD1","destination":[420,310]}
```

`EXIT_BUILDING.destination` must be outside that building. The simulation engine deliberately does **not** enforce building occupancy by echelon, personnel count, or authored capacity. Such constraints belong in the higher-level BML/COA generation policy if a scenario requires them.

## Assigned watch / guard bearing (v49.9)
For a halted/defending formation, an order may include `watch_heading_deg` (legacy alias `facing_deg`) to assign its principal observation/engagement bearing. The value is a desired bearing, not an instantaneous teleport: the formation slews toward it according to its sensor/orientation profile. Moving formations normally orient toward the movement axis; actionable targets, shared situational cues, or incoming-fire cues may temporarily take priority according to simulation doctrine.
