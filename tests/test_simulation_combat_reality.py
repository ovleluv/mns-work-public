import math

from mnsim.model import Track, UnitState
from mnsim.scenario import load_scenario


def _track(observer, target, now=0.0, zone="CLOSE"):
    observer.local_tracks[target.uid] = Track(
        track_id=f"{observer.uid}:{target.uid}",
        target_id=target.uid,
        estimated_pos=target.pos,
        position_error_m=1.0,
        classification=target.branch,
        confidence=0.98,
        last_seen_time=now,
        observations=5,
        source="LOCAL",
        observation_zone=zone,
        state="IDENTIFIED",
        belief_confidence=0.98,
        existence_confirmed=True,
        last_confirmed_time=now,
    )


def _clear_orders_and_tracks(sim):
    for unit in sim.units.values():
        unit.order_queue.clear()
        unit.current_order = None
        unit.local_tracks.clear()
        unit.target_id = None
        unit.metadata.pop("_local_direct_target_locks", None)
        unit.metadata.pop("_direct_fire_state", None)
        unit.metadata.pop("_direct_fire_cycle_state", None)


def _make_direct_fire_immediate(sim):
    sim.combat_config["direct_fire_requires_watch_alignment"] = False
    for unit in sim.units.values():
        unit.state = UnitState.DEFENDING
        for element in unit.elements.values():
            for weapon in element.weapons:
                if weapon.capability.upper() == "INDIRECT_FIRE":
                    continue
                weapon.metadata["acquisition_delay_min_s"] = 0.0
                weapon.metadata["acquisition_delay_max_s"] = 0.0
                weapon.metadata["engagement_cycle_min_s"] = 0.0
                weapon.metadata["engagement_cycle_max_s"] = 0.0
                weapon.metadata["base_hit_probability"] = 1.0
                weapon.metadata["max_hit_probability"] = 1.0
                weapon.metadata["range_hit_factor_min"] = 1.0
                weapon.metadata["range_hit_falloff"] = 0.0
                weapon.metadata["defending_hit_factor"] = 1.0
                weapon.metadata["guided_track_factor_floor"] = 1.0
                weapon.metadata["guided_error_penalty_max"] = 0.0


def _retain_only_personnel_weapons(unit):
    for element in unit.elements.values():
        element.weapons = [
            weapon
            for weapon in element.weapons
            if {tag.upper() for tag in weapon.target_tags} <= {"PERSONNEL"}
        ]


def _process_due_events(sim, dt=0.25):
    sim.time += dt
    for event in list(sim.events.pop_due(sim.time)):
        sim._handle_event(event.kind, event.payload)


def _weapon_from_fire_log(sim, fire):
    shooter = sim.units[fire["shooter"]]
    source = shooter.elements[fire["source_element"]]
    weapon = next(w for w in source.weapons if w.name == fire["weapon"])
    target = sim.units[fire["target"]]
    target_element = target.elements[fire["target_element"]]
    return shooter, source, weapon, target, target_element


def test_one_vs_one_rifle_only_infantry_cannot_damage_armor():
    sim = load_scenario("scenarios/demo.json")
    _clear_orders_and_tracks(sim)
    _make_direct_fire_immediate(sim)

    infantry = sim.units["B-INF-3"]
    tank = sim.units["R-TK-1"]
    infantry.pos = (1000.0, 1000.0)
    tank.pos = (1080.0, 1000.0)
    _retain_only_personnel_weapons(infantry)
    _track(infantry, tank, sim.time)
    _track(tank, infantry, sim.time)

    tank_equipment_before = tank.equipment
    tank_strength_before = tank.current_strength

    assert not sim._unit_can_affect(infantry, tank)
    assert sim._unit_can_affect(tank, infantry)

    for _ in range(3):
        sim.combat.fire_local(infantry, [tank], {})
        sim.combat.fire_local(tank, [infantry], {})
        sim.time += 0.01
    _process_due_events(sim)

    assert not any(
        rec["kind"] == "FIRE" and rec.get("shooter") == infantry.uid and rec.get("target") == tank.uid
        for rec in sim.logs
    )
    assert not any(
        rec["kind"] in {"EQUIPMENT_EFFECT", "EQUIPMENT_STATE_CHANGE", "DESTROYED"}
        and rec.get("source") == infantry.uid
        and rec.get("target", rec.get("unit")) == tank.uid
        for rec in sim.logs
    )
    assert tank.equipment == tank_equipment_before
    assert math.isclose(tank.current_strength, tank_strength_before)


def test_many_to_many_direct_engagements_only_emit_physically_valid_fire_and_losses():
    sim = load_scenario("scenarios/tdg1.json")
    _clear_orders_and_tracks(sim)
    _make_direct_fire_immediate(sim)

    blue_ids = ["B-INF_BN-1", "B-INF_PLT-1"]
    red_ids = ["R-INF_PLT-1", "R-INF_PLT-2", "R-INF_PLT-6"]
    participants = [sim.units[uid] for uid in blue_ids + red_ids]
    positions = {
        "B-INF_BN-1": (1500.0, 1500.0),
        "B-INF_PLT-1": (1540.0, 1500.0),
        "R-INF_PLT-1": (1600.0, 1500.0),
        "R-INF_PLT-2": (1600.0, 1540.0),
        "R-INF_PLT-6": (1600.0, 1460.0),
    }
    for unit in sim.units.values():
        if unit.uid not in positions:
            unit.metadata["noncombat_proxy"] = True
    for unit in participants:
        unit.pos = positions[unit.uid]
        unit.watch_heading_deg = 0.0

    for observer in participants:
        for target in participants:
            if observer.side != target.side:
                _track(observer, target, sim.time)

    sim._combat_step()
    sim.time += 0.01
    sim._combat_step()

    assert sim.engagements
    assert any(group["blue"] >= 2 and group["red"] >= 2 for group in sim.engagements)

    fires = [
        rec for rec in sim.logs
        if rec["kind"] == "FIRE" and rec.get("mode") == "DIRECT"
    ]
    assert fires

    for fire in fires:
        shooter, _, weapon, target, target_element = _weapon_from_fire_log(sim, fire)
        assert shooter.side != target.side
        assert target.uid in shooter.local_tracks
        assert fire["distance"] <= weapon.range_m + 0.1
        assert target_element.tags & {tag.upper() for tag in weapon.target_tags}

    _process_due_events(sim)
    for unit in participants:
        assert 0 <= unit.personnel <= unit.initial_personnel
        assert 0 <= unit.equipment <= unit.initial_equipment
        assert 0.0 <= unit.strength_ratio <= 1.0
