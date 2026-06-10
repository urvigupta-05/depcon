# TUI Spec — `depcon/tui.py`

> Implement this file. Activated by `depcon run --watch`.
> Textual framework is already installed (`uv sync` already did it).
> Read this fully before writing any code.

---

## What you're building

A live terminal UI that shows the full `depcon run` cycle in real time — service
startup, smoke test results, agent tool calls streaming in, and the final diagnosis
with a fix diff. Three panels side by side, keyboard shortcuts at the bottom.

```
┌─────────────────────┬──────────────────────────────┬──────────────────────────┐
│  SERVICE & SMOKE     │  AGENT REASONING             │  DIAGNOSIS & FIX         │
│                      │                              │                          │
│  ● Starting...       │  [iter 1] get_problems()     │  Hypothesis:             │
│  ● Healthy ✓         │    → 1 problem found         │  Unhandled exception in  │
│                      │  [iter 2] get_problem_       │  handlers/run.py:47      │
│  Smoke tests:        │    detail(P-12345)            │                          │
│    ✓ happy_path 200  │    → OOM in /run handler     │  Confidence: HIGH        │
│    ✗ empty_input 500 │  [iter 3] query_logs()       │  Error: RuntimeError     │
│    ✓ large_payload   │    → 3 ERROR logs found      │                          │
│                      │  [iter 4] query_traces()     │  Affected:               │
│  CHAOS: error        │    → 2 error spans           │  handlers/run.py:run()   │
│                      │                              │                          │
│  2/3 failed          │                              │  Fix diff:               │
│                      │                              │  --- a/handlers/run.py   │
│                      │                              │  +++ b/handlers/run.py   │
│                      │                              │  @@ -45,7 +45,8 @@       │
└─────────────────────┴──────────────────────────────┴──────────────────────────┘
  [a] apply fix    [r] re-run    [q] quit    [s] save session
```

---

## Setup — nothing to install

Textual is already in `pyproject.toml` and installed:
```bash
uv run python -c "import textual; print(textual.__version__)"
```

Run your TUI during development with:
```bash
uv run textual run --dev depcon/tui.py
```
The `--dev` flag enables hot reload and the Textual devtools inspector.

Textual docs: https://textual.textualize.io
Textual tutorial: https://textual.textualize.io/tutorial/

---

## How it connects to existing code

You do NOT rewrite the business logic. You call the exact same functions that
`cli.py` calls, just with callbacks to push updates into the UI.

### Imports you need from depcon

```python
from depcon.config import load_config, DepconConfig
from depcon.smoketest import run_smoke_tests, SmokeResult, TestCaseResult
from depcon.agent import run_agent, Diagnosis
from depcon.fix import apply_fix, save_session, load_last_diagnosis
```

### The function signatures (read-only, don't change them)

```python
# smoketest.py
async def run_smoke_tests(config: DepconConfig, chaos_mode: str = "") -> SmokeResult:
    ...
# Returns SmokeResult with:
#   .passed: int
#   .failed: int
#   .total: int
#   .cases: list[TestCaseResult]   # each has .name, .status_code, .passed, .latency_ms
#   .all_passed: bool
#   .test_window: TimeWindow       # .start_iso(), .end_iso()
#   .summary() -> str              # pre-formatted multi-line string

# agent.py
async def run_agent(config: DepconConfig, staged_diff: str, smoke: SmokeResult) -> Diagnosis | None:
    ...
# Returns Diagnosis | None with:
#   .hypothesis: str
#   .confidence: "high" | "medium" | "low"
#   .evidence: list[Evidence]      # each has .tool_name, .finding, .relevant_snippet
#   .affected_file: str | None
#   .affected_function: str | None
#   .error_class: str
#   .fix_description: str
#   .fix_diff: str | None          # unified diff string

# fix.py
def apply_fix(diff: str) -> ApplyResult:
    ...
# Returns ApplyResult with .success: bool, .applied_files: list[str], .error: str | None

def save_session(sessions_dir, context, diagnosis) -> Path:
    ...

def load_last_diagnosis(sessions_dir) -> Diagnosis | None:
    ...
```

### How to wire CLI into TUI — the `--watch` flag

