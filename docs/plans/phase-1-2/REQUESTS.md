# 후속 요청 및 승인 기록

사용자 지시(2026-10-30): 필요한 요청은 별도 파일에 기록하고 승인된 현재 목표는 계속 진행한다.
이 파일 작성은 미승인 운영 변경·유료 API·개인 자료 사용·모델 설치/교체/학습·도구 권한 확대의 승인이 아니다.

| 항목 | 상태 | 이번 진행 |
|---|---|---|
| P1-04 자료 삭제 정책 | 사용자 1번 보관 처리 확정 | 원문·출처 유지, 검색/문맥 사용 차단 |
| P1-04 기존 UI 문구·API 연결 | 제시한 이미지 시안대로 사용자 승인 | 구성 유지, 보관/개인 보관함 문구·프로젝트 전달만 적용 |
| 운영 DB v5/v6 이행/배포 | 미승인·이번 목표 외 | 수행하지 않음. 합성 DB/CI만 사용 |
| 보관 본문 보존기한·영구 삭제·보관 해제 UI | 후속 설계·승인 필요 | 이번에는 추가하지 않음 |
| 새 모델·학습·유료 API·실제 개인 데이터 | 미승인 | 기존 허용 범위 안의 합성 평가만 진행 |

새 요청은 근거·선택지·승인 범위·현재 단계에 미치는 영향을 이 파일에 추가한다.
미승인 항목은 보류하고 독립적으로 가능한 승인 범위의 작업은 계속한다. 단계 완료/CI/병합 조건은 생략하지 않는다.

## P1-05 후속 요청

- 실제 PC/외부 도구 실행 권한 확대: 기존 기본 거부 유지. 새 도구 활성화는 도구별 허용 범위·샌드박스·반환 증거에 대한 별도 승인이 필요하다. 이번 단계는 등록된 도구의 현재 승인 검증과 제한된 dispatcher이며 기본 실행기는 없다.
- 운영 v6 이행: 승인 전에는 실행하지 않는다. 새 빈 승인 증거 테이블만 추가하며 운영 백업·오프라인 전환은 후속 승인 후 수행한다.

## P1-06 후속 정책

- 체크포인트 정리/보존기한: 기존 안전 제한을 재사용해 사용자별 최대 16개 작업·작업당 최대 128회 상태 전이를 유지한다. 자동 삭제/기록 덮어쓰기 없이 한도 도달 시 중단한다. 보관/정리 UI나 한도 확대는 후속 설계로 남긴다.
- 만료·철회·정책 변경으로 이전 계약 승인이 무효해지면 그 계약을 재사용하지 않는다. 새로운 승인 참조를 포함한 새 계약/작업을 만들고 이전 기록은 보존한다. 유효한 기존 계약의 pause/interrupted 재개만 현재 승인 확인 후 가능하다.

## P1-07 실행 경계

- GPU: 기존 GPU 측정/admission 수식을 재사용한다. 로컬 Ollama 프로세스의 물리적 VRAM hard cap이나 시스템 전체 격리는 제공하지 않는다. 실제 측정이 없거나 한도를 넘으면 실행/결과 사용을 차단한다. 전체 모델 unload, 설정 변경, 새 모델 설치는 수행하지 않는다.
- runtime callback: 신뢰된 서버가 등록한 취소 가능한 bounded I/O만 연결한다. 임의 동기 Python/호스트 명령·모델 제공 callback은 허용하지 않는다. 실제 로컬 위임/독립 runner 연결은 P2 승인 범위에서 진행한다.
- 불명확한 timeout/중단: 예약을 보수적으로 유지하고 자동 재실행하지 않는다. 기존 결과의 실제 완료 여부를 새 실행 성공으로 추정하지 않는다. 운영자의 실제 프로세스 정리·운영 전환은 별도 승인이다.

## P1-08 복원 활성화/공유 문서

