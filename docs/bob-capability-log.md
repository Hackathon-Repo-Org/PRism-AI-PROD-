# Bob Capability Log — PRism-AI Member 1

> **Evidence note:** The original Phase 0 run was performed in the same Bob session as this
> document was created. No separate screenshot export or chat-history file was saved during
> that run — the original tool-call outputs exist only in the session transcript visible in
> the Bob UI and are not recoverable as standalone files. Experiments 2, 3, 5, and 6 were
> subsequently **rerun** to correct gaps identified in the Phase 0 review; each rerun is
> labelled as such. Experiments 1 and 4 retain their original observations, corrected where
> claims exceeded what was actually observed.

---

## Decision (read this first)

**Proven behaviour (from experiments below):**
- Bob's Agent mode runs in a single conversation thread. File I/O (`read_file`, `write_file`) and
  shell commands (`execute_command`) run inline in that thread without a separate permission
  prompt for non-destructive operations tested so far (see Exp 1 and 4 for caveats).
- `spawn_subagent` is the native mechanism for dispatching a specialist. Instructions can live in a
  Markdown file on disk; the subagent reads and follows them. Results are returned via a one-sentence
  summary string and disk-written JSON files — there is no shared-memory or direct object hand-back.
- In a single two-subagent test (Exp 3 rerun), both tasks started within 4 ms of each other and
  their execution windows genuinely overlapped. **This is one data point.** Whether this holds under
  load or with more than two subagents is unknown and must be confirmed at CP3 with real agents.
- A failing subagent (read of a nonexistent file) does not prevent the other subagent from
  completing. The parent receives both results normally when both calls are in the same turn.
- The concurrency limit of `spawn_subagent` is unknown; 2 was tested, 3 has not been tested.

**Planned behaviour (not yet proven):**
- Orchestrator will call `python -m orchestrator.agent_io begin` before dispatching each specialist
  and `python -m orchestrator.agent_io finish` after it returns, to stamp `startedAt`/`finishedAt`
  and validate the findings file. The specialist never writes `startedAt`/`finishedAt` directly.
- Each specialist obtains its task context by reading `runs/<runId>/context.json` from disk
  (path passed via runId), never from evaluation files or live working tree.
- Models write raw findings and limitations; all metadata stamping, schema validation, and
  aggregation is performed by deterministic Python code.

**Invocation/parallelism/fallback decision (required by brief):**
Specialists are dispatched as `spawn_subagent` calls, one per agent role. At CP3 we will dispatch
all three in one turn and record real `startedAt`/`finishedAt` timestamps from `agent_io`. If
genuine overlap is observed the pipeline records `overlapObserved: true`; if not, it records
`false` and the log documents why. We do not fake overlap with a Python thread pool. The primary
fallback is sequential dispatch (one subagent at a time) with the same disk-file handoff
mechanism — the aggregator is identical in both cases.

**Parallel execution handoff to M2 and M3 (delivery: PENDING — not yet sent):**
> *Draft for human to forward:* "From M1: In Phase 0 Experiment 3 (rerun), two subagents
> dispatched in the same turn started within 4 ms of each other and genuinely overlapped.
> However this was one controlled test with lightweight tasks. The concurrency limit is
> unknown; 3 simultaneous subagents (required for CP3) has not been tested. Until CP3
> confirms overlap with real agents, treat parallelism as unconfirmed. The fallback is
> sequential dispatch — the aggregator and report are identical either way. Impact on
> the hackathon brief: if genuine parallel execution cannot be demonstrated with real agents,
> we document this honestly with timestamps and explain the constraint."

---

## Experiment 1 — Bob reads a 3-file toy repo and writes a JSON file

**Original run** (not a rerun).

**What we tried:** Establish which Bob mode and entry point handles file I/O, and what permissions
it requires.

**Exact steps:**
1. Created `.bob_phase0_exp/toy_repo/` with `a.py`, `b.py`, `c.py` using `execute_command`
   (PowerShell `Set-Content`).
