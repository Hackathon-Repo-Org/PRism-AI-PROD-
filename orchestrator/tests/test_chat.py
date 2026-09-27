"""
Chat mode (`prismai` / `prism.py chat`), the results table, `doctor`, and the launchers.

The AI is a scripted stub: each test lists the replies it gives, in order, and every
message it received is recorded so we can check what the AI was (and was not) shown.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import prism  # noqa: E402
from orchestrator import chat as chat_mod, ingest, results  # noqa: E402
from orchestrator.chat import ChatSession  # noqa: E402

SECRET = "sk-live-do-not-leak-4242"
PRICING = '''"""Prices."""


def bulk_price(price, quantity):
    """10% off when buying 10 or more."""
    if quantity > 10:
        return price * quantity * 0.9
    return price * quantity
'''


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "shop project"
    (root / "shop").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "shop" / "__init__.py").write_text("")
    (root / "shop" / "pricing.py").write_text(PRICING)
    (root / "tests" / "test_pricing.py").write_text(
        "from shop.pricing import bulk_price\n\n\ndef test_small():\n"
        "    assert bulk_price(1, 2) == 2\n")
    (root / "README.md").write_text("# Shop\n")
    (root / ".env").write_text(f"KEY={SECRET}\n")
    return root


PROVIDER = prism.Provider("openai", "http://stub", "k", "stub-model", "stub")


@pytest.fixture
def ai(monkeypatch):
    """Scripted AI. replies: list of strings or exceptions, used in order."""
    replies: list = []
    seen: list[list[dict]] = []

    def fake_call(provider, system, messages):
        if system == chat_mod.SYSTEM:
            seen.append([dict(m) for m in messages])
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        # reviewers of a /check or run_check
        out = {"findings": [], "limitations": []}
        if system.startswith("# Code Review Agent") and "if quantity > 10:" in messages[0]["content"]:
            out["findings"] = [{
                "id": "CODE-001",
                "key": "LOGIC_ERROR|project/shop/pricing.py|bulk_price|threshold",
                "severity": "HIGH", "category": "LOGIC_ERROR",
                "title": "Discount starts at 11, docstring says 10",
                "file": "project/shop/pricing.py", "line": 6, "symbol": "bulk_price",
                "description": "quantity > 10 excludes 10.", "evidence": "if quantity > 10:",
                "evidenceType": "source-analysis", "recommendation": "Use >= 10.",
                "relatedFiles": []}]
        if system.startswith("# Testing Agent"):
            out["behaviourMap"] = "| Behaviour | Test(s) | Covered? |"
        return json.dumps(out)

    monkeypatch.setattr(prism, "call_model", fake_call)
    monkeypatch.setattr(prism, "pick_provider", lambda: PROVIDER)
    monkeypatch.setenv("PRISM_PLAIN", "1")
    before = set(prism.RUNS.glob("run-*")) | set(prism.RUNS.glob("workspaces/ws-*"))
    yield replies, seen
    for p in (set(prism.RUNS.glob("run-*")) | set(prism.RUNS.glob("workspaces/ws-*"))) - before:
        ingest._remove(p)


def _tool(name, **args):
    return json.dumps({"tool": name, "args": args})


def _answer(text):
    return json.dumps({"answer": text})


def _session(project):
    return ChatSession(project, PROVIDER, fancy=False)


def _all_text(seen) -> str:
    return "\n".join(m["content"] for conv in seen for m in conv)


# ---------------------------------------------------------------------------
# Greeting and the conversation loop
# ---------------------------------------------------------------------------

def test_welcome_says_hi_and_describes_the_project(project, ai, capsys):
    _session(project).welcome()
    out = capsys.readouterr().out
    assert "Hi! I'm your pre-review code checker" in out
    assert "shop project" in out and "4 files" in out   # .env is not counted
    assert "stub - stub-model" in out.replace("·", "-") or "stub" in out


def test_question_uses_tools_then_answers(project, ai, capsys):
    replies, seen = ai
    replies += [_tool("project_map"), _tool("read_file", path="shop/pricing.py"),
                _answer("bulk_price starts the discount at 11 (shop/pricing.py:6).")]
    answer = _session(project).ask("where is the discount logic?")
    assert "shop/pricing.py:6" in answer
    last = seen[-1]
    assert "TOOL RESULT (project_map)" in last[2]["content"]
    assert "def bulk_price(price, quantity)" in last[2]["content"]
    assert "6|     if quantity > 10:" in last[4]["content"]      # numbered lines were read


def test_follow_up_questions_keep_the_conversation(project, ai):
    replies, seen = ai
    s = _session(project)
    replies += [_answer("It is in shop/pricing.py."), _answer("Use >= 10 on line 6.")]
    s.ask("where is the discount?")
    s.ask("how do I fix it?")
    assert [m["content"] for m in seen[-1] if m["role"] == "user"] == [
        "where is the discount?", "how do I fix it?"]


def test_plain_text_reply_is_shown_as_the_answer(project, ai):
    replies, _ = ai
    replies.append("Sure — the project has one module, shop/pricing.py.")
    assert _session(project).ask("hi") == "Sure — the project has one module, shop/pricing.py."


def test_endless_tool_use_is_stopped(project, ai):
    replies, _ = ai
    replies += [_tool("search", text="x")] * chat_mod.MAX_STEPS
    assert "many steps" in _session(project).ask("loop forever")


def test_unknown_tool_and_bad_arguments_are_explained_to_the_ai(project, ai):
    replies, seen = ai
    replies += [_tool("delete_everything"), _tool("read_file", nope=1), _answer("ok")]
    _session(project).ask("x")
    text = _all_text(seen)
    assert "Unknown tool 'delete_everything'" in text
    assert "Bad arguments for read_file" in text


# ---------------------------------------------------------------------------
# Safety: read-only, inside the folder, secrets hidden
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["../outside.txt", "..\\..\\x.py", ".env", "/etc/passwd",
                                  "C:/Windows/win.ini", ".prism/summaries.json"])
def test_read_file_refuses_paths_outside_or_hidden(project, ai, path, tmp_path):
    (tmp_path / "outside.txt").write_text("outside")
    (project / ".prism").mkdir(exist_ok=True)
    (project / ".prism" / "summaries.json").write_text("{}")
    result = _session(project).run_tool("read_file", {"path": path})
    assert result.startswith("Not possible")


def test_search_and_map_never_show_secrets(project, ai):
    s = _session(project)
    assert SECRET not in s.run_tool("search", {"text": "KEY="})
    assert SECRET not in s.run_tool("search", {"text": SECRET[:10]})
    assert ".env" not in s.run_tool("project_map", {})


def test_read_file_is_limited_and_paged(project, ai):
    (project / "shop" / "big.py").write_text("\n".join(f"x{i} = {i}" for i in range(1000)))
    s = _session(project)
    first = s.run_tool("read_file", {"path": "shop/big.py"})
    assert "lines 1-400" in first and "file has 1000 lines" in first
    page = s.run_tool("read_file", {"path": "shop/big.py", "start": 990})
    assert "lines 990-1000" in page


def test_report_paths_with_project_prefix_work(project, ai):
    assert "bulk_price" in _session(project).run_tool(
        "read_file", {"path": "project/shop/pricing.py"})


# ---------------------------------------------------------------------------
# run_check / findings / slash commands
# ---------------------------------------------------------------------------

def test_analyse_request_runs_the_real_check_and_writes_the_report(project, ai, capsys):
    replies, seen = ai
    replies += [_tool("run_check"),
                _answer("Riskiest: shop/pricing.py:6. 1. Use >= 10. See PRISM-REPORT.md.")]
    answer = _session(project).ask("analyse the project and give me a report")
    out = capsys.readouterr().out
    assert "RESULT: ATTENTION REQUIRED" in out                 # the official pipeline ran
    assert (project / "PRISM-REPORT.md").is_file()             # D3: report in the project
    assert "Verdict: ATTENTION_REQUIRED" in seen[-1][-1]["content"]
    assert "Use >= 10." in seen[-1][-1]["content"]             # recommendations given to AI
    assert "PRISM-REPORT.md" in answer


def test_slash_commands(project, ai, capsys):
    s = _session(project)
    assert s.handle("/help") and "/check" in capsys.readouterr().out
    assert s.handle("/findings") and "No check yet" in capsys.readouterr().out
    assert s.handle("/report") and "No report yet" in capsys.readouterr().out
    assert s.handle("/check")
    out = capsys.readouterr().out
    assert "RESULT: ATTENTION REQUIRED" in out and "What to do first" in out
    assert s.handle("/findings") and "Discount starts at 11" in capsys.readouterr().out
    assert s.handle("/report") and "PRISM-REPORT.md" in capsys.readouterr().out
    assert s.handle("/nonsense") and "Unknown command" in capsys.readouterr().out
    assert s.handle("/clear") and s.history == []
    assert s.handle("/exit") is False


def test_ai_errors_keep_the_session_alive(project, ai, capsys):
    replies, _ = ai
    s = _session(project)
    replies += [prism.ModelError("could not reach api.example.com"), _answer("back again")]
    assert s.handle("first question") is True
    assert "could not reach" in capsys.readouterr().out
    assert s.history == []                                   # the failed question was dropped
    assert s.handle("second question") and "back again" in capsys.readouterr().out


def test_run_loop_ends_on_exit_eof_and_ctrl_c(project, ai, capsys):
    replies, _ = ai
    replies.append(_answer("hello!"))
    lines = iter(["hi", "/exit", "never read"])
    assert _session(project).run(read=lambda _p: next(lines)) == 0
    out = capsys.readouterr().out
    assert "hello!" in out and "Bye!" in out and "1 AI call" in out

    def eof(_p):
        raise EOFError
    assert _session(project).run(read=eof) == 0

    def ctrl_c(_p):
        raise KeyboardInterrupt
    assert _session(project).run(read=ctrl_c) == 0


def test_long_conversations_are_trimmed_from_the_oldest(project, ai, monkeypatch):
    replies, seen = ai
    monkeypatch.setattr(chat_mod, "MAX_HISTORY_TOKENS", 300)
    s = _session(project)
    for i in range(6):
        replies.append(_answer(f"answer {i} " + "word " * 60))
        s.ask(f"question {i}")
    assert seen[-1][0]["role"] == "user"                      # still starts with the user
    assert "question 0" not in _all_text([seen[-1]])


# ---------------------------------------------------------------------------
# prism.py chat / doctor / prismai
# ---------------------------------------------------------------------------

def test_chat_command_with_missing_folder(tmp_path, capsys):
    assert prism.main(["chat", str(tmp_path / "missing")]) == 3
    assert "is not a folder" in capsys.readouterr().out


def test_chat_without_ai_offers_setup_and_stops_cleanly(project, monkeypatch, capsys):
    monkeypatch.setattr(prism, "pick_provider", lambda: None)
    monkeypatch.setattr(prism, "cmd_setup", lambda _a: 3)
    assert prism.main(["chat", str(project)]) == 3
    out = capsys.readouterr().out
    assert "I need an AI provider" in out and "DEEPSEEK_API_KEY" in out


def test_prismai_defaults_to_chat_and_passes_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(prism, "main", lambda argv: calls.append(argv) or 0)
    for argv in ([], ["C:/proj"], ["doctor"], ["check", "--scan", "x"], ["--help"]):
        monkeypatch.setattr(sys, "argv", ["prismai"] + argv)
        prism.prismai_main()
    assert calls == [["chat"], ["chat", "C:/proj"], ["doctor"], ["check", "--scan", "x"],
                     ["--help"]]


def test_doctor_reports_problems_with_fixes(monkeypatch, capsys):
    monkeypatch.setattr(prism, "pick_provider", lambda: None)
    assert prism.main(["doctor", "--offline"]) == 1
    out = capsys.readouterr().out
    assert "[OK  ] Python" in out and "[FAIL] AI provider" in out and "prism.py setup" in out


def test_doctor_all_good_with_a_working_ai(monkeypatch, capsys):
    monkeypatch.setattr(prism, "pick_provider", lambda: PROVIDER)
    monkeypatch.setattr(prism, "call_model", lambda *a: "OK")
    assert prism.main(["doctor"]) == 0
    assert "[OK  ] AI connection" in capsys.readouterr().out


def test_windows_launcher_runs_from_another_folder(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows launcher")
    proc = subprocess.run(["cmd", "/c", str(_REPO / "prismai.cmd"), "doctor", "--offline"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PRISM_PLAIN": "1"})
    assert "PRism-AI doctor" in proc.stdout


# ---------------------------------------------------------------------------
# results table
# ---------------------------------------------------------------------------

def _f(sev, cat, title, blocking=True, file="project/a.py", line=1, rec="fix it"):
    return {"severity": sev, "category": cat, "title": title, "blocking": blocking,
            "file": file, "line": line, "recommendation": rec}


def test_results_are_ordered_security_first_then_severity():
    fs = [_f("MEDIUM", "MISSING_TEST", "m"), _f("HIGH", "LOGIC_ERROR", "h"),
          _f("HIGH", "SECURITY", "s"), _f("LOW", "WEAK_TEST", "l", blocking=False)]
    assert [f["title"] for f in results.ordered(fs)] == ["s", "h", "m", "l"]
    assert results.counts(fs) == "2 high · 1 medium · 1 suggestion"


def test_plain_results_table_and_recommendations(capsys):
    report = {"readiness": {"state": "ATTENTION_REQUIRED"},
              "findings": [_f("HIGH", "SECURITY", "SQL injection", file="project/db.py", line=7,
                              rec="Use a parameterised query.")] +
                          [_f("MEDIUM", "MISSING_TEST", f"gap {i}", rec=f"add test {i}")
                           for i in range(12)]}
    results.print_result(report, "C:/p/PRISM-REPORT.md", fancy=False, scope="project")
    out = capsys.readouterr().out
    assert "Severity" in out and "Security" in out and "db.py:7" in out
    assert "project/db.py" not in out                         # workspace prefix hidden
    assert "1. db.py:7 — Use a parameterised query." in out
    assert "10. " in out and "11. " not in out                 # top 10 recommendations only
    assert "all 13 finding(s): C:/p/PRISM-REPORT.md" in out


def test_fancy_results_table_renders(capsys):
    report = {"readiness": {"state": "ATTENTION_REQUIRED"},
              "findings": [_f("HIGH", "SECURITY", "SQL injection")]}
    results.print_result(report, "r.md", fancy=True, scope="project")
    out = capsys.readouterr().out
    assert "SQL injection" in out and "What to do first" in out


def test_answer_with_raw_line_breaks_inside_the_json_is_understood(project, ai):
    """DeepSeek sometimes writes real newlines inside the JSON string (invalid strict JSON)."""
    replies, _ = ai
    replies.append('{"answer": "## Report\n\n| # | Issue |\n|---|---|\n| 1 | SQL injection |"}')
    answer = _session(project).ask("report please")
    assert answer.startswith("## Report") and '{"answer"' not in answer


# A real DeepSeek reply: {"answer": ...} with UNESCAPED """ inside a code block, which makes
# the JSON invalid. It must still be shown as a clean answer, never as raw JSON.
BROKEN_REAL_REPLY = r'''{"answer": "## The bug\n\nshopmart/orders/shipping.py:5-7:\n\n```python\n5| def shipping_label(name: str, address: Optional[str] = None) -> str:\n6|     """Printable label. Address is optional for in-store pickup."""\n7|     return f\"{name}\n{address.upper()}\"\n```\n\nWhen you call shipping_label(\"Bob\"), address is None.\n\n## The fix\n\n```python\n    return f\"{name}\n{address.upper() if address else ''}\"\n```"}'''


