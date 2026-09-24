# External Data Model

The simulator separates **simulation mechanisms** from **user-maintainable definitions**.
The intent is that adding another rifle, ATGM, tank gun, artillery system, or alternate
loadout should normally be a data operation rather than an engine-code edit.

## Responsibility boundary

```text
CSV definition DB        JSON composition             Scenario
-----------------        ----------------             --------
weapon specifications -> formation weapon slots ->   unit instances
                                                   -> side/faction
                                                   -> loadout selection
                                                   -> one-off overrides
       \________________ runtime resolution __________________/
                              |
                              v
                     existing M&S kernel
```

* `database/weapons.csv` stores flat weapon/performance definitions.
* `database/loadouts.json` maps named loadout profiles from a slot to a `weapon_id`.
* `config/toe_templates.json` stores hierarchical formation/TO&E structure and weapon slots.
* `scenarios/*.json` places unit instances and may select different loadouts per unit.
* Python implements mechanisms (combat, sensing, movement, doctrine), not inventories for a side.

`BLUE`/`RED` are simulation sides only. Weapon definitions are deliberately side-neutral.
The same `weapon_id` may be used by either side, and two units of the same side/type may choose
different loadouts.

## Weapon DB

`database/weapons.csv` columns:

```text
weapon_id,name,capability,range_m,shots_per_min,pk,target_tags,
max_effect_count,ammo,metadata_json
```

`weapon_id` is the stable reference key. `name` is display text. `target_tags` and
`metadata_json` are JSON cells because those fields are structured while the main table remains
convenient to edit in Excel or another CSV editor.

The values currently in the CSV are the exact synthetic v42 tuning values moved out of
`toe_templates.json`; they are not claimed to be real-world weapon performance.

## Formation slots

A TO&E element references a slot and a default weapon definition:

```json
"weapons": [
  {"slot": "PRIMARY_RIFLE", "weapon_id": "SMALL_ARMS_GENERIC"}
]
```

Slots describe *what position in the formation is being equipped*. Current slots include:

```text
PRIMARY_RIFLE
COMMAND_RIFLE
AT_TEAM_RIFLE
AT_WEAPON
MARKSMAN_RIFLE
MACHINE_GUN
MAIN_GUN_AP
MAIN_GUN_HE
COAX_MG
INDIRECT_PRIMARY
```

The slot is intentionally independent of side, faction, and exact weapon model.

## Per-unit loadout

A unit may select a named loadout without changing its formation type:

```json
{
  "id": "B-INF_PLT-1",
  "side": "BLUE",
  "faction": "FORCE_A",
  "type": "INF_PLT",
  "echelon": "PLT",
  "loadout": "GENERIC_INFANTRY",
  "pos": [1000, 1200]
}
```

Another `INF_PLT` on the same side may select a different profile. A RED unit could also select
the same profile. The engine does not infer equipment from side.

`database/loadouts.json`:

```json
{
  "loadouts": {
    "GENERIC_INFANTRY": {
      "slots": {
        "PRIMARY_RIFLE": "SMALL_ARMS_GENERIC",
        "AT_WEAPON": "ATGM_GENERIC"
      }
    }
  }
}
```

Profiles may use `"extends": "BASE_PROFILE"` and override only selected slots.

## One-off experimental override

For rapid experiments or LLM-authored prototypes, a scenario unit can replace selected slots
without creating another formation template:

```json
{
  "id": "B-INF_PLT-X",
  "side": "BLUE",
  "type": "INF_PLT",
  "echelon": "PLT",
  "loadout": "GENERIC_INFANTRY",
  "loadout_overrides": {
    "AT_WEAPON": "EXPERIMENTAL_ATGM_X"
  },
  "pos": [1000, 1200]
}
```

The referenced weapon must exist in the configured CSV. The override changes only that unit
instance.

## File resolution

`config/toe_templates.json` declares its default database files:

```json
"weapon_database_file": "../database/weapons.csv",
"loadouts_file": "../database/loadouts.json"
```

Therefore old scenarios/tests that only point to `toe_templates.json` continue to work.
A scenario can explicitly set `weapon_database_file` or `loadouts_file` to use an alternate
experimental database.

## Runtime rule

CSV/JSON data are resolved when a scenario is loaded. Combat loops use normal `WeaponModel`
objects and do **not** repeatedly read CSV. Definition data and mutable runtime state therefore
remain separate: ammunition expenditure, damage, etc. occur on the per-unit runtime copies.

## Backward compatibility

