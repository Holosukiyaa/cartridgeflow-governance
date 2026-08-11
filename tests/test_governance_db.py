from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.governance_db import build_database, export_database, verify_database


def publication() -> dict:
    package = {
        "schema": "cartridgeflow.governance.card-publication.v2",
        "publication_id": "test-publication",
        "published_at": "2026-08-11T00:00:00Z",
        "cards": [
            {
                "card_id": "constitution.test",
                "card_type": "constitution",
                "title": "Test constitution",
                "summary": "Global test rule.",
                "status": "active",
                "authority": "normative",
                "revision": 1,
                "body_markdown": "# Test constitution",
            },
            {
                "card_id": "floor.producer",
                "card_type": "floor",
                "title": "Producer",
                "summary": "Produces a boundary value.",
                "status": "active",
                "authority": "normative",
                "revision": 1,
                "body_markdown": "# Producer",
            },
            {
                "card_id": "floor.consumer",
                "card_type": "floor",
                "title": "Consumer",
                "summary": "Consumes a boundary value.",
                "status": "active",
                "authority": "normative",
                "revision": 1,
                "body_markdown": "# Consumer",
            },
            {
                "card_id": "boundary.test",
                "card_type": "boundary",
                "title": "Boundary",
                "summary": "Connects producer and consumer.",
                "status": "active",
                "authority": "normative",
                "revision": 1,
                "body_markdown": "# Boundary",
            },
        ],
        "scopes": [
            {
                "scope_id": "scope.constitution",
                "card_id": "constitution.test",
                "target_id": "test",
                "selector_kind": "path_glob",
                "selector": "**",
                "polarity": "include",
                "ownership": "supporting",
                "rationale": "test",
            },
            {
                "scope_id": "scope.producer",
                "card_id": "floor.producer",
                "target_id": "test",
                "selector_kind": "path_glob",
                "selector": "producer/**",
                "polarity": "include",
                "ownership": "primary",
                "rationale": "test",
            },
            {
                "scope_id": "scope.consumer",
                "card_id": "floor.consumer",
                "target_id": "test",
                "selector_kind": "path_glob",
                "selector": "consumer/**",
                "polarity": "include",
                "ownership": "primary",
                "rationale": "test",
            },
            {
                "scope_id": "scope.boundary",
                "card_id": "boundary.test",
                "target_id": "test",
                "selector_kind": "api",
                "selector": "/test",
                "polarity": "include",
                "ownership": "supporting",
                "rationale": "test",
            },
        ],
        "relations": [
            {
                "relation_id": "relation.producer",
                "source_card_id": "boundary.test",
                "relation_type": "has_producer",
                "target_card_id": "floor.producer",
                "required": 1,
                "rationale": "test",
            },
            {
                "relation_id": "relation.consumer",
                "source_card_id": "boundary.test",
                "relation_type": "has_consumer",
                "target_card_id": "floor.consumer",
                "required": 1,
                "rationale": "test",
            },
        ],
        "checkers": [
            {
                "checker_id": "checker.source",
                "checker_kind": "python",
                "entrypoint": "scripts/governance_db.py",
                "description": "source verifier",
                "checker_stage": "source",
                "output_contract": "diagnostic-json-v1",
                "enabled": 1,
            }
        ],
        "rules": [
            {
                "rule_id": "rule.test",
                "card_id": "constitution.test",
                "severity": "blocker",
                "statement": "The test database must verify.",
                "failure_message": "The test database is invalid.",
            }
        ],
        "rule_check_bindings": [
            {"rule_id": "rule.test", "checker_id": "checker.source", "binding_mode": "required"}
        ],
    }
    card_ids = [card["card_id"] for card in package["cards"]]
    package["responsibilities"] = [
        {
            "responsibility_id": f"responsibility.{card_id}.{kind}",
            "card_id": card_id,
            "responsibility_kind": kind,
            "item_order": 0,
            "statement": f"{kind} fixture behavior",
        }
        for card_id in card_ids
        for kind in ("owns", "excludes")
    ]
    package["interfaces"] = [
        {
            "interface_id": f"interface.{card_id}.{direction}",
            "card_id": card_id,
            "direction": direction,
            "name": f"fixture-{direction}",
            "contract_ref": None,
            "counterparty_card_id": None,
            "description": f"Fixture {direction}.",
        }
        for card_id in card_ids
        for direction in ("input", "output")
    ]
    package["examples"] = [
        {
            "example_id": f"example.{card_id}.{kind}",
            "card_id": card_id,
            "example_kind": kind,
            "title": f"{kind} fixture",
            "description": f"A {kind} example.",
            "fixture_ref": None,
            "expected_rule_id": None,
            "expected_outcome": "pass" if kind == "valid" else "reject",
        }
        for card_id in card_ids
        for kind in ("valid", "invalid")
    ]
    package["evidence_requirements"] = [
        {
            "requirement_id": f"evidence.{card_id}",
            "card_id": card_id,
            "evidence_kind": "check",
            "statement": "The fixture checker must pass.",
        }
        for card_id in card_ids
    ]
    package["source_references"] = [
        {
            "source_ref_id": f"source.{card_id}",
            "card_id": card_id,
            "target_id": "test",
            "reference_kind": "path",
            "reference": "fixture.txt",
            "purpose": "Fixture source.",
        }
        for card_id in card_ids
    ]
    package["contract_bindings"] = [
        {
            "binding_id": "binding.boundary.test",
            "card_id": "boundary.test",
            "target_id": "test",
            "contract_id": "TEST-CONTRACT",
            "version_constraint": "1",
            "binding_role": "governs",
            "disposition": "boundary",
            "required": 1,
            "rationale": "Fixture boundary contract.",
        }
    ]
    return package


