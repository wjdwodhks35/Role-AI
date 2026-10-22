# AI 피드백 기반 강화학습

Role-AI에 기존 SFT/QLoRA와 별도로 `rlaif_grpo_qlora` 학습 경로를 추가합니다.
평가 AI가 현재 학습 중인 모델의 생성 답변에 보상을 주고 TRL GRPO가 LoRA 가중치를 갱신하는 direct-RLAIF 구조입니다.
SFT 데이터 선별, GPT 학습 승인, 분석 라벨 평가와 구분됩니다. 학습기는 Ollama 답변을 수집해서 SFT로 흉내 내는 방식이 아닙니다.

## 구성

1. 검수된 질문·참고 답변 snapshot과 별도의 고정 평가 질문을 준비합니다.
2. 로컬 Teacher·Reviewer가 선택된 train/eval 전체 행을 검수합니다.
3. 소유자가 방법·평가 기준·평가 모델·자원 한도를 확인하고 RLAIF 계획을 승인합니다.
4. Worker가 승인 해시·데이터·기반 모델·평가 모델·선택한 부모 SFT 어댑터를 재검증합니다.
5. GRPO가 현재 학습 정책으로 질문당 답변 2개를 생성합니다.
6. 고정된 로컬 AIJudge가 각 답변에 보상을 부여하고 GRPO가 가중치를 갱신합니다.
7. 학습 전후 held-out completion loss와 독립 고정 질문 exact-match 점수를 기록하고 어댑터를 저장합니다.

기존 SFT 경로를 그대로 사용할 수 있습니다. 평가 완료된 같은 기반 모델의 SFT 작업 ID를 부모로 지정하면 해당 어댑터에서 이어서 RLAIF를 학습합니다.
부모를 지정하지 않으면 설정된 로컬 기반 모델에서 새 LoRA를 시작합니다. 부모 작업·파일 해시가 바뀌면 실행하지 않습니다.
현재 KL 기준은 **어댑터를 비활성화한 로컬 기반 모델**입니다. 부모 SFT 어댑터 자체를 고정한 별도 reference 모델은 아닙니다.

## 기본 비활성화 설정

기존 .env를 덮어쓰지 말고 필요한 항목만 검토해 설정하세요.

```dotenv
ROLE_AI_TRAINING_ENABLED=true
ROLE_AI_RLAIF_ENABLED=true
ROLE_AI_RLAIF_JUDGE_URL=http://127.0.0.1:11434
ROLE_AI_RLAIF_JUDGE_MODEL=설치된-정확한-평가모델명
ROLE_AI_RLAIF_JUDGE_TIMEOUT_SECONDS=60
```

기반 모델 경로·revision은 기존 학습 설정을 사용합니다. 로컬 Safetensors와 대화 템플릿이 필요합니다.
가상환경의 학습 의존성은 `python -m pip install -e ".[training]"`로 별도 준비합니다.
TRL 0.28 계열 인터페이스를 사용하며 CPU 통합 검사는 0.28.0으로 수행합니다.

평가 AI는 HTTP 루프백 또는 host.docker.internal 주소만 허용합니다. 프록시 환경변수·리디렉션·cloud 모델을 사용하지 않습니다.
모델을 다운로드하거나 설치하지 않습니다. `/api/tags`의 모델 digest와 `/api/show`의 completion/cloud 정보를 검사하고 승인 시 고정합니다.
각 점수 요청 전후에도 identity를 확인합니다. URL·모델·rubric이 바뀌면 다시 승인해야 합니다.
API와 Worker 양쪽에서 동일한 설정 주소에 접근할 수 있어야 합니다. 컨테이너의 127.0.0.1은 해당 컨테이너 자신입니다.
API가 제공하는 메타데이터 검증이며 네트워크 격리나 악의적인 서버의 신원 보장 자체를 제공하지는 않습니다.

평가 요청에는 `num_gpu=0`을 지정합니다. **CPU 요청이며 하드웨어 격리 보장은 아닙니다.**
단일 8GB GPU에서 평가 모델과 학습 모델을 함께 상주시킬 수 있다고 가정하지 마세요.
CPU 전용 Ollama 실행 환경을 별도로 확인하고, 학습 중에는 다른 GPU 추론 부하를 제어해야 합니다.
기존 vLLM 환경을 재설치하거나 사용하지 않습니다(`use_vllm=false`).

## 평가 기준과 기록

`rlaif-rubric-v1`은 정확성 40%, 요청 충족 30%, 제약 준수 20%, 근거 적절성 10%입니다.
각 항목은 엄격한 0~4 정수이며 가중합을 0~1로 환산합니다. 정확성 또는 제약 준수가 0이면 보상은 0입니다.
이 가중치는 초기 설계값이며 검증된 최적값이나 모델의 신뢰도가 아닙니다.
참고 답변은 검수된 데이터의 completion이며 참고 답변 자체도 틀릴 수 있다는 지시를 평가 AI에 제공합니다.

불확실(`uncertain=true`)·잘못된 JSON·미완료 응답·다른 모델 응답·시간 초과는 **숫자 0으로 바꾸지 않고 학습을 중단**합니다.
데이터 속 채점 지시를 따르지 않도록 시스템 지시와 JSON 데이터를 구분합니다. 이것만으로 prompt injection을 완전히 막는다는 뜻은 아닙니다.
기준 확인은 정답·오답·점수 조작 지시가 들어 있는 오답의 합성 3개 사례입니다. 승인 전과 Worker 시작 시 실행합니다.
이는 최소 sanity check이며 전문 지식·한국어·역할 전체의 채점 정확도 검증을 대신하지 않습니다.

