# Depcon — Team Context Doc

> Read this before touching any code. 15 minute read, saves hours of confusion.

---

## What Are We Building?

A command-line tool called **Depcon** that plugs into your git workflow.

When you run `git commit`, before the commit actually goes through, Depcon:

1. Spins up your service in Docker
2. Fires test requests at it
3. Watches what Dynatrace recorded during those tests
4. Uses an AI agent to figure out what went wrong
5. Either lets the commit through, or **blocks it** and tells you exactly what broke and how to fix it

Think of it like a linter — except instead of checking code style, it checks if your service actually works at runtime, using real observability data.

**The demo flow:**
```
git commit -m "add new feature"
  ↓
[Depcon] Starting service...
[Depcon] Running smoke tests... 3/4 passed
[Depcon] Querying Dynatrace for errors...
[Depcon] Agent diagnosing...
  ↓
✗ Commit blocked.

  Root cause: Unhandled exception in handlers/run.py:47
  Confidence: HIGH
  Evidence: 3 error spans, 2 ERROR logs in test window

  Fix: depcon fix apply
```

Then you run `depcon fix apply`, it patches the file, you commit again, it passes.

---

## Why Is This Interesting?

Most pre-commit hooks do **static** checks — linting, formatting, type errors. They look at your code without running it.

Depcon does something nobody else does: it actually **runs your code**, generates real telemetry, and uses an AI agent to diagnose failures against live observability data. Observability-backed validation at commit time. The industry term for this is "shifting left" — catching problems earlier in the dev cycle. We're shifting it all the way to before the commit exists.

For the hackathon specifically: the Dynatrace track judges want to see Dynatrace as the "brain" of the system, not just a dashboard you screenshot. In Depcon, Dynatrace is literally what the AI agent queries to figure out what went wrong. Without Dynatrace, the agent is blind. That's the story.

---

## How The Pieces Fit Together

```
git commit
    │
    ▼
[pre-commit hook]
    │  fires depcon
    ▼
[Depcon CLI]
    │
    ├─► [Docker] starts FastAPI service fresh (examples/fastapi-service/)
    │
    ├─► [Smoke tests] hits /health, /run with test cases
    │       records test_window.start → test_window.end
    │
    ├─► if all pass → exit 0, commit goes through ✓
    │
    └─► if failures →
            │
            ▼
        [ADK Agent Loop] ── talks to ──► [Dynatrace MCP Server]
            │                                   │
            │  iteration 1: get_problems()       │ queries your Dynatrace tenant
            │  iteration 2: get_problem_detail() │ for what happened during
            │  iteration 3: query_logs()         │ the smoke test window
            │  iteration 4: query_traces()       │
            │  iteration 5: (if needed)          │
            │
            ▼
        [Diagnosis] hypothesis + confidence + evidence + fix diff
            │
            ├─► auto-applies fix to working tree
            ├─► prints result to terminal
            └─► exit 1, commit blocked ✗
```

---

## The Three External Services We Depend On

### 1. Dynatrace
What it is: observability platform. Stores logs, traces, and metrics from your running service.

What we use it for: after the smoke tests run, the agent queries Dynatrace to find out what actually happened — error rates, crash logs, slow spans, anomalies. This is the data source that makes the AI diagnosis grounded in reality instead of guessing.

How it connects: your FastAPI service sends telemetry to Dynatrace via OpenTelemetry (OTel). OTel is the standard protocol for this — you instrument the code once and it ships data to whatever backend supports it. Dynatrace supports it natively.

What you need: a free trial account at dynatrace.com/signup. You get an `environment ID` (looks like `abc12345`) and an `API token`. Both go in `.env`.

The MCP server is how the AI agent talks to Dynatrace. It's a Node package (`@dynatrace-oss/dynatrace-mcp-server`) that translates between the AI's tool calls and Dynatrace's APIs. It runs as a local process alongside the agent.

Dynatrace docs for this project:
- MCP server: https://github.com/dynatrace-oss/dynatrace-mcp
- DQL (their query language): https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/dql-guide
- OTel ingestion: https://docs.dynatrace.com/docs/extend-dynatrace/opentelemetry

### 2. Google ADK + Gemini
What it is: Google's Agent Development Kit. A Python framework for building AI agents that can use tools in a loop.

