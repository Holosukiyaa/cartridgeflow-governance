"""检查 CF WS 的仓库归属与产品/治理边界。"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REPOSITORIES = {
    "CartridgeFlow": "https://github.com/Holosukiyaa/CartridgeFlow.git",
    "CartridgeFlow-governance": None,
    "CartridgeFlow-runtime-shell": "https://github.com/Holosukiyaa/cartridgeflow-runtime-shell.git",
}
EXPECTED_ROOT_FILES = {"AGENTS.md"}
FORBIDDEN_PRODUCT_PATHS = (
    "AGENT.md",
    "AGENTS.md",
    "MENTOR_WORKERS.md",
    "PLAN.md",
    "PRODUCT_EXPERIENCE_ARCHITECTURE.md",
    "todo.md",
    "protocol-source",
    "config/protocol-viewer",
    "demos",
    "docs/PROJECT_STATUS_AND_LINEAGE.md",
    "docs/development",
    "docs/protocol-rebuild",
    "scripts/launch_protocol_viewer.py",
    "scripts/demo_personal_runtime.py",
    "view-protocols.bat",
)
PRODUCT_TEXT_EXTENSIONS = {".css", ".html", ".js", ".json", ".md", ".mjs", ".py", ".ts", ".tsx"}


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


def _contains_material(path: Path) -> bool:
    return path.is_file() or (path.is_dir() and any(item.is_file() for item in path.rglob("*")))


def _external_governance_references(product: Path):
    ignored = {".git", ".data", "dist", "node_modules", "__pycache__"}
    for path in product.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in PRODUCT_TEXT_EXTENSIONS:
            continue
        if any(part in ignored for part in path.relative_to(product).parts):
            continue
        if "CartridgeFlow-governance" in path.read_text(encoding="utf-8", errors="replace"):
            yield path.relative_to(product).as_posix()


def inspect_workspace(
    workspace_root: Path | None = None,
    *,
    verify_git: bool = True,
) -> list[str]:
    workspace = (workspace_root or ROOT.parent).resolve()
    errors: list[str] = []
    if not workspace.is_dir():
        return [f"工作区不存在: {workspace}"]

    actual = {path.name for path in workspace.iterdir()}
    expected = set(EXPECTED_REPOSITORIES) | EXPECTED_ROOT_FILES
    for name in sorted(expected - actual):
        errors.append(f"缺少正式仓库: {name}")
    for name in sorted(actual - expected):
        errors.append(f"CF WS 根目录存在非正式内容: {name}")

    for name, expected_remote in EXPECTED_REPOSITORIES.items():
        repository = workspace / name
        if not repository.is_dir() or not verify_git:
            continue
        try:
            git_root = Path(_git(repository, "rev-parse", "--show-toplevel")).resolve()
        except (OSError, subprocess.CalledProcessError) as exc:
            errors.append(f"无法检查 Git 仓库 {name}: {exc}")
            continue
        if git_root != repository.resolve():
            errors.append(f"正式仓库不是独立 Git 根: {name}:{git_root}")
        if expected_remote:
            try:
                remote = _git(repository, "remote", "get-url", "origin")
            except (OSError, subprocess.CalledProcessError) as exc:
                errors.append(f"正式仓库缺少 origin: {name}:{exc}")
                continue
            if _normalize_remote(remote) != _normalize_remote(expected_remote):
                errors.append(f"正式仓库 origin 不匹配: {name}:{remote}")

    agent_entry = workspace / "AGENTS.md"
    if agent_entry.is_file():
        entry_text = agent_entry.read_text(encoding="utf-8", errors="replace")
        for required in ("git worktree", "compile_context.py", "CartridgeFlow-governance"):
            if required not in entry_text:
                errors.append(f"CF WS/AGENTS.md 缺少施工边界: {required}")

    product = workspace / "CartridgeFlow"
    for relative in FORBIDDEN_PRODUCT_PATHS:
        if _contains_material(product / relative):
            errors.append(f"产品仓包含外部治理或协议源内容: {relative}")
    if _contains_material(product / "DR"):
        errors.append("DR 必须是 CF WS 下的独立正式仓库，不能嵌入 CartridgeFlow")
    for relative in _external_governance_references(product):
        errors.append(f"产品仓显式引用外挂治理仓: {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=ROOT.parent)
    args = parser.parse_args()
    errors = inspect_workspace(args.workspace_root)
    if errors:
        print("工作区归属检查失败:\n- " + "\n- ".join(errors))
        return 1
    print("工作区归属检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
