"""Analyze saved native results against implementation contracts, without changing the engine."""
import json,math,statistics,hashlib,sys
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
OUT=Path(__file__).resolve().parent
CASES=ROOT/'tests/upate/many_vs_many/results/cases'

def main():
    report=json.loads((CASES.parent/'results.json').read_text(encoding='utf-8'))
    assert all(hashlib.sha256((ROOT/k).read_bytes()).hexdigest()==v for k,v in report['source_sha256'].items())
    rows=report['runs'];majority=defaultdict(Counter);equal=Counter();exceptions=[];groups=defaultdict(list)
    for r in rows:
        c=r['case'];groups[(c['blue'],c['red'],c['nb'],c['nr'],c['layout'])].append(r)
        if c['blue']!=c['red']:continue
        b,rfinal=r['final']['BLUE']['strength'],r['final']['RED']['strength']
        if c['nb']==c['nr']:
            equal['BLUE_more' if b>rfinal else 'RED_more' if rfinal>b else 'equal']+=1
        else:
            m,n=(b,rfinal) if c['nb']>c['nr'] else (rfinal,b)
            status='majority_more' if m>n else 'minority_more' if n>m else 'equal'
            majority[c['layout']][status]+=1
            if status=='minority_more':exceptions.append(dict(case=c['id'],seed=r['seed'],BLUE=b,RED=rfinal))
    table=[]
    for name in ('INF_PLT','TANK_PLT','ARTY_PLT','US_MECH_INF_PLT_BRADLEY','US_MOT_INF_PLT_HMMWV'):
        for layout in ('line','column','staggered'):
            rr=groups[(name,name,3,3,layout)]
            table.append(dict(template=name,layout=layout,initial_personnel=rr[0]['initial']['BLUE']['personnel'],
                blue_survivors=statistics.mean(r['final']['BLUE']['personnel'] for r in rr),
                red_survivors=statistics.mean(r['final']['RED']['personnel'] for r in rr),
                direct=statistics.mean(r['event_counts'].get('FIRE',0) for r in rr),
                indirect=statistics.mean(r['event_counts'].get('INDIRECT_FIRE',0) for r in rr),
                equipment=statistics.mean(sum(r['final'][s]['equipment'] for s in ('BLUE','RED')) for r in rr)))
    rifle_checks=rifle_bad=arty_checks=arty_bad=0;rifle_participants=Counter();arty_examples=[];both=Counter();first_shots=defaultdict(list)
    for file in sorted(CASES.glob('*.result.json')):
        r=json.loads(file.read_text(encoding='utf-8'));c=r['case'];firing_sides=set()
        rifle_case=c['blue']==c['red']=='INF_PLT'
        arty_sides={s for s in ('BLUE','RED') if c[s.lower()]=='ARTY_PLT'}
        counts={};states={}
        for side,num in (('BLUE',c['nb']),('RED',c['nr'])):
            for i in range(1,num+1):
                uid=f'{side}-{i}'
                for eid in ('rifle_1','rifle_2','rifle_3'):counts[(uid,eid)]=8
                counts[(uid,'gun_crew')]=25;states[uid]=['OPERATIONAL']*5
        first={}
        for e in r['events']:
            kind=e['kind']
            if kind=='ELEMENT_LOSS':counts[(e['target'],e['element'])]=e['remaining']
            if kind=='EQUIPMENT_STATE_CHANGE' and e['element']=='towed_guns' and e['unit'].split('-')[0] in arty_sides:
                states[e['unit']][e['item_index']]=e['new_state']
            if kind in ('FIRE','INDIRECT_FIRE'):
                side=e['shooter'].split('-')[0];firing_sides.add(side);first.setdefault(kind,e['t'])
            if kind=='FIRE' and rifle_case and e['source_element'] in ('rifle_1','rifle_2','rifle_3') and e['weapon']=='small arms':
                n=counts[(e['shooter'],e['source_element'])];rifle_checks+=1
                rifle_bad+=e['firing_participants']!=n;rifle_participants[n]+=1
            if kind=='INDIRECT_FIRE' and e['shooter'].split('-')[0] in arty_sides:
                n=counts[(e['shooter'],'gun_crew')]
                available=sum(x in ('OPERATIONAL','MOBILITY_KILL') for x in states[e['shooter']])
                expected=min(available,n//5)
                # Shipped ARTY_PLT uses unlimited ammunition and one round per piece.
                assert e['ammo_remaining']==-1 and e['fire_profile']=='CONCENTRATED'
                arty_checks+=1;arty_bad+=e['rounds']!=expected
                if c['id']=='ARTY_PLT__INF_PLT__3v3__line' and r['seed']==7:
                    arty_examples.append(dict(t=e['t'],shooter=e['shooter'],crew=n,available=available,rounds=e['rounds']))
        both['both_sides' if len(firing_sides)==2 else 'one_side' if firing_sides else 'none']+=1
        for kind,t in first.items():first_shots[kind].append(t)
    geometry=[]
    for layout in ('line','column','staggered'):
        scenario=json.loads((CASES/f'INF_PLT__INF_PLT__3v3__{layout}__seed7.scenario.json').read_text(encoding='utf-8'))
        b=[u for u in scenario['units'] if u['side']=='BLUE'];red=[u for u in scenario['units'] if u['side']=='RED']
        geometry.append(dict(layout=layout,units=scenario['units'],paired_distances=[math.dist(x['pos'],y['pos']) for x,y in zip(b,red)],
            own_adjacent_distance=math.dist(b[0]['pos'],b[1]['pos']),
            enemy_distances_minmax=[min(math.dist(x['pos'],y['pos']) for x in b for y in red),max(math.dist(x['pos'],y['pos']) for x in b for y in red)]))
    from mnsim.scenario import load_scenario
    from mnsim.formation_geometry import footprint_for
    footprints={}
    for template in ('INF_PLT','INF_COY','INF_BN','ARTY_PLT','ARTY_BN'):
        sim=load_scenario(str(CASES/f'{template}__{template}__3v3__line__seed7.scenario.json'))
        fp=footprint_for(sim,sim.units['BLUE-1'])
        footprints[template]=dict(length=fp.length_m,width=fp.width_m)
    timeouts=[r for r in rows if r['outcome']=='TIME_LIMIT']
    timeout_zero=sum(any(r['final'][side]['weapon_systems']==0 for side in ('BLUE','RED')) for r in timeouts)
    output=dict(footprints=footprints,timeout_count=len(timeouts),timeouts_with_zero_participation_side=timeout_zero,runs=len(rows),source_hashes_match=True,majority_by_layout=majority,equal_strength_counts=equal,
        minority_more_cases=exceptions,representative_layouts=table,rifle_checks=rifle_checks,rifle_mismatches=rifle_bad,
        rifle_participant_histogram=rifle_participants,artillery_checks=arty_checks,artillery_mismatches=arty_bad,
        artillery_examples=arty_examples,firing_sides=both,first_fire_times={k:dict(min=min(v),median=statistics.median(v),max=max(v)) for k,v in first_shots.items()},geometry=geometry)
    (OUT/'design_findings.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in output.items() if k not in ('geometry','representative_layouts','artillery_examples','minority_more_cases')},ensure_ascii=False,indent=2))
    print('EXCEPTIONS',json.dumps(exceptions[:12],ensure_ascii=False))
    print('LAYOUT TABLE',json.dumps(table,ensure_ascii=False))
    print('ARTILLERY EXAMPLE',json.dumps(arty_examples[:10]))

if __name__=='__main__':main()
