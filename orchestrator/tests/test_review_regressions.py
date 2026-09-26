"""Regression coverage for the reviewed T1–T7 boundaries."""
import copy
import json
from pathlib import Path

import pytest

from orchestrator.agent_io import begin, finish, FinishStatus
from orchestrator.aggregate import aggregate
from orchestrator.context import _build_relevance, _parse_diff, build_context
from orchestrator.validate import validate_result, _main
from orchestrator.tests.test_t7_run_fixtures import toy_repo

EXAMPLES = Path(__file__).resolve().parents[2] / 'schemas' / 'examples'


def example(name):
    return json.loads((EXAMPLES / name).read_text(encoding='utf-8'))


@pytest.fixture
def run(tmp_path):
    ctx = example('context.example.json')
    rdir = tmp_path / ctx['runId']
    rdir.mkdir()
    (rdir / 'context.json').write_text(json.dumps(ctx), encoding='utf-8')
    for agent in ('code-review', 'testing', 'documentation'):
        result = example(f'{agent}-result.bad.json')
        result.update(runId=ctx['runId'], snapshotId=ctx['snapshotId'])
        (rdir / f'{agent}-result.json').write_text(json.dumps(result), encoding='utf-8')
    return ctx, rdir


@pytest.mark.parametrize('raw', ['[]', '{}', '{"findingz": [{}]}', '{',
    '{"findings": [null], "limitations": []}',
    '{"findings": null, "limitations": []}',
    '{"findings": [], "limitations": null}', '{"findings": []}'])
@pytest.mark.parametrize('repair_ok', [False, True])
def test_raw_repair_lifecycle(run, raw, repair_ok):
    ctx, rdir = run
    target = rdir / 'code-review-result.json'
    target.unlink()
    begin(ctx['runId'], 'code-review', runs_dir=rdir.parent)
    fp = rdir / 'raw.json'
    fp.write_text(raw, encoding='utf-8')
    first = finish(ctx['runId'], 'code-review', fp, runs_dir=rdir.parent)
    assert first.status == FinishStatus.NEEDS_REPAIR
    assert not target.exists()
    if repair_ok:
        fp.write_text('{"findings": [], "limitations": []}', encoding='utf-8')
    second = finish(ctx['runId'], 'code-review', fp, runs_dir=rdir.parent)
    assert second.status == (FinishStatus.OK if repair_ok else FinishStatus.ERROR)
    data = json.loads(target.read_text(encoding='utf-8'))
    assert validate_result(data, ctx).valid
    assert data['status'] == ('completed' if repair_ok else 'error')
    if not repair_ok:
        assert data['statusReason'].startswith('SCHEMA_INVALID:')


@pytest.mark.parametrize('replacement', ['documentation', 'context', 'report', 'null', '[]', 'broken', 'finding'])
def test_aggregate_rejects_substitution_and_malformed(run, replacement):
    ctx, rdir = run
    if replacement == 'documentation':
        data = example('documentation-result.clean.json')
    elif replacement == 'context':
        data = ctx
    elif replacement == 'report':
        data = aggregate(ctx['runId'], runs_dir=rdir.parent)
    elif replacement == 'finding':
        data = example('code-review-result.clean.json')
        data['findings'] = [None]
    else:
        data = None
    if isinstance(data, dict):
        data.update(runId=ctx['runId'], snapshotId=ctx['snapshotId'])
    text = json.dumps(data) if data is not None else {'null': 'null', '[]': '[]', 'broken': '{'}[replacement]
    (rdir / 'code-review-result.json').write_text(text, encoding='utf-8')
    report = aggregate(ctx['runId'], runs_dir=rdir.parent)
    assert report['readiness']['state'] == 'VERIFICATION_FAILED'
    assert 'SCHEMA_INVALID' in {r['code'] for r in report['readiness']['reasons']}
    assert report['findings']  # Other agents' valid findings survive.
    assert validate_result(report, ctx).valid


@pytest.mark.parametrize('data', [None, [], 1, 'text', False])
def test_nonobject_validation(data):
    assert not validate_result(data).valid


@pytest.mark.parametrize('value', [None, 1, 'oops', {}, [None], [[]]])
def test_malformed_findings(value):
    data = example('code-review-result.clean.json')
    data['findings'] = value
    assert not validate_result(data, example('context.example.json')).valid


@pytest.mark.parametrize('path', ['sample-project/../secret', r'sample-project\..\secret',
    r'sample-project/app\..\..\secret', r'C:\sample-project\app.py'])
