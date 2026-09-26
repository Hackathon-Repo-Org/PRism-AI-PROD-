"""
T1 + T2 tests: schema fixture validation and orchestrator.validate.

T1 rules:
- Every file in schemas/examples/ validates against the matching schema.
- Invalid findings (bad severity, missing evidence, wrong category for agent,
  null statusReason on error, execution non-null for code-review/documentation)
  are rejected by the schema.
- testing-result.bad.json uses exitCode 0 (passing tests, blocking MISSING_TEST finding).
- testing-result.tests-failed.json exists and has exitCode 1 (reserved for T7 VERIFICATION_FAILED path).

T2 rules (orchestrator.validate):
- Valid artifacts pass.
- Schema-invalid artifacts are rejected.
- With --context: runId mismatch, snapshotId mismatch, outside-scope path,
  and path-traversal path are all rejected.
- Kind auto-detection works for all three artifact types.
"""

import json
import pathlib
import pytest
from jsonschema import Draft202012Validator

from orchestrator.validate import validate_result, ValidationResult

REPO = pathlib.Path(__file__).parent.parent.parent
SCHEMAS_DIR = REPO / "schemas"
EXAMPLES_DIR = SCHEMAS_DIR / "examples"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _schema_errors(validator: Draft202012Validator, data: dict) -> list[str]:
    return [e.message for e in validator.iter_errors(data)]


CONTEXT_VALIDATOR      = _load_validator("context.schema.json")
AGENT_RESULT_VALIDATOR = _load_validator("agent-result.schema.json")

CONTEXT_EXAMPLES      = list(EXAMPLES_DIR.glob("context*.json"))
AGENT_RESULT_EXAMPLES = list(EXAMPLES_DIR.glob("*-result.*.json"))

assert CONTEXT_EXAMPLES,      "No context example fixtures found"
assert AGENT_RESULT_EXAMPLES, "No agent-result example fixtures found"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


def _context() -> dict:
    return _load("context.example.json")


# ---------------------------------------------------------------------------
# T1: every fixture validates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", CONTEXT_EXAMPLES, ids=lambda p: p.name)
def test_context_fixture_is_valid(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    errors = _schema_errors(CONTEXT_VALIDATOR, data)
    assert errors == [], f"{path.name}:\n" + "\n".join(errors)


@pytest.mark.parametrize("path", AGENT_RESULT_EXAMPLES, ids=lambda p: p.name)
def test_agent_result_fixture_is_valid(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    errors = _schema_errors(AGENT_RESULT_VALIDATOR, data)
    assert errors == [], f"{path.name}:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# T1: required fixture files exist
# ---------------------------------------------------------------------------

def test_all_required_fixture_files_exist():
    required = [
        "context.example.json",
        "code-review-result.clean.json",
        "code-review-result.bad.json",
        "code-review-result.error.json",
        "testing-result.clean.json",
        "testing-result.bad.json",
        "testing-result.tests-failed.json",
        "testing-result.error.json",
        "documentation-result.clean.json",
        "documentation-result.bad.json",
        "documentation-result.error.json",
    ]
    for name in required:
        assert (EXAMPLES_DIR / name).exists(), f"Missing fixture: {name}"


# ---------------------------------------------------------------------------
# T1: testing-result fixture properties
# ---------------------------------------------------------------------------

def test_testing_bad_fixture_has_passing_tests_and_blocking_finding():
    """Default bad scenario: tests pass (exitCode 0), but has a blocking finding."""
    data = _load("testing-result.bad.json")
    assert data["execution"]["exitCode"] == 0, "testing-result.bad.json should have exitCode 0"
    assert any(f["category"] == "MISSING_TEST" for f in data["findings"]), \
        "testing-result.bad.json should contain a MISSING_TEST finding"


def test_testing_tests_failed_fixture_has_exit_code_1():
    """VERIFICATION_FAILED fixture: tests fail (exitCode 1)."""
    data = _load("testing-result.tests-failed.json")
    assert data["execution"]["exitCode"] == 1, \
        "testing-result.tests-failed.json should have exitCode 1"


# ---------------------------------------------------------------------------
# T1: schema rejection tests
# ---------------------------------------------------------------------------

def _bad_result() -> dict:
    return _load("code-review-result.bad.json")


def test_bad_severity_rejected():
    data = _bad_result()
    data["findings"][0]["severity"] = "BLOCKER"
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), "Expected error for unknown severity"


def test_missing_evidence_rejected():
    data = _bad_result()
    del data["findings"][0]["evidence"]
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), "Expected error for missing evidence"


