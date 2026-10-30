# P2-E 비교/기준선 구현 10 Pass 자체 재검토

시작 dev6ec1c887… (#46). P2-E 전체 성능 평가/병합은 미완료이며 구현 검토와 실측 완료를 구분한다.

|관점|새 발견/수정|잔여 위험|
|---|---|---|
|1 요구|동일10과제·반복 수·seed·조건/구현 hash 비교|실제 A/B 제출 미확보|
|2 모순|Codex 모델 호출·시간/복구 미측정에 null 허용|unknown을0으로 취급하지 않는 consumer 필요|
|3 사실|실제 계획 응답119 backend tokens만 존재|코딩 점수/절감률 아님|
|4 실행|공개 패키지/채점/비교 명령 준비|Docker 접근 요청 미해결|
|5 구조|기존 TaskContract/합성 DB/독립 grader 재사용|추가 worker·모델 API 없음|
|6 보안|경로·원본 public fixture 고정/쓰기 scope solution만|로컬 operator의 자료 제공/컨텍스트 검토 신뢰 필요|
|7 자원|기존 sandbox/GPU/승인 제한 유지|물리 VRAM cap 및 GPU 독점 보장 아님|
|8 결과|품질 회귀/미사용량/미공정 조건에서 개선 거부|표본 확대·실환경 반복 평가 필요|
|9 반론|실제 Docker 참조 점수도 Codex 작성 증거가 없어 실제 집계 제외|제공된 usage/context 원문은 별도 감사 필요|
|10 재통합|현재 세션 oracle 노출로 직접 기준선 생성 보류|필수 실측 부족으로 P2-E draft 유지·dev 미병합|

공개 파일 안전 읽기 helper가 bytes를 반환한다는 사실을 테스트에서 확인하고 UTF-8 비교/변환을 수정했다. 비교 회귀/불충분 결과는 CLI exit2로 내보내 성공으로 숨기지 않는다. 독립 10개 AI 리뷰나 실제 A/B 실험 통과를 의미하지 않는다.

최종 로컬 QA479시나리오/922실행 통과, source snapshot gmqodo8e와 app/QA/tests 해시 일치. Ruff/diff 통과. 최종 CI 수신은 PR 본문에 남기며 실제 A/B 부족으로 dev 병합하지 않는다.
