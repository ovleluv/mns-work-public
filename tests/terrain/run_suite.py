"""Run terrain files in order, retain failures and produce Korean reports.
Usage: python tests/terrain/run_suite.py
"""
import collections
import datetime
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
TERRAINS={"open":"평지","river":"강","ford":"도하","bridge":"다리","road":"도로","forest":"숲","lake":"호수","building":"건물","elevation":"고도·경사","woods":"성긴 숲"}

def main():
    results=HERE/"results"
    results.mkdir(exist_ok=True)
    all_cases=[]; summaries=[]; exits=[]
    for key,label in TERRAINS.items():
        xml=results/(key+".xml")
        if xml.exists(): xml.unlink()
        cmd=[sys.executable,"-m","pytest","-q","--tb=short",str(HERE/("test_"+key+".py")),"--junitxml="+str(xml)]
        proc=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,encoding="utf-8",errors="replace")
        (results/(key+".log")).write_text(proc.stdout+proc.stderr,encoding="utf-8")
        exits.append(proc.returncode)
        cases=[]
        if xml.exists():
            for case in ET.parse(xml).iter("testcase"):
                fail=case.find("failure"); error=case.find("error"); skip=case.find("skipped")
                status="error" if error is not None else "failed" if fail is not None else "skipped" if skip is not None else "passed"
                details=error if error is not None else fail
                props={p.attrib["name"]:p.attrib["value"] for p in case.findall("properties/property")}
                row=dict(terrain=key,name=case.attrib["name"],status=status,wall_time_s=float(case.attrib.get("time",0)),failure=details.text if details is not None else "",movement=json.loads(props.get("movement","[]")))
                cases.append(row)
        counts=collections.Counter(c["status"] for c in cases)
        summaries.append(dict(terrain=key,label=label,counts=dict(counts),exit_code=proc.returncode))
        all_cases.extend(cases)
        lines=[f"# {label} 이동 테스트 결과", "", f"통과 {counts['passed']} / 실패 {counts['failed']} / 오류 {counts['error']} / 건너뜀 {counts['skipped']}","",f"실행 종료 코드: {proc.returncode}. 원본: [로그](results/{key}.log), [JUnit](results/{key}.xml)","","| 테스트 | 결과 | 이동 결과 |","|---|---|---|"]
        for c in cases:
            metrics=[]
            for m in c["movement"]:
                if "arrived" in m: metrics.append(f"도착={m['arrived']}, 시간={m['elapsed_s']:.2f}s, 잔여={m['remaining_m']:.2f}m, 위치={m['final']}, 정지={m['stationary_ticks']}틱")
            lines.append(f"| {c['name']} | {c['status']} | {'; '.join(metrics)} |")
        for c in cases:
            if c["failure"]: lines.extend(["", "## "+c["name"],"", "~~~~text",c["failure"],"~~~~"])
        (HERE/(key+"_results.md")).write_text("\n".join(lines)+"\n",encoding="utf-8")
        print(label,dict(counts),flush=True)
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(list((ROOT/"mnsim").rglob("*.py"))+list((ROOT/"config").glob("*.json"))+list((ROOT/"database").rglob("*"))+list(HERE.glob("*.py"))) if p.is_file()}
    output=dict(created_at=datetime.datetime.now().astimezone().isoformat(),python=platform.python_version(),platform=platform.platform(),seed=7,dt_s=[0.25,1,20],summaries=summaries,cases=all_cases,sha256=hashes)
    (results/"results.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    total=collections.Counter(c["status"] for c in all_cases)
    lines=["# 지형 이동 테스트 종합 결과","",f"실행: {output['created_at']} / Python {output['python']} / 시드 7", "",f"총 {len(all_cases)}건: 통과 {total['passed']}, 실패 {total['failed']}, 오류 {total['error']}, 건너뜀 {total['skipped']}.","","| 지형 | 통과 | 실패 | 오류 | 상세 |","|---|---:|---:|---:|---|"]
    for s in summaries:
        c=s["counts"]
        lines.append(f"| {s['label']} | {c.get('passed',0)} | {c.get('failed',0)} | {c.get('error',0)} | [{s['terrain']}]({s['terrain']}_results.md) |")
    lines.extend(["","실패는 xfail 처리 없이 집계합니다. 숲·도로 수정 전 결과는 results/before_forest_road_fix/results.json에 보존했습니다. 실행 오류는 로그와 종료 코드를 확인하세요.","","원시 좌표 궤적, 계획 경로, 도착 시간, 잔여 거리, 입력·코드 SHA-256: [results.json](results/results.json).", "", "검증 범위와 재현 방법은 [README](README.md), 원인 분석은 [findings](findings.md)를 참고하세요."])
    (HERE/"summary.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return 1 if any(exits) else 0

if __name__=="__main__":
    raise SystemExit(main())
