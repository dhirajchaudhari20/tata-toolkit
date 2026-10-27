"""
====================================================================================================
                        TATA COMMUNICATIONS - NETWORK OPERATIONS CENTER
                     ENTERPRISE AUTO-UPDATER ENGINE (GitHub + Offline)
====================================================================================================
Designed and Developed for Tata Communications / Advanced Network Engineering
Supports:
  1. GitHub Private Repo — fetches version.json & downloads new builds via GitHub API
  2. Offline / Network Share (UNC / mapped drive) — for air-gapped deployments
  3. Intranet HTTP server — for corporate update servers
====================================================================================================
"""

import os
import sys
import json
import time
import shutil
import subprocess
import threading
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import urllib.request
import urllib.error
from datetime import datetime

CURRENT_VERSION = "1.2.0"
CONFIG_FILE = os.path.join(os.getcwd(), "update_config.json")

# ── GitHub Repository Configuration ─────────────────────────────────────────
GITHUB_REPO = "dhirajchaudhari20/tata-toolkit"
GITHUB_BRANCH = "main"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"
GITHUB_VERSION_PATH = "version.json"                         # root of repo
GITHUB_DIST_PREFIX = "dist"                                   # dist/v{version}/Versa_SDWAN_Auditor/
GITHUB_EXE_NAME = "Versa_SDWAN_Auditor.exe"
# ────────────────────────────────────────────────────────────────────────────

DEFAULT_UPDATE_SOURCE = f"github://{GITHUB_REPO}"


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


# ═══════════════════════════════════════════════════════════════════════════════
#   GITHUB AUTHENTICATION — uses Windows Git Credential Manager automatically
# ═══════════════════════════════════════════════════════════════════════════════

