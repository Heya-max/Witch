import os
import sys

print("PYTHONSTARTUP=", os.environ.get("PYTHONSTARTUP"))
print("sys.path sample:", sys.path[:3])

for mod in ("sitecustomize", "usercustomize"):
    try:
        print(f"trying import {mod}...")
        __import__(mod)
        print(f"{mod} imported")
    except Exception as e:
        print(f"{mod} import failed: {e!r}")

print("done")
