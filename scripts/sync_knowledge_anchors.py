"""Publish reviewed Knowledge source anchors and rebuild the matching index."""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .build_governance_index import (
        DEFAULT_INDEX,
        DEFAULT_TARGETS,
        build_index,
        knowledge_anchor_observation,
    )
    from .governance_db import DEFAULT_DATABASE, build_database, export_database
    from .governance_ledger import (
        DEFAULT_LEDGER,
        digest_payload,
        knowledge_snapshot_digest,
        record_knowledge_sync,
    )
except ImportError:  # Direct execution: python scripts/sync_knowledge_anchors.py
    from build_governance_index import DEFAULT_INDEX, DEFAULT_TARGETS, build_index, knowledge_anchor_observation
    from governance_db import DEFAULT_DATABASE, build_database, export_database
    from governance_ledger import DEFAULT_LEDGER, digest_payload, knowledge_snapshot_digest, record_knowledge_sync


ANCHOR_ALGORITHM = "artifact-set-sha256-v2"
ANCHOR_SECTION = """# Knowledge 源码锚点

Knowledge 卡只保存当前局部理解，不拥有规范规则或修订历史。每个 Knowledge 源码引用必须绑定审核时的确定性 artifact 集合摘要；摘要失配或无法解析时，代码事实优先，路由状态进入保守模式并扩大到对应目标楼层。同步原因和引用快照写入外部 Ledger。"""


def _upsert(items: list[dict[str, Any]], key: str, value: dict[str, Any]) -> bool:
    for index, item in enumerate(items):
        if item[key] != value[key]:
            continue
        if item == value:
            return False
        items[index] = value
        return True
    items.append(value)
    return True


def _apply_governance_rule(package: dict[str, Any]) -> bool:
    changed = False
    constitution = next(card for card in package["cards"] if card["card_id"] == "constitution.project")
    if "# Knowledge 源码锚点" not in constitution["body_markdown"]:
        constitution["revision"] = int(constitution["revision"]) + 1
        constitution["body_markdown"] = constitution["body_markdown"].rstrip() + "\n\n" + ANCHOR_SECTION + "\n"
        constitution["change_summary"] = "Require reviewed Knowledge source anchors and conservative drift routing."
        changed = True
    changed |= _upsert(
        package.setdefault("rules", []),
        "rule_id",
        {
            "rule_id": "constitution.knowledge-source-current",
            "card_id": "constitution.project",
            "severity": "warning",
            "statement": "Selected Knowledge source anchors must match current governed code facts.",
            "failure_message": "Knowledge source changed or cannot be resolved; validation must expand until the card is reviewed.",
        },
    )
    changed |= _upsert(
        package.setdefault("rule_check_bindings", []),
        "rule_id",
        {
            "rule_id": "constitution.knowledge-source-current",
            "checker_id": "check.governance.index",
            "binding_mode": "required",
        },
    )
    return changed


