# PRism-AI Evaluation Results

*Generated 2026-09-26 07:20 UTC*
*Total runs scored: 4*

> Specialists in these runs were executed by Claude Code subagents following
> `orchestrator/ORCHESTRATOR.md` and `agents/*/instructions.md` (IBM Bob credits had run out).
> Each subagent received only its instruction file and the runId.

## Ref: `control-clean-change` (1 run(s))

**§3.1 Seed recall**: N/A

**§3.2 Precision**: N/A (no adjudication verdicts recorded)

**§3.3 False positives on control** (MEDIUM+ findings): median 0, range [0, 0], n=1
  *(Expected: 0)*

**§3.6 Latency**: median 86804 ms, range [86804, 86804] ms, n=1

**§3.8 Reliability**: 1/1 runs fully completed, n=1

**Readiness states:**
- `READY_FOR_HUMAN_REVIEW`: 1

## Ref: `demo-bad` (1 run(s))

**§3.1 Seed recall** (overall): median 100%, range [100%, 100%], n=1

**§3.2 Precision**: N/A (no adjudication verdicts recorded)

**§3.6 Latency**: median 142765 ms, range [142765, 142765] ms, n=1

**§3.8 Reliability**: 1/1 runs fully completed, n=1

**Readiness states:**
- `ATTENTION_REQUIRED`: 1

## Ref: `demo-fixed` (1 run(s))

**§3.1 Seed recall**: N/A

**§3.2 Precision**: N/A (no adjudication verdicts recorded)

**§3.6 Latency**: median 100808 ms, range [100808, 100808] ms, n=1

**§3.8 Reliability**: 1/1 runs fully completed, n=1

**Readiness states:**
- `ATTENTION_REQUIRED`: 1

## Ref: `heldout-variation` (1 run(s))

**§3.1 Seed recall** (overall): median 100%, range [100%, 100%], n=1

**§3.2 Precision**: N/A (no adjudication verdicts recorded)

**§3.4 Held-out recall**: 1/1 runs surfaced the held-out problem (planned: 3), n=1

  Seed-by-seed breakdown:
  - `SEED-06`: found in 1/1 runs

**§3.6 Latency**: median 110376 ms, range [110376, 110376] ms, n=1

**§3.8 Reliability**: 1/1 runs fully completed, n=1

**Readiness states:**
- `ATTENTION_REQUIRED`: 1

## §3.7 Parallel speedup

N/A — timing runs not yet scored (use `--ref timing-sequential` and `--ref timing-parallel` when scoring the two timing runs).

## §3.9 Action quality

N/A — no `action-result.json` files found under `runs/`.
Run `agents/testing/generate-tests.md` workflow on a completed run to populate this.

---

## Limitations

- One small sample app — results may not generalise to larger or more complex codebases.
- Five planted problems that are related (same domain); not a random sample of real defects.
- Self-tuned prompts — prompts were written by the same team that designed the evaluation.
- Small sample sizes — five runs is insufficient for statistical confidence intervals.
- LLM non-determinism — same prompt on same input may produce different findings across runs.
- Single model — all agents use the same underlying model.
- Action quality sample size is 1 — presented as qualitative demonstration only.
- Seed matching uses a greedy heuristic (fewest-options-first); ambiguous cases are flagged for human adjudication and recorded in ambiguousSeeds.