What we use it for: the diagnosis loop. ADK manages the back-and-forth between Gemini (the LLM doing the reasoning) and the Dynatrace tools (which fetch real data). You define the tools, write a system prompt, and ADK handles the iteration.

How it works in practice:
```python
from google.adk.agents import Agent

agent = Agent(
    name="depcon",
    model="gemini-2.0-flash",
    instruction="You diagnose service failures using Dynatrace telemetry...",
    tools=[get_problems, query_logs, query_traces, ...]
)
# ADK runs the loop: Gemini decides which tool to call,
# calls it, gets the result, decides what to call next, repeat
```

What you need: either a Google Cloud project with Vertex AI enabled, or a Gemini API key from aistudio.google.com. API key is simpler for a 1-day build.

ADK docs: https://google.github.io/adk-docs/

### 3. Docker / Docker Compose
What it is: containerization. We use it to run the FastAPI service in a clean, reproducible environment.

What we use it for: every commit attempt starts the service fresh (`docker compose up --build`) and tears it down after (`docker compose down`). This ensures every validation is against a clean state, not a service that's been running for hours with accumulated state.

What you need: Docker Desktop or OrbStack (on Mac, OrbStack is faster and lighter).

---

## The Target Service (`examples/fastapi-service/`)

This is the "target" — the thing Depcon validates. It's intentionally simple, and lives under `examples/` so it also serves as a reference for how to instrument your own FastAPI service for use with Depcon.

**Endpoints:**
- `GET /health` — returns `{"status":"ok"}`. Depcon polls this until it's up.
- `POST /run` — the main endpoint. Accepts `{"input":"..."}`, does some work, returns a result. This is what smoke tests hit.

**Fault injection** via `CHAOS_MODE` environment variable:
- `CHAOS_MODE=off` — normal operation
- `CHAOS_MODE=latency` — adds 800ms sleep to every request (triggers latency alerts)
- `CHAOS_MODE=error` — 50% of requests return 500 (triggers error rate alerts)
- `CHAOS_MODE=panic` — raises unhandled exception after 3 requests (triggers crash alerts)

We set this in `docker-compose.yml` to simulate different failure modes for the demo.

**OpenTelemetry instrumentation:**
Every request creates a trace span with: HTTP method, path, status code, duration, error flag. Logs are captured via the OTel logging handler so they also appear in Dynatrace. The service name is `depcon-target` — this is how Dynatrace and our DQL queries identify which service's data to look at.

Reference for Python OTel: https://opentelemetry.io/docs/languages/python/getting-started/

---

## The Agent Loop In Detail

The agent receives three things as context before it starts:
1. `git diff --staged` — exactly what code changed in this commit
2. Smoke test summary — which tests failed, what status codes came back, latencies
3. `test_window` — the exact timestamps (`start`, `end`) of when the smoke tests ran

The time window is critical. Dynatrace stores data for your whole tenant. We only want to query what happened during our 30-second test run, not the last 2 hours. Every DQL query is scoped to this window:
```
fetch logs, timeframe:"2026-05-30T10:00:00Z/2026-05-30T10:00:45Z"
| filter dt.entity.service == "depcon-target"
| filter loglevel in ("ERROR", "WARN")
```

The agent iterates up to 5 times. Typical path:
- Iter 1: "are there any problems?" → yes, problem P-12345
- Iter 2: "give me details on P-12345" → OOM in /run handler
- Iter 3: "show me error logs from the window" → 3 ERROR logs with stack traces
- Iter 4: "show me the failing traces" → 2 spans with status=error, both in `handlers/run.py`
- Agent synthesises: hypothesis, maps to staged diff file, generates fix diff

The agent is instructed to output structured JSON. We parse that into a `Diagnosis` object.

---

## Configuration (`depcon.toml`)

Every repo that uses Depcon needs this file. Generated by `depcon config init`.

```toml
[service]
compose_file = "examples/fastapi-service/docker-compose.yml"
health_endpoint = "http://localhost:8080/health"
startup_timeout_secs = 15
run_endpoint = "http://localhost:8080/run"

[smoke]
cases = [
  { name = "happy_path",  body = '{"input": "hello"}', expect_status = 200 },
  { name = "empty_input", body = '{"input": ""}',      expect_status = 400 },
]

[dynatrace]
service_name = "depcon-target"
error_rate_threshold = 0.05
latency_p99_threshold_ms = 500

[agent]
max_iterations = 5
model = "gemini-2.0-flash"
```

