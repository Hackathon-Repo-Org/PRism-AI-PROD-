"""
orchestrator.context — snapshot + shared context producer.

CLI:
    python -m orchestrator.context --base <ref> --candidate <ref> [--intent "..."]
    → prints the runId on stdout.

Creates runs/<runId>/ with:
  snapshot/  — git archive of candidate's sample-project/
  diff.patch — git diff base..candidate -- sample-project
  context.json (validated, written atomically)
"""

from __future__ import annotations

import argparse
import ast
import datetime
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Any

_REPO = pathlib.Path(__file__).parent.parent
_CONFIG_DIR = _REPO / "config"
_RUNS_DIR = _REPO / "runs"

# Files / patterns to exclude
_SECRET_PATTERNS = re.compile(
    r"(^|/)(\.env(\.[^/]+)?|.*\.pem|.*key.*\.pem|.*\.key|id_rsa|id_ecdsa)$",
    re.IGNORECASE,
)
_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".pdf", ".docx", ".xlsx", ".zip", ".tar", ".gz",
    ".whl", ".pyc", ".so", ".dll", ".exe", ".bin",
    ".db", ".sqlite", ".sqlite3",
}
_MAX_FILE_BYTES = 200 * 1024  # 200 KB


def _is_safe_excluded(name: str, size: int) -> tuple[bool, str | None]:
    """
    Return (excluded, reason) for a file entry from git archive.
    reason is one of the exclusion reason strings or None.
    """
    p = pathlib.PurePosixPath(name)
    if p.suffix in _BINARY_EXTENSIONS:
        return True, "binary"
    if size > _MAX_FILE_BYTES:
        return True, "too-large"
    if _SECRET_PATTERNS.search(name):
        return True, "secret"
    return False, None


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: pathlib.Path | None = None) -> str:
    """Run a git command, return stdout. Raises SystemExit on failure."""
    cmd = ["git"] + list(args)
    result = subprocess.run(
        cmd,
        cwd=str(cwd or _REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"ERROR: git {' '.join(args)} failed:\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def _resolve_sha(ref: str, cwd: pathlib.Path | None = None) -> str:
    """Resolve a ref to a full 40-char SHA. Exits clearly on failure."""
    if not ref or ref.startswith("-"):
        print(f"ERROR: invalid git ref {ref!r}", file=sys.stderr)
        sys.exit(1)
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", ref],
        cwd=str(cwd or _REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(
            f"ERROR: ref {ref!r} does not exist in this repository.\n"
            f"  git rev-parse output: {result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)
    return result.stdout.strip()


def _commit_message(sha: str, cwd: pathlib.Path | None = None) -> str:
    return _git("log", "-1", "--format=%s", sha, cwd=cwd).strip()


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def _export_snapshot(
    sha: str,
    scope_path: str,
    dest_dir: pathlib.Path,
    cwd: pathlib.Path,
) -> list[dict]:
    """
    git archive --format=zip <sha> <scope_path> → extract into dest_dir.
    Returns a list of exclusion dicts for files that were skipped.
    """
    result = subprocess.run(
        ["git", "archive", "--format=zip", sha, scope_path],
        cwd=str(cwd),
        capture_output=True,
    )
    if result.returncode != 0:
        print(
            f"ERROR: git archive failed for {sha!r}:\n"
            f"{result.stderr.decode('utf-8', errors='replace').strip()}",
            file=sys.stderr,
        )
        sys.exit(1)

    exclusions: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(result.stdout)) as zf:
        for name in zf.namelist():
            info = zf.getinfo(name)
            excluded, reason = _is_safe_excluded(name, info.file_size)
            if excluded:
                exclusions.append({"path": name, "reason": reason})
                continue
            # extract
            target = dest_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not name.endswith("/"):
                target.write_bytes(zf.read(name))

    return exclusions


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

@dataclass
class _Hunk:
    oldStart: int
    oldLines: int
    newStart: int
    newLines: int


@dataclass
class _ChangedFile:
    path: str
    status: str  # A M D R
    additions: int = 0
    deletions: int = 0
    hunks: list[_Hunk] = field(default_factory=list)


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NAME_STATUS = re.compile(r"^([AMDRC]\d*)\t(.+?)(?:\t(.+))?$")


def _header_path(line: str) -> str | None:
    """Path from 'diff --git a/P b/P' (both sides equal unless it is a rename)."""
    rest = line[len("diff --git a/"):]
    half = (len(rest) - 3) // 2
    path = rest[:half]
    return path if rest[half:] == f" b/{path}" else None


def _parse_diff(patch: str) -> list[_ChangedFile]:
    """Parse a unified diff into ChangedFile records."""
    files: dict[str, _ChangedFile] = {}
    current: _ChangedFile | None = None
    # pending status for the next +++ line (set by header markers)
    _pending_status: str = "M"
    header_path: str | None = None

    for line in patch.splitlines():
        # diff --git a/<path> b/<path>
        if line.startswith("diff --git "):
            current = None
            _pending_status = "M"
            header_path = _header_path(line)
            continue

        # header markers — set pending status BEFORE the +++ line.
        # Empty files have no ---/+++ lines, so record them from the header.
        if line.startswith("new file") or line.startswith("deleted file"):
            _pending_status = "A" if line.startswith("new file") else "D"
            if header_path:
                current = files.setdefault(header_path,
                                           _ChangedFile(path=header_path, status=_pending_status))
            continue
        if line.startswith("rename to "):
            path = line[len("rename to "):]
            if path not in files:
                files[path] = _ChangedFile(path=path, status="R")
            current = files[path]
            _pending_status = "R"
            continue

        # --- a/path or +++ b/path
        if line.startswith("--- a/"):
            # for deleted files we record the path here
            if _pending_status == "D":
                path = line[6:]
                if path not in files:
                    files[path] = _ChangedFile(path=path, status="D")
                current = files[path]
            continue
        if line.startswith("--- /dev/null"):
            continue
        if line.startswith("+++ b/"):
            path = line[6:]
            if path not in files:
                files[path] = _ChangedFile(path=path, status=_pending_status)
            else:
                files[path].status = _pending_status
            current = files[path]
            _pending_status = "M"  # reset
            continue
        if line.startswith("+++ /dev/null"):
            # deleted file: current already set via --- a/; keep it for hunk counting
            _pending_status = "M"
            continue

        if current is None:
            continue

        # hunk header
        m = _HUNK_HEADER.match(line)
        if m:
            old_start = int(m.group(1))
            old_lines = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_lines = int(m.group(4)) if m.group(4) is not None else 1
            current.hunks.append(_Hunk(old_start, old_lines, new_start, new_lines))
            continue

        # count + / - lines
        if line.startswith("+") and not line.startswith("+++"):
            current.additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            current.deletions += 1

    return list(files.values())


def _get_diff(base_sha: str, candidate_sha: str, scope_path: str, cwd: pathlib.Path) -> str:
    # quotepath=false: keep non-ASCII file names readable instead of octal-escaped
    return _git("-c", "core.quotepath=false", "diff", base_sha, candidate_sha, "--",
                scope_path, cwd=cwd)


# ---------------------------------------------------------------------------
# Relevance analysis
# ---------------------------------------------------------------------------

def _module_name(rel_path: str) -> str:
    """Convert sample-project/app/service.py → service (just the stem)."""
    return pathlib.PurePosixPath(rel_path).stem


def _scan_imports(text: str) -> set[str]:
    """Return module stems imported by the given Python source text."""
    stems: set[str] = set()
    for m in re.finditer(
        r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.,\s]+))",
        text,
        re.MULTILINE,
    ):
        raw = m.group(1) or m.group(2)
        for part in raw.split(","):
            stem = part.strip().split(".")[0]
            if stem:
                stems.add(stem)
    return stems


