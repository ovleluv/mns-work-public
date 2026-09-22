"""Only escaped foot crews switch from vehicle optics to ordinary infantry vision."""
import copy
import json
from pathlib import Path

import pytest

from mnsim.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[1]


def scenario(tmp_path, vehicle_type, echelon):
    raw = {"units": [
        {"id": "T", "side": "BLUE", "type": vehicle_type, "echelon": echelon,
         "pos": [100, 100]},
        {"id": "I", "side": "BLUE", "type": "INF_IND", "echelon": "IND",
         "pos": [100, 100]}]}
    path = tmp_path / "crew.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return load_scenario(path)


@pytest.mark.parametrize("vehicle_type,echelon", [
    ("TANK_PLT", "PLT"), ("US_M1A2_ABRAMS_IND", "IND"), ("ROK_K2_TANK_IND", "IND")])
@pytest.mark.parametrize("detach_first", [False, True])
def test_escaped_crew_matches_infantry_but_vehicle_retains_its_sensor(tmp_path, vehicle_type, echelon, detach_first):
    sim = scenario(tmp_path, vehicle_type, echelon)
    tank, infantry = sim.units["T"], sim.units["I"]
    # Vehicle-specific optics must never become the foot crew's sensor baseline.
    tank.unit_type.metadata.setdefault("visual_sensor", {}).update(
        forward_range_m=2400, forward_fov_deg=42, watch_slew_deg_per_s=19,
        sensor_mode="THERMAL")
    vehicle_type_before = copy.deepcopy(tank.unit_type)
    vehicle_profile = sim._visual_sensor_profile(tank)
    carrier = tank
    platform = next(e for e in carrier.elements.values() if "ARMOR" in e.tags)
    if detach_first:
        sim.damage.apply_equipment_effect(carrier, platform, "MOBILITY_KILL", item_index=0)
        carrier = sim.units["T-DET-1"]
        platform = carrier.elements[platform.eid]
        assert sim._visual_sensor_profile(carrier) == vehicle_profile
        assert carrier.unit_type.metadata["visual_sensor"]["sensor_mode"] == "THERMAL"
    sim.damage.apply_equipment_effect(carrier, platform, "DESTROYED", item_index=0)
    crew, = [u for u in sim.units.values() if u.metadata.get("escaped_from")]
    assert crew.branch == "INFANTRY" and crew.unit_type.metadata["mobility_class"] == "FOOT"
    assert crew.unit_type.detection_range_m == infantry.unit_type.detection_range_m
    assert tank.unit_type == vehicle_type_before
    assert sim._visual_sensor_profile(tank) == vehicle_profile
    for weather, illumination in [("CLEAR", "DAY"), ("FOG", "DAY"), ("CLEAR", "NIGHT")]:
        sim.combat_config["environment"].update(weather=weather, illumination=illumination)
        assert sim._visual_sensor_profile(crew) == sim._visual_sensor_profile(infantry)
    crew.unit_type.metadata["visual_sensor"]["forward_range_m"] = 1
    assert sim._visual_sensor_profile(infantry)[0] > 1
    assert tank.unit_type == vehicle_type_before


def test_crew_uses_scenario_infantry_template_override(tmp_path):
    library = json.loads((ROOT / "config/toe_templates.json").read_text(encoding="utf-8"))
    for key, value in list(library.items()):
        if key.endswith("_file"):
            library[key] = str((ROOT / "config" / value).resolve())
    infantry = library["unit_types"]["INF_IND"]
    infantry["detection_range_m"] = 420
    infantry["metadata"]["visual_sensor"].update(
        forward_range_m=420, watch_slew_deg_per_s=63, forward_fov_deg=95)
    library_path = tmp_path / "toe.json"
    library_path.write_text(json.dumps(library), encoding="utf-8")
    raw = {"unit_types_file": str(library_path), "units": [
        {"id": "T", "side": "BLUE", "type": "TANK_PLT", "echelon": "PLT", "pos": [100, 100]},
        {"id": "I", "side": "BLUE", "type": "INF_IND", "echelon": "IND", "pos": [100, 100]}]}
    p = tmp_path / "scenario.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    sim = load_scenario(p)
    tank = sim.units["T"]
    sim.damage.apply_equipment_effect(tank, tank.elements["tanks"], "DESTROYED", item_index=0)
    crew, = [u for u in sim.units.values() if u.metadata.get("escaped_from")]
    assert crew.unit_type.detection_range_m == 420
    assert sim._visual_sensor_profile(crew) == sim._visual_sensor_profile(sim.units["I"])
