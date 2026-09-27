"""
Checking code outside this repository: --scan, --before/--after, --patch/--folder,
--out and `prism.py watch`.

The AI is replaced by a stub, so no API key is needed. Every test builds its own
small project in tmp_path; run folders and workspaces are removed afterwards.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import threading
import time

import pytest

_REPO = pathlib.Path(__file__).parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import prism  # noqa: E402
from orchestrator import ingest  # noqa: E402
from orchestrator.context import build_context  # noqa: E402

SECRET = "super-secret-value-123"

PRICING_V1 = '''"""Prices."""


def apply_discount(price, percent):
    """percent must be 0-100."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * (1 - percent / 100), 2)
'''

PRICING_V2 = PRICING_V1 + '''

def bulk_price(price, quantity):
    """10% off when buying 10 or more."""
    if quantity > 10:
        return apply_discount(price * quantity, 10)
    return price * quantity
'''

TESTS = '''from shop.pricing import apply_discount


def test_discount():
    assert apply_discount(100, 10) == 90
'''


def _make_project(root: pathlib.Path, pricing: str = PRICING_V1) -> pathlib.Path:
    (root / "shop").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "shop" / "__init__.py").write_text("")
    (root / "shop" / "pricing.py").write_text(pricing)
    (root / "tests" / "test_pricing.py").write_text(TESTS)
    (root / "README.md").write_text("# Shop\n`apply_discount(price, percent)`\n")
    return root


@pytest.fixture
def runs(tmp_path):
    """A private runs/ folder so tests never touch the real one."""
    return tmp_path / "runs"


def _context(runs: pathlib.Path, ws: pathlib.Path) -> dict:
    project = ingest.detect_project(ws / ingest.SCOPE)
    _, rdir = build_context("base", "candidate", repo_dir=ws, runs_dir=runs, project=project)
    return json.loads((rdir / "context.json").read_text(encoding="utf-8"))


def _changed(ctx: dict) -> dict[str, str]:
    return {c["path"]: c["status"] for c in ctx["changedFiles"]}


# ---------------------------------------------------------------------------
# --scan
# ---------------------------------------------------------------------------

def test_scan_reviews_every_file_as_new(tmp_path, runs):
    proj = _make_project(tmp_path / "proj")
    ctx = _context(runs, ingest.from_scan(runs, str(proj)))
    assert _changed(ctx) == {
        "project/README.md": "A", "project/shop/__init__.py": "A",
        "project/shop/pricing.py": "A", "project/tests/test_pricing.py": "A"}
    assert "project/tests/test_pricing.py" in ctx["relevantTests"]
    assert ctx["scopePath"] == "project"
    assert ctx["project"]["testCommand"] == "python -m pytest -q"


def test_scan_never_copies_secrets_binaries_caches_or_old_reports(tmp_path, runs):
    proj = _make_project(tmp_path / "proj")
    (proj / ".env").write_text(f"TOKEN={SECRET}\n")
    (proj / "server.pem").write_text(SECRET)
    (proj / "logo.png").write_bytes(b"\x89PNG" + SECRET.encode())
    (proj / "big.py").write_text("x = 1\n" + "#" * (201 * 1024))
    (proj / "node_modules" / "lib").mkdir(parents=True)
    (proj / "node_modules" / "lib" / "index.js").write_text(SECRET)
    (proj / ".git").mkdir()
    (proj / ".git" / "config").write_text(SECRET)
    (proj / ingest.REPORT_NAME).write_text(f"old findings {SECRET}")

    ws = ingest.from_scan(runs, str(proj))
    ctx = _context(runs, ws)
    copied = {p.relative_to(ws / "project").as_posix()
              for p in (ws / "project").rglob("*") if p.is_file()}
    assert copied == {"README.md", "shop/__init__.py", "shop/pricing.py",
                      "tests/test_pricing.py"}
    leaked = [p for p in runs.rglob("*") if p.is_file() and ".git" not in p.parts
              and SECRET in p.read_text(encoding="utf-8", errors="ignore")]
    assert leaked == []
    assert all(ingest.REPORT_NAME not in path for path in _changed(ctx))


def test_scan_refuses_projects_too_big_for_one_review(tmp_path, runs, monkeypatch):
    proj = _make_project(tmp_path / "proj")
    monkeypatch.setattr(ingest, "MAX_SCAN_BYTES", 100)
    with pytest.raises(ingest.IngestError, match="too much for one scan"):
        ingest.from_scan(runs, str(proj))
    assert not (runs / "workspaces").exists() or not any((runs / "workspaces").iterdir())


@pytest.mark.parametrize("make", [
    lambda t: t / "missing",                                # does not exist
    lambda t: (t / "file.txt").write_text("x") and t / "file.txt",   # a file
])
def test_scan_needs_a_folder(tmp_path, runs, make):
    with pytest.raises(ingest.IngestError, match="is not a folder"):
        ingest.from_scan(runs, str(make(tmp_path)))


def test_scan_of_folder_with_nothing_reviewable(tmp_path, runs):
    empty = tmp_path / "empty"
    (empty / "node_modules").mkdir(parents=True)
    (empty / ".env").write_text("A=1")
    with pytest.raises(ingest.IngestError, match="no reviewable files"):
        ingest.from_scan(runs, str(empty))


def test_scan_handles_spaces_and_unicode_in_paths(tmp_path, runs):
    proj = _make_project(tmp_path / "my project ünï")
    (proj / "shop" / "naïve module.py").write_text("x = 'héllo'\n", encoding="utf-8")
    ctx = _context(runs, ingest.from_scan(runs, str(proj)))
    assert "project/shop/naïve module.py" in _changed(ctx)


# ---------------------------------------------------------------------------
# --before / --after
# ---------------------------------------------------------------------------

def test_folders_show_only_what_changed(tmp_path, runs):
    before = _make_project(tmp_path / "before")
    after = _make_project(tmp_path / "after", PRICING_V2)
    (after / "shop" / "shipping.py").write_text("def fee():\n    return 5\n")
    (after / "README.md").unlink()
    ctx = _context(runs, ingest.from_folders(runs, str(before), str(after)))
    assert _changed(ctx) == {"project/shop/pricing.py": "M",
                             "project/shop/shipping.py": "A",
                             "project/README.md": "D"}
    assert "project/tests/test_pricing.py" in ctx["relevantTests"]


def test_identical_folders_mean_no_changes(tmp_path, runs):
    a, b = _make_project(tmp_path / "a"), _make_project(tmp_path / "b")
    assert _changed(_context(runs, ingest.from_folders(runs, str(a), str(b)))) == {}


def test_folders_ignore_line_ending_only_copies(tmp_path, runs):
    a = _make_project(tmp_path / "a")
    b = _make_project(tmp_path / "b")
    (b / "shop" / "pricing.py").write_bytes(PRICING_V1.replace("\n", "\r\n").encode())
    changed = _changed(_context(runs, ingest.from_folders(runs, str(a), str(b))))
    assert set(changed) <= {"project/shop/pricing.py"}  # reported at most as a modification


def test_folders_must_exist(tmp_path, runs):
    a = _make_project(tmp_path / "a")
    with pytest.raises(ingest.IngestError, match="--after"):
        ingest.from_folders(runs, str(a), str(tmp_path / "nope"))


# ---------------------------------------------------------------------------
# --patch / --folder
# ---------------------------------------------------------------------------

def _git_diff(before: pathlib.Path, after: pathlib.Path) -> str:
    """A patch as `git diff` writes it, with a/ b/ prefixes relative to the project."""
    proc = subprocess.run(["git", "diff", "--no-index", "--no-prefix", str(before), str(after)],
                          capture_output=True, text=True)
    text = proc.stdout
    for side, root in (("a", before), ("b", after)):
        text = text.replace(root.as_posix().lstrip("/") + "/", f"{side}/")
        text = text.replace(str(root).replace("\\", "/") + "/", f"{side}/")
    return text


def test_patch_from_git_diff_is_applied(tmp_path, runs):
    before = _make_project(tmp_path / "before")
    after = _make_project(tmp_path / "after", PRICING_V2)
    repo = tmp_path / "repo"
    shutil.copytree(before, repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "v1"],
                   cwd=repo, check=True)
    (repo / "shop" / "pricing.py").write_text(PRICING_V2)
    patch = tmp_path / "change.patch"
    patch.write_bytes(subprocess.run(["git", "diff"], cwd=repo, capture_output=True,
                                     check=True).stdout)
    ws = ingest.from_patch(runs, str(before), str(patch))
    assert (ws / "project" / "shop" / "pricing.py").read_text() == PRICING_V2
    assert _changed(_context(runs, ws)) == {"project/shop/pricing.py": "M"}
    assert after  # keep fixture symmetry explicit


def test_patch_from_plain_unified_diff_is_applied(tmp_path, runs):
    before = _make_project(tmp_path / "before")
    patch = tmp_path / "plain.diff"
    old = PRICING_V1.splitlines(keepends=True)
    new = PRICING_V2.splitlines(keepends=True)
    import difflib
    patch.write_text("".join(difflib.unified_diff(old, new, "a/shop/pricing.py",
                                                  "b/shop/pricing.py")))
    ws = ingest.from_patch(runs, str(before), str(patch))
    assert (ws / "project" / "shop" / "pricing.py").read_text() == PRICING_V2


def test_patch_that_does_not_apply_is_a_clear_error(tmp_path, runs):
    before = _make_project(tmp_path / "before")
    bad = tmp_path / "bad.patch"
    bad.write_text("--- a/shop/pricing.py\n+++ b/shop/pricing.py\n@@ -1,1 +1,1 @@\n"
                   "-this line is not in the file\n+replacement\n")
    with pytest.raises(ingest.IngestError, match="patch does not apply"):
        ingest.from_patch(runs, str(before), str(bad))
    assert not any((runs / "workspaces").iterdir())  # half-built workspace removed


def test_patch_file_must_exist(tmp_path, runs):
    before = _make_project(tmp_path / "before")
    with pytest.raises(ingest.IngestError, match="not found"):
        ingest.from_patch(runs, str(before), str(tmp_path / "missing.patch"))


def test_patch_cannot_write_outside_the_workspace(tmp_path, runs):
    before = _make_project(tmp_path / "before")
    evil = tmp_path / "evil.patch"
    evil.write_text("--- /dev/null\n+++ b/../../escaped.txt\n@@ -0,0 +1 @@\n+pwned\n")
    with pytest.raises(ingest.IngestError):
        ingest.from_patch(runs, str(before), str(evil))
    assert not list(tmp_path.rglob("escaped.txt"))


# ---------------------------------------------------------------------------
# detect_project
# ---------------------------------------------------------------------------

def test_detect_project_defaults_and_override(tmp_path):
    py = _make_project(tmp_path / "py")
    assert ingest.detect_project(py)["testCommand"] == "python -m pytest -q"
    assert ingest.detect_project(py, "npm test")["testCommand"] == "npm test"
    js = tmp_path / "js"
    js.mkdir()
    (js / "index.js").write_text("module.exports = 1\n")
    settings = ingest.detect_project(js)
    assert settings["language"] == "Unknown"
    assert "pytest" not in settings["testCommand"]


# ---------------------------------------------------------------------------
# prism.py check: argument rules, end to end, --out
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv, message", [
    (["check", "--scan", "x", "--before", "y", "--after", "z"], "use only one"),
    (["check", "--before", "x"], "--before and --after go together"),
    (["check", "--after", "x"], "--before and --after go together"),
    (["check", "--patch", "x.patch"], "--patch and --folder go together"),
    (["check", "--folder", "x"], "--patch and --folder go together"),
])
def test_check_rejects_incomplete_or_mixed_inputs(argv, message, capsys):
    with pytest.raises(SystemExit) as exc:
        prism.main(argv)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


@pytest.fixture
def ai(monkeypatch, tmp_path):
    """Stub AI: code review flags bulk_price when that code is present; others find nothing."""
    finding = {
        "id": "CODE-001", "key": "LOGIC_ERROR|project/shop/pricing.py|bulk_price|threshold",
        "severity": "HIGH", "category": "LOGIC_ERROR",
        "title": "Bulk discount starts at 11, docstring says 10",
        "file": "project/shop/pricing.py", "line": 14, "symbol": "bulk_price",
        "description": "quantity > 10 excludes exactly 10.", "evidence": "if quantity > 10:",
        "evidenceType": "source-analysis", "recommendation": "Use >= 10.", "relatedFiles": [],
    }
    prompts: list[str] = []

    def fake_call(provider, system, messages):
        prompts.append(messages[0]["content"])
        reply = {"findings": [], "limitations": []}
        if system.startswith("# Code Review Agent") and "def bulk_price" in messages[0]["content"]:
            reply["findings"] = [finding]
        if system.startswith("# Testing Agent"):
            reply["behaviourMap"] = "| Behaviour | Test(s) | Covered? |"
        return json.dumps(reply)

    monkeypatch.setattr(prism, "pick_provider",
                        lambda: prism.Provider("openai", "http://stub", "k", "stub", "stub"))
    monkeypatch.setattr(prism, "call_model", fake_call)
    # aggregate/agent_io use the real runs/ folder, so use it and clean up afterwards
    before = set(prism.RUNS.glob("run-*")) | set(prism.RUNS.glob("workspaces/ws-*"))
    yield prompts
    for p in (set(prism.RUNS.glob("run-*")) | set(prism.RUNS.glob("workspaces/ws-*"))) - before:
        ingest._remove(p)


def test_scan_end_to_end_writes_report_into_the_project(tmp_path, ai, capsys):
    proj = _make_project(tmp_path / "proj", PRICING_V2)
    (proj / ".env").write_text(f"TOKEN={SECRET}\n")
    out = proj / ingest.REPORT_NAME
    code = prism.main(["check", "--scan", str(proj), "--out", str(out)])
    printed = capsys.readouterr().out
    assert code == 1, printed                               # the HIGH finding blocks
    assert "ATTENTION REQUIRED" in printed
    assert "Bulk discount starts at 11" in out.read_text(encoding="utf-8")
    assert "tests: 1 passed, 0 failed" in printed        # the project's own tests really ran
    assert all(SECRET not in p for p in ai)               # the secret never reached the AI


def test_folders_end_to_end(tmp_path, ai, capsys):
    before = _make_project(tmp_path / "before")
    after = _make_project(tmp_path / "after", PRICING_V2)
    code = prism.main(["check", "--before", str(before), "--after", str(after)])
    printed = capsys.readouterr().out
    assert code == 1, printed
    assert "1 changed file(s)" in printed


def test_input_problem_exits_3_without_calling_the_ai(tmp_path, ai, capsys):
    code = prism.main(["check", "--scan", str(tmp_path / "missing")])
    assert code == 3
    assert "Input problem" in capsys.readouterr().out
    assert ai == []


def test_out_creates_missing_folders(tmp_path, ai, capsys):
    proj = _make_project(tmp_path / "proj", PRICING_V2)
    out = tmp_path / "reports" / "nested" / "r.md"
    prism.main(["check", "--scan", str(proj), "--out", str(out)])
    assert out.read_text(encoding="utf-8").startswith("#")


def test_shown_path_never_crashes_on_paths_outside_the_repo(tmp_path):
    outside = tmp_path / "report.md"
    outside.write_text("x")
    assert prism._shown_path(outside) == outside.resolve()
    inside = prism.REPO / "README.md"
    assert prism._shown_path(inside) == pathlib.Path("README.md")


# ---------------------------------------------------------------------------
# prism.py watch
# ---------------------------------------------------------------------------

def test_watch_checks_at_start_and_again_after_a_save(tmp_path, ai, capsys):
    proj = _make_project(tmp_path / "proj")
    report = proj / ingest.REPORT_NAME
    result = {}

    def run():
        result["code"] = prism.main(["watch", str(proj), "--quiet-seconds", "0.5",
                                     "--max-runs", "2"])

    t = threading.Thread(target=run, daemon=True)
    t.start()
    deadline = time.time() + 120
    while not report.exists() and time.time() < deadline:
        time.sleep(0.2)
    assert report.exists(), "first check did not write the report"
    first = report.read_text(encoding="utf-8")
    assert "READY FOR HUMAN REVIEW" in first     # v1 has no bulk_price, stub finds nothing new

    time.sleep(1.1)  # make sure the next save gets a newer timestamp
    (proj / "shop" / "pricing.py").write_text(PRICING_V2)
    t.join(120)
    assert not t.is_alive(), "watch did not re-check after the save"
    assert result["code"] == 1
    # The written report is never itself a trigger or an input.
    assert "ATTENTION REQUIRED" in report.read_text(encoding="utf-8")


def test_watch_needs_a_folder(tmp_path, capsys):
    assert prism.main(["watch", str(tmp_path / "missing")]) == 3
    assert "is not a folder" in capsys.readouterr().out


def test_vscode_tasks_file_is_valid_and_uses_only_supported_flags():
    import re
    raw = (_REPO / "integrations" / "vscode" / "tasks.json").read_text(encoding="utf-8")
    tasks = json.loads(re.sub(r"(?m)^\s*//.*$", "", raw))["tasks"]
    labels = {t["label"] for t in tasks}
    assert labels == {"PRism: scan whole project", "PRism: auto-check on save (watch)"}
    for task in tasks:
        argv = [a.replace("${env:PRISM_HOME}/prism.py", "prism.py")
                 .replace("${workspaceFolder}", "WS") for a in task["args"]]
        assert argv[0] == "prism.py"
        # parse with the real CLI parser: unknown flags would raise SystemExit
        parser_args = argv[1:]
        try:
            prism.main(parser_args + ["--help"])
        except SystemExit as exc:
            assert exc.code == 0


# ---------------------------------------------------------------------------
# --test-command
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command, exit_code, result", [
    ('python -c "import sys; sys.exit(3)"', 2, "test command exited 3"),
    ('python -c "print(\'custom tests ok\')"', 0, "READY FOR HUMAN REVIEW"),
    ("no-such-test-tool-xyz run", 2, "tests could not be started"),
])
def test_custom_test_command_runs_as_written(tmp_path, ai, capsys, command, exit_code, result):
    proj = _make_project(tmp_path / "proj")
    assert prism.main(["check", "--scan", str(proj), "--test-command", command]) == exit_code
    printed = capsys.readouterr().out
    assert result in printed
    assert "tests: ? passed, ? failed" in printed  # counts unknown, never invented as 0
