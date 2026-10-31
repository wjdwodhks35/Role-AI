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

## 추천안 실행 후 소켓 연결 10 Pass

|관점|새 발견/수정|잔여 위험|
|---|---|---|
|1 요구|A/B 모두 동일한 명시적 socket 인수 지원|개발 daemon 아직 미준비|
|2 모순|기존 CLI 고정 endpoint 공백 해결|기존 기본값과 새 explicit 정책을 구분해야 함|
|3 사실|Rootless tool 부재, subuid/cgroup/userns 존재를 직접 확인|metadata는 실제 container 집행 증거와 다름|
|4 실행|관리자 패키지 dry-run 신규2/upgrade0, 구체적 준비안 작성|실제 설치는 관리자 인증 필요|
|5 구조|기존 CodeExecutor preflight 재사용·모델 도구에 socket 미노출|rootless 실제 호환성 확인 필요|
|6 보안|private owner/정규 Unix path 및 inode 승인 바인딩|동일 uid의 악성 host 프로세스까지 완전 격리하지 않음|
|7 자원|메모리/swap/CPU/pids/seccomp 미지원 및 잘못된 info 거부|실제 자원 검증 후 정식 평가 가능|
|8 결과|실패 시 모델0 유지·기존 token null 유지|실제 paired 자료 없음|
|9 반론|소켓 교체 시 이전 승인 무효화 테스트|namespace/물리 장치 공유 경쟁은 별도 환경 검증|
|10 통합|기존 계약 digest/API/운영 endpoint·데이터 유지|새 구현 hash의 공개 패키지로 새 기준선 필요|

공식 Codex usage 이벤트가 있다는 사실과 현재 원문을 확보했다는 주장을 분리했다. 관리자 준비/공정한 기준선/실제 사용량이 필요하다는 상태는 유지한다.

소켓 후속 최종 검증: QA486시나리오/952실행 통과. snapshot _qkvn9fx와 app/QA/tests SHA256 일치, Ruff/diff 통과. `sudo -n -l`은 password required이므로 관리자 인증이 필요한 패키지 준비는 수행하지 않았다. 소켓·서비스 권한을 우회하지 않는다. 새 head CI는 PR 수신으로 기록한다.

## 실제 Rootless/B10 실행 후 10 Pass (2026-10-31 KST)

|관점|새 발견/조치|잔여 위험|
|---|---|---|
|1 요구|사용자 설치 후 B10과제 실행·실패 포함 집계|A0으로 비교 미완료|
|2 정책|Codex 미측정 허용을 원문 지시와 재대조·잘못된 추가 승인 요구 정정|미측정에서 절감률 추정 금지|
|3 사실|4/10성공, 채점7건39/42, 미채점3건 구분|단일 시도·소형 합성 과제 일반화 불가|
|4 실행|QA fixture의 endpoint 덮어쓰기 발견·명시 전달·실제 Docker 통과|daemon 재시작 시 재검증 필요|
|5 구조|기존 계약/독립 grader/backup·합성 DB 재사용|서로 다른 측정 시간 경계 혼합 금지|
|6 보안|rootless 실제 nonroot/readonly/networknone/no secrets·승인 유지|동일 host 사용자·GPU 공유 완전 격리 아님|
|7 자원|memory/swap/CPU/pids kernel 값 직접 확인·GPUpeak5447|VRAM 물리 hard cap 아님|
|8 실패|승인 시각 역행 관측·fail-closed 보존·동일 과제 재시도 없음|호스트 clock 원인 해소 미검증|
|9 반론|점수 부족/예산 제한/미채점을 성공으로 바꾸지 않음|low B quality를 토큰 절약으로 정당화 불가|
|10 재통합|원문 private 보관·A 독립성 요청·Draft 유지|최종 head CI와 A 비교 충족 전 병합 금지|

추가 독립 AI 리뷰가 아닌 10관점 자체 재검토다. core source digest f028…4061 유지, 이번 코드 변경은 QA socket 전달뿐이다. 최초 안전 검사 실패와 후속 통과를 함께 기록한다.

## 독립 A 및 실제 비교 후 최종 10 Pass

|관점|새 발견/조치|잔여 위험|
|---|---|---|
|1 요구|사용자 명시 승인 후 fresh A10생성·채점, A/B20기록 비교|목표 토큰 절감은 미입증|
|2 모순|A100%/B40%를 기존 비교기가 regression으로 판정·변경 안 함|평가 시스템 완료를 모델 개선 달성으로 오인 금지|
|3 사실|A실제 usage10개,전체 비용 토큰은 null|A 정확한 provider 모델ID 미확인|
|4 실행|umask077 첫10채점 실패 원문 보존·같은 후보만022 재채점|runner 엄격 umask 호환성 후속 검토|
|5 구조|기존 frozen source/grader/contract/digest 유지|보조 평가 스크립트는 private 원문과 함께 보관|
|6 보안|모델도구 실행0/부모대화·oracle 전달0/후보host 실행0 확인|CLI built-in 지침 제거·완전 OS read 격리를 주장하지 않음|
|7 자원|동일 host/runtime/image/runnerlimits,모델 예산 차이 명시|provider/hardware/context 동등성 미입증|
|8 실패|초기환경 오류·B차단/실패/예산·미채점 모두 보존|단일시도·순차B→A,인과적 우열 일반화 불가|
|9 반론|참조/가짜모델/사전smoke 제외,부정적 결과도 게재|작은 과제10개·비무작위 순서|
|10 통합|기존기능 CI·리뷰 확인 후 평가도구/부정적 보고만 dev 반영|운영 위임 활성화·모델 교체·Phase3 범위 밖|

모델 재호출/답안 수정/숨김 피드백 없이 동일 A 답안만 재채점했다. metadata coverage=true와 비교 한계를 함께 표시한다. 최종 docs head CI/merge 수신은 PR 본문과 외부 상태 기록에 남긴다. 독립10AI 리뷰로 표현하지 않는다.
