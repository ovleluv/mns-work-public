from __future__ import annotations
import copy
from pathlib import Path
from .model import Side, Unit
from .simulation import Simulation
from .bml import parse_order, apply_bml_document
from .config import deep_update, load_json_config
from .terrain import TerrainModel
from .definitions import default_definition_registry
from .catalog import UnitTypeCatalog
from .database import WeaponCatalog, LoadoutCatalog, PlatformCatalog
from .doctrine_profiles import DoctrineProfileCatalog
from .mounted import initialize_transport_metadata
from . import validation as V



def _project_resource(scenario_path: str, ref, default_relative: str | None = None,
                      roots=None) -> Path | None:
    """Resolve scenario resources without tying saved maps to a versioned project folder.

    Explicit scenario-relative/absolute references win when they exist and lie inside an
    allowed root (the scenario's folder tree or the project tree).  Standard engine resources
    transparently fall back to the currently running project so maps copied out of an older
    release remain loadable.  A reference outside the allowed roots is never read.
    """
    root = Path(__file__).resolve().parent.parent
    roots = roots if roots is not None else V.allowed_resource_roots(scenario_path)
    if ref:
        rp = Path(str(ref))
        candidate = rp.resolve() if rp.is_absolute() else (Path(scenario_path).parent / rp).resolve()
        if not V.is_within(candidate, roots):
            if not default_relative:
                raise V.ValidationError(f"resource {ref!r} resolves outside the allowed folders")
        elif candidate.exists():
            return candidate
    if default_relative:
        fallback = (root / default_relative).resolve()
        if fallback.exists():
            return fallback
    return None


def _explicit_resource(ref, base: Path, roots, field: str) -> Path:
    """Resolve a required reference relative to *base*, confined to the allowed roots."""
    rp = Path(str(ref))
    resolved = rp.resolve() if rp.is_absolute() else (base / rp).resolve()
    if not V.is_within(resolved, roots):
        raise V.ValidationError(f"{field}: {ref!r} resolves outside the allowed folders")
    return resolved


