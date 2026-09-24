
# MNS / COA Wargame Prototype

This repository is a research-oriented tactical M&S prototype for closing the loop between
**COA generation (human / BML / LLM / RL)** and an executable **digital-twin / wargame engine**.
The intended scale is primarily platoon/company entities used to compose battalion through
division-level engagements, while retaining enough internal TO&E detail to represent losses,
capability degradation, ammunition, sensors, and local tactical reactions.

## Design philosophy — read this before changing the code

A future developer or LLM should preserve these boundaries unless there is a strong reason not to.

1. **Formation ≠ HP bar.** A unit is a composite TO&E formation containing countable personnel,
   equipment, crews, weapons, ammunition and capabilities. Damage changes those concrete elements;
   aggregate combat strength is derived from survivors.
2. **Ground truth ≠ what a commander knows.** The simulation owns true positions/states, but tactical
   decisions and weapon employment must use Fog-of-War `Track` objects. Sensors, C2 sharing,
   counter-battery radar, staleness and uncertainty create those tracks.
3. **COA/order logic ≠ tactical reflexes.** BML/LLM/RL should normally issue mission-level intents
   such as ATTACK/DEFEND/MOVE/HOLD. `DoctrineEngine` handles local reactions such as breaking contact
   when infantry has no anti-armor capability or displacing artillery under close direct contact.
4. **Weapon effect comes from surviving capability.** A weapon belongs to a formation element.
   If the element is destroyed, required crew is lost, or finite ammunition reaches zero, that
   capability disappears automatically. Other surviving weapons remain usable.
5. **Multi-entity combat is engagement-based but losses remain compositional.** Local N:M engagements
   group interacting formations. A formation keeps a primary target cue for orientation/mission context,
   but each surviving FormationElement/weapon stream independently selects an actionable enemy Track.
   Fire allocation uses diminishing returns to avoid unrealistic whole-battalion pile-on against one
   platoon when several valid targets are simultaneously available. Damage remains element/component based.
6. **DE + ABM hybrid.** Continuous movement/local behavior uses time stepping; delayed consequences
   such as damage, track sharing, counter-battery processing and future effects use the event queue.
7. **Data-driven extension over hard-coded subclasses.** Prefer adding/editing unit types, elements,
   weapons, sensor parameters and doctrine configuration through validated scenario/model data.
   New Python modules should expose small stable interfaces rather than adding special cases to UI code.
8. **UI is presentation, not simulation truth.** `main.py` should query the engine; combat/sensor/doctrine
   decisions must remain headless and testable without Pygame.
9. **LLM extension path.** Future LLM-generated systems should preferably produce validated JSON/DSL
   definitions (TO&E + capabilities + sensors + doctrine/event parameters), not arbitrary executable code.
10. **Demo numbers are synthetic tuning values.** Weapon probabilities/ranges in this prototype exist
    to exercise the engine architecture. Replace them with validated scenario data when doing calibrated studies.

## Current module responsibilities

```text
main.py                 Pygame tactical UI only
mnsim/model.py          Core data model: Unit, FormationElement, WeaponModel, Track, Order
mnsim/simulation.py     Orchestrator, time loop, movement, engagements, FoW/C2/event integration
mnsim/combat.py         Direct-fire compatibility, target choice, ammunition use, fire resolution
mnsim/indirect_fire.py  Artillery launch/impact model, CEP/dispersion, spatial area effects
mnsim/fire_control.py    Delayed fire-request/FDC/gun-preparation/reload pipeline
mnsim/terrain.py         2D terrain/mobility: OPEN/ROAD/RIVER/BRIDGE
mnsim/damage.py          Equipment/component damage states and mobility/firepower kills
mnsim/config.py          External JSON config loading and recursive override support
config/defaults.json     Default tunable timing/behavior parameters
config/toe_templates.json External editable TO&E/unit templates
config/terrain_demo.json  Scenario terrain geometry
config/artillery_doctrine.json Artillery fire-pattern/doctrine profiles
mnsim/belief.py         Persistent enemy-order-of-battle belief above short-lived firing tracks
mnsim/doctrine.py       Branch-aware local tactical FSM below COA/BML orders
mnsim/events.py         Discrete-event priority queue
mnsim/bml.py            BML-lite conditional orders / branch evaluator
mnsim/scenario.py       JSON -> trusted engine model factory
scenarios/demo.json     Demo world, TO&E, weapons, parameters and initial COA
tests/                  Headless regression tests
```

When adding a new weapon/system, first ask: **is this new data, a reusable engine capability, or a
new tactical policy?** Put it in the scenario/model, combat/sensor engine, or doctrine module
respectively. Avoid one-off `if unit_name == ...` logic.

### Direct-fire local allocation (v42)

Direct fire is deliberately **not** a formation-wide single-target fire pool. `Unit.target_id` remains
a primary observation/mission cue. The engine's combat step calls `CombatResolver.fire_hybrid()`:
weapon streams that can reach a CLOSE (all-round awareness) contact pick among those contacts
independently, while the remaining streams stay on the formation's primary target.
`CombatResolver.fire_local()` is the fully decentralised variant (every stream chooses among all
actionable targets); it is kept as a library/experiment API and is not used by the default loop. The allocator preserves FoW Track
requirements, weapon range and compatibility, per-stream target locks/acquisition delay, and a configurable
mission-target preference. A saturation penalty distributes otherwise comparable fire streams across
multiple enemy formations. This changes target allocation, not calibrated weapon rates or damage mechanics.

Relevant defaults: `local_target_lock_min_s`, `local_target_switch_score_ratio`,
`local_fire_mission_target_bonus`, and `local_fire_saturation_penalty`.

## Run

```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
pip install -r requirements.txt
python main.py scenarios/demo.json
```

Development / tests (the exact versions verified by CI are pinned in `constraints.txt`):

```bash
pip install -r requirements-dev.txt -c constraints.txt
SDL_VIDEODRIVER=dummy python -m pytest -q      # headless; long TDG3/engagement matrices are opt-in
ruff check .                                    # defect-level lint configured in pyproject.toml
```

Scenario, terrain and BML files are treated as untrusted data and validated at load time
(`mnsim/validation.py`): JSON `NaN`/`Infinity` are rejected, coordinates must be finite and inside
the world (plus a 25% margin), conditions are type-checked, and file references must stay inside
the scenario's folder tree or this project (extra roots: `MNSIM_RESOURCE_ROOTS`).

Controls: `SPACE` pause/resume; top-right buttons select `1x/2x/4x/8x/16x/32x`; `1/2/4/8` remain direct keyboard shortcuts and `[` / `]` step slower/faster; click a unit to inspect it; `L` writes a timestamped `logs/replay-YYYYMMDD-HHMMSS.jsonl` (never overwriting an earlier log). If the engine raises during a run, the view pauses and shows the error instead of exiting.

## Architecture

- `mnsim/model.py`: entity and weapon data models.
- `mnsim/events.py`: timestamped discrete-event priority queue.
- `mnsim/simulation.py`: continuous movement/ABM stepping + discrete combat/damage events.
- `mnsim/bml.py`: BML-lite order and condition evaluator.
- `mnsim/scenario.py`: JSON scenario loader.
- `main.py`: presentation only (Pygame). The simulation engine is UI-independent.

The engine therefore follows a DE + ABM hybrid pattern: movement and local behavior are advanced with a time step; firing/damage and future effects are scheduled through a discrete-event queue.

## Conditional order example

```json
{
  "id": "B1-ATTACK-ALPHA",
  "kind": "ATTACK",
  "params": {"destination": [5000,3300]},
  "conditions": [{"lhs":"self.loss_ratio","op":">=","rhs":0.40}],
  "on_true": {
    "id":"B1-RETREAT",
    "kind":"RETREAT",
    "params":{"destination":[2100,1800]}
  }
}
```

The branch is evaluated reactively after damage, so a unit can abandon the current mission immediately when the specified condition becomes true. The same interface can represent "hold at all costs" by branching to `HOLD` rather than `RETREAT`.

## Intended LLM/RL interface

Treat `scenario JSON + order JSON` as the action/control interface and `logs/replay.jsonl + periodic state snapshots` as observations. A later Gymnasium wrapper can expose:

- `reset(scenario)`
- `step(COA/order bundle)`
- observation: unit state, detections, losses, objectives, event log
- reward: mission score, losses, time, logistics, terrain/objective weights

Do not let an LLM generate executable Python modules directly inside a production simulator. Prefer a constrained schema/DSL for generated unit, sensor, weapon, doctrine, and event models, validate it, then instantiate trusted engine components.

## Next extensions

1. Terrain/raster/hex map layer and roads/elevation/LOS.
2. Sensor/contact model: detected, classified, identified, track confidence.
3. Hierarchical headquarters entities and command delay/communications.
4. Aggregation/deaggregation between platoon/company/battalion entities.
5. Logistics, ammunition, fuel, mobility kill, morale.
6. Fire support and delayed events (artillery/UAS/air support).
7. State snapshots + deterministic replay viewer.
8. BML XML/JSON adapter and Gymnasium/PettingZoo wrapper.
9. Schema-driven model factory for rapidly adding new equipment types.


