import typer

app = typer.Typer(
    name="depcon",
    help="Pre-commit hook: validates your service with Dynatrace telemetry before every commit.",
    no_args_is_help=True,
)


@app.command()
def run(
    watch: bool = typer.Option(False, "--watch", help="Show live TUI"),
    chaos: str = typer.Option("", "--chaos", help="Override CHAOS_MODE (off|latency|error|panic)"),
) -> None:
    """Full validation cycle — what pre-commit calls."""
    typer.echo("depcon run: not yet implemented")
    raise typer.Exit(0)


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


fix_app = typer.Typer(help="Manage fix application.")
app.add_typer(fix_app, name="fix")


@fix_app.command("apply")
def fix_apply() -> None:
    """Apply the fix diff from the last session."""
    typer.echo("depcon fix apply: not yet implemented")
    raise typer.Exit(0)


config_app = typer.Typer(help="Manage depcon configuration.")
app.add_typer(config_app, name="config")


@config_app.command("init")
def config_init() -> None:
    """Scaffold depcon.toml in the current repo."""
    typer.echo("depcon config init: not yet implemented")
    raise typer.Exit(0)


session_app = typer.Typer(help="Inspect past sessions.")
app.add_typer(session_app, name="session")


@session_app.command("list")
def session_list() -> None:
    """List saved sessions."""
    typer.echo("depcon session list: not yet implemented")
    raise typer.Exit(0)


@session_app.command("show")
def session_show(timestamp: str = typer.Argument(...)) -> None:
    """Print a past diagnosis."""
    typer.echo(f"depcon session show {timestamp}: not yet implemented")
    raise typer.Exit(0)
