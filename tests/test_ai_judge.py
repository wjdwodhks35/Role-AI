import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from role_ai.ai_judge import AIJudge, Feedback, JudgeError, RewardRecorder, RUBRIC_HASH
from role_ai.config import Settings


def score(value=4, **kwargs):
    return {'correctness': value, 'instruction_following': value,
            'constraint_compliance': value, 'groundedness': value,
            'uncertain': False, 'reason': 'synthetic judge', **kwargs}


def transport(mode='valid', calls=None):
    def handler(request):
        if calls is not None:
            calls.append(request)
        if mode == 'network':
            raise httpx.ConnectError('PRIVATE_TOKEN', request=request)
        if mode == 'redirect':
            return httpx.Response(302, headers={'location': 'http://external.invalid'})
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'judge:test', 'digest': 'b' * 64}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['completion'],
                                            'remote_model': 'cloud' if mode == 'cloud' else ''})
        body = json.loads(request.content)
        assert body['options']['num_gpu'] == 0 and 'tools' not in body
        data = json.loads(body['messages'][1]['content'])
        value = 4 if mode == 'all_good' else (0 if data['candidate'].startswith('5') else 4)
        feedback = score(value, uncertain=mode == 'uncertain')
        if mode == 'schema':
            feedback['correctness'] = '4'
        text = '```json\n{}\n```' if mode == 'fenced' else json.dumps(feedback)
        return httpx.Response(200, json={'model': 'wrong' if mode == 'model' else 'judge:test',
            'done': mode != 'incomplete', 'message': {'content': text}})
    return httpx.MockTransport(handler)


def judge(**kwargs):
    return AIJudge(Settings(_env_file=None, rlaif_enabled=True, rlaif_judge_model='judge:test'), **kwargs)


@pytest.mark.parametrize('mutation', [{'correctness': True}, {'groundedness': 5},
    {'instruction_following': '4'}, {'uncertain': 'false'}, {'reason': ' '}, {'extra': 1}])
def test_judge_feedback_strict_schema(mutation):
    with pytest.raises(ValidationError):
        Feedback.model_validate(score(**mutation))


def test_reward_weighting_uncertainty_and_zero_constraints():
    assert Feedback.model_validate(score()).reward() == 1
    assert Feedback.model_validate(score(correctness=0)).reward() == 0
    assert Feedback.model_validate(score(constraint_compliance=0)).reward() == 0
    assert Feedback.model_validate(score(correctness=2)).reward() == 0.8
    with pytest.raises(JudgeError):
        Feedback.model_validate(score(uncertain=True)).reward()


@pytest.mark.parametrize('mode', ['network', 'redirect', 'cloud', 'uncertain', 'schema', 'fenced', 'model', 'incomplete'])
def test_judge_failures_never_become_zero_rewards(mode):
    item = judge(transport=transport(mode))
    try:
        with pytest.raises(JudgeError) as exc:
            item.score('request', 'answer')
        assert 'PRIVATE' not in str(exc.value)
    finally:
        item.close()


def test_pinning_calibration_and_budget():
    item = judge(transport=transport(), max_calls=3)
    try:
        result = item.calibrate()
        assert result['passed'] is True and len(result['cases']) == 3
        assert item.identity['rubric_hash'] == RUBRIC_HASH
        with pytest.raises(JudgeError):
            item.score('request', 'answer')
    finally:
        item.close()
    item = judge(transport=transport(), approved={'digest': 'changed'})
    try:
        with pytest.raises(JudgeError):
            item.verify()
    finally:
        item.close()


@pytest.mark.parametrize('url', ['http://external.invalid', 'https://localhost', 'http://secret:key@localhost', 'http://localhost/api'])
def test_judge_local_endpoint_only(url):
    with pytest.raises(JudgeError):
        AIJudge(Settings(_env_file=None, rlaif_enabled=True,
                         rlaif_judge_model='judge:test', rlaif_judge_url=url))


def test_judge_disabled_and_deadline_guards():
    with pytest.raises(JudgeError):
        AIJudge(Settings(_env_file=None))
    item = judge(transport=transport(), max_seconds=-1)
    try:
        with pytest.raises(JudgeError):
            item.verify()
    finally:
        item.close()


def test_reward_recorder_preserves_exact_rollouts_and_step(tmp_path):
    item = judge(transport=transport())
    path = tmp_path / 'rewards.jsonl'
    recorder = RewardRecorder(item, path, model_identity={'base': 'fixed'}, role_prompt='role')
    try:
        values = recorder(prompts=[[{'role': 'system', 'content': 'role'}, {'role': 'user', 'content': 'request'}]],
            completions=[[{'role': 'assistant', 'content': 'answer'}]], reference=['reference'],
            trainer_state=SimpleNamespace(global_step=7))
        assert values == [1] and recorder.records == 1
        data = json.loads(path.read_text())
        assert data['prompt'] == 'request' and data['completion'] == 'answer'
        assert data['reference'] == 'reference' and data['step'] == 7
        assert data['judge']['digest'] == 'b' * 64
        with pytest.raises(JudgeError):
            recorder(prompts=[], completions=[[]], reference=[])
    finally:
        item.close()


def test_recorder_failure_does_not_write_fabricated_reward(tmp_path):
    item = judge(transport=transport('uncertain'))
    path = tmp_path / 'rewards.jsonl'
    recorder = RewardRecorder(item, path, model_identity={}, role_prompt='role')
    try:
        with pytest.raises(JudgeError):
            recorder(prompts=[[{'role': 'system', 'content': 'role'}, {'role': 'user', 'content': 'request'}]],
                     completions=[[{'role': 'assistant', 'content': 'answer'}]], reference=['reference'])
        assert not path.exists()
    finally:
        item.close()
