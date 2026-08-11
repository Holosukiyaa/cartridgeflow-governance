from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.build_governance_index import DEFAULT_INDEX, DEFAULT_TARGETS
from scripts.governance_db import DEFAULT_DATABASE, build_database, export_database
from scripts.governance_ledger import (
    evidence_freshness,
    initialize_ledger,
    record_knowledge_sync,
    verify_ledger,
)
from scripts.run_governance_checks import run_checks


class GovernanceLedgerTests(unittest.TestCase):
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
            )
            connection = sqlite3.connect(ledger)
            row = connection.execute(
                "SELECT card_id, floor_card_id FROM knowledge_sync_event WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            self.assertEqual(("knowledge.kernel-architecture", "floor.kernel-v060"), row)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute("DELETE FROM knowledge_sync_event WHERE event_id = ?", (event_id,))
            connection.close()
            self.assertEqual([], verify_ledger(ledger))


if __name__ == "__main__":
    unittest.main()
