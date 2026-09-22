"""Surviving vehicle crews become count-conserving, movable foot entities."""
import pytest

from mnsim.model import Track, UnitState
from mnsim.scenario import load_scenario


def setup_tank():
    sim = load_scenario("scenarios/canada_tdg4.json")
    tank = sim.units["R-TANK_PLT-1"]
    return sim, tank


def escaped(sim):
    return [u for u in sim.units.values() if u.metadata.get("escaped_from")]


@pytest.mark.parametrize("state", ["MOBILITY_KILL", "DISABLED"])
def test_destroyed_detached_tank_releases_crew_and_foot_unit_can_move(state):
    sim, tank = setup_tank()
    total = sum(u.personnel for u in sim.units.values())
    sim.damage.apply_equipment_effect(tank, tank.elements["tanks"], state)
    detached = sim.units[tank.uid + "-DET-1"]
    assert not escaped(sim)
    assert detached.unit_type.max_speed_mps == 0
    sim.damage.apply_equipment_effect(detached, detached.elements["tanks"], "DESTROYED")
    crew, = escaped(sim)
    assert crew.personnel == 4 and crew.equipment == 0
    assert crew.pos == detached.pos
    assert crew.branch == "INFANTRY"
    assert crew.unit_type.metadata["mobility_class"] == "FOOT"
    assert not crew.operational_weapons()
    assert not detached.alive and detached.personnel == 0
    assert tank.personnel == 12 and tank.equipment == 3
    assert sum(u.personnel for u in sim.units.values()) == total
    assert all(crew.element_exposed(e) for e in crew.elements.values())
    start = crew.pos
    crew.state = UnitState.MOVING
    sim._move_toward(crew, (start[0] + 20, start[1]), 1)
    assert crew.pos != start
    assert detached.pos == start
    sim.damage.apply_equipment_effect(detached, detached.elements["tanks"], "DESTROYED")
    assert len(escaped(sim)) == 1


def test_partial_and_total_platoon_destruction_preserve_other_crews_and_counts():
    sim, tank = setup_tank()
    element = tank.elements["tanks"]
    total = sum(u.personnel for u in sim.units.values())
    initial = sum(u.initial_personnel for u in sim.units.values())
    for i in range(4):
        sim.damage.apply_equipment_effect(tank, element, "DESTROYED")
        assert tank.personnel == 12 - 4*i
        assert tank.equipment == 3 - i
        assert len(escaped(sim)) == i + 1
        assert sum(u.personnel for u in sim.units.values()) == total
        assert sum(u.initial_personnel for u in sim.units.values()) == initial
        if i < 3:
            assert element.mobile_item_count == 3 - i
            assert tank.operational_weapons()
    assert not tank.alive
    assert all(u.personnel == 4 for u in escaped(sim))


@pytest.mark.parametrize("survivors", [0, 2])
def test_only_surviving_explicit_crew_leave_destroyed_vehicle(survivors):
    sim, tank = setup_tank()
    sim.damage.apply_equipment_effect(tank, tank.elements["tanks"], "DISABLED")
    detached = sim.units[tank.uid + "-DET-1"]
    detached.elements["tanks_crew"].count = survivors
    total = sum(u.personnel for u in sim.units.values())
    sim.damage.apply_equipment_effect(detached, detached.elements["tanks"], "DESTROYED")
    assert sum(u.personnel for u in escaped(sim)) == survivors
    assert len(escaped(sim)) == (1 if survivors else 0)
    assert sum(u.personnel for u in sim.units.values()) == total


def test_unarmed_escaped_crew_with_perceived_threat_withdraws_on_unit_step():
    sim, tank = setup_tank()
    enemy = sim.units["B-INF_PLT-1"]
    enemy.pos = (tank.pos[0] + 100, tank.pos[1])
    tank.local_tracks[enemy.uid] = Track(
        track_id="crew-threat", target_id=enemy.uid, estimated_pos=enemy.pos,
        position_error_m=0, confidence=1, last_seen_time=sim.time, state="IDENTIFIED")
    sim.damage.apply_equipment_effect(tank, tank.elements["tanks"], "DESTROYED")
    crew, = escaped(sim)
    start = crew.pos
    sim._step_unit(crew, 1)
    assert crew.state == UnitState.RETREATING
    assert crew.pos[0] < start[0]
    assert any(e["kind"] == "CREW_DISMOUNTED" and e["child"] == crew.uid for e in sim.logs)


def test_legacy_implicit_crew_does_not_create_unmodelled_personnel():
    sim, tank = setup_tank()
    tank.elements.pop("tanks_crew")
    tank.elements["tanks"].metadata.pop("crew_elements", None)
    sim.damage.apply_equipment_effect(tank, tank.elements["tanks"], "DESTROYED")
    assert escaped(sim) == []


def test_shared_crew_pool_keeps_operators_for_other_vehicle_elements():
    import copy
    sim, tank = setup_tank()
    first = tank.elements["tanks"]
    first.count = first.initial_count = 1
    first.item_states = ["OPERATIONAL"]
    other = copy.deepcopy(first)
    other.eid = "other_tank"
    tank.elements[other.eid] = other
    tank.elements["tanks_crew"].count = 8
    sim.damage.apply_equipment_effect(tank, first, "DESTROYED")
    crew, = escaped(sim)
    assert crew.personnel == 4
    assert tank.personnel == 4
    assert other.mobile_item_count == 1
    assert tank.operational_weapons()


def test_personal_weapon_inventory_and_ammo_are_transferred_not_duplicated():
    import copy
    sim, tank = setup_tank()
    weapon = copy.deepcopy(sim.units["B-INF_PLT-1"].elements["rifle_1"].weapons[0])
    weapon.ammo_remaining = 160
    weapon.ammo_capacity = 320
    weapon.metadata["weapon_count"] = 16
    tank.elements["tanks_crew"].weapons = [weapon]
    sim.damage.apply_equipment_effect(tank, tank.elements["tanks"], "DESTROYED")
    crew, = escaped(sim)
    transferred = crew.elements["tanks_crew"].weapons[0]
    assert transferred.ammo_remaining == 40
    assert weapon.ammo_remaining == 120
    assert transferred.ammo_capacity + weapon.ammo_capacity == 320
    assert transferred.metadata["weapon_count"] == 4
    assert weapon.metadata["weapon_count"] == 12
    assert all(e.category == "PERSONNEL" for e in crew.elements.values())
