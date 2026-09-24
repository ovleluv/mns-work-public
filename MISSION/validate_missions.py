"""Audit mission capability only; never persist combat outcomes or replay logs.
Run from any directory: python <project>/MISSION/validate_missions.py [--case KEY].
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from mnsim.scenario import load_scenario
from mnsim.bml import MISSION_TASKS, ConditionEvaluator, compile_order_fragment


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def validate_document(sim, doc, side):
    assert doc['side'] == side
    assert doc.get('replace_existing_orders') is True
    commanded = set()
    seen_ids = set()
    def fragment(raw, uid):
        assert raw['task'] in MISSION_TASKS, raw['task']
        assert raw['id'] not in seen_ids, raw['id']
        seen_ids.add(raw['id'])
        assert raw.get('unit', uid) == uid
        compile_order_fragment(sim, sim.units[uid], raw)
        assert 'target_position' not in raw and 'target' not in raw, 'These cases use coordinates/Tracks'
        for key in ('destination', 'center'):
            if key in raw:
                x, y = raw[key]
                assert 0 <= x <= sim.world['width_m'] and 0 <= y <= sim.world['height_m']
        for cond in raw.get('conditions', []):
            assert cond['lhs'] != 'self.enemy_count_near', 'Ground-truth helper is forbidden here'
            assert cond.get('op', '>=') in ConditionEvaluator.OPS
            ConditionEvaluator.resolve(sim, sim.units[uid], cond['lhs'])
        for key in ('on_true', 'on_false', 'on_deadline'):
            if raw.get(key):
                fragment(raw[key], uid)
    for mission in doc['missions']:
        uid = mission['unit']
        assert sim.units[uid].side.value == side
        commanded.add(uid)
        fragment(mission, uid)
    assert commanded == {u.uid for u in sim.units.values() if u.side.value == side}


# Checks that must hold on EVERY seed (contract/invariant).  All other checks are capability
# demonstrations: stochastic combat means a single seed may legitimately not exhibit them, so a
# capability passes when at least one validation seed demonstrates it.
INVARIANT_CHECKS = {'json_and_recursive_bml_valid', 'normal_engine_run_without_order_errors',
                    'initial_enemy_beyond_sensor', 'reserve_waited', 'blue_remained_in_area'}


def audit(case, dt, seed=None):
    scenario = HERE / case['scenario']
    sim = load_scenario(str(scenario), bml_files={s: str(HERE / case[s.lower() + '_bml']) for s in ('BLUE', 'RED')})
    if seed is not None:
        sim.rng.seed(int(seed))
    for side in ('BLUE', 'RED'):
        validate_document(sim, read(HERE / case[side.lower() + '_bml']), side)
    initial = {u.uid: tuple(u.pos) for u in sim.units.values()}
    blue = {u.uid for u in sim.units.values() if u.side.value == 'BLUE'}
    assert all(not u.local_tracks for u in sim.units.values())
    seen = {uid: set() for uid in sim.units}
    moved = set()
    fired = set()
    tracked = set()
    shared = set()
    branches = set()
    completed = set()
    invalid = set()
    early_fire_before_ambush_release = False
    reserve_held_until_release = True
    reserve_moved_after_release = False
    defender_within_area = True
    # Sensors, movement, combat, damage and communications run normally. No injected Tracks,
    # forced casualties, teleportation, altered weapon data or monkey-patched engine methods.
    for _ in range(round(case['duration_s'] / dt)):
        sim.tick(dt)
        for uid, u in sim.units.items():
            if u.current_order:
                seen[uid].add(u.current_order.order_id)
            if math.dist(initial[uid], u.pos) > 1:
                moved.add(uid)
            if uid in blue and u.local_tracks:
                tracked.add(uid)
            if uid == 'B-RESERVE':
                displacement = math.dist(initial[uid], u.pos)
                if sim.time < 90 and displacement > 1:
                    reserve_held_until_release = False
                if sim.time > 90 and displacement > 1:
                    reserve_moved_after_release = True
            if uid == 'B-DEFENDER' and math.dist(u.pos, (650, 600)) > 100.01:
                defender_within_area = False
        for event in sim.logs:
            kind = event['kind']
            if kind == 'FIRE':
                fired.add(event['shooter'])
                if event['shooter'] == 'B-WAIT' and event['t'] < 120:
                    early_fire_before_ambush_release = True
            elif kind == 'TRACK_SHARED':
                shared.add((event.get('source'), event.get('recipient')))
            elif kind in ('CONDITION_BRANCH', 'DEADLINE_BRANCH'):
                branches.add(event['from_order'])
            elif kind == 'ORDER_COMPLETE':
                completed.add(event['order_id'])
            elif kind in ('ORDER_UNKNOWN', 'ORDER_UNREACHABLE', 'BML_TARGET_MISSING'):
                invalid.add(kind)
        # Discard detailed engagement events; persist capability booleans only.
        sim.logs.clear()
    checks = {'json_and_recursive_bml_valid': True, 'normal_engine_run_without_order_errors': not invalid}
    key = case['key']
    if key == 'ATTACK':
        checks.update(blue_advanced=bool(blue & moved), blue_acquired_track=bool(blue & tracked), blue_engaged=bool(blue & fired))
    elif key == 'MOVEMENT_TO_CONTACT':
        u = sim.units['B-SEARCH']
        checks.update(initial_enemy_beyond_sensor=math.dist(initial['B-SEARCH'], initial['R-UNKNOWN']) > u.unit_type.detection_range_m,
                      blue_advanced='B-SEARCH' in moved, contact_acquired='B-SEARCH' in tracked, contact_engaged='B-SEARCH' in fired)
    elif key == 'AMBUSH':
        checks.update(red_passage_moved='R-PATROL' in moved, blue_contact_acquired='B-WAIT' in tracked,
                      timed_transition='AMBUSH-WAIT' in branches, displacement_entered='AMBUSH-DISPLACE' in seen['B-WAIT'],
                      limitation_hold_allows_early_fire=early_fire_before_ambush_release)
    elif key == 'RECONNAISSANCE':
        checks.update(scout_moved='B-SCOUT' in moved, scout_acquired_track='B-SCOUT' in tracked,
                      report_shared=('B-SCOUT', 'B-REPORT-RECIPIENT') in shared,
                      observation_phase_entered='RECON-OBSERVE' in seen['B-SCOUT'],
                      return_completed='RECON-RETURN' in completed)
    elif key == 'AREA_DEFENSE':
        checks.update(red_advanced='R-ATTACKER' in moved, blue_engaged='B-DEFENDER' in fired, blue_remained_in_area=defender_within_area)
    elif key == 'MOBILE_DEFENSE':
        checks.update(fixing_force_engaged='B-FIX' in fired, reserve_waited=reserve_held_until_release,
                      timed_release='MOBILE-RESERVE-WAIT' in branches, reserve_maneuvered=reserve_moved_after_release,
                      reserve_engaged='B-RESERVE' in fired)
    elif key == 'DELAYING_OPERATION':
        checks.update(initial_contact_engaged='B-DELAY' in fired, first_line_transition='DELAY-LINE-1' in branches,
                      second_line_entered='DELAY-LINE-2' in seen['B-DELAY'], second_line_transition='DELAY-LINE-2' in branches,
                      final_line_entered='DELAY-LINE-3' in seen['B-DELAY'],
                      first_withdrawal_completed=bool({'DELAY-RETREAT-1','DELAY-EARLY-RETREAT-1'} & completed),
                      second_withdrawal_completed='DELAY-RETREAT-2' in completed)
    elif key == 'WITHDRAWAL':
        checks.update(initial_contact_engaged='B-WITHDRAW' in fired, withdrawal_triggered='WITHDRAW-CONTACT' in branches,
                      retreat_executed='WITHDRAW-BREAK' in seen['B-WITHDRAW'], retreat_completed='WITHDRAW-BREAK' in completed,
                      rally_hold_entered='WITHDRAW-RALLY' in seen['B-WITHDRAW'])
    return {'case': key, 'capability': case['capability'], 'verification': 'PASS' if all(checks.values()) else 'REVIEW_REQUIRED', 'checks': checks}


def audit_seeds(case, dt, seeds):
    runs = [audit(case, dt, seed) for seed in seeds]
    keys = list(runs[0]['checks'])
    checks = {}
    for k in keys:
        values = [bool(r['checks'].get(k)) for r in runs]
        checks[k] = all(values) if k in INVARIANT_CHECKS else any(values)
    per_seed = {str(seed): {k: v for k, v in r['checks'].items() if not v} for seed, r in zip(seeds, runs)}
    return {'case': case['key'], 'capability': case['capability'],
            'verification': 'PASS' if all(checks.values()) else 'REVIEW_REQUIRED', 'checks': checks,
            'seeds': list(seeds), 'failed_checks_by_seed': per_seed}


def unsupported_contracts():
    case = read(HERE / 'MISSION_MANIFEST.json')['cases'][0]
    sim = load_scenario(str(HERE / case['scenario']), bml_files={})
    unit = sim.units['B-MAIN']
    checks = {}
    for task in ('AMBUSH','RECONNAISSANCE','MOBILE_DEFENSE','DELAY'):
        try:
            compile_order_fragment(sim, unit, {'id':'UNSUPPORTED-PROBE','task':task})
        except ValueError as exc:
            checks['dedicated_' + task.lower() + '_task_absent'] = 'unsupported BML mission task' in str(exc)
        else:
            checks['dedicated_' + task.lower() + '_task_absent'] = False
    try:
        ConditionEvaluator.resolve(sim, unit, 'self.track_count')
    except ValueError as exc:
        checks['track_count_condition_absent'] = 'unsupported condition path' in str(exc)
    else:
        checks['track_count_condition_absent'] = False
    return checks


def fingerprint():
    files = sorted(list((ROOT/'mnsim').glob('*.py')) + list((ROOT/'config').glob('*.json')) + list((ROOT/'database').glob('*')))
    files += sorted(HERE.rglob('*.json'))
    files.append(Path(__file__).resolve())
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files if p.is_file() and p.name != 'VALIDATION_STATUS.json'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', help='Run only the named case; does not overwrite the full status file')
    args = parser.parse_args()
    manifest = read(HERE / 'MISSION_MANIFEST.json')
    cases = [c for c in manifest['cases'] if not args.case or c['key'] == args.case.upper()]
    if not cases:
        parser.error('Unknown case: ' + str(args.case))
    results = []
    base = int(manifest['seed'])
    seeds = [int(x) for x in manifest.get('validation_seeds', [base + i for i in range(5)])]
    for case in cases:
        try:
            result = audit_seeds(case, manifest['dt_s'], seeds)
        except Exception as exc:
            result = {'case':case['key'], 'capability':case['capability'], 'verification':'ERROR', 'error':str(exc)}
        results.append(result)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    contracts = unsupported_contracts()
    status = {'checked_at_utc':datetime.now(timezone.utc).isoformat(), 'python':sys.version.split()[0],
              'seed':manifest['seed'], 'validation_seeds':seeds, 'dt_s':manifest['dt_s'],
              'meaning':'PASS verifies the declared supported subset and stated limitations, not a battle victory or full operation support. '
                        'Invariant checks must hold on every validation seed; capability checks must be demonstrated on at least one.',
              'cases':results, 'unsupported_contracts':contracts, 'sha256':fingerprint()}
    if not args.case:
        (HERE/'VALIDATION_STATUS.json').write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return 0 if all(r['verification']=='PASS' for r in results) and all(contracts.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
