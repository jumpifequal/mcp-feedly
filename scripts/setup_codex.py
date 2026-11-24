#!/usr/bin/env python3
"""Register the Feedly MCP server in Codex (no GUI).

  python scripts/setup_codex.py            # add/update [mcp_servers.feedly]
  python scripts/setup_codex.py --remove   # remove it
  python scripts/setup_codex.py --test     # also run a live test afterwards
  python scripts/setup_codex.py --config PATH   # use another config file

Edits ~/.codex/config.toml (timestamped backup first; other tables preserved,
result validated as TOML). It writes exactly:

    [mcp_servers.feedly]
    command = '<system Python>\\pythonw.exe'
    args = ['<project>\\feedly_mcp_server.py']
    startup_timeout_sec = 30
    tool_timeout_sec = 120

pythonw is the windowless interpreter (no console window). No token and no
env_vars are written: the server reads FEEDLY_ACCESS_TOKEN / FEEDLY_REFRESH_TOKEN
from your user environment itself.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402


def remove(path: Path) -> str:
    if not path.exists():
        return f"Codex: {path} does not exist, nothing to remove."
    text = path.read_text(encoding="utf-8")
    out, skipping, found = [], False, False
    header = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*$")
    for ln in text.splitlines(keepends=True):
        m = header.match(ln)
        if m:
            name = m.group(1).strip().strip("'\"")
            skipping = name == f"mcp_servers.{c.SERVER_NAME}" or name.startswith(f"mcp_servers.{c.SERVER_NAME}.")
            found = found or skipping
        if not skipping:
            out.append(ln)
    if not found:
        return f"Codex: no [mcp_servers.{c.SERVER_NAME}] in {path}."
    bak = c.backup(path)
    path.write_text("".join(out).rstrip("\n") + "\n", encoding="utf-8")
    return f"Codex: removed [mcp_servers.{c.SERVER_NAME}] from {path} (backup: {bak.name})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Register the Feedly MCP server in Codex.")
    ap.add_argument("--remove", action="store_true", help="remove the entry instead of adding it")
    ap.add_argument("--test", action="store_true", help="run a live server test afterwards")
    ap.add_argument("--config", type=Path, default=None, help="config file (default: ~/.codex/config.toml)")
    args = ap.parse_args(argv)
    path = args.config or c.codex_config_path()

    try:
        if args.remove:
            print(remove(path))
            return 0
        cmd, a = c.client_command()
        ok, why = c.dependencies_ok(c.console_python(cmd))
        if not c.LAUNCHER.exists() or not ok:
            print("The server is not ready for Codex: "
                  + ("launcher not found." if not c.LAUNCHER.exists() else
                     f"the Python at {c.console_python(cmd)} lacks the dependencies ({why}). "
                     f"Install them with: {c.console_python(cmd)} -m pip install -r \"{c.ROOT / 'requirements.txt'}\""))
            return 1
        print(c.register_codex(cmd, a, path))
        print(f"  command = {cmd}")
        print(f"  args    = {a}")
        print("Fully quit and restart Codex to load the server.")
    except Exception as e:  # noqa: BLE001
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1

    if args.test:
        for line in c.test_server():
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
