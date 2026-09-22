# 부대 상태 전환 통합 테스트

## 이번 단계의 범위

권장 순서의 세 가지 연속 전환을 tests/test_unit_state_transitions.py에서 검증한다.
명중 확률이나 이동속도 대신 상태 변경 API를 직접 호출하여 소유권 오류를 결정적으로 재현한다.

1. 통합 → 상태 변경 → 해체: 승무원 연결·보호 관계, 손상 상태, 유한 탄약,
   중첩 통합, 반복 해체 방지, 분리 차량·탈출 승무원의 부모 관계.
2. 무장 승무원 → 손상 차량 분리 → 파괴 → 탈출: 개인 무기 수와 탄약의 이전,
   0·1·홀수·무제한 탄약, 마지막 차량 분리, 중복 탈출 방지.
3. 보병 탑승 → 차량 분리·파괴 → 하차: 첫·중간·마지막 차량의 좌석 참조,
   외부 부대 이전, 편제 내 보병과 승무원 구분, 좌석 부족 시 재탑승 거절,
   마지막 차량 파괴 후 생존 부대 활성화.

## 적용한 소유권 규칙

- 통합 전 비활성 원부대와 해체 후 비활성 통합부대는 보존 합계에서 제외한다.
  외부 탑승부대는 비활성이어도 실제 인원을 소유하므로 포함한다.
- 통합 시 요소 ID뿐 아니라 crew_elements와 protected_by 참조를 변환한다.
  역할 기반 승무원 연결은 통합 전에 각 원부대 안에서 확정한다.
- 해체는 현재 요소 전체를 복원한다. 사라진 요소를 이전 편제로 재생성하지 않는다.
- 승무원의 개인 무기·탄약은 인원과 함께 이전한다. 정수 나눗셈 잔량은 원소유자에게
  남기고 마지막 인원 이전 때 모두 이전한다. -1은 무제한 표기로 유지한다.
- 외부 부대 전원이 단일 분리 차량에 탑승했다면 해당 차량을 새 수송자로 지정한다.
  여러 차량에 걸쳐 탑승한 부대는 피해 차량 발생 시 부대 전체가 비상 하차한다.
- 편제 내 탑승 보병은 기존 하차 모델에 맞춰 한 부대로 비상 하차한다.
- 하차 처리는 현재 생존 인원만 이전하며 별도의 추가 사망 확률을 적용하지 않는다.
- 분리로 차량 목록이 축소될 때만 뒤쪽 좌석 인덱스를 조정한다.
  파괴된 슬롯이 목록에 남아 있으면 그 인덱스를 유지하며 신규 좌석 배정에서는 제외한다.

## 실행

프로젝트 루트에서:

~~~powershell
python -B -m pytest tests/test_unit_state_transitions.py -q
python -B -m pytest tests/test_unit_state_transitions.py tests/test_mounted_transport.py tests/test_destroyed_vehicle_crew.py tests/test_real_detached_vehicle.py tests/test_echelon_mobility.py -q
~~~

전자는 26개, 후자는 69개 테스트 사례를 검증한다.

## 후속 검증 범위

전체 제안 중 보고서·임무 판정, 통신·관측 승계, 편집기 저장·불러오기,
장시간 성능 및 전체 로그 감사는 별도 검증 단계다. 이번 테스트의 수량 보존 검증을
보고서 집계나 저장·복원 기능의 검증으로 해석하지 않는다.

## 검증 결과

- 신규 통합 테스트: 26개 통과.
- 최종 관련 회귀 테스트: 69개 통과.
- 전체 기본 테스트 실행: 1,613개 통과, 323개 건너뜀, 7개 실패.
  이 실행 후 부대 ID 접두사 충돌 사례 1개를 추가하고 관련 69개를 재검증했다.
- 전체 실행의 실패 7개는 변경한 4개 기존 모듈을 HEAD 버전으로 불러온
  비교 실행에서도 동일한 메시지로 재현되었다.

| 기존 실패 테스트 | 원인/관측값 |
|---|---|
| test_echelon_artillery_scaling.py::test_shell_personnel_cap_is_per_formation_not_per_element | R-ARTY_PLT-1 부대 ID 없음 |
| test_environmental_observation.py::test_clear_day_open_terrain_is_neutral | 기대 탐지거리 900, 실제 750 |
| test_external_definition_database.py::test_weapon_catalog_resolves_csv_definition | 무기 이름 기대값과 현재 정의 불일치 |
| test_optional_machine_gun.py::test_editor_optional_attachment_is_metadata_driven | 기본 cp949로 editor.py 읽기 실패 |
| test_scenario_suite_contracts.py::test_scenario_directory_json_assets_are_classified | 목록에 없는 scenarios/tdg3.json |
| test_targeting_doctrine.py::test_target_score_uses_perceived_classification_not_ground_truth_branch | 두 표적 점수 모두 0 |
| test_weapon_inventory_editor_semantics.py::test_editor_distinguishes_disposable_crew_served_and_platform_mounts | 기본 cp949로 editor.py 읽기 실패 |
