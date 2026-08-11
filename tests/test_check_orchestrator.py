from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.build_governance_index import (
    DEFAULT_INDEX,
    DEFAULT_TARGETS,
    _governance_facts_digest,
    build_index,
)
from scripts.governance_db import DEFAULT_DATABASE
from scripts.governance_ledger import verify_ledger
from scripts.run_governance_checks import run_checks


class CheckOrchestratorTests(unittest.TestCase):
    def test_selected_checker_records_digest_verified_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.sqlite"
            ledger = Path(directory) / "ledger.sqlite"
            shutil.copy2(DEFAULT_INDEX, index)
            results = run_checks(
                DEFAULT_DATABASE,
                DEFAULT_TARGETS,
                index,
                path_specs=[],
                changed=False,
                requested_checker_ids={"check.governance.source"},
                timeout_seconds=30,
                ledger_path=ledger,
            )
            self.assertEqual(["passed"], [result["status"] for result in results])
            connection = sqlite3.connect(ledger)
            run = connection.execute(
                "SELECT checker_id, status, acceptance_stage FROM check_run"
            ).fetchone()
            evidence_count = connection.execute("SELECT COUNT(*) FROM evidence_dependency").fetchone()[0]
            acceptance = dict(connection.execute("SELECT acceptance_kind, status FROM acceptance_result"))
            connection.close()
            self.assertEqual(("check.governance.source", "passed", "static"), run)
            self.assertGreater(evidence_count, 10)
            self.assertEqual("passed", acceptance["static"])
            self.assertEqual("not-run", acceptance["complete"])
            self.assertEqual([], verify_ledger(ledger))

            before = ledger.read_bytes()
            build_index(DEFAULT_DATABASE, DEFAULT_TARGETS, index)
            self.assertEqual(before, ledger.read_bytes())

    def test_evidence_tampering_fails_index_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.sqlite"
            ledger = Path(directory) / "ledger.sqlite"
            shutil.copy2(DEFAULT_INDEX, index)
            run_checks(
                DEFAULT_DATABASE,
                DEFAULT_TARGETS,
                index,
                path_specs=[],
                changed=False,
                requested_checker_ids={"check.governance.source"},
                timeout_seconds=30,
                ledger_path=ledger,
            )
            connection = sqlite3.connect(ledger)
            evidence_id = connection.execute("SELECT dependency_id FROM evidence_dependency LIMIT 1").fetchone()[0]
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "UPDATE evidence_dependency SET observed_digest = ? WHERE dependency_id = ?",
                    ("tampered", evidence_id),
                )
            connection.close()
            self.assertEqual([], verify_ledger(ledger))

    def test_failed_index_checker_marks_only_the_finding_rule_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.sqlite"
            ledger = Path(directory) / "ledger.sqlite"
            shutil.copy2(DEFAULT_INDEX, index)
            connection = sqlite3.connect(index)
            connection.execute("PRAGMA foreign_keys = ON")
            artifact_id = connection.execute(
                "SELECT artifact_id FROM observed_artifact ORDER BY artifact_id LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO finding VALUES (?, 'warning', 'scope-uncovered', ?, ?, ?, ?, '{}', 'open')",
                (
                    "test-orchestrator-finding",
                    "constitution.scope-primary-owner",
                    "constitution.project",
                    artifact_id,
                    "Synthetic uncovered fixture",
                ),
            )
            connection.execute(
                "UPDATE registry_metadata SET value = ? WHERE key = 'governance_facts_digest'",
                (_governance_facts_digest(connection),),
            )
            connection.commit()
            connection.close()
            results = run_checks(
                DEFAULT_DATABASE,
                DEFAULT_TARGETS,
                index,
                path_specs=[],
                changed=False,
                requested_checker_ids={"check.governance.index"},
                timeout_seconds=30,
                ledger_path=ledger,
            )
            by_checker = {result["checker_id"]: result for result in results}
            self.assertEqual("passed", by_checker["check.governance.source"]["status"])
            index_result = by_checker["check.governance.index"]
            self.assertEqual("failed", index_result["status"])
            self.assertEqual(
                {
                    "constitution.dependency-declared": "passed",
                    "constitution.dependency-observable": "passed",
                    "constitution.knowledge-source-current": "passed",
                    "constitution.references-exist": "passed",
                    "constitution.scope-primary-owner": "failed",
                },
                index_result["rule_results"],
            )
            connection = sqlite3.connect(ledger)
            diagnostic = connection.execute(
                "SELECT rule_id, card_id, reason, expected, actual, boundary_card_id "
                "FROM check_diagnostic WHERE run_id = ?",
                (index_result["run_id"],),
            ).fetchone()
            connection.close()
            self.assertEqual("constitution.scope-primary-owner", diagnostic[0])
            self.assertEqual("constitution.project", diagnostic[1])
            self.assertTrue(diagnostic[2])
            self.assertTrue(diagnostic[3])
            self.assertTrue(diagnostic[4])
            self.assertIsNone(diagnostic[5])


if __name__ == "__main__":
    unittest.main()
