#!/usr/bin/env python3
"""Feedly MCP setup wizard (GUI, stdlib only; lives in scripts/).

Steps: 1 Checks -> 2 Feedly tokens -> 3 Targets -> 4 Review/Apply -> 5 Test.

  python scripts/setup_wizard.py            # launch the wizard
  python scripts/setup_wizard.py --selftest # build the UI headlessly and exit (CI/dev)

What it can do
  * install the dependencies (source layout: local ./.venv; packaged copy: requirements.txt into this Python)
  * validate your Feedly tokens (read-only call; refreshes if the access token expired)
  * save tokens as user environment variables (registry, no command line exposure)
  * register the server in Claude Desktop (%APPDATA%/Claude/claude_desktop_config.json)
  * register the server in Codex (~/.codex/config.toml)
  * run a live stdio test (initialize, list tools, feedly_auth_status)

Token values you enter are shown (this is a local tool on your own machine). Config files are backed up
(<name>.bak-YYYYmmdd-HHMMSS) before they are changed.
"""

from __future__ import annotations

import os
import queue
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (  # noqa: E402
    IS_WIN, ROOT, TOKEN_PAGE, claude_config_path, codex_config_path, get_user_env,
    client_command, dependencies_ok, is_packaged, prepare_environment, register_claude, register_codex,
    server_command, server_ready, set_user_env, test_server, validate_tokens,
)

# ----------------------------------------------------------------------------
# Wizard UI
# ----------------------------------------------------------------------------

STEPS = ["Checks", "Feedly tokens", "Where to install", "Review & apply", "Test & finish"]


