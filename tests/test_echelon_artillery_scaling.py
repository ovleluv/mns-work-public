from mnsim.scenario import load_scenario
from mnsim.formation_geometry import footprint_for


def _artillery_weapon(sim):
    # tdg1 no longer contains artillery; borrow the demo battery as an off-map shooter.
    import copy
    demo=load_scenario('scenarios/demo.json')
    shooter=copy.deepcopy(demo.units['R-ART-1'])
    sim.add_unit(shooter)
    return shooter, next(w for e in shooter.elements.values() for w in e.weapons if w.capability=='INDIRECT_FIRE')


def test_battalion_footprint_scales_with_echelon():
    sim=load_scenario('scenarios/tdg1.json')
    bn=sim.units['B-INF_BN-1']
    plt=sim.units['B-INF_PLT-1']
    fbn=footprint_for(sim,bn); fpl=footprint_for(sim,plt)
    assert fbn.length_m >= fpl.length_m*3.0
    assert fbn.width_m >= fpl.width_m*3.0


def test_shell_personnel_cap_is_per_formation_not_per_element():
    sim=load_scenario('scenarios/tdg1.json')
    shooter,w=_artillery_weapon(sim)
    bn=sim.units['B-INF_BN-1']
    # Force every sampled person to be inside a highly lethal shell radius.  The result must
    # still respect one shell-level cap across the whole battalion, not once per element.
    w.metadata['effect_radius_personnel_m']=5000.0
    w.metadata['effect_p_personnel']=1.0
    w.metadata['max_personnel_loss_per_round']=6
    before=bn.personnel
    sim.indirect_fire.resolve_impact({
        'shooter':shooter.uid,'target':bn.uid,'weapon':w.name,'round':1,
        'pos':bn.pos,'mode':'TEST'
    })
    sim.time += 1.0
    for ev in sim.events.pop_due(sim.time):
        sim._handle_event(ev.kind,ev.payload)
    assert before-bn.personnel <= 6
    assert before-bn.personnel > 0


def test_indirect_cue_temporarily_dispenses_formation():
    sim=load_scenario('scenarios/tdg1.json')
    bn=sim.units['B-INF_BN-1']
    bn.metadata['threat_cue_type']='INDIRECT_FIRE'
    bn.metadata['threat_cue_until_t']=sim.time+5.0
    sim.doctrine.step(bn,0.1)
    assert bn.metadata.get('dispersion_posture')=='DISPERSED'
