"""Offline, opt-in SFT/QLoRA worker. No automatic model activation."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import select, update

from role_ai.artifacts import ArtifactStore, canonical, digest, storage_lock
from role_ai.config import Settings
from role_ai.database import Base, build_database
from role_ai.models import DatasetSnapshot, TrainingJob


class ResourcesBusy(Exception):
    pass


def model_fingerprint(directory):
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError("로컬 기반 모델 폴더가 필요합니다.")
    files = sorted(p for p in root.rglob("*") if p.is_file())
    selected = [p for p in files if p.suffix in {".json", ".safetensors", ".model", ".txt", ".tiktoken"}]
    if not any(p.suffix == ".safetensors" for p in selected):
        raise ValueError("Safetensors 기반 모델이 필요합니다.")
    digestor = hashlib.sha256()
    for path in selected:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("모델 폴더의 외부 참조는 허용하지 않습니다.")
        digestor.update(str(path.relative_to(root)).encode())
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1048576), b""):
                digestor.update(chunk)
    return digestor.hexdigest()


def run_sft(plan, train, evaluation, output):
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from datasets import Dataset
    from peft import LoraConfig, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer
    import torch

    if not torch.cuda.is_available():
        raise ResourcesBusy("NVIDIA CUDA 환경이 준비되지 않았습니다.")
    free, total = torch.cuda.mem_get_info()
    if free / total < 0.85:
        raise ResourcesBusy("다른 GPU 작업을 중지하고 VRAM을 확보하세요.")
    local = plan["base_model_path"]
    tokenizer = AutoTokenizer.from_pretrained(local, local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    if not tokenizer.chat_template:
        raise ValueError("대화 템플릿을 제공하는 기반 모델이 필요합니다.")
    def clean(records):
        prepared = [{"prompt": [{"role": "user", "content": r["prompt"]}],
                     "completion": [{"role": "assistant", "content": r["completion"]}]}
                    for r in records]
        for row in prepared:
            if len(tokenizer.apply_chat_template(row["prompt"] + row["completion"], tokenize=True)) > plan["max_length"]:
                raise ValueError("샘플이 승인된 문맥 길이를 초과합니다.")
        return prepared
    training_data, evaluation_data = clean(train), clean(evaluation)
    model = AutoModelForCausalLM.from_pretrained(
        local, local_files_only=True, trust_remote_code=False, use_safetensors=True,
        device_map={"": 0}, quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
        ),
    )
    model = prepare_model_for_kbit_training(model)
    bf16 = torch.cuda.is_bf16_supported()
    args = SFTConfig(
        output_dir=str(output), max_steps=plan["max_steps"], max_length=plan["max_length"],
        per_device_train_batch_size=1, per_device_eval_batch_size=1,
        gradient_accumulation_steps=8, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        learning_rate=0.0001, bf16=bf16, fp16=not bf16, packing=False,
        completion_only_loss=True, save_strategy="no", report_to="none",
        push_to_hub=False, seed=42, logging_steps=1,
    )
    trainer = SFTTrainer(
        model=model, processing_class=tokenizer, args=args,
        train_dataset=Dataset.from_list(training_data), eval_dataset=Dataset.from_list(evaluation_data),
        peft_config=LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05,
                              target_modules="all-linear", task_type="CAUSAL_LM"),
    )
    baseline = trainer.evaluate()
    trainer.train()
    after = trainer.evaluate()
    import math
    if not all(math.isfinite(metrics["eval_loss"]) for metrics in [baseline, after]):
        raise ValueError("평가 loss가 유효하지 않습니다.")
    trainer.save_model(str(output / "adapter"))
    tokenizer.save_pretrained(str(output / "adapter"))
    return {
        "execution": "gpu_sft_qlora", "baseline_eval_loss": baseline["eval_loss"],
        "trained_eval_loss": after["eval_loss"], "activation": "not_activated",
        "evaluation_scope": "held-out loss only; task-level benchmarks still required",
    }


def run_one(settings, trainer=run_sft):
    if not settings.training_enabled or settings.mode != "local":
        raise ValueError("ROLE_AI_TRAINING_ENABLED가 false입니다. GPU 학습을 실행하지 않습니다.")
    store = ArtifactStore(settings)
    engine, factory = build_database(settings.database_url)
    Base.metadata.create_all(engine)
    try:
        with storage_lock(store.root):
            with factory() as db:
                job = db.scalar(select(TrainingJob).where(TrainingJob.state == "queued")
                                .order_by(TrainingJob.created_at).limit(1))
                if not job:
                    return None
                claimed = db.execute(update(TrainingJob).where(
                    TrainingJob.id == job.id, TrainingJob.state == "queued"
                ).values(state="running").execution_options(synchronize_session=False))
                if claimed.rowcount != 1:
                    db.rollback()
                    return None
                db.commit()
                jid, uid, sid, approved = job.id, job.user_id, job.dataset_id, job.approval_hash
                item = db.get(DatasetSnapshot, sid)
                output = None
                try:
                    plan, reviews = json.loads(item.plan), json.loads(item.reviews)
                    if item.user_id != uid or item.approval_hash != approved or digest({
                        "plan": plan, "reviews": reviews, "data_hash": item.data_hash
                    }) != approved:
                        raise ValueError("승인된 계획이 변경됐습니다.")
                    if (plan["base_model_revision"] != settings.training_base_model_revision
                            or Path(plan["base_model_path"]).resolve()
                            != Path(settings.training_base_model_path).resolve()
                            or model_fingerprint(plan["base_model_path"]) != plan["base_model_fingerprint"]):
                        raise ValueError("승인된 기반 모델이 변경됐습니다.")
                    train, evaluation, _ = store.read(uid, sid, item.data_hash)
                    item.state = "running"
                    db.commit()
                    output = store.path(uid, sid) / ("run-" + jid)
                    output.mkdir(exist_ok=False)
                    report = trainer(plan, train, evaluation, output)
                    used = sum(p.stat().st_size for p in store.root.rglob("*") if p.is_file())
                    if used > settings.artifact_quota_bytes:
                        import shutil
                        shutil.rmtree(output)
                        raise ValueError("학습 결과 저장 한도를 초과했습니다.")
                    (output / "evaluation.json").write_text(canonical(report), encoding="utf-8")
                    final_state, dataset_state = "evaluated", "evaluated"
                except ResourcesBusy as exc:
                    if output is not None and output.is_dir() and not any(output.iterdir()):
                        output.rmdir()
                    report = {"reason": str(exc), "activation": "not_activated"}
                    final_state, dataset_state = "blocked", "queued"
                except Exception as exc:
                    report = {"reason": "학습 실행 또는 파일 검증에 실패했습니다.",
                              "error_type": type(exc).__name__, "activation": "not_activated"}
                    final_state, dataset_state = "failed", "reviewed"
                    item.plan, item.approval_hash = None, None
                job = db.get(TrainingJob, jid)
                job.state, job.report = final_state, canonical(report)
                item.state = dataset_state
                db.commit()
                return {"job_id": jid, "state": final_state, "report": report}
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", required=True)
    parser.parse_args()
    try:
        result = run_one(Settings())
        print(canonical(result or {"state": "idle"}))
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
