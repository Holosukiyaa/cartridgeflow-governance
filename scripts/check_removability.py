"""Prove target startup/build facts do not change when governance is absent."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from check_dr_floor import go_tool


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT.parent / "CartridgeFlow"
DR = PRODUCT / "DR"
REPORT = ROOT / ".data" / "removability-report.json"


def run(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", check=False, timeout=300)
    if completed.returncode:
        raise RuntimeError(f"probe failed: {' '.join(command)}\n{completed.stdout}\n{completed.stderr}")
    return completed


def main() -> int:
    detach = run([sys.executable, str(ROOT / "scripts" / "check_detachability.py")], ROOT, os.environ.copy())
    product_probe = (
        "import json,sys; from pathlib import Path; root=Path.cwd(); sys.path.insert(0,str(root/'src')); "
        "from backend.main import app; from core.protocol import load_base_implementation,load_protocol_release_catalog; "
        "base=load_base_implementation(root); catalog=load_protocol_release_catalog(root); "
        "print(json.dumps({'routes':len(app.routes),'base':base.get('base_contract'),'default':catalog.data.get('default_for_new_flows')},sort_keys=True))"
    )
    mode_results = {}
    go = go_tool()
    with tempfile.TemporaryDirectory(prefix="cartridgeflow-governance-removal-") as temporary:
        temp = Path(temporary)
        binaries = {}
        for mode in ("enabled", "absent"):
            env = os.environ.copy()
            env["PYTHONPATH"] = str(PRODUCT / "src")
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["CF_GOVERNANCE_LOCATION"] = str(ROOT if mode == "enabled" else temp / "removed-governance")
            product = run([sys.executable, "-c", product_probe], PRODUCT, env)
            binary = temp / f"cf-shell-{mode}.exe"
            run([str(go), "build", "-trimpath", "-o", str(binary), "."], DR / "shell" / "go", env)
            binaries[mode] = hashlib.sha256(binary.read_bytes()).hexdigest()
            env["CF_SHELL_DATA_ROOT"] = str(temp / f"data-{mode}")
            status = json.loads(run([str(binary), "status"], DR, env).stdout)
            status.pop("data_root", None)
            mode_results[mode] = {"product": json.loads(product.stdout), "dr_status": status, "binary_digest": binaries[mode]}
    if mode_results["enabled"] != mode_results["absent"]:
        raise RuntimeError(f"target facts changed when governance was absent: {mode_results}")
    report = {
        "schema": "cartridgeflow.governance.removability-evidence.v1",
        "ok": True,
        "dependency_scan": detach.stdout.strip(),
        "modes": mode_results,
        "interpretation": "Target processes use only target-local PYTHONPATH/binaries; the absent path is nonexistent.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"Removability proof failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
