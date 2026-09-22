"""Ownership contracts across aggregation, damage, and crew evacuation."""
import pytest

from mnsim.model import FormationElement, Side, Unit, UnitState, UnitType, WeaponModel
from mnsim.simulation import Simulation
from mnsim.mounted import (board_external, disembark_external, free_seats,
                           mount_organic, transport_capacity)


def vehicle_unit(sim, uid, vehicles=2, crew_size=4, ammo=17, passengers=0):
    gun = WeaponModel("gun", "ANTI_ARMOR", 1000, 10, .5,
                      ammo_capacity=40, ammo_remaining=20)
    rifle = WeaponModel("personal", "ANTI_PERSONNEL", 100, 10, .5,
                        ammo_capacity=ammo, ammo_remaining=ammo,
                        metadata={"weapon_count": vehicles * crew_size,
                                  "system_count": vehicles * crew_size})
    platform = FormationElement("vehicle", "vehicle", "EQUIPMENT", "ARMOR",
                                vehicles, vehicles, weapons=[gun],
                                metadata={"crew_elements": ["crew"], "crew": crew_size,
                                          "passengers": passengers})
    crew = FormationElement("crew", "crew", "PERSONNEL", "CREW",
                            vehicles * crew_size, vehicles * crew_size, weapons=[rifle],
                            metadata={"protected_by": "vehicle"})
    typ = UnitType("test_vehicle", "ARMOR", 10, 1000)
    unit = Unit(uid, uid, Side.BLUE, "PLT", typ, (100, 100),
                elements={"vehicle": platform, "crew": crew})
    sim.add_unit(unit)
    return unit


def owned_units(sim):
    # Aggregation snapshots are not owners. Inactive embarked passengers still are.
    return [u for u in sim.units.values()
            if u.state != UnitState.AGGREGATED or u.metadata.get("embarked_in")]


def inventory(sim):
    units = owned_units(sim)
    return (sum(u.personnel for u in units), sum(u.initial_personnel for u in units),
            sum(u.equipment for u in units), sum(u.initial_equipment for u in units),
            sum(w.ammo_remaining for u in units for e in u.elements.values() for w in e.weapons),
            sum(w.ammo_capacity for u in units for e in u.elements.values() for w in e.weapons))


@pytest.mark.parametrize("crew_ref", [["crew"], "crew", None])
def test_aggregate_preserves_crew_references_and_protection(crew_ref):
    sim = Simulation()
    a, b = vehicle_unit(sim, "A"), vehicle_unit(sim, "B", crew_size=3)
    for u in (a, b):
        if crew_ref is None:
            u.elements["vehicle"].metadata.pop("crew_elements")
        else:
            u.elements["vehicle"].metadata["crew_elements"] = crew_ref
    before = inventory(sim)
    parent = sim.aggregate_units("P", "company", ["A", "B"])
    for u in (a, b):
        v = parent.elements[f"{u.uid}:vehicle"]
        c = parent.elements[f"{u.uid}:crew"]
        assert not parent.element_exposed(c)
        assert parent.firepower(v, v.weapons[0]).participants == 2
    parent.elements["A:crew"].count = 0
    v = parent.elements["A:vehicle"]
    assert parent.firepower(v, v.weapons[0]).participants == 0
    v = parent.elements["B:vehicle"]
    assert parent.firepower(v, v.weapons[0]).participants == 2
    parent.elements["A:crew"].count = 8
    sim.deaggregate_unit("P")
    assert inventory(sim) == before
    assert not a.element_exposed(a.elements["crew"])


