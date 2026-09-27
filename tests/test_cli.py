import asyncio
import json
from pathlib import Path

import pytest

from prompt_maestro import cli
from prompt_maestro.llm import OpenAICompatibleLLM, ScriptedLLM
from prompt_maestro.sandbox import ContainerSandbox, Sandbox
from prompt_maestro.workspace import Workspace
from tests.conftest import IMPL, TESTS, FakeGateRunner, plan_json, review_json


def test_cli_requires_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["prompt-maestro", "run", "hacer algo"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_parser_defaults() -> None:
    args = cli.build_parser().parse_args(["run", "req", "--max-attempts", "5"])
    assert args.requirement == "req"
    assert args.max_attempts == 5


def test_container_image_selects_container_sandbox(tmp_path: Path) -> None:
    args = cli.build_parser().parse_args(
        ["run", "req", "--repo", str(tmp_path), "--container-image", "pm-gates:latest"]
    )
    runner = cli.build_runner(args)
    assert isinstance(runner, ContainerSandbox)
    assert runner.image == "pm-gates:latest"


def test_host_sandbox_warns_about_missing_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PM_CONTAINER_IMAGE", raising=False)
    args = cli.build_parser().parse_args(["run", "req", "--repo", str(tmp_path)])
    assert isinstance(cli.build_runner(args), Sandbox)
    assert "sin aislamiento" in capsys.readouterr().err


def test_gemini_provider_uses_its_own_key_and_endpoint(tmp_path: Path) -> None:
    args = cli.build_parser().parse_args(["run", "req", "--provider", "gemini"])
    llm = cli.build_llm(args, api_key="k", model="gemini-3.8-flash")
    assert isinstance(llm, OpenAICompatibleLLM)
    assert llm.max_retries == 6
    assert cli.PROVIDERS["gemini"].key_env == "GEMINI_API_KEY"


def test_missing_provider_key_names_the_right_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["prompt-maestro", "run", "r", "--provider", "gemini"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "GEMINI_API_KEY" in capsys.readouterr().err


def test_provider_without_default_model_requires_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.delenv("PM_MODEL", raising=False)
    monkeypatch.setattr("sys.argv", ["prompt-maestro", "run", "r", "--provider", "openai"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "--model" in capsys.readouterr().err


class _ClosableScriptedLLM(ScriptedLLM):
    async def aclose(self) -> None:
        return None


def _happy_llm() -> _ClosableScriptedLLM:
    return _ClosableScriptedLLM(
        {
            "planner": [plan_json()],
            "implementer": [IMPL],
            "tester": [TESTS],
            "reviewer": [review_json()],
        }
    )


def _run_cli(repo: Path, monkeypatch: pytest.MonkeyPatch, llm: ScriptedLLM) -> int:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(cli, "build_llm", lambda *_, **__: llm)
    monkeypatch.setattr(cli, "SandboxGateRunner", lambda *_: FakeGateRunner())
    args = cli.build_parser().parse_args(
        ["run", "Agregar subtract", "--repo", str(repo), "--container-image", "img"]
    )
    return asyncio.run(cli._run(args))


def test_each_run_keeps_its_own_report_and_events(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regresión: el reporte de una corrida pisaba el de la anterior."""
    assert _run_cli(repo, monkeypatch, _happy_llm()) == 0
    (repo / "src" / "app" / "calc.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    (repo / "tests" / "test_subtract.py").unlink()
    assert _run_cli(repo, monkeypatch, _happy_llm()) == 0

    runs = sorted((repo / cli.RUNS_DIR).iterdir())
    assert len(runs) == 2
    for run in runs:
        report = json.loads((run / "report.json").read_text(encoding="utf-8"))
        assert report["status"] == "done"
        assert run.name.endswith(report["task_id"])
        events = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert events and all(report["task_id"] in line for line in events)


def test_unexpected_error_leaves_a_trace_in_the_run(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExplodingLLM(_ClosableScriptedLLM):
        async def complete(self, *, role: str, system: str, prompt: str) -> str:
            raise RuntimeError("boom inesperado")

    with pytest.raises(RuntimeError):
        _run_cli(repo, monkeypatch, ExplodingLLM({}))
    (run,) = (repo / cli.RUNS_DIR).iterdir()
    assert "boom inesperado" in (run / "error.txt").read_text(encoding="utf-8")
    assert not (run / "report.json").exists()


async def test_run_reports_are_invisible_to_the_agents(repo: Path) -> None:
    run = repo / cli.RUNS_DIR / "20260101T000000Z-abc"
    run.mkdir(parents=True)
    (run / "report.json").write_text("{}", encoding="utf-8")
    assert not any(p.startswith(".prompt-maestro") for p in await Workspace(repo).repo_map())
