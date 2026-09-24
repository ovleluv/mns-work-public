# MNS COA Simulator Architecture

## 1. Design goal

The simulator is intentionally organized so that simulation behavior is independent from the UI and, increasingly, from the concrete syntax used to define platforms/formations/weapons.

Long-term extension target:

```text
Natural-language system concept
        ↓
LLM-assisted authoring
        ↓
validated structured definition
        ↓
DefinitionRegistry / catalogs
        ↓
existing simulation primitives
        ↓
Simulation / Monte-Carlo / COA experiment
```

The registry is an extension seam, not a second combat engine. Combat, sensing, movement, doctrine and damage remain authoritative in their existing `mnsim` modules.

## 2. Current layers

```text
main.py / editor.py                     UI layer (currently Pygame)
        ↓
mnsim/application.py                    UI-neutral session/controller layer
        ↓
mnsim/scenario.py                       scenario orchestration
        ↓
+-------------------------------+
| mnsim/definitions.py          |       definition factories / registry
| mnsim/catalog.py              |       TO&E / UnitType template catalog
+-------------------------------+
        ↓
mnsim/model.py                           core runtime data model
        ↓
mnsim/simulation.py + combat/doctrine/... simulation behavior
```

## 3. Definition boundary

`mnsim/definitions.py` owns conversion from structured dictionaries to existing runtime model objects:

- `WeaponModel`
- `FormationElement`
- `UnitType`

`DefinitionRegistry` currently uses only the legacy/default builders. Existing JSON therefore has the same behavior as v42.

A future definition adapter/plugin can be registered under an explicit `definition_kind` without adding parser branches to `scenario.py`.

Example future pattern:

```json
{
  "definition_kind": "PLUGIN_X",
  "name": "NEW_EFFECTOR",
  "...": "..."
}
```

No existing file uses such a kind, so the current execution path remains `DEFAULT`.

## 4. TO&E catalog boundary

`mnsim/catalog.py` owns UnitType template lookup and echelon/template resolution. Scenario loading no longer needs to know the internal rule used to map a legacy `INF_PLT + echelon=BN` request onto its matching formation-family template.

This is useful later for:

- multiple TO&E libraries,
- user-authored formation catalogs,
- LLM-generated definitions,
- versioned force packages,
- plugin-provided template families.

## 5. Rules for future capability-driven evolution

Do not convert existing branch behavior merely for architectural purity. Behavior changes require separate tests and validation.

For new work, prefer these boundaries:

1. **Definition/data** — what the system contains and its parameters.
2. **Capability primitive** — reusable engine mechanism such as sensing, mobility, direct fire, indirect fire, communications, protection, EW.
3. **Doctrine/behavior** — how a formation chooses to use available capabilities.
4. **Scenario/BML** — who is present and what mission is assigned.
5. **UI** — visualization and user interaction only.

Avoid introducing new system-specific checks into broad engine modules when a reusable capability primitive can represent the same mechanism.

## 6. LLM-assisted fast prototyping principle

The preferred future workflow is data-first:

- Existing mechanism, new parameterization/composition → generate a structured definition only.
- Truly new physical/behavioral mechanism → add and test one reusable engine primitive, then expose it through definitions.

The LLM should not normally rewrite `combat.py`, `simulation.py`, or `doctrine.py` merely to add a new named platform.

## 7. Compatibility requirement

Architecture refactors must preserve simulation behavior unless a task explicitly requests a model change. Regression tests and deterministic state comparisons should be used whenever a loader/factory/controller boundary is changed.

## External definition DB and loadouts

Flat weapon specifications now live in `database/weapons.csv`; hierarchical TO&E remains JSON.
`mnsim.database.WeaponCatalog` resolves stable `weapon_id` references at scenario load time.
`LoadoutCatalog` resolves named slot-to-weapon mappings. Formation templates define equipment
**slots**, while individual scenario units may select different loadouts or slot overrides.

This is an extension boundary, not a combat-model change. Do not introduce `if side == BLUE`
or `if branch == INFANTRY` equipment inventories into simulation modules. Side, branch,
formation structure, and exact equipment are separate concepts.

For a new system that uses existing mechanisms, prefer:

1. add a definition row to the external DB;
2. add or extend a loadout profile;
3. select that loadout/override in the scenario;
4. modify engine code only when the system requires a genuinely new simulation mechanism.

See `DATA_MODEL.md` for the authoring contract.

## Command / doctrine separation (v44)

Timed and phased BML is compiled into the existing Order queue rather than implemented as a second
simulation formalism. `start_at_s`, `deadline_s`, reactive `conditions/on_true`, and phase labels
are command constraints. Local tactical reactions remain in `DoctrineEngine`.

