# 기준선과 검증 범위

기준일 2026-10-28. 기반 commit `3e021e4da7dd25e39d37251b4d79408eb654b03e`.
개발 경로는 `/home/jjw/projects/Role-AI-coding-workspace`, 브랜치는
`feature/coding-workspace-foundation`이다. 기존 체크아웃의 미추적 통합 기획서는 보존한다.

## 검증 구분

- 신규 검증: 실제 임시 Git 저장소/worktree에서 파일 수정과 diff 적용을 검사한다.
  Git 실행은 실제지만 제출된 Python/테스트/모델 코드는 실행하지 않는다.
- 기존 pytest: 임시 SQLite·합성 fixture·HTTP mock이 중심이다. 전체 회귀 통과는 운영 동작이나
  모델 성능 향상의 증거가 아니다.
- Node 검사: 인증 응답·비밀번호 입력·Enter/Shift+Enter·IME 및 구문 검사이며 실브라우저 E2E가 아니다.
- 의존성은 `/tmp/role-ai-coding-venv`에 격리 설치한다. 운영 패키지·서비스 설정을 변경하지 않는다.
- 실제 모델 호출/학습/다운로드/유료 API/운영 Docker·DB·Gateway 변경은 수행하지 않는다.

## 후속 실측

| 지표 | 현재 상태 | 측정 방법 |
|---|---|---|
| 코딩 과제 성공·수정·복구율 | 미측정 | runner/orchestrator 구현 후 동일한 독립 과제와 base SHA로 반복 |
| TTFT·총 지연 p50/p95·모델 재로드 | 미측정 | 실제 로컬 모델, cold/warm 분리, 입력/모델/옵션 고정 |
| VRAM/RAM/CPU·저장량 | 미측정 | 사용자 GPU 호스트에서 작업 전후·피크와 환경 기록 |
| 두 Gateway·Windows 제어 | 미검증 | 별도 승인 경계에서 설정·실행 결과 확인 |
| 학습 품질·승격 | 미검증 | 승인된 실제 GPU 실험, 독립 평가, 기존 모델 대비 비교·롤백 |

`baseline_manifest`의 소스 해시와 `performance.state=not_measured`는 실제 운영 이미지나
실모델 벤치마크를 대신하지 않는다. 최종 검증 수치는 이 문서의 후속 결과 절에 기록한다.
