"""Saved-run audit is opt-in; fresh synthetic checks validate the audit itself."""
import copy
import json

import pytest

from tdg3_checks import audit_events


def test_initial_tdg3_log_is_valid_but_not_reported_as_a_completed_mission(tdg3_sim):
    report = audit_events(tdg3_sim.logs, tdg3_sim)
    assert report["south_road_order_complete_s"] == {}
    assert report["b_security_orv_order_complete_s"] is None


def test_replay_audit_does_not_infer_run_duration_from_last_event(tdg3_sim):
    rows = copy.deepcopy(tdg3_sim.logs)
    rows.append({"t": 10, "kind": "TEST_EVENT"})
    # These same rows could come from a run ending at 10 s or continuing
    # without new events until 3600 s. A saved log cannot distinguish them.
    report = audit_events(rows, tdg3_sim)
    assert report["last_event_time_s"] == 10
    assert "duration_s" not in report


def test_replay_audit_accounts_for_ammunition_transferred_to_detached_tank(tdg3_sim):
    rows = copy.deepcopy(tdg3_sim.logs)
    e, w = next((e, w) for e, w in tdg3_sim.units["R-T55-1"].operational_weapons() if w.ammo_capacity == 15)
    rows.append({"t": 1, "kind": "DEAGGREGATE_DAMAGED_ITEM", "parent": "R-T55-1",
                 "child": "R-T55-1-DET-1", "element": e.eid, "parent_remaining": 0})
    rows.append({"t": 2, "kind": "FIRE", "shooter": "R-T55-1-DET-1", "source_element": e.eid,
                 "weapon": w.name, "ammo_remaining": 14, "track_confidence": .8})
    audit_events(rows, tdg3_sim)
    # The original unit may not also retain and spend the transferred inventory.
    rows.append({**rows[-1], "t": 3, "shooter": "R-T55-1"})
    with pytest.raises(AssertionError, match="finite ammunition accounting"):
        audit_events(rows, tdg3_sim)


@pytest.mark.parametrize("violation", ["radio", "unknown_order", "early_completion", "ammo", "unobserved_fire", "time"])
def test_replay_audit_rejects_injected_execution_violations(tdg3_sim, violation):
    rows = copy.deepcopy(tdg3_sim.logs)
    if violation == "radio":
        rows.append({"t": 1, "kind": "TRACK_SHARED", "source": "B-HQ", "recipient": "B-SEC-B"})
    elif violation == "unknown_order":
        rows.append({"t": 1, "kind": "ORDER_UNKNOWN", "unit": "B-SEC-B"})
    elif violation == "early_completion":
        rows.append({"t": 1, "kind": "ORDER_COMPLETE", "unit": "R-T55-1", "order_id": "R-T55-1-CROSSING-APPROACH"})
    elif violation == "time":
        rows += [{"t": 2, "kind": "TEST_EVENT"}, {"t": 1, "kind": "TEST_EVENT"}]
    else:
        e, w = next((e, w) for e, w in tdg3_sim.units["B-AT-1"].operational_weapons() if w.capability == "ANTI_ARMOR")
        rows.append({"t": 1, "kind": "FIRE", "shooter": "B-AT-1", "source_element": e.eid, "weapon": w.name,
                     "ammo_remaining": 3 if violation == "ammo" else 2,
                     "track_confidence": 0 if violation == "unobserved_fire" else .8})
    with pytest.raises(AssertionError):
        audit_events(rows, tdg3_sim)


def test_saved_tdg3_replay(tdg3_sim, tdg3_replay_path, tmp_path):
    rows = []
    with tdg3_replay_path.open(encoding="utf-8") as stream:
        for lineno, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                pytest.fail(f"Invalid replay JSON at line {lineno}: {error}")
    report = audit_events(rows, tdg3_sim)
    report["source_file"] = str(tdg3_replay_path)
    report["configuration_limit"] = "Replay has no configuration hashes. Audit assumes the same checked-in TDG3 model; do not use it to compare unrelated scenarios."
    target = tmp_path / "tdg3_replay_audit.json"
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"TDG3 saved replay audit: {target}\n{json.dumps(report, indent=2)}")
