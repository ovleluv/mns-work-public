# 지형 이동 테스트 종합 결과

실행: 2026-09-14T20:35:39.550779+09:00 / Python 3.11.9 / 시드 7

총 1272건: 통과 1272, 실패 0, 오류 0, 건너뜀 0.

| 지형 | 통과 | 실패 | 오류 | 상세 |
|---|---:|---:|---:|---|
| 평지 | 72 | 0 | 0 | [open](open_results.md) |
| 강 | 288 | 0 | 0 | [river](river_results.md) |
| 도하 | 144 | 0 | 0 | [ford](ford_results.md) |
| 다리 | 216 | 0 | 0 | [bridge](bridge_results.md) |
| 도로 | 168 | 0 | 0 | [road](road_results.md) |
| 숲 | 72 | 0 | 0 | [forest](forest_results.md) |
| 호수 | 72 | 0 | 0 | [lake](lake_results.md) |
| 건물 | 72 | 0 | 0 | [building](building_results.md) |
| 고도·경사 | 96 | 0 | 0 | [elevation](elevation_results.md) |
| 성긴 숲 | 72 | 0 | 0 | [woods](woods_results.md) |

실패는 xfail 처리 없이 집계합니다. 숲·도로 수정 전 결과는 results/before_forest_road_fix/results.json에 보존했습니다. 실행 오류는 로그와 종료 코드를 확인하세요.

원시 좌표 궤적, 계획 경로, 도착 시간, 잔여 거리, 입력·코드 SHA-256: [results.json](results/results.json).

검증 범위와 재현 방법은 [README](README.md), 원인 분석은 [findings](findings.md)를 참고하세요.
