# CLAUDE.md — Depcon

> Pre-commit devtool that spins up your service, smoke tests it, watches Dynatrace for
> what went wrong during the test run, runs an ADK+Gemini agent loop to diagnose the
> failure, generates a fix diff, and either clears the commit or blocks it with a
> one-command fix. Runs standalone as a CLI/TUI or wired into git pre-commit.

---

## Project Structure

```
depcon/
├── CLAUDE.md
├── depcon.toml.example              # config template
├── .pre-commit-hooks.yaml           # pre-commit framework registration
├── pyproject.toml                   # packaging + deps
│
├── examples/
│   └── fastapi-service/             # example target service (FastAPI + OTel)
│       ├── main.py
│       ├── requirements.txt
│       ├── Dockerfile
│       └── docker-compose.yml
│
├── depcon/
│   ├── __init__.py
│   ├── cli.py                       # typer CLI entrypoint
│   ├── hook.py                      # pre-commit wrapper
│   ├── config.py                    # depcon.toml loader
│   ├── smoketest.py                 # service startup + smoke test runner
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   └── dynatrace.py             # Dynatrace MCP tool wrappers
│   │
│   ├── agent.py                     # ADK + Gemini agent loop
│   ├── fix.py                       # diff apply + depcon fix apply logic
│   └── tui.py                       # Textual TUI (--watch mode)
│
└── tests/
    ├── test_smoketest.py
    ├── test_tools.py
    └── test_agent.py
```

---

## Tech Stack

| Component | Choice |
|-----------|--------|
| Language | Python 3.11+ |
| Package / env manager | `uv` (replaces pip + venv) |
| CLI framework | Typer |
| TUI framework | Textual |
| Agent framework | Google ADK (`google-adk`) + Gemini 2.0 Flash |
| MCP client | `mcp` Python SDK |
| Target service | FastAPI (Python 3.11+, under `examples/fastapi-service/`) |
| OTel instrumentation | `opentelemetry-sdk` + `opentelemetry-exporter-otlp` |
| Observability backend | Dynatrace SaaS tenant (free trial) |
| Container runtime | Docker Compose (OrbStack on macOS) |
| Config format | TOML (`depcon.toml`) |
| HTTP client | `httpx` (async) |

---

## Environment Variables

```bash
# Dynatrace
DT_ENVIRONMENT_ID=abc12345          # your tenant ID
DT_API_TOKEN=dt0c01.xxxxx           # platform token with Grail + problems read
DT_OTLP_ENDPOINT=https://<env>.live.dynatrace.com/api/v2/otlp

# Google / Gemini
GOOGLE_CLOUD_PROJECT=your-project
GOOGLE_GENAI_USE_VERTEXAI=true      # or false to use Gemini API key directly
GEMINI_API_KEY=AIza...              # if not using Vertex

# Depcon behaviour
DEPCON_SKIP=1                        # set in CI to bypass validation
DEPCON_TIMEOUT=120                  # max seconds for full run (default 120)
DEPCON_CONFIDENCE_THRESHOLD=medium  # block commit at: low | medium | high
```

All vars loaded from `.env` at project root via `python-dotenv`. Never commit `.env`.

---

## Configuration (`depcon.toml`)

```toml
[service]
compose_file = "examples/fastapi-service/docker-compose.yml"
health_endpoint = "http://localhost:8080/health"
startup_timeout_secs = 15
run_endpoint = "http://localhost:8080/run"

[smoke]
# Each entry is one test case POSTed to run_endpoint
cases = [
  { name = "happy_path",    body = '{"input": "hello"}',         expect_status = 200 },
  { name = "empty_input",   body = '{"input": ""}',              expect_status = 400 },
  { name = "large_payload", body = '{"input": "x"}',             expect_status = 200 },
]
request_timeout_secs = 10

[dynatrace]
service_name = "depcon-target"    # must match otel.service.name in FastAPI service
error_rate_threshold = 0.05         # >5% error rate = flag
latency_p99_threshold_ms = 500

[agent]
max_iterations = 5
model = "gemini-2.0-flash"

[output]
save_sessions = true                # save diagnosis sessions to .depcon/sessions/
sessions_dir = ".depcon/sessions"
```

