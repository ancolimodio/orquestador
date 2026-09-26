import pytest

from prompt_maestro.agents import Planner, extract_json
from prompt_maestro.errors import HandoffValidationError
from prompt_maestro.llm import ScriptedLLM
from prompt_maestro.prompts import build_system_prompt
from tests.conftest import plan_json


def test_extract_json_handles_markdown_fences() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_json_ignores_surrounding_text() -> None:
    assert extract_json('Acá va el plan: {"a": {"b": 2}} listo.') == '{"a": {"b": 2}}'


def test_extract_json_without_object_fails() -> None:
    with pytest.raises(HandoffValidationError):
        extract_json("no hay json")


def test_system_prompt_injects_repo_rules() -> None:
    prompt = build_system_prompt("planner", "- Nunca uses print.")
    assert "Planner" in prompt
    assert "<reglas_del_repositorio>" in prompt and "Nunca uses print" in prompt


async def test_agent_retries_invalid_handoff_with_feedback() -> None:
    llm = ScriptedLLM({"planner": ["esto no es json", plan_json()]})
    plan = await Planner(llm).run(task_id="t-1", requirement="r", repo_map=["src/app/calc.py"])
    assert plan.task_id == "t-1"
    assert len(llm.calls) == 2
    assert "no cumplió el contrato" in llm.calls[1][1]


async def test_agent_gives_up_after_format_retries() -> None:
    llm = ScriptedLLM({"planner": ["{}", "{}", "{}"]})
    with pytest.raises(HandoffValidationError):
        await Planner(llm, max_format_retries=2).run(task_id="t", requirement="r", repo_map=[])
