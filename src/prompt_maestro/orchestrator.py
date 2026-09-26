"""Orchestrator: conduce la tarea por los gates A→D con presupuesto de reintentos."""

import time
import uuid
from dataclasses import dataclass, field

from prompt_maestro.agents import Implementer, Planner, Reviewer, Tester
from prompt_maestro.errors import (
    EscalationRequiredError,
    GuardrailViolationError,
    HandoffValidationError,
    LLMError,
)
from prompt_maestro.gates import GateRunner
from prompt_maestro.guardrails import sensitive_areas
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


@dataclass(slots=True)
class _TaskState:
    task_id: str
    attempts: dict[str, int] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    changed_files: set[str] = field(default_factory=set)
    plan: Plan | None = None


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
            attempt = self._consume_attempt(state, "A")
            plan = await team.planner.run(
                task_id=state.task_id, requirement=requirement, repo_map=repo_map, feedback=feedback
            )
            errors = await self._ws.validate_plan(plan)
            result = "fail" if errors else "pass"
            await self._emit(state, "planner", "A", "gate", result, attempt=attempt)
            if not errors:
                return plan
            feedback = "\n".join(errors)
            state.history.append(f"Gate A (intento {attempt}): {feedback}")
            self._ensure_budget_left(state, "A")

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
        feedback = ""
        while True:
            files = await self._ws.read_many(i.path for i in plan.impacted)
            implementation = await team.implementer.run(plan=plan, files=files, feedback=feedback)
            await self._apply(state, implementation, self.config.code_prefixes)
            gate_b = await self._run_gate(state, "B", "implementer")
            if not gate_b.passed:
                feedback = gate_b.failure_report()
                continue

            tests = await team.tester.run(
                plan=plan,
                implementation=implementation,
                existing_tests=await self._existing_tests(),
                feedback=feedback,
            )
            await self._apply(state, tests, self.config.test_prefixes)
            gate_c = await self._run_gate(state, "C", "tester")
            if not gate_c.passed:
                feedback = "Fallaron los tests:\n" + gate_c.failure_report()
                continue

            gate_d = await self._run_gate(state, "D", "reviewer", consume=False)
            review = await team.reviewer.run(
                plan=plan, implementation=implementation, tests=tests, static_analysis=gate_d
            )
            attempt = self._consume_attempt(state, "D")
            approved = gate_d.passed and review.approved
            await self._emit(
                state, "reviewer", "D", "gate", "pass" if approved else "fail", attempt=attempt
            )
            if approved:
                return review
            feedback = self._review_feedback(review, gate_d)
            state.history.append(f"Gate D (intento {attempt}): {feedback[:500]}")
            self._ensure_budget_left(state, "D")

    # --- helpers ---------------------------------------------------------------

    async def _run_gate(
        self, state: _TaskState, gate: str, agent: str, *, consume: bool = True
    ) -> GateResult:
        attempt = self._consume_attempt(state, gate) if consume else state.attempts.get(gate, 0)
        started = time.perf_counter()
        result = await self._gates.run_gate(gate)
        if consume:
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
                state.history.append(f"Gate {gate} (intento {attempt}): falló")
                self._ensure_budget_left(state, gate)
        return result

    async def _apply(
        self, state: _TaskState, changes: ChangeSet, prefixes: tuple[str, ...]
    ) -> None:
        for change in changes.changes:
            await self._ws.write(change.path, change.content, allowed_prefixes=prefixes)
            state.changed_files.add(change.path)

    async def _existing_tests(self) -> dict[str, str]:
        repo_map = await self._ws.repo_map()
        paths = [
            p for p in repo_map if p.startswith(self.config.test_prefixes) and p.endswith(".py")
        ][: self.config.max_context_tests]
        return await self._ws.read_many(paths)

    def _consume_attempt(self, state: _TaskState, gate: str) -> int:
        attempt = state.attempts.get(gate, 0) + 1
        state.attempts[gate] = attempt
        if attempt > self.config.max_attempts_per_gate:
            raise EscalationRequiredError(
                f"Gate {gate}: se agotó el presupuesto de "
                f"{self.config.max_attempts_per_gate} reintentos."
            )
        return attempt

    def _ensure_budget_left(self, state: _TaskState, gate: str) -> None:
        """Escala apenas falla el último intento: no gasta una llamada al modelo de más."""
        if state.attempts.get(gate, 0) >= self.config.max_attempts_per_gate:
            raise EscalationRequiredError(
                f"Gate {gate}: se agotó el presupuesto de "
                f"{self.config.max_attempts_per_gate} reintentos."
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
