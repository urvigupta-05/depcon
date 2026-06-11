# Depcon — AI-Powered Pre-commit Validation

> Depcon catches broken services **before the commit lands**. It spins up your service in Docker,
> runs smoke tests, queries Dynatrace for what went wrong, and uses a Gemini AI agent to
> diagnose the failure and generate a one-command fix.

📹 **[Demo Video](https://youtu.be/Gln33jzrUg8)** &nbsp;·&nbsp; 📦 **[Download Binary (GitHub Releases)](https://github.com/urvigupta-05/depcon/releases/latest)**

---

## The Problem

Pre-commit hooks today run linters and type checkers — they check your **code**, not your
**running service**. Bugs that only appear at runtime (HTTP 500s, panics, latency regressions)
sail straight through and land in production.

Depcon shifts that validation left: every `git commit` triggers a real smoke test against a
live Docker container, backed by Dynatrace observability and a Gemini AI agent that diagnoses
failures against actual telemetry data — not guesswork.

---

## How It Works

```
git commit -m "add feature"
      │
      ▼
[Depcon pre-commit hook]
      │
      ├─► Docker spins up your service fresh
      ├─► Smoke tests hit /health and /run
      ├─► If tests fail → ADK agent loop begins
      │       ├─ Dynatrace MCP: get_problems()
      │       ├─ Dynatrace MCP: query_logs()
      │       └─ Dynatrace MCP: query_traces()
      │
      └─► Commit blocked with root cause + fix diff
              ↓
          depcon fix apply   ← one command patches the file
              ↓
          git commit         ← passes ✓
```

---

## Built With

| Component | Technology |
|-----------|-----------|
| AI Agent | **Google Cloud Agent Builder** (ADK) + **Gemini 2.0 Flash** |
| Observability | **Dynatrace MCP Server** (`@dynatrace-oss/dynatrace-mcp-server`) |
| Telemetry | **OpenTelemetry** → Dynatrace OTLP endpoint |
| Target service | FastAPI + `opentelemetry-instrumentation-fastapi` |
| Container runtime | Docker Compose |
| CLI | Python 3.11+ · Typer · Rich |

---

## Quick Start

### 1. Install

**Option A — pip (requires Python 3.11+):**
```bash
pip install git+https://github.com/urvigupta-05/depcon
depcon --help
```

**Option B — uv tool (isolated environment, no pip needed):**
```bash
uv tool install git+https://github.com/urvigupta-05/depcon
depcon --help
```

**Option C — wheel from [GitHub Releases](https://github.com/urvigupta-05/depcon/releases/latest):**
```bash
pip install https://github.com/urvigupta-05/depcon/releases/latest/download/depcon-0.1.0-py3-none-any.whl
depcon --help
```

> **Note:** Depcon requires **Node.js 18+** on your PATH for the Dynatrace MCP server
> (`npx @dynatrace-oss/dynatrace-mcp-server`). Install from [nodejs.org](https://nodejs.org/).

---

### 2. Configure environment variables

Copy `.env.example` to `.env` in the repo you want to protect:

```bash
cp .env.example .env
```

```bash
# Dynatrace — sign up free at dynatrace.com/signup
DT_ENVIRONMENT=https://<your-env-id>.apps.dynatrace.com
DT_API_TOKEN=dt0c01.xxxxx          # scopes: logs.read, traces.read, problems.read
DT_OTLP_ENDPOINT=https://<env-id>.live.dynatrace.com/api/v2/otlp

# Gemini — free key from aistudio.google.com/apikey (starts with AIza...)
GEMINI_API_KEY=AIza...
```

| Variable | Description |
|----------|-------------|
| `DT_ENVIRONMENT` | Full Dynatrace tenant URL |
| `DT_API_TOKEN` | API token with `storage:logs:read`, `storage:traces:read`, `problems.read` |
| `DT_OTLP_ENDPOINT` | OTLP ingest endpoint for the target service |
| `GEMINI_API_KEY` | Gemini API key (Google AI Studio — free tier, starts with `AIza`) |
| `DEPCON_SKIP` | Set to `1` to bypass in CI |

---

### 3. Create depcon.toml

```bash
depcon config init
```

Edit the generated file to match your service:

```toml
[service]
compose_file    = "examples/fastapi-service/docker-compose.yml"
health_endpoint = "http://localhost:8080/health"
run_endpoint    = "http://localhost:8080/run"

[smoke]
cases = [
  { name = "happy_path", body = '{"input": "hello"}', expect_status = 200 },
  { name = "empty_input", body = '{"input": ""}',     expect_status = 400 },
]

[dynatrace]
service_name = "depcon-target"   # must match otel.service.name in your service
```

---

### 4. Wire the git hook

```bash
# Mac / Linux
echo '#!/bin/sh
depcon run' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

```powershell
# Windows PowerShell
"#!/bin/sh`ndepcon run" | Set-Content .git/hooks/pre-commit
```

Bypass when needed: `DEPCON_SKIP=1 git commit` or `git commit --no-verify`.

---

### 5. Commands

```bash
depcon run                  # full validation (what the hook calls)
depcon run --chaos error    # force 50% failures for testing
depcon run --chaos panic    # crash after 3rd request
depcon run --web            # open browser dashboard (live SSE stream)
depcon fix apply            # apply the last AI-suggested fix
depcon session list         # list past diagnosis sessions
```

---

## Demo Output

```
  ⬡ depcon  ·  Pre-commit Service Validation

  ✗  happy_path      HTTP 500 → 200  (47ms)
  ✓  empty_input     HTTP 400        (32ms)
  ✗  large_payload   HTTP 500 → 200  (61ms)

  1 passed  ·  2 failed

  ⠋ AI agent analysing failure…

  ┌─ ✗  Commit Blocked ──────────────────────────────────────────┐
  │  Root cause   CHAOS_MODE=panic triggers crash after 3 requests │
  │  Confidence   HIGH  ·  Error class  RuntimeError               │
  │  Location     main.py → run()                                  │
  └────────────────────────────────────────────────────────────────┘

  ─────────────────────────────────────────────
  depcon fix apply   ←  apply this fix automatically
  ─────────────────────────────────────────────
```

```bash
depcon fix apply   # patches the file
git add .
git commit         # ✓ passes
```

---

## Example Target Service

`examples/fastapi-service/` is a FastAPI service with full OpenTelemetry instrumentation
that ships traces and logs to Dynatrace. Supports `CHAOS_MODE` fault injection:

| `CHAOS_MODE` | Behaviour |
|-------------|-----------|
| `off` | Normal operation |
| `error` | 50% of requests return HTTP 500 |
| `panic` | Crash after 3rd request |
| `latency` | 800ms delay per request |

Run without Docker:
```bash
cd examples/fastapi-service
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for full setup instructions (Windows + Mac).

---

## License

MIT © 2026 Depcon — see [LICENSE](LICENSE).
