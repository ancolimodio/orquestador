from pathlib import Path

import pytest

from prompt_maestro import cli
from prompt_maestro.llm import OpenAICompatibleLLM
from prompt_maestro.sandbox import ContainerSandbox, Sandbox


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
