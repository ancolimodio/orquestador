"""Guardrails: qué puede y qué no puede hacer un agente.

Todas las reglas son deterministas. El harness no le pide permiso al modelo:
las verifica antes de ejecutar cualquier acción.
"""

import re
from collections.abc import Iterable, Sequence
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

PROTECTED_PATHS: tuple[str, ...] = ("AGENTS.md", ".github/", ".gitlab-ci.yml", "pyproject.toml")

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


def find_secrets(text: str) -> list[str]:
    """Devuelve los tipos de secreto detectados, sin exponer su valor."""
    return [label for label, pattern in SECRET_PATTERNS if pattern.search(text)]


def ensure_no_secrets(text: str, *, where: str) -> None:
    found = find_secrets(text)
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


def ensure_path_writable(path: str, allowed_prefixes: Iterable[str]) -> None:
    """Un agente solo escribe dentro de sus carpetas y nunca en archivos protegidos.

    La ruta debe ser relativa y sin `..`: los permisos se validan sobre la ruta real,
    no sobre un string que después se resuelve a otro lugar.
    """
    candidate = PurePosixPath(path.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise GuardrailViolationError(f"Ruta no normalizada: {path}")
    normalized = candidate.as_posix()
    folded = normalized.casefold()
    if any(folded == p.casefold() or folded.startswith(p.casefold()) for p in PROTECTED_PATHS):
        raise GuardrailViolationError(f"Archivo protegido por el harness: {path}")
    if is_secret_file(normalized):
        raise GuardrailViolationError(f"Archivo de secretos: {path}")
    prefixes = tuple(allowed_prefixes)
    if not any(normalized.startswith(p) for p in prefixes):
        raise GuardrailViolationError(
            f"Escritura fuera de alcance: {path} (permitido: {', '.join(prefixes)})"
        )


TEST_WEAKENING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pytest skip/xfail", re.compile(r"\bpytest\.(mark\.)?(skip|skipif|xfail)\b")),
    ("unittest skip", re.compile(r"\bunittest\.(skip|skipIf|skipUnless|expectedFailure)\b")),
    ("unittest skip", re.compile(r"\.skipTest\(")),
)


def ensure_no_test_weakening(text: str, *, where: str) -> None:
    """Un test no se saltea ni se marca como falla esperada para que un gate pase."""
    found = sorted({label for label, pattern in TEST_WEAKENING_PATTERNS if pattern.search(text)})
    if found:
        raise GuardrailViolationError(
            f"Test debilitado en {where}: {', '.join(found)}. Se detiene la tarea."
        )


SENSITIVE_KEYWORDS: tuple[str, ...] = (
    "auth",
    "login",
    "password",
    "payment",
    "pago",
    "billing",
    "credential",
    "secret",
    "token",
    "pii",
)


def sensitive_areas(texts: Iterable[str]) -> list[str]:
    """Palabras sensibles presentes: auth, pagos o datos personales requieren a un humano."""
    joined = " ".join(texts).lower()
    return [k for k in SENSITIVE_KEYWORDS if re.search(rf"\b{k}", joined)]
