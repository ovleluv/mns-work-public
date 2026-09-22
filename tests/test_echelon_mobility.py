"""Echelon penalties at runtime, during planning, and after aggregation."""
import json
import math
from pathlib import Path

import pytest

from mnsim.mobility import movement_speed_mps
from mnsim.model import UnitState
from mnsim.scenario import load_scenario
from mnsim.terrain import TerrainModel


@pytest.fixture
def make_sim(tmp_path):
    def make(family="INF", echelons=("PLT", "COY", "BN")):
        path=tmp_path / "mobility.json"
        path.write_text(json.dumps(dict(seed=7,world=dict(width_m=240,height_m=240),
            units=[dict(id=e,name=e,side="BLUE",type=f"{family}_{e}",echelon=e,pos=[40,100])
                   for e in echelons])),encoding="utf-8")
        sim=load_scenario(str(path))
        sim.terrain=TerrainModel({})
        for u in sim.units.values():u.state=UnitState.MOVING
        return sim
    return make


@pytest.mark.parametrize("family",["INF","TANK","ARTY"])
@pytest.mark.parametrize("state",[UnitState.MOVING,UnitState.ATTACKING,UnitState.RETREATING])
@pytest.mark.parametrize("road",[False,True])
def test_final_half_kph_steps_and_runtime(make_sim,family,state,road):
    sim=make_sim(family)
    if road:sim.terrain=TerrainModel({"roads":[{"points":[[0,100],[240,100]],"width_m":12}]})
    speeds=[]
    for u in sim.units.values():
        u.state=state
        speed=movement_speed_mps(u,sim.terrain,u.pos,(200,100))
        speeds.append(speed*3.6)
        before=u.pos
        sim._move_toward(u,(200,100),0.25)
        assert math.dist(before,u.pos)/0.25==pytest.approx(speed)
    assert speeds[0]-speeds[1]==pytest.approx(0.5)
    assert speeds[1]-speeds[2]==pytest.approx(0.5)


def test_low_speed_floor_and_stops(make_sim):
    sim=make_sim()
    sim.terrain=TerrainModel({"areas":[{"type":"WOODS","polygon":[[0,0],[240,0],[240,240],[0,240]],"movement_factor":0.16}]})
    platoon=movement_speed_mps(sim.units["PLT"],sim.terrain)
    for e in ("COY","BN"):
        u=sim.units[e]
        assert movement_speed_mps(u,sim.terrain)==pytest.approx(platoon*0.1)
        before=u.pos
        sim._move_toward(u,(200,100),1)
        assert math.dist(before,u.pos)>0
        for state in (UnitState.IDLE,UnitState.DEFENDING,UnitState.ENGAGING):
            u.state=state
            assert movement_speed_mps(u,sim.terrain)==0
            before=u.pos
            sim._move_toward(u,(200,100),1)
            assert u.pos==before
        u.state=UnitState.MOVING
        u.unit_type.max_speed_mps=0
        assert movement_speed_mps(u,sim.terrain)==0


def test_planning_uses_adjusted_speed_even_when_idle(make_sim):
    sim=make_sim()
    for u in sim.units.values():
        expected=120/movement_speed_mps(u,sim.terrain)
        u.state=UnitState.IDLE
        assert sim.terrain.navigation._segment_time_cost(u,(40,100),(160,100))==pytest.approx(expected)
        assert len(sim.terrain.plan_route(u,(160,100)))>1
    u=sim.units["BN"]
    u.unit_type.max_speed_mps=0
    assert math.isinf(sim.terrain.navigation._segment_time_cost(u,(40,100),(160,100)))
    assert sim.terrain.plan_route(u,(160,100))==[u.pos]


def test_zero_terrain_and_impassable_segment_do_not_move(make_sim):
    sim=make_sim()
    for data in (
        {"areas":[{"type":"WOODS","polygon":[[0,0],[240,0],[240,240],[0,240]],"movement_factor":0}]},
        {"rivers":[{"polygon":[[110,0],[130,0],[130,240],[110,240]]}]},
    ):
        sim.terrain=TerrainModel(data)
        for u in sim.units.values():
            assert sim.terrain.plan_route(u,(200,100))==[u.pos]
            before=u.pos
            sim._move_toward(u,(200,100),1)
            assert u.pos==before


def test_aggregation_uses_live_echelon_without_double_penalty(make_sim):
    sim=make_sim(echelons=("PLT",))
    child=sim.units["PLT"]
    original=movement_speed_mps(child,sim.terrain)
    parent=sim.aggregate_units("P","Company",[child.uid],echelon="COY")
    parent.state=UnitState.MOVING
    assert parent.unit_type.metadata["echelon"]=="PLT"
    assert movement_speed_mps(parent,sim.terrain)==pytest.approx(original-0.5/3.6)
    battalion=sim.aggregate_units("B","Battalion",[parent.uid],echelon="BN")
    battalion.state=UnitState.MOVING
    assert movement_speed_mps(battalion,sim.terrain)==pytest.approx(original-1/3.6)
    sim.deaggregate_unit("B")
    parent.state=UnitState.MOVING
    assert movement_speed_mps(parent,sim.terrain)==pytest.approx(original-0.5/3.6)
    sim.deaggregate_unit("P")
    child.state=UnitState.MOVING
    assert movement_speed_mps(child,sim.terrain)==pytest.approx(original)


def test_penalty_follows_equipment_and_grade_multipliers(make_sim,monkeypatch):
    sim=make_sim("TANK")
    monkeypatch.setattr(sim.terrain,"slope_angle_deg",lambda p,q:19.0)
    for index,u in enumerate(sim.units.values()):
        armor=[e for e in u.elements.values() if e.category=="EQUIPMENT" and "ARMOR" in e.tags]
        for e in armor:
            e.item_states=["OPERATIONAL"]*(e.count//2)+["MOBILITY_KILL"]*(e.count-e.count//2)
        ratio=sum(e.count//2 for e in armor)/sum(e.count for e in armor)
        expected=10*0.85*ratio*0.5-index*0.5/3.6
        assert movement_speed_mps(u,sim.terrain,u.pos,(200,100))==pytest.approx(expected)
        for e in armor:e.item_states=["MOBILITY_KILL"]*e.count
        assert movement_speed_mps(u,sim.terrain,u.pos,(200,100))==0
        assert sim.terrain.plan_route(u,(200,100))==[u.pos]
