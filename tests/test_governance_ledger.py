from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.build_governance_index import DEFAULT_INDEX, DEFAULT_TARGETS
from scripts.governance_db import DEFAULT_DATABASE, build_database, export_database
from scripts.governance_ledger import (
    KNOWLEDGE_SYNC_V1,
    KNOWLEDGE_SYNC_V2,
    SCHEMA_PATH,
    canonical_json,
    digest_payload,
    evidence_freshness,
    initialize_ledger,
    record_knowledge_sync,
    verify_ledger,
)
from scripts.run_governance_checks import run_checks


class GovernanceLedgerTests(unittest.TestCase):
    def test_legacy_ledger_migrates_without_rewriting_event_digests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.sqlite"
            schema = SCHEMA_PATH.read_text(encoding="utf-8")
            start = schema.index("CREATE TABLE knowledge_sync_event (")
            end = schema.index("\n\nCREATE INDEX route_run_finished_idx", start)
            legacy_table = """CREATE TABLE knowledge_sync_event (
    event_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    card_id TEXT NOT NULL,
    floor_card_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_digest TEXT,
    after_digest TEXT NOT NULL,
    actor TEXT NOT NULL,
    source_refs_json TEXT NOT NULL CHECK (json_valid(source_refs_json)),
    content_digest TEXT NOT NULL
) STRICT;"""
            schema = schema.replace("PRAGMA user_version = 2;", "PRAGMA user_version = 1;")
            schema = schema[:start] + legacy_table + schema[end:]
            payload = {
                "actor": "legacy-test",
                "after_digest": "after",
                "before_digest": None,
                "card_id": "knowledge.kernel-architecture",
                "floor_card_id": "floor.kernel-v060",
                "occurred_at": "2026-08-11T00:00:00+00:00",
                "reason": "legacy event",
                "source_refs": [],
            }
            connection = sqlite3.connect(ledger)
            connection.executescript(schema)
            connection.executemany(
                "INSERT INTO ledger_metadata VALUES (?, ?)",
                (
                    ("schema", "cartridgeflow.governance.ledger.v1"),
                    ("schema_version", "1"),
                    ("event_policy", "append-only"),
                ),
            )
            connection.execute(
                "INSERT INTO knowledge_sync_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "legacy-event",
                    payload["occurred_at"],
                    payload["card_id"],
                    payload["floor_card_id"],
                    payload["reason"],
                    payload["before_digest"],
                    payload["after_digest"],
                    payload["actor"],
                    canonical_json(payload["source_refs"]),
                    digest_payload(payload),
                ),
            )
            connection.commit()
            connection.close()

            initialize_ledger(ledger)

            connection = sqlite3.connect(ledger)
            row = connection.execute(
                "SELECT event_schema, trigger_kind, changed_paths_json, content_digest "
                "FROM knowledge_sync_event WHERE event_id = 'legacy-event'"
            ).fetchone()
            connection.close()
            self.assertEqual((KNOWLEDGE_SYNC_V1, "legacy", "[]", digest_payload(payload)), row)
            self.assertEqual([], verify_ledger(ledger))

    def test_unrelated_knowledge_change_does_not_stale_floor_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "ledger.sqlite"
            run_checks(
                DEFAULT_DATABASE,
                DEFAULT_TARGETS,
                DEFAULT_INDEX,
                path_specs=[],
                changed=False,
                requested_checker_ids={"check.governance.detachability"},
                timeout_seconds=30,
                ledger_path=ledger,
            )
            package = export_database(DEFAULT_DATABASE)
            knowledge = next(
                card for card in package["cards"]
                if card["card_id"] == "knowledge.dr-runtime"
            )
            knowledge["body_markdown"] += "\n\nCurrent-only test clarification.\n"
            package["publication_id"] = "knowledge-freshness-test"
            package["published_at"] = "2026-08-11T00:00:00Z"
            changed_source = root / "changed-source.sqlite"
            build_database(package, changed_source)
            freshness = evidence_freshness(
                ledger,
                changed_source,
                DEFAULT_INDEX,
                DEFAULT_TARGETS,
            )
            by_checker = {item["checker_id"]: item for item in freshness}
            self.assertEqual("stale", by_checker["check.governance.source"]["status"])
            self.assertEqual("current", by_checker["check.governance.detachability"]["status"])

    def test_ledger_is_append_only_and_records_external_knowledge_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.sqlite"
            initialize_ledger(ledger)
            event_id = record_knowledge_sync(
                ledger,
                DEFAULT_DATABASE,
                card_id="knowledge.kernel-architecture",
                reason="Refresh current architecture knowledge from its source references.",
                actor="test",
                before_digest=None,
                trigger_kind="source-review",
                trigger_reference="test-change",
                changed_paths=["src/core/cartridge/runner.py"],
                verification_run_ids=["test-verification"],
            )
            connection = sqlite3.connect(ledger)
            row = connection.execute(
                "SELECT card_id, floor_card_id, event_schema, trigger_kind, trigger_reference, "
                "changed_paths_json, verification_run_ids_json "
                "FROM knowledge_sync_event WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            self.assertEqual(
                (
                    "knowledge.kernel-architecture",
                    "floor.kernel-v060",
                    KNOWLEDGE_SYNC_V2,
                    "source-review",
                    "test-change",
                    '["src/core/cartridge/runner.py"]',
                    '["test-verification"]',
                ),
                row,
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute("DELETE FROM knowledge_sync_event WHERE event_id = ?", (event_id,))
            connection.close()
            self.assertEqual([], verify_ledger(ledger))


if __name__ == "__main__":
    unittest.main()
