"""Auditable TDG3 event checks shared by fresh runs and saved-log tests.

Event logs are not position snapshots. Completion times below are command
milestones, not exact river entry/exit times or proof of an overall victory.
"""
import collections
import math
from pathlib import PureWindowsPath


def audit_events(rows, initial_sim):
    assert rows, "Replay is empty"
    last_time = -1.0
    ammo = {(u.uid, e.eid, w.name): w.ammo_capacity
            for u in initial_sim.units.values() for e in u.elements.values() for w in e.weapons}
    starts, completed = {}, {}
    lost_starts = []
    forbidden = {"ORDER_UNKNOWN", "BML_TARGET_MISSING", "ORDER_UNREACHABLE", "BUILDING_MOVE_FAILED"}
    for row in rows:
        t = row["t"]
        assert math.isfinite(t) and t >= last_time, ("non-monotonic/invalid time", row)
        last_time = t
        kind = row["kind"]
        assert kind not in forbidden, row
        if kind in ("COMM_TX", "TRACK_SHARED"):
            assert "B-SEC-B" not in (row.get("source"), row.get("recipient")), ("lost radio delivered a report", row)
        if kind == "DEAGGREGATE_DAMAGED_ITEM":
            # Each authored TDG3 tank owns exactly one vehicle. Its entire
            # remaining inventory transfers to the child; it is not resupply.
            assert row["parent_remaining"] == 0, "Replay audit expects the TDG3 individual-vehicle model"
            transferred = [key for key in ammo if key[:2] == (row["parent"], row["element"])]
            assert transferred, ("unknown detachment parent", row)
            for key in transferred:
                child_key = (row["child"], key[1], key[2])
                assert child_key not in ammo, ("duplicate vehicle detachment", row)
                ammo[child_key] = ammo[key]
                if ammo[key] >= 0:
                    ammo[key] = 0
        if kind == "FIRE":
            key = (row["shooter"], row["source_element"], row["weapon"])
            assert key in ammo, ("unrecognized weapon", row)
            previous = ammo[key]
            if previous >= 0:
                assert previous > 0 and row["ammo_remaining"] == previous - 1, ("finite ammunition accounting", row)
                ammo[key] -= 1
            else:
                assert row["ammo_remaining"] == -1, row
            assert 0 <= row["track_confidence"] <= 1, row
            assert row["track_confidence"] > 0, ("fire without reported track confidence", row)
        if kind == "ORDER_START":
            key = (row["unit"], row["order_id"])
            starts[key] = t
            if row["unit"] == "B-SEC-B":
                lost_starts.append(row["order_id"])
        if kind == "ORDER_COMPLETE":
            completed[(row["unit"], row["order_id"])] = t

    loaded = {r["side"]: PureWindowsPath(r["file"]).name for r in rows if r["kind"] == "BML_LOADED"}
    assert loaded == {"BLUE": "tdg3_blue_bml.json", "RED": "tdg3_red_bml.json"}, loaded
    added = {r["unit"]: r for r in rows if r["kind"] == "UNIT_ADD"}
    for uid, u in initial_sim.units.items():
        assert uid in added, ("missing initial unit", uid)
        assert added[uid]["personnel"] == u.personnel, uid
        assert added[uid]["equipment"] == u.equipment, uid

    expected = [o.order_id for o in initial_sim.units["B-SEC-B"].order_queue]
    assert lost_starts == expected[:len(lost_starts)], ("B Security standing orders changed", lost_starts)
    for before, after in zip(expected, expected[1:]):
        if ("B-SEC-B", after) in starts:
            assert ("B-SEC-B", before) in completed, (before, after)
            assert starts[("B-SEC-B", after)] >= completed[("B-SEC-B", before)]
    if ("B-SEC-B", expected[1]) in starts:
        assert starts[("B-SEC-B", expected[1])] - starts[("B-SEC-B", expected[0])] >= 180
    for uid, unit in initial_sim.units.items():
        for order in unit.order_queue:
            if order.start_at_s is not None and (uid, order.order_id) in completed:
                # ORDER_START logs queue activation, which may precede its time gate.
                assert completed[(uid, order.order_id)] >= order.start_at_s, (uid, order.order_id)

    return {
        # A run may continue silently after this event. Its elapsed simulation
        # time must come from the live simulation, not the final JSONL row.
        "last_event_time_s": last_time,
        "event_counts": dict(collections.Counter(r["kind"] for r in rows)),
        "bridge_crossing_order_complete_s": {uid: t for (uid, oid), t in completed.items() if oid.endswith("-CROSS-BRIDGE")},
        "south_road_order_complete_s": {uid: t for (uid, oid), t in completed.items() if oid.endswith("-SOUTH-ROAD")},
        "b_security_orv_order_complete_s": completed.get(("B-SEC-B", "B-SEC-B-STANDING-RALLY-ORV")),
        "destroyed_units": [r["unit"] for r in rows if r["kind"] == "DESTROYED"],
        "interpretation": "Execution checks only; no assertion of BLUE/RED victory. Saved events cannot prove exact movement paths or calibrated combat outcomes."
    }
