"""Ejecución de comandos con guardrails, timeout y entorno mínimo."""

import asyncio
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from prompt_maestro.guardrails import check_command
from prompt_maestro.models import CheckResult

SAFE_ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    # Windows: sin SYSTEMROOT, Python no puede inicializar sockets ni importar asyncio
    # (WinError 10106). Son rutas del sistema, no secretos.
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
)

CONTAINER_CLIENT_ENV_KEYS: tuple[str, ...] = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
    "XDG_RUNTIME_DIR",
)

CONTAINER_WORKDIR = "/workspace"
# tmpfs privado de cada contenedor efímero, no el /tmp compartido del host.
CONTAINER_TMP = "/tmp"  # noqa: S108  # nosec B108

# El repo se monta read-only: los caches de las herramientas van al tmpfs.
CONTAINER_ENV: Mapping[str, str] = {
    "HOME": CONTAINER_TMP,
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTEST_ADDOPTS": "-p no:cacheprovider",
    "MYPY_CACHE_DIR": f"{CONTAINER_TMP}/.mypy_cache",
    "RUFF_CACHE_DIR": f"{CONTAINER_TMP}/.ruff_cache",
}


class CommandRunner(Protocol):
    async def run(self, argv: Sequence[str], *, name: str) -> CheckResult: ...


def _filter_env(source: Mapping[str, str], keys: Sequence[str]) -> dict[str, str]:
    return {k: v for k, v in source.items() if k in keys}


async def _execute(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: float,
    max_output_chars: int,
) -> tuple[int, str] | None:
    """Corre el proceso y devuelve (returncode, salida acotada), o None si venció el timeout."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=dict(env),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with asyncio.timeout(timeout_s):
            stdout, _ = await proc.communicate()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    output = stdout.decode("utf-8", errors="replace")
    if len(output) > max_output_chars:
        output = "[...salida truncada...]\n" + output[-max_output_chars:]
    return (proc.returncode if proc.returncode is not None else -1, output)


def _result(
    name: str,
    argv: Sequence[str],
    outcome: tuple[int, str] | None,
    started: float,
    timeout_s: float,
) -> CheckResult:
    returncode, output = outcome if outcome else (-1, f"Timeout después de {timeout_s:.0f}s.")
    return CheckResult(
        name=name,
        command=list(argv),
        returncode=returncode,
        output=output,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


class Sandbox:
    """Corre comandos del harness en el host, dentro del directorio del proyecto.

    Aísla el entorno (allowlist de variables), pero NO el sistema de archivos ni la red:
    el código generado por el modelo corre con los permisos del usuario. Para ejecutar
    tests escritos por un agente, usá `ContainerSandbox`.
    """

    def __init__(
        self,
        cwd: Path,
        *,
        timeout_s: float = 300.0,
        max_output_chars: int = 20_000,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.cwd = cwd.resolve()
        self.timeout_s = timeout_s
        self.max_output_chars = max_output_chars
        self._env = _filter_env(env if env is not None else os.environ, SAFE_ENV_KEYS)

    async def run(self, argv: Sequence[str], *, name: str) -> CheckResult:
        check_command(argv)
        started = time.perf_counter()
        outcome = await _execute(
            argv,
            cwd=self.cwd,
            env=self._env,
            timeout_s=self.timeout_s,
            max_output_chars=self.max_output_chars,
        )
        return _result(name, argv, outcome, started, self.timeout_s)


class ContainerSandbox:
    """Corre cada comando en un contenedor efímero, sin red y con el repo en solo lectura.

    La imagen debe traer las herramientas de los gates y las dependencias del proyecto:
    sin red, no se puede instalar nada adentro.
    """

    def __init__(
        self,
        cwd: Path,
        *,
        image: str,
        runtime: Sequence[str] = ("docker",),
        timeout_s: float = 300.0,
        max_output_chars: int = 20_000,
        memory: str = "2g",
        cpus: str = "2",
        pids_limit: int = 512,
        user: str = "65534:65534",
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.cwd = cwd.resolve()
        self.image = image
        self.runtime = tuple(runtime)
        self.timeout_s = timeout_s
        self.max_output_chars = max_output_chars
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.user = user
        self._client_env = _filter_env(
            env if env is not None else os.environ, SAFE_ENV_KEYS + CONTAINER_CLIENT_ENV_KEYS
        )

    def build_command(self, argv: Sequence[str], *, container_name: str) -> list[str]:
        env_flags = [flag for k, v in CONTAINER_ENV.items() for flag in ("--env", f"{k}={v}")]
        return [
            *self.runtime,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            f"{CONTAINER_TMP}:rw,exec,size=512m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--user",
            self.user,
            "--mount",
            f"type=bind,source={self.cwd},target={CONTAINER_WORKDIR},readonly",
            "--workdir",
            CONTAINER_WORKDIR,
            *env_flags,
            self.image,
            *argv,
        ]

    async def run(self, argv: Sequence[str], *, name: str) -> CheckResult:
        check_command(argv)
        container_name = f"prompt-maestro-{uuid.uuid4().hex[:12]}"
        started = time.perf_counter()
        outcome = await _execute(
            self.build_command(argv, container_name=container_name),
            cwd=self.cwd,
            env=self._client_env,
            timeout_s=self.timeout_s,
            max_output_chars=self.max_output_chars,
        )
        if outcome is None:
            # Matar el cliente no detiene el contenedor. `rm --force` lo mata y lo borra en
            # forma sincrónica; `kill` dejaría el borrado de `--rm` corriendo en segundo plano.
            await _execute(
                [*self.runtime, "rm", "--force", container_name],
                cwd=self.cwd,
                env=self._client_env,
                timeout_s=30.0,
                max_output_chars=1_000,
            )
        return _result(name, argv, outcome, started, self.timeout_s)
