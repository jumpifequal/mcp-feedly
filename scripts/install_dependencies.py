#!/usr/bin/env python3
"""Install the Python dependencies the Feedly MCP server needs.

  python scripts/install_dependencies.py          # install (if missing) and verify
  python scripts/install_dependencies.py --check  # only verify, change nothing
  python scripts/install_dependencies.py --python PATH   # target a specific interpreter

* --python PATH: `pip install -r requirements.txt` into that interpreter (for
  example the system Python that setup_codex registers).
* packaged copy (extracted distribution zip): same, into the Python running
  this script - the interpreter the setup scripts register.
* source checkout: creates the local `.venv` support environment and installs
  the project in editable mode.

Exit codes: 0 ready, 1 failed / not ready.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install the dependencies of the Feedly MCP server.")
    ap.add_argument("--check", action="store_true", help="only verify, do not install")
    ap.add_argument("--python", help="install into this interpreter instead of the default one")
    args = ap.parse_args(argv)

    py = args.python or (sys.executable if c.is_packaged() else str(c.server_command()[0]))
    ok, why = c.dependencies_ok(py, refresh=True)
    if ok:
        print(f"Dependencies are installed for {py}.")
        return 0
    print(f"Dependencies missing for {py}: {why}")
    if args.check:
        return 1
    ok = c.install_requirements(py, print) if args.python else c.prepare_environment(print)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
