"""Run authoritative governance checkers and record revision-bound evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .build_governance_index import DEFAULT_INDEX, DEFAULT_TARGETS, verify_index_freshness
    from .compile_context import compile_context
    from .governance_db import DEFAULT_DATABASE, canonical_json, verify_database
    from .governance_ledger import DEFAULT_LEDGER, digest_payload, initialize_ledger
except ImportError:  # Direct execution: python scripts/run_governance_checks.py
    from build_governance_index import DEFAULT_INDEX, DEFAULT_TARGETS, verify_index_freshness
    from compile_context import compile_context
    from governance_db import DEFAULT_DATABASE, canonical_json, verify_database
    from governance_ledger import DEFAULT_LEDGER, digest_payload, initialize_ledger


ROOT = Path(__file__).resolve().parents[1]
MAX_CAPTURE_CHARS = 20000


class CheckOrchestrationError(RuntimeError):
    """Raised when authoritative checks cannot be selected or recorded."""


def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {str(key): str(value) for key, value in connection.execute("SELECT key, value FROM registry_metadata")}


def _clip(value: str | bytes | None) -> str:
    if value is None:
        value = ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if len(value) <= MAX_CAPTURE_CHARS:
        return value
    return value[:MAX_CAPTURE_CHARS] + "\n...[output truncated by governance orchestrator]"


def _checker_command(
    checker: dict[str, Any],
    source_path: Path,
    targets_path: Path,
    index_path: Path,
) -> list[str]:
    entrypoint = (ROOT / str(checker["entrypoint"])).resolve()
    try:
        entrypoint.relative_to(ROOT)
    except ValueError as exc:
        raise CheckOrchestrationError(f"checker entrypoint escapes governance repository: {checker['checker_id']}") from exc
    if not entrypoint.is_file():
        raise CheckOrchestrationError(f"checker entrypoint does not exist: {checker['checker_id']}:{entrypoint}")
    kind = str(checker["checker_kind"])
    if kind == "python":
        command = [sys.executable, str(entrypoint)]
        checker_id = str(checker["checker_id"])
        if checker_id == "check.governance.source":
            return command + ["--database", str(source_path), "verify"]
        if checker_id == "check.governance.detachability":
            return command + ["--config", str(targets_path)]
        if checker_id == "check.governance.index":
            return command + [
                "--source", str(source_path), "--targets", str(targets_path),
                "--index", str(index_path), "check",
            ]
        return command
    if kind == "powershell":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(entrypoint)]
    if kind == "executable":
        return [str(entrypoint)]
    raise CheckOrchestrationError(f"unsupported checker kind: {checker['checker_id']}:{kind}")


def _load_checkers(
    source_path: Path,
    context: dict[str, Any] | None,
    requested_checker_ids: set[str],
) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        checkers = {
            str(row["checker_id"]): dict(row)
            for row in connection.execute("SELECT * FROM checker WHERE enabled = 1 ORDER BY checker_id")
        }
        unknown = sorted(requested_checker_ids - set(checkers))
        if unknown:
            raise CheckOrchestrationError("unknown or disabled checker: " + ", ".join(unknown))
        bindings: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute(
            "SELECT binding.checker_id, binding.binding_mode, rule.*, card.status AS card_status, "
            "card.card_type AS card_type "
            "FROM rule_check_binding AS binding "
            "JOIN rule ON rule.rule_id = binding.rule_id "
            "JOIN card ON card.card_id = rule.card_id "
            "ORDER BY binding.checker_id, rule.rule_id"
        ):
            bindings.setdefault(str(row["checker_id"]), []).append(dict(row))

        selected_card_ids = (
            {str(card["card_id"]) for card in context["cards"]}
            if context is not None
            else None
        )
        if requested_checker_ids:
            selected_ids = set(requested_checker_ids)
        elif context is None:
            selected_ids = set(checkers)
        elif context["routing"]["state"] == "conservative":
            selected_ids = set(checkers)
        else:
            selected_ids = {
                checker_id
                for checker_id, rules in bindings.items()
                if any(
                    str(rule["card_id"]) in selected_card_ids
                    and str(rule["card_type"]) != "constitution"
                    for rule in rules
                )
            }
            selected_ids.update(
                {
                    "check.governance.index",
                    "check.governance.detachability",
                }
                & set(checkers)
            )
            selected_ids.update(
                str(binding["checker_id"])
                for scenario in context.get("scenarios", [])
                for binding in scenario.get("checker_bindings", [])
                if int(binding["required"]) == 1
            )
            if any(str(item["target_id"]) == "cartridgeflow" for item in context.get("contracts", [])):
                selected_ids.add("check.product.formal")
        if "check.governance.source" in checkers:
            selected_ids.add("check.governance.source")
        selected: list[dict[str, Any]] = []
        for checker_id in sorted(selected_ids, key=lambda item: (item != "check.governance.source", item)):
            checker = {**checkers[checker_id], "rules": bindings.get(checker_id, [])}
            if context is None:
                checker["selection_reason"] = "all-enabled-checkers"
            elif context["routing"]["state"] == "conservative":
                checker["selection_reason"] = "conservative-target-fallback"
            else:
                matching_cards = sorted(
                    {str(rule["card_id"]) for rule in checker["rules"] if str(rule["card_id"]) in selected_card_ids}
                )
                checker["selection_reason"] = (
                    "required-by-selected-cards:" + ",".join(matching_cards)
                    if matching_cards else "source-precondition"
                )
            selected.append(checker)
        return selected
    finally:
        connection.close()


def _run_checker(
    checker: dict[str, Any],
    source_path: Path,
    targets_path: Path,
    index_path: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    command = _checker_command(checker, source_path, targets_path, index_path)
    started = datetime.now(timezone.utc)
    monotonic_start = time.monotonic()
    status = "error"
    exit_code: int | None = None
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        status = "passed" if completed.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        stdout = _clip(exc.stdout)
        stderr = f"checker timed out after {timeout_seconds} seconds"
    except OSError as exc:
        stderr = f"cannot execute checker: {exc}"
    finished = datetime.now(timezone.utc)
    checker_id = str(checker["checker_id"])
    declared_stage = str(checker["checker_stage"])
    acceptance_stage = (
        "static"
        if declared_stage == "source" or checker_id in {"check.governance.index", "check.governance.detachability"}
        else declared_stage
    )
    return {
        "run_id": str(uuid.uuid4()),
        "checker_id": str(checker["checker_id"]),
        "status": status,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_ms": max(0, round((time.monotonic() - monotonic_start) * 1000)),
        "exit_code": exit_code,
        "command": command,
        "stdout": _clip(stdout),
        "stderr": _clip(stderr),
        "selection_reason": str(checker["selection_reason"]),
        "checker_digest": hashlib.sha256((ROOT / str(checker["entrypoint"])).read_bytes()).hexdigest(),
        "checker_stage": str(checker["checker_stage"]),
        "acceptance_stage": acceptance_stage,
        "output_contract": str(checker["output_contract"]),
        "rules": checker["rules"],
    }


def _assign_rule_results(
    result: dict[str, Any],
    index_path: Path,
    targets_path: Path,
) -> None:
    rules = result["rules"]
    if result["status"] == "passed":
        result["rule_results"] = {str(rule["rule_id"]): "passed" for rule in rules}
        return
    if result["status"] == "error":
        result["rule_results"] = {str(rule["rule_id"]): "error" for rule in rules}
        return

    failed_rule_ids: set[str] = set()
    checker_id = str(result["checker_id"])
    if checker_id == "check.governance.index":
        connection = sqlite3.connect(f"{index_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            failed_rule_ids = {
                str(rule_id)
                for (rule_id,) in connection.execute(
                    "SELECT DISTINCT rule_id FROM finding WHERE status = 'open' ORDER BY rule_id"
                )
            }
        finally:
            connection.close()
    elif checker_id == "check.governance.detachability":
        failed_rule_ids.add("constitution.external-only")
        diagnostic_lines = [
            line.lower()
            for line in (str(result["stdout"]) + "\n" + str(result["stderr"])).splitlines()
            if "runtime references governance marker" in line.lower()
            or "runtime root does not exist" in line.lower()
        ]
        config = json.loads(targets_path.read_text(encoding="utf-8"))
        target_paths = {
            str(target["id"]): str((ROOT / str(target["path"])).resolve()).lower()
            for target in config.get("targets", [])
        }
        if any(target_paths.get("desktop-runner", "") in line for line in diagnostic_lines):
            failed_rule_ids.add("dr.no-governance-runtime")
        if any(target_paths.get("cartridgeflow", "") in line for line in diagnostic_lines):
            failed_rule_ids.add("workbench.no-governance-runtime")
    if not failed_rule_ids:
        failed_rule_ids = {str(rule["rule_id"]) for rule in rules}
    result["rule_results"] = {
        str(rule["rule_id"]): "failed" if str(rule["rule_id"]) in failed_rule_ids else "passed"
        for rule in rules
    }


def _structured_diagnostics(result: dict[str, Any], index_path: Path) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    if result["status"] == "passed":
        return diagnostics
    findings_by_rule: dict[str, list[dict[str, Any]]] = {}
    if str(result["checker_id"]) == "check.governance.index":
        connection = sqlite3.connect(f"{index_path.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            for row in connection.execute(
                "SELECT finding.*, artifact.artifact_path FROM finding "
                "LEFT JOIN observed_artifact AS artifact ON artifact.artifact_id = finding.artifact_id "
                "WHERE finding.status = 'open' ORDER BY finding.rule_id, finding.finding_id"
            ):
                findings_by_rule.setdefault(str(row["rule_id"]), []).append(dict(row))
        finally:
            connection.close()
    output = (str(result["stdout"]) + "\n" + str(result["stderr"])).strip()
    first_line = next((line.strip() for line in output.splitlines() if line.strip()), "checker returned nonzero")
    for rule in result["rules"]:
        rule_id = str(rule["rule_id"])
        if result["rule_results"].get(rule_id) == "passed":
            continue
        findings = findings_by_rule.get(rule_id, [])
        if findings:
            for finding in findings:
                try:
                    details = json.loads(str(finding["details_json"]))
                except json.JSONDecodeError:
                    details = {}
                diagnostics.append(
                    {
                        "rule_id": rule_id,
                        "card_id": str(finding["card_id"]),
                        "artifact_id": finding["artifact_id"],
                        "artifact_path": finding["artifact_path"],
                        "reason": str(details.get("reason") or finding["message"]),
                        "expected": str(details.get("expected") or rule["statement"]),
                        "actual": str(details.get("actual") or finding["message"]),
                        "boundary_card_id": details.get("boundary_card_id"),
                        "details": details,
                    }
                )
            continue
        diagnostics.append(
            {
                "rule_id": rule_id,
                "card_id": str(rule["card_id"]),
                "artifact_id": None,
                "artifact_path": None,
                "reason": str(rule["failure_message"]),
                "expected": str(rule["statement"]),
                "actual": f"{first_line} (exit={result['exit_code']})",
                "boundary_card_id": str(rule["card_id"]) if rule.get("card_type") == "boundary" else None,
                "details": {"checker_id": result["checker_id"], "checker_stage": result["checker_stage"]},
            }
        )
    return diagnostics


def _row_digest(row: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(row).encode("utf-8")).hexdigest()


def _evidence_dependencies(
    source_path: Path,
    index_path: Path,
    targets_path: Path,
    context: dict[str, Any] | None,
    results: list[dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    source = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    index = sqlite3.connect(f"{index_path.resolve().as_uri()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    index.row_factory = sqlite3.Row
    router_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    compiler_path = ROOT / "scripts" / "compile_context.py"
    compiler_digest = hashlib.sha256(compiler_path.read_bytes()).hexdigest()
    target_digest = hashlib.sha256(targets_path.read_bytes()).hexdigest()
    selected_context_ids = {
        str(card["card_id"]) for card in context.get("cards", [])
    } if context else set()
    selected_scenario_ids = {
        str(scenario["scenario_id"]) for scenario in context.get("scenarios", [])
    } if context else set()
    dependencies_by_checker: dict[str, list[dict[str, str]]] = {}
    try:
        for result in results:
            checker_id = str(result["checker_id"])
            card_ids = {str(rule["card_id"]) for rule in result["rules"]}
            if checker_id == "check.governance.source":
                card_ids = {
                    str(row[0]) for row in source.execute("SELECT card_id FROM card WHERE status = 'active'")
                }
            elif context is not None:
                card_ids &= selected_context_ids

            relation_rows = [
                dict(row)
                for row in source.execute(
                    "SELECT * FROM card_relation ORDER BY relation_id"
                )
                if str(row["source_card_id"]) in card_ids or str(row["target_card_id"]) in card_ids
            ]
            if checker_id in {"check.boundary.contracts", "check.scenario.handoff"}:
                card_ids.update(
                    str(row[key])
                    for row in relation_rows
                    for key in ("source_card_id", "target_card_id")
                )
            if checker_id == "check.governance.index":
                card_ids = {
                    str(row[0])
                    for row in source.execute(
                        "SELECT card_id FROM card WHERE status = 'active' AND card_type <> 'knowledge'"
                    )
                }

            items: list[dict[str, str]] = []

            def add(kind: str, subject_id: str, digest: str, reason: str, role: str = "exact") -> None:
                items.append(
                    {
                        "dependency_kind": kind,
                        "subject_id": subject_id,
                        "observed_digest": digest,
                        "freshness_role": role,
                        "selection_reason": reason,
                    }
                )

            for row in source.execute("SELECT * FROM card ORDER BY card_id"):
                if str(row["card_id"]) in card_ids:
                    add("card", str(row["card_id"]), str(row["content_digest"]), "checker-card-closure")
            scope_rows = [
                dict(row)
                for row in source.execute("SELECT * FROM card_scope ORDER BY scope_id")
                if str(row["card_id"]) in card_ids
            ]
            for row in scope_rows:
                add("scope", str(row["scope_id"]), _row_digest(row), "scope-used-by-card-closure")
            for row in relation_rows:
                if str(row["source_card_id"]) in card_ids or str(row["target_card_id"]) in card_ids:
                    add("relation", str(row["relation_id"]), _row_digest(row), "relation-used-by-card-closure")

            contract_rows = [
                dict(row)
                for row in source.execute("SELECT * FROM card_contract_binding ORDER BY binding_id")
                if str(row["card_id"]) in card_ids
            ]
            for row in contract_rows:
                add("contract-binding", str(row["binding_id"]), _row_digest(row), "contract-bound-card")
                observed = index.execute(
                    "SELECT * FROM observed_contract WHERE target_id = ? AND contract_id = ? AND version = ?",
                    (row["target_id"], row["contract_id"], row["version_constraint"]),
                ).fetchone()
                if observed is not None:
                    add("contract", str(observed["contract_key"]), str(observed["content_digest"]), "matched-contract")

            scenario_rows = [
                dict(row)
                for row in source.execute(
                    "SELECT binding.scenario_id, binding.checker_id, binding.required "
                    "FROM scenario_checker_binding AS binding ORDER BY binding.scenario_id, binding.checker_id"
                )
                if str(row["checker_id"]) == checker_id
                and (not selected_scenario_ids or str(row["scenario_id"]) in selected_scenario_ids)
            ]
            for row in scenario_rows:
                add(
                    "scenario-binding",
                    f"{row['scenario_id']}:{checker_id}",
                    _row_digest(row),
                    "scenario-check-plan",
                )

            target_ids = {str(row["target_id"]) for row in scope_rows}
            if checker_id in {"check.product.formal", "check.product.floors"}:
                target_ids = {"cartridgeflow"}
            elif checker_id == "check.dr.floor":
                target_ids = {"desktop-runner"}
            elif checker_id in {"check.governance.index", "check.governance.detachability", "check.scenario.removability"}:
                target_ids = {str(row[0]) for row in index.execute("SELECT target_id FROM target_revision")}
            if checker_id not in {"check.governance.source", "check.boundary.contracts"}:
                for row in index.execute("SELECT * FROM observed_artifact ORDER BY artifact_id"):
                    if str(row["target_id"]) in target_ids:
                        add("artifact", str(row["artifact_id"]), str(row["content_digest"]), "checker-target-artifact")

            checker_row = dict(source.execute("SELECT * FROM checker WHERE checker_id = ?", (checker_id,)).fetchone())
            checker_bindings = [
                dict(row)
                for row in source.execute(
                    "SELECT * FROM rule_check_binding WHERE checker_id = ? ORDER BY rule_id", (checker_id,)
                )
            ]
            add("checker", checker_id, str(result["checker_digest"]), "executed-checker")
            add(
                "checker-config",
                checker_id,
                _row_digest({"checker": checker_row, "bindings": checker_bindings}),
                "checker-selection-config",
            )
            add("router", "run_governance_checks.py", router_digest, "routing-algorithm")
            add("context-compiler", "compile_context.py", compiler_digest, "context-selection-algorithm")
            add("target-config", str(targets_path.resolve()), target_digest, "target-configuration")
            if checker_id == "check.governance.source":
                source_digest = source.execute(
                    "SELECT value FROM registry_metadata WHERE key = 'publication_digest'"
                ).fetchone()
                if source_digest is not None:
                    add(
                        "source-global",
                        "governance-source.sqlite",
                        str(source_digest[0]),
                        "source-database-integrity",
                        "conservative",
                    )
            if checker_id == "check.governance.index":
                index_digest = index.execute(
                    "SELECT value FROM registry_metadata WHERE key = 'governance_facts_digest'"
                ).fetchone()
                if index_digest is not None:
                    add(
                        "index-global",
                        "governance-index.sqlite",
                        str(index_digest[0]),
                        "index-database-integrity",
                        "conservative",
                    )
            closure_payload = {
                "cards": sorted(card_ids),
                "contracts": sorted(row["binding_id"] for row in contract_rows),
                "relations": sorted(row["relation_id"] for row in relation_rows),
                "scenarios": sorted(row["scenario_id"] for row in scenario_rows),
                "scopes": sorted(row["scope_id"] for row in scope_rows),
            }
            add("selected-closure", checker_id, digest_payload(closure_payload), "checker-specific-closure")
            plan_payload = {
                "acceptance_stage": result["acceptance_stage"],
                "checker_id": checker_id,
                "rules": sorted(str(rule["rule_id"]) for rule in result["rules"]),
                "selection_reason": result["selection_reason"],
            }
            add("check-plan", checker_id, digest_payload(plan_payload), "checker-specific-plan")
            dependencies_by_checker[checker_id] = items
    finally:
        source.close()
        index.close()
    return dependencies_by_checker


def _acceptance_results(results: list[dict[str, Any]], *, complete_run: bool) -> list[dict[str, Any]]:
    acceptance: list[dict[str, Any]] = []
    by_stage = {
        stage: [result for result in results if result["acceptance_stage"] == stage]
        for stage in ("static", "floor", "boundary", "scenario")
    }
    for stage, stage_results in by_stage.items():
        statuses = {str(result["status"]) for result in stage_results}
        if not stage_results:
            status = "not-run"
        elif "failed" in statuses:
            status = "failed"
        elif "error" in statuses:
            status = "error"
        elif statuses == {"passed"}:
            status = "passed"
        else:
            status = "error"
        acceptance.append(
            {
                "kind": stage,
                "status": status,
                "required_checker_count": len(stage_results),
                "passed_checker_count": sum(result["status"] == "passed" for result in stage_results),
                "details": {
                    "checkers": [
                        {"checker_id": result["checker_id"], "status": result["status"]}
                        for result in stage_results
                    ]
                },
            }
        )
    stage_statuses = {item["kind"]: item["status"] for item in acceptance}
    if not complete_run:
        complete_status = "not-run"
    elif "failed" in stage_statuses.values():
        complete_status = "failed"
    elif "error" in stage_statuses.values() or "not-run" in stage_statuses.values():
        complete_status = "error"
    else:
        complete_status = "passed"
    acceptance.append(
        {
            "kind": "complete",
            "status": complete_status,
            "required_checker_count": len(results) if complete_run else 0,
            "passed_checker_count": sum(result["status"] == "passed" for result in results) if complete_run else 0,
            "details": {"complete_run": complete_run, "stage_statuses": stage_statuses},
        }
    )
    return acceptance


def _record_results(
    ledger_path: Path,
    *,
    route_run_id: str,
    route_started_at: str,
    route_finished_at: str,
    invocation: dict[str, Any],
    context: dict[str, Any] | None,
    source_digest: str,
    facts_digest: str,
    target_config_digest: str,
    results: list[dict[str, Any]],
    dependencies_by_checker: dict[str, list[dict[str, str]]],
    acceptance: list[dict[str, Any]],
) -> None:
    initialize_ledger(ledger_path)
    route_status = "failed" if any(result["status"] == "failed" for result in results) else (
        "error" if any(result["status"] != "passed" for result in results) else "passed"
    )
    routing_state = str(context["routing"]["state"]) if context else "all"
    fallback_reasons = list(context["routing"]["fallback_reasons"]) if context else []
    closure_payload = {
        "cards": sorted(str(card["card_id"]) for card in context.get("cards", [])) if context else "all",
        "contracts": sorted(str(item["contract_key"]) for item in context.get("contracts", [])) if context else "all",
        "relations": sorted(str(item["relation_id"]) for item in context.get("relations", [])) if context else "all",
        "scenarios": sorted(str(item["scenario_id"]) for item in context.get("scenarios", [])) if context else "all",
    }
    plan_payload = [
        {
            "acceptance_stage": result["acceptance_stage"],
            "checker_id": result["checker_id"],
            "rules": sorted(str(rule["rule_id"]) for rule in result["rules"]),
            "selection_reason": result["selection_reason"],
        }
        for result in results
    ]
    connection = sqlite3.connect(ledger_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute(
            "INSERT INTO route_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                route_run_id,
                route_started_at,
                route_finished_at,
                route_status,
                str(invocation.get("goal", "")),
                canonical_json(invocation),
                routing_state,
                canonical_json(fallback_reasons),
                source_digest,
                facts_digest,
                hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                target_config_digest,
                digest_payload(closure_payload),
                digest_payload(plan_payload),
                1,
            ),
        )
        for result in results:
            connection.execute(
                "INSERT INTO check_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result["run_id"], route_run_id, result["checker_id"], result["acceptance_stage"],
                    result["status"], result["started_at"], result["finished_at"], result["duration_ms"],
                    result["exit_code"], canonical_json(result["command"]), result["stdout"], result["stderr"],
                    result["selection_reason"], result["checker_digest"], result["output_contract"],
                ),
            )
            for rule in result["rules"]:
                payload = {
                    "binding_mode": rule["binding_mode"],
                    "card_id": rule["card_id"],
                    "failure_message": rule["failure_message"],
                    "rule_id": rule["rule_id"],
                    "severity": rule["severity"],
                    "status": result["rule_results"][str(rule["rule_id"])],
                }
                connection.execute(
                    "INSERT INTO rule_result VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        hashlib.sha256(f"{result['run_id']}:rule:{rule['rule_id']}".encode("utf-8")).hexdigest(),
                        result["run_id"], rule["rule_id"], rule["card_id"], rule["severity"],
                        payload["status"], canonical_json(payload), digest_payload(payload),
                    ),
                )
            for diagnostic_index, diagnostic in enumerate(result["diagnostics"]):
                connection.execute(
                    "INSERT INTO check_diagnostic VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        hashlib.sha256(
                            f"{result['run_id']}:diagnostic:{diagnostic_index}:{diagnostic['rule_id']}".encode("utf-8")
                        ).hexdigest(),
                        result["run_id"], diagnostic["rule_id"], diagnostic["card_id"],
                        diagnostic["artifact_id"], diagnostic["reason"], diagnostic["expected"],
                        diagnostic["actual"], diagnostic["boundary_card_id"], canonical_json(diagnostic["details"]),
                    ),
                )
            for dependency in dependencies_by_checker[result["checker_id"]]:
                connection.execute(
                    "INSERT INTO evidence_dependency VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        hashlib.sha256(
                            f"{result['run_id']}:{dependency['dependency_kind']}:{dependency['subject_id']}".encode("utf-8")
                        ).hexdigest(),
                        result["run_id"], dependency["dependency_kind"], dependency["subject_id"],
                        dependency["observed_digest"], dependency["freshness_role"], dependency["selection_reason"],
                    ),
                )
        for item in acceptance:
            details_digest = digest_payload(item["details"])
            connection.execute(
                "INSERT INTO acceptance_result VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hashlib.sha256(f"{route_run_id}:acceptance:{item['kind']}".encode("utf-8")).hexdigest(),
                    route_run_id, item["kind"], item["status"], item["required_checker_count"],
                    item["passed_checker_count"], canonical_json(item["details"]), details_digest,
                ),
            )
        connection.commit()
    finally:
        connection.close()


def run_checks(
    source_path: Path,
    targets_path: Path,
    index_path: Path,
    *,
    path_specs: list[str],
    changed: bool,
    contract_specs: list[str] | None = None,
    requested_checker_ids: set[str],
    timeout_seconds: int,
    ledger_path: Path = DEFAULT_LEDGER,
    goal: str = "",
    return_report: bool = False,
) -> list[dict[str, Any]] | dict[str, Any]:
    route_run_id = str(uuid.uuid4())
    route_started_at = datetime.now(timezone.utc).isoformat()
    source_errors = verify_database(source_path)
    if source_errors:
        raise CheckOrchestrationError("card source verification failed:\n- " + "\n- ".join(source_errors))
    freshness_errors = verify_index_freshness(source_path, targets_path, index_path)
    if freshness_errors:
        raise CheckOrchestrationError("governance index is not current:\n- " + "\n- ".join(freshness_errors))
    context: dict[str, Any] | None = None
    if path_specs or changed or contract_specs:
        context = compile_context(
            source_path,
            index_path,
            path_specs,
            targets_path=targets_path,
            changed=changed,
            goal=goal or "Select affected governance checks",
            contract_specs=contract_specs or [],
        )
    checkers = _load_checkers(source_path, context, requested_checker_ids)
    if not checkers:
        raise CheckOrchestrationError("no enabled checker applies to the selected cards")

    index = sqlite3.connect(f"{index_path.resolve().as_uri()}?mode=ro", uri=True)
    index.row_factory = sqlite3.Row
    source = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        index_metadata = _metadata(index)
        source_metadata = _metadata(source)
    finally:
        index.close()
        source.close()
    results = [
        _run_checker(checker, source_path, targets_path, index_path, timeout_seconds)
        for checker in checkers
    ]
    for result in results:
        _assign_rule_results(result, index_path, targets_path)
        result["diagnostics"] = _structured_diagnostics(result, index_path)
    post_errors = verify_index_freshness(source_path, targets_path, index_path)
    if post_errors:
        message = "evidence rejected because governed state changed during checks:\n- " + "\n- ".join(post_errors)
        for result in results:
            if result["status"] == "passed":
                result["status"] = "error"
                result["stderr"] = _clip((result["stderr"] + "\n" + message).strip())
                result["rule_results"] = {
                    str(rule["rule_id"]): "error" for rule in result["rules"]
                }
                result["diagnostics"] = _structured_diagnostics(result, index_path)
    complete_run = not path_specs and not changed and not contract_specs and not requested_checker_ids
    acceptance = _acceptance_results(results, complete_run=complete_run)
    dependencies_by_checker = _evidence_dependencies(
        source_path,
        index_path,
        targets_path,
        context,
        results,
    )
    route_finished_at = datetime.now(timezone.utc).isoformat()
    invocation = {
        "changed": changed,
        "contract_specs": sorted(contract_specs or []),
        "goal": goal,
        "path_specs": sorted(path_specs),
        "requested_checker_ids": sorted(requested_checker_ids),
        "timeout_seconds": timeout_seconds,
    }
    _record_results(
        ledger_path,
        route_run_id=route_run_id,
        route_started_at=route_started_at,
        route_finished_at=route_finished_at,
        invocation=invocation,
        context=context,
        source_digest=source_metadata["publication_digest"],
        facts_digest=index_metadata["governance_facts_digest"],
        target_config_digest=hashlib.sha256(targets_path.read_bytes()).hexdigest(),
        results=results,
        dependencies_by_checker=dependencies_by_checker,
        acceptance=acceptance,
    )
    report = {
        "route_run_id": route_run_id,
        "routing": context["routing"] if context else {"state": "all", "fallback_reasons": [], "fallback_target_ids": []},
        "acceptance": acceptance,
        "results": results,
    }
    return report if return_report else results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--path", action="append", default=[], dest="paths")
    parser.add_argument("--contract", action="append", default=[], dest="contracts")
    parser.add_argument("--changed", action="store_true")
    parser.add_argument("--goal", default="")
    parser.add_argument("--checker", action="append", default=[], dest="checkers")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        report = run_checks(
            args.source.resolve(),
            args.targets.resolve(),
            args.index.resolve(),
            path_specs=args.paths,
            changed=args.changed,
            contract_specs=args.contracts,
            requested_checker_ids=set(args.checkers),
            timeout_seconds=args.timeout,
            ledger_path=args.ledger.resolve(),
            goal=args.goal,
            return_report=True,
        )
        results = report["results"]
        failed = False
        for result in results:
            rules = ", ".join(str(rule["rule_id"]) for rule in result["rules"]) or "no-bound-rules"
            print(f"[{result['status']}] {result['checker_id']} ({rules}) in {result['duration_ms']} ms")
            if result["status"] != "passed":
                failed = True
                for rule in result["rules"]:
                    if result["rule_results"][str(rule["rule_id"])] == "passed":
                        continue
                    print(
                        f"- [{result['rule_results'][str(rule['rule_id'])]}/{rule['severity']}] "
                        f"{rule['rule_id']} -> {rule['card_id']}: "
                        f"{rule['failure_message']}"
                    )
                diagnostic = (result["stdout"] + "\n" + result["stderr"]).strip()
                if diagnostic:
                    print(diagnostic)
        print("Acceptance states:")
        for item in report["acceptance"]:
            print(
                f"- {item['kind']}: {item['status']} "
                f"({item['passed_checker_count']}/{item['required_checker_count']} checkers passed)"
            )
        print(f"Ledger route run: {report['route_run_id']}")
        return 1 if failed else 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error, CheckOrchestrationError) as exc:
        print(f"Governance check orchestration failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
