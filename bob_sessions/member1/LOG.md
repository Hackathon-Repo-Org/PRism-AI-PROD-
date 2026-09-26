# Bob Session Log — Member 1

> **Evidence note:** No screenshot export or chat-history file was saved during the original
> Phase 0 run. Disk artifact evidence for reruns is preserved in `docs/bob-capability-log.md`.
> Screenshots must be taken by the human member from the Bob UI; this file records what to
> capture and references the corresponding log section.

| Date | Task | Outcome | Evidence / Notes |
|---|---|---|---|
| 2026-09-26 | Phase 0 Exp 1 (original): read 3-file toy repo, write JSON | Completed. `read_file` / `write_file` confirmed working, no prompt. Throwaway folder deleted; artifact not preserved. | See `docs/bob-capability-log.md` §Exp 1. Screenshot: none (original run). |
| 2026-09-26 | Phase 0 Exp 2 (RERUN): specialist reads instructions .md file | Completed. Subagent correctly read instructions from `.bob_phase0_exp2/specialist_instructions.md`, wrote `exp2_result.json` with `["IMPORT_COUNT", "reverse_words"]`. Throwaway folder deleted. | See `docs/bob-capability-log.md` §Exp 2. Screenshot: **PENDING** (human to capture from chat). |
| 2026-09-26 | Phase 0 Exp 3 (RERUN): two tasks + controlled failure + timestamps | Completed. Genuine overlap observed (4 ms gap). Failure in Task B did not affect Task A. Concurrency limit (3+) still unknown. | See `docs/bob-capability-log.md` §Exp 3. Screenshot: **PENDING** (human to capture from chat). |
| 2026-09-26 | Phase 0 Exp 4 (original, corrected): exit code + stdout/stderr | Completed. Exit code 3 confirmed. stdout and stderr captured separately. Permission behaviour for `git`/`pytest` unknown. | See `docs/bob-capability-log.md` §Exp 4. Screenshot: none (original run). |
| 2026-09-26 | Phase 0 Exp 5 (RERUN): JSON schema validation + repair loop | Completed. Spontaneous generation valid on first try. 4-error invalid payload repaired to valid in one attempt. Claim "one retry generally sufficient" removed. | See `docs/bob-capability-log.md` §Exp 5. Screenshot: **PENDING** (human to capture from chat). |
| 2026-09-26 | Phase 0 Exp 6 (RERUN): session evidence collection | Completed. No timestamp API found. No session-info tool. Evidence = screenshots + disk artifacts + this LOG.md. | See `docs/bob-capability-log.md` §Exp 6. |
| 2026-09-26 | Phase 0 overall: M2/M3 parallelism handoff | **PENDING** — draft written in `docs/bob-capability-log.md` §Decision. Human must send to M2 and M3. Mark delivered once confirmed. | — |
