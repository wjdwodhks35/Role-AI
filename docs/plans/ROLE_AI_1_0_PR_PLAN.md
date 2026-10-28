# 단계별 PR·검증·롤백 계획

모든 구현은 최신 dev → 독립 feature/fix worktree → dev 대상 PR → 필수 CI/충돌/미해결 검토
확인 흐름을 따른다. main에 직접 반영하지 않는다. 운영 반영과 실제 학습은 별도 승인이다.

| 순서 | PR 범위와 예정 코드 | 테스트 / 진입·완료 조건 |
|---|---|---|
| 0 | GAP/기준선/보안 계약; 기존 baseline_manifest 활용 | 소스/Mock/실모델 기준선 구분, 운영 실측 미확인 표시 |
| 1-A/B (이번) | coding_tasks / coding_workspace; 내부 계약·worktree·patch | 실제 임시 Git, 해시/범위/한도/원본 보존/hook·filter 차단/유효 diff; 실행 권한 없음 |
| 1-C | 별도 저장소 테스트 runner + code_execution 연계 | 실제 Docker 성공/실패/0개 테스트/timeout/OOM/출력·정리 실패; 호스트 fallback 없음 |
| 1-D | coding orchestrator + 승인·작업 API/영속성 | 기존 auth/CSRF, 사용자 격리, 모델 malformed 응답, 실패→재수정, 취소/재시작/예산 종료, 테스트 약화 탐지 |
| 1-E | 작업 화면·결과·PR 후보 산출 | 다른 소형 저장소의 버그/기능/해결 불가 과제, 독립 테스트·로그/diff 일치; push/merge 별도 경계 |
| 2-A | 요구사항 ID/acceptance/source registry | 사용자·프로젝트 격리, 변경 버전/출처, 코드/테스트 연결 |
| 2-B/C | Preview L1/L2, 피드백 부분 재생성, Intent Gap | 실제 브라우저 폼/버튼/전환, 모바일, 누락/불일치/검증 불가; 모의 인증 운영 승격 금지 |
| 3 | PDF 출처→OCR→이미지/VLM→영상/시각 QA를 분리 | 페이지/프레임/불확실성, 손상/대형/악성 미디어, GPU 직렬화 |
| 4 | 소스 수집→원본·계보·누출 차단→캐시/Hybrid RAG·기억 평가 | revision 증분, 라이선스/유사 중복/소스 계열 split, 비밀 차단, Recall@K/정정 반영 |
| 5 | 학습 재현성→Discovery→Evolution 실험→승인/롤백 | 독립 평가/숨김 최종셋 분리, 동일 조건 반복, 회귀/자원/실패 기록; 실제 학습 별도 승인 |
| 6 | 우선순위 큐→SSE→모델 상주·캐시·프로필 | 대화 TTFT/p50/p95, VRAM/모델 전환/작업 기아, 안전 지점 중단·재개 |
| 7 | AI Office·접근성·전체 E2E | 실제 heartbeat/승인/오류/연결 불명, 재연결/키보드/모션, 허위 퍼센트 없음 |

성능 상한·권한·상태 이벤트는 0~1단계부터 적용한다. 1-C 이후 실제 모델 품질 검증 전까지
Coding Agent 전체 완료라고 표시하지 않는다. PR 단위로 성공/실패 증거와 미측정을 첨부한다.

이번 PR의 롤백은 신규 내부 모듈/테스트/문서/QA 계획만 제거하는 revert PR이다.
실행 경로와 DB schema가 바뀌지 않아 DB 복원이 필요하지 않다. 임시 작업공간은 등록 정보가
일치할 때만 명시적으로 close한다. 정리 실패나 등록 정보 변조는 자동 강제 삭제로 우회하지 않는다.
후속 영속 작업 저장이 필요하면 별도 additive migration PR에서 이전 DB fixture·백업/복원·
구버전 호환성을 검증하고, 운영 DB 적용은 별도 승인을 받는다.
