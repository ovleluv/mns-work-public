# 공격·방어 작전 BML 수행 가능성 조사

- 대상: 현재 프로젝트의 실제 소스·기존 편제·무장·센서·교리 설정.
- 범위: BLUE 중심의 공격·방어 8개 작전. 안정화·특수 작전은 제외.
- 작성 위치: `mns_v49_7_work/MISSION`.
- 엔진·기존 시나리오·데이터베이스는 수정하지 않았다. 새 시나리오와 양측 BML만으로 가능한 범위를 조사했다.

## 1. 판정

| 분류 | 작전 | 수행 가능 여부 | 확인 범위와 경계 |
|---|---|---|---|
| 공격 | 공격 | **가능** | 지정 좌표로 전진하고 관측된 적과 교전하는 공격. 승리·격멸·점령 보장을 의미하지 않음. |
| 공격 | 이동간 접적 | **가능** | 적 ID·위치 정보를 BLUE BML에 주지 않고 수색 방향으로 전진한 뒤 Track에 따라 교전. |
| 공격 | 매복 | **부분 가능** | 대기·통과 중인 적과 교전·시간 기반 전환·이탈은 가능. 사격 보류와 탐지 기반 개시 통제는 미지원. |
| 공격 | 정찰 | **부분 가능** | 경로 이동·관측·적 Track 생성·아군 전파·복귀는 가능. 비접촉 규칙, 정보 수집 목표 및 완료 판정은 미지원. |
| 방어 | 지역 방어 | **가능** | 방어 구역과 추격 제한을 지정하여 지역 내 방어·교전 가능. 지형 소유권 판정은 별도 기능. |
| 방어 | 기동 방어 | **부분 가능** | 고정 방어부대와 예비대의 예정된 반격을 조합 가능. 관측된 적의 돌파·구역 진입에 반응하는 반격 조건은 미지원. |
| 방어 | 지연 작전 | **조건부 가능** | 명시한 지연선에서 방어→철수→재방어를 순차 실행 가능. 자동 교대·시간 확보량 평가·피해 최소화 최적화는 미지원. |
| 방어 | 후퇴 / 철수 | **부분 가능** | 접촉 중 지정 집결지로 철수하는 이동은 가능. 적과의 접촉 단절을 명령의 완료 조건으로 정의하지 못함. |

‘가능’은 해당 행동을 현재 모델과 BML로 실행할 수 있다는 뜻이다. 실제 작전 성공률, 임의의 지형·편제에서의 성공, 군사적 효과의 검증을 뜻하지 않는다. ‘부분 가능’ 케이스도 실행 가능한 BML을 제공했으며, 미지원 요구를 구현한 것처럼 이름만 붙이지 않았다.

## 2. 조사·검증 방법

1. `mnsim/bml.py`의 허용 task·조건 경로·분기 컴파일과 `simulation.py`의 실제 실행을 대조했다.
2. 기존 `RIFLE_SQD` 편제와 기본 데이터베이스를 재사용하고, 작전마다 BLUE 계획과 이를 시험할 RED 계획을 작성했다. 지도는 가상 좌표이며, 복잡한 지형의 영향과 명령 표현의 영향을 구분하기 위한 소규모 기능 확인 케이스이다.
3. 모든 시나리오와 양측 BML을 실제 로더로 불러왔다. 조건·기한 분기 내부까지 컴파일하고, 부대 소속·좌표 범위·명령 참조를 검사했다.
4. seed=7, dt=0.25초, 배속 기본값으로 `Simulation.tick`을 호출했다. 관측·통신·기동·피해·사격은 기존 엔진이 처리하며, 적 Track 주입·순간이동·피해 강제 설정·무장 성능 변경을 하지 않았다.
5. 오류가 없는지만 확인하지 않고 작전별 전진, Track 획득, 교전, 단계 전환, 복귀 등 필요한 기능의 실행 여부를 검사했다. 검증 창의 길이는 [MISSION_MANIFEST.json](MISSION_MANIFEST.json)에 있는 **입력 설정**이다.
6. 지연 작전에 사용한 손실 조건과 단계별 명령이 부대별로 진행되는 의미는 기존 단위 테스트 2개로 추가 확인했다. 이 두 테스트의 인위적 상태 설정은 독립된 엔진 계약 시험이며, 작전 시나리오 실행에는 사용하지 않았다.

