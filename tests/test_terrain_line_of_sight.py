"""Ridges/crests block observation and direct fire; high ground sees over them."""
from mnsim.terrain import TerrainModel

HILL = {"areas": [
    {"id": "H1", "type": "ELEVATION", "elevation_m": 30.0, "transition_width_m": 40.0,
     "polygon": [[450, 300], [550, 300], [550, 700], [450, 700]]},
    {"id": "H2", "type": "ELEVATION", "elevation_m": 60.0, "transition_width_m": 40.0,
     "polygon": [[100, 450], [200, 450], [200, 550], [100, 550]]},
]}


def _terrain():
    t = TerrainModel(HILL, {"los_dem_cell_m": 10.0})
    t.world = {"width_m": 1000.0, "height_m": 1000.0}
    return t


def test_ridge_masks_observation_and_direct_fire():
    t = _terrain()
    a, b = (300.0, 500.0), (800.0, 500.0)          # on the flat, hill H1 in between
    assert t.terrain_masks(a, b)
    assert t.observation_modifier(a, b)["detection_factor"] == 0.0
    fire = t.direct_fire_modifier(a, b)
    assert fire["allowed"] is False and fire.get("terrain_masked")


def test_open_ground_is_not_masked():
    t = _terrain()
    a, b = (300.0, 900.0), (800.0, 900.0)          # passes north of the hill
    assert not t.terrain_masks(a, b)
    assert t.observation_modifier(a, b)["detection_factor"] == 1.0


def test_higher_ground_sees_over_a_lower_crest():
    t = _terrain()
    top = (150.0, 500.0)                           # 60 m hill
    far = (950.0, 500.0)                           # far side: sight line clears the 30 m ridge
    assert not t.terrain_masks(top, far)
    assert t.terrain_masks((150.0, 500.0), (620.0, 500.0))   # dead ground just behind the ridge


def test_ui_envelope_stops_at_the_crest():
    t = _terrain()
    limit = t.approx_visual_limit((300.0, 500.0), 0.0, 700.0)
    assert limit < 300.0