def test_aggregate_change_deaggregate_preserves_damage_ammo_and_detached_ownership():
    sim = Simulation()
    a, b = vehicle_unit(sim, "A"), vehicle_unit(sim, "B")
    before = inventory(sim)
    parent = sim.aggregate_units("P", "company", ["A", "B"])
    v = parent.elements["A:vehicle"]
    assert v.weapons[0].expend_round()
    sim.damage.apply_equipment_effect(parent, v, "FIREPOWER_KILL", item_index=0)
    sim.damage.apply_equipment_effect(parent, v, "MOBILITY_KILL", item_index=1)
    detached = sim.units["P-DET-1"]
    sim.damage.apply_equipment_effect(detached, detached.elements[v.eid], "DESTROYED")
    escaped, = [u for u in sim.units.values() if u.metadata.get("escaped_from")]
    sim.deaggregate_unit("P")
    assert a.elements["vehicle"].item_states == ["FIREPOWER_KILL"]
    assert a.elements["vehicle"].initial_count == 1
    assert a.personnel == 4 and escaped.personnel == 4
    assert detached.parent_id == a.uid and detached.uid in a.children
    assert escaped.parent_id == detached.uid
    expected = list(before)
    expected[2] -= 1  # destroyed vehicle
    expected[4] -= 1  # expended round
    assert inventory(sim) == tuple(expected)
    for i in range(3):
        p = sim.aggregate_units(f"REPEAT-{i}", "company", ["A", "B"])
        sim.deaggregate_unit(p.uid)
        assert inventory(sim) == tuple(expected)
        assert a.elements["vehicle"].item_states == ["FIREPOWER_KILL"]
    assert sim.deaggregate_unit(parent.uid) == []  # old snapshot cannot restore again
    assert inventory(sim) == tuple(expected)


def test_nested_aggregation_restores_destroyed_items_without_resurrection():
    sim = Simulation()
    a = vehicle_unit(sim, "A")
    before = inventory(sim)
    sim.aggregate_units("P", "company", ["A"])
    battalion = sim.aggregate_units("BN", "battalion", ["P"], echelon="BN")
    v = battalion.elements["P:A:vehicle"]
    sim.damage.apply_equipment_effect(battalion, v, "DESTROYED", item_index=0)
    sim.deaggregate_unit("BN")
    sim.deaggregate_unit("P")
    assert a.elements["vehicle"].item_states == ["DESTROYED", "OPERATIONAL"]
    assert a.elements["vehicle"].mobile_item_count == 1
    crew, = [u for u in sim.units.values() if u.metadata.get("escaped_from")]
    assert crew.parent_id == "A" and crew.uid in a.children
    assert a.personnel == 4 and crew.personnel == 4
    expected = list(before)
    expected[2] -= 1
    assert inventory(sim) == tuple(expected)


@pytest.mark.parametrize("ammo", [0, 1, 17, -1])
@pytest.mark.parametrize("state", ["MOBILITY_KILL", "DISABLED"])
def test_armed_crew_detach_destroy_escape_conserves_inventory(ammo, state):
    sim = Simulation()
    tank = vehicle_unit(sim, "T", ammo=ammo)
    for number in (1, 2):
        sim.damage.apply_equipment_effect(tank, tank.elements["vehicle"], state, item_index=0)
        detached = sim.units[f"T-DET-{number}"]
        assert detached.elements["crew"].weapons
        sim.damage.apply_equipment_effect(detached, detached.elements["vehicle"], "DESTROYED")
        crew = sim.units[f"{detached.uid}-CREW-1"]
        assert crew.personnel == 4 and crew.active
        assert crew.elements["crew"].weapons[0].metadata["weapon_count"] == 4
        assert crew.elements["crew"].weapons[0].metadata["system_count"] == 4
        assert crew.element_exposed(crew.elements["crew"])
        assert not detached.alive and detached.personnel == 0
        units = owned_units(sim)
        rifles = [w for u in units for e in u.elements.values() for w in e.weapons if w.name == "personal"]
        assert sum(u.personnel for u in units) == 8
        assert sum(u.initial_personnel for u in units) == 8
        assert sum(w.metadata["weapon_count"] for w in rifles) == 8
        if ammo >= 0:
            assert sum(w.ammo_remaining for w in rifles) == ammo
            assert sum(w.ammo_capacity for w in rifles) == ammo
        else:
            assert all(w.ammo_remaining == w.ammo_capacity == -1 for w in rifles)
        logs_before = len(sim.logs)
        sim.damage.apply_equipment_effect(detached, detached.elements["vehicle"], "DESTROYED")
        assert len(sim.logs) == logs_before
    assert tank.personnel == 0 and not tank.operational_weapons()


