"""
orchestrator.results — show a check's result in the terminal: a findings table sorted by
severity (security first), the top recommendations, and where the full report is.

Used by `prism.py check` and by chat. Fancy (rich table) in a real terminal, a plain
fixed-width table everywhere else. The readiness decision is only displayed here, never
changed.
"""

from __future__ import annotations

import pathlib

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
AREA = {
    "SECURITY": "Security", "NULL_HANDLING": "Null handling", "INPUT_VALIDATION": "Input check",
    "LOGIC_ERROR": "Logic", "ERROR_HANDLING": "Error handling", "REGRESSION": "Regression",
    "MAINTAINABILITY": "Maintainability", "MISSING_TEST": "Missing test", "WEAK_TEST": "Weak test",
    "TEST_FAILURE": "Test failure", "DOC_MISMATCH": "Docs wrong", "DOC_MISSING": "Docs missing",
    "DOC_EXAMPLE_INVALID": "Docs example",
}
STATE_ICON = {"READY_FOR_HUMAN_REVIEW": "✅", "ATTENTION_REQUIRED": "⚠️ ",
              "VERIFICATION_FAILED": "❌"}
MAX_RECOMMENDATIONS = 10


def ordered(findings: list[dict]) -> list[dict]:
    """Blocking first, security first, then by severity, then by file and line."""
    return sorted(findings, key=lambda f: (not f.get("blocking"),
                                           f.get("category") != "SECURITY",
                                           SEVERITY_ORDER.get(f.get("severity"), 9),
                                           f.get("file", ""), f.get("line") or 0))


def short_path(path: str, scope: str | None = None) -> str:
    """'project/shopmart/orders/x.py' -> 'shopmart/orders/x.py' (scan workspaces)."""
    if scope and path.startswith(scope + "/"):
        return path[len(scope) + 1:]
    return path


def where(f: dict, scope: str | None = None) -> str:
    return short_path(f.get("file", "?"), scope) + (f":{f['line']}" if f.get("line") else "")


def counts(findings: list[dict]) -> str:
    blocking = [f for f in findings if f.get("blocking")]
    by_sev: dict[str, int] = {}
    for f in blocking:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    parts = [f"{n} {s.lower()}" for s, n in sorted(by_sev.items(),
                                                     key=lambda kv: SEVERITY_ORDER.get(kv[0], 9))]
    others = len(findings) - len(blocking)
    if others:
        parts.append(f"{others} suggestion{'s' if others != 1 else ''}")
    return " · ".join(parts) if parts else "no findings"


def _clip(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width - 1] + "…"


def recommendations(findings: list[dict], limit: int = MAX_RECOMMENDATIONS,
                    scope: str | None = None) -> list[str]:
    rows = []
    for f in ordered(findings):
        if not f.get("blocking") or not f.get("recommendation"):
            continue
        rows.append(f"{where(f, scope)} — {_clip(f['recommendation'], 160)}")
        if len(rows) == limit:
            break
    return rows


def print_result(report: dict, report_path: pathlib.Path | str | None = None,
                 fancy: bool | None = None, scope: str | None = None) -> None:
    """Findings table + top recommendations + pointer to the full report."""
    from orchestrator.ui import _fancy_possible
    fancy = _fancy_possible() if fancy is None else fancy
    findings = ordered(report.get("findings", []))
    blocking = [f for f in findings if f.get("blocking")]
    recs = recommendations(findings, scope=scope)
    state = report["readiness"]["state"]
    more = len(findings) - len(blocking)

    if fancy:
        from rich.console import Console
        from rich.table import Table
        console = Console(highlight=False)
        if blocking:
            table = Table(show_lines=False, header_style="bold #a78bfa", border_style="grey42",
                          expand=False)
            for col, kw in (("#", {"justify": "right"}), ("Severity", {}), ("Area", {}),
                            ("Where", {"overflow": "fold"}), ("Problem", {"overflow": "fold"})):
                table.add_column(col, **kw)
            for i, f in enumerate(blocking, 1):
                sev_style = {"CRITICAL": "bold red", "HIGH": "#f87171",
                             "MEDIUM": "#fbbf24"}.get(f["severity"], "default")
                table.add_row(str(i), f"[{sev_style}]{f['severity']}[/]",
                              AREA.get(f.get("category"), f.get("category", "")),
                              where(f, scope), _clip(f["title"], 90))
            console.print(table)
        if more:
            console.print(f"[grey58]+{more} non-blocking suggestion(s) in the report[/]")
        if recs:
            console.print("\n[bold #a78bfa]What to do first[/]")
            for i, r in enumerate(recs, 1):
                console.print(f" [#f472b6]{i}.[/] {r}")
        if report_path:
            console.print(f"\n📄 Full details, evidence and all {len(findings)} finding(s): "
                          f"[bold]{report_path}[/]")
        return

    if blocking:
        print(f"\n {'#':>2}  {'Severity':<8}  {'Area':<15}  {'Where':<34}  Problem")
        for i, f in enumerate(blocking, 1):
            print(f" {i:>2}  {f['severity']:<8}  {_clip(AREA.get(f.get('category'), ''), 15):<15}  "
                  f"{_clip(where(f, scope), 34):<34}  {_clip(f['title'], 70)}")
    if more:
        print(f"  (+{more} non-blocking suggestion(s) in the report)")
    if recs:
        print("\nWhat to do first")
        for i, r in enumerate(recs, 1):
            print(f" {i:>2}. {r}")
    if report_path:
        print(f"\nFull details, evidence and all {len(findings)} finding(s): {report_path}")
    _ = state  # the state line itself is printed by the caller (kept stable for scripts)
