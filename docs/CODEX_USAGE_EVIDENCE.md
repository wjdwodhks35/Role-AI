# Codex 사용량 원문 검증 — 로컬·읽기 전용

`tools/codex/usage_evidence.py`는 기존 `codex exec --json` 원문과 종료코드를 검토하는 표준 라이브러리 도구다. 모델 호출·로그인·계정/확장/시스템 설정 변경·로그 자동 수집·외부 전송을 수행하지 않는다. VS Code 작업 방식은 유지한다.

공식 CLI 문서는 JSONL의 thread.started/turn.started/item.*/turn.completed/turn.failed/error 및 turn.completed.usage 예시를 제공한다. 현재 도구는 실제 검증한 `codex-cli 0.164.0` 형식만 받으며 다른 client/version/schema는 재검토 전 거부한다. [OpenAI 공식 non-interactive 문서](https://learn.chatgpt.com/docs/non-interactive-mode#make-output-machine-readable)

## 사용

```bash
python tools/codex/usage_evidence.py --root /path/to/private-evidence --manifest manifest.json
```

원문은 `.codex-local/logs/` 등 명시한 비공개 root 아래 보관한다. 압축/가림/일부만 복사한 출력을 원래 usage event로 대신하지 않는다. 새 Codex 실행은 별도 승인 범위와 기존 안전 정책을 확인한 뒤 운영자가 준비하며 이 도구는 실행기를 제공하지 않는다. Windows/WSL에서는 동일한 Python 명령을 쓰고 ACL·기존 비공개 보관 정책을 유지한다.

manifest는 `role-ai-codex-usage-v1`, `client_version`, `protocol_sha256`, `approach`, `expected_sessions`, `sessions`를 가진다. `approach=codex_direct`의 기대 phase는 definition/execution/review, `local_delegate`는 definition/review다. B의 로컬 실행 중 Codex 세션이 없다는 사실과 그 외 수동 개입이 없다는 사실은 운영자가 별도 확인해야 한다.

```json
{
  "format": "role-ai-codex-usage-v1",
  "client_version": "codex-cli 0.164.0",
  "protocol_sha256": "실제 비용범위 문서의 64자리 SHA256으로 교체",
  "approach": "codex_direct",
  "expected_sessions": {"definition": 1, "execution": 1, "review": 1},
  "sessions": []
}
```

각 session은 `phase`, `path`, `sha256`, `exit_code`를 기록한다. 경로는 root 내부의 canonical ASCII 상대 `.jsonl` 경로다. 예: `.codex-local/logs/execution-01.jsonl`. 해시는 실제 원문 바이트의 SHA256, 종료코드는 실제 프로세스 결과다. 없는 종료코드는 null로 남긴다. 위 예시는 identity 교체와 session 추가가 필요한 미완성 템플릿이다.

phase마다 사전에 기대한 세션 수를 기록하고 빠진 로그를 임의 제외해 분모를 줄이지 않는다. 이 버전은 독립 fresh 세션당 정확히1회 turn이 있는 원문만 받는다. resume/cumulative stream은 해석을 검증하기 전 지원하지 않는다. 동일 원문/경로/thread의 이중 집계, SHA 불일치, 중복 JSON key, 잘린/비정상 이벤트, 미래 schema, 음수/문자열/bool 토큰을 거부한다. 원문4MiB/16384event, session128개 및 경로 제한이 있다. symlink/junction·부모 이탈·UNC·Windows device/ADS 경로는 받지 않는다. 같은 uid의 악성 host 프로세스와 완전한 OS 격리를 보장하지 않는다.

## 결과 해석

- `reported_category_subtotals`: 제공된 원문의 관측 소계. 미제공 optional category는 null이다. 원문에 명시된0은0으로 보존한다.
- `declared_phase_category_totals`: 기대한 phase 수·각 종료코드/원문이 완전할 때만 생성한다. 누락/실패/unknown이면 null과 exit2다. 완전한 입력 분석은 exit0이며 작업 성공·모델 품질을 뜻하지 않는다.
- input/cached_input/cache_write_input/output/reasoning_output을 각각 보존하며 캐시/추론을 중복 합산한 총량을 만들지 않는다.
- source_authenticated/model_identity_verified/full_cost_scope_verified는 false다. 해시는 provider 인증이나 실행 권한이 아니다. 선언된 phase coverage는 숨겨진 추가 개입·같은 모델/조건·전체 비용 구간의 증명이 아니므로 별도 원문/프로토콜 검토가 필요하다.
- token_reduction은 null이다. 이 도구만으로 토큰 절감·모델/과제 성공·A/B 개선을 인증하지 않는다. 임의 값을 MetricRecord에 자동 주입하지 않는다.
- 출력은 enum·숫자·해시·고정 오류 코드만 포함한다. 모델 본문·명령·서버 식별정보·원문 오류를 복사하지 않는다. 원본 파일은 수정/삭제하지 않는다.

## 기존 실제 자료로 검증한 범위

원래 P2-E A10개 세션을 hash 대조한 별도 private 복사본에서 생성 범주 input130510/cached24064/cache_write0/output2261/reasoning390을 재현했다. definition/review20개 로그가 없어 완전 합계null/exit2로 보고했다. 새 모델 호출0, 기존 원문/성능 수신은 보존했다. 이번 inventory는 사후 자료 검토이며 과거 실험을 사전 등록된 공통 비용 비교로 바꾸지 않는다.
