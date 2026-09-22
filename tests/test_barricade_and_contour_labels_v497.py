import math
from pathlib import Path

from mnsim.cartography import contour_label_candidate
from mnsim.terrain import TerrainModel
from mnsim.scenario import load_scenario
from mnsim.bml import compile_mission


def test_contour_label_uses_tangent_and_avoids_prior_label():
    pts=[(0,0),(200,0),(200,120),(0,120)]
    first=contour_label_candidate(pts,(40,14),[],5)
    assert first is not None
    _, angle1, rect1=first
    assert abs(angle1) < 1e-6
    second=contour_label_candidate(pts,(40,14),[rect1],5)
    assert second is not None
    assert second[2] != rect1


def test_mil1_geometry_and_directional_cover():
    t=TerrainModel({'roads':[],'rivers':[],'bridges':[],'areas':[],'barricades':[]})
    wall=t.add_barricade((100,100),0)
    a,b=t.barricade_endpoints(wall)
    assert math.isclose(math.dist(a,b),10.0,rel_tol=1e-6)
    # heading 0 faces east, so west side is protected.
    assert t.barricade_cover((130,100),(95,100)) is wall
    assert t.barricade_cover((70,100),(95,100)) is None
    lane=t.direct_fire_modifier((130,100),(95,100))
    assert lane.get('barricade_cover')==wall['id']
    assert lane.get('barricade_small_arms_factor')==0.40


def test_barricade_blocks_direct_path_but_has_endpoint_route(tmp_path):
    # Generic geometry contract: an edge through the wall is blocked.
    t=TerrainModel({'roads':[],'rivers':[],'bridges':[],'areas':[],'barricades':[]})
    t.add_barricade((50,50),0)
    assert t.segment_crosses_barricade((80,50),(20,50))
    assert not t.segment_crosses_barricade((80,70),(20,70))


def test_build_barricade_requires_allocated_capacity_and_constructs():
    root=Path(__file__).resolve().parents[1]
    sim=load_scenario(str(root/'scenarios'/'test2.json'),bml_files={})
    u=sim.units['B-INF_PLT-1']
    u.metadata['barricade_limit']=1
    u.order_queue.clear();u.current_order=None
    uid,o=compile_mission(sim,{'unit':u.uid,'task':'BUILD_BARRICADE','position':list(u.pos),'heading_deg':90,'construction_time_s':1})
    sim.issue_order(uid,o)
    for _ in range(10): sim.tick(0.25)
    assert len(sim.terrain.barricades)==1
    assert sim.terrain.barricades[0]['builder_uid']==u.uid
    assert u.metadata['barricades_built']==1


def test_build_barricade_default_capacity_zero_rejects():
    root=Path(__file__).resolve().parents[1]
    sim=load_scenario(str(root/'scenarios'/'test2.json'),bml_files={})
    u=sim.units['B-INF_PLT-1']
    u.metadata.pop('barricade_limit',None)
    u.order_queue.clear();u.current_order=None
    uid,o=compile_mission(sim,{'unit':u.uid,'task':'BUILD_BARRICADE','position':list(u.pos),'heading_deg':0,'construction_time_s':1})
    sim.issue_order(uid,o)
    for _ in range(4): sim.tick(0.25)
    assert not sim.terrain.barricades