def _get_github_token():
    """
    Retrieves GitHub auth token from the Windows Git Credential Manager.
    This works automatically if the user has authenticated via `git push/pull`
    to github.com at least once (which they have, since the repo is pushed).
    Returns the token string, or None if unavailable.
    """
    try:
        proc = subprocess.Popen(
            ["git", "credential", "fill"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        stdin_data = "protocol=https\nhost=github.com\n\n"
        stdout_bytes, _ = proc.communicate(input=stdin_data.encode(), timeout=10)
        output = stdout_bytes.decode("utf-8", errors="replace")
        for line in output.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def _github_api_request(url, token=None):
    """Makes an authenticated GitHub API request and returns parsed JSON."""
    headers = {
        "User-Agent": "VersaAuditor-Updater/2.1",
        "Accept": "application/vnd.github.v3+json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _github_download_file(url, dest_path, token=None):
    """Downloads a file from GitHub API download_url to a local path (authenticated)."""
    headers = {
        "User-Agent": "VersaAuditor-Updater/2.1",
        "Accept": "application/octet-stream"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(1024 * 256)  # 256KB chunks
                if not chunk:
                    break
                f.write(chunk)


# ═══════════════════════════════════════════════════════════════════════════════
#   FETCH REMOTE MANIFEST (GitHub / HTTP / Local / UNC)
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_manifest_github():
    """Fetches version.json from the private GitHub repo via API."""
    import base64
    token = _get_github_token()
    if not token:
        raise ConnectionError(
            "GitHub authentication failed.\n\n"
            "Please run 'git pull' once in a terminal to store your GitHub credentials,\n"
            "then try again."
        )
    url = f"{GITHUB_API_BASE}/contents/{GITHUB_VERSION_PATH}?ref={GITHUB_BRANCH}"
    data = _github_api_request(url, token)
    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content)


def fetch_remote_manifest(source_path):
    """
    Fetches version.json from one of:
    1. GitHub private repo (if source starts with 'github://')
    2. Intranet HTTP/HTTPS URL
    3. Local directory or UNC network share
    """
    if not source_path:
        raise ValueError("Update source path is empty.")

    source_path = source_path.strip()

    # Case A: GitHub private repo
    if source_path.startswith("github://"):
        return _fetch_manifest_github()

    # Case B: Intranet HTTP / HTTPS URL
    if source_path.startswith("http://") or source_path.startswith("https://"):
        manifest_url = source_path if source_path.endswith(".json") else f"{source_path.rstrip('/')}/version.json"
        req = urllib.request.Request(manifest_url, headers={"User-Agent": "VersaAuditor-Updater/2.1"})
        with urllib.request.urlopen(req, timeout=5) as response:
            content = response.read().decode("utf-8")
            return json.loads(content)

    # Case C: Local directory or UNC Network Share (\\server\share\...)
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


# ═══════════════════════════════════════════════════════════════════════════════
#   UPDATE DIALOG — Modern modal prompt when a new version is found
# ═══════════════════════════════════════════════════════════════════════════════

class UpdateDialog:
    """Modern modal dialog prompting the user to update when a new version is found."""
    def __init__(self, parent, manifest, source_path):
        self.parent = parent
        self.manifest = manifest
        self.source_path = source_path
        self.result = False

        self.win = tk.Toplevel(parent)
        self.win.title("🚀 Software Update Available - Tata Communications")
        self.win.geometry("600x520")
        self.win.minsize(540, 440)
        self.win.resizable(True, True)
        self.win.transient(parent)
        self.win.grab_set()

        # Center on parent
        try:
            parent.update_idletasks()
            x = parent.winfo_x() + (parent.winfo_width() - 600) // 2
            y = parent.winfo_y() + (parent.winfo_height() - 520) // 2
            self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self):
        # 1. Top Header Frame (docked top)
        hdr_frame = tk.Frame(self.win, bg="#0f172a", padx=20, pady=16)
        hdr_frame.pack(side="top", fill="x")

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
            text="A new version of Versa SD-WAN Auditor is ready to install.",
            font=("Segoe UI", 9),
            fg="#94a3b8",
            bg="#0f172a"
        ).pack(anchor="w", pady=(2, 0))

        # Source badge
        src_label = "GitHub Cloud (github.com/" + GITHUB_REPO + ")" if self.source_path.startswith("github://") else "Network Share"
        tk.Label(
            hdr_frame,
            text=f"Source: {src_label}",
            font=("Segoe UI", 8),
            fg="#38bdf8",
            bg="#0f172a"
        ).pack(anchor="w", pady=(2, 0))

        # 2. Bottom Buttons Frame (docked bottom FIRST so it is NEVER clipped)
        btn_frame = tk.Frame(self.win, bg="#f1f5f9", padx=20, pady=14)
        btn_frame.pack(side="bottom", fill="x")

        # Visual separator above buttons
        tk.Frame(self.win, bg="#cbd5e1", height=1).pack(side="bottom", fill="x")

        tk.Button(
            btn_frame,
            text="⚡ Update & Restart Now",
            font=("Segoe UI", 10, "bold"),
            bg="#16a34a",
            fg="white",
            activebackground="#15803d",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=20,
            pady=8,
            command=self._on_update
        ).pack(side="right", padx=(10, 0))

        tk.Button(
            btn_frame,
            text="Remind Me Later",
            font=("Segoe UI", 9),
            bg="#64748b",
            fg="white",
            activebackground="#475569",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=16,
            pady=8,
            command=self.win.destroy
        ).pack(side="right")

        # 3. Middle Body Frame (fills all remaining space)
        body = tk.Frame(self.win, bg="#f8fafc", padx=20, pady=14)
        body.pack(side="top", fill="both", expand=True)

        # Version comparison badges
        ver_box = tk.Frame(body, bg="#ffffff", padx=14, pady=10, highlightbackground="#e2e8f0", highlightthickness=1)
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
            font=("Segoe UI", 11, "bold"),
            fg="#0284c7",
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

        cl_text = tk.Text(cl_frame, font=("Segoe UI", 9), bg="#ffffff", fg="#1e293b", wrap="word", height=8, padx=10, pady=8)
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

    def _on_update(self):
        self.result = True
        self.win.destroy()


class GitHubDownloadDialog:
    """
    Progress dialog that downloads the new release from the GitHub private repo.
    Downloads the EXE and all _internal/ files into a temp staging folder.
    """
    def __init__(self, parent, manifest):
        self.parent = parent
        self.manifest = manifest
        self.new_version = manifest.get("latest_version", "0.0.0")
        self.staging_dir = os.path.join(tempfile.gettempdir(), f"versa_update_v{self.new_version}")
        self.success = False
        self.cancelled = False

        self.win = tk.Toplevel(parent)
        self.win.title("⬇ Downloading Update — Tata Communications")
        self.win.geometry("520x260")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self._on_cancel)

        try:
            parent.update_idletasks()
            x = parent.winfo_x() + (parent.winfo_width() - 520) // 2
            y = parent.winfo_y() + (parent.winfo_height() - 260) // 2
            self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        self._build_ui()
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _build_ui(self):
        # Header (top)
        hdr = tk.Frame(self.win, bg="#0f172a", padx=18, pady=12)
        hdr.pack(side="top", fill="x")

        tk.Label(
            hdr, text=f"⬇ Downloading v{self.new_version} from GitHub...",
            font=("Segoe UI", 11, "bold"), fg="#38bdf8", bg="#0f172a"
        ).pack(anchor="w")

        tk.Label(
            hdr, text=f"Source: github.com/{GITHUB_REPO}",
            font=("Segoe UI", 8), fg="#64748b", bg="#0f172a"
        ).pack(anchor="w", pady=(2, 0))

        # Buttons (bottom first)
        btn_frame = tk.Frame(self.win, bg="#f1f5f9", padx=18, pady=10)
        btn_frame.pack(side="bottom", fill="x")

        tk.Frame(self.win, bg="#cbd5e1", height=1).pack(side="bottom", fill="x")

        self.cancel_btn = tk.Button(
            btn_frame, text="Cancel Download", font=("Segoe UI", 9),
            bg="#ef4444", fg="white", activebackground="#dc2626",
            activeforeground="white", relief="flat", cursor="hand2",
            padx=14, pady=5, command=self._on_cancel
        )
        self.cancel_btn.pack(side="right")

        # Body (middle)
        body = tk.Frame(self.win, bg="#f8fafc", padx=18, pady=16)
        body.pack(side="top", fill="both", expand=True)

        self.status_label = tk.Label(
            body, text="Authenticating with GitHub...",
            font=("Segoe UI", 9), fg="#334155", bg="#f8fafc", anchor="w"
        )
        self.status_label.pack(fill="x", pady=(0, 8))

        self.progress = ttk.Progressbar(body, orient="horizontal", mode="determinate", length=460)
        self.progress.pack(fill="x", pady=(0, 8))

        self.file_label = tk.Label(
            body, text="",
            font=("Consolas", 8), fg="#94a3b8", bg="#f8fafc", anchor="w"
        )
        self.file_label.pack(fill="x")

        self.detail_label = tk.Label(
            body, text="",
            font=("Segoe UI", 8), fg="#64748b", bg="#f8fafc", anchor="w"
        )
        self.detail_label.pack(fill="x", pady=(4, 0))

    def _update_status(self, text):
        try:
            self.win.after(0, lambda: self.status_label.config(text=text))
        except Exception:
            pass

    def _update_file(self, text):
        try:
            self.win.after(0, lambda: self.file_label.config(text=text))
        except Exception:
            pass

    def _update_detail(self, text):
        try:
            self.win.after(0, lambda: self.detail_label.config(text=text))
        except Exception:
            pass

    def _update_progress(self, value, maximum=100):
        try:
            self.win.after(0, lambda: self.progress.config(value=value, maximum=maximum))
        except Exception:
            pass

    def _on_cancel(self):
        self.cancelled = True
        try:
            self.win.destroy()
        except Exception:
            pass

    def _collect_github_files(self, api_url, token, prefix=""):
        """Recursively collects all files in a GitHub directory, returns list of (relative_path, download_url, size)."""
        files = []
        data = _github_api_request(api_url, token)
        for item in data:
            rel_path = os.path.join(prefix, item["name"]) if prefix else item["name"]
            if item["type"] == "file":
                # For private repos, download_url requires auth, so use the API url instead
                files.append((rel_path, item["url"], item.get("size", 0)))
            elif item["type"] == "dir":
                sub_url = item["url"]
                files.extend(self._collect_github_files(sub_url, token, rel_path))
        return files

    def _download_single_file(self, api_url, dest_path, token):
        """Downloads a single file from GitHub Contents API (handles base64 and redirect)."""
        import base64
        headers = {
            "User-Agent": "VersaAuditor-Updater/2.1",
            "Accept": "application/vnd.github.v3+json"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        # If file is small enough, GitHub returns base64-encoded content
        if "content" in data and data.get("encoding") == "base64":
            with open(dest_path, "wb") as f:
                f.write(base64.b64decode(data["content"]))
        elif "download_url" in data and data["download_url"]:
            # For larger files or private repos, download via the download_url with auth
            dl_headers = {"User-Agent": "VersaAuditor-Updater/2.1"}
            if token:
                dl_headers["Authorization"] = f"Bearer {token}"
            dl_req = urllib.request.Request(data["download_url"], headers=dl_headers)
            with urllib.request.urlopen(dl_req, timeout=120) as dl_resp:
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = dl_resp.read(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)

    def _download_worker(self):
        """Background thread: authenticates, lists files, downloads them all."""
        try:
            # Step 1: Authenticate
            self._update_status("🔑 Authenticating with GitHub...")
            token = _get_github_token()
            if not token:
                self._update_status("❌ GitHub authentication failed — run 'git pull' first")
                return

            if self.cancelled:
                return

            # Step 2: List all files in dist/v{version}/Versa_SDWAN_Auditor/
            self._update_status(f"📂 Scanning release files for v{self.new_version}...")
            dist_path = f"{GITHUB_DIST_PREFIX}/v{self.new_version}/Versa_SDWAN_Auditor"
            api_url = f"{GITHUB_API_BASE}/contents/{dist_path}?ref={GITHUB_BRANCH}"

            try:
                all_files = self._collect_github_files(api_url, token)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self._update_status(f"❌ Release v{self.new_version} not found on GitHub")
                else:
                    self._update_status(f"❌ GitHub API error: {e}")
                return

            if not all_files:
                self._update_status("❌ No release files found on GitHub")
                return

            if self.cancelled:
                return

            total_files = len(all_files)
            total_bytes = sum(f[2] for f in all_files)
            total_mb = total_bytes / (1024 * 1024)
            self._update_status(f"⬇ Downloading {total_files} files ({total_mb:.1f} MB)...")
            self._update_detail(f"Staging folder: {self.staging_dir}")

            # Step 3: Clean staging dir
            if os.path.exists(self.staging_dir):
                shutil.rmtree(self.staging_dir, ignore_errors=True)
            os.makedirs(self.staging_dir, exist_ok=True)

            # Step 4: Download each file
            downloaded_bytes = 0
            for idx, (rel_path, file_api_url, file_size) in enumerate(all_files, 1):
                if self.cancelled:
                    return

                short_name = rel_path.replace("\\", "/")
                self._update_file(f"[{idx}/{total_files}]  {short_name}")
                self._update_progress(idx - 1, total_files)

                dest = os.path.join(self.staging_dir, rel_path)
                self._download_single_file(file_api_url, dest, token)

                downloaded_bytes += file_size
                dl_mb = downloaded_bytes / (1024 * 1024)
                self._update_detail(f"Downloaded: {dl_mb:.1f} / {total_mb:.1f} MB")

            self._update_progress(total_files, total_files)
            self._update_status(f"✅ Download complete — {total_files} files ready to install")
            self._update_file("")

            # Verify EXE was downloaded
            exe_path = os.path.join(self.staging_dir, GITHUB_EXE_NAME)
            if os.path.exists(exe_path):
                self.success = True
                time.sleep(1)
                try:
                    self.win.after(0, self.win.destroy)
                except Exception:
                    pass
            else:
                self._update_status("❌ EXE not found in downloaded files")

        except Exception as e:
            self._update_status(f"❌ Download failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#   APPLY UPDATE — handles both GitHub and Offline sources
# ═══════════════════════════════════════════════════════════════════════════════

def apply_github_update(manifest, parent=None):
    """
    Downloads the new version from GitHub and applies it.
    Shows a progress dialog during download, then creates an apply_update.bat
    that replaces the running exe + _internal folder and relaunches.
    """
    dlg = GitHubDownloadDialog(parent, manifest)
    parent.wait_window(dlg.win)

    if not dlg.success:
        if not dlg.cancelled:
            messagebox.showerror(
                "Download Failed",
                "Could not download the update from GitHub.\nPlease check your internet connection and try again."
            )
        return

    staging_dir = dlg.staging_dir
    is_frozen = getattr(sys, "frozen", False)
    current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

    if is_frozen:
        # Running as compiled EXE — create a batch script to replace files while app is closed
        exe_name = os.path.basename(sys.executable)
        source_exe = os.path.join(staging_dir, GITHUB_EXE_NAME)
        target_exe = sys.executable
        source_internal = os.path.join(staging_dir, "_internal")
        target_internal = os.path.join(current_dir, "_internal")

        bat_path = os.path.join(current_dir, "apply_update.bat")
        v_target = manifest.get('latest_version', '?')
        lines = [
            "@echo off",
            "title Tata Communications - Software Updater",
            "echo ==================================================",
            "echo       Tata Communications - Updating Software...",
            f"echo       Updating to v{v_target}",
            "echo ==================================================",
            "echo Waiting for application to exit...",
            "timeout /t 2 /nobreak >nul",
            f'taskkill /F /IM "{exe_name}" >nul 2>&1',
            "timeout /t 1 /nobreak >nul",
            "echo Copying updated executable...",
            f'copy /y "{source_exe}" "{target_exe}" >nul',
        ]
        if os.path.exists(source_internal):
            lines.append("echo Copying runtime libraries...")
            lines.append(f'xcopy /s /e /y /q "{source_internal}" "{target_internal}\\" >nul 2>&1')
        lines.extend([
            "echo.",
            "echo ==================================================",
            "echo       Update installed successfully!",
            "echo       Relaunching Versa SD-WAN Auditor...",
            "echo ==================================================",
            f'start "" "{target_exe}"',
            "timeout /t 2 /nobreak >nul",
            'del "%~f0" >nul 2>&1',
            "exit"
        ])

        with open(bat_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        messagebox.showinfo(
            "Ready to Update",
            f"Versa SD-WAN Auditor will now close and update to v{manifest.get('latest_version')}.\n\n"
            "The application will automatically restart in a few seconds."
        )
        subprocess.Popen(["cmd.exe", "/c", bat_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
        sys.exit(0)

    else:
        # Running as Python script — copy updated .py files from the staging dir
        py_files = [f for f in os.listdir(staging_dir) if f.endswith(".py")]
        copied = 0
        for fn in py_files:
            src_f = os.path.join(staging_dir, fn)
            dst_f = os.path.join(current_dir, fn)
            shutil.copy2(src_f, dst_f)
            copied += 1

        messagebox.showinfo(
            "Update Complete",
            f"Successfully updated {copied} files to v{manifest.get('latest_version')}!\n\n"
            "Please restart the application to apply the new version."
        )


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


# ═══════════════════════════════════════════════════════════════════════════════
#   CHECK FOR UPDATES — Main entry point called by the GUI
# ═══════════════════════════════════════════════════════════════════════════════

def check_for_updates(parent=None, silent=False):
    """
    Checks for available updates from GitHub or configured network share.
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
                # Route to correct updater based on source type
                if source_path.startswith("github://"):
                    apply_github_update(manifest, parent=parent)
                else:
                    apply_offline_update(manifest, source_path, parent=parent)
        else:
            if not silent:
                messagebox.showinfo(
                    "Up to Date",
                    f"Versa SD-WAN Auditor is up to date!\n\n"
                    f"Current Version: v{CURRENT_VERSION}\n"
                    f"Latest Available: v{latest_v_str}\n\n"
                    f"Source: {'GitHub Cloud' if source_path.startswith('github://') else source_path}\n\n"
                    f"No updates needed."
                )
    except Exception as e:
        if not silent:
            src_display = "GitHub (github.com/" + GITHUB_REPO + ")" if source_path.startswith("github://") else source_path
            messagebox.showwarning(
                "Update Check Failed",
                f"Could not connect to update source:\n{src_display}\n\n"
                f"Details: {e}\n\n"
                f"Please check your internet connection or verify the update path in Settings."
            )


# ═══════════════════════════════════════════════════════════════════════════════
#   UPDATE SETTINGS DIALOG — Configure source (GitHub / Local / HTTP)
# ═══════════════════════════════════════════════════════════════════════════════

def open_update_settings_dialog(parent):
    """Modal dialog allowing the user or network admin to configure the update source."""
    cfg = load_update_config()

    win = tk.Toplevel(parent)
    win.title("⚙ Update Source Configuration - Tata Communications")
    win.geometry("620x340")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()

    # Center on parent
    try:
        parent.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 620) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 340) // 2
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
    except Exception:
        pass

    content = tk.Frame(win, bg="#ffffff", padx=20, pady=18)
    content.pack(fill="both", expand=True)

    tk.Label(
        content,
        text="Update Source Configuration:",
        font=("Segoe UI", 10, "bold"),
        fg="#0f172a",
        bg="#ffffff"
    ).pack(anchor="w")

    tk.Label(
        content,
        text="Set the source where updates are published — GitHub, UNC share, or intranet URL:",
        font=("Segoe UI", 8),
        fg="#64748b",
        bg="#ffffff"
    ).pack(anchor="w", pady=(2, 10))

    # Source type selector
    source_type_frame = tk.Frame(content, bg="#ffffff")
    source_type_frame.pack(fill="x", pady=(0, 8))

    source_var = tk.StringVar()
    current_source = cfg.get("update_source", DEFAULT_UPDATE_SOURCE)
    if current_source.startswith("github://"):
        source_var.set("github")
    else:
        source_var.set("custom")

    path_var = tk.StringVar()
    if current_source.startswith("github://"):
        path_var.set(GITHUB_REPO)
    else:
        path_var.set(current_source)

    def _on_source_change(*args):
        if source_var.get() == "github":
            path_var.set(GITHUB_REPO)
            path_entry.config(state="disabled")
            browse_btn.config(state="disabled")
        else:
            if path_var.get() == GITHUB_REPO:
                path_var.set("")
            path_entry.config(state="normal")
            browse_btn.config(state="normal")

    ttk.Radiobutton(
        source_type_frame, text="🐙 GitHub Repository (Private/Public)",
        variable=source_var, value="github", command=_on_source_change
    ).pack(anchor="w")

    ttk.Radiobutton(
        source_type_frame, text="📁 Local / Network Share / HTTP URL",
        variable=source_var, value="custom", command=_on_source_change
    ).pack(anchor="w", pady=(4, 0))

    # Path entry
    path_frame = tk.Frame(content, bg="#ffffff")
    path_frame.pack(fill="x", pady=(6, 12))

    path_entry = ttk.Entry(path_frame, textvariable=path_var, font=("Segoe UI", 9))
    path_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

    def _browse_folder():
        folder = filedialog.askdirectory(title="Select Update Share Folder")
        if folder:
            path_var.set(folder)

    browse_btn = ttk.Button(path_frame, text="Browse...", command=_browse_folder)
    browse_btn.pack(side="left")

    # GitHub info label
    gh_info = tk.Label(
        content,
        text=f"GitHub Repo: github.com/{GITHUB_REPO} (branch: {GITHUB_BRANCH})\nAuth: Windows Git Credential Manager (auto-detected)",
        font=("Segoe UI", 8),
        fg="#0284c7",
        bg="#ffffff",
        justify="left"
    )
    gh_info.pack(anchor="w", pady=(0, 6))

    auto_var = tk.BooleanVar(value=cfg.get("auto_check_on_startup", True))
    chk = ttk.Checkbutton(content, text="Automatically check for updates on startup", variable=auto_var)
    chk.pack(anchor="w", pady=(0, 10))

    last_chk = cfg.get("last_checked", "Never")
    tk.Label(
        content,
        text=f"Last Checked: {last_chk}",
        font=("Segoe UI", 8),
        fg="#94a3b8",
        bg="#ffffff"
    ).pack(anchor="w")

    # Initialize state
    _on_source_change()

    btn_box = tk.Frame(content, bg="#ffffff")
    btn_box.pack(side="bottom", fill="x")

    def _save():
        if source_var.get() == "github":
            cfg["update_source"] = f"github://{GITHUB_REPO}"
        else:
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
