# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-11-20

### Added

- `feedly_status` tool and `--version` flag: package version, Python/platform, transport, uptime, tool inventory, token configuration flags and the distribution build summary. No Feedly call, no credentials, no local paths. The MCP handshake now reports this package's version instead of the SDK's.
- `manifest.json` in every distribution: version, build time, entry point, requirements, environment variables, the full tool list and the SHA-256 of every file.
- Distributable zip `distribution/feedly-mcp-<version>.zip` (about 70 KB, pure Python, any Python >= 3.10) with `.sha256`, `VERIFICATION.json` and install notes, produced by `scripts/build_distribution.py`. It bundles **no third-party libraries**: `requirements.txt` lists the dependencies. The build verifies the zip, including a clean-room check that installs only `requirements.txt` into a fresh virtual environment. The package is assembled in a temporary staging folder; no release folder is kept.
- `scripts/install_dependencies.py` (`--check`, `--python PATH`), `scripts/setup_claude.py` and `scripts/setup_codex.py` (register / `--remove` / `--test`), `scripts/build_distribution.py`, and a committed `requirements.txt` (a test keeps it equal to `pyproject.toml`).
- `scripts/publishme.py` and `publishme.bat`: publish the project as a new GitHub repository (read-only preflight with secret / personal-data scan and test run, plan, explicit confirmation, `--dry-run`, `--release`, `--existing`). It retargets `origin` to the new repository and keeps the upstream as `upstream`, publishes with the GitHub no-reply address, and detects a `GITHUB_TOKEN` that cannot create repositories, switching to the stored `gh auth login` credentials.
- Project logo (`assets/logo.svg`).

### Changed

- New layout: the MCP package (`feedly_mcp/`) and launcher (`feedly_mcp_server.py`) live in the project root; every support script is Python under `scripts/`; the `.bat` files are thin launchers that call them. `.venv` is only a local support environment.
- Claude Desktop and Codex are both registered with the system Python's windowless interpreter (`pythonw`) plus the launcher. The Codex block is exactly `command`, `args`, `startup_timeout_sec = 30`, `tool_timeout_sec = 120`: no `env_vars`, because the server reads the tokens from the user environment itself.
- `set_feedly_token.py` replaces the batch logic (hidden-input prompts, read-only validation, `--silent` mode used by the server after a token renewal).
- The setup wizard registers Claude Desktop (`claude_desktop_config.json`) and Codex, installs the dependencies in step 1, and works from both the source tree and an extracted distribution.
- Dependency declaration: `mcp>=1.9,<2` (mcp 2.x renamed `FastMCP`); the version has a single source in `feedly_mcp/__init__.py`.

## [1.1.0] - 2026-11-19

### Added

- 19 new tools (31 in total): save/unsave, tag/untag/rename/delete tags, subscribe/unsubscribe, rename/delete categories, feed details, feed search, full-text article search, most-engaging articles, recent reads, OPML export/import, authentication status and forced token renewal.
- Automatic access-token renewal from `FEEDLY_REFRESH_TOKEN` on a 401, persistence of the renewed token to the user environment, and a metadata-only audit log (`feedly-mcp-token.log`, expiry as local date/time). Falls back to the Windows user environment when the client does not pass variables through.
- Streamable HTTP transport protected by a bearer token (`--transport http`, `MCP_BEARER_TOKEN`).
- Paging, filtering and `compact` output for subscriptions and article lists.
- GUI setup wizard and offline test suite.

## [1.0.0]

- Initial upstream version: 12 tools over stdio.
