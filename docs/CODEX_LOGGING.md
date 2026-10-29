# Codex 명령 로그 절약

VS Code Codex 채팅과 터미널 사용 방식은 유지한다. 필요한 긴 명령만 아래 래퍼로 실행한다.
자동 가로채기, Codex 내부 API, 모델/API 호출, 전역 설정 변경은 없다.
[공식 AGENTS.md 지침](https://learn.chatgpt.com/docs/agent-configuration/agents-md)에 따라 저장소 사용법을 안내한다.

```sh
python tools/codex/run_logged.py -- python -m pytest -q tests/test_task_contracts.py
python tools/codex/run_logged.py -- python -m unittest discover -s tools/codex/tests -v
python tools/codex/run_logged.py --preview-read-bytes 4194304 --display-chars 12000 -- python build.py
```

명령은 인수 배열로 실행하며 shell을 켜거나 작업 디렉터리/환경/권한을 바꾸지 않는다.
파이프/리다이렉션/셸 내장 명령은 자동 해석하지 않는다. 인수에 비밀값을 넣지 않는다.
Windows는 Python 3.12 이상과 기본 PowerShell/icacls, WSL/Linux는 Python 3.12 이상을 사용한다.
추가 Python 패키지는 필요 없다.

## 보존과 실패 처리

- unittest/pytest 성공 진행 및 다운로드 진행의 알려진 패턴만 요약한다. 반복 성공 줄은 개수로 대체하며, 줄어들지 않으면 그대로 둔다.
- 알 수 없는 출력, JSON/NDJSON, diff/source는 의미 요약을 하지 않는다. 오류/경고/예외와 traceback을 성공 줄로 취급하지 않는다.
- stdout/stderr는 각각 유지한다. 병렬 스트림 간 원래의 시간 순서는 복원하지 않는다. 짧은 일반 출력은 머리말/꼬리말 없이 반환한다.
- 원본 바이트는 `.codex-local/logs/run-*/stdout.log`, `stderr.log`에 저장한다. `result.json`에 종료 코드·시간·바이트 수·오류 상태를 보관한다. 명령 인수/환경 변수는 기록하지 않는다.
- 원본 저장과 미리보기 한도는 별개다. 스트림당 기본 4 MiB만 미리보기로 읽고, 초과하면 head/tail과 `PREVIEW CAP`을 보여준다. 원본은 잘라내지 않는다. `--max-log-bytes`는 PR #23 호환용 별칭이며 **원본 저장 한도가 아니다**.
- 화면은 스트림당 기본 12,000자로 제한한다. `DISPLAY CAP`이면 첫/마지막 진단 주변과 head/tail을 우선한다. 긴 traceback/중간 오류가 모두 표시된다고 보장하지 않는다. 원본의 필요한 부분을 로컬에서 확인한 뒤 진단한다. 한도는 본문 기준이며 안내 문구/Windows CRLF 바이트는 추가될 수 있다.
- 압축 예외는 경고와 함께 마스킹한 원문 미리보기로 대체한다. 마스킹까지 실패하면 원문을 터미널에 내보내지 않는다. 비공개 원본은 유지한다.
- 저장 실패 시 파이프를 계속 비워 교착을 피하고 불완전 로그 경고/메타데이터를 남긴다. 실행한 자식의 종료 코드는 그대로 반환한다. 자식 코드 0이라도 래퍼 경고가 있으면 검증 완료로 판단하지 않는다. 시작 전 래퍼 실패는 125, 명령 시작 불가는 127, POSIX signal은 128+signal이다.
- 이 도구는 타임아웃/프로세스 트리 격리 도구가 아니다. 임의 명령의 승인 범위를 넓히지 않는다.

## 보안과 보관 한계

원본은 민감정보를 포함할 수 있다. `.codex-local/` 전체를 Git에서 제외하고 POSIX 디렉터리 0700/파일 0600,
Windows는 해당 로그 디렉터리만 현재 사용자 ACL로 제한한다. 기존 비어 있지 않은 일반 디렉터리와 symlink/junction 경로는 거부한다.
공개 미리보기는 일반적인 key/token/password, Bearer/JWT, PEM 비밀키, 이메일, 한국 휴대전화, HTTP URL 자격 증명을 마스킹한다.
이는 완전한 개인정보/비밀 탐지가 아니다. 사용자 이름/임의 형식 비밀값 등이 남을 수 있으므로 민감 출력을 예상하는 명령은 실행하지 않는다.
원본을 채팅·PR·artifact·외부 모델에 업로드하지 않는다. 임의 `--log-root`는 별도로 보호/ignore된 전용 로컬 경로만 사용한다.

소유 표식이 있는 로그 루트의 완료된 run만 다음 실행 전 7일/128 MiB 보관 예산에 따라 정리한다.
진행 중 run은 삭제하지 않는다. 실행 중 원본 저장에는 강제 한도가 없으므로 폭주 출력은 디스크를 소진할 수 있다.
저장 장애 시 원본 전체 보존은 불가능하다. 장기 보존할 합성 진단 자료는 만료 전에 별도 비공개 경로로 복사한다.
래퍼를 쓰지 않으면 기존 방식으로 즉시 복귀한다. 변경은 앱·DB·서비스·Task Contract에 연결되지 않는다.

## 재현 가능한 측정

```sh
python -m unittest discover -s tools/codex/tests -v
python tools/codex/measure_logs.py
```

`measure_logs.py`는 같은 합성 명령을 직접/래퍼 방식으로 실행하고 stdout+stderr 안내 문구를 포함해 비교한다.
오류 파일/행/유형과 종료 코드를 함께 검증한다. 숫자는 **문자 수/UTF-8 바이트 수**이며 실제 Codex 토큰 수가 아니다.
토크나이저나 Codex 호출은 사용하지 않는다. VS Code의 실제 입력 토큰·캐시·추론 정확도/성공률은 측정하지 않았다.
PR #23의 과거 Codex 벤치마크를 현재 결과로 재사용하지 않는다.

WSL Python 3.12 측정:

| 동일 합성 작업 | 원래 문자 수 | 래퍼 문자 수 | 종료 코드 | 검증한 핵심 정보 |
|---|---:|---:|---:|---|
| 짧은 성공 | 3 | 3 | 0 | 출력 바이트 동일 |
| 성공 2,000줄 | 60,018 | 103 | 0 | 2,000 tests passed |
| 성공 후 실패 | 60,102 | 188 | 37 | traceback, example.py, line 42, ValueError |

run 이름 길이는 고정이지만 OS 개행에 따라 바이트 수는 달라진다. 실제 작업에서 이미 `pytest -q`가 짧게 출력하면 추가 절감은 작다.

## PR #23 선별 범위와 10 Pass

기준 dev: `cda418736d4c031a970af96d148f436ad0462326`.
PR #23 `fc4a63101fd76ef55280dc96e18c1ee163b86026`의 `tools/codex/run_logged.py`와 테스트만 선별 재사용/수정했다.
Windows launcher/서비스 시작/환경 변경/프로젝트 맵/과거 벤치마크는 제외했다. PR #31 코드는 가져오지 않았다.
P1-02 데이터 모델·Migration·기존 미커밋 자료는 변경하지 않는다.

`06_10회_재검토_체크리스트.md`의 서로 다른 관점에 따른 자체 검토이며 독립 외부 평가가 아니다.

| Pass | 새 발견과 반영 | 잔여 위험 |
|---:|---|---|
| 1 요구사항 | PR #23 원본 cap은 중간 바이트를 버림 → 원본 전체 저장/미리보기 cap 분리 | 디스크 장애 시 불완전 |
| 2 논리 | 짧은 출력에 헤더가 붙음 → 일반 짧은 출력 그대로, 줄어들 때만 요약 | 마스킹 필요 시 안내 증가 |
| 3 사실 | 내부 Codex 자동 후킹 근거 없음 → 공식 AGENTS+명시적 CLI | 지침 준수는 모델 행동에 의존 |
| 4 현실성 | Windows Python에서 실제 테스트, stdlib만 사용 | Python/PowerShell/icacls 필요 |
| 5 아키텍처 | 보조 도구/별도 CI로 앱·P1-02와 파일 분리 | PR #23 도구 중복, #24 ignore/#26 AGENTS 수동 조정 필요 |
| 6 보안/복구 | quoted secret/PEM 마스킹·private ACL·Git ignore 추가 | 임의 비밀 패턴 탐지 보장 없음 |
| 7 자원 | 원본 읽기와 화면 크기 제한, 실제 문자/바이트 측정 | 실행 중 원본 디스크는 무제한 |
| 8 실패 예측 | 압축/저장 실패 주입, 종료 코드·원본 확인 | 원본 부족 시 재현 필요 |
| 9 반례 | 큰 unknown/traceback는 단순 요약 불가 → 생략 표시와 원본 확인 지침 | 화면만으로 진단하면 누락 가능 |
| 10 통합 | 전용 회귀·기존 Coding/Task Contract 검사와 CI | 실제 Codex 토큰 절감률 미검증 |

진행 판단: 개발 보조 도구로 사용 가능. 보안 마스킹/디스크 한계와 생략 표식을 지침에 따라 다룬다.

로컬 검증 결과: WSL Python 3.12 전용 23개 중 22 통과/Windows ACL 1 skip,
Windows Python 3.13 전용 23개 중 21 통과/POSIX 전용 2 skip.
기존 Role-AI 전체 회귀는 648 passed, 기존 Starlette/httpx deprecation warning 1건이며 실패는 없다.
Windows 합성 비교는 short 4→4, long_success 62,019→107, failure 62,105→194 문자였다.
별도 CI는 Ubuntu/Windows Python 3.12를 실행한다. PR의 CI 결과는 PR checks에서 확인한다.
