"""Demo end-to-end sin API key.

Crea un repo temporal, simula al modelo con ScriptedLLM y ejecuta verificación REAL:
compila el código y corre pytest dentro del Sandbox. Incluye un fallo intencional
para mostrar el loop de verificación en acción.

    python examples/demo.py
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from prompt_maestro import Orchestrator, OrchestratorConfig
from prompt_maestro.gates import CheckSpec, SandboxGateRunner
from prompt_maestro.llm import ScriptedLLM
from prompt_maestro.observability import EventLog
from prompt_maestro.sandbox import Sandbox
from prompt_maestro.workspace import Workspace

CALC = "def add(a: int, b: int) -> int:\n    return a + b\n"

BUGGY = CALC + "\n\ndef subtract(a: int, b: int) -> int:\n    return a + b  # bug intencional\n"
FIXED = CALC + "\n\ndef subtract(a: int, b: int) -> int:\n    return a - b\n"
TEST = (
    "from app.calc import subtract\n\n\n"
    "def test_subtract() -> None:\n    assert subtract(5, 3) == 2\n\n\n"
    "def test_subtract_negative() -> None:\n    assert subtract(3, 5) == -2\n"
)


def _changes(path: str, content: str) -> str:
    return json.dumps({"changes": [{"path": path, "content": content}], "notes": ""})


def _scripted_model() -> ScriptedLLM:
    plan = {
        "task_id": "demo",
        "goal": "Agregar la función subtract a la calculadora",
        "impacted": [{"path": "src/app/calc.py", "symbols": ["add"], "change": "modify"}],
        "acceptance_criteria": ["subtract(5, 3) == 2", "subtract(3, 5) == -2"],
        "risks": [],
        "open_questions": [],
    }
    review = {
        "task_id": "demo",
        "verdict": "approve",
        "findings": [],
        "acceptance_criteria_met": True,
    }
    return ScriptedLLM(
        {
            "planner": [json.dumps(plan)],
            "implementer": [_changes("src/app/calc.py", BUGGY), _changes("src/app/calc.py", FIXED)],
            "tester": [_changes("tests/test_subtract.py", TEST)] * 2,
            "reviewer": [json.dumps(review)],
        }
    )


def _create_repo(root: Path) -> None:
    (root / "src" / "app").mkdir(parents=True)
    (root / "src" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "app" / "calc.py").write_text(CALC, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n', encoding="utf-8"
    )


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _create_repo(root)
        gates = {
            "B": (CheckSpec("compile", (sys.executable, "-m", "compileall", "-q", "src")),),
            "C": (
                CheckSpec(
                    "pytest", (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")
                ),
            ),
        }
        events = EventLog()
        orchestrator = Orchestrator(
            llm=_scripted_model(),
            workspace=Workspace(root),
            gate_runner=SandboxGateRunner(Sandbox(root), gates),
            events=events,
            config=OrchestratorConfig(),
        )
        report = await orchestrator.run("Agregá una función subtract a la calculadora.")

        print("\nEventos:")
        for ev in events.events:
            print(
                f"  [{ev.phase:>8}] {ev.agent:<12} {ev.action:<5} → {ev.result} (intento {ev.attempt})"
            )
        print(f"\nEstado: {report.status.value}")
        print(f"Intentos por gate: {report.attempts}")
        print(f"Archivos cambiados: {', '.join(report.changed_files)}")
        return 0 if report.status.value == "done" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
