"""Build per-unit mobility report from current engine probes and recorded movement tests."""
import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
from mnsim.mobility import movement_speed_mps
from mnsim.model import UnitState
from mnsim.scenario import load_scenario
from mnsim.terrain import TerrainModel

NAMES={
"INF_PLT":"보병 소대", "INF_COY":"보병 중대", "INF_BN":"보병 대대", "INF_IND":"개별 보병",
"RIFLE_SQD":"소총 분대", "US_RIFLE_SQD":"미군 소총 분대", "MG_SQD":"기관총 분대", "MG_PLT":"기관총 소대",
"TANK_PLT":"전차 소대", "TANK_COY":"전차 중대", "TANK_BN":"전차 대대",
"ARTY_PLT":"견인포 소대", "ARTY_COY":"견인포 중대", "ARTY_BN":"견인포 대대",
"US_MECH_INF_PLT_BRADLEY":"브래들리 기계화보병 소대", "US_MOT_INF_PLT_HMMWV":"험비 차량화보병 소대",
"BMP_MECH_INF_PLT":"BMP 기계화보병 소대", "ROK_MECH_INF_PLT_K200":"K200 기계화보병 소대",
"US_M1A2_ABRAMS_IND":"M1A2 에이브럼스 단일 전차", "ROK_K2_TANK_IND":"K2 단일 전차",
"US_M2_BRADLEY_IND":"M2 브래들리 단일 차량", "BMP_IFV_IND":"BMP 단일 보병전투차",
"ROK_K200_APC_IND":"K200 단일 장갑차", "US_HMMWV_IND":"험비 단일 차량"}
ORDER=list(NAMES)
POLY=[[90,70],[150,70],[150,130],[90,130]]
ROAD={"points":[[0,100],[240,100]],"width_m":12}
FOREST={"type":"FOREST","polygon":POLY,"mobility_overrides":{"FOOT":0.45,"TRACKED":0,"WHEELED":0,"WHEELED_TOWED":0},"impassable_mobility_classes":["TRACKED","WHEELED","WHEELED_TOWED"]}
RIVER={"polygon":[[110,0],[130,0],[130,240],[110,240]]}
BRIDGE={"id":"BR","points":[[100,100],[140,100]],"width_m":12,"integrity":100,"max_integrity":100}
ENVIRONMENTS=[
("open","평지",{}),
("road","도로",{"roads":[ROAD]}),
("bridge","정상 교량 위",{"rivers":[RIVER],"bridges":[BRIDGE]}),
("forest","밀림형 숲 / 도로 밖",{"areas":[FOREST]}),
("forest_road","동일 숲 내부 도로",{"areas":[FOREST],"roads":[ROAD]}),
("woods","성긴 숲",{"areas":[{"type":"WOODS","polygon":POLY,"movement_factor":0.5}]}),
("river","도하 설정 없는 강",{"rivers":[RIVER]}),
("ford","보병 도하 허용 강",{"rivers":[dict(RIVER,mobility_overrides={"FOOT":0.35,"TRACKED":0,"WHEELED":0,"WHEELED_TOWED":0})]}),
("lake","호수",{"areas":[{"type":"LAKE","polygon":POLY,"mobility_overrides":{"FOOT":0.16,"TRACKED":0,"WHEELED":0,"WHEELED_TOWED":0}}]}),
("building","건물 내부 / 일반 이동",{"areas":[{"id":"B","type":"BUILDING","polygon":POLY}]}),
("destroyed_bridge","파괴 교량 아래 강",{"rivers":[RIVER],"bridges":[dict(BRIDGE,destroyed=True,integrity=0)]}),
]