- 새 companion은 기존 DB/artifacts와 공통 task checkpoint를 새 비활성 경로에 보존한다. 기존 Coding Agent candidate/worktree 전체, 외부 volume·모델·시크릿을 전부 포함한다고 주장하지 않는다.
- 오래된 DB 백업의 세션/Grant·승인 활성화, 현재 철회 기록과의 대조·정책 세대 변경·운영 경로 전환은 DEC-015 별도 승인 사항이다. 이번에는 복원본을 운영에 연결하지 않는다.
- 공유 5개 문서 PR #33 head1435db5e…는 여전히 draft/open이다. 자동 통합하지 않고 최신 구현/테스트·병합 수신은 이 진행 문서와 단계별 검토 파일에 기록한다. 공유 hub 게시/통합은 별도 범위로 남긴다.

## P2-A 제외 범위

- 기존 qa/bench_coding_models.py의 HostRunner/전체 모델 unload 및 qa/bench_coding_report.py의 성공·시간/VRAM 집계는 새 평가 경로에 연결하지 않는다. 파일 존재가 현재 실행 승인이라는 뜻은 아니다.
- PR #31의 자동 테스트 기본 활성화, context 재구성, 반환 전 예약 환불·새 토큰 정산은 통합하지 않는다. 최종 후보 실모델 성능이 검증됐다고 주장하지 않는다.
- 현재 선별한 부분은 type-only 오류 분류와 checkpoint 저장 실패 구분이다. 일반 모델/PC 도구 권한 및 실제 모델 설치/운영 설정은 확대하지 않는다.

## P2-B 평가 범위/복구

- 혼합10과제는 Role-AI 도메인의 합성 순수 함수5개와 소형 Python 프로젝트5개다. 실제 운영 앱 전체 기능 구현 능력을 이 점수만으로 일반화하지 않는다. 더 큰 다중 파일/서비스 과제 확장은 후속 실험이다.
- 현재 독립 grader는 정답이 들어 있지 않은 digest-pinned 공식 Python3.12-slim 이미지로 한정한다. 임의 앱/학습 데이터 이미지, host runner, 모델 자체 점수는 승인하지 않는다.
- cleanup 불명확 표식은 자동으로 삭제하지 않는다. 실제 운영 프로세스 정리/표식 해제/보고서 보존기한·정리 UI는 별도 승인이다. 보고서는 사용자별64개 한도 후 중단하며 자동 삭제하지 않는다.
- 실제 모델·Codex 토큰 성능은 아직 미측정이다. 합성 transport 테스트나 참조 코드 Docker 검사를 LLM 성능 수치로 사용하지 않는다.

## P2-C 로컬 실측 환경 요청

- 현재 WSL `serveradmin` 프로세스는 `/var/run/docker.sock`에 접근할 수 없다(2026-10-30 실제 `docker image ls` permission denied). docker 그룹에도 속하지 않으며 `sudo -n`은 비밀번호를 요구한다. 소켓 chmod/chown·그룹 추가·sudo 권한·서비스/Compose 설정을 변경하지 않았다.
- 기존 Windows Ollama localhost11434, 설정된 설치 모델 digest `d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`와 resident0을 읽기 확인했다. WSL loopback 연결은 실패하므로 선택적 고정 HTTP transport를 사용한다. 모델 설치/교체나 서버 네트워크 설정 변경은 없다.
- 필요한 조치: 사용자가 이미 승인한 별도 격리 개발 계정/런타임에서 기존 Docker 소켓 접근을 제공하거나, 개발 계정에 대한 접근 정책을 별도로 승인·적용한 뒤 합성 lab을 실행한다. 운영 서비스 권한은 필요하지 않다. Codex가 임의로 sudo·그룹/소켓 정책을 변경하지 않는다.
- 현재 진행: 단위/회귀 및 GitHub의 기존 disposable Docker job에서 실제 통합 실행을 검증한다. 로컬 실모델+Docker 전체 성능과 A/B 비교를 통과했다고 보고하지 않는다. 독립적으로 가능한 구현/검증은 계속한다.

## P2-E 완료에 필요한 실제 자료·환경 (2026-10-31 KST)

승인된 구현/단위/회귀·CI 준비는 계속 진행했고, 아래 항목이 없어 실제 A/B 완료·개선 판정·P2-E dev 병합만 보류한다. 승인이나 모델 점수를 임의로 만들지 않는다.

