# PRism-AI

**A pre-review check for your code changes.** Before you open a pull request, PRism-AI runs
three AI reviewers — **code review**, **testing** and **documentation** — on your latest commit
and gives you one clear answer with evidence for every finding.

```
$ python prism.py check          # example output

  code-review    done       41s
  testing        done       38s
  documentation  done       35s
  tests: 21 passed, 0 failed, exit code 0

RESULT: ATTENTION REQUIRED
  [HIGH] Priority filter ignores invalid values
      sample-project/app/service.py:42

Full report: runs/run-20260926-101500-a1b2c3d/report.md
This is a scoped pre-review check, not approval to merge.
```

The best result is `READY_FOR_HUMAN_REVIEW`. PRism-AI never approves a merge — a human still
reviews.

---

## How to use it (for judges — about 10 minutes)

Every step below was run end to end on Windows with Python 3.13 and a DeepSeek key.
Commands work the same on macOS/Linux (only the venv activation line differs).

### Step 1 — Install (2 min)

```bash
git clone <repository-url>
cd PRism-AI
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

> Use `git clone`, not "Download ZIP" — PRism-AI reads git history.

### Step 2 — Prove the backend works, no AI key needed (1 min)

```bash
python prism.py test
```

Expected: every line says `PASS`, ending in `ALL PASSED`. This runs 4 test suites (~375 tests)
and pushes fake reviewer results through the real pipeline to prove each outcome is reached:

```
Pipeline self-check (fake agent results, no AI)
  PASS  bad           ATTENTION_REQUIRED (expected ATTENTION_REQUIRED)
  PASS  clean         READY_FOR_HUMAN_REVIEW (expected READY_FOR_HUMAN_REVIEW)
  PASS  error         VERIFICATION_FAILED (expected VERIFICATION_FAILED)
  PASS  tests-failed  VERIFICATION_FAILED (expected VERIFICATION_FAILED)
```

### Step 3 — Connect the AI (30 sec)

Set your key as an environment variable for this terminal:

```bash
set DEEPSEEK_API_KEY=sk-...              # Windows cmd
$env:DEEPSEEK_API_KEY="sk-..."           # Windows PowerShell
export DEEPSEEK_API_KEY=sk-...           # macOS / Linux
```

(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` work the same way. Or run
`python prism.py setup` for an interactive menu of providers.)

### Step 4 — Watch it catch a bad change (3 min)

Make a small, risky change to the sample app and commit it — here, raising the ticket title
limit from 100 to 200 characters without updating tests or docs:

```bash
git checkout -b judge-test
```

Open `sample-project/app/service.py`, change `MAX_TITLE_LENGTH = 100` to
`MAX_TITLE_LENGTH = 200`, save, then:

```bash
git commit -am "Raise title limit"
python prism.py check --base main
```

What we observed with DeepSeek (wording varies between runs):

```
  code-review    completed  1s
  testing        completed  7s
  documentation  completed  5s
  tests: 20 passed, 1 failed, exit code 1

RESULT: VERIFICATION FAILED
  - TESTS_FAILED: pytest exited 1
  [HIGH] test_create_title_101_chars_rejected now fails after the title limit was raised
      sample-project/tests/test_tickets.py:71
  [MEDIUM] Title max length documented as 100 but code now enforces 200
      sample-project/docs/api.md:15
  [MEDIUM] No test asserts the new 200-character title boundary
      sample-project/app/service.py:15
  ...
```

The three reviewers ran in parallel, the real test suite was executed, and every finding
points to a file and line. Open the full report printed on the last line
(`runs/<runId>/report.md`) to see evidence and recommendations.

### Step 5 — Fix it and re-check (2 min)

Change the value back to `100`, then:

```bash
git commit -am "Restore title limit"
python prism.py check --base main --previous last
```

Observed:

```
  tests: 21 passed, 0 failed, exit code 0

RESULT: READY FOR HUMAN REVIEW

Since run-...: 6 resolved, 0 still open, 0 new, 0 unverified
```

