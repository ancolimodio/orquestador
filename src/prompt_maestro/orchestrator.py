"""Orchestrator: conduce la tarea por los gates A→D con presupuesto de reintentos."""

import time
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from prompt_maestro.agents import Implementer, Planner, Reviewer, Tester
from prompt_maestro.errors import (
    EscalationRequiredError,
    GuardrailViolationError,
    HandoffValidationError,
    LLMError,
)
from prompt_maestro.gates import GateRunner
from prompt_maestro.guardrails import ensure_no_test_weakening, sensitive_areas
from prompt_maestro.llm import LLMClient
from prompt_maestro.models import (
    ChangeKind,
    ChangeSet,
    GateResult,
    Plan,
    Review,
    TaskReport,
    TaskStatus,
)
from prompt_maestro.observability import Event, EventLog
from prompt_maestro.workspace import Workspace


@dataclass(frozen=True, slots=True)
class OrchestratorConfig:
    max_attempts_per_gate: int = 3
    escalate_sensitive_areas: bool = True
    code_prefixes: tuple[str, ...] = ("src/",)
    test_prefixes: tuple[str, ...] = ("tests/",)
    rules_file: str = "AGENTS.md"
    max_context_tests: int = 20
    allow_modifying_existing_tests: bool = False


@dataclass(slots=True)
class _TaskState:
    task_id: str
    attempts: dict[str, int] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    changed_files: set[str] = field(default_factory=set)
    plan: Plan | None = None
    preexisting_tests: frozenset[str] = frozenset()


@dataclass(slots=True)
class _Team:
    planner: Planner
    implementer: Implementer
    tester: Tester
    reviewer: Reviewer


