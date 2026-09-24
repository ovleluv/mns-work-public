# 테스트 안내

## 목적

이 폴더에는 전투·이동 모델과 연관 기능을 검증하는 자동 테스트 및 실험 실행 코드가 있습니다.
코드를 수정한 뒤 기존 기능이 유지되는지 확인하고, 같은 조건으로 실험을 재현하는 데 사용합니다.
테스트 통과는 정의한 조건과 기대 동작을 만족한다는 뜻입니다. 실제 전투 예측 정확도나
모든 상황에서의 모델 타당성을 보장하는 것은 아닙니다.

공유본에서는 기존 실험 results 디렉터리와 임시 tmp 파일을 제외합니다.
테스트 코드, 공통 실행 코드, config·database·scenarios 입력 자료는 함께 유지해야 합니다.
기존 결과가 없어도 기본 검사는 실행할 수 있으며, 결과 보고서는 해당 실험을 다시 실행해 생성합니다.
이 안내를 작성하면서 공유 압축본 제작이나 파일 삭제를 수행하지는 않았습니다.

## 구성

| 구분 | 위치 / 대표 파일 | 검증 내용 |
|---|---|---|
| 기능·회귀 검사 | tests/test_*.py | 전투, 명령, 통신, 관측, 편제, 편집기 등 기존 동작 |
| 부대 상태 전환 | test_unit_state_transitions.py | 통합·해체, 차량 분리·파괴, 승무원 탈출, 탑승객 하차 과정의 소유권 보존 |
| 승무원·수송 | test_destroyed_vehicle_crew.py, test_real_detached_vehicle.py, test_mounted_transport.py | 생존 승무원 처리, 실제 분리 차량, 승하차 |
| 탈출 승무원 관측 | test_dismounted_crew_observation.py | 차량 센서 유지, 탈출한 보행 승무원만 일반 보병 INF_IND 관측 설정 적용 |
| 지형·제대별 이동 | terrain/, test_echelon_mobility.py | 통행·우회, 이동 시간, 소대·중대·대대 감속 |
| 교전 실험 | upate/one_vs_one/, upate/many_vs_many/ | 1대1·다대다 교전, 여러 시드와 배치, 통제 모델 비교 |
| TDG3 검증 | test_tdg3_*.py, tdg3_checks.py | 시나리오 구성·명령 실행·저장 로그 감사 |
| 공통 설정 | conftest.py | 공통 시나리오 준비, 선택 실행 옵션, 건너뛰기 설정 |

upate는 현재 저장소의 실제 디렉터리 이름입니다. 경로를 임의로 update로 바꾸지 않습니다.
1대1 실험은 기본적으로 편제 하나씩의 대결이며, 반드시 전투원 한 명씩의 대결을 뜻하지 않습니다.
일부 기존 파일은 독립 실행용 run() 형태입니다. pytest는 수집한 테스트만 실행하므로
파일 이름이 test_로 시작한다고 해서 그 파일의 모든 코드가 자동 실행되는 것은 아닙니다.

## 시작하기

Python 3.11 환경에서 검증했습니다. 아래 명령은 main.py, mnsim/, tests/가 있는 프로젝트 루트에서 실행합니다.

~~~powershell
python -m pip install -r requirements-dev.txt
~~~

Windows에서 소스 파일을 UTF-8로 읽도록 아래 예시는 -X utf8을 사용합니다.
-B는 Python 바이트코드 캐시 생성을 줄이는 옵션이며, pytest 캐시나 실험 결과 생성을 막지는 않습니다.

### 1. 최근 변경 사항부터 확인

~~~powershell
python -X utf8 -B -m pytest -q tests/test_unit_state_transitions.py tests/test_destroyed_vehicle_crew.py tests/test_real_detached_vehicle.py tests/test_mounted_transport.py tests/test_dismounted_crew_observation.py tests/test_echelon_mobility.py
~~~

인원·장비·탄약 보존, 차량 분리와 탑승객 처리, 탈출 승무원의 보병 시야, 제대별 이동을 확인합니다.
상태 전환의 상세 기준은 [STATE_TRANSITIONS.md](STATE_TRANSITIONS.md)를 참고하세요.

