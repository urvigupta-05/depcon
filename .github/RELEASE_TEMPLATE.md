<div align="center">

# ⬡ Depcon

**AI-powered pre-commit validation — catch broken services before the commit lands**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org)
[![Gemini](https://img.shields.io/badge/Gemini-2.0_Flash-4285F4?style=flat-square&logo=google&logoColor=white)](https://ai.google.dev)
[![Dynatrace](https://img.shields.io/badge/Dynatrace-MCP_Server-00B4FF?style=flat-square&logoColor=white)](https://dynatrace.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e?style=flat-square)](LICENSE)

</div>

---

## 🚀 What is Depcon?

Depcon is a **pre-commit hook** that spins up your service in Docker, runs smoke tests against it, and uses a **Gemini AI agent** to diagnose failures via the **Dynatrace MCP Server** — blocking bad commits with a root cause and a one-command fix.

```
git commit
    ↓
Docker starts your service  →  Smoke tests run
    ↓ (on failure)
Gemini + Dynatrace MCP diagnose the root cause
    ↓
✗ Commit blocked  —  depcon fix apply  —  git commit ✓
```

---

## 📦 Installation

> Requires Python 3.11+ and Node.js 18+ (for the Dynatrace MCP server)

**Option A — pip**
```bash
pip install git+https://github.com/urvigupta-05/depcon
```

**Option B — uv tool** *(isolated environment, recommended)*
```bash
uv tool install git+https://github.com/urvigupta-05/depcon
```

**Option C — wheel from this release**

Download `depcon-0.1.0-py3-none-any.whl` from the assets below ↓ then:
```bash
pip install depcon-0.1.0-py3-none-any.whl
```

Verify the install:
```bash
depcon --help
```

---

## ⚡ Quick Start

**1. Configure credentials**
```bash
# In the repo you want to protect:
cp /path/to/depcon/.env.example .env
# Fill in: DT_ENVIRONMENT, DT_API_TOKEN, DT_OTLP_ENDPOINT, GEMINI_API_KEY
```

**2. Create `depcon.toml`**
```bash
depcon config init
```

**3. Wire the pre-commit hook**

```bash
# Mac / Linux
echo '#!/bin/sh
depcon run' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```
```powershell
# Windows PowerShell
"#!/bin/sh`ndepcon run" | Set-Content .git/hooks/pre-commit
```

**4. Commit — Depcon runs automatically**
```bash
git commit -m "my change"
# → Depcon starts Docker, runs smoke tests, calls Gemini if tests fail
```

**5. Apply the suggested fix**
```bash
depcon fix apply   # patches the file from the last session
git add .
git commit         # ✓ passes
```

---

## 🛠️ Commands

| Command | Description |
|---------|-------------|
| `depcon run` | Full validation cycle (what the hook calls) |
| `depcon run --chaos error` | Force 50% failures — useful for testing |
| `depcon run --chaos panic` | Crash after 3rd request |
| `depcon run --web` | Open browser dashboard with live AI stream |
| `depcon fix apply` | Apply the AI-suggested fix from the last session |
| `depcon session list` | List past diagnosis sessions |

---

## 🔧 Requirements

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | [python.org](https://python.org) |
| Docker Desktop | [docker.com](https://www.docker.com/products/docker-desktop) — must be running |
| Node.js 18+ | [nodejs.org](https://nodejs.org) — for Dynatrace MCP server via `npx` |
| Dynatrace tenant | Free trial at [dynatrace.com/signup](https://www.dynatrace.com/signup/) |
| Gemini API key | Free at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — starts with `AIza` |

---

## 🏗️ Built With

| Component | Technology |
|-----------|------------|
| AI Agent | **Google Cloud Agent Builder** (ADK) + **Gemini 2.0 Flash** |
| Observability | **Dynatrace MCP Server** (`@dynatrace-oss/dynatrace-mcp-server`) |
| Telemetry | OpenTelemetry → Dynatrace OTLP |
| Target service | FastAPI + `opentelemetry-instrumentation-fastapi` |
| Container runtime | Docker Compose |
| CLI | Python · Typer · Rich |

---

## 📚 Documentation

Full setup guide, architecture details, and contributing instructions are in the [README](https://github.com/urvigupta-05/depcon#readme).

---

<div align="center">

Made for the **Google Cloud Rapid Agent Hackathon** — Dynatrace Track

</div>
