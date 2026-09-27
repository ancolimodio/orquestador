import json
from pathlib import Path

import pytest

from prompt_maestro.errors import GuardrailViolationError
from prompt_maestro.guardrails import (
    RoleScope,
    WritePolicy,
    ensure_no_test_weakening,
    find_secrets,
    matches_glob,
)
from prompt_maestro.llm import ScriptedLLM
from prompt_maestro.models import TaskStatus
from prompt_maestro.orchestrator import Orchestrator, OrchestratorConfig
from prompt_maestro.project import ProjectConfigError, ProjectProfile, load_profile
from prompt_maestro.prompts import build_system_prompt
from prompt_maestro.workspace import Workspace
from tests.conftest import FakeGateRunner, changes_json, review_json

REACT_PROFILE = ProjectProfile(
    stack="TypeScript + React (Create React App)",
    test_framework="Jest + Testing Library",
    code=("web/src/**/*.ts", "web/src/**/*.tsx"),
    tests=("web/src/**/*.test.ts", "web/src/**/*.test.tsx"),
    protected=("web/package.json",),
)

REACT_TOML = """
stack = "TypeScript + React (Create React App)"
test_framework = "Jest + Testing Library"
code = ["web/src/**/*.ts", "web/src/**/*.tsx"]
tests = ["web/src/**/*.test.ts", "web/src/**/*.test.tsx"]
protected = ["web/package.json"]
secret_allowlist = ["public-web-key-123456"]

[[gates.B]]
name = "tsc"
argv = ["node", "web/node_modules/typescript/bin/tsc", "--noEmit", "-p", "web"]

[[gates.C]]
name = "jest"
argv = ["npm", "--prefix", "web", "test", "--", "--watchAll=false"]
"""


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("src/app/calc.py", "src/", True),
        ("src/app/calc.py", "src/*.py", False),
        ("src/calc.py", "src/*.py", True),
        ("web/src/pages/Dashboard.tsx", "web/src/**/*.tsx", True),
        ("web/src/Dashboard.tsx", "web/src/**/*.tsx", True),
        ("web/src/Dashboard.tsx", "web/src/**/*.test.tsx", False),
        ("web/public/index.html", "web/src/**", False),
        ("a.md", "?.md", True),
    ],
)
def test_matches_glob(path: str, pattern: str, expected: bool) -> None:
    assert matches_glob(path, pattern) is expected


def test_colocated_tests_belong_to_the_tester_not_the_implementer() -> None:
    code = REACT_PROFILE.code_policy.scope
    tests = REACT_PROFILE.test_policy.scope
    assert code.contains("web/src/pages/Dashboard.tsx")
    assert not code.contains("web/src/pages/Dashboard.test.tsx")
    assert tests.contains("web/src/pages/Dashboard.test.tsx")
    assert "excepto" in (code.reason("web/src/pages/Dashboard.test.tsx") or "")


def test_project_protected_paths_add_to_the_defaults() -> None:
    policy = REACT_PROFILE.code_policy
    for path in ("web/package.json", "AGENTS.md", "prompt-maestro.toml"):
        with pytest.raises(GuardrailViolationError, match="protegido"):
            policy.safe_path(path)


def test_secret_allowlist_only_skips_the_declared_value() -> None:
    public = 'apiKey: "' + "public-web-key-123456" + '"'
    private = 'apiKey: "' + "another-private-value-999" + '"'
    assert find_secrets(public, allow=["public-web-key-123456"]) == []
    assert find_secrets(public + "\n" + private, allow=["public-web-key-123456"]) != []


@pytest.mark.parametrize(
    "code",
    ["it.skip('x', () => {})", "test.only('x', () => {})", "xit('x', () => {})", "it.todo('x')"],
)
def test_jest_weakening_is_blocked(code: str) -> None:
    with pytest.raises(GuardrailViolationError):
        ensure_no_test_weakening(code, where="web/src/a.test.tsx")


def test_load_profile_defaults_to_python_without_config(tmp_path: Path) -> None:
    profile = load_profile(tmp_path)
    assert profile == ProjectProfile()
    assert "C" in profile.gate_specs()


def test_load_profile_reads_the_project_config(tmp_path: Path) -> None:
    (tmp_path / "prompt-maestro.toml").write_text(REACT_TOML, encoding="utf-8")
    profile = load_profile(tmp_path)
    assert profile.test_framework == "Jest + Testing Library"
    assert [c.name for c in profile.gate_specs()["B"]] == ["tsc"]
    assert profile.code_policy.secret_allowlist == ("public-web-key-123456",)


