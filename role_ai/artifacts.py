import gzip
import hashlib
import json
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@contextmanager
def storage_lock(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # WSL2/Linux backend: OS releases flock even if the worker crashes.
    import fcntl
    with (root / ".write-lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("저장소 작업이 진행 중입니다. 다시 시도하세요.") from None
        try:
            yield root
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


PII = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b010[- ]?\d{4}[- ]?\d{4}\b|"
    r"\b\d{6}-[1-4]\d{6}\b|\bsk-[A-Za-z0-9_-]{12,}\b"
)


def normalize(rows):
    accepted, seen, rejected = [], {}, 0
    for index, row in enumerate(rows):
        prompt, completion = row.get("prompt"), row.get("completion")
        if not isinstance(prompt, str) or not isinstance(completion, str):
            rejected += 1
            continue
        prompt, completion = prompt.strip(), completion.strip()
        if not prompt or not completion or len(prompt) > 4000 or len(completion) > 4000:
            rejected += 1
            continue
        if PII.search(prompt + "\n" + completion):
            rejected += 1
            continue
        key = re.sub(r"\s+", " ", prompt.casefold())
        if key in seen:
            if seen[key] != completion:
                raise ValueError("같은 질문에 서로 다른 정답이 있습니다. 데이터를 검토하세요.")
            rejected += 1
            continue
        seen[key] = completion
        accepted.append({"prompt": prompt, "completion": completion, "source_row": index})
    if len(accepted) < 10:
        raise ValueError("정제 후 최소 10개 샘플이 필요합니다.")
    # Group by normalized prompt before splitting; deterministic held-out split.
    accepted.sort(key=lambda row: digest(row))
    count = max(2, len(accepted) // 10)
    return accepted[count:], accepted[:count], rejected


class ArtifactStore:
    def __init__(self, settings):
        self.settings = settings
        self.root = Path(settings.storage_dir).resolve()

    def path(self, uid, snapshot_id):
        # Only server-issued UUID identifiers are filesystem path components.
        from uuid import UUID
        UUID(uid)
        UUID(snapshot_id)
        path = (self.root / uid / snapshot_id).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("잘못된 저장소 경로입니다.")
        return path

    def create(self, uid, snapshot_id, rows, source):
        train, evaluation, rejected = normalize(rows)
        manifest = {
            "format": "prompt_completion_jsonl", "source": source,
            "received_rows": len(rows), "train_rows": len(train), "eval_rows": len(evaluation),
            "excluded_rows": rejected,
            "selection": "bounded prefix or user-provided sample; not representative sampling",
            "filter": "exact prompt dedup, limited PII/secret regex; human review required",
        }
        data_hash = digest({"train": train, "eval": evaluation, "manifest": manifest})
        manifest["data_hash"] = data_hash
        with storage_lock(self.root):
            destination = self.path(uid, snapshot_id)
            temporary = self.root / (".tmp-" + str(uuid4()))
            temporary.mkdir(mode=0o700)
            try:
                for name, records in [("train", train), ("eval", evaluation)]:
                    with gzip.open(temporary / (name + ".jsonl.gz"), "wt", encoding="utf-8") as out:
                        for row in records:
                            out.write(canonical(row) + "\n")
                (temporary / "manifest.json").write_text(canonical(manifest), encoding="utf-8")
                card = [
                    "# 선별된 학습 데이터 카드", "",
                    "이 문서는 데이터 출처와 선택 범위를 설명합니다. 학습 샘플을 대신하지 않습니다.", "",
                    "## 출처", canonical(source), "", "## 샘플 수",
                    f"- 학습: {len(train)}", f"- 평가: {len(evaluation)}",
                    f"- 제외: {rejected}", "", "## 데이터 해시", data_hash, "",
                    "## 선택 한계", manifest["selection"], "",
                    "최신 검수·승인 결과는 Role-AI의 데이터 카드 내보내기에서 확인하세요.",
                ]
                (temporary / "dataset-card.md").write_text("\n".join(card), encoding="utf-8")
                used = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
                if used > self.settings.artifact_quota_bytes:
                    raise ValueError("학습 데이터 저장 한도를 초과했습니다.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary.rename(destination)
            except BaseException:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
        return manifest, data_hash

    def read(self, uid, sid, expected_hash):
        path = self.path(uid, sid)
        manifest = json.loads((path / "manifest.json").read_text("utf-8"))
        datasets = []
        for name in ["train", "eval"]:
            with gzip.open(path / (name + ".jsonl.gz"), "rt", encoding="utf-8") as stream:
                text = stream.read(self.settings.hf_max_scan_bytes + 1)
                if len(text.encode("utf-8")) > self.settings.hf_max_scan_bytes:
                    raise ValueError("정규화 데이터 크기 제한을 초과했습니다.")
                datasets.append([json.loads(line) for line in text.splitlines() if line])
        checked = dict(manifest)
        checked.pop("data_hash", None)
        actual = digest({"train": datasets[0], "eval": datasets[1], "manifest": checked})
        if actual != expected_hash or manifest.get("data_hash") != expected_hash:
            raise ValueError("학습 데이터가 변경됐습니다. 승인을 다시 받아야 합니다.")
        return datasets[0], datasets[1], manifest

    def delete(self, uid, sid):
        with storage_lock(self.root):
            shutil.rmtree(self.path(uid, sid), ignore_errors=True)