## v0.2 branch/symbology update
Demo forces are Infantry, Armor (tank), and Artillery. The Pygame map renders simplified NATO APP-6/MIL-STD-2525-style unit frames: infantry crossed diagonals, armor ellipse, artillery center dot, with echelon amplifiers above the frame. Symbology code is isolated in `draw_nato_symbol()` for later replacement by a full APP-6 renderer.

## v0.3 close-combat / multi-entity update

The demo scenario is now 4 km x 4 km (`world.width_m`, `world.height_m`), and the UI reads map extent from the scenario rather than hard-coding it. The default view uses a square tactical map and 500 m grid spacing.

Direct-fire combat is no longer a set of independent nearest-target 1-v-1 duels. Infantry and armor form **local engagement groups** as connected components of opposing units within `combat.engagement_link_m`. Nearby friendly platoons are pulled into the same local fight, producing N-v-M engagements. Each shooter then selects among enemy members using branch-specific target priorities, distance, and current damage state.

Artillery is deliberately kept outside the direct local engagement component. If friendly direct-fire forces are engaged, artillery can provide `FIRE_SUPPORT` against enemy members of that engagement when they are within abstract weapon range. This separates maneuver entities from supporting fires and makes later command/support-request delays straightforward to add.

`branch_effectiveness` and `target_priority` are scenario-configurable abstract coefficients. They are placeholders for M&S experimentation, not real weapon-system performance data.

## v0.5 TO&E / composition combat model

v0.5 removes unit HP from combat resolution. A formation owns countable `elements` such as rifle personnel, an anti-armor specialist, tanks, artillery pieces, and crews. Loss events decrement those concrete elements. `loss_ratio` is derived from weighted surviving combat strength rather than an HP bar.

A weapon belongs to the element that provides it. If `at_specialist.count` becomes zero, the formation no longer has `ANTI_ARMOR`; rifle weapons only have the `PERSONNEL` target tag, so an infantry platoon without a surviving anti-armor element cannot damage an armor-only target. The demo values are synthetic engine-test parameters and are not intended as real-world weapon performance data.

Useful BML-lite conditions now include `self.loss_ratio`, `self.strength_ratio`, `self.personnel`, `self.equipment`, and `self.capability.ANTI_ARMOR`.

### Aggregation / deaggregation API

```python
company = sim.aggregate_units(
    "B-C-1", "B 1 Infantry Company",
    ["B-INF-1", "B-INF-2", "B-INF-3"], echelon="COY"
)

# Losses occur against source-prefixed elements inside the company.
# Later, split it back to the surviving platoons:
sim.deaggregate_unit("B-C-1")
```

The aggregated parent keeps each source element as `<child-id>:<element-id>`. Therefore casualties received while aggregated can be pushed back to the correct subordinate when the formation is split again. This is the basis for future reorganization rules such as "deaggregate after 50% combat-strength loss" or merging depleted platoons into an ad-hoc company/team.


## v0.5 CMO-style tactical command UI

The pygame front-end has been redesigned around a CMO-like operational picture while preserving the v0.4 TO&E/combat kernel.

- 4 km x 4 km scenario-configurable tactical map
- NATO/APP-6-inspired unit symbols
- Order vectors and current target/engagement links
- Selected-unit sensor and surviving weapon range rings
- Terrain-like background, contour lines, roads, stream and 500 m grid
- Bottom command panels: unit/TO&E status, ROE/tactical status, weapons & sensors, event log
- Mouse wheel zoom (1x-5x), right-button pan, HOME reset
- SPACE pause, 1x/2x/4x/8x/16x/32x simulation speed, L replay log

The terrain graphics are deliberately abstract placeholders. Terrain/LOS mechanics remain separate simulation work.

## v0.6 Fog of War
- Combat is no longer triggered from omniscient enemy coordinates.
- Every unit maintains its own local track database: DETECTED -> CLASSIFIED -> IDENTIFIED -> STALE -> LOST.
- Track position contains uncertainty; target selection and weapon-envelope checks use the estimated position.
- Sensor reports are shared to friendly units after a stochastic C2 delay, with reduced confidence.
- BLUE/RED tactical views hide ground truth. `B`, `R`, `G` switch BLUE, RED, and observer/God view.
- `1`, `2`, `4`, `8` select those speeds directly; `[` / `]` steps through 1x/2x/4x/8x/16x/32x. The top-right speed buttons allow direct mouse selection of all six speeds.

## v0.7: counter-battery radar + configurable artillery formation

Artillery is now a composition of independent TO&E elements rather than one generic
"artillery HP" object.  The demo `ARTY_PLT` contains **towed artillery + a
counter-battery radar** (with their crews).  The same schema can later define gun-only,
radar-only, self-propelled-only, or mixed formations by adding/removing elements.

When an `INDIRECT_FIRE` weapon fires, every operational hostile counter-battery radar
inside its configured range gets a stochastic trajectory-acquisition opportunity. A multi-round
salvo provides multiple acquisition opportunities, capped by the radar element's
`max_salvo_detection_opportunities` parameter so rounds from one salvo are not treated as
unlimited independent trials. A successful observation does **not** reveal ground truth. After
a configurable processing delay it creates a normal Fog-of-War `Track` with:

- estimated fire-origin position,
- position-error radius,
- confidence,
- CLASSIFIED/IDENTIFIED state,
- source = `COUNTER_BATTERY`.

Repeated successful observations refine the location estimate. Multiple usable trajectories
within one salvo also modestly improve the initial fire-origin estimate. The solution is then
shared through the existing delayed C2 track-sharing mechanism. Friendly artillery can use an
actionable counter-battery track for a `COUNTER_BATTERY` fire mission.

The demo radar parameters are intentionally synthetic/general M&S tuning values and are
stored in external TO&E/config data (`radar_range_m`, near/edge single-trajectory detection
probability, salvo-opportunity cap, position error, and processing delay), so they can be
replaced by scenario-specific validated data later. Public descriptions of real weapon-locating
radars do not provide enough information to treat these demo probabilities as calibrated system
specifications.

A counter-battery radar is operational only while the radar equipment survives and the
required radar crew is available.  If the `cb_radar` element reaches zero, pending
solutions are aborted and no new counter-battery detections are generated.

In the tactical UI, select an artillery formation to see the counter-battery radar range
ring and its ON/DISABLED status.  Counter-battery contacts appear as ordinary hostile
Fog-of-War contacts labelled `CB ORIGIN ...`, including confidence and position error.

Simulation speed controls support 1x/2x/4x/8x/16x/32x. At high acceleration the UI uses bounded simulation-time substeps (0.25 s by default) so discrete events, sensor scans, and chained fire-control stages are not delayed by a single large render-frame jump.

## v0.9 UI/navigation updates

- `F1`: open/close modal help. `ESC` also closes it. The help includes the range-overlay color legend and all map/view controls.
- Mouse wheel: cursor-centered zoom, from 1x to 12x.
- Right mouse-button drag: pan the tactical map. `HOME` resets the camera.
- Resizable window: map and information panels reflow dynamically. Wide windows use a 1x4 lower panel row; narrower windows use a 2x2 arrangement.
- Grid geometry: X and Y use one common metres-per-pixel camera scale, so a 500 m x 500 m grid cell is always rendered square regardless of window aspect ratio.
- `1`, `2`, `4`, `8`: direct simulation-speed shortcuts.
- `[` / `]`: step through 1x/2x/4x/8x/16x/32x; the top-right buttons select any speed directly.
- `B`, `R`, `G`: BLUE FoW, RED FoW, and ground-truth observer views.


## v0.9 persistent engagement fix

- ATTACK orders are persistent by default and no longer complete merely because the waypoint is reached.
- Attack behavior cycles through advance, engage, search-last-known-position, reacquire, and continue-attack states.
- Direct-fire engagement formation is weapon-envelope-aware; the former fixed 420 m link no longer cuts off tank/direct-fire combat that is still in range.
- Lost contacts are searched at their estimated last-known position using Fog-of-War data, not ground-truth coordinates.
- Tactical Status now shows ACTION and LAST CONTACT so a stopped unit can be diagnosed from the UI.
- Added a regression test for long-running re-engagement behavior.


## v0.11
- Left click selects; left-drag pans the map.
- Anti-armor team has finite ammunition and a rifle fallback.
- Generic branch-aware tactical FSM: rifle infantry breaks contact from armor when anti-armor capability is unavailable; artillery displaces from close direct contact.


## v0.12 modularization + armor/infantry update

- `CombatResolver` was extracted to `mnsim/combat.py`.
- `DoctrineEngine` was extracted to `mnsim/doctrine.py`; `Simulation` keeps thin compatibility delegates.
- Tank platoons now expose separate abstract anti-personnel mechanisms: **main-gun HE** and
  **coaxial machine-gun fire**, in addition to anti-armor direct fire. This makes armor materially
  dangerous to exposed infantry in the demo rather than treating a tank as an anti-tank-only entity.
