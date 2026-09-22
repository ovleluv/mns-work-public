from pathlib import Path

from mnsim.model import Order, UnitState
from mnsim.scenario import load_scenario

ROOT=Path(__file__).resolve().parents[1]


def _destroy_all_bridges(sim):
    for br in sim.terrain.bridges:
        br['destroyed']=True
        br['integrity']=0.0
        br['structural_integrity']=0.0

def _non_fording_scenario():
    sim=load_scenario(str(ROOT/'scenarios/tdg1.json'))
    # TDG1 now explicitly allows FOOT fording. This test needs a formation
    # that depends on bridges; do not silently rely on older scenario data.
    for river in sim.terrain.rivers:
        river.setdefault('mobility_overrides',{})['FOOT']=0.0
    return sim


def test_destroyed_bridge_causes_unreachable_move_to_be_abandoned_instead_of_retried_forever():
    sim=_non_fording_scenario()
    u=sim.units['B-INF_BN-1']
    _destroy_all_bridges(sim)

    # BLUE battalion starts south of the river; this point is north of it.
    dest=(2500.0,700.0)
    assert len(sim.terrain.plan_route(u,dest)) == 1

    u.current_order=Order('TEST-CROSSING','MOVE',{'destination':dest})
    u.order_queue=[]
    sim.tick(0.25)

    assert u.current_order is None
    assert u.state == UnitState.DEFENDING
    assert u.metadata.get('autonomous_fallback_reason') == 'NO ROUTE / AUTONOMOUS HOLD'
    assert any(x['kind']=='ORDER_UNREACHABLE' and x['unit']==u.uid for x in sim.logs)

    # Further ticks must not resurrect/retry the impossible route.
    before=tuple(u.pos)
    for _ in range(8):
        sim.tick(0.25)
    assert tuple(u.pos) == before
    assert u.state == UnitState.DEFENDING


def test_destroyed_bridge_invalidates_cached_route_and_reports_no_path():
    sim=_non_fording_scenario()
    u=sim.units['B-INF_BN-1']
    dest=(2500.0,700.0)

    route=sim.terrain.plan_route(u,dest)
    assert len(route)>1
    # Prime next-waypoint cache while the bridge still exists.
    sim.terrain.movement_target(u,dest)

    _destroy_all_bridges(sim)
    wp=sim.terrain.movement_target(u,dest)
    assert tuple(wp)==tuple(u.pos)
    assert u.metadata.get('_nav_no_path') is True
