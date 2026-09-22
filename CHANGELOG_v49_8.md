# v49.8 - Explicit Building Access + LOS Performance

## Building movement
- Operational BUILDING footprints are hard obstacles for ordinary MOVE/ATTACK/RETREAT routing.
- Added BML `ENTER_BUILDING` and `EXIT_BUILDING`.
- Temporary building access is scoped to the active ENTER/EXIT order only.
- Sparse A* now adds operational building-corner nodes so ordinary movement can route around buildings.
- FOOT mobility remains required for building occupancy; vehicle entry remains blocked.

## Building occupancy
- Removed engine/editor enforcement of platoon-equivalent building capacity.
- Legacy `capacity_platoons` data is ignored by the engine for compatibility.
- Echelon/personnel/building-size constraints are intentionally deferred to higher-level BML/COA generation policy.

## LOS performance
- Replaced fixed-distance polygon ray marching for crossed-path length with segment/polygon intersection intervals.
- Added AABB broad-phase rejection.
- Replaced long-ray canopy/building height marching with a small fixed number of representative samples per crossed interval.
- Added UI-only `TerrainModel.approx_visual_limit()` so selected-unit LOS footprint rendering performs one cheap polygon pass per radial ray rather than dozens of full `observation_modifier()` calls.
- Simulation sensing still uses `observation_modifier()`; UI approximation does not create simulation truth.

## Regression
- Added `tests/test_building_entry_exit_v498.py`.
- Updated legacy building-capacity expectation to unlimited engine-level occupancy.
- Targeted building/terrain/vegetation/direct-fire/navigation/BML/contour-barricade suite: 26 passed.
- A broader selected suite previously reached 29/29 before editor/document cleanup; syntax compilation also passes.
- Full legacy suite includes long-running simulation tests and exceeded the execution window before completion; no failure was observed before timeout.
