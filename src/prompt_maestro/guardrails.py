"""Guardrails: qué puede y qué no puede hacer un agente.

Todas las reglas son deterministas. El harness no le pide permiso al modelo:
las verifica antes de ejecutar cualquier acción.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import PurePosixPath

from prompt_maestro.errors import GuardrailViolationError

FORBIDDEN_COMMANDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rm recursivo forzado", re.compile(r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b")),
    ("git push --force", re.compile(r"\bgit\s+push\b.*(--force|\s-f\b)")),
    ("git reset --hard", re.compile(r"\bgit\s+reset\s+--hard\b")),
    ("drop de base de datos", re.compile(r"\bdrop\s+(table|database|schema)\b", re.IGNORECASE)),
    ("acceso a red", re.compile(r"\b(curl|wget|nc|ssh|scp)\b")),
    ("instalación de dependencias", re.compile(r"\b(pip|uv|poetry|npm)\s+(install|add)\b")),
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("clave privada", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("token de Anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("token de GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("token de GitLab", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    (
        "credencial hardcodeada",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"
        ),
    ),
)

# Globs (ver `matches_glob`); un proyecto puede sumar los suyos en `prompt-maestro.toml`.
PROTECTED_PATHS: tuple[str, ...] = (
    "AGENTS.md",
    ".github/",
    ".gitlab-ci.yml",
    "pyproject.toml",
    "prompt-maestro.toml",
)

SECRET_FILE_NAMES: frozenset[str] = frozenset({".env", ".netrc", "id_rsa", "id_ed25519"})
SECRET_FILE_SUFFIXES: frozenset[str] = frozenset({".pem", ".key", ".p12", ".pfx"})


def check_command(argv: Sequence[str]) -> None:
    """Rechaza comandos destructivos o con efectos fuera del sandbox."""
    if not argv:
        raise GuardrailViolationError("Comando vacío.")
    command = " ".join(argv)
    for label, pattern in FORBIDDEN_COMMANDS:
        if pattern.search(command):
            raise GuardrailViolationError(f"Comando prohibido ({label}): {command}")


def find_secrets(text: str, *, allow: Iterable[str] = ()) -> list[str]:
    """Devuelve los tipos de secreto detectados, sin exponer su valor.

    `allow` lista valores exactos que el proyecto declaró públicos (por ejemplo, la
    `apiKey` web de Firebase, que va en el cliente por diseño): no cuentan como secreto.
    """
    for value in allow:
        text = text.replace(value, "")
    return [label for label, pattern in SECRET_PATTERNS if pattern.search(text)]


def ensure_no_secrets(text: str, *, where: str, allow: Iterable[str] = ()) -> None:
    found = find_secrets(text, allow=allow)
    if found:
        raise GuardrailViolationError(
            f"Posible secreto en {where}: {', '.join(found)}. Se detiene la tarea."
        )


def is_secret_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return (
        name in SECRET_FILE_NAMES
        or name.startswith(".env.")
        or PurePosixPath(name).suffix in SECRET_FILE_SUFFIXES
    )


@cache
def _glob_regex(pattern: str, *, ignore_case: bool = False) -> re.Pattern[str]:
    # Un patrón terminado en `/` es un directorio: equivale a `dir/**`.
    if pattern.endswith("/"):
        pattern += "**"
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(parts) + r"\Z", re.IGNORECASE if ignore_case else 0)


def matches_glob(path: str, pattern: str, *, ignore_case: bool = False) -> bool:
    """Glob sobre rutas POSIX: `*` no cruza `/`, `**` sí y `dir/` equivale a `dir/**`."""
    return _glob_regex(pattern, ignore_case=ignore_case).match(path) is not None


def ensure_path_safe(path: str, *, protected: Iterable[str] = ()) -> str:
    """Rechaza rutas que ningún agente debería tocar y devuelve la ruta normalizada.

    La ruta debe ser relativa y sin `..`: los permisos se validan sobre la ruta real,
    no sobre un string que después se resuelve a otro lugar. Estas violaciones son
    sospechosas (posible manipulación del modelo), así que detienen la tarea.
    """
    candidate = PurePosixPath(path.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise GuardrailViolationError(f"Ruta no normalizada: {path}")
    normalized = candidate.as_posix()
    if any(matches_glob(normalized, p, ignore_case=True) for p in (*PROTECTED_PATHS, *protected)):
        raise GuardrailViolationError(f"Archivo protegido por el harness: {path}")
    if is_secret_file(normalized):
        raise GuardrailViolationError(f"Archivo de secretos: {path}")
    return normalized


@dataclass(frozen=True, slots=True)
class RoleScope:
    """Qué archivos puede escribir un rol: los que coinciden con `allowed` y no con `excluded`.

    El Implementer excluye los tests del proyecto, que pueden vivir junto al código
    (`src/**/*.test.tsx`); el Tester solo escribe esos tests.
    """

    allowed: tuple[str, ...]
    excluded: tuple[str, ...] = ()

    def contains(self, path: str) -> bool:
        return any(matches_glob(path, p) for p in self.allowed) and not any(
            matches_glob(path, p) for p in self.excluded
        )

    def reason(self, path: str) -> str | None:
        """Motivo si la ruta (ya normalizada) queda fuera del alcance del rol, o None."""
        if self.contains(path):
            return None
        detail = f"permitido: {', '.join(self.allowed)}"
        if self.excluded:
            detail += f"; excepto: {', '.join(self.excluded)}"
        return f"Escritura fuera de alcance: {path} ({detail})"


@dataclass(frozen=True, slots=True)
class WritePolicy:
    """Todo lo que se valida antes de que un rol escriba un archivo."""

    scope: RoleScope
    protected: tuple[str, ...] = ()
    secret_allowlist: tuple[str, ...] = ()

    def safe_path(self, path: str) -> str:
        return ensure_path_safe(path, protected=self.protected)

    def check_content(self, content: str, *, where: str) -> None:
        ensure_no_secrets(content, where=where, allow=self.secret_allowlist)


def ensure_path_writable(path: str, allowed: Iterable[str]) -> None:
    """Un agente solo escribe dentro de su alcance y nunca en archivos protegidos."""
    reason = RoleScope(tuple(allowed)).reason(ensure_path_safe(path))
    if reason:
        raise GuardrailViolationError(reason)


TEST_WEAKENING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pytest skip/xfail", re.compile(r"\bpytest\.(mark\.)?(skip|skipif|xfail)\b")),
    ("unittest skip", re.compile(r"\bunittest\.(skip|skipIf|skipUnless|expectedFailure)\b")),
    ("unittest skip", re.compile(r"\.skipTest\(")),
    ("jest/vitest skip", re.compile(r"\b(?:it|test|describe)\.(?:skip|todo)\b")),
    ("jest/vitest skip", re.compile(r"\bx(?:it|test|describe)\(")),
    ("jest/vitest only", re.compile(r"\b(?:it|test|describe)\.only\b|\bf(?:it|describe)\(")),
)


def ensure_no_test_weakening(text: str, *, where: str) -> None:
    """Un test no se saltea ni se marca como falla esperada para que un gate pase."""
    found = sorted({label for label, pattern in TEST_WEAKENING_PATTERNS if pattern.search(text)})
    if found:
        raise GuardrailViolationError(
            f"Test debilitado en {where}: {', '.join(found)}. Se detiene la tarea."
        )


SENSITIVE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "auth": ("auth", "authn", "authz", "authentication", "authorization", "autenticación"),
    "login": ("login", "logins", "signin", "logout"),
    "password": ("password", "passwords", "passwd", "contraseña", "contraseñas"),
    "payment": ("payment", "payments"),
    "pago": ("pago", "pagos"),
    "billing": ("billing",),
    "credential": ("credential", "credentials", "credencial", "credenciales"),
    "secret": ("secret", "secrets"),
    "token": ("token", "tokens"),
    "pii": ("pii",),
}


def _whole_word(forms: Iterable[str]) -> re.Pattern[str]:
    # Límite de palabra solo por letras: `_`, `/` o `.` separan ("user_password", "auth/"),
    # pero "author" o "tokenizer" no cuentan como "auth" o "token".
    alternatives = "|".join(re.escape(f) for f in forms)
    return re.compile(rf"(?<![^\W\d_])(?:{alternatives})(?![^\W\d_])")


_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (keyword, _whole_word(forms)) for keyword, forms in SENSITIVE_KEYWORDS.items()
)


def sensitive_areas(texts: Iterable[str]) -> list[str]:
    """Palabras sensibles presentes: auth, pagos o datos personales requieren a un humano."""
    joined = " ".join(texts).lower()
    return [keyword for keyword, pattern in _SENSITIVE_PATTERNS if pattern.search(joined)]