In `cli.py`, the `run` command already has this:
```python
@app.command()
def run(
    watch: bool = typer.Option(False, "--watch", help="Show live TUI (not yet implemented)"),
    chaos: str = typer.Option("", "--chaos", help="Override CHAOS_MODE (off|latency|error|panic)"),
) -> None:
```

You need to add the TUI branch **at the top of that function body**, before the
existing smoke test logic:

```python
@app.command()
def run(watch, chaos):
    load_dotenv()
    ...
    if watch:
        from depcon.tui import DepconApp
        app_tui = DepconApp(config=config, chaos=chaos, staged_diff=staged_diff)
        app_tui.run()
        raise typer.Exit(0)
    # ... existing non-TUI code stays below
```

The TUI app handles its own exit — it doesn't need to return a meaningful exit code
because `--watch` mode is for the developer watching interactively, not for
pre-commit (which never uses `--watch`).

---

## Textual app skeleton

```python
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Log, Label, RichLog
from textual.containers import Horizontal
from textual import work

class DepconApp(App):
    """Main TUI application."""

    CSS = """
    # put your CSS here — see layout section below
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("a", "apply_fix", "Apply fix"),
        ("r", "rerun", "Re-run"),
        ("s", "save", "Save session"),
    ]

    def __init__(self, config, chaos: str, staged_diff: str):
        super().__init__()
        self.config = config
        self.chaos = chaos
        self.staged_diff = staged_diff
        self._diagnosis = None        # set when agent finishes
        self._session_path = None     # set when session is saved
        self._smoke_result = None     # set after smoke tests

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            ServicePanel(id="service-panel"),
            AgentPanel(id="agent-panel"),
            DiagnosisPanel(id="diagnosis-panel"),
        )
        yield Footer()

    def on_mount(self) -> None:
        # kick off the background worker as soon as the UI is ready
        self.run_validation()

    @work(exclusive=True)
    async def run_validation(self) -> None:
        # This runs in a background thread/task — use self.call_from_thread()
        # or Textual's message system to push updates to the UI
        ...
```

---

## The three panels

Each panel is a custom Textual `Widget`. Keep them in `tui.py`.

### Panel 1 — `ServicePanel`

Shows service lifecycle and smoke test results.

**States to display (update as they happen):**

| State | What to show |
|-------|-------------|
| Starting | `● Starting service...` (spinner or blinking) |
| Waiting for health | `● Waiting for /health...` |
| Healthy | `✓ Service healthy` (green) |
| Health timeout | `✗ Service did not start` (red) |
| After smoke tests | List of test cases, ✓/✗, status code, latency |
| Footer | `CHAOS: {chaos_mode}` or `CHAOS: off` |

**Suggested widget: `RichLog`** — append lines as they come in. Or a `Static` widget
you `.update()` with a Rich `Table` after smoke tests complete.

### Panel 2 — `AgentPanel`

Streams agent tool calls as they happen.

**What to show:**
```
[iter 1]  get_problems("2026-06-10T10:00:00Z")
          → 1 problem found: P-12345

[iter 2]  get_problem_detail("P-12345")
          → OOM error in /run handler after 3rd request

[iter 3]  query_logs("...", "...")
          → 3 ERROR logs: RuntimeError: panic mode triggered

[iter 4]  query_traces("...", "...")
          → 2 error spans in handlers/run.py
```

**Suggested widget: `RichLog`** — call `.write_line()` for each tool call event.

**Challenge:** `run_agent()` is a black box — it doesn't currently emit progress
events. Two approaches:

- **Simple (recommended for hackathon):** Just show a spinner while the agent runs,
  then dump all evidence from `diagnosis.evidence` once it completes. Each
  `Evidence` object has `.tool_name` and `.finding` — reconstruct the call log from
  those post-hoc.

- **Full streaming (more impressive):** Modify `agent.py` to accept an optional
  `on_tool_call` callback:
  ```python
  async def run_agent(
      config, staged_diff, smoke,
      on_tool_call=None   # callable(tool_name: str, result: str) | None
  ) -> Diagnosis | None:
  ```
  Then inside the ADK event loop, detect tool call events and invoke the callback.
  The TUI passes a callback that posts a message to the UI.

