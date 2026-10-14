# 기억과 승인 기반 학습

개인용 Windows/WSL2 환경을 기준으로 구현했습니다. 공개 서비스 배포는 수행하지 않습니다.
백엔드와 학습 Worker는 WSL2/Linux에서 실행합니다.

## 기억: SQLite가 원본

사용자/프로젝트/피드백/지식으로 분류합니다. 사용자 직접 등록·수정은 confirmed,
Ollama가 원문 사용자 메시지에서 추출한 내용은 candidate로 저장합니다.
기억 관리 화면에서 후보를 확인한 뒤에만 채팅에 활용합니다.
원문 message ID의 사용자 소유권과 user 역할을 서버에서 확인합니다.
AI 답변을 자동으로 사실이나 장기 기억으로 승격하지 않습니다.
완전 동일한 기억은 중복 저장을 줄이고, 수정 revision과 이전 내용은 DB에 기록합니다.
수정은 expected revision 검사로 동시 덮어쓰기를 방지합니다.
삭제 시 현재 기억과 변경 이력을 제거하고, Markdown 내보내기는 DB에서 다시 생성합니다.
파일과 DB를 동시에 자유롭게 수정하는 구조는 사용하지 않습니다.
만료 기한이 지난 기억은 검색·채팅·내보내기에서 제외하며 물리 삭제는 삭제 API를 사용합니다.
후보 자동 추출은 메시지의 '기억 후보 추출' 버튼으로 요청합니다. 매 대화 자동 추출은 아닙니다.

현재 검색은 제한된 키워드/부분 문자열 검색이며 임베딩 의미 검색은 미구현입니다.
공통 프로필/피드백과 현재 프로젝트의 관련 confirmed 기억만 작은 문맥 예산으로 주입합니다.
채팅의 프로젝트 이름을 지정하면 해당 프로젝트와 공통 기억을 사용합니다.
기억은 참고 데이터이며 시스템 규칙이나 도구 실행 승인을 대체하지 않습니다.
기억이 학습 데이터로 자동 편입되지는 않습니다.
장기 기억을 바꿔도 LLM 가중치가 학습되는 것은 아닙니다.

## 데이터: 정확한 샘플 + 사람이 읽는 카드

전체 Hugging Face 저장소를 clone하거나 데이터셋 전부를 다운로드하지 않습니다.
사용자 제공 JSONL은 UI에서 최대 48KB, 10~100행을 선택합니다.
API의 전체 JSON 요청 본문 64KiB 제한도 적용됩니다.
각 행에는 prompt와 completion 문자열이 필요합니다.

    {"prompt":"정리할 입력 또는 질문", "completion":"검증된 정답"}

Hugging Face에서는 특정 저장소의 고정 40자 commit SHA와 JSONL 파일을 지정합니다.
자동 repo script 실행, Parquet, CSV, 압축 원본 import는 현재 지원하지 않습니다.
네트워크 조회/가져오기는 기본 비활성화입니다.
HF 조회를 활성화하면 로컬 Ollama가 실제 검색 후보 중에서 repo ID와 이유를 제안할 수 있습니다.
후보 선별은 자동 학습 승인이나 데이터 품질 보장이 아닙니다.
가져오기는 사용자 선택 후 제한된 HTTP stream으로 파일 앞부분만 읽습니다.
UI는 최대 30행, API는 최대 100행, 기본 읽기 제한은 2MiB입니다.
행 제한에 도달하면 stream을 닫습니다. 제한 안에서 충분한 행을 얻지 못하면 실패합니다.
전체 파일의 균등 무작위 샘플이 아니므로 데이터 편향을 검토해야 합니다.
원본 dataset revision, 파일, 필드 매핑과 선택 범위를 카드에 기록합니다.

정제 후 다음 파일을 사용자/스냅샷별 runtime storage에 저장합니다.

    data/artifacts/<user-id>/<snapshot-id>/
      manifest.json
      dataset-card.md
      train.jsonl.gz
      eval.jsonl.gz
      run-<job-id>/          # 실제 Worker 실행 후
        evaluation.json
        adapter/

