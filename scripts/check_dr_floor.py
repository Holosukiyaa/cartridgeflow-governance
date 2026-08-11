"""Run Desktop Runner's existing Go checks without modifying its source tree."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DR = ROOT.parent / "CartridgeFlow" / "DR"


def go_tool() -> Path:
    configured = os.environ.get("CF_GOVERNANCE_GO", "").strip()
    found = configured if configured and Path(configured).is_file() else shutil.which("go")
    if found:
        return Path(found).resolve()
    candidates = sorted(Path("C:/_HOLOLAB/toolchains").glob("go*/go/bin/go.exe"), reverse=True) if os.name == "nt" else []
    if candidates:
        return candidates[0].resolve()
    raise RuntimeError("Go toolchain is unavailable")


def main() -> int:
    go = go_tool()
    module = DR / "shell" / "go"
    commands = [[str(go), "vet", "./..."], [str(go), "test", "./...", "-count=1"]]
    with tempfile.TemporaryDirectory(prefix="cartridgeflow-governance-dr-") as temporary:
        commands.append([str(go), "build", "-trimpath", "-o", str(Path(temporary) / ("cf-shell.exe" if os.name == "nt" else "cf-shell")), "."])
        results = []
        for command in commands:
            completed = subprocess.run(command, cwd=module, capture_output=True, text=True, encoding="utf-8", check=False, timeout=300)
            results.append({"command": command, "returncode": completed.returncode})
            if completed.returncode:
                print(completed.stdout)
                print(completed.stderr, file=sys.stderr)
                return completed.returncode
    print(json.dumps({"ok": True, "stage": "floor", "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