생성된 실제 prompt/completion/reference, 항목별 점수·이유, 보상, 평가 모델 digest·rubric hash,
학습 기반 모델·부모 어댑터 지문과 global step을 다음 위치에 기록합니다.

```
storage_dir/<user-id>/<snapshot-id>/run-<job-id>/rewards.jsonl
storage_dir/<user-id>/<snapshot-id>/run-<job-id>/judge-calibration.json
storage_dir/<user-id>/<snapshot-id>/run-<job-id>/evaluation.json
storage_dir/<user-id>/<snapshot-id>/run-<job-id>/adapter/
```

최종 보고서는 라이브러리 버전·주요 GRPO 파라미터·optimizer step 수·실제 trainable 가중치 변경 여부·보상 기록 해시를 포함합니다.
보상 기록은 소유자 API/UI에서 앞 50개와 전체 개수를 확인할 수 있습니다. 기록 파일은 기존 artifact 백업에 포함되며 Git에 올리지 않습니다.
실패한 작업의 부분 기록도 존재하는 범위에서 조회할 수 있습니다. 자원 부족 재시도는 이전 부분 결과를
`run-<job-id>-attempt-<uuid>/`로 보존하고 새 `run-<job-id>/`를 사용합니다. 기존 결과를 덮어쓰지 않습니다.

## 승인과 실행

화면의 학습 관리 → RLAIF 영역에서 방법·스텝·최대 평가 횟수·시간·선택적 부모 작업을 설정합니다.
고정 평가 질문을 선택한 뒤 데이터/모델 조건 확인과 RLAIF 동의를 거쳐 승인·큐 등록합니다.
별도의 “평가 AI 기준 확인 및 답변 채점”은 명시적으로 전송 동의한 입력만 채점하고 학습·큐 등록은 하지 않습니다.
이 미리보기 결과는 저장하지 않습니다. 학습 승인 기준 검사와 실제 학습 보상은 저장됩니다.

API 승인 예시(기존 data_hash·이용 조건 확인 필드와 함께 사용):

```json
{
  "method": "rlaif_grpo_qlora",
  "rlaif_approved": true,
  "max_steps": 5,
  "max_length": 512,
  "benchmark_id": "소유한-독립-평가-묶음-ID",
  "rlaif_max_judge_calls": 64,
  "rlaif_max_seconds": 1800,
  "rlaif_max_completion_length": 64,
  "rlaif_minimum_benchmark_score": 0.5,
  "parent_job_id": null
}
```

학습은 최대 20스텝입니다. 평가 횟수에는 Worker 기준 확인 3회가 포함되며 스텝당 답변 2개 이상을 수용해야 합니다.
승인 시 별도로 수행하는 3회 기준 검사는 학습 실행 횟수 한도와 별개입니다.
시간 한도·취소는 HTTP 응답과 학습 단계 경계에서 확인하는 협력적 중단입니다. 실행 중 CUDA kernel을 강제 종료하는 하드웨어 제한은 아닙니다.
문맥은 tokenizer로 미리 검증해 초과하면 자동 truncation 없이 거부합니다. 평가 AI 입력도 크기를 제한합니다.
보상 기록은 실행당 최대 4MiB이며 시작 시 artifact 잔여 한도도 반영합니다. 학습 결과에는 기존 artifact quota 검사를 적용합니다.

```bash
python -m role_ai.training --once
```

RLAIF에는 유료 GPT 오케스트레이터 검수를 요구하지 않습니다. Teacher·Reviewer, 소유자 승인, 고정 평가, 평가 AI 기준 검사를 사용합니다.
기존 GPT 자동 승인 정책은 계속 SFT 전용이며 RLAIF로 확장되지 않습니다. AI 채점은 파일/shell/코드 실행 권한이 아닙니다.

## 평가·적용 범위

보상을 계산한 train 질문과 held-out/eval 및 benchmark를 분리합니다. 고정 평가 정답은 학습 보상 AI에 전송하지 않습니다.
후보 선택은 실제 GPU RLAIF 실행, optimizer step·가중치 변경·기준 검사·보상 기록 해시를 검증합니다.
held-out loss 악화가 없고 고정 질문 점수가 기존 이상이며 승인된 최소 점수(기본 0.5) 이상이어야 후보를 선택할 수 있습니다.
보상 상승만으로 후보를 선택하지 않습니다. exact-match 평가는 작은 고정 질문 집합에만 유효하며 의미 평가나 일반 능력 향상을 보장하지 않습니다.

**배포 후보 선택·되돌리기는 후보 기록 변경입니다. 실제 Ollama 채팅 모델 교체·서비스 롤백은 여전히 별도 미구현입니다.**
이 변경은 기존 JSON 계획·보고서와 artifact를 재사용하므로 DB 스키마 버전은 v3로 유지합니다.
실제 사용자 GPU의 4bit 학습, 평가 AI 품질, 실제 채팅 적용은 별도로 검증해야 합니다.

## 개발 검증

일반 단위 테스트 외에 `qa/check_rlaif_contract.py`는 로컬에서 임의 생성한 아주 작은 CPU 모델로 실제 TRL GRPO·LoRA 학습을 1스텝 실행합니다.
HTTP 평가 응답은 합성 fixture입니다. 실제 가중치 갱신·현재 모델 생성 답변의 보상 연결·독립 평가·Safetensors 저장을 확인합니다.
사전학습 모델 다운로드·CUDA·유료 API·사용자 DB는 사용하지 않습니다. 이 검사의 성공을 실제 AIJudge 품질이나 GPU 양자화 성공으로 해석하지 않습니다.

참고: [direct-RLAIF 연구](https://proceedings.mlr.press/v235/lee24t.html),
[TRL 0.28.0 GRPO](https://huggingface.co/docs/trl/v0.28.0/en/grpo_trainer).