---

## How It Works — Full Flow

```
git commit
  → pre-commit fires depcon/hook.py
  → hook.py calls: depcon run

depcon run:
  1. Load depcon.toml + env vars
  2. Read git diff --staged  (context for agent)
  3. docker compose up --build  (start FastAPI service fresh, from examples/fastapi-service/)
  4. Poll /health until 200 or timeout
  5. Record test_window.start = now()
  6. Run smoke test suite → local result summary
  7. Record test_window.end = now()
  8. docker compose down

  IF smoke tests all pass AND no errors in local summary:
    → exit 0, commit proceeds, print "✓ Depcon: service healthy"

  IF failures detected:
    9.  Agent loop begins (ADK + Gemini):
        - Context: staged diff + smoke summary + test_window
        - Iter 1: get_problems(since=test_window.start)
        - Iter 2: get_problem_detail(problem_id) if problems found
        - Iter 3: query_logs(test_window)  ← DQL: errors/warns in window
        - Iter 4: query_traces(test_window) ← DQL: error spans in window
        - Iter 5: natural_language_query fallback if needed
        - get_troubleshooting_guides(hypothesis)
        - Synthesise root cause → {hypothesis, confidence, evidence, affected_file}
    10. Fix generation → unified diff scoped to staged files
    11. Auto-apply attempt → git apply <diff>
    12. Print to stderr:
        - Root cause hypothesis + confidence
        - Evidence list (which tool, what it found)
        - Fix diff (inline)
        - "Run: depcon fix apply" command
    13. exit 1  (commit blocked)
```

---

## Target Service (`examples/fastapi-service/`)

The service is intentionally minimal. Its only job is to be a realistic OTel-instrumented
HTTP API that Dynatrace can observe. Lives under `examples/` so it doubles as a reference
implementation for users integrating Depcon into their own FastAPI services.

**Endpoints:**
- `GET /health` → `200 {"status":"ok"}`
- `POST /run` → core logic, returns `200` or `400/500`
- `GET /metrics` → prometheus-format metrics (optional)

**Fault injection** via `CHAOS_MODE` env var (set in docker-compose.yml for tests):
- `off` — normal operation
- `latency` — `asyncio.sleep(0.8)` on every request
- `error` — return `500` on 50% of requests
- `panic` — raise unhandled exception after 3 requests (tests crash recovery)

**OTel setup:**
- Exporter: OTLP/HTTP → `DT_OTLP_ENDPOINT`
- `otel.service.name` = `depcon-target` (must match `depcon.toml`)
- Spans on every request: method, path, status_code, latency_ms, error (bool)
- Structured log output via `opentelemetry-sdk` logging handler

---

## Dynatrace MCP Tools (`depcon/tools/dynatrace.py`)

All tools are thin wrappers around the Dynatrace MCP server. The MCP server runs via:
```bash
npx -y @dynatrace-oss/dynatrace-mcp-server
```
configured with `DT_ENVIRONMENT_ID` and `DT_API_TOKEN`.

**Tools:**

