"""Reproducible engine experiments; no production combat code is replaced.

Native runs use loaded TO&E, ordinary sensors, doctrine, ammo, and damage.
Only the small analytic controls use perfect tracks and synthetic weapons.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import statistics
from collections import Counter
from pathlib import Path

from mnsim.model import FormationElement, Side, Track, Unit, UnitType, WeaponModel
from mnsim.scenario import load_scenario
from mnsim.simulation import Simulation

ROOT = Path(__file__).resolve().parents[2]
TOE = json.loads((ROOT / "config/toe_templates.json").read_text(encoding="utf-8"))["unit_types"]
REPRESENTATIVES = ("INF_PLT", "TANK_PLT", "ARTY_PLT", "US_MECH_INF_PLT_BRADLEY", "US_MOT_INF_PLT_HMMWV")
SEEDS = (7, 19, 41)
LAYOUTS = ("line", "column", "staggered")


def matchups():
    # Every template is mirrored; every ordered branch pair is also covered.
    return [(name, name) for name in TOE] + [
        (b, r) for b in REPRESENTATIVES for r in REPRESENTATIVES if b != r
    ]


def cases(many=False):
    out = []
    for blue, red in matchups():
        for layout in LAYOUTS if many else ("line",):
            ratios = [(3, 3)] if many else [(1, 1)]
            if many and blue == red and layout == "line":
                ratios += [(4, 2), (2, 4)]
            for nb, nr in ratios:
                out.append(dict(blue=blue, red=red, nb=nb, nr=nr, layout=layout,
                                id=f"{blue}__{red}__{nb}v{nr}__{layout}"))
    return out


def deployment(case, seed):
    units = []
    for side, name, n in (("BLUE", case["blue"], case["nb"]), ("RED", case["red"], case["nr"])):
        sign = -1 if side == "BLUE" else 1
        for i in range(n):
            # 150 m front gap, 70 m spacing, mirrored around x=2000.
            depth = i * 70 if case["layout"] == "column" else (i % 2) * 70 if case["layout"] == "staggered" else 0
            lateral = 0 if case["layout"] == "column" else (i - (n - 1) / 2) * 70
            units.append(dict(id=f"{side}-{i+1}", name=f"{name} {side} {i+1}", side=side,
                              type=name, echelon=TOE[name]["metadata"]["echelon"],
                              pos=[2000 + sign * (75 + depth), 2000 + lateral],
                              heading_deg=0 if side == "BLUE" else 180,
                              watch_heading_deg=0 if side == "BLUE" else 180))
    return dict(seed=seed, world={"width_m": 4000, "height_m": 4000}, units=units,
                objectives={}, description="Open terrain, initially idle, native autonomous combat; no BML.")


def snapshot(sim, initial):
    row = {"t": round(sim.time, 6)}
    for side in Side:
        units = [u for u in sim.units.values() if u.side == side]
        row[side.value] = dict(
            personnel=sum(u.personnel for u in units), equipment=sum(u.equipment for u in units),
            strength=sum(u.current_strength for u in units),
            formation_equivalents=sum(u.current_strength for u in units) / initial[side.value],
            active_formations=sum(u.alive for u in units),
            weapon_systems=sum(u.firepower(e,w).participants for u in units if u.alive
                               for e, w in u.operational_weapons()),
        )
    return row


def invariant_diagnostics(rows, same_type):
    if not same_type:
        return {"status": "NOT_APPLICABLE", "reason": "Different types: equal effectiveness coefficients are unjustified."}
    b0, r0 = (rows[0][s]["formation_equivalents"] for s in ("BLUE", "RED"))
    # Normalize by INITIAL total, never by an invariant that can be zero in 1v1.
    errors = {}
    for power, label in ((1, "linear"), (2, "square")):
        values = [abs((r["BLUE"]["formation_equivalents"] ** power - r["RED"]["formation_equivalents"] ** power)
                      - (b0 ** power - r0 ** power)) / (b0 ** power + r0 ** power) for r in rows]
        errors[label] = dict(max_normalized_drift=max(values), final_normalized_drift=values[-1])
    return dict(status="DIAGNOSTIC_ONLY", **errors,
                reason="Native mixed weapons, finite ammo, damage states and perception violate constant-coefficient assumptions; equal 1v1 cannot distinguish laws.")


def run_native(case, seed, directory, duration=None, dt=0.25):
    directory.mkdir(parents=True, exist_ok=True)
    spec = deployment(case, seed)
    scenario_path = directory / f"{case['id']}__seed{seed}.scenario.json"
    scenario_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    sim = load_scenario(str(scenario_path))
    initial = {side.value: next(u.initial_strength for u in sim.units.values() if u.side == side) for side in Side}
    totals = {side.value: (sum(u.personnel for u in sim.units.values() if u.side == side),
                          sum(u.equipment for u in sim.units.values() if u.side == side)) for side in Side}
    duration = duration or (240.0 if "ARTILLERY" in (TOE[case['blue']]['branch'], TOE[case['red']]['branch']) else 120.0)
    rows = [snapshot(sim, initial)]
    violations = []
    # Fixed horizon: do not call a timeout or ammunition exhaustion a victory.
    for step in range(1, round(duration / dt) + 1):
        sim.tick(dt)
        for u in sim.units.values():
            for e in u.elements.values():
                if e.count < 0 or e.count > e.initial_count:
                    violations.append(f"invalid count {u.uid}:{e.eid} at {sim.time}")
        if step % max(1, round(5 / dt)) == 0:
            row = snapshot(sim, initial)
            for side in Side:
                value = row[side.value]
                if not 0 <= value['personnel'] <= totals[side.value][0] or not 0 <= value['equipment'] <= totals[side.value][1]:
                    violations.append(f"side inventory increased: {side.value} at {sim.time}")
            rows.append(row)
    if rows[-1]['t'] != round(sim.time, 6):
        rows.append(snapshot(sim, initial))
    fires = [r for r in sim.logs if r['kind'] == 'FIRE']
    for rec in fires:
        if sim.units[rec['shooter']].side == sim.units[rec['target']].side:
            violations.append("direct friendly fire")
    end = rows[-1]
    b, r = end['BLUE']['strength'], end['RED']['strength']
    outcome = 'MUTUAL_DESTRUCTION' if b == r == 0 else 'BLUE_ELIMINATED' if b == 0 else 'RED_ELIMINATED' if r == 0 else 'TIME_LIMIT'
    result = dict(case=case, seed=seed, dt=dt, duration_s=duration, outcome=outcome,
                  initial=rows[0], final=end, time_series=rows, violations=violations,
                  law_diagnostics=invariant_diagnostics(rows, case['blue'] == case['red']),
                  event_counts=dict(Counter(r['kind'] for r in sim.logs)),
                  direct_fire_by_side=dict(Counter(sim.units[r['shooter']].side.value for r in fires)),
                  pending_events=len(sim.events),
                  events=[r for r in sim.logs if r['kind'] in {'FIRE','ELEMENT_LOSS','EQUIPMENT_STATE_CHANGE','INDIRECT_FIRE','ARTY_IMPACT','DESTROYED'}])
    (directory / f"{case['id']}__seed{seed}.result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def source_hashes():
    files = sorted((ROOT / 'mnsim').glob('*.py')) + sorted((ROOT / 'config').glob('*.json')) + sorted((ROOT / 'database').glob('*'))
    files += sorted((ROOT / 'tests/upate').rglob('*.py')) + [ROOT / 'tests/conftest.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}


def write_summary(directory, results, controls):
    directory.mkdir(parents=True, exist_ok=True)
    compact = [{k:v for k,v in row.items() if k not in ('events','time_series')} for row in results]
    groups = {}
    for result in results:
        groups.setdefault(result['case']['id'], []).append(result)
    statistics_rows = []
    for case_id, runs in groups.items():
        row = {'case_id':case_id, 'runs':len(runs), 'outcomes':dict(Counter(r['outcome'] for r in runs)),
               'no_fire_runs':sum(not r['event_counts'].get('FIRE',0) and not r['event_counts'].get('INDIRECT_FIRE',0) for r in runs)}
        for side in Side:
            values = [r['final'][side.value]['formation_equivalents'] for r in runs]
            row[side.value] = {'mean':statistics.mean(values), 'min':min(values), 'max':max(values),
                               'sample_sd':statistics.stdev(values) if len(values)>1 else 0.0}
        statistics_rows.append(row)
    report = dict(python=platform.python_version(), pythonhashseed=os.environ.get('PYTHONHASHSEED'),
                  seeds=list(SEEDS), source_sha256=source_hashes(),
                  controls=controls, run_count=len(results), case_count=len(groups),
                  no_fire_runs=sum(r['no_fire_runs'] for r in statistics_rows),
                  violations=sum(len(r['violations']) for r in results),
                  outcomes=dict(Counter(r['outcome'] for r in results)), statistics=statistics_rows, runs=compact)
    (directory / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    lines = ['# 교전 검증 결과', '',
             f"배치 {len(groups)}개, 시뮬레이션 {len(results)}회, 물리 상태 검사 위반 {report['violations']}건.", '',
             f"**사격 미발생: {report['no_fire_runs']}회. 해당 배치는 교전 성립 검사 실패이며 법칙 검증 성공으로 집계하지 않는다.**", '',
             '## 판정 범위', '',
             '- 엔진에는 란체스터 제1/제2법칙 전용 선택 모드나 미분방정식 적분기가 없다.',
             '- 아래 통제 실험은 실제 Simulation.tick 및 CombatResolver를 호출한다. 합성 무기/완전 관측 조건에서의 법칙 재현 여부이며, 모든 실전 편제가 법칙을 만족한다는 증거는 아니다.',
             '- 원래 편제 실험은 무기·탄약·센서·피해 모델을 유지한다. 보병 1대1은 편제 하나씩을 뜻하며, IND 편제는 개별 병사/차량이다.',
             '- 같은 편제에 한해 전력 환산값 B/R의 선형·제곱 불변량 오차를 기록한다. 서로 다른 병종은 동일 계수를 가정할 수 없어 비교 대상에서 제외한다.',
             '- TIME_LIMIT는 시간 종료이며 승패/무승부 판정이 아니다. 잔여 이벤트는 처리되지 않은 채 개수를 기록한다.',
             '- 3개 시드는 재현 및 변동 확인용이다. 통계적 유의성이나 실제 전투 예측 정확도를 주장하지 않는다.', '',
             '## 통제 실험', '', '```json', json.dumps(controls, ensure_ascii=False, indent=2), '```', '',
             '## 실제 편제 결과', '',
             '| 배치 | BLUE 잔존 편제 환산 평균 [최소, 최대] | RED 잔존 편제 환산 평균 [최소, 최대] | 종료 사유 | 사격 미발생 |',
             '|---|---:|---:|---|---:|']
    for row in statistics_rows:
        b,r=row['BLUE'],row['RED']
        lines.append(f"| {row['case_id']} | {b['mean']:.3f} [{b['min']:.3f}, {b['max']:.3f}] | {r['mean']:.3f} [{r['min']:.3f}, {r['max']:.3f}] | {row['outcomes']} | {row['no_fire_runs']} |")
    lines += ['', '개별 배치·시간별 전력·사격/피해 이벤트: `cases/*.scenario.json`, `cases/*.result.json`.',
              '전체 수치 및 소스 SHA-256: `results.json`.', '',
              '이론 기준: [Lanchester, Aircraft in Warfare (1916), Chapter V](https://en.wikisource.org/wiki/Aircraft_in_Warfare_%281916%29/Chapter_5).', '']
    (directory / 'summary.md').write_text('\n'.join(lines), encoding='utf-8')


def perfect_track(observer, target, now):
    observer.local_tracks[target.uid] = Track(
        track_id=f'{observer.uid}:{target.uid}', target_id=target.uid, estimated_pos=target.pos,
        position_error_m=0, classification=target.branch, confidence=1, last_seen_time=now,
        source='LOCAL', observation_zone='CLOSE', state='IDENTIFIED', belief_confidence=1,
        existence_confirmed=True, last_confirmed_time=now)


def controlled_sim(blue_count, red_count, law, blue_rate=1, red_rate=1, explicit_cycle=False):
    sim = Simulation(seed=7)
    sim._next_sensor_update = math.inf  # Known-target laboratory condition, documented above.
    for side, n, rate, x in ((Side.BLUE,blue_count,blue_rate,1000),(Side.RED,red_count,red_rate,1100)):
        md = dict(acquisition_delay_min_s=0,
                  acquisition_delay_max_s=0, base_hit_probability=1, max_hit_probability=1,
                  range_hit_falloff=0, defending_hit_factor=1)
        if law == 'linear':
            md['max_engaged_operators'] = 1
        if explicit_cycle:
            md.update(engagement_cycle_min_s=1/rate, engagement_cycle_max_s=1/rate)
        weapon=WeaponModel('control', 'ANTI_PERSONNEL', 1000, 60*rate, 1, metadata=md)
        el=FormationElement('fighters','fighters','PERSONNEL','RIFLE',n,n,weapons=[weapon])
        typ=UnitType('CONTROL','INFANTRY',0,1000,elements=[el])
        sim.add_unit(Unit(side.value,side.value,side,'PLT',typ,(x,1000),elements={el.eid:el}))
    return sim


def run_control(law, blue=40, red=20, duration=40, dt=0.05, blue_rate=0.01, red_rate=0.01):
    sim=controlled_sim(blue,red,law,blue_rate,red_rate)
    b,r=sim.units['BLUE'],sim.units['RED']
    max_drift=0.0
    initial=blue_rate*blue**(2 if law=='square' else 1)-red_rate*red**(2 if law=='square' else 1)
    scale=blue_rate*blue**(2 if law=='square' else 1)+red_rate*red**(2 if law=='square' else 1)
    for _ in range(round(duration/dt)):
        perfect_track(b,r,sim.time); perfect_track(r,b,sim.time)
        sim.tick(dt)
        power=2 if law=='square' else 1
        max_drift=max(max_drift,abs(blue_rate*b.personnel**power-red_rate*r.personnel**power-initial)/scale)
    if law=='square':
        k=math.sqrt(blue_rate*red_rate)
        expected_b=blue*math.cosh(k*duration)-red*math.sqrt(red_rate/blue_rate)*math.sinh(k*duration)
        expected_r=red*math.cosh(k*duration)-blue*math.sqrt(blue_rate/red_rate)*math.sinh(k*duration)
    else:
        expected_b=blue-red_rate*duration
        expected_r=red-blue_rate*duration
    return dict(law=law, initial=[blue,red], duration=duration, dt=dt,
                rates=[blue_rate,red_rate], expected=[expected_b,expected_r],
                observed=[b.personnel,r.personnel], max_normalized_invariant_drift=max_drift,
                fire_events=sum(e['kind']=='FIRE' for e in sim.logs))


def rate_probe(systems, explicit_cycle):
    sim=controlled_sim(systems,10000,'square',1/6,0,explicit_cycle=False)
    shooter,target=sim.units['BLUE'],sim.units['RED']
    target.elements['fighters'].weapons=[]
    weapon=shooter.elements['fighters'].weapons[0]
    shooter.metadata['target_acquired_t']=0.0
    if explicit_cycle:
        weapon.metadata.update(engagement_cycle_min_s=6,engagement_cycle_max_s=6)
    perfect_track(shooter,target,0)
    # Resolver-level cadence observation, event losses still use the engine.
    for i in range(1201):
        sim.time=i*0.05
        perfect_track(shooter,target,sim.time)
        sim.combat.fire_weapon(shooter,target,shooter.elements['fighters'],weapon)
        for ev in sim.events.pop_due(sim.time):
            sim._handle_event(ev.kind,ev.payload)
    return sum(r['kind']=='FIRE' for r in sim.logs)


def run_compact(case, seed, directory):
    """Full events/timeline stay on disk; return only fields needed by the tests."""
    result=run_native(case,seed,directory)
    result.pop('events',None)
    result['time_series']=result['time_series'][-1:]
    return result
