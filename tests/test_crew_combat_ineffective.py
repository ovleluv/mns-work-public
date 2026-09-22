"""Crew loss suspends actions without erasing a surviving physical platform."""
import copy
from pathlib import Path

import pytest

from mnsim.model import FormationElement, Order, Side, Unit, UnitState, UnitType, WeaponModel
from mnsim.mobility import movement_speed_mps
from mnsim.scenario import load_scenario
from mnsim.simulation import Simulation


def vehicle(branch="MOTORIZED_INFANTRY", role="CUSTOM_OPERATORS", crew=2):
    sim = Simulation()
    gun = WeaponModel("gun", "ANTI_PERSONNEL", 1000, 10, .5)
    platform = FormationElement("vehicle", "vehicle", "EQUIPMENT", branch, 1, 1,
                                combat_value=5, weapons=[gun],
                                metadata={"crew_elements": ["operators"], "crew": crew})
    operators = FormationElement("operators", "operators", "PERSONNEL", role, crew, crew)
    u = Unit("B", "B", Side.BLUE, "IND", UnitType("vehicle", branch, 10, 1000),
             (100, 100), elements={"vehicle": platform, "operators": operators})
    u.current_order = Order(kind="ATTACK", params={"destination": (500, 100)}, order_id="advance")
    sim.add_unit(u)
    return sim, u


def lose(sim, u, count):
    sim._handle_event("ELEMENT_LOSS", {"target": u.uid, "element": "operators",
                                      "count": count, "source": "enemy"})


@pytest.mark.parametrize("branch", ["MOTORIZED_INFANTRY", "MECH_INFANTRY", "ARMOR",
                                    "INFANTRY", "ARTILLERY", "AIR_DEFENSE", "CUSTOM_BRANCH"])
def test_short_crew_immediately_suspends_attack_for_any_branch(branch):
    sim, u = vehicle(branch)
    assert u.operational_weapons()
    start = u.pos
    lose(sim, u, 1)
    assert u.state == UnitState.COMBAT_INEFFECTIVE
    assert u.crew_failure_reason == "INSUFFICIENT_WEAPON_CREW"
    assert u.alive and u.equipment == 1
    assert u.can_observe and u.can_communicate  # one surviving operator
    assert not u.operational_weapons()
    for _ in range(10):
        sim.tick(.25)
    assert u.pos == start and u.current_order.order_id == "advance"
    assert sum(e["kind"] == "COMBAT_INEFFECTIVE" for e in sim.logs) == 1
    assert not any(e["kind"] in ("FIRE", "ORDER_COMPLETE") for e in sim.logs)
    assert movement_speed_mps(u, state=UnitState.MOVING) == 0


def test_no_crew_blocks_observation_and_communication_but_not_target_damage():
    sim, u = vehicle()
    friend = copy.deepcopy(u)
    friend.uid = "friend"
    sim.add_unit(friend)
    enemy = copy.deepcopy(u)
    enemy.uid, enemy.side, enemy.pos = "enemy", Side.RED, (110, 100)
    sim.add_unit(enemy)
    lose(sim, u, 2)
    sim.time = 100
    sim._sensor_step()
    assert u.crew_failure_reason == "NO_CREW"
    assert not u.can_observe and not u.can_communicate
    assert u.local_tracks == {}
    assert enemy.local_tracks.get(u.uid) is not None  # the empty vehicle still exists
    assert not sim.communications.send(u.uid, friend.uid, "TRACK_REPORT", {})
    assert not sim.communications.send(friend.uid, u.uid, "TRACK_REPORT", {})
    sim._handle_event("COMM_DELIVER", {"message": {"recipient_uid": u.uid,
                     "message_type": "TRACK_REPORT", "payload": {"target": enemy.uid,
                     "estimated_pos": enemy.pos}}})
    assert u.local_tracks == {}
    assert u.alive and u.state == UnitState.COMBAT_INEFFECTIVE


def test_remaining_crew_can_still_be_hit_after_combat_ineffective():
    sim, u = vehicle()
    lose(sim, u, 1)
    lose(sim, u, 1)
    assert u.personnel == 0 and u.equipment == 1 and u.alive
    assert u.crew_failure_reason == "NO_CREW"
    assert [e["reason"] for e in sim.logs if e["kind"] == "COMBAT_INEFFECTIVE"] == [
        "INSUFFICIENT_WEAPON_CREW", "NO_CREW"]


@pytest.mark.parametrize("metadata,min_ops", [({"inventory_model": "CREW_SERVED", "crew_per_weapon": 3}, 1),
                                              ({"operators_per_system": 3}, 1), ({}, 3)])
def test_personnel_served_weapon_uses_its_actual_operator_requirement(metadata, min_ops):
    sim, u = vehicle(branch="CUSTOM_BRANCH")
    u.elements.clear()
    gun = WeaponModel("team gun", "ANTI_PERSONNEL", 1000, 10, .5, metadata=metadata)
    team = FormationElement("team", "team", "PERSONNEL", "UNUSUAL_ROLE", 3, 3,
                            weapons=[gun], min_operators=min_ops)
    u.elements[team.eid] = team
    assert u.crew_failure_reason is None
    team.count = 2
    sim._step_unit(u, 1)
    assert u.state == UnitState.COMBAT_INEFFECTIVE and not u.operational_weapons()


