"""
T7 tests: orchestrator.run --fixtures (CP1 fixture path)

All tests use an isolated temporary git repo — no dependency on teammate tags
(baseline-clean, demo-bad, etc.) which are unavailable until M2 delivers them.

CP1 acceptance (fixture mode):
  --fixtures bad          → ATTENTION_REQUIRED
  --fixtures clean        → READY_FOR_HUMAN_REVIEW
  --fixtures error        → VERIFICATION_FAILED
  --fixtures tests-failed → VERIFICATION_FAILED

Additional:
  - report.json and report.md are written to the run dir
  - report.json validates against the report schema
  - re-stamped fixtures contain the real runId and snapshotId from context
  - --agents-done mode: given a run dir with pre-written results, produces report
  - run_id format is correct

CP1 STATUS:
  These tests prove the full pipeline (context → re-stamp → aggregate → report)
  on an isolated toy repo. CP1 is considered PASS on the fixture path.

  CP1 real-tag acceptance ("on baseline-clean..demo-bad it lists exactly the
  files Member 2 changed") is PENDING until M2 pushes the required git tags.
  See T8 blocker list.
"""

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

_REPO = pathlib.Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))

from orchestrator.run import run_fixtures, run_agents_done
from orchestrator.validate import validate_result


# ── toy git repo (same pattern as T3) ────────────────────────────────────────

