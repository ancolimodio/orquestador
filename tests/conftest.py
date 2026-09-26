import json
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from prompt_maestro.models import CheckResult, GateResult


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repositorio mínimo de ejemplo."""
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app" / "calc.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from app.calc import add\n\n\ndef test_add() -> None:\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("# Reglas\n- Código async.\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=nope\n", encoding="utf-8")
    return tmp_path


class FakeGateRunner:
    """Devuelve resultados de gates predefinidos; por defecto todos pasan."""

    def __init__(self, failures: dict[str, int] | None = None) -> None:
        self._failures = {gate: deque(range(n)) for gate, n in (failures or {}).items()}
        self.runs: list[str] = []

    async def run_gate(self, gate: str) -> GateResult:
        self.runs.append(gate)
        pending = self._failures.get(gate)
        if pending:
            pending.popleft()
            check = CheckResult(name=f"check-{gate}", command=["fake"], returncode=1, output="boom")
        else:
            check = CheckResult(name=f"check-{gate}", command=["fake"], returncode=0, output="")
        return GateResult(gate=gate, checks=[check])


def plan_json(**overrides: Any) -> str:
    data: dict[str, Any] = {
        "task_id": "ignored",
        "goal": "Agregar la función subtract",
        "impacted": [{"path": "src/app/calc.py", "symbols": ["add"], "change": "modify"}],
        "acceptance_criteria": ["subtract(5, 3) devuelve 2"],
        "risks": [],
        "open_questions": [],
    }
    data.update(overrides)
    return json.dumps(data)


def changes_json(path: str, content: str) -> str:
    return json.dumps({"changes": [{"path": path, "content": content}], "notes": ""})


IMPL = changes_json(
    "src/app/calc.py",
    "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
    "def subtract(a: int, b: int) -> int:\n    return a - b\n",
)
TESTS = changes_json(
    "tests/test_subtract.py",
    "from app.calc import subtract\n\n\ndef test_subtract() -> None:\n    assert subtract(5, 3) == 2\n",
)


def review_json(verdict: str = "approve", findings: list[dict[str, Any]] | None = None) -> str:
    return json.dumps(
        {
            "task_id": "ignored",
            "verdict": verdict,
            "findings": findings or [],
            "acceptance_criteria_met": verdict == "approve",
        }
    )