def test_missing_aggregate_element_does_not_recreate_old_equipment():
    sim = Simulation()
    a = vehicle_unit(sim, "A")
    parent = sim.aggregate_units("P", "company", ["A"])
    parent.elements.pop("A:vehicle")
    sim.deaggregate_unit("P")
    assert a.equipment == 0
    assert "vehicle" not in a.elements


# Passenger ownership uses the same damage resolver as crew evacuation.


def foot_unit(sim, uid, personnel):
    rifle = WeaponModel("rifle", "ANTI_PERSONNEL", 100, 10, .5,
                        ammo_remaining=23, ammo_capacity=30,
                        metadata={"weapon_count": personnel})
    element = FormationElement("rifle", "rifle", "PERSONNEL", "RIFLE",
                               personnel, personnel, weapons=[rifle])
    unit = Unit(uid, uid, Side.BLUE, "SQD", UnitType("inf", "INFANTRY", 1.8, 500),
                (100, 100), elements={element.eid: element})
    sim.add_unit(unit)
    return unit


@pytest.mark.parametrize("item_index", [0, 1, 2])
@pytest.mark.parametrize("state", ["MOBILITY_KILL", "DISABLED"])
def test_board_detach_destroy_disembark_preserves_passenger_ownership(item_index, state):
    sim = Simulation()
    carrier = vehicle_unit(sim, "C", vehicles=3, crew_size=3, passengers=6)
    squads = [foot_unit(sim, f"S{i}", 6) for i in range(3)]
    before = inventory(sim)
    for squad in squads:
        assert board_external(sim, squad, carrier)
    sim.damage.apply_equipment_effect(carrier, carrier.elements["vehicle"], state,
                                     item_index=item_index)
    detached = sim.units["C-DET-1"]
    affected = squads[item_index]
    assert not affected.active
    assert affected.metadata["embarked_in"] == detached.uid
    assert detached.metadata["external_embarked_units"] == [affected.uid]
    assert affected.metadata["vehicle_seat_allocation"] == [
        {"element": "vehicle", "vehicle_index": 0, "seats": 6}]
    for i, squad in enumerate(squads):
        if i == item_index:
            continue
        assert squad.metadata["embarked_in"] == carrier.uid
        allocation = squad.metadata["vehicle_seat_allocation"]
        assert allocation == carrier.metadata["external_passenger_allocations"][squad.uid]
        assert allocation[0]["vehicle_index"] == i - (i > item_index)
    assert inventory(sim) == before
    sim.damage.apply_equipment_effect(detached, detached.elements["vehicle"], "DESTROYED")
    assert affected.alive and affected.pos == detached.pos
    assert "embarked_in" not in affected.metadata
    assert "vehicle_seat_allocation" not in affected.metadata
    assert detached.metadata["external_embarked_units"] == []
    assert detached.metadata["external_passenger_allocations"] == {}
    assert not detached.alive
    assert sorted(u.uid for u in disembark_external(sim, carrier)) == sorted(
        s.uid for s in squads if s is not affected)
    assert free_seats(sim, carrier) == 12
    expected = list(before)
    expected[2] -= 1
    assert inventory(sim) == tuple(expected)
    assert len([r for r in sim.logs if r["kind"] == "DISEMBARK_COMPLETE"
                and r["unit"] == affected.uid]) == 1


def test_destroyed_slot_does_not_shift_other_passengers_or_get_reused():
    sim = Simulation()
    carrier = vehicle_unit(sim, "C", vehicles=3, passengers=6)
    squads = [foot_unit(sim, f"S{i}", 6) for i in range(3)]
    for squad in squads:
        assert board_external(sim, squad, carrier)
    sim.damage.apply_equipment_effect(carrier, carrier.elements["vehicle"], "DESTROYED", item_index=1)
    assert squads[1].alive and "embarked_in" not in squads[1].metadata
    assert squads[2].metadata["vehicle_seat_allocation"][0]["vehicle_index"] == 2
    disembark_external(sim, carrier, squads[0].uid)
    replacement = foot_unit(sim, "R", 6)
    assert board_external(sim, replacement, carrier)
    assert replacement.metadata["vehicle_seat_allocation"][0]["vehicle_index"] == 0
    assert free_seats(sim, carrier) == 0
    sim.damage.apply_equipment_effect(carrier, carrier.elements["vehicle"], "MOBILITY_KILL", item_index=0)
    assert squads[2].metadata["vehicle_seat_allocation"][0]["vehicle_index"] == 1


