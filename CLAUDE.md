# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project overview

`mcp-feedly` is a Python MCP server for the Feedly API (32 tools, stdio or bearer-protected HTTP, self-renewing access token). Everything is plain Python - no compiled launchers, no console-script executables, no shipped bytecode, no bundled third-party libraries. Published at github.com/jumpifequal/mcp-feedly; based on c0ffee0wl/feedly-mcp-server (GPL-3.0, upstream history is kept).

## Layout

```
feedly_mcp_server.py   launcher: python feedly_mcp_server.py [--version] [--transport stdio|http]
feedly_mcp/            the MCP package
  server.py            FastMCP app, all tools, status tool, stdio/HTTP entry point, bearer middleware
  client.py            FeedlyClient (async httpx): error mapping, 401 -> renew -> retry
  auth.py              TokenManager: renewal via FEEDLY_REFRESH_TOKEN, registry fallback for env vars
  persist.py           saves renewed tokens (runs scripts/set_feedly_token.py --silent) + metadata-only log
  models.py            Pydantic v2 input models
  __init__.py          __version__ = single source of the version
requirements.txt       runtime dependencies (must equal [project].dependencies in pyproject.toml)
scripts/               ALL support scripts, Python only (shared helpers in _common.py)
*.bat                  thin launchers that only call scripts/*.py
assets/logo.svg  tests/  CHANGELOG.md
.venv/                 LOCAL support environment (dev/build only, never shipped)
distribution/          BUILD OUTPUT: zip + .sha256 + VERIFICATION.json + README.txt (no release folder is kept)
```

## Commands

```bash
python -m venv .venv && .venv\Scripts\python -m pip install -e .   # local support env
.venv\Scripts\python tests\test_offline.py                         # 12 tests
.venv\Scripts\python tests\test_scripts.py                         # 18 tests
.venv\Scripts\python scripts\setup_wizard.py --selftest
.venv\Scripts\python scripts\build_distribution.py                 # distribution/*.zip (all checks must pass; --skip-clean-install for speed)
python feedly_mcp_server.py --version
```

## Rules to keep

- **Plain Python only (no executables, no bytecode, no bundled libraries):** do not add `[project.scripts]` entries, compiled launchers or a `lib/` folder; the launcher sets `sys.dont_write_bytecode`; the distribution must contain no `.pyc`/`__pycache__` and no third-party packages.
- **Scripts are Python in `scripts/`**; `.bat` files contain no logic beyond locating Python and calling the script.
- **Version:** change `__version__` in `feedly_mcp/__init__.py` only, and add a `## [x.y.z]` section to `CHANGELOG.md` (a test checks both).
- **Dependencies:** declared in `pyproject.toml` and mirrored in `requirements.txt` (a test and a build check compare them); keep `mcp<2`. The build proves sufficiency by installing only `requirements.txt` into a fresh venv.
- **Client registration:** Claude Desktop and Codex both get the system Python's `pythonw` + the launcher; Codex's block is exactly command/args/startup_timeout_sec/tool_timeout_sec (no `env_vars`). No secrets in client configs.
- **Secrets:** the *server* never logs or returns token values (its renewal log holds metadata only). The local setup tools (wizard, `set_feedly_token`) may show the values the user types - it is the user's own machine; client configs still hold no tokens. Tests must not touch the real user environment (`FEEDLY_PERSIST_TOKEN=0`, temp log, temp config paths).
- **Publishing:** `publishme.bat` is the only way the project is pushed to GitHub; it scans for token values and personal data first. Never hard-code personal data (user name, e-mail, local paths) in tracked files; scanner patterns and test fixtures that must contain such text carry a `publish-scan:ignore` marker on the same line.
- README links must be explicit `[text](url)` links, not bare `<url>` autolinks.

## Feedly API

Base URL `https://cloud.feedly.com/v3`; Bearer access token from `FEEDLY_ACCESS_TOKEN`; renewal `POST /auth/token` with `FEEDLY_REFRESH_TOKEN` (client `feedlydev`). Token page: https://feedly.com/v3/auth/dev

Stream IDs: `feed/URL`, `user/{userId}/category/{label}`, `user/{userId}/tag/{label}`, `user/{userId}/category/global.all`, `user/{userId}/tag/global.saved`.