- The UI shows finite ammunition beside weapons when applicable.
- Infantry AT ammunition depletion still removes only `ANTI_ARMOR`; its rifle fallback remains available.
- These demo weapon values are architecture/tuning placeholders, not calibrated real-world performance.


## v0.13 indirect-fire / artillery area-effect model

Indirect fire is no longer resolved as a direct `Pk` roll against one target element.
`mnsim/indirect_fire.py` now owns artillery fire-mission resolution.

The sequence is:

```text
FoW Track / counter-battery solution
        -> estimated aim point
        -> combine track uncertainty + weapon dispersion
        -> sample one impact point per fired round
        -> spatial distance from impact to nearby formations
        -> personnel / equipment / armor effect radii
        -> element casualties through normal ELEMENT_LOSS events
```

The demo towed-artillery platoon has four gun elements and therefore produces four impact
samples per one-round-per-piece salvo. A formation with fewer surviving guns automatically
produces fewer rounds. Counter-battery observation still triggers on each fire mission.

`dispersion_cep_m` is represented as a circular-error-probable parameter. For the generic
isotropic Gaussian used here, CEP is converted to the underlying standard deviation and
combined in quadrature with uncertainty from the target Track. Thus poor/stale target
information can make an otherwise precise fire mission land well away from the true formation.

Area effect is category-dependent:
- personnel: largest effect radius,
- soft equipment: smaller,
- armored equipment: smallest and lowest probability.

Impacts can affect any formation physically near the impact point, not merely the originally
selected target. `combat.indirect_friendly_fire` controls whether friendly formations can also
be affected. The demo enables this, so firing onto a close mixed engagement has an actual
spatial risk.

The current formation position is still a centroid; sub-unit spatial footprints/dispersion
inside a platoon are a later extension. All radii/probabilities in `demo.json` remain generic
M&S tuning parameters rather than calibrated specifications for a named real artillery system.


## v0.14 persistent contact / enemy-order-of-battle belief

Fog of War now separates **firing-quality track validity** from **belief that an enemy formation
still exists**. Losing sensor contact no longer deletes an enemy formation from the commander's
operational picture.

A contact has two related but different concepts:

```text
TACTICAL TRACK
  confidence / age / position error
  -> DETECTED / CLASSIFIED / IDENTIFIED / STALE / LOST
  -> determines whether combat logic may act on the contact

EXISTENCE BELIEF
  belief_confidence / last_confirmed_time / classification
  -> remains after tactical track is LOST
  -> displayed as EST-INFANTRY / EST-ARMOR / EST-ARTILLERY
  -> does NOT by itself authorize firing
```

Counter-battery detection is strong evidence that an artillery formation exists. When the firing
solution ages out, BLUE/RED therefore retains an `EST-ARTILLERY` contact at the last estimated
location with decaying existence confidence instead of making the artillery disappear.

Default policy:
- 90 s grace period after confirmation,
- then slow exponential belief decay (15 min half-life),
- a 0.28 confidence floor / 0.25 display threshold,
- absence of observation alone is **not** treated as destruction evidence.

The belief can be removed through an explicit terminal-evidence/BDA path (`mark_destroyed`).
Future doctrine can replace these defaults with branch/echelon-specific memory, intelligence
fusion, false-contact handling, relocation prediction, or battle-damage-assessment thresholds.

The crucial architectural rule is: **persistent inferred contacts are intelligence beliefs, not
omniscient tracks.** Combat still requires an actionable sensor/shared/counter-battery track.


## v0.15 counter-battery launch-signature rule

Counter-battery detection is explicitly **launch-based, not target-based**. Any operational
enemy indirect-fire formation that fires while its firing position is inside an operational
counter-battery radar's coverage may generate a firing-origin solution probabilistically.

```text
RED artillery fires at BLUE infantry / armor / another artillery unit / any other target
        -> INDIRECT_LAUNCH
        -> enemy counter-battery radars check shooter-to-radar range
        -> stochastic detection + processing delay + origin error
        -> counter-battery Track / persistent EST-ARTILLERY belief
```

Whether the mission is `FIRE_SUPPORT` or `COUNTER_BATTERY` is irrelevant to detection. A radar
does not need to be the target and does not need to be hit. If the radar or required radar crew
is disabled, the launch produces no solution from that sensor.


## v0.16 delayed reporting and artillery fire-control timeline

Situation awareness is no longer propagated to artillery as a near-live target feed. The
simulation now preserves the age of the original observation and explicitly models several
delays before indirect fire can occur.

```text
Infantry observes target at T0
  -> local Track only
  -> observer reporting delay
  -> report reaches C2/HQ
  -> C2 dissemination delay
  -> artillery receives the old coordinate with original observation timestamp
  -> fire-support request delay
  -> fire-direction/FDC computation delay
  -> gun preparation/loading/laying delay
  -> guns fire using the coordinate snapshot captured when the mission was requested
  -> projectile time of flight
  -> spatial impact / CEP resolution
```

A target that moves during this chain is **not continuously tracked by the artillery mission**.
The already-prepared mission continues to use its frozen aim point. A later observation must
propagate through C2 and result in a later fire mission before a newer coordinate is used.

Default generic timing assumptions are stored in `config/defaults.json`, not hard-coded into
the fire-control module. The current defaults are intentionally broad M&S values rather than
claimed timings for a particular military:

- observer report: 8–20 s, with at least 12 s between repeated reports of the same contact
- C2 dissemination: 8–18 s
- fire-support request handling: 5–12 s
- fire-direction computation: 10–24 s
- gun preparation/loading/laying: 14–30 s
- reload/cycle before another mission: 20–35 s
- projectile time of flight: 8–15 s

The scenario may override any value under its `combat` object. `scenario.py` currently merges:

```text
engine fallback < config/defaults.json < scenario combat overrides
```

This loader boundary is intentional. A later CSV/SQLite/PostgreSQL equipment/doctrine database
can populate the same normalized configuration/model objects without changing combat, doctrine,
or UI code.


## v0.17 doctrine-driven artillery patterns, terrain, and external TO&E library

### Artillery fire-pattern doctrine

Artillery no longer has to place every gun on the same deliberate aim point. A fire profile
defines the deliberate distribution of gun aim points **before** random track uncertainty and
weapon dispersion/CEP are applied.

The demo profile library is `config/artillery_doctrine.json`:

```text
CONCENTRATED  -> tight radial concentration
AREA          -> wider radial distribution over the target area
LINEAR        -> deliberate line pattern with configurable spacing
```

Each artillery formation selects a profile through unit metadata:

```json
"metadata": {
  "artillery_fire_profile": "AREA"
}
```

The demo artillery platoon contains five surviving/initial guns. Therefore a one-round-per-piece
salvo produces five deliberate pattern points, after which each round independently receives the
existing CEP + track-location error. Loss of guns automatically reduces the number of rounds.

This separation is intentional for doctrine experiments:

```text
same gun / same ammunition / same sensor information
        + different fire-pattern doctrine
        -> different spatial concentration / coverage / friendly-fire risk / expected effects
```

Pattern radius, line spacing, and rounds per piece are data values and can later be selected by
BML/LLM doctrine or a scenario editor without changing `indirect_fire.py`.

### Terrain and mobility

`mnsim/terrain.py` introduces a 2D terrain layer without elevation. The first supported terrain
types are:

- `OPEN`
- `ROAD`
- `RIVER`
- `BRIDGE`

Terrain geometry is stored in `config/terrain_demo.json` and the same geometry is used by both the
simulation and the Pygame map. The current mobility model is intentionally simple and extensible.

Unit templates declare a mobility class and terrain factors. Current generic defaults:

```text
FOOT           OPEN 1.00   ROAD 1.05
TRACKED        OPEN 1.00   ROAD 1.15
WHEELED_TOWED  OPEN 0.72   ROAD 1.35
```

Thus wheeled/towed systems benefit much more from roads, while tracked armor has a smaller
road/open-ground difference. These are abstract scenario factors, not measured vehicle performance.

A river is impassable unless:
1. the current point lies on a bridge, or
2. the unit type explicitly has `AMPHIBIOUS` or `WATER_CROSSING` in `mobility_capabilities`.

The current tank and artillery templates have no such capability. When a straight route would
cross the river, the movement model seeks the nearest reasonable bridge waypoint instead of
allowing the entity to pass through water. A future engineer/bridge unit can add a crossing
capability or dynamically create a bridge/crossing terrain object without redesigning movement.

### External TO&E templates and future editor/DB

Unit composition has been moved out of `demo.json` to `config/toe_templates.json`.
The scenario references it through:

```json
"unit_types_file": "../config/toe_templates.json"
```

This is preparation for a later force-structure editor and CSV/database backend. The editor should
modify normalized TO&E data rather than Python classes.