def test_bad_evidence_type_rejected():
    data = _bad_result()
    data["findings"][0]["evidenceType"] = "made-up"
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), "Expected error for unknown evidenceType"


def test_bad_finding_id_pattern_rejected():
    data = _bad_result()
    data["findings"][0]["id"] = "CODE-1"
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), "Expected error for bad id pattern"


def test_key_with_wrong_separator_rejected():
    """Keys must use '|' — a real agent run once produced 'CAT:file:symbol:subject'."""
    data = _bad_result()
    f = data["findings"][0]
    f["key"] = f["key"].replace("|", ":")
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), "Expected error for ':' separators"


def test_key_must_start_with_own_category_and_file():
    data = _load("code-review-result.bad.json")
    f = data["findings"][0]
    parts = f["key"].split("|")
    f["key"] = "|".join(["LOGIC_ERROR" if f["category"] != "LOGIC_ERROR" else "SECURITY"]
                        + parts[1:])
    result = validate_result(data, context=_context())
    assert not result.valid
    assert any("must start with its category" in e for e in result.errors)


def test_wrong_category_for_code_review_rejected():
    """A code-review result must not use testing-only categories."""
    data = _bad_result()
    data["findings"][0]["category"] = "MISSING_TEST"
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), \
        "Expected error: MISSING_TEST is not a valid code-review category"


def test_wrong_category_for_testing_rejected():
    """A testing result must not use code-review categories."""
    data = _load("testing-result.bad.json")
    data["findings"][0]["category"] = "NULL_HANDLING"
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), \
        "Expected error: NULL_HANDLING is not a valid testing category"


def test_wrong_category_for_documentation_rejected():
    """A documentation result must not use code-review categories."""
    data = _load("documentation-result.bad.json")
    data["findings"][0]["category"] = "LOGIC_ERROR"
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), \
        "Expected error: LOGIC_ERROR is not a valid documentation category"


def test_non_completed_status_requires_nonempty_status_reason():
    """error/timeout/skipped status must have a non-empty statusReason."""
    data = _bad_result()
    data["status"] = "error"
    data["statusReason"] = None
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), \
        "Expected error: statusReason must be non-empty string when status != completed"


def test_non_completed_empty_string_status_reason_rejected():
    data = _bad_result()
    data["status"] = "timeout"
    data["statusReason"] = ""
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), \
        "Expected error: empty statusReason is not allowed for non-completed status"


def test_completed_status_allows_null_status_reason():
    """completed status may have null statusReason."""
    data = _bad_result()
    assert data["status"] == "completed"
    assert data["statusReason"] is None
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data) == [], \
        "completed with null statusReason should be valid"


def test_code_review_execution_must_be_null():
    """code-review execution field must be null (not a testingExecution object)."""
    data = _bad_result()
    data["execution"] = {
        "command": "python -m pytest -q",
        "workingDirectory": "runs/x/snapshot/sample-project",
        "exitCode": 0,
        "logPath": "runs/x/logs/pytest.log",
        "junitPath": "runs/x/logs/junit.xml",
        "collected": 5, "passed": 5, "failed": 0, "skipped": 0, "errors": 0,
        "startedAt": "2026-09-26T10:15:07.000Z",
        "finishedAt": "2026-09-26T10:15:09.000Z",
        "durationMs": 2000
    }
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), \
        "Expected error: code-review execution must be null"


def test_documentation_execution_must_be_null():
    """documentation execution field must be null."""
    data = _load("documentation-result.bad.json")
    data["execution"] = {
        "command": "python -m pytest -q",
        "workingDirectory": "runs/x/snapshot/sample-project",
        "exitCode": 0,
        "logPath": "runs/x/logs/pytest.log",
        "junitPath": "runs/x/logs/junit.xml",
        "collected": 5, "passed": 5, "failed": 0, "skipped": 0, "errors": 0,
        "startedAt": "2026-09-26T10:15:07.000Z",
        "finishedAt": "2026-09-26T10:15:09.000Z",
        "durationMs": 2000
    }
    assert _schema_errors(AGENT_RESULT_VALIDATOR, data), \
        "Expected error: documentation execution must be null"


# ---------------------------------------------------------------------------
# T2: orchestrator.validate — kind detection
# ---------------------------------------------------------------------------

def test_validate_detects_context():
    data = _load("context.example.json")
    result = validate_result(data)
    assert result.valid, result.errors
    assert result.kind == "context"


def test_validate_detects_agent_result():
    data = _load("code-review-result.bad.json")
    result = validate_result(data)
    assert result.valid, result.errors
    assert result.kind == "agent-result"


def test_validate_unknown_kind_returns_invalid():
    result = validate_result({"schemaVersion": "1.0", "mystery": True})
    assert not result.valid
    assert result.kind is None


