"""Untrusted scenario/BML input must fail fast at load time, never hang or escape the project."""
import json
import time
from pathlib import Path

import pytest

from mnsim.scenario import load_scenario
from mnsim.validation import ValidationError
from mnsim.model import Track

ROOT = Path(__file__).resolve().parents[1]


def _scenario(tmp_path, **extra):
    raw = json.loads((ROOT / "scenarios" / "demo.json").read_text(encoding="utf-8"))
    # Point the copied scenario back at the project's standard resources.
    for key in ("config_file", "unit_types_file", "artillery_doctrine_file", "targeting_doctrine_file"):
        raw.pop(key, None)
    raw.pop("terrain_file", None)
    raw.update(extra)
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path, raw


def _bml(tmp_path, side, doc):
    p = tmp_path / f"{side.lower()}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


def test_nan_and_infinity_are_rejected_in_json(tmp_path):
    path, _ = _scenario(tmp_path)
    text = path.read_text(encoding="utf-8").replace('"seed": 7', '"seed": NaN', 1)
    if "NaN" not in text:
        text = text.replace("{", '{"bogus": Infinity, ', 1)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_scenario(str(path))


def test_far_destination_is_rejected_quickly(tmp_path):
    path, raw = _scenario(tmp_path)
    uid = next(u["id"] for u in raw["units"] if u["side"] == "BLUE")
    bml = _bml(tmp_path, "BLUE", {"side": "BLUE", "missions": [
        {"unit": uid, "task": "MOVE_TO", "destination": [1e13, 2000]}]})
    t0 = time.time()
    with pytest.raises(ValidationError, match="outside"):
        load_scenario(str(path), bml_files={"BLUE": bml})
    assert time.time() - t0 < 5.0


def test_condition_types_are_validated_at_load(tmp_path):
    path, raw = _scenario(tmp_path)
    uid = next(u["id"] for u in raw["units"] if u["side"] == "BLUE")
    for bad in ({"lhs": "sim.time", "op": ">=", "rhs": "x"},
                {"lhs": "self.__class__", "op": "==", "rhs": 1},
                {"lhs": "self.loss_ratio", "op": "~", "rhs": 0.5}):
        bml = _bml(tmp_path, "BLUE", {"side": "BLUE", "missions": [
            {"unit": uid, "task": "HOLD", "conditions": [bad],
             "on_true": {"task": "WITHDRAW", "destination": [100, 100]}}]})
        with pytest.raises(ValidationError):
            load_scenario(str(path), bml_files={"BLUE": bml})


def test_nested_branch_coordinates_are_validated(tmp_path):
    path, raw = _scenario(tmp_path)
    uid = next(u["id"] for u in raw["units"] if u["side"] == "BLUE")
    bml = _bml(tmp_path, "BLUE", {"side": "BLUE", "missions": [
        {"unit": uid, "task": "HOLD", "conditions": [{"lhs": "sim.time", "op": ">=", "rhs": 5}],
         "on_true": {"task": "WITHDRAW", "destination": [float("1e15"), 0]}}]})
    with pytest.raises(ValidationError):
        load_scenario(str(path), bml_files={"BLUE": bml})


def test_resource_reference_outside_allowed_roots_is_refused(tmp_path):
    outside = tmp_path / "outside"
    inside = tmp_path / "inside"
    outside.mkdir(); inside.mkdir()
    (outside / "terrain.json").write_text("{}", encoding="utf-8")
    raw = json.loads((ROOT / "scenarios" / "demo.json").read_text(encoding="utf-8"))
    for key in ("config_file", "unit_types_file", "artillery_doctrine_file", "targeting_doctrine_file"):
        raw.pop(key, None)
    raw["terrain_file"] = "../outside/terrain.json"
    path = inside / "scenario.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValidationError, match="outside the allowed"):
        load_scenario(str(path))


def test_replay_log_records_project_relative_bml_path(tmp_path):
    sim = load_scenario(str(ROOT / "scenarios" / "tdg1.json"),
                        bml_files={"BLUE": str(ROOT / "scenarios" / "tdg1_blue_bml.json")})
    rec = next(e for e in sim.logs if e["kind"] == "BML_LOADED")
    assert rec["file"] == "scenarios/tdg1_blue_bml.json"
    assert str(ROOT) not in json.dumps(sim.logs)


def test_enemy_count_near_uses_tracks_not_ground_truth():
    from mnsim.bml import ConditionEvaluator
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"))
    blue = next(u for u in sim.units.values() if u.side.value == "BLUE")
    red = next(u for u in sim.units.values() if u.side.value == "RED")
    red.pos = blue.pos  # adjacent in ground truth, but never observed
    blue.local_tracks.clear()
    assert ConditionEvaluator.resolve(sim, blue, "self.enemy_count_near") == 0
    blue.local_tracks[red.uid] = Track(track_id="t", target_id=red.uid, estimated_pos=blue.pos,
                                       position_error_m=5.0, confidence=0.9, last_seen_time=sim.time,
                                       source="LOCAL", state="CLASSIFIED")
    assert ConditionEvaluator.resolve(sim, blue, "self.enemy_count_near") == 1


def test_long_segment_sampling_is_bounded():
    sim = load_scenario(str(ROOT / "scenarios" / "tdg1.json"))
    u = next(iter(sim.units.values()))
    t0 = time.time()
    sim.terrain.navigation.segment_passable(u, u.pos, (u.pos[0] + 1e9, u.pos[1]))
    assert time.time() - t0 < 5.0


def test_editor_save_never_writes_outside_scenario_folder(tmp_path, monkeypatch):
    pytest.importorskip("pygame")
    import editor
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    scen_dir = tmp_path / "maps"; scen_dir.mkdir()
    raw = {"world": {"width_m": 1000, "height_m": 1000}, "units": [], "terrain_file": "../victim.txt"}
    scen = scen_dir / "map.json"
    scen.write_text(json.dumps(raw), encoding="utf-8")
    ed = editor.Editor.__new__(editor.Editor)
    ed.scenario = raw; ed.scenario_path = scen
    ed.terrain = {"roads": [], "rivers": [], "bridges": [], "barricades": [], "areas": []}
    ed.terrain_path = (scen_dir / "../victim.txt").resolve()
    assert ed._confine_terrain_path() is True
    assert ed.terrain_path == scen_dir / "map_terrain.json"
    assert victim.read_text(encoding="utf-8") == "keep me"


def test_replay_log_path_is_unique(tmp_path):
    pytest.importorskip("pygame")
    import main
    a = main._replay_log_path(str(tmp_path))
    Path(a).write_text("x", encoding="utf-8")
    b = main._replay_log_path(str(tmp_path))
    assert a != b
