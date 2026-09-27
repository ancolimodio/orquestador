"""Perfil del proyecto de destino: stack, alcance de cada rol, archivos protegidos y gates.

Se declara en `prompt-maestro.toml`, en la raíz del repo. Sin ese archivo, el harness usa
el perfil de un proyecto Python con `src/` y `tests/`.
"""

import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from prompt_maestro.errors import PromptMaestroError
from prompt_maestro.gates import DEFAULT_GATES, CheckSpec
from prompt_maestro.guardrails import RoleScope, WritePolicy

CONFIG_FILE = "prompt-maestro.toml"


class ProjectConfigError(PromptMaestroError):
    """`prompt-maestro.toml` no existe como se espera o no cumple el esquema."""


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CheckConfig(_Config):
    name: str = Field(min_length=1)
    argv: tuple[str, ...] = Field(min_length=1)


class ProjectProfile(_Config):
    """Lo que el harness necesita saber del repo de destino."""

    # Descripción del stack para los prompts, por ejemplo "TypeScript + React (CRA)".
    stack: str = "Python"
    test_framework: str = "pytest"
    rules_file: str = "AGENTS.md"
    # Globs (ver `guardrails.matches_glob`); un test que coincide con `code` sigue siendo test.
    code: tuple[str, ...] = ("src/",)
    tests: tuple[str, ...] = ("tests/",)
    protected: tuple[str, ...] = ()
    # Valores exactos que el proyecto publica a propósito (por ejemplo, la apiKey web de
    # Firebase) y que el detector de secretos no debe bloquear.
    secret_allowlist: tuple[str, ...] = ()
    # Checks por gate (B, C, D); None usa los gates de Python del harness.
    gates: Mapping[str, tuple[CheckConfig, ...]] | None = None

    @property
    def code_policy(self) -> WritePolicy:
        return self._policy(RoleScope(self.code, excluded=self.tests))

    @property
    def test_policy(self) -> WritePolicy:
        return self._policy(RoleScope(self.tests))

    def _policy(self, scope: RoleScope) -> WritePolicy:
        return WritePolicy(scope, self.protected, self.secret_allowlist)

    def gate_specs(self) -> Mapping[str, tuple[CheckSpec, ...]]:
        if self.gates is None:
            return DEFAULT_GATES
        return {
            gate: tuple(CheckSpec(c.name, c.argv) for c in checks)
            for gate, checks in self.gates.items()
        }


def load_profile(repo: Path) -> ProjectProfile:
    """Lee `prompt-maestro.toml` del repo; sin archivo, devuelve el perfil por defecto."""
    path = repo / CONFIG_FILE
    if not path.is_file():
        return ProjectProfile()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return ProjectProfile.model_validate(data)
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ProjectConfigError(f"{CONFIG_FILE} inválido: {exc}") from exc
