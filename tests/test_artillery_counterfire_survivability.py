from mnsim.scenario import load_scenario


def _arty_gun_and_weapon(sim):
    arty=sim.units['B-ART-1']
    gun=next(e for e in arty.elements.values() if e.metadata.get('protection_class')=='UNARMORED_GUN')
    weapon=next(w for e in arty.elements.values() for w in e.weapons if w.capability=='INDIRECT_FIRE')
    return gun, weapon


def test_unarmored_gun_uses_conservative_counterfire_profile():
    sim=load_scenario('scenarios/demo.json')
    gun, _ = _arty_gun_and_weapon(sim)
    p=sim.combat_config['indirect_fire_vulnerability_profiles'][gun.metadata['protection_class']]
    assert p['p_disable_near'] <= 0.06
    assert p['p_disable_fragment'] <= 0.008


def test_near_and_fragment_counterfire_do_not_frequently_disable_towed_gun():
    sim=load_scenario('scenarios/demo.json')
    gun, weapon = _arty_gun_and_weapon(sim)
    n=6000
    near=sum(sim.indirect_fire._equipment_blast_effect(gun, 10.0, weapon)[0] is not None for _ in range(n))/n
    frag=sum(sim.indirect_fire._equipment_blast_effect(gun, 45.0, weapon)[0] is not None for _ in range(n))/n
    # Monte-Carlo guardrails, deliberately broad enough to avoid a flaky test.
    assert 0.04 <= near <= 0.085
    assert 0.002 <= frag <= 0.02
