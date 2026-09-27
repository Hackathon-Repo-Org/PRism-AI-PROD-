"""
orchestrator.codemap — a free, exact map of a codebase, and fitting code into a budget.

M2  build_map()     : every file's classes, functions (with signatures) and docstrings,
                      read with Python's own parser. No AI, no cost, same result every time.
L5  pack_files()    : when a reviewer's input is too big for one AI call, choose which files
                      it sees in full (highest priority first) and which it sees only as a
                      summary line. The choice is deterministic and reported to the user.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field

CHARS_PER_TOKEN = 3.5  # conservative for source code; keeps estimates on the safe side

_ENTRY_NAMES = {"main", "app", "api", "server", "routes", "views", "service", "services",
                "cli", "handlers", "models", "__main__"}
_DOC_SUFFIXES = {".md", ".rst", ".txt"}


def estimate_tokens(text_or_chars: str | int) -> int:
    chars = text_or_chars if isinstance(text_or_chars, int) else len(text_or_chars)
    return int(chars / CHARS_PER_TOKEN) + 1


def is_test(rel: str) -> bool:
    p = pathlib.PurePosixPath(rel)
    return (p.name.startswith("test_") or p.name.endswith("_test.py")
            or "tests" in p.parts or "test" in p.parts)


def is_doc(rel: str) -> bool:
    return pathlib.PurePosixPath(rel).suffix.lower() in _DOC_SUFFIXES


# ---------------------------------------------------------------------------
# M2 — project map
# ---------------------------------------------------------------------------

@dataclass
class FileMap:
    path: str
    lines: int
    doc: str = ""
    symbols: list[str] = field(default_factory=list)   # "def f(a, b)", "class C", "C.m(x)"
    imports: list[str] = field(default_factory=list)
    parse_error: bool = False

    def one_line(self) -> str:
        head = f"{self.path} ({self.lines} lines)"
        if self.doc:
            head += f" — {self.doc}"
        if self.symbols:
            shown = ", ".join(self.symbols[:12])
            more = f", +{len(self.symbols) - 12} more" if len(self.symbols) > 12 else ""
            head += f"\n    {shown}{more}"
        return head


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [a.arg for a in node.args.posonlyargs + node.args.args if a.arg not in ("self", "cls")]
    if node.args.vararg:
        args.append("*" + node.args.vararg.arg)
    args += [a.arg for a in node.args.kwonlyargs]
    if node.args.kwarg:
        args.append("**" + node.args.kwarg.arg)
    return f"{node.name}({', '.join(args)})"


def map_file(root: pathlib.Path, rel: str) -> FileMap:
    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    fm = FileMap(path=rel, lines=text.count("\n") + (1 if text and not text.endswith("\n") else 0))
    if not rel.endswith(".py"):
        first = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), "")
        fm.doc = first[:100]
        return fm
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        fm.parse_error = True
        return fm
    doc = ast.get_docstring(tree) or ""
    fm.doc = doc.strip().splitlines()[0][:100] if doc.strip() else ""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fm.symbols.append("def " + _signature(node))
        elif isinstance(node, ast.ClassDef):
            fm.symbols.append("class " + node.name)
            fm.symbols += [f"{node.name}.{_signature(m)}" for m in node.body
                           if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        elif isinstance(node, ast.Import):
            fm.imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            fm.imports.append("." * node.level + node.module)
    return fm


def build_map(root: pathlib.Path, files: list[str]) -> dict[str, FileMap]:
    return {rel: map_file(root, rel) for rel in files if (root / rel).is_file()}


def render_map(maps: dict[str, FileMap], summaries: dict[str, dict] | None = None,
               budget_tokens: int | None = None) -> tuple[str, list[str]]:
    """Text table of contents; with summaries, each file also gets its one-line AI summary.
    Returns (text, paths that did not fit in budget_tokens)."""
    summaries = summaries or {}
    lines, left_out, used = [], [], 0
    for rel in sorted(maps):
        entry = maps[rel].one_line()
        s = summaries.get(rel)
        if s and s.get("summary"):
            entry += f"\n    summary: {s['summary']}"
            if s.get("risk"):
                entry += f"  [risk {s['risk']}: {s.get('reason', '')}]"
        cost = estimate_tokens(entry) + 1
        if budget_tokens is not None and used + cost > budget_tokens:
            left_out.append(rel)
            continue
        lines.append(entry)
        used += cost
    return "\n".join(lines), left_out


# ---------------------------------------------------------------------------
# L5 — deciding what each reviewer sees in full
# ---------------------------------------------------------------------------

def priority(rel: str, agent: str, changed: set[str], summaries: dict[str, dict]) -> float:
    """Higher = shown in full first. Changed files always come first."""
    name = pathlib.PurePosixPath(rel).stem.lower()
    score = 0.0
    if rel in changed:
        score += 10
    score += 3 * float((summaries.get(rel) or {}).get("risk") or 0)
    if name in _ENTRY_NAMES:
        score += 2
    if agent == "testing" and is_test(rel):
        score += 4
    if agent == "documentation" and is_doc(rel):
        score += 6
    return score


def pack_files(candidates: list[str], sizes: dict[str, int], budget_tokens: int,
               agent: str, changed: set[str], summaries: dict[str, dict]) -> tuple[list[str], list[str]]:
    """Split candidates into (shown in full, summary only) within budget_tokens.
    Order: priority, then smaller files first so more files fit."""
    order = sorted(dict.fromkeys(candidates),
                   key=lambda r: (-priority(r, agent, changed, summaries), sizes.get(r, 0), r))
    full, rest, used = [], [], 0
    for rel in order:
        cost = estimate_tokens(sizes.get(rel, 0)) + 20  # + line numbers and header
        if used + cost <= budget_tokens:
            full.append(rel)
            used += cost
        else:
            rest.append(rel)
    return full, rest
