#!/usr/bin/env python3
"""Save the Feedly tokens as user environment variables.

Interactive (default): prompts for the access and refresh token (what you type
is visible - this is your own machine). An empty answer keeps the current value. The tokens are validated with a
read-only call to Feedly before saving.

  python scripts/set_feedly_token.py
  python scripts/set_feedly_token.py --no-validate

Silent mode, used by the MCP server after it renewed the access token:

  python scripts/set_feedly_token.py --silent

reads FEEDLY_NEW_ACCESS_TOKEN (required) and FEEDLY_NEW_REFRESH_TOKEN (optional)
from the *process environment* - never from the command line - and saves them
as FEEDLY_ACCESS_TOKEN / FEEDLY_REFRESH_TOKEN. Exit codes: 0 ok, 1 save error,
2 nothing to save.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402


def silent() -> int:
    access = os.environ.get("FEEDLY_NEW_ACCESS_TOKEN", "").strip()
    refresh = os.environ.get("FEEDLY_NEW_REFRESH_TOKEN", "").strip()
    if not access:
        return 2
    try:
        c.set_user_env("FEEDLY_ACCESS_TOKEN", access)
        if refresh:
            c.set_user_env("FEEDLY_REFRESH_TOKEN", refresh)
    except Exception:
        return 1
    return 0


def interactive(validate: bool) -> int:
    print("Feedly token setup")
    print(f"Get your tokens at {c.TOKEN_PAGE} (signed in to Feedly).")
    print("Leave a prompt empty to keep the current value.\n")
    access = input("Access token  (FEEDLY_ACCESS_TOKEN):  ").strip()
    refresh = input("Refresh token (FEEDLY_REFRESH_TOKEN): ").strip()
    if not access and not refresh:
        print("Nothing entered. Nothing was changed.")
        return 2

    if validate:
        a = access or c.get_user_env("FEEDLY_ACCESS_TOKEN")
        r = refresh or c.get_user_env("FEEDLY_REFRESH_TOKEN")
        ok, msg, _ = c.validate_tokens(a, r)
        print(("OK:   " if ok else "FAIL: ") + msg)
        if not ok and input("Save anyway? [y/N] ").strip().lower() != "y":
            print("Nothing was changed.")
            return 1

    try:
        if access:
            c.set_user_env("FEEDLY_ACCESS_TOKEN", access)
            print("FEEDLY_ACCESS_TOKEN saved for your user account.")
        if refresh:
            c.set_user_env("FEEDLY_REFRESH_TOKEN", refresh)
            print("FEEDLY_REFRESH_TOKEN saved for your user account.")
    except Exception as e:  # noqa: BLE001
        print(f"Failed to save: {e}")
        return 1
    print("\nFully quit and restart Claude Desktop / Codex / your terminal for the change to take effect.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Save Feedly tokens as user environment variables.")
    ap.add_argument("--silent", action="store_true",
                    help="non-interactive: read FEEDLY_NEW_ACCESS_TOKEN / FEEDLY_NEW_REFRESH_TOKEN from the environment")
    ap.add_argument("--no-validate", action="store_true", help="skip the read-only Feedly check")
    args = ap.parse_args(argv)
    return silent() if args.silent else interactive(validate=not args.no_validate)


if __name__ == "__main__":
    sys.exit(main())