문서 요약으로 학습 원문을 대체하지 않습니다. 실제 학습 쌍은 압축 JSONL에 보존합니다.
카드는 출처·라이선스·샘플 수·제외 수·선택 한계·데이터 해시를 설명합니다.
기본 카드 파일은 immutable snapshot 요약이며 최신 검수/승인 카드 내보내기는 DB에서 생성합니다.
저장소 기본 한도는 100MiB이며 보관 파일과 학습 결과를 포함합니다.
데이터 저장 시 파일 크기를 확인하고, 학습 결과 저장 후에도 총량을 검사합니다.
대형 원본 또는 모델 가중치는 Git에 올리지 않습니다.

정제는 빈 데이터/길이 제한, 정규화한 동일 질문의 중복,
일부 이메일·전화·주민번호·키 패턴을 검사합니다.
같은 질문에 서로 다른 정답이 있으면 import를 거부합니다.
개인정보/유해성 탐지를 완벽하게 보장하는 필터는 아닙니다.
의미가 비슷한 중복 제거와 전문 데이터 품질 검사는 후속 기능입니다.
정규화된 질문 단위로 결정적인 train/eval 분리를 만들고 평가 샘플은 학습에서 제외합니다.

## 검수와 승인

Teacher → Reviewer는 Ollama의 공유 모델로 선택된 학습·평가 전체 샘플을 행별 순차 검수합니다.
오케스트레이터는 공식 OpenAI Provider를 사용하며 별도 호출 동의와 예산 검사 후 실행합니다.
실제 API 키가 없으면 검수 성공으로 가장하지 않습니다.
GPT에 보내는 자료는 학습 미리보기 3개와 manifest이며 평가셋은 보내지 않습니다.
현재 검수는 표본 검수이지 전체 데이터의 정답·권리 보장이 아닙니다.
동일 로컬 모델의 두 역할은 독립적인 전문가 검증을 대신하지 않습니다.

최종 학습 승인에는 다음을 요구합니다.

- 동일 data_hash에 대한 세 역할의 pass 검수
- 사용자의 데이터 라이선스/개인정보/기반 모델 이용 조건 확인
- 초기 허용 license ID: apache-2.0, mit, cc-by-4.0, cc0-1.0
- 서버에 설정한 로컬 Safetensors 기반 모델과 원본 모델 revision
- 모델 파일 fingerprint와 고정 SFT/QLoRA 학습 설정

라이선스 ID 허용만으로 법적 이용 조건이 충족됐다는 뜻은 아닙니다.
원본/파생 데이터의 실제 조건과 표시 의무를 검토해야 합니다.
새 검수 결과를 기록하면 기존 학습 승인을 무효화합니다.
데이터 해시·모델 파일·계획·검수 결과를 결합한 approval hash를 Worker가 다시 검증합니다.
선택 가능한 대상 Skill은 Developer/Meeting/Professor/Counselor입니다.
현재 UI의 기본 학습 대상은 Developer, API에서는 target_skill로 지정합니다.
Trainer는 선택한 역할의 system prompt를 대화 샘플에 적용합니다.
최종 승인 전 UI의 전체 선택 샘플 확인 버튼으로 정제된 train/eval을 직접 확인할 수 있습니다.
의료 전문 모델의 검증·임상 판단은 이 학습 기능의 범위 밖입니다.

## 학습 실행: SFT + QLoRA

실제 GPU 학습은 이번 개발 과정에서 실행하지 않았습니다.
Mock trainer 테스트는 승인/큐/파일 검증 흐름만 검증합니다.
선택한 모델의 CUDA/라이브러리/메모리 적합성은 실제 WSL 환경에서 별도 확인해야 합니다.
1~3B급 대화 모델의 작은 실험부터 시작하는 계획이며 8GB에서 모든 모델이 된다고 보장하지 않습니다.

먼저 기반 모델을 별도로 확보하고 라이선스를 확인합니다.
HF 모델 다운로드는 Worker가 자동 수행하지 않습니다.
Safetensors 가중치, tokenizer와 대화 템플릿을 갖춘 로컬 디렉터리가 필요합니다.
HF cache의 심볼릭 링크를 그대로 쓰지 말고 독립된 실제 파일 디렉터리를 준비하세요.

    python -m pip install -e ".[training]"

