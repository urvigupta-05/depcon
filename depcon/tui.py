from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Footer, Header, RichLog, Static, Rule

from depcon.config import DepconConfig
from depcon.fix import apply_fix, save_session
from depcon.smoketest import SmokeResult, run_smoke_tests
from depcon.agent import Diagnosis, run_agent


# ── Confidence colours ────────────────────────────────────────────────────────

CONFIDENCE_COLOR = {
    "high": "bold red",
    "medium": "bold yellow",
    "low": "bold cyan",
}

CONFIDENCE_LABEL = {
    "high":   "HIGH   — Commit blocked. A fix is available below.",
    "medium": "MEDIUM — Review recommended before proceeding.",
    "low":    "LOW    — Minor issue detected. Proceed with caution.",
}


# ── Messages ──────────────────────────────────────────────────────────────────

class StatusUpdate(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

class SmokeUpdate(Message):
    def __init__(self, smoke: SmokeResult) -> None:
        super().__init__()
        self.smoke = smoke

class DiagnosisReady(Message):
    def __init__(self, diagnosis: Diagnosis, session_path) -> None:
        super().__init__()
        self.diagnosis = diagnosis
        self.session_path = session_path

class AgentFailed(Message):
    def __init__(self, reason: str) -> None:
        super().__init__()
        self.reason = reason


# ── Panels ────────────────────────────────────────────────────────────────────

class ServicePanel(RichLog):
    """Left panel — service health and test results."""

    def reset(self) -> None:
        self.clear()
        self._init_content()

    def on_mount(self) -> None:
        self.markup = True
        self.border_title = " Service Status "
        self._init_content()

    def _init_content(self) -> None:
        self.write("[dim]Waiting for validation to begin...[/dim]")
        self.write("")


class AgentPanel(RichLog):
    """Middle panel — what the agent is doing."""

    def reset(self) -> None:
        self.clear()
        self._init_content()

    def on_mount(self) -> None:
        self.markup = True
        self.border_title = " Agent Activity "
        self._init_content()

    def _init_content(self) -> None:
        self.write("[dim]Agent will run if any tests fail.[/dim]")
        self.write("")


class DiagnosisPanel(Static):
    """Right panel — final result and recommended action."""

    def reset(self) -> None:
        self.update(self._placeholder())

    def on_mount(self) -> None:
        self.markup = True
        self.border_title = " Result & Recommended Action "
        self.update(self._placeholder())

    def _placeholder(self) -> str:
        return "\n  [dim]Diagnosis will appear here once the agent completes its analysis.[/dim]"

    def show_diagnosis(self, diagnosis: Diagnosis) -> None:
        color = CONFIDENCE_COLOR.get(diagnosis.confidence, "white")
        label = CONFIDENCE_LABEL.get(diagnosis.confidence, "")
        lines: list[str] = []

        # Status banner
        lines.append(f"  [{color}]▐ {diagnosis.confidence.upper()} CONFIDENCE[/{color}]")
        lines.append(f"  [dim]{label}[/dim]")
        lines.append("")

        # Root cause
        lines.append("  [bold]Root Cause[/bold]")
        lines.append(f"  {diagnosis.hypothesis}")
        lines.append("")

        # Details
        lines.append("  [bold]Details[/bold]")
        lines.append(f"  Error type:  {diagnosis.error_class}")
        if diagnosis.affected_file:
            loc = diagnosis.affected_file
            if diagnosis.affected_function:
                loc += f"  →  {diagnosis.affected_function}()"
            lines.append(f"  Location:    {loc}")
        lines.append("")

        # Evidence
        lines.append("  [bold]Evidence Collected[/bold]")
        for ev in diagnosis.evidence:
            lines.append(f"  [dim]▸[/dim] {ev.finding}")
            if ev.relevant_snippet:
                for snippet_line in ev.relevant_snippet.splitlines()[:2]:
                    lines.append(f"      [dim]{snippet_line}[/dim]")
        lines.append("")

        # Recommended fix
        lines.append("  [bold]Recommended Fix[/bold]")
        lines.append(f"  {diagnosis.fix_description}")

        if diagnosis.fix_diff:
            lines.append("")
            lines.append("  [bold]Code Changes[/bold]")
            lines.append("  [dim]Press  a  to apply these changes automatically.[/dim]")
            lines.append("")
            for diff_line in diagnosis.fix_diff.splitlines():
                if diff_line.startswith("+++") or diff_line.startswith("---"):
                    lines.append(f"  [dim]{diff_line}[/dim]")
                elif diff_line.startswith("+"):
                    lines.append(f"  [green]{diff_line}[/green]")
                elif diff_line.startswith("-"):
                    lines.append(f"  [red]{diff_line}[/red]")
                elif diff_line.startswith("@@"):
                    lines.append(f"  [cyan]{diff_line}[/cyan]")
                else:
                    lines.append(f"  [dim]{diff_line}[/dim]")

        self.update("\n".join(lines))

    def show_passed(self) -> None:
        lines = [
            "",
            "  [bold green]✓  All Tests Passed[/bold green]",
            "",
            "  [dim]Your service is healthy. This commit is clear to proceed.[/dim]",
        ]
        self.update("\n".join(lines))

    def show_error(self, reason: str) -> None:
        lines = [
            "",
            "  [bold red]⚠  Agent Could Not Complete Analysis[/bold red]",
            "",
            f"  [dim]{reason}[/dim]",
            "",
            "  [dim]Your commit has been allowed through. Review the service manually.[/dim]",
        ]
        self.update("\n".join(lines))


# ── Main App ──────────────────────────────────────────────────────────────────

class DepconApp(App):
    """Depcon — pre-commit service validation dashboard."""

    CSS = """
    Screen {
        background: $surface;
    }

    Header {
        background: $primary;
        color: $text;
    }

    Horizontal {
        height: 1fr;
        padding: 0 1;
    }

    ServicePanel {
        width: 1fr;
        border: tall $primary-darken-2;
        border-title-color: $primary;
        border-title-style: bold;
        padding: 1 2;
        margin: 1 1 0 0;
        background: $surface-darken-1;
        overflow-y: auto;
    }

    AgentPanel {
        width: 2fr;
        border: tall $primary-darken-2;
        border-title-color: $primary;
        border-title-style: bold;
        padding: 1 2;
        margin: 1 1 0 0;
        background: $surface-darken-1;
        overflow-y: auto;
    }

    DiagnosisPanel {
        width: 2fr;
        border: tall $primary-darken-2;
        border-title-color: $primary;
        border-title-style: bold;
        padding: 1 0;
        margin: 1 0 0 0;
        background: $surface-darken-1;
        overflow-y: auto;
    }

    Footer {
        background: $primary-darken-2;
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("a", "apply_fix", "Apply Fix"),
        ("r", "rerun", "Re-run"),
        ("s", "save", "Save Session"),
        ("q", "quit", "Quit"),
    ]

    TITLE = "Depcon — Pre-commit Validation"

    def __init__(self, config: DepconConfig, chaos: str, staged_diff: str) -> None:
        super().__init__()
        self.config = config
        self.chaos = chaos
        self.staged_diff = staged_diff
        self._diagnosis: Diagnosis | None = None
        self._session_path = None
        self._smoke_result: SmokeResult | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            ServicePanel(id="service-panel"),
            AgentPanel(id="agent-panel"),
            DiagnosisPanel(id="diagnosis-panel"),
        )
        yield Footer()

    def on_mount(self) -> None:
        chaos_label = self.chaos.upper() if self.chaos else "OFF"
        self.sub_title = f"Fault mode: {chaos_label}"
        self.run_validation()

    # ── Background worker ─────────────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def run_validation(self) -> None:
        service = self.query_one("#service-panel", ServicePanel)
        agent_panel = self.query_one("#agent-panel", AgentPanel)
        diag_panel = self.query_one("#diagnosis-panel", DiagnosisPanel)

        try:
            # Step 1 — start service and run smoke tests
            service.clear()
            service.write("[bold]Starting service...[/bold]")
            service.write("[dim]Building Docker image and waiting for health check.[/dim]")
            service.write("")

            smoke = await run_smoke_tests(self.config, chaos_mode=self.chaos)
            self._smoke_result = smoke
            self.post_message(SmokeUpdate(smoke))

            if smoke.all_passed:
                agent_panel.clear()
                agent_panel.write("[bold green]No issues found.[/bold green]")
                agent_panel.write("[dim]All tests passed — agent analysis was not needed.[/dim]")
                diag_panel.show_passed()
                return

            # Step 2 — run agent
            agent_panel.clear()
            agent_panel.write("[bold]Analysing test failures...[/bold]")
            agent_panel.write(
                f"[dim]{smoke.failed} of {smoke.total} tests failed. "
                f"Querying Dynatrace for traces, logs, and error events.[/dim]"
            )
            agent_panel.write("")

            diagnosis = await run_agent(self.config, self.staged_diff, smoke)
            self._diagnosis = diagnosis

            if diagnosis is None:
                self.post_message(AgentFailed("Agent returned no result. This may be a quota or connectivity issue."))
                return

            # Show evidence in agent panel
            agent_panel.write("[bold]Investigation Summary[/bold]")
            agent_panel.write("")
            for i, ev in enumerate(diagnosis.evidence, 1):
                agent_panel.write(f"  [dim]Step {i}[/dim]  {ev.finding}")
                if ev.relevant_snippet:
                    for line in ev.relevant_snippet.splitlines()[:2]:
                        agent_panel.write(f"          [dim]{line}[/dim]")
                agent_panel.write("")

            agent_panel.write("[dim]Analysis complete. See Result panel for diagnosis.[/dim]")

            # Save and show diagnosis
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

        except Exception as e:
            self.post_message(AgentFailed(str(e)))

    # ── Message handlers ──────────────────────────────────────────────────────

    def on_status_update(self, message: StatusUpdate) -> None:
        self.query_one("#service-panel", ServicePanel).write(message.text)

    def on_smoke_update(self, message: SmokeUpdate) -> None:
        panel = self.query_one("#service-panel", ServicePanel)
        smoke = message.smoke

        panel.clear()

        if smoke.total == 0:
            panel.write("[bold red]Service did not start[/bold red]")
            panel.write("[dim]Docker failed to build or the health check timed out.[/dim]")
            return

        panel.write("[bold green]Service is healthy[/bold green]")
        panel.write("")
        panel.write("[bold]Test Results[/bold]")
        panel.write("")

        for case in smoke.cases:
            if case.passed:
                status = "[bold green]PASS[/bold green]"
            else:
                status = "[bold red]FAIL[/bold red]"
            panel.write(
                f"  {status}  {case.name}"
            )
            panel.write(
                f"        Response: {case.status_code}  "
                f"[dim]Expected: {case.expected_status}  |  {case.latency_ms:.0f}ms[/dim]"
            )
            if case.error:
                panel.write(f"        [red]{case.error}[/red]")
            panel.write("")

        color = "green" if smoke.all_passed else "red"
        panel.write(f"  [{color}]{smoke.passed} passed,  {smoke.failed} failed[/{color}]")

    def on_diagnosis_ready(self, message: DiagnosisReady) -> None:
        self.query_one("#diagnosis-panel", DiagnosisPanel).show_diagnosis(message.diagnosis)
        self.notify(
            f"Session saved — run 'depcon fix apply' to apply the fix",
            severity="information",
            timeout=6,
        )

    def on_agent_failed(self, message: AgentFailed) -> None:
        self.query_one("#agent-panel", AgentPanel).write(
            f"[bold red]Agent stopped:[/bold red] {message.reason}"
        )
        self.query_one("#diagnosis-panel", DiagnosisPanel).show_error(message.reason)

    # ── Keyboard actions ──────────────────────────────────────────────────────

    def action_quit(self) -> None:
        self.exit()

    def action_apply_fix(self) -> None:
        if self._diagnosis and self._diagnosis.fix_diff:
            result = apply_fix(self._diagnosis.fix_diff)
            if result.success:
                files = ", ".join(result.applied_files)
                self.notify(f"Fix applied to: {files}", severity="information", timeout=5)
            else:
                self.notify(f"Could not apply fix: {result.error}", severity="error", timeout=6)
        else:
            self.notify("No fix is available for this diagnosis.", severity="warning")

    def action_rerun(self) -> None:
        self.query_one("#service-panel", ServicePanel).reset()
        self.query_one("#agent-panel", AgentPanel).reset()
        self.query_one("#diagnosis-panel", DiagnosisPanel).reset()
        self._diagnosis = None
        self._smoke_result = None
        self._session_path = None
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
            self.notify(f"Session saved: {path.name}", timeout=4)
        else:
            self.notify("Nothing to save yet.", severity="warning")