class GovernanceDatabaseTests(unittest.TestCase):
    def test_publish_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "governance.sqlite"
            build_database(publication(), database)
            self.assertEqual([], verify_database(database))

    def test_unbound_blocker_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "governance.sqlite"
            build_database(publication(), database)
            connection = sqlite3.connect(database)
            connection.execute("DELETE FROM rule_check_binding")
            connection.commit()
            connection.close()
            errors = verify_database(database)
            self.assertTrue(any("strong rule has no enabled required checker" in error for error in errors))

    def test_export_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.sqlite"
            second = root / "second.sqlite"
            build_database(publication(), first)
            build_database(export_database(first), second)
            self.assertEqual([], verify_database(second))

    def test_revision_history_survives_publication_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.sqlite"
            revised = root / "revised.sqlite"
            round_trip = root / "round-trip.sqlite"
            build_database(publication(), first)
            package = export_database(first)
            package["publication_id"] = "test-publication-revised"
            package["published_at"] = "2026-08-11T01:00:00Z"
            producer = next(
                card for card in package["cards"] if card["card_id"] == "floor.producer"
            )
            producer["revision"] = 2
            producer["title"] = "Revised producer"
            producer["body_markdown"] = "# Revised producer"
            producer["change_summary"] = "Revise the producer card only."
            build_database(package, revised)
            self.assertEqual([], verify_database(revised))
            exported = export_database(revised)
            producer_history = [
                item for item in exported["card_revisions"]
                if item["card_id"] == "floor.producer"
            ]
            self.assertEqual([1, 2], [item["revision"] for item in producer_history])
            self.assertEqual("Producer", producer_history[0]["snapshot"]["title"])
            self.assertEqual("Revised producer", producer_history[1]["snapshot"]["title"])
            build_database(exported, round_trip)
            self.assertEqual(exported["card_revisions"], export_database(round_trip)["card_revisions"])

    def test_incomplete_revision_history_fails(self) -> None:
        package = publication()
        package["cards"][1]["revision"] = 2
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "governance.sqlite"
            with self.assertRaisesRegex(RuntimeError, "card revision history is incomplete"):
                build_database(package, database)

    def test_missing_required_card_type_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "governance.sqlite"
            build_database(publication(), database)
            connection = sqlite3.connect(database)
            connection.execute("DELETE FROM card WHERE card_type = 'constitution'")
            connection.commit()
            connection.close()
            errors = verify_database(database)
            self.assertTrue(any("active constitution card is required" in error for error in errors))

    def test_dependency_cycle_fails(self) -> None:
        package = publication()
        package["relations"].extend(
            [
                {
                    "relation_id": "relation.cycle-a",
                    "source_card_id": "floor.producer",
                    "relation_type": "depends_on",
                    "target_card_id": "floor.consumer",
                    "required": 1,
                    "rationale": "test cycle",
                },
                {
                    "relation_id": "relation.cycle-b",
                    "source_card_id": "floor.consumer",
                    "relation_type": "depends_on",
                    "target_card_id": "floor.producer",
                    "required": 1,
                    "rationale": "test cycle",
                },
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "governance.sqlite"
            with self.assertRaisesRegex(RuntimeError, "card dependency cycle"):
                build_database(package, database)

    def test_every_active_card_requires_valid_and_invalid_examples(self) -> None:
        package = publication()
        package["examples"] = [
            item
            for item in package["examples"]
            if not (item["card_id"] == "floor.producer" and item["example_kind"] == "invalid")
        ]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "governance.sqlite"
            with self.assertRaisesRegex(RuntimeError, "active card lacks invalid example"):
                build_database(package, database)

    def test_knowledge_card_is_current_only_and_cannot_own_rules(self) -> None:
        package = publication()
        package["cards"].append(
            {
                "card_id": "knowledge.producer",
                "card_type": "knowledge",
                "title": "Producer knowledge",
                "summary": "Reusable producer knowledge.",
                "status": "active",
                "authority": "descriptive",
                "revision": None,
                "body_markdown": "# Producer knowledge",
            }
        )
        package["responsibilities"].extend(
            {
                "responsibility_id": f"responsibility.knowledge.producer.{kind}",
                "card_id": "knowledge.producer",
                "responsibility_kind": kind,
                "item_order": 0,
                "statement": f"{kind} producer knowledge",
            }
            for kind in ("owns", "excludes")
        )
        package["examples"].extend(
            {
                "example_id": f"example.knowledge.producer.{kind}",
                "card_id": "knowledge.producer",
                "example_kind": kind,
                "title": f"{kind} knowledge",
                "description": f"A {kind} knowledge use.",
                "fixture_ref": None,
                "expected_rule_id": None,
                "expected_outcome": "pass" if kind == "valid" else "reject",
            }
            for kind in ("valid", "invalid")
        )
        package["evidence_requirements"].append(
            {
                "requirement_id": "evidence.knowledge.producer",
                "card_id": "knowledge.producer",
                "evidence_kind": "source",
                "statement": "Knowledge must remain traceable to current source.",
            }
        )
        package["scopes"].append(
            {
                "scope_id": "scope.knowledge.producer",
                "card_id": "knowledge.producer",
                "target_id": "test",
                "selector_kind": "path_glob",
                "selector": "producer/**",
                "polarity": "include",
                "ownership": "reference",
                "rationale": "test",
            }
        )
        package["source_references"].append(
            {
                "source_ref_id": "source.knowledge.producer",
                "card_id": "knowledge.producer",
                "target_id": "test",
                "reference_kind": "path",
                "reference": "producer/fixture.py",
                "purpose": "Current implementation anchor.",
            }
        )
        package["relations"].append(
            {
                "relation_id": "relation.knowledge.producer",
                "source_card_id": "knowledge.producer",
                "relation_type": "explains",
                "target_card_id": "floor.producer",
                "required": 1,
                "rationale": "Knowledge explains exactly one floor.",
            }
        )
        package["knowledge_profiles"] = [
            {
                "card_id": "knowledge.producer",
                "floor_card_id": "floor.producer",
                "audience": "AI and maintainers",
                "applicability": "Producer work",
                "non_goals": "No history or normative rules",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "governance.sqlite"
            build_database(package, database)
            self.assertEqual([], verify_database(database))
            exported = export_database(database)
            self.assertIsNone(next(card for card in exported["cards"] if card["card_id"] == "knowledge.producer")["revision"])
            self.assertFalse(any(item["card_id"] == "knowledge.producer" for item in exported["card_revisions"]))

            package["rules"].append(
                {
                    "rule_id": "rule.knowledge.invalid",
                    "card_id": "knowledge.producer",
                    "severity": "info",
                    "statement": "Knowledge must not own this rule.",
                    "failure_message": "Invalid knowledge rule.",
                }
            )
            with self.assertRaisesRegex(RuntimeError, "knowledge card must not own normative rules"):
                build_database(package, Path(directory) / "invalid.sqlite")


if __name__ == "__main__":
    unittest.main()
