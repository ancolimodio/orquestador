import pytest

from prompt_maestro import cli


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
