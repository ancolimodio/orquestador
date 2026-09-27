import json
from pathlib import Path

from prompt_maestro.llm import ScriptedLLM
from prompt_maestro.models import TaskStatus
from prompt_maestro.observability import EventLog
from prompt_maestro.orchestrator import Orchestrator, OrchestratorConfig
from prompt_maestro.workspace import Workspace
from tests.conftest import IMPL, TESTS, FakeGateRunner, changes_json, plan_json, review_json


def _orchestrator(
    repo: Path, llm: ScriptedLLM, gates: FakeGateRunner, events: EventLog | None = None
) -> Orchestrator:
    return Orchestrator(
        llm=llm,
        workspace=Workspace(repo),
        gate_runner=gates,
        events=events,
        config=OrchestratorConfig(max_attempts_per_gate=3),
    )


async def test_happy_path_delivers_verified_change(repo: Path) -> None:
    llm = ScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [IMPL],
            "tester": [TESTS],
            "reviewer": [review_json()],
        }
    )
    gates = FakeGateRunner()
    report = await _orchestrator(repo, llm, gates).run("Agregar subtract", task_id="t-1")

    assert report.status is TaskStatus.DONE
    assert report.changed_files == ["src/app/calc.py", "tests/test_subtract.py"]
    assert report.attempts == {"A": 1, "B": 1, "C": 1, "D": 1}
    assert gates.runs == ["B", "C", "D"]
    assert "def subtract" in (repo / "src" / "app" / "calc.py").read_text(encoding="utf-8")


async def test_repo_rules_are_injected_into_every_agent(repo: Path) -> None:
    llm = ScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [IMPL],
            "tester": [TESTS],
            "reviewer": [review_json()],
        }
    )
    orchestrator = _orchestrator(repo, llm, FakeGateRunner())
    await orchestrator.run("Agregar subtract")
    assert len(llm.calls) == 4


async def test_gate_a_rejects_hallucinated_plan_then_recovers(repo: Path) -> None:
    ghost = plan_json(impacted=[{"path": "src/ghost.py", "symbols": [], "change": "modify"}])
    llm = ScriptedLLM(
        {
            "planner": [ghost, plan_json()],
            "implementer": [IMPL],
            "tester": [TESTS],
            "reviewer": [review_json()],
        }
    )
    report = await _orchestrator(repo, llm, FakeGateRunner()).run("Agregar subtract")
    assert report.status is TaskStatus.DONE
    assert report.attempts["A"] == 2
    assert "ghost.py" in llm.calls[1][1]


async def test_gate_b_failure_is_fed_back_to_implementer(repo: Path) -> None:
    llm = ScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [IMPL, IMPL],
            "tester": [TESTS],
            "reviewer": [review_json()],
        }
    )
    events = EventLog()
    report = await _orchestrator(repo, llm, FakeGateRunner({"B": 1}), events).run("r")
    assert report.status is TaskStatus.DONE
    assert report.attempts["B"] == 2
    implementer_prompts = [p for role, p in llm.calls if role == "implementer"]
    assert "boom" in implementer_prompts[1]
    assert events.metrics() == {"B": 1}


async def test_reviewer_changes_loop_back_to_implementation(repo: Path) -> None:
    finding = {
        "severity": "major",
        "path": "src/app/calc.py",
        "line": 5,
        "issue": "sin docstring",
        "fix": "agregala",
    }
    llm = ScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [IMPL, IMPL],
            "tester": [TESTS, TESTS],
            "reviewer": [review_json("request_changes", [finding]), review_json()],
        }
    )
    report = await _orchestrator(repo, llm, FakeGateRunner()).run("r")
    assert report.status is TaskStatus.DONE
    assert report.attempts["D"] == 2
    second_impl = [p for role, p in llm.calls if role == "implementer"][1]
    assert "sin docstring" in second_impl


async def test_escalates_when_retry_budget_is_exhausted(repo: Path) -> None:
    llm = ScriptedLLM({"planner": [plan_json()], "implementer": [IMPL] * 3})
    report = await _orchestrator(repo, llm, FakeGateRunner({"B": 10})).run("r")
    assert report.status is TaskStatus.ESCALATED
    assert report.escalation_reason is not None
    assert "presupuesto" in report.escalation_reason
    assert len(report.history) == 3