class Orchestrator:
    def __init__(
        self,
        *,
        llm: LLMClient,
        workspace: Workspace,
        gate_runner: GateRunner,
        events: EventLog | None = None,
        config: OrchestratorConfig | None = None,
    ) -> None:
        self._llm = llm
        self._ws = workspace
        self._gates = gate_runner
        self.events = events or EventLog()
        self.config = config or OrchestratorConfig()

    async def run(self, requirement: str, *, task_id: str | None = None) -> TaskReport:
        state = _TaskState(task_id=task_id or uuid.uuid4().hex[:8])
        await self._emit(state, "orchestrator", "start", "task", "ok", detail=requirement[:200])
        try:
            team = await self._build_team()
            plan = await self._plan_phase(state, team, requirement)
            state.plan = plan
            self._check_escalation_rules(plan)
            review = await self._delivery_loop(state, team, plan)
        except EscalationRequiredError as exc:
            return await self._escalated(state, exc.reason, [*state.history, *exc.history])
        except (GuardrailViolationError, HandoffValidationError, LLMError) as exc:
            return await self._escalated(state, f"{type(exc).__name__}: {exc}", state.history)

        await self._emit(state, "orchestrator", "done", "task", "ok")
        return TaskReport(
            task_id=state.task_id,
            status=TaskStatus.DONE,
            plan=plan,
            review=review,
            changed_files=sorted(state.changed_files),
            attempts=dict(state.attempts),
            history=state.history,
        )

    # --- fases -----------------------------------------------------------------

    async def _build_team(self) -> _Team:
        rules = ""
        if await self._ws.exists(self.config.rules_file):
            rules = await self._ws.read(self.config.rules_file)
        return _Team(
            planner=Planner(self._llm, harness_rules=rules),
            implementer=Implementer(self._llm, harness_rules=rules),
            tester=Tester(self._llm, harness_rules=rules),
            reviewer=Reviewer(self._llm, harness_rules=rules),
        )

    async def _plan_phase(self, state: _TaskState, team: _Team, requirement: str) -> Plan:
        repo_map = await self._ws.repo_map()
        feedback = ""
        while True:
            attempt = self._next_attempt(state, "A")
            plan = await team.planner.run(
                task_id=state.task_id, requirement=requirement, repo_map=repo_map, feedback=feedback
            )
            errors = await self._ws.validate_plan(plan)
            result = "fail" if errors else "pass"
            await self._emit(state, "planner", "A", "gate", result, attempt=attempt)
            if not errors:
                return plan
            feedback = "\n".join(errors)
            self._record_failure(state, "A", attempt, feedback)

    def _check_escalation_rules(self, plan: Plan) -> None:
        if plan.open_questions:
            raise EscalationRequiredError(
                "El plan tiene preguntas abiertas: " + "; ".join(plan.open_questions)
            )
        deletions = [i.path for i in plan.impacted if i.change is ChangeKind.DELETE]
        if deletions:
            raise EscalationRequiredError(
                "Borrar archivos requiere aprobación humana: " + ", ".join(deletions)
            )
        if self.config.escalate_sensitive_areas:
            areas = sensitive_areas([plan.goal, *(i.path for i in plan.impacted)])
            if areas:
                raise EscalationRequiredError(
                    "El cambio toca un área sensible (" + ", ".join(areas) + ")."
                )

    async def _delivery_loop(self, state: _TaskState, team: _Team, plan: Plan) -> Review:
        state.preexisting_tests = await self._preexisting_tests()
        # Cada agente recibe solo el feedback que le corresponde: un error de lint del
        # Implementer no le llega al Tester como si fuera una falla de sus tests.
        impl_feedback = ""
        test_feedback = ""
        while True:
            files = await self._ws.read_many(i.path for i in plan.impacted)
            implementation = await team.implementer.run(
                plan=plan, files=files, feedback=impl_feedback
            )
            await self._apply(state, implementation, self.config.code_prefixes)
            gate_b = await self._run_gate(state, "B", "implementer")
            if not gate_b.passed:
                impl_feedback = gate_b.failure_report()
                continue

            tests = await team.tester.run(
                plan=plan,
                implementation=implementation,
                existing_tests=await self._existing_tests(),
                feedback=test_feedback,
            )
            await self._apply_tests(state, tests)
            gate_c = await self._run_gate(state, "C", "tester")
            if not gate_c.passed:
                # La falla puede estar en el código o en el test: la ven los dos agentes.
                impl_feedback = test_feedback = "Fallaron los tests:\n" + gate_c.failure_report()
                continue
            test_feedback = ""

            attempt = self._next_attempt(state, "D")
            gate_d = await self._gates.run_gate("D")
            review = await team.reviewer.run(
                plan=plan, implementation=implementation, tests=tests, static_analysis=gate_d
            )
            approved = gate_d.passed and review.approved
            await self._emit(
                state, "reviewer", "D", "gate", "pass" if approved else "fail", attempt=attempt
            )
            if approved:
                return review
            # El checklist del Reviewer también cubre la calidad de los tests.
            impl_feedback = test_feedback = self._review_feedback(review, gate_d)
            self._record_failure(state, "D", attempt, impl_feedback[:500])

    # --- helpers ---------------------------------------------------------------

    async def _run_gate(self, state: _TaskState, gate: str, agent: str) -> GateResult:
        attempt = self._next_attempt(state, gate)
        started = time.perf_counter()
        result = await self._gates.run_gate(gate)
        await self._emit(
            state,
            agent,
            gate,
            "gate",
            "pass" if result.passed else "fail",
            attempt=attempt,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        if not result.passed:
            self._record_failure(state, gate, attempt, "falló")
        return result

    async def _apply(
        self, state: _TaskState, changes: ChangeSet, prefixes: tuple[str, ...]
    ) -> None:
        for change in changes.changes:
            await self._ws.write(change.path, change.content, allowed_prefixes=prefixes)
            state.changed_files.add(change.path)

    async def _apply_tests(self, state: _TaskState, tests: ChangeSet) -> None:
        """El Tester agrega tests; no reescribe ni debilita los que ya protegían el repo."""
        for change in tests.changes:
            key = PurePosixPath(change.path.replace("\\", "/")).as_posix().casefold()
            if key in state.preexisting_tests:
                raise GuardrailViolationError(
                    f"El Tester no puede modificar un test existente: {change.path}"
                )
            ensure_no_test_weakening(change.content, where=change.path)
        await self._apply(state, tests, self.config.test_prefixes)

    async def _preexisting_tests(self) -> frozenset[str]:
        if self.config.allow_modifying_existing_tests:
            return frozenset()
        repo_map = await self._ws.repo_map()
        return frozenset(p.casefold() for p in repo_map if p.startswith(self.config.test_prefixes))

    async def _existing_tests(self) -> dict[str, str]:
        repo_map = await self._ws.repo_map()
        paths = [
            p for p in repo_map if p.startswith(self.config.test_prefixes) and p.endswith(".py")
        ][: self.config.max_context_tests]
        return await self._ws.read_many(paths)

    @staticmethod
    def _next_attempt(state: _TaskState, gate: str) -> int:
        """Cuenta ejecuciones del gate; es lo que reporta `TaskReport.attempts`."""
        attempt = state.attempts.get(gate, 0) + 1
        state.attempts[gate] = attempt
        return attempt

    def _record_failure(self, state: _TaskState, gate: str, attempt: int, detail: str) -> None:
        """El presupuesto cuenta fallos propios del gate, no vueltas del loop.

        Así una falla en C o D no consume el presupuesto de B, que se re-ejecuta en cada
        vuelta. Escala apenas se alcanza el máximo: no gasta una llamada al modelo de más.
        """
        state.history.append(f"Gate {gate} (intento {attempt}): {detail}")
        failures = state.failures.get(gate, 0) + 1
        state.failures[gate] = failures
        if failures >= self.config.max_attempts_per_gate:
            raise EscalationRequiredError(
                f"Gate {gate}: falló {failures} veces, se agotó el presupuesto de "
                f"{self.config.max_attempts_per_gate} intentos."
            )

    @staticmethod
    def _review_feedback(review: Review, gate_d: GateResult) -> str:
        lines = [
            f"- [{f.severity}] {f.path}:{f.line or '?'} {f.issue} → {f.fix}"
            for f in review.findings
        ]
        if not review.acceptance_criteria_met:
            lines.append("- No se cumplen todos los criterios de aceptación.")
        if not gate_d.passed:
            lines.append(gate_d.failure_report())
        return "Cambios pedidos por el Reviewer:\n" + "\n".join(lines)

    async def _escalated(self, state: _TaskState, reason: str, history: list[str]) -> TaskReport:
        await self._emit(state, "orchestrator", "escalate", "task", "escalated", detail=reason)
        return TaskReport(
            task_id=state.task_id,
            status=TaskStatus.ESCALATED,
            plan=state.plan,
            changed_files=sorted(state.changed_files),
            attempts=dict(state.attempts),
            escalation_reason=reason,
            history=history,
        )

    async def _emit(
        self,
        state: _TaskState,
        agent: str,
        phase: str,
        action: str,
        result: str,
        *,
        attempt: int = 1,
        duration_ms: int | None = None,
        detail: str = "",
    ) -> None:
        await self.events.emit(
            Event(
                task_id=state.task_id,
                agent=agent,
                phase=phase,
                action=action,
                result=result,
                attempt=attempt,
                duration_ms=duration_ms,
                detail=detail,
            )
        )
