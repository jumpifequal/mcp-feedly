"""Shared helpers for the Feedly MCP support scripts (stdlib only).

Layout awareness
----------------
The same scripts work in two layouts, detected automatically:

* **source / dev**  - repository root with a local ``.venv`` (dev support only)
* **packaged**      - an extracted distribution zip (it contains ``manifest.json``); the dependencies
  (``requirements.txt``) are installed into the Python that runs the server

``ROOT`` is the folder that contains ``feedly_mcp/`` and ``feedly_mcp_server.py``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LAUNCHER = ROOT / "feedly_mcp_server.py"
VENV_DIR = ROOT / ".venv"
IS_WIN = sys.platform == "win32"

SERVER_NAME = "feedly"
API = "https://cloud.feedly.com/v3"
UA = "Mozilla/5.0 (feedly-mcp-setup)"
TOKEN_VARS = ("FEEDLY_ACCESS_TOKEN", "FEEDLY_REFRESH_TOKEN")
TOKEN_PAGE = "https://feedly.com/v3/auth/dev"


# ----------------------------------------------------------------------------
# Layout / interpreter
# ----------------------------------------------------------------------------


def is_packaged() -> bool:
    """True in an extracted distribution zip (it ships manifest.json; the source tree does not)."""
    return (ROOT / "manifest.json").is_file()


def build_info() -> dict:
    """The release manifest (manifest.json), or {} in a source checkout."""
    try:
        return json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _console_python(exe: str | Path) -> Path:
    """Prefer the console interpreter over the windowed one (pythonw) so MCP stdio works."""
    p = Path(exe)
    if p.stem.lower() == "pythonw":
        console = p.with_name("python" + p.suffix)
        if console.exists():
            return console
    return p


def windowless_python(py: str | Path) -> Path:
    """The windowless sibling (pythonw) of an interpreter, or the interpreter itself if there is none."""
    p = Path(py)
    sibling = p.with_name("pythonw" + p.suffix)
    return sibling if p.stem.lower() in ("python", "pythonw") and sibling.exists() else p


def console_python(py: str | Path) -> Path:
    """The console sibling (python) of an interpreter; used for probes that need stdout."""
    p = Path(py)
    sibling = p.with_name("python" + p.suffix)
    return sibling if p.stem.lower() == "pythonw" and sibling.exists() else p


def client_command() -> tuple[str, list[str]]:
    """(command, args) registered in Claude Desktop and Codex: the system Python's
    windowless interpreter (pythonw, no console window) + the launcher.

    No environment variables are needed: the server reads FEEDLY_* from the user
    environment itself. The first interpreter that has the dependencies wins:
    the base (system) Python, then - source layout only - the local .venv.
    """
    candidates = [Path(getattr(sys, "_base_executable", sys.executable))]
    if not is_packaged() and venv_python().exists():
        candidates.append(venv_python())
    for cand in candidates:
        if dependencies_ok(console_python(cand))[0]:
            return str(windowless_python(cand)), [str(LAUNCHER)]
    return str(windowless_python(candidates[0])), [str(LAUNCHER)]


def venv_python() -> Path:
    """Interpreter of the local .venv (same file name/suffix convention as the running one)."""
    suffix = Path(sys.executable).suffix
    return VENV_DIR / ("Scripts" if IS_WIN else "bin") / ("python" + suffix)


def server_command() -> tuple[str, list[str]]:
    """(command, args) used to start the server. No tokens, no env needed."""
    if is_packaged():
        py = _console_python(sys.executable)
    elif venv_python().exists():
        py = venv_python()
    else:
        py = _console_python(sys.executable)
    return str(py), [str(LAUNCHER)]


_DEPS_PROBE = "import mcp, httpx, pydantic, uvicorn; from mcp.server.fastmcp import FastMCP; print('deps-ok')"
_deps_cache: dict[str, tuple[bool, str]] = {}


def dependencies_ok(python: str | Path | None = None, refresh: bool = False) -> tuple[bool, str]:
    """Can `python` import everything the server needs? (mcp 1.x, httpx, pydantic, uvicorn)"""
    py = str(python or server_command()[0])
    if not refresh and py in _deps_cache:
        return _deps_cache[py]
    try:
        r = subprocess.run([py, "-c", _DEPS_PROBE], capture_output=True, text=True, timeout=90,
                           cwd=tempfile.gettempdir())
        if r.returncode == 0 and "deps-ok" in r.stdout:
            result = (True, "ok")
        else:
            result = (False, (r.stderr.strip().splitlines() or ["import failed"])[-1][:160])
    except Exception as e:  # noqa: BLE001
        result = (False, type(e).__name__)
    _deps_cache[py] = result
    return result


def server_ready() -> bool:
    return LAUNCHER.exists() and dependencies_ok()[0]


# ----------------------------------------------------------------------------
# Client config locations
# ----------------------------------------------------------------------------


def claude_config_path() -> Path:
    """Claude Desktop config: %APPDATA%/Claude/claude_desktop_config.json."""
    override = os.environ.get("WIZARD_CLAUDE_JSON")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Claude" / "claude_desktop_config.json"


def codex_config_path() -> Path:
    return Path(os.environ.get("WIZARD_CODEX_TOML") or Path.home() / ".codex" / "config.toml")


# ----------------------------------------------------------------------------
# User environment variables
# ----------------------------------------------------------------------------


def get_user_env(name: str) -> str:
    """User-level environment variable (registry on Windows, process env elsewhere)."""
    if IS_WIN:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except OSError:
            return ""
    return os.environ.get(name, "")


def set_user_env(name: str, value: str) -> None:
    """Persist a user environment variable and notify running apps (Windows)."""
    if not IS_WIN:
        raise RuntimeError(
            f"Persisting user env vars is only implemented for Windows. "
            f"Add `export {name}=...` to your shell profile."
        )
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 2, 5000, None)
    os.environ[name] = value


# ----------------------------------------------------------------------------
# Feedly token validation (read-only)
# ----------------------------------------------------------------------------


def _http_json(url: str, method: str = "GET", token: str | None = None, body: dict | None = None):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None
    except Exception as e:  # network down, DNS...
        return 0, {"errorMessage": type(e).__name__}


def validate_tokens(access: str, refresh: str) -> tuple[bool, str, str]:
    """Read-only check. Returns (ok, message, working_access_token)."""
    if access:
        code, _ = _http_json(f"{API}/profile", token=access)
        if code == 200:
            return True, "Access token is valid.", access
        if code == 0:
            return False, "Could not reach Feedly (network problem).", access
        if code != 401:
            return False, f"Feedly answered HTTP {code}.", access
    if refresh:
        code, data = _http_json(
            f"{API}/auth/token", "POST",
            body={"refresh_token": refresh, "client_id": "feedlydev",
                  "client_secret": "feedlydev", "grant_type": "refresh_token"},
        )
        if code == 200 and data and data.get("access_token"):
            return True, ("Access token was missing/expired; the refresh token works "
                          "and issued a new one."), data["access_token"]
        return False, f"Refresh token rejected (HTTP {code}). Generate a new pair at {TOKEN_PAGE}", access
    return False, "Access token rejected and no refresh token provided.", access


# ----------------------------------------------------------------------------
# Client registration (no secrets are ever written)
# ----------------------------------------------------------------------------


def backup(path: Path) -> Path | None:
    """Copy `path` to <name>.bak-<timestamp> (never overwrites an earlier backup)."""
    if not path.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak-{stamp}")
    n = 1
    while dest.exists():
        dest = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    shutil.copy2(path, dest)
    return dest


def register_claude(command: str, args: list[str], path: Path | None = None) -> str:
    """Add/replace the server in Claude Desktop's claude_desktop_config.json."""
    path = path or claude_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        raw = path.read_text(encoding="utf-8-sig").strip()
        data = json.loads(raw) if raw else {}
    bak = backup(path)
    servers = data.setdefault("mcpServers", {})
    existed = SERVER_NAME in servers
    servers[SERVER_NAME] = {"command": command, "args": list(args)}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return f"Claude Desktop: {'updated' if existed else 'added'} '{SERVER_NAME}' in {path}" + (
        f" (backup: {bak.name})" if bak else "")


