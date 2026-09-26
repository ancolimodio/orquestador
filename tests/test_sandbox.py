import sys
from pathlib import Path

import pytest

from prompt_maestro.errors import GuardrailViolationError
from prompt_maestro.gates import CheckSpec, SandboxGateRunner
from prompt_maestro.sandbox import Sandbox


async def test_runs_command_and_captures_output(tmp_path: Path) -> None:
    result = await Sandbox(tmp_path).run([sys.executable, "-c", "print('hola')"], name="py")
    assert result.passed
    assert result.output.strip() == "hola"


async def test_nonzero_exit_is_a_failure(tmp_path: Path) -> None:
    result = await Sandbox(tmp_path).run([sys.executable, "-c", "raise SystemExit(3)"], name="py")
    assert result.returncode == 3
    assert not result.passed


async def test_timeout_kills_the_process(tmp_path: Path) -> None:
    sandbox = Sandbox(tmp_path, timeout_s=0.2)
    result = await sandbox.run([sys.executable, "-c", "import time; time.sleep(5)"], name="slow")
    assert result.returncode == -1
    assert "Timeout" in result.output


async def test_output_is_truncated(tmp_path: Path) -> None:
    sandbox = Sandbox(tmp_path, max_output_chars=50)
    result = await sandbox.run([sys.executable, "-c", "print('x' * 500)"], name="big")
    assert result.output.startswith("[...salida truncada...]")


async def test_environment_does_not_leak_secrets(tmp_path: Path) -> None:
    sandbox = Sandbox(tmp_path, env={"PATH": "/usr/bin:/bin", "ANTHROPIC_API_KEY": "leak"})
    code = "import os; print(os.environ.get('ANTHROPIC_API_KEY', 'none'))"
    result = await sandbox.run([sys.executable, "-c", code], name="env")
    assert result.output.strip() == "none"


async def test_forbidden_command_never_runs(tmp_path: Path) -> None:
    with pytest.raises(GuardrailViolationError):
        await Sandbox(tmp_path).run(["rm", "-rf", "."], name="danger")


async def test_gate_runner_runs_checks_in_parallel(tmp_path: Path) -> None:
    gates = {
        "B": (
            CheckSpec("ok", (sys.executable, "-c", "pass")),
            CheckSpec("fail", (sys.executable, "-c", "raise SystemExit(1)")),
        )
    }
    result = await SandboxGateRunner(Sandbox(tmp_path), gates).run_gate("B")
    assert [c.name for c in result.checks] == ["ok", "fail"]
    assert not result.passed


async def test_unknown_gate_passes_empty(tmp_path: Path) -> None:
    result = await SandboxGateRunner(Sandbox(tmp_path), {}).run_gate("Z")
    assert result.passed and result.checks == []