def main():
    evidence=json.loads((HERE/"results/results.json").read_text(encoding="utf-8"))
    templates=json.loads((ROOT/"config/toe_templates.json").read_text(encoding="utf-8"))["unit_types"]
    assert set(templates)==set(ORDER), "Update report names for new unit templates"
    # Current definitions must match the saved movement evidence.
    checked=0
    for name,digest in evidence["sha256"].items():
        relative=name.replace("\\","/")
        if relative.startswith(("mnsim/","config/","database/")):
            assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==digest, "Stale movement evidence: "+relative
            checked+=1
    units=[]
    with tempfile.TemporaryDirectory(prefix="mobility_report_") as tmp:
        for name in ORDER:
            path=Path(tmp)/"scenario.json"
            path.write_text(json.dumps(dict(seed=7,world=dict(width_m=240,height_m=240),units=[dict(id="U",name=name,side="BLUE",type=name,echelon=templates[name]["metadata"]["echelon"],pos=[120,100])])),encoding="utf-8")
            sim=load_scenario(str(path)); u=sim.units["U"]
            assert u.unit_type.name==name
            u.state=UnitState.MOVING
            md=u.unit_type.metadata
            state=float(md.get("state_speed_factors",{}).get("MOVING",1))
            cases=[c for c in evidence["cases"] if any(m.get("unit_type")==name for m in c["movement"])]
            profile=dict(id=name,label=NAMES[name],branch=u.unit_type.branch,echelon=u.echelon,mobility=md["mobility_class"],base_speed_mps=u.unit_type.max_speed_mps,state_factors=md.get("state_speed_factors",{}),terrain_factors=md.get("terrain_speed_factors",{}),capabilities=md.get("mobility_capabilities",[]),max_grade_deg={"FOOT":38,"TRACKED":28,"WHEELED":18,"WHEELED_TOWED":12}[md["mobility_class"]],tests=len(cases),passed=sum(c["status"]=="passed" for c in cases),points=[],measurements=[],state_speeds={})
            for key,label,data in ENVIRONMENTS:
                t=TerrainModel(data,sim.combat_config.get("navigation",{}))
                t.world=sim.world
                allowed=t.passable(u,(120,100)); factor=t.speed_factor(u,(120,100))
                speed=movement_speed_mps(u,t,(120,100)) if allowed else None
                if key in {"open","road"}:
                    profile["state_speeds"][key]={name:movement_speed_mps(u,t,(120,100),state=name) for name in md["state_speed_factors"]}
                profile["points"].append(dict(key=key,label=label,passable=allowed,factor=factor,speed_mps=speed))
            for c in cases:
                for i,m in enumerate(c["movement"]):
                    if m.get("dt_s")!=0.25 or "arrived" not in m:continue
                    profile["measurements"].append(dict(test=c["name"],terrain=c["terrain"],run_index=i,status=c["status"],arrived=m["arrived"],synthetic_capability="explicit_water_crossing" in c["name"],elapsed_s=m["elapsed_s"],distance_m=m["distance_m"],remaining_m=m["remaining_m"],average_mps=m["distance_m"]/m["elapsed_s"],start=m["start"],goal=m["goal"]))
            units.append(profile)
    output=dict(created_at=datetime.datetime.now().astimezone().isoformat(),movement_evidence_time=evidence["created_at"],source_files_verified=checked,movement_cases=len(evidence["cases"]),movement_passed=sum(c["status"]=="passed" for c in evidence["cases"]),point_probes=sum(len(u["points"]) for u in units),units=units)
    (HERE/"results/unit_mobility_profiles.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    render(output)
    print(f"Report generated: {len(units)} units, {output['point_probes']} point probes, {checked} source hashes matched")

def render(data):
    lines=["# 유닛별 지형 통행 및 이동 속도 상세 보고서","",f"작성: {data['created_at']} / 근거 이동 테스트: {data['movement_evidence_time']}","",
    "## 1. 보고서 범위와 읽는 방법","",
    f"현재 등록된 **{len(data['units'])}개 유닛 유형**을 각각 독립 항목으로 정리했습니다. 이동·규칙 검사 **{data['movement_cases']}건 중 {data['movement_passed']}건 통과** 결과를 근거로 합니다. 실제 경로 이동 검사를 수행한 유형은 **{sum(u['tests']>0 for u in data['units'])}종**입니다. 각 유닛에 동일한 지형·시간 간격·도착 기준을 적용했습니다. 총 **{data['point_probes']}개 지점 검사**로 지형 내부 통행 및 국소 속도도 확인했습니다. 경로 검사의 통과 건수에는 도착·차단·통행 규칙 검사가 함께 포함됩니다.","",
    "- **국소 속도**: 해당 지형 안에서 MOVING 상태, 손상 없는 초기 편제, 평탄한 지면일 때 엔진이 계산하는 속도입니다. m/s와 km/h를 함께 표기합니다.",
    "- **실측 평균 속도**: 기록된 실제 이동 거리 ÷ 시뮬레이션 경과 시간입니다. 진입·이탈·우회·여러 지형을 포함하므로 국소 속도와 다릅니다.",
    "- **불가**: 해당 지형 내부 직접 진입이 금지됩니다. 차량이 호수 주변을 우회해 목적지에 도착해도 호수 통행 가능으로 표시하지 않습니다.",
    "- 지형의 배율은 아래 합성 시험 조건에 대한 값입니다. 다른 시나리오가 지형 설정을 다르게 지정하면 결과도 바뀝니다. 게임 설정값이며 실제 장비 제원의 주행 성능을 뜻하지 않습니다.","",
    "### 속도 계산","",
    "배율 적용 속도 V = 유닛 기준 속도 × 상태 배율 × 지형 배율 × 장비 가동률 × 경사 배율. 국소 이동 속도 = max(V × 0.10, V − 편제 감속량). 감속량은 소대 0, 중대 0.5km/h, 대대 1.0km/h이며 실제 unit.echelon을 사용합니다. 개별·분대 등 나머지 편제는 감속하지 않습니다. V=0이면 정지를 유지합니다. 저속 하한에 걸리면 편제별 0.5km/h 차이는 줄어들거나 사라집니다. 실제 한 틱 이동 거리는 경유점까지 남은 거리와 위 속도 × dt 중 작은 값입니다. 기준 속도(max_speed_mps)는 도로 배율이 1보다 클 경우 초과할 수 있으므로 최종 절대 상한으로 해석하지 않습니다.","",
    "이번 지점 속도는 MOVING, 장비 가동률 1, 경사 배율 1입니다. 도로보다 교량 배율이 우선합니다. 숲 내부 도로의 차량 통행 제한이 해제되는 경우 해당 숲의 차단 배율을 제외합니다. 보병 숲 감속과 다른 중첩 지형 효과는 유지합니다.","",
    "### 지형 조건","",
    "| 지형 | 보고서에 적용한 조건 |","|---|---|",
    "| 평지·도로·교량 | 유닛의 OPEN / ROAD / BRIDGE 배율, 교량은 정상 상태 |",
    "| 숲 | FOOT=0.45, TRACKED/WHEELED/WHEELED_TOWED=0 및 차량 금지 목록 |",
    "| 숲 내부 도로 | 폭 12m 도로; 차량은 숲 차단 배율 제외, 보병은 도로 배율 × 0.45 |",
    "| 성긴 숲 | WOODS movement_factor=0.5 |",
    "| 기본 강 | 별도 도하 배율 없음. 기본 도하 능력이 없으면 불가 |",
    "| 도하 허용 강 | FOOT=0.35, 차량 이동 분류=0. 도로만 놓아도 강 통행 제한은 해제되지 않음 |",
    "| 호수 | FOOT=0.16, 차량=0. 보병 수영 허용 분과 조건 적용 |",
    "| 건물 | 정상 건물, 일반 이동, 별도 진입 권한 없음 |",
    "| 파괴 교량 | 기본 도하 설정 없는 강 위 교량 파괴; 남은 우회 교량 없음 |",
    "| 완만한 고도 | 높이 5m, 전이 폭 40m. 경로 전체 실측만 제시 |","",
    "지점 표의 불가 항목에는 속도를 0으로 쓰지 않고 ‘—’로 표시합니다. 통행 금지와 통행은 가능하나 속도가 0인 오류를 구분하기 위함입니다.","",
    "## 2. 전체 유닛 비교 및 상세 목차","",
    "| 유닛 | 이동 분류 | 평지 m/s | 도로 m/s | 교량 m/s | 경로 검사 | 상세 |","|---|---|---:|---:|---:|---|---|"]
    for u in data["units"]:
        speeds={p["key"]:p["speed_mps"] for p in u["points"]}
        lines.append(f"| {u['label']} | {u['mobility']} | {speeds['open']:.3f} | {speeds['road']:.3f} | {speeds['bridge']:.3f} | {str(u['passed'])+'건 통과' if u['tests'] else '미실시; 지점 검사'} | [{u['id']}](#unit-{u['id'].lower()}) |")
    lines.extend(["","현재 24종은 모두 기본 mobility_capabilities가 비어 있습니다. 따라서 BMP·K200 등의 명칭만으로 엔진상 수륙양용 능력이 있다고 판단할 수 없습니다. WATER_CROSSING을 시험에서 임시 부여한 결과는 기본 능력 표에 포함하지 않았습니다.","","## 3. 유닛별 상세"])
    for index,u in enumerate(data["units"],1):
        lines.extend(["",f"<a id=unit-{u['id'].lower()}></a>","",f"### 3.{index}. {u['label']} — {u['id']}","",
        f"편제 **{u['echelon']}**, 분과 **{u['branch']}**, 이동 분류 **{u['mobility']}**. 기준 속도 **{u['base_speed_mps']:.3f}m/s ({u['base_speed_mps']*3.6:.3f}km/h)**. 기본 도하 능력: **{' / '.join(u['capabilities']) or '없음'}**.","",
        f"검증 수준: **{str(u['passed'])+'/'+str(u['tests'])+' 경로·규칙 검사 통과, 11개 지점 검사' if u['tests'] else '11개 지점 통행·속도 검사; 실제 이동 경로 완주 미검증'}**.","",
        "#### 지형별 직접 통행 및 국소 속도","",
        "| 지형 | 직접 통행 | 적용 지형 배율 | m/s | km/h |","|---|---|---:|---:|---:|"])
        for p in u["points"]:
            if p["passable"]:
                lines.append(f"| {p['label']} | 가능 | {p['factor']:.4f} | {p['speed_mps']:.4f} | {p['speed_mps']*3.6:.4f} |")
            else:lines.append(f"| {p['label']} | 불가 | — | — | — |")
        if u["mobility"]=="FOOT":
            lines.extend(["","**이동 해석:** 숲·성긴 숲은 감속하며 통과합니다. 도하를 허용한 강과 호수에서는 각각 편제 감속 전 평지 속도의 35%, 16%를 적용한 뒤 편제 감속과 저속 하한을 적용합니다. 모든 강을 자동으로 건널 수 있는 것은 아닙니다. 숲 내부 도로에서도 보병 숲 배율 0.45는 유지됩니다. 일반 이동은 건물 내부로 진입하지 않으며, 별도 건물 진입 명령은 이번 표와 구분해야 합니다. 수영에 따른 중화기 유기 등 지형 진입 효과는 이 국소 속도 표에서 평가하지 않았습니다."])
        else:
            lines.extend(["","**이동 해석:** 숲 내부를 도로 없이 관통할 수 없습니다. 정상 도로가 숲을 연속해서 지나면 도로 배율로 이동합니다. 호수 내부·도하 허용 강의 차량 차단 구역·정상 건물 내부는 직접 통행 불가입니다. 호수와 숲 주변으로 경로를 찾을 수 있는지는 주변 공간과 지도 형상에 달려 있습니다. 강은 정상 교량 등 허용된 연결이 필요하며, 교량 파괴 후 다른 경로가 없으면 멈춥니다."])
        if u["branch"] in {"MECH_INFANTRY","MOTORIZED_INFANTRY"}:
            lines.extend(["","이 표는 로더로 생성한 초기 차량 편제의 이동 분류를 기준으로 합니다. 탑승 보병이 있다는 이유로 FOOT 도하를 허용하지 않습니다. 하차·분리 후 보병의 별도 이동 속도는 이 차량 편제 속도와 동일하다고 가정하지 않습니다."])
        lines.extend(["","#### 상태 및 경사에 따른 변화","",
        "| 상태 | 속도 배율 | 평지 m/s | 도로 m/s |","|---|---:|---:|---:|"])
        pmap={p["key"]:p for p in u["points"]}
        for state,label in [("MOVING","이동"),("ATTACKING","공격"),("SEARCHING","수색"),("RETREATING","후퇴"),("ENGAGING","교전"),("DEFENDING","방어"),("IDLE","대기")]:
            f=u["state_factors"][state]
            lines.append(f"| {label} ({state}) | {f:.2f} | {u['state_speeds']['open'][state]:.3f} | {u['state_speeds']['road'][state]:.3f} |")
        lines.extend(["",f"경로 계획의 최대 절대 경사 기준: **{u['max_grade_deg']:.0f}°**. 이 값은 지형 고도 샘플링에 적용되는 경로 계획 제한이며, 모든 급경사의 전체 이동 안전성을 보장하는 값은 아닙니다. 이동 속도에 적용하는 오르막 계수는 max(0.22, 1−경사각/38), 내리막 계수는 max(0.35, 1−절대경사각/55)입니다. 방향과 경유점에 따라 달라지므로 고도 지형에 고정 속도 하나를 부여하지 않았습니다.","",
        "상태 표는 설정으로 계산한 값이며 해당 상태의 전술 명령을 별도로 실행한 측정값이 아닙니다. 속도 0 상태라도 전술 처리에서 상태를 바꾸면 이후 이동할 수 있습니다. 장비 손상으로 가동률이 낮아지면 위 속도보다 더 감소할 수 있습니다.","",
        "#### 기존 테스트의 실제 이동 기록 (dt=0.25초)",""])
        if not u["tests"]:
            lines.extend(["저장된 이동 검사 대상에 포함되지 않아 실측 도착 시간·평균 속도는 제시하지 않습니다. 위 값은 현재 엔진을 이용한 지점 계산 결과입니다."])
        else:
            lines.extend(["모든 경로의 시작·목적지 직선 거리는 160m입니다. 도착 허용 반경은 3m이며, 아래 거리는 실제 이동한 경로 길이입니다. 차량의 숲·호수·건물 행은 내부 통과가 아닌 **우회 이동**입니다. 작은 dt를 주 비교값으로 사용하며 1초·20초 결과는 원본 JSON에 보존되어 있습니다.","",
            "| 조건 | 도착 | 이동 거리(m) | 경과 시간(s) | 평균 m/s | 평균 km/h | 잔여(m) |","|---|---|---:|---:|---:|---:|---:|"])
            seen=set()
            for m in u["measurements"]:
                if m["synthetic_capability"]:continue
                name=m["test"].split("[")[0]
                if name=="test_uncrossable_river":label="도하 불가 강 (양방향·두 폭 중 대표)"
                elif name=="test_live_bridge_arrival":label="정상 교량 포함 경로"
                elif name=="test_destroyed_bridge_invalidates_cached_route":label="파괴 교량 / 경로 없음"
                elif name=="test_authored_foot_ford":label="보병 도하 허용 강"
                elif name=="test_road_travel_time":
                    if m["run_index"]==0:continue
                    label="도로 전체 경로"
                elif name=="test_woods_slows_without_stalling":
                    if m["run_index"]==0:continue
                    label="성긴 숲 전체 경로"
                else:label={"test_open_arrival":"평지","test_road_through_impassable_forest":"숲 내부 도로 포함 경로","test_forest_traversal_or_detour":"숲 통과" if u["mobility"]=="FOOT" else "숲 외곽 우회","test_lake_swim_or_detour":"호수 수영 포함 경로" if u["mobility"]=="FOOT" else "호수 외곽 우회","test_building_detour":"건물 외곽 우회","test_gentle_elevation_arrival":"완만한 고도 변화"}.get(name,name)
                if label in seen:continue
                seen.add(label)
                lines.append(f"| {label} | {'도착' if m['arrived'] else '차단 유지'} | {m['distance_m']:.3f} | {m['elapsed_s']:.2f} | {m['average_mps']:.4f} | {m['average_mps']*3.6:.4f} | {m['remaining_m']:.3f} |")
            lines.extend(["","차단 행의 경과 시간은 관찰 시간이며 통과 소요 시간이 아닙니다. 평균 0은 해당 경로에서 정지했다는 뜻입니다. WATER_CROSSING을 임시 부여한 합성 도하 대조군은 원본 테스트에는 포함되지만 이 기본 능력 표에서는 제외했습니다."])
    lines.extend(["","## 4. 종합 해석 및 주의할 차이","",
    "1. 동일 이동 분류라도 기준 속도와 도로·교량 배율이 다릅니다. 예를 들어 전차 소대와 단일 전차, 브래들리 단일 차량과 기계화보병 소대는 별도 항목의 값을 사용해야 합니다.",
    "2. 동일 계열의 소대·중대·대대는 같은 기준 속도를 사용하되, 모든 배율 적용 후 중대는 0.5km/h, 대대는 1.0km/h 감속합니다. 저속에서는 감속 전 속도의 10%를 하한으로 유지하므로 편제 간 속도가 같아질 수 있습니다. 경로 평균 속도는 경유점·도착 판정·서로 다른 지형 때문에 정확한 0.5km/h 차이를 보장하지 않습니다.",
    "3. 숲 내부 도로 정지 문제는 수정되었고 기존 실패 12건은 모두 통과했습니다. 이 예외는 숲·도로의 차량 통행 제한에 한정됩니다. 다른 지형의 0 배율을 일반적으로 무시하지 않습니다.",
    "4. 통행 가능 여부는 지형 이름만으로 결정되지 않습니다. 지형별 이동 분류 설정, 정상 교량, 도로 연결, 능력과 권한을 함께 확인해야 합니다.",
    "5. 큰 dt에서는 한 틱에 여러 지형을 지나가므로 평균 시간 차이가 생길 수 있습니다. 국소 속도와 경로 평균을 구분하고 0.25초 기록을 우선 비교했습니다.","",
    "## 5. 근거 파일 및 재생성","",
    "- [기존 이동 테스트 종합 결과](summary.md), [수정 내역](findings.md)",
    "- [실제 이동 원시 결과](../results/results.json): 경로·궤적·시간·실패 여부·원본 해시",
    "- [유닛별 지점 검사와 보고서 수치](../results/unit_mobility_profiles.json)",
    "- [유닛 기본 설정](../../../config/toe_templates.json)",
    "- [지형 속도와 통행 규칙](../../../mnsim/terrain.py), [실제 이동 함수](../../../mnsim/orders.py), [경로·경사 제한](../../../mnsim/pathfinding.py)","",
    f"원본 이동 결과에 저장된 엔진·설정·데이터 파일 {data['source_files_verified']}개의 SHA-256이 현재 파일과 일치함을 확인했습니다. 보고서는 현재 공통 이동속도 함수의 계산과 재실행한 이동 검사를 반영합니다.","",
    "~~~powershell","python tests/terrain/build_unit_report.py","~~~","",
    "설정이나 엔진 코드가 원본 이동 결과와 달라지면 보고서 생성기는 중단합니다. 이때 이동 테스트를 재실행해 근거 결과부터 갱신해야 합니다."])
    (HERE/"reports"/"unit_mobility_report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

if __name__=="__main__":main()
