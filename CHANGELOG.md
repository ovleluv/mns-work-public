# Changelog

Newest first. Earlier per-version files (v49.8–v49.11) were merged here unchanged. v50 was
developed in parallel with v49.11 and merged on top.

## v50 — review fixes and combat realism model

### Combat realism model

Measured on `scenarios/demo.json`, 1800 s, 5 seeds, with the stress model enabled (mean, before ->
after): formations wiped out 1.8 -> 1.2, artillery crew surviving 47.6 -> 81.4 of 87, personnel lost
141.6 -> 110.4, direct-fire rounds 648 -> 311 (suppressed formations fire less).

- **Suppression and morale** (`mnsim/stress.py`, `combat.stress_model`): every incoming round,
  near miss and casualty suppresses (scaled by formation size and prepared positions); suppression
  cuts rate of fire, accuracy, movement and detection. Morale falls with losses, leader loss and
  sustained fire; SHAKEN/PINNED formations stop advancing, BROKEN ones fall back and rally.
  `hold_at_all_costs` lowers the break point. **Disabled by default** (the coefficients are
  uncalibrated); enable with `combat.stress_model.enabled: true`.
- **Prepared positions**: protection builds from hasty to dug-in over `dig_in_time_s`; applies to
  direct fire, artillery effects and the per-round casualty cap.
- **Observation**: halted formations sweep their sector (all round when none is assigned); large
  formations observe from their footprint; ridges/crests block observation and fire (DEM LOS).
- **Detection** is a per-second hazard (independent of `sensor_update_s`), with size and firing
  signature, range-proportional position error and classification error below IDENTIFIED.
- **Direct fire**: firing on the move / at moving targets; kill probabilities by weapon
  penetration class x target protection class (`combat.armor_vulnerability`).
- **Fire and movement** (`mnsim/assault.py`, `combat.assault`): support by fire, then an assault
  once the attacker has fire superiority (or after `commit_after_s`) and is steady; closing at full
  movement speed; close combat at `contact_m` with casualties and morale shock by fighting-power
  ratio; failed assaults revert to support by fire. Duel probe (infantry platoon attacking a
  hold-at-all-costs platoon across 700 m of open ground, 5 seeds): at 3:1 the attacker now takes
  the objective in 5/5 runs (stand-off only: stops 120-240 m short in 4/5); at 1:1 against a
  prepared position the assault fails in 4/5.
- **Doctrine**: formations under fire they cannot answer withdraw (`outranged_reaction`); batteries
  under counter-battery fire displace (shoot and scoot).
- **Artillery**: time of flight from range; one aim bias per mission plus per-round dispersion.

### Correctness
- Same seed now gives the same run regardless of `PYTHONHASHSEED` (engagement grouping is sorted).
- Sensor scans follow a fixed `k * sensor_update_s` grid; track-confidence decay is per second;
  events run at their own timestamps; the UI advances with a fixed-step accumulator, so results
  no longer depend on frame rate or the 1x–32x speed setting.
- Fog of war: weapon/target decisions use the shooter's perceived composition
  (`Track.perceived_tags`) instead of the live inventory; infantry break-contact uses track
  classification; `self.enemy_count_near` counts tracks; kills become known only through
  battle-damage assessment (witnesses + communications).
- Delayed artillery effects address vehicles by stable item id (no wrong-vehicle hits after a
  detachment shifts the item list).
- Deaggregation restores subordinate layout, orders and tracks.

### Robustness / security
- `mnsim/validation.py`: strict JSON (no NaN/Infinity), finite in-world coordinates, condition type
  checks at load time (including nested branches), world/terrain size limits.
- Scenario resource references are confined to the scenario folder tree and the project tree.
- Editor never writes terrain outside the scenario folder; UTF-8 everywhere.
- UI reports load errors, pauses on engine exceptions and writes timestamped replay logs.

### Performance
- Exact interval passability instead of 20 m sampling, cached planner edges, reuse of a leg
  verified from the current position, grid spatial index for road/river polylines, cached polygon
  bounding boxes, per-step memo of firepower/crew queries, and sensor range/sector culling before
  ray casting.
