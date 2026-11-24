#!/usr/bin/env python3
r"""Register the Feedly MCP server in Claude Desktop (no GUI).

  python scripts/setup_claude.py            # add/update the 'feedly' entry
  python scripts/setup_claude.py --remove   # remove it
  python scripts/setup_claude.py --test     # also run a live test afterwards
  python scripts/setup_claude.py --config PATH   # use another config file

Edits %APPDATA%/Claude/claude_desktop_config.json (a timestamped backup is made
first; all other servers and settings are preserved). No tokens are written -
the server reads FEEDLY_* from your user environment. The command is the system
Python's windowless interpreter (pythonw, no console window) plus the launcher:

    "feedly": {"command": "<system Python>\pythonw.exe", "args": ["<project>\feedly_mcp_server.py"]}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402


def remove(path: Path) -> str:
    if not path.exists():
        return f"Claude Desktop: {path} does not exist, nothing to remove."
    data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    if c.SERVER_NAME not in data.get("mcpServers", {}):
        return f"Claude Desktop: no '{c.SERVER_NAME}' entry in {path}."
    bak = c.backup(path)
    del data["mcpServers"][c.SERVER_NAME]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"Claude Desktop: removed '{c.SERVER_NAME}' from {path} (backup: {bak.name})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Register the Feedly MCP server in Claude Desktop.")
    ap.add_argument("--remove", action="store_true", help="remove the entry instead of adding it")
    ap.add_argument("--test", action="store_true", help="run a live server test afterwards")
    ap.add_argument("--config", type=Path, default=None, help="config file (default: %%APPDATA%%/Claude/claude_desktop_config.json)")
    args = ap.parse_args(argv)
    path = args.config or c.claude_config_path()

    try:
        if args.remove:
            print(remove(path))
            return 0
        cmd, a = c.client_command()
        ok, why = c.dependencies_ok(c.console_python(cmd))
        if not c.LAUNCHER.exists() or not ok:
            print("The server is not ready for Claude Desktop: "
                  + ("launcher not found." if not c.LAUNCHER.exists() else
                     f"the Python at {c.console_python(cmd)} lacks the dependencies ({why}). "
                     f"Install them with: {c.console_python(cmd)} -m pip install -r \"{c.ROOT / 'requirements.txt'}\""))
            return 1
        print(c.register_claude(cmd, a, path))
        print(f"  command = {cmd}")
        print(f"  args    = {a}")
        print("Fully quit and restart Claude Desktop to load the server.")
    except Exception as e:  # noqa: BLE001
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1

    if args.test:
        for line in c.test_server():
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
