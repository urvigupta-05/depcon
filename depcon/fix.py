import json
import logging
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ApplyResult(BaseModel):
    success: bool
    applied_files: list[str]
    error: str | None = None


def apply_fix(diff: str) -> ApplyResult:
    if not diff or not diff.strip():
        return ApplyResult(success=False, applied_files=[], error="Empty diff")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".diff", delete=False, encoding="utf-8"
    ) as f:
        f.write(diff)
        tmp_path = f.name

    try:
        check = subprocess.run(
            ["git", "apply", "--check", tmp_path],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            return ApplyResult(
                success=False,
                applied_files=[],
                error=f"git apply --check failed: {check.stderr.strip()}",
            )

        apply = subprocess.run(
            ["git", "apply", tmp_path],
            capture_output=True,
            text=True,
        )
        if apply.returncode != 0:
            return ApplyResult(
                success=False,
                applied_files=[],
                error=f"git apply failed: {apply.stderr.strip()}",
            )

        files = _list_diff_files(diff)
        return ApplyResult(success=True, applied_files=files)

    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass


def _list_diff_files(diff: str) -> list[str]:
    files = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:])
    return files


def save_session(
    sessions_dir: str,
    context: dict,
    diagnosis,
    iterations: list[dict] | None = None,
) -> Path:
    from depcon.agent import Diagnosis

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    session_path = Path(sessions_dir) / ts
    session_path.mkdir(parents=True, exist_ok=True)

    (session_path / "context.json").write_text(
        json.dumps(context, indent=2, default=str), encoding="utf-8"
    )

    if diagnosis:
        (session_path / "diagnosis.json").write_text(
            diagnosis.model_dump_json(indent=2), encoding="utf-8"
        )
        if diagnosis.fix_diff:
            (session_path / "latest.diff").write_text(
                diagnosis.fix_diff, encoding="utf-8"
            )

    if iterations:
        lines = "\n".join(json.dumps(it, default=str) for it in iterations)
        (session_path / "iterations.jsonl").write_text(lines, encoding="utf-8")

    _update_latest_pointer(Path(sessions_dir), session_path)
    return session_path


def _update_latest_pointer(sessions_dir: Path, session_path: Path) -> None:
    latest = sessions_dir / "latest"
    if sys.platform == "win32":
        # Symlinks on Windows require admin rights — use a plain pointer file instead
        (sessions_dir / "latest.txt").write_text(str(session_path), encoding="utf-8")
    else:
        if latest.is_symlink():
            latest.unlink()
        latest.symlink_to(session_path.name)


def load_last_session_diff(sessions_dir: str = ".depcon/sessions") -> str | None:
    base = Path(sessions_dir)

    # Windows fallback
    pointer_file = base / "latest.txt"
    if pointer_file.exists():
        path = Path(pointer_file.read_text(encoding="utf-8").strip())
    else:
        latest = base / "latest"
        if not latest.exists():
            return None
        path = latest.resolve()

    diff_file = path / "latest.diff"
    if diff_file.exists():
        return diff_file.read_text(encoding="utf-8")
    return None


def load_last_diagnosis(sessions_dir: str = ".depcon/sessions"):
    from depcon.agent import Diagnosis

    base = Path(sessions_dir)
    pointer_file = base / "latest.txt"
    if pointer_file.exists():
        path = Path(pointer_file.read_text(encoding="utf-8").strip())
    else:
        latest = base / "latest"
        if not latest.exists():
            return None
        path = latest.resolve()

    diag_file = path / "diagnosis.json"
    if not diag_file.exists():
        return None
    try:
        return Diagnosis.model_validate_json(diag_file.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Could not load diagnosis: {e}")
        return None