def _git(*args, cwd):
    result = subprocess.run(
        ["git"] + list(args), cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


def _write(path: pathlib.Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


@pytest.fixture()
def toy_repo(tmp_path):
    """
    Minimal git repo that satisfies orchestrator.context's requirements.
    base tag  — sample-project with one Python file and a test
    cand tag  — adds service.py, modifies main.py
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com",
    }
    subprocess.run(["git", "init", "-b", "main"],
                   cwd=str(repo), check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t.com"],
                   cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(repo), check=True, capture_output=True)

    # base
    _write(repo / "sample-project/app/main.py",
           "VERSION = '1.0'\n")
    _write(repo / "sample-project/tests/test_main.py",
           "from app import main\ndef test_v(): assert main.VERSION\n")
    _write(repo / "sample-project/README.md", "# Sample Project\n")
    subprocess.run(["git", "add", "."], cwd=str(repo),
                   check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"],
                   cwd=str(repo), check=True, capture_output=True, env=env)
    _git("tag", "base", cwd=repo)

    # candidate
    _write(repo / "sample-project/app/service.py",
           "def get_items(): return []\n")
    _write(repo / "sample-project/app/main.py",
           "from app import service\nVERSION = '1.1'\n")
    subprocess.run(["git", "add", "."], cwd=str(repo),
                   check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add service"],
                   cwd=str(repo), check=True, capture_output=True, env=env)
    _git("tag", "cand", cwd=repo)
    return repo


@pytest.fixture()
def runs_dir(tmp_path):
    d = tmp_path / "runs"
    d.mkdir()
    return d


# ── CP1 acceptance: fixture pipeline produces correct states ─────────────────

class TestFixturePipelineCP1:
    """
    These are the CP1 fixture-mode acceptance tests.
    They use an isolated toy repo; real M2 tags are not required.
    """

    def test_fixtures_bad_attention_required(self, toy_repo, runs_dir):
        run_id, state, _ = run_fixtures(
            "base", "cand", "bad",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        assert state == "ATTENTION_REQUIRED", \
            f"--fixtures bad should give ATTENTION_REQUIRED, got {state}"
        report = json.loads((runs_dir / run_id / "report.json").read_text(encoding="utf-8"))
        assert report["counts"]["blocking"] > 0
        assert report["execution"]["exitCode"] == 0

    def test_fixtures_clean_ready(self, toy_repo, runs_dir):
        run_id, state, _ = run_fixtures(
            "base", "cand", "clean",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        assert state == "READY_FOR_HUMAN_REVIEW", \
            f"--fixtures clean should give READY_FOR_HUMAN_REVIEW, got {state}"

    def test_fixtures_error_verification_failed(self, toy_repo, runs_dir):
        run_id, state, _ = run_fixtures(
            "base", "cand", "error",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        assert state == "VERIFICATION_FAILED", \
            f"--fixtures error should give VERIFICATION_FAILED, got {state}"

    def test_fixtures_tests_failed_verification_failed(self, toy_repo, runs_dir):
        run_id, state, _ = run_fixtures(
            "base", "cand", "tests-failed",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        assert state == "VERIFICATION_FAILED", \
            f"--fixtures tests-failed should give VERIFICATION_FAILED, got {state}"
        report = json.loads((runs_dir / run_id / "report.json").read_text(encoding="utf-8"))
        assert "TESTS_FAILED" in {r["code"] for r in report["readiness"]["reasons"]}


# ── report files are written ──────────────────────────────────────────────────

class TestOutputFiles:
    def test_report_json_written(self, toy_repo, runs_dir):
        run_id, _, _ = run_fixtures(
            "base", "cand", "clean",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        assert (runs_dir / run_id / "report.json").exists()

    def test_report_md_written(self, toy_repo, runs_dir):
        run_id, _, report_path = run_fixtures(
            "base", "cand", "clean",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        assert report_path.exists()
        assert report_path.name == "report.md"

    def test_report_json_schema_valid(self, toy_repo, runs_dir):
        run_id, _, _ = run_fixtures(
            "base", "cand", "bad",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        data = json.loads(
            (runs_dir / run_id / "report.json").read_text(encoding="utf-8")
        )
        vr = validate_result(data)
        assert vr.valid, f"report.json invalid: {vr.errors}"

    def test_report_md_contains_state(self, toy_repo, runs_dir):
        _, state, report_path = run_fixtures(
            "base", "cand", "clean",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        content = report_path.read_text(encoding="utf-8")
        assert "READY FOR HUMAN REVIEW" in content

    def test_no_tmp_files_after_run(self, toy_repo, runs_dir):
        run_id, _, _ = run_fixtures(
            "base", "cand", "bad",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        tmp_files = list((runs_dir / run_id).glob("*.tmp"))
        assert tmp_files == []


# ── re-stamping ───────────────────────────────────────────────────────────────

class TestReStamping:
    def test_restamped_run_id_matches_context(self, toy_repo, runs_dir):
        run_id, _, _ = run_fixtures(
            "base", "cand", "bad",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        run_dir = runs_dir / run_id
        ctx  = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        cr   = json.loads((run_dir / "code-review-result.json").read_text(encoding="utf-8"))
        assert cr["runId"] == ctx["runId"]

    def test_restamped_snapshot_id_matches_context(self, toy_repo, runs_dir):
        run_id, _, _ = run_fixtures(
            "base", "cand", "bad",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        run_dir = runs_dir / run_id
        ctx = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        cr  = json.loads((run_dir / "code-review-result.json").read_text(encoding="utf-8"))
        assert cr["snapshotId"] == ctx["snapshotId"]

    def test_all_three_result_files_written(self, toy_repo, runs_dir):
        run_id, _, _ = run_fixtures(
            "base", "cand", "clean",
            repo_dir=toy_repo, runs_dir=runs_dir,
        )
        run_dir = runs_dir / run_id
        for agent in ["code-review", "testing", "documentation"]:
            assert (run_dir / f"{agent}-result.json").exists(), \
                f"Missing: {agent}-result.json"


# ── --agents-done mode ────────────────────────────────────────────────────────

class TestAgentsDone:
    def test_agents_done_produces_report(self, toy_repo, runs_dir):
        """
        Write fixture results by hand, then use run_agents_done to aggregate.
        This simulates the scenario where agents ran manually.
        """
        from orchestrator.context import build_context
        from orchestrator.run import _write_fixture_results

        run_id, run_dir = build_context(
            "base", "cand", repo_dir=toy_repo, runs_dir=runs_dir
        )
        ctx = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        _write_fixture_results(run_dir, "clean", run_id, ctx["snapshotId"])

        _, state, report_path = run_agents_done(run_id, runs_dir=runs_dir)
        assert state == "READY_FOR_HUMAN_REVIEW"
        assert report_path.exists()


# ── run_id format ─────────────────────────────────────────────────────────────

def test_run_id_format(toy_repo, runs_dir):
    import re
    run_id, _, _ = run_fixtures(
        "base", "cand", "clean",
        repo_dir=toy_repo, runs_dir=runs_dir,
    )
    assert re.match(r"^run-\d{8}-\d{6}-[0-9a-f]{7}$", run_id), \
        f"Unexpected runId format: {run_id}"


# ── CP1 REAL-TAG STATUS (not a pytest test — documentation) ──────────────────
#
# CP1 fixture-mode: PASS (proved by tests above on isolated toy repo)
#
# CP1 real-tag mode: PENDING
#   Blocked on M2 delivering:
#     git tag baseline-clean   (clean sample-project, all tests pass)
#     git tag demo-bad         (planted problems)
#   Until those tags exist, the acceptance criterion
#   "on baseline-clean..demo-bad it lists exactly the files Member 2 changed"
#   cannot be verified.  Do not claim CP1 fully passed.