[VALIDATION_STATUS.json](VALIDATION_STATUS.json)의 PASS는 **표에 선언한 지원 범위와 제한의 검증 통과**이다. 8개 작전 모두 완전 지원한다는 뜻이 아니다. 단일 seed의 기능 실행 확인이며, 성공률 산출이나 Monte Carlo 평가를 하지 않았다. 소스·설정·입력 파일의 SHA-256을 함께 보관하여 이후 변경 시 재검증 대상을 구분한다.

전투 승패, 인명·장비 손실, 격멸률, 점령 성과, 교전 경과, 소요시간 결과 및 리플레이는 보고서에 수록하거나 별도 저장하지 않았다. 실행 중 생성되는 상세 이벤트는 검증기가 메모리에서 확인한 뒤 버린다.

## 3. 작전별 시나리오와 BLUE BML 구성

### 3.1 공격 — ATTACK

- 시나리오: BLUE `B-MAIN`, `B-SUPPORT`가 알려진 좌표의 RED 방어부대를 향해 이동한다. RED는 `DEFEND_AREA`로 해당 위치를 방어한다.
- BLUE 변경: 두 부대에 `ATTACK_POSITION`을 부여한다. `engagement_range_policy=COMBINED_ARMS`와 `engagement_range_fraction=0.9`로 기존 접근 정책을 명시한다. 적 부대의 실시간 좌표를 직접 조회하는 명령은 사용하지 않는다.
- 판정 기준: 공격 방향 이동, 자체 Track 획득, 실제 교전 기능의 실행 여부.
- 한계: 지속형 공격은 목표에 도착해도 자동으로 ‘지역 점령 성공’이 되지 않는다. `DESTROY_UNIT`도 별도 지원하지만 이번 케이스는 좌표 기반 공격이다. 건물 파괴는 별도 `ATTACK_STRUCTURE`의 영역이며 이 케이스의 시험 범위는 아니다.

### 3.2 이동간 접적 — MOVEMENT_TO_CONTACT

- 시나리오: BLUE `B-SEARCH`와 RED `R-UNKNOWN`은 초기 BLUE 탐지거리 밖에 있다. RED는 제자리 방어를 유지한다.
- BLUE 변경: `ATTACK_POSITION`에 수색 진행 방향의 좌표만 지정한다. 적 `target`, `target_position`, 초기 Track은 제공하지 않는다. 이 좌표는 적의 위치를 뜻하지 않는다.
- 판정 기준: 무접촉 상태의 전진에서 정상 센서에 의한 접촉 획득·교전으로 이어질 수 있는지.
- 한계: 자동 수색 구역 분할이나 체계적인 경로 탐색 정책은 아니다. 하나의 축을 따라 이동하다 접촉하는 형태는 현재 공격 명령의 Track 기반 동작으로 표현된다.

### 3.3 매복 — AMBUSH

