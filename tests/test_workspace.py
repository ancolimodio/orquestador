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


@pytest.mark.parametrize(
    ("path", "prefixes"),
    [("src/../AGENTS.md", ["src/"]), ("tests/../src/app/calc.py", ["tests/"])],
)
async def test_write_cannot_escape_scope_with_dotdot(
    repo: Path, path: str, prefixes: list[str]
) -> None:
    before = {
        p: p.read_text(encoding="utf-8") for p in (repo / "AGENTS.md", repo / "src/app/calc.py")
    }
    with pytest.raises(GuardrailViolationError):
        await Workspace(repo).write(path, "pwned", allowed_prefixes=prefixes)
    assert {p: p.read_text(encoding="utf-8") for p in before} == before


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


async def test_validate_plan_requires_whole_identifiers(repo: Path) -> None:
    (repo / "src" / "app" / "net.py").write_text("address = '1.2.3.4'\n", encoding="utf-8")
    plan = _plan(ImpactedItem(path="src/app/net.py", symbols=["add"], change=ChangeKind.MODIFY))
    errors = await Workspace(repo).validate_plan(plan)
    assert errors and "'add'" in errors[0]


async def test_read_many_raises_domain_error_not_exception_group(repo: Path) -> None:
    (repo / "src" / "big.py").write_text("x" * 100, encoding="utf-8")
    with pytest.raises(GuardrailViolationError, match="demasiado grande"):
        await Workspace(repo, max_file_bytes=50).read_many(["src/app/calc.py", "src/big.py"])


async def test_non_utf8_file_is_a_domain_error(repo: Path) -> None:
    (repo / "src" / "blob.py").write_bytes(b"\xff\xfe\x00binary")
    with pytest.raises(GuardrailViolationError, match="UTF-8"):
        await Workspace(repo).read("src/blob.py")


async def test_validate_plan_accepts_new_symbols_in_existing_files(repo: Path) -> None:
    """Regresión de una corrida real: el Planner no tenía cómo declarar un método nuevo."""
    plan = _plan(
        ImpactedItem(
            path="src/app/calc.py",
            symbols=["add"],
            new_symbols=["subtract"],
            change=ChangeKind.MODIFY,
        )
    )
    assert await Workspace(repo).validate_plan(plan) == []


async def test_validate_plan_rejects_new_symbols_that_already_exist(repo: Path) -> None:
    plan = _plan(
        ImpactedItem(path="src/app/calc.py", new_symbols=["add"], change=ChangeKind.MODIFY)
    )
    errors = await Workspace(repo).validate_plan(plan)
    assert len(errors) == 1 and "ya existe" in errors[0]


async def test_validate_plan_requires_the_owner_of_a_new_symbol(repo: Path) -> None:
    plan = _plan(
        ImpactedItem(
            path="src/app/calc.py", new_symbols=["Calculator.subtract"], change=ChangeKind.MODIFY
        )
    )
    errors = await Workspace(repo).validate_plan(plan)
    assert len(errors) == 1 and "'Calculator'" in errors[0]


async def test_missing_symbol_error_points_to_new_symbols(repo: Path) -> None:
    plan = _plan(
        ImpactedItem(path="src/app/calc.py", symbols=["subtract"], change=ChangeKind.MODIFY)
    )
    errors = await Workspace(repo).validate_plan(plan)
    assert "new_symbols" in errors[0]
