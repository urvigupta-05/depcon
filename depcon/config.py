import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class SmokeCase(BaseModel):
    name: str
    body: str
    expect_status: int


class ServiceConfig(BaseModel):
    compose_file: str
    health_endpoint: str
    startup_timeout_secs: int = 15
    run_endpoint: str


class SmokeConfig(BaseModel):
    cases: list[SmokeCase]
    request_timeout_secs: int = 10


class DynatraceConfig(BaseModel):
    service_name: str = "depcon-target"
    error_rate_threshold: float = 0.05
    latency_p99_threshold_ms: int = 500


class AgentConfig(BaseModel):
    max_iterations: int = 5
    model: str = "gemini-2.0-flash"


class OutputConfig(BaseModel):
    save_sessions: bool = True
    sessions_dir: str = ".depcon/sessions"


class DepconConfig(BaseModel):
    service: ServiceConfig
    smoke: SmokeConfig
    dynatrace: DynatraceConfig
    agent: AgentConfig
    output: OutputConfig = OutputConfig()


def load_config(path: str = "depcon.toml") -> DepconConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"depcon.toml not found at {config_path.absolute()}\n"
            "Run `depcon config init` to create one."
        )
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    return DepconConfig.model_validate(data)
