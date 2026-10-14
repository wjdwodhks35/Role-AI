# 요구사항 기반 검증

`Role-AI_unit_tests.xlsx`의 단위테스트 시트가 실행 순서를 정의합니다. 실행 전 `pip install -e '.[test]'`를 설치한 뒤 저장소 루트에서 `python qa/run_excel_tests.py`를 실행하세요. 결과는 `qa/results/Role-AI_unit_tests_results.xlsx`, `results.json`, `test-results.xml`입니다.

계획과 수집한 테스트 함수가 다르면 실행은 실패합니다. 매개변수별 테스트는 같은 행 아래 순서대로 실행합니다. 스킵·미실행을 PASS로 집계하지 않습니다. 실행 결과에는 커밋과 CI 링크를 기록합니다. 실환경 검증과 미구현 기능은 단위 테스트 통과 수에 포함하지 않습니다.

## 이번 검증 결과

보고서: `qa/reports/Role-AI_unit_tests_results.xlsx`. 검증한 코드 커밋과 실제 실행 링크는 보고서 요약 및 `qa/reports/results.json`에 명시했습니다. 72개 시나리오/98개 실행이 엑셀 순서대로 통과했습니다. 최초 실행의 기억 수정 중복 결함은 `qa/defects.json`과 `qa/reports/initial-results.json`에 남겼습니다. 동시 쓰기는 예방적으로 직렬화했습니다. CI의 매 실행별 최신 보고서는 `requirements-unit-test-results` 아티팩트에서 받을 수 있습니다.

실환경 API/GPU 검증, 의미 기반 검색·문서 RAG·회의 처리·Windows 앱 등 미구현 기능은 이 통과 결과에 포함하지 않습니다. 테스트 의존성에서 사용 중단 예정 경고 1건이 발생했으며 실패는 없습니다.

## 문서 기능 검증 확장

문서 보관·프로젝트 격리·로컬 문서 대화 등 12개 시나리오를 추가해 계획은 84개가 되었습니다. 이전 `qa/reports/` 결과는 해당 파일의 커밋/실행 날짜에 대한 기록입니다. 최신 CI 실행의 `requirements-unit-test-results` 아티팩트가 최신 전체 결과입니다.

문서 기능 보강 후 전체 검증: 84개 시나리오/116개 실행 모두 PASS, Excel 순서 일치. 검증 근거: https://github.com/wjdwodhks35/Role-AI/actions/runs/38522557598. `qa/reports/Role-AI_unit_tests_results.xlsx`와 `results.json`을 이 실행 결과로 갱신했습니다. 기존 기억 수정 결함의 최초 실패 기록은 유지합니다.