def run_gui(selftest: bool = False) -> int:
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ModuleNotFoundError:
        print("tkinter is not available in this Python. Install the tcl/tk component and retry.")
        return 1

    root = tk.Tk()
    root.title("Feedly MCP - Setup Wizard")
    root.geometry("760x560")
    root.minsize(680, 520)

    ev: queue.Queue = queue.Queue()  # worker thread -> UI

    state = {
        "access": tk.StringVar(),
        "refresh": tk.StringVar(),
        "show": tk.BooleanVar(value=True),
        "t_claude": tk.BooleanVar(value=True),
        "t_codex": tk.BooleanVar(value=True),
        "save_env": tk.BooleanVar(value=True),
        "validated": False,
        "applied": False,
    }

    style = ttk.Style(root)
    try:
        style.theme_use("vista" if IS_WIN else "clam")
    except tk.TclError:
        pass

    # --- chrome ---------------------------------------------------------
    header = ttk.Frame(root, padding=(16, 12))
    header.pack(fill="x")
    title_var = tk.StringVar()
    ttk.Label(header, textvariable=title_var, font=("Segoe UI", 15, "bold")).pack(anchor="w")
    crumbs = ttk.Label(header, foreground="#666")
    crumbs.pack(anchor="w", pady=(2, 0))
    ttk.Separator(root).pack(fill="x")

    body = ttk.Frame(root, padding=16)
    body.pack(fill="both", expand=True)

    ttk.Separator(root).pack(fill="x")
    nav = ttk.Frame(root, padding=(16, 10))
    nav.pack(fill="x")
    btn_cancel = ttk.Button(nav, text="Cancel", command=root.destroy)
    btn_cancel.pack(side="left")
    btn_next = ttk.Button(nav, text="Next >")
    btn_next.pack(side="right")
    btn_back = ttk.Button(nav, text="< Back")
    btn_back.pack(side="right", padx=(0, 8))

    pages: list[ttk.Frame] = []
    cur = {"i": 0}

    def make_log(parent, height=10):
        box = tk.Text(parent, height=height, wrap="word", state="disabled",
                      font=("Consolas", 9), background="#fafafa", relief="solid", borderwidth=1)
        box.pack(fill="both", expand=True, pady=(8, 0))

        def write(msg: str):
            box.configure(state="normal")
            box.insert("end", msg + "\n")
            box.see("end")
            box.configure(state="disabled")
        return box, write

    # --- page 0: checks ---------------------------------------------------
    p0 = ttk.Frame(body)
    pages.append(p0)
    ttk.Label(p0, text="Let's make sure everything is in place.", wraplength=680).pack(anchor="w")
    checks_frame = ttk.Frame(p0)
    checks_frame.pack(fill="x", pady=12)
    install_btn = ttk.Button(p0, text="Install dependencies now")
    _, log0 = make_log(p0, 8)

    def refresh_checks():
        for w in checks_frame.winfo_children():
            w.destroy()
        rows = [
            ("Python 3.10+", sys.version_info >= (3, 10), sys.version.split()[0]),
            ("Server files" + (" (packaged)" if is_packaged() else " (source)"), (ROOT / "feedly_mcp_server.py").exists(), str(ROOT)),
            ("Dependencies installed", dependencies_ok()[0],
             str(server_command()[0]) if dependencies_ok()[0] else "missing: " + dependencies_ok()[1]),
            ("Claude Desktop config", claude_config_path().exists(), str(claude_config_path())),
            ("Codex config", codex_config_path().exists(), str(codex_config_path())),
        ]
        for label, ok, detail in rows:
            f = ttk.Frame(checks_frame)
            f.pack(fill="x", pady=2)
            ttk.Label(f, text="[OK]" if ok else "[ - ]", width=6,
                      foreground="#1a7f37" if ok else "#b35900").pack(side="left")
            ttk.Label(f, text=label, width=22).pack(side="left")
            ttk.Label(f, text=detail, foreground="#666").pack(side="left")
        if server_ready():
            install_btn.pack_forget()
        else:
            install_btn.pack(anchor="w")
        update_nav()

    def do_install():
        install_btn.configure(state="disabled")
        log0("Installing ...")

        def work():
            ok = prepare_environment(lambda m: ev.put(("log0", m)))
            ev.put(("installed", ok))
        threading.Thread(target=work, daemon=True).start()

    install_btn.configure(command=do_install)

    # --- page 1: tokens ---------------------------------------------------
    p1 = ttk.Frame(body)
    pages.append(p1)
    ttk.Label(p1, wraplength=680, text=(
        "Open the Feedly token page while signed in to Feedly and paste the access "
        "token and the refresh token below. The refresh token lets the server renew an expired "
        "access token on its own. Leave a field empty to keep the value already stored.")).pack(anchor="w")

    link = ttk.Button(p1, text="Open Feedly token page",
                      command=lambda: __import__("webbrowser").open(TOKEN_PAGE))
    link.pack(anchor="w", pady=(8, 8))

    def field(label, var, var_name):
        f = ttk.Frame(p1)
        f.pack(fill="x", pady=4)
        ttk.Label(f, text=label, width=18).pack(side="left")
        e = ttk.Entry(f, textvariable=var, show="" if state["show"].get() else "*")
        e.pack(side="left", fill="x", expand=True)
        stored = "stored in your user environment" if get_user_env(var_name) else "not stored yet"
        ttk.Label(f, text=stored, foreground="#666", width=30).pack(side="left", padx=(8, 0))
        return e

    e_access = field("Access token", state["access"], "FEEDLY_ACCESS_TOKEN")
    e_refresh = field("Refresh token", state["refresh"], "FEEDLY_REFRESH_TOKEN")

    def toggle_show():
        ch = "" if state["show"].get() else "*"
        e_access.configure(show=ch)
        e_refresh.configure(show=ch)

    ttk.Checkbutton(p1, text="Show tokens", variable=state["show"], command=toggle_show).pack(anchor="w", pady=4)
    val_row = ttk.Frame(p1)
    val_row.pack(fill="x", pady=(10, 0))
    val_btn = ttk.Button(val_row, text="Validate with Feedly")
    val_btn.pack(side="left")
    val_msg = ttk.Label(val_row, text="(optional - read-only check)", foreground="#666", wraplength=520)
    val_msg.pack(side="left", padx=10)

    def effective(name_key, env_name):
        return state[name_key].get().strip() or get_user_env(env_name) or os.environ.get(env_name, "")

    def do_validate():
        a, r = effective("access", "FEEDLY_ACCESS_TOKEN"), effective("refresh", "FEEDLY_REFRESH_TOKEN")
        val_btn.configure(state="disabled")
        val_msg.configure(text="Checking ...", foreground="#666")

        def work():
            ev.put(("validated", validate_tokens(a, r)))
        threading.Thread(target=work, daemon=True).start()

    val_btn.configure(command=do_validate)

    # --- page 2: targets --------------------------------------------------
    p2 = ttk.Frame(body)
    pages.append(p2)
    ttk.Label(p2, text="Choose where to register the Feedly MCP server.", wraplength=680).pack(anchor="w")
    box = ttk.LabelFrame(p2, text="Clients", padding=12)
    box.pack(fill="x", pady=12)
    ttk.Checkbutton(box, text="Claude Desktop  (edits claude_desktop_config.json; windowless pythonw + launcher)", variable=state["t_claude"]).pack(anchor="w", pady=3)
    ttk.Checkbutton(box, text="Codex  (edits ~/.codex/config.toml; windowless pythonw + launcher)", variable=state["t_codex"]).pack(anchor="w", pady=3)
    box2 = ttk.LabelFrame(p2, text="Tokens", padding=12)
    box2.pack(fill="x")
    ttk.Checkbutton(box2, text="Save the tokens above as user environment variables (replaces existing values)",
                    variable=state["save_env"]).pack(anchor="w", pady=3)
    ttk.Label(p2, foreground="#666", wraplength=680, text=(
        "The server reads FEEDLY_ACCESS_TOKEN / FEEDLY_REFRESH_TOKEN from your user environment (it falls back to "
        "the Windows registry itself), so no secrets are written into the Claude or Codex config files. "
        "Fully quit and restart the client afterwards.")).pack(anchor="w", pady=10)

    # --- page 3: review & apply --------------------------------------------
    p3 = ttk.Frame(body)
    pages.append(p3)
    review_var = tk.StringVar()
    ttk.Label(p3, textvariable=review_var, justify="left", wraplength=680).pack(anchor="w")
    _, log3 = make_log(p3, 10)

    def build_review():
        cmd, cargs = server_command()
        lines = [f"Server:  {cmd} {' '.join(cargs)}"]
        lines.append("Claude Desktop:  " + (f"register ({client_command()[0]})" if state["t_claude"].get() else "skip"))
        lines.append("Codex:  " + (f"register ({client_command()[0]})" if state["t_codex"].get() else "skip"))
        a, r = state["access"].get().strip(), state["refresh"].get().strip()
        if state["save_env"].get() and (a or r):
            what = "; ".join(f"{n} = {v}" for n, v in (("FEEDLY_ACCESS_TOKEN", a), ("FEEDLY_REFRESH_TOKEN", r)) if v)
            lines.append(f"Environment:  save (user level)  {what}")
        else:
            lines.append("Environment:  leave unchanged")
        lines.append("\nConfig files are backed up before they are modified. Press Apply to continue.")
        review_var.set("\n".join(lines))

    def apply_all():
        btn_next.configure(state="disabled")
        btn_back.configure(state="disabled")
        a, r = state["access"].get().strip(), state["refresh"].get().strip()

        def work():
            ok = True

            def step(fn, *args):
                nonlocal ok
                try:
                    ev.put(("log3", "OK   " + fn(*args)))
                except Exception as e:  # noqa: BLE001
                    ok = False
                    ev.put(("log3", f"FAIL {fn.__name__}: {type(e).__name__}: {e}"))

            if state["save_env"].get():
                for val, name in ((a, "FEEDLY_ACCESS_TOKEN"), (r, "FEEDLY_REFRESH_TOKEN")):
                    if val:
                        step(lambda n=name, v=val: (set_user_env(n, v), f"saved {n} = {v} (user environment)")[1])
            if not server_ready():
                ok = False
                ev.put(("log3", "FAIL server is not ready - go back to step 1."))
            else:
                cmd, cargs = server_command()
                if state["t_claude"].get():
                    step(register_claude, *client_command())
                if state["t_codex"].get():
                    step(register_codex, *client_command())
            ev.put(("applied", ok))
        threading.Thread(target=work, daemon=True).start()

    # --- page 4: test & finish --------------------------------------------
    p4 = ttk.Frame(body)
    pages.append(p4)
    done_msg = ttk.Label(p4, wraplength=680, justify="left")
    done_msg.pack(anchor="w")
    test_btn = ttk.Button(p4, text="Run live test")
    test_btn.pack(anchor="w", pady=10)
    _, log4 = make_log(p4, 8)

    def do_test():
        test_btn.configure(state="disabled")
        log4("Starting the server and talking to Feedly ...")
        extra = {}
        if state["access"].get().strip():
            extra["FEEDLY_ACCESS_TOKEN"] = state["access"].get().strip()
        if state["refresh"].get().strip():
            extra["FEEDLY_REFRESH_TOKEN"] = state["refresh"].get().strip()

        def work():
            for line in test_server(extra):
                ev.put(("log4", line))
            ev.put(("tested", None))
        threading.Thread(target=work, daemon=True).start()

    test_btn.configure(command=do_test)

    # --- navigation ---------------------------------------------------------
    def show(i: int):
        cur["i"] = i
        for pg in pages:
            pg.pack_forget()
        pages[i].pack(fill="both", expand=True)
        title_var.set(STEPS[i])
        crumbs.configure(text="  >  ".join(("[" + s + "]") if k == i else s for k, s in enumerate(STEPS)))
        if i == 0:
            refresh_checks()
        if i == 3:
            build_review()
        if i == 4:
            done_msg.configure(text=("Done. Fully quit and restart Claude Desktop / Codex so they pick up the new server and "
                                     "environment variables.\n\nRun the live test to confirm the whole chain works."
                                     if state["applied"] else ""))
        update_nav()

    def update_nav():
        i = cur["i"]
        btn_back.configure(state="normal" if 0 < i < 4 else "disabled")
        btn_next.configure(state="normal")
        btn_next.configure(text={3: "Apply", 4: "Finish"}.get(i, "Next >"))
        if i == 0 and not server_ready():
            btn_next.configure(state="disabled")
        if i == 3 and state["applied"]:
            btn_next.configure(text="Next >")

    def go_next():
        i = cur["i"]
        if i == 3 and not state["applied"]:
            apply_all()
            return
        if i == 4:
            root.destroy()
            return
        if i == 2 and not (state["t_claude"].get() or state["t_codex"].get() or
                           (state["save_env"].get() and (state["access"].get() or state["refresh"].get()))):
            messagebox.showinfo("Nothing to do", "Select at least one client or provide a token to save.")
            return
        show(i + 1)

    btn_next.configure(command=go_next)
    btn_back.configure(command=lambda: show(max(0, cur["i"] - 1)))

    # --- event pump -----------------------------------------------------------
    def pump():
        try:
            while True:
                kind, payload = ev.get_nowait()
                if kind == "log0":
                    log0(payload)
                elif kind == "installed":
                    log0("Installation finished." if payload else "Installation failed - see messages above.")
                    install_btn.configure(state="normal")
                    refresh_checks()
                elif kind == "validated":
                    ok, msg, _tok = payload
                    val_btn.configure(state="normal")
                    val_msg.configure(text=msg, foreground="#1a7f37" if ok else "#b00020")
                elif kind == "log3":
                    log3(payload)
                elif kind == "applied":
                    state["applied"] = bool(payload)
                    btn_back.configure(state="normal")
                    btn_next.configure(state="normal", text="Next >" if payload else "Apply")
                    if payload:
                        log3("All steps completed.")
                    else:
                        log3("Some steps failed - fix the problem and press Apply again.")
                elif kind == "log4":
                    log4(payload)
                elif kind == "tested":
                    test_btn.configure(state="normal")
        except queue.Empty:
            pass
        root.after(100, pump)

    show(0)
    pump()
    if selftest:
        root.update_idletasks()
        root.update()
        for i in range(len(STEPS)):
            show(i)
            root.update()
        root.destroy()
        print("selftest ok")
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run_gui(selftest="--selftest" in sys.argv))
