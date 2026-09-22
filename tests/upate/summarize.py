"""Audit complete current-version runs and summarize BLUE/RED survivors."""
import hashlib
import json
import statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
BASE=Path(__file__).resolve().parent


def main():
    totals=[]
    for group,expected in (('one_vs_one',132),('many_vs_many',828)):
        folder=BASE/group/'results'
        report=json.loads((folder/'results.json').read_text(encoding='utf-8'))
        assert report['run_count']==expected
        assert report['pythonhashseed']=='0'
        for name,digest in report['source_sha256'].items():
            assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest, name
        groups={};hashes={};seen=set();zero_participant=0;no_fire=0;violations=0
        for path in sorted((folder/'cases').glob('*.result.json')):
            raw=path.read_bytes();row=json.loads(raw)
            key=(row['case']['id'],row['seed']);assert key not in seen;seen.add(key)
            assert row['seed'] in (7,19,41)
            assert row['time_series'][-1]['t']==row['duration_s']
            assert row['initial']['BLUE']['active_formations']==row['case']['nb']
            assert row['initial']['RED']['active_formations']==row['case']['nr']
            violations+=len(row['violations'])
            no_fire+=not(row['event_counts'].get('FIRE',0)+row['event_counts'].get('INDIRECT_FIRE',0))
            zero_participant+=sum(e['kind']=='FIRE' and e.get('firing_participants',0)<=0 for e in row['events'])
            groups.setdefault(key[0],[]).append(row)
            hashes[str(path.relative_to(folder))]=hashlib.sha256(raw).hexdigest()
            scenario=path.with_name(path.name.replace('.result.json','.scenario.json'))
            hashes[str(scenario.relative_to(folder))]=hashlib.sha256(scenario.read_bytes()).hexdigest()
        assert len(seen)==expected
        assert all({r['seed'] for r in rows}=={7,19,41} for rows in groups.values())
        assert no_fire==violations==zero_participant==0
        result=dict(group=group,runs=len(seen),cases=len(groups),no_fire_runs=no_fire,
            state_violations=violations,zero_participant_direct_fire=zero_participant,
            source_hashes_match=True,artifact_sha256=hashes)
        (folder/'audit.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        lines=['# BLUE / RED 생존자 결과','',
            '시드 7/19/41의 종료 시점 평균이다. 인원과 장비를 구분한다. 소수점은 여러 실행의 평균이며 부분 인원이 아니다.',
            '생존자 수나 평균 비율만으로 순수 란체스터 법칙 일치를 판정하지 않는다. 사격 발생은 양측 모두의 발사를 뜻하지 않는다.',
            '','| 배치 | BLUE 인원 초기 → 평균 생존 | RED 인원 초기 → 평균 생존 | BLUE 장비 초기 → 평균 잔존 | RED 장비 초기 → 평균 잔존 |',
            '|---|---:|---:|---:|---:|']
        for key,rows in groups.items():
            fields=[]
            for category in ('personnel','equipment'):
                for side in ('BLUE','RED'):
                    initial=rows[0]['initial'][side][category]
                    assert all(r['initial'][side][category]==initial for r in rows)
                    fields.append(f"{initial} → {statistics.mean(r['final'][side][category] for r in rows):.2f}")
            lines.append('| '+key+' | '+' | '.join(fields)+' |')
        (folder/'survivors.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        totals.append({k:v for k,v in result.items() if k!='artifact_sha256'})
        print(totals[-1],flush=True)
    (BASE/'verification.json').write_text(json.dumps(totals,indent=2),encoding='utf-8')


if __name__=='__main__':main()
