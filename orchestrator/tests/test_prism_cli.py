"""
prism.py — the one-command entry point.

The AI call is replaced by a stub, so these tests need no API key. They create
real run folders under runs/ (git-ignored) and remove them afterwards.
"""

from __future__ import annotations

import http.server
import json
import pathlib
import shutil
import subprocess
import sys
import threading

import pytest

_REPO = pathlib.Path(__file__).parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import prism  # noqa: E402

CODE_FINDING = {
    "id": "CODE-001",
    "key": "NULL_HANDLING|sample-project/app/service.py|create_ticket|priority",
    "severity": "HIGH", "category": "NULL_HANDLING",
    "title": "Missing priority is dereferenced",
    "file": "sample-project/app/service.py", "line": 45, "symbol": "create_ticket",
    "description": "POST /tickets without priority raises AttributeError -> 500.",
    "evidence": "priority = priority.upper()", "evidenceType": "source-analysis",
    "recommendation": "Check for None before calling upper().", "relatedFiles": [],
}


def _has_ref(ref: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=_REPO,
                          capture_output=True).returncode == 0


needs_tags = pytest.mark.skipif(not (_has_ref("demo-bad") and _has_ref("baseline-clean")),
                                reason="demo tags not fetched")


@pytest.fixture
def stub(monkeypatch):
    """Route every AI call to a per-agent script of replies."""
    replies: dict[str, list] = {}
    calls: list[str] = []

    headings = {"code-review": "# Code Review Agent", "testing": "# Testing Agent",
                "documentation": "# Documentation Agent"}

    def fake_call(provider, system, messages):
        agent = next(a for a, h in headings.items() if system.startswith(h))
        calls.append(agent)
        reply = replies[agent].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(prism, "pick_provider",
                        lambda: prism.Provider("openai", "http://stub", "k", "stub-model", "stub"))
    monkeypatch.setattr(prism, "call_model", fake_call)
    before = set(prism.RUNS.glob("run-*"))
    yield replies, calls
    for p in set(prism.RUNS.glob("run-*")) - before:
        shutil.rmtree(p, ignore_errors=True)


def _latest_report() -> dict:
    run = sorted(prism.RUNS.glob("run-*"), key=lambda p: p.stat().st_mtime)[-1]
    return json.loads((run / "report.json").read_text(encoding="utf-8"))


def test_extract_json_strips_fences_and_prose():
    assert json.loads(prism._extract_json('Sure:\n```json\n{"a": 1}\n```\nDone.')) == {"a": 1}


def test_check_without_key_explains_setup(monkeypatch, capsys):
    monkeypatch.setattr(prism, "pick_provider", lambda: None)
    assert prism.main(["check"]) == 3
    assert "prism.py setup" in capsys.readouterr().out


@needs_tags
def test_check_end_to_end_with_repair_and_parallel_agents(stub, capsys):
    replies, calls = stub
    replies["code-review"] = [
        "not json at all",  # first reply invalid -> one repair round
        json.dumps({"findings": [CODE_FINDING], "limitations": []}),
    ]
    replies["testing"] = [json.dumps({"findings": [], "limitations": ["stub"],
                                      "behaviourMap": "| b | t | c |"})]
    replies["documentation"] = ["```json\n" + json.dumps({"findings": [], "limitations": []})
                                + "\n```"]

    code = prism.main(["check", "demo-bad", "--base", "baseline-clean"])
    out = capsys.readouterr().out

    report = _latest_report()
    assert code == 1
    assert report["readiness"]["state"] == "ATTENTION_REQUIRED"
    assert [f["id"] for f in report["findings"]] == ["CODE-001"]
    assert all(a["status"] == "completed" for a in report["agents"])
    assert report["execution"]["exitCode"] == 0  # the real test runner ran
    assert calls.count("code-review") == 2
    assert "RESULT: ATTENTION REQUIRED" in out and "Full report:" in out


@needs_tags
def test_model_failure_becomes_verification_failed(stub, capsys):
    replies, _ = stub
    replies["code-review"] = [prism.ModelError("ANTHROPIC_API_KEY was rejected (invalid key)")]
    replies["testing"] = [json.dumps({"findings": [], "limitations": []})]
    replies["documentation"] = [json.dumps({"findings": [], "limitations": []})]

    code = prism.main(["check", "demo-bad", "--base", "baseline-clean"])
    report = _latest_report()
    assert code == 2
    assert report["readiness"]["state"] == "VERIFICATION_FAILED"
    reasons = {r["code"] for r in report["readiness"]["reasons"]}
    assert "AGENT_ERROR" in reasons
    assert "invalid key" in capsys.readouterr().out


@needs_tags
def test_invalid_twice_is_an_error_not_a_clean_result(stub):
    replies, _ = stub
    replies["code-review"] = ["{}", '{"findings": "nope"}']
    replies["testing"] = [json.dumps({"findings": [], "limitations": []})]
    replies["documentation"] = [json.dumps({"findings": [], "limitations": []})]

    assert prism.main(["check", "demo-bad", "--base", "baseline-clean"]) == 2
    report = _latest_report()
    row = next(a for a in report["agents"] if a["agent"] == "code-review")
    assert row["status"] == "error"


# ---------------------------------------------------------------------------
# Provider settings: any OpenAI- or Anthropic-compatible provider
# ---------------------------------------------------------------------------