Legacy inline weapon dictionaries are still accepted by `DefinitionRegistry`. This is useful
for old tests and small standalone scenarios. New maintained content should prefer stable
`weapon_id` references.

## Per-unit composition overrides

A formation template defines defaults; a scenario unit may override only that symbol/instance:

```json
"element_overrides": [
  {
    "id": "mg_crew",
    "count": 9,
    "initial_count": 9,
    "weapon_system_counts": {"GPMG_SINGLE_GENERIC": 3},
    "weapon_operators_per_system": {"GPMG_SINGLE_GENERIC": 3}
  }
]
```

This keeps `side`, `branch`, TO&E template, loadout, platform and actual per-unit composition separate. A vehicle element may reference `platform_id` from `database/platforms.csv`; weapon slots continue to resolve through `database/weapons.csv`/loadouts.

## v48 infantry observation and engagement-range doctrine

Foot-infantry visual ranges are open-terrain baseline observation ranges, not guaranteed detection.
Terrain/vegetation, target concealment, weather/illumination, FOV and track quality are applied later by
the sensing pipeline.  The default baselines are deliberately echelon-aware (individual < squad <
platoon < company < battalion) because larger formations distribute observers over a wider frontage.

`RIFLE_SQD` is the generic squad template and contains no machine gun by default.  Its
`machine_gun_attachment` element starts at count 0 and is opt-in.  `US_RIFLE_SQD` remains a distinct
US-inspired template and retains automatic riflemen; these are labelled `AUTOMATIC_RIFLE`, not a
crew-served machine-gun team.  `MG_SQD`/`MG_PLT` remain explicit support-weapon formations.

Direct-fire positioning is doctrine-driven without changing weapon physics:

- `STANDOFF` (default): stop once any relevant direct-fire weapon can reach the perceived target.
- `COMBINED_ARMS`: continue closing while longer-ranged weapons may fire, until the shortest currently
  operational/relevant direct-fire weapon can also participate.
- `BALANCED`: midpoint between shortest and longest relevant weapon envelopes.

Profiles live in `config/doctrine_profiles.json`.  A mission may override the profile without changing
unit type or loadout, e.g. `directives: {"engagement_range_policy":"COMBINED_ARMS", "engagement_range_fraction":0.9}`.
Only weapons that can affect the perceived target are considered, so rifles do not force an AT formation
to close against armor when only its AT weapon is relevant.

## Mounted infantry / transport state (v49)
Vehicle transport capacity comes from `database/platforms.csv` (`crew`, `passengers`) and is resolved
into equipment-element metadata at scenario load. Scenario `element_overrides[].metadata` may override
those values per formation; the map editor writes the same fields.

A transport-capable unit exposes runtime metadata including `mount_state`, `external_embarked_units`,
and per-unit `external_passenger_allocations`. Organic personnel that may leave the carrier are marked
`metadata.dismountable=true`; vehicle crew elements use role/tag `CREW` and are never moved by organic
`DISMOUNT`.

Organic dismounting physically transfers FormationElement runtime objects (including weapon/ammo state)
to a child infantry Unit, avoiding duplicate strength. External passengers keep their original Unit and
TO&E and become inactive/AGGREGATED only while embarked, allowing lossless disembark back to the original
squad/individual identity.

## v49.1 weapon terminal-targeting metadata

Weapon catalog metadata may define terminal targeting constraints independently of physical effects:

- `requires_target_lock`: whether an actionable Track must also satisfy a weapon lock gate.
- `lockable_target_tags`: perceived target classifications/signatures eligible for lock; the lock gate does not inspect hidden target components.
- `lock_required_states`: acceptable Track identification states (e.g. CLASSIFIED/IDENTIFIED).
- `lock_min_confidence`: minimum Track confidence for lock.
- `aim_point_capable`: descriptive metadata reserved for later commanded-point direct fire; it does not currently enable that action.
- `structure_capable`: permits existing direct-fire attacks against mapped BUILDING structures.
- `top_attack_capable`: descriptive catalog metadata; the current damage resolver has no separate top-armor or attack-angle calculation.

Physical target effects remain driven by `target_tags`; seeker/terminal targeting is a separate gate.
Per-unit finite inventory uses `element_overrides[].weapon_system_counts` and `weapon_ammo`.


