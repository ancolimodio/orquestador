"""Base común: llamada al modelo, extracción de JSON y validación del handoff."""

import json
from typing import ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

from prompt_maestro.errors import HandoffValidationError
from prompt_maestro.llm import LLMClient
from prompt_maestro.project import ProjectProfile
from prompt_maestro.prompts import build_system_prompt

ModelT = TypeVar("ModelT", bound=BaseModel)


def extract_json(raw: str) -> str:
    """Extrae el objeto JSON de la respuesta, tolerando fences de Markdown o texto extra."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise HandoffValidationError("La respuesta no contiene un objeto JSON.")
    return text[start : end + 1]


def render_files(files: dict[str, str]) -> str:
    return "\n\n".join(f'<file path="{p}">\n{c}\n</file>' for p, c in files.items()) or "(ninguno)"


class Agent:
    """Un agente = un rol + el harness que valida su salida."""

    role: ClassVar[str]

    def __init__(
        self,
        llm: LLMClient,
        *,
        harness_rules: str = "",
        profile: ProjectProfile | None = None,
        max_format_retries: int = 2,
    ) -> None:
        self._llm = llm
        self._system = build_system_prompt(self.role, harness_rules, profile)
        self._max_format_retries = max_format_retries

    async def _ask(self, prompt: str, output_model: type[ModelT]) -> ModelT:
        last_error = ""
        for _ in range(self._max_format_retries + 1):
            full_prompt = prompt
            if last_error:
                full_prompt += (
                    "\n\nTu respuesta anterior no cumplió el contrato:\n"
                    f"{last_error}\nRespondé solo con el JSON corregido."
                )
            raw = await self._llm.complete(role=self.role, system=self._system, prompt=full_prompt)
            try:
                return output_model.model_validate_json(extract_json(raw))
            except (ValidationError, HandoffValidationError, json.JSONDecodeError) as exc:
                last_error = str(exc)
        raise HandoffValidationError(f"{self.role}: handoff inválido tras reintentos. {last_error}")
