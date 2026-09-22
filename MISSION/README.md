# MISSION — 공격·방어 작전 수행 가능성

먼저 [MISSION_FEASIBILITY.md](MISSION_FEASIBILITY.md)를 읽는다. **공격·이동간 접적·지역 방어는 가능**, **지연 작전은 명시적 단계 계획으로 조건부 가능**, **매복·정찰·기동 방어·철수는 일부 요구만 가능**으로 판정했다.

이 폴더는 작전별 시나리오, 양측 BML, 지형, 검증기 및 수행 가능성 문서로 구성된다. 전투 결과 보고서는 포함하지 않는다.

## 파일 구성

- `MISSION_FEASIBILITY.md`: 작전별 판정, BLUE 계획, RED 역할, 미지원 원인과 해결 방향, 소스 근거.
- `MISSION_MANIFEST.json`: 8개 케이스의 입력 파일 연결과 검증 시간 설정.
- `VALIDATION_STATUS.json`: 수행 기능의 검증 여부와 입력·엔진 해시. 전투 승패·손실 통계 없음.
- `validate_missions.py`: 정상 엔진을 사용한 headless 기능 확인.
- `run_case.py`: 시나리오와 BLUE/RED BML을 명시적으로 선택해 기존 GUI 실행.
- 각 작전 폴더: `<작전>_SCENARIO.json`, `<작전>_BLUE_BML.json`, `<작전>_RED_BML.json`, `<작전>_TERRAIN.json`.

## 실행

PowerShell에서 프로젝트 안쪽 디렉터리로 이동한다.

```powershell
Set-Location 'C:\Users\user\Desktop\mns_v49_7_work\mns_v49_7_work'
```

전체 수행 기능을 다시 검사한다. 이 명령은 `MISSION/VALIDATION_STATUS.json`을 갱신한다.

```powershell
python -B MISSION/validate_missions.py
```

한 케이스만 검사한다. 단일 검사에서는 전체 판정 파일을 덮어쓰지 않고 콘솔에만 검증 여부를 출력한다.

```powershell
python -B MISSION/validate_missions.py --case DELAYING_OPERATION
```

GUI로 확인하려는 작전 하나를 선택한다. 아래 명령은 **각각 별도로** 실행한다.

```powershell
python -B MISSION/run_case.py ATTACK
python -B MISSION/run_case.py MOVEMENT_TO_CONTACT
python -B MISSION/run_case.py AMBUSH
python -B MISSION/run_case.py RECONNAISSANCE
python -B MISSION/run_case.py AREA_DEFENSE
python -B MISSION/run_case.py MOBILE_DEFENSE
python -B MISSION/run_case.py DELAYING_OPERATION
python -B MISSION/run_case.py WITHDRAWAL
```

GUI에는 프로젝트의 기존 pygame 실행 환경이 필요하다. 이번 조사는 headless 엔진 실행으로 검증했으며 GUI 화면 조작을 통한 별도 검증은 하지 않았다. 실행기는 작업 디렉터리를 MISSION으로 두므로 사용자가 GUI의 L 키로 리플레이를 저장할 경우에도 이 폴더 아래 `logs`가 사용된다. 본 조사에서는 리플레이를 생성하지 않았다.

기존 `main.py`를 직접 실행하려면 양측 BML 경로를 모두 지정한다.

```powershell
python -B main.py MISSION/ATTACK/ATTACK_SCENARIO.json --blue-bml MISSION/ATTACK/ATTACK_BLUE_BML.json --red-bml MISSION/ATTACK/ATTACK_RED_BML.json
```

시나리오만 `main.py`에 넘기면 현재 UI는 BML 선택 창을 연다. 시나리오의 `bml_files`는 `load_scenario(path)`의 기본 로드용이며, UI의 명시적 실행 선택과는 구분된다. `run_case.py`는 이 혼동을 방지한다. BML을 직접 편집한 뒤 검증기를 다시 실행하면 변경된 파일을 사용한다.

## 검증 상태 해석

- `PASS`: 해당 케이스에 선언한 지원 기능과 알려진 제약의 검사가 통과했다는 뜻이다.
- `REVIEW_REQUIRED`: 실행은 끝났지만 필요한 기능 확인 중 일부가 충족되지 않았다. BML·입력·엔진 변경을 검토해야 한다.
- `ERROR`: 로드, 컴파일 또는 실행 중 예외가 발생했다.
- `capability`: 실제 지원 수준. `PASS`여도 `부분 가능`인 작전은 완전 지원이 아니다.
- `limitation_hold_allows_early_fire=true`: 매복 개시 통제의 **미지원 제약이 재현되었음**을 뜻한다.

기능 검사는 전투 성공률을 계산하지 않는다. 검증 코드 안의 `duration_s`와 시간 기준은 입력 설정이며, 전투 수행 결과를 보고하는 수치가 아니다.

## 추가로 확인한 기존 엔진 계약

손실 조건 분기와 부대별 단계 큐 의미를 확인한 기존 테스트:

```powershell
python -B -m pytest tests/test_phase_doctrine_bml.py::test_loss_condition_can_redirect_to_rally_point tests/test_external_bml_missions.py::test_phases_advance_per_unit_without_an_implicit_team_barrier -q -p no:cacheprovider --basetemp=MISSION/_validation_tmp
```

이 두 테스트는 통과했다. `_validation_tmp`는 테스트 도구가 사용하는 임시 입력 디렉터리이며 작전 산출물이 아니다.

## TDG 기반 방어 케이스

Warfighters 공개 사례를 변환한 추가 맵·시나리오·양측 BML은 [TDG_DEFENSE 패키지](TDG_DEFENSE/README.md)에 있다. 원문의 근거와 시뮬레이터용 변경 사항, BLUE 기본안·대안을 별도로 제공한다.