1. **격리 개발 Docker 접근 제공**: 현재 계정의 소켓 permission denied. 운영 Docker 소켓 chmod/chown·그룹/sudo/서비스 정책은 기존 도구 실행 권한 확대에 해당하므로 사용자 별도 승인 없이 변경하지 않는다(AGENTS.md의 운영/권한 보존 및 사용자 금지 범위). 이미 허용된 별도 개발 실행 환경에서 기존 이미지/endpoint 접근을 제공하거나 개발 계정 정책을 사용자 측에서 결정해야 한다. host fallback/새 runtime 설치는 수행하지 않았다.
2. **별도 Codex 직접 기준선**: 공개 10과제 패키지 `/home/serveradmin/role-ai-worktrees/docs/p2-e-public-baseline-3w6w09pq/README.md`. seed7, 구현 digest `cd550f91aaef47d59c7127d787d6e3abd9ce82ae0418fcc1b0662ab0e0153d05`. 공개 목표·stub·공개 테스트만 포함하며 정답/숨김 사례 없음. 새 독립 세션에 이 폴더만 제공하고 과제별 solution.py만 작성한다. 현재 세션은 oracle을 작성/열람했으므로 공정한 직접 기준선 답안을 작성하지 않는다. 새 AI agent/유료 API를 자동 실행하지 않는다. 파일이 바뀐 새 소스에서는 패키지를 새로 export해야 한다.
3. **공정성·사용량 원문**: Codex 직접 답안의 context 접근 범위, 동일 기기/조건, 실제 사용량을 검토할 원문/수신이 필요하다. 사용량 원문을 확보할 수 없으면 Codex 토큰·절감률은 null/미검증으로 유지한다. 문자열 길이·예산·일부 계획 응답119 backend tokens를 전체 절감률로 대신하지 않는다. raw 문맥·원문 사용량·개인 자료는 GitHub에 올리지 않는다.

### 준비된 실행 경로

P2-E 브랜치에서 기존 가상환경을 사용한다. 아래 명령은 Docker 권한과 기존 설치 이미지가 제공된 **격리 개발 환경**에서만 실행한다. 모델/이미지 신규 설치 명령이 아니며 사전검사를 통과하지 못하면 중단한다.

```bash
python -m role_ai.task_delegation_lab --task budget-check --seed 7 --model qwen3.5:4b-q4_K_M --model-digest d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04 --image python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 --windows-loopback
python -m role_ai.task_baseline --public-root /home/serveradmin/role-ai-worktrees/docs/p2-e-public-baseline-3w6w09pq --task budget-check
```

각 과제마다 같은 seed/image/예산/기기에서 반복하고 실패·중단도 보존한다. baseline 명령은 후보를 독립 채점할 뿐 작성 주체·Codex 토큰을 추정하지 않는다. `--producer-receipt` 없이는 실제 Docker 통과라도 unverified_submission으로 실제 A/B 집계에서 제외한다. 직접 기준선 제출물은 별도 context 검토 수신(format role-ai-codex-baseline-context-v1, task_id/candidate_sha256/public_manifest_sha256/context_review_sha256)과 결합해야 실제 작성 근거를 기록할 수 있다. checksum은 인증/실행 권한이 아니다.

`python -m role_ai.task_comparison compare --image <동일 digest> --metrics <private metrics.json ...>`는 실제 paired10과제와 검토/CI·사용량 근거가 부족하면 exit2로 미완료/불충분을 보고한다. `--regression-receipt`는 구현 digest/head SHA 및 최종6 CI job 수신, `--protocol-receipt`는 public_manifest/context_review/hardware_conditions 원문 hash를 받는다. 이 자료가 있다고만 쓰지 말고 실제 원문을 확인해야 한다.

현재 실제 실행한 비교 명령은 A0/B0/coverage false/개선 false/절감률 null, exit2로 올바르게 미완료를 반환했다. P2-E는 실측·검증 근거 부족 상태로 Draft PR을 유지하며, dev 병합 및 Phase 3 착수를 완료로 보고하지 않는다.
