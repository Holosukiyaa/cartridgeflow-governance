from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.build_governance_index import (
    DEFAULT_INDEX,
    DEFAULT_TARGETS,
    _governance_facts_digest,
    build_index,
    failing_findings,
)
from scripts.compile_context import compile_context
from scripts.governance_db import DEFAULT_DATABASE, build_database, export_database


def _repository(path: Path, remote: str, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Governance Test"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "governance-test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "fixture"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)


class ContextCompilerTests(unittest.TestCase):
    def test_stale_knowledge_expands_validation_to_target_floors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite"
            package = export_database(DEFAULT_DATABASE)
            package["publication_id"] = "stale-knowledge-test"
            package["published_at"] = "2026-08-11T10:00:00Z"
            reference = next(
                item for item in package["source_references"]
                if item["card_id"] == "knowledge.kernel-architecture"
            )
            reference["anchor_digest"] = "0" * 64
            build_database(package, source)
            index = root / "index.sqlite"
            build_index(source, DEFAULT_TARGETS, index)

            failures = failing_findings(index, "warning")
            self.assertTrue(
                any(item["finding_type"] == "knowledge-source-stale" for item in failures),
                failures,
            )
            context = compile_context(
                source,
                index,
                ["cartridgeflow:src/core/cartridge/runner.py"],
                targets_path=DEFAULT_TARGETS,
            )
            floors = {
                item["card_id"] for item in context["cards"] if item["card_type"] == "floor"
            }
            self.assertEqual(
                {
                    "floor.kernel-v060",
                    "floor.workbench-v070",
                    "floor.intent-studio-v070",
                    "floor.capability-workshop-v070",
                },
                floors,
            )
            self.assertEqual("conservative", context["routing"]["state"])
            self.assertEqual(["cartridgeflow"], context["routing"]["fallback_target_ids"])
            self.assertTrue(
                any(reason.startswith("knowledge-stale:knowledge.kernel-architecture:") for reason in context["routing"]["fallback_reasons"]),
                context["routing"],
            )

    def test_unowned_artifact_expands_validation_to_target_floors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.sqlite"
            shutil.copy2(DEFAULT_INDEX, index)
            connection = sqlite3.connect(index)
            artifact_id = connection.execute(
                "SELECT artifact_id FROM observed_artifact "
                "WHERE target_id = 'cartridgeflow' AND artifact_path = 'src/core/cartridge/runner.py'"
            ).fetchone()[0]
            connection.execute(
                "UPDATE scope_coverage SET coverage_status = 'uncovered' WHERE artifact_id = ?",
                (artifact_id,),
            )
            connection.execute(
                "UPDATE registry_metadata SET value = ? WHERE key = 'governance_facts_digest'",
                (_governance_facts_digest(connection),),
            )
            connection.commit()
            connection.close()
            context = compile_context(
                DEFAULT_DATABASE,
                index,
                ["cartridgeflow:src/core/cartridge/runner.py"],
                targets_path=DEFAULT_TARGETS,
            )
            floors = {
                item["card_id"] for item in context["cards"] if item["card_type"] == "floor"
            }
            self.assertEqual(
                {
                    "floor.kernel-v060",
                    "floor.workbench-v070",
                    "floor.intent-studio-v070",
                    "floor.capability-workshop-v070",
                },
                floors,
            )
            self.assertEqual("conservative", context["routing"]["state"])
            self.assertEqual(["cartridgeflow"], context["routing"]["fallback_target_ids"])

    def test_public_contract_reverse_routes_boundary_ends_and_scenario(self) -> None:
        context = compile_context(
            DEFAULT_DATABASE,
            DEFAULT_INDEX,
            [],
            targets_path=DEFAULT_TARGETS,
            contract_specs=["cartridgeflow:cartridgeflow.distribution.envelope@1.0.0"],
            goal="Review the public distribution envelope",
        )
        card_ids = {item["card_id"] for item in context["cards"]}
        self.assertIn("boundary.cartridge-handoff", card_ids)
        self.assertIn("floor.kernel-v060", card_ids)
        self.assertIn("floor.workbench-v070", card_ids)
        self.assertIn("floor.dr-v060-sp", card_ids)
        self.assertNotIn("boundary.runtime-delivery", card_ids)
        self.assertEqual(["scenario.workbench-to-dr"], [item["scenario_id"] for item in context["scenarios"]])
        self.assertEqual("precise", context["routing"]["state"])
        self.assertTrue(context["routing"]["contract_reverse_routing"])

    def test_context_isolated_by_path_and_adds_cross_repository_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / "CartridgeFlow"
            runner = root / "DesktopRunner"
            product_remote = "https://example.invalid/CartridgeFlow.git"
            runner_remote = "https://example.invalid/DesktopRunner.git"
            _repository(
                product,
                product_remote,
                {"src/backend/api.py": "def fixture_api():\n    return 1\n"},
            )
            _repository(
                runner,
                runner_remote,
                {"shell/main.go": "package main\n\nfunc main() {}\n"},
            )
            config = root / "targets.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "cartridgeflow.governance.targets.v1",
                        "targets": [
                            {
                                "id": "cartridgeflow",
                                "role": "product-and-workbench",
                                "path": str(product),
                                "remote": product_remote,
                                "governed_roots": ["src"],
                                "python_roots": ["src"],
                            },
                            {
                                "id": "desktop-runner",
                                "role": "runtime-shell",
                                "path": str(runner),
                                "remote": runner_remote,
                                "governed_roots": ["shell"],
                                "python_roots": [],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            index = root / "index.sqlite"
            build_index(DEFAULT_DATABASE, config, index)

            dr_context = compile_context(
                DEFAULT_DATABASE,
                index,
                ["desktop-runner:shell/main.go"],
                targets_path=config,
                goal="Inspect the runner shell",
            )
            dr_cards = {item["card_id"] for item in dr_context["cards"]}
            self.assertEqual({"constitution.project", "floor.dr-v060-sp"}, dr_cards)
            self.assertNotIn("floor.workbench-v070", dr_cards)
            self.assertNotIn("boundary.cartridge-handoff", dr_cards)

            cross_context = compile_context(
                DEFAULT_DATABASE,
                index,
                ["cartridgeflow:src/backend/api.py", "desktop-runner:shell/main.go"],
                targets_path=config,
                goal="Trace the cartridge handoff",
            )
            cross_cards = {item["card_id"] for item in cross_context["cards"]}
            self.assertEqual(
                {
                    "constitution.project",
                    "floor.kernel-v060",
                    "floor.workbench-v070",
                    "floor.dr-v060-sp",
                    "boundary.cartridge-handoff",
                },
                cross_cards,
            )
            repeated = compile_context(
                DEFAULT_DATABASE,
                index,
                ["cartridgeflow:src/backend/api.py", "desktop-runner:shell/main.go"],
                targets_path=config,
                goal="Trace the cartridge handoff",
            )
            self.assertEqual(cross_context["context_digest"], repeated["context_digest"])

    def test_changed_context_includes_transitive_dependency_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / "CartridgeFlow"
            remote = "https://example.invalid/CartridgeFlow.git"
            _repository(
                product,
                remote,
                {
                    "src/core/cartridge/base.py": "VALUE = 1\n\ndef fixture_base():\n    return VALUE\n",
                    "src/backend/api.py": "from core.cartridge.base import VALUE\n\ndef fixture_api():\n    return VALUE\n",
                },
            )
            (product / "src" / "core" / "cartridge" / "base.py").write_text(
                "VALUE = 2\n\ndef fixture_base():\n    return VALUE\n", encoding="utf-8"
            )
            config = root / "targets.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "cartridgeflow.governance.targets.v1",
                        "targets": [
                            {
                                "id": "cartridgeflow",
                                "role": "product-and-workbench",
                                "path": str(product),
                                "remote": remote,
                                "governed_roots": ["src"],
                                "python_roots": ["src"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            index = root / "index.sqlite"
            build_index(DEFAULT_DATABASE, config, index)
            context = compile_context(
                DEFAULT_DATABASE,
                index,
                [],
                targets_path=config,
                changed=True,
                goal="Review the kernel change",
            )
            artifacts = {item["artifact_path"]: item for item in context["artifacts"]}
            self.assertEqual(
                {"src/core/cartridge/base.py", "src/backend/api.py"},
                set(artifacts),
            )
            self.assertIn(
                "dependency-consumer-of:cartridgeflow:src/core/cartridge/base.py",
                artifacts["src/backend/api.py"]["selection_reasons"],
            )
            card_ids = {item["card_id"] for item in context["cards"]}
            self.assertEqual(
                {
                    "constitution.project",
                    "floor.kernel-v060",
                    "floor.workbench-v070",
                    "knowledge.kernel-architecture",
                },
                card_ids,
            )
            knowledge = next(
                item for item in context["cards"]
                if item["card_id"] == "knowledge.kernel-architecture"
            )
            self.assertIsNone(knowledge["revision"])
            self.assertTrue(
                any(reason.startswith("scoped-knowledge:") for reason in knowledge["selection_reasons"])
            )


if __name__ == "__main__":
    unittest.main()
