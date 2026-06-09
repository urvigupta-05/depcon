import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from depcon.smoketest import TimeWindow

logger = logging.getLogger(__name__)

_NPX = "npx.cmd" if sys.platform == "win32" else "npx"


def _server_params() -> StdioServerParameters:
    env = {
        **os.environ,
        "DT_ENVIRONMENT": os.getenv("DT_ENVIRONMENT", ""),
        "DT_API_TOKEN": os.getenv("DT_API_TOKEN", ""),
    }
    return StdioServerParameters(
        command=_NPX,
        args=["-y", "@dynatrace-oss/dynatrace-mcp-server"],
        env=env,
    )


async def _call(session: ClientSession, tool: str, args: dict) -> str:
    try:
        result = await session.call_tool(tool, args)
        parts = [item.text for item in result.content if hasattr(item, "text")]
        return "\n".join(parts) if parts else "(empty result)"
    except Exception as e:
        logger.warning(f"MCP tool {tool!r} failed: {e}")
        return f"(tool error: {e})"


def make_tools(session: ClientSession, service_name: str, window: TimeWindow):
    """Return a list of ADK-compatible tool functions bound to this MCP session."""

    async def get_problems(since: str) -> str:
        """Get active Dynatrace problems opened after the given ISO 8601 timestamp.

        Args:
            since: ISO 8601 UTC timestamp, e.g. '2026-06-03T10:00:00Z'
        """
        return await _call(session, "list_problems", {
            "from": since,
            "entitySelector": f"type(SERVICE),entityName({service_name})",
        })

    async def get_problem_detail(problem_id: str) -> str:
        """Get full details for a specific Dynatrace problem by ID.

        Args:
            problem_id: The problem ID, e.g. 'P-12345'
        """
        return await _call(session, "get_problem_details", {"problemId": problem_id})

    async def query_logs(start: str, end: str) -> str:
        """Query ERROR and WARN logs for the service during the test window.

        Args:
            start: ISO 8601 UTC start timestamp
            end: ISO 8601 UTC end timestamp
        """
        dql = (
            f'fetch logs, timeframe:"{start}/{end}"\n'
            f'| filter service.name == "{service_name}"\n'
            '| filter loglevel in ("ERROR","WARN")\n'
            "| sort timestamp desc\n"
            "| limit 50"
        )
        return await _call(session, "execute_dql_query", {"query": dql})

    async def query_traces(start: str, end: str) -> str:
        """Query error spans and slow traces for the service during the test window.

        Args:
            start: ISO 8601 UTC start timestamp
            end: ISO 8601 UTC end timestamp
        """
        threshold_ns = 500_000_000  # 500ms in nanoseconds
        dql = (
            f'fetch spans, timeframe:"{start}/{end}"\n'
            f'| filter service.name == "{service_name}"\n'
            f'| filter span.status_code == "ERROR" or duration > {threshold_ns}\n'
            "| sort duration desc\n"
            "| limit 20"
        )
        return await _call(session, "execute_dql_query", {"query": dql})

    async def natural_language_query(prompt: str) -> str:
        """Run a free-text Dynatrace query when structured tools haven't found the answer.

        Args:
            prompt: Natural language description of what data to retrieve
        """
        dql_prompt = (
            f"{prompt}\n\n"
            f"Scope the query to service '{service_name}' "
            f"between {window.start_iso()} and {window.end_iso()}."
        )
        return await _call(session, "execute_dql_query", {"query": dql_prompt})

    async def get_troubleshooting_guides(description: str) -> str:
        """Retrieve Dynatrace troubleshooting guides relevant to the described problem.

        Args:
            description: Short description of the problem type, e.g. 'HTTP 500 errors in FastAPI'
        """
        return await _call(session, "get_troubleshooting_guides", {"description": description})

    return [
        get_problems,
        get_problem_detail,
        query_logs,
        query_traces,
        natural_language_query,
        get_troubleshooting_guides,
    ]


@asynccontextmanager
async def dynatrace_session(service_name: str, window: TimeWindow):
    """Async context manager: starts MCP server, yields tool list, shuts down cleanly.

    Yields an empty list if credentials are missing or the server fails to start,
    so the caller always gets a valid (possibly empty) tool list.
    """
    dt_env = os.getenv("DT_ENVIRONMENT", "")
    token = os.getenv("DT_API_TOKEN", "")

    if not dt_env or not token:
        logger.warning("DT_ENVIRONMENT or DT_API_TOKEN not set — Dynatrace tools unavailable")
        yield []
        return

    # Manually manage the nested context managers so we can yield exactly once
    # regardless of whether setup succeeds, and clean up properly in finally.
    stdio_cm = None
    session_cm = None
    tools: list = []

    try:
        stdio_cm = stdio_client(_server_params())
        read, write = await stdio_cm.__aenter__()
        session_cm = ClientSession(read, write)
        session = await session_cm.__aenter__()
        await session.initialize()
        tools = make_tools(session, service_name, window)
    except Exception as e:
        logger.warning(f"Could not start Dynatrace MCP server: {e}")
        # partial cleanup before yielding empty list
        if session_cm is not None:
            try:
                await session_cm.__aexit__(None, None, None)
            except Exception:
                pass
            session_cm = None
        if stdio_cm is not None:
            try:
                await stdio_cm.__aexit__(None, None, None)
            except Exception:
                pass
            stdio_cm = None

    try:
        yield tools
    finally:
        if session_cm is not None:
            try:
                await session_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"MCP session cleanup: {e}")
        if stdio_cm is not None:
            try:
                await stdio_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"MCP stdio cleanup: {e}")
