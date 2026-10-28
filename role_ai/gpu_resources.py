"""Single-GPU admission budgets; not hardware partitioning or a system-wide hard cap."""
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


class GpuBudgetError(ValueError):
    pass


class GpuPolicy(BaseModel):
    enabled: bool = False
    limit_mib: int = Field(default=6144, ge=512, le=131072, strict=True)
    reserve_mib: int = Field(default=1024, ge=0, le=65536, strict=True)
    inference_mib: int = Field(default=4096, ge=128, le=131072, strict=True)
    embedding_mib: int = Field(default=1024, ge=128, le=131072, strict=True)
    training_mib: int = Field(default=5120, ge=128, le=131072, strict=True)


def policy_path(settings):
    return Path(settings.storage_dir).resolve() / "gpu-memory-policy.json"


def read_policy(settings):
    path = policy_path(settings)
    if not path.exists():
        return GpuPolicy()
    try:
        if path.is_symlink() or path.stat().st_size > 16384:
            raise ValueError("invalid policy file")
        return GpuPolicy.model_validate_json(path.read_text("utf-8"))
    except (OSError, ValueError):
        raise GpuBudgetError("GPU 메모리 설정을 읽을 수 없습니다. 설정을 다시 저장하세요.") from None


def write_policy(settings, policy):
    root = policy_path(settings).parent
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / ("gpu-policy-" + str(uuid4()) + ".tmp")
    try:
        temporary.write_text(policy.model_dump_json(), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(policy_path(settings))
    finally:
        temporary.unlink(missing_ok=True)


def gpu_snapshot():
    executable = shutil.which("nvidia-smi")
    if not executable and Path("/usr/lib/wsl/lib/nvidia-smi").is_file():
        executable = "/usr/lib/wsl/lib/nvidia-smi"
    if not executable:
        raise GpuBudgetError("nvidia-smi를 사용할 수 없습니다. NVIDIA 드라이버 또는 컨테이너 GPU 접근을 확인하세요.")
    try:
        result = subprocess.run(
            [executable, "-i", "0", "--query-gpu=memory.total,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=True,
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) != 1 or len(result.stdout) > 1024:
            raise ValueError("unexpected GPU response")
        total, used = [int(v.strip()) for v in lines[0].split(",")]
        if total <= 0 or not 0 <= used <= total:
            raise ValueError("invalid GPU values")
        return {"gpu_index": 0, "total_mib": total, "used_mib": used, "free_mib": total - used}
    except (OSError, subprocess.SubprocessError, ValueError):
        raise GpuBudgetError("GPU 메모리 측정에 실패했습니다. 작업을 시작하지 않습니다.") from None


def admission(policy, snapshot, kind):
    if kind not in {"inference", "embedding", "training"}:
        raise GpuBudgetError("알 수 없는 GPU 작업입니다.")
    total, used = snapshot["total_mib"], snapshot["used_mib"]
    if total <= 0 or not 0 <= used <= total:
        raise GpuBudgetError("GPU 메모리 측정값이 올바르지 않습니다.")
    effective = min(policy.limit_mib, total - policy.reserve_mib)
    estimate = getattr(policy, kind + "_mib")
    # Conservative: existing resident models are not assumed to be reusable.
    if effective <= 0 or used + estimate > effective:
        raise GpuBudgetError(
            f"GPU 메모리 대기: 사용 {used} + 작업 예상 {estimate}MiB가 "
            f"유효 한도 {max(0, effective)}MiB를 초과합니다."
        )
    return {"enabled": True, "limit_mib": effective, "estimated_mib": estimate,
            "snapshot": snapshot, "kind": kind, "allocator_limit_mib": effective - used}


def preflight(settings, kind):
    if kind in {"inference", "embedding"} and (Path(settings.storage_dir) / ".inference-uncertain").exists():
        raise GpuBudgetError("로컬 모델의 이전 실행 종료가 불명확합니다. 서버 상태와 추론 보호 파일을 확인하세요.")
    policy = read_policy(settings)
    if not policy.enabled:
        return {"enabled": False}
    if kind in {"inference", "embedding"}:
        url = urlsplit(settings.ollama_url)
        if (url.scheme != "http" or url.hostname not in {
            "localhost", "127.0.0.1", "::1", "host.docker.internal"
        } or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}):
            raise GpuBudgetError("VRAM 한도는 같은 호스트의 로컬 GPU 작업만 지원합니다.")
    return admission(policy, gpu_snapshot(), kind)


def configure_allocator(torch, limits, total_bytes):
    if limits.get("enabled"):
        if total_bytes <= 0:
            raise GpuBudgetError("GPU 전체 메모리를 확인할 수 없습니다.")
        fraction = min(1.0, limits.get("allocator_limit_mib", limits["limit_mib"]) * 1024 * 1024 / total_bytes)
        if fraction <= 0:
            raise GpuBudgetError("학습 메모리 한도가 올바르지 않습니다.")
        torch.cuda.set_per_process_memory_fraction(fraction, device=0)
        return fraction
    return None


def gpu_router(authenticated, settings):
    router = APIRouter(prefix="/api/settings/gpu-memory", tags=["resources"])

    def owner(actor=Depends(authenticated)):
        if not actor[0].is_owner or settings.mode != "local":
            raise HTTPException(403, "GPU 설정은 로컬 소유자 전용입니다.")
        return actor[0]

    @router.get("")
    def status(user=Depends(owner)):
        policy_state = "valid"
        try:
            policy = read_policy(settings)
        except GpuBudgetError:
            policy, policy_state = GpuPolicy(), "invalid"
        try:
            snapshot = gpu_snapshot()
            measurement = {"state": "available", **snapshot,
                           "effective_limit_mib": max(0, min(
                               policy.limit_mib, snapshot["total_mib"] - policy.reserve_mib))}
        except GpuBudgetError as exc:
            measurement = {"state": "unavailable", "reason": str(exc)}
        return {"policy": policy.model_dump(), "policy_state": policy_state,
                "measurement": measurement,
                "scope": "single_gpu_0_admission_control",
                "system_wide_hard_limit": False,
                "training_allocator_limit": True, "applies_to": "new_tasks_only"}

    @router.put("")
    def save(body: GpuPolicy, user=Depends(owner)):
        try:
            write_policy(settings, body)
        except OSError:
            raise HTTPException(503, "GPU 설정을 저장할 수 없습니다.") from None
        return {"policy": body.model_dump(), "applies_to": "new_tasks_only"}
    return router
