#!/usr/bin/env python3
"""extract_api.py — deterministic API-surface extractor for the documentation agent.

Usage:
    python agents/documentation/extract_api.py --run <runId> [--app app.main:app]

Imports the FastAPI application from the run snapshot, calls app.openapi() to get
the OpenAPI schema, writes:
  runs/<runId>/openapi.json       — full OpenAPI JSON
  runs/<runId>/api-surface.md     — human-readable route/field summary

If the import fails for any reason, writes the error to api-surface.md and exits
with a non-zero code so the documentation agent can record a limitation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract API surface from a snapshot.")
    p.add_argument("--run", required=True, help="Run ID (e.g. run-20260926-101500-a1b2c3d)")
    p.add_argument(
        "--app",
        default="app.main:app",
        help="Module path and attribute of the FastAPI app (default: app.main:app)",
    )
    return p.parse_args()


def import_app(snapshot_dir: Path, app_ref: str):
    """Import the FastAPI app from the snapshot directory."""
    module_path, _, attr = app_ref.partition(":")
    if not attr:
        attr = "app"

    # Add the snapshot sample-project directory to sys.path so that
    # `from app.main import app` resolves to the snapshot copy.
    sample_project_dir = snapshot_dir / "sample-project"
    if not sample_project_dir.exists():
        # Fallback: add snapshot root directly.
        sample_project_dir = snapshot_dir

    sys.path.insert(0, str(sample_project_dir))
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, attr)
    finally:
        # Leave sys.path clean for subsequent imports.
        if str(sample_project_dir) in sys.path:
            sys.path.remove(str(sample_project_dir))


def describe_schema(schema: dict, schemas: dict) -> str:
    """Return a compact field list from a JSON Schema object."""
    if not isinstance(schema, dict):
        return ""
    # Resolve $ref
    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        schema = schemas.get(ref_name, {})

    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    if not props:
        return "  (no properties)"
    lines = []
    for name, prop in props.items():
        ptype = prop.get("type", "any")
        req = "required" if name in required else "optional"
        lines.append(f"  - `{name}` ({ptype}, {req})")
    return "\n".join(lines)


def build_surface(openapi: dict) -> str:
    """Build a markdown API-surface summary from an OpenAPI dict."""
    schemas = openapi.get("components", {}).get("schemas", {})
    paths = openapi.get("paths", {})
    lines: list[str] = [
        "# API Surface",
        "",
        f"OpenAPI version: {openapi.get('openapi', 'unknown')}  ",
        f"Title: {openapi.get('info', {}).get('title', 'unknown')}  ",
        f"Version: {openapi.get('info', {}).get('version', 'unknown')}",
        "",
    ]

    for path, path_item in sorted(paths.items()):
        for method, operation in path_item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            lines.append(f"## {method.upper()} {path}")
            lines.append("")
            summary = operation.get("summary") or operation.get("operationId") or ""
            if summary:
                lines.append(f"{summary}")
                lines.append("")

            # Query / path parameters
            params = operation.get("parameters", [])
            if params:
                lines.append("**Parameters**")
                lines.append("")
                for p in params:
                    pname = p.get("name", "?")
                    ploc = p.get("in", "?")
                    preq = "required" if p.get("required") else "optional"
                    pschema = p.get("schema", {})
                    ptype = pschema.get("type", "any")
                    lines.append(f"  - `{pname}` ({ploc}, {ptype}, {preq})")
                lines.append("")

            # Request body
            body = operation.get("requestBody", {})
            if body:
                content = body.get("content", {})
                json_body = content.get("application/json", {})
                body_schema = json_body.get("schema", {})
                lines.append("**Request body (application/json)**")
                lines.append("")
                lines.append(describe_schema(body_schema, schemas))
                lines.append("")

            # Responses
            responses = operation.get("responses", {})
            if responses:
                lines.append("**Responses**")
                lines.append("")
                for status, resp in sorted(responses.items()):
                    desc = resp.get("description", "")
                    resp_content = resp.get("content", {})
                    json_resp = resp_content.get("application/json", {})
                    resp_schema = json_resp.get("schema", {})
                    lines.append(f"  - `{status}` {desc}")
                    field_desc = describe_schema(resp_schema, schemas)
                    if field_desc and field_desc.strip() != "(no properties)":
                        lines.append(field_desc)
                lines.append("")

    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    run_dir = Path("runs") / args.run
    snapshot_dir = run_dir / "snapshot"
    openapi_path = run_dir / "openapi.json"
    surface_path = run_dir / "api-surface.md"

    # Ensure the run directory exists before writing any output files.
    run_dir.mkdir(parents=True, exist_ok=True)

    if not snapshot_dir.exists():
        surface_path.write_text(
            f"ERROR: snapshot directory not found: {snapshot_dir}\n", encoding="utf-8"
        )
        sys.exit(1)

    try:
        app = import_app(snapshot_dir, args.app)
    except Exception as exc:
        msg = f"ERROR: could not import app ({args.app}) from {snapshot_dir}: {exc}\n"
        surface_path.write_text(msg, encoding="utf-8")
        sys.exit(1)

    try:
        openapi = app.openapi()
    except Exception as exc:
        msg = f"ERROR: app.openapi() failed: {exc}\n"
        surface_path.write_text(msg, encoding="utf-8")
        sys.exit(1)

    openapi_path.write_text(
        json.dumps(openapi, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    surface_path.write_text(build_surface(openapi), encoding="utf-8")
    print(f"Wrote {openapi_path} and {surface_path}")


if __name__ == "__main__":
    main()