- Against v49.11 (`5e0281e`), same machine: `passable` 69.0 -> 10.7 us, `river_at` 23.5 -> 1.0 us,
  `speed_factor` 128 -> 9.3 us, A* route 1.00 -> 0.32 s, sensor scan (21 units) 36.3 -> 3.1 ms;
  600 simulated seconds of `tdg3.json` 95.8 -> 18.7 s and of `demo.json` 21.3 -> 9.2 s.

### Tooling
- `pyproject.toml` (pytest/ruff/mypy settings), `constraints.txt`, GitHub Actions CI.

### Project layout
- Documentation moved to `docs/`: `ARCHITECTURE.md` (now also holding the README's UI-seam and
  extensibility sections), `DATA_MODEL.md`, `BML_GUIDE.md` (the former `BML_GENERATION_GUIDE.md`
  as part 1 and `BML_GUIDE.md` as part 2) and `MODEL_REFERENCE.md` (the README's per-version model
  notes v0.2–v49.11, unchanged). README keeps the overview, usage, a documentation index and v50.
- `mnsim/simulation.py` split into `orders.py` (order execution, movement) and `engagement.py`
  (engagement grouping, combat pass) mixins; `main.py` drawing split into the `ui/` package
  (`theme`, `view`, `render`, `panels`, `dialogs`). Behaviour-neutral: identical event logs for
  demo/tdg1/tdg3 and pixel-identical UI frames.
- `CHANGELOG_v49_11.md` merged into this file; `tests/TERRAIN_TRAVERSAL.md` merged into
  `tests/TDG3_TESTING.md`; terrain suite reports moved to `tests/terrain/reports/`; stale
  2026-09-07 test report snapshots and regenerable analysis outputs removed.

## v49.11 — BML generation and simulator consistency

### Scope

This release records the simulator repair sequence following v49.10 and adds a practical BML authoring reference. The engine remains a research M&S prototype with synthetic weapon and sensor parameters.

### Work log

| Date (KST) | Commit | Work |
|---|---|---|
| 2026-09-24 | `de91c6f` | Optimized terrain observation/navigation hot paths and repaired delayed effects, movement, transport, and command-state consistency cases. |
| 2026-09-24 | `f817a33` | Added `mnsim.batch` for deterministic process-based execution of independent scenarios and seeds. A single live simulation tick remains sequential. |
| 2026-09-24 | `23b51aa` | Fixed the audited belief, direct-fire, FoW, aggregation, BML, and world-boundary defects. Added behavioral regressions and regenerated `MISSION/VALIDATION_STATUS.json`. |
| 2026-09-24 | Documentation update in this version | Added `BML_GENERATION_GUIDE.md`, updated README version notes and links, and recorded this work log. Use `git log --oneline -- README.md BML_GENERATION_GUIDE.md` to identify the documentation commit. |

### Major fixes

- Anchored existence-belief decay to the most recent observation so the configured 900-second half-life is independent of sensor scan frequency. Delayed C2 reports retain the source observation time, and newer reports can replace stale local positions.
- Used perceived classification, Track coordinates, and watch direction when deciding whether to attempt direct fire. Actual range, cover, and physical component compatibility are checked after ammunition is spent; a physically out-of-range target cannot be damaged.
- Created load-time aggregate parents before resolving BML unit IDs. Pending equipment and personnel effects follow the physical element through aggregation or deaggregation.
- Validated BML branches, condition paths, supported directives, and bounded coordinates before changing live orders. Completion-time branches retain the finishing order's objective/time state; deadline reporting is per order.
- Corrected README vegetation fallback values and clarified that named-weapon probabilities and top-attack metadata are not validated physical performance.

### BML authoring

