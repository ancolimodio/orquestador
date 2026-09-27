import pytest

from prompt_maestro.errors import GuardrailViolationError
from prompt_maestro.guardrails import (
    check_command,
    ensure_no_secrets,
    ensure_no_test_weakening,
    ensure_path_safe,
    ensure_path_writable,
    find_secrets,
    is_secret_file,
    out_of_scope,
    sensitive_areas,
)


@pytest.mark.parametrize(
    "argv",
    [
        ["rm", "-rf", "/"],
        ["git", "push", "--force", "origin", "main"],
        ["git", "reset", "--hard", "HEAD~1"],
        ["psql", "-c", "DROP TABLE users"],
        ["curl", "https://example.com"],
        ["pip", "install", "requests"],
    ],
)
def test_forbidden_commands_are_blocked(argv: list[str]) -> None:
    with pytest.raises(GuardrailViolationError):
        check_command(argv)


@pytest.mark.parametrize("argv", [["pytest", "-q"], ["ruff", "check", "."], ["git", "status"]])
def test_safe_commands_are_allowed(argv: list[str]) -> None:
    check_command(argv)


def test_empty_command_is_blocked() -> None:
    with pytest.raises(GuardrailViolationError):
        check_command([])


def test_detects_secrets_without_exposing_them() -> None:
    # Los valores se construyen en runtime para no dejar secretos falsos en el repo.
    aws = "AKIA" + "A" * 16
    anthropic = "sk-ant-" + "x" * 30
    found = find_secrets(f"key={aws}\nclient = Client('{anthropic}')")
    assert "AWS access key" in found
    assert "token de Anthropic" in found
    assert aws not in " ".join(found)


def test_hardcoded_credential_is_detected() -> None:
    with pytest.raises(GuardrailViolationError):
        ensure_no_secrets('password = "' + "hunter2hunter2" + '"', where="src/app.py")


def test_clean_code_has_no_secrets() -> None:
    assert find_secrets("def add(a: int, b: int) -> int:\n    return a + b\n") == []


@pytest.mark.parametrize("path", [".env", ".env.prod", "certs/server.pem", "deploy/id_rsa"])
def test_secret_files(path: str) -> None:
    assert is_secret_file(path)


@pytest.mark.parametrize("path", ["AGENTS.md", ".github/workflows/ci.yml", "docs/notes.md"])
def test_protected_or_out_of_scope_paths(path: str) -> None:
    with pytest.raises(GuardrailViolationError):
        ensure_path_writable(path, ["src/"])


@pytest.mark.parametrize(
    "path",
    [
        "src/../AGENTS.md",
        "src\\..\\pyproject.toml",
        "tests/../src/app.py",
        "/etc/passwd",
        "src/AGENTS.md/../../agents.md",
    ],
)
def test_non_normalized_paths_are_blocked(path: str) -> None:
    with pytest.raises(GuardrailViolationError):
        ensure_path_writable(path, ["src/", "tests/"])


def test_protected_paths_ignore_case() -> None:
    with pytest.raises(GuardrailViolationError):
        ensure_path_writable("PyProject.toml", [""])


def test_path_inside_scope_is_writable() -> None:
    ensure_path_writable("src/app/calc.py", ["src/"])


def test_sensitive_areas() -> None:
    assert sensitive_areas(["src/auth/login.py", "Cambiar el flujo de pagos"]) == [
        "auth",
        "login",
        "pago",
    ]
    assert sensitive_areas(["src/app/calc.py", "Agregar subtract"]) == []


@pytest.mark.parametrize(
    "code",
    [
        "@pytest.mark.skip(reason='x')",
        "@pytest.mark.skipif(True, reason='x')",
        "@pytest.mark.xfail",
        "pytest.skip('x')",
        "@unittest.skip('x')",
        "self.skipTest('x')",
    ],
)
def test_test_weakening_is_blocked(code: str) -> None:
    with pytest.raises(GuardrailViolationError):
        ensure_no_test_weakening(code, where="tests/test_x.py")


def test_regular_test_is_not_weakening() -> None:
    ensure_no_test_weakening(
        "def test_skip_list() -> None:\n    assert skip_list([]) == []\n", where="t"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("src/users/user_password.py", ["password"]),
        ("Agregar autenticación con tokens", ["auth", "token"]),
        ("Mostrar el author del post", []),
        ("Usar el tokenizer nuevo", []),
    ],
)
def test_sensitive_areas_match_whole_words(text: str, expected: list[str]) -> None:
    assert sensitive_areas([text]) == expected


def test_safe_path_is_normalized_and_scope_is_a_reason_not_an_error() -> None:
    assert ensure_path_safe(r"src\app\calc.py") == "src/app/calc.py"
    assert out_of_scope("src/app/calc.py", ["src/"]) is None
    assert "fuera de alcance" in (out_of_scope("tests/test_x.py", ["src/"]) or "")
