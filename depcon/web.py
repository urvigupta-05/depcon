from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Depcon Web")

# ── SSE stream ────────────────────────────────────────────────────────────────

def _event(kind: str, data: dict) -> str:
    payload = json.dumps({"type": kind, **data})
    return f"data: {payload}\n\n"


async def _run_validation(chaos: str):
    from depcon.config import load_config
    from depcon.smoketest import run_smoke_tests
    from depcon.agent import run_agent
    from depcon.fix import save_session

    try:
        config = load_config()
    except FileNotFoundError as e:
        yield _event("error", {"message": str(e)})
        return

    staged_diff = ""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "diff", "--staged"],
            capture_output=True, text=True, check=True
        )
        staged_diff = result.stdout
    except Exception:
        pass

    # Step 1 — smoke tests
    yield _event("status", {"panel": "service", "message": "Starting service and running tests..."})

    try:
        smoke = await run_smoke_tests(config, chaos_mode=chaos)
    except Exception as e:
        yield _event("error", {"message": f"Smoke tests failed: {e}"})
        return

    # Send smoke results
    cases = [
        {
            "name": c.name,
            "passed": c.passed,
            "status_code": c.status_code,
            "expected_status": c.expected_status,
            "latency_ms": round(c.latency_ms),
            "error": c.error,
        }
        for c in smoke.cases
    ]
    yield _event("smoke", {
        "passed": smoke.passed,
        "failed": smoke.failed,
        "total": smoke.total,
        "all_passed": smoke.all_passed,
        "cases": cases,
    })

    if smoke.all_passed:
        yield _event("all_passed", {"message": "All tests passed. This commit is clear to proceed."})
        yield _event("done", {})
        return

    # Step 2 — agent
    yield _event("status", {"panel": "agent", "message": f"{smoke.failed} test(s) failed. Running agent diagnosis..."})

    try:
        diagnosis = await run_agent(config, staged_diff, smoke)
    except Exception as e:
        yield _event("agent_failed", {"message": str(e)})
        return

    if diagnosis is None:
        yield _event("agent_failed", {"message": "Agent returned no result. This may be a quota or connectivity issue."})
        yield _event("done", {})
        return

    # Send evidence
    for i, ev in enumerate(diagnosis.evidence, 1):
        yield _event("evidence", {
            "step": i,
            "tool": ev.tool_name,
            "finding": ev.finding,
            "snippet": ev.relevant_snippet,
        })
        await asyncio.sleep(0.1)

    # Save session
    context = {
        "staged_diff": staged_diff,
        "smoke_summary": smoke.summary(),
        "test_window": smoke.test_window.model_dump(),
    }
    try:
        save_session(config.output.sessions_dir, context, diagnosis)
    except Exception:
        pass

    # Send diagnosis then signal stream end
    yield _event("diagnosis", {
        "hypothesis": diagnosis.hypothesis,
        "confidence": diagnosis.confidence,
        "error_class": diagnosis.error_class,
        "affected_file": diagnosis.affected_file,
        "affected_function": diagnosis.affected_function,
        "fix_description": diagnosis.fix_description,
        "fix_diff": diagnosis.fix_diff,
        "evidence": [
            {"tool": e.tool_name, "finding": e.finding}
            for e in diagnosis.evidence
        ],
    })
    yield _event("done", {})


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/run")
async def run_stream(chaos: str = ""):
    async def stream():
        async for chunk in _run_validation(chaos):
            yield chunk
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/fix")
async def apply_fix_route():
    from depcon.fix import load_last_diagnosis, apply_fix
    try:
        config = load_config()
        sessions_dir = config.output.sessions_dir
    except Exception:
        sessions_dir = ".depcon/sessions"

    diagnosis = load_last_diagnosis(sessions_dir)
    if diagnosis is None or not diagnosis.fix_diff:
        return {"success": False, "error": "No fix available"}

    result = apply_fix(diagnosis.fix_diff)
    return {
        "success": result.success,
        "files": result.applied_files,
        "error": result.error,
    }
