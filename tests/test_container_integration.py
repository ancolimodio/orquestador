"""Integración real con Docker. Opt-in: requiere una imagen construida y la variable
PM_TEST_CONTAINER_IMAGE, así la suite normal sigue sin red ni dependencias externas.

    docker build -t prompt-maestro-gates -f docker/gates.Dockerfile .
    PM_TEST_CONTAINER_IMAGE=prompt-maestro-gates pytest tests/test_container_integration.py
"""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from prompt_maestro.sandbox import ContainerSandbox

IMAGE = os.environ.get("PM_TEST_CONTAINER_IMAGE", "")
RUNTIME = os.environ.get("PM_TEST_CONTAINER_RUNTIME", "docker")

pytestmark = pytest.mark.skipif(
    not IMAGE or shutil.which(RUNTIME) is None,
    reason="Definí PM_TEST_CONTAINER_IMAGE y tené docker/podman instalado.",
)


def _sandbox(root: Path, **kwargs: float) -> ContainerSandbox:
    return ContainerSandbox(root, image=IMAGE, runtime=(RUNTIME,), **kwargs)


def _python(code: str) -> list[str]:
    return ["python", "-c", code]


async def test_real_pytest_passes_inside_the_container(tmp_path: Path) -> None:
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "app" / "calc.py").write_text(
        "def subtract(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from app.calc import subtract\n\n\ndef test_subtract() -> None:\n"
        "    assert subtract(5, 3) == 2\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n', encoding="utf-8"
    )
    result = await _sandbox(tmp_path).run(["pytest", "-q"], name="pytest")
    assert result.passed, result.output
    assert not (tmp_path / ".pytest_cache").exists()


async def test_network_is_unreachable(tmp_path: Path) -> None:
    code = "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)"
    result = await _sandbox(tmp_path).run(_python(code), name="net")
    assert not result.passed
    assert "Network is unreachable" in result.output


async def test_repository_is_read_only(tmp_path: Path) -> None:
    code = "open('/workspace/pwned.txt', 'w').write('x')"
    result = await _sandbox(tmp_path).run(_python(code), name="write")
    assert not result.passed
    assert "Read-only file system" in result.output
    assert not (tmp_path / "pwned.txt").exists()


async def test_host_secrets_do_not_reach_the_container(tmp_path: Path) -> None:
    sandbox = ContainerSandbox(
        tmp_path,
        image=IMAGE,
        runtime=(RUNTIME,),
        env={**os.environ, "ANTHROPIC_API_KEY": "leak"},
    )
    code = "import os; print(os.environ.get('ANTHROPIC_API_KEY', 'none'))"
    result = await sandbox.run(_python(code), name="env")
    assert result.output.strip() == "none"


async def test_timeout_removes_the_container(tmp_path: Path) -> None:
    result = await _sandbox(tmp_path, timeout_s=5).run(
        _python("import time; time.sleep(60)"), name="slow"
    )
    assert result.returncode == -1
    proc = await asyncio.create_subprocess_exec(
        RUNTIME, "ps", "-aq", "--filter", "name=prompt-maestro-", stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    assert proc.returncode == 0
    assert stdout.decode().strip() == ""