- 시나리오: BLUE `B-WAIT`는 수목 구역에서 대기하고, RED `R-PATROL`은 `MOVE_TO`로 통과한다. 수목은 기존 은폐·시야 모델을 사용할 뿐, 사격 잠금을 대신하지 않는다.
- BLUE 변경: `HOLD`에서 `sim.time >= 120` 조건으로 `ATTACK_POSITION`에 전환한다. 해당 공격의 `deadline_s=180`과 `on_deadline: WITHDRAW`로 이탈 명령을 연결하고, 이후 집결지 대기를 둔다. 숫자는 통제 기능 확인을 위한 시나리오 입력이다.
- 가능한 범위: 대기, 이동 중인 적의 관측·교전, 시간 지정 공격 전환과 이탈.
- 불가능한 범위: ‘접촉했어도 사격하지 않고 관측된 적이 지정 구역에 들어오면 동시에 사격’이라는 통제된 매복. 현재 `HOLD` 중에도 자동 교전이 허용되므로 이 계획을 정식 사격 개시 통제라고 볼 수 없다.
- 해결 방향: 사격 파이프라인 전체에 적용하는 검증된 `weapons_hold / weapons_free` 정책, Track의 추정 위치·신뢰도·관측 시각을 사용한 구역 진입 조건, 지휘 신호 및 여러 부대의 동시 개시 규칙을 추가한다. 이 이름들은 **향후 설계 예시**이며 현재 JSON에 삽입하면 지원되는 기능이 아니다.

### 3.4 정찰 — RECONNAISSANCE

- 시나리오: BLUE `B-SCOUT`가 관측 지점으로 이동하고, `B-REPORT-RECIPIENT`는 별도 위치에서 보고를 받는다. RED는 관측 대상 위치를 방어한다.
- BLUE 변경: `MOVE_TO` 두 단계 → 유한 시간 `HOLD` → `WITHDRAW` → `HOLD`. 아군 보고 수신 부대에는 대기 명령을 준다. Track 전파는 별도 가짜 보고 명령이 아니라 기존 센서·C2·통신 경로를 사용한다.
- 가능한 범위: 관측 이동, 적 Track 생성, 지연을 포함한 아군 공유, 귀환.
- 불가능한 범위: ‘발각·교전을 피하면서 지정 정보만 확보하면 임무 완료’라는 정찰 전용 제어. 무장 정찰부대는 교전 조건을 만족하면 사격할 수 있다. 지형은 미지의 정보를 수집해 새로 드러내는 모델이 아니라 이미 제공된 지형을 이동·관측 계산에 사용하는 구조다.
- 해결 방향: 정찰 정보 요구와 완료 조건, Track/Belief 기반 보고 충분성, 교전 회피·자위 사격 정책, 관측으로 갱신하는 지형 지식 계층을 추가한다. 현재 아군 공유는 기본적으로 SIDE_WIDE 통신이므로 계층별 지휘 보고를 재현하려면 통신 라우팅도 확장한다.

### 3.5 지역 방어 — AREA_DEFENSE

- 시나리오: BLUE `B-DEFENDER`가 구역을 방어하고 RED `R-ATTACKER`가 그 방향으로 공격한다.
- BLUE 변경: `DEFEND_AREA`에 `center`, `radius_m`, `pursue_within_area=false`, `return_to_center=true`를 명시한다. `hold_at_all_costs`, `allow_withdrawal=false`, `allow_break_contact=false`로 자율 이탈을 제한한다.
- 판정 기준: 공격해 오는 상대와 교전하면서 BLUE가 지정 방어 구역에 머무르는지.
- 한계: `radius_m`는 사격 금지선이 아니다. 외부의 사격 가능한 적에게도 발포할 수 있다. 구역 내 모든 공간의 차단이나 지형 소유권·방어 성공 판정은 이 명령 자체가 제공하지 않는다.

### 3.6 기동 방어 — MOBILE_DEFENSE

