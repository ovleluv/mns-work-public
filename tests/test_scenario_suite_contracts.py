import json
from pathlib import Path

import pytest

from mnsim.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[1]

AUTHORED_SCENARIOS = (
    "scenarios/_usercheck/tdg1.json",
    "scenarios/bench_mg.json",
    "scenarios/bench_user.json",
    "scenarios/canada_tdg4.json",
    "scenarios/demo.json",
    "scenarios/tdg1.json",
    "scenarios/tdg3.json",
    "scenarios/test2.json",
    "scenarios/test3.json",
)

AUTHORED_TERRAINS = (
    "scenarios/_usercheck/tdg1_terrain.json",
    "scenarios/bench_2_terrain.json",
    "scenarios/tdg1_terrain.json",
    "scenarios/tdg3_terrain.json",
    "scenarios/test2_terrain.json",
    "scenarios/test3_terrain.json",
)

AUTHORED_BMLS = (
    "scenarios/TDG4_solution(GPT)_blue_bml.json",
    "scenarios/TDG4_solution(GPT)_red_bml.json",
    "scenarios/bml_phase_doctrine_example.json",
    "scenarios/mounted_transport_bml_example.json",
    "scenarios/tdg1_addTargetPos_blue_bml.json",
    "scenarios/tdg1_blue_bml.json",
    "scenarios/tdg1_red_bml.json",
    "scenarios/tdg3_blue_bml.json",
    "scenarios/tdg3_red_bml.json",
    "scenarios/tdg4_blue_bml_ver0.json",
    "scenarios/tdg4_red_bml_ver0.json",
    "scenarios/test2_blue_bml.json",
    "scenarios/test2_blue_bml_coordinate_example.json",
    "scenarios/test2_red_bml.json",
)

BML_PAIRINGS = (
    (
        "tdg1_default",
        "scenarios/tdg1.json",
        {
            "BLUE": "scenarios/tdg1_blue_bml.json",
            "RED": "scenarios/tdg1_red_bml.json",
        },
    ),
    (
        "tdg1_with_target_pos",
        "scenarios/tdg1.json",
        {
            "BLUE": "scenarios/tdg1_addTargetPos_blue_bml.json",
            "RED": "scenarios/tdg1_red_bml.json",
        },
    ),
    (
        "tdg3",
        "scenarios/tdg3.json",
        {"BLUE": "scenarios/tdg3_blue_bml.json", "RED": "scenarios/tdg3_red_bml.json"},
    ),
    (
        "test2_targets",
        "scenarios/test2.json",
        {
            "BLUE": "scenarios/test2_blue_bml.json",
            "RED": "scenarios/test2_red_bml.json",
        },
    ),
    (
        "test2_coordinates",
        "scenarios/test2.json",
        {
            "BLUE": "scenarios/test2_blue_bml_coordinate_example.json",
            "RED": "scenarios/test2_red_bml.json",
        },
    ),
    (
        "tdg4_ver0",
        "scenarios/canada_tdg4.json",
        {
            "BLUE": "scenarios/tdg4_blue_bml_ver0.json",
            "RED": "scenarios/tdg4_red_bml_ver0.json",
        },
    ),
    (
        "tdg4_solution",
        "scenarios/canada_tdg4.json",
        {
            "BLUE": "scenarios/TDG4_solution(GPT)_blue_bml.json",
            "RED": "scenarios/TDG4_solution(GPT)_red_bml.json",
        },
    ),
    (
        "phase_example",
        "scenarios/tdg1.json",
        {
            "BLUE": "scenarios/bml_phase_doctrine_example.json",
        },
    ),
)

ENGINE_ORDER_KINDS = {
    "ATTACK",
    "ATTACK_STRUCTURE",
    "ATTACK_UNIT",
    "BOARD",
    "BUILD_BARRICADE",
    "DEFEND",
    "DEFEND_AREA",
    "DESTROY_UNIT",
    "DISEMBARK",
    "DISMOUNT",
    "ENTER_BUILDING",
    "EXIT_BUILDING",
    "HOLD",
    "MOUNT",
    "MOVE",
    "RETREAT",
    "STRIKE_INFRASTRUCTURE",
    "WAIT",
}

EQUIPMENT_DAMAGE_STATES = {
    "OPERATIONAL",
    "MOBILITY_KILL",
    "FIREPOWER_KILL",
    "DISABLED",
    "DESTROYED",
}