---

## Environment Variables (`.env`)

```bash
# Dynatrace — get from your tenant
DT_ENVIRONMENT_ID=abc12345
DT_API_TOKEN=dt0c01.xxxxx
DT_OTLP_ENDPOINT=https://abc12345.live.dynatrace.com/api/v2/otlp

# Gemini — from aistudio.google.com OR Google Cloud
GEMINI_API_KEY=AIza...
# GOOGLE_CLOUD_PROJECT=your-project   # if using Vertex AI instead

# Depcon behaviour
DEPCON_SKIP=1                        # set this in CI to bypass the hook
DEPCON_TIMEOUT=120                   # max seconds before fail-open
DEPCON_CONFIDENCE_THRESHOLD=medium   # only block at: low | medium | high
```

Copy `.env.example` → `.env`. Never commit `.env`.

---

## Fail-Open Policy

Depcon never blocks a commit due to its own failure. Only blocks when it has a real diagnosis with sufficient confidence.

- Dynatrace unreachable → warn in terminal, exit 0 (commit goes through)
- Agent throws an error → log to `.depcon/sessions/latest/error.log`, exit 0
- Total run exceeds `DEPCON_TIMEOUT` → warn, exit 0
- Diagnosis confidence is `low` → warn, exit 0 (configurable threshold)

The mental model: Depcon is a helpful reviewer, not a gatekeeper. It should never be the reason you can't commit.

---

## File Layout (what gets created where)

```
your-repo/
├── depcon.toml          ← your config (committed)
├── .env                   ← secrets (never committed)
├── .pre-commit-config.yaml ← wires depcon in (committed)
├── .gitignore             ← includes .depcon/
│
└── .depcon/             ← runtime data (gitignored)
    └── sessions/
        ├── latest -> 2026-05-30T10-00-00/   ← symlink
        └── 2026-05-30T10-00-00/
            ├── context.json       staged diff + smoke summary + test_window
            ├── diagnosis.json     full Diagnosis object
            ├── latest.diff        the fix diff
            └── iterations.jsonl   one line per agent iteration
```

---

## Commands

```bash
# Main commands
depcon run              # full cycle — this is what pre-commit calls
depcon run --watch      # same but with TUI (pretty panels)
depcon run --chaos error # run with fault injection on
depcon fix apply        # apply the fix from the last session

# Utility
depcon config init      # create depcon.toml in current directory
depcon session list     # list past sessions
depcon session show <timestamp>  # print a past diagnosis
```

---

## Setting Up Pre-commit

In the repo you want to protect, add this to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/<your-org>/depcon
    rev: v0.1.0
    hooks:
      - id: depcon
```

Then:
```bash
uv tool install pre-commit   # or: pip install pre-commit
pre-commit install
```

Now every `git commit` runs Depcon first. To bypass: `git commit --no-verify` or set `DEPCON_SKIP=1`.

---

## Quick Links

| Resource | URL |
|----------|-----|
| Dynatrace free trial | https://www.dynatrace.com/signup/ |
| Dynatrace MCP server (GitHub) | https://github.com/dynatrace-oss/dynatrace-mcp |
| Dynatrace MCP server tools list | https://www.dynatrace.com/hub/detail/mcp-server-tools/ |
| DQL guide | https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/dql-guide |
| Google ADK docs | https://google.github.io/adk-docs/ |
| ADK Python quickstart | https://google.github.io/adk-docs/get-started/quickstart/ |
| Gemini API key | https://aistudio.google.com/apikey |
| Python OTel getting started | https://opentelemetry.io/docs/languages/python/getting-started/ |
| uv (package manager) | https://docs.astral.sh/uv/ |
| pre-commit framework | https://pre-commit.com |
| Textual (TUI framework) | https://textual.textualize.io |
| Hackathon page | https://rapid-agent.devpost.com |
| Dynatrace track resources | https://rapid-agent.devpost.com/details/dynatrace-resources |