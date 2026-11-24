#!/usr/bin/env python3
"""Build the distributable zip (no release folder is kept on disk).

  python scripts/build_distribution.py                     # distribution/feedly-mcp-<version>.zip
  python scripts/build_distribution.py --skip-clean-install  # faster: skip the fresh-venv install check
  python scripts/build_distribution.py --keep-going        # do not stop at the first failed check
  python scripts/build_distribution.py --keep-staging      # keep the temporary build folder (debugging)

What the zip contains
  The pure-Python package: feedly_mcp/, the launcher, the support scripts and
  .bat launchers, docs, requirements.txt (kept in sync with pyproject.toml) and
  manifest.json. It contains NO third-party libraries: the recipient installs
  them once with `python scripts/install_dependencies.py` (or the setup wizard).
  Because it is pure Python the zip works with any Python >= 3.10.

How it works
  The package is assembled in a temporary staging folder, verified, zipped and
  the staging folder is deleted. Nothing but the outputs below stays on disk.

Outputs (distribution/)
  feedly-mcp-<version>.zip, its .sha256, VERIFICATION.json (build checks), README.txt
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    sys.exit("Python 3.11+ is required to run the build script.")

ROOT = c.ROOT
DIST = ROOT / "distribution"
STAGE = ROOT / "distribution" / ".staging"  # replaced by a temp folder in main()
MANIFEST_VERSION = 2

PACKAGE_DIRS = ["feedly_mcp"]
ROOT_FILES = ["feedly_mcp_server.py", "README.md", "LICENSE", "CHANGELOG.md", "requirements.txt"]
# local convenience launchers (git-ignored): shipped when present, never required
OPTIONAL_ROOT_FILES = ["set_feedly_token.bat", "setup_wizard.bat", "setup_claude.bat", "setup_codex.bat",
                       "install_dependencies.bat"]
SCRIPT_FILES = ["_common.py", "set_feedly_token.py", "setup_wizard.py", "setup_claude.py",
                "setup_codex.py", "install_dependencies.py"]
ASSET_FILES = ["logo.svg"]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak-*", "*.log")


# ---------------------------------------------------------------------------


def read_version() -> str:
    text = (ROOT / "feedly_mcp" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        sys.exit("Cannot read __version__ from feedly_mcp/__init__.py")
    return m.group(1)


def target_tag() -> str:
    """Pure Python: the same zip works on any platform/Python >= 3.10."""
    return "any"


def runtime_deps() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


def min_python() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return re.sub(r"[^0-9.]", "", data["project"]["requires-python"])


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def say(msg: str) -> None:
    print(msg, flush=True)


def clean_env() -> dict[str, str]:
    """Environment without Feedly credentials or Python overrides (hermetic probes)."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("FEEDLY_", "PYTHON", "VIRTUAL_ENV", "MCP_"))}
    env["FEEDLY_PERSIST_TOKEN"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts" if sys.platform == "win32" else "bin") / ("python" + Path(sys.executable).suffix)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def assemble() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for d in PACKAGE_DIRS:
        shutil.copytree(ROOT / d, STAGE / d, ignore=IGNORE)
    for f in ROOT_FILES:
        shutil.copy2(ROOT / f, STAGE / f)
    for f in OPTIONAL_ROOT_FILES:
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, STAGE / f)
    (STAGE / "scripts").mkdir()
    for f in SCRIPT_FILES:
        shutil.copy2(ROOT / "scripts" / f, STAGE / "scripts" / f)
    (STAGE / "assets").mkdir()
    for f in ASSET_FILES:
        shutil.copy2(ROOT / "assets" / f, STAGE / "assets" / f)


def probe(root: Path, python: str | Path | None = None) -> dict:
    """Start the package in `root` like a client would (isolated -I -B, no credentials),
    do the handshake, list the tools and call feedly_status (no network)."""
    py = str(python or sys.executable)
    cl = c.StdioClient(py, ["-I", "-B", str(root / "feedly_mcp_server.py")], clean_env())
    try:
        info = cl.handshake()
        tools = cl.request("tools/list")["tools"]
        status = json.loads(cl.call_tool("feedly_status", {"params": {"response_format": "json"}}))
        return {"server": info["serverInfo"], "tools": tools, "status": status}
    finally:
        cl.close()


