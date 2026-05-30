# Contributing to Depcon

Everything you need to go from a fresh clone to a running `depcon run` command.

---

## Prerequisites

| Tool | Min version | Install |
|------|-------------|---------|
| Python | 3.11 | [python.org](https://www.python.org/downloads/) |
| uv | latest | see below |
| Docker Desktop | any recent | [docker.com](https://www.docker.com/products/docker-desktop/) |
| Node.js | 18+ | [nodejs.org](https://nodejs.org/) — needed for the Dynatrace MCP server |
| Git | any | [git-scm.com](https://git-scm.com/) |

> **Mac only:** OrbStack is a faster Docker Desktop alternative — `brew install orbstack`

---

## 1. Install uv

uv is the package and environment manager for this project (replaces pip + venv).

**Mac / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

After installing, restart your terminal and verify:
```bash
uv --version
```

---

## 2. Clone and install

```bash
git clone https://github.com/<org>/depcon
cd depcon
uv sync
```

`uv sync` reads `pyproject.toml` and `uv.lock`, creates `.venv/`, and installs all
dependencies — including dev extras. The lockfile is committed so everyone gets
identical versions.

---

## 3. Activate the environment

You have two options. Pick one and stick with it.

**Option A — activate once per shell session:**

Mac / Linux:
```bash
source .venv/bin/activate
depcon --help
```

Windows (PowerShell):
```powershell
.venv\Scripts\Activate.ps1
depcon --help
```

Windows (CMD):
```cmd
.venv\Scripts\activate.bat
depcon --help
```

**Option B — prefix every command with `uv run` (no activation needed):**
```bash
uv run depcon --help
uv run pytest
```

Option B is safer in scripts and CI because it never leaks into other projects.

---

## 4. Set up environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Variable | Where to get it |
|----------|----------------|
| `DT_ENVIRONMENT_ID` | Dynatrace free trial → Settings → Environment ID |
| `DT_API_TOKEN` | Dynatrace → Access Tokens → create with *Grail*, *problems.read*, *logs.read*, *traces.read* scopes |
| `DT_OTLP_ENDPOINT` | `https://<your-env-id>.live.dynatrace.com/api/v2/otlp` |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |

**Never commit `.env`** — it's in `.gitignore`.

---

## 5. Verify the setup

```bash
# Check the CLI entry point works
depcon --help          # or: uv run depcon --help

# Run the test suite
pytest                 # or: uv run pytest
```

---

## 6. Run the FastAPI example service (for local testing)

```bash
# From repo root
docker compose -f examples/fastapi-service/docker-compose.yml up --build
```

The service starts on `http://localhost:8080`. Hit `Ctrl+C` to stop, then:
```bash
docker compose -f examples/fastapi-service/docker-compose.yml down
```

To test a specific fault mode:
```bash
# Mac / Linux
CHAOS_MODE=error docker compose -f examples/fastapi-service/docker-compose.yml up --build

# Windows PowerShell
$env:CHAOS_MODE="error"; docker compose -f examples/fastapi-service/docker-compose.yml up --build
```

---

## 7. Run depcon against the example service

With the service already running (step 6 above):
```bash
depcon run

# Live TUI mode
depcon run --watch

# Force a failure scenario
depcon run --chaos error
```

---

## Project layout

```
depcon/
├── depcon/              # main Python package
│   ├── cli.py           # Typer CLI entrypoint
│   ├── config.py        # depcon.toml loader
│   ├── smoketest.py     # Docker + smoke test runner
│   ├── agent.py         # ADK + Gemini agent loop
│   ├── fix.py           # git apply logic
│   ├── hook.py          # pre-commit wrapper
│   ├── tui.py           # Textual TUI
│   └── tools/
│       └── dynatrace.py # Dynatrace MCP tool wrappers
├── examples/
│   └── fastapi-service/ # demo target service (FastAPI + OTel)
├── tests/               # pytest test suite
├── pyproject.toml       # dependencies + build config
├── uv.lock              # locked dependency versions (committed)
├── depcon.toml.example  # config template
└── .env.example         # env var template
```

---

## Adding or updating dependencies

```bash
# Add a runtime dep
uv add <package>

# Add a dev-only dep
uv add --dev <package>

# Remove a dep
uv remove <package>
```

Always commit both `pyproject.toml` and `uv.lock` together.

---

## Running tests

```bash
pytest                        # all tests
pytest tests/test_smoketest.py  # one file
pytest -k "test_health"         # by name pattern
pytest -v                       # verbose
```

---

## Code style

We use `ruff` for linting and formatting (configured in `pyproject.toml`).

```bash
# Check
uv run ruff check .

# Auto-fix
uv run ruff check --fix .

# Format
uv run ruff format .
```

---

## Module ownership (who's building what)

| Module | Owner |
|--------|-------|
| `examples/fastapi-service/` | groupmate |
| `depcon/config.py` + `depcon/smoketest.py` | TBD |
| `depcon/tools/dynatrace.py` | TBD |
| `depcon/agent.py` | TBD |
| `depcon/fix.py` + `depcon/cli.py` | TBD |
| `depcon/tui.py` | TBD |

---

## Common issues

**`depcon: command not found` after `uv sync`**
You need to activate the venv first (`source .venv/bin/activate` on Mac/Linux,
`.venv\Scripts\Activate.ps1` on Windows) or prefix commands with `uv run`.

**`uv sync` fails on Windows with a permission error**
Run PowerShell as Administrator for the first install, or check that your
execution policy allows scripts: `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser`.

**Docker not found**
Make sure Docker Desktop is running (it doesn't start automatically on reboot by default).

**Dynatrace MCP server not found**
The MCP server runs via `npx` — make sure Node.js 18+ is installed and `npx` is on your PATH.
Test with: `npx -y @dynatrace-oss/dynatrace-mcp-server --help`
