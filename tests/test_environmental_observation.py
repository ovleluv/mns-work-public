from pathlib import Path
from mnsim.scenario import load_scenario

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "demo.json"


def test_clear_day_open_terrain_is_neutral():
    sim=load_scenario(str(SCENARIO))
    u=sim.units["B-INF-2"]
    p=sim._visual_sensor_profile(u)
    assert p[0] == 750.0  # INF_PLT visual_sensor.forward_range_m in config/toe_templates.json
    assert p[1] == 90.0
    assert p[2] == 220.0
    assert p[4] == 1.0


def test_weather_dynamically_degrades_same_baseline_profile():
    sim=load_scenario(str(SCENARIO))
    u=sim.units["B-INF-2"]
    clear=sim._visual_sensor_profile(u)
    sim.combat_config["environment"]["weather"]="FOG"
    fog=sim._visual_sensor_profile(u)
    assert fog[0] < clear[0]
    assert fog[1] < clear[1]
    assert fog[2] < clear[2]
    assert fog[4] < clear[4]


def test_sensor_mode_override_can_reduce_environmental_penalty():
    sim=load_scenario(str(SCENARIO))
    u=sim.units["B-TK-1"]
    sim.combat_config["environment"]["weather"]="FOG"
    u.unit_type.metadata.setdefault("visual_sensor", {})["sensor_mode"]="VISUAL"
    visual=sim._visual_sensor_profile(u)
    u.unit_type.metadata["visual_sensor"]["sensor_mode"]="THERMAL"
    thermal=sim._visual_sensor_profile(u)
    assert thermal[0] > visual[0]
    assert thermal[4] > visual[4]


def test_terrain_observation_zone_contract_degrades_crossed_sightline():
    sim=load_scenario(str(SCENARIO))
    obs=sim.units["B-INF-2"]; tgt=sim.units["R-INF-1"]
    obs.pos=(100.0,100.0); tgt.pos=(500.0,100.0)
    sim.terrain.data["observation_zones"]=[{
        "id":"BRUSH-1", "type":"BRUSH",
        "polygon":[[250,0],[350,0],[350,200],[250,200]],
        "observation_modifier":{"range_factor":0.65,"fov_factor":0.9,"awareness_factor":0.9,"detection_factor":0.7}
    }]
    clear_profile=sim._visual_sensor_profile(obs)
    crossed=sim._visual_sensor_profile(obs,tgt)
    assert crossed[0] < clear_profile[0]
    assert crossed[4] < clear_profile[4]
