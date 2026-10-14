# 요구사항 기반 검증

`Role-AI_unit_tests.xlsx`의 단위테스트 시트가 실행 순서를 정의합니다. `pip install -e '.[test]'` 설치 후 저장소 루트에서 `python qa/run_excel_tests.py`를 실행하세요. 결과는 `qa/results/Role-AI_unit_tests_results.xlsx`, `results.json`, `test-results.xml`입니다.

계획/코드 불일치, 스킵/미실행/순서 불일치는 PASS로 집계하지 않습니다. 최신 실행 보고서는 CI `requirements-unit-test-results` 아티팩트에서 받을 수 있습니다.

보관한 보고서: `qa/reports/Role-AI_unit_tests_results.xlsx`, 117개 시나리오/173개 실행 모두 PASS. 코드/실행 근거: c543acb17aad511df3665a584e510b5c99568b3c / https://github.com/wjdwodhks35/Role-AI/actions/runs/38531372158. 기존 기억 수정 결함의 최초 실패는 `initial-results.json` 및 결함이력에 남깁니다. 테스트 의존성 deprecation 경고1건이 있습니다.

GPT 자동 허용 정책은 실제 Responses API 형식의 Mock으로 검증했으며 비용이 발생하지 않았습니다. 실제 유료 호출/청구·GPU·HF/로컬LLM 품질·장시간운영·브라우저 E2E는 별도 실환경 시트의 미실행 항목입니다.