### Panel 3 — `DiagnosisPanel`

Shows the final Diagnosis once the agent is done.

**What to show (once `diagnosis` is available):**
```
Hypothesis:
  Unhandled exception after 3 requests in CHAOS panic mode

Confidence: HIGH
Error class: RuntimeError

Affected: handlers/run.py → run()

Evidence:
  • [get_problems]    1 active problem P-12345
  • [query_logs]      3 ERROR logs with traceback
  • [query_traces]    2 error spans, duration 45ms

Fix description:
  Add a request counter reset or guard around the
  panic trigger condition.

Fix diff:
  --- a/handlers/run.py
  +++ b/handlers/run.py
  @@ -47,6 +47,7 @@ ...
```

**Before diagnosis is ready:** show a `Spinner` or "Waiting for agent..." text.
**After:** use a `Static` widget updated with Rich markup for colors.

---

## Colours

Match Dynatrace severity conventions:

| Level | Colour | Textual markup |
|-------|--------|---------------|
| CRITICAL | Red | `[bold red]` |
| HIGH | Orange | `[bold #FF6B00]` |
| MEDIUM | Yellow | `[bold yellow]` |
| LOW | Cyan | `[bold cyan]` |
| OK / pass | Green | `[bold green]` |
| Fail | Red | `[bold red]` |
| Info / neutral | Blue | `[blue]` |

Apply `confidence` → colour mapping:
```python
CONFIDENCE_COLOR = {"high": "bold red", "medium": "bold yellow", "low": "bold cyan"}
```

---

## Layout CSS

Textual uses CSS for layout. Put it in the `CSS` class variable on `DepconApp`.
Basic 3-column layout:

```css
Horizontal {
    height: 1fr;
}

ServicePanel {
    width: 1fr;
    border: solid $primary;
    padding: 1;
    overflow-y: auto;
}

AgentPanel {
    width: 2fr;
    border: solid $primary;
    padding: 1;
    overflow-y: auto;
}

DiagnosisPanel {
    width: 2fr;
    border: solid $primary;
    padding: 1;
    overflow-y: auto;
}
```

Give each panel a `border-title` (Textual feature) so the label appears in the
border like the ASCII diagram above:
```python
self.query_one("#service-panel").border_title = "SERVICE & SMOKE"
self.query_one("#agent-panel").border_title = "AGENT REASONING"
self.query_one("#diagnosis-panel").border_title = "DIAGNOSIS & FIX"
```

---

## Keyboard actions

```python
def action_quit(self) -> None:
    self.exit()

def action_apply_fix(self) -> None:
    if self._diagnosis and self._diagnosis.fix_diff:
        result = apply_fix(self._diagnosis.fix_diff)
        if result.success:
            self.notify("Fix applied!", severity="information")
        else:
            self.notify(f"Fix failed: {result.error}", severity="error")
    else:
        self.notify("No fix available", severity="warning")

def action_rerun(self) -> None:
    # reset panel contents, re-run the worker
    self.query_one("#service-panel").reset()
    self.query_one("#agent-panel").reset()
    self.query_one("#diagnosis-panel").reset()
    self._diagnosis = None
    self.run_validation()

def action_save(self) -> None:
    if self._diagnosis and self._smoke_result:
        context = {
            "staged_diff": self.staged_diff,
            "smoke_summary": self._smoke_result.summary(),
            "test_window": self._smoke_result.test_window.model_dump(),
        }
        path = save_session(
            self.config.output.sessions_dir, context, self._diagnosis
        )
        self.notify(f"Session saved: {path.name}")
    else:
        self.notify("Nothing to save yet", severity="warning")
```

---

## Pushing updates from background worker to UI

Textual is single-threaded on the UI side. Background workers run in a thread.
Use the message system to safely send data to the UI:

```python
from textual.message import Message

class SmokeUpdate(Message):
    def __init__(self, smoke: SmokeResult):
        super().__init__()
        self.smoke = smoke

class AgentToolCall(Message):
    def __init__(self, tool_name: str, finding: str):
        super().__init__()
        self.tool_name = tool_name
        self.finding = finding

class DiagnosisReady(Message):
    def __init__(self, diagnosis: Diagnosis, session_path):
        super().__init__()
        self.diagnosis = diagnosis
        self.session_path = session_path
```