def _toml_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'" if "'" not in s else json.dumps(s)


def _toml_block(command: str, args: list[str]) -> str:
    args_toml = ", ".join(_toml_literal(a) for a in args)
    return (
        f"[mcp_servers.{SERVER_NAME}]\n"
        f"command = {_toml_literal(command)}\n"
        f"args = [{args_toml}]\n"
        "startup_timeout_sec = 30\n"
        "tool_timeout_sec = 120\n"
    )


def register_codex(command: str, args: list[str], path: Path | None = None) -> str:
    """Add/replace [mcp_servers.feedly] in Codex's config.toml (text edit, validated)."""
    path = path or codex_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    bak = backup(path)

    out, skipping, existed = [], False, False
    header = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*$")
    for ln in text.splitlines(keepends=True):
        m = header.match(ln)
        if m:
            name = m.group(1).strip().strip("'\"")
            skipping = name == f"mcp_servers.{SERVER_NAME}" or name.startswith(f"mcp_servers.{SERVER_NAME}.")
            existed = existed or skipping
        if not skipping:
            out.append(ln)
    new_text = "".join(out).rstrip("\n")
    new_text += ("\n\n" if new_text else "") + _toml_block(command, args)

    if tomllib is not None:
        parsed = tomllib.loads(new_text)  # raises if we produced invalid TOML
        assert SERVER_NAME in parsed.get("mcp_servers", {})
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
    return f"Codex: {'updated' if existed else 'added'} [mcp_servers.{SERVER_NAME}] in {path}" + (
        f" (backup: {bak.name})" if bak else "")