def load_scenario(path: str, bml_files: dict | None = None) -> Simulation:
    """Build a Simulation from a scenario file.

    ``bml_files`` are run-time selections made by the operator (file picker / CLI) and are
    trusted paths; every reference *inside* the scenario is confined to the scenario's folder
    tree or the project tree.
    """
    raw = V.read_json_file(path)
    if not isinstance(raw, dict):
        raise V.ValidationError(f"{path}: scenario must be a JSON object")
    roots = V.allowed_resource_roots(path)
    sim = Simulation(seed=int(V.finite_number(raw.get("seed", 7), "seed")))
    sim.objectives = raw.get("objectives", {})
    sim.world = V.validate_world(raw.get("world", {"width_m": 4000, "height_m": 4000}))
    for name, pos in dict(sim.objectives).items():
        V.point(pos, f"objectives.{name}", sim.world)
    V.validate_scenario_units(raw.get("units", []), sim.world)

    # Configuration precedence:
    # built-in engine fallbacks < external JSON config < scenario-local combat overrides.
    cfg_ref = raw.get("config_file")
    cfg_path = _project_resource(path, cfg_ref, "config/defaults.json", roots)
    if cfg_path:
        cfg = load_json_config(cfg_path)
        deep_update(sim.combat_config, dict(cfg.get("combat", {})))
    deep_update(sim.combat_config, dict(raw.get("combat", {})))

    terrain_ref = raw.get("terrain_file")
    terrain_path = _explicit_resource(terrain_ref, Path(path).parent, roots, "terrain_file") if terrain_ref else None
    terrain_data = V.validate_terrain(load_json_config(terrain_path), "terrain") if terrain_path else {}
    sim.terrain = TerrainModel(terrain_data, sim.combat_config.get("navigation", {}))
    sim.terrain.world = sim.world
    sim.terrain._units_provider = lambda: sim.units.values()
    sim.terrain_file = str(terrain_path) if terrain_path else ""

    doctrine_ref = raw.get("artillery_doctrine_file")
    sim.artillery_doctrine_profiles = {}
    sim.targeting_doctrine = {}
    doctrine_path = _project_resource(path, doctrine_ref, "config/artillery_doctrine.json", roots)
    if doctrine_path:
        doctrine_data = load_json_config(doctrine_path)
        sim.artillery_doctrine_profiles = dict(doctrine_data.get("profiles", {}))

    targeting_ref = raw.get("targeting_doctrine_file")
    targeting_path = _project_resource(path, targeting_ref, "config/targeting_doctrine.json", roots)
    if targeting_path:
        targeting_data = load_json_config(targeting_path)
        profiles = dict(targeting_data.get("profiles", {}))
        active = str(targeting_data.get("active_profile", "DEFAULT_ARMY"))
        sim.targeting_doctrine = dict(profiles.get(active, {}))
        sim.targeting_doctrine["profile_name"] = active

    # Unit templates may live in the scenario or an external TO&E library.
    unit_type_raw = raw.get("unit_types")
    unit_type_lib = {}
    unit_type_lib_path = None
    if unit_type_raw is None:
        ref = raw.get("unit_types_file")
        unit_type_lib_path = _project_resource(path, ref, "config/toe_templates.json", roots)
        if unit_type_lib_path is None:
            raise ValueError("Scenario requires unit_types or an available project TO&E library")
        unit_type_lib = load_json_config(unit_type_lib_path)
        unit_type_raw = unit_type_lib["unit_types"]

    # Local tactical doctrine profiles are side-neutral definitions, selected explicitly by each
    # unit (or left unset for the historical generic behavior).  Loading them here mirrors the
    # weapon/loadout catalogs and keeps DoctrineEngine free of file I/O.
    sim.doctrine_profiles = {}
    doctrine_profiles_ref = raw.get("doctrine_profiles_file", unit_type_lib.get("doctrine_profiles_file"))
    if doctrine_profiles_ref:
        ref_path = Path(str(doctrine_profiles_ref))
        base = unit_type_lib_path.parent if raw.get("doctrine_profiles_file") is None and unit_type_lib_path else Path(path).parent
        doctrine_profiles_path = _explicit_resource(ref_path, base, roots, "doctrine_profiles_file")
        sim.doctrine_profiles = DoctrineProfileCatalog.from_json(doctrine_profiles_path).profiles
        sim.doctrine_profiles_file = str(doctrine_profiles_path)

    # External flat definition DB. Weapon definitions are side/faction-neutral and are
    # resolved once at load time; the simulation inner loop never reads CSV. Legacy inline
    # weapon definitions remain supported when no database is supplied.
    weapon_catalog = None
    weapon_db_ref = raw.get("weapon_database_file", unit_type_lib.get("weapon_database_file"))
    if weapon_db_ref:
        ref_path = Path(str(weapon_db_ref))
        base = unit_type_lib_path.parent if raw.get("weapon_database_file") is None and unit_type_lib_path else Path(path).parent
        weapon_db_path = _explicit_resource(ref_path, base, roots, "weapon_database_file")
        weapon_catalog = WeaponCatalog.from_csv(weapon_db_path)

    platform_catalog = None
    platform_ref = raw.get("platform_database_file", unit_type_lib.get("platform_database_file"))
    if platform_ref:
        ref_path = Path(str(platform_ref))
        base = unit_type_lib_path.parent if raw.get("platform_database_file") is None and unit_type_lib_path else Path(path).parent
        platform_path = _explicit_resource(ref_path, base, roots, "platform_database_file")
        platform_catalog = PlatformCatalog.from_csv(platform_path)

    loadout_catalog = None
    loadout_ref = raw.get("loadouts_file", unit_type_lib.get("loadouts_file"))
    if loadout_ref:
        ref_path = Path(str(loadout_ref))
        base = unit_type_lib_path.parent if raw.get("loadouts_file") is None and unit_type_lib_path else Path(path).parent
        loadout_path = _explicit_resource(ref_path, base, roots, "loadouts_file")
        loadout_catalog = LoadoutCatalog.from_json(loadout_path)

    # Data-definition construction is isolated from scenario orchestration.
    definition_registry = default_definition_registry(weapon_catalog=weapon_catalog, platform_catalog=platform_catalog)
    unit_catalog = UnitTypeCatalog.from_mapping(unit_type_raw, definition_registry)
    infantry = unit_catalog.types.get("INF_IND")
    if infantry is not None and infantry.branch.upper() == "INFANTRY":
        sim.dismounted_crew_sensor = {
            "detection_range_m": infantry.detection_range_m,
            "visual_sensor": copy.deepcopy(infantry.metadata.get("visual_sensor", {})),
        }

    for r in raw["units"]:
        resolved_name, typ, was_upgraded = unit_catalog.resolve_formation_type(r["type"], r.get("echelon", "PLT"))
        md = dict(r.get("metadata", {})); md.setdefault("branch", typ.branch)
        if r.get("faction") is not None:
            md.setdefault("faction", str(r.get("faction")))
        if r.get("loadout") is not None:
            md.setdefault("loadout", str(r.get("loadout")))
        if r.get("doctrine_profile") is not None:
            profile_name=str(r.get("doctrine_profile"))
            if profile_name not in sim.doctrine_profiles:
                raise KeyError(f"Unit {r['id']} selects unknown doctrine_profile {profile_name!r}")
            md.setdefault("doctrine_profile", profile_name)
        if was_upgraded:
            md["legacy_type_requested"] = r["type"]
            md["resolved_type"] = resolved_name
        # each formation gets its own mutable copy of its TO&E template
        elements = {e.eid: copy.deepcopy(e) for e in typ.elements}

        # Optional per-unit loadout. Slots belong to the formation template; profiles simply
        # map those slots to weapon IDs. This allows same side/type units to carry different
        # systems without cloning a TO&E template. Inline loadout_overrides are ideal for
        # one-off experiments and LLM-generated fast prototypes.
        slot_map = {}
        loadout_name = r.get("loadout")
        if loadout_name:
            if loadout_catalog is None:
                raise ValueError(f"Unit {r['id']} selects loadout {loadout_name!r} but no loadouts_file is configured")
            slot_map.update(loadout_catalog.get(str(loadout_name)))
        slot_map.update({str(k): str(v) for k, v in dict(r.get("loadout_overrides", {})).items()})
        if slot_map:
            if weapon_catalog is None:
                raise ValueError(f"Unit {r['id']} uses a loadout but no weapon_database_file is configured")
            for e in elements.values():
                replaced = []
                for w in e.weapons:
                    slot = str(w.metadata.get("loadout_slot", ""))
                    if slot and slot in slot_map:
                        replaced.append(definition_registry.create_weapon({"weapon_id": slot_map[slot], "slot": slot}))
                    else:
                        replaced.append(w)
                e.weapons = replaced

        # optional per-unit overrides (counts, readiness, etc.)
        for ov in r.get("element_overrides", []):
            if ov["id"] in elements:
                e = elements[ov["id"]]
                if "count" in ov: e.count = int(ov["count"])
                if "initial_count" in ov: e.initial_count = int(ov["initial_count"])
                if "combat_value" in ov: e.combat_value = float(ov["combat_value"])
                if "min_operators" in ov: e.min_operators = int(ov["min_operators"])
                if "metadata" in ov: e.metadata.update(dict(ov["metadata"]))
                # Per-unit weapon inventory. Keys may be stable weapon_id, loadout slot,
                # or legacy display name so hand-authored scenarios remain convenient.
                system_counts = dict(ov.get("weapon_system_counts", {}))
                operators_per = dict(ov.get("weapon_operators_per_system", {}))
                ammo_by_weapon = dict(ov.get("weapon_ammo", {}))
                for w in e.weapons:
                    keys=[str(w.metadata.get("weapon_id","")),str(w.metadata.get("loadout_slot","")),w.name]
                    for key in keys:
                        if key and key in system_counts:
                            w.metadata["system_count"] = max(0,int(system_counts[key])); break
                    for key in keys:
                        if key and key in operators_per:
                            w.metadata["operators_per_system"] = max(1,int(operators_per[key])); break
                    for key in keys:
                        if key and key in ammo_by_weapon:
                            w.ammo_capacity = int(ammo_by_weapon[key]); w.ammo_remaining = int(ammo_by_weapon[key]); break
        u = Unit(
            uid=r["id"], name=r.get("name", r["id"]), side=Side(r["side"]),
            echelon=r.get("echelon", "PLT"), unit_type=typ, pos=tuple(r["pos"]),
            heading_deg=float(r.get("heading_deg", 0.0)),
            watch_heading_deg=float(r.get("watch_heading_deg", r.get("heading_deg", 0.0))),
            parent_id=r.get("parent_id"), metadata=md, elements=elements,
        )
        initialize_transport_metadata(u)
        sim.add_unit(u)
        for o in r.get("orders", []): sim.issue_order(u.uid, parse_order(o))

    for u in sim.units.values():
        if u.parent_id and u.parent_id in sim.units:
            sim.units[u.parent_id].children.append(u.uid)

    # External side-specific BML mission files.  Scenario files describe the battlefield/OOB;
    # BML plans may be selected independently at run time.  For backward compatibility only,
    # load_scenario(path) still honors legacy scenario-embedded bml_files references.  Passing
    # bml_files explicitly (including an empty dict) means "use exactly these run-time plans".
    runtime_bml_selection = bml_files is not None
    if not runtime_bml_selection:
        bml_refs = dict(raw.get("bml_files", {}))
        if raw.get("blue_bml_file"): bml_refs.setdefault("BLUE", raw.get("blue_bml_file"))
        if raw.get("red_bml_file"): bml_refs.setdefault("RED", raw.get("red_bml_file"))
    else:
        bml_refs = {str(k).upper(): v for k, v in dict(bml_files).items() if v}

    sim.bml_files = {}
    for side, ref in bml_refs.items():
        ref_path = Path(str(ref))
        # Run-time selections come from the file picker / CLI and are cwd-relative when not
        # absolute. Legacy references embedded inside a scenario remain scenario-relative.
        if runtime_bml_selection:
            bml_path = ref_path.resolve()
        else:
            bml_path = _explicit_resource(ref_path, Path(path).parent, roots, f"bml_files.{side}")
        bml_raw = load_json_config(bml_path)
        apply_bml_document(sim, bml_raw, expected_side=str(side).upper())
        sim.bml_files[str(side).upper()] = str(bml_path)
        # Replays are shared artefacts: record a project-relative name, not the absolute path.
        sim.log("BML_LOADED", side=str(side).upper(), file=V.display_path(bml_path))

    # Optional load-time aggregation, e.g. three platoons displayed/fought as one company.
    for a in raw.get("aggregations", []):
        sim.aggregate_units(
            new_uid=a["id"], name=a.get("name", a["id"]), child_ids=a["children"],
            echelon=a.get("echelon", "COY"), pos=tuple(a["pos"]) if "pos" in a else None,
        )
    return sim
