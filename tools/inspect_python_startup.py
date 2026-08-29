"""Diagnostic helper to inspect Python startup hooks and environment.

Run this with the same Python executable you use to run tests (e.g. `py -3 tools/inspect_python_startup.py`).
"""

import os
import sys
import traceback


def show_env():
    print("Python executable:", sys.executable)
    print("Python version:", sys.version)
    print("PYTHONSTARTUP=", os.environ.get("PYTHONSTARTUP"))
    print("sys.path (first 10):")
    for p in sys.path[:10]:
        print("  ", p)


def try_import(name):
    try:
        print(f"trying import {name}...")
        __import__(name)
        print(f"imported {name} OK")
    except Exception:
        print(f"failed to import {name}")
        traceback.print_exc()


def show_startup_file():
    startup = os.environ.get("PYTHONSTARTUP")
    if startup:
        print("PYTHONSTARTUP file path:", startup)
        try:
            with open(startup, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i < 50:
                        print(f"{i + 1:03d}: {line.rstrip()}")
                    else:
                        break
        except Exception:
            print("Could not read PYTHONSTARTUP file:")
            traceback.print_exc()
    else:
        print("No PYTHONSTARTUP env var set")


def main():
    show_env()
    show_startup_file()
    try_import("sitecustomize")
    try_import("usercustomize")


if __name__ == "__main__":
    main()