.env 예시:

    ROLE_AI_STORAGE_DIR=./data/artifacts
    ROLE_AI_HF_DOWNLOAD_ENABLED=false
    ROLE_AI_HF_MAX_SCAN_BYTES=2097152
    ROLE_AI_ARTIFACT_QUOTA_BYTES=104857600
    ROLE_AI_TRAINING_ENABLED=false
    ROLE_AI_TRAINING_BASE_MODEL_PATH=/path/to/local/base-model
    ROLE_AI_TRAINING_BASE_MODEL_REVISION=<원본의 40자 commit SHA>

데이터 검수/승인을 마치고 자원을 확보한 뒤 실행을 명시적으로 활성화합니다.
UI의 작업 등록은 큐에만 넣으며 API 프로세스가 GPU 학습을 실행하지 않습니다.
한 프로세스의 API와 별도 Worker로 운영합니다. API의 기존 학습 선택 기능을 자동 변경하지 않습니다.

    ROLE_AI_TRAINING_ENABLED=true
    python -m role_ai.training --once

Worker는 큐의 한 작업을 처리하고 종료합니다.
다른 Worker/데이터 파일 쓰기와 Role-AI의 Ollama/OpenClaw 추론 요청은
동일한 OS 파일 lock으로 충돌을 방지합니다. 외부 프로그램의 GPU 작업은 이 lock에 참여하지 않습니다.
Ollama/vLLM/Whisper 작업을 직접 중지하고 충분한 VRAM을 확보하세요.
CUDA가 없거나 VRAM 여유가 85% 미만이면 blocked로 기록합니다.
이 검사는 GPU 작업 스케줄러나 OOM 방지 보장을 대신하지 않습니다.
서버 모델을 자동 종료하는 명령은 실행하지 않습니다.

Worker는 offline/local-only 로딩, trust_remote_code=false, Safetensors 로딩을 사용합니다.
4bit NF4 기본 모델에 LoRA rank 8, batch 1, gradient accumulation 8을 적용합니다.
기본 30 steps/512 context이며 승인 API에서는 100 steps/1024 context 이내로 선택합니다.
승인한 문맥 길이를 넘는 샘플은 몰래 잘라 학습하지 않고 실패 처리합니다.
학습 전후 held-out loss를 기록하고 adapter를 저장합니다. 수동 승인에 고정 평가 묶음을 연결하면 동일 질문의 정답 일치율도 비교합니다.
loss 감소만으로 실용 성능이나 안전성이 검증된 것은 아니므로 별도 역할별 평가가 필요합니다.
DPO/GRPO/자동 모델 배포는 아직 구현하지 않았습니다.

## 작업 복구와 데이터 수명주기

queued → running → evaluated / failed / blocked.
blocked는 자원 확보 후 사용자 요청으로만 재시도합니다.
failed는 원인 확인과 새 승인 후에 새 작업을 등록합니다.
Worker가 중단돼 running으로 남으면 Worker가 종료됐음을 확인하고 상태 복구를 요청합니다.
활성 Worker가 lock을 보유하면 복구를 거부합니다.
복구는 실패 처리하고 기존 승인을 무효화하며 자동 재학습하지 않습니다.
queued/blocked는 취소 가능하고 실행 중 즉시 취소는 현재 지원하지 않습니다.

작업이 없는 데이터셋 또는 모든 작업이 종료된 데이터셋은 삭제할 수 있습니다.
삭제는 압축 샘플·문서·학습 결과와 종료된 작업 기록을 함께 제거합니다.
진행 중 작업이 있으면 먼저 취소/복구해야 합니다.
현재는 수동 삭제 방식이며 자동 보존기한 정리·클라우드 아카이브는 후속 기능입니다.
평가된 adapter를 Ollama로 옮기는 변환/가져오기와 활성화는 별도 검증·승인 단계입니다.
원래 서비스 모델은 변경하지 않으므로 실패해도 기존 모델을 계속 사용할 수 있습니다.

## 참고

- [파일 기억 분류 아이디어](https://wikidocs.net/343843)
- [OpenClaw 기억](https://docs.openclaw.ai/concepts/memory)
- [TRL SFT 0.28](https://huggingface.co/docs/trl/v0.28.0/en/sft_trainer)
- [PEFT QLoRA](https://huggingface.co/docs/peft/developer_guides/quantization)
- [Hugging Face 데이터 카드](https://huggingface.co/docs/hub/datasets-cards)
