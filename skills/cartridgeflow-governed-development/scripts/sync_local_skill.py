from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
from pathlib import Path


SKILL_NAME = "cartridgeflow-governed-development"
SOURCE = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc"}


def files(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in IGNORED_PARTS for part in relative.parts) or path.suffix in IGNORED_SUFFIXES:
            continue
        result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def default_target() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    return codex_home / "skills" / SKILL_NAME


def check(target: Path) -> tuple[bool, list[str]]:
    expected = files(SOURCE)
    actual = files(target)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(path for path in set(expected) & set(actual) if expected[path] != actual[path])
    details = [*(f"missing: {path}" for path in missing), *(f"extra: {path}" for path in extra), *(f"changed: {path}" for path in changed)]
    return not details, details


def install(target: Path) -> None:
    if target == SOURCE:
        raise ValueError("refusing to install the local mirror over the governance source")
    skills_root = target.parent.resolve()
    if target.name != SKILL_NAME:
        raise ValueError(f"target directory must end with {SKILL_NAME}: {target}")
    skills_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{SKILL_NAME}-", dir=skills_root) as temp_dir:
        staged = Path(temp_dir) / SKILL_NAME
        shutil.copytree(SOURCE, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        backup = Path(temp_dir) / "previous"
        if target.exists():
            target.replace(backup)
        try:
            staged.replace(target)
        except Exception:
            if backup.exists() and not target.exists():
                backup.replace(target)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Install or verify the local Codex mirror of this governed skill.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--install", action="store_true")
    parser.add_argument("--target", type=Path, default=default_target())
    args = parser.parse_args()
    target = args.target.expanduser().resolve()

    if args.install:
        install(target)
    matched, details = check(target)
    if not matched:
        print(f"Local skill mirror differs: {target}")
        for detail in details:
            print(f"- {detail}")
        return 1
    version = (SOURCE / "VERSION").read_text(encoding="utf-8").strip()
    print(f"Local skill mirror is current: {SKILL_NAME}@{version} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
