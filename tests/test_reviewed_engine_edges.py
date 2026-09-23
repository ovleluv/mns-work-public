"""Input integrity and delayed command-message behavior."""

import math

import pytest

from mnsim.bml import apply_bml_document
from mnsim.model import FormationElement, Side, Track, Unit, UnitState, UnitType
from mnsim.scenario import load_scenario
from mnsim.simulation import Simulation


def test_duplicate_unit_id_is_rejected_without_replacing_existing_formation():
    sim = Simulation()
    unit = Unit("B", "first", Side.BLUE, "PLT", UnitType("infantry", "INFANTRY", 1, 500),
                (100, 100), elements={"rifle": FormationElement("rifle", "rifle", "PERSONNEL", "RIFLE", 1, 1)})
    sim.add_unit(unit)
    duplicate = Unit("B", "second", Side.RED, "PLT", unit.unit_type, (200, 200),
                     elements=unit.elements)
    with pytest.raises(ValueError, match="Unit ID already exists"):
        sim.add_unit(duplicate)
    assert sim.units["B"] is unit


def test_aggregate_rejects_missing_child_instead_of_silently_dropping_it():
    sim = Simulation()
    unit = Unit("B", "first", Side.BLUE, "PLT", UnitType("infantry", "INFANTRY", 1, 500),
                (100, 100), elements={"rifle": FormationElement("rifle", "rifle", "PERSONNEL", "RIFLE", 1, 1)})
    sim.add_unit(unit)
    with pytest.raises(ValueError, match="aggregate children"):
        sim.aggregate_units("P", "company", ["B", "MISSING"])
    assert unit.active and "P" not in sim.units


@pytest.mark.parametrize("bad_dt", [-0.25, math.inf, math.nan])
def test_tick_rejects_invalid_time_without_corrupting_clock(bad_dt):
    sim = Simulation()
    with pytest.raises(ValueError, match="finite and non-negative"):
        sim.tick(bad_dt)
    assert sim.time == 0.0


def test_tick_rejects_finite_inputs_that_overflow_the_clock():
    sim = Simulation()
    sim.speed = 1e308
    with pytest.raises(ValueError, match="finite clock range"):
        sim.tick(1e308)
    assert sim.time == 0.0


def test_zero_speed_freezes_tick_and_realtime_advance():
    sim = Simulation()
    sim.speed = 0.0
    sim.events.push(0.0, "UNHANDLED")
    sim.tick(1.0)
    sim.advance_realtime(1.0)
    assert sim.time == 0.0 and len(sim.events) == 1


def test_hq_report_survives_observer_loss_before_dissemination():
    sim = load_scenario("scenarios/demo.json", bml_files={})
    source = sim.units["B-INF-2"]
    recipient = sim.units["B-ART-1"]
    target = sim.units["R-INF-1"]
    link = sim.combat_config["communications"]["default_link"]
    link.update(min_delay_s=0.0, max_delay_s=0.0, reliability=1.0)
    source.active = False
    report = {"source": source.uid, "side": source.side.value, "target": target.uid,
              "estimated_pos": target.pos, "position_error_m": 10.0,
              "classification": "INFANTRY", "confidence": 0.9,
              "state": "IDENTIFIED", "observation_time": sim.time}
    sim._handle_event("C2_DISSEMINATE_TRACK", {"report": report})
    for event in sim.events.pop_due(sim.time):
        sim._handle_event(event.kind, event.payload)
    assert target.uid in recipient.local_tracks
    delivery = next(x for x in sim.logs if x["kind"] == "TRACK_DISSEMINATION" and x.get("origin") == "C2")
    assert delivery["source"] == source.uid and delivery["relay"] != source.uid
    assert any(x["kind"] == "TRACK_SHARED" and x["source"] == source.uid
               and x["recipient"] == recipient.uid for x in sim.logs)


def test_invalid_later_bml_mission_does_not_replace_earlier_orders():
    sim = load_scenario("scenarios/demo.json", bml_files={})
    unit = sim.units["B-INF-2"]
    original = list(unit.order_queue)
    log_count = len(sim.logs)
    document = {"side": "BLUE", "replace_existing_orders": True, "missions": [
        {"id": "valid-first", "unit": unit.uid, "task": "HOLD"},
        {"id": "invalid-second", "unit": "B-TK-1", "task": "ATTACK_UNIT", "target": "MISSING"},
    ]}
    with pytest.raises(KeyError, match="unknown target unit"):
        apply_bml_document(sim, document, expected_side="BLUE")
    assert unit.order_queue == original
    assert len(sim.logs) == log_count


def test_bml_declared_side_cannot_command_an_opponent_without_external_hint():
    sim = load_scenario("scenarios/demo.json", bml_files={})
    opponent = sim.units["R-INF-1"]
    original = list(opponent.order_queue)
    with pytest.raises(ValueError, match="attempts to command"):
        apply_bml_document(sim, {"side": "BLUE", "missions": [
            {"unit": opponent.uid, "task": "HOLD"}]})
    assert opponent.order_queue == original


def test_infantry_reaction_uses_perceived_classification_and_existence():
    sim = load_scenario("scenarios/demo.json", bml_files={})
    infantry = sim.units["B-INF-2"]
    tank = sim.units["R-TK-1"]
    for element in infantry.elements.values():
        for weapon in element.weapons:
            if weapon.capability == "ANTI_ARMOR":
                weapon.ammo_remaining = 0
    assert not infantry.capability_available("ANTI_ARMOR")
    track = Track(f"{infantry.uid}:{tank.uid}", tank.uid, tank.pos, 5.0,
                  classification="UNKNOWN", confidence=1.0, last_seen_time=sim.time,
                  source="LOCAL", state="IDENTIFIED")
    infantry.local_tracks[tank.uid] = track
    assert sim.doctrine.step(infantry, 0.25) is False
    assert infantry.state != UnitState.RETREATING

    # The actual vehicle was destroyed elsewhere, but the observer has not received that news.
    tank.state = UnitState.DESTROYED
    track.classification = "ARMOR"
    assert sim.doctrine.step(infantry, 0.25) is True
    assert infantry.state == UnitState.RETREATING