2. Called `read_file` on each of the three files in the same turn (parallel — no dependencies).
3. Called `write_file` to produce `.bob_phase0_exp/exp1_summary.json`.

**What happened:**
- **Bob mode:** Agent mode (the mode active during this session). No mode switch was performed
  or required.
- **Entry point:** The conversation thread itself — no separate subtask or subagent was spawned.
- `read_file` and `write_file` are listed as native file tools in the Agent mode tool set.
  Both returned results immediately with no permission prompt **in this session**.
- The throwaway folder was deleted after the experiment; the summary JSON is not preserved as a
  file artifact (it existed only in the throwaway folder).

**Permission observations (this session only):**
- `read_file` on files within the workspace: **no prompt observed**.
- `write_file` to new files within the workspace: **no prompt observed**.
- Whether `read_file` or `write_file` on paths outside the workspace directory would prompt or
  be blocked is **unknown — not tested**.

**Decision:** `read_file` / `write_file` handle all JSON artifact production without a shell.
No permission prompt was observed for workspace-internal file I/O in this session. Do not
generalise to arbitrary paths.

---

## Experiment 2 — Specialist invocation (RERUN)

*Original run dispatched instructions inside the `description` string rather than having the
subagent read a Markdown file. This rerun corrects that.*

**What we tried:** Place instructions in a Markdown file on disk; dispatch a subagent telling
it only to read that file and an input file; verify it follows the instructions and writes a
correct result.

**Setup:**
- Toy repo: `.bob_phase0_exp2/toy_repo/utils.py`
  ```python
  # utility module
  IMPORT_COUNT = 3

  def reverse_words(sentence):
      return ' '.join(reversed(sentence.split()))
  ```
- Instructions file: `.bob_phase0_exp2/specialist_instructions.md`
  (Symbol Extractor specialist — lists top-level names, writes a JSON with `specialist`,
  `input_file`, `symbols` fields, returns a one-sentence summary.)

**Dispatch description (exact):**
> "Read your instructions from `.bob_phase0_exp2/specialist_instructions.md` and follow them
> exactly. Your input file is `.bob_phase0_exp2/toy_repo/utils.py`. Your output path is
> `.bob_phase0_exp2/exp2_result.json`."

The description contained **no copy of the task rules** — only pointers.

**What happened:**
- Subagent read `specialist_instructions.md` independently (confirmed: it cited reading the file
  in its tool calls).
- Output written to `.bob_phase0_exp2/exp2_result.json`:
  ```json
  {
    "specialist": "symbol-extractor",
    "input_file": ".bob_phase0_exp2/toy_repo/utils.py",
    "symbols": ["IMPORT_COUNT", "reverse_words"]
  }
  ```
- Returned summary: *"Found 2 top-level symbols in utils.py: the constant IMPORT_COUNT and the
  function reverse_words."*
- `IMPORT_COUNT` and `reverse_words` are correct; the comment line (`# utility module`) was
  correctly excluded.

**Hand-back mechanism observed:**
- The subagent returns a plain-text summary string to the parent.
- All structured data travels via a disk-written JSON file that the parent reads after the
  subagent returns.
- No shared memory, no direct object reference, no live callback.

**Decision (updated):** `spawn_subagent` with a description that references an on-disk
instructions file is the correct specialist pattern. The subagent reads and follows the
instructions file without the parent needing to duplicate them.

---

## Experiment 3 — Two independent tasks with timestamps and a controlled failure (RERUN)

*Original run did not include a controlled failure case. This rerun adds it and re-examines
overlap with a fresh measurement.*

**What we tried:** Dispatch two subagents simultaneously — one succeeds, one intentionally fails
— and record code-generated timestamps to measure overlap.

**Dispatch:** Both `spawn_subagent` calls issued in the same `<function_calls>` block (same turn,
no dependencies). Each subagent ran `python -c "import datetime,json; ..."` to get `startedAt`,
performed its work step, then ran the same command for `finishedAt`.

