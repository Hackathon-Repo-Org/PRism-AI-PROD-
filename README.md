# PRism-AI

**A pre-review code checker.** Before code goes to human review, PRism-AI runs three AI
specialists — **code review**, **testing** and **documentation** — in parallel, runs the
project's real tests, and gives one clear verdict with file-and-line evidence for every finding.
Talk to it in plain English, or run a single command.

```
RESULT: ATTENTION REQUIRED   ⚠️  (1 critical · 6 high · 8 medium · 6 suggestions)

  #  Severity  Area           Where                            Problem
  1  CRITICAL  Security       shopmart/reports/sales.py:7      sales_for_customer builds SQL by f-string …
  2  HIGH      Security       shopmart/users/auth.py:5         Hardcoded admin credentials and unsalted MD5 …
  5  HIGH      Logic          shopmart/orders/discounts.py:7   bulk_discount applies discount only above 10 …

What to do first
  1. shopmart/reports/sales.py:7 — Use a parameterised query: conn.execute("… = ?", (customer_name,))
```

The best verdict is `READY_FOR_HUMAN_REVIEW`. **PRism-AI never approves a merge** — a human
still reviews.

---

## Judges: quick start (5 minutes)

You need **Python 3.11+** and an API key from **any** AI provider (DeepSeek, OpenAI, Gemini,
Claude, OpenRouter, …). No database, server, Docker or cloud account is needed.

```powershell
git clone https://github.com/Hackathon-Repo-Org/PRism-AI-PROD-.git
cd PRism-AI-PROD-
pip install -r requirements.txt

python prism.py test                         # 1. no key needed: proves the whole backend works

$env:DEEPSEEK_API_KEY = "sk-..."             # 2. your key (macOS/Linux: export DEEPSEEK_API_KEY=sk-...)
python prism.py doctor                       #    checks everything, prints how to fix problems

python tools/make_demo_project.py            # 3. creates demo-large-project/ (messy code, 10 planted bugs)
python prism.py chat demo-large-project      # 4. say hi, then type: analyse this project and give me a report
```

