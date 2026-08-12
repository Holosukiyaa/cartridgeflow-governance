#!/usr/bin/env python
"""Validate the deliverable state of an authored CartridgeFlow package.

This complements ``preflight_flow.py``.  Preflight checks contracts; this
script also checks the values a user will actually see, plus local resource and
model bindings that a structural Flow analysis cannot establish by itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


TEXT_EXTENSIONS = {".json", ".md", ".html", ".txt"}
QUESTION_RUN = re.compile(r"\?{3,}")
MOJIBAKE_MARKERS = ("\ufffd", "\u00c3", "\u00e2\u20ac", "\u00e2\u20ac\u2122")


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def text_findings(package: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in package.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        relative = path.relative_to(package).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append({
                "code": "TEXT_NOT_UTF8",
                "path": relative,
                "message": "Text asset is not valid UTF-8.",
            })
            continue
        if QUESTION_RUN.search(content):
            findings.append({
                "code": "TEXT_PLACEHOLDER_CORRUPTION",
                "path": relative,
                "message": "Found a run of three or more question marks in a user-deliverable file.",
            })
        marker = next((item for item in MOJIBAKE_MARKERS if item in content), "")
        if marker:
            findings.append({
                "code": "TEXT_MOJIBAKE_SUSPECTED",
                "path": relative,
                "message": "Found a replacement character or common UTF-8 mojibake marker.",
            })
    return findings


def required_text_findings(manifest: dict[str, Any], root_flow: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    def require(value: Any, path: str) -> None:
        if not isinstance(value, str) or not value.strip():
            findings.append({"code": "TEXT_REQUIRED_MISSING", "path": path, "message": "Required user-facing text is empty."})

    require(manifest.get("name"), "manifest.name")
    require(manifest.get("description"), "manifest.description")
    for index, item in enumerate(manifest.get("inputs") or []):
        if isinstance(item, dict):
            require(item.get("label"), f"manifest.inputs[{index}].label")
    for index, item in enumerate(manifest.get("outputs") or []):
        if isinstance(item, dict):
            require(item.get("label"), f"manifest.outputs[{index}].label")
    for index, item in enumerate(manifest.get("mcp_tools") or []):
        if isinstance(item, dict):
            require(item.get("name"), f"manifest.mcp_tools[{index}].name")
            require(item.get("description"), f"manifest.mcp_tools[{index}].description")
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    for node_id, state in states.items():
        if isinstance(state, dict):
            require(state.get("title"), f"root_flow.states.{node_id}.title")
            require(state.get("display_name"), f"root_flow.states.{node_id}.display_name")
    return findings


def execution_plan_findings(root_flow: dict[str, Any]) -> list[dict[str, str]]:
    """Flag a broken main chain without rejecting protocol-legal dead ends.

    CF-FARP@1.1 allows a state to end the flow with no successful outgoing
    edge, so this is a warning, not a blocker. It exists because a lost
    sequence edge (e.g. an interaction node whose approval path was not saved)
    silently breaks the chain and degrades both the runner and the canvas.
    """
    findings: list[dict[str, str]] = []
    plan = root_flow.get("execution_plan") if isinstance(root_flow.get("execution_plan"), dict) else None
    if not plan:
        return findings
    edges = [edge for edge in plan.get("edges") or [] if isinstance(edge, dict)]
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}

    def is_success(edge: dict) -> bool:
        return str(edge.get("kind") or "sequence") != "failure"

    with_success_incoming: set[str] = set()
    for edge in edges:
        if is_success(edge) and str(edge.get("to") or "") in states:
            with_success_incoming.add(str(edge.get("to")))
    for node_id, state in states.items():
        if not isinstance(state, dict) or str(state.get("type") or "") == "terminal":
            continue
        if node_id not in with_success_incoming:
            continue
        has_success_outgoing = any(is_success(edge) and str(edge.get("from") or "") == node_id for edge in edges)
        if not has_success_outgoing:
            findings.append({
                "severity": "warning",
                "code": "FLOW_SUCCESSOR_EDGE_MISSING",
                "path": f"root_flow.states.{node_id}",
                "message": (
                    f"State '{node_id}' has a non-failure incoming edge but no non-failure outgoing edge; "
                    "the main chain breaks here. Add a sequence edge, or make the state a terminal if this is the flow end."
                ),
            })
    return findings


def review_route_findings(root_flow: dict[str, Any]) -> list[dict[str, str]]:
    """Review nodes (confirm_checkpoint) with answer_routes: a rejected route
    that does NOT clear the approval store key leaves the next review run
    auto-approved (store key still present), silently skipping the revision
    gate. Flag it so authors add clear_store_keys + copy_answer_to."""
    findings: list[dict[str, Any]] = []
    for node_id, state in (root_flow.get("states") or {}).items():
        if not isinstance(state, dict) or state.get("action") != "confirm_checkpoint":
            continue
        params = state.get("params") if isinstance(state.get("params"), dict) else {}
        interaction = params.get("interaction") if isinstance(params.get("interaction"), dict) else {}
        routes = interaction.get("answer_routes")
        if not isinstance(routes, list):
            continue
        for index, route in enumerate(routes):
            if not isinstance(route, dict):
                continue
            matcher = route.get("match") if isinstance(route.get("match"), dict) else {}
            equals_raw = matcher.get("equals")
            equals_values: list[str] = []
            if isinstance(equals_raw, list):
                equals_values = [str(item) for item in equals_raw if isinstance(item, (str, int, float, bool))]
            elif isinstance(equals_raw, str):
                equals_values = [equals_raw]
            equals_normalized = {str(v).strip().lower() for v in equals_values if str(v).strip()}
            is_rejection = bool(equals_normalized & {"rejected", "deny", "no", "disapproved"})
            if not is_rejection:
                continue
            if not route.get("clear_store_keys"):
                findings.append({
                    "severity": "warning",
                    "code": "REVIEW_ROUTE_CLEAR_MISSING",
                    "path": f"root_flow.states.{node_id}.params.interaction.answer_routes[{index}]",
                    "message": (
                        f"Review node '{node_id}' rejection route (equals matching a rejection "
                        "value heuristically) does not clear the approval "
                        "store key. Without clear_store_keys the next review run sees the old "
                        "answer and auto-approves, skipping the revision gate. Add "
                        "clear_store_keys (approval key) and copy_answer_to (feedback key)."
                    ),
                })
    return findings


def flow_start_entry_findings(root_flow: dict[str, Any]) -> list[dict[str, str]]:
    """Detect root flows whose top-level entry wiring is missing.

    A v1 root flow must declare ``start`` (top-level) and a ``start`` state;
    without them the runnable performs a structure check that flags every
    node as unreachable and finishes without executing anything (a silent
    no-op run). Preflight/static conformance do not always catch this.
    """
    findings: list[dict[str, str]] = []
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    start_decl = root_flow.get("start")
    if not isinstance(start_decl, str):
        start_decl = ""
    start_key = start_decl.strip()
    if not start_key:
        findings.append({
            "code": "FLOW_START_ENTRY_MISSING",
            "severity": "blocker",
            "path": "root_flow.start",
            "message": "root flow 缺少顶层 start 入口声明（如 start: start）——运行时会空跑（所有节点被判不可达后直接完成）。",
        })
    elif start_key not in states:
        findings.append({
            "code": "FLOW_START_ENTRY_MISSING",
            "severity": "blocker",
            "path": f"root_flow.start={start_key}",
            "message": f"顶层 start 指向 {start_key}，但 states 中无此节点。",
        })
    if start_key == "start" and "start" not in states:
        findings.append({
            "code": "FLOW_START_ENTRY_MISSING",
            "severity": "blocker",
            "path": "root_flow.states.start",
            "message": "states 缺少顶层 start 指向的节点（入口节点 type 应为 control，不是 terminal）。",
        })
    entry_state = states.get(start_key)
    if isinstance(entry_state, dict) and entry_state.get("type") == "terminal":
        findings.append({
            "code": "FLOW_START_ENTRY_MISSING",
            "severity": "blocker",
            "path": f"root_flow.states.{start_key}.type",
            "message": f"入口节点 {start_key} 的 type 是 terminal——应为 control，否则运行时空跑（所有节点被判不可达后直接完成）。",
        })
    return findings


def llm_retry_policy_findings(root_flow: dict[str, Any]) -> list[dict[str, str]]:
    """Warn when an llm_prompt node has no node-level retry_policy.

    Real LLM calls intermittently return PROVIDER_EMPTY_RESPONSE
    (finish_reason=length on reasoning models). Without retry_policy the
    node fails immediately and follows the failure edge; with it the engine
    re-schedules automatically (max_attempts/backoff). Treat retry_policy as
    required on every LLM decision node.
    """
    findings: list[dict[str, str]] = []
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    for node_id, state in states.items():
        if not isinstance(state, dict):
            continue
        if state.get("action") != "llm_prompt":
            continue
        params = state.get("params") if isinstance(state.get("params"), dict) else {}
        preset = params.get("preset_config") if isinstance(params.get("preset_config"), dict) else {}
        has_policy = bool(state.get("retry_policy") or params.get("retry_policy") or preset.get("retry_policy"))
        if not has_policy:
            findings.append({
                "code": "LLM_RETRY_POLICY_MISSING",
                "severity": "warning",
                "path": f"root_flow.states.{node_id}.retry_policy",
                "message": f"LLM 节点 {node_id} 没有 retry_policy——真实模型调用偶发空响应（PROVIDER_EMPTY_RESPONSE），建议配置 max_attempts>=3 让引擎自动重试。",
            })
    return findings


def description_findings(root_flow: dict[str, Any]) -> list[dict[str, str]]:
    """Flag nodes whose card guidance is generic because no description was written.

    The canvas card shows params.description as the node's "what it does" text;
    without it the frontend falls back to template copy. This is a warning so a
    missing description never blocks a runnable package, but it signals the AI
    should write real per-node guidance.
    """
    findings: list[dict[str, str]] = []
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    for node_id, state in states.items():
        if not isinstance(state, dict):
            continue
        if str(state.get("type") or "") == "terminal" or str(node_id) in {"start", "complete"}:
            continue
        params = state.get("params") if isinstance(state.get("params"), dict) else {}
        description = str(params.get("description") or "").strip()
        if not description:
            findings.append({
                "severity": "warning",
                "code": "NODE_DESCRIPTION_MISSING",
                "path": f"root_flow.states.{node_id}.params.description",
                "message": (
                    f"State '{node_id}' has no description. The canvas card shows this text as the "
                    "node's guidance; write a concrete one-liner (what it consumes, produces, and why)."
                ),
            })
    return findings


def review_binding_findings(root_flow: dict[str, Any]) -> list[dict[str, str]]:
    """Flag review-node (confirm_checkpoint) input bindings that silently render
    an empty review screen.

    Lesson A3 (AI video daily): the drafted brief binding referenced an output
    that was never declared in the source node's ``outputs`` contract, so the
    runtime could not resolve it and ``review_content`` came back empty — the
    user was asked to confirm an empty screen. Bindings to ``artifact`` targets
    resolve to a descriptor (path/name) instead of the text the reviewer needs.
    """
    findings: list[dict[str, str]] = []
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    for node_id, state in states.items():
        if not isinstance(state, dict) or str(state.get("action") or "") != "confirm_checkpoint":
            continue
        inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
        for port, contract in inputs.items():
            if not isinstance(contract, dict):
                continue
            binding = contract.get("binding") if isinstance(contract.get("binding"), dict) else None
            if not binding:
                continue
            source = str(binding.get("source") or "")
            if source == "node_output":
                src_id = str(binding.get("node_id") or "")
                out_name = str(binding.get("output") or "")
                src_node = states.get(src_id) if isinstance(states.get(src_id), dict) else None
                if not src_node or not out_name:
                    findings.append({
                        "severity": "warning",
                        "code": "REVIEW_BINDING_UNRESOLVED",
                        "path": f"root_flow.states.{node_id}.inputs.{port}.binding",
                        "message": (
                            f"Review node '{node_id}' input '{port}' references '{src_id}:{out_name}' "
                            "which cannot be resolved. The review screen will be empty."
                        ),
                    })
                    continue
                src_outputs = src_node.get("outputs") if isinstance(src_node.get("outputs"), dict) else {}
                contract_out = src_outputs.get(out_name)
                target = contract_out.get("target") if isinstance(contract_out, dict) else None
                target_type = str(target.get("type") or "").strip() if isinstance(target, dict) else ""
                identity_ok = (
                    isinstance(target, dict)
                    and target_type in {"store", "artifact"}
                    and bool(str((target.get("key") or "") if target_type == "store" else (target.get("artifact_id") or "")).strip())
                )
                if not identity_ok:
                    findings.append({
                        "severity": "warning",
                        "code": "REVIEW_BINDING_UNRESOLVED",
                        "path": f"root_flow.states.{node_id}.inputs.{port}.binding",
                        "message": (
                            f"Review node '{node_id}' input '{port}' binds '{src_id}:{out_name}' but "
                            f"'{src_id}.outputs.{out_name}' declares no valid target contract (store "
                            "needs key, artifact needs artifact_id; the runtime treats a missing or "
                            "malformed target as undeclared). The review screen shows nothing."
                        ),
                    })
                elif target_type == "artifact":
                    findings.append({
                        "severity": "info",
                        "code": "REVIEW_BINDING_ARTIFACT",
                        "path": f"root_flow.states.{node_id}.inputs.{port}.binding",
                        "message": (
                            f"Review node '{node_id}' input '{port}' binds an artifact output "
                            f"('{out_name}') — the review screen would show a descriptor, not the "
                            "content. Prefer binding a store text key written upstream."
                        ),
                    })
            elif source == "artifact":
                findings.append({
                    "severity": "info",
                    "code": "REVIEW_BINDING_ARTIFACT",
                    "path": f"root_flow.states.{node_id}.inputs.{port}.binding",
                    "message": (
                        f"Review node '{node_id}' input '{port}' binds an artifact resource — the "
                        "review screen would show a descriptor, not the content. Prefer binding a "
                        "store text key written upstream."
                    ),
                })
            elif source == "store" and not (binding.get("key") or binding.get("store_key")):
                findings.append({
                    "severity": "warning",
                    "code": "REVIEW_BINDING_UNRESOLVED",
                    "path": f"root_flow.states.{node_id}.inputs.{port}.binding",
                    "message": f"Review node '{node_id}' input '{port}' store binding has no key.",
                })
    return findings


def llm_budget_findings(root_flow: dict[str, Any]) -> list[dict[str, str]]:
    """Flag llm_prompt nodes whose max_tokens is too small for reasoning models.

    Lesson A1: a reasoning model burns most of the budget on reasoning; 8000
    still failed intermittently with PROVIDER_EMPTY_RESPONSE
    (finish_reason=length). Prefer 20000.
    """
    findings: list[dict[str, str]] = []
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    for node_id, state in states.items():
        if not isinstance(state, dict) or str(state.get("action") or "") != "llm_prompt":
            continue
        params = state.get("params") if isinstance(state.get("params"), dict) else {}
        llm = state.get("llm_options") if isinstance(state.get("llm_options"), dict) else (
            params.get("llm_options") if isinstance(params.get("llm_options"), dict) else None
        )
        if not llm:
            continue
        budget = llm.get("max_tokens")
        if isinstance(budget, int) and 0 < budget < 20000:
            findings.append({
                "severity": "warning",
                "code": "LLM_BUDGET_LOW",
                "path": f"root_flow.states.{node_id}.llm_options.max_tokens",
                "message": (
                    f"LLM node '{node_id}' caps max_tokens at {budget}. Reasoning models burn most "
                    "of the budget on reasoning; budgets below 20000 fail intermittently with "
                    "PROVIDER_EMPTY_RESPONSE (finish_reason=length). Prefer 20000."
                ),
            })
    return findings


def failure_terminal_findings(root_flow: dict[str, Any]) -> list[dict[str, str]]:
    """Suggest sharing one failure terminal instead of one per step.

    Lesson C1: precise failure detail lives in run.error (envelope with
    node_id/code/message), so per-step failure terminals are redundant.
    """
    findings: list[dict[str, str]] = []
    plan = root_flow.get("execution_plan") if isinstance(root_flow.get("execution_plan"), dict) else None
    if not plan:
        return findings
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    targets: set[str] = set()
    for edge in plan.get("edges") or []:
        if isinstance(edge, dict) and edge.get("kind") == "failure":
            targets.add(str(edge.get("to") or ""))
    failure_terminals = [
        t for t in targets
        if isinstance(states.get(t), dict) and str(states[t].get("type") or "") == "terminal" and t != "complete"
    ]
    if len(failure_terminals) > 1:
        findings.append({
            "severity": "info",
            "code": "FAILURE_TERMINALS_MULTIPLE",
            "path": "root_flow.execution_plan.edges",
            "message": (
                f"{len(failure_terminals)} failure terminals: {', '.join(sorted(failure_terminals))}. "
                "Share one generic failure terminal — run.error already carries the precise "
                "node/code/message."
            ),
        })
    return findings


def interaction_prompt_findings(root_flow: dict[str, Any]) -> list[dict[str, str]]:
    """Flag confirm_checkpoint nodes whose review prompt is the empty default.

    Lesson C3: without an interaction prompt the user sees only '请确认是否继续执行。'
    — write what they are actually approving.
    """
    findings: list[dict[str, str]] = []
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    for node_id, state in states.items():
        if not isinstance(state, dict) or str(state.get("action") or "") != "confirm_checkpoint":
            continue
        params = state.get("params") if isinstance(state.get("params"), dict) else {}
        preset = params.get("preset_config") if isinstance(params.get("preset_config"), dict) else {}
        interaction = params.get("interaction") if isinstance(params.get("interaction"), dict) else (
            preset.get("interaction") if isinstance(preset.get("interaction"), dict) else None
        )
        prompt = ""
        if interaction:
            prompt = str(interaction.get("prompt") or "").strip()
        if not prompt:
            prompt = str(params.get("condition") or "").strip()
        if not prompt:
            prompt = str(preset.get("message") or "").strip()
        if not prompt:
            findings.append({
                "severity": "warning",
                "code": "INTERACTION_PROMPT_MISSING",
                "path": f"root_flow.states.{node_id}.params.interaction.prompt",
                "message": (
                    f"Review node '{node_id}' has no interaction prompt — the user sees only the "
                    "default '请确认是否继续执行。'. Write what they are approving (e.g. the "
                    "deliverable name and what happens after approval)."
                ),
            })
    return findings


def node_semantic_findings(root_flow: dict[str, Any]) -> list[dict[str, str]]:
    """Reject common runtime-valid but CF-FARP@1.1-invalid node disguises."""
    findings: list[dict[str, str]] = []
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    for node_id, state in states.items():
        if not isinstance(state, dict) or state.get("type") != "process":
            continue
        kind = str(state.get("kind") or "").strip()
        executor = str(state.get("executor") or "").strip()
        effect = str(state.get("effect") or "").strip()
        path = f"root_flow.states.{node_id}"

        if executor == "llm":
            if kind != "decision":
                findings.append({
                    "code": "LLM_KIND_MISMATCH",
                    "path": f"{path}.kind",
                    "message": "A real LLM call must be kind='decision'; do not hide it behind transform or validation.",
                })
            if effect != "none":
                findings.append({
                    "code": "LLM_EFFECT_MISMATCH",
                    "path": f"{path}.effect",
                    "message": "An AI decision must declare effect='none'.",
                })
            if state.get("output_contract") != "decision_envelope.v1":
                findings.append({
                    "code": "LLM_OUTPUT_CONTRACT_MISSING",
                    "path": f"{path}.output_contract",
                    "message": "An AI decision must emit decision_envelope.v1.",
                })
            contract = state.get("decision_contract") if isinstance(state.get("decision_contract"), dict) else {}
            consume = contract.get("consume") if isinstance(contract.get("consume"), dict) else {}
            consume_path = str(consume.get("path") or "")
            if contract.get("schema") != "decision_envelope.v1" or not consume:
                findings.append({
                    "code": "LLM_DECISION_CONSUME_MISSING",
                    "path": f"{path}.decision_contract",
                    "message": "An AI decision needs a decision_envelope.v1 contract and explicit consume projection.",
                })
            elif not (consume_path == "payload" or consume_path.startswith("payload.")):
                findings.append({
                    "code": "LLM_DECISION_CONSUME_PATH_INVALID",
                    "path": f"{path}.decision_contract.consume.path",
                    "message": "Decision consume.path must be 'payload' or start with 'payload.'.",
                })

        required_effect = {"input": "writes_store", "transfer": "writes_store"}.get(kind)
        if required_effect and effect != required_effect:
            findings.append({
                "code": "NODE_EFFECT_MISMATCH",
                "path": f"{path}.effect",
                "message": f"A {kind} node must declare effect='{required_effect}'.",
            })
    return findings


def delivery_contract_findings(manifest: dict[str, Any], root_flow: dict[str, Any]) -> list[dict[str, str]]:
    delivery = manifest.get("delivery") if isinstance(manifest.get("delivery"), dict) else {}
    primary_output = str(delivery.get("primary_output") or "").strip()
    if not primary_output:
        return []

    produced_identities: set[str] = set()
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    for state in states.values():
        if not isinstance(state, dict):
            continue
        params = state.get("params") if isinstance(state.get("params"), dict) else {}
        artifact_id = str(params.get("artifact_id") or "").strip()
        if artifact_id:
            produced_identities.add(artifact_id)
        outputs = state.get("outputs") if isinstance(state.get("outputs"), dict) else {}
        for output in outputs.values():
            if not isinstance(output, dict):
                continue
            target = output.get("target") if isinstance(output.get("target"), dict) else {}
            identity = target.get("artifact_id") if target.get("type") == "artifact" else target.get("key")
            if identity:
                produced_identities.add(str(identity))

    if primary_output in produced_identities:
        return []
    return [{
        "code": "DELIVERY_PRIMARY_OUTPUT_UNDECLARED",
        "path": "manifest.delivery.primary_output",
        "message": f"Primary output '{primary_output}' has no declared Store or Artifact producer.",
    }]


def declared_output_types(root_flow: dict[str, Any]) -> dict[str, str]:
    identities: dict[str, str] = {}
    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    for state in states.values():
        if not isinstance(state, dict):
            continue
        params = state.get("params") if isinstance(state.get("params"), dict) else {}
        artifact_id = str(params.get("artifact_id") or "").strip()
        if artifact_id:
            identities[artifact_id] = "artifact"
        outputs = state.get("outputs") if isinstance(state.get("outputs"), dict) else {}
        for output in outputs.values():
            if not isinstance(output, dict):
                continue
            target = output.get("target") if isinstance(output.get("target"), dict) else {}
            target_type = str(target.get("type") or "").strip()
            identity = target.get("artifact_id") if target_type == "artifact" else target.get("key")
            if identity and target_type in {"store", "artifact"}:
                identities[str(identity)] = target_type
    return identities


def runtime_delivery_findings(
    repo: Path,
    run_id: str,
    *,
    api_url: str | None = None,
    expected_primary_output: str | None = None,
    expected_primary_type: str | None = None,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    normalized_id = str(run_id or "").strip()
    if not normalized_id or Path(normalized_id).name != normalized_id:
        return [{"code": "RUN_ID_INVALID", "path": "run_id", "message": "Run id is invalid."}]
    runs_root = (repo / ".data" / "runtime" / "runs").resolve()
    run_dir = (runs_root / normalized_id).resolve()
    if run_dir.parent != runs_root:
        return [{"code": "RUN_PATH_INVALID", "path": "run_id", "message": "Run directory escapes the runtime root."}]
    run_path = run_dir / "run.json"
    delivery_path = run_dir / "delivery.json"
    if not run_path.is_file():
        return [{"code": "RUN_NOT_FOUND", "path": str(run_path), "message": "Run snapshot does not exist."}]

    run = load_json(run_path)
    if run.get("status") != "completed":
        findings.append({"code": "RUN_NOT_COMPLETED", "path": "run.status", "message": f"Run status is '{run.get('status')}'."})
    if run.get("errors"):
        findings.append({"code": "RUN_HAS_ERRORS", "path": "run.errors", "message": "Completed run still contains runtime errors."})
    data_chain = run.get("data_chain") if isinstance(run.get("data_chain"), dict) else {}
    if data_chain and data_chain.get("passed") is not True:
        findings.append({"code": "RUN_DATA_CHAIN_FAILED", "path": "run.data_chain", "message": data_chain.get("summary") or "Run data chain did not pass."})
    if not delivery_path.is_file():
        findings.append({"code": "DELIVERY_SNAPSHOT_MISSING", "path": str(delivery_path), "message": "Delivery snapshot does not exist."})
        return findings

    delivery = load_json(delivery_path)
    if delivery.get("status") != "delivered":
        findings.append({"code": "DELIVERY_NOT_READY", "path": "delivery.status", "message": f"Delivery status is '{delivery.get('status')}'."})
    primary_output = str(delivery.get("primary_output") or "").strip()
    primary_artifact = delivery.get("primary_artifact") if isinstance(delivery.get("primary_artifact"), dict) else {}
    if not primary_output or (expected_primary_output and primary_output != expected_primary_output):
        findings.append({"code": "DELIVERY_PRIMARY_OUTPUT_MISSING", "path": "delivery.primary_output", "message": "Delivery primary_output is missing or differs from the Manifest contract."})
    if expected_primary_type == "artifact":
        if str(primary_artifact.get("artifact_id") or "") != primary_output:
            findings.append({"code": "DELIVERY_PRIMARY_ARTIFACT_MISSING", "path": "delivery.primary_artifact", "message": "Artifact-backed primary output has no matching primary_artifact."})
    elif primary_artifact:
        if str(primary_artifact.get("artifact_id") or "") != primary_output:
            findings.append({"code": "DELIVERY_PRIMARY_ARTIFACT_MISMATCH", "path": "delivery.primary_artifact", "message": "Delivery primary_artifact does not match primary_output."})
    elif delivery.get("result") in (None, "", [], {}):
        findings.append({"code": "DELIVERY_PRIMARY_RESULT_MISSING", "path": "delivery.result", "message": "Store-backed primary output has no non-empty delivery result."})

    artifacts = [item for item in delivery.get("artifacts") or [] if isinstance(item, dict)]
    artifacts_root = (run_dir / "artifacts").resolve()
    for index, artifact in enumerate(artifacts):
        raw_path = str(artifact.get("path") or artifact.get("name") or "").strip()
        artifact_path = Path(raw_path)
        if not artifact_path.is_absolute():
            artifact_path = artifacts_root / artifact_path.name
        artifact_path = artifact_path.resolve()
        item_path = f"delivery.artifacts[{index}]"
        if artifact_path.parent != artifacts_root:
            findings.append({"code": "ARTIFACT_PATH_INVALID", "path": item_path, "message": "Artifact path escapes the run artifact directory."})
            continue
        if not artifact_path.is_file() or artifact_path.stat().st_size <= 0:
            findings.append({"code": "ARTIFACT_EMPTY", "path": item_path, "message": f"Artifact '{artifact.get('name')}' is missing or empty."})
            continue
        if api_url:
            artifact_url = str(artifact.get("url") or "").strip()
            if not artifact_url:
                findings.append({"code": "ARTIFACT_URL_MISSING", "path": item_path, "message": "Artifact has no browser-accessible URL."})
            else:
                resolved_url = urllib.parse.urljoin(api_url.rstrip("/") + "/", artifact_url)
                try:
                    request = urllib.request.Request(resolved_url, headers={"Range": "bytes=0-0"})
                    with urllib.request.urlopen(request, timeout=10) as response:
                        if not response.read(1):
                            findings.append({"code": "ARTIFACT_URL_EMPTY", "path": item_path, "message": f"Artifact URL returned no bytes: {artifact_url}"})
                except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                    findings.append({"code": "ARTIFACT_URL_UNREACHABLE", "path": item_path, "message": f"Artifact URL is not readable: {exc}"})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an authored CartridgeFlow package before handoff.")
    parser.add_argument("--repo", default=".", help="CartridgeFlow repository root")
    parser.add_argument("--package", required=True, help="Cartridge package directory")
    parser.add_argument("--run-id", help="Also verify one completed local run and its delivery artifacts")
    parser.add_argument("--api-url", help="Also require each delivered Artifact URL to return real bytes")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    package = Path(args.package).resolve()
    if not (repo / "src" / "core").is_dir():
        parser.error(f"--repo is not a CartridgeFlow checkout: {repo}")
    if not package.is_dir():
        parser.error(f"--package does not exist: {package}")
    sys.path.insert(0, str(repo / "src"))

    from core.cartridge.validator import ManifestValidationError, ManifestValidator
    from core.lab.flow_analyzer import analyze_flow
    from core.lab.node_executor import SUPPORTED_ACTIONS
    from core.llm.config_manager import build_model_binding_report
    from core.protocol import load_base_implementation
    from core.studio.resource_catalog import build_flow_resource_catalog

    manifest = load_json(package / "manifest.json")
    root_entry = str((manifest.get("root_flow") or {}).get("entry") or "root.flow.json")
    root_flow = load_json(package / root_entry)
    findings: list[dict[str, Any]] = []
    try:
        ManifestValidator().validate_package(package, manifest)
    except ManifestValidationError as exc:
        findings.append({"code": "MANIFEST_INVALID", "path": "manifest.json", "message": str(exc)})

    analysis = analyze_flow(root_flow, manifest, target="dev", base=load_base_implementation(repo))
    for item in analysis.get("findings") or []:
        if isinstance(item, dict) and item.get("severity") == "blocker":
            findings.append({"code": "FLOW_ANALYSIS_BLOCKER", "path": item.get("path", "root.flow.json"), "message": item.get("message", "Flow analysis blocker")})

    states = root_flow.get("states") if isinstance(root_flow.get("states"), dict) else {}
    for node_id, state in states.items():
        if not isinstance(state, dict) or state.get("type") != "process":
            continue
        action = str(state.get("action") or "").strip()
        if action and action not in SUPPORTED_ACTIONS:
            findings.append({
                "code": "ACTION_EXECUTOR_MISSING",
                "path": f"root_flow.states.{node_id}.action",
                "message": f"No runtime executor is registered for action '{action}'.",
            })

    catalog = build_flow_resource_catalog(repo, manifest, root_flow, package_path=package)
    for item in catalog.get("findings") or []:
        if isinstance(item, dict) and item.get("severity") == "blocker":
            findings.append({"code": "RESOURCE_CATALOG_BLOCKER", "path": item.get("path", "resources"), "message": item.get("message", "Resource catalog blocker")})

    model_report = build_model_binding_report(manifest, root_flow)
    for item in model_report.get("items") or []:
        if isinstance(item, dict) and item.get("status") == "blocked":
            findings.append({"code": "MODEL_BINDING_BLOCKED", "path": f"llm_recipe.roles.{item.get('id', '')}", "message": item.get("message", "Model binding blocked")})

    findings.extend(required_text_findings(manifest, root_flow))
    findings.extend(text_findings(package))
    findings.extend(execution_plan_findings(root_flow))
    findings.extend(description_findings(root_flow))
    findings.extend(flow_start_entry_findings(root_flow))
    findings.extend(llm_retry_policy_findings(root_flow))
    findings.extend(review_binding_findings(root_flow))
    findings.extend(review_route_findings(root_flow))
    findings.extend(llm_budget_findings(root_flow))
    findings.extend(failure_terminal_findings(root_flow))
    findings.extend(interaction_prompt_findings(root_flow))
    findings.extend(node_semantic_findings(root_flow))
    findings.extend(delivery_contract_findings(manifest, root_flow))
    if args.run_id:
        manifest_delivery = manifest.get("delivery") if isinstance(manifest.get("delivery"), dict) else {}
        expected_primary_output = str(manifest_delivery.get("primary_output") or "").strip()
        output_types = declared_output_types(root_flow)
        findings.extend(runtime_delivery_findings(
            repo,
            args.run_id,
            api_url=args.api_url,
            expected_primary_output=expected_primary_output or None,
            expected_primary_type=output_types.get(expected_primary_output),
        ))
    blockers = [item for item in findings if item.get("severity") != "warning"]
    warnings = [item for item in findings if item.get("severity") == "warning"]
    result = {
        "cartridge_id": manifest.get("id"),
        "package": str(package),
        "ok": not blockers,
        "finding_count": len(findings),
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "findings": findings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())

