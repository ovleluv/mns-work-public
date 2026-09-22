from mnsim.model import Order, UnitState
from mnsim.scenario import load_scenario


def _clear_orders_and_tracks(sim):
    for unit in sim.units.values():
        unit.order_queue.clear()
        unit.current_order = None
        unit.local_tracks.clear()
        unit.target_id = None
        unit.metadata.pop("autonomous_fallback_reason", None)


def _indirect_weapon(unit):
    return next((element, weapon) for element, weapon in unit.operational_weapons() if weapon.capability == "INDIRECT_FIRE")


def _make_bridge_killing_round(weapon):
    weapon.metadata["bridge_deck_damage_min"] = 250.0
    weapon.metadata["bridge_deck_damage_max"] = 250.0
    weapon.metadata["bridge_near_effect_m"] = 0.0


def _queue_bridge_impact(sim, shooter, weapon, bridge, at):
    sim.events.push(
        at,
        "ARTY_IMPACT_RESOLVE",
        shooter=shooter.uid,
        target=f"INFRA:{bridge['id']}",
        weapon=weapon.name,
        round=1,
        pos=sim.terrain.bridge_center(bridge),
        mode="TEST",
    )


def test_artillery_impact_event_collapses_bridge_only_when_due():
    sim = load_scenario("scenarios/demo.json")
    _clear_orders_and_tracks(sim)

    shooter = sim.units["B-ART-2"]
    _, weapon = _indirect_weapon(shooter)
    _make_bridge_killing_round(weapon)
    bridge = sim.terrain.bridge_by_id("BR1")
    _queue_bridge_impact(sim, shooter, weapon, bridge, at=5.0)

    sim.tick(4.9)
    assert sim.terrain.bridge_operational(bridge)
    assert not any(rec["kind"] == "BRIDGE_DESTROYED" and rec.get("bridge") == "BR1" for rec in sim.logs)

    sim.tick(0.2)
    assert not sim.terrain.bridge_operational(bridge)
    assert any(rec["kind"] == "BRIDGE_DAMAGE" and rec.get("bridge") == "BR1" for rec in sim.logs)
    assert any(rec["kind"] == "BRIDGE_DESTROYED" and rec.get("bridge") == "BR1" for rec in sim.logs)


def test_bridge_collapse_events_can_make_a_cached_crossing_order_unreachable():
    sim = load_scenario("scenarios/demo.json")
    _clear_orders_and_tracks(sim)

    tank = sim.units["B-TK-1"]
    tank.pos = (2050.0, 1800.0)
    destination = (2050.0, 3500.0)
    assert len(sim.terrain.plan_route(tank, destination)) > 1
    sim.terrain.movement_target(tank, destination)
    assert "_nav_route" in tank.metadata

    shooter = sim.units["B-ART-2"]
    _, weapon = _indirect_weapon(shooter)
    _make_bridge_killing_round(weapon)
    for bridge in sim.terrain.bridges:
        _queue_bridge_impact(sim, shooter, weapon, bridge, at=1.0)

    sim.tick(1.1)
    assert all(not sim.terrain.bridge_operational(bridge) for bridge in sim.terrain.bridges)

    tank.current_order = Order("TEST-CROSSING", "MOVE", {"destination": destination})
    tank.state = UnitState.MOVING
    sim._step_unit(tank, 0.25)

    assert tank.current_order is None
    assert tank.state == UnitState.DEFENDING
    assert tank.metadata.get("autonomous_fallback_reason") == "NO ROUTE / AUTONOMOUS HOLD"
    assert any(rec["kind"] == "ORDER_UNREACHABLE" and rec.get("unit") == tank.uid for rec in sim.logs)


def test_delayed_damage_events_apply_once_and_clamp_to_available_strength():
    sim = load_scenario("scenarios/demo.json")
    _clear_orders_and_tracks(sim)

    target = sim.units["R-INF-2"]
    element = next(el for el in target.elements.values() if el.category.upper() == "PERSONNEL" and el.count > 0)
    before_count = element.count

    sim.events.push(
        3.0,
        "ELEMENT_LOSS",
        target=target.uid,
        source="TEST",
        element=element.eid,
        count=before_count + 100,
        weapon="test",
    )
    sim.events.push(
        3.0,
        "ELEMENT_LOSS",
        target=target.uid,
        source="TEST",
        element=element.eid,
        count=before_count + 100,
        weapon="test",
    )

    sim.tick(2.9)
    assert element.count == before_count

    sim.tick(0.2)
    assert element.count == 0
    assert target.personnel >= 0
    assert 0.0 <= target.strength_ratio <= 1.0
    loss_logs = [
        rec for rec in sim.logs
        if rec["kind"] == "ELEMENT_LOSS" and rec.get("target") == target.uid and rec.get("element") == element.eid
    ]
    assert len(loss_logs) == 1
    assert loss_logs[0]["count"] == before_count


def test_equipment_effect_event_changes_one_component_without_negative_equipment():
    sim = load_scenario("scenarios/demo.json")
    _clear_orders_and_tracks(sim)

    tank = sim.units["R-TK-1"]
    element = next(el for el in tank.elements.values() if "ARMOR" in el.tags)
    element.ensure_item_states()
    before_equipment = tank.equipment

    sim.events.push(
        2.0,
        "EQUIPMENT_EFFECT",
        target=tank.uid,
        source="TEST",
        element=element.eid,
        item_index=0,
        effect="FIREPOWER_KILL",
        weapon="test",
        reason="TEST",
    )
    sim.tick(2.1)

    assert any(
        rec["kind"] == "EQUIPMENT_STATE_CHANGE"
        and rec.get("unit") == tank.uid
        and rec.get("element") == element.eid
        for rec in sim.logs
    )
    assert 0 <= tank.equipment <= before_equipment
    assert all(state in sim.damage.STATES for state in element.item_states)
    assert 0.0 <= tank.strength_ratio <= 1.0