Post from the worker, handle in the app or panels:
```python
# inside the worker (background task):
self.post_message(SmokeUpdate(smoke))

# in the app or panel:
def on_smoke_update(self, message: SmokeUpdate) -> None:
    self.query_one("#service-panel").show_smoke(message.smoke)
```

---

## Background worker pattern

Use Textual's `@work` decorator. It handles async tasks and thread safety:

```python
from textual import work

@work(exclusive=True, thread=False)   # thread=False → asyncio task (not thread)
async def run_validation(self) -> None:
    try:
        # Step 1: smoke tests
        self.post_message(StatusUpdate("Starting service..."))
        smoke = await run_smoke_tests(self.config, chaos_mode=self.chaos)
        self._smoke_result = smoke
        self.post_message(SmokeUpdate(smoke))

        if smoke.all_passed:
            self.post_message(StatusUpdate("✓ All tests passed — commit clear"))
            return

        # Step 2: agent
        self.post_message(StatusUpdate("Running agent diagnosis..."))
        diagnosis = await run_agent(self.config, self.staged_diff, smoke)
        self._diagnosis = diagnosis

        if diagnosis:
            context = {
                "staged_diff": self.staged_diff,
                "smoke_summary": smoke.summary(),
                "test_window": smoke.test_window.model_dump(),
            }
            session_path = save_session(
                self.config.output.sessions_dir, context, diagnosis
            )
            self._session_path = session_path
            self.post_message(DiagnosisReady(diagnosis, session_path))
        else:
            self.post_message(StatusUpdate("⚠ Agent produced no diagnosis"))

    except Exception as e:
        self.post_message(StatusUpdate(f"Error: {e}"))
```

---

## Testing the TUI during development

```bash
# Run directly against the real service (Docker must be running)
uv run python -c "
from depcon.config import load_config
from depcon.tui import DepconApp
import subprocess
app = DepconApp(config=load_config(), chaos='error', staged_diff='')
app.run()
"

# Or wire it up via CLI (once you've added the --watch branch in cli.py):
uv run depcon run --watch --chaos error
```

Use the **Textual devtools** for inspecting the widget tree during development:
```bash
uv run textual run --dev depcon/tui.py
```
Then press `Ctrl+\`` in another terminal to open the inspector.

Textual CSS reference: https://textual.textualize.io/css_types/
Textual widgets reference: https://textual.textualize.io/widgets/

---

## What to do in `cli.py` (one small change needed)

Find this block in `depcon/cli.py` around line 108–113 and add the `--watch` branch:

```python
@app.command()
def run(
    watch: bool = typer.Option(False, "--watch", help="Show live TUI"),
    chaos: str = typer.Option("", "--chaos", help="Override CHAOS_MODE"),
) -> None:
    load_dotenv()

    if os.getenv("DEPCON_SKIP"):
        ...

    from depcon.config import load_config
    ...
    config = load_config()
    staged_diff = _get_staged_diff()

    # ← ADD THIS BLOCK
    if watch:
        from depcon.tui import DepconApp
        DepconApp(config=config, chaos=chaos, staged_diff=staged_diff).run()
        raise typer.Exit(0)
    # ← END ADD

    # rest of the existing non-TUI code stays unchanged below
    timeout = int(os.getenv("DEPCON_TIMEOUT", "120"))
    ...
```

---

## Checklist before calling it done

- [ ] `uv run depcon run --watch` launches the TUI without errors
- [ ] Service start / health poll progress appears in panel 1
- [ ] Smoke test results appear in panel 1 with ✓/✗ per case
- [ ] Agent thinking shows in panel 2 (either live tool calls or post-hoc evidence dump)
- [ ] Diagnosis appears in panel 3 with correct confidence colour
- [ ] Fix diff is visible in panel 3 if one was generated
- [ ] `[a]` applies the fix and shows a notification
- [ ] `[r]` re-runs the full cycle without restarting the process
- [ ] `[q]` exits cleanly
- [ ] No crash when agent returns `None` (Dynatrace unavailable, quota hit, etc.)
- [ ] Panels scroll independently when content overflows
