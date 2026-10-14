# 요구사항 기반 검증

`Role-AI_unit_tests.xlsx`의 단위테스트 시트가 실행 순서를 정의합니다. `pip install -e '.[test]'` 설치 후 저장소 루트에서 `python qa/run_excel_tests.py`를 실행하세요. 결과는 `qa/results/Role-AI_unit_tests_results.xlsx`, `results.json`, `test-results.xml`입니다.

계획과 수집한 테스트 함수가 다르면 실패합니다. 매개변수별 테스트는 같은 행 아래 순서대로 실행합니다. 스킵·미실행·순서 불일치를 PASS로 집계하지 않습니다. 실행 커밋과 CI 링크는 보고서 요약에 기록됩니다. 최신 실행의 보고서는 CI `requirements-unit-test-results` 아티팩트에서 받을 수 있습니다.

현재 보관한 보고서: `qa/reports/Role-AI_unit_tests_results.xlsx`, 95개 시나리오/141개 실행 모두 PASS. 검증 코드/실행 근거: 1ff9e4fd4e7b9d151c2cb2297ecb03b5a9fa6358 / https://github.com/wjdwodhks35/Role-AI/actions/runs/38526015394. 이전 기억 수정 결함 최초 실패는 `initial-results.json` 및 결함이력 시트에 남겨두었습니다. 테스트 의존성 사용 중단 예정 경고 1건이 있습니다.

실제 로컬 임베딩/LLM 품질·GPU, 브라우저 E2E, 외부 API 및 미구현 DOCX/OCR/회의 처리 등은 별도 표시하며 통과 수에 포함하지 않습니다.
