# 최신 화력 API 전체 교전 재실험

사용자가 지정한 폴더명 `upate`를 사용한다. 이전 결과와 별도 보관한다.

- one_vs_one/test_update_one_vs_one.py: 44개 조합 × 3개 시드 = 132회.
- many_vs_many/test_update_many_vs_many.py: 기본·추가 배치를 합친 276개 조합 × 3개 시드 = 828회.
- experiment.py: 현재 엔진을 호출하는 공통 실행·기록 코드. 기존 실험 조건을 유지한다.
- 각 results/: summary.md, results.json, pytest.xml, 실행 로그, cases/의 시나리오·전체 이벤트·시계열.

기본 24개 편제의 동종 대결과 대표 편제의 순서 있는 이종 병종 대결을 포함한다. 다대다 동종 편제는 횡대·종대·엇갈림 및 3:3·4:2·2:4를 모두 검사한다. 모든 24×24 편제 쌍을 검사하는 것은 아니다.
시드 7/19/41, dt=0.25초, 포병 포함 240초·나머지 120초, 열린 지형과 기존 자동 관측·교전 조건을 사용한다.
런타임 참여량은 Unit.firepower()를 사용한다. 소스 해시는 결과에 기록한다.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONHASHSEED='0'
python tests/upate/one_vs_one/test_update_one_vs_one.py --junitxml=tests/upate/one_vs_one/results/pytest.xml
python tests/upate/many_vs_many/test_update_many_vs_many.py --junitxml=tests/upate/many_vs_many/results/pytest.xml
```

일반 pytest에서는 통제 검사를 실행하고 장시간 native 실험은 생략한다. 위 직접 실행 명령으로 전체 실험을 수행한다.
같은 명령 재실행 시 이 폴더의 결과는 갱신된다. 초기 1대1은 편제 하나씩이라는 뜻이며 개별 전투원 1명씩은 별도 통제 검사다.
교전 성립과 상태 보존의 통과는 순수 란체스터 법칙 또는 실제 예측 정확도의 보장이 아니다.

최종 실행 및 검증 결과: [summary.md](summary.md). 전체 실행 후 `python tests/upate/summarize.py`로 원시 결과·코드 해시와 생존자 표를 재검증할 수 있다.