Other keys work the same way: `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, or any
provider via `python prism.py setup`. The answer key for the demo is in
`demo-large-project-ANSWERS.md` (kept outside the scanned folder so the AI cannot read it).

**If anything goes wrong, run `python prism.py doctor`.**

---

## Contents

1. [Overview](#overview) · 2. [Key features](#key-features) · 3. [Architecture](#architecture) ·
4. [Sample run](#sample-run) · 5. [How to use it](#how-to-use-it) · 6. [Results](#results-and-exit-codes) ·
7. [Configuration](#configuration) · 8. [Large projects & memory](#large-projects-and-memory) ·
9. [Testing](#testing) · 10. [Project structure](#project-structure) · 11. [Security](#security-and-privacy) ·
12. [Troubleshooting](#troubleshooting) · 13. [Limitations](#known-limitations) ·
14. [Built with IBM Bob](#built-with-ibm-bob) · 15. [Further documentation](#further-documentation)

---

## Overview

**The problem.** Pull requests often reach reviewers with bugs, missing tests and docs that no
longer match the code. Reviewers spend their time on problems a machine could have caught, and
AI review tools that "approve" code are hard to trust.

**What PRism-AI does.** It checks a change — or a whole folder — *before* human review:

- three AI specialists look at the code, the tests and the docs, **in parallel**;
- the project's **real test suite runs**, and pass/fail counts come from the test runner, not the AI;
- every AI answer is **validated against JSON schemas**; an invalid or missing answer can never
  count as a pass;
- the **verdict is decided by fixed rules** (`config/policy.json`), so the same findings always
  give the same result.

**Who it is for.** Developers who want a pre-review check, reviewers who want evidence rather
than opinions, and teams who want AI help without giving AI the final say.

**Why it matters.** It catches the cheap problems early, shows exactly where and why, tells you
what to fix first — and is honest about what it did not check.

---

## Key features

| Feature | What it means |
|---|---|
| **3 specialists in parallel** | Code review, testing, documentation — each with its own instructions (`agents/*/instructions.md`) |
| **Evidence, not opinions** | Every finding has a file, line, evidence and a recommended fix |
| **Real test execution** | The project's tests run on a copy of the code; results come from JUnit XML |
| **Rule-based verdict** | `READY_FOR_HUMAN_REVIEW` / `ATTENTION_REQUIRED` / `VERIFICATION_FAILED`, never "approved" |
| **Chat (`prismai`)** | Ask in plain English: "which parts are risky?", "explain this bug", "give me a report" |
| **Any input** | A git commit, any folder (no git), before/after folders, or a `.patch` file |
| **Large projects** | Two-pass review keeps every AI call within the model's limit, and says what was not read in full |
| **Memory** | Remembers file summaries in `.prism/`; an unchanged project is re-checked in ~1 s with 0 AI calls |
| **Any AI provider** | Any OpenAI-compatible or Anthropic-compatible API; invalid AI answers get one automatic repair |
| **Before/after tracking** | `--previous last` shows what was resolved, still open or new |
| **Auto-check on save** | `watch` mode and VS Code tasks write `PRISM-REPORT.md` into your project |
| **Safe by design** | Read-only, secrets never sent to the AI, repository content treated as data |

---

## Architecture

PRism-AI is a **Python command-line tool**. It has no web server, database, login or open
ports. `sample-project/` is a small FastAPI app that exists only to be reviewed in demos.

```mermaid
flowchart TD
    user["Developer or judge"]
    input["Input: git commit, folder or patch"]
    snap["Snapshot and context (context.py)"]
    size{"Too big for one AI call?"}
    map["Code map and AI summaries (codemap.py, memory.py)"]
    spec["3 specialists in parallel"]
    cr["Code review AI"]
    te["Testing AI and real pytest run"]
    docs["Documentation AI and API extraction"]
    val["Schema validation (agent_io.py)"]
    verdict["Rule-based verdict (aggregate.py, policy.json)"]
    out["Findings table and PRISM-REPORT.md"]

    user -->|"chat or check"| input
    input --> snap
    snap --> size
    size -->|"no"| spec
    size -->|"yes"| map
    map --> spec
    spec --> cr
    spec --> te
    spec --> docs
    cr --> val
    te --> val
    docs --> val
    val --> verdict
    verdict --> out
```

**Where the AI is used — and where it is not:**

| Step | Done by | Why |
|---|---|---|
| Snapshot, diff, choosing relevant files | Python (`orchestrator/context.py`) | Must be exact and repeatable |
| Finding bugs, test gaps, doc gaps | AI specialists | Needs reading and reasoning |
| Running tests, counting results | Python (`agents/testing/runner.py`) | Counts come from JUnit XML, never from the AI |
| Reading the API surface | Python (`agents/documentation/extract_api.py`) | OpenAPI is the ground truth |
| Checking AI answers | Python (`agent_io.py`, `validate.py`, `schemas/`) | The AI must not invent run IDs, timing or fields |
| The verdict | Python (`aggregate.py` + `config/policy.json`) | Same inputs → same decision |
| Chat answers | AI with 5 **read-only** tools | Advice only; the verdict still comes from the check |

---

## Sample run

A real run on the demo project (117 files, ~700 KB) with DeepSeek. AI wording varies between runs.

**1. Check a whole folder** — `python prism.py check --scan demo-large-project` (26 seconds):

```
[1/4] Preparing code...
Large project: 117 files (~267,039 tokens, limit 60,000 per AI call) - two-pass review.
[2/4] Reviewing: 3 specialists in parallel...
      documentation  completed  7s
      testing        completed  10s
      code-review    completed  11s
[3/4] Deciding...
[4/4] Writing report...
  tests: 7 passed, 0 failed, exit code 0

RESULT: ATTENTION REQUIRED   ⚠️  (1 critical · 6 high · 8 medium · 6 suggestions)

  #  Severity  Area           Where                            Problem
  1  CRITICAL  Security       shopmart/reports/sales.py:7      sales_for_customer builds SQL by f-string …
  2  HIGH      Security       shopmart/users/auth.py:5         Hardcoded admin credentials and unsalted MD5 …
  3  HIGH      Security       shopmart/users/profile.py:9      read_avatar joins user-supplied filename …
  4  HIGH      Null handling  shopmart/inventory/stock.py:16   load_stock swallows errors and returns None …
  5  HIGH      Logic          shopmart/orders/discounts.py:7   bulk_discount applies discount only above 10 …
  6  HIGH      Null handling  shopmart/orders/shipping.py:7    shipping_label crashes on the optional address
  7  HIGH      Logic          shopmart/payments/refund.py:3    can_refund uses 14 days; the API says 30
  8  MEDIUM    Docs missing   docs/api.md                      shipping_label public function is undocumented
 …
 15  MEDIUM    Missing test   shopmart/payments/refund.py:11   No test for the refund window boundary
  (+6 non-blocking suggestion(s) in the report)

What to do first
  1. shopmart/reports/sales.py:7 — Use a parameterised query: conn.execute("… = ?", (customer_name,))
  2. shopmart/users/auth.py:5 — Load credentials from a secret store; replace MD5 with bcrypt/argon2
  …
Full details, evidence and all 21 finding(s): demo-large-project\PRISM-REPORT.md
```

All **10 planted problems** were found (see `demo-large-project-ANSWERS.md`). Running the same
command again with no changes takes **~1 second with 0 AI calls** (memory).

**2. Ask in chat** — `python prism.py chat demo-large-project`:

```
PRism-AI - Hi! I'm your pre-review code checker.
  Project  demo-large-project  ·  117 files  ·  726 KB of code
  Memory   117 file summaries remembered  ·  last check: just now
  AI       DeepSeek · deepseek-chat

 you › analyse this project and give me a report
   ... mapping the project files
   ... running full review and tests
   (findings table as above)

 Verdict: ATTENTION_REQUIRED — nearly all the real risk sits in ~7 small hand-written files;
 the ~100 *_helpers_*.py files are legacy boilerplate.
 | # | File:line                   | Risk        | Issue                         | Fix                         |
 | 1 | shopmart/reports/sales.py:7 | 🔴 Critical | SQL built with an f-string    | Use a parameterised query   |
 | 2 | shopmart/users/auth.py:5    | 🟠 High     | Hardcoded creds + MD5         | Env secrets; bcrypt/argon2  |
 …
 Recommended order: 1. SQL injection  2. the other security issues  3. crash bugs  4. boundary tests

 you › explain the bug in shopmart/orders/shipping.py and show me the fix
   ... reading shopmart/orders/shipping.py
 The signature says address is optional, but line 7 calls address.upper() — so
 shipping_label("Ada") raises AttributeError. Fix: return the name alone when address is None,
 plus two regression tests (shown in full in the chat).

 you › /exit
Bye! 👋  (6 AI call(s) this session)
```

A step-by-step run with every intermediate file (context, diff, test log, each specialist's JSON)
is in [`docs/sample-run.md`](docs/sample-run.md).

---

## How to use it

### Chat — `prismai`

```powershell
python prism.py chat C:\my-project     # or, from inside the project folder: prismai
```

Ask anything, or use a command:

| Command | Does |
|---|---|
| `/check` | Full 3-specialist check; writes `PRISM-REPORT.md` (the official verdict) |
| `/findings` | Findings table + top recommendations from the last check |
| `/report` | Where `PRISM-REPORT.md` is, and its first lines |
| `/doctor` | Check the setup |
| `/clear` · `/help` · `/exit` | Forget the conversation · help · leave |

The AI can only use five **read-only** tools on that folder: project map, read a file (400 lines
at a time), search, run the full check, show findings. If no AI key is set, chat offers to set
one up.

**Getting the short `prismai` command** (optional — `python prism.py chat` always works):

| Way | Command |
|---|---|
| Launcher, no install | `C:\path\to\PRism-AI-PROD-\prismai.cmd` (macOS/Linux: `./prismai`) |
| Install | `pip install -e .` in the PRism-AI folder (note the dot), then open a **new** terminal |

`prismai` chats about the current folder; `prismai C:\other` about another; `prismai doctor`
and `prismai check …` also work.

### Check code — `prism.py check`

| You have… | Command |
|---|---|
| **Any folder** — review all of it (no git needed) | `python prism.py check --scan C:\my-project` |
| **Two copies** — before and after a change | `python prism.py check --before C:\old --after C:\new` |
| **A folder + a `.patch` / `.diff` file** | `python prism.py check --patch fix.patch --folder C:\my-project` |
| **A git commit** in this repository (`sample-project/`) | `python prism.py check` (latest commit vs `main`) or `check <ref> --base <ref>` |
| **A re-check after fixing** | add `--previous last` → resolved / still open / new |

Folder checks write `PRISM-REPORT.md` into that folder (change it with `--out FILE`). Use
`--test-command "CMD"` if the project's tests are not run with `python -m pytest -q`.

### Catch a bad change in the built-in sample app (about 5 minutes)

```powershell
git checkout -b judge-test
# edit sample-project/app/service.py:  MAX_TITLE_LENGTH = 100  ->  MAX_TITLE_LENGTH = 200
git commit -am "Raise title limit"
python prism.py check --base main
```

Expected (wording varies): `VERIFICATION FAILED` — an existing test now fails, plus findings for
the outdated API docs and the missing boundary test. Change the value back, then:

```powershell
git commit -am "Restore title limit"
python prism.py check --base main --previous last     # -> READY FOR HUMAN REVIEW, findings resolved
git checkout main; git branch -D judge-test            # clean up
```

### Auto-check on save

- Terminal: `python prism.py watch C:\my-project` — checks once, then again after each save
  (when files stop changing for 5 s), rewriting `PRISM-REPORT.md`. Each check is one AI run.
- VS Code: copy `integrations/vscode/tasks.json` to your project's `.vscode/`, set `PRISM_HOME`
  to the PRism-AI folder, then *Tasks: Run Task* → **PRism: scan whole project** or
  **PRism: auto-check on save (watch)**.

### Without an AI key

`python prism.py test` runs every test suite plus a pipeline self-check with fake specialist
results, proving the validation and decision logic end to end.

---

## Results and exit codes

| Verdict | Meaning | Exit code |
|---|---|---|
| ✅ `READY_FOR_HUMAN_REVIEW` | Nothing blocking found. A human should still review. | `0` |
| ⚠️ `ATTENTION_REQUIRED` | At least one blocking finding | `1` |
| ❌ `VERIFICATION_FAILED` | Tests failed or a specialist could not finish — no conclusion | `2` |
| (setup or input problem) | No AI configured, folder not found, patch does not apply, … | `3` |

**What blocks** (`config/policy.json`): `HIGH` and above; `MEDIUM` and above for missing tests
and documentation gaps; any test failure. A missing, timed-out or invalid specialist answer
always gives `VERIFICATION_FAILED`, never a pass.

Each check keeps everything in `runs/<runId>/` (git-ignored): `report.md`, `report.json`, the
snapshot, the diff, each specialist's result and the test logs.

---

## Configuration

### AI provider

Set **one** of these, or run `python prism.py setup` (saves to `.prism.env`, git-ignored). A
template is in `.env.example`.

| Variable | Meaning |
|---|---|
| `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` | Works on its own with a default model |
| `PRISM_BASE_URL` + `PRISM_API_KEY` + `PRISM_MODEL` | Any other provider (Groq, OpenRouter, Qwen, Kimi, a local Ollama, …) |
| `PRISM_API_STYLE` | `openai` (default) or `anthropic` — the API shape the provider speaks |

### Tuning

| Variable | Default | Meaning |
|---|---|---|
| `PRISM_MAX_INPUT_TOKENS` | `60000` | Largest input for one AI call; raise it for models with a bigger context |
| `PRISM_MAX_SCAN_BYTES` | 5 MB | Largest folder `--scan` accepts |
| `PRISM_PLAIN` | – | `1` = plain progress lines instead of the live panel |

### Project settings (`config/`)

| File | Controls |
|---|---|
| `config/project.json` | For commit checks: which folder (`sample-project`), test command, language |
| `config/policy.json` | Which specialists are required and which severities block |

---

## Large projects and memory

One AI call can only read so much. When a project is bigger than `PRISM_MAX_INPUT_TOKENS`,
PRism-AI switches to a **two-pass review** automatically:

1. **Map** — Python's parser lists every file's classes, functions and docstrings (free).
2. **Summarise** — the AI writes a one-line summary and a risk rating (0–3) per file, in parallel.
3. **Review** — each specialist gets the whole map plus the **full code of the most important
   files that fit**: changed files first, then the riskiest. The report lists every file that was
   only seen as a summary.

PRism-AI remembers its work in `.prism/` inside the checked folder (git-ignored):

| File | Remembers |
|---|---|
| `summaries.json` | The AI summary of each file, keyed by content — unchanged files are never summarised twice |
| `project-map.json` | Files, classes, functions, docstrings, imports, summaries, risk |
| `last-check.json` | A fingerprint of every file at the last scan |

Measured on the demo project: first scan **26–37 s**; no changes **~1 s, 0 AI calls**; one file
changed **~24 s**, one file re-summarised. Small projects skip all of this.

---

## Testing

```powershell
python prism.py test
```

Runs four test suites (~456 tests) and a pipeline self-check, no AI key needed:

| Suite | Run alone |
|---|---|
| Orchestrator, chat, large projects | `python -m pytest -q orchestrator/tests` |
| Test runner | `python -m pytest -q agents/testing/tests` |
| Evaluation scorer | `python -m pytest -q evaluation/tests` |
| Sample app | `cd sample-project; python -m pytest -q` |

Run the suites separately (each has its own `tests` package). AI calls are replaced by stubs in
tests; the real-AI runs above were done by hand with DeepSeek.

---

## Project structure

```
PRism-AI-PROD-/
├── prism.py               # CLI: setup · chat · check · watch · doctor · test
├── prismai.cmd, prismai   # launchers for the `prismai` command (Windows / macOS+Linux)
├── pyproject.toml         # pip install -e .  ->  prismai command
├── requirements.txt       # pinned dependencies
├── .env.example           # AI settings template (placeholders only)
│
├── orchestrator/          # the pipeline (plain Python) + tests
│   ├── context.py         #   snapshot, diff, relevant files
│   ├── ingest.py          #   --scan / --before / --patch inputs
│   ├── agent_io.py        #   start/finish a specialist, validate its JSON
│   ├── aggregate.py       #   the rule-based verdict
│   ├── report.py          #   report.md
│   ├── reverify.py        #   before/after comparison
│   ├── codemap.py         #   code map + fitting code into one AI call
│   ├── memory.py          #   .prism/ memory
│   ├── chat.py            #   prismai chat + read-only tools
│   ├── results.py         #   findings table + recommendations
│   ├── ui.py              #   live progress display
│   └── validate.py        #   schema validation CLI
├── agents/                # the three specialists' instructions + helpers
│   ├── code-review/
│   ├── testing/           #   + runner.py (runs the tests)
│   └── documentation/     #   + extract_api.py (reads the API)
├── schemas/               # JSON Schema contracts
├── config/                # project.json, policy.json
├── sample-project/        # demo FastAPI app that gets reviewed
├── tools/                 # make_demo_project.py (large demo project)
├── integrations/vscode/   # tasks.json for VS Code
├── evaluation/            # answer key, scorer, measured results
├── reports/               # example reports
├── bob_sessions/          # IBM Bob session evidence
└── docs/                  # architecture, sample run, demo script, project history
```

---

## Security and privacy

- **Your code is sent to your AI provider.** Choose a provider you are allowed to share it with.
- **`.env` files, private keys, images, binaries and files over 200 KB are never sent**, and
  folders like `.git`, `node_modules` and `.venv` are skipped.
- **The project's tests run on your machine**, on a copy of the code, with your permissions.
  For code you do not trust, use a container or throwaway VM.
- **Chat is read-only** and cannot read outside the chosen folder.
- **Repository content is data.** The specialists are told to ignore instructions found in code,
  comments or docs.
- **Your API key** stays in your environment or `.prism.env` (git-ignored). If a key is ever
  shared, revoke it at the provider.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Anything | `python prism.py doctor` — checks Python, packages, git, the AI key and connection, the terminal |
| `No AI provider is set up yet` | Set a key (see [Configuration](#ai-provider)) or run `python prism.py setup` |
| `the API key was rejected` / `model … not found` | Check the key; use the model name exactly as in your provider's docs |
| `prismai` is not recognized | Use `python prism.py chat`, or `pip install -e .` (with the dot) and open a new terminal |
| `pip install -e` says "requires 1 argument" | The dot is missing: `pip install -e .` |
| `--out: expected one argument` | Give a file name after `--out`, or leave `--out` out |
| Result is `VERIFICATION_FAILED` | Read the reasons: a test failed, or a specialist could not finish |
| A project's tests "could not be started" | Install that project's dependencies, or pass `--test-command` |
| `ref 'main' does not exist` | Commit checks need a git clone; use `--scan` for plain folders |
| Boxes or odd symbols instead of emoji | Use Windows Terminal or VS Code, or set `PRISM_PLAIN=1` |
| `Filename too long` (Windows) | Clone into a shorter folder, e.g. `C:\PRism-AI`; Windows limits paths to 260 characters |

---

## Known limitations

- **AI output varies** between runs; the verdict is deterministic *given* the findings.
- **Large projects:** files that do not fit one AI call are judged from their summary only; the
  report lists them, and bugs there can be missed.
- **Tests of other projects** only run if their dependencies are installed.
- **Mainly Python:** test running and API extraction are built for Python/pytest; other
  languages get the AI review and need `--test-command`.
- **Tested on Windows with DeepSeek.** Other providers and macOS/Linux are supported by design
  but were not tested end to end.
- **No CI pipeline** is set up in this repository yet.

---

## Built with IBM Bob

The team used IBM Bob during development. `bob_sessions/` holds the session evidence (logs
and screenshots), and `docs/bob-capability-log.md` records what Bob could and could not do.

---

## Further documentation

| Document | Contents |
|---|---|
| [`docs/sample-run.md`](docs/sample-run.md) | A commit check step by step, with every intermediate file |
| [`docs/architecture.md`](docs/architecture.md) | Component diagram and safety rules |
| [`docs/demo-script.md`](docs/demo-script.md) | The team's original demo script (uses git tags that are not included here) |
| [`evaluation/results.md`](evaluation/results.md) | The team's measured results on planted problems (original tagged fixtures) |
| [`docs/project-history/`](docs/project-history/) | Team plans and hand-over notes |