```python
get_problems(since: datetime) -> list[Problem]
# → Root Cause Agent: active problems opened after `since`

get_problem_detail(problem_id: str) -> ProblemDetail
# → Root Cause Details Agent: full detail on one problem

query_logs(time_window: TimeWindow, service: str) -> list[LogRecord]
# → Data Analysis Agent, DQL:
#   fetch logs, timeframe:"<start>/<end>"
#   | filter dt.entity.service == "<service>"
#   | filter loglevel in ("ERROR","WARN")
#   | sort timestamp desc | limit 50

query_traces(time_window: TimeWindow, service: str) -> list[Span]
# → Data Analysis Agent, DQL:
#   fetch spans, timeframe:"<start>/<end>"
#   | filter service.name == "<service>"
#   | filter status == "error" OR duration > <threshold>ms
#   | sort duration desc | limit 20

natural_language_query(prompt: str, time_window: TimeWindow) -> QueryResult
# → Grail Query Agent (generates DQL) → Data Analysis Agent (executes it)
# Fallback when the above don't surface the root cause

get_troubleshooting_guides(description: str) -> list[Guide]
# → Troubleshooting Agent: Dynatrace's own playbooks for this problem type
```

**TimeWindow** is always scoped to the smoke test run. Never query the full tenant history.

---

## Agent Loop (`depcon/agent.py`)

Uses Google ADK. Agent is defined as an `Agent` with the Dynatrace tools registered.

```python
from google.adk.agents import Agent

depcon_agent = Agent(
    name="depcon",
    model="gemini-2.0-flash",
    description="Diagnoses service failures from OTel telemetry in Dynatrace.",
    instruction=SYSTEM_PROMPT,  # see below
    tools=[
        get_problems,
        get_problem_detail,
        query_logs,
        query_traces,
        natural_language_query,
        get_troubleshooting_guides,
    ],
)
```

**System prompt key points:**
- You are diagnosing a service that failed smoke tests during a pre-commit check
- You have access to telemetry from Dynatrace for the exact test window provided
- Always start with `get_problems`, then drill down
- Map your findings back to the staged diff provided — which file/function is implicated
- Output MUST be structured JSON matching the `Diagnosis` schema
- Max iterations: 5. If confidence is still low after 5, output what you have

**Diagnosis schema:**
```python
class Diagnosis(BaseModel):
    hypothesis: str
    confidence: Literal["high", "medium", "low"]
    evidence: list[Evidence]      # {tool_name, finding, relevant_snippet}
    affected_file: str | None     # file from staged diff implicated
    affected_function: str | None
    error_class: str              # e.g. "NullPointerException", "timeout", "panic"
    fix_description: str          # plain English, one paragraph
    fix_diff: str | None          # unified diff, None if not applicable
```

---

## Fix Application (`depcon/fix.py`)

```python
def apply_fix(diff: str) -> ApplyResult:
    # 1. Write diff to temp file
    # 2. Run: git apply --check <tmpfile>  (dry run first)
    # 3. If check passes: git apply <tmpfile>
    # 4. Return: {success: bool, applied_files: list[str], error: str | None}
```

`depcon fix apply` re-reads the last saved session from `.depcon/sessions/latest.json`
and re-runs `apply_fix` on the stored diff. Idempotent.

On blocked commit, stderr always ends with:
```
─────────────────────────────────────────
  To apply the suggested fix:

    depcon fix apply

  Or manually review and apply:

    git apply .depcon/sessions/latest.diff

─────────────────────────────────────────
```

---

## TUI (`depcon/tui.py`)

Activated with `depcon run --watch`. Textual app, 3-panel layout.

```
┌─────────────────┬──────────────────────────┬─────────────────────┐
│ SERVICE STATUS  │   AGENT REASONING STREAM  │  DIAGNOSIS & FIX    │
│                 │                           │                     │
│ ● Starting...   │  [iter 1] get_problems()  │  Hypothesis:        │
│ ● Smoke tests   │    → 1 problem found      │  ...                │
│   pass: 3/4     │  [iter 2] get_problem_    │                     │
│   fail: 1/4     │    detail(P-12345)        │  Confidence: HIGH   │
│                 │    → OOM in /run handler  │                     │
│ CHAOS: off      │  [iter 3] query_logs()    │  Fix:               │
│                 │    → 3 ERROR logs found   │  <diff preview>     │
│                 │  [iter 4] query_traces()  │                     │
│                 │    → 2 error spans        │  > depcon fix     │
│                 │                           │    apply            │
└─────────────────┴──────────────────────────┴─────────────────────┘
  [q] quit   [a] apply fix   [r] re-run   [s] save session
```