@pytest.mark.parametrize('field', ['file', 'relatedFiles'])
def test_traversal(path, field):
    data = example('code-review-result.bad.json')
    data['findings'][0][field] = [path] if field == 'relatedFiles' else path
    assert not validate_result(data, example('context.example.json')).valid


def test_report_context_checks_and_cli(run):
    ctx, rdir = run
    report = aggregate(ctx['runId'], runs_dir=rdir.parent)
    report['runId'] = 'run-20260926-000000-abcdef0'
    report['findings'][0]['relatedFiles'] = [r'sample-project\..\secret']
    vr = validate_result(report, ctx)
    assert any('runId mismatch' in e for e in vr.errors)
    assert any('traversal' in e for e in vr.errors)
    fp = rdir / 'report.json'
    fp.write_text(json.dumps(report), encoding='utf-8')
    assert _main([str(fp), '--context', str(rdir / 'context.json')]) == 1


@pytest.mark.parametrize('location', ['default', 'relative', 'external'])
def test_context_paths_resolve(toy_repo, tmp_path, location):
    runs = {'default': None, 'relative': Path('custom/runs'), 'external': tmp_path / 'external'}[location]
    _, rdir = build_context('base', 'cand', repo_dir=toy_repo, runs_dir=runs)
    ctx = json.loads((rdir / 'context.json').read_text(encoding='utf-8'))
    assert (toy_repo / ctx['snapshotDir']).resolve() == (rdir / 'snapshot').resolve()
    assert (toy_repo / ctx['diffPath']).resolve() == (rdir / 'diff.patch').resolve()
    assert (toy_repo / ctx['diffPath']).is_file()
    if location == 'default':
        assert ctx['snapshotDir'].startswith('runs/')


@pytest.mark.parametrize('importer', ['from app import service', 'import app.service as svc',
    'from .service import work', 'from . import service', 'from app.service import work'])
def test_package_relevance_both_directions(tmp_path, importer):
    files = {'app/service.py': 'from . import helper\nfrom .nested.dep import value\n',
             'app/helper.py': '', 'app/nested/dep.py': 'value = 1\n',
             'app/consumer.py': importer, 'app/unrelated.py': '',
             'tests/test_behavior.py': 'from app import service'}
    for name, text in files.items():
        p = tmp_path / 'sample-project' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
    source, tests, _ = _build_relevance(tmp_path, 'sample-project', ['sample-project/app/service.py'])
    assert set(source) == {'sample-project/' + p for p in files if p.startswith('app/') and 'unrelated' not in p}
    assert tests == ['sample-project/tests/test_behavior.py']


def test_rename_with_edits():
    patch = '''diff --git a/sample-project/old.py b/sample-project/new.py
similarity index 80%
rename from sample-project/old.py
rename to sample-project/new.py
--- a/sample-project/old.py
+++ b/sample-project/new.py
@@ -1 +1 @@
-old
+new
'''
    change, = _parse_diff(patch)
    assert (change.status, change.path, change.additions, change.deletions) == ('R', 'sample-project/new.py', 1, 1)
    assert len(change.hunks) == 1

@pytest.mark.parametrize('field,value', [('file', None), ('file', []), ('relatedFiles', None), ('relatedFiles', [None]), ('relatedFiles', 'bad')])
def test_malformed_finding_paths(field, value):
    data = example('code-review-result.bad.json')
    data['findings'][0][field] = value
    assert not validate_result(data, example('context.example.json')).valid


def test_context_artifact_identity_scope():
    ctx = example('context.example.json')
    data = copy.deepcopy(ctx)
    data['snapshotId'] = 'f' * 40
    data['relevantSource'] = [r'sample-project\..\secret']
    result = validate_result(data, ctx)
    assert any('snapshotId mismatch' in e for e in result.errors)
    assert any('traversal' in e for e in result.errors)
    assert not validate_result(ctx, []).valid


def test_parent_relative_and_package_initializers(tmp_path):
    files = {'app/__init__.py': 'from . import service',
             'app/service.py': 'from .sub import helper',
             'app/sub/__init__.py': '',
             'app/sub/helper.py': 'from ..service import work',
             'app/sub/consumer.py': 'from .. import service'}
    for name, text in files.items():
        path = tmp_path / 'sample-project' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    source, _, _ = _build_relevance(tmp_path, 'sample-project', ['sample-project/app/service.py'])
    assert set(source) == {'sample-project/' + name for name in files}
