# PRism-AI Evaluation Protocol — v1.0

> **Frozen before measurement.** This document is written once and committed before any scored
> runs are executed. No result may be retroactively excluded. No re-runs until a passing result
> is obtained. Illustrative or estimated numbers are never mixed with measured ones.

---

## 1. Pinned configuration

| Item | Value |
|------|-------|
| Agent prompt commit | to be filled in from `git rev-parse HEAD` at run time |
| Policy version | `1.0` (from `config/policy.json`) |
| Orchestrator module | `orchestrator/` (Member 1) |
| Runner module | `agents/testing/runner.py` (Member 3) |
| Python version | 3.11+ (exact version recorded per run from `python --version`) |
| Key packages | `fastapi`, `pytest`, `httpx`, `jsonschema` (exact versions from `pip freeze`) |

### Pinned git tags (A9)

| Tag | Role |
|-----|------|
| `baseline-clean` | Clean sample app — all tests pass, docs match code |
| `demo-bad` | Candidate with planted defects |
| `demo-fixed` | Candidate with defects fixed |
| `control-clean-change` | Harmless well-tested change — expected result: `READY_FOR_HUMAN_REVIEW` |
| `heldout-variation` | Different planted problem, not used in prompt tuning |

---

## 2. Planned runs

All runs use `--base baseline-clean --candidate <tag>` unless stated otherwise.

| Run set | Candidate | Count | Purpose |
|---------|-----------|-------|---------|
| Main detection | `demo-bad` | 5 | Seed recall and consistency |
| False-positive control | `control-clean-change` | 3 | False positives on clean change |
| Held-out | `heldout-variation` | 3 | Generalisation beyond tuning set |
| Re-verification | `demo-fixed` | 1 | Delta: planted issues resolved |
| Timing — sequential | `demo-bad` | 1 | Sequential wall time baseline |
| Timing — parallel | `demo-bad` | 1 | Parallel wall time |

**Total:** 14 runs.

---

## 3. Metrics

### 3.1 Seed recall

*Seeds matched / seeds present in the ref*, per run, overall and per agent.

A finding **matches** a seed when (A10):
- `agent` matches `seed.agent`
- `category` ∈ `seed.acceptedCategories`
- `file` == `seed.file`
- `symbol` ∈ `seed.symbols` (skip check if `symbols` is empty)
- `key` subject contains one of `seed.subjectHints` (skip check if empty)

Each seed and each finding is used at most once per run.

**Assignment algorithm (declared deviation from A10):** A10 states "the scorer picks the
assignment that matches the most seeds". `evaluation/score.py` uses a greedy heuristic
rather than true maximum bipartite matching: seeds are sorted by the number of compatible
findings (fewest first) and each is assigned the first unassigned compatible finding in that
order. For the seed counts used in this evaluation (≤ 5 seeds) the greedy result is identical
to the optimal assignment in all non-pathological cases. Any case where a finding is
compatible with more than one seed is flagged in `ambiguousFindings` in the raw-results JSON
and in the adjudication CSV (`rowType = AMBIGUOUS-FINDING`) for human review.

**Reported as:** fraction and percentage, with sample size. E.g. `3/5 (60%)` across 5 runs.

### 3.2 Precision

*Adjudicated true findings / all findings after de-duplication*, per run and overall.

Precision is calculated after a teammate fills in the adjudication CSV (see §6.2).
Findings not matched to a seed are classified as `TP-extra`, `FP`, or `duplicate`.

### 3.3 False positives on control

*Count of `MEDIUM`+ findings on `control-clean-change`.*

Expected value: 0. Any positive result is a false positive.

### 3.4 Held-out recall

*Did the agents surface the problem in `heldout-variation`?* Reported as count / 3 runs.

Exact matching criteria are defined by inspecting `seeded-issues.json` before scoring (seeds with
`presentIn` containing `heldout-variation`).

### 3.5 Consistency

*For each seeded issue in `demo-bad`: how many of the 5 runs matched it?*

Reported as a table: seed ID × runs 1–5, with ✓/✗.

### 3.6 Latency

*Wall clock time from request start to report written*, in milliseconds.

Source: `report.json` → `agents[].startedAt` (earliest) to `generatedAt`.

Reported as: median and range over the 5 `demo-bad` runs.

### 3.7 Parallel speedup

*Sequential wall time / parallel wall time*, for `demo-bad`.

Both raw times are shown. The ratio is a point estimate with no statistical claim.

### 3.8 Reliability

*Completed valid runs / attempted runs.*

Count separately: timeout failures, tool failures, schema validation failures.

### 3.9 Action quality

From the generate-tests corrective action on `demo-bad`:

- Number of tests generated
- Number executed
- Number that **failed** on the buggy candidate (proof of detection)
- Number that **passed** on `demo-fixed` (proof of correctness)
- Number that needed manual edits before applying (note the type of edit)

---

## 4. Scoring procedure

1. Confirm all runs used the pinned tags and the pinned agent commit. Record `git rev-parse HEAD`
   and `pip freeze` output at the time of the runs in `evaluation/raw-results/env.txt`.
2. For each run, call:
   ```
   python evaluation/score.py --run <runId> --ref <tag>
   ```
3. Collect `evaluation/raw-results/<runId>.json` for all runs.
4. Have a teammate (not the agent author) fill in the adjudication CSV for each run.
5. Call:
   ```
   python evaluation/score.py --summarise
   ```
   to produce `evaluation/results.md`.

---

## 5. Failures policy

- **No run is excluded** after it starts, regardless of outcome. Timeouts and schema failures
  are counted in reliability and included in recall denominators.
- **No re-run** is performed simply because the result was poor. Re-runs are only allowed if a
  reproducible infrastructure fault (e.g. network outage) can be documented.
- **Partial results** (e.g. only two of three agents completed) are scored with the completed
  findings only, and reliability is decremented.

---

## 6. Adjudication

### 6.1 Who adjudicates

A teammate who did **not** write the testing agent instructions fills in the adjudication CSV.

### 6.2 Adjudication CSV columns

`runId`, `findingId`, `findingKey`, `agent`, `category`, `severity`, `title`, `verdict`

`verdict` values: `TP-extra` (true positive not in the seed list), `FP` (false positive),
`duplicate` (same issue as a matched seed, different finding).

---

## 7. Limitations (declared in advance)

The following limitations apply to this evaluation and will be stated in `evaluation/results.md`:

1. **One small sample app.** Results may not generalise to larger or more complex codebases.
2. **Five planted problems.** The seeded issues are related (all in the same domain); they are
   not a random sample of real defects.
3. **Self-tuned prompts.** Agent prompts were written by the same team that designed the
   evaluation. Independent prompt authors may produce different recall.
4. **Small sample sizes.** Five runs is sufficient for consistency observation but insufficient
   for statistical confidence intervals.
5. **LLM non-determinism.** The same prompt on the same input may produce different findings
   across runs. Consistency metric captures this.
6. **Single model.** All agents use the same underlying model. Results do not generalise to
   other models.
7. **Action quality sample size is 1.** One generate-tests run is not enough for a rate estimate.
   It is presented as a qualitative demonstration only.
