"""
====================================================================================================
                        TATA COMMUNICATIONS - NETWORK OPERATIONS CENTER
                     ENTERPRISE OFFLINE AUTO-UPDATER ENGINE
====================================================================================================
Designed and Developed for Tata Communications / Advanced Network Engineering
Provides offline, network-share (UNC/mapped drive) or intranet HTTP based update checking
without requiring GitHub or external internet access.
====================================================================================================
"""

import os
import sys
import json
import time
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import urllib.request
from datetime import datetime

CURRENT_VERSION = "1.4.0"
CONFIG_FILE = os.path.join(os.getcwd(), "update_config.json")
DEFAULT_UPDATE_SOURCE = os.path.join(os.getcwd(), "updates")  # Default to local 'updates' folder or network share


def get_current_version():
    return CURRENT_VERSION


def parse_version(v_str):
    """Parses a version string like '2.1.0' into a tuple of ints: (2, 1, 0)."""
    try:
        clean = v_str.strip().lstrip("vV")
        parts = [int(p) for p in clean.split(".") if p.isdigit()]
        return tuple(parts)
    except Exception:
        return (0, 0, 0)


def load_update_config():
    """Loads update configuration from update_config.json or returns defaults."""
    default_cfg = {
        "update_source": DEFAULT_UPDATE_SOURCE,
        "auto_check_on_startup": True,
        "last_checked": None
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_cfg.update(data)
        except Exception:
            pass
    return default_cfg


def save_update_config(cfg):
    """Saves update configuration to update_config.json."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception:
        pass


def fetch_remote_manifest(source_path):
    """
    Fetches version.json from either:
    1. A network shared folder / local path (e.g. \\\\server\\share\\version.json or Z:\\updates\\version.json)
    2. An intranet HTTP server (e.g. http://10.x.x.x:8080/version.json)
    """
    if not source_path:
        raise ValueError("Update source path is empty.")

    source_path = source_path.strip()

    # Case A: Intranet HTTP / HTTPS URL
    if source_path.startswith("http://") or source_path.startswith("https://"):
        manifest_url = source_path if source_path.endswith(".json") else f"{source_path.rstrip('/')}/version.json"
        req = urllib.request.Request(manifest_url, headers={"User-Agent": "VersaAuditor-Updater/2.1"})
        with urllib.request.urlopen(req, timeout=5) as response:
            content = response.read().decode("utf-8")
            return json.loads(content)

    # Case B: Local directory or UNC Network Share (\\\\server\\share\\...)
    if os.path.isdir(source_path):
        manifest_file = os.path.join(source_path, "version.json")
    elif os.path.isfile(source_path):
        manifest_file = source_path
    else:
        manifest_file = os.path.join(source_path, "version.json")

    if not os.path.exists(manifest_file):
        raise FileNotFoundError(f"Could not find 'version.json' at:\n{manifest_file}")

    with open(manifest_file, "r", encoding="utf-8") as f:
        return json.load(f)


class UpdateDialog:
    """Modern modal dialog prompting the user to update when a new version is found."""
    def __init__(self, parent, manifest, source_path):
        self.parent = parent
        self.manifest = manifest
        self.source_path = source_path
        self.result = False

        self.win = tk.Toplevel(parent)
        self.win.title("🚀 Software Update Available - Tata Communications")
        self.win.geometry("560x420")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()

        # Center on parent
        try:
            parent.update_idletasks()
            x = parent.winfo_x() + (parent.winfo_width() - 560) // 2
            y = parent.winfo_y() + (parent.winfo_height() - 420) // 2
            self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self):
        # Header Frame
        hdr_frame = tk.Frame(self.win, bg="#0f172a", padx=20, pady=16)
        hdr_frame.pack(fill="x")

        tk.Label(
            hdr_frame,
            text="✨ New Update Available!",
            font=("Segoe UI", 13, "bold"),
            fg="#38bdf8",
            bg="#0f172a"
        ).pack(anchor="w")

        v_new = self.manifest.get("latest_version", "Unknown")
        rel_date = self.manifest.get("release_date", "Recent")
        tk.Label(
            hdr_frame,
            text=f"A new version of Versa SD-WAN Auditor is ready to install.",
            font=("Segoe UI", 9),
            fg="#94a3b8",
            bg="#0f172a"
        ).pack(anchor="w", pady=(2, 0))

        # Body Frame
        body = tk.Frame(self.win, bg="#f8fafc", padx=20, pady=14)
        body.pack(fill="both", expand=True)

        # Version comparison badges
        ver_box = tk.Frame(body, bg="#ffffff", padx=12, pady=10, highlightbackground="#e2e8f0", highlightthickness=1)
        ver_box.pack(fill="x", pady=(0, 10))

        tk.Label(
            ver_box,
            text=f"Current Installed: v{CURRENT_VERSION}",
            font=("Segoe UI", 9, "bold"),
            fg="#64748b",
            bg="#ffffff"
        ).pack(side="left")

        tk.Label(
            ver_box,
            text=" ➔ ",
            font=("Segoe UI", 10, "bold"),
            fg="#94a3b8",
            bg="#ffffff"
        ).pack(side="left", padx=8)

        tk.Label(
            ver_box,
            text=f"New Version: v{v_new} ({rel_date})",
            font=("Segoe UI", 10, "bold"),
            fg="#16a34a",
            bg="#ffffff"
        ).pack(side="left")

        # Changelog / Release Notes
        tk.Label(
            body,
            text="What's New in this Release:",
            font=("Segoe UI", 9, "bold"),
            fg="#334155",
            bg="#f8fafc"
        ).pack(anchor="w", pady=(0, 4))

        cl_frame = ttk.Frame(body)
        cl_frame.pack(fill="both", expand=True)

        cl_text = tk.Text(cl_frame, font=("Consolas", 9), bg="#ffffff", fg="#1e293b", wrap="word", height=7, padx=8, pady=6)
        cl_scroll = ttk.Scrollbar(cl_frame, orient="vertical", command=cl_text.yview)
        cl_text.configure(yscrollcommand=cl_scroll.set)
        cl_text.pack(side="left", fill="both", expand=True)
        cl_scroll.pack(side="right", fill="y")

        raw_changelog = self.manifest.get("changelog", "No release notes provided.")
        if isinstance(raw_changelog, list):
            changelog_str = "\n".join(f"• {item}" for item in raw_changelog)
        else:
            changelog_str = str(raw_changelog)

        cl_text.insert(tk.END, changelog_str)
        cl_text.config(state="disabled")

        # Buttons Frame
        btn_frame = tk.Frame(self.win, bg="#f1f5f9", padx=18, pady=12)
        btn_frame.pack(fill="x", side="bottom")

        tk.Button(
            btn_frame,
            text="⚡ Update Now",
            font=("Segoe UI", 9, "bold"),
            bg="#16a34a",
            fg="white",
            activebackground="#15803d",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=16,
            pady=6,
            command=self._on_update
        ).pack(side="right", padx=(8, 0))

        tk.Button(
            btn_frame,
            text="Remind Me Later",
            font=("Segoe UI", 9),
            bg="#94a3b8",
            fg="white",
            activebackground="#64748b",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=14,
            pady=6,
            command=self.win.destroy
        ).pack(side="right")

    def _on_update(self):
        self.result = True
        self.win.destroy()


def apply_offline_update(manifest, source_path, parent=None):
    """
    Applies the offline update:
    - If running as PyInstaller frozen exe: spawns apply_update.bat which waits 2s,
      copies new exe / _internal files, and relaunches the app.
    - If running as Python script: copies updated .py files and prompts user to restart.
    """
    is_frozen = getattr(sys, "frozen", False)
    current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

    # Determine source directory where new binaries or scripts reside
    if os.path.isdir(source_path):
        src_dir = source_path
    else:
        src_dir = os.path.dirname(source_path)

    try:
        if is_frozen:
            # Running as compiled EXE (e.g. Versa_SDWAN_Auditor.exe)
            exe_name = os.path.basename(sys.executable)
            source_exe = os.path.join(src_dir, exe_name)
            target_exe = sys.executable

            if not os.path.exists(source_exe):
                # Try finding any .exe in source dir
                exes = [f for f in os.listdir(src_dir) if f.lower().endswith(".exe")]
                if exes:
                    source_exe = os.path.join(src_dir, exes[0])
                else:
                    messagebox.showerror(
                        "Update Error",
                        f"Could not find updated executable '{exe_name}' in update folder:\n{src_dir}"
                    )
                    return

            bat_path = os.path.join(current_dir, "apply_update.bat")
            source_internal = os.path.join(src_dir, "_internal")
            target_internal = os.path.join(current_dir, "_internal")

            # Create updater batch script
            lines = [
                "@echo off",
                "echo ==================================================",
                "echo       Tata Communications - Updating Software...",
                "echo ==================================================",
                "timeout /t 2 /nobreak >nul",
                f'copy /y "{source_exe}" "{target_exe}"',
            ]
            if os.path.exists(source_internal):
                lines.append(f'xcopy /s /e /y "{source_internal}" "{target_internal}\\"')

            lines.extend([
                f'start "" "{target_exe}"',
                'echo Update completed successfully!',
                'del "%~f0"',
                'exit'
            ])

            with open(bat_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

            messagebox.showinfo(
                "Updating",
                "Versa SD-WAN Auditor will now close and update itself.\nIt will automatically restart in a few seconds."
            )
            subprocess.Popen(["cmd.exe", "/c", bat_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
            sys.exit(0)

        else:
            # Running as Python script
            files_to_copy = ["versa_gui.py", "pam_terminal_controller.py", "updater.py"]
            copied = 0
            for fn in files_to_copy:
                src_f = os.path.join(src_dir, fn)
                dst_f = os.path.join(current_dir, fn)
                if os.path.exists(src_f):
                    shutil.copy2(src_f, dst_f)
                    copied += 1

            messagebox.showinfo(
                "Update Complete",
                f"Successfully updated {copied} script files to v{manifest.get('latest_version')}!\n\nPlease restart the application to apply the new version."
            )

    except Exception as e:
        messagebox.showerror("Update Failed", f"An error occurred while applying update:\n{e}")


def check_for_updates(parent=None, silent=False):
    """
    Checks for available updates from the configured network share or intranet location.
    - If silent=True: only prompts if an update IS available.
    - If silent=False: alerts user if already up-to-date or if path is unreachable.
    """
    cfg = load_update_config()
    source_path = cfg.get("update_source", DEFAULT_UPDATE_SOURCE)

    try:
        manifest = fetch_remote_manifest(source_path)
        latest_v_str = manifest.get("latest_version", "0.0.0")

        # Update last checked timestamp
        cfg["last_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_update_config(cfg)

        if parse_version(latest_v_str) > parse_version(CURRENT_VERSION):
            # Show update dialog
            dlg = UpdateDialog(parent, manifest, source_path)
            parent.wait_window(dlg.win)
            if dlg.result:
                apply_offline_update(manifest, source_path, parent=parent)
        else:
            if not silent:
                messagebox.showinfo(
                    "Up to Date",
                    f"Versa SD-WAN Auditor is up to date!\n\nCurrent Version: v{CURRENT_VERSION}\nLatest Available: v{latest_v_str}\n\nNo updates needed."
                )
    except Exception as e:
        if not silent:
            messagebox.showwarning(
                "Update Check Failed",
                f"Could not connect to update source location:\n{source_path}\n\nDetails: {e}\n\nPlease check your network connection or verify the update path in Settings."
            )


def open_update_settings_dialog(parent):
    """Modal dialog allowing the user or network admin to configure the offline update source path."""
    cfg = load_update_config()

    win = tk.Toplevel(parent)
    win.title("⚙ Update Source Configuration - Tata Communications")
    win.geometry("580x280")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()

    # Center on parent
    try:
        parent.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 580) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 280) // 2
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
    except Exception:
        pass

    content = tk.Frame(win, bg="#ffffff", padx=20, pady=18)
    content.pack(fill="both", expand=True)

    tk.Label(
        content,
        text="Offline Update Source Location (UNC / Shared Folder / Intranet URL):",
        font=("Segoe UI", 9, "bold"),
        fg="#0f172a",
        bg="#ffffff"
    ).pack(anchor="w")

    tk.Label(
        content,
        text="Set the corporate network path where version.json and new releases are placed:",
        font=("Segoe UI", 8),
        fg="#64748b",
        bg="#ffffff"
    ).pack(anchor="w", pady=(2, 8))

    path_frame = tk.Frame(content, bg="#ffffff")
    path_frame.pack(fill="x", pady=(0, 12))

    path_var = tk.StringVar(value=cfg.get("update_source", DEFAULT_UPDATE_SOURCE))
    path_entry = ttk.Entry(path_frame, textvariable=path_var, font=("Segoe UI", 9))
    path_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

    def _browse_folder():
        folder = filedialog.askdirectory(title="Select Update Share Folder")
        if folder:
            path_var.set(folder)

    ttk.Button(path_frame, text="Browse...", command=_browse_folder).pack(side="left")

    auto_var = tk.BooleanVar(value=cfg.get("auto_check_on_startup", True))
    chk = ttk.Checkbutton(content, text="Automatically check for updates on startup", variable=auto_var)
    chk.pack(anchor="w", pady=(0, 14))

    last_chk = cfg.get("last_checked", "Never")
    tk.Label(
        content,
        text=f"Last Checked: {last_chk}",
        font=("Segoe UI", 8),
        fg="#94a3b8",
        bg="#ffffff"
    ).pack(anchor="w")

    btn_box = tk.Frame(content, bg="#ffffff")
    btn_box.pack(side="bottom", fill="x")

    def _save():
        cfg["update_source"] = path_var.get().strip()
        cfg["auto_check_on_startup"] = auto_var.get()
        save_update_config(cfg)
        messagebox.showinfo("Saved", "Update settings saved successfully.")
        win.destroy()

    tk.Button(
        btn_box,
        text="Save Configuration",
        font=("Segoe UI", 9, "bold"),
        bg="#0284c7",
        fg="white",
        activebackground="#0369a1",
        activeforeground="white",
        relief="flat",
        cursor="hand2",
        padx=14,
        pady=5,
        command=_save
    ).pack(side="right", padx=(6, 0))

    tk.Button(
        btn_box,
        text="Test & Check Now",
        font=("Segoe UI", 9),
        bg="#10b981",
        fg="white",
        activebackground="#059669",
        activeforeground="white",
        relief="flat",
        cursor="hand2",
        padx=12,
        pady=5,
        command=lambda: check_for_updates(parent=win, silent=False)
    ).pack(side="right")
