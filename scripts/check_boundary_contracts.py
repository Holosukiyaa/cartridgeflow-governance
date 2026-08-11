"""Verify every active boundary has exact contracts and independently checked ends."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "governance-source.sqlite"
INDEX = ROOT / ".data" / "governance-index.sqlite"


def main() -> int:
    source = sqlite3.connect(f"{SOURCE.resolve().as_uri()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    index = sqlite3.connect(f"{INDEX.resolve().as_uri()}?mode=ro", uri=True)
    index.row_factory = sqlite3.Row
    errors: list[dict[str, str]] = []
    checked = []
    try:
        for boundary in source.execute(
            "SELECT card_id FROM card WHERE card_type = 'boundary' AND status = 'active' ORDER BY card_id"
        ):
            card_id = str(boundary["card_id"])
            ends: dict[str, list[str]] = {}
            for relation_type in ("has_producer", "has_consumer"):
                ends[relation_type] = [
                    str(row[0])
                    for row in source.execute(
                        "SELECT target_card_id FROM card_relation WHERE source_card_id = ? AND relation_type = ? ORDER BY target_card_id",
                        (card_id, relation_type),
                    )
                ]
                if not ends[relation_type]:
                    errors.append({"card_id": card_id, "reason": f"missing {relation_type}"})
            for direction in ("input", "output"):
                if not source.execute(
                    "SELECT 1 FROM card_interface WHERE card_id = ? AND direction = ? LIMIT 1",
                    (card_id, direction),
                ).fetchone():
                    errors.append({"card_id": card_id, "reason": f"missing {direction} declaration"})
            bindings = [
                str(row[0])
                for row in source.execute(
                    "SELECT binding_id FROM card_contract_binding WHERE card_id = ? AND disposition = 'boundary' ORDER BY binding_id",
                    (card_id,),
                )
            ]
            if not bindings:
                errors.append({"card_id": card_id, "reason": "no boundary contract binding"})
            for binding_id in bindings:
                match = index.execute(
                    "SELECT match_status FROM card_contract_match WHERE binding_id = ?", (binding_id,)
                ).fetchone()
                if match is None or match[0] != "matched":
                    errors.append({"card_id": card_id, "reason": f"contract binding is not current: {binding_id}"})
            for end in sorted(set(ends.get("has_producer", []) + ends.get("has_consumer", []))):
                covered = source.execute(
                    "SELECT 1 FROM rule JOIN rule_check_binding AS binding ON binding.rule_id = rule.rule_id "
                    "JOIN checker ON checker.checker_id = binding.checker_id "
                    "WHERE rule.card_id = ? AND rule.severity IN ('blocker', 'error') "
                    "AND binding.binding_mode = 'required' AND checker.enabled = 1 AND checker.checker_stage = 'floor' LIMIT 1",
                    (end,),
                ).fetchone()
                if not covered:
                    errors.append({"card_id": card_id, "reason": f"boundary end lacks an independent floor checker: {end}"})
            checked.append({"card_id": card_id, "contracts": len(bindings), **ends})
    finally:
        source.close()
        index.close()
    result = {"ok": not errors, "stage": "boundary", "checked": checked, "errors": errors}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
