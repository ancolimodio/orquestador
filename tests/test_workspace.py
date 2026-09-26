from pathlib import Path

import pytest

from prompt_maestro.errors import GuardrailViolationError
from prompt_maestro.models import ChangeKind, ImpactedItem, Plan
from prompt_maestro.workspace import Workspace


def _plan(*items: ImpactedItem) -> Plan:
    return Plan(task_id="t", goal="g", impacted=list(items), acceptance_criteria=["a"])


async def test_repo_map_excludes_secret_files(repo: Path) -> None:
    files = await Workspace(repo).repo_map()
    assert "src/app/calc.py" in files
    assert ".env" not in files


async def test_path_traversal_is_blocked(repo: Path) -> None:
    with pytest.raises(GuardrailViolationError):
        await Workspace(repo).read("../../etc/passwd")


async def test_secret_file_cannot_be_read(repo: Path) -> None:
    with pytest.raises(GuardrailViolationError):
        await Workspace(repo).read(".env")


async def test_large_files_are_rejected(repo: Path) -> None:
    (repo / "src" / "big.py").write_text("x" * 100, encoding="utf-8")
    with pytest.raises(GuardrailViolationError):
        await Workspace(repo, max_file_bytes=10).read("src/big.py")


async def test_write_respects_scope_and_secrets(repo: Path) -> None:
    ws = Workspace(repo)
    await ws.write("src/app/new.py", "VALUE = 1\n", allowed_prefixes=["src/"])
    assert (repo / "src" / "app" / "new.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    with pytest.raises(GuardrailViolationError):
        await ws.write("tests/test_x.py", "", allowed_prefixes=["src/"])
    with pytest.raises(GuardrailViolationError):
        await ws.write("src/k.py", "k = '" + "AKIA" + "B" * 16 + "'", allowed_prefixes=["src/"])


async def test_read_many_skips_missing_files(repo: Path) -> None:
    files = await Workspace(repo).read_many(["src/app/calc.py", "src/missing.py"])
    assert list(files) == ["src/app/calc.py"]


async def test_validate_plan_accepts_real_files_and_symbols(repo: Path) -> None:
    plan = _plan(
        ImpactedItem(path="src/app/calc.py", symbols=["calc.add"], change=ChangeKind.MODIFY),
        ImpactedItem(path="src/app/new.py", change=ChangeKind.CREATE),
    )
    assert await Workspace(repo).validate_plan(plan) == []


async def test_validate_plan_reports_hallucinations(repo: Path) -> None:
    plan = _plan(
        ImpactedItem(path="src/app/ghost.py", change=ChangeKind.MODIFY),
        ImpactedItem(path="src/app/calc.py", symbols=["multiply"], change=ChangeKind.MODIFY),
        ImpactedItem(path="src/app/calc.py", change=ChangeKind.CREATE),
        ImpactedItem(path="../outside.py", change=ChangeKind.MODIFY),
    )
    errors = await Workspace(repo).validate_plan(plan)
    assert len(errors) == 4
    assert any("ghost.py" in e for e in errors)
    assert any("multiply" in e for e in errors)
