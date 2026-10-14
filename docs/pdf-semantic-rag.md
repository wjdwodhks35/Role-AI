# PDF와 로컬 의미 검색

PDF는 `POST /api/documents/pdf?filename=...&project=...`에 application/pdf 바이너리로 전송합니다. 최대 512KiB, 20페이지, 추출 텍스트 16,000자. WSL2/Linux에서 별도 프로세스에 주소공간 512MiB, CPU 8초, 실행 12초 제한을 적용합니다. pypdf로 페이지별 텍스트와 페이지 표시를 추출하고 기존 SQLite 문서 검색에 연결합니다. 암호화·손상·텍스트 없는 PDF는 422, OCR 필요 상태를 명시합니다. Windows 직접 실행은 격리 제한이 없으므로 503을 반환합니다. 원본 PDF와 첨부·스크립트는 저장하거나 실행하지 않습니다. 내보내기는 추출된 텍스트입니다. 페이지 표시는 텍스트에 포함되며 발췌의 position은 페이지 번호가 아니라 chunk 순서입니다.

환경변수 `ROLE_AI_EMBEDDING_MODEL`에 이미 설치된 로컬 임베딩 모델의 태그를 입력하면 의미 검색을 사용할 수 있습니다. 기본은 빈 값/비활성화입니다. `ROLE_AI_EMBEDDING_MIN_SCORE` 기본 0.35는 코사인 유사도 하한이며 모델에 따라 조정해야 합니다. 자동 모델 다운로드 및 유료 API는 없습니다. HTTP 루프백 또는 Docker의 host.docker.internal Ollama만 허용하고 `/api/show`에서 embedding capability 및 remote_host/remote_model을 확인해 cloud 모델을 거절합니다.

문서 관리의 검색 방식과 채팅의 retrieval_mode를 semantic으로 선택합니다. 검색할 문서 1~5개를 반드시 지정해야 합니다. 소유권/프로젝트를 먼저 검증하고 질의와 최대128개 발췌를 `/api/embed` 한 번으로 임베딩합니다. truncate=false, keep_alive=0, 유한·동일 차원·비영 벡터와 응답 크기를 검사합니다. 코사인 상위5개와 점수를 반환하고 채팅 문맥은 4,500자로 제한합니다. 채팅 질의가 길면 검색에는 앞200자를 사용합니다. 실패 시 키워드로 몰래 바꾸지 않습니다. 역할별 모델과 같이 GPU 협력 잠금을 사용합니다. 외부에서 별도로 실행한 GPU 작업까지 제어하지는 않습니다.

첫 버전은 검색마다 임베딩하며 지속 벡터 캐시/ANN 인덱스는 없습니다. 소규모 개인 문서에 한정된 방식이고 대규모 확장은 후속 단계입니다. 실제 임베딩 모델의 한국어 검색 품질과 GPU 메모리는 실환경 검증 대기입니다. 자동 테스트의 가짜 벡터는 동의어 검색 경로와 수학적 순위를 검증하며 실제 모델 품질을 증명하지 않습니다. 기존 출처 이력·사용자 격리·문서 대화 유료 전송 차단을 유지합니다. Docker 주소는 Compose의 host-gateway 매핑으로 호스트 PC에 연결합니다. 운영자가 이 로컬 주소를 외부 서버에 프록시하지 않는 구성이 전제입니다.

공식 근거: [Ollama Embeddings API](https://docs.ollama.com/api/embed), [Ollama ShowResponse](https://github.com/ollama/ollama/blob/main/api/types.go), [pypdf PDF 텍스트 추출](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).
