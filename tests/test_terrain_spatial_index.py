"""The terrain spatial indices must return exactly what a brute-force scan returns."""
import random
from pathlib import Path

from mnsim.scenario import load_scenario
from mnsim.terrain import BOUNDARY_EPS_M, _point_in_poly, _point_polygon_boundary_distance, _point_segment_distance

ROOT = Path(__file__).resolve().parents[1]


def _brute_on_road(t, p):
    for road in t.roads:
        pts = [tuple(x) for x in road.get("points", [])]
        w = float(road.get("width_m", 30.0))
        if any(_point_segment_distance(p, a, b) <= w / 2 + BOUNDARY_EPS_M for a, b in zip(pts, pts[1:])):
            return True
    return False


def _brute_river(t, p):
    for river in t.rivers:
        poly = [tuple(x) for x in river.get("polygon", [])]
        if poly and (_point_in_poly(p, poly) or _point_polygon_boundary_distance(p, poly) <= BOUNDARY_EPS_M):
            return river
        pts = [tuple(x) for x in river.get("points", [])]
        w = float(river.get("width_m", 0.0))
        if len(pts) >= 2 and w > 0 and any(_point_segment_distance(p, a, b) <= w / 2 + BOUNDARY_EPS_M
                                            for a, b in zip(pts, pts[1:])):
            return river
    return None


def _brute_areas(t, p):
    return [a for a in t.areas if a.get("polygon") and _point_in_poly(p, [tuple(x) for x in a["polygon"]])]


def test_indices_match_brute_force_on_shipped_terrains():
    rng = random.Random(4)
    for scen in ("tdg3.json", "tdg1.json", "demo.json", "test3.json"):
        sim = load_scenario(str(ROOT / "scenarios" / scen), bml_files={})
        t = sim.terrain
        W, H = sim.world["width_m"], sim.world["height_m"]
        pts = [(rng.uniform(0, W), rng.uniform(0, H)) for _ in range(3000)]
        # Points exactly on authored vertices exercise the boundary tolerance.
        pts += [tuple(map(float, x)) for f in t.roads + t.rivers for x in f.get("points", [])][:500]
        for p in pts:
            assert t.on_road(p) == _brute_on_road(t, p), (scen, p)
            assert t.river_at(p) is _brute_river(t, p), (scen, p)
            assert t.area_at(p) == _brute_areas(t, p), (scen, p)


def test_index_follows_runtime_terrain_edits():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
    t = sim.terrain
    p = (123.0, 456.0)
    assert not t.on_road(p)
    t.roads.append({"id": "NEW", "points": [[100.0, 456.0], [200.0, 456.0]], "width_m": 10.0})
    assert t.on_road(p)
    t.areas.append({"id": "BOX", "type": "WOODS", "polygon": [[110, 440], [140, 440], [140, 470], [110, 470]]})
    assert any(a.get("id") == "BOX" for a in t.area_at(p))
