from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "cartridgeflow-flow-author"
    / "scripts"
    / "validate_authored_cartridge.py"
)
SPEC = importlib.util.spec_from_file_location("authored_cartridge_validation", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class AuthoredCartridgeValidationTests(unittest.TestCase):
    def test_detects_question_mark_corruption_in_deliverable_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            (package / "manifest.json").write_text('{"name":"???"}', encoding="utf-8")
            findings = VALIDATOR.text_findings(package)

        self.assertEqual("TEXT_PLACEHOLDER_CORRUPTION", findings[0]["code"])
        self.assertEqual("manifest.json", findings[0]["path"])

    def test_requires_visible_labels_for_manifest_and_nodes(self) -> None:
        findings = VALIDATOR.required_text_findings(
            {"name": "Card", "description": "", "inputs": [{"label": ""}]},
            {"states": {"start": {"title": "", "display_name": "Start"}}},
        )

        self.assertEqual(
            {"manifest.description", "manifest.inputs[0].label", "root_flow.states.start.title"},
            {item["path"] for item in findings},
        )

    def test_rejects_llm_hidden_as_transform_without_decision_contract(self) -> None:
        findings = VALIDATOR.node_semantic_findings({
            "states": {
                "draft": {
                    "type": "process",
                    "kind": "transform",
                    "executor": "llm",
                    "effect": "read_only",
                },
            },
        })

        self.assertEqual(
            {"LLM_KIND_MISMATCH", "LLM_EFFECT_MISMATCH", "LLM_OUTPUT_CONTRACT_MISSING", "LLM_DECISION_CONSUME_MISSING"},
            {item["code"] for item in findings},
        )

    def test_accepts_protocol_clean_llm_decision(self) -> None:
        findings = VALIDATOR.node_semantic_findings({
            "states": {
                "draft": {
                    "type": "process",
                    "kind": "decision",
                    "executor": "llm",
                    "effect": "none",
                    "output_contract": "decision_envelope.v1",
                    "decision_contract": {
                        "schema": "decision_envelope.v1",
                        "consume": {"mode": "payload_path", "path": "payload.daily_brief", "as": "daily_brief"},
                    },
                },
            },
        })

        self.assertEqual([], findings)

    def test_primary_artifact_can_be_declared_by_action_params(self) -> None:
        findings = VALIDATOR.delivery_contract_findings(
            {"delivery": {"primary_output": "daily_video"}},
            {"states": {"render": {"type": "process", "params": {"artifact_id": "daily_video"}}}},
        )

        self.assertEqual([], findings)

    def test_runtime_delivery_requires_real_nonempty_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            run_dir = repo / ".data" / "runtime" / "runs" / "run_demo"
            artifacts_dir = run_dir / "artifacts"
            artifacts_dir.mkdir(parents=True)
            artifact_path = artifacts_dir / "result.json"
            artifact_path.write_text('{"ok":true}', encoding="utf-8")
            (run_dir / "run.json").write_text(json.dumps({
                "status": "completed",
                "errors": [],
                "data_chain": {"passed": True},
            }), encoding="utf-8")
            artifact = {
                "artifact_id": "result",
                "name": "result.json",
                "path": str(artifact_path),
                "type": "json",
                "mime_type": "application/json",
            }
            (run_dir / "delivery.json").write_text(json.dumps({
                "status": "delivered",
                "primary_output": "result",
                "primary_artifact": artifact,
                "artifacts": [artifact],
            }), encoding="utf-8")

            findings = VALIDATOR.runtime_delivery_findings(repo, "run_demo")

        self.assertEqual([], findings)

    def test_runtime_delivery_accepts_store_backed_primary_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            run_dir = repo / ".data" / "runtime" / "runs" / "run_store"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({
                "status": "completed",
                "errors": [],
                "data_chain": {"passed": True},
            }), encoding="utf-8")
            (run_dir / "delivery.json").write_text(json.dumps({
                "status": "delivered",
                "primary_output": "final_result",
                "primary_artifact": None,
                "artifacts": [],
                "result": {"status": "ready"},
            }), encoding="utf-8")

            findings = VALIDATOR.runtime_delivery_findings(
                repo,
                "run_store",
                expected_primary_output="final_result",
                expected_primary_type="store",
            )

        self.assertEqual([], findings)