def test_passenger_group_spanning_vehicles_emergency_disembarks_as_one_unit():
    sim = Simulation()
    carrier = vehicle_unit(sim, "C", vehicles=2, passengers=6)
    squad = foot_unit(sim, "S", 9)
    before = inventory(sim)
    assert board_external(sim, squad, carrier)
    sim.damage.apply_equipment_effect(carrier, carrier.elements["vehicle"], "MOBILITY_KILL", item_index=0)
    assert squad.alive and squad.personnel == 9
    assert "embarked_in" not in squad.metadata
    assert carrier.metadata["external_passenger_allocations"] == {}
    assert free_seats(sim, carrier) == 6
    assert not board_external(sim, squad, carrier)
    assert inventory(sim) == before


@pytest.mark.parametrize("state", ["MOBILITY_KILL", "DESTROYED"])
def test_organic_and_external_passengers_do_not_become_crew_or_ghosts(state):
    sim = Simulation()
    carrier = vehicle_unit(sim, "C", vehicles=2, crew_size=3, passengers=6)
    organic = FormationElement("inf", "inf", "PERSONNEL", "RIFLE", 6, 6,
                               metadata={"dismountable": True})
    carrier.elements["inf"] = organic
    external = foot_unit(sim, "S", 6)
    before = inventory(sim)
    assert board_external(sim, external, carrier)
    sim.damage.apply_equipment_effect(carrier, carrier.elements["vehicle"], state, item_index=0)
    dismounted = sim.units[carrier.metadata["dismount_child_id"]]
    assert dismounted.alive and dismounted.personnel == 6
    assert external.metadata["embarked_in"] == carrier.uid
    assert carrier.personnel == 3
    # The remaining vehicle is full of external passengers; organic troops cannot remount.
    assert not mount_organic(sim, carrier)
    disembark_external(sim, carrier)
    assert mount_organic(sim, carrier)
    assert carrier.personnel == 9 and not dismounted.active
    assert transport_capacity(carrier) == 6
    if state == "MOBILITY_KILL":
        detached = sim.units["C-DET-1"]
        sim.damage.apply_equipment_effect(detached, detached.elements["vehicle"], "DESTROYED")
    escaped = [u for u in sim.units.values() if u.metadata.get("escaped_from")]
    assert sum(u.personnel for u in escaped) == 3
    expected = list(before)
    expected[2] -= 1
    assert inventory(sim) == tuple(expected)
    assert dismounted.personnel == 0


def test_last_carrier_destroyed_releases_passengers_once_and_cannot_board_again():
    sim = Simulation()
    carrier = vehicle_unit(sim, "C", vehicles=1, crew_size=3, passengers=6)
    squad = foot_unit(sim, "S", 6)
    assert board_external(sim, squad, carrier)
    sim.damage.apply_equipment_effect(carrier, carrier.elements["vehicle"], "DESTROYED", item_index=0)
    assert squad.alive and squad.personnel == 6
    assert not carrier.alive and free_seats(sim, carrier) == 0
    assert not board_external(sim, squad, carrier)
    assert disembark_external(sim, carrier) == []
    assert len([r for r in sim.logs if r["kind"] == "DISEMBARK_COMPLETE"]) == 1


def test_overlapping_unit_id_prefixes_remain_separate_after_deaggregation():
    sim = Simulation()
    a, b = vehicle_unit(sim, "A"), vehicle_unit(sim, "A:B")
    before = inventory(sim)
    parent = sim.aggregate_units("P", "company", [a.uid, b.uid])
    parent.elements["A:B:vehicle"].weapons[0].expend_round()
    sim.deaggregate_unit(parent.uid)
    assert set(a.elements) == set(b.elements) == {"vehicle", "crew"}
    assert a.elements["vehicle"].weapons[0].ammo_remaining == 20
    assert b.elements["vehicle"].weapons[0].ammo_remaining == 19
    after = list(before)
    after[4] -= 1
    assert inventory(sim) == tuple(after)
