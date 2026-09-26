"""Input hardening: run ids cannot escape runs/, git refs cannot become options."""

import pytest

from orchestrator import safe_run_id
from orchestrator import agent_io, context, reverify


@pytest.mark.parametrize("good", ["run-20260926-071648-db642d6", "run_test", "abc"])
def test_safe_run_id_accepts_normal_ids(good):
    assert safe_run_id(good) == good


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "a\b", "-x", ".hidden", "C:x", None])
def test_safe_run_id_rejects_traversal(bad):
    with pytest.raises(ValueError):
        safe_run_id(bad)


def test_agent_io_rejects_traversal():
    with pytest.raises(ValueError):
        agent_io._run_dir("../../etc")


def test_reverify_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        reverify._load_report("../outside", tmp_path)


@pytest.mark.parametrize("ref", ["", "--output=x", "-h"])
def test_resolve_sha_rejects_option_like_refs(ref):
    with pytest.raises(SystemExit):
        context._resolve_sha(ref)


def test_runner_rejects_traversal(tmp_path):
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "runner", pathlib.Path(__file__).resolve().parents[2] / "agents/testing/runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    with pytest.raises(ValueError):
        runner.run("../x", runs_root=tmp_path)


def test_extract_json_ignores_text_and_objects_after_the_first():
    import json, prism
    reply = '{"findings": [], "limitations": []}\n\nNote: {see above}\n{"x": 1}'
    assert json.loads(prism._extract_json(reply)) == {"findings": [], "limitations": []}


def test_relevant_tests_follow_imports_transitively(tmp_path):
    # tests/test_api.py -> app.main -> app.service (changed)
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "service.py").write_text("X = 1\n")
    (root / "app" / "main.py").write_text("from .service import X\n")
    (root / "tests" / "test_api.py").write_text("from app.main import X\n")
    _, tests, _ = context._build_relevance(tmp_path, "proj", ["proj/app/service.py"])
    assert tests == ["proj/tests/test_api.py"]
