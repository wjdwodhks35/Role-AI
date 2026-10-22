"""Direct AI feedback + on-policy GRPO. Never activates a serving model."""
import hashlib
import math
import os

from role_ai.ai_judge import AIJudge, JudgeError, RewardRecorder, audit_digest
from role_ai.artifacts import canonical
from role_ai.gpu_resources import configure_allocator
from role_ai.quality import benchmark_result, suite_hash


def role_instruction(skill):
    from role_ai.skills import system_prompt
    return ('회의 원문을 근거로 주요 주제, 결정, Action Item을 정리하세요. 없는 사실을 만들지 마세요.'
            if skill == 'meeting' else system_prompt(skill))


def grpo_options(plan, output, *, cpu=False):
    return dict(output_dir=str(output), max_steps=plan['max_steps'],
                per_device_train_batch_size=1, gradient_accumulation_steps=2,
                num_generations=2, generation_batch_size=2, num_iterations=1,
                max_completion_length=plan['rlaif_max_completion_length'],
                learning_rate=0.00001, beta=0.02, use_vllm=False,
                gradient_checkpointing=not cpu,
                gradient_checkpointing_kwargs={'use_reentrant': False},
                remove_unused_columns=False, save_strategy='no', eval_strategy='no',
                report_to='none', push_to_hub=False, seed=42, logging_steps=1,
                log_completions=False, use_cpu=cpu)


def policy_fingerprint(model):
    value = hashlib.sha256()
    for name, parameter in model.named_parameters():
        if parameter.requires_grad:
            value.update(name.encode())
            value.update(parameter.detach().float().cpu().numpy().tobytes())
    return value.hexdigest()


def prepare_prompts(rows, tokenizer, role_prompt, max_length, completion_length):
    result = []
    for row in rows:
        prompt = [{'role': 'system', 'content': role_prompt}, {'role': 'user', 'content': row['prompt']}]
        if len(tokenizer.apply_chat_template(prompt, tokenize=True, add_generation_prompt=True)) + completion_length > max_length:
            raise ValueError('RLAIF 질문이 승인된 문맥 길이를 초과합니다. 자동으로 자르지 않습니다.')
        result.append({'prompt': prompt, 'reference': row['completion']})
    return result


def independent_evaluation(model, tokenizer, rows, benchmark, role_prompt, plan, check):
    """No judge feedback or benchmark answers enter GRPO's reward data."""
    import torch
    model.eval()
    weighted, count = 0.0, 0
    for row in rows:
        check()
        prompt = [{'role': 'system', 'content': role_prompt}, {'role': 'user', 'content': row['prompt']}]
        prefix = tokenizer.apply_chat_template(prompt, tokenize=True, add_generation_prompt=True)
        tokens = tokenizer.apply_chat_template(prompt + [{'role': 'assistant', 'content': row['completion']}], tokenize=True)
        if tokens[:len(prefix)] != prefix or len(tokens) <= len(prefix) or len(tokens) > plan['max_length']:
            raise ValueError('평가 토큰 정렬 또는 문맥 길이를 검증할 수 없습니다.')
        ids = torch.tensor([tokens], device=model.device)
        labels = ids.clone()
        labels[:, :len(prefix)] = -100
        with torch.inference_mode():
            loss = float(model(input_ids=ids, attention_mask=torch.ones_like(ids), labels=labels).loss)
        n = len(tokens) - len(prefix)
        weighted, count = weighted + loss * n, count + n
    responses = []
    for case in benchmark['cases']:
        check()
        prompt = [{'role': 'system', 'content': role_prompt}, {'role': 'user', 'content': case['prompt']}]
        tokens = tokenizer.apply_chat_template(prompt, tokenize=True, add_generation_prompt=True, return_tensors='pt')
        if tokens.shape[-1] + plan['rlaif_max_completion_length'] > plan['max_length']:
            raise ValueError('고정 평가 질문이 승인된 문맥 길이를 초과합니다.')
        tokens = tokens.to(model.device)
        with torch.inference_mode():
            generated = model.generate(tokens, attention_mask=torch.ones_like(tokens),
                                       max_new_tokens=plan['rlaif_max_completion_length'], do_sample=False,
                                       pad_token_id=tokenizer.pad_token_id)
        responses.append(tokenizer.decode(generated[0, tokens.shape[-1]:], skip_special_tokens=True))
    loss = weighted / count
    if not math.isfinite(loss):
        raise ValueError('유효한 held-out loss가 필요합니다.')
    return {'loss': loss, 'tasks': benchmark_result(benchmark['cases'], responses)}