@pytest.mark.parametrize("content", ["stack = ", 'unknown_key = "x"', "code = 3"])
def test_invalid_config_is_a_clear_error(tmp_path: Path, content: str) -> None:
    (tmp_path / "prompt-maestro.toml").write_text(content, encoding="utf-8")
    with pytest.raises(ProjectConfigError, match="inválido"):
        load_profile(tmp_path)


def test_prompts_come_from_the_profile() -> None:
    tester = build_system_prompt("tester", "", REACT_PROFILE)
    implementer = build_system_prompt("implementer", "", REACT_PROFILE)
    assert "Jest + Testing Library" in tester and "pytest" not in tester
    assert "`web/src/**/*.test.tsx`" in tester
    assert "TypeScript + React" in implementer and "Python" not in implementer


def _react_repo(root: Path) -> Path:
    (root / "web" / "src").mkdir(parents=True)
    (root / "web" / "src" / "format.ts").write_text(
        "export const upper = (s: string): string => s.toUpperCase();\n", encoding="utf-8"
    )
    (root / "web" / "src" / "format.test.ts").write_text(
        "import { upper } from './format';\ntest('upper', () => expect(upper('a')).toBe('A'));\n",
        encoding="utf-8",
    )
    return root


def _react_plan() -> str:
    return json.dumps(
        {
            "task_id": "x",
            "goal": "Agregar lower",
            "impacted": [
                {
                    "path": "web/src/format.ts",
                    "symbols": ["upper"],
                    "new_symbols": ["lower"],
                    "change": "modify",
                }
            ],
            "acceptance_criteria": ["lower('A') devuelve 'a'"],
        }
    )


FORMAT_TS = (
    "export const upper = (s: string): string => s.toUpperCase();\n"
    "export const lower = (s: string): string => s.toLowerCase();\n"
)


async def test_react_project_runs_end_to_end_with_colocated_tests(tmp_path: Path) -> None:
    repo = _react_repo(tmp_path)
    colocated_test = changes_json(
        "web/src/lower.test.ts",
        "import { lower } from './format';\ntest('lower', () => expect(lower('A')).toBe('a'));\n",
    )
    llm = ScriptedLLM(
        {
            "planner": [_react_plan()],
            # El primer intento mete un test junto al código: vuelve como feedback.
            "implementer": [
                changes_json("web/src/format.test.ts", "// pisado\n"),
                changes_json("web/src/format.ts", FORMAT_TS),
            ],
            "tester": [colocated_test],
            "reviewer": [review_json()],
        }
    )
    report = await Orchestrator(
        llm=llm,
        workspace=Workspace(repo),
        gate_runner=FakeGateRunner(),
        config=OrchestratorConfig(profile=REACT_PROFILE),
    ).run("Agregar lower")

    assert report.status is TaskStatus.DONE
    assert report.attempts["B"] == 2
    assert "excepto" in [p for role, p in llm.calls if role == "implementer"][1]
    assert (repo / "web" / "src" / "lower.test.ts").exists()
    tester_prompt = next(p for role, p in llm.calls if role == "tester")
    assert "web/src/format.test.ts" in tester_prompt  # los tests existentes van de contexto


async def test_tester_cannot_rewrite_existing_colocated_tests(tmp_path: Path) -> None:
    repo = _react_repo(tmp_path)
    llm = ScriptedLLM(
        {
            "planner": [_react_plan()],
            "implementer": [changes_json("web/src/format.ts", FORMAT_TS)],
            "tester": [changes_json("web/src/format.test.ts", "test('x', () => {});\n")],
        }
    )
    report = await Orchestrator(
        llm=llm,
        workspace=Workspace(repo),
        gate_runner=FakeGateRunner(),
        config=OrchestratorConfig(profile=REACT_PROFILE),
    ).run("r")
    assert report.status is TaskStatus.ESCALATED
    assert "test existente" in (report.escalation_reason or "")


def test_write_policy_is_frozen() -> None:
    policy = WritePolicy(RoleScope(("src/",)))
    with pytest.raises(AttributeError):
        policy.scope = RoleScope(("tests/",))  # type: ignore[misc]