**Task A (success):**
- startedAt:  `2026-09-26T04:42:32.537899Z`
- finishedAt: `2026-09-26T04:42:35.975731Z`
- work: read `.bob_phase0_exp2/toy_repo/utils.py` — succeeded
- status: `completed`

**Task B (controlled failure):**
- startedAt:  `2026-09-26T04:42:32.541901Z`
- finishedAt: `2026-09-26T04:42:37.068392Z`
- work: attempt to read `.bob_phase0_exp3_NONEXISTENT_FILE.py` — file did not exist
- error recorded: `"File does not exist: .bob_phase0_exp3_NONEXISTENT_FILE.py"`
- status: `failed`

**Overlap analysis:**
```
A: |====32.537======35.975|
B:   |====32.541=========37.068|
```
B started 4 ms after A. Both windows cover `04:42:32 – 04:42:35` together.
**Genuine overlap observed in this test** (one data point, lightweight tasks).

**Failure isolation observed:**
- Task B recorded its failure and still wrote its JSON output file.
- Task A completed normally — the failure in B did not affect A.
- The parent received both task result strings in the same turn. The failure was visible in B's
  summary and in its written JSON (`status: "failed"`, `error: "..."`).

**What is still unknown:**
- Concurrency limit: only 2 subagents were tested. Whether 3 can overlap (required for CP3) is
  **not yet confirmed**.
- Whether overlap holds for heavier tasks (model reasoning, large file reads) is unknown.
- Whether the API scheduler ever serialises subagents under different conditions is unknown.
- Do **not** infer simultaneous execution merely from calls sharing one `<function_calls>` block —
  the 4 ms gap in this test is evidence, not the block structure itself.

**Decision:** Overlap was observed in this single test. Record honestly at CP3 with real agents.
The pipeline must not claim `overlapObserved: true` without real `startedAt`/`finishedAt`
evidence from `agent_io`. Concurrency limit of 3 is untested — see handoff note in Decision section.

---

## Experiment 4 — Run a command, exit code, stdout and stderr capture

**Original run** (corrected for over-broad claims).

**What we tried:** Run `python -c "import sys; sys.exit(3)"` via `execute_command` and observe
exit code; also probe stdout and stderr capture separately.

**Exact steps:**
1. Ran `python -c "import sys; sys.exit(3)"; Write-Host "Exit code: $LASTEXITCODE"`.
2. Ran `python -c "import sys; sys.stdout.write('hello stdout\n'); sys.stderr.write('hello stderr\n'); sys.exit(3)"` followed by `Write-Host "LASTEXITCODE=$LASTEXITCODE"`.

**What happened:**
```
# Step 1
Exit code: 3

# Step 2
hello stdout
LASTEXITCODE=3

Stderr:
hello stderr
```
- Exit code `3` correctly captured in `$LASTEXITCODE` in both runs.
- `stdout` text (`hello stdout`) appears in the main tool result.
- `stderr` text (`hello stderr`) appears in the tool result under a `Stderr:` section, separate
  from stdout.
- Neither command produced a permission prompt in this session.

**Permission observations (this session only):**
- Short, non-destructive Python one-liners: **no prompt observed**.
- Git commands, `pytest`, and `python -m` module invocations have **not been tested** in this
  session; whether they prompt is **unknown**.
- Long-running background processes (`background: true`) have not been tested; behaviour unknown.
- Claims about "any Python/shell command" available without approval are **removed** — only the
  tested commands are confirmed.

**Decision:** `execute_command` captures exit code, stdout, and stderr. Confirmed for short
non-destructive Python commands in this session. Test `git`, `pytest`, and module invocations
before relying on them; document results in this log.

---

## Experiment 5 — JSON schema validation and model repair (RERUN)

*Original run only validated pre-written files (not model-generated output) and made an
unsubstantiated claim that "one repair retry is sufficient". This rerun tests a simulated
model-generated output with deliberate errors and a single repair attempt.*