def _build_relevance(
    snapshot_dir: pathlib.Path,
    scope_path: str,
    changed_paths: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """
    Returns (relevantSource, relevantTests, relevantDocs) — all relative to snapshot root.
    """
    scope_root = snapshot_dir / scope_path

    all_py = sorted(scope_root.rglob("*.py")) if scope_root.exists() else []
    modules = {}
    for path in all_py:
        parts = list(path.relative_to(scope_root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path.relative_to(snapshot_dir).as_posix()

    changed = {p for p in changed_paths if p.endswith(".py")}
    changed_stems = {_module_name(p) for p in changed}
    dependencies = {}
    tests = set()
    for py_file in all_py:
        rel = py_file.relative_to(snapshot_dir).as_posix()
        parts = list(py_file.relative_to(scope_root).parent.parts)
        dependencies[rel] = set()
        if (py_file.name.startswith("test_") or py_file.name.endswith("_test.py")
                or "tests" in parts or "test" in parts):
            tests.add(rel)
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = parts[:len(parts) - node.level + 1] if node.level else []
                base = ".".join(prefix + ([node.module] if node.module else []))
                names.add(base)
                names.update(".".join(filter(None, (base, alias.name)))
                             for alias in node.names if alias.name != "*")
        for name in names:
            # Include package initializers and the most specific local module.
            pieces = name.split(".")
            for end in range(1, len(pieces) + 1):
                module = ".".join(pieces[:end])
                if module in modules:
                    dependencies[rel].add(modules[module])

    related = set(changed)
    for path, imports in dependencies.items():
        if imports & changed:
            related.add(path)
        if path in changed:
            related.update(imports)
    # Tests usually reach changed code indirectly (test -> app.main -> app.service),
    # so follow imports transitively when picking tests.
    reaches = set(changed)
    grew = True
    while grew:
        grew = False
        for path, imports in dependencies.items():
            if path not in reaches and imports & reaches:
                reaches.add(path)
                grew = True
    relevant_tests = (related | reaches) & tests
    relevant_tests.update(p for p in tests
                          if any(stem in pathlib.PurePosixPath(p).name for stem in changed_stems))
    relevant_source = (related - tests) & set(dependencies)

    # docs: README.md + everything under docs/
    relevant_docs: list[str] = []
    readme = scope_root / "README.md"
    if readme.exists():
        relevant_docs.append((scope_path + "/README.md"))
    docs_dir = scope_root / "docs"
    if docs_dir.is_dir():
        for f in sorted(docs_dir.rglob("*")):
            if f.is_file():
                relevant_docs.append(f.relative_to(snapshot_dir).as_posix())

    return (
        sorted(relevant_source),
        sorted(relevant_tests),
        relevant_docs,
    )


# ---------------------------------------------------------------------------
# Run ID
# ---------------------------------------------------------------------------

def _make_run_id(candidate_sha: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return f"run-{now.strftime('%Y%m%d-%H%M%S')}-{candidate_sha[:7]}"


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------

def _atomic_write_json(path: pathlib.Path, data: dict) -> None:
    tmp = pathlib.Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------------------
# Main build function (importable)
# ---------------------------------------------------------------------------

def _repository_path(path: pathlib.Path, repo: pathlib.Path) -> str:
    """Repository-relative when possible; absolute for external custom runs."""
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return path.as_posix()


def build_context(
    base_ref: str,
    candidate_ref: str,
    intent: str | None = None,
    repo_dir: pathlib.Path | None = None,
    runs_dir: pathlib.Path | None = None,
    project: dict | None = None,
) -> tuple[str, pathlib.Path]:
    """
    Build a run context.

    Returns (runId, run_dir).
    Writes runs/<runId>/context.json (validated), diff.patch, and snapshot/.

    All git operations run in repo_dir (defaults to the PRism-AI repo root).
    runs_dir defaults to repo_dir/runs; relative overrides resolve from repo_dir.
    External custom directories use absolute snapshotDir/diffPath values.
    """
    cwd = (repo_dir or _REPO).resolve()
    runs = runs_dir if runs_dir is not None else cwd / "runs"
    if not runs.is_absolute():
        runs = cwd / runs
    runs = runs.resolve()

    # 1. Resolve refs
    base_sha      = _resolve_sha(base_ref, cwd=cwd)
    candidate_sha = _resolve_sha(candidate_ref, cwd=cwd)

    # load project config
    # project overrides config/project.json (used for code outside this repo)
    project_cfg = project or json.loads((_CONFIG_DIR / "project.json").read_text(encoding="utf-8"))
    scope_path = project_cfg["scopePath"]

    # 2. Create run directory
    run_id  = _make_run_id(candidate_sha)
    run_dir = runs / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    snapshot_dir = run_dir / "snapshot"
    snapshot_dir.mkdir(exist_ok=True)
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(exist_ok=True)

    # 3. Export snapshot + collect exclusions
    exclusions = _export_snapshot(candidate_sha, scope_path, snapshot_dir, cwd=cwd)

    # 4. Generate diff patch
    diff_text = _get_diff(base_sha, candidate_sha, scope_path, cwd=cwd)
    diff_path = run_dir / "diff.patch"
    diff_path.write_text(diff_text, encoding="utf-8")

    # Parse diff into changedFiles
    changed_file_objs = _parse_diff(diff_text)
    changed_files_json = [
        {
            "path": cf.path,
            "status": cf.status,
            "additions": cf.additions,
            "deletions": cf.deletions,
            "hunks": [
                {
                    "oldStart": h.oldStart,
                    "oldLines": h.oldLines,
                    "newStart": h.newStart,
                    "newLines": h.newLines,
                }
                for h in cf.hunks
            ],
        }
        for cf in changed_file_objs
    ]
    changed_paths = [cf.path for cf in changed_file_objs]

    # 5. Relevance analysis
    relevant_source, relevant_tests, relevant_docs = _build_relevance(
        snapshot_dir, scope_path, changed_paths
    )

    # 6. Intent
    resolved_intent = intent if intent else _commit_message(candidate_sha, cwd=cwd)

    # 7. Build context dict
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    context = {
        "schemaVersion": "1.0",
        "runId": run_id,
        "snapshotId": candidate_sha,
        "baseCommit": base_sha,
        "baseRef": base_ref,
        "candidateRef": candidate_ref,
        "intent": resolved_intent,
        "scopePath": scope_path,
        "snapshotDir": _repository_path(snapshot_dir, cwd),
        "diffPath": _repository_path(diff_path, cwd),
        "project": {
            "language": project_cfg["language"],
            "framework": project_cfg["framework"],
            "testCommand": project_cfg["testCommand"],
            "testWorkingDirectory": project_cfg["testWorkingDirectory"],
        },
        "changedFiles": changed_files_json,
        "relevantSource": relevant_source,
        "relevantTests": relevant_tests,
        "relevantDocs": relevant_docs,
        "exclusions": exclusions,
        "scopeLimitations": [
            f"Only files under {scope_path}/ were analysed."
        ],
        "createdAt": now_utc,
    }

    # Validate before writing
    from orchestrator.validate import validate_result, ValidationResult
    vr = validate_result(context)
    if not vr.valid:
        print("ERROR: generated context.json failed schema validation:", file=sys.stderr)
        for e in vr.errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    # Atomic write
    _atomic_write_json(run_dir / "context.json", context)

    return run_id, run_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.context",
        description="Create a run snapshot and context.json.",
    )
    parser.add_argument("--base",      required=True, help="Base git ref or SHA.")
    parser.add_argument("--candidate", required=True, help="Candidate git ref or SHA.")
    parser.add_argument("--intent",    default=None,  help="Optional intent string.")
    args = parser.parse_args(argv)

    run_id, _ = build_context(
        base_ref=args.base,
        candidate_ref=args.candidate,
        intent=args.intent,
    )
    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