The current generic infantry platoon is a Western-style abstract template with:
- three rifle squads,
- platoon HQ,
- a separate anti-armor team,
- separate designated marksmen.

Counts are intentionally explicit. For example, a scenario/editor can change an existing platoon
without defining a new Python class:

```json
"element_overrides": [
  {"id": "at_specialist", "count": 3, "initial_count": 3},
  {"id": "designated_marksmen", "count": 5, "initial_count": 5}
]
```

Finite weapon inventory can also be overridden:

```json
{
  "id": "at_specialist",
  "weapon_ammo": {
    "abstract guided AT weapon": 6
  }
}
```

The intended future data flow is:

```text
Force/TO&E Editor
      or
CSV / SQLite / PostgreSQL
        -> normalized UnitType / FormationElement / WeaponModel definitions
        -> scenario instance + per-unit overrides
        -> headless Simulation
```

Do not build the future editor around assumptions such as "every infantry platoon has exactly
three squads" or "AT team is always two people." The engine is supposed to treat those as data.


## v0.18 counter-battery symmetry + target persistence

### Counter-battery radar / mission priority

Counter-battery radar detection remains side-symmetric: BLUE and RED radars use the same
launch-signature, range, probability, processing-delay, and crew-availability path. A dedicated
bidirectional regression test now verifies both:

```text
BLUE indirect launch -> RED CBR solution
RED indirect launch  -> BLUE CBR solution
```

A second issue existed above the sensor layer: a battery already preparing a normal
`FIRE_SUPPORT` mission could ignore a newly received counter-battery solution because the gun
group already had a pending mission. `FireControlEngine` now supports mission priorities and
token-based preemption. By default:

```text
COUNTER_BATTERY  priority 30
FIRE_SUPPORT     priority 20
INDIRECT_FIRE    priority 10
```

and `counter_battery_preempts_fire_support=true`. The superseded event remains harmless in the
event queue because its mission token no longer matches the active fire mission.

This policy is configurable; future doctrine may disable preemption, change priorities, or assign
different priorities by echelon/mission.

### Target persistence / hysteresis

Direct-fire target selection no longer performs a weighted random re-selection every combat tick.
A shooter now keeps its current target while that target remains actionable and can still be
affected.

Default target behavior:

1. acquire the highest-scoring valid target;
2. retain it for at least `target_lock_min_s` (18 s by default);
3. after the lock interval, retain it unless another valid target's score is at least
   `target_switch_score_ratio` (1.55x by default);
4. switch immediately if the current target is destroyed, lost, or outside all usable weapon
   envelopes;
5. doctrine may explicitly force a target by setting `unit.metadata["force_target_id"]`.

The same persistence mechanism is used by artillery when selecting between candidate fire-support
or counter-battery targets. Mission-type priority can still force a necessary switch; persistence
is not meant to override doctrine or an urgent higher-priority threat.

The design goal is **hysteresis, not permanent fixation**: target changes should have an explicit
reason visible in logs (`TARGET_ACQUIRED`, `TARGET_SWITCH`, `FIRE_MISSION_PREEMPTED`) rather than
being a side effect of a new random draw every simulation tick.


## v0.19 equipment vulnerability, artillery geometry, and degraded vehicles

Equipment no longer survives indirect/direct fire merely because the formation still has a
positive equipment count. `mnsim/damage.py` introduces per-item equipment states:

```text
OPERATIONAL
MOBILITY_KILL     cannot move, may still fire
FIREPOWER_KILL    may move, cannot provide its weapon
DISABLED          neither useful movement nor weapon employment
DESTROYED
```

This distinction follows the common modeling idea that an armored vehicle can lose mobility or
firepower without being catastrophically destroyed. Public U.S. Army material likewise
distinguishes mobility, firepower, and catastrophic kills; historical artillery testing also
reports track/road-wheel/sight damage from near-hit fragmentation rather than requiring a direct
hit for every armored-vehicle effect.

### Indirect fire against equipment

Each artillery impact is classified geometrically relative to a formation centroid:

```text
DIRECT band
NEAR-HIT band
FRAGMENT / indirect-effect band
OUTSIDE
```

The bands then use the target element's protection class and configurable probabilities. Current
demo data treats:
- tanks as `HEAVY_ARMOR`,
- towed guns as `UNARMORED_GUN`.

A direct artillery hit on a soft gun has a high path to destruction; a near hit has a substantial
path to disabling it. Heavy armor is much more resistant, but near/fragment hits can still cause
mobility or firepower kills. This avoids both unrealistic extremes: "every shell kills a tank"
and "anything except a direct hit does nothing."

All radii/probabilities are externalized in `config/toe_templates.json` weapon metadata and remain
generic M&S tuning values, not vulnerability specifications for a named vehicle or ammunition.

### Direct anti-armor effects

A successful direct anti-armor weapon hit no longer simply decrements the tank count. It samples
a configurable consequence such as catastrophic destruction, mobility kill, firepower kill, or
disablement. The same mechanism is shared by tank anti-armor fire and the abstract guided
infantry AT weapon.

### Damaged-vehicle deaggregation

When an armor element receives a `MOBILITY_KILL` or `DISABLED`, the simulator creates a separate
vehicle-level proxy at the damage location:

```text
Tank platoon x4
  -> one tank mobility-killed
  -> parent platoon continues as the aggregate of its remaining mobile vehicles
  -> B-TK-1-DET-n remains at the location as the damaged vehicle
```

The detached vehicle is now a real combat entity rather than a display-only proxy. Physical
ownership is transferred out of the parent element: a four-tank platoon with one mobility kill
therefore becomes a mobile three-tank residual platoon plus one stationary vehicle entity. A
`MOBILITY_KILL` vehicle may continue to employ surviving weapons and ammunition from its damage
location; a `DISABLED` vehicle can neither move nor fire. The residual platoon is no longer
artificially slowed by vehicles that it has left behind. Finite abstract ammunition pools are
partitioned when an item is detached rather than cloned.

The detached entity inherits the parent's current tactical picture/target state at separation so
that detachment does not erase crew awareness or create an artificial immediate extra shot. It is
subsequently sensed, targeted, damaged, and destroyed as an ordinary vehicle-level combat entity.
This is the basis for later recovery, repair, abandonment, crew transfer, or explicit reaggregation.

The UI shows the residual formation and detached vehicle separately (for example parent `x3` plus
`detached 1 x1`).

## v0.19 counter-battery salvo acquisition correction

Counter-battery sensing now models the fact that a multi-round artillery salvo exposes more than
one ballistic trajectory to a weapon-locating radar. `INDIRECT_LAUNCH` carries the actual launched
round count, and the radar converts its configured single-trajectory detection probability into a
capped salvo probability. The default demo cap is three effective trajectory opportunities. This
preserves stochastic misses, especially near the edge of coverage, without treating a five-gun
salvo as only one all-or-nothing radar trial.

The fire-origin solution is based on the observed projectile trajectory. Therefore, once a
`CB_TRACK_READY` solution is pending, the firing formation becoming destroyed/disabled during the
short radar-processing delay no longer erases that already observed origin solution. The radar
itself and required crew must still remain operational until processing completes.


## v0.19 tactical timing / perception calibration

The demo now treats movement, perception, and firing as separate time processes rather than
allowing a usable Track to trigger an instantaneous first shot.

- Direct-fire weapons have data-driven target-acquisition/aim delays before the first effective
  shot on a newly acquired or switched target. `shots_per_min` remains an *effective formation
  engagement-cycle rate* rather than the mechanical cyclic rate of every individual firearm.
- Visual detection range is a hard envelope, not a guarantee. Per-scan detection probability now
  falls sharply toward the edge of the observer's visual envelope; repeated observation improves
  Track confidence/classification over time.
- Dismounted and vehicle movement use unit-type `state_speed_factors` and terrain factors. The
  default infantry planning speed is approximately 4 km/h on road and lower cross-country, rather
  than the previous sustained ~8 km/h open-ground movement.
- Indirect fire now enforces both the configurable reload delay and the weapon's effective
  `shots_per_min` cycle floor. Repeat missions against the same target within a configurable
  window use shorter adjustment/FDC/gun-lay delays than the initial mission, without bypassing
  the weapon cycle.

These remain generic, openly defensible planning assumptions rather than exact performance data
for a named unit or weapon. All newly introduced timing/perception parameters live in JSON config
or weapon/unit metadata so they can later move to CSV/SQLite/PostgreSQL without changing combat
logic.

### Selected-unit range-ring symbols (UI)
The tactical map keeps the existing color-coded range rings and now adds small vector symbols directly on a visible part of each selected-unit ring so the meaning can be inferred without opening F1 help. Cyan/eye is the ordinary visual/sensor detection envelope; purple/radar is an operational counter-battery radar envelope; generic direct-fire weapons use a bullet symbol; guided/anti-armor weapons use a missile symbol; and indirect-fire weapons use an artillery-shell symbol. Weapon-ring colors continue to indicate the sorted distinct operational weapon ranges rather than a hard-coded weapon name. The F1 help legend mirrors these symbols and explains that the visual detection circle is a maximum detection envelope, not guaranteed instantaneous detection.

