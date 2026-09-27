"""
Large projects: token limit (L1), no duplicate code in scans (L2), two-pass review (L5),
only re-checking what changed (L6), summary memory (M1), project map (M2), and the
progress display (P1-P3).

The AI is a stub. The token limit is shrunk so a small test project counts as "large".
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys

import pytest

_REPO = pathlib.Path(__file__).parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import prism  # noqa: E402
from orchestrator import codemap, ingest, memory as memory_mod  # noqa: E402
from orchestrator.ui import Progress  # noqa: E402

BUGGY = '''"""Discounts."""


def bulk_price(price, quantity):
    """10% off when buying 10 or more."""
    if quantity > 10:
        return price * quantity * 0.9
    return price * quantity
'''


def _filler(i: int) -> str:
    body = "\n".join(f"    x{j} = {j} * n  # padding line {j}" for j in range(60))
    return f'"""Helper module {i}."""\n\n\ndef helper_{i}(n):\n{body}\n    return n\n'


def _project(root: pathlib.Path, modules: int = 12) -> pathlib.Path:
    (root / "shop").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "shop" / "__init__.py").write_text("")
    (root / "shop" / "pricing.py").write_text(BUGGY)
    for i in range(modules):
        (root / "shop" / f"helper_{i:02d}.py").write_text(_filler(i))
    (root / "tests" / "test_pricing.py").write_text(
        "from shop.pricing import bulk_price\n\n\ndef test_small():\n"
        "    assert bulk_price(1, 2) == 2\n")
    (root / "README.md").write_text("# Shop\n`bulk_price(price, quantity)`\n")
    return root


@pytest.fixture
def ai(monkeypatch):
    """Stub AI. Records every call as (kind, user text)."""
    calls: list[tuple[str, str]] = []
    finding = {
        "id": "CODE-001", "key": "LOGIC_ERROR|project/shop/pricing.py|bulk_price|threshold",
        "severity": "HIGH", "category": "LOGIC_ERROR",
        "title": "Discount starts at 11, docstring says 10",
        "file": "project/shop/pricing.py", "line": 6, "symbol": "bulk_price",
        "description": "quantity > 10 excludes exactly 10.", "evidence": "if quantity > 10:",
        "evidenceType": "source-analysis", "recommendation": "Use >= 10.", "relatedFiles": [],
    }
    state = {"fail_summaries": False}

    def fake_call(provider, system, messages):
        user = messages[0]["content"]
        if system == prism.SUMMARY_SYSTEM:
            calls.append(("summary", user))
            if state["fail_summaries"]:
                raise prism.ModelError("summary service down")
            paths = re.findall(r"^=== (.+?) ===$", user, flags=re.M)
            return json.dumps({"files": [
                {"path": p, "summary": f"summary of {p}",
                 "risk": 3 if p.endswith("pricing.py") else 0, "reason": "boundary check"}
                for p in paths]})
        calls.append(("review", user))
        reply = {"findings": [], "limitations": []}
        if system.startswith("# Code Review Agent") and "if quantity > 10:" in user:
            reply["findings"] = [finding]
        if system.startswith("# Testing Agent"):
            reply["behaviourMap"] = "| Behaviour | Test(s) | Covered? |"
        return json.dumps(reply)

    monkeypatch.setattr(prism, "pick_provider",
                        lambda: prism.Provider("openai", "http://stub", "k", "stub", "stub"))
    monkeypatch.setattr(prism, "call_model", fake_call)
    monkeypatch.setenv("PRISM_PLAIN", "1")
    before = set(prism.RUNS.glob("run-*")) | set(prism.RUNS.glob("workspaces/ws-*"))
    yield calls, state
    for p in (set(prism.RUNS.glob("run-*")) | set(prism.RUNS.glob("workspaces/ws-*"))) - before:
        ingest._remove(p)


@pytest.fixture
def small_limit(monkeypatch):
    """Make a ~15-file project 'too large' so the two-pass review is used."""
    monkeypatch.setattr(prism, "MAX_INPUT_TOKENS", 9000)
    monkeypatch.setattr(prism, "SUMMARY_BATCH_TOKENS", 1500)


def _reviews(calls):
    return [u for kind, u in calls if kind == "review"]


def _summaries(calls):
    return [u for kind, u in calls if kind == "summary"]


# ---------------------------------------------------------------------------
# L1 / L5: large projects get a two-pass review that fits the limit
# ---------------------------------------------------------------------------

def test_large_scan_uses_two_passes_and_every_call_fits(tmp_path, ai, small_limit, capsys):
    calls, _ = ai
    proj = _project(tmp_path / "proj")
    code = prism.main(["check", "--scan", str(proj), "--out", str(proj / "PRISM-REPORT.md")])
    out = capsys.readouterr().out
    assert code == 1, out                                   # the planted bug still blocks
    assert "two-pass review" in out
    assert len(_summaries(calls)) >= 2                      # pass 1 ran, in several batches
    for user in _reviews(calls):
        assert codemap.estimate_tokens(user) <= prism.MAX_INPUT_TOKENS  # L1
        assert "PROJECT MAP" in user                        # L5 second pass
    report = (proj / "PRISM-REPORT.md").read_text(encoding="utf-8")
    assert "Discount starts at 11" in report
    assert "seen only as a one-line summary" in report      # honest about coverage


def test_risky_and_changed_files_are_shown_in_full_first(tmp_path, ai, small_limit):
    calls, _ = ai
    proj = _project(tmp_path / "proj", modules=20)
    prism.main(["check", "--scan", str(proj)])
    reviews = _reviews(calls)
    assert len(reviews) == 3
    for user in reviews:
        assert "=== project/shop/pricing.py (runs/" in user  # rated risk 3 -> in full
        shown = re.findall(r"^=== (project/shop/helper_\d+\.py) \(runs/", user, flags=re.M)
        assert len(shown) < 20                               # not everything fits in full


def test_small_projects_are_reviewed_exactly_as_before(tmp_path, ai):
    calls, _ = ai
    proj = _project(tmp_path / "proj", modules=1)
    prism.main(["check", "--scan", str(proj)])
    assert _summaries(calls) == []                           # no pass 1
    assert all("PROJECT MAP" not in u for u in _reviews(calls))


def test_scan_does_not_send_the_code_twice(tmp_path, ai):
    calls, _ = ai
    proj = _project(tmp_path / "proj", modules=1)
    prism.main(["check", "--scan", str(proj)])
    for user in _reviews(calls):                             # L2
        assert "/diff.patch ===" not in user                  # no diff section
        assert user.count("if quantity > 10:") <= 1


def test_before_after_still_gets_the_diff(tmp_path, ai):
    calls, _ = ai
    before = _project(tmp_path / "before", modules=1)
    after = _project(tmp_path / "after", modules=1)
    (after / "shop" / "pricing.py").write_text(BUGGY.replace("0.9", "0.85"))
    prism.main(["check", "--before", str(before), "--after", str(after)])
    assert any("/diff.patch ===" in u for u in _reviews(calls))


def test_failed_summaries_do_not_fail_the_check(tmp_path, ai, small_limit, capsys):
    calls, state = ai
    state["fail_summaries"] = True
    proj = _project(tmp_path / "proj")
    assert prism.main(["check", "--scan", str(proj)]) == 1   # reviewers still ran
    review = _reviews(calls)[0]
    assert "PROJECT MAP" in review and "summary of" not in review   # code-map fallback used
    cache = proj / ".prism" / "summaries.json"
    assert not cache.exists() or json.loads(cache.read_text())["files"] == {}  # not cached


# ---------------------------------------------------------------------------
# M1 / L6: memory
# ---------------------------------------------------------------------------

def test_unchanged_project_reuses_last_report_with_no_ai_calls(tmp_path, ai, small_limit, capsys):
    calls, _ = ai
    proj = _project(tmp_path / "proj")
    first = prism.main(["check", "--scan", str(proj)])
    n = len(calls)
    capsys.readouterr()
    second = prism.main(["check", "--scan", str(proj), "--out", str(tmp_path / "r.md")])
    out = capsys.readouterr().out
    assert second == first
    assert len(calls) == n                                   # 0 AI calls
    assert "No changes since the last check" in out
    assert (tmp_path / "r.md").read_text(encoding="utf-8").startswith("#")


def test_only_changed_files_are_summarised_again(tmp_path, ai, small_limit):
    calls, _ = ai
    proj = _project(tmp_path / "proj")
    prism.main(["check", "--scan", str(proj)])
    summarised_first = sum(u.count("\n=== ") + 1 for u in _summaries(calls))
    calls.clear()
    (proj / "shop" / "helper_03.py").write_text(_filler(3) + "\n# edited\n")
    prism.main(["check", "--scan", str(proj)])
    again = re.findall(r"^=== (.+?) ===$", "\n".join(_summaries(calls)), flags=re.M)
    assert again == ["project/shop/helper_03.py"]             # everything else remembered
    assert summarised_first > 10
    review = _reviews(calls)[0]
    assert "=== project/shop/helper_03.py (runs/" in review   # the edited file is shown in full


def test_memory_files_are_written_and_never_scanned(tmp_path, ai, small_limit):
    proj = _project(tmp_path / "proj")
    prism.main(["check", "--scan", str(proj)])
    mem = proj / ".prism"
    summaries = json.loads((mem / "summaries.json").read_text(encoding="utf-8"))
    pmap = json.loads((mem / "project-map.json").read_text(encoding="utf-8"))
    assert len(summaries["files"]) >= 14
    entry = pmap["files"]["project/shop/pricing.py"]
    assert "def bulk_price(price, quantity)" in entry["symbols"]
    assert entry["summary"]["risk"] == 3
    assert (mem / "last-check.json").exists()
    assert all(".prism" not in str(f) for f in ingest._included_files(proj))


def test_corrupt_memory_is_rebuilt(tmp_path, ai, small_limit):
    proj = _project(tmp_path / "proj")
    (proj / ".prism").mkdir()
    (proj / ".prism" / "summaries.json").write_text("{not json")
    (proj / ".prism" / "last-check.json").write_text("[]")
    assert prism.main(["check", "--scan", str(proj)]) == 1


def test_changed_instructions_invalidate_the_reused_report(tmp_path, ai, small_limit,
                                                           monkeypatch):
    calls, _ = ai
    proj = _project(tmp_path / "proj")
    prism.main(["check", "--scan", str(proj)])
    n = len(calls)
    monkeypatch.setattr(prism, "_settings_digest", lambda: "different-instructions")
    prism.main(["check", "--scan", str(proj)])
    assert len(calls) > n


# ---------------------------------------------------------------------------
# M2 / L5 building blocks
# ---------------------------------------------------------------------------

def test_code_map_lists_symbols_and_survives_bad_files(tmp_path):
    (tmp_path / "a.py").write_text('"""Mod doc."""\nclass C:\n    def m(self, x, *a, k=1, **kw): pass\n'
                                   'async def f(a, /, b): pass\n')
    (tmp_path / "broken.py").write_text("def (:\n")
    (tmp_path / "notes.md").write_text("# Title here\ntext\n")
    maps = codemap.build_map(tmp_path, ["a.py", "broken.py", "notes.md"])
    assert maps["a.py"].doc == "Mod doc."
    assert maps["a.py"].symbols == ["class C", "C.m(x, *a, k, **kw)", "def f(a, b)"]
    assert maps["broken.py"].parse_error
    assert maps["notes.md"].doc == "Title here"


def test_pack_files_respects_budget_and_priority():
    files = [f"p/f{i}.py" for i in range(10)]
    sizes = {f: 3500 for f in files}             # ~1000 tokens each
    full, rest = codemap.pack_files(files, sizes, 3100, "code-review",
                                    changed={"p/f7.py"},
                                    summaries={"p/f2.py": {"risk": 3}})
    assert full[:2] == ["p/f7.py", "p/f2.py"]     # changed first, then risky
    assert len(full) == 3 and len(rest) == 7


def test_render_map_stops_at_budget():
    maps = {f"f{i}.py": codemap.FileMap(f"f{i}.py", 10, "doc " * 20) for i in range(50)}
    text, left_out = codemap.render_map(maps, budget_tokens=300)
    assert left_out and codemap.estimate_tokens(text) <= 300


def test_memory_roundtrip_and_fingerprint(tmp_path):
    m = memory_mod.Memory(tmp_path)
    m.remember_summary("abc", {"summary": "s", "risk": 1, "reason": "r"})
    m.save()
    assert memory_mod.Memory(tmp_path).summary("abc")["summary"] == "s"
    a = memory_mod.fingerprint({"x.py": "1"}, "model-a")
    assert a != memory_mod.fingerprint({"x.py": "2"}, "model-a")
    assert a != memory_mod.fingerprint({"x.py": "1"}, "model-b")


# ---------------------------------------------------------------------------
# P1-P3: progress display
# ---------------------------------------------------------------------------

def test_plain_progress_prints_numbered_steps(capsys):
    with Progress("demo", ["Preparing code", "Reviewing", "Deciding", "Writing report"],
                  fancy=False) as p:
        p.step(0)
        p.detail("large project: summarising files 3/9")
        p.step(1)
        p.agent_start("testing")
        p.agent_done("testing", "completed")
        p.step(2)
        p.step(3)
    out = capsys.readouterr().out
    for line in ("[1/4] Preparing code", "[2/4] Reviewing", "[3/4] Deciding",
                 "[4/4] Writing report", "summarising files 3/9", "testing        completed"):
        assert line in out


def test_fancy_progress_renders_all_states():
    from rich.console import Console
    p = Progress("demo", ["Preparing code", "Reviewing: 3 specialists", "Deciding"],
                 fancy=True)
    p.step(0)
    p.step(1, "large project")
    for a in ("code-review", "testing", "documentation"):
        p.agent_start(a)
    p.agent_done("testing", "completed")
    p.agent_done("documentation", "error")
    console = Console(record=True, width=100, force_terminal=True)
    console.print(p._render())
    text = console.export_text()
    for piece in ("PRism-AI", "✓", "[2/3] Reviewing", "code-review", "completed", "✗",
                  "error", "[3/3] Deciding"):
        assert piece in text


def test_progress_is_plain_when_not_a_terminal(monkeypatch):
    monkeypatch.delenv("PRISM_PLAIN", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    assert Progress("t", ["a"]).fancy is False
    monkeypatch.setenv("PRISM_PLAIN", "1")
    assert Progress("t", ["a"]).fancy is False