## v49.2 terrain / elevation extension
- `LAKE` polygon: non-amphibious vehicles/equipment cannot enter. FOOT formations may swim at a severe speed penalty; on first water entry, crew-served/heavy machine guns and anti-armor weapons are dropped and logged (`SWIM_HEAVY_WEAPONS_DROPPED`).
- `BUILDING` polygon: FOOT formations may occupy only via explicit `ENTER_BUILDING` / `EXIT_BUILDING` boundary-crossing orders during runtime; ordinary movement routes around operational footprints. Engine-level occupancy is intentionally unlimited regardless of echelon/personnel count, leaving force-to-building constraints to higher-level BML/COA generation. Operational buildings hard-occlude visual/direct-fire rays unless elevation clears the roof. Occupants are harder to detect/hit from outside. Structure-capable direct weapons and indirect-fire impacts reduce structural integrity; collapse destroys occupants.
- `ELEVATION` polygon: authored contour/plateau elevation in metres above the 0 m map datum. Overlapping contours use the highest value. Grade affects path passability and movement speed. Observation/direct-fire ray height is compared with building roof height, so elevated observers may see/fire over lower obstacles.
- Editor tools: `L` Lake, `K` Building, `V` Elevation; comma/period changes current contour elevation by 10 m; semicolon/apostrophe changes current building height by 1 m.
- Elevation is currently vector/step-contour rather than a continuous DEM. It is deliberately isolated behind `TerrainModel.elevation_at()` for later bilinear/raster replacement.

## Weapon inventory/editor semantics (v49.3)
Weapon entries now declare an `inventory_model` in `database/weapons.csv` so the editor does not force every weapon into the same `systems + operators + ammo` UI.

- `DISPOSABLE_ROUNDS`: AT4/LAW/BDM-like single-use munitions. Edit carried rounds only; there is no persistent launcher count or dedicated operator count. Runtime availability is bounded by surviving personnel and remaining rounds.
- `INDIVIDUAL_ASSIGNED`: individually assigned weapons such as SAW/DMR. Edit weapon count; no separate crew field. Count cannot exceed personnel in the owning element.
- `CREW_SERVED`: reusable team weapons such as GPMG/ATGM abstractions. Edit weapon/launcher count and crew per weapon; runtime active systems are crew-limited.
- `PLATFORM_MOUNT`: vehicle-mounted guns. Edit mounts where the template explicitly exposes them; weapon operators are not counted separately from `crew / vehicle`. Runtime mount contribution is provider-limited.
- `PERSONNEL_AGGREGATE` / `EQUIPMENT_PROVIDER`: legacy aggregate tuned streams; they are not exposed as arbitrary editable system counts unless explicitly configured.

Scenario overrides remain data-driven. For disposable weapons, use `weapon_ammo` only. `weapon_system_counts` and `weapon_operators_per_system` are unnecessary for AT4-style rounds.

## v49.5 burst lethality and direct-fire cycle metadata
Direct-fire weapons may define `engagement_cycle_min_s`, `engagement_cycle_max_s`, `reload_after_cycles`, `reload_delay_min_s`, and `reload_delay_max_s`. Machine-gun-family weapons may additionally define `burst lethality_capable` and `burst lethality_per_cycle`. Runtime burst lethality is transient unit metadata (`burst lethality_level`, 0..1) and is not permanent damage.


## v49.6 machine-gun lethality correction
The transient suppression mechanic introduced in v49.5 was removed. Machine guns now differentiate themselves through tuned burst casualty probability, multi-effect burst size, weapon-system multiplicity, engagement-cycle cadence, and periodic reload pauses. Small arms and machine guns remain in the same direct-fire pipeline; no machine-gun-only movement or accuracy debuff is applied.

## Barricades / field fortifications (v49.7)

Terrain JSON may contain a top-level `barricades` array. A barrier is a directional 10 m HESCO MIL1-style line primitive with `center`, `heading_deg`, `length_m`, `width_m`, and cover factors. `heading_deg` is the exposed/outward normal; the opposite half-space is the protected side. Barricades block ground route edges, so the sparse A* planner routes around their endpoints. Direct-fire cover is applied only when the barrier physically intersects the shooter-target ray and the target is immediately behind the protected side.

Per-unit scenario metadata may define `barricade_limit`; omitted values mean zero. Runtime `barricades_built` tracks consumption without changing the unit's personnel TO&E.

## Watch / engagement orientation (v49.9)
`Unit.heading_deg` is physical/movement-body heading. `Unit.watch_heading_deg` is a separate persistent formation-level principal observation/guard/engagement bearing. Scenario unit records may initialize both independently. Runtime tactical cues, movement axis, assigned order facing, and tracked targets determine a desired watch bearing; the current bearing slews toward it at the data-driven `watch_slew_deg_per_s` rate using the shortest angular path. Direct-fire outside CLOSE awareness requires the target to have entered the current watch sector before weapon acquisition/lay timing proceeds.
