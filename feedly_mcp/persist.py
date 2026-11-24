"""Persist renewed tokens to the user environment and keep an audit log.

After a successful token renewal the new access token (and the refresh token,
if Feedly rotated it) replaces the stored ``FEEDLY_ACCESS_TOKEN`` /
``FEEDLY_REFRESH_TOKEN`` user environment variables by running
``scripts/set_feedly_token.py --silent``. Token values are handed to that
script through its process environment, never on a command line, and are NEVER
written to the log - only metadata (time, outcome, lengths).

Disable with ``FEEDLY_PERSIST_TOKEN=0``. Override the log location with
``FEEDLY_MCP_LOG``.
"""

import asyncio
import datetime as _dt
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# <root>/feedly_mcp/persist.py -> <root>  (same in the source tree and in a packaged copy)
PROJECT_DIR = Path(__file__).resolve().parents[1]
SETTER_PATH = PROJECT_DIR / "scripts" / "set_feedly_token.py"


def log_path() -> Path:
    return Path(os.environ.get("FEEDLY_MCP_LOG") or PROJECT_DIR / "feedly-mcp-token.log")


def persistence_enabled() -> bool:
    return os.environ.get("FEEDLY_PERSIST_TOKEN", "1").strip().lower() not in ("0", "false", "no", "off")


def write_log(event: str, **fields) -> None:
    """Append one line to the token log. Never raises."""
    try:
        ts = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(f"{ts} {event}" + (f" {extra}" if extra else "") + "\n")
    except Exception:
        pass


def _run_setter(access_token: str, refresh_token: Optional[str]) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "not-windows"
    if not SETTER_PATH.is_file():
        return False, f"missing-{SETTER_PATH.name}"
    env = dict(os.environ)
    env["FEEDLY_NEW_ACCESS_TOKEN"] = access_token
    if refresh_token:
        env["FEEDLY_NEW_REFRESH_TOKEN"] = refresh_token
    else:
        env.pop("FEEDLY_NEW_REFRESH_TOKEN", None)
    # Isolated stdlib-only child: no site-packages or vendored libs needed.
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-S", str(SETTER_PATH), "--silent"],
            env=env,
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}"
    return proc.returncode == 0, f"exit={proc.returncode}"


async def persist_renewed_tokens(
    access_token: str,
    refresh_token: Optional[str] = None,
    rotated_refresh: bool = False,
    expires_in: Optional[float] = None,
) -> bool:
    """Store the renewed token(s) as user env vars and log the outcome.

    Also updates ``os.environ`` of this process. Returns True on success.
    """
    # Only rewrite the refresh token if Feedly actually issued a new one.
    refresh_to_save = refresh_token if rotated_refresh else None
    meta = {
        "access_len": len(access_token),
        "refresh_rotated": rotated_refresh,
        "expires_at": (
            (_dt.datetime.now().astimezone() + _dt.timedelta(seconds=float(expires_in)))
            .strftime("%Y-%m-%d_%H:%M:%S%z")
            if expires_in
            else "unknown"
        ),
    }

    os.environ["FEEDLY_ACCESS_TOKEN"] = access_token
    if refresh_to_save:
        os.environ["FEEDLY_REFRESH_TOKEN"] = refresh_to_save

    if not persistence_enabled():
        write_log("TOKEN_REFRESHED", persisted="disabled", **meta)
        return False

    ok, detail = await asyncio.to_thread(_run_setter, access_token, refresh_to_save)
    write_log(
        "TOKEN_REFRESHED",
        persisted="user-env-updated" if ok else "FAILED",
        detail=detail,
        **meta,
    )
    return ok