SMOKE_RUNS = tuple(
    (f"base:{rel.removeprefix('scenarios/').removesuffix('.json')}", rel, {})
    for rel in AUTHORED_SCENARIOS
) + tuple(
    (f"bml:{name}", scenario, bml_files)
    for name, scenario, bml_files in BML_PAIRINGS
)


def _read_json(rel_path):
    return json.loads((ROOT / rel_path).read_text(encoding="utf-8"))


def _absolute_bml_files(bml_files):
    return {side: str((ROOT / rel).resolve()) for side, rel in bml_files.items()}


def _classify_scenario_asset(raw):
    if isinstance(raw, dict) and "units" in raw:
        return "scenario"
    if isinstance(raw, dict) and any(key in raw for key in ("areas", "bridges", "rivers", "roads")):
        return "terrain"
    if isinstance(raw, dict) and "side" in raw and any(key in raw for key in ("missions", "phases", "orders_by_unit")):
        return "bml"
    return "unknown"


def _missions_from_bml(raw):
    missions = list(raw.get("missions", []))
    for phase in raw.get("phases", []):
        missions.extend(phase.get("missions", []))
    return missions


def _coordinate_fields(raw):
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in {"destination", "center", "position", "target_position", "last_known_position"}:
                yield key, value
            yield from _coordinate_fields(value)
    elif isinstance(raw, list):
        for item in raw:
            yield from _coordinate_fields(item)


def _is_xy(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(coord, (int, float)) for coord in value)
    )


def _assert_inside_world(point, world):
    x, y = point
    assert 0.0 <= float(x) <= float(world["width_m"])
    assert 0.0 <= float(y) <= float(world["height_m"])


def _assert_order_supported(order):
    assert order.kind in ENGINE_ORDER_KINDS
    if order.on_true:
        assert str(order.on_true.get("task", order.on_true.get("kind", ""))).upper() in ENGINE_ORDER_KINDS | {
            "ATTACK_POSITION",
            "DEFEND_POSITION",
            "MOVE_TO",
            "SECURE_AREA",
            "SEIZE",
            "WITHDRAW",
        }
    if order.on_deadline:
        assert str(order.on_deadline.get("task", order.on_deadline.get("kind", ""))).upper() in ENGINE_ORDER_KINDS | {
            "ATTACK_POSITION",
            "DEFEND_POSITION",
            "MOVE_TO",
            "SECURE_AREA",
            "SEIZE",
            "WITHDRAW",
        }


def _assert_unit_state_sane(sim):
    width = float(sim.world["width_m"])
    height = float(sim.world["height_m"])
    for unit in sim.units.values():
        assert 0.0 <= float(unit.pos[0]) <= width
        assert 0.0 <= float(unit.pos[1]) <= height
        assert unit.initial_strength > 0.0
        assert unit.current_strength >= 0.0
        assert 0.0 <= unit.strength_ratio <= 1.0
        assert unit.personnel >= 0
        assert unit.equipment >= 0
        assert unit.branch == unit.branch.upper()
        assert sim.terrain.passable(unit, unit.pos)

        for order in list(unit.order_queue) + ([unit.current_order] if unit.current_order else []):
            _assert_order_supported(order)

        for element in unit.elements.values():
            assert element.count >= 0
            assert element.initial_count >= 0
            if element.category.upper() == "EQUIPMENT":
                element.ensure_item_states()
                assert all(state in EQUIPMENT_DAMAGE_STATES for state in element.item_states)
            for weapon in element.weapons:
                assert weapon.range_m >= 0.0
                assert weapon.shots_per_min >= 0.0
                assert weapon.pk >= 0.0
                assert weapon.ammo_capacity >= -1
                assert weapon.ammo_remaining >= -1


def _assert_infrastructure_state_sane(sim):
    for bridge in sim.terrain.bridges:
        assert float(bridge.get("integrity", 1.0)) >= 0.0
        if bridge.get("destroyed"):
            assert float(bridge.get("integrity", 0.0)) <= 0.0
    for area in sim.terrain.areas:
        if str(area.get("type", "")).upper() == "BUILDING":
            assert float(area.get("integrity", 1.0)) >= 0.0
            if area.get("destroyed"):
                assert float(area.get("integrity", 0.0)) <= 0.0


