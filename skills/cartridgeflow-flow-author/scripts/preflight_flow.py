#!/usr/bin/env python
"""Validate one CartridgeFlow development cartridge without starting the API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight a CartridgeFlow development cartridge.")
    parser.add_argument("--repo", default=".", help="CartridgeFlow repository root")
    parser.add_argument("--package", required=True, help="Cartridge package directory")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    package = Path(args.package).resolve()
    source_root = repo / "src"
    if not (source_root / "core").is_dir():
        parser.error(f"--repo is not a CartridgeFlow checkout: {repo}")
    if not package.is_dir():
        parser.error(f"--package does not exist: {package}")
    sys.path.insert(0, str(source_root))

    from core.cartridge.validator import ManifestValidationError, ManifestValidator
    from core.lab.flow_analyzer import analyze_flow
    from core.protocol import load_base_implementation
    from core.studio.resource_catalog import build_flow_resource_catalog

    manifest = _load_json(package / "manifest.json")
    root_entry = str((manifest.get("root_flow") or {}).get("entry") or "root.flow.json")
    root_flow = _load_json(package / root_entry)
    result: dict[str, object] = {
        "cartridge_id": manifest.get("id"),
        "package": str(package),
        "manifest": {"ok": True, "error": ""},
    }
    try:
        ManifestValidator().validate_package(package, manifest)
    except ManifestValidationError as exc:
        result["manifest"] = {"ok": False, "error": str(exc)}

    # A v1 execution plan is runnable only relative to this checkout's Base
    # declaration. Passing no Base makes every v1 cartridge look unsupported.
    analysis = analyze_flow(root_flow, manifest, target="dev", base=load_base_implementation(repo))
    findings = analysis.get("findings") or []
    result["flow_analysis"] = {
        "blockers": sum(item.get("severity") == "blocker" for item in findings if isinstance(item, dict)),
        "warnings": sum(item.get("severity") == "warning" for item in findings if isinstance(item, dict)),
        "findings": findings,
    }
    catalog = build_flow_resource_catalog(repo, manifest, root_flow, package_path=package)
    result["resources"] = {
        "tools": [
            {
                "id": item.get("id"),
                "node_references": item.get("node_references"),
                "status": item.get("status"),
                "parse_status": item.get("parse_status"),
            }
            for item in catalog.get("tools") or []
            if item.get("manifest_requirement", {}).get("declared")
        ],
        "findings": catalog.get("findings") or [],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["manifest"]["ok"] and not result["flow_analysis"]["blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
