"""Ejecución de comandos con guardrails, timeout y entorno mínimo."""

import asyncio
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

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


class Sandbox:
    """Corre comandos del harness dentro del directorio del proyecto.

    En producción, este proceso se ejecuta dentro de un contenedor efímero:
    el Sandbox agrega una segunda barrera (allowlist de entorno, timeout y guardrails).
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
        source = env if env is not None else os.environ
        self._env = {k: v for k, v in source.items() if k in SAFE_ENV_KEYS}

    async def run(self, argv: Sequence[str], *, name: str) -> CheckResult:
        check_command(argv)
        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self.cwd,
            env=self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            async with asyncio.timeout(self.timeout_s):
                stdout, _ = await proc.communicate()
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return CheckResult(
                name=name,
                command=list(argv),
                returncode=-1,
                output=f"Timeout después de {self.timeout_s:.0f}s.",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        output = stdout.decode("utf-8", errors="replace")
        if len(output) > self.max_output_chars:
            output = "[...salida truncada...]\n" + output[-self.max_output_chars :]
        return CheckResult(
            name=name,
            command=list(argv),
            returncode=proc.returncode if proc.returncode is not None else -1,
            output=output,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
