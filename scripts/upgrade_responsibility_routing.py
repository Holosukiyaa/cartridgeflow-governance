"""Apply idempotent source-card upgrades for responsibility routing governance."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .governance_db import DEFAULT_DATABASE, build_database, export_database
except ImportError:  # Direct execution: python scripts/upgrade_responsibility_routing.py
    from governance_db import DEFAULT_DATABASE, build_database, export_database


FORMAL_SECTION = """# 产品正式验收

静态治理、楼层局部检查、边界检查和场景检查必须分别报告。CartridgeFlow 完整验收必须运行产品当前官方协议锁审计与 conformance；任何一项失败时不得报告产品通过。全局目录不等于全局失效域，不确定性只能扩大验证范围。"""


def _upsert(items: list[dict[str, Any]], key: str, value: dict[str, Any]) -> bool:
    for index, item in enumerate(items):
        if item[key] == value[key]:
            if item == value:
                return False
            items[index] = value
            return True
    items.append(value)
    return True


def upgrade(package: dict[str, Any]) -> bool:
    changed = False
    constitution = next(card for card in package["cards"] if card["card_id"] == "constitution.project")
    if "# 产品正式验收" not in constitution["body_markdown"]:
        constitution["revision"] = int(constitution["revision"]) + 1
        constitution["body_markdown"] = constitution["body_markdown"].rstrip() + "\n\n" + FORMAL_SECTION + "\n"
        constitution["change_summary"] = "Separate acceptance states and require official product acceptance."
        changed = True

    changed |= _upsert(
        package.setdefault("checkers", []),
        "checker_id",
        {
            "checker_id": "check.product.formal",
            "checker_kind": "python",
            "entrypoint": "scripts/check_product_formal.py",
            "description": "Run the product's official protocol lock audit and conformance suite.",
            "checker_stage": "floor",
            "output_contract": "diagnostic-json-v1",
            "enabled": 1,
        },
    )
    changed |= _upsert(
        package.setdefault("rules", []),
        "rule_id",
        {
            "rule_id": "constitution.product-formal-acceptance",
            "card_id": "constitution.project",
            "severity": "blocker",
            "statement": "Complete CartridgeFlow acceptance runs the current official protocol lock audit and conformance suite.",
            "failure_message": "Official product acceptance failed; static or local floor success cannot be reported as product success.",
        },
    )
    changed |= _upsert(
        package.setdefault("rule_check_bindings", []),
        "rule_id",
        {
            "rule_id": "constitution.product-formal-acceptance",
            "checker_id": "check.product.formal",
            "binding_mode": "required",
        },
    )
    changed |= _upsert(
        package.setdefault("scenarios", []),
        "scenario_id",
        {
            "scenario_id": "scenario.workbench-to-dr",
            "title": "工作台到 DR 的真实卡带交付",
            "description": "工作台生成并认证 Flow，通过正式包装 API 发行 CF-CRE@2 与 clean 安装计划，DR 通过公开安装 API 独立安装、消费公开设置、运行并产生交付结果。",
            "status": "active",
        },
    )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    database = args.database.resolve()
    package = export_database(database)
    if not upgrade(package):
        print("Responsibility routing source is already current.")
        return 0
    now = datetime.now(timezone.utc).isoformat()
    package["publication_id"] = "responsibility-routing-p0"
    package["published_at"] = now
    build_database(package, database)
    print(f"Published responsibility routing source upgrade to {database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
