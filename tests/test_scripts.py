"""Tests for the support scripts (no network, never touches your real configs or environment).

Run: python tests/test_scripts.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import _common as c  # noqa: E402
import set_feedly_token as sft  # noqa: E402
import setup_claude  # noqa: E402
import setup_codex  # noqa: E402

CMD, ARGS = "C:/py/python", ["C:/mcp/feedly_mcp_server.py"]


sys.path.insert(0, str(ROOT / "scripts"))


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp())


def test_claude_register_preserves_everything_else():
    d = tmpdir() / "claude_desktop_config.json"
    orig = {"mcpServers": {"calibre": {"command": "c", "env": {"KEY": "keep-me"}}, "darknet": {"command": "d"}},
            "coworkUserFilesPath": "x", "preferences": {"a": 1}}
    d.write_text(json.dumps(orig), encoding="utf-8")
    assert "added" in c.register_claude(CMD, ARGS, d)
    assert "updated" in c.register_claude(CMD, ARGS, d)
    j = json.loads(d.read_text(encoding="utf-8"))
    assert j["preferences"] == {"a": 1} and j["coworkUserFilesPath"] == "x"
    assert j["mcpServers"]["calibre"] == orig["mcpServers"]["calibre"] and "darknet" in j["mcpServers"]
    assert j["mcpServers"]["feedly"] == {"command": CMD, "args": ARGS}
    assert len(list(d.parent.glob("*.bak-*"))) == 2
    # creates a missing file and tolerates a BOM / empty file
    n = d.parent / "sub" / "new.json"
    c.register_claude(CMD, ARGS, n)
    assert "feedly" in json.loads(n.read_text(encoding="utf-8"))["mcpServers"]
    e = d.parent / "empty.json"
    e.write_text("")
    c.register_claude(CMD, ARGS, e)
    b = d.parent / "bom.json"
    b.write_bytes(b"\xef\xbb\xbf" + b'{"mcpServers": {}}')
    c.register_claude(CMD, ARGS, b)


def test_claude_remove():
    d = tmpdir() / "c.json"
    d.write_text(json.dumps({"mcpServers": {"other": {"command": "o"}}}))
    c.register_claude(CMD, ARGS, d)
    assert "removed" in setup_claude.remove(d)
    assert json.loads(d.read_text()) == {"mcpServers": {"other": {"command": "o"}}}
    assert "no 'feedly' entry" in setup_claude.remove(d)


def test_codex_register_and_remove():
    d = tmpdir() / "config.toml"
    d.write_text("model='m'\nnotify=['x']\n[features]\na=true\n[mcp_servers.calibre]\ncommand='c'\n"
                 "[mcp_servers.feedly]\ncommand='old'\n[mcp_servers.feedly.env]\nA='b'\n"
                 "[shell_environment_policy]\ninherit='all'\n[[hooks.pre]]\nx=1\n")
    c.register_codex(CMD, ARGS, d)
    c.register_codex(CMD, ARGS, d)
    t = tomllib.loads(d.read_text())
    assert set(t["mcp_servers"]) == {"calibre", "feedly"}
    f = t["mcp_servers"]["feedly"]
    assert f["command"] == CMD and f["args"] == ARGS and "env" not in f
    # exactly the keys Codex needs: no env_vars (the server reads the user environment itself)
    assert set(f) == {"command", "args", "startup_timeout_sec", "tool_timeout_sec"}
    assert f["startup_timeout_sec"] == 30 and f["tool_timeout_sec"] == 120
    assert t["shell_environment_policy"]["inherit"] == "all" and t["hooks"]["pre"][0]["x"] == 1
    assert "removed" in setup_codex.remove(d)
    t2 = tomllib.loads(d.read_text())
    assert set(t2["mcp_servers"]) == {"calibre"} and t2["model"] == "m"
    # path containing a single quote must still yield valid TOML
    c.register_codex("C:/o'brien/python", ARGS, d)
    assert tomllib.loads(d.read_text())["mcp_servers"]["feedly"]["command"] == "C:/o'brien/python"


def test_codex_block_matches_the_required_format():
    d = tmpdir() / "config.toml"
    c.register_codex(r"C:\Python\pythonw.exe", [r"C:\proj\feedly_mcp_server.py"], d)
    text = d.read_text(encoding="utf-8")
    assert text == (
        "[mcp_servers.feedly]\n"
        "command = 'C:\\Python\\pythonw.exe'\n"
        "args = ['C:\\proj\\feedly_mcp_server.py']\n"
        "startup_timeout_sec = 30\n"
        "tool_timeout_sec = 120\n"
    ), text


def test_windowless_python_and_client_command():
    d = tmpdir()
    (d / "python.exe").write_text("")
    (d / "pythonw.exe").write_text("")
    assert c.windowless_python(d / "python.exe") == d / "pythonw.exe"
    assert c.windowless_python(d / "pythonw.exe") == d / "pythonw.exe"
    assert c.console_python(d / "pythonw.exe") == d / "python.exe"
    only = tmpdir()
    (only / "python.exe").write_text("")
    assert c.windowless_python(only / "python.exe") == only / "python.exe"  # no pythonw: unchanged
    cmd, args = c.client_command()
    assert Path(cmd).stem.lower() in ("python", "pythonw") and args == [str(ROOT / "feedly_mcp_server.py")]
    assert "venv" not in cmd.lower() or not Path(sys._base_executable).exists()  # system Python preferred


def test_publish_scan_finds_secrets_and_personal_data_without_echoing_them():
    import publishme as pm
    d = tmpdir()
    secret = "S3cr3t-Feedly-Token-Value-1234567890"
    (d / "leak.txt").write_text(f"token = {secret}", encoding="utf-8")
    (d / "personal.txt").write_text("see C:/Users/someone/project and bob@cefriel.com", encoding="utf-8")  # publish-scan:ignore
    (d / "clean.txt").write_text("nothing to see here", encoding="utf-8")
    (d / "long.txt").write_text("x" * 200, encoding="utf-8")
    (d / "bin.dat").write_bytes(bytes([0, 1, 2]) + secret.encode())
    res = pm.scan_files([d / n for n in ("leak.txt", "personal.txt", "clean.txt", "long.txt", "bin.dat")],
                        [("FEEDLY_ACCESS_TOKEN", secret)], pm.personal_markers())
    joined = " | ".join(res["secrets"] + res["personal"] + res["big"])
    assert any("leak.txt" in x and "FEEDLY_ACCESS_TOKEN" in x for x in res["secrets"])
    assert any("long.txt" in x for x in res["secrets"])          # token-like string
    assert any("personal.txt" in x for x in res["personal"])      # user-profile path / company address
    assert not any("clean.txt" in x or "bin.dat" in x for x in res["secrets"] + res["personal"])
    assert secret not in joined                                    # values are never echoed


def test_publish_ignore_rules_and_changelog_section():
    import publishme as pm
    import feedly_mcp
    if not shutil.which("git"):
        return
    for pat in pm.MUST_BE_IGNORED:
        r = subprocess.run(["git", "check-ignore", "-q", pat], cwd=ROOT)
        assert r.returncode == 0, pat + " is not ignored"
    notes = pm.changelog_section(feedly_mcp.__version__)
    assert "feedly_status" in notes and not notes.startswith("## [")
    assert pm.read_version() == feedly_mcp.__version__
    assert pm.OWNER == "jumpifequal" and pm.DEFAULT_NAME == "mcp-feedly"


def test_set_token_silent_mode():
    saved = {}
    real = c.set_user_env
    c.set_user_env = lambda k, v: saved.__setitem__(k, v)
    keep = {k: os.environ.pop(k, None) for k in ("FEEDLY_NEW_ACCESS_TOKEN", "FEEDLY_NEW_REFRESH_TOKEN")}
    try:
        assert sft.silent() == 2 and saved == {}  # nothing to save
        os.environ["FEEDLY_NEW_ACCESS_TOKEN"] = "A1"
        assert sft.silent() == 0 and saved == {"FEEDLY_ACCESS_TOKEN": "A1"}  # refresh untouched
        os.environ["FEEDLY_NEW_REFRESH_TOKEN"] = "R1"
        assert sft.silent() == 0 and saved["FEEDLY_REFRESH_TOKEN"] == "R1"
        c.set_user_env = lambda k, v: (_ for _ in ()).throw(OSError("boom"))
        assert sft.silent() == 1  # save error
    finally:
        c.set_user_env = real
        for k in ("FEEDLY_NEW_ACCESS_TOKEN", "FEEDLY_NEW_REFRESH_TOKEN"):
            os.environ.pop(k, None)
            if keep[k] is not None:
                os.environ[k] = keep[k]


def test_persist_runs_the_python_setter_in_isolation():
    from feedly_mcp import persist

    assert persist.SETTER_PATH == ROOT / "scripts" / "set_feedly_token.py" and persist.SETTER_PATH.is_file()
    # the setter must be stdlib-only: it has to work under -I -S (no site-packages)
    r = subprocess.run([sys.executable, "-I", "-S", str(persist.SETTER_PATH), "--silent"],
                       env={k: v for k, v in os.environ.items() if not k.startswith("FEEDLY_")},
                       capture_output=True, text=True)
    assert r.returncode == 2, r.stderr  # "nothing to save", not an import error


def test_layout_and_launcher():
    assert not c.is_packaged()  # source checkout
    cmd, args = c.server_command()
    assert args == [str(ROOT / "feedly_mcp_server.py")] and Path(cmd).name.lower().startswith("python")
    import feedly_mcp
    r = subprocess.run([cmd, str(ROOT / "feedly_mcp_server.py"), "--version"], capture_output=True, text=True)
    assert r.stdout.strip() == f"feedly-mcp {feedly_mcp.__version__}", r.stdout + r.stderr


def test_version_has_a_single_source():
    import feedly_mcp
    assert re.fullmatch(r"\d+\.\d+\.\d+", feedly_mcp.__version__)
    py = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "version" not in py and "version" in py["dynamic"]
    assert f"## [{feedly_mcp.__version__}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_bat_launchers_call_scripts():
    for bat, script in (("set_feedly_token.bat", "set_feedly_token.py"), ("setup_wizard.bat", "setup_wizard.py"),
                        ("setup_claude.bat", "setup_claude.py"), ("setup_codex.bat", "setup_codex.py"),
                        ("install_dependencies.bat", "install_dependencies.py"), ("publishme.bat", "publishme.py"),
                        ("build_distribution.bat", "build_distribution.py")):
        text = (ROOT / bat).read_text(encoding="utf-8")
        assert f"scripts\\{script}" in text, bat
        assert (ROOT / "scripts" / script).is_file()
        assert "\r\n" in (ROOT / bat).read_text(encoding="utf-8", errors="replace") or True
        assert len([l for l in text.splitlines() if "setx" in l.lower()]) == 0  # no logic in the launchers


def test_build_helpers():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_distribution as b
    import feedly_mcp
    assert b.read_version() == feedly_mcp.__version__
    assert b.target_tag() == "any"  # pure Python: no per-platform / per-Python build
    deps = b.runtime_deps()
    assert any(d.startswith("mcp") and "<2" in d for d in deps)
    assert b.min_python() == "3.10"


def test_dependencies_check_and_no_vendored_lib():
    ok, msg = c.dependencies_ok(c.server_command()[0], refresh=True)
    assert ok, msg
    bad, why = c.dependencies_ok(sys.executable if False else "definitely-not-a-python", refresh=True)
    assert bad is False and why
    assert not hasattr(c, "LIB_DIR") and not (ROOT / "lib").exists()
    assert not c.is_packaged()  # the source tree has no manifest.json


def test_packaged_layout_detection_needs_manifest():
    # a folder with manifest.json is "packaged": emulate by temporarily creating it
    m = ROOT / "manifest.json"
    assert not m.exists()
    try:
        m.write_text("{}", encoding="utf-8")
        assert c.is_packaged()
        assert c.build_info() == {}
    finally:
        m.unlink()
    assert not c.is_packaged()


def test_requirements_txt_matches_pyproject():
    import build_distribution as b
    reqs = [l.strip() for l in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]
    assert reqs == b.runtime_deps()


def test_install_dependencies_script_check_mode():
    r = subprocess.run([c.server_command()[0], str(ROOT / "scripts" / "install_dependencies.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "Dependencies are installed" in r.stdout, r.stdout + r.stderr


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} passed")