Doctrine profiles are side-neutral data selected explicitly per formation. Active mission
`directives` may temporarily override normal local reactions. This keeps `side`, `branch`,
`loadout`, `doctrine_profile`, and mission intent independent and supports future LLM-generated
COA/doctrine experiments without adding side-specific Python branches.

## Transport capability boundary (v49)
`mnsim/mounted.py` owns mounted/dismounted bookkeeping and seat allocation. It has no pygame dependency.
BML compiles transport tasks into generic Orders; Simulation calls the transport module; editor/main only
edit/display the same metadata. This keeps transport mechanics additive and reusable for future APC/IFV/
utility platforms without branch- or vehicle-name conditionals in the engine.


## v49.2 terrain / elevation extension
- `LAKE` polygon: non-amphibious vehicles/equipment cannot enter. FOOT formations may swim at a severe speed penalty; on first water entry, crew-served/heavy machine guns and anti-armor weapons are dropped and logged (`SWIM_HEAVY_WEAPONS_DROPPED`).
- `BUILDING` polygon: FOOT formations may occupy only via explicit `ENTER_BUILDING` / `EXIT_BUILDING` boundary-crossing orders during runtime; ordinary movement routes around operational footprints. Engine-level occupancy is intentionally unlimited regardless of echelon/personnel count, leaving force-to-building constraints to higher-level BML/COA generation. Operational buildings hard-occlude visual/direct-fire rays unless elevation clears the roof. Occupants are harder to detect/hit from outside. Structure-capable direct weapons and indirect-fire impacts reduce structural integrity; collapse destroys occupants.
- `ELEVATION` polygon: authored contour/plateau elevation in metres above the 0 m map datum. Overlapping contours use the highest value. Grade affects path passability and movement speed. Observation/direct-fire ray height is compared with building roof height, so elevated observers may see/fire over lower obstacles.
- Editor tools: `L` Lake, `K` Building, `V` Elevation; comma/period changes current contour elevation by 10 m; semicolon/apostrophe changes current building height by 1 m.
- Elevation is currently vector/step-contour rather than a continuous DEM. It is deliberately isolated behind `TerrainModel.elevation_at()` for later bilinear/raster replacement.

## Weapon inventory semantics
Weapon inventory/editing semantics are defined by weapon metadata rather than weapon-name conditionals. Runtime and editor distinguish disposable-round, individual-assigned, crew-served, and platform-mounted inventory. This prevents UI-only composition values from creating impossible runtime weapon streams and keeps future LAW/MAAWS/ATGM/vehicle weapon additions data-driven.

## v49.5 direct-fire timing / burst lethality
`CombatResolver.fire_weapon()` owns weapon acquisition, tactical engagement-cycle timing, periodic direct-fire reload pauses, hit resolution, and machine-gun burst lethality injection. `Simulation.tick()` owns burst lethality decay and `_move_toward()` applies the generic burst lethality movement factor. This avoids weapon-name branches in doctrine or UI.


## v49.6 machine-gun lethality correction
The transient suppression mechanic introduced in v49.5 was removed. Machine guns now differentiate themselves through tuned burst casualty probability, multi-effect burst size, weapon-system multiplicity, engagement-cycle cadence, and periodic reload pauses. Small arms and machine guns remain in the same direct-fire pipeline; no machine-gun-only movement or accuracy debuff is applied.

### v49.7 cartography / barricades

Contour caption placement is shared by editor and simulator through `mnsim/cartography.py`: labels are placed on contour segments, rotated to the local tangent and collision-tested against earlier contour labels. HESCO-style barricades are terrain primitives (`TerrainModel.barricades`) rather than special unit types; geometry, route blocking, directional cover, BML construction, editor placement and rendering all consume the same terrain record.

### Building movement and LOS performance (v49.8)

Operational BUILDING polygons are hard navigation obstacles. Temporary per-order access is granted only while `ENTER_BUILDING` or `EXIT_BUILDING` executes; ordinary movement pathfinding uses building-corner visibility nodes to route around footprints. Engine-level occupancy capacity is intentionally unlimited and higher-level BML/COA policy owns any force-to-building sizing constraint.

Terrain LOS no longer estimates polygon crossing length by fixed-distance marching. It uses segment/polygon intersection intervals with AABB broad-phase rejection, making cost primarily depend on polygon edge count rather than ray length. Selected-unit LOS rendering uses `TerrainModel.approx_visual_limit()` as a UI-only approximation so visualization cannot multiply full simulation LOS queries across dozens of radial samples. Simulation sensing still uses `observation_modifier()` as truth.
