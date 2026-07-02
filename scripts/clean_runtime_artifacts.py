"""
clean_runtime_artifacts.py - Clean up runtime files.

Removes:
  - .tendata-chrome-profile/
  - __pycache__/
  - *.pyc
  - Temporary result Excel files (result_*.xlsx, single_test_result.xlsx)

If Chrome is still running and holding the profile, this script will
detect it and print a clear warning instead of silently failing.

Usage: python scripts/clean_runtime_artifacts.py
"""

import os, shutil, glob, subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent

to_remove = [
    ROOT / ".tendata-chrome-profile",
]

patterns_to_delete = [
    "__pycache__",
    "*.pyc",
    "result_*.xlsx",
    "single_test_result.xlsx",
]

def is_chrome_running():
    """Check if any Chrome process is still running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
            capture_output=True, text=True, shell=True
        )
        return "chrome.exe" in result.stdout.lower()
    except Exception:
        return False

def remove_path(p):
    """Remove a path, handling errors gracefully."""
    if not p.exists():
        return False
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return True
    except PermissionError:
        return False
    except Exception as e:
        print(f"  ERROR removing {p}: {e}")
        return False

def clean():
    removed = 0
    force_needed = False

    # Check Chrome status first
    chrome_running = is_chrome_running()
    if chrome_running:
        print("WARNING: Chrome is still running.")
        print("         The .tendata-chrome-profile/ directory may be locked.")
        print("         Close Chrome and re-run this script if cleanup fails.\n")

    for p in to_remove:
        if remove_path(p):
            print(f"Deleted: {p}")
            removed += 1
        elif p.exists():
            print(f"LOCKED: {p} — Chrome is still holding it.")
            print("        Close Chrome, then re-run this script.")
            force_needed = True

    for root, dirs, files in os.walk(ROOT):
        for d in dirs:
            if d == "__pycache__":
                dp = os.path.join(root, d)
                if remove_path(Path(dp)):
                    print(f"Deleted: {dp}")
                    removed += 1
        for f in files:
            if f.endswith(".pyc"):
                fp = os.path.join(root, f)
                if remove_path(Path(fp)):
                    print(f"Deleted: {fp}")
                    removed += 1

    for pat in ["result_*.xlsx", "single_test_result.xlsx"]:
        for f in ROOT.glob(pat):
            if remove_path(f):
                print(f"Deleted: {f}")
                removed += 1

    # Final summary
    print()
    if force_needed:
        print("CLEANUP INCOMPLETE: Close Chrome and re-run this script.")
    elif removed == 0:
        print("Nothing to clean.")
    else:
        print(f"Cleaned {removed} item(s).")

if __name__ == "__main__":
    clean()
