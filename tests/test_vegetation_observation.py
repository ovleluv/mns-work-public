from mnsim.terrain import TerrainModel


def _forest(poly, **extra):
    z={"id":"F","type":"FOREST","polygon":poly,
       "observation_modifier":{"range_factor":0.9,"fov_factor":0.8,"awareness_factor":0.75,"detection_factor":0.55},
       "sensor_penetration_m":{"VISUAL":100.0,"THERMAL":120.0}}
    z.update(extra)
    return z


def test_forest_path_blocking_is_reciprocal():
    t=TerrainModel({"areas":[_forest([(40,-20),(180,-20),(180,20),(40,20)])]})
    ab=t.observation_modifier((0,0),(220,0),"VISUAL")
    ba=t.observation_modifier((220,0),(0,0),"VISUAL")
    assert ab["detection_factor"] == 0.0
    assert ba["detection_factor"] == 0.0


def test_forest_edge_can_look_far_into_open_ground():
    # Only 10 m of the 800 m ray crosses forest: no blanket 100 m view-range cap.
    t=TerrainModel({"areas":[_forest([(-50,-50),(10,-50),(10,50),(-50,50)])]})
    m=t.observation_modifier((0,0),(800,0),"VISUAL")
    assert m["range_factor"] > 0.98
    assert m["detection_factor"] > 0.90


def test_target_in_forest_is_harder_to_detect_than_open_target():
    forest=_forest([(0,-50),(40,-50),(40,50),(0,50)])
    t=TerrainModel({"areas":[forest]})
    # Same short vegetation crossing; direction changes only which endpoint receives concealment.
    open_to_forest=t.observation_modifier((100,0),(10,0),"VISUAL")
    forest_to_open=t.observation_modifier((10,0),(100,0),"VISUAL")
    assert open_to_forest["range_factor"] == forest_to_open["range_factor"]
    assert open_to_forest["detection_factor"] < forest_to_open["detection_factor"]


def test_thermal_has_slightly_more_forest_penetration_than_visual():
    t=TerrainModel({"areas":[_forest([(20,-20),(130,-20),(130,20),(20,20)])]})
    assert t.observation_modifier((0,0),(160,0),"VISUAL")["detection_factor"] == 0.0
    assert t.observation_modifier((0,0),(160,0),"THERMAL")["detection_factor"] > 0.0


def test_close_geometry_is_still_blocked_by_deep_forest_attenuation():
    # The terrain modifier itself is the contract used by CLOSE and FORWARD sensing.  Zero means
    # the sensor path is hard-blocked and Simulation must not create a proximity track through it.
    t=TerrainModel({"areas":[_forest([(-80,-20),(80,-20),(80,20),(-80,20)])]})
    m=t.observation_modifier((-70,0),(70,0),"VISUAL")
    assert m["detection_factor"] == 0.0