# ----------------------------------------------------------------------------
# Dev-environment install (source layout only)
# ----------------------------------------------------------------------------


def install_requirements(python: str | Path, log) -> bool:
    """`pip install -r requirements.txt` into `python` (retries as a per-user install outside a venv)."""
    quiet = ["-q", "--disable-pip-version-check"]
    req = ROOT / "requirements.txt"
    log(f"Installing dependencies into {python} ...")
    cmd = [str(python), "-m", "pip", "install", *quiet, "-r", str(req)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode and "venv" not in str(python).lower():
        log("Normal install failed (permissions?); retrying with --user ...")
        r = subprocess.run(cmd + ["--user"], capture_output=True, text=True)
    if r.returncode:
        log("pip failed: " + (r.stderr or r.stdout).strip()[-500:])
        return False
    log("Dependencies installed.")
    return dependencies_ok(console_python(python), refresh=True)[0]


def prepare_environment(log) -> bool:
    """Make the server runnable.

    * source layout : create ./.venv (local support env) and `pip install -e .`
    * packaged copy : install requirements.txt into the Python running this script
    """
    if is_packaged():
        return install_requirements(sys.executable, log)

    log("Creating the local support environment (.venv) ...")
    r = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], capture_output=True, text=True)
    if r.returncode:
        log("venv failed: " + r.stderr.strip()[-300:])
        return False
    log("Installing dependencies (pip install -e .) - this can take a minute ...")
    r = subprocess.run([str(venv_python()), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-e", str(ROOT)],
                       capture_output=True, text=True)
    if r.returncode:
        log("pip failed: " + (r.stderr or r.stdout).strip()[-400:])
        return False
    log("Environment ready.")
    return dependencies_ok(venv_python(), refresh=True)[0]


# ----------------------------------------------------------------------------
# Stdio client (live tests, build verification)
# ----------------------------------------------------------------------------


class StdioClient:
    """Minimal MCP client over stdio (newline-delimited JSON-RPC)."""

    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None):
        self.proc = subprocess.Popen([command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
        self._q: queue.Queue = queue.Queue()
        self._id = 0
        threading.Thread(target=lambda: [self._q.put(l) for l in self.proc.stdout], daemon=True).start()

    def _send(self, obj: dict) -> None:
        self.proc.stdin.write(json.dumps(obj) + chr(10))
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method: str, params: dict | None = None, timeout: float = 40.0) -> dict:
        self._id += 1
        mid = self._id
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
        end = dt.datetime.now() + dt.timedelta(seconds=timeout)
        while dt.datetime.now() < end:
            try:
                msg = json.loads(self._q.get(timeout=1))
            except (queue.Empty, json.JSONDecodeError):
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"])[:200])
                return msg["result"]
        raise TimeoutError(f"no response to {method}")

    def handshake(self) -> dict:
        res = self.request("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "feedly-setup", "version": "1"}})
        self.notify("notifications/initialized")
        return res

    def call_tool(self, name: str, arguments: dict | None = None, timeout: float = 40.0) -> str:
        res = self.request("tools/call", {"name": name, "arguments": arguments or {"params": {}}}, timeout)
        return res["content"][0]["text"]

    def close(self) -> None:
        try:
            self.proc.kill()
        except Exception:
            pass


def _server_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    env = dict(os.environ)
    for k in TOKEN_VARS:
        if not env.get(k) and get_user_env(k):
            env[k] = get_user_env(k)
    env.update(extra_env or {})
    return env


def test_server(extra_env: dict[str, str] | None = None, timeout: float = 40.0,
                command: str | None = None, args: list[str] | None = None) -> list[str]:
    """initialize -> tools/list -> feedly_auth_status. Returns result lines."""
    cmd, a = (command, args) if command else server_command()
    res: list[str] = []
    cl = StdioClient(cmd, a, _server_env(extra_env))
    try:
        info = cl.handshake()["serverInfo"]
        res.append(f"OK  server starts ({info.get('name')} {info.get('version', '')})".strip())
        res.append(f"OK  {len(cl.request('tools/list', timeout=timeout)['tools'])} tools available")
        st = json.loads(cl.call_tool("feedly_auth_status", timeout=timeout))
        if st.get("token_valid"):
            res.append("OK  Feedly accepts the token (auto-renew "
                       + ("available)" if st.get("auto_renew_available") else "NOT available)"))
        else:
            res.append("FAIL Feedly rejected the token: " + str(st.get("error", "unknown")))
    except Exception as e:  # noqa: BLE001
        res.append(f"FAIL {type(e).__name__}: {e}")
    finally:
        cl.close()
    return res
