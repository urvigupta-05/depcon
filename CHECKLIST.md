# Depcon — Build Checklist

## Critical Path (must ship)

- [x] **Repo + env setup** — public GitHub repo, MIT license at root, `pyproject.toml` with deps (`typer`, `textual`, `httpx`, `python-dotenv`, `google-adk`, `mcp`, `pydantic`), `uv.lock` committed, `.env.example`, `.gitignore`. Install uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`), then `uv sync` to get a working env. Everyone gets Dynatrace free trial credentials + Gemini API key before touching code. Run everything via `uv run depcon <cmd>` or activate with `source .venv/bin/activate`.

- [ ] **FastAPI target service** (`examples/fastapi-service/`) — `GET /health`, `POST /run` with basic input validation. OTel instrumented (`opentelemetry-sdk`, `otel.service.name=depcon-target`, one span per request with status + latency + error bool, logs via OTel logging handler), OTLP exporting to Dynatrace. `CHAOS_MODE` env var: `off|latency|error|panic`. `Dockerfile` + `docker-compose.yml`. Verify traces + error spans appear in Dynatrace tenant before moving on.

- [ ] **Smoke test runner** (`depcon/smoketest.py`) — `docker compose up --build`, poll `/health` with backoff (15s timeout), fire test cases from `depcon.toml` against `/run`, record `test_window.start` and `test_window.end`, `docker compose down`. Returns `SmokeResult` with pass/fail counts. Config loader (`depcon/config.py`) reads `depcon.toml`.

- [ ] **Dynatrace MCP tools** (`depcon/tools/dynatrace.py`) — connect to `npx @dynatrace-oss/dynatrace-mcp-server` via stdio MCP client. Implement: `get_problems(since)`, `get_problem_detail(id)`, `query_logs(time_window, service)` via DQL `fetch logs, timeframe:"<start>/<end>" | filter ...`, `query_traces(time_window, service)` via DQL for error spans, `natural_language_query(prompt, time_window)` as fallback. All queries scoped to `test_window` only. Fail open if MCP unreachable.

- [ ] **Agent loop** (`depcon/agent.py`) — ADK `Agent` with Gemini 2.0 Flash, all Dynatrace tools registered. Context fed in: `git diff --staged` + smoke summary + `test_window`. Runs max 5 iterations. Outputs structured `Diagnosis`: `{hypothesis, confidence: high|medium|low, evidence[], affected_file, fix_description, fix_diff (unified diff)}`. Prompt must instruct: map findings back to staged diff, output valid JSON matching schema.

- [ ] **Fix apply + CLI** (`depcon/fix.py`, `depcon/cli.py`) — `apply_fix(diff)` does `git apply --check` then `git apply`. Save session to `.depcon/sessions/<ts>/`. CLI: `depcon run` (full cycle, exit 0/1), `depcon fix apply` (re-apply from last session). On exit 1 stderr: hypothesis, confidence, diff inline, then prominent `depcon fix apply` command at the bottom. Nothing else.

- [ ] **Pre-commit hook** (`.pre-commit-hooks.yaml`, `depcon/hook.py`) — registers depcon as a pre-commit hook. `hook.py` is a thin wrapper calling `depcon run`, exit code passes through. `DEPCON_SKIP=1` bypasses. Test in a dummy repo: chaos on → commit blocked, chaos off → commit passes, `--no-verify` bypasses.

---

## Second Priority (ship if time allows)

- [ ] **TUI** (`depcon/tui.py`) — `depcon run --watch`. Textual, 3 panels: service status + smoke results (left), agent tool call stream live (center), diagnosis + fix diff preview (right). Bottom bar: `[a]` apply fix, `[r]` re-run, `[q]` quit. Severity colors matching Dynatrace conventions.

- [ ] **README + demo** — quick start (install → configure → `pre-commit install`), architecture in ASCII, env var reference. Demo GIF or recorded video showing: commit blocked → `depcon fix apply` → commit passes. This is what judges watch.
