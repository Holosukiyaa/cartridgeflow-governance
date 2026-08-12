"""Run CartridgeFlow's official protocol lock audit and conformance suite."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT.parent / "CartridgeFlow"
MAX_OUTPUT_CHARS = 12000


def _clip(value: str) -> str:
    if len(value) <= MAX_OUTPUT_CHARS:
        return value
    return value[:MAX_OUTPUT_CHARS] + "\n...[output truncated by product formal checker]"


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PRODUCT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    commands = [
        [sys.executable, "scripts/audit_protocol_registry.py"],
        [sys.executable, "-B", "scripts/run_conformance.py", "--quiet"],
    ]
    results: list[dict[str, object]] = []
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=PRODUCT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=600,
        )
        results.append(
            {
                "command": command,
                "returncode": completed.returncode,
                "stdout": _clip(completed.stdout.strip()),
                "stderr": _clip(completed.stderr.strip()),
            }
        )

    failed = [result for result in results if result["returncode"] != 0]
    payload = {
        "schema": "cartridgeflow.governance.diagnostic.v1",
        "ok": not failed,
        "stage": "floor",
        "check": "product-formal-acceptance",
        "results": results,
        "errors": [
            {
                "reason": "official product acceptance command failed",
                "command": result["command"],
                "returncode": result["returncode"],
            }
            for result in failed
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