### Selected-unit range overlay semantics (v19 UI refinement)

Selected-unit range rings now use **stable semantic families**, not the ordinal order of weapon ranges. This prevents the meaning of a color from changing when a unit receives an additional secondary weapon.

- CYAN + eye: visual / ordinary sensor detection envelope.
- PURPLE + radar mast: operational counter-battery radar envelope.
- ORANGE + bullet: small arms / machine-gun range.
- RED + cannon round: direct-fire cannon / tank-gun range.
- MAGENTA + missile: guided anti-armor / missile range.
- GOLD + artillery shell: indirect-fire / artillery range.

Weapon definitions may set `metadata.ui_range_family` (`SMALL_ARMS`, `CANNON`, `GUIDED_AT`, `INDIRECT_FIRE`); this metadata is authoritative for the UI. The renderer has a conservative capability/name fallback for legacy/external data without that field. Multiple ranges belonging to one family intentionally reuse the same color and icon. If different families have exactly the same physical range, the renderer separates the display circles by only a few screen pixels so both meanings remain visible. F1 uses the exact same legend as the map overlay.


### Range-ring labels
Selected-unit range overlays use stable semantic colors/icons and also show a short data-driven label plus range (for example `SA 350m`, `DMR 500m`, `ATGM 600m`, `CB RADAR 3.2km`). `weapon.metadata.ui_range_label` is authoritative when present, so new weapon systems can remain understandable without adding unit-name special cases to the UI.


## Targeting doctrine and counterfire memory

Target selection is perception-driven and configurable through `config/targeting_doctrine.json`
(`targeting_doctrine_file` in a scenario). The default profile is a generic Army-like starting
point, not a claim that one fixed priority list applies to every mission. Direct-fire and indirect-fire
priorities are separate, track source quality can be weighted, and artillery/counterfire timing can be
changed without adding unit-name special cases.

Counter-battery point-of-origin tracks use a longer stale/lost window than ordinary visual tracks.
This is intentional: a radar-derived firing coordinate remains a plausible fire mission after the
visual-contact timeout, although it eventually expires as a firing-quality solution while persistent
enemy OOB belief may remain. The default profile keeps a counterfire solution for 180 s, subject to
confidence/belief and position-error gates. This prevents the former failure mode where a 45 s generic
track timeout could expire during FDC, gun preparation, and reload, causing artillery to stop
counterfire after only one mission.

Current default indirect-fire priority is broadly: enemy artillery/radar first, then other high-value
capabilities such as C2/air defense when those classifications are introduced, then armor, then
infantry. Exact weights belong to doctrine/scenario data and are expected to be edited later.
Target scoring uses the shooter's Track classification/source/confidence rather than the ground-truth
branch or ground-truth loss ratio. Physical damage compatibility remains a ground-truth resolution
concern after a target has been selected.

### Range-ring label readability
Selected-unit sensor/weapon range captions are drawn tangentially to their circles. At the preferred right-hand (0°) anchor the caption is vertical, while the sensor/weapon icon remains upright. This reduces overlap when several concentric ranges are close together; fallback anchors rotate the caption to the local circle tangent.

### Mobility-kill calibration and detached display spacing

The demo vulnerability model now treats persistent mobility kills as deliberately rare.  Current
generic defaults use 0.01 conditional mobility-kill probability for a tank-gun armor hit, 0.015 for
the abstract guided AT weapon, 0.02 for an artillery near hit on heavy armor, and 0.002 for armor
exposed only to the configured fragmentation band.  A direct armor hit can also result in no
persistent equipment-state damage instead of forcing every hit into DISABLED.  This represents
armor defeat and locally repairable/non-mission-ending damage at the current abstraction level.
Temporary track/road-wheel impairment and field repair/recovery are reserved for a future
maintenance/recovery module rather than being counted as permanent MOBILITY_KILL events.  These
are scenario-tuning assumptions rather than claimed vulnerability data for a named vehicle and
remain externalized in weapon metadata.

A detached vehicle's simulation/world position remains the actual damage position.  The UI may fan
co-located NATO symbols by only a small screen-space offset so both the residual platoon and the
stationary vehicle can be selected; this display offset does not change range, movement, sensing,
or combat geometry.

### Combat-ineffective maneuver behavior
- A maneuver formation may remain physically mobile after every usable weapon has been lost (for example, all remaining tanks are `FIREPOWER_KILL`).
- Such a formation no longer continues an `ATTACK` order toward contacts/objectives. With an actionable threat it performs one bounded, perception-based disengagement and then holds for recovery; without a threat, or without tactical mobility, it holds in place.
- The bounded withdrawal distance is configured by `combat.combat_ineffective_withdraw_m`; the destination is latched to prevent attack/search objective oscillation.

### Directional visual sensing / watch direction
- Ordinary visual sensing is no longer a perfect 360-degree long-range circle. Each formation has a persistent `watch_heading_deg` property and combines a short 360-degree local-awareness bubble with a longer directional observation sector.
- `watch_heading_deg` is distinct from body/vehicle `heading_deg`. It slews toward the axis of movement while maneuvering, toward a currently tracked/engaged threat when one exists, and may be assigned explicitly with `watch_heading_deg` (or `facing_deg`) in a halt/defend order. A scenario may also provide an initial `watch_heading_deg`.
- Sensor geometry is data-driven. `combat.visual_sensor_profiles` supplies branch defaults and `unit_type.metadata.visual_sensor` may override `forward_range_m`, `forward_fov_deg`, `all_round_awareness_m`, and `watch_slew_deg_per_s` for a particular TO&E template. Current demo calibration uses a wider infantry observation sector, a longer/narrower armor optics sector, and an artillery local-security/observation sector. These are generic M&S calibration values, not specifications for a named real platform.
- A target beyond the all-round awareness bubble must lie inside the current long-range sector before the ordinary visual probability model is evaluated. Probability still declines with range and also declines toward the sector edges. The short 360-degree zone has reduced detection effectiveness except for the existing very-close `proximity_contact_m` rule.
- Counter-battery radar remains a separate omnidirectional radial sensor and is not constrained by `watch_heading_deg`.
- The selected-unit map overlay mirrors the engine geometry: cyan sector = long-range visual observation, thin cyan circle = local 360-degree awareness, purple circle = counter-battery radar. The properties panel shows visual range/FOV, all-round awareness distance, and current watch direction.


### Directional visual FOV calibration
The default visual sectors are intentionally narrower than earlier prototypes: infantry 100 deg, armor 70 deg, artillery 90 deg. These values are configuration data, not engine constants. Change `combat.visual_sensor_profiles` in `config/defaults.json`, or override a formation through `unit_type.metadata.visual_sensor` in `config/toe_templates.json`. `forward_range_m`, `forward_fov_deg`, `all_round_awareness_m`, and `watch_slew_deg_per_s` are independently configurable. Counter-battery radar remains an independent 360-degree sensor.

### Directional observation and environment-ready sensing

Visual detection is a formation-level observation model, not a literal human eyeball FOV. A unit combines a primary watch sector with a shorter 360-degree local-awareness bubble. Branch defaults live in `config/defaults.json` under `combat.visual_sensor_profiles`, and a TO&E may override them through `metadata.visual_sensor`. Current clear-day/open-ground calibration is infantry 90 deg + 220 m local awareness, armor 60 deg + 160 m, and artillery 75 deg + 180 m. Radar remains a separate sensor path.

Environmental effects are separated from sensor baselines through `mnsim/environment.py`. `EnvironmentObservationModel` applies multiplicative `range_factor`, `fov_factor`, `awareness_factor`, and `detection_factor` values for weather, illumination, and terrain. With the current `CLEAR` / `DAY` scenario and no optical terrain zones all factors are 1.0, so the map behaves as open terrain. Future scenarios can select `RAIN`, `HEAVY_RAIN`, `FOG`, `DUSK`, or `NIGHT`, add `observation_zones` polygons (brush/forest/urban/smoke etc.) to terrain JSON, or introduce a full LOS/elevation model by implementing the same terrain observation-modifier contract. Sensor-specific overrides (for example `sensor_mode: THERMAL`) allow optics to respond differently from naked-eye visual observation without unit-name special cases.

### Communications abstraction (current baseline)
Friendly situational-awareness dissemination is routed through `mnsim/communications.py`. The current
`SIDE_WIDE` profile is a permissive baseline in which all active friendly entities are potential
recipients regardless of echelon. Delivery remains recipient-specific and event-delayed. Link medium,
latency, reliability, optional range and jamming hooks are explicitly separated so future platoon-
company-battalion radio nets, wired links, relay nodes, EMCON and EW can replace the baseline routing.
Shared contacts can reorient `watch_heading_deg` as situational cues, but communications alone never
creates maneuver/support orders; that remains doctrine/agent/BML responsibility.


