"""Prove that governance observes target repositories without becoming their dependency."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TARGETS_PATH = ROOT / "targets.json"
FORBIDDEN_RUNTIME_MARKERS = (
    "cartridgeflow-governance",
    "governance-source.sqlite",
    "governance-index.sqlite",
    "cf_governance",
)
TEXT_SUFFIXES = {
    ".c", ".cs", ".css", ".go", ".html", ".js", ".json", ".jsx", ".mjs",
    ".ps1", ".py", ".pyi", ".rs", ".sh", ".toml", ".ts", ".tsx", ".yaml", ".yml",
}
SKIP_DIRECTORIES = {".git", ".data", ".tools", "dist", "node_modules", "__pycache__"}


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _normalize_remote(value: str) -> str:
    normalized = value.strip().lower().replace("\\", "/")
    return normalized[:-4] if normalized.endswith(".git") else normalized


def _scan_runtime(root: Path) -> list[str]:
    findings: list[str] = []
    if not root.exists():
        return [f"runtime root does not exist: {root}"]
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        content = path.read_text(encoding="utf-8", errors="ignore").lower()
        for marker in FORBIDDEN_RUNTIME_MARKERS:
            if marker in content:
                findings.append(f"runtime references governance marker {marker}: {path}")
    return findings


def inspect_targets(config_path: Path = TARGETS_PATH) -> tuple[list[str], dict[str, Any]]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    snapshot: dict[str, Any] = {"schema": "cartridgeflow.governance.target-snapshot.v1", "targets": []}
    if config.get("schema") != "cartridgeflow.governance.targets.v1":
        errors.append("target registry schema is invalid")
    governance_root = ROOT.resolve()
    for target in config.get("targets", []):
        target_id = str(target.get("id", "<missing>"))
        target_path = (ROOT / str(target.get("path", ""))).resolve()
        if not target_path.is_dir():
            errors.append(f"target path does not exist: {target_id}:{target_path}")
            continue
        try:
            governance_root.relative_to(target_path)
            errors.append(f"governance repository is embedded inside target: {target_id}")
        except ValueError:
            pass
        try:
            repository_root = Path(_git(target_path, "rev-parse", "--show-toplevel")).resolve()
            head = _git(target_path, "rev-parse", "HEAD")
            remote = _git(target_path, "remote", "get-url", "origin")
            dirty_count = len(_git(target_path, "status", "--porcelain=v1").splitlines())
        except (OSError, subprocess.CalledProcessError) as exc:
            errors.append(f"cannot inspect target Git repository {target_id}: {exc}")
            continue
        if repository_root != target_path:
            errors.append(f"target path is not its Git root: {target_id}:{repository_root}")
        expected_remote = str(target.get("remote", ""))
        if expected_remote and _normalize_remote(remote) != _normalize_remote(expected_remote):
            errors.append(f"target origin mismatch: {target_id}:{remote}")
        for runtime_root in target.get("runtime_roots", []):
            errors.extend(_scan_runtime(target_path / str(runtime_root)))
        snapshot["targets"].append(
            {
                "id": target_id,
                "role": target.get("role"),
                "path": str(target_path),
                "repository_root": str(repository_root),
                "remote": remote,
                "head": head,
                "dirty_path_count": dirty_count,
            }
        )
    return errors, snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=TARGETS_PATH)
    parser.add_argument("--snapshot", action="store_true")
    args = parser.parse_args()
    try:
        errors, snapshot = inspect_targets(args.config.resolve())
        if errors:
            print("Detachability check failed:\n- " + "\n- ".join(errors))
            return 1
        if args.snapshot:
            output = ROOT / ".data" / "target-snapshot.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Target snapshot: {output}")
        print("Governance detachability verified.")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Detachability check failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