def synchronize(
    source_path: Path,
    index_path: Path,
    targets_path: Path,
    ledger_path: Path,
    *,
    actor: str,
    reason: str,
) -> list[str]:
    current = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    current.row_factory = sqlite3.Row
    before_digests: dict[str, str] = {}
    try:
        for card in current.execute(
            "SELECT card_id, content_digest FROM card WHERE card_type = 'knowledge' AND status = 'active'"
        ):
            source_refs = [
                dict(row)
                for row in current.execute(
                    "SELECT target_id, reference_kind, reference, purpose, anchor_algorithm, anchor_digest "
                    "FROM card_source_reference WHERE card_id = ? ORDER BY source_ref_id",
                    (card["card_id"],),
                )
            ]
            before_digests[str(card["card_id"])] = knowledge_snapshot_digest(
                str(card["content_digest"]), source_refs
            )
    finally:
        current.close()
    package = export_database(source_path)
    package_changed = _apply_governance_rule(package)
    cards = {str(item["card_id"]): item for item in package["cards"]}
    knowledge_ids = {
        card_id for card_id, item in cards.items() if item["card_type"] == "knowledge" and item["status"] == "active"
    }
    index = sqlite3.connect(f"{index_path.resolve().as_uri()}?mode=ro", uri=True)
    index.row_factory = sqlite3.Row
    changed_cards: set[str] = set()
    changed_paths_by_card: dict[str, set[str]] = {}
    try:
        for reference in package.get("source_references", []):
            card_id = str(reference["card_id"])
            if card_id not in knowledge_ids:
                reference.setdefault("anchor_algorithm", None)
                reference.setdefault("anchor_digest", None)
                continue
            observed_digest, artifacts = knowledge_anchor_observation(
                index,
                target_id=str(reference["target_id"]),
                reference_kind=str(reference["reference_kind"]),
                reference=str(reference["reference"]),
            )
            if observed_digest is None:
                raise RuntimeError(
                    f"Knowledge source reference does not resolve to governed artifacts: "
                    f"{card_id}:{reference['target_id']}:{reference['reference']}"
                )
            if reference.get("anchor_digest") != observed_digest or reference.get("anchor_algorithm") != ANCHOR_ALGORITHM:
                changed_cards.add(card_id)
                package_changed = True
                changed_paths_by_card.setdefault(card_id, set()).update(
                    str(item["artifact_path"]) for item in artifacts
                )
            reference["anchor_algorithm"] = ANCHOR_ALGORITHM
            reference["anchor_digest"] = observed_digest
            reference["anchored_artifact_paths"] = [item["artifact_path"] for item in artifacts]
    finally:
        index.close()
    if not package_changed:
        return []
    for reference in package.get("source_references", []):
        reference.pop("anchored_artifact_paths", None)
    package["publication_id"] = "knowledge-source-anchors-v2"
    package["published_at"] = datetime.now(timezone.utc).isoformat()
    source_path = source_path.resolve()
    index_path = index_path.resolve()
    with tempfile.TemporaryDirectory(prefix="knowledge-anchor-", dir=source_path.parent) as directory:
        temporary_root = Path(directory)
        next_source = temporary_root / "governance-source.sqlite"
        next_index = temporary_root / "governance-index.sqlite"
        build_database(package, next_source)
        build_index(next_source, targets_path, next_index)
        os.replace(next_source, source_path)
        os.replace(next_index, index_path)
    for card_id in sorted(changed_cards):
        current_references = [
            {
                "target_id": reference["target_id"],
                "reference_kind": reference["reference_kind"],
                "reference": reference["reference"],
                "anchor_algorithm": reference["anchor_algorithm"],
                "anchor_digest": reference["anchor_digest"],
            }
            for reference in package.get("source_references", [])
            if reference["card_id"] == card_id
        ]
        record_knowledge_sync(
            ledger_path,
            source_path,
            card_id=card_id,
            reason=reason,
            actor=actor,
            before_digest=before_digests.get(card_id),
            trigger_kind="source-anchor-review",
            trigger_reference=f"{ANCHOR_ALGORITHM}:{digest_payload(current_references)}",
            changed_paths=sorted(changed_paths_by_card.get(card_id, set())),
        )
    return sorted(changed_cards)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--actor", default="governance-anchor-sync")
    parser.add_argument("--reason", default="Review current source facts and synchronize Knowledge anchors.")
    args = parser.parse_args()
    try:
        changed = synchronize(
            args.source.resolve(),
            args.index.resolve(),
            args.targets.resolve(),
            args.ledger.resolve(),
            actor=args.actor,
            reason=args.reason,
        )
    except (OSError, RuntimeError, sqlite3.Error, KeyError, ValueError) as exc:
        print(f"Knowledge anchor synchronization failed: {exc}")
        return 1
    if changed:
        print("Synchronized Knowledge source anchors: " + ", ".join(changed))
    else:
        print("Knowledge source anchors are already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
