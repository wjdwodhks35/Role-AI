# 격리 개발 Rootless 환경 준비안 — 관리자 준비 미완료

2026-10-31 KST. 사용자 "추천안대로 진행"에 따라 로컬 socket 연결 구현과 읽기 전용 사전 검토를 진행했다. 이 문서는 실제 설치/서비스 생성 완료 수신이 아니다. 운영 Docker/서비스/DB/권한·전역 context는 변경하지 않았다.

## 실제 확인

|항목|확인 결과|남은 조건|
|---|---|---|
|Docker context/socket|default → unix:///var/run/docker.sock 하나, root:docker 0660|운영 daemon 접근을 확대하지 않음|
|사용자 Rootless 서비스/socket/data|서비스 not-found, 대표 사용자 socket/data 경로 없음|별도 개발 환경 또는 사용자 daemon 필요|
|설치 도구|docker/dockerd29.1.3 Ubuntu 패키지, RootlessKit/rootless shell/slirp4netns/newuidmap 없음|기존 Docker 패키지와 별도로 호환 도구 준비|
|subuid/subgid|serveradmin:100000:65536 두 항목 있음|기존 범위 변경 불필요|
|user namespace|unshare --user --map-root-user true exit0|다중 UID/GID mapping helper 필요|
|사용자 cgroup|Delegate=yes, cpu/memory/pids controller 표시|실제 daemon/container에서 제한 집행 검증 필요|
|APT readonly simulation|uidmap + libsubid4 신규2, upgrade/remove0|관리자 인증이 필요한 실제 설치는 수행 안 함|

## 관리자에게 필요한 최소 준비

현재 세션은 관리자 인증을 대신할 수 없으며 비밀번호를 요청/수집하지 않는다. 우선 이미 분리된 개발 daemon 제공이 가능하면 그것을 우선한다. 같은 WSL에 Rootless daemon을 준비하려면 다음 작업 범위를 확인해야 한다.

1. 관리자 환경에서 설치 직전 다음 **모의 실행**의 거래 내역을 다시 확인한다. 현재 결과가 향후 패키지 상태를 보장하지 않는다.

```bash
apt-get --simulate --no-install-recommends install uidmap
```

2. 관리자 측에서 위 거래가 승인된 범위임을 확인한 경우에만 `uidmap`과 해당 필수 의존성 설치를 진행한다. `newuidmap/newgidmap`은 다중 UID/GID mapping에 필요한 특권 helper이므로 Codex가 운영 권한을 우회해 설치하지 않는다. Docker/Compose/운영 서비스 upgrade·restart가 거래에 포함되거나 추가 변경이 필요하면 중단한다. 설치가 필요하다는 설명을 설치 완료로 취급하지 않는다. [공식 prerequisite](https://docs.docker.com/engine/security/rootless/#prerequisites)

3. Rootless daemon 도구는 현재 Ubuntu Docker29.1.3과 호환되는 공식 배포 경로·버전·artifact hash를 확정한 뒤 사용자 전용 경로에 준비한다. 현재 APT cache에는 docker-ce-rootless-extras 후보가 확인되지 않았다. 이를 해결하려고 운영 Docker 저장소/패키지를 자동 교체하지 않는다. 정체불명의 설치 스크립트를 즉시 실행하거나 신규 모델을 설치하지 않는다.

4. daemon은 전용 Unix socket/data/exec/config 경로와 사용자 범위에서만 준비한다. 운영 rootful daemon을 중지/disable하거나 /var/run/docker.sock을 삭제하는 공식 문서의 선택 절차는 이 프로젝트에 적용하지 않는다. 설치 도구의 전역 context 전환/자동 시작/linger/system-wide 설정은 자동 적용하지 않는다. 필요한 사용자 서비스 변경 범위는 실행 전 구체적으로 확인한다.

5. 준비된 사용자 전용 socket을 `--socket`으로 두 평가 경로에 동일하게 전달한다. rootless/cgroup-v2/systemd/seccomp/메모리/swap/CPU/pids metadata 사전검사와 실제 timeout·memory·network-none·read-only·nonroot·cleanup 검사를 통과해야 평가 환경으로 채택한다. Rootless 설치 자체를 기존 sandbox와 동등하다는 증거로 삼지 않는다. 기존 제한을 빼거나 host runner로 우회하지 않는다.

## 되돌리기와 보존

새 사용자 daemon에 대한 검증이 실패하면 그 daemon만 중단하고 해당 평가를 미완료로 기록한다. 생성한 실험용 서비스/파일만 식별한 뒤 정리 범위를 결정하며, 자동으로 기존 image/volume/user data나 운영 Docker를 삭제하지 않는다. 시스템 패키지 제거도 다른 의존성에 영향을 줄 수 있으므로 자동 롤백하지 않는다. 합성 DB/실행 로그/실패 수신은 보존하고 운영 DB로 전환하지 않는다.

이 조건이 충족되기 전에는 PR #47의 실제 A/B와 dev 병합이 미완료다. Codex 공개 기준선 및 사용량 원문 검토도 별도 필요하다.
