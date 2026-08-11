"""Compile a bounded AI context from exact paths, card relations, and findings."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

try:
    from .build_governance_index import DEFAULT_INDEX, DEFAULT_TARGETS, path_matches, verify_index_freshness
    from .governance_db import DEFAULT_DATABASE, canonical_json, verify_database
except ImportError:  # Direct execution: python scripts/compile_context.py
    from build_governance_index import DEFAULT_INDEX, DEFAULT_TARGETS, path_matches, verify_index_freshness
    from governance_db import DEFAULT_DATABASE, canonical_json, verify_database


CONTEXT_SCHEMA = "cartridgeflow.governance.context.v2"


class ContextCompilationError(RuntimeError):
    """Raised when deterministic context selection cannot be completed."""


def _read_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {str(key): str(value) for key, value in connection.execute("SELECT key, value FROM registry_metadata")}


def _placeholders(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


def _select_artifacts(
    connection: sqlite3.Connection,
    path_specs: list[str],
    changed: bool,
    *,
    allow_empty: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    artifacts: dict[str, dict[str, Any]] = {}
    reasons: dict[str, set[str]] = {}
    for spec in path_specs:
        target_id, separator, raw_path = spec.partition(":")
        artifact_path = raw_path.replace("\\", "/").strip("/")
        if not separator or not target_id or not artifact_path:
            raise ContextCompilationError(f"path must use target-id:relative/path form: {spec}")
        rows = connection.execute(
            "SELECT artifact.*, coverage.primary_card_ids_json, coverage.coverage_status "
            "FROM observed_artifact AS artifact "
            "JOIN scope_coverage AS coverage ON coverage.artifact_id = artifact.artifact_id "
            "WHERE artifact.target_id = ? AND "
            "(artifact.artifact_path = ? OR artifact.artifact_path LIKE ?) "
            "ORDER BY artifact.artifact_path",
            (target_id, artifact_path, artifact_path + "/%"),
        ).fetchall()
        if not rows:
            raise ContextCompilationError(f"path is not present in the governance index: {spec}")
        for row in rows:
            item = dict(row)
            artifacts[str(item["artifact_id"])] = item
            reasons.setdefault(str(item["artifact_id"]), set()).add(f"requested-path:{spec}")
    if changed:
        for row in connection.execute(
            "SELECT artifact.*, coverage.primary_card_ids_json, coverage.coverage_status "
            "FROM observed_artifact AS artifact "
            "JOIN scope_coverage AS coverage ON coverage.artifact_id = artifact.artifact_id "
            "WHERE artifact.worktree_state <> 'tracked' ORDER BY artifact.target_id, artifact.artifact_path"
        ):
            item = dict(row)
            artifacts[str(item["artifact_id"])] = item
            reasons.setdefault(str(item["artifact_id"]), set()).add("changed-worktree-artifact")
    if not artifacts and not allow_empty:
        raise ContextCompilationError("at least one --path or --changed artifact is required")
    return [artifacts[key] for key in sorted(artifacts)], reasons


def _expand_dependency_consumers(
    connection: sqlite3.Connection,
    artifacts: list[dict[str, Any]],
    reasons: dict[str, set[str]],
) -> list[dict[str, Any]]:
    selected = {str(item["artifact_id"]): item for item in artifacts}
    frontier = sorted(selected)
    while frontier:
        rows = connection.execute(
            "SELECT DISTINCT source.*, coverage.primary_card_ids_json, coverage.coverage_status, "
            "dependency.resolved_artifact_id "
            "FROM observed_dependency AS dependency "
            "JOIN observed_artifact AS source ON source.artifact_id = dependency.source_artifact_id "
            "JOIN scope_coverage AS coverage ON coverage.artifact_id = source.artifact_id "
            f"WHERE dependency.resolution_status = 'resolved' "
            f"AND dependency.resolved_artifact_id IN ({_placeholders(frontier)}) "
            "ORDER BY source.target_id, source.artifact_path",
            tuple(frontier),
        ).fetchall()
        next_frontier: list[str] = []
        for row in rows:
            item = dict(row)
            artifact_id = str(item["artifact_id"])
            dependency_target = str(item.pop("resolved_artifact_id"))
            reasons.setdefault(artifact_id, set()).add(f"dependency-consumer-of:{dependency_target}")
            if artifact_id in selected:
                continue
            selected[artifact_id] = item
            next_frontier.append(artifact_id)
        frontier = sorted(next_frontier)
    return [selected[key] for key in sorted(selected)]


def _relevant_findings(connection: sqlite3.Connection, artifact_ids: list[str]) -> list[dict[str, Any]]:
    if not artifact_ids:
        return []
    rows = connection.execute(
        "SELECT finding.*, artifact.target_id, artifact.artifact_path "
        "FROM finding JOIN observed_artifact AS artifact ON artifact.artifact_id = finding.artifact_id "
        f"WHERE finding.status = 'open' AND finding.artifact_id IN ({_placeholders(artifact_ids)}) "
        "ORDER BY CASE finding.severity WHEN 'blocker' THEN 0 WHEN 'error' THEN 1 "
        "WHEN 'warning' THEN 2 ELSE 3 END, finding.rule_id, artifact.target_id, artifact.artifact_path",
        tuple(artifact_ids),
    )
    return [dict(row) for row in rows]


def _select_contracts(
    connection: sqlite3.Connection,
    contract_specs: list[str],
    registry_targets: set[str],
) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for spec in contract_specs:
        target_id, separator, identity = spec.partition(":")
        contract_id, version_separator, version = identity.rpartition("@")
        if not separator or not target_id or not version_separator or not contract_id or not version:
            raise ContextCompilationError(
                f"contract must use target-id:contract-id@version form: {spec}"
            )
        rows = connection.execute(
            "SELECT * FROM observed_contract WHERE target_id = ? AND contract_id = ? AND version = ?",
            (target_id, contract_id, version),
        ).fetchall()
        if not rows:
            raise ContextCompilationError(f"contract is not present in the governance index: {spec}")
        for row in rows:
            item = dict(row)
            item["selection_reason"] = f"requested-contract:{spec}"
            selected[str(item["contract_key"])] = item
    for target_id in sorted(registry_targets):
        for row in connection.execute(
            "SELECT * FROM observed_contract WHERE target_id = ? ORDER BY contract_id, version",
            (target_id,),
        ):
            item = dict(row)
            item["selection_reason"] = f"changed-contract-registry:{target_id}"
            selected[str(item["contract_key"])] = item
    return [selected[key] for key in sorted(selected)]


def _select_card_ids(
    source: sqlite3.Connection,
    artifacts: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    goal: str,
    *,
    conservative_target_ids: set[str],
    contracts: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], list[dict[str, Any]]]:
    selected: dict[str, set[str]] = {}
    for (card_id,) in source.execute(
        "SELECT card_id FROM card WHERE status = 'active' AND card_type = 'constitution' ORDER BY card_id"
    ):
        selected.setdefault(str(card_id), set()).add("active-constitution")
    for artifact in artifacts:
        for card_id in json.loads(str(artifact["primary_card_ids_json"])):
            selected.setdefault(str(card_id), set()).add(
                f"primary-owner:{artifact['target_id']}:{artifact['artifact_path']}"
            )
        for card_id, selector_kind, selector in source.execute(
            "SELECT card.card_id, scope.selector_kind, scope.selector "
            "FROM card_scope AS scope JOIN card ON card.card_id = scope.card_id "
            "WHERE card.status = 'active' AND card.card_type = 'knowledge' "
            "AND scope.target_id = ? AND scope.polarity = 'include' "
            "ORDER BY card.card_id, scope.scope_id",
            (artifact["target_id"],),
        ):
            matches = (
                path_matches(str(artifact["artifact_path"]), str(selector))
                if selector_kind == "path_glob"
                else False
            )
            if matches:
                selected.setdefault(str(card_id), set()).add(
                    f"scoped-knowledge:{artifact['target_id']}:{artifact['artifact_path']}"
                )
    for finding in findings:
        selected.setdefault(str(finding["card_id"]), set()).add(f"open-finding:{finding['rule_id']}")
        details = json.loads(str(finding["details_json"]))
        target_card = details.get("target_card_id")
        if target_card:
            selected.setdefault(str(target_card), set()).add(f"finding-target:{finding['rule_id']}")

    for target_id in sorted(conservative_target_ids):
        for (card_id,) in source.execute(
            "SELECT DISTINCT card.card_id FROM card "
            "JOIN card_scope AS scope ON scope.card_id = card.card_id "
            "WHERE card.status = 'active' AND card.card_type = 'floor' "
            "AND scope.target_id = ? ORDER BY card.card_id",
            (target_id,),
        ):
            selected.setdefault(str(card_id), set()).add(f"conservative-target-fallback:{target_id}")

    for contract in contracts:
        for (card_id,) in source.execute(
            "SELECT card_id FROM card_contract_binding "
            "WHERE target_id = ? AND contract_id = ? AND version_constraint = ? "
            "AND disposition = 'boundary' ORDER BY card_id",
            (contract["target_id"], contract["contract_id"], contract["version"]),
        ):
            selected.setdefault(str(card_id), set()).add(
                f"contract-binding:{contract['target_id']}:{contract['contract_id']}@{contract['version']}"
            )

    relations = [
        dict(row)
        for row in source.execute(
            "SELECT relation.* FROM card_relation AS relation "
            "JOIN card AS source ON source.card_id = relation.source_card_id "
            "JOIN card AS target ON target.card_id = relation.target_card_id "
            "WHERE source.status = 'active' AND target.status = 'active' "
            "ORDER BY relation.relation_id"
        )
    ]
    changed = True
    while changed:
        changed = False
        for relation in relations:
            source_card = str(relation["source_card_id"])
            target_card = str(relation["target_card_id"])
            relation_type = str(relation["relation_type"])
            expands = relation_type == "depends_on" or (
                relation_type in {"has_producer", "has_consumer"}
                and source_card in selected
            )
            if not expands or source_card not in selected or target_card in selected:
                continue
            selected.setdefault(target_card, set()).add(f"{relation_type}-of:{source_card}")
            changed = True

    selected_floor_ids = {
        str(card_id)
        for (card_id,) in source.execute(
            f"SELECT card_id FROM card WHERE card_type = 'floor' AND card_id IN ({_placeholders(sorted(selected))})",
            tuple(sorted(selected)),
        )
    }
    boundaries: dict[str, dict[str, set[str]]] = {}
    for row in relations:
        if row["relation_type"] not in ("has_producer", "has_consumer"):
            continue
        roles = boundaries.setdefault(str(row["source_card_id"]), {"has_producer": set(), "has_consumer": set()})
        roles[str(row["relation_type"])].add(str(row["target_card_id"]))
    eligible_boundaries: list[tuple[str, list[str]]] = []
    for boundary_id, roles in sorted(boundaries.items()):
        producers = roles["has_producer"] & selected_floor_ids
        consumers = roles["has_consumer"] & selected_floor_ids
        if producers and consumers:
            eligible_boundaries.append((boundary_id, sorted(producers | consumers)))
    goal_terms = {
        term.casefold()
        for term in goal.replace("_", " ").replace("-", " ").split()
        if len(term.strip()) >= 3
    }
    goal_matches: set[str] = set()
    if goal_terms and eligible_boundaries:
        eligible_ids = [boundary_id for boundary_id, _ in eligible_boundaries]
        rows = source.execute(
            f"SELECT card_id, title, summary, body_markdown FROM card "
            f"WHERE card_id IN ({_placeholders(eligible_ids)}) ORDER BY card_id",
            tuple(eligible_ids),
        )
        for card_id, title, summary, body in rows:
            haystack = " ".join((str(card_id), str(title), str(summary), str(body))).casefold()
            if any(term in haystack for term in goal_terms):
                goal_matches.add(str(card_id))
    for boundary_id, participants in eligible_boundaries:
        if contracts and boundary_id not in selected:
            continue
        if goal_matches and boundary_id not in goal_matches:
            continue
        reason = "goal-matched-boundary" if boundary_id in goal_matches else "selected-boundary"
        selected.setdefault(boundary_id, set()).add(reason + ":" + ",".join(participants))

    scenarios: list[dict[str, Any]] = []
    selected_boundary_ids = {
        card_id
        for card_id in selected
        if source.execute(
            "SELECT card_type FROM card WHERE card_id = ?", (card_id,)
        ).fetchone()[0] == "boundary"
    }
    if selected_boundary_ids:
        rows = source.execute(
            f"SELECT DISTINCT scenario.* FROM scenario "
            f"JOIN scenario_card_binding AS binding ON binding.scenario_id = scenario.scenario_id "
            f"WHERE scenario.status <> 'retired' AND binding.card_id IN ({_placeholders(sorted(selected_boundary_ids))}) "
            "ORDER BY scenario.scenario_id",
            tuple(sorted(selected_boundary_ids)),
        )
        scenarios = [dict(row) for row in rows]
        for scenario in scenarios:
            scenario_id = str(scenario["scenario_id"])
            scenario["card_bindings"] = [
                dict(row)
                for row in source.execute(
                    "SELECT * FROM scenario_card_binding WHERE scenario_id = ? ORDER BY role, card_id",
                    (scenario_id,),
                )
            ]
            scenario["checker_bindings"] = [
                dict(row)
                for row in source.execute(
                    "SELECT * FROM scenario_checker_binding WHERE scenario_id = ? ORDER BY checker_id",
                    (scenario_id,),
                )
            ]
            for binding in scenario["card_bindings"]:
                selected.setdefault(str(binding["card_id"]), set()).add(f"scenario:{scenario_id}")
    return selected, scenarios


def _card_payload(source: sqlite3.Connection, selected: dict[str, set[str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    card_ids = sorted(selected)
    placeholders = _placeholders(card_ids)
    cards: list[dict[str, Any]] = []
    for row in source.execute(
        "SELECT * FROM card "
        f"WHERE card_id IN ({placeholders}) "
        "ORDER BY CASE card_type WHEN 'constitution' THEN 0 WHEN 'floor' THEN 1 "
        "WHEN 'boundary' THEN 2 WHEN 'knowledge' THEN 3 ELSE 4 END, card_id",
        tuple(card_ids),
    ):
        card = dict(row)
        card_id = str(card["card_id"])
        card["selection_reasons"] = sorted(selected[card_id])
        card["scopes"] = [
            dict(item)
            for item in source.execute("SELECT * FROM card_scope WHERE card_id = ? ORDER BY scope_id", (card_id,))
        ]
        card["responsibilities"] = [
            dict(item)
            for item in source.execute(
                "SELECT * FROM card_responsibility WHERE card_id = ? "
                "ORDER BY responsibility_kind, item_order",
                (card_id,),
            )
        ]
        card["source_references"] = [
            dict(item)
            for item in source.execute(
                "SELECT * FROM card_source_reference WHERE card_id = ? ORDER BY source_ref_id",
                (card_id,),
            )
        ]
        card["task_directives"] = [
            dict(item)
            for item in source.execute(
                "SELECT * FROM task_directive WHERE card_id = ? ORDER BY directive_kind, item_order",
                (card_id,),
            )
        ]
        rules = []
        for rule_row in source.execute("SELECT * FROM rule WHERE card_id = ? ORDER BY rule_id", (card_id,)):
            rule = dict(rule_row)
            rule["checkers"] = [
                str(item[0])
                for item in source.execute(
                    "SELECT checker_id FROM rule_check_binding WHERE rule_id = ? ORDER BY checker_id",
                    (rule["rule_id"],),
                )
            ]
            rules.append(rule)
        card["rules"] = rules
        cards.append(card)
    relations = [
        dict(row)
        for row in source.execute(
            "SELECT * FROM card_relation "
            f"WHERE source_card_id IN ({placeholders}) AND target_card_id IN ({placeholders}) "
            "ORDER BY relation_id",
            tuple(card_ids + card_ids),
        )
    ]
    return cards, relations


def compile_context(
    source_path: Path,
    index_path: Path,
    path_specs: list[str],
    *,
    targets_path: Path = DEFAULT_TARGETS,
    changed: bool = False,
    goal: str = "",
    contract_specs: list[str] | None = None,
) -> dict[str, Any]:
    source_errors = verify_database(source_path)
    if source_errors:
        raise ContextCompilationError("card source verification failed:\n- " + "\n- ".join(source_errors))
    index_errors = verify_index_freshness(source_path, targets_path, index_path)
    if index_errors:
        raise ContextCompilationError("governance index verification failed:\n- " + "\n- ".join(index_errors))
    source = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    index = sqlite3.connect(f"{index_path.resolve().as_uri()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    index.row_factory = sqlite3.Row
    try:
        source_metadata = _read_metadata(source)
        index_metadata = _read_metadata(index)
        if source_metadata.get("publication_digest") != index_metadata.get("card_source_publication_digest"):
            raise ContextCompilationError("governance index is stale for the current card source; rebuild it first")
        artifacts, artifact_reasons = _select_artifacts(
            index,
            path_specs,
            changed,
            allow_empty=bool(contract_specs),
        )
        if changed:
            artifacts = _expand_dependency_consumers(index, artifacts, artifact_reasons)
        artifact_ids = [str(item["artifact_id"]) for item in artifacts]
        findings = _relevant_findings(index, artifact_ids)
        target_config = json.loads(targets_path.read_text(encoding="utf-8"))
        contract_registries = {
            (str(target["id"]), str(target.get("contract_registry", "")).replace("\\", "/").strip("/"))
            for target in target_config.get("targets", [])
            if target.get("contract_registry")
        }
        registry_targets = {
            str(artifact["target_id"])
            for artifact in artifacts
            if (str(artifact["target_id"]), str(artifact["artifact_path"])) in contract_registries
        }
        contracts = _select_contracts(index, contract_specs or [], registry_targets)
        conservative_artifacts = [
            item for item in artifacts if str(item["coverage_status"]) in {"uncovered", "ambiguous"}
        ]
        conservative_target_ids = {str(item["target_id"]) for item in conservative_artifacts}
        fallback_reasons = sorted(
            f"{item['coverage_status']}:{item['target_id']}:{item['artifact_path']}"
            for item in conservative_artifacts
        )
        selected, scenarios = _select_card_ids(
            source,
            artifacts,
            findings,
            goal,
            conservative_target_ids=conservative_target_ids,
            contracts=contracts,
        )
        cards, relations = _card_payload(source, selected)
        target_ids = sorted(
            {str(item["target_id"]) for item in artifacts}
            | {str(item["target_id"]) for item in contracts}
        )
        targets = [
            dict(row)
            for row in index.execute(
                f"SELECT * FROM target_revision WHERE target_id IN ({_placeholders(target_ids)}) ORDER BY target_id",
                tuple(target_ids),
            )
        ]
        artifact_payload = []
        for item in artifacts:
            artifact_payload.append(
                {
                    "artifact_id": item["artifact_id"],
                    "target_id": item["target_id"],
                    "artifact_path": item["artifact_path"],
                    "worktree_state": item["worktree_state"],
                    "content_digest": item["content_digest"],
                    "coverage_status": item["coverage_status"],
                    "primary_card_ids": json.loads(str(item["primary_card_ids_json"])),
                    "selection_reasons": sorted(artifact_reasons[str(item["artifact_id"])]),
                }
            )
        payload: dict[str, Any] = {
            "schema": CONTEXT_SCHEMA,
            "goal": goal.strip(),
            "source_publication_id": source_metadata["publication_id"],
            "source_publication_digest": source_metadata["publication_digest"],
            "governance_facts_digest": index_metadata["governance_facts_digest"],
            "scanner_version": index_metadata["scanner_version"],
            "parser_versions": json.loads(index_metadata["parser_versions"]),
            "targets": targets,
            "artifacts": artifact_payload,
            "findings": findings,
            "cards": cards,
            "relations": relations,
            "contracts": contracts,
            "scenarios": scenarios,
            "routing": {
                "state": "conservative" if fallback_reasons else "precise",
                "fallback_reasons": fallback_reasons,
                "fallback_target_ids": sorted(conservative_target_ids),
                "contract_reverse_routing": bool(contracts),
            },
        }
        payload["context_digest"] = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        return payload
    finally:
        source.close()
        index.close()


def render_markdown(context: dict[str, Any]) -> str:
    lines = ["# Governance Task Context", ""]
    if context["goal"]:
        lines.extend([f"Goal: {context['goal']}", ""])
    lines.extend(
        [
            f"Context digest: `{context['context_digest']}`",
            f"Governance facts: `{context['governance_facts_digest']}`",
            f"Routing state: `{context['routing']['state']}`",
            "",
            "## Selected Artifacts",
            "",
        ]
    )
    for item in context["artifacts"]:
        owners = ", ".join(item["primary_card_ids"]) or "UNOWNED"
        lines.append(
            f"- `{item['target_id']}:{item['artifact_path']}` [{item['worktree_state']}] owner: `{owners}`"
        )
    if context["findings"]:
        lines.extend(["", "## Open Findings", ""])
        for item in context["findings"]:
            lines.append(
                f"- **{item['severity']}** `{item['rule_id']}` -> `{item['card_id']}`: {item['message']}"
            )
    if context["contracts"]:
        lines.extend(["", "## Routed Contracts", ""])
        for item in context["contracts"]:
            lines.append(
                f"- `{item['target_id']}:{item['contract_id']}@{item['version']}` -> contract binding"
            )
    lines.extend(["", "## Required Cards", ""])
    for card in context["cards"]:
        lines.extend(
            [
                f"### {card['card_id']}: {card['title']}",
                "",
                "Selected because: " + "; ".join(card["selection_reasons"]),
                "",
                card["body_markdown"],
                "",
            ]
        )
        if card["rules"]:
            lines.extend(["Rules:", ""])
            for rule in card["rules"]:
                checkers = ", ".join(rule["checkers"]) or "none"
                lines.append(
                    f"- `{rule['rule_id']}` [{rule['severity']}]: {rule['statement']} (checker: `{checkers}`)"
                )
            lines.append("")
    if context["relations"]:
        lines.extend(["## Selected Relations", ""])
        for relation in context["relations"]:
            lines.append(
                f"- `{relation['source_card_id']}` --{relation['relation_type']}--> `{relation['target_card_id']}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--path", action="append", default=[], dest="paths")
    parser.add_argument("--changed", action="store_true")
    parser.add_argument("--goal", default="")
    parser.add_argument("--contract", action="append", default=[], dest="contracts")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--max-chars", type=int, default=30000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        context = compile_context(
            args.source.resolve(),
            args.index.resolve(),
            args.paths,
            targets_path=args.targets.resolve(),
            changed=args.changed,
            goal=args.goal,
            contract_specs=args.contracts,
        )
        rendered = (
            json.dumps(context, ensure_ascii=False, indent=2) + "\n"
            if args.format == "json"
            else render_markdown(context)
        )
        if len(rendered) > args.max_chars:
            raise ContextCompilationError(
                f"compiled context has {len(rendered)} characters, exceeding --max-chars {args.max_chars}"
            )
        if args.output:
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            print(f"Compiled governance context: {output}")
        else:
            print(rendered, end="")
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error, ContextCompilationError) as exc:
        print(f"Context compilation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
