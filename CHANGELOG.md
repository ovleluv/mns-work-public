# Changelog

Newest first. Earlier per-version files (`CHANGELOG_v49_*.md`) were merged here unchanged.

## v50.0 — combat realism model

Measured on `scenarios/demo.json`, 1800 s, 5 seeds (mean, before -> after): formations wiped out
1.8 -> 1.2, artillery crew surviving 47.6 -> 81.4 of 87, personnel lost 141.6 -> 110.4,
direct-fire rounds 648 -> 311 (suppressed formations fire less).

- **Suppression and morale** (`mnsim/stress.py`, `combat.stress_model`): every incoming round,
  near miss and casualty suppresses (scaled by formation size and prepared positions); suppression
  cuts rate of fire, accuracy, movement and detection. Morale falls with losses, leader loss and
  sustained fire; SHAKEN/PINNED formations stop advancing, BROKEN ones fall back and rally.
  `hold_at_all_costs` lowers the break point. `enabled: false` restores the pure attrition kernel.
- **Prepared positions**: protection builds from hasty to dug-in over `dig_in_time_s`; applies to
  direct fire, artillery effects and the per-round casualty cap.
- **Observation**: halted formations sweep their sector (all round when none is assigned); large
  formations observe from their footprint; ridges/crests block observation and fire (DEM LOS).
- **Detection** is a per-second hazard (independent of `sensor_update_s`), with size and firing
  signature, range-proportional position error and classification error below IDENTIFIED.
- **Direct fire**: firing on the move / at moving targets; kill probabilities by weapon
  penetration class x target protection class (`combat.armor_vulnerability`).
- **Doctrine**: formations under fire they cannot answer withdraw (`outranged_reaction`); batteries
  under counter-battery fire displace (shoot and scoot).
- **Artillery**: time of flight from range; one aim bias per mission plus per-round dispersion.

## v49.11 — review fixes (branch `fix/review-findings`)

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
- About 2.3x faster on `scenarios/demo.json`: exact interval passability instead of 20 m sampling,
  cached planner edges, sensor range culling before ray casting.

### Tooling
- `pyproject.toml` (pytest/ruff/mypy settings), `constraints.txt`, GitHub Actions CI.

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

