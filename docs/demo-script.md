# PRism-AI — 5-minute demo script

All numbers below come from real runs on 2026-09-26 (see `evaluation/results.md` and the
reports in `reports/`). If anything is re-run live, quote the new numbers instead.

> **Disclosure.** The specialist agents in the recorded runs were executed by Claude Code
> subagents following the same `orchestrator/ORCHESTRATOR.md` and `agents/*/instructions.md`,
> because the team's IBM Bob credits ran out. Say this plainly if asked, and check the
> organisers' rules on it before submitting.

## Before you start

```
git fetch --tags --force
pip install -r requirements.txt
```

Open these in tabs: `reports/demo-bad/report.md`, `reports/demo-fixed/report.md`,
`reports/demo-fixed/delta.md`, `reports/control-clean-change/report.md`,
`reports/heldout-variation/report.md`.

---

## 0:00–0:40 — The problem

- Reviewers spend their time on routine gaps: a crash on a missing field, an untested edge
  case, docs that no longer match the API.
- Show `sample-project/`: a small ticket API. The change under review (`demo-bad`) adds a
  ticket `priority`.

## 0:40–1:15 — Why passing tests are not enough

```
git diff baseline-clean demo-bad -- sample-project
```

- All 22 existing tests pass on `demo-bad`.
- Yet `POST /tickets` without a priority crashes with a 500, and `"URGENT"` is accepted.

## 1:15–2:15 — One request, three specialists in parallel

- Ask the agent: *"Follow `orchestrator/ORCHESTRATOR.md` with base `baseline-clean` and
  candidate `demo-bad`."*
- Point out: the snapshot contains only `sample-project/` (the answer key cannot leak), and the
  three specialists start at the same time.
- Recorded run: all three overlapped; wall clock 132 s vs. 251 s of summed agent time.

## 2:15–3:00 — The report (`reports/demo-bad/report.md`)

- State: **ATTENTION REQUIRED**, with reasons.
- Code review: `priority.upper()` on a missing value → HTTP 500 (HIGH); no allowed-value check (HIGH).
- Testing: 22/22 passed, *and* no test for missing, null, or unsupported priority.
- Docs: `api.md` never mentions `priority`.
- Scored against the hidden answer key: **5 of 5 planted problems found**.
- The decision is plain Python applying `config/policy.json` — the model never decides "ready".

## 3:00–4:10 — Fix and re-check (`reports/demo-fixed/`)

```
git diff demo-bad demo-fixed -- sample-project
```

- Re-run on `demo-fixed`, then `python -m orchestrator.reverify --previous <bad run> --run <fixed run>`.
- `delta.md`: **12 findings resolved**, 3 persistent, 3 new (e.g. the README example still lacks `priority`).
- Still **ATTENTION REQUIRED** — and that is the point: the fix forgot to update the README
  example and to test whitespace trimming and `priority` in read responses. The tool caught
  gaps the team's own fix missed.

## 4:10–5:00 — Trust: controls and limits

- `control-clean-change` (a harmless, tested, documented search filter): **READY FOR HUMAN
  REVIEW**, zero blocking findings — it does not cry wolf.
- `heldout-variation` (an off-by-one nobody tuned the prompts for): found as a HIGH logic error,
  plus the deleted boundary test that hid it.
- Limits: one small sample app, one run per scenario, precision not yet adjudicated; the
  before/after matching can still split one issue into "resolved" + "new" when two runs word or
  locate it differently. A scoped pre-review check — never approval to merge.

---

## Fallback

If the live run fails, use the saved reports above and say clearly that they are recordings of
earlier runs, not live results.
