# Handoff note — M1 → M2 and M3: agent_io usage and specialist invocation

**Date:** 2026-09-26  
**From:** Member 1 (Orchestration)  
**To:** Members 2 and 3  
**Status:** Local implementation; not committed or merged (`main` / `origin/main`: `969fae3`; M1 files untracked)

---

## How agents are invoked and how results get written

### The lifecycle: begin → (your agent runs) → finish

```
# Step 1: M1 orchestrator records the start time
python -m orchestrator.agent_io begin \
    --run <runId> --agent <code-review|testing|documentation>

# Step 2: YOUR agent runs (reads context.json, writes findings)
#   - Read:  runs/<runId>/context.json  (all input you need)
#   - Write: runs/<runId>/<agent>-findings.json  (your output, see format below)
#   - For testing only, also write: runs/<runId>/testing-execution.json

# Step 3: M1 orchestrator stamps and validates the result
python -m orchestrator.agent_io finish \
    --run <runId> --agent <agent> \
    --findings runs/<runId>/<agent>-findings.json \
    [--execution runs/<runId>/testing-execution.json]  # testing only
```

**M1 calls `begin` and `finish`. Your agent only writes the findings file.**  
Never write `<agent>-result.json` directly — `finish` stamps all metadata.

---

### Findings file format (what your agent writes)

```json
{
  "findings": [
    {
      "id": "CODE-001",
      "key": "<CATEGORY>|<file>|<symbol or ->|<subject>",
      "severity": "HIGH",
      "category": "NULL_HANDLING",
      "title": "Short description",
      "file": "sample-project/app/service.py",
      "line": 42,
      "symbol": "create_ticket",
      "description": "What is wrong and under which input it fails.",
      "evidence": "Quoted code, tool output, or doc text supporting the claim.",
      "evidenceType": "source-analysis",
      "recommendation": "One concrete, actionable fix.",
      "relatedFiles": []
    }
  ],
  "limitations": [
    "Any scope limits, e.g. third-party code not analysed."
  ]
}
```

**Rules:**
- Both `findings` and `limitations` arrays are required; no metadata header is allowed.
- `file` must start with `sample-project/` — never use absolute paths
- `line` and `symbol` may be `null` when not applicable; never guess
- `evidence` is required and must quote real content — never invent
- `key` subject: lowercase snake_case identifier, never a line number
- All `file` paths are relative to the snapshot root (same convention as context.json)

Full schema: `schemas/agent-result.schema.json` — look at the `$defs/finding` section.

---

### Testing agent: execution block (M3)

Write a separate file `runs/<runId>/testing-execution.json`:

```json
{
  "command": "python -m pytest -q --junitxml=../../logs/junit.xml",
  "workingDirectory": "runs/<runId>/snapshot/sample-project",
  "exitCode": 0,
  "logPath": "runs/<runId>/logs/pytest.log",
  "junitPath": "runs/<runId>/logs/junit.xml",
  "collected": 18, "passed": 18, "failed": 0, "skipped": 0, "errors": 0,
  "startedAt": "...", "finishedAt": "...", "durationMs": 2310
}
```

Any count that could not be determined: use `null`, **never `0`**.  
`exitCode: null` only if the command could not be started at all.

Hand this file to M1, who supplies `--execution runs/<runId>/testing-execution.json` to `agent_io finish`.

---

### Repair loop

If your raw findings has malformed JSON, a missing/invalid envelope, or invalid findings, `agent_io finish` exits **2** and
prints the exact errors. The specialist repairs the raw file once and calls
the same `finish` command again (no extra flag — `agent_io` remembers the
first failure in `runs/<runId>/<agent>.repair.json`; `--repair` is accepted
but ignored).

On a second failure it writes an `error` result automatically. Never silently
ignore the errors — the orchestrator will see `VERIFICATION_FAILED`.

An agent that fails before producing findings reports it directly, without a
findings file:

```bash
python -m orchestrator.agent_io finish --run <runId> --agent <name> \
  --status error --reason "what went wrong"
```

---

### How M1 invokes your specialist (spawn_subagent)

The planned real integration uses Bob's `spawn_subagent` mechanism; T7 fixtures do not exercise specialist invocation. Your `agents/<name>/instructions.md`
is the only input your specialist receives besides the runId:

```
Workspace: c:\Github Repo\PRism-AI.
Read your instructions from agents/<name>/instructions.md and follow them.
Your runId is <runId>.
Read runs/<runId>/context.json for all task context.
Write your findings to runs/<runId>/<name>-findings.json.
```

**What your instructions.md may assume:**
- `runs/<runId>/context.json` exists and is schema-valid
- `runs/<runId>/snapshot/` contains a read-only snapshot of `sample-project/`
- Read only context-listed snapshot inputs (`changedFiles[].path`, `relevantSource`, `relevantTests`, `relevantDocs`) under `snapshotDir`, plus the diff at `diffPath`. Skip absent deleted/excluded files; arbitrary snapshot reads are not permitted.
- You must NOT read `evaluation/` or any file outside `runs/<runId>/`
- You must NOT edit source files
- Raw findings contains only `findings` and `limitations`; M1 stamps identity and lifecycle metadata in the final result.

**Parallelism note (one data point — still being confirmed at CP3):**  
In Phase 0 Experiment 3 (two lightweight subagents), both started within 4 ms
of each other. Whether three real agents genuinely overlap is untested until CP3.
Sequential dispatch is the safe assumption until M1 confirms at CP3.

---

## Checklist before your first integration run

- [ ] `agents/<name>/instructions.md` exists and is generic (no ticket/priority/planted-problem mentions)
- [ ] Your agent reads context-listed files from the snapshot plus the diff, never arbitrary snapshot files
- [ ] M1 processes raw findings with `python -m orchestrator.agent_io finish --run <runId> --agent <agent> --findings runs/<runId>/<agent>-findings.json` (including `--execution` for testing)
- [ ] The resulting final artifact passes `python -m orchestrator.validate runs/<runId>/<agent>-result.json --context runs/<runId>/context.json`
- [ ] All `file` values start with `sample-project/`
- [ ] `id` values match `^(CODE|TEST|DOC)-\d{3}$` and are unique within the file
- [ ] `evidence` is non-empty and quotes real content