## Formation footprint / area-effect damage

Aggregated formations are not point targets for area effects. `mnsim/formation_geometry.py` provides a configurable elliptical footprint (`length_m`, `width_m`, orientation, density model) derived from branch defaults plus unit metadata and a `dispersion_posture`. Personnel remain aggregated and are spatially sampled only when an area-effect munition is resolved. Low-count equipment such as tanks and artillery pieces receives deterministic relative positions inside the footprint, so an impact affects the spatially exposed item rather than a randomly chosen vehicle.

`COMPACT`, `NORMAL`, `DISPERSED`, `COLUMN`, and `LINE` are geometry postures under `combat.formation_posture_modifiers`. `Simulation.set_formation_posture()` is intentionally decision-neutral: future tactical doctrine, BML, minefield logic, terrain constraints, burst lethality/recovery logic, or an RL agent may select a posture without coupling that decision to the damage resolver. Current demo units default to `NORMAL`. Severe losses reduce density before materially shrinking the footprint.

Indirect fire now resolves projectile impact -> formation footprint -> personnel/equipment exposure -> radial blast/fragment effect -> component/state damage. Armored vehicles are much less vulnerable to ordinary near/fragment effects than personnel or unarmored equipment; direct/very-close impacts remain dangerous. All generic radii/probabilities remain config/weapon metadata and are not claimed as calibrated vulnerability data for a named platform.

### Counterfire vulnerability profiles
Indirect-fire equipment damage now resolves geometry (DIRECT/NEAR/FRAGMENT) separately from a
configuration-driven target `protection_class`. `combat.indirect_fire_vulnerability_profiles`
contains generic profiles such as `UNARMORED_GUN` and `RADAR_SENSOR`; this avoids treating every
unarmored system as equally fragile and allows future platform/position/fortification data to
replace the default calibration without changing combat code.

## Destructible bridge infrastructure

Bridges are now persistent terrain infrastructure objects with data-driven structural integrity. Indirect-fire impacts can accumulate structural damage; once a bridge reaches zero integrity it becomes destroyed, no longer counts as a passable crossing, and is ignored by bridge-routing logic. Known fixed infrastructure is attacked with the `STRIKE_INFRASTRUCTURE` order through the normal delayed fire-control pipeline rather than by fabricating a hostile Track. The demo includes `B-ART-2`, which attacks `BR1` and then `BR2` sequentially.

## Sparse A* movement routing
Movement now uses a sparse vector navigation graph rather than the older "straight line, then nearest bridge" heuristic. Current position, destination, road vertices, and portals/centers of operational bridges form a visibility graph. A* edge cost estimates travel time from each formation's data-driven terrain speed factors, so road-favoring wheeled formations may accept a longer road route while tracked units may cut across open ground. Destroyed bridges disappear from the navigation graph immediately. The route-cost layer is intentionally separated so later doctrine/COA can add minefield, threat, concealment, congestion, or risk penalties without changing movement APIs.


## Belief-guided persistent counterfire

A counter-battery radar loss no longer erases already disseminated enemy-battery knowledge. Fresh
point-of-origin solutions remain high-quality counterfire Tracks for the normal doctrine window.
After that Track becomes LOST, a sufficiently credible persistent ARTILLERY Belief can still produce
an `INFERRED` counterfire solution for `counter_battery.belief_fire_max_age_s`. Its positional error
grows by `belief_position_error_growth_m_per_min` and its effective confidence decays, so repeat fire
against a stale location becomes progressively less precise rather than magically current. This path
does not inspect ground-truth target survival when deciding whether a believed coordinate remains
worth firing on; BDA/new observations are expected to terminate or update the Belief.

## Rich vector terrain authoring

`TerrainModel` now accepts rivers as either the legacy `polygon` or an editor-friendly `points`
polyline with `width_m`. Polyline rivers are treated as a continuous channel for mobility and are
drawn with rounded segment joins. Bridges may now use an authored centerline `points` polyline plus `width_m`; legacy `center`/`length_m`/`heading_deg` bridges remain supported. Passability, artillery structural
damage, UI geometry, and A* bridge portals all use the same bridge geometry. Generic polygon
`areas` (e.g. WOODS/BRUSH/URBAN/MARSH) can provide `movement_factor`, mobility-class overrides,
`observation_modifier`, sensor overrides, and an optional UI color. The current demo uses these
features while remaining elevation-free; future terrain editors can emit the same JSON interfaces.


## Map / scenario editor
`editor.py` is a separate Pygame authoring tool for unit placement and vector terrain (roads, polyline rivers, polyline bridges, woods/forest/brush/urban polygons). It saves the same scenario/terrain JSON consumed by `mnsim.scenario.load_scenario`; no editor-only runtime format is introduced. `main.py` accepts a scenario path plus optional side-specific BML paths, and Ctrl+O reopens the full run-selection workflow.

Dense FOREST terrain is a polygon area distinct from lighter WOODS. Default dense-forest calibration allows FOOT movement at 0.45x open-ground speed, forbids TRACKED/WHEELED/WHEELED_TOWED off-road traversal (explicit roads remain usable), and limits ordinary visual penetration through tree cover to about 100 m (THERMAL 120 m). These are data-driven area properties and can be changed per map/forest polygon. The map/scenario editor authors FOREST by clicking polygon vertices and right-clicking to close the area.

Startup workflow: `editor.py` with no argument now starts a completely empty 4 km x 4 km OPEN plain with no units or terrain. Use N to reset to another blank map, O to open an existing scenario, and S/Shift+S to save. `main.py` no longer silently loads demo.json. Normal GUI launch uses three independent choices: Scenario (required) -> BLUE BML (optional; Cancel means none) -> RED BML (optional; Cancel means none). Ctrl+O repeats the same three-step run selection. Explicit command-line use is `python main.py scenario.json [--blue-bml blue.json] [--red-bml red.json]`.

Map dimensions are scenario data (`world.width_m`, `world.height_m`). In editor.py press M to set width and height in metres (100 m to 200 km per axis). Both editor.py and main.py draw a dynamic metric grid plus a lower-left scale bar. Grid spacing automatically uses 1/2/5 × 10^n metre intervals as map size/zoom changes; the visible `Grid ...` label and metre/km scale bar make sub-cell and multi-cell distances easy to estimate.

Editor camera/navigation: maps are fully rectangular and width/height are independent. `editor.py` fits the entire authored rectangle at zoom 1.0, mouse-wheel zooms around the cursor up to 20x, and the arrow keys pan the map. Shift+Arrow moves a selected unit for fine placement. The dynamic grid and metric scale bar use the current camera zoom. `main.py` already uses the scenario's independent `world.width_m` and `world.height_m` for camera scaling/clamping, so non-square scenarios preserve their real aspect ratio.

Optional support weapons: INF_PLT now defines an optional machine-gun section (`machine_gun_section`) whose template count is zero. A scenario activates it with `element_overrides` (`count` and `initial_count` 4); therefore infantry platoons of the same type may have or lack the MG section. The represented section aggregates two general-purpose machine guns at roughly 600 m effective M&S range. In editor.py select a unit and press G to toggle the first TO&E element marked `metadata.optional_attachment`; this editor mechanism is generic and is not hard-coded to the infantry/MG element, so later mortar, recon, EW, or other attachments can use the same interface.

## External BML mission files (v39)
Scenario and BML are separate artifacts. A scenario contains the battlefield, OOB, initial unit state, terrain/config references, and optional legacy unit `orders`; it does not need to name a BLUE or RED plan. At normal GUI startup the user selects the scenario, then independently selects an optional BLUE BML and RED BML. Command-line runs use `--blue-bml` and `--red-bml`. This lets one scenario be reused across many BLUE COAs and RED ECOAs without copying the scenario file.

For backward compatibility, the low-level `load_scenario(path)` API still understands old scenario files containing `bml_files`; however the main GUI/CLI passes an explicit run-time BML selection, so embedded references do not silently override what the user selected. New scenarios should keep BML references out of the scenario JSON.

The BML file supports a `missions` list. Implemented mission tasks are `MOVE_TO`, `ATTACK_POSITION`, `ATTACK_UNIT`, `DESTROY_UNIT`, `DEFEND_POSITION`, `DEFEND_AREA`, `SEIZE`, `HOLD`, and `WITHDRAW`. Existing scenario-local `orders` remain supported. By default, BML replaces existing orders only for units explicitly mentioned in that BML; omitted units are left untouched. `ATTACK_UNIT`/`DESTROY_UNIT` use only the target's FoW Track / last-known Track position, or an explicit BML `target_position`; enemy identity alone never grants Ground Truth position knowledge. For identity-agnostic combat, use coordinate-based `ATTACK_POSITION`, which engages hostile contacts discovered through the normal Track/Belief pipeline. See `BML_GUIDE.md`.

