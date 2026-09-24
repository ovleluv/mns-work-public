from mnsim.scenario import load_scenario

sim=load_scenario('scenarios/demo.json')
# 600 s: with suppression/morale an initial clash may pause while formations rally, then resume.
for _ in range(int(600/0.25)):
    sim.tick(0.25)

attackers=[sim.units[x] for x in ('B-INF-2','B-INF-3','B-TK-1')]
# ATTACK must not silently complete merely because the waypoint/objective was reached.
# A reactive BML casualty branch (e.g. HOLD/RETREAT) is legitimate and may replace ATTACK.
assert all(u.current_order is not None for u in attackers)
assert not any(x['kind']=='ORDER_COMPLETE' and x.get('unit') in {'B-INF-2','B-INF-3','B-TK-1'} for x in sim.logs)
# Living attackers must remain active. A destroyed vehicle formation may now have
# evacuated its survivors into separate foot units instead of retaining immobile crews.
assert all(u.state.value in {'ATTACKING','SEARCHING','ENGAGING','DEFENDING','RETREATING'}
           for u in attackers if u.alive)
assert all(u.state.value == 'DESTROYED' and u.current_strength == 0
           for u in attackers if not u.alive)
# Verify that combat still occurs well into the run rather than dying after the initial contact.
direct=[x for x in sim.logs if x['kind']=='FIRE' and x.get('mode')=='DIRECT']
assert direct and direct[-1]['t'] > 400.0
print('persistent attack / re-engagement tests: PASS')
