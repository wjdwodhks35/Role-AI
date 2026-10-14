# 단일 GPU VRAM 한도 설정

로그인한 로컬 소유자의 GPU 메모리 설정 화면에서 최대 사용량, 시스템 여유 메모리, 채팅/임베딩/학습의 예상 추가 필요량을 지정합니다. 화면 단위는 GiB(1GiB=1024MiB), API 단위는 MiB입니다. 초기 검사 설정은 꺼짐입니다. 초기 입력값은 한도6GiB, 여유1GiB, 추가 예상 채팅4GiB·임베딩1GiB·학습5GiB이며 특정 모델에 대한 검증된 요구량이 아닙니다.

## 적용 방식

GPU 0 한 개를 대상으로 nvidia-smi의 total/used MiB를 조회합니다. WSL에서는 PATH 외에 /usr/lib/wsl/lib/nvidia-smi도 확인합니다. 시간 제한3초 및 응답 형식/값 검증을 적용합니다.

유효 한도 = min(설정한 최대 사용량, 실제 전체 VRAM - 시스템 여유 VRAM).
현재 전체 사용량 + 작업의 예상 추가 필요량이 유효 한도를 넘으면 새 작업을 시작하지 않습니다. 현재 사용량에는 다른 프로그램과 이미 로드된 모델도 포함되므로 보수적인 검사입니다. 추정값이 실제 모델 메모리보다 작거나 외부 프로그램이 뒤늦게 VRAM을 사용하면 실제 메모리가 한도를 넘거나 OOM이 날 수 있습니다. 하드웨어 분할/시스템 전체 강제 상한이 아닙니다.

설정은 storage_dir/gpu-memory-policy.json에 원자적으로 저장하며 API/수집/학습 Worker가 새 작업 전에 읽습니다. 설정 변경은 다음 작업부터 적용되고 진행 중 작업의 메모리를 회수하거나 외부 앱을 강제 종료하지 않습니다. 새 환경변수/키/모델 다운로드는 없습니다. 파일은 사용자 데이터 디렉터리에 남으며 GitHub에 올라가지 않습니다.

## 실행 경로

- Ollama 채팅·로컬 수집/Teacher/Reviewer 검수: 기존 GPU 협력 잠금 안에서 검사. 한도 켜짐일 때 Role-AI가 요청한 모델은 keep_alive=0으로 요청 후 반납하도록 요청함
- 의미 검색: embedding 예산 검사. 기존 keep_alive=0 유지
- OpenClaw: inference 예산 검사. Gateway의 실제 모델 residency는 별도 관리해야 함
- 학습: 기존 승인/데이터/모델 검증 후 예산 검사. 초과/측정불가면 blocked로 남기고 명시적 재시도 가능
- PyTorch 학습: 유효 한도에서 기존 사용량을 뺀 범위로 caching allocator의 per-process fraction 제한. 비-PyTorch 할당/드라이버/다른 프로세스까지 제한하지 않음
- 실제 학습 CUDA OOM: blocked로 처리하며 승인 유지, 자동 모델 배포 없음
- OpenAI/Mock/CPU 문서 처리: 로컬 GPU 검사 대상 아님

같은 호스트의 GPU를 조회해야 하므로 로컬/WSL 또는 Docker host-gateway Ollama에 한정합니다. 임의 원격 Ollama 서버에 로컬 한도를 적용했다고 표시하지 않습니다. 단일 GPU 0 및 대응하는 CUDA 장치를 전제로 하며 다중 GPU 매핑은 후속 단계입니다. GPU 수치가 없거나 설정 파일이 손상되면 켜진 검사를 대신 성공으로 처리하지 않습니다. 설정 자체는 측정 전 저장할 수 있습니다.

## Docker 모니터링

기존 Compose는 기본적으로 API/수집 컨테이너에 GPU 접근을 부여하지 않습니다. NVIDIA 드라이버와 NVIDIA Container Toolkit을 준비한 뒤 선택적으로 다음 overlay를 사용합니다:

docker compose -f compose.yaml -f compose.gpu.yaml --profile collection up -d --build

overlay는 GPU0과 utility 드라이버 기능을 API/수집 컨테이너에 제공해 nvidia-smi 모니터링을 허용합니다. 이것이 호스트 Ollama 메모리를 강제로 제한하거나 GPU 모델 추론을 API 컨테이너로 옮기지는 않습니다. GPU 접근이 준비되지 않은 상태에서 검사를 켜면 로컬 작업은 대기/503이 됩니다.

## 검증 범위

자동 테스트는 가짜 측정값으로 저장/권한/한도/여유/장애/실행 경로/학습 blocked/할당기 호출/OOM을 검증합니다. 실제 RTX4060Ti·WSL·NVIDIA Container Toolkit·PyTorch GPU 메모리는 실환경 검증 대기입니다. 모델별 추정치는 실제 환경에서 조정해야 합니다.

공식 기준: [NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/index.html), [PyTorch allocator fraction](https://docs.pytorch.org/docs/stable/generated/torch.cuda.memory.set_per_process_memory_fraction.html), [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html).

수집 CLI 컨테이너에는 웹 API 이미지의 HTTP healthcheck를 적용하지 않습니다. Worker 운영 상태는 종료 코드/로그와 수집 예약 이력으로 확인합니다. 이 구성은 실제 GPU 접근을 검증한 결과가 아닙니다.
