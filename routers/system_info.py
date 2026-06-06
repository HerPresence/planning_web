"""
Diagnostic endpoint — no business logic, no DB writes.
GET /api/system/runtime-info  (no auth required — accessible even if auth breaks)
"""
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone

from fastapi import APIRouter

logger = logging.getLogger("system_info")

router = APIRouter(prefix="/api/system")

# Set once at module import time (survives across requests within one server process)
_started_at = datetime.now(timezone.utc).isoformat()


def _git(args: list, cwd: str) -> str:
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        r = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=5, cwd=cwd,
            creationflags=flags,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _masked_db_url(raw: str) -> str:
    if not raw:
        return ""
    if "@" in raw:
        before, after = raw.rsplit("@", 1)
        if ":" in before:
            prefix = before.rsplit(":", 1)[0]
            return f"{prefix}:***@{after}"
    return (raw[:40] + "...") if len(raw) > 40 else raw


@router.get("/runtime-info")
def runtime_info():
    cwd = os.getcwd()
    file_root = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    )

    git_branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    git_commit = _git(["rev-parse", "--short", "HEAD"], cwd)
    git_status = _git(["status", "--short"], cwd)

    env_path = os.path.join(cwd, ".env")

    return {
        "backend_cwd":        cwd,
        "backend_file_root":  file_root,
        "python_executable":  sys.executable,
        "pid":                os.getpid(),
        "port":               int(os.environ.get("PORT", "8000")),
        "git_branch":         git_branch,
        "git_commit":         git_commit,
        "git_status_short":   git_status,
        "env_file_path":      env_path if os.path.exists(env_path) else "(not found)",
        "database_url_masked": _masked_db_url(os.environ.get("DATABASE_URL", "")),
        "started_at":         _started_at,
        "server_time":        datetime.now(timezone.utc).isoformat(),
    }
