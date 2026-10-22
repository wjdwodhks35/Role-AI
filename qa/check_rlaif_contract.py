"""Actual TRL/LoRA gradient update on a random tiny CPU model; synthetic HTTP judge only.
No downloaded pretrained models, CUDA, paid API, user DB or serving activation.
"""
import copy
import json
import tempfile
from pathlib import Path

import httpx
import torch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast

from role_ai.ai_judge import AIJudge, RUBRIC
from role_ai.config import Settings
from role_ai.quality import suite_hash
from role_ai.rlaif import train_and_evaluate


def main():
    torch.set_num_threads(1)
    torch.manual_seed(42)
    vocab = {v: i for i, v in enumerate(['[PAD]', '[EOS]', '[UNK]', 'system', 'user', 'assistant',
                                       'question', 'answer', 'one', 'two', 'three', 'four', 'yes', 'no'])}
    raw = Tokenizer(WordLevel(vocab, unk_token='[UNK]'))
    raw.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=raw, unk_token='[UNK]',
                                       pad_token='[PAD]', eos_token='[EOS]', padding_side='left')
    tokenizer.chat_template = "{% for m in messages %}{{ m['role'] + ' ' + m['content'] + ' ' }}{% if m['role']=='assistant' %}{{ eos_token + ' ' }}{% endif %}{% endfor %}{% if add_generation_prompt %}{{ 'assistant ' }}{% endif %}"
    model = GPT2LMHeadModel(GPT2Config(vocab_size=len(vocab), n_positions=1024, n_embd=32,
        n_layer=1, n_head=2, bos_token_id=1, eos_token_id=1, pad_token_id=0,
        resid_pdrop=0, embd_pdrop=0, attn_pdrop=0))
    unadapted = copy.deepcopy(model)
    calls = []
    def handler(request):
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'fixture:tiny', 'digest': 'b' * 64}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['completion']})
        body = json.loads(request.content)
        assert body['options']['num_gpu'] == 0 and 'tools' not in body
        candidate = json.loads(body['messages'][1]['content'])['candidate']
        calls.append(candidate)
        point = 4 if len(calls) % 2 == 0 else 1
        return httpx.Response(200, json={'model': 'fixture:tiny', 'done': True, 'message': {'content': json.dumps({
            'correctness': point, 'instruction_following': point, 'constraint_compliance': point,
            'groundedness': point, 'uncertain': False, 'reason': 'synthetic transport fixture only'})}})
    settings = Settings(_env_file=None, rlaif_enabled=True, rlaif_judge_model='fixture:tiny')
    judge = AIJudge(settings, max_calls=8, max_seconds=300, transport=httpx.MockTransport(handler))
    cases = [{'prompt': 'question one', 'expected': 'answer one'},
             {'prompt': 'question two', 'expected': 'answer two'}]
    plan = {'max_steps': 1, 'max_length': 512, 'rlaif_max_completion_length': 8,
            'target_skill': 'developer', 'base_model_fingerprint': 'a' * 64,
            'benchmark': {'cases': cases, 'digest': suite_hash(cases)},
            'rlaif_rubric': RUBRIC, 'rlaif_minimum_benchmark_score': 0.5}
    with tempfile.TemporaryDirectory(prefix='role-ai-grpo-contract-') as folder:
        root = Path(folder)
        try:
            result = train_and_evaluate(model, tokenizer,
                [{'prompt': 'question three', 'completion': 'answer three'},
                 {'prompt': 'question four', 'completion': 'answer four'}],
                [{'prompt': 'question', 'completion': 'answer'}], plan, root, judge, cpu=True)
            assert result['optimizer_steps'] == 1 and result['policy_changed'] is True, result
            assert result['execution'] == 'cpu_grpo_contract' and not result['actual_gpu_verified']
            assert result['reward_records'] == len(calls) == 2
            records = [json.loads(line) for line in (root / 'rewards.jsonl').read_text().splitlines()]
            assert [r['completion'] for r in records] == calls
            assert all(r['prompt'] not in {c['prompt'] for c in cases} for r in records)
            assert (root / 'adapter' / 'adapter_model.safetensors').is_file()
            from peft import PeftModel
            continued = PeftModel.from_pretrained(unadapted, str(root / 'adapter'), is_trainable=True,
                                                  local_files_only=True)
            next_output = root / 'continuation'
            next_output.mkdir()
            calls.clear()
            resumed = train_and_evaluate(continued, tokenizer,
                [{'prompt': 'question three', 'completion': 'answer three'},
                 {'prompt': 'question four', 'completion': 'answer four'}],
                [{'prompt': 'question', 'completion': 'answer'}], plan, next_output, judge, cpu=True)
            assert resumed['optimizer_steps'] == 1 and resumed['policy_changed'] is True
            assert resumed['reward_records'] == len(calls) == 2
        finally:
            judge.close()
    print('PASS: real CPU GRPO/LoRA optimizer update, on-policy feedback, independent evaluation, adapter export')
    print('Synthetic judge only; GPU, quantization, actual AI judge quality and serving are unverified.')


if __name__ == '__main__':
    main()