## v38 echelon-aware artillery survivability
- Indirect-fire `max_personnel_loss_per_round` is enforced once per impacted formation per shell, not independently for every FormationElement.
- Aggregate formation footprints scale by echelon (`PLT 1.0`, `COY 1.8`, `BN 3.1` by default), preventing company/battalion manpower from being packed into a platoon-sized ellipse.
- Formations receiving an indirect-fire threat cue automatically adopt `DISPERSED` posture temporarily (default 120 s), then restore their previous posture. All values are data-driven in `config/defaults.json`.

## Runtime scenario/BML selection

- `python main.py`: choose Scenario, then optional BLUE BML, then optional RED BML.
- `python main.py scenarios/foo.json`: choose optional BLUE/RED BML for that scenario.
- `python main.py scenarios/foo.json --blue-bml scenarios/blue.json --red-bml scenarios/red.json`: use the specified plans without BML dialogs.
- `python main.py scenarios/foo.json --no-bml`: intentionally use only the scenario's built-in/default orders.

At startup the console prints the resolved Scenario, Terrain, BLUE BML, and RED BML paths.

## UI migration seam (v41 refactor)

The Pygame frontend remains the current operational UI, but generic application/session controls
now live in `mnsim/application.py` and contain **no Pygame, Qt, or Tk imports**.  This is an
intentional migration seam for a later PySide6 frontend rather than a functional change to the
simulator.

Frontend responsibilities should remain split as follows:

```text
framework UI (Pygame now / PySide6 later)
    keyboard, mouse, widgets, drawing, dialogs
             |
             v
mnsim.application.SimulationController
    pause/speed/realtime advancement, view/selection session state
             |
             v
mnsim.simulation.Simulation
    movement, sensing, FoW/C2, doctrine, combat, events, pathfinding integration
```

Migration rules:

- Do not add Pygame/Qt imports to `mnsim/` engine modules.
- Do not move combat, sensing, doctrine, mobility, FoW, or order rules into a GUI controller.
- A future Qt frontend should translate Qt signals/events into `SimulationController` operations
  and render/query `controller.sim`; it should not duplicate simulation rules.
- Keep scenario/BML JSON and headless tests as the behavioral contract across frontend migrations.
- Refactors intended only for UI migration must preserve deterministic engine behavior for the
  same scenario, BML inputs, seed, and simulation-time stepping.

## Architecture for future model extensibility

The v42 extensibility refactor adds two behavior-neutral boundaries:

- `mnsim/definitions.py`: `DefinitionRegistry` and default factories for `WeaponModel`, `FormationElement`, and `UnitType`.
- `mnsim/catalog.py`: `UnitTypeCatalog`, including the existing echelon/formation-family resolution rule.

`scenario.py` remains the scenario orchestrator but no longer owns concrete model-object construction. Existing scenario/TO&E JSON follows the `DEFAULT` registry path and retains v42 semantics. This boundary is intended for future LLM-assisted structured model authoring and plugin/adaptor definitions; it does **not** change combat, sensing, doctrine, damage, movement, or BML behavior.

See `ARCHITECTURE.md` for extension rules. In particular, prefer adding reusable capability primitives and data definitions over adding new named-platform `if/elif` branches throughout the engine.

## Data-driven weapon definitions / loadouts

Weapon performance definitions have been moved to `database/weapons.csv`. TO&E templates now
reference stable `weapon_id` values through named slots, and `database/loadouts.json` can replace
those slots per unit. This means two units with the same `INF_PLT`/`TANK_PLT` template can carry
different weapon systems regardless of BLUE/RED side.

Existing simulation mechanisms and the current synthetic v42 values are unchanged. CSV/JSON is
resolved only during scenario loading; runtime combat continues to operate on normal model
objects. See `DATA_MODEL.md` for examples including `loadout` and `loadout_overrides`.

### v44 command/doctrine extensions

BML-lite now supports per-step reactive conditions, absolute `start_at_s`, `deadline_s` with optional
`on_deadline`, phase-organized mission lists, and mission-specific tactical directives. Units may
select side-neutral doctrine profiles from `config/doctrine_profiles.json`. Existing flat BML and
legacy `orders_by_unit` remain supported. See `BML_GUIDE.md`.

## v45 close-aware direct-fire allocation

Direct fire now distinguishes the geometry by which the shooter acquired a local visual Track.
A contact acquired inside the short-range all-round awareness zone is marked `CLOSE`; a contact
acquired through the principal directional observation cone is marked `FORWARD`.

- `CLOSE`: compatible FormationElement/weapon streams may distribute across several simultaneous
  close enemy formations. This models local sub-elements reacting in different directions.
- `FORWARD` (and shared/non-local tracks): the formation keeps one primary direct-fire target and
  concentrates compatible fire on that target, preserving target lock/hysteresis.
- A specialist weapon that cannot affect any close contact may continue to service the formation's
  primary forward target while other elements react locally.
- Known close contacts and close incoming-fire cues do not, by default, slew the formation's
  principal watch sector; long-range/direct-sector cues still can.

The behavior remains perception-driven: `observation_zone` is stored on the observer's Track and
is not inferred from live enemy ground truth during target allocation. Configuration switches are
`combat.close_multi_target_fire_enabled` and `combat.close_contacts_preserve_watch_sector`.

## v46 vegetation-aware observation / LOS foundation

Visual sensing now treats WOODS/FOREST as clutter crossed by the observer-target ray rather than as a blanket observer-side view-range penalty.

- Vegetation penetration is reciprocal: if the ray crosses more than the sensor penetration budget, both directions are optically blocked.
- Path attenuation scales with the actual distance travelled through vegetation, so a formation at a forest edge can still observe far into open terrain.
- Target concealment is separate from geometric/path LOS. A target embedded in WOODS/FOREST is harder to detect than an exposed target looking back along the same ray.
- CLOSE/proximity awareness obeys the same LOS attenuation and cannot see through deeply occluding vegetation.
- VISUAL and THERMAL may use different penetration budgets.
- The interface remains in `TerrainModel.observation_modifier()` / `EnvironmentObservationModel`, so future BUILDING, smoke, and elevation occlusion can use the same sensing pipeline instead of special-casing unit branches.

Current fallback penetration values are 100 m VISUAL / 120 m THERMAL for FOREST and 250 m VISUAL / 300 m THERMAL for WOODS when a terrain object does not explicitly author its own values. Terrain-authored values take precedence.

## v47 editable formation composition and mounted infantry templates

The map/scenario editor now supports `IND`, `SQD`, `PLT`, `COY`, and `BN` echelons. Selecting a unit opens a transactional composition draft in the right-hand panel. Personnel/equipment counts and explicit multi-system weapon inventories can be changed with `- / +`; `SAVE CHANGES` writes per-unit `element_overrides`, while `CANCEL` discards draft edits. This means two symbols using the same TO&E template can still have different manning, vehicle counts, machine-gun counts, and crew-per-system values.

Explicit crew-served systems use `weapon_system_counts` and `weapon_operators_per_system`. Runtime fire contribution is limited by the personnel/equipment providers still available. Legacy aggregate weapon streams that do not declare `system_count` retain their previous one-stream behavior, preserving older scenarios.

New starter templates include an individual rifleman, a US-style rifle squad, generic machine-gun squad/platoon, Bradley-based mechanized infantry platoon, HMMWV-based motorized infantry platoon, BMP-type mechanized infantry platoon, and a K200-type ROK-inspired mechanized infantry platoon. These are **public-source-inspired M&S starting points, not authoritative/current national TO&E claims**; every placed unit is intentionally editable. Vehicle definitions are externalized in `database/platforms.csv` and weapon definitions remain in `database/weapons.csv`.

Map symbols now use simplified APP-6/MIL-STD-2525-style branch frames: infantry cross, armor ellipse, mechanized infantry cross+ellipse, artillery dot, plus echelon markings for squad/platoon/company/battalion. They are intentionally lightweight renderer approximations rather than a complete symbol standard implementation.

## NATO echelon amplifier correction

The pygame simulator and map editor share `mnsim.symbology.echelon_amplifier()`.
Echelon amplifiers follow MIL-STD-2525/APP-6 convention: squad `•`, section `••`,
platoon/detachment `•••`, company/battery/troop `I`, battalion/squadron `II`,
regiment/group `III`, brigade `X`, division `XX`, corps/MEF `XXX`, and army `XXXX`.
`IND` is a simulator-specific individual entity and intentionally has no echelon amplifier.

### Responsive map editor UI

`editor.py` uses a resizable Pygame window.  The tactical viewport and inspector panel are
recomputed from the current window dimensions, while the inspector retains a minimum usable
width.  Text is wrapped or ellipsized instead of being allowed to overlap adjacent controls,
and editor fonts retain a readability floor when the window is reduced.  Composition rows,
Save/Cancel actions, and the status area are positioned relative to the live panel bounds rather
than the original 1500x900 pixel layout.

