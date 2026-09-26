"""
T3 tests: orchestrator.context

Uses a self-contained temporary git repository (no dependency on M2 tags).

Fixture layout:
  base commit  — sample-project/app/main.py + sample-project/tests/test_main.py
  candidate    — adds sample-project/app/service.py, modifies main.py

Tests cover:
- runId format
- context.json validates against schema
- changedFiles lists exactly the files modified between commits
- snapshot contains sample-project/ but NOT evaluation/ or other repo dirs
- snapshot excludes binary files, >200 KB files, and secret-named files
- relevantSource includes changed files + files that import them
- relevantTests includes tests that import changed modules
- relevantDocs includes README.md and docs/ files
- diff.patch is written and non-empty when there are changes
- intent falls back to commit message; --intent overrides
- unknown ref exits non-zero
- generated context.json is schema-valid
- import scan (_scan_imports) helper unit tests
"""

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

# ── make orchestrator importable from repo root ──────────────────────────────
_REPO = pathlib.Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))

from orchestrator.context import (
    _parse_diff,
    _scan_imports,
    _is_safe_excluded,
    build_context,
    _make_run_id,
)
from orchestrator.validate import validate_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Temp git repo fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def toy_repo(tmp_path):
    """
    A minimal git repo that mimics PRism-AI's sample-project/ scope.

    base commit (tagged 'base'):
      sample-project/app/main.py          — imports nothing from changed modules
      sample-project/tests/test_main.py   — imports main
      sample-project/README.md
      sample-project/docs/api.md
      evaluation/seeded-issues.json       — must NOT appear in snapshot

    candidate commit (tagged 'cand'):
      sample-project/app/service.py (NEW) — simple module
      sample-project/app/main.py (MOD)    — now imports service
      sample-project/tests/test_main.py   — imports service (should appear in relevantTests)
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"}

    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), check=True,
                   capture_output=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo),
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo),
                   check=True, capture_output=True)

    # base commit
    _write(repo / "sample-project/app/main.py",
           "# main module\nVERSION = '1.0'\n")
    _write(repo / "sample-project/tests/test_main.py",
           "from app import main\ndef test_version():\n    assert main.VERSION == '1.0'\n")
    _write(repo / "sample-project/README.md",
           "# Sample Project\n")
    _write(repo / "sample-project/docs/api.md",
           "# API\n")
    _write(repo / "evaluation/seeded-issues.json",
           '{"seeds": []}\n')
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline: initial sample-project"],
                   cwd=str(repo), check=True, capture_output=True, env=env)
    _git("tag", "base", cwd=repo)

    # candidate commit
    _write(repo / "sample-project/app/service.py",
           "# service module\ndef get_items():\n    return []\n")
    _write(repo / "sample-project/app/main.py",
           "# main module\nfrom app import service\nVERSION = '1.1'\n")
    _write(repo / "sample-project/tests/test_main.py",
           "from app import main, service\n"
           "def test_version():\n    assert main.VERSION == '1.1'\n"
           "def test_service():\n    assert service.get_items() == []\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add service module"],
                   cwd=str(repo), check=True, capture_output=True, env=env)
    _git("tag", "cand", cwd=repo)

    return repo


@pytest.fixture()
def toy_runs(tmp_path):
    d = tmp_path / "runs"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Unit tests: pure helpers (no git)
# ---------------------------------------------------------------------------

class TestScanImports:
    def test_from_import(self):
        # "from app import service" → the *module* is 'app'; we track module deps
        assert "app" in _scan_imports("from app import service\n")

    def test_plain_import(self):
        assert "os" in _scan_imports("import os\n")

    def test_dotted_from(self):
        assert "app" in _scan_imports("from app.service import get_items\n")

    def test_empty(self):
        assert _scan_imports("# no imports\n") == set()

    def test_multi_import(self):
        result = _scan_imports("import os, sys\n")
        assert "os" in result
        assert "sys" in result


class TestParseDiff:
    def test_modified_file(self):
        patch = (
            "diff --git a/sample-project/app/main.py b/sample-project/app/main.py\n"
            "--- a/sample-project/app/main.py\n"
            "+++ b/sample-project/app/main.py\n"
            "@@ -1,1 +1,2 @@\n"
            " VERSION = '1.0'\n"
            "+VERSION2 = '2.0'\n"
        )
        files = _parse_diff(patch)
        assert len(files) == 1
        assert files[0].path == "sample-project/app/main.py"
        assert files[0].status == "M"
        assert files[0].additions == 1
        assert files[0].deletions == 0
        assert len(files[0].hunks) == 1

    def test_added_file(self):
        patch = (
            "diff --git a/sample-project/app/new.py b/sample-project/app/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/sample-project/app/new.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+# new\n"
            "+def f(): pass\n"
        )
        files = _parse_diff(patch)
        assert files[0].status == "A"
        assert files[0].additions == 2

    def test_deleted_file(self):
        patch = (
            "diff --git a/sample-project/app/old.py b/sample-project/app/old.py\n"
            "deleted file mode 100644\n"
            "--- a/sample-project/app/old.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-# old\n"
            "-def g(): pass\n"
        )
        files = _parse_diff(patch)
        assert files[0].status == "D"
        assert files[0].deletions == 2

    def test_empty_diff(self):
        assert _parse_diff("") == []


class TestIsExcluded:
    def test_secret_env(self):
        excluded, reason = _is_safe_excluded(".env", 10)
        assert excluded
        assert reason == "secret"

    def test_secret_pem(self):
        excluded, reason = _is_safe_excluded("certs/server.pem", 10)
        assert excluded
        assert reason == "secret"

    def test_binary_png(self):
        excluded, reason = _is_safe_excluded("sample-project/img/logo.png", 10)
        assert excluded
        assert reason == "binary"

    def test_too_large(self):
        excluded, reason = _is_safe_excluded("sample-project/data.py", 300 * 1024)
        assert excluded
        assert reason == "too-large"

    def test_normal_py_file(self):
        excluded, reason = _is_safe_excluded("sample-project/app/service.py", 1024)
        assert not excluded
        assert reason is None


# ---------------------------------------------------------------------------
# Integration tests: build_context on toy_repo
# ---------------------------------------------------------------------------

class TestBuildContext:
    def test_run_id_format(self, toy_repo, toy_runs):
        run_id, _ = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        import re
        assert re.match(r"^run-\d{8}-\d{6}-[0-9a-f]{7}$", run_id), \
            f"Unexpected runId format: {run_id}"

    def test_context_json_written_and_schema_valid(self, toy_repo, toy_runs):
        run_id, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        ctx_path = run_dir / "context.json"
        assert ctx_path.exists()
        data = json.loads(ctx_path.read_text(encoding="utf-8"))
        result = validate_result(data)
        assert result.valid, f"context.json invalid: {result.errors}"

    def test_changed_files_lists_modified_and_added(self, toy_repo, toy_runs):
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        data = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        paths = {cf["path"] for cf in data["changedFiles"]}
        # service.py is new (A), main.py and test_main.py are modified (M)
        assert "sample-project/app/service.py" in paths
        assert "sample-project/app/main.py" in paths

    def test_changed_files_status_codes(self, toy_repo, toy_runs):
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        data = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        by_path = {cf["path"]: cf for cf in data["changedFiles"]}
        assert by_path["sample-project/app/service.py"]["status"] == "A"
        assert by_path["sample-project/app/main.py"]["status"] == "M"

    def test_snapshot_contains_sample_project(self, toy_repo, toy_runs):
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        snap = run_dir / "snapshot"
        assert (snap / "sample-project" / "app" / "service.py").exists()
        assert (snap / "sample-project" / "app" / "main.py").exists()

    def test_snapshot_excludes_evaluation(self, toy_repo, toy_runs):
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        snap = run_dir / "snapshot"
        # evaluation/ must not be in the snapshot
        assert not (snap / "evaluation").exists()

    def test_diff_patch_written(self, toy_repo, toy_runs):
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        patch = run_dir / "diff.patch"
        assert patch.exists()
        content = patch.read_text(encoding="utf-8")
        assert "service.py" in content

    def test_relevant_source_includes_changed_files(self, toy_repo, toy_runs):
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        data = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        assert "sample-project/app/service.py" in data["relevantSource"]
        assert "sample-project/app/main.py" in data["relevantSource"]

    def test_relevant_tests_includes_test_file(self, toy_repo, toy_runs):
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        data = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        assert any("test_main" in t for t in data["relevantTests"])

    def test_relevant_docs_includes_readme_and_docs_dir(self, toy_repo, toy_runs):
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        data = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        assert "sample-project/README.md" in data["relevantDocs"]
        assert any("api.md" in d for d in data["relevantDocs"])

    def test_intent_from_commit_message(self, toy_repo, toy_runs):
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        data = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        assert data["intent"] == "add service module"

    def test_intent_override(self, toy_repo, toy_runs):
        _, run_dir = build_context(
            "base", "cand", intent="my custom intent",
            repo_dir=toy_repo, runs_dir=toy_runs,
        )
        data = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        assert data["intent"] == "my custom intent"

    def test_snapshot_id_is_candidate_sha(self, toy_repo, toy_runs):
        cand_sha = _git("rev-parse", "cand", cwd=toy_repo)
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        data = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        assert data["snapshotId"] == cand_sha

    def test_base_commit_is_base_sha(self, toy_repo, toy_runs):
        base_sha = _git("rev-parse", "base", cwd=toy_repo)
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        data = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        assert data["baseCommit"] == base_sha

    def test_scope_limitations_present(self, toy_repo, toy_runs):
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        data = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        assert data["scopeLimitations"]

    def test_context_atomic_write(self, toy_repo, toy_runs):
        """No .tmp file should remain after a successful run."""
        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        tmp_files = list(run_dir.glob("*.tmp"))
        assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"

    def test_exclusion_of_binary_file(self, toy_repo, toy_runs):
        """A .png file committed under sample-project/ must appear in exclusions."""
        # add a png to the candidate
        png = toy_repo / "sample-project" / "logo.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
               "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"}
        subprocess.run(["git", "add", "."], cwd=str(toy_repo), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add png"],
                       cwd=str(toy_repo), check=True, capture_output=True, env=env)
        _git("tag", "-f", "cand", cwd=toy_repo)

        _, run_dir = build_context("base", "cand", repo_dir=toy_repo, runs_dir=toy_runs)
        data = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        reasons = {e["reason"] for e in data["exclusions"]}
        assert "binary" in reasons
        # and the file must NOT be in the snapshot
        assert not (run_dir / "snapshot" / "sample-project" / "logo.png").exists()


# ---------------------------------------------------------------------------
# Bad-ref test: unknown ref exits non-zero
# ---------------------------------------------------------------------------

def test_unknown_ref_exits(toy_repo, toy_runs, monkeypatch):
    """build_context with a nonexistent ref must call sys.exit."""
    monkeypatch.chdir(toy_repo)
    with pytest.raises(SystemExit) as exc_info:
        build_context("base", "nonexistent-ref-xyz", repo_dir=toy_repo, runs_dir=toy_runs)
    assert exc_info.value.code != 0
