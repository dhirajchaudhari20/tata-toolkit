"""
============================================================
  Network Operations Suite - Build Script
  Tata Communications
============================================================
Usage:
    python build.py

What it does:
    1. Reads current version from updater.py
    2. Runs PyInstaller -> outputs to dist\\v{version}\\
    3. Copies exe + version.json into updates\\ folder
    4. Prints a summary
============================================================
"""

import os
import re
import sys
import shutil
import subprocess

# ── Config ────────────────────────────────────────────────
PROJECT_DIR  = os.path.dirname(os.path.abspath(__file__))
UPDATER_FILE = os.path.join(PROJECT_DIR, "updater.py")
VERSION_JSON = os.path.join(PROJECT_DIR, "version.json")
SPEC_FILE    = os.path.join(PROJECT_DIR, "Versa_SDWAN_Auditor.spec")
UPDATES_DIR  = os.path.join(PROJECT_DIR, "updates")
DIST_ROOT    = os.path.join(PROJECT_DIR, "dist")
# ──────────────────────────────────────────────────────────


def get_version():
    """Reads CURRENT_VERSION from updater.py."""
    with open(UPDATER_FILE, "r", encoding="utf-8") as f:
        m = re.search(r'CURRENT_VERSION\s*=\s*"([^"]+)"', f.read())
    if not m:
        raise RuntimeError("Could not find CURRENT_VERSION in updater.py")
    return m.group(1)


def run_pyinstaller(dist_path):
    """Runs PyInstaller, outputting directly to dist\\v{version}\\."""
    pyexe = sys.executable
    cmd = [
        pyexe, "-m", "PyInstaller",
        SPEC_FILE,
        "--noconfirm",
        "--distpath", dist_path,
    ]
    print(f"\n[Build] Running PyInstaller...")
    print(f"        Output -> {dist_path}\n")
    result = subprocess.run(cmd, cwd=PROJECT_DIR)
    if result.returncode != 0:
        raise RuntimeError(f"PyInstaller failed with exit code {result.returncode}")


def copy_to_updates(version, dist_path):
    """Copies the new exe and version.json into the updates\\ folder."""
    exe_src = os.path.join(dist_path, "Versa_SDWAN_Auditor", "Versa_SDWAN_Auditor.exe")
    os.makedirs(UPDATES_DIR, exist_ok=True)

    if os.path.exists(exe_src):
        shutil.copy2(exe_src, os.path.join(UPDATES_DIR, "Versa_SDWAN_Auditor.exe"))
        print(f"[Update] Copied exe      -> updates\\Versa_SDWAN_Auditor.exe")
    else:
        print(f"[Warning] EXE not found at: {exe_src}")

    if os.path.exists(VERSION_JSON):
        shutil.copy2(VERSION_JSON, os.path.join(UPDATES_DIR, "version.json"))
        print(f"[Update] Copied manifest -> updates\\version.json")


def print_summary(version, dist_path):
    exe_path = os.path.join(dist_path, "Versa_SDWAN_Auditor", "Versa_SDWAN_Auditor.exe")
    size_mb = ""
    if os.path.exists(exe_path):
        size_mb = f"  ({round(os.path.getsize(exe_path) / 1024 / 1024, 2)} MB)"

    sep = "=" * 58
    print(f"\n{sep}")
    print(f"  BUILD COMPLETE  -  v{version}")
    print(sep)
    print(f"  EXE    : dist\\v{version}\\Versa_SDWAN_Auditor\\Versa_SDWAN_Auditor.exe{size_mb}")
    print(f"  Files  : dist\\v{version}\\Versa_SDWAN_Auditor\\_internal\\")
    print(f"  Update : updates\\Versa_SDWAN_Auditor.exe  (ready to push)")
    print(sep)
    print(f"\n  To send to colleague:")
    print(f"    Copy entire folder:  dist\\v{version}\\Versa_SDWAN_Auditor\\")
    print(f"    (.exe + _internal\\ must always stay together)\n")


if __name__ == "__main__":
    sep = "=" * 58
    print(sep)
    print("  Network Operations Suite - Build Script")
    print("  Tata Communications")
    print(sep)

    version  = get_version()
    dist_path = os.path.join(DIST_ROOT, f"v{version}")

    print(f"\n[Info] Building version : v{version}")
    print(f"[Info] Output folder    : dist\\v{version}\\Versa_SDWAN_Auditor\\")

    run_pyinstaller(dist_path)
    copy_to_updates(version, dist_path)
    print_summary(version, dist_path)
