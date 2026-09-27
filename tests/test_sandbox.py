import json
import sys
from pathlib import Path
from typing import Any

import pytest

from prompt_maestro.errors import GuardrailViolationError
from prompt_maestro.gates import CheckSpec, SandboxGateRunner
from prompt_maestro.sandbox import ContainerSandbox, Sandbox


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


FAKE_RUNTIME = """
import json, pathlib, sys, time
args = sys.argv[1:]
if args[:2] == ["rm", "--force"]:
    pathlib.Path(sys.argv[0]).with_name("removed.txt").write_text(args[2])
    raise SystemExit(0)
inner = args[args.index("img") + 1 :]
if inner == ["slow"]:
    time.sleep(5)
print(json.dumps(args))
raise SystemExit(int(inner[-1]) if inner[-1].isdigit() else 0)
"""


def _container(tmp_path: Path, **kwargs: Any) -> ContainerSandbox:
    script = tmp_path / "fake_runtime.py"
    script.write_text(FAKE_RUNTIME, encoding="utf-8")
    return ContainerSandbox(tmp_path, image="img", runtime=(sys.executable, str(script)), **kwargs)


def test_container_command_isolates_network_and_repo(tmp_path: Path) -> None:
    cmd = ContainerSandbox(tmp_path, image="img").build_command(
        ["pytest", "-q"], container_name="c1"
    )
    assert cmd[:2] == ["docker", "run"]
    joined = " ".join(cmd)
    assert "--network none" in joined
    assert "--read-only" in joined
    assert "--cap-drop ALL" in joined
    assert f"source={tmp_path.resolve()},target=/workspace,readonly" in joined
    assert cmd[-3:] == ["img", "pytest", "-q"]


def test_container_does_not_forward_host_secrets(tmp_path: Path) -> None:
    sandbox = ContainerSandbox(tmp_path, image="img", env={"ANTHROPIC_API_KEY": "leak"})
    assert "leak" not in " ".join(sandbox.build_command(["pytest"], container_name="c"))


async def test_container_run_reports_inner_command_and_exit_code(tmp_path: Path) -> None:
    result = await _container(tmp_path).run(["pytest", "3"], name="pytest")
    assert result.command == ["pytest", "3"]
    assert result.returncode == 3
    assert "--network" in json.loads(result.output)


async def test_container_timeout_removes_the_container_by_name(tmp_path: Path) -> None:
    result = await _container(tmp_path, timeout_s=0.5).run(["slow"], name="slow")
    assert result.returncode == -1
    assert "Timeout" in result.output
    assert (tmp_path / "removed.txt").read_text().startswith("prompt-maestro-")


async def test_container_applies_command_guardrails(tmp_path: Path) -> None:
    with pytest.raises(GuardrailViolationError):
        await _container(tmp_path).run(["curl", "https://example.com"], name="net")


async def test_host_sandbox_can_run_asyncio_programs(tmp_path: Path) -> None:
    """Regresión: en Windows, sin SYSTEMROOT, `import asyncio` falla con WinError 10106."""
    code = "import asyncio; print(asyncio.run(asyncio.sleep(0, 'ok')))"
    result = await Sandbox(tmp_path).run([sys.executable, "-c", code], name="asyncio")
    assert result.passed, result.output
    assert result.output.strip() == "ok"


def test_system_paths_are_forwarded_but_secrets_are_not(tmp_path: Path) -> None:
    env = {"SYSTEMROOT": r"C:\Windows", "PATH": "/bin", "GITHUB_TOKEN": "leak"}
    forwarded = Sandbox(tmp_path, env=env)._env
    assert forwarded == {"SYSTEMROOT": r"C:\Windows", "PATH": "/bin"}
