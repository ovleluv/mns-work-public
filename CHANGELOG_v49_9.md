# v49.9 - Stateful watch / engagement orientation

- Preserved the existing separation between body/movement `heading_deg` and principal `watch_heading_deg`.
- Recalibrated generic formation-level watch slew to infantry 45 deg/s, armor 20 deg/s, artillery/default 30 deg/s; existing generic TO&E overrides were migrated consistently.
- Rotation already used the shortest signed angular path; regression tests now lock that behavior and the relative infantry/armor timing.
- Direct fire outside CLOSE all-round awareness now waits for the target to enter the current forward watch arc before the existing weapon acquisition/lay delay begins.
- Map editor now stores an explicit initial `watch_heading_deg` for new units, draws a cyan centerline bearing indicator without rotating the NATO symbol, and supports Ctrl+Left/Right for +/-10 degree adjustment on a selected unit.
- Shift+Arrow remains unit translation; plain Arrow remains camera pan.