def test_one_staffed_system_keeps_mixed_formation_operational():
    sim, u = vehicle()
    other = copy.deepcopy(u.elements["vehicle"])
    other.eid = "other"
    other.metadata["crew_elements"] = ["other_operators"]
    crew = copy.deepcopy(u.elements["operators"])
    crew.eid = "other_operators"
    u.elements.update({other.eid: other, crew.eid: crew})
    lose(sim, u, 2)
    assert u.crew_failure_reason is None
    assert len(u.operational_weapons()) == 1
    start = u.pos
    sim._step_unit(u, 1)
    assert u.pos != start


def test_shared_crew_pool_does_not_disable_remaining_staffed_platform():
    sim, u = vehicle()
    second = copy.deepcopy(u.elements["vehicle"])
    second.eid = "second"
    u.elements[second.eid] = second
    assert len(u.operational_weapons()) == 1
    assert u.crew_failure_reason is None
    lose(sim, u, 1)
    assert u.state == UnitState.COMBAT_INEFFECTIVE


def test_unloaded_other_weapon_cannot_mask_loss_of_remaining_gun_crew():
    sim, u = vehicle()
    other = copy.deepcopy(u.elements["vehicle"])
    other.eid = "other"
    other.metadata["crew_elements"] = ["other_operators"]
    other.weapons[0].ammo_remaining = 0
    crew = copy.deepcopy(u.elements["operators"])
    crew.eid = "other_operators"
    u.elements.update({other.eid: other, crew.eid: crew})
    lose(sim, u, 1)
    assert u.state == UnitState.COMBAT_INEFFECTIVE


def test_restored_operators_resume_suspended_order_once():
    sim, u = vehicle()
    lose(sim, u, 1)
    start = u.pos
    u.elements["operators"].count = 2
    sim._step_unit(u, 1)
    assert u.pos != start
    assert u.state != UnitState.COMBAT_INEFFECTIVE
    assert "crew_failure_reason" not in u.metadata
    sim._step_unit(u, 1)
    assert sum(e["kind"] == "COMBAT_CAPABILITY_RESTORED" for e in sim.logs) == 1


def test_deadline_does_not_override_crew_failure():
    sim, u = vehicle()
    u.current_order.deadline_s = 1
    u.current_order.on_deadline = {"task": "WITHDRAW", "destination": [300, 100]}
    lose(sim, u, 1)
    sim.time = 2
    sim._step_unit(u, 1)
    assert u.state == UnitState.COMBAT_INEFFECTIVE
    assert u.current_order.order_id == "advance"
    assert not any(e["kind"] == "DEADLINE_BRANCH" for e in sim.logs)


def test_no_ammo_is_not_mislabelled_as_missing_crew():
    sim, u = vehicle()
    u.elements["vehicle"].weapons[0].ammo_remaining = 0
    assert not u.operational_weapons()
    assert u.crew_failure_reason is None


def test_unarmed_sensor_and_legacy_implicit_crew_remain_operational():
    sim, u = vehicle()
    u.elements.pop("operators")
    u.elements["vehicle"].metadata.clear()
    assert u.crew_failure_reason is None and u.can_observe
    u.elements["vehicle"].weapons.clear()
    assert u.crew_failure_reason is None and u.can_observe


def test_explicit_empty_or_missing_crew_disables_unarmed_transport():
    sim, u = vehicle()
    u.elements.pop("operators")
    u.elements["vehicle"].weapons.clear()
    sim._step_unit(u, 1)
    assert u.state == UnitState.COMBAT_INEFFECTIVE
    assert not u.can_observe and u.pos == (100, 100)


def test_legacy_platform_explicit_embedded_crew_loss():
    sim, u = vehicle()
    u.elements.pop("operators")
    u.elements["vehicle"].metadata = {"crew": 2, "embedded_crew_remaining": 1}
    sim._step_unit(u, 1)
    assert u.state == UnitState.COMBAT_INEFFECTIVE
    u.elements["vehicle"].metadata["embedded_crew_remaining"] = 0
    sim._step_unit(u, 1)
    assert not u.can_observe and u.crew_failure_reason == "NO_CREW"


def test_tdg20_real_template_no_crew_does_not_complete_crossing():
    scenario = Path("MISSION/TDG_DEFENSE/TDG20_SCREEN_DELAY/TDG20_SCREEN_DELAY_SCENARIO.json")
    sim = load_scenario(str(scenario))
    lead = sim.units["R-LEAD"]
    lead.elements["vehicle_crew"].count = 0
    start = lead.pos
    for _ in range(40):
        sim.tick(.25)
    assert lead.pos == start and lead.state == UnitState.COMBAT_INEFFECTIVE
    assert lead.alive and lead.equipment == 1
    assert not any(e["kind"] == "ORDER_COMPLETE" and e["unit"] == lead.uid for e in sim.logs)


def test_combat_ineffective_symbol_renders_with_distinct_colour():
    import pygame
    import main
    sim, u = vehicle()
    lose(sim, u, 1)
    pygame.font.init()
    screen = pygame.Surface((1600, 950))
    layout = main.build_layout(1600, 950)
    camera = main.Camera(100, 100, 1)
    font = pygame.font.Font(None, 18)
    main.draw_nato_symbol(screen, camera, font, font, sim, u, None, layout)
    x, y = camera.world_to_screen(u.pos, layout.map_rect)
    # The top-left border of BLUE's rectangle now marks an ineffective platform.
    assert tuple(screen.get_at((x - 22, y - 13)))[:3] == (220, 155, 45)
