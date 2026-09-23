"""Delayed effects keep the information and capabilities present at their source time."""

import pytest

from mnsim.model import Track
from mnsim.scenario import load_scenario


def _artillery(sim):
    shooter = sim.units["R-ART-1"]
    element, weapon = next((e, w) for e, w in shooter.operational_weapons()
                           if w.capability == "INDIRECT_FIRE")
    return shooter, element, weapon


def test_in_flight_shell_keeps_launch_weapon_effect_after_gun_is_removed():
    sim = load_scenario("scenarios/demo.json", bml_files={})
    shooter, element, weapon = _artillery(sim)
    target = sim.units["B-INF-2"]
    weapon.metadata.update(effect_radius_personnel_m=5000.0, effect_p_personnel=1.0,
                           max_personnel_loss_per_round=2)
    sim.indirect_fire.launch_prepared_mission(
        shooter, element, weapon, target.uid, target.pos, 0.0, 1.0,
        sim.time, "FIRE_SUPPORT", sim.time,
    )
    impacts = [event for event in sim.events._q if event.kind == "ARTY_IMPACT_RESOLVE"]
    assert impacts
    impact = impacts[0]
    impact.payload["pos"] = target.pos
    element.weapons.remove(weapon)

    sim.time = impact.time
    sim._handle_event(impact.kind, impact.payload)
    assert any(record["kind"] == "ARTY_IMPACT" and record["effects"] > 0
               for record in sim.logs)


def test_aborted_prepared_mission_does_not_claim_a_firing_cycle():
    sim = load_scenario("scenarios/demo.json", bml_files={})
    shooter, element, weapon = _artillery(sim)
    target = sim.units["B-INF-2"]
    aim = (shooter.pos[0] + weapon.range_m * 0.5, shooter.pos[1])
    shooter.local_tracks[target.uid] = Track(
        f"{shooter.uid}:{target.uid}", target.uid, aim, 5.0,
        classification="INFANTRY", confidence=0.9, last_seen_time=sim.time,
        source="LOCAL", state="IDENTIFIED",
    )
    for key in ("fire_support_request_delay_min_s", "fire_support_request_delay_max_s",
                "fire_direction_compute_delay_min_s", "fire_direction_compute_delay_max_s",
                "gun_prepare_delay_min_s", "gun_prepare_delay_max_s"):
        sim.combat_config[key] = 0.0
    assert sim.fire_control.request(shooter, target, element, weapon, "FIRE_SUPPORT")
    ammo_before = weapon.ammo_remaining
    shooter.pos = (shooter.pos[0] - weapon.range_m, shooter.pos[1])
    for event in sim.events.pop_due(sim.time):
        sim._handle_event(event.kind, event.payload)
    key = (shooter.uid, f"{element.eid}:{weapon.name}")
    assert weapon.ammo_remaining == ammo_before
    assert key not in sim.fire_control.next_available
    assert key not in sim.fire_control.last_fired
    assert not any(record["kind"] == "INDIRECT_FIRE" for record in sim.logs)


@pytest.mark.parametrize("friendly_fire", [False, True])
def test_indirect_friendly_fire_setting_applies_to_firing_unit(friendly_fire):
    sim = load_scenario("scenarios/demo.json", bml_files={})
    shooter, _, weapon = _artillery(sim)
    sim.combat_config["indirect_friendly_fire"] = friendly_fire
    weapon.metadata.update(effect_radius_personnel_m=5000.0, effect_p_personnel=1.0,
                           max_personnel_loss_per_round=2)
    sim.indirect_fire.resolve_impact({"shooter": shooter.uid, "target": shooter.uid,
                                      "weapon": weapon.name, "round": 1,
                                      "pos": shooter.pos, "mode": "TEST"})
    self_losses = [event for event in sim.events._q
                   if event.kind == "ELEMENT_LOSS" and event.payload["target"] == shooter.uid]
    assert bool(self_losses) is friendly_fire


def test_repeated_spatial_hit_on_destroyed_item_does_not_hit_sibling():
    sim = load_scenario("scenarios/demo.json", bml_files={})
    tank = sim.units["B-TK-1"]
    element = tank.elements["tanks"]
    for _ in range(2):
        sim.events.push(0.15, "EQUIPMENT_EFFECT", target=tank.uid, element=element.eid,
                        item_index=0, effect="DESTROYED", source="R-ART-1")
    sim.time = 0.15
    for event in sim.events.pop_due(sim.time):
        sim._handle_event(event.kind, event.payload)
    assert element.item_states == ["DESTROYED", "OPERATIONAL", "OPERATIONAL", "OPERATIONAL"]


def test_queued_spatial_hits_follow_items_after_vehicle_detaches():
    sim = load_scenario("scenarios/demo.json", bml_files={})
    tank = sim.units["B-TK-1"]
    element = tank.elements["tanks"]
    for index, effect in ((0, "MOBILITY_KILL"), (1, "DESTROYED"), (0, "DESTROYED")):
        sim.events.push(0.15, "EQUIPMENT_EFFECT", target=tank.uid, element=element.eid,
                        item_index=index, effect=effect, source="R-ART-1")
    sim.time = 0.15
    for event in sim.events.pop_due(sim.time):
        sim._handle_event(event.kind, event.payload)
    detached = sim.units[f"{tank.uid}-DET-1"]
    assert element.item_states == ["DESTROYED", "OPERATIONAL", "OPERATIONAL"]
    assert detached.elements[element.eid].item_states == ["DESTROYED"]


@pytest.mark.parametrize("source", ["LOCAL", "SHARED"])
def test_out_of_order_report_cannot_replace_newer_track(source):
    sim = load_scenario("scenarios/demo.json", bml_files={})
    observer = sim.units["B-INF-2"]
    target = sim.units["R-INF-1"]
    sim.time = 25.0
    current = Track(f"{observer.uid}:{target.uid}", target.uid, (1000.0, 1000.0),
                    10.0, classification="INFANTRY", confidence=0.7,
                    last_seen_time=20.0, source=source, state="IDENTIFIED")
    observer.local_tracks[target.uid] = current
    message = {"message_type": "TRACK_REPORT", "sender_uid": "B-INF-1",
               "payload": {"target": target.uid, "estimated_pos": (3000.0, 3000.0),
                           "confidence": 0.99, "classification": "INFANTRY",
                           "observation_time": 15.0, "state": "IDENTIFIED"}}
    sim._receive_comm_message(observer, message)
    assert observer.local_tracks[target.uid] is current
    assert observer.local_tracks[target.uid].estimated_pos == (1000.0, 1000.0)
    if source == "LOCAL":
        assert sim._track_for(observer, target, "DIRECT") is current

    message["payload"]["observation_time"] = 25.0
    sim._receive_comm_message(observer, message)
    if source == "SHARED":
        assert observer.local_tracks[target.uid].estimated_pos == (3000.0, 3000.0)
