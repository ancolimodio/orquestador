"""Contratos de handoff entre agentes, validados con Pydantic v2."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _Contract(BaseModel):
    """Base estricta: un campo desconocido invalida el handoff."""

    model_config = ConfigDict(extra="forbid")


class ChangeKind(StrEnum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class ImpactedItem(_Contract):
    path: str = Field(min_length=1)
    # Símbolos que ya existen y el cambio toca; el Gate A verifica que estén en el archivo.
    symbols: list[str] = Field(default_factory=list)
    # Símbolos que el cambio agrega; el Gate A verifica que todavía no existan.
    new_symbols: list[str] = Field(default_factory=list)
    change: ChangeKind


class Plan(_Contract):
    """Salida del Planner (`plan.json`)."""

    task_id: str
    goal: str = Field(min_length=1)
    impacted: list[ImpactedItem] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class FileChange(_Contract):
    path: str = Field(min_length=1)
    content: str


class ChangeSet(_Contract):
    """Salida del Implementer y del Tester: archivos completos a escribir."""

    changes: list[FileChange] = Field(min_length=1)
    notes: str = ""


class Severity(StrEnum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


class Verdict(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"


class Finding(_Contract):
    severity: Severity
    path: str
    line: int | None = None
    issue: str
    fix: str


class Review(_Contract):
    """Salida del Reviewer (`review.json`)."""

    task_id: str
    verdict: Verdict
    findings: list[Finding] = Field(default_factory=list)
    acceptance_criteria_met: bool

    @property
    def has_blocking_findings(self) -> bool:
        return any(f.severity in {Severity.BLOCKER, Severity.MAJOR} for f in self.findings)

    @property
    def approved(self) -> bool:
        return (
            self.verdict is Verdict.APPROVE
            and self.acceptance_criteria_met
            and not self.has_blocking_findings
        )


class CheckResult(BaseModel):
    name: str
    command: list[str]
    returncode: int
    output: str
    duration_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.returncode == 0


class GateResult(BaseModel):
    gate: str
    checks: list[CheckResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def failure_report(self) -> str:
        """Salida completa de los checks fallidos, para devolver al agente."""
        parts = [
            f"### {c.name} (exit {c.returncode})\n$ {' '.join(c.command)}\n{c.output}"
            for c in self.checks
            if not c.passed
        ]
        return "\n\n".join(parts)


class TaskStatus(StrEnum):
    DONE = "done"
    ESCALATED = "escalated"


class TaskReport(BaseModel):
    task_id: str
    status: TaskStatus
    plan: Plan | None = None
    review: Review | None = None
    changed_files: list[str] = Field(default_factory=list)
    attempts: dict[str, int] = Field(default_factory=dict)
    escalation_reason: str | None = None
    history: list[str] = Field(default_factory=list)