# ---------------------------------------------------------------------------
# T2: orchestrator.validate — valid artifacts pass
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "code-review-result.clean.json",
    "code-review-result.bad.json",
    "code-review-result.error.json",
    "testing-result.clean.json",
    "testing-result.bad.json",
    "testing-result.tests-failed.json",
    "testing-result.error.json",
    "documentation-result.clean.json",
    "documentation-result.bad.json",
    "documentation-result.error.json",
])
def test_validate_valid_agent_result_passes(name):
    data = _load(name)
    result = validate_result(data)
    assert result.valid, f"{name}: {result.errors}"


def test_validate_valid_context_passes():
    data = _load("context.example.json")
    result = validate_result(data)
    assert result.valid, result.errors


# ---------------------------------------------------------------------------
# T2: orchestrator.validate — schema-invalid artifacts are rejected
# ---------------------------------------------------------------------------

def test_validate_rejects_bad_severity():
    data = _bad_result()
    data["findings"][0]["severity"] = "BLOCKER"
    result = validate_result(data)
    assert not result.valid
    assert result.errors


# ---------------------------------------------------------------------------
# T2: orchestrator.validate — with context: identity checks
# ---------------------------------------------------------------------------

def test_validate_runid_mismatch_rejected():
    data = _load("code-review-result.bad.json")
    ctx = _context()
    data["runId"] = "run-20991231-235959-0000000"
    result = validate_result(data, context=ctx)
    assert not result.valid
    assert any("runId" in e for e in result.errors)


def test_validate_snapshotid_mismatch_rejected():
    data = _load("code-review-result.bad.json")
    ctx = _context()
    data["snapshotId"] = "a" * 40
    result = validate_result(data, context=ctx)
    assert not result.valid
    assert any("snapshotId" in e for e in result.errors)


def test_validate_matching_ids_pass():
    data = _load("code-review-result.bad.json")
    ctx = _context()
    # both fixtures use the same runId/snapshotId — should pass
    result = validate_result(data, context=ctx)
    assert result.valid, result.errors


# ---------------------------------------------------------------------------
# T2: orchestrator.validate — with context: scope checks
# ---------------------------------------------------------------------------

def test_validate_outside_scope_path_rejected():
    data = _load("code-review-result.bad.json")
    ctx = _context()
    data["findings"][0]["file"] = "outside-scope/evil.py"
    result = validate_result(data, context=ctx)
    assert not result.valid
    assert any("outside scope" in e for e in result.errors)


def test_validate_path_traversal_rejected():
    data = _load("code-review-result.bad.json")
    ctx = _context()
    data["findings"][0]["file"] = "sample-project/../etc/passwd"
    result = validate_result(data, context=ctx)
    assert not result.valid
    assert any("traversal" in e or "outside scope" in e for e in result.errors)


def test_validate_related_file_outside_scope_rejected():
    data = _load("code-review-result.bad.json")
    ctx = _context()
    data["findings"][0]["relatedFiles"] = ["evil/../../secret.py"]
    result = validate_result(data, context=ctx)
    assert not result.valid
    assert any("outside scope" in e or "traversal" in e for e in result.errors)


def test_validate_scope_passes_for_valid_paths():
    data = _load("code-review-result.bad.json")
    ctx = _context()
    # all file paths in bad.json start with sample-project/
    result = validate_result(data, context=ctx)
    assert result.valid, result.errors


# ---------------------------------------------------------------------------
# T2: CLI smoke test (via _main directly)
# ---------------------------------------------------------------------------

def test_cli_valid_file_exits_zero(tmp_path):
    from orchestrator.validate import _main
    src = EXAMPLES_DIR / "code-review-result.clean.json"
    dst = tmp_path / "test.json"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    assert _main([str(dst)]) == 0


def test_cli_invalid_file_exits_nonzero(tmp_path):
    from orchestrator.validate import _main
    bad = {"schemaVersion": "1.0", "agent": "code-review", "status": "NOPE"}
    dst = tmp_path / "bad.json"
    dst.write_text(json.dumps(bad), encoding="utf-8")
    assert _main([str(dst)]) == 1


def test_cli_with_context_and_scope_violation_exits_nonzero(tmp_path):
    from orchestrator.validate import _main
    data = _load("code-review-result.bad.json")
    data["findings"][0]["file"] = "outside/evil.py"
    artifact = tmp_path / "artifact.json"
    artifact.write_text(json.dumps(data), encoding="utf-8")
    ctx_src = EXAMPLES_DIR / "context.example.json"
    assert _main([str(artifact), "--context", str(ctx_src)]) == 1
