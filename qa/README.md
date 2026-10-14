# 요구사항 기반 검증

`Role-AI_unit_tests.xlsx`의 단위테스트 시트가 실행 순서를 정의합니다. `pip install -e '.[test]'` 설치 후 저장소 루트에서 `python qa/run_excel_tests.py`를 실행하세요. 결과는 `qa/results/Role-AI_unit_tests_results.xlsx`, `results.json`, `test-results.xml`입니다.

계획/코드 불일치, 스킵/미실행/순서 불일치는 PASS로 집계하지 않습니다. 최신 실행 보고서는 CI `requirements-unit-test-results` 아티팩트에서 받을 수 있습니다.

보관한 보고서: `qa/reports/Role-AI_unit_tests_results.xlsx`, 129개 시나리오/189개 실행 모두 PASS. 코드/실행 근거: 9c410f7a2a27be006945103bb01168fa3ab36c24 / https://github.com/wjdwodhks35/Role-AI/actions/runs/38536591380. GPU 설정은 Fake 측정/할당기·Mock 모델로 검증했으며 실제 GPU/WSL/컨테이너 접근은 미실행입니다. 기존 기억 수정의 최초 실패는 `initial-results.json` 및 결함이력에 유지합니다. 테스트 의존성 deprecation 경고1건입니다.

GPU 테스트 fixture import 정적검사 충돌(F811)을 수정했습니다. Docker CLI 수집 Worker의 상속 HTTP healthcheck를 비활성화했고, 최종 CI에서 기본 및 GPU overlay 구성을 검증합니다. 실제 유료/GPU/HF/장기간운영/브라우저E2E는 별도 실환경 시트입니다.
