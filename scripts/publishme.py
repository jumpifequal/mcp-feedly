#!/usr/bin/env python3
"""Publish this project as a NEW repository on your GitHub account.

  python scripts/publishme.py                 # interactive: checks, plan, confirmation, publish
  python scripts/publishme.py --dry-run       # checks + plan only, changes nothing
  python scripts/publishme.py --public        # visibility (asked interactively if omitted; default private)
  python scripts/publishme.py --name mcp-feedly --owner jumpifequal
  python scripts/publishme.py --release       # also attach distribution/feedly-mcp-<version>.zip as a GitHub release
  python scripts/publishme.py --existing      # push into a repository that already exists (empty one created on github.com)
  python scripts/publishme.py --yes           # skip the confirmation prompts

Run again later to publish updates: once `origin` points at the target repository
the script only commits and pushes.

What it does (nothing is sent anywhere before you confirm):
  1. read-only checks: git + GitHub CLI (`gh`) signed in as the owner, repository does not
     exist yet, no token values / personal data in the files that would be committed, the
     ignore rules, the test suites
  2. shows the plan (account, repository, visibility, commit author, files)
  3. renames the current `origin` (the upstream project this was cloned from) to `upstream`
     so nothing can be pushed there, commits your work, creates the repository and pushes

The existing history (including the upstream authors' commits) is kept, which preserves
the attribution the GPL-3.0 licence requires. New commits use your GitHub no-reply address
by default so your private e-mail is not published; `--email keep` uses your git identity.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

ROOT = c.ROOT
OWNER = "jumpifequal"
DEFAULT_NAME = "mcp-feedly"
DEFAULT_DESCRIPTION = ("MCP server for Feedly: read, search, save, tag and organize your feeds from Claude, "
                       "Codex and any MCP client (stdio or bearer-protected HTTP, self-renewing token)")
TOPICS = ["mcp", "model-context-protocol", "feedly", "rss", "python", "claude", "codex"]
MUST_BE_IGNORED = [".venv/", "distribution/", "feedly-mcp-token.log", "config.json.bak-20260101"]
SECRET_LIKE = re.compile(r"[A-Za-z0-9_\-\.]{150,}")
BIG_FILE = 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def say(msg: str = "") -> None:
    print(msg, flush=True)


def run(cmd: list[str], cwd: Path = ROOT, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return r


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return run(["git", *args], check=check)


def gh(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return run(["gh", *args], check=check)


def read_version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "feedly_mcp" / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1) if m else "0.0.0"


def candidate_files() -> list[Path]:
    """Files that `git add -A` would commit: tracked + untracked, minus ignored, that exist."""
    r = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", check=True)
    paths = [ROOT / p for p in r.stdout.split("\0") if p]
    return [p for p in paths if p.is_file()]


def token_values() -> list[tuple[str, str]]:
    """The user's real secrets (never printed): Feedly tokens and the HTTP bearer token."""
    out = []
    for name in (*c.TOKEN_VARS, "MCP_BEARER_TOKEN"):
        for val in (os.environ.get(name, ""), c.get_user_env(name)):
            if len(val) >= 20 and (name, val) not in out:
                out.append((name, val))
    return out


def personal_markers() -> list[tuple[str, re.Pattern]]:
    """Patterns of local/personal data, computed from this machine (nothing hard-coded)."""
    import getpass
    names = {getpass.getuser(), Path.home().name} - {""}
    pats = [(f"user name '{n}'", re.compile(re.escape(n), re.I)) for n in sorted(names)]
    pats.append(("a Cefriel address", re.compile(r"@cefriel\.com", re.I)))
    pats.append(("your local project path", re.compile(re.escape(str(ROOT)).replace(r"\\", r"[\\/]{1,2}"), re.I)))
    pats.append(("a Windows user-profile path", re.compile(r"C:[\\/]{1,2}Users[\\/]{1,2}", re.I)))
    return pats


def scan_files(files: list[Path], secrets: list[tuple[str, str]], markers: list[tuple[str, re.Pattern]]) -> dict[str, list[str]]:
    """Pure function used by the preflight and by the tests. Never returns secret values."""
    res: dict[str, list[str]] = {"secrets": [], "personal": [], "big": []}
    for f in files:
        rel = f.relative_to(ROOT).as_posix() if ROOT in f.parents else f.name
        try:
            size = f.stat().st_size
            if size > BIG_FILE:
                res["big"].append(f"{rel} ({size / 1e6:.1f} MB)")
            data = f.read_bytes()[: 2 * 1024 * 1024]
        except OSError:
            continue
        if b"\0" in data:
            continue  # binary
        text = data.decode("utf-8", errors="replace")
        # lines tagged with the ignore marker (scanner patterns, test fixtures) are not scanned
        text = chr(10).join(l for l in text.splitlines() if "publish-scan:" + "ignore" not in l)
        for name, val in secrets:
            if val in text:
                res["secrets"].append(f"{rel}: contains the value of {name}")
        if SECRET_LIKE.search(text):
            res["secrets"].append(f"{rel}: contains a long token-like string")
        for label, pat in markers:
            if pat.search(text):
                res["personal"].append(f"{rel}: mentions {label}")
    return res


