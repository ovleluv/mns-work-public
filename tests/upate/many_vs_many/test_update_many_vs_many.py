"""Run: python tests/upate/many_vs_many/test_update_many_vs_many.py (writes results/)."""
import os
import sys
from pathlib import Path

if __name__ == '__main__':
    if os.environ.get('PYTHONHASHSEED') != '0':
        import subprocess
        raise SystemExit(subprocess.call([sys.executable,*sys.argv], env={**os.environ,'PYTHONHASHSEED':'0'}))
    sys.path[:0] = [str(Path(__file__).resolve().parents[3]), str(Path(__file__).resolve().parents[2])]

import pytest
from concurrent.futures import ProcessPoolExecutor
from upate.experiment import (TOE, SEEDS, LAYOUTS, cases, deployment,
                                   run_compact, run_control, rate_probe, write_summary)

HERE = Path(__file__).resolve().parent
CASES = cases(many=True) + [
    dict(blue=name,red=name,nb=nb,nr=nr,layout=layout,
         id=f'{name}__{name}__{nb}v{nr}__{layout}')
    for name in TOE for layout in ('column','staggered') for nb,nr in ((4,2),(2,4))]


@pytest.fixture(scope='module')
def native_pool():
    workers=max(1,min(6,os.cpu_count() or 1))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        yield {(case['id'],seed):pool.submit(run_compact,case,seed,HERE/'results/cases')
               for case in CASES for seed in SEEDS}


@pytest.fixture(scope='module')
def report():
    rows, controls = [], []
    yield rows, controls
    if os.environ.get('MNS_RUN_ENGAGEMENT') == '1':
        write_summary(HERE / 'results', rows, controls)


def test_each_template_has_every_layout_and_both_numerical_advantages():
    for name in TOE:
        own = [c for c in CASES if c['blue'] == c['red'] == name]
        assert {c['layout'] for c in own} == set(LAYOUTS)
        assert {(c['nb'],c['nr']) for c in own} == {(3,3),(4,2),(2,4)}
    branches = {t['branch'] for t in TOE.values()}
    for layout in LAYOUTS:
        assert {(TOE[c['blue']]['branch'],TOE[c['red']]['branch']) for c in CASES if c['layout']==layout} == {(b,r) for b in branches for r in branches}
    for case in CASES:
        spec = deployment(case, SEEDS[0])
        assert len({tuple(u['pos']) for u in spec['units']}) == case['nb']+case['nr']
        assert sum(u['side']=='BLUE' for u in spec['units']) == case['nb']
        assert sum(u['side']=='RED' for u in spec['units']) == case['nr']


@pytest.mark.parametrize('blue,red,br,rr', [(40,20,1,1),(20,40,1,1),(40,20,1,0.5)])
def test_first_law_fixed_frontage_losses_and_linear_invariant(blue,red,br,rr,report):
    # One engaged weapon per side, other personnel are reserves. B'=-rr, R'=-br.
    result = run_control('linear',blue,red,duration=6,blue_rate=br,red_rate=rr)
    report[1].append({'name':'fixed_frontage_linear_law', **result})
    assert result['fire_events'] > 0
    assert result['observed'] == pytest.approx(result['expected'],abs=1)
    assert result['max_normalized_invariant_drift'] <= 0.025


@pytest.mark.parametrize('blue,red,br,rr', [(40,20,.01,.01),(20,40,.01,.01),(40,20,.01,.02)])
def test_second_law_aimed_fire_trajectory_and_square_invariant(blue,red,br,rr,report):
    # All surviving personnel supply one system: B'=-rr*R, R'=-br*B.
    result = run_control('square',blue,red,blue_rate=br,red_rate=rr)
    report[1].append({'name':'aimed_fire_square_law', **result})
    assert result['fire_events'] > 0
    assert result['observed'] == pytest.approx(result['expected'],abs=2)
    assert result['max_normalized_invariant_drift'] <= 0.08


def test_square_control_is_stable_when_time_step_is_halved(report):
    coarse = run_control('square',dt=.05)
    fine = run_control('square',dt=.025)
    report[1].append({'name':'time_step_convergence','coarse':coarse,'fine':fine})
    assert coarse['observed'] == pytest.approx(fine['observed'],abs=1)


def test_linear_cadence_scales_with_surviving_personnel(report):
    observed=[rate_probe(n,False) for n in (1,2,4)]
    report[1].append({'name':'rpm_path_system_scaling','systems':[1,2,4],'shots':observed})
    assert observed == pytest.approx([10,20,40],abs=1)


def test_explicit_cycle_path_scales_with_surviving_personnel(report):
    observed=[rate_probe(n,True) for n in (1,2,4)]
    result={'name':'native_cycle_path_square_law_diagnostic','systems':[1,2,4],
            'shots':observed,'pure_square_law_expected':[10,20,40],
            'square_law_satisfied':abs(observed[2]-4*observed[0])<=1,
            'reason':'Each surviving shooter contributes one engagement-cycle rate.'}
    report[1].append(result)
    assert observed == pytest.approx([10,20,40], abs=1)
    assert result['square_law_satisfied']


@pytest.mark.skipif(os.environ.get('MNS_RUN_ENGAGEMENT') != '1', reason='Run this file as a script for the full native simulation matrix.')
@pytest.mark.parametrize('case', CASES, ids=lambda c:c['id'])
def test_native_many_vs_many(case, report, native_pool):
    runs=[]
    futures=[native_pool[(case['id'],seed)] for seed in SEEDS]
    for future in futures:
        result=future.result()
        report[0].append(result)
        runs.append(result)
        assert result['initial']['BLUE']['active_formations'] == case['nb']
        assert result['initial']['RED']['active_formations'] == case['nr']
        assert result['violations'] == []
        assert result['time_series'][-1]['t'] == result['duration_s']
    assert all(r['event_counts'].get('FIRE',0) + r['event_counts'].get('INDIRECT_FIRE',0) > 0 for r in runs), f"No engagement fire: {case['id']} (see saved seed results)"


@pytest.mark.skipif(os.environ.get('MNS_RUN_ENGAGEMENT') != '1', reason='Strict native-model conformance check runs with the full experiment.')
def test_native_explicit_cycle_satisfies_pure_square_law_requirement(report):
    observed=[rate_probe(n,True) for n in (1,2,4)]
    report[1].append({'name':'strict_native_square_law_requirement','shots':observed,
                      'expected':[10,20,40],'status':'PASS' if observed==[10,20,40] else 'FAIL'})
    assert observed == pytest.approx([10,20,40],abs=1), 'Native explicit-cycle cadence does not scale linearly with weapon count.'


if __name__ == '__main__':
    os.environ['MNS_RUN_ENGAGEMENT']='1'
    raise SystemExit(pytest.main([str(Path(__file__).resolve()),'-q',*sys.argv[1:]]))
