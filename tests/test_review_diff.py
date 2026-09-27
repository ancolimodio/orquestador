from pathlib import Path

from prompt_maestro.diffs import unified_diff
from prompt_maestro.llm import ScriptedLLM
from prompt_maestro.models import TaskStatus
from prompt_maestro.orchestrator import Orchestrator
from prompt_maestro.workspace import Workspace
from tests.conftest import TESTS, FakeGateRunner, changes_json, plan_json, review_json

# Un archivo grande: el caso real fue un Dashboard.tsx de 717 líneas reescrito entero.
BIG = "".join(f"CONSTANT_{i} = {i}\n" for i in range(300))
BIG_WITH_CHANGE = BIG.replace(
    "CONSTANT_10 = 10\n", "CONSTANT_10 = 10\nDISASTER_FIX = False  # fuera del plan\n"
) + ("\n\ndef subtract(a: int, b: int) -> int:\n    return a - b\n")


def test_unified_diff_shows_only_touched_lines() -> None:
    diff = unified_diff("src/big.py", BIG, BIG_WITH_CHANGE)
    assert "+DISASTER_FIX = False  # fuera del plan" in diff
    assert "+def subtract(a: int, b: int) -> int:" in diff
    assert "CONSTANT_150" not in diff
    assert diff.startswith("--- a/src/big.py\n+++ b/src/big.py")


def test_unified_diff_of_new_and_unchanged_files() -> None:
    assert unified_diff("src/new.py", None, "X = 1\n").startswith("--- /dev/null")
    assert unified_diff("src/same.py", "X = 1\n", "X = 1\n") == ""


async def test_reviewer_sees_a_diff_against_the_original_not_whole_files(repo: Path) -> None:
    """Regresión de una corrida real: una línea basura en un archivo grande pasó la revisión.

    El Reviewer recibía los archivos completos; ahora recibe el diff contra el repo previo
    a la tarea, aunque el Implementer haya reescrito el archivo en varias vueltas.
    """
    (repo / "src" / "app" / "big.py").write_text(BIG, encoding="utf-8")
    plan = plan_json(
        impacted=[
            {
                "path": "src/app/big.py",
                "symbols": ["CONSTANT_10"],
                "new_symbols": ["subtract"],
                "change": "modify",
            }
        ]
    )
    first_try = changes_json("src/app/big.py", BIG + "roto(\n")
    llm = ScriptedLLM(
        {
            "planner": [plan],
            "implementer": [first_try, changes_json("src/app/big.py", BIG_WITH_CHANGE)],
            "tester": [TESTS],
            "reviewer": [review_json()],
        }
    )
    report = await Orchestrator(
        llm=llm, workspace=Workspace(repo), gate_runner=FakeGateRunner({"B": 1})
    ).run("r")
    assert report.status is TaskStatus.DONE

    review_prompt = next(p for role, p in llm.calls if role == "reviewer")
    assert "+DISASTER_FIX = False  # fuera del plan" in review_prompt
    assert "CONSTANT_150" not in review_prompt  # el resto del archivo no tapa el cambio
    assert "roto(" not in review_prompt  # la vuelta fallida no deja rastro en el diff
    assert "--- /dev/null\n+++ b/tests/test_subtract.py" in review_prompt