- 시나리오: BLUE `B-FIX`가 구역을 방어하고, `B-RESERVE`는 별도 위치에서 대기한다. RED는 방어 구역으로 공격한다.
- BLUE 변경: 고정부대에 `DEFEND_AREA`, 예비대에 `HOLD`와 `sim.time >= 90` 조건을 부여한다. 조건 충족 시 예비대만 `ATTACK_POSITION`으로 반격한다.
- 가능한 범위: 방어 역할과 반격 역할의 분리, 예정 시점의 예비대 기동·교전.
- 불가능한 범위: 실제로 관측된 적의 돌파나 침투 깊이를 평가하여 예비대를 투입하는 조건. 동일한 시간 조건은 적이 아직 오지 않았어도 충족된다. ‘부대 전체가 준비되면 다음 단계’라는 암묵적 동기화도 없다.
- 해결 방향: Track 기반 적 구역 진입·돌파 조건, 아군 임무 완료/준비 상태 조회, 명시적인 팀 단계 장벽과 재계획 기능을 BML에 추가한다. 개별 부대 명령 큐와 지휘 조정은 분리해 유지한다.

### 3.7 지연 작전 — DELAYING_OPERATION

- 시나리오: BLUE `B-DELAY`가 세 지연선을 사용하고 `B-COVER`는 별도 위치에서 일정 기간 방어 후 철수한다. RED는 BLUE 후방 방향의 좌표로 공격한다.
- BLUE 변경: 첫 `DEFEND_AREA`에 손실 조건 `self.loss_ratio >= 0.25`의 `on_true` 철수와 절대 기한 `deadline_s=20`의 `on_deadline` 철수를 함께 둔다. 두 분기는 같은 다음 지점을 사용한다. 다음 방어에서는 `self.time_in_order >= 20`으로 다시 철수하고, 최종 지연선에서 방어한다.
- 가능한 범위: 방어→손실 또는 시간 기준 철수→다음 지연선 방어→재철수. `WITHDRAW` 완료 후 원래 명령 큐의 다음 방어가 이어진다.
- 조건: 이동 가능한 경로와 살아서 기동 가능한 부대가 필요하다. 다음 단계의 시작은 해당 부대의 이전 명령 완료에 달려 있다. 엄호부대와의 자동 교대·상호 완료 확인은 없다.
- 한계와 해결 방향: 기존 BML만으로 명시적 단계 계획은 구성 가능하다. ‘최소 피해로 필요한 시간을 확보했는가’의 평가에는 지연 시간 목표와 평가기, 상황에 따른 교대에는 아군 상태 조건 및 동기화 기능이 추가로 필요하다.
- 작성 주의: `conditions` 배열은 AND이다. 손실 조건과 시간 조건을 배열에 함께 넣으면 OR가 되지 않는다. 이 케이스는 `on_true`와 `on_deadline`을 분리하며, 같은 tick에는 현재 엔진에서 조건 분기가 기한 분기보다 우선한다. 두 조건이 실제 교전에서 모두 발동했다는 주장은 하지 않는다.

### 3.8 후퇴 / 철수 — WITHDRAWAL

- 시나리오: BLUE `B-WITHDRAW`가 접촉 상태에서 지정 집결지로 이동하고, RED `R-PURSUER`는 그 방향으로 공격한다.
- BLUE 변경: 초기 `DEFEND_AREA`에서 `sim.time >= 25` 조건으로 `WITHDRAW`에 전환하고, 뒤에 집결지 `HOLD`를 둔다.
- 가능한 범위: 조건부 철수 명령, `RETREATING` 상태의 목적지 이동, 도착 후 대기.
- 불가능한 범위: 철수 명령의 완료를 ‘적과의 교전을 끊었다’로 해석하는 것. `WITHDRAW`는 `RETREAT`로 컴파일되어 목적지 도착으로 완료하며, 사격을 금지하지 않는다. 적이 계속 관측·추격하거나 사격할 수 있다. 현재 케이스의 집결지 도착 확인을 접촉 단절 확인으로 대체하지 않았다.
- 해결 방향: 아군이 이용할 수 있는 접촉 정보와 최근 피격/사격 기록을 사용한 이탈 조건, 집결지 도착과 이탈 조건의 결합, 필요 시 적 반응과 엄호부대 조정을 별도 기능으로 추가한다. 부대가 실제로 적에게 보이지 않는지 여부와 아군이 접촉을 잃었는지는 구분해야 한다.

