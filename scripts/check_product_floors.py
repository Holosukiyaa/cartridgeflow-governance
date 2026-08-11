"""Run existing product tests and frontend builds through an external adapter."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT.parent / "CartridgeFlow"


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PRODUCT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("npm is unavailable")
    commands = [
        [sys.executable, "-m", "unittest", "scripts.tests.integration.test_creator_runtime_handoff", "scripts.tests.conformance.test_release_builder", "scripts.tests.runtime.test_runtime_recovery", "scripts.tests.api.test_api_surface"],
        [npm, "run", "test"],
        [npm, "run", "build"],
        [npm, "run", "test"],
        [npm, "run", "build"],
    ]
    working_directories = [
        PRODUCT,
        PRODUCT / "src" / "intent-studio",
        PRODUCT / "src" / "intent-studio",
        PRODUCT / "src" / "capability-workshop",
        PRODUCT / "src" / "capability-workshop",
    ]
    results = []
    for command, cwd in zip(commands, working_directories, strict=True):
        completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", check=False, timeout=300)
        results.append({"cwd": str(cwd), "command": command, "returncode": completed.returncode})
        if completed.returncode:
            print(completed.stdout)
            print(completed.stderr, file=sys.stderr)
            return completed.returncode
    print(json.dumps({"ok": True, "stage": "floor", "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