[BML_GENERATION_GUIDE.md](docs/BML_GUIDE.md#part-1--authoring-checklist) is the concise creation checklist. [BML_GUIDE.md](docs/BML_GUIDE.md#part-2--execution-reference) remains the detailed behavior reference (both now merged into `docs/BML_GUIDE.md`). Generate separate BLUE/RED plans from the scenario's active IDs, map bounds, structure IDs, and available information. A target ID alone grants no live enemy position or death confirmation. Unsupported `hold_fire` directives and unrecognized branch tasks fail at load time.

### Verification

- Default pytest suite at `23b51aa`: **1,700 passed, 323 opt-in tests skipped**.
- Full TDG3 integration suite: **14 passed**.
- Eight saved mission capability cases: **PASS**. The recorded input and engine hashes in `MISSION/VALIDATION_STATUS.json` matched the tested version.
- Documentation example in `BML_GENERATION_GUIDE.md`: JSON syntax and headless load checked against `scenarios/demo.json`.

The opt-in engagement matrix and GUI interaction were not part of these checks. The partially supported operations in [MISSION/MISSION_FEASIBILITY.md](MISSION/MISSION_FEASIBILITY.md) remain partial; BML load success is not a claim of operational effectiveness or real-world calibration.

## v49.10 - Building-corner navigation stall fix

### Fixed
- Ordinary MOVE/ATTACK/RETREAT routes can no longer treat a narrow building-corner clip as passable merely because coarse navigation samples miss it.
- `NavigationPlanner.segment_passable()` now performs continuous polygon-intersection checks against operational BUILDING footprints (except the single footprint explicitly authorized by ENTER_BUILDING/EXIT_BUILDING).
- Route waypoint advancement no longer cuts obstacle corners just because the formation is within the generic waypoint-arrival radius. The next waypoint is selected only when the next leg is continuously passable from the formation's current position.

### Reproduction
Using the supplied `test1` scenario and BLUE `ATTACK_POSITION`, v49.9 stalled near BLD3 around `(1776,1564)` with `TERRAIN BLOCKED / SEEKING BRIDGE`. v49.10 clears the corner and proceeds into engagement.

### Regression
- Added `tests/test_building_corner_navigation_v4910.py`.
- Relevant navigation/building/terrain/orientation regression suite: 26 passed.

## v49.9 - Stateful watch / engagement orientation

- Preserved the existing separation between body/movement `heading_deg` and principal `watch_heading_deg`.
- Recalibrated generic formation-level watch slew to infantry 45 deg/s, armor 20 deg/s, artillery/default 30 deg/s; existing generic TO&E overrides were migrated consistently.
- Rotation already used the shortest signed angular path; regression tests now lock that behavior and the relative infantry/armor timing.
- Direct fire outside CLOSE all-round awareness now waits for the target to enter the current forward watch arc before the existing weapon acquisition/lay delay begins.
- Map editor now stores an explicit initial `watch_heading_deg` for new units, draws a cyan centerline bearing indicator without rotating the NATO symbol, and supports Ctrl+Left/Right for +/-10 degree adjustment on a selected unit.
- Shift+Arrow remains unit translation; plain Arrow remains camera pan.

## v49.8 - Explicit Building Access + LOS Performance

### Building movement
- Operational BUILDING footprints are hard obstacles for ordinary MOVE/ATTACK/RETREAT routing.
- Added BML `ENTER_BUILDING` and `EXIT_BUILDING`.
- Temporary building access is scoped to the active ENTER/EXIT order only.
- Sparse A* now adds operational building-corner nodes so ordinary movement can route around buildings.
- FOOT mobility remains required for building occupancy; vehicle entry remains blocked.

### Building occupancy
- Removed engine/editor enforcement of platoon-equivalent building capacity.
- Legacy `capacity_platoons` data is ignored by the engine for compatibility.
- Echelon/personnel/building-size constraints are intentionally deferred to higher-level BML/COA generation policy.

### LOS performance
- Replaced fixed-distance polygon ray marching for crossed-path length with segment/polygon intersection intervals.
- Added AABB broad-phase rejection.
- Replaced long-ray canopy/building height marching with a small fixed number of representative samples per crossed interval.
- Added UI-only `TerrainModel.approx_visual_limit()` so selected-unit LOS footprint rendering performs one cheap polygon pass per radial ray rather than dozens of full `observation_modifier()` calls.
- Simulation sensing still uses `observation_modifier()`; UI approximation does not create simulation truth.

### Regression
- Added `tests/test_building_entry_exit_v498.py`.
- Updated legacy building-capacity expectation to unlimited engine-level occupancy.
- Targeted building/terrain/vegetation/direct-fire/navigation/BML/contour-barricade suite: 26 passed.
- A broader selected suite previously reached 29/29 before editor/document cleanup; syntax compilation also passes.
- Full legacy suite includes long-running simulation tests and exceeded the execution window before completion; no failure was observed before timeout.