def fresh_environment_probe(root: Path) -> tuple[list[str], dict | None]:
    """Clean-room proof that requirements.txt is sufficient: new venv, install ONLY
    requirements.txt, run the extracted package with that interpreter."""
    tmp = Path(tempfile.mkdtemp(prefix="feedly-mcp-fresh-"))
    try:
        r = subprocess.run([sys.executable, "-m", "venv", str(tmp / "venv")], capture_output=True, text=True)
        if r.returncode:
            return ["venv creation failed: " + r.stderr[-200:]], None
        py = venv_python(tmp / "venv")
        r = subprocess.run([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check", "--no-input",
                            "-r", str(root / "requirements.txt")], capture_output=True, text=True)
        if r.returncode:
            return ["pip install -r requirements.txt failed: " + (r.stderr or r.stdout)[-300:]], None
        return [], probe(root, py)
    except Exception as e:  # noqa: BLE001
        return [f"{type(e).__name__}: {e}"], None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def write_manifest(version: str, probed: dict) -> dict:
    tools = probed["tools"]
    files = []
    for p in sorted(STAGE.rglob("*")):
        rel = p.relative_to(STAGE)
        if p.is_dir() or rel.name == "manifest.json":
            continue
        files.append({"path": rel.as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)})
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "name": "feedly-mcp",
        "display_name": "mcp-feedly",
        "version": version,
        "description": "MCP server for Feedly: read, search, save, tag, organize subscriptions; "
                       "stdio or bearer-protected HTTP; self-renewing access token.",
        "license": "GPL-3.0-only",
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "target": target_tag(),
        "python": {"minimum": min_python(), "built_with": f"{sys.version_info.major}.{sys.version_info.minor}"},
        "entry": {
            "launcher": "feedly_mcp_server.py",
            "command": "python",
            "args": ["feedly_mcp_server.py"],
            "transports": ["stdio", "http"],
            "http_endpoint": "/mcp",
            "http_auth": "Authorization: Bearer <MCP_BEARER_TOKEN>",
        },
        "requirements": {
            "file": "requirements.txt",
            "packages": runtime_deps(),
            "install": "python scripts/install_dependencies.py",
        },
        "environment": {
            "required_any_of": ["FEEDLY_ACCESS_TOKEN", "FEEDLY_REFRESH_TOKEN"],
            "optional": ["FEEDLY_CLIENT_ID", "FEEDLY_CLIENT_SECRET", "FEEDLY_PERSIST_TOKEN",
                         "FEEDLY_MCP_LOG", "MCP_TRANSPORT", "MCP_HOST", "MCP_PORT", "MCP_BEARER_TOKEN"],
        },
        "tools": {
            "count": len(tools),
            "read_only": sum(1 for t in tools if (t.get("annotations") or {}).get("readOnlyHint")),
            "destructive": sum(1 for t in tools if (t.get("annotations") or {}).get("destructiveHint")),
            "names": sorted(t["name"] for t in tools),
        },
        "scripts": [f"scripts/{f}" for f in SCRIPT_FILES],
        "files": files,
    }
    (STAGE / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _leak_patterns() -> list[re.Pattern]:
    """Patterns of local/personal data, derived from THIS machine (no name is hard-coded)."""
    import getpass
    names = {getpass.getuser(), Path.home().name} - {''}
    raw = [r"C:\\Users\\", r"/Users/[a-z]", re.escape(str(ROOT))] + [re.escape(n) for n in sorted(names)]  # publish-scan:ignore
    return [re.compile(p, re.I) for p in raw]


LEAK_PATTERNS = _leak_patterns()
SECRET_LIKE = re.compile(r"[A-Za-z0-9_\-\.]{150,}")
TEXT_EXT = {".py", ".md", ".bat", ".json", ".svg", ".txt", ".toml"}


def scan_for_leaks() -> list[str]:
    bad = []
    for p in STAGE.rglob("*"):
        rel = p.relative_to(STAGE)
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT or rel.name == "manifest.json":
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for pat in LEAK_PATTERNS:
            if pat.search(text):
                bad.append(f"{rel.as_posix()}: matches {pat.pattern}")
        if SECRET_LIKE.search(text):
            bad.append(f"{rel.as_posix()}: long token-like string")
    return bad


def verify_manifest(manifest: dict, root: Path) -> list[str]:
    bad = []
    for f in manifest["files"]:
        p = root / f["path"]
        if not p.is_file():
            bad.append(f"missing {f['path']}")
        elif sha256(p) != f["sha256"]:
            bad.append(f"hash mismatch {f['path']}")
    return bad


def make_zip(version: str) -> Path:
    DIST.mkdir(exist_ok=True)
    for old in list(DIST.glob("feedly-mcp-*.zip")) + list(DIST.glob("feedly-mcp-*.zip.sha256")):
        old.unlink()  # never leave a stale zip next to the new one
    top = f"feedly-mcp-{version}"
    zpath = DIST / f"{top}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(STAGE.rglob("*")):
            if p.is_file() and p.suffix not in (".pyc", ".pyo") and "__pycache__" not in p.parts:
                z.write(p, f"{top}/{p.relative_to(STAGE).as_posix()}")
    (DIST / (zpath.name + ".sha256")).write_text(f"{sha256(zpath)}  {zpath.name}\n", encoding="utf-8")
    return zpath


DIST_README = """MCP-FEEDLY {version} - DISTRIBUTION

File to use:
  {zip}

The package is pure Python and contains no third-party libraries, so the same
zip works with any Python {minpy} or newer. Dependencies are listed in
requirements.txt and installed once on the target machine.

Installation
1. Extract the whole ZIP to a stable folder (the path is stored in the client
   configuration, so do not move it afterwards).
2. Install Python {minpy}+ if you do not have it.
3. Run  python scripts/setup_wizard.py  (GUI). Its first step installs the
   dependencies; the console equivalents are:
     python scripts/install_dependencies.py   pip install -r requirements.txt
     python scripts/set_feedly_token.py       save your Feedly access/refresh tokens
     python scripts/setup_claude.py           register in Claude Desktop
     python scripts/setup_codex.py            register in Codex
   (.bat files, when present in the zip, are shortcuts for these commands.)
4. Fully quit and restart the client.

Feedly tokens: sign in to Feedly and open https://feedly.com/v3/auth/dev
The server renews an expired access token itself from the refresh token and
saves the new one in your user environment (see feedly-mcp-token.log).

Contents
- feedly_mcp_server.py   launcher (stdio by default, --transport http)
- feedly_mcp/            the MCP server (Python source)
- scripts/               setup scripts (all Python)
- requirements.txt       the Python dependencies
- manifest.json          version, tools, requirements and SHA-256 of every file
- README.md              full documentation

The .sha256 file lets you check the integrity of the ZIP.
VERIFICATION.json documents the checks run on the extracted copy, including an
install of requirements.txt into a brand-new virtual environment.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the distributable zip in distribution/")
    ap.add_argument("--keep-going", action="store_true", help="continue after a failed check")
    ap.add_argument("--keep-staging", action="store_true", help="keep the temporary staging folder")
    ap.add_argument("--skip-clean-install", action="store_true", help="skip the fresh-venv install check")
    args = ap.parse_args(argv)

    global STAGE
    STAGE = Path(tempfile.mkdtemp(prefix="feedly-mcp-staging-")) / "package"

    version, target = read_version(), target_tag()
    say(f"Building mcp-feedly {version} ({target}, Python {min_python()}+)")
    checks: dict[str, str] = {}

    def check(name: str, problems: list[str] | str | None) -> None:
        ok = not problems
        checks[name] = "ok" if ok else "FAILED: " + (problems if isinstance(problems, str) else "; ".join(problems[:5]))
        say(f"  [{'ok' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {checks[name][8:]}"))
        if not ok and not args.keep_going:
            if not args.keep_staging:
                shutil.rmtree(STAGE.parent, ignore_errors=True)
            sys.exit(f"Check failed: {name}")

    say("1/5 assemble the package in a temporary staging folder")
    assemble()
    say("2/5 start-up tests of the staged package")
    probed = probe(STAGE)
    manifest = write_manifest(version, probed)
    probed = probe(STAGE)  # again, now with the manifest present
    st = probed["status"]
    check("starts_with_declared_dependencies", [] if probed["tools"] else "no tools")
    check("handshake_version_matches_package", [] if probed["server"]["version"] == version else
          f"handshake {probed['server']['version']} != {version}")
    check("status_tool_matches_package_version", [] if st["version"] == version and st["tools"] == len(probed["tools"]) else "mismatch")
    check("status_tool_reads_manifest", [] if (st.get("distribution") or {}).get("target") == target else "manifest not picked up")
    check("plain_python_no_bytecode", [p.relative_to(STAGE).as_posix() for p in STAGE.rglob("*")
                                        if p.suffix in (".pyc", ".pyo") or p.name == "__pycache__"][:5])
    check("no_third_party_libraries_bundled", [p.name for p in STAGE.rglob("*")
                                               if p.name in ("lib", "site-packages") or p.name.endswith(".dist-info")][:5])
    check("manifest_hashes_match_package", verify_manifest(manifest, STAGE))
    check("no_local_paths_identity_or_credentials", scan_for_leaks())
    dev_names = {".venv", "tests", "distribution", ".git", "feedly-mcp-token.log", "build_distribution.py",
                 "build_distribution.bat", "pyproject.toml"}
    check("no_dev_files_in_package", [p.relative_to(STAGE).as_posix() for p in STAGE.rglob("*")
                                      if p.name in dev_names or p.name.endswith(".bak")])
    reqs = [l.strip() for l in (STAGE / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]
    check("requirements_match_pyproject", [] if reqs == runtime_deps() else f"requirements.txt {reqs} != pyproject {runtime_deps()}")

    result = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "version": version, "target": target}
    say("3/5 create zip")
    zpath = make_zip(version)
    with zipfile.ZipFile(zpath) as z:
        check("zip_integrity", [] if z.testzip() is None else "corrupt member")
        tmp = Path(tempfile.mkdtemp(prefix="feedly-mcp-verify-"))
        z.extractall(tmp)
    extracted = tmp / f"feedly-mcp-{version}"
    say("4/5 verify the extracted copy")
    check("extracted_copy_starts", [] if probe(extracted)["status"]["version"] == version else "version mismatch")
    check("extracted_copy_matches_manifest", verify_manifest(json.loads((extracted / "manifest.json").read_text(encoding="utf-8")), extracted))
    if args.skip_clean_install:
        say("5/5 fresh-environment install check skipped")
    else:
        say("5/5 clean-room check: new venv + ONLY requirements.txt, run the extracted package")
        problems, fresh = fresh_environment_probe(extracted)
        if fresh and fresh["server"]["version"] != version:
            problems = problems + ["version mismatch in fresh environment"]
        check("fresh_environment_install_works", problems)
        if fresh:
            check("fresh_environment_lists_all_tools", [] if len(fresh["tools"]) == manifest["tools"]["count"] else "tool count differs")
    shutil.rmtree(tmp, ignore_errors=True)

    result.update({"zip": zpath.name, "sha256": sha256(zpath), "bytes": zpath.stat().st_size})
    (DIST / "README.txt").write_text(
        DIST_README.format(version=version, zip=zpath.name, minpy=min_python()), encoding="utf-8")

    failed = [k for k, v in checks.items() if v != "ok"]
    result.update({"checks": checks, "status": "failed" if failed else "passed",
                   "tools": manifest["tools"]["count"], "third_party_libraries_bundled": 0,
                   "requirements": runtime_deps(), "provider_calls": 0, "release_folder_kept": False})
    (DIST / "VERIFICATION.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    say("")
    say(f"Zip     : {DIST / result['zip']}  ({result['bytes'] / 1e3:.0f} KB)")
    say(f"SHA-256 : {result['sha256']}")
    say(f"Status  : {result['status']}  ({manifest['tools']['count']} tools, {len(runtime_deps())} requirements)")
    if args.keep_staging:
        say(f"Staging : {STAGE}")
    else:
        shutil.rmtree(STAGE.parent, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