def _assert_fire_logs_reference_real_opponents(sim):
    for record in sim.logs:
        if record.get("kind") != "FIRE":
            continue
        shooter = sim.units.get(record.get("shooter"))
        target = sim.units.get(record.get("target"))
        assert shooter is not None
        assert target is not None
        assert shooter.side != target.side


def _assert_missions_match_loaded_orders(sim, bml_files):
    for side, rel_path in bml_files.items():
        raw = _read_json(rel_path)
        for mission in _missions_from_bml(raw):
            uid = str(mission["unit"])
            assert uid in sim.units
            assert sim.units[uid].side.value == side

            mission_id = str(mission.get("id", f"BML-{uid}-{mission.get('task', mission.get('kind', 'HOLD'))}"))
            orders = list(sim.units[uid].order_queue)
            assert any(order.order_id == mission_id for order in orders)

            target = mission.get("target")
            if target is not None and str(mission.get("task", "")).upper() in {"ATTACK_UNIT", "DESTROY_UNIT"}:
                assert str(target) in sim.units


def test_scenario_directory_json_assets_are_classified():
    classified = {"scenario": set(), "terrain": set(), "bml": set()}
    unknown = []

    for path in sorted((ROOT / "scenarios").rglob("*.json")):
        rel = path.relative_to(ROOT).as_posix()
        kind = _classify_scenario_asset(json.loads(path.read_text(encoding="utf-8")))
        if kind == "unknown":
            unknown.append(rel)
        else:
            classified[kind].add(rel)

    assert unknown == []
    assert classified["scenario"] == set(AUTHORED_SCENARIOS)
    assert classified["terrain"] == set(AUTHORED_TERRAINS)
    assert classified["bml"] == set(AUTHORED_BMLS)


@pytest.mark.parametrize("rel_path", AUTHORED_SCENARIOS, ids=lambda path: path.removeprefix("scenarios/"))
def test_authored_scenarios_load_headlessly_and_have_valid_oob(rel_path):
    sim = load_scenario(str(ROOT / rel_path), bml_files={})

    assert sim.units
    assert {unit.side.value for unit in sim.units.values()} == {"BLUE", "RED"}
    assert float(sim.world["width_m"]) > 0.0
    assert float(sim.world["height_m"]) > 0.0
    if sim.terrain_file:
        assert Path(sim.terrain_file).exists()

    _assert_unit_state_sane(sim)
    _assert_infrastructure_state_sane(sim)


@pytest.mark.parametrize("name,scenario,bml_files", BML_PAIRINGS, ids=[case[0] for case in BML_PAIRINGS])
def test_known_bml_pairings_compile_against_their_scenarios(name, scenario, bml_files):
    sim = load_scenario(str(ROOT / scenario), bml_files=_absolute_bml_files(bml_files))

    assert set(sim.bml_files) == set(bml_files)
    assert all(Path(path).exists() for path in sim.bml_files.values())
    _assert_missions_match_loaded_orders(sim, bml_files)
    _assert_unit_state_sane(sim)


@pytest.mark.parametrize("name,scenario,bml_files", BML_PAIRINGS, ids=[case[0] for case in BML_PAIRINGS])
def test_bml_coordinates_stay_inside_scenario_world(name, scenario, bml_files):
    world = _read_json(scenario)["world"]

    for rel_path in bml_files.values():
        raw = _read_json(rel_path)
        for field, point in _coordinate_fields(raw):
            assert _is_xy(point), f"{rel_path}:{field} should be an [x, y] coordinate"
            _assert_inside_world(point, world)


@pytest.mark.parametrize("name,scenario,bml_files", SMOKE_RUNS, ids=[case[0] for case in SMOKE_RUNS])
def test_scenario_smoke_run_preserves_core_state_invariants(name, scenario, bml_files):
    had_initial_orders = any(unit.get("orders") for unit in _read_json(scenario).get("units", [])) or bool(bml_files)
    sim = load_scenario(str(ROOT / scenario), bml_files=_absolute_bml_files(bml_files))

    for _ in range(40):
        sim.tick(0.5)

    assert sim.time == pytest.approx(20.0)
    if had_initial_orders:
        assert any(record["kind"] == "ORDER_START" for record in sim.logs)
    assert not any(record["kind"] == "ORDER_UNKNOWN" for record in sim.logs)

    _assert_unit_state_sane(sim)
    _assert_infrastructure_state_sane(sim)
    _assert_fire_logs_reference_real_opponents(sim)
