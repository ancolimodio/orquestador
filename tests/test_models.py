import pytest
from pydantic import ValidationError

from prompt_maestro.models import CheckResult, GateResult, Plan, Review, Severity, Verdict


def test_plan_requires_impacted_files() -> None:
    with pytest.raises(ValidationError):
        Plan(task_id="t", goal="x", impacted=[], acceptance_criteria=["a"])


def test_plan_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(
            {
                "task_id": "t",
                "goal": "x",
                "impacted": [{"path": "a.py", "change": "modify"}],
                "acceptance_criteria": ["a"],
                "extra": True,
            }
        )


@pytest.mark.parametrize(
    ("verdict", "severity", "met", "approved"),
    [
        (Verdict.APPROVE, None, True, True),
        (Verdict.APPROVE, Severity.MINOR, True, True),
        (Verdict.APPROVE, Severity.MAJOR, True, False),
        (Verdict.APPROVE, None, False, False),
        (Verdict.REQUEST_CHANGES, None, True, False),
    ],
)
def test_review_approval_rules(
    verdict: Verdict, severity: Severity | None, met: bool, approved: bool
) -> None:
    findings = (
        [{"severity": severity, "path": "a.py", "issue": "i", "fix": "f"}] if severity else []
    )
    review = Review.model_validate(
        {"task_id": "t", "verdict": verdict, "findings": findings, "acceptance_criteria_met": met}
    )
    assert review.approved is approved


def test_gate_failure_report_only_includes_failed_checks() -> None:
    gate = GateResult(
        gate="B",
        checks=[
            CheckResult(name="ok", command=["true"], returncode=0, output="fine"),
            CheckResult(name="mypy", command=["mypy"], returncode=1, output="error: bad type"),
        ],
    )
    report = gate.failure_report()
    assert not gate.passed
    assert "mypy" in report and "error: bad type" in report
    assert "fine" not in report