def train_and_evaluate(model, tokenizer, rows, evaluation, plan, output, judge, monitor=None, *, cpu=False):
    """Shared engine; CPU contract uses a locally created tiny model, never a pretrained download."""
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import TrainerCallback
    from trl import GRPOConfig, GRPOTrainer
    from peft import PeftModel

    benchmark = plan['benchmark']
    if suite_hash(benchmark['cases']) != benchmark['digest']:
        raise ValueError('고정 평가 질문 해시가 변경됐습니다.')
    instruction = role_instruction(plan['target_skill'])
    prompts = prepare_prompts(rows, tokenizer, instruction, plan['max_length'], plan['rlaif_max_completion_length'])
    recorder = RewardRecorder(judge, output / 'rewards.jsonl', model_identity={
        'base': plan['base_model_fingerprint'], 'parent': plan.get('parent_adapter_fingerprint')}, role_prompt=instruction,
        max_bytes=plan.get('_reward_byte_limit', 4194304))
    # TRL names metrics from this attribute when a callable object is supplied.
    recorder.__name__ = 'local_ai_feedback'
    config = GRPOConfig(**grpo_options(plan, output, cpu=cpu))
    peft = None if isinstance(model, PeftModel) else LoraConfig(
        r=8, lora_alpha=16, lora_dropout=0.0, target_modules='all-linear', task_type='CAUSAL_LM')
    trainer = GRPOTrainer(model=model, processing_class=tokenizer, args=config,
                          train_dataset=Dataset.from_list(prompts), reward_funcs=recorder, peft_config=peft)

    def check():
        judge.check_budget()
        if monitor:
            monitor.check_cancel()

    class Progress(TrainerCallback):
        def on_step_begin(self, args, state, control, **kwargs):
            check()
        def on_substep_end(self, args, state, control, **kwargs):
            check()
        def on_step_end(self, args, state, control, **kwargs):
            check()
            if monitor:
                monitor.stage('rlaif_training', completed=state.global_step, total=state.max_steps)
    trainer.add_callback(Progress())
    if monitor:
        monitor.stage('rlaif_baseline_evaluation')
    before = independent_evaluation(trainer.model, tokenizer, evaluation, benchmark, instruction, plan, check)
    initial = policy_fingerprint(trainer.model)
    if monitor:
        monitor.stage('rlaif_training', completed=0, total=plan['max_steps'])
    trainer.train()
    if trainer.state.global_step < 1 or recorder.records < 2:
        raise ValueError('실제 강화학습 스텝과 AI 보상 기록이 필요합니다.')
    changed = initial != policy_fingerprint(trainer.model)
    if monitor:
        monitor.stage('rlaif_independent_evaluation')
    after = independent_evaluation(trainer.model, tokenizer, evaluation, benchmark, instruction, plan, check)
    check()
    judge.verify()
    trainer.save_model(str(output / 'adapter'))
    tokenizer.save_pretrained(str(output / 'adapter'))
    from importlib.metadata import version
    return {'runtime': {key: version(key) for key in ['torch', 'trl', 'transformers', 'peft']},
            'parameters': {key: getattr(config, key) for key in ['max_steps', 'num_generations',
                           'gradient_accumulation_steps', 'max_completion_length', 'beta', 'learning_rate']},
            'execution': 'cpu_grpo_contract' if cpu else 'gpu_rlaif_grpo_qlora',
            'method': 'direct_rlaif_grpo', 'target_skill': plan['target_skill'], 'activation': 'not_activated',
            'optimizer_steps': trainer.state.global_step, 'policy_changed': changed,
            'judge': judge.identity, 'rubric': plan['rlaif_rubric'],
            'judge_calls': judge.calls, 'reward_records': recorder.records,
            'reward_audit_hash': audit_digest(output / 'rewards.jsonl'),
            'baseline_eval_loss': before['loss'], 'trained_eval_loss': after['loss'],
            'benchmark': {'digest': benchmark['digest'], 'baseline': before['tasks'], 'trained': after['tasks']},
            'minimum_benchmark_score': plan['rlaif_minimum_benchmark_score'],
            'evaluation_scope': 'independent held-out loss and fixed exact-match questions; not general quality',
            'reference_policy': 'local_base_without_adapter', 'actual_gpu_verified': not cpu}


def run_rlaif(plan, train, evaluation, output):
    import torch
    from role_ai.training import ResourcesBusy
    judge = AIJudge(plan['_rlaif_settings'], approved=plan['rlaif_judge'],
                    max_calls=plan['rlaif_max_judge_calls'], max_seconds=plan['rlaif_max_seconds'],
                    monitor=plan.get('_worker_monitor'))
    try:
        calibration = judge.calibrate()
        (output / 'judge-calibration.json').write_text(canonical(calibration), encoding='utf-8')
        if not calibration['passed']:
            raise JudgeError('평가 AI의 기준 확인에 실패했습니다.')
        if not torch.cuda.is_available():
            raise ResourcesBusy('NVIDIA CUDA 환경이 준비되지 않았습니다.')
        free, total = torch.cuda.mem_get_info()
        limits = plan.get('resource_limits', {'enabled': False})
        configure_allocator(torch, limits, total)
        if not limits.get('enabled') and free / total < 0.85:
            raise ResourcesBusy('학습 전에 GPU 메모리를 확보하세요.')
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from peft import PeftModel, prepare_model_for_kbit_training
        local = plan['base_model_path']
        tokenizer = AutoTokenizer.from_pretrained(local, local_files_only=True, trust_remote_code=False)
        if not tokenizer.chat_template:
            raise ValueError('대화 템플릿이 필요합니다.')
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        tokenizer.padding_side = 'left'
        model = AutoModelForCausalLM.from_pretrained(local, local_files_only=True,
            trust_remote_code=False, use_safetensors=True, device_map={'': 0},
            quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16))
        model = prepare_model_for_kbit_training(model)
        if plan.get('parent_adapter_path'):
            model = PeftModel.from_pretrained(model, plan['parent_adapter_path'], is_trainable=True,
                                             local_files_only=True)
        report = train_and_evaluate(model, tokenizer, train, evaluation, plan, output, judge, plan.get('_worker_monitor'))
        report['calibration'] = calibration
        return report
    except torch.cuda.OutOfMemoryError:
        raise ResourcesBusy('RLAIF 학습에 사용할 GPU 메모리가 부족합니다.') from None
    finally:
        judge.close()
