# 요구사항 기반 검증

`Role-AI_unit_tests.xlsx`의 단위테스트 시트가 실행 순서를 정의합니다. `pip install -e '.[test]'` 설치 후 저장소 루트에서 `python qa/run_excel_tests.py`를 실행하세요. 결과는 `qa/results/Role-AI_unit_tests_results.xlsx`, `results.json`, `test-results.xml`입니다.

계획/코드 불일치, 스킵/미실행/순서 불일치는 PASS로 집계하지 않습니다. 최신 실행 보고서는 CI `requirements-unit-test-results` 아티팩트에서 받을 수 있습니다.

보관한 보고서: `qa/reports/Role-AI_unit_tests_results.xlsx`, 104개 시나리오/154개 실행 모두 PASS. 코드/실행 근거: cc53cc3c0a1f61be0a3cc90ee0e0254184b9bd07 / https://github.com/wjdwodhks35/Role-AI/actions/runs/38529508989. 기존 기억 수정 결함의 최초 실패 기록은 `initial-results.json` 및 결함이력에 유지합니다. 테스트 의존성 사용 중단 예정 경고1건이 있습니다.

자동 수집은 Mock HF/Ollama로 전체 흐름과 실제 정제/저장/일정을 검증했습니다. 실제 HF/Ollama/GPU/장기간운영/브라우저 E2E 및 미구현 기능은 별도 시트로 구분합니다.
