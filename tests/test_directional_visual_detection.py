from pathlib import Path
from mnsim.scenario import load_scenario
from mnsim.model import UnitState

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "demo.json"


def _place_relative(obs, tgt, distance, bearing_deg):
    import math
    a=math.radians(bearing_deg)
    tgt.pos=(obs.pos[0]+math.cos(a)*distance, obs.pos[1]+math.sin(a)*distance)


def test_infantry_visual_geometry_combines_forward_sector_and_close_360_awareness():
    sim=load_scenario(str(SCENARIO))
    obs=sim.units["B-INF-2"]
    tgt=sim.units["R-INF-1"]
    obs.watch_heading_deg=0.0
    # Long-range target directly forward is eligible.
    _place_relative(obs,tgt,600,0)
    eligible,limit,_,close=sim._visual_target_geometry(obs,tgt)
    assert eligible and not close and limit==750
    # Same target behind is outside the long-range viewing sector.
    _place_relative(obs,tgt,600,180)
    eligible,_,_,_=sim._visual_target_geometry(obs,tgt)
    assert not eligible
    # But a nearby target behind is still sensed in the 360-degree local awareness bubble.
    _place_relative(obs,tgt,100,180)
    eligible,_,_,close=sim._visual_target_geometry(obs,tgt)
    assert eligible and close


def test_tank_has_narrower_long_range_sector_and_more_constrained_close_awareness_than_infantry():
    sim=load_scenario(str(SCENARIO))
    inf=sim.units["B-INF-2"]
    tank=sim.units["B-TK-1"]
    inf_profile=sim._visual_sensor_profile(inf)
    tank_profile=sim._visual_sensor_profile(tank)
    assert tank_profile[0] > inf_profile[0]    # optics reach farther
    assert tank_profile[1] < inf_profile[1]    # primary viewing sector is narrower
    assert tank_profile[2] < inf_profile[2]    # buttoned vehicle has more constrained all-round local awareness


def test_watch_direction_follows_axis_of_movement_and_slews_instead_of_teleporting():
    sim=load_scenario(str(SCENARIO))
    u=sim.units["B-TK-1"]
    u.watch_heading_deg=180.0
    u.heading_deg=0.0
    u.state=UnitState.MOVING
    # Tank profile is 55 deg/s, so a one-second update cannot snap 180 degrees instantly.
    sim._update_watch_heading(u,1.0)
    assert abs(sim._angle_delta_deg(u.watch_heading_deg,180.0)) <= 55.0001
    assert abs(sim._angle_delta_deg(0.0,u.watch_heading_deg)) < 180.0


def test_counter_battery_radar_geometry_remains_omnidirectional():
    sim=load_scenario(str(SCENARIO))
    # Directional visual geometry does not alter the independent radar model: radar elements still
    # carry only a radial radar_range_m and the launch detector evaluates range, not watch heading.
    radar_unit=sim.units["B-ART-1"]
    radar=[e for e in radar_unit.elements.values() if e.role.upper()=="COUNTER_BATTERY_RADAR"]
    assert radar
    assert radar[0].metadata.get("radar_range_m",0) > 0
    assert "radar_fov_deg" not in radar[0].metadata


def test_default_directional_fov_calibration_is_configurable_and_narrow():
    sim=load_scenario(str(SCENARIO))
    inf=sim.units["B-INF-2"]
    tank=sim.units["B-TK-1"]
    arty=sim.units["B-ART-1"]
    assert sim._visual_sensor_profile(inf)[1] == 90.0
    assert sim._visual_sensor_profile(tank)[1] == 60.0
    assert sim._visual_sensor_profile(arty)[1] == 75.0
    assert sim._visual_sensor_profile(inf)[2] == 220.0
    assert sim._visual_sensor_profile(tank)[2] == 160.0
    assert sim._visual_sensor_profile(arty)[2] == 180.0
    # Prove this is an overrideable data path rather than a branch hard-code.
    tank.unit_type.metadata.setdefault("visual_sensor", {})["forward_fov_deg"] = 42.0
    assert sim._visual_sensor_profile(tank)[1] == 42.0