### 2. 전체 기본 검사

~~~powershell
python -X utf8 -B -m pytest tests -q -ra
~~~

지형별 검사와 통제 검사를 포함하므로 몇 분 이상 걸릴 수 있습니다.
별도 활성화하지 않은 장시간 교전 실험, TDG3 전체 실행, 입력 로그가 필요한 검사는 건너뜁니다.
이전에 MNS_RUN_ENGAGEMENT=1을 설정한 환경에서는 교전 실험도 실행될 수 있습니다.

### 3. 지형 검사와 보고서 생성

검사만 실행:

~~~powershell
python -X utf8 -B -m pytest tests/terrain -q
~~~

실험 결과와 보고서를 새로 생성:

~~~powershell
python -X utf8 -B tests/terrain/run_suite.py
python -X utf8 -B tests/terrain/build_unit_report.py
~~~

build_unit_report.py는 앞 단계가 생성한 results/results.json을 읽으므로 단독으로 먼저 실행하지 않습니다.
보고서 생성 명령은 results뿐 아니라 지형별 *_results.md, summary.md 등도 갱신합니다.
자세한 조건은 [terrain/README.md](terrain/README.md)에 있습니다.

### 4. 장시간 교전 실험

~~~powershell
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONHASHSEED='0'
python tests/upate/one_vs_one/test_update_one_vs_one.py
python tests/upate/many_vs_many/test_update_many_vs_many.py
python tests/upate/summarize.py
~~~

앞의 두 명령은 전체 교전 실험을 실행하고 결과를 생성·갱신합니다.
마지막 summarize.py는 두 실험의 생성 결과를 읽는 후처리이므로 결과가 없는 공유본에서 먼저 실행하지 않습니다.
조건과 대상 조합은 [upate/README.md](upate/README.md)를 참고하세요.

### 5. TDG3 선택 실행

~~~powershell
python -X utf8 -B -m pytest tests/test_tdg3_execution.py --run-tdg3-integration -q
python -X utf8 -B -m pytest tests/test_tdg3_replay.py --tdg3-replay logs/replay.jsonl -q
~~~

두 번째 명령은 지정한 로그 파일이 실제로 있을 때만 사용합니다.
별도 로그 없이 실행하는 기본 검사와 구분합니다. [TDG3_TESTING.md](TDG3_TESTING.md)를 참고하세요.

## 결과 해석과 팀 내 공유 방법

- passed: 해당 실행 조건에서 기대 동작을 만족했습니다.
- failed: 기대값과 실제 동작이 달랐습니다. 실패 메시지와 입력 조건을 확인해야 합니다.
- error: 수집·초기화·실행 과정에서 문제가 발생했습니다. 파일 누락이나 의존성 문제도 확인합니다.
- skipped: 옵션·입력·환경 조건에 따라 실행하지 않았습니다. 통과로 집계하지 않습니다.

기존 문서의 통과 건수와 summary.md는 특정 시점의 기록입니다.
results/tmp를 제외하면 그 안의 원시 결과 링크가 열리지 않을 수 있습니다.
이전 전체 실행에서는 기존 코드에서도 재현되는 실패 7개가 있었으며,
시나리오 ID·기준값 불일치와 Windows 인코딩 문제가 포함됩니다.
상세 내역은 STATE_TRANSITIONS.md에 있습니다. 이는 현재 공유본의 최신 전체 실행 결과를 뜻하지 않으며,
UTF-8 모드 등 실행 환경에 따라서도 결과가 달라질 수 있습니다.

팀원은 테스트 결과를 아래 형식으로 전달하면 됩니다.

~~~text
코드 버전: 커밋 해시
환경: OS / Python 버전
실행 명령:
입력 변경: 시나리오·설정 변경 여부, 실험 시드·시간 간격
결과: passed / failed / error / skipped
실패 항목: 테스트 이름과 오류 메시지
생성 결과 위치: 실험을 수행한 경우에만 기록
~~~