## v47.3 editor unit-template selector
- The editor now shows the compatible unit templates for the active echelon as clickable choices in the right inspector; Q/W remains a shortcut.
- Added standalone IND vehicle templates for M1A2 Abrams, K2 Black Panther, M2 Bradley, BMP-type IFV, K200-type APC, and HMMWV.
- Platoon choices continue to include tank, Bradley mechanized, BMP mechanized, K200 mechanized, and HMMWV motorized platoons.
- Vehicle definitions remain side-neutral; BLUE/RED only determines affiliation.

### v48 infantry realism / engagement positioning
- Generic `RIFLE_SQD` has no machine gun by default; crew-served MGs are explicit/optional attachments.
- US-inspired `US_RIFLE_SQD` keeps automatic riflemen as a separate specialized template.
- Foot-infantry open-terrain observation baselines are echelon-aware and still pass through terrain/concealment sensing.
- Doctrine can select `STANDOFF`, `BALANCED`, or `COMBINED_ARMS` engagement-range positioning; BML mission directives may override it per mission.

### v49 mounted/dismounted mechanized infantry
- Generic transport primitive driven by platform `crew`/`passengers`, not Bradley/K200 name checks.
- Organic `DISMOUNT`/`MOUNT`: vehicle crews stay mounted; dismountable infantry becomes a real child unit.
- External `BOARD`/`DISEMBARK`: friendly squad/IND units can occupy available seats in crewed empty IFV/APC vehicles while preserving their own TO&E identity.
- Editor composition inspector exposes vehicle count, crew/vehicle, and passenger seats/vehicle and saves them as scenario overrides.
- Main selected-unit panel reports mount state, free/total seats, and current/required vehicle crew.
- Baselines: M2 Bradley 4-vehicle platoon, 3 crew + 6 passenger seats per IFV; K200-type 4-vehicle platoon, 3 crew + 9 passenger seats per APC. These remain editable scenario/template data, not engine constants.

## v49.1 light anti-armor / timed-order notes

- Added `AT4_MULTIROLE_GENERIC`, an unguided line-of-sight disposable/light anti-armor abstraction.
  Infantry rifle/grenadier elements expose an editor-configurable `LIGHT_AT_WEAPON` stream with
  default system count 0 so legacy scenarios do not silently gain firepower.
- The editor can change explicit weapon system count and finite ammunition/rounds; scenario overrides
  are persisted through `weapon_system_counts` and `weapon_ammo`.
- Guided `ATGM_GENERIC` now carries data-driven lock requirements.  It requires a sufficiently
  classified/confident local firing Track and a lockable target tag; it remains anti-armor only.
- AT4-type weapons do not require seeker lock and can affect PERSONNEL, ARMOR, EQUIPMENT and future
  STRUCTURE entities.  `structure_capable`/`aim_point_capable` metadata is retained for the future
  destructible-building/terrain-target pipeline; current terrain polygons are not yet damageable entities.
- BML `start_at_s` is absolute simulation/scenario elapsed time (T+seconds from `Simulation.time=0`).
  The common order gate applies to MOVE/ATTACK/DISMOUNT/MOUNT/BOARD/etc.


## v49.2 terrain / elevation extension
- `LAKE` polygon: non-amphibious vehicles/equipment cannot enter. FOOT formations may swim at a severe speed penalty; on first water entry, crew-served/heavy machine guns and anti-armor weapons are dropped and logged (`SWIM_HEAVY_WEAPONS_DROPPED`).
- `BUILDING` polygon: FOOT formations may occupy only via explicit `ENTER_BUILDING` / `EXIT_BUILDING` boundary-crossing orders during runtime; ordinary movement routes around operational footprints. Engine-level occupancy is intentionally unlimited regardless of echelon/personnel count, leaving force-to-building constraints to higher-level BML/COA generation. Operational buildings hard-occlude visual/direct-fire rays unless elevation clears the roof. Occupants are harder to detect/hit from outside. Structure-capable direct weapons and indirect-fire impacts reduce structural integrity; collapse destroys occupants.
- `ELEVATION` polygon: authored contour/plateau elevation in metres above the 0 m map datum. Overlapping contours use the highest value. Grade affects path passability and movement speed. Observation/direct-fire ray height is compared with building roof height, so elevated observers may see/fire over lower obstacles.
- Editor tools: `L` Lake, `K` Building, `V` Elevation; comma/period changes current contour elevation by 10 m; semicolon/apostrophe changes current building height by 1 m.
- Elevation is currently vector/step-contour rather than a continuous DEM. It is deliberately isolated behind `TerrainModel.elevation_at()` for later bilinear/raster replacement.

### v49.3 weapon composition consistency
The editor now uses weapon inventory semantics rather than showing `systems / operators / ammo` for every weapon. AT4-type disposable weapons are configured as carried rounds only; crew-served weapons expose weapon count + crew; assigned individual weapons expose weapon count; vehicle-mounted weapons rely on vehicle crew and provider-limited mounts. A regression audit rejects impossible default TO&E combinations such as more one-person assigned weapons than personnel or more platform mounts than providers.

### v49.4: bounded area defence and anti-armor calibration
- Added `SECURE_AREA`: center/radius or polygon defence with Track-based bounded pursuit and re-centering.
- Direct-fire anti-armor hit probability is now explicitly separated from post-hit equipment effect.
- `ATGM_GENERIC` is calibrated as a modern guided ATGM baseline; `JAVELIN_FGM148` is an explicit higher-end entry used by US mechanized/motorized AT teams.
- AT4 remains an unguided disposable round with substantially lower MBT kill probability per hit than Javelin-class guided AT.

## v49.5 machine-gun and direct-fire timing audit
- Machine guns now apply transient burst lethality to personnel even when a burst causes no immediate casualty. Direct-fire lethality decays over time and reduces exposed tactical movement and outgoing direct-fire accuracy.
- M1A2 individual template includes coaxial MG, commander heavy MG, and loader MG streams; vehicle MGs are platform-mounted rather than separate personnel crews.
- Optional two-gun GPMG section now exposes two crew-supported systems at runtime.
- Direct-fire timing separates first target acquisition/lay delay from repeated engagement cycles. Weapons may define `engagement_cycle_min_s/max_s` and periodic `reload_after_cycles` / `reload_delay_min_s/max_s` pauses.
- Weapon `shots_per_min` remains a backward-compatible legacy fallback, not mechanical cyclic RPM.
- Selected-unit UI displays current burst lethality percentage.


## v49.6 machine-gun lethality correction
The transient suppression mechanic introduced in v49.5 was removed. Machine guns now differentiate themselves through calibrated burst casualty probability, multi-effect burst size, weapon-system multiplicity, engagement-cycle cadence, and periodic reload pauses. Small arms and machine guns remain in the same direct-fire pipeline; no machine-gun-only movement or accuracy debuff is applied.

### v49.6.1 portable legacy scenario loading
- Standard engine resources (`config/defaults.json`, `config/toe_templates.json`, artillery/targeting doctrine) are no longer serialized as version-folder-relative paths by the editor.
- A saved scenario may omit those standard resource paths; the running project supplies its current standard resources.
- Legacy maps that contain stale versioned paths automatically fall back to the current project resources when those paths no longer resolve.
- Map-local `terrain_file` remains scenario-relative and is not silently replaced.

### v49.7 terrain UI / HESCO barricades

- Elevation captions follow the local contour tangent and avoid other elevation captions in both `editor.py` and `main.py`.
- Editor: `H` selects BARRICADE; while that tool is selected, `,` / `.` rotate the outward normal by -/+10 degrees. Click places one fixed 10 m MIL1-class section. The red direction tick is the exposed/facing side; protection is on the opposite side.
- Unit composition inspector exposes `Barricade capacity (10m MIL1 sections)`, default 0.
- BML `BUILD_BARRICADE` lets eligible foot infantry build allocated sections. Default construction time is 1200 s.

### v49.9 watch / engagement orientation timing
- `watch_heading_deg` is the formation's principal observation, guard, and engagement bearing. It remains separate from NATO-symbol/body `heading_deg`.
- Direction changes are stateful: `current watch_heading_deg` slews toward the current desired bearing by the shortest clockwise/counter-clockwise path rather than teleporting.
- Generic formation-level calibration is infantry 45 deg/s, armor 20 deg/s, artillery 30 deg/s, default 30 deg/s. These values intentionally represent formation/crew attention and engagement orientation, not literal human head or turret mechanical slew specifications. TO&E `metadata.visual_sensor.watch_slew_deg_per_s` may override them.
- Outside the CLOSE all-round awareness zone, a remembered direct-fire Track does not permit immediate fire through the rear of the current observation sector. The target must first enter the current forward watch arc; normal weapon acquisition/lay delay then applies.
- The scenario editor writes `watch_heading_deg` explicitly for newly placed units. Select a unit and use `Ctrl+Left/Right` to adjust the initial watch bearing by 10 degrees. The NATO symbol itself remains north-up/unrotated; a cyan bearing line from its center shows the assigned watch direction. `Shift+Arrow` continues to move the selected unit, while unmodified arrows pan the map.
