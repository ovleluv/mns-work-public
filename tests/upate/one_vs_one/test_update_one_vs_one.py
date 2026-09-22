"""Run: python tests/upate/one_vs_one/test_update_one_vs_one.py (writes results/)."""
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
from upate.experiment import (TOE, SEEDS, cases, deployment, run_compact,
                                   run_control, write_summary)

HERE = Path(__file__).resolve().parent
CASES = cases()


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


def test_all_templates_and_ordered_branch_pairs_are_covered():
    assert {c['blue'] for c in CASES if c['blue'] == c['red']} == set(TOE)
    branches = {t['branch'] for t in TOE.values()}
    assert {(TOE[c['blue']]['branch'], TOE[c['red']]['branch']) for c in CASES} == {(b,r) for b in branches for r in branches}
    for case in CASES:
        spec = deployment(case, SEEDS[0])
        assert len(spec['units']) == 2
        assert spec['units'][0]['pos'] != spec['units'][1]['pos']
        assert spec['units'][0]['type'] == case['blue']
        assert spec['units'][1]['type'] == case['red']


@pytest.mark.parametrize('law', ['linear', 'square'])
def test_one_vs_one_control_is_symmetric_but_cannot_identify_law(law, report):
    # One physical combatant per side; a simultaneous deterministic hit kills both.
    result = run_control(law, blue=1, red=1, duration=0.5, blue_rate=1, red_rate=1)
    report[1].append({'name':'one_physical_combatant_each', **result})
    assert result['fire_events'] == 2
    assert result['observed'] == [0,0]
    assert result['max_normalized_invariant_drift'] == 0


@pytest.mark.skipif(os.environ.get('MNS_RUN_ENGAGEMENT') != '1', reason='Run this file as a script for the full native simulation matrix.')
@pytest.mark.parametrize('case', CASES, ids=lambda c:c['id'])
def test_native_one_vs_one(case, report, native_pool):
    runs = []
    futures = [native_pool[(case['id'],seed)] for seed in SEEDS]
    for future in futures:
        result = future.result()
        report[0].append(result)
        runs.append(result)
        assert result['initial']['BLUE']['active_formations'] == 1
        assert result['initial']['RED']['active_formations'] == 1
        assert result['violations'] == []
        assert result['time_series'][-1]['t'] == result['duration_s']
    assert all(r['event_counts'].get('FIRE',0) + r['event_counts'].get('INDIRECT_FIRE',0) > 0 for r in runs), f"No engagement fire: {case['id']} (see saved seed results)"


if __name__ == '__main__':
    os.environ['MNS_RUN_ENGAGEMENT'] = '1'
    raise SystemExit(pytest.main([str(Path(__file__).resolve()), '-q', *sys.argv[1:]]))
