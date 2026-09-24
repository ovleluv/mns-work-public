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
main.py                 Pygame UI entry point: argument handling, run selection, main loop
ui/                     Pygame drawing: theme, view/camera, map render, panels, file dialogs
mnsim/model.py          Core data model: Unit, FormationElement, WeaponModel, Track, Order
mnsim/simulation.py     Orchestrator: time loop, events, prepared positions, reactive branches
mnsim/orders.py         (mixin) order execution, movement, navigation, bridge/building destruction
mnsim/engagement.py     (mixin) local N:M engagement grouping, target selection, combat pass
mnsim/perception.py     (mixin) sensing, watch orientation, cues, counter-battery, tracks, C2 receive, BDA
mnsim/composition.py    (mixin) aggregation/deaggregation, vehicle detachment, stable item lookup
mnsim/validation.py     Load-time validation of untrusted scenario/terrain/BML input
mnsim/stress.py         Suppression and morale/cohesion (combat stress)
mnsim/assault.py        Fire and movement: support by fire, assault, close combat
mnsim/batch.py          Independent scenario/seed runs using worker processes
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

## Runtime scenario/BML selection

- `python main.py`: choose Scenario, then optional BLUE BML, then optional RED BML.
- `python main.py scenarios/foo.json`: choose optional BLUE/RED BML for that scenario.
- `python main.py scenarios/foo.json --blue-bml scenarios/blue.json --red-bml scenarios/red.json`: use the specified plans without BML dialogs.
- `python main.py scenarios/foo.json --no-bml`: intentionally use only the scenario's built-in/default orders.

At startup the console prints the resolved Scenario, Terrain, BLUE BML, and RED BML paths.

## Map / scenario editor
`editor.py` is a separate Pygame authoring tool for unit placement and vector terrain (roads, polyline rivers, polyline bridges, woods/forest/brush/urban polygons). It saves the same scenario/terrain JSON consumed by `mnsim.scenario.load_scenario`; no editor-only runtime format is introduced. `main.py` accepts a scenario path plus optional side-specific BML paths, and Ctrl+O reopens the full run-selection workflow.

Dense FOREST terrain is a polygon area distinct from lighter WOODS. Default dense-forest tuning allows FOOT movement at 0.45x open-ground speed, forbids TRACKED/WHEELED/WHEELED_TOWED off-road traversal (explicit roads remain usable), and limits ordinary visual penetration through tree cover to 75 m (THERMAL 105 m). These are data-driven area properties and can be changed per map/forest polygon. The map/scenario editor authors FOREST by clicking polygon vertices and right-clicking to close the area.

Startup workflow: `editor.py` with no argument now starts a completely empty 4 km x 4 km OPEN plain with no units or terrain. Use N to reset to another blank map, O to open an existing scenario, and S/Shift+S to save. `main.py` no longer silently loads demo.json. Normal GUI launch uses three independent choices: Scenario (required) -> BLUE BML (optional; Cancel means none) -> RED BML (optional; Cancel means none). Ctrl+O repeats the same three-step run selection. Explicit command-line use is `python main.py scenario.json [--blue-bml blue.json] [--red-bml red.json]`.

Map dimensions are scenario data (`world.width_m`, `world.height_m`). In editor.py press M to set width and height in metres (100 m to 200 km per axis). Both editor.py and main.py draw a dynamic metric grid plus a lower-left scale bar. Grid spacing automatically uses 1/2/5 × 10^n metre intervals as map size/zoom changes; the visible `Grid ...` label and metre/km scale bar make sub-cell and multi-cell distances easy to estimate.

Editor camera/navigation: maps are fully rectangular and width/height are independent. `editor.py` fits the entire authored rectangle at zoom 1.0, mouse-wheel zooms around the cursor up to 20x, and the arrow keys pan the map. Shift+Arrow moves a selected unit for fine placement. The dynamic grid and metric scale bar use the current camera zoom. `main.py` already uses the scenario's independent `world.width_m` and `world.height_m` for camera scaling/clamping, so non-square scenarios preserve their real aspect ratio.

Optional support weapons: INF_PLT now defines an optional machine-gun section (`machine_gun_section`) whose template count is zero. A scenario activates it with `element_overrides` (`count` and `initial_count` 4); therefore infantry platoons of the same type may have or lack the MG section. The represented section aggregates two general-purpose machine guns at roughly 600 m effective M&S range. In editor.py select a unit and press G to toggle the first TO&E element marked `metadata.optional_attachment`; this editor mechanism is generic and is not hard-coded to the infantry/MG element, so later mortar, recon, EW, or other attachments can use the same interface.

## Multicore batch runs

Independent scenario runs can use multiple CPU cores. One interactive `Simulation.tick()` remains
single-process so its RNG, event queue, and mutable formation state retain deterministic ordering.
Run a seed sweep and write compact JSON summaries with:

```bash
python -m mnsim.batch scenarios/tdg3.json --runs 8 --seed-start 7 --steps 600 --workers 4 > batch.json
```

`--workers 1` runs the same workload serially; omitting it uses up to four processes. The output
contains each side's surviving inventory, event counts, simulated time, and a full-log checksum,
in input order. To ignore a scenario's embedded BML plans, add `--ignore-embedded-bml`.

