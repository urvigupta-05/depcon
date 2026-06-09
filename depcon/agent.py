import json
import logging
import os
import re
from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part
from pydantic import BaseModel

from depcon.config import DepconConfig
from depcon.smoketest import SmokeResult
from depcon.tools.dynatrace import dynatrace_session

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Depcon, a service health diagnostic agent running as part of a pre-commit hook.

A developer just ran `git commit` and their service failed smoke tests. Your job is to diagnose
the root cause using Dynatrace telemetry data captured during the test run.

You will receive:
1. STAGED DIFF — the exact code changes in this commit
2. SMOKE TEST RESULTS — which tests failed, status codes, latencies
3. TEST WINDOW — the exact UTC timestamps when the tests ran (all Dynatrace queries MUST use this window)

INVESTIGATION STEPS (follow this order):
1. Call get_problems(since=<test_window.start>) — check for Dynatrace-detected anomalies
2. If problems found, call get_problem_detail(problem_id) for root cause detail
3. Call query_logs(start, end) — find ERROR/WARN log lines during the window
4. Call query_traces(start, end) — find error spans and slow calls during the window
5. Call natural_language_query as a fallback if steps 1-4 are inconclusive
6. Call get_troubleshooting_guides(description) if you have a hypothesis and want guidance

RULES:
- Always scope queries to the test window provided — do NOT query the full tenant history
- Map your findings back to the staged diff — identify which file/function is implicated
- After gathering evidence, produce a fix diff in unified diff format if possible
- Maximum 5 tool calls — after that, synthesise what you have

FINAL OUTPUT:
Your last message MUST be a single valid JSON object — no markdown fences, no explanation:

{
  "hypothesis": "one sentence root cause",
  "confidence": "high" | "medium" | "low",
  "evidence": [
    {"tool_name": "...", "finding": "...", "relevant_snippet": "... or null"}
  ],
  "affected_file": "path/from/staged/diff or null",
  "affected_function": "function name or null",
  "error_class": "e.g. ValueError, HTTP500, timeout, panic",
  "fix_description": "plain English explanation of the fix",
  "fix_diff": "unified diff string or null"
}
"""


class Evidence(BaseModel):
    tool_name: str
    finding: str
    relevant_snippet: str | None = None


class Diagnosis(BaseModel):
    hypothesis: str
    confidence: Literal["high", "medium", "low"]
    evidence: list[Evidence]
    affected_file: str | None = None
    affected_function: str | None = None
    error_class: str
    fix_description: str
    fix_diff: str | None = None


def _build_context(staged_diff: str, smoke: SmokeResult, config: DepconConfig) -> str:
    return (
        f"STAGED DIFF:\n{staged_diff or '(no staged changes)'}\n\n"
        f"SMOKE TEST RESULTS:\n{smoke.summary()}\n\n"
        f"TEST WINDOW:\n"
        f"  start:   {smoke.test_window.start_iso()}\n"
        f"  end:     {smoke.test_window.end_iso()}\n"
        f"  service: {config.dynatrace.service_name}\n"
    )


def _extract_json(text: str) -> str | None:
    """Pull the first {...} block out of the agent's final response."""
    text = text.strip()
    if text.startswith("{"):
        return text
    match = re.search(r"\{[\s\S]*\}", text)
    return match.group(0) if match else None


def _parse_diagnosis(text: str) -> Diagnosis | None:
    raw = _extract_json(text)
    if not raw:
        logger.warning("Agent response contained no JSON block")
        return None
    try:
        data = json.loads(raw)
        return Diagnosis.model_validate(data)
    except Exception as e:
        logger.warning(f"Could not parse Diagnosis from agent response: {e}\nRaw: {raw[:500]}")
        return None


async def run_agent(
    config: DepconConfig,
    staged_diff: str,
    smoke: SmokeResult,
) -> Diagnosis | None:
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "false")

    context = _build_context(staged_diff, smoke, config)

    try:
        async with dynatrace_session(
            config.dynatrace.service_name, smoke.test_window
        ) as tools:
            agent = LlmAgent(
                name="depcon",
                model=config.agent.model,
                instruction=SYSTEM_PROMPT,
                tools=tools,
            )

            session_service = InMemorySessionService()
            runner = Runner(
                agent=agent,
                app_name="depcon",
                session_service=session_service,
            )
            session = await session_service.create_session(
                app_name="depcon", user_id="depcon_user"
            )

            final_text = ""
            async for event in runner.run_async(
                user_id="depcon_user",
                session_id=session.id,
                new_message=Content(parts=[Part(text=context)], role="user"),
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    final_text = event.content.parts[0].text or ""
                    break

            if not final_text:
                logger.warning("Agent produced no final response")
                return None

            return _parse_diagnosis(final_text)

    except Exception as e:
        logger.error(f"Agent loop failed: {e}", exc_info=True)
        return None
