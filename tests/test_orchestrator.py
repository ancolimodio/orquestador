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
