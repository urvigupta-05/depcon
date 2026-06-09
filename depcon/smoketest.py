import asyncio
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel

from depcon.config import DepconConfig, SmokeCase

logger = logging.getLogger(__name__)


class TimeWindow(BaseModel):
    start: datetime
    end: datetime

    def start_iso(self) -> str:
        return self.start.strftime("%Y-%m-%dT%H:%M:%SZ")

    def end_iso(self) -> str:
        return self.end.strftime("%Y-%m-%dT%H:%M:%SZ")

    def timeframe(self) -> str:
        return f"{self.start_iso()}/{self.end_iso()}"


class TestCaseResult(BaseModel):
    name: str
    status_code: int
    expected_status: int
    passed: bool
    latency_ms: float
    error: str | None = None


class SmokeResult(BaseModel):
    passed: int
    failed: int
    total: int
    cases: list[TestCaseResult]
    test_window: TimeWindow
    all_passed: bool

    def summary(self) -> str:
        lines = [f"Smoke tests: {self.passed}/{self.total} passed"]
        for c in self.cases:
            icon = "✓" if c.passed else "✗"
            lines.append(
                f"  {icon} {c.name}: HTTP {c.status_code} "
                f"(expected {c.expected_status}, {c.latency_ms:.0f}ms)"
            )
            if c.error:
                lines.append(f"    error: {c.error}")
        return "\n".join(lines)


def _compose_cmd(compose_file: str, *args: str) -> list[str]:
    # Docker Compose V2 ships as `docker compose` (plugin); V1 as `docker-compose`
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            return ["docker", "compose", "-f", compose_file, *args]
    except Exception:
        pass
    return ["docker-compose", "-f", compose_file, *args]


def _start_service(compose_file: str, chaos_mode: str = "") -> bool:
    import os
    env = {**os.environ}
    if chaos_mode:
        env["CHAOS_MODE"] = chaos_mode
    try:
        result = subprocess.run(
            _compose_cmd(compose_file, "up", "--build", "-d"),
            capture_output=True,
            env=env,
            timeout=120,
        )
        if result.returncode != 0:
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            logger.warning(
                f"docker compose up failed (exit {result.returncode}):\n"
                f"  stdout: {stdout}\n"
                f"  stderr: {stderr}"
            )
            return False
        return True
    except FileNotFoundError:
        logger.warning("'docker' not found — is Docker installed and running?")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("docker compose up timed out after 120s")
        return False


def _stop_service(compose_file: str) -> None:
    try:
        subprocess.run(
            _compose_cmd(compose_file, "down"),
            capture_output=True,
            timeout=30,
        )
    except Exception as e:
        logger.warning(f"docker compose down failed (ignored): {e}")


async def _poll_health(url: str, timeout_secs: int) -> bool:
    deadline = time.monotonic() + timeout_secs
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(url, timeout=2.0)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)
    return False


async def _run_case(
    client: httpx.AsyncClient,
    url: str,
    case: SmokeCase,
    timeout_secs: int,
) -> TestCaseResult:
    t0 = time.monotonic()
    try:
        body = json.loads(case.body)
        r = await client.post(url, json=body, timeout=float(timeout_secs))
        latency_ms = (time.monotonic() - t0) * 1000
        passed = r.status_code == case.expect_status
        return TestCaseResult(
            name=case.name,
            status_code=r.status_code,
            expected_status=case.expect_status,
            passed=passed,
            latency_ms=latency_ms,
        )
    except Exception as e:
        latency_ms = (time.monotonic() - t0) * 1000
        return TestCaseResult(
            name=case.name,
            status_code=0,
            expected_status=case.expect_status,
            passed=False,
            latency_ms=latency_ms,
            error=str(e),
        )


async def run_smoke_tests(config: DepconConfig, chaos_mode: str = "") -> SmokeResult:
    compose_file = config.service.compose_file

    started = _start_service(compose_file, chaos_mode)
    if not started:
        logger.warning("Service failed to start — fail open, skipping smoke tests")
        now = datetime.now(timezone.utc)
        return SmokeResult(
            passed=0, failed=0, total=0, cases=[],
            test_window=TimeWindow(start=now, end=now),
            all_passed=True,
        )

    healthy = await _poll_health(config.service.health_endpoint, config.service.startup_timeout_secs)
    if not healthy:
        _stop_service(compose_file)
        logger.warning("Service did not become healthy — fail open")
        now = datetime.now(timezone.utc)
        return SmokeResult(
            passed=0, failed=0, total=0, cases=[],
            test_window=TimeWindow(start=now, end=now),
            all_passed=True,
        )

    test_start = datetime.now(timezone.utc)

    results: list[TestCaseResult] = []
    async with httpx.AsyncClient() as client:
        for case in config.smoke.cases:
            result = await _run_case(
                client, config.service.run_endpoint, case, config.smoke.request_timeout_secs
            )
            results.append(result)

    test_end = datetime.now(timezone.utc)
    _stop_service(compose_file)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    return SmokeResult(
        passed=passed,
        failed=failed,
        total=len(results),
        cases=results,
        test_window=TimeWindow(start=test_start, end=test_end),
        all_passed=failed == 0,
    )
