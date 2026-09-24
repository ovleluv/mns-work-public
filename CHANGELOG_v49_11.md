# v49.11 — BML generation and simulator consistency

## Scope

This release records the simulator repair sequence following v49.10 and adds a practical BML authoring reference. The engine remains a research M&S prototype with synthetic weapon and sensor parameters.

## Work log

| Date (KST) | Commit | Work |
|---|---|---|
| 2026-09-24 | `de91c6f` | Optimized terrain observation/navigation hot paths and repaired delayed effects, movement, transport, and command-state consistency cases. |
| 2026-09-24 | `f817a33` | Added `mnsim.batch` for deterministic process-based execution of independent scenarios and seeds. A single live simulation tick remains sequential. |
| 2026-09-24 | `23b51aa` | Fixed the audited belief, direct-fire, FoW, aggregation, BML, and world-boundary defects. Added behavioral regressions and regenerated `MISSION/VALIDATION_STATUS.json`. |
| 2026-09-24 | Documentation update in this version | Added `BML_GENERATION_GUIDE.md`, updated README version notes and links, and recorded this work log. Use `git log --oneline -- README.md BML_GENERATION_GUIDE.md CHANGELOG_v49_11.md` to identify the documentation commit. |

## Major fixes

- Anchored existence-belief decay to the most recent observation so the configured 900-second half-life is independent of sensor scan frequency. Delayed C2 reports retain the source observation time, and newer reports can replace stale local positions.
- Used perceived classification, Track coordinates, and watch direction when deciding whether to attempt direct fire. Actual range, cover, and physical component compatibility are checked after ammunition is spent; a physically out-of-range target cannot be damaged.
- Created load-time aggregate parents before resolving BML unit IDs. Pending equipment and personnel effects follow the physical element through aggregation or deaggregation.
- Validated BML branches, condition paths, supported directives, and bounded coordinates before changing live orders. Completion-time branches retain the finishing order's objective/time state; deadline reporting is per order.
- Corrected README vegetation fallback values and clarified that named-weapon probabilities and top-attack metadata are not validated physical performance.

## BML authoring

[BML_GENERATION_GUIDE.md](BML_GENERATION_GUIDE.md) is the concise creation checklist. [BML_GUIDE.md](BML_GUIDE.md) remains the detailed behavior reference. Generate separate BLUE/RED plans from the scenario's active IDs, map bounds, structure IDs, and available information. A target ID alone grants no live enemy position or death confirmation. Unsupported `hold_fire` directives and unrecognized branch tasks fail at load time.

## Verification

- Default pytest suite at `23b51aa`: **1,700 passed, 323 opt-in tests skipped**.
- Full TDG3 integration suite: **14 passed**.
- Eight saved mission capability cases: **PASS**. The recorded input and engine hashes in `MISSION/VALIDATION_STATUS.json` matched the tested version.
- Documentation example in `BML_GENERATION_GUIDE.md`: JSON syntax and headless load checked against `scenarios/demo.json`.

The opt-in engagement matrix and GUI interaction were not part of these checks. The partially supported operations in [MISSION/MISSION_FEASIBILITY.md](MISSION/MISSION_FEASIBILITY.md) remain partial; BML load success is not a claim of operational effectiveness or real-world calibration.
