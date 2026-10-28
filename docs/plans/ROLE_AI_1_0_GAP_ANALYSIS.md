# Role-AI 1.0 현황과 첫 구현 범위

분석 기준: 2026-10-28, GitHub/로컬 dev `3e021e4da7dd25e39d37251b4d79408eb654b03e`.
사용자가 제공한 최종 통합 기획서를 기준으로 실제 소스를 확인했다. 원본 기획서는 기존
체크아웃의 미추적 파일로 보존하며 이 PR에 일괄 반입하지 않는다.

| 기능 | dev 기준 | 코드 근거 / 남은 작업 |
|---|---|---|
| API·SQLite·인증·CSRF | 구현됨 | `main.py`, `security.py`, `migrations.py`; 기존 DB v3 유지 |
| 기억 | 부분 구현 | `memory.py`, `models.py`; 후보/확인/정정/삭제/만료/이력, 개인화 품질 실측 필요 |
| OpenClaw 대화 | 구현됨, 실환경 미검증 | `assistant.py`; owner/loopback/token/전체 도구 차단, Windows Gateway 별도 확인 |
| 6개 역할 | 정의만 구현 | `agent_registry.py`; 실행/스케줄러 비활성 |
| Python 테스트 | 제한적 구현, 실환경 미검증 | `code_execution.py`; 승인된 코드+unittest, 일회성 Docker |
| Coding Agent | 미구현 | 저장소 수정→실제 테스트→실패 복구→PR 후보 루프 없음 |
| 요구사항 분석 | 부분 구현 | `request_analysis.py`, `requirements_eval.py`; 역할 추천과 평가, 추적 레지스트리 없음 |
| Preview Lab / Intent Gap | 미구현 | 분석 preview는 작동 시제품 생성 기능이 아님 |
| 문서/PDF RAG | 부분 구현 | `documents.py`, `pdf_extract.py`, `semantic.py`; OCR/캐시/Hybrid 없음 |
| 이미지/설계도/영상 | 미구현 | VLM/핵심 프레임/시각 QA 없음 |
| 연구 수집·전처리 | 부분 구현 | `collection.py`, `huggingface.py`, `artifacts.py`; HF JSONL 제한 표본 중심 |
| 학습/평가/롤백 | 구현 기반, 실GPU 미검증 | `training.py`, `rlaif.py`, `quality.py`, `lifecycle.py` |
| Discovery/Evolution Lab | 미구현 | 병목 기반 탐색·격리 비교 실험·승격 흐름 필요 |
| Adaptive Runtime | 부분 구현 | `gpu_resources.py`, `workers.py`; 중앙 우선순위 큐/스트리밍 없음 |
| AI Office | 미구현 | 역할별 실제 이벤트 연결 후 마지막 UI 단계 |

## 이번 PR

`coding_tasks.py`와 `coding_workspace.py`에 내부 작업 계약과 격리 파일 변경 기반을 추가한다.
소유자가 관리하는 저장소 허용 목록에서 경로를 선택하고, 정확한 commit/사용자/프로젝트/
파일 범위/한도 및 변경 내용에 대한 Permission Gate 결정을 해시에 연결한다.
해시는 승인 인증 수단이 아니며 외부 요청 또는 모델이 만든 해시만으로 권한을 부여하면 안 된다.

실제 Git worktree를 생성하되 `--no-checkout` 후 Git blob을 직접 읽어 텍스트 파일을 구성한다.
hook/filter 실행을 피하고, 원본 dirty/untracked 파일과 브랜치 HEAD를 보존한다.
허용된 파일 생성/수정/삭제와 적용 가능한 diff를 제공한다. 실패한 부분 결과는 failed로
보존하고 재적용을 차단한다. 운영 DB·인증·Gateway·Compose는 변경하지 않는다.

이 코드는 아직 HTTP/채팅/모델 도구에 연결되지 않았다. 소형 UTF-8 저장소만 지원하며
파일 256개, 파일당 256KiB, 전체 2MiB 제한이다. binary/symlink/submodule/추적된 비밀 경로는 거부한다.
테스트 실행·오류 복구·모델 호출·push/PR 생성은 다음 PR의 작업이다.

## 의존성과 위험

권한 계약 → 격리 파일 변경 → 별도 실행 sandbox → 실패 복구 → PR 후보 → Preview Lab 순서다.
worktree 자체는 실행 sandbox가 아니다. 이후 runner에는 이 사유 작업 디렉터리·Git 메타데이터·
호스트 socket을 노출하지 않고 정제된 복사본만 전달해야 한다. 원본 테스트도 신뢰하지 않는다.
운영/Windows/GPU 기준선은 아직 측정하지 않았으며 소스 검증과 구분한다.