## 4. 공통 제약 및 코드 근거

| 확인한 구현 | 소스 위치 | BML 작성 시 의미 |
|---|---|---|
| 허용 task 목록과 컴파일 | [bml.py](../mnsim/bml.py), `MISSION_TASKS` 및 `compile_mission` (93행, 101행 부근) | 전용 AMBUSH/RECONNAISSANCE/MOBILE_DEFENSE/DELAY task는 없음. 기존 명령 조합의 가능성과 전용 task 유무는 구분. |
| 조건 언어 | [bml.py](../mnsim/bml.py), `ConditionEvaluator.resolve` (36행) | 시간·자기 손실·상태·목표 거리 등만 사용 가능. Track 개수·구역 진입 조건은 없음. |
| 실제 적 위치를 쓰는 근접 수 (해결됨) | [bml.py](../mnsim/bml.py), `self.enemy_count_near` | 이제 자기 Track의 추정 위치만 센다. 기존 8개 케이스는 계속 이 조건을 쓰지 않는다. |
| 시간/조건/기한 처리 | [simulation.py](../mnsim/simulation.py), `_step_unit` (166행) | 조건은 명령 실행 전제조건이 아니라 실행 중 명령 교체 트리거. 시작 대기는 사격 금지와 다름. |
| 구역 방어 | [simulation.py](../mnsim/simulation.py), `_step_defend_area_order` (440행) | 추격 경계와 사격 가능 범위는 별도. |
| 좌표 공격·표적 공격 | [simulation.py](../mnsim/simulation.py), `_step_attack_order` (587행), `_step_entity_attack_order` (515행) | 적 ID만 주어도 자동으로 실제 위치를 알아내지 않음. 표적 ID 명령에서 Track/명시적 참조가 없으면 대기 가능. |
| 자동 교전 | [simulation.py](../mnsim/simulation.py), `tick` 및 `_combat_step` (136행, 1406행), [combat.py](../mnsim/combat.py) | HOLD/MOVE/RETREAT 여부만으로 사격 중지 정책이 되지 않음. |
| 철수 매핑 | [bml.py](../mnsim/bml.py) 227행, [simulation.py](../mnsim/simulation.py) 228행 부근 | WITHDRAW→RETREAT, 목적지 도착 시 명령 완료. |
| 단계별 큐 | [bml.py](../mnsim/bml.py), `apply_bml_document` (289행) | phases는 부대별 명령 큐로 컴파일. 팀 전체 동기화 기능이 아님. |
| 보고 전파 | [simulation.py](../mnsim/simulation.py), `_sensor_step` (1048행), [communications.py](../mnsim/communications.py), `eligible_recipients` (74행) | 현재 기본 공유 모델은 지연·신뢰도를 가진 SIDE_WIDE 통신. |

`self.state` 조건은 존재하지만 자기 부대의 행위 상태를 읽는다. 특정 적의 최신 관측·구역 진입·다른 아군 부대의 준비 상태를 조회하는 기능과 같지 않다.

지속형 `ATTACK_POSITION` 또는 무기한 `HOLD` 뒤에 후속 명령을 단순히 추가하면 다음 명령이 실행되지 않을 수 있다. 다음 단계로 넘기려면 유한 지속시간, 명시적 조건/기한 분기, 또는 적절한 `persistent=false`를 사용해야 한다. 이번 케이스는 필요 지점에 종료·교체 규칙을 명시했다.

정의되지 않은 `task`는 거부되지만 임의의 `directives` 키가 모두 엄격하게 검증되는 것은 아니다. 따라서 `hold_fire: true` 같은 키를 넣고 파일이 로드된다는 사실만으로 그 기능이 구현되었다고 판단하면 안 된다. 기능 확장은 컴파일러, 실행부, 재현 가능한 검증을 함께 변경해야 한다.

## 5. 재현 파일

