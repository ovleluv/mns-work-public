# v49.10 - Building-corner navigation stall fix

## Fixed
- Ordinary MOVE/ATTACK/RETREAT routes can no longer treat a narrow building-corner clip as passable merely because coarse navigation samples miss it.
- `NavigationPlanner.segment_passable()` now performs continuous polygon-intersection checks against operational BUILDING footprints (except the single footprint explicitly authorized by ENTER_BUILDING/EXIT_BUILDING).
- Route waypoint advancement no longer cuts obstacle corners just because the formation is within the generic waypoint-arrival radius. The next waypoint is selected only when the next leg is continuously passable from the formation's current position.

## Reproduction
Using the supplied `test1` scenario and BLUE `ATTACK_POSITION`, v49.9 stalled near BLD3 around `(1776,1564)` with `TERRAIN BLOCKED / SEEKING BRIDGE`. v49.10 clears the corner and proceeds into engagement.

## Regression
- Added `tests/test_building_corner_navigation_v4910.py`.
- Relevant navigation/building/terrain/orientation regression suite: 26 passed.
