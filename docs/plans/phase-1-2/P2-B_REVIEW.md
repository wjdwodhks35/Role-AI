# P2-B 혼합 과제와 독립 채점

시작 dev `28629cd70aaf7672a227b7b9d4bf2877da80c9d0`, feature/p2-b-independent-grading.
P2-A가 CI/병합 완료된 뒤 시작했다. 운영 DB/실제 개인 데이터/실모델은 사용하지 않는다.

## 과제/채점 경계

- Role-AI 합성5: 정확 버전 자료 가시성, 기억 revision, 대화 바인딩, 예산 적격성, 안전한 성공 로그 fold.
- 소형 프로젝트5: 안정 dedup, interval merge, duration parser, stable topology, pagination.
- 과제별 공개2/숨김4, 총60개 입력 관측. 고정 seed/목표/입력·oracle source의 digest로 재현한다.
- 모델 공개 뷰는 stub/public unittest/goal/hash만 반환한다. hidden 기대값·oracle 구현을 prompt/후보 실행 컨테이너로 보내지 않는다.
- 후보에게 전달하는 trusted unittest는 함수 입력과 실제 결과/입력 변경을 관측하는 코드다. 정답 대조는 컨테이너 밖에서 수행한다.
- 정확한 단일 관측 vector·JSON 의미/타입·입력 보존·모든 사례 일치를 검사한다. passed/finished 선언·자기 점수/종료0만으로 성공 판정하지 않는다.

## 구현·안전

- P1-01~07과 호환: 현재 DB Task 승인/소유권/정책·정확 Repo revision·sandbox_run 권한을 전후 검증한다.
- criterion에 benchmark_sha256을 고정해 task/seed 변경을 차단한다. 후보 path는 선언된 경로, 수정은 propose 권한·기존 TaskCreate 파일 제약·기존+새 bytes patch budget을 검사한다.
- 기존 CodeExecutor의 Python/unittest 고정 템플릿, pin image·non-root·network-off·read-only·CPU/RAM/pid·stdout/timeout/cleanup 제한 재사용. host fallback/새 shell tool/API 실행 경로 없음.
- 실제 grader는 공식 Python3.12-slim digest 이미지로 한정한다. probe는 role_ai package가 이미지에 없음을 후보 import 전에 확인한다. 앱 이미지에 정답 모듈이 설치되어 있을 위험을 차단한다.
- private executor의 timeout은 계약/기존 설정 중 더 짧은 값으로 한정하고 전역 Settings를 바꾸지 않는다. 전체 controller 예산은 후속 P2-C common Runtime과 결합한다.
- 보고서/candidate/raw stdout은 인증 후 private root·사용자 namespace·서버 UUID에 새로 저장한다. report checksum/atomic/fsync 및 candidate/raw fsync. 기존 파일 덮어쓰기·자동 삭제 없음, 사용자64개 한도.
- 실패/중단/권한 철회도 보고서를 보존하며 success로 바꾸지 않는다. 미확인 통과 수는 None, test_count는 검증된 의미 관측 수(미검증이면0), Codex tokens=None. test double/offline Docker 증거를 구분한다.
- cleanup 불명확이면 기존 runner-cleanup-required 표식을 생성·보존해 다음 호출을 차단한다. 자동 삭제/재시도/전체 모델 unload 없음.
- 새 grader/source/CodeExecutor 및 실행 설정도 승인 environment 구현 지문에 결합한다. 기존 계약 digest나 API/인증·격리를 약화하지 않는다.

## 실제 검증

- 초기 unit23 PASS, 기존 CodeExecutor/Task 승인 포함69 PASS, 계약 호환 포함132 PASS, 최종 unit25 PASS.
- unit의 fake transport 결과는 실제 실행/LLM 성능으로 표현하지 않는다. oracle 공개 답은 별도 명세 표로 대조한다.
- 실제 컨테이너 증거는 qa/check_independent_grader.py로 기존 code-execution CI에 추가했다. 10 reference/60 관측 및 자기 점수·중복 marker·timeout 거부를 실행하고 sanitized receipt만 CI artifact로 남긴다.
- 기존 CI 단계/테스트를 제거하거나 완화하지 않았다. 실제 모델/유료 API/운영 서버 테스트는 없다. 로컬은 socket·Docker/Ollama/GPU 차단된 합성 환경이다.
- 최종 전체 QA/소스 지문/실제 Docker CI 결과는 확인 후 기록한다.

## 10 Pass 자체 검토

06_10회_재검토_체크리스트 적용, 독립 모델 평가 아님.

| Pass | 새 발견·조치 | 잔여 위험 |
|---:|---|---|
| 1 요구 | 5개 고정 과제만으로 부족 → 합성 Role-AI5/소형5 혼합10, 공개2/숨김4 | 단일 모듈 함수 중심, 전체 앱 능력으로 일반화 금지 |
| 2 모순 | hidden 정답을 후보 테스트 파일에 넣으면 유출 → 입력/관측만 inside, oracle outside | 과제 코드 공개 자체가 엄격한 전세계 비밀/훈련 누출 방어는 아님 |
| 3 사실 | test double 통과를 실모델 성공으로 오인 → scope 명시·Codex tokens=None | 실제 모델 결과는 P2-C/E |
| 4 현실성 | 환경 추가 설치/host 대체 위험 → 기존 CI Python Docker 이미지/runner 재사용 | 실제 로컬 환경 가용성은 다음 단계에서 확인 |
| 5 구조 | 평가가 계약 외 코드를 실행할 위험 → 현재 승인+repo pin+path/criterion/propose/budget 결합 | Python 서버 callback은 신뢰된 구현 경계 |
| 6 보안/복구 | 모델 자기 점수·중복 stdout 위조 → 외부 oracle/단일 vector·strict evidence, cleanup 차단 | 악성 OS/신뢰된 Docker 이미지 자체 공격은 범위 밖 |
| 7 자원 | 무제한 보고서/긴 출력·시간 → code16k/기존 stdout·CPU/RAM·짧은 timeout/64개 quota | 완료 기록 정리/보존기한 별도 정책 |
| 8 예측 | timeout/철회/cleanup가 성공으로 덮임 → interrupted/blocked·감사 원문 보존·미측정 None | 이미 실행된 효과 소급 회수 불가 |
| 9 반론 | 앱 이미지에 oracle가 있으면 cheating 가능 → 공식 pinned base+package absence 검사 | custom image는 별도 검증 필요 |
| 10 통합 | mock만으로 프로토콜 오류 놓칠 수 있음 → 기존 CI에 실제 Docker grader 검사 추가 | CI 전부/merge 확인 전 미완료 |

롤백은 새 grader/fixture 호출 중단 및 코드 revert다. DB schema v6/기존 데이터 미변경, artifact/quarantine를 지우거나 pending 작업을 replay하지 않는다.

## 최종 로컬 결과

- 전체 Excel QA445 시나리오·863회 PASS, ordered=true/exit0. 기존436행 보존, 신규9개 시나리오·25회.
- `/tmp/role-ai-p2b-verified-ohasaypq` Python/QA129개 지문 일치. 실제 socket·Docker/Ollama/GPU 차단·별도 HOME/TMPDIR/합성 DB.
- Ruff 전체/diff 통과, 기존 Starlette/httpx warning1. 기존 CodeExecutor/계약 포함132 PASS, 최종 독립 채점 unit25 PASS.
- 실제 Docker oracle 검사는 PR의 추가 CI 단계 결과로만 확인한다. 이 로컬 숫자는 실모델/실제 토큰·VRAM 측정이 아니다.
