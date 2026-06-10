import asyncio
import logging
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import typer
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = typer.Typer(
    name="depcon",
    help="Pre-commit hook: validates your service with Dynatrace telemetry before every commit.",
    no_args_is_help=True,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_staged_diff() -> str:
    try:
        result = subprocess.run(
            ["git", "diff", "--staged"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except Exception:
        return ""


def _should_block(confidence: str, threshold: str) -> bool:
    levels = {"low": 0, "medium": 1, "high": 2}
    return levels.get(confidence, 0) >= levels.get(threshold, 1)


@contextmanager
def _timeout_guard(seconds: int):
    import signal

    if sys.platform == "win32" or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handler(signum, frame):
        raise TimeoutError(f"Depcon exceeded {seconds}s timeout")

    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _print_blocked(diagnosis, session_path: Path) -> None:
    lines = [
        "",
        "✗ Commit blocked.",
        "",
        f"  Root cause:  {diagnosis.hypothesis}",
        f"  Confidence:  {diagnosis.confidence.upper()}",
        f"  Error class: {diagnosis.error_class}",
    ]
    if diagnosis.affected_file:
        loc = diagnosis.affected_file
        if diagnosis.affected_function:
            loc += f":{diagnosis.affected_function}"
        lines.append(f"  Affected:    {loc}")
    lines += ["", "  Evidence:"]
    for ev in diagnosis.evidence:
        lines.append(f"    • [{ev.tool_name}] {ev.finding}")
        if ev.relevant_snippet:
            for snippet_line in ev.relevant_snippet.splitlines()[:3]:
                lines.append(f"        {snippet_line}")

    if diagnosis.fix_diff:
        lines += ["", "  Suggested fix:", ""]
        for diff_line in diagnosis.fix_diff.splitlines():
            lines.append(f"    {diff_line}")

    separator = "─" * 45
    lines += [
        "",
        separator,
        "  To apply the suggested fix:",
        "",
        "    depcon fix apply",
        "",
        "  Or manually:",
        "",
        f"    git apply {session_path / 'latest.diff'}",
        "",
        separator,
        "",
    ]
    print("\n".join(lines), file=sys.stderr)


# ── commands ─────────────────────────────────────────────────────────────────

@app.command()
def run(
    watch: bool = typer.Option(False, "--watch", help="Show live TUI"),
    web: bool = typer.Option(False, "--web", help="Open browser dashboard"),
    chaos: str = typer.Option("", "--chaos", help="Override CHAOS_MODE"),
) -> None:
    """Full validation cycle — what pre-commit calls."""
    load_dotenv()

    if os.getenv("DEPCON_SKIP"):
        typer.echo("✓ Depcon: skipped (DEPCON_SKIP set)")
        raise typer.Exit(0)

    from depcon.config import load_config
    from depcon.smoketest import run_smoke_tests
    from depcon.agent import run_agent
    from depcon.fix import apply_fix, save_session

    try:
        config = load_config()
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(0)  # fail open — no config means not set up yet

    staged_diff = _get_staged_diff()
    if watch:
        from depcon.tui import DepconApp
        DepconApp(config=config, chaos=chaos, staged_diff=staged_diff).run()
        raise typer.Exit(0)
    if web:
        import uvicorn
        import webbrowser
        import threading
        def _open():
            import time
            time.sleep(1.5)
            webbrowser.open("http://localhost:8765")
        threading.Thread(target=_open, daemon=True).start()
        uvicorn.run("depcon.web:app", host="0.0.0.0", port=8765, reload=False)
        raise typer.Exit(0)
    timeout = int(os.getenv("DEPCON_TIMEOUT", "120"))
    threshold = os.getenv("DEPCON_CONFIDENCE_THRESHOLD", "medium")

    try:
        with _timeout_guard(timeout):
            typer.echo("[depcon] Starting service and running smoke tests...")
            smoke = asyncio.run(run_smoke_tests(config, chaos_mode=chaos))

            typer.echo(smoke.summary())

            if smoke.all_passed:
                typer.echo("✓ Depcon: all smoke tests passed")
                raise typer.Exit(0)

            typer.echo(f"[depcon] {smoke.failed}/{smoke.total} tests failed — running agent diagnosis...")

            diagnosis = asyncio.run(run_agent(config, staged_diff, smoke))

            if diagnosis is None:
                typer.echo("⚠ Depcon: agent failed to produce a diagnosis — letting commit through", err=True)
                raise typer.Exit(0)

            if not _should_block(diagnosis.confidence, threshold):
                typer.echo(
                    f"⚠ Depcon: {diagnosis.confidence} confidence diagnosis "
                    f"(threshold: {threshold}) — letting commit through",
                    err=True,
                )
                raise typer.Exit(0)

            context = {
                "staged_diff": staged_diff,
                "smoke_summary": smoke.summary(),
                "test_window": smoke.test_window.model_dump(),
            }
            session_path = save_session(config.output.sessions_dir, context, diagnosis)
            _print_blocked(diagnosis, session_path)
            raise typer.Exit(1)

    except TimeoutError:
        typer.echo(
            f"⚠ Depcon: timed out after {timeout}s — letting commit through", err=True
        )
        raise typer.Exit(0)
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"⚠ Depcon: unexpected error ({e}) — letting commit through", err=True)
        raise typer.Exit(0)


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


# ── fix subcommand ────────────────────────────────────────────────────────────

fix_app = typer.Typer(help="Apply the fix from the last diagnosis session.")
app.add_typer(fix_app, name="fix")


@fix_app.command("apply")
def fix_apply() -> None:
    """Apply the fix diff from the last session."""
    load_dotenv()
    from depcon.fix import load_last_session_diff, apply_fix, load_last_diagnosis
    from depcon.config import load_config

    try:
        config = load_config()
        sessions_dir = config.output.sessions_dir
    except FileNotFoundError:
        sessions_dir = ".depcon/sessions"

    diagnosis = load_last_diagnosis(sessions_dir)
    if diagnosis is None:
        typer.echo("No saved diagnosis found. Run `depcon run` first.", err=True)
        raise typer.Exit(1)

    if not diagnosis.fix_diff:
        typer.echo("Last diagnosis has no fix diff.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Applying fix for: {diagnosis.hypothesis}")
    result = apply_fix(diagnosis.fix_diff)

    if result.success:
        typer.echo(f"✓ Fix applied to: {', '.join(result.applied_files)}")
    else:
        typer.echo(f"✗ Failed to apply fix: {result.error}", err=True)
        raise typer.Exit(1)


# ── config subcommand ─────────────────────────────────────────────────────────

config_app = typer.Typer(help="Manage depcon configuration.")
app.add_typer(config_app, name="config")

_TOML_TEMPLATE = """\
[service]
compose_file = "examples/fastapi-service/docker-compose.yml"
health_endpoint = "http://localhost:8080/health"
startup_timeout_secs = 15
run_endpoint = "http://localhost:8080/run"

[smoke]
cases = [
  {{ name = "happy_path",  body = '{{"input": "hello"}}', expect_status = 200 }},
  {{ name = "empty_input", body = '{{"input": ""}}',      expect_status = 400 }},
]
request_timeout_secs = 10

[dynatrace]
service_name = "depcon-target"
error_rate_threshold = 0.05
latency_p99_threshold_ms = 500

[agent]
max_iterations = 5
model = "gemini-2.0-flash"

[output]
save_sessions = true
sessions_dir = ".depcon/sessions"
"""


@config_app.command("init")
def config_init() -> None:
    """Scaffold depcon.toml in the current directory."""
    target = Path("depcon.toml")
    if target.exists():
        typer.echo("depcon.toml already exists — not overwriting.")
        raise typer.Exit(0)
    target.write_text(_TOML_TEMPLATE, encoding="utf-8")
    typer.echo(f"✓ Created {target.absolute()}")
    typer.echo("Edit it to match your service, then run `depcon run`.")


# ── session subcommand ────────────────────────────────────────────────────────

session_app = typer.Typer(help="Inspect past sessions.")
app.add_typer(session_app, name="session")


@session_app.command("list")
def session_list() -> None:
    """List saved sessions."""
    load_dotenv()
    from depcon.config import load_config

    try:
        config = load_config()
        sessions_dir = Path(config.output.sessions_dir)
    except FileNotFoundError:
        sessions_dir = Path(".depcon/sessions")

    if not sessions_dir.exists():
        typer.echo("No sessions found.")
        raise typer.Exit(0)

    dirs = sorted(
        [d for d in sessions_dir.iterdir() if d.is_dir() and d.name != "latest"],
        reverse=True,
    )
    if not dirs:
        typer.echo("No sessions found.")
        raise typer.Exit(0)

    for d in dirs:
        diag_file = d / "diagnosis.json"
        if diag_file.exists():
            import json
            data = json.loads(diag_file.read_text(encoding="utf-8"))
            confidence = data.get("confidence", "?").upper()
            hypothesis = data.get("hypothesis", "")[:70]
            typer.echo(f"  {d.name}  [{confidence}]  {hypothesis}")
        else:
            typer.echo(f"  {d.name}  (no diagnosis)")


@session_app.command("show")
def session_show(timestamp: str = typer.Argument(..., help="Session timestamp")) -> None:
    """Print a past diagnosis."""
    load_dotenv()
    from depcon.config import load_config

    try:
        config = load_config()
        sessions_dir = Path(config.output.sessions_dir)
    except FileNotFoundError:
        sessions_dir = Path(".depcon/sessions")

    session_path = sessions_dir / timestamp
    diag_file = session_path / "diagnosis.json"

    if not diag_file.exists():
        typer.echo(f"Session '{timestamp}' not found.", err=True)
        raise typer.Exit(1)

    from depcon.agent import Diagnosis
    diagnosis = Diagnosis.model_validate_json(diag_file.read_text(encoding="utf-8"))
    _print_blocked(diagnosis, session_path)