The Python API supports different scenarios or per-run BML selections:

```python
from mnsim.batch import BatchRun, run_batch

if __name__ == "__main__":
    runs = [BatchRun("scenarios/tdg3.json", steps=600, seed=seed) for seed in range(7, 15)]
    results = run_batch(runs, workers=4)
```

Use the `__main__` guard in scripts so worker processes start correctly on macOS and Windows.

## Architecture

- `mnsim/model.py`: entity and weapon data models.
- `mnsim/events.py`: timestamped discrete-event priority queue.
- `mnsim/simulation.py`: continuous movement/ABM stepping + discrete combat/damage events.
- `mnsim/bml.py`: BML-lite order and condition evaluator.
- `mnsim/scenario.py`: JSON scenario loader.
- `main.py` + `ui/`: presentation only (Pygame). The simulation engine is UI-independent.

Layer boundaries, extension rules and the UI migration seam are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, definition/catalog boundaries, extension rules, UI migration seam |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Scenario/TO&E/weapon/loadout data contract |
| [docs/BML_GUIDE.md](docs/BML_GUIDE.md) | BML authoring checklist (part 1) and execution reference (part 2) |
| [docs/MODEL_REFERENCE.md](docs/MODEL_REFERENCE.md) | Model behaviour notes accumulated per version (v0.2 – v49.11) |
| [CHANGELOG.md](CHANGELOG.md) | Release notes, newest first |
| [MISSION/README.md](MISSION/README.md) | Saved mission capability cases and their validation |
| [tests/README.md](tests/README.md) | Test layout, opt-in suites and how to report results |

## v50 engine optimization, performance and bug fixes

Changes relative to v49.11. Details and per-commit notes are in [CHANGELOG.md](CHANGELOG.md).

### Performance

Measured on the same machine against the v49.11 baseline (`5e0281e`).

| Benchmark (tdg3 terrain, fixed inputs) | v49.11 | v50 |
|---|---:|---:|
| `TerrainModel.passable` | 69.0 µs | 10.7 µs |
| `TerrainModel.river_at` | 23.5 µs | 1.0 µs |
| `TerrainModel.speed_factor` | 128.0 µs | 9.3 µs |
| `observation_modifier` (one sight line) | 120.2 µs | 78.1 µs |
| Planner `segment_passable` | 100.9 µs | 34.9 µs |
| `plan_route` (A*) | 1.00 s | 0.32 s |
| Sensor scan, 21 units | 36.3 ms | 3.1 ms |

| 600 s of simulated time | v49.11 | v50 |
|---|---:|---:|
| `scenarios/tdg3.json` + BML | 95.8 s | 18.7 s |
| `scenarios/demo.json` | 21.3 s | 9.2 s |

`tdg1` is not comparable: in v49.11 its formations never detected each other, so no combat was computed.

- Movement passability is tested exactly once per terrain boundary interval instead of every 20 m, planner edge results are cached per terrain revision, and a leg already verified from the current position is not re-checked every step.
- Road and river polylines use a grid spatial index; polygon areas carry cached bounding boxes.
- Firepower and crew-status queries are memoised within a step (keyed on the unit's composition).
- Sensor pairs outside every possible range or sector are rejected before any terrain ray is cast.

### Engine correctness

- The same seed gives the same run regardless of `PYTHONHASHSEED`; engagement groups are built in a fixed order.
- Results no longer depend on the integration step, frame rate or the 1x–32x speed setting: sensor scans run on a fixed schedule, events are handled at their own timestamps, detection is a per-second hazard, and the UI advances in fixed 0.25 s steps with render interpolation.
- Fog of war: fire decisions use the composition seen at the last observation, and a kill becomes known only to formations that watched it (and through their reports).

### Bug fixes

- Delayed artillery effects could hit the wrong vehicle after another vehicle was detached; items now carry stable ids.
- Dismounted infantry could not fire; formations with both direct and indirect weapons never completed direct-fire acquisition.
- `STRIKE_INFRASTRUCTURE` ignored `start_at_s`; damage could trigger the branch of a phase order that had not started; a stale "no route" flag from a withdrawal cancelled later `HOLD` orders; `BOARD` skipped completion bookkeeping.
- `ATTACK_UNIT` / `DESTROY_UNIT` waited forever when the kill was not observed; a formation emptied by vehicle detachment was reported destroyed.
- Shared reports could overwrite a closer local track; a formation on a collapsing bridge was stranded; a unit with a dead radio could still report to HQ.
- `direct_fire_requires_local_track` in `config/defaults.json` was outside the `combat` block and ignored.
- Malformed scenario/BML input (`NaN`, far-off coordinates, wrong condition types) no longer hangs or crashes the engine; it is rejected at load time with the offending field.

### Model additions

- Attacks no longer stop at the stand-off line: after support by fire the attacker assaults and resolves close combat (`mnsim/assault.py`, `combat.assault`).
- Optional realism blocks in `config/defaults.json` (sector scanning, terrain line of sight, armor protection classes, artillery shoot-and-scoot, etc.). The combat stress model (suppression/morale, `combat.stress_model`) is **disabled by default**.
