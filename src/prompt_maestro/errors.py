"""Excepciones de dominio del harness."""


class PromptMaestroError(Exception):
    """Base de todos los errores del framework."""


class GuardrailViolationError(PromptMaestroError):
    """Un agente intentó una acción fuera de sus permisos."""


class HandoffValidationError(PromptMaestroError):
    """Un agente devolvió un handoff que no cumple el contrato."""


class LLMError(PromptMaestroError):
    """El proveedor del modelo falló después de agotar los reintentos."""


class EscalationRequiredError(PromptMaestroError):
    """La tarea necesita una decisión humana para continuar."""

    def __init__(self, reason: str, history: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.history = history or []
