"""Loops de verificación: checks deterministas agrupados por gate."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from prompt_maestro.models import CheckResult, GateResult
from prompt_maestro.sandbox import CommandRunner


@dataclass(frozen=True, slots=True)
class CheckSpec:
    name: str
    argv: tuple[str, ...]


DEFAULT_GATES: Mapping[str, tuple[CheckSpec, ...]] = {
    "B": (
        CheckSpec("ruff-lint", ("ruff", "check", ".")),
        CheckSpec("ruff-format", ("ruff", "format", "--check", ".")),
        CheckSpec("mypy", ("mypy", "--strict", "src")),
    ),
    "C": (CheckSpec("pytest", ("pytest", "-q")),),
    "D": (CheckSpec("bandit", ("bandit", "-q", "-r", "src")),),
}


class GateRunner(Protocol):
    async def run_gate(self, gate: str) -> GateResult: ...


class SandboxGateRunner:
    """Ejecuta en paralelo los checks de un gate, con concurrencia acotada."""

    def __init__(
        self,
        sandbox: CommandRunner,
        gates: Mapping[str, tuple[CheckSpec, ...]] = DEFAULT_GATES,
        *,
        max_parallel: int = 3,
    ) -> None:
        self._sandbox = sandbox
        self._gates = gates
        self._semaphore = asyncio.Semaphore(max_parallel)

    async def _run_one(self, spec: CheckSpec) -> CheckResult:
        async with self._semaphore:
            return await self._sandbox.run(spec.argv, name=spec.name)

    async def run_gate(self, gate: str) -> GateResult:
        specs = self._gates.get(gate, ())
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(self._run_one(spec)) for spec in specs]
        return GateResult(gate=gate, checks=[t.result() for t in tasks])