각 폴더는 작전명 접두사의 SCENARIO, BLUE_BML, RED_BML, TERRAIN JSON을 포함한다. 원본 프로젝트의 config/database를 상대경로로 참조하므로 MISSION만 단독 배포하는 형식은 아니다.

| 작전 | 시나리오 | BLUE BML | RED BML |
|---|---|---|---|
| 공격 | [ATTACK_SCENARIO.json](ATTACK/ATTACK_SCENARIO.json) | [ATTACK_BLUE_BML.json](ATTACK/ATTACK_BLUE_BML.json) | [ATTACK_RED_BML.json](ATTACK/ATTACK_RED_BML.json) |
| 이동간 접적 | [MOVEMENT_TO_CONTACT_SCENARIO.json](MOVEMENT_TO_CONTACT/MOVEMENT_TO_CONTACT_SCENARIO.json) | [MOVEMENT_TO_CONTACT_BLUE_BML.json](MOVEMENT_TO_CONTACT/MOVEMENT_TO_CONTACT_BLUE_BML.json) | [MOVEMENT_TO_CONTACT_RED_BML.json](MOVEMENT_TO_CONTACT/MOVEMENT_TO_CONTACT_RED_BML.json) |
| 매복 | [AMBUSH_SCENARIO.json](AMBUSH/AMBUSH_SCENARIO.json) | [AMBUSH_BLUE_BML.json](AMBUSH/AMBUSH_BLUE_BML.json) | [AMBUSH_RED_BML.json](AMBUSH/AMBUSH_RED_BML.json) |
| 정찰 | [RECONNAISSANCE_SCENARIO.json](RECONNAISSANCE/RECONNAISSANCE_SCENARIO.json) | [RECONNAISSANCE_BLUE_BML.json](RECONNAISSANCE/RECONNAISSANCE_BLUE_BML.json) | [RECONNAISSANCE_RED_BML.json](RECONNAISSANCE/RECONNAISSANCE_RED_BML.json) |
| 지역 방어 | [AREA_DEFENSE_SCENARIO.json](AREA_DEFENSE/AREA_DEFENSE_SCENARIO.json) | [AREA_DEFENSE_BLUE_BML.json](AREA_DEFENSE/AREA_DEFENSE_BLUE_BML.json) | [AREA_DEFENSE_RED_BML.json](AREA_DEFENSE/AREA_DEFENSE_RED_BML.json) |
| 기동 방어 | [MOBILE_DEFENSE_SCENARIO.json](MOBILE_DEFENSE/MOBILE_DEFENSE_SCENARIO.json) | [MOBILE_DEFENSE_BLUE_BML.json](MOBILE_DEFENSE/MOBILE_DEFENSE_BLUE_BML.json) | [MOBILE_DEFENSE_RED_BML.json](MOBILE_DEFENSE/MOBILE_DEFENSE_RED_BML.json) |
| 지연 작전 | [DELAYING_OPERATION_SCENARIO.json](DELAYING_OPERATION/DELAYING_OPERATION_SCENARIO.json) | [DELAYING_OPERATION_BLUE_BML.json](DELAYING_OPERATION/DELAYING_OPERATION_BLUE_BML.json) | [DELAYING_OPERATION_RED_BML.json](DELAYING_OPERATION/DELAYING_OPERATION_RED_BML.json) |
| 후퇴 / 철수 | [WITHDRAWAL_SCENARIO.json](WITHDRAWAL/WITHDRAWAL_SCENARIO.json) | [WITHDRAWAL_BLUE_BML.json](WITHDRAWAL/WITHDRAWAL_BLUE_BML.json) | [WITHDRAWAL_RED_BML.json](WITHDRAWAL/WITHDRAWAL_RED_BML.json) |

실행 방법은 [README.md](README.md), 판정의 기계 판독용 확인은 [VALIDATION_STATUS.json](VALIDATION_STATUS.json), 재검증 코드는 [validate_missions.py](validate_missions.py)를 참조한다.