Severity colors: CRITICAL=red, HIGH=orange, MEDIUM=yellow, INFO=blue, OK=green.

---

## Pre-commit Integration

**`.pre-commit-hooks.yaml`** (repo root):
```yaml
- id: depcon
  name: Depcon — runtime health check
  description: Spins up service, smoke tests, diagnoses via Dynatrace+ADK
  entry: depcon run
  language: python
  pass_filenames: false
  always_run: false
  stages: [pre-commit]
```

**User's `.pre-commit-config.yaml`**:
```yaml
repos:
  - repo: https://github.com/<org>/depcon
    rev: v0.1.0
    hooks:
      - id: depcon
```

**`depcon/hook.py`** — wraps `depcon run`, ensures stdout/stderr format is
compatible with pre-commit's output capture. Non-zero exit blocks commit.

Skip in CI: `DEPCON_SKIP=1 git commit -m "..."` or standard `--no-verify`.

---

## Session Storage

Each run writes to `.depcon/sessions/<timestamp>/`:
```
<timestamp>/
├── context.json      # staged diff + smoke summary + test_window
├── diagnosis.json    # full Diagnosis object
├── latest.diff       # fix diff (if generated)
└── iterations.jsonl  # one line per agent iteration (tool, input, output)
```

`.depcon/sessions/latest.json` → symlink to most recent session dir.

`.depcon/` should be in `.gitignore`.

---

## Key Constraints

- **Total run time target: under 90 seconds.** Service startup ≤15s, smoke tests ≤30s,
  agent loop ≤45s. If any phase exceeds budget, depcon exits with a timeout error
  and does NOT block the commit (fail open, not fail closed).
- **Fail open on Dynatrace unavailability.** If the MCP server is unreachable or
  credentials are missing, depcon warns but exits 0. Never block a commit because
  observability is down.
- **Fail open on agent errors.** If the ADK loop throws, log the error to
  `.depcon/sessions/latest/error.log` and exit 0.
- **Only block on high/medium confidence diagnoses.** Low confidence → warn, exit 0.
  Threshold configurable in `depcon.toml`.
- **Never mutate staged files without explicit `depcon fix apply`.** Auto-apply
  in F4.5 only writes to working tree files not in the index. The staged commit
  is never touched.

---

## Commands Reference

```bash
depcon run                  # full validation cycle (what pre-commit calls)
depcon run --watch          # same but TUI mode
depcon run --chaos latency  # override CHAOS_MODE before starting service
depcon fix apply            # apply fix from last session
depcon config init          # scaffold depcon.toml in current repo
depcon session list         # list saved sessions
depcon session show <ts>    # print diagnosis from a past session
depcon replay <ts>          # re-run agent on saved telemetry (no service start)
```

---

## Development Setup

```bash
# Clone and install
git clone https://github.com/<org>/depcon
cd depcon
uv sync                        # creates .venv + installs all deps from uv.lock
source .venv/bin/activate      # or: use `uv run <cmd>` to skip manual activation

# Copy and fill env vars
cp .env.example .env

# Start the FastAPI service manually for testing
cd examples/fastapi-service && docker compose up --build

# Run depcon against the already-running service
depcon run

# Run with TUI
depcon run --watch

# Run with fault injection
depcon run --chaos error
```

---

## What the Demo Shows

1. Repo with depcon wired in pre-commit
2. Developer makes a change that introduces a bug → `git commit`
3. Terminal shows: service starting → smoke tests running → agent loop streaming
4. Commit blocked: hypothesis printed, fix diff shown, `depcon fix apply` command
5. Developer runs `depcon fix apply` → patch applied
6. `git commit` again → depcon clears → commit goes through
7. Optional: `depcon run --watch` to show the TUI version of the same flow