**What we tried:** Have a subagent generate JSON claiming to match a schema (without telling it
the schema rules), validate the output, feed errors back for exactly one repair, and revalidate.

**Schema** (`.bob_phase0_exp5/schema.json`):
- required: `name` (string, minLength 1), `score` (integer 0–100), `tags` (array of strings, minItems 1)
- `additionalProperties: false`

**Attempt 1 — first generation (by subagent, no schema rules given):**
Output: `{"name": "PRism-AI", "score": 42, "tags": ["ai", "code-review", "pull-request"]}`
Validation result: **VALID (0 errors)**

*Note: the subagent's first attempt happened to be valid. To test the repair path, a separate
invalid payload was constructed deliberately:*

**Attempt 1-bad (deliberately invalid, to test repair path):**
Input: `{"name": "", "score": 150, "tags": [], "extra": "bad"}`
Validation result: **INVALID (4 errors)**
```
path=['root']: Additional properties are not allowed ('extra' was unexpected)
path=['name']: '' should be non-empty
path=['score']: 150 is greater than the maximum of 100
path=['tags']: [] should be non-empty
```

**Repair attempt (one only):**
Exact errors above were fed back to a subagent told to fix every reported error.
Repaired output (`.bob_phase0_exp5/attempt2_repaired.json`):
`{"name": "repaired", "score": 100, "tags": ["fixed"]}`
Revalidation result: **VALID (0 errors)**

**Outcomes observed:**
| Attempt | Input source | Errors | After repair |
|---|---|---|---|
| 1 | Subagent (no schema hints) | 0 — valid on first try | — |
| 1-bad | Deliberately invalid (4 errors) | 4 | Repaired to valid in one attempt |

**What is not claimed:**
- We tested exactly 1 spontaneous generation (valid) and 1 forced-invalid repair (succeeded).
  **We cannot claim one retry is "generally sufficient"** — this is 2 data points. Output quality
  depends on schema complexity and model state. The orchestrator must treat a failed second
  attempt as a hard error (`status: "error"`, reason: `SCHEMA_INVALID: ...`).

**Decision:** `jsonschema` validation works reliably and produces actionable error messages.
Feed exact error messages back for the single allowed repair. Treat a second failure as
permanent and report it as `status: "error"` — never silently drop or invent findings.

---

## Experiment 6 — Session evidence collection (RERUN)

*Original run claimed Bob "shows tool call details inline with timestamps" — the timestamp claim
was not precisely true. This rerun corrects the description to what was actually observed.*

**What we tried:** Inspect the Bob UI and available tools to determine what session/usage
information is accessible and how to capture `bob_sessions/` evidence.

**Exact steps:**
1. Scanned the available tool list in the current session for session/usage/token-count APIs.
2. Observed what the Bob chat UI displays during and after tool calls.

**What the Bob UI actually shows (observed):**
- Each tool call appears as a collapsible block in the chat: tool name, parameter values, and
  return value (or error).
- The conversation thread order implies chronological sequence, but **no wall-clock timestamps
  are displayed** on individual tool calls in the UI as observed in this session.
- Token counts, session IDs, and per-call latency metrics are **not surfaced** in any observed
  UI element or available tool.

**Available evidence capture methods (confirmed):**
- Screenshots of individual tool-call blocks in the Bob UI — show tool name, params, result.
- Copy/paste or export of the chat text — preserves the tool call sequence and content.
- Disk artifacts written by the session (JSON files, `.md` files) — the most durable evidence.

**What is not available (confirmed absent):**
- No tool named `session_info`, `get_usage`, or similar was found in the available tool list.
- No API for token counts, session IDs, or timestamps on individual tool calls.

**Decision:** `bob_sessions/member1/` evidence = screenshots of key tool-call blocks (taken by
the human member) + a `LOG.md` entry per meaningful task. The absence of a timestamp on each
tool call means we rely on file artifact timestamps (`createdAt` fields in JSON) for ordering
evidence, not on UI-displayed per-call times.
