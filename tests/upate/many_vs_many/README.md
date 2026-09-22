# 다대다 교전 재실험

- 테스트 코드: [test_update_many_vs_many.py](test_update_many_vs_many.py)
- 실험 요약: [results/summary.md](results/summary.md)
- BLUE/RED 생존 인원·잔존 장비: [results/survivors.md](results/survivors.md)
- 수치·조건·소스 해시: [results/results.json](results/results.json)
- 결과 파일 검증 및 SHA-256: [results/audit.json](results/audit.json)
- pytest 결과: results/pytest.xml, results/run.log
- 배치별 입력과 전체 시계열·이벤트: results/cases/

공통 실험 코드와 실행 방법은 [상위 README](../README.md)를 참고한다. 생존자 표와 audit는 전체 실행 후 `python tests/upate/summarize.py`로 생성한다.
