"""Shared real-engine movement harness; synthetic terrain, unmodified TO&E."""
import math
from mnsim.model import UnitState
from mnsim.terrain import TerrainModel

UNITS = (
    "INF_PLT",
    "TANK_PLT",
    "ARTY_PLT",
    "INF_COY",
    "INF_BN",
    "TANK_COY",
    "TANK_BN",
    "ARTY_COY",
    "ARTY_BN",
    "INF_IND",
    "RIFLE_SQD",
    "US_RIFLE_SQD",
    "MG_SQD",
    "MG_PLT",
    "US_MECH_INF_PLT_BRADLEY",
    "US_MOT_INF_PLT_HMMWV",
    "BMP_MECH_INF_PLT",
    "ROK_MECH_INF_PLT_K200",
    "US_M1A2_ABRAMS_IND",
    "ROK_K2_TANK_IND",
    "US_M2_BRADLEY_IND",
    "BMP_IFV_IND",
    "ROK_K200_APC_IND",
    "US_HMMWV_IND",
)
DT = (0.25, 1.0, 20.0)
START, GOAL = (40.0, 100.0), (200.0, 100.0)

def install(sim, data):
    sim.terrain = TerrainModel(data, sim.combat_config.get("navigation", {}))
    sim.terrain.world = sim.world
    sim.terrain._units_provider = lambda: sim.units.values()
    return sim.terrain

def river(**extra):
    return dict(id="R", polygon=[[110,0],[130,0],[130,240],[110,240]], **extra)

def rectangle(kind, **extra):
    return dict(id=kind, type=kind, polygon=[[90,70],[150,70],[150,130],[90,130]], **extra)

def bridge(y=100, bid="BR"):
    return dict(id=bid, points=[[100,y],[140,y]], width_m=12, integrity=100, max_integrity=100)

def run(sim, unit, record, dt=1.0, start=START, goal=GOAL, reachable=True, check=None, reset=True):
    if reset:
        unit.pos=start
        unit.metadata={k:v for k,v in unit.metadata.items() if not k.startswith("_nav")}
    unit.state=UnitState.MOVING
    trace=[list(unit.pos)]
    row=dict(unit_type=unit.unit_type.name, echelon=unit.echelon, mobility_class=unit.unit_type.metadata.get("mobility_class"), dt_s=dt, start=list(unit.pos), goal=list(goal), expected_reachable=reachable)
    record.append(row)
    arrived=False; stuck=0; distance=0.0
    try:
        route=sim.terrain.plan_route(unit,goal)
        row["route"]=[list(p) for p in route]
        if reachable:
            assert len(route)>1 and math.dist(route[-1],goal)<1e-6, "planner did not reach destination"
        for a,b in zip(route,route[1:]):
            assert sim.terrain.navigation.segment_passable(unit,a,b), "invalid planned leg"
            if check: check(a,b)
        # Echelon slowdown retains at least 10% of the original speed.
        # Extend observation time; keep the per-tick stall assertion unchanged.
        horizon=12000 if unit.echelon in {"COY","BN"} else 1200
        for tick in range(math.ceil(horizon/dt)):
            previous=unit.pos
            arrived=sim._move_toward(unit,goal,dt)
            sim.time+=dt
            assert all(math.isfinite(v) for v in unit.pos), "nonfinite position"
            assert 0<=unit.pos[0]<=240 and 0<=unit.pos[1]<=240, "escaped map"
            assert sim.terrain.segment_passable(unit,previous,unit.pos), "illegal runtime leg"
            if check: check(previous,unit.pos)
            step=math.dist(previous,unit.pos); distance+=step
            stuck=stuck+1 if step<1e-8 else 0
            trace.append(list(unit.pos))
            if arrived or (reachable and stuck>=12): break
            if not reachable and tick>=max(12,math.ceil(40/dt)): break
        row.update(arrived=arrived, elapsed_s=(tick+1)*dt, distance_m=distance, final=list(unit.pos), remaining_m=math.dist(unit.pos,goal), stationary_ticks=stuck, final_speed_factor=sim.terrain.speed_factor(unit,unit.pos), final_passable=sim.terrain.passable(unit,unit.pos))
        assert arrived is reachable, "arrival expectation mismatch (stalled, timed out, or illegal crossing)"
        if not reachable:
            assert math.dist(unit.pos,start)<1e-6, "unreachable route unexpectedly moved"
        return row
    finally:
        row["trace"]=trace

def outside_rectangle(a,b):
    # Independent slab intersection; closed rectangle is a hard obstacle.
    lo,hi=0.0,1.0
    for origin,delta,mn,mx in ((a[0],b[0]-a[0],90,150),(a[1],b[1]-a[1],70,130)):
        if abs(delta)<1e-12:
            if origin<mn or origin>mx:return
        else:
            t1,t2=sorted(((mn-origin)/delta,(mx-origin)/delta))
            lo=max(lo,t1); hi=min(hi,t2)
            if lo>hi:return
    assert lo>hi, "segment intersects forbidden rectangle"

def bridge_corridor(a,b):
    # Across x=110..130 water, the entire straight leg must stay on the 12 m deck.
    dx=b[0]-a[0]
    if abs(dx)<1e-12:
        if 110<=a[0]<=130: assert 94<=a[1]<=106 and 94<=b[1]<=106
        return
    ts=sorted(((110-a[0])/dx,(130-a[0])/dx))
    lo,hi=max(0,ts[0]),min(1,ts[1])
    if lo<=hi:
        for t in (lo,hi): assert 94-1e-7<=a[1]+t*(b[1]-a[1])<=106+1e-7, "crossed water outside bridge"
