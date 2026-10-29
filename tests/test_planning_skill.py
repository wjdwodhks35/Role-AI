"""Planning specialist is opt-in and has no execution privileges."""
from role_ai.skills import SKILLS, system_prompt
from role_ai.schemas import ChatRequest


def test_planning_skill_is_selectable_and_versioned():
    assert "planning" in SKILLS
    prompt = system_prompt("planning")
    for phrase in ("요구사항", "미래 결과 예측", "Critical Review", "수정할 곳", "롤백", "최종"):
        assert phrase in prompt
    assert ChatRequest(content="기획서를 작성해줘", skill="planning").skill == "planning"


def test_planning_skill_never_grants_tool_execution():
    prompt = system_prompt("planning")
    assert "스스로 수행하지 않는다" in prompt
    assert "실행했다고 주장하지" in prompt


def test_existing_skills_preserved():
    for key in ("assistant", "developer", "code_tester", "professor", "doctor", "counselor"):
        assert key in SKILLS
