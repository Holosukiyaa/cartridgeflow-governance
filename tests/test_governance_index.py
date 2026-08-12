from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from scripts.build_governance_index import (
    GovernanceIndexError,
    _go_import_references,
    _go_module_path,
    _resolve_go_reference,
    _resolve_typescript_reference,
    _target_snapshot,
    build_index,
    failing_findings,
    path_matches,
    verify_index,
    verify_index_freshness,
)
from scripts.governance_db import DEFAULT_DATABASE


class GovernanceIndexTests(unittest.TestCase):
    def test_path_matching(self) -> None:
        self.assertTrue(path_matches("src/core/cartridge/runner.py", "src/core/cartridge/**"))
        self.assertFalse(path_matches("src/core/studio/service.py", "src/core/cartridge/**"))

    def test_typescript_relative_resolution(self) -> None:
        artifacts = {
            "src/ui/api.ts": "target:api",
            "src/ui/toast.tsx": "target:toast",
            "src/shared/index.ts": "target:shared",
        }
        self.assertEqual(
            "target:api",
            _resolve_typescript_reference("src/ui/main.tsx", "./api", artifacts),
        )
        self.assertEqual(
            "target:toast",
            _resolve_typescript_reference("src/ui/main.tsx", "./toast.tsx", artifacts),
        )
        self.assertEqual(
            "target:api",
            _resolve_typescript_reference("src/ui/main.tsx", "./api.js", artifacts),
        )
        self.assertEqual(
            "target:shared",
            _resolve_typescript_reference("src/ui/main.tsx", "../shared", artifacts),
        )
        self.assertIsNone(_resolve_typescript_reference("src/ui/main.tsx", "react", artifacts))

    def test_go_ast_imports_and_resolution(self) -> None:
        references, parser_version = _go_import_references(
            b'''package sample
import (
    "fmt"
    alias "cf.shell/internal/store"
    _ `embed`
)
'''
        )
        self.assertEqual(
            [("fmt", 3), ("cf.shell/internal/store", 4), ("embed", 5)],
            references,
        )
        self.assertIn("tree-sitter-go/", parser_version)
        self.assertEqual("cf.shell", _go_module_path("module cf.shell\n\ngo 1.24\n"))
        packages = {"shell/go/internal/store": "desktop-runner:store.go"}
        self.assertEqual(
            "desktop-runner:store.go",
            _resolve_go_reference(
                "cf.shell/internal/store", "cf.shell", "shell/go", packages
            ),
        )
        self.assertIsNone(
            _resolve_go_reference("fmt", "cf.shell", "shell/go", packages)
        )
        with self.assertRaisesRegex(ValueError, "Go syntax tree contains an error"):
            _go_import_references(b"package broken\nimport (\n")

    def test_clean_tracked_digest_uses_git_blob_across_checkout_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "CartridgeFlow"
            source = target / "src" / "sample.py"
            source.parent.mkdir(parents=True)
            (target / ".gitattributes").write_text("*.py text eol=crlf\n", encoding="ascii")
            source.write_bytes(b"VALUE = 1\n")
            subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(target), "config", "user.name", "Governance Test"], check=True)
            subprocess.run(
                ["git", "-C", str(target), "config", "user.email", "governance-test@example.invalid"],
                check=True,
            )
            subprocess.run(["git", "-C", str(target), "add", ".gitattributes", "src/sample.py"], check=True)
            subprocess.run(
                ["git", "-C", str(target), "commit", "-m", "fixture"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(target), "remote", "add", "origin", "https://example.invalid/CartridgeFlow.git"],
                check=True,
            )
            expected, *_ = _target_snapshot(target, ["src"])

            source.unlink()
            subprocess.run(
                ["git", "-C", str(target), "checkout-index", "--force", "--", "src/sample.py"],
                check=True,
            )
            self.assertEqual(b"VALUE = 1\r\n", source.read_bytes())
            observed, *_ = _target_snapshot(target, ["src"])

            self.assertEqual(expected, observed)

            subprocess.run(
                ["git", "-C", str(target), "update-index", "--assume-unchanged", "src/sample.py"],
                check=True,
            )
            with self.assertRaisesRegex(GovernanceIndexError, "hidden Git index flags"):
                _target_snapshot(target, ["src"])

    def test_build_records_go_module_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "DesktopRunner"
            module = target / "shell" / "go"
            store = module / "internal" / "store" / "store.go"
            store.parent.mkdir(parents=True)
            (module / "go.mod").write_text("module cf.shell\n\ngo 1.24\n", encoding="utf-8")
            (module / "main.go").write_text(
                'package main\n\nimport (\n    "fmt"\n    "cf.shell/internal/store"\n)\n\nfunc main() { fmt.Println(store.Value) }\n',
                encoding="utf-8",
            )
            store.write_text("package store\n\nconst Value = 1\n", encoding="utf-8")
            subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(target), "config", "user.name", "Governance Test"], check=True)
            subprocess.run(
                ["git", "-C", str(target), "config", "user.email", "governance-test@example.invalid"],
                check=True,
            )
            subprocess.run(["git", "-C", str(target), "add", "shell"], check=True)
            subprocess.run(["git", "-C", str(target), "commit", "-m", "fixture"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(target), "remote", "add", "origin", "https://example.invalid/DesktopRunner.git"],
                check=True,
            )
            config = root / "targets.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "cartridgeflow.governance.targets.v1",
                        "targets": [
                            {
                                "id": "desktop-runner",
                                "role": "test",
                                "path": str(target),
                                "remote": "https://example.invalid/DesktopRunner.git",
                                "governed_roots": ["shell"],
                                "python_roots": [],
                                "typescript_packages": [],
                                "go_modules": ["shell/go"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            index = root / "index.sqlite"
            build_index(DEFAULT_DATABASE, config, index)
            self.assertEqual([], verify_index(index))
            connection = sqlite3.connect(index)
            dependencies = connection.execute(
                "SELECT target_reference, resolved_artifact_path, resolution_status "
                "FROM dependency_catalog WHERE dependency_kind = 'go-import' "
                "ORDER BY target_reference"
            ).fetchall()
            parser_versions = json.loads(connection.execute(
                "SELECT value FROM registry_metadata WHERE key = 'parser_versions'"
            ).fetchone()[0])
            connection.close()
            self.assertEqual(
                [
                    ("cf.shell/internal/store", "shell/go/internal/store/store.go", "resolved"),
                    ("fmt", None, "external"),
                ],
                dependencies,
            )
            self.assertIn("desktop-runner:shell/go:go", parser_versions)

    def test_build_reports_uncovered_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "CartridgeFlow"
            (target / "src" / "core" / "cartridge").mkdir(parents=True)
            (target / "src" / "core" / "cartridge" / "owned.py").write_text(
                "VALUE = 1\n\ndef owned():\n    return VALUE\n", encoding="utf-8"
            )
            (target / "src" / "unowned.py").write_text(
                "VALUE = 2\n\ndef unowned():\n    return VALUE\n", encoding="utf-8"
            )
            subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(target), "config", "user.name", "Governance Test"], check=True)
            subprocess.run(["git", "-C", str(target), "config", "user.email", "governance-test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(target), "add", "src"], check=True)
            subprocess.run(["git", "-C", str(target), "commit", "-m", "fixture"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(target), "remote", "add", "origin", "https://example.invalid/CartridgeFlow.git"], check=True)
            config = root / "targets.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "cartridgeflow.governance.targets.v1",
                        "targets": [
                            {
                                "id": "cartridgeflow",
                                "role": "test",
                                "path": str(target),
                                "remote": "https://example.invalid/CartridgeFlow.git",
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
            self.assertEqual([], verify_index(index))
            connection = sqlite3.connect(index)
            statuses = dict(connection.execute(
                "SELECT artifact.artifact_path, coverage.coverage_status "
                "FROM scope_coverage AS coverage JOIN observed_artifact AS artifact "
                "ON artifact.artifact_id = coverage.artifact_id"
            ))
            connection.close()
            self.assertEqual("covered", statuses["src/core/cartridge/owned.py"])
            self.assertEqual("uncovered", statuses["src/unowned.py"])
            failures = failing_findings(index, "warning")
            self.assertEqual(1, len(failures))
            self.assertEqual("scope-uncovered", failures[0]["finding_type"])
            self.assertEqual("src/unowned.py", failures[0]["artifact_path"])

            repeated_index = root / "repeated-index.sqlite"
            build_index(DEFAULT_DATABASE, config, repeated_index)
            first = sqlite3.connect(index)
            repeated = sqlite3.connect(repeated_index)
            first_digest = first.execute(
                "SELECT value FROM registry_metadata WHERE key = 'governance_facts_digest'"
            ).fetchone()[0]
            repeated_digest = repeated.execute(
                "SELECT value FROM registry_metadata WHERE key = 'governance_facts_digest'"
            ).fetchone()[0]
            first.close()
            repeated.close()
            self.assertEqual(first_digest, repeated_digest)
            (target / "src" / "core" / "cartridge" / "owned.py").write_text(
                "VALUE = 3\n\ndef owned():\n    return VALUE\n", encoding="utf-8"
            )
            freshness_errors = verify_index_freshness(DEFAULT_DATABASE, config, index)
            self.assertTrue(
                any("governed content changed" in error for error in freshness_errors),
                freshness_errors,
            )

    def test_undocumented_cross_card_dependency_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "CartridgeFlow"
            kernel = target / "src" / "core" / "cartridge" / "owned.py"
            workbench = target / "src" / "backend" / "api.py"
            kernel.parent.mkdir(parents=True)
            workbench.parent.mkdir(parents=True)
            kernel.write_text(
                "from backend.api import VALUE\n\ndef kernel():\n    return VALUE\n",
                encoding="utf-8",
            )
            workbench.write_text(
                "VALUE = 1\n\ndef backend():\n    return VALUE\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(target), "config", "user.name", "Governance Test"], check=True)
            subprocess.run(
                ["git", "-C", str(target), "config", "user.email", "governance-test@example.invalid"],
                check=True,
            )
            subprocess.run(["git", "-C", str(target), "add", "src"], check=True)
            subprocess.run(["git", "-C", str(target), "commit", "-m", "fixture"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(target), "remote", "add", "origin", "https://example.invalid/CartridgeFlow.git"],
                check=True,
            )
            config = root / "targets.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "cartridgeflow.governance.targets.v1",
                        "targets": [
                            {
                                "id": "cartridgeflow",
                                "role": "test",
                                "path": str(target),
                                "remote": "https://example.invalid/CartridgeFlow.git",
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
            failures = failing_findings(index, "error")
            self.assertEqual(1, len(failures))
            self.assertEqual("card-dependency-undocumented", failures[0]["finding_type"])
            connection = sqlite3.connect(index)
            dependency = connection.execute(
                "SELECT source_artifact_path, resolved_artifact_path, resolution_status "
                "FROM dependency_catalog WHERE target_reference = 'backend.api.VALUE'"
            ).fetchone()
            connection.close()
            self.assertEqual(
                ("src/core/cartridge/owned.py", "src/backend/api.py", "resolved"),
                dependency,
            )


if __name__ == "__main__":
    unittest.main()