def test_real_broken_json_answer_is_shown_cleanly(project, ai):
    replies, _ = ai
    replies.append(BROKEN_REAL_REPLY)
    answer = _session(project).ask("explain the shipping bug")
    assert answer.startswith("## The bug")
    assert '{"answer"' not in answer
    assert '"""Printable label. Address is optional for in-store pickup."""' in answer
    assert 'return f"{name}\n{address.upper()}"' in answer      # the code's own \n is kept
    assert 'shipping_label("Bob")' in answer                      # \" became "
    assert "\n## The fix\n" in answer                              # \n became a real line break


def test_plain_markdown_answer_with_quotes_and_json_code_is_kept_as_is(project, ai):
    replies, _ = ai
    text = ('## Fix\n```python\ndef f():\n    """Doc."""\n    return {"findings": []}\n```\n'
            'Use `"quotes"` freely.')
    replies.append(text)
    assert _session(project).ask("q") == text


def test_tool_call_after_a_short_sentence_is_still_a_tool_call(project, ai):
    replies, seen = ai
    replies += ['Let me look at that file. {"tool": "read_file", "args": {"path": "shop/pricing.py"}}',
                "It starts the discount at 11."]
    assert _session(project).ask("where?") == "It starts the discount at 11."
    assert "TOOL RESULT (read_file)" in seen[-1][-1]["content"]


def test_system_prompt_asks_for_plain_markdown_answers():
    assert "plain Markdown" in chat_mod.SYSTEM and "NOT JSON" in chat_mod.SYSTEM