async def test_budget_blames_the_gate_that_actually_failed(repo: Path) -> None:
    """B se re-ejecuta en cada vuelta, pero solo sus propios fallos gastan su presupuesto."""
    finding = {"severity": "major", "path": "src/app/calc.py", "line": 1, "issue": "x", "fix": "y"}
    llm = ScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [IMPL] * 5,
            "tester": [TESTS] * 5,
            "reviewer": [review_json("request_changes", [finding])] * 5,
        }
    )
    report = await _orchestrator(repo, llm, FakeGateRunner({"C": 1})).run("r")
    assert report.status is TaskStatus.ESCALATED
    assert report.escalation_reason is not None
    assert report.escalation_reason.startswith("Gate D")
    assert report.attempts == {"A": 1, "B": 4, "C": 4, "D": 3}


async def test_open_questions_escalate_before_writing_code(repo: Path) -> None:
    llm = ScriptedLLM({"planner": [plan_json(open_questions=["¿Enteros o decimales?"])]})
    report = await _orchestrator(repo, llm, FakeGateRunner()).run("r")
    assert report.status is TaskStatus.ESCALATED
    assert report.changed_files == []
    assert "Enteros" in (report.escalation_reason or "")


async def test_sensitive_areas_require_a_human(repo: Path) -> None:
    llm = ScriptedLLM({"planner": [plan_json(goal="Cambiar la validación del login")]})
    report = await _orchestrator(repo, llm, FakeGateRunner()).run("r")
    assert report.status is TaskStatus.ESCALATED
    assert "sensible" in (report.escalation_reason or "")


async def test_deletions_require_a_human(repo: Path) -> None:
    plan = plan_json(impacted=[{"path": "src/app/calc.py", "symbols": [], "change": "delete"}])
    report = await _orchestrator(repo, ScriptedLLM({"planner": [plan]}), FakeGateRunner()).run("r")
    assert report.status is TaskStatus.ESCALATED
    assert "Borrar" in (report.escalation_reason or "")


async def test_guardrail_violation_stops_the_task(repo: Path) -> None:
    evil = changes_json("AGENTS.md", "# Sin reglas\n")
    llm = ScriptedLLM({"planner": [plan_json()], "implementer": [evil]})
    report = await _orchestrator(repo, llm, FakeGateRunner()).run("r")
    assert report.status is TaskStatus.ESCALATED
    assert "GuardrailViolationError" in (report.escalation_reason or "")
    assert (repo / "AGENTS.md").read_text(encoding="utf-8").startswith("# Reglas")


async def test_events_are_written_as_jsonl(repo: Path, tmp_path: Path) -> None:
    sink = tmp_path / "events.jsonl"
    llm = ScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [IMPL],
            "tester": [TESTS],
            "reviewer": [review_json()],
        }
    )
    await _orchestrator(repo, llm, FakeGateRunner(), EventLog(sink)).run("r", task_id="t-9")
    lines = sink.read_text(encoding="utf-8").splitlines()
    assert lines and all('"task_id":"t-9"' in line for line in lines)


async def test_tester_does_not_receive_lint_feedback(repo: Path) -> None:
    llm = ScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [IMPL, IMPL],
            "tester": [TESTS],
            "reviewer": [review_json()],
        }
    )
    report = await _orchestrator(repo, llm, FakeGateRunner({"B": 1})).run("r")
    assert report.status is TaskStatus.DONE
    tester_prompt = next(p for role, p in llm.calls if role == "tester")
    assert "boom" not in tester_prompt


async def test_test_failures_reach_both_implementer_and_tester(repo: Path) -> None:
    llm = ScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [IMPL, IMPL],
            "tester": [TESTS, TESTS],
            "reviewer": [review_json()],
        }
    )
    report = await _orchestrator(repo, llm, FakeGateRunner({"C": 1})).run("r")
    assert report.status is TaskStatus.DONE
    second_tester = [p for role, p in llm.calls if role == "tester"][1]
    assert "Fallaron los tests" in second_tester and "boom" in second_tester