_ALL_VARS = ("PRISM_API_STYLE", "PRISM_BASE_URL", "PRISM_API_KEY", "PRISM_MODEL",
             "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
             "GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for v in _ALL_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(prism, "ENV_FILE", tmp_path / ".prism.env")
    return tmp_path / ".prism.env"


def test_no_settings_means_no_provider(clean_env):
    assert prism.pick_provider() is None


def test_generic_settings_from_env_file(clean_env, monkeypatch):
    clean_env.write_text("# comment\nPRISM_BASE_URL=https://api.example.com/v1\n"
                         "PRISM_API_KEY='k-123'\nPRISM_MODEL=some-model\n", encoding="utf-8")
    p = prism.pick_provider()
    assert (p.style, p.base_url, p.api_key, p.model, p.label) == (
        "openai", "https://api.example.com/v1", "k-123", "some-model", "api.example.com")
    monkeypatch.setenv("PRISM_MODEL", "env-wins")  # real env overrides the file
    assert prism.pick_provider().model == "env-wins"


def test_generic_settings_need_url_and_model(clean_env, monkeypatch):
    monkeypatch.setenv("PRISM_API_KEY", "k")
    with pytest.raises(prism.SetupError, match="PRISM_BASE_URL"):
        prism.pick_provider()
    monkeypatch.setenv("PRISM_BASE_URL", "https://api.example.com/v1")
    with pytest.raises(prism.SetupError, match="PRISM_MODEL"):
        prism.pick_provider()
    monkeypatch.setenv("PRISM_API_STYLE", "grpc")
    with pytest.raises(prism.SetupError, match="PRISM_API_STYLE"):
        prism.pick_provider()


def test_shortcut_keys(clean_env, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    p = prism.pick_provider()
    assert (p.style, p.base_url, p.model) == ("openai", "https://api.deepseek.com", "deepseek-chat")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    p = prism.pick_provider()
    assert (p.style, p.base_url, p.model) == ("anthropic", None, "claude-opus-5")


def test_setup_problem_is_explained(clean_env, monkeypatch, capsys):
    monkeypatch.setenv("PRISM_API_KEY", "k")
    assert prism.main(["check"]) == 3
    out = capsys.readouterr().out
    assert "PRISM_BASE_URL" in out and "prism.py setup" in out


# ---------------------------------------------------------------------------
# Real HTTP against local mock providers (no internet, no key)
# ---------------------------------------------------------------------------

_SSE = [
    ("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "m", "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 0}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0,
                             "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "OK from mock"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta",
                       "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                       "usage": {"output_tokens": 3}}),
    ("message_stop", {"type": "message_stop"}),
]


@pytest.fixture
def mock_provider():
    seen: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            if self.path.endswith("/chat/completions"):
                out = json.dumps({"choices": [{"message": {
                    "content": "<think>{not json}</think>OK from mock"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            elif self.path.endswith("/v1/messages"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event, data in _SSE:
                    self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
            else:
                self.send_response(404)
                self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()


def test_openai_compatible_provider_over_http(mock_provider):
    base, seen = mock_provider
    p = prism.Provider("openai", base + "/v1", "k-1", "any-model", "mock")
    reply = prism.call_model(p, "sys", [{"role": "user", "content": "hi"}])
    assert prism._extract_json(reply + ' {"a": 1}') == '{"a": 1}'  # <think> is skipped
    req = seen[-1]
    assert req["path"] == "/v1/chat/completions"
    assert req["headers"]["authorization"] == "Bearer k-1"
    assert req["body"]["model"] == "any-model"
    assert req["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_anthropic_compatible_provider_over_http(mock_provider):
    base, seen = mock_provider
    p = prism.Provider("anthropic", base, "k-2", "any-model", "mock")
    assert prism.call_model(p, "sys", [{"role": "user", "content": "hi"}]) == "OK from mock"
    req = seen[-1]
    assert req["path"] == "/v1/messages"
    assert req["headers"]["x-api-key"] == "k-2"
    assert "fallbacks" not in req["body"]  # first-party-only option not sent to third parties


def test_bad_url_is_a_clear_error():
    p = prism.Provider("openai", "http://127.0.0.1:9/v1", "k", "m", "nowhere")
    with pytest.raises(prism.ModelError, match="could not reach"):
        prism.call_model(p, "s", [{"role": "user", "content": "x"}])


def test_setup_other_provider_saves_after_connection_test(clean_env, mock_provider,
                                                          monkeypatch, capsys):
    base, _ = mock_provider
    answers = iter([str(len(prism.PRESETS)), "1", base + "/v1", "mimo-test-model"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(prism.getpass, "getpass", lambda _prompt: "secret-key")
    assert prism.main(["setup"]) == 0
    saved = clean_env.read_text(encoding="utf-8")
    assert "PRISM_BASE_URL=" + base + "/v1" in saved and "PRISM_MODEL=mimo-test-model" in saved
    assert "secret-key" not in capsys.readouterr().out  # the key is never echoed
    p = prism.pick_provider()
    assert (p.style, p.model, p.api_key) == ("openai", "mimo-test-model", "secret-key")


def test_setup_does_not_save_when_connection_fails(clean_env, monkeypatch):
    answers = iter([str(len(prism.PRESETS)), "1", "http://127.0.0.1:9/v1", "m"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(prism.getpass, "getpass", lambda _prompt: "k")
    assert prism.main(["setup"]) == 3
    assert not clean_env.exists()


def test_findings_json_with_raw_newlines_in_strings_is_accepted():
    raw = '{"findings": [], "limitations": ["line one\nline two"]}'
    assert json.loads(prism._extract_json(raw), strict=False)["limitations"] == ["line one\nline two"]
