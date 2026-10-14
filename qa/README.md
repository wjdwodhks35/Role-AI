# 요구사항 기반 검증

`Role-AI_unit_tests.xlsx`의 단위테스트 시트가 실행 순서를 정의합니다. 실행 전 `pip install -e '.[test]'`를 설치한 뒤 저장소 루트에서 `python qa/run_excel_tests.py`를 실행하세요. 결과는 `qa/results/Role-AI_unit_tests_results.xlsx`, `results.json`, `test-results.xml`입니다.

계획과 수집한 테스트 함수가 다르면 실행은 실패합니다. 매개변수별 테스트는 같은 행 아래 순서대로 실행합니다. 스킵·미실행을 PASS로 집계하지 않습니다. 실행 결과에는 커밋과 CI 링크를 기록합니다. 실환경 검증과 미구현 기능은 단위 테스트 통과 수에 포함하지 않습니다.
