from __future__ import annotations

import hashlib
import sqlite3
import unittest

from fastapi.testclient import TestClient

from viewer.app import DEFAULT_INDEX, DEFAULT_SOURCE, create_app
from scripts.build_governance_index import DEFAULT_TARGETS
from scripts.run_governance_checks import run_checks
from scripts.governance_ledger import DEFAULT_LEDGER


def _digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CardBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        connection = sqlite3.connect(DEFAULT_LEDGER)
        has_source_run = connection.execute(
            "SELECT 1 FROM check_run WHERE checker_id = 'check.governance.source' LIMIT 1"
        ).fetchone()
        connection.close()
        if has_source_run is None:
            run_checks(
                DEFAULT_SOURCE,
                DEFAULT_TARGETS,
                DEFAULT_INDEX,
                path_specs=[],
                changed=False,
                requested_checker_ids={"check.governance.source"},
                timeout_seconds=30,
            )
        cls.client = TestClient(create_app(DEFAULT_SOURCE, DEFAULT_INDEX, DEFAULT_LEDGER))

    def test_core_read_only_views(self) -> None:
        for path, expected in (
            ("/", "治理总览"),
            ("/catalog", "总管目录"),
            ("/cards", "卡片目录"),
            ("/cards/constitution.project", "CartridgeFlow 外置治理宪法"),
            ("/rules", "规则目录"),
            ("/relations?view=ownership", "关系视图"),
            ("/coverage?status=uncovered", "作用域覆盖"),
            ("/dependencies?kind=typescript-import", "TypeScript"),
            ("/dependencies?kind=go-import", "cf.shell/internal"),
            ("/symbols?language=go", "符号视图"),
            ("/contracts?generation=legacy", "产品合同全局视图"),
            ("/findings?severity=error", "开放诊断"),
            ("/checks", "检测证据"),
            ("/impact?kind=card&q=floor.kernel-v060", "影响查询"),
            ("/context?paths=desktop-runner%3Ashell%2Fgo%2Finternal%2Fapi%2Fapi.go", "floor.dr-v060-sp"),
        ):
            response = self.client.get(path)
            self.assertEqual(200, response.status_code, path)
            self.assertIn(expected, response.text)
            self.assertEqual("1", response.headers["X-CartridgeFlow-Governance-Browser"])
            self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_card_detail_shows_revision_history(self) -> None:
        response = self.client.get("/cards/floor.kernel-v060")
        self.assertEqual(200, response.status_code)
        self.assertIn("修订历史", response.text)
        self.assertIn("v0.6.0 共同核心内核", response.text)
        self.assertIn("v0.6.0 共同 Base", response.text)

    def test_knowledge_card_has_current_content_without_history(self) -> None:
        response = self.client.get("/cards/knowledge.kernel-architecture")
        self.assertEqual(200, response.status_code)
        self.assertIn("当前可复用知识 · 无修订历史", response.text)
        self.assertIn("此卡只表达当前可复用知识，不保存历史", response.text)
        self.assertNotIn("rNone", response.text)

    def test_card_filters_and_global_contract_classification(self) -> None:
        response = self.client.get(
            "/cards",
            params={"card_type": "knowledge", "floor": "floor.kernel-v060", "scope": "src/core"},
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("knowledge.kernel-architecture", response.text)
        self.assertNotIn("knowledge.dr-runtime", response.text)

        response = self.client.get("/contracts", params={"disposition": "boundary"})
        self.assertEqual(200, response.status_code)
        self.assertIn("boundary.cartridge-handoff", response.text)
        self.assertNotIn('badge-neutral">unclassified', response.text)

    def test_check_detail_shows_rule_and_execution_evidence(self) -> None:
        connection = sqlite3.connect(DEFAULT_LEDGER)
        run_id = connection.execute(
            "SELECT run_id FROM check_run WHERE checker_id = 'check.governance.source' "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()[0]
        connection.close()
        response = self.client.get(f"/checks/{run_id}")
        self.assertEqual(200, response.status_code)
        self.assertIn("constitution.source-valid", response.text)
        self.assertIn("精确证据足迹", response.text)
        self.assertIn("checker-specific-closure", response.text)
        self.assertIn("target-configuration", response.text)
        self.assertIn("governance_db.py", response.text)

    def test_fts_card_search(self) -> None:
        response = self.client.get("/cards", params={"q": "语义", "mode": "fts"})
        self.assertEqual(200, response.status_code)
        self.assertIn("floor.workbench-v070", response.text)
        self.assertNotIn("没有匹配的卡片", response.text)

    def test_dr_context_preview_stays_isolated(self) -> None:
        response = self.client.get(
            "/context",
            params={"paths": "desktop-runner:shell/go/internal/api/api.go", "goal": "Inspect DR"},
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("floor.dr-v060-sp", response.text)
        self.assertNotIn("floor.workbench-v070", response.text)
        self.assertNotIn("boundary.cartridge-handoff", response.text)

    def test_contract_context_shows_reverse_routed_scenario(self) -> None:
        response = self.client.get(
            "/context",
            params={"contracts": "cartridgeflow:cartridgeflow.distribution.envelope@1.0.0"},
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("boundary.cartridge-handoff", response.text)
        self.assertIn("floor.dr-v060-sp", response.text)
        self.assertIn("floor.workbench-v070", response.text)
        self.assertIn("scenario.workbench-to-dr", response.text)
        self.assertIn("precise", response.text)

    def test_write_requests_are_rejected_without_database_changes(self) -> None:
        before = (_digest(DEFAULT_SOURCE), _digest(DEFAULT_INDEX), _digest(DEFAULT_LEDGER))
        for path in ("/", "/cards", "/findings", "/checks", "/context", "/api/summary"):
            response = self.client.post(path, json={"attempt": "write"})
            self.assertEqual(405, response.status_code, path)
        self.assertEqual(before, (_digest(DEFAULT_SOURCE), _digest(DEFAULT_INDEX), _digest(DEFAULT_LEDGER)))


if __name__ == "__main__":
    unittest.main()