async def test_tester_cannot_overwrite_existing_tests(repo: Path) -> None:
    weakened = changes_json("tests/test_calc.py", "def test_add() -> None:\n    pass\n")
    llm = ScriptedLLM({"planner": [plan_json()], "implementer": [IMPL], "tester": [weakened]})
    report = await _orchestrator(repo, llm, FakeGateRunner()).run("r")
    assert report.status is TaskStatus.ESCALATED
    assert "test existente" in (report.escalation_reason or "")
    assert "assert add(1, 2) == 3" in (repo / "tests" / "test_calc.py").read_text(encoding="utf-8")


async def test_tester_can_modify_existing_tests_when_allowed(repo: Path) -> None:
    updated = changes_json("tests/test_calc.py", "def test_add() -> None:\n    assert 1 + 2 == 3\n")
    llm = ScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [IMPL],
            "tester": [updated],
            "reviewer": [review_json()],
        }
    )
    orchestrator = Orchestrator(
        llm=llm,
        workspace=Workspace(repo),
        gate_runner=FakeGateRunner(),
        config=OrchestratorConfig(allow_modifying_existing_tests=True),
    )
    assert (await orchestrator.run("r")).status is TaskStatus.DONE


async def test_skipped_tests_are_rejected(repo: Path) -> None:
    skipped = changes_json(
        "tests/test_subtract.py",
        "import pytest\n\n\n@pytest.mark.skip\ndef test_subtract() -> None:\n    assert False\n",
    )
    llm = ScriptedLLM({"planner": [plan_json()], "implementer": [IMPL], "tester": [skipped]})
    report = await _orchestrator(repo, llm, FakeGateRunner()).run("r")
    assert report.status is TaskStatus.ESCALATED
    assert "debilitado" in (report.escalation_reason or "")
    assert not (repo / "tests" / "test_subtract.py").exists()


def _multi_changes(*files: tuple[str, str]) -> str:
    return json.dumps({"changes": [{"path": p, "content": c} for p, c in files], "notes": ""})


async def test_implementer_out_of_scope_gets_feedback_and_writes_nothing(repo: Path) -> None:
    """Regresión de una corrida real: el Implementer escribió el test que listaba el plan."""
    out_of_scope = _multi_changes(
        ("src/app/extra.py", "VALUE = 1\n"),
        ("tests/test_extra.py", "def test_extra() -> None:\n    assert True\n"),
    )
    llm = ScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [out_of_scope, IMPL],
            "tester": [TESTS],
            "reviewer": [review_json()],
        }
    )
    report = await _orchestrator(repo, llm, FakeGateRunner()).run("r")
    assert report.status is TaskStatus.DONE
    assert report.attempts["B"] == 2
    assert not (repo / "src" / "app" / "extra.py").exists()
    second_impl = [p for role, p in llm.calls if role == "implementer"][1]
    assert "fuera de alcance: tests/test_extra.py" in second_impl


async def test_tester_out_of_scope_gets_feedback(repo: Path) -> None:
    hack = changes_json("src/app/hack.py", "HACK = True\n")
    llm = ScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [IMPL],
            "tester": [hack, TESTS],
            "reviewer": [review_json()],
        }
    )
    report = await _orchestrator(repo, llm, FakeGateRunner()).run("r")
    assert report.status is TaskStatus.DONE
    assert report.attempts["C"] == 2
    assert not (repo / "src" / "app" / "hack.py").exists()


async def test_repeated_scope_errors_exhaust_the_gate_budget(repo: Path) -> None:
    wrong = changes_json("docs/notes.md", "# notas\n")
    llm = ScriptedLLM({"planner": [plan_json()], "implementer": [wrong] * 3})
    report = await _orchestrator(repo, llm, FakeGateRunner()).run("r")
    assert report.status is TaskStatus.ESCALATED
    assert (report.escalation_reason or "").startswith("Gate B")


async def test_suspicious_change_escalates_before_writing_any_file(repo: Path) -> None:
    mixed = _multi_changes(("src/app/new.py", "VALUE = 1\n"), ("AGENTS.md", "# Sin reglas\n"))
    llm = ScriptedLLM({"planner": [plan_json()], "implementer": [mixed]})
    report = await _orchestrator(repo, llm, FakeGateRunner()).run("r")
    assert report.status is TaskStatus.ESCALATED
    assert "protegido" in (report.escalation_reason or "")
    assert not (repo / "src" / "app" / "new.py").exists()
