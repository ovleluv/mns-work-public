"""Reproduce selected surprising cases, preserving original experiment files."""
import os,sys,json
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from mnsim.scenario import load_scenario

def main():
    if os.environ.get('PYTHONHASHSEED')!='0':
        import subprocess
        raise SystemExit(subprocess.call([sys.executable,*sys.argv],env={**os.environ,'PYTHONHASHSEED':'0'}))
    base=ROOT/'tests/upate/many_vs_many/results/cases'
    for name in ('US_M2_BRADLEY_IND__US_M2_BRADLEY_IND__4v2__column__seed7','INF_IND__INF_IND__4v2__staggered__seed7'):
        sim=load_scenario(str(base/(name+'.scenario.json')))
        for _ in range(480):sim.tick(.25)
        old=json.loads((base/(name+'.result.json')).read_text(encoding='utf-8'))
        assert dict(Counter(e['kind'] for e in sim.logs))==old['event_counts']
        filtered=[e for e in sim.logs if e['kind'] in ('DIRECT_FIRE_ACQUIRING','DIRECT_FIRE_WAIT_ORIENTATION','DIRECT_FIRE_NO_TARGETING_SOLUTION','ELEMENT_LOSS','FIRE')]
        (Path(__file__).parent/(name+'.trace.json')).write_text(json.dumps(dict(original_counts_reproduced=True,events=filtered),indent=2),encoding='utf-8')
        print(name,'REPRODUCED')

if __name__=='__main__':main()