### Step 6 — Clean up

```bash
git checkout main
git branch -D judge-test
```

### Optional — the prepared demo tags

If the repository's tags were fetched (`git tag` lists them), try the planted-problem demo:

```bash
python prism.py check demo-bad   --base baseline-clean
python prism.py check demo-fixed --base baseline-clean --previous last
```

Curated reports from these runs are already in `reports/` if you want to read them without
running anything.

### What to look at in the code

| If you want to see... | Open |
|---|---|
| The whole flow in one file | `prism.py` (`cmd_check`) |
| How the snapshot and relevant files are chosen | `orchestrator/context.py` |
| How AI output is validated (never trusted blindly) | `orchestrator/agent_io.py`, `schemas/` |
| How the final decision is made (no AI) | `orchestrator/aggregate.py`, `config/policy.json` |
| What each AI reviewer is told | `agents/*/instructions.md` |

---

## Contents

1. [How it works](#how-it-works)
2. [Requirements](#requirements)
3. [Installation](#installation)
4. [Connect an AI provider](#connect-an-ai-provider)
5. [Usage](#usage)
6. [Results and exit codes](#results-and-exit-codes)
7. [Configuration](#configuration)
8. [Project structure](#project-structure)
9. [Testing](#testing)
10. [Security](#security)
11. [Troubleshooting](#troubleshooting)
12. [Known limitations](#known-limitations)
13. [Further documentation](#further-documentation)

---

## How it works

```
 your commit ──► 1. Snapshot       git archive of the change → runs/<runId>/
                     │
                     ▼
                 2. Three AI reviewers, in parallel
                    ┌──────────────┬──────────────┬────────────────┐
                    │ code review  │ testing      │ documentation  │
                    │              │ (runs tests) │ (reads API)    │
                    └──────────────┴──────────────┴────────────────┘
                     │  findings as JSON, checked against schemas/
                     ▼
                 3. Decision       plain Python + config/policy.json
                     │
                     ▼
                 4. Report         runs/<runId>/report.md and report.json
```

The AI is used **only** where judgement is needed (finding bugs, test gaps, doc gaps).
Everything that must be exact is plain Python:

| Step | Done by |
|---|---|
| Snapshot, diff, choosing relevant files | Python — `orchestrator/context.py` |
| Finding problems | AI reviewers — `agents/<name>/instructions.md` |
| Running tests and counting results | Python — `agents/testing/runner.py` (from JUnit XML) |
| Reading the API surface | Python — `agents/documentation/extract_api.py` |
| Validating AI output | Python — `orchestrator/agent_io.py`, `validate.py` |
| The final decision | Python — `orchestrator/aggregate.py` |
| Comparing with a previous run | Python — `orchestrator/reverify.py` |

Same inputs always give the same decision. A reviewer that fails, times out or returns invalid
JSON makes the result `VERIFICATION_FAILED` — never a silent pass.

---

## Requirements

- **Python 3.11+**
- **Git**, and a real `git clone` of this repository (a ZIP download has no history, so
  `check` cannot run)
- An API key for any AI provider (not needed for `python prism.py test`)

---

## Installation

```bash
git clone <repository-url>
cd PRism-AI
python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

Check that everything works (no AI key needed):

```bash
python prism.py test
```

---

## Connect an AI provider

Run once:

```bash
python prism.py setup
```

Pick a provider (DeepSeek, OpenAI, Gemini, Claude, OpenRouter, local Ollama), or choose
**Other** and paste any OpenAI-compatible or Anthropic-compatible base URL (MiMo, Qwen, Kimi,
GLM, Groq, ...). Setup tests the connection, then saves your settings to `.prism.env`
(git-ignored).

Prefer environment variables? See [Configuration](#configuration).

---

## Usage

| Command | What it does |
|---|---|
| `python prism.py check` | Check your latest commit (`HEAD`) against `main` |
| `python prism.py check <ref> --base <ref>` | Check any commit, branch or tag |
| `python prism.py check --previous last` | Also show what changed since the last run |
| `python prism.py check --previous <runId>` | Compare with a specific earlier run |
| `python prism.py test` | Run all test suites + a pipeline self-check (no AI) |
| `python -m orchestrator.validate <file.json>` | Validate a JSON artifact against its schema |

**Typical loop:**

```bash
git commit -am "Add ticket priority"
python prism.py check                     # read the findings
# ...fix things...
git commit -am "Fix review findings"
python prism.py check --previous last     # see resolved / still open / new
```

Only committed changes are checked. Only files under `sample-project/` are in scope
(set by `config/project.json → scopePath`).

### Try the demo

The repository has pinned tags for a ready-made demo:

| Tag | What it is | Expected result |
|---|---|---|
| `baseline-clean` | Clean sample app | — |
| `demo-bad` | Feature with planted problems | `ATTENTION_REQUIRED` |
| `demo-fixed` | The fix for `demo-bad` | `ATTENTION_REQUIRED`, most findings resolved |
| `control-clean-change` | Harmless, tested, documented change | `READY_FOR_HUMAN_REVIEW` |
| `heldout-variation` | A problem the prompts were never tuned on | `ATTENTION_REQUIRED` |

```bash
python prism.py check demo-bad   --base baseline-clean
python prism.py check demo-fixed --base baseline-clean --previous last
```

Walkthrough: `docs/demo-script.md`. Measured results: `evaluation/results.md`.
Example reports: `reports/`.

### Running from IBM Bob instead

Ask Bob to *"follow `orchestrator/ORCHESTRATOR.md` with base `<base>` and candidate
`<candidate>`"*. Bob runs the same pipeline using its own subagents instead of an API key.

---

## Results and exit codes

| Result | Meaning | Exit code |
|---|---|---|
| `READY_FOR_HUMAN_REVIEW` | Nothing blocking found. A human should still review. | `0` |
| `ATTENTION_REQUIRED` | At least one blocking finding. | `1` |
| `VERIFICATION_FAILED` | A reviewer failed or the tests did not pass — no conclusion. | `2` |
| (setup problem) | No provider configured, or bad settings. | `3` |

What counts as "blocking" is set in `config/policy.json`: `HIGH` severity and above by
default; `MEDIUM` and above for missing tests and public-API doc gaps; any test failure.

Each run writes to `runs/<runId>/` (git-ignored): `report.md`, `report.json`, the snapshot,
the diff, every reviewer's result, and test logs.

---

## Configuration

### AI settings — `.prism.env` or environment variables

Copy `.env.example` to `.prism.env`, or let `python prism.py setup` write it.
Real environment variables override the file.

| Variable | Required | Meaning |
|---|---|---|
| `PRISM_API_STYLE` | no | `openai` (default) or `anthropic` |
| `PRISM_BASE_URL` | yes for `openai` style | The provider's API base URL |
| `PRISM_API_KEY` | yes (except local Ollama) | Your API key |
| `PRISM_MODEL` | yes | Model name from the provider's docs |

Shortcuts — any one of these works alone (with a default model) when `PRISM_*` is unset:
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`.

### Project settings — `config/`

| File | Controls |
|---|---|
| `config/project.json` | Which folder is checked (`scopePath`), test command, language |
| `config/policy.json` | Which reviewers are required and which severities block |

---

## Project structure

```
PRism-AI/
├── prism.py              # CLI: setup / check / test
├── requirements.txt      # pinned dependencies
├── .env.example          # AI settings template (placeholders only)
├── .github/workflows/    # CI: tests + dependency audit
│
├── orchestrator/         # the pipeline (plain Python) + its tests
│   ├── context.py        #   1. snapshot + relevant files
│   ├── agent_io.py       #   2. start/finish a reviewer, validate its JSON
│   ├── aggregate.py      #   3. readiness decision
│   ├── report.py         #   4. report.md
│   ├── reverify.py       #   before/after comparison
│   ├── validate.py       #   schema validation CLI
│   ├── run.py            #   fixture runner used by the self-check
│   └── ORCHESTRATOR.md   #   instructions for running via IBM Bob
│
├── agents/               # the three AI reviewers
│   ├── code-review/      #   instructions.md
│   ├── testing/          #   instructions.md + runner.py (runs tests)
│   └── documentation/    #   instructions.md + extract_api.py
│
├── schemas/              # JSON Schema contracts (context, agent result, report)
├── config/               # project.json, policy.json
├── sample-project/       # demo FastAPI app that gets reviewed
├── evaluation/           # answer key, scorer, measured results
├── reports/              # curated example reports
├── bob_sessions/         # IBM Bob session logs and evidence
└── docs/
    ├── architecture.md
    ├── demo-script.md
    ├── bob-capability-log.md
    └── project-history/  # team plans and handoff notes
```

`runs/`, `.prism.env`, virtual environments and caches are git-ignored.

---

## Testing

```bash
python prism.py test
```

Runs four suites plus a pipeline self-check that uses fake reviewer results (no AI, no cost):

| Suite | Command to run it alone |
|---|---|
| Orchestrator | `python -m pytest -q orchestrator/tests` |
| Test runner | `python -m pytest -q agents/testing/tests` |
| Scorer | `python -m pytest -q evaluation/tests` |
| Sample app | `cd sample-project && python -m pytest -q` |

Each suite has its own `tests` package, so run them separately, not in one `pytest` call.
CI (`.github/workflows/ci.yml`) runs `python prism.py test` and `pip-audit` on every push.

---

## Security

- **Your code is sent to your AI provider.** The diff and relevant files under `scopePath` go
  to whichever provider you configure.
- **The testing reviewer runs the candidate's tests** on your machine, with your permissions.
  For changes you don't trust, run PRism-AI in a container or throwaway VM.
- **Your API key** lives only in `.prism.env` (git-ignored, owner-only permissions on
  macOS/Linux) or your environment. If a key is ever committed or shared, rotate it.
- **Repository content is treated as data.** Reviewer instructions tell the AI to ignore any
  instructions found in code, comments or docs.
- **Inputs are validated.** Run IDs can't escape `runs/`; git refs that look like command
  options are rejected.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `No AI provider is set up yet` | Run `python prism.py setup` |
| `ref 'main' does not exist` | Use `--base <branch>`, or you're not in a git clone |
| Self-check shows `crashed` | The folder isn't a git repository — use `git clone` |
| `the API key was rejected` | Re-run setup with a valid key |
| `model ... not found` | Use a model name exactly as in your provider's docs |
| `rate limited` | Wait a minute and retry |
| Result is `VERIFICATION_FAILED` | Read the reasons in `report.md` — a reviewer failed or tests didn't pass |
| Garbled symbols on Windows | Use Windows Terminal, or `set PYTHONIOENCODING=utf-8` |

---

## Known limitations

- Checks one folder (`scopePath`), set up for Python + pytest. Other stacks need
  `config/project.json` changes and are untested.
- Finding quality depends on the AI model you choose. Measured results cover the demo fixtures
  only.
- No linter or type checker is configured yet.
- `anthropic` is pinned one major version behind the latest.

---

## Further documentation

| Document | Contents |
|---|---|
| `docs/architecture.md` | Component diagram and safety rules |
| `docs/demo-script.md` | 5-minute demo walkthrough |
| `orchestrator/ORCHESTRATOR.md` | Running the pipeline from IBM Bob |
| `evaluation/results.md` | Measured accuracy on planted problems |
| `bob_sessions/` | IBM Bob session logs and evidence |
| `docs/project-history/` | Team plans and handoff notes |