def git_push(*args: str) -> subprocess.CompletedProcess:
    """git push that authenticates through the GitHub CLI for this command only (no global config change)."""
    return git("-c", "credential.https://github.com.helper=", "-c",
               "credential.https://github.com.helper=!gh auth git-credential", "push", *args)


def changelog_section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## \[{re.escape(version)}\].*?$(.*?)(?=^## \[|\Z)", text, re.S | re.M)
    return (m.group(1).strip() if m else f"mcp-feedly {version}") + "\n"


def ask(prompt: str, default: str = "") -> str:
    try:
        return input(prompt).strip() or default
    except EOFError:
        return default


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publish this project as a new GitHub repository.")
    ap.add_argument("--owner", default=OWNER, help=f"GitHub account (default {OWNER})")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"repository name (default {DEFAULT_NAME})")
    vis = ap.add_mutually_exclusive_group()
    vis.add_argument("--public", action="store_true", help="create a public repository")
    vis.add_argument("--private", action="store_true", help="create a private repository")
    ap.add_argument("--description", default=DEFAULT_DESCRIPTION)
    ap.add_argument("--email", choices=["noreply", "keep"], default="noreply",
                    help="author of the new commit: your GitHub no-reply address (default) or your git identity")
    ap.add_argument("--existing", action="store_true", help="push into an existing (empty) repository")
    ap.add_argument("--release", action="store_true", help="also create a GitHub release with the distribution zip")
    ap.add_argument("--skip-tests", action="store_true", help="do not run the test suites first")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--dry-run", action="store_true", help="checks and plan only; change nothing")
    args = ap.parse_args(argv)

    repo = f"{args.owner}/{args.name}"
    url = f"https://github.com/{repo}"
    version = read_version()
    problems: list[str] = []
    warnings: list[str] = []

    say(f"Publish mcp-feedly {version} to {url}")
    say("-" * 60)

    # ---- 1. read-only checks --------------------------------------------------------------
    if not shutil.which("git"):
        say("git is not installed (https://git-scm.com/download/win).")
        return 1
    if not shutil.which("gh"):
        say("The GitHub CLI is not installed (https://cli.github.com/). Install it, run `gh auth login`, and retry.")
        return 1
    if not (ROOT / ".git").exists():
        say("This folder is not a git repository (no .git).")
        return 1

    who = gh("api", "user", "--jq", ".login")
    login = who.stdout.strip()
    if who.returncode or not login:
        say("The GitHub CLI is not signed in. Run `gh auth login`, then retry.")
        return 1
    if login.lower() != args.owner.lower():
        problems.append(f"gh is signed in as '{login}', not '{args.owner}'")
    say(f"[ok] GitHub CLI signed in as {login}")

    origin = git("remote", "get-url", "origin").stdout.strip()
    update_mode = origin.rstrip("/").removesuffix(".git").lower() == url.lower()
    exists = gh("repo", "view", repo, "--json", "name").returncode == 0
    if update_mode:
        say(f"[ok] origin already points at {url}: this run will only commit and push updates")
    elif exists and not args.existing:
        problems.append(f"{repo} already exists on GitHub (use --existing to push into it, or --name for another name)")
    elif args.existing and not exists:
        problems.append(f"--existing was given but {repo} does not exist")
    else:
        say(f"[ok] {repo} " + ("exists (will push into it)" if exists else "does not exist yet (will be created)"))

    branch = git("branch", "--show-current").stdout.strip() or "(detached)"
    files = candidate_files()
    say(f"[ok] {len(files)} files would be committed (ignored files are excluded)")

    # ignore rules
    for pat in MUST_BE_IGNORED:
        if git("check-ignore", "-q", pat).returncode != 0:
            problems.append(f"'{pat}' is not ignored by .gitignore")
    tracked_bad = [p for p in git("ls-files").stdout.splitlines() if p.startswith((".venv/", "distribution/"))]
    if tracked_bad:
        problems.append(f"already tracked but should not be: {tracked_bad[:3]}")

    found = scan_files(files, token_values(), personal_markers())
    for s in found["secrets"]:
        problems.append("SECRET: " + s)
    for s in found["personal"]:
        warnings.append("personal/local data: " + s)
    for s in found["big"]:
        warnings.append("large file: " + s)
    if not found["secrets"]:
        say("[ok] no token values or token-like strings in the files to commit")
    if not found["personal"]:
        say("[ok] no local paths, user name or company address in the files to commit")

    if not args.skip_tests:
        py = str(c.venv_python()) if c.venv_python().exists() else sys.executable
        bad_tests = []
        for t in ("tests/test_offline.py", "tests/test_scripts.py"):
            r = run([py, str(ROOT / t)])
            if r.returncode or "passed" not in r.stdout:
                bad_tests.append(t)
        if bad_tests:
            problems.append(f"tests failed: {bad_tests}")
        else:
            say("[ok] test suites pass")

    # ---- plan -----------------------------------------------------------------------------
    gh_id = gh("api", "user", "--jq", ".id").stdout.strip()
    if args.email == "noreply" and gh_id.isdigit():
        author_name, author_email = login, f"{gh_id}+{login}@users.noreply.github.com"
    else:
        author_name = git("config", "user.name").stdout.strip()
        author_email = git("config", "user.email").stdout.strip()

    visibility = "public" if args.public else "private" if args.private else None
    if visibility is None and not update_mode and not exists:
        if args.yes or args.dry_run:
            visibility = "private"
        else:
            visibility = "public" if ask("Visibility - public or private? [private]: ", "private").lower().startswith("pub") else "private"

    dirty = len(git("status", "--short").stdout.splitlines())
    say("")
    say("PLAN")
    say(f"  repository : {url}" + (f"  ({visibility})" if visibility and not (update_mode or exists) else ""))
    say(f"  branch     : {branch} -> {args.branch}")
    say(f"  history    : kept ({git('rev-list', '--count', 'HEAD').stdout.strip()} existing commits incl. the upstream authors')")
    say(f"  commit     : {dirty} changed/untracked entries -> 1 new commit by {author_name} <{author_email}>")
    if not update_mode and origin:
        say(f"  remotes    : '{origin}' is renamed 'upstream'; '{url}.git' becomes 'origin'")
    say(f"  release    : " + (f"v{version} with the distribution zip" if args.release else "no"))
    for w in warnings:
        say("  WARNING    : " + w)
    for p in problems:
        say("  BLOCKER    : " + p)

    if problems:
        say("\nFix the blockers above and run again. Nothing was changed.")
        return 1
    if args.dry_run:
        say("\nDry run: nothing was changed.")
        return 0
    if warnings and not args.yes:
        if ask("Warnings above. Continue anyway? [y/N]: ", "n").lower() != "y":
            say("Cancelled. Nothing was changed.")
            return 1
    if not args.yes:
        if ask(f"Type 'publish' to push to {url}: ") != "publish":
            say("Cancelled. Nothing was changed.")
            return 1

    # ---- 2. do it --------------------------------------------------------------------------
    zip_path = ROOT / "distribution" / f"feedly-mcp-{version}.zip"
    if args.release and not zip_path.exists():
        say(f"The zip {zip_path.name} does not exist. Run build_distribution.bat first (or publish without --release).")
        return 1

    renamed = False
    try:
        if not update_mode and origin and not git("remote", "get-url", "upstream").returncode == 0:
            git("remote", "rename", "origin", "upstream", check=True)
            renamed = True
        elif not update_mode and origin:
            raise RuntimeError("a remote named 'upstream' already exists; remove or rename it, then retry")

        git("add", "-A", check=True)
        if git("diff", "--cached", "--quiet").returncode != 0:
            msg = f"Publish mcp-feedly {version}\n\nSee CHANGELOG.md for the changes in this version."
            git("-c", f"user.name={author_name}", "-c", f"user.email={author_email}", "commit", "-m", msg, check=True)
            say("[done] committed")
        else:
            say("[done] nothing new to commit")

        if not update_mode and not exists:
            gh("repo", "create", repo, f"--{visibility}", "--description", args.description,
               "--source", str(ROOT), "--remote", "origin", check=True)
            say(f"[done] created {url} ({visibility})")
        elif not update_mode:
            git("remote", "add", "origin", url + ".git", check=True)

        r = git_push("-u", "origin", f"HEAD:{args.branch}")
        if r.returncode:
            raise RuntimeError("git push failed: " + (r.stderr or r.stdout).strip()[-500:])
        say(f"[done] pushed to {url}")

        if not update_mode:
            for t in TOPICS:
                gh("repo", "edit", repo, "--add-topic", t)

        if args.release:
            notes = Path(tempfile.mkdtemp()) / "notes.md"
            notes.write_text(changelog_section(version), encoding="utf-8")
            files_to_attach = [str(zip_path)]
            sha = zip_path.with_name(zip_path.name + ".sha256")
            if sha.exists():
                files_to_attach.append(str(sha))
            gh("release", "create", f"v{version}", *files_to_attach, "--repo", repo, "--target", args.branch,
               "--title", f"mcp-feedly {version}", "--notes-file", str(notes), check=True)
            say(f"[done] release v{version} created with the distribution zip")
    except Exception as e:  # noqa: BLE001
        say(f"\nFAILED: {e}")
        if renamed and git("remote", "get-url", "origin").returncode != 0:
            git("remote", "rename", "upstream", "origin")
            say("(the remote 'origin' was restored)")
        return 1

    say(f"\nPublished: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
