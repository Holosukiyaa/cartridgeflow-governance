"""Publish, inspect, and verify the authoritative governance card database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "governance-source.sqlite"
SCHEMA_PATH = ROOT / "schema" / "governance_source.sql"
PUBLICATION_SCHEMA = "cartridgeflow.governance.card-publication.v3"
DATABASE_SCHEMA = "cartridgeflow.governance.cards.v3"


class GovernanceDatabaseError(RuntimeError):
    """Raised when a governance database cannot be published or verified."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def card_snapshot(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "authority": card["authority"],
        "body_markdown": card["body_markdown"],
        "card_id": card["card_id"],
        "card_type": card["card_type"],
        "revision": card["revision"],
        "status": card["status"],
        "summary": card["summary"],
        "title": card["title"],
    }


def _required(item: dict[str, Any], names: Iterable[str], kind: str) -> None:
    missing = [name for name in names if name not in item]
    if missing:
        raise GovernanceDatabaseError(f"{kind} is missing required fields: {', '.join(missing)}")


def _insert_cards(connection: sqlite3.Connection, package: dict[str, Any]) -> None:
    published_at = str(package["published_at"])
    current_cards: dict[tuple[str, int], dict[str, Any]] = {}
    for item in package.get("cards", []):
        _required(
            item,
            ("card_id", "card_type", "title", "summary", "status", "authority", "revision", "body_markdown"),
            "card",
        )
        snapshot = card_snapshot(item)
        digest = digest_json(snapshot)
        connection.execute(
            "INSERT INTO card VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item["card_id"], item["card_type"], item["title"], item["summary"],
                item["status"], item["authority"], item["revision"], item["body_markdown"], digest,
            ),
        )
        if item["card_type"] in {"constitution", "floor", "boundary"}:
            current_cards[(str(item["card_id"]), int(item["revision"]))] = item
        connection.execute(
            "INSERT INTO card_fts(card_id, title, summary, body_markdown) VALUES (?, ?, ?, ?)",
            (item["card_id"], item["title"], item["summary"], item["body_markdown"]),
        )

    inserted_revisions: set[tuple[str, int]] = set()
    for item in package.get("card_revisions", []):
        _required(
            item,
            ("card_id", "revision", "published_at", "change_summary", "snapshot"),
            "card_revision",
        )
        if not isinstance(item["snapshot"], dict):
            raise GovernanceDatabaseError("card_revision snapshot must be an object")
        snapshot = card_snapshot(item["snapshot"])
        key = (str(item["card_id"]), int(item["revision"]))
        if str(snapshot["card_id"]) != key[0] or int(snapshot["revision"]) != key[1]:
            raise GovernanceDatabaseError(
                f"card_revision identity does not match its snapshot: {key[0]}@{key[1]}"
            )
        digest = digest_json(snapshot)
        connection.execute(
            "INSERT INTO card_revision VALUES (?, ?, ?, ?, ?, ?)",
            (
                key[0], key[1], digest, str(item["published_at"]),
                str(item["change_summary"]), canonical_json(snapshot),
            ),
        )
        inserted_revisions.add(key)

    for key, item in current_cards.items():
        if key in inserted_revisions:
            continue
        snapshot = card_snapshot(item)
        connection.execute(
            "INSERT INTO card_revision VALUES (?, ?, ?, ?, ?, ?)",
            (
                key[0], key[1], digest_json(snapshot), published_at,
                item.get("change_summary", "Initial publication"), canonical_json(snapshot),
            ),
        )


def _insert_rows(connection: sqlite3.Connection, package: dict[str, Any]) -> None:
    table_fields = {
        "card_sections": (
            "card_section",
            ("section_id", "card_id", "section_order", "heading", "content", "content_digest"),
        ),
        "scopes": (
            "card_scope",
            ("scope_id", "card_id", "target_id", "selector_kind", "selector", "polarity", "ownership", "rationale"),
        ),
        "responsibilities": (
            "card_responsibility",
            ("responsibility_id", "card_id", "responsibility_kind", "item_order", "statement"),
        ),
        "relations": (
            "card_relation",
            ("relation_id", "source_card_id", "relation_type", "target_card_id", "required", "rationale"),
        ),
        "checkers": (
            "checker",
            (
                "checker_id", "checker_kind", "entrypoint", "description",
                "checker_stage", "output_contract", "enabled",
            ),
        ),
        "rules": (
            "rule",
            ("rule_id", "card_id", "severity", "statement", "failure_message"),
        ),
        "rule_check_bindings": (
            "rule_check_binding",
            ("rule_id", "checker_id", "binding_mode"),
        ),
        "interfaces": (
            "card_interface",
            ("interface_id", "card_id", "direction", "name", "contract_ref", "counterparty_card_id", "description"),
        ),
        "examples": (
            "card_example",
            ("example_id", "card_id", "example_kind", "title", "description", "fixture_ref", "expected_rule_id", "expected_outcome"),
        ),
        "evidence_requirements": (
            "card_evidence_requirement",
            ("requirement_id", "card_id", "evidence_kind", "statement"),
        ),
        "source_references": (
            "card_source_reference",
            (
                "source_ref_id", "card_id", "target_id", "reference_kind", "reference", "purpose",
                "anchor_algorithm", "anchor_digest",
            ),
        ),
        "contract_bindings": (
            "card_contract_binding",
            (
                "binding_id", "card_id", "target_id", "contract_id", "version_constraint",
                "binding_role", "disposition", "required", "rationale",
            ),
        ),
        "knowledge_profiles": (
            "knowledge_profile",
            ("card_id", "floor_card_id", "audience", "applicability", "non_goals"),
        ),
        "task_directives": (
            "task_directive",
            ("directive_id", "card_id", "directive_kind", "item_order", "value"),
        ),
        "scenarios": (
            "scenario",
            ("scenario_id", "title", "description", "status"),
        ),
        "scenario_card_bindings": (
            "scenario_card_binding",
            ("scenario_id", "card_id", "role"),
        ),
        "scenario_checker_bindings": (
            "scenario_checker_binding",
            ("scenario_id", "checker_id", "required"),
        ),
    }
    for package_key, (table, fields) in table_fields.items():
        placeholders = ", ".join("?" for _ in fields)
        for item in package.get(package_key, []):
            if package_key == "card_sections" and "content_digest" not in item:
                item = {**item, "content_digest": hashlib.sha256(item["content"].encode("utf-8")).hexdigest()}
            if package_key == "source_references":
                item = {
                    **item,
                    "anchor_algorithm": item.get("anchor_algorithm"),
                    "anchor_digest": item.get("anchor_digest"),
                }
            _required(item, fields, package_key)
            connection.execute(
                f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({placeholders})",
                tuple(item[field] for field in fields),
            )


def build_database(package: dict[str, Any], target: Path) -> None:
    if package.get("schema") != PUBLICATION_SCHEMA:
        raise GovernanceDatabaseError(f"publication schema must be {PUBLICATION_SCHEMA}")
    _required(package, ("publication_id", "published_at", "cards"), "publication")
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix="governance-source-", suffix=".sqlite", dir=target.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temporary)
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        metadata = {
            "schema": DATABASE_SCHEMA,
            "schema_version": "3",
            "publication_id": str(package["publication_id"]),
            "published_at": str(package["published_at"]),
            "publication_digest": digest_json(package),
            "embedding_policy": "deferred-advisory-only",
        }
        for key, value in sorted(metadata.items()):
            connection.execute("INSERT INTO registry_metadata VALUES (?, ?)", (key, value))
        _insert_cards(connection, package)
        _insert_rows(connection, package)
        connection.commit()
        errors = verify_connection(connection, root=ROOT)
        if errors:
            raise GovernanceDatabaseError("publication verification failed:\n- " + "\n- ".join(errors))
        connection.execute("PRAGMA optimize")
        connection.commit()
        connection.close()
        connection = None
        os.replace(temporary, target)
    except Exception:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
        raise


def _dependency_cycle(connection: sqlite3.Connection) -> list[str] | None:
    graph: dict[str, list[str]] = {}
    for source, target in connection.execute(
        "SELECT source_card_id, target_card_id FROM card_relation WHERE relation_type = 'depends_on'"
    ):
        graph.setdefault(str(source), []).append(str(target))
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node: str, path: list[str]) -> list[str] | None:
        if node in visiting:
            index = path.index(node)
            return path[index:] + [node]
        if node in visited:
            return None
        visiting.add(node)
        path.append(node)
        for target in graph.get(node, []):
            cycle = walk(target, path)
            if cycle:
                return cycle
        path.pop()
        visiting.remove(node)
        visited.add(node)
        return None

    for node in sorted(graph):
        cycle = walk(node, [])
        if cycle:
            return cycle
    return None


def verify_connection(connection: sqlite3.Connection, *, root: Path | None = None) -> list[str]:
    errors: list[str] = []
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        errors.append(f"SQLite integrity check failed: {integrity}")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        errors.append("SQLite foreign key check failed")
    metadata = dict(connection.execute("SELECT key, value FROM registry_metadata"))
    if metadata.get("schema") != DATABASE_SCHEMA:
        errors.append(f"registry schema must be {DATABASE_SCHEMA}")
    if metadata.get("schema_version") != "3":
        errors.append("registry schema_version must be 3")
    if metadata.get("embedding_policy") != "deferred-advisory-only":
        errors.append("embedding policy must remain deferred and advisory-only")

    cards = connection.execute("SELECT * FROM card ORDER BY card_id").fetchall()
    if not cards:
        errors.append("at least one card is required")
    for row in cards:
        item = dict(row)
        expected = digest_json(card_snapshot(item))
        if item["content_digest"] != expected:
            errors.append(f"card digest mismatch: {item['card_id']}")
        history = connection.execute(
            "SELECT revision, content_digest, snapshot_json FROM card_revision "
            "WHERE card_id = ? ORDER BY revision",
            (item["card_id"],),
        ).fetchall()
        revisions = [int(history_row[0]) for history_row in history]
        if item["card_type"] in {"constitution", "floor", "boundary"}:
            revision = connection.execute(
                "SELECT content_digest FROM card_revision WHERE card_id = ? AND revision = ?",
                (item["card_id"], item["revision"]),
            ).fetchone()
            if revision is None or revision[0] != expected:
                errors.append(f"current card revision is missing or mismatched: {item['card_id']}")
            if revisions != list(range(1, int(item["revision"]) + 1)):
                errors.append(f"card revision history is incomplete: {item['card_id']}:{revisions}")
        elif revisions:
            errors.append(f"{item['card_type']} card must not have revision history: {item['card_id']}")
        for history_revision, history_digest, snapshot_json in history:
            try:
                snapshot = json.loads(str(snapshot_json))
                normalized = card_snapshot(snapshot)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(
                    f"card revision snapshot is invalid: {item['card_id']}@{history_revision}:{exc}"
                )
                continue
            if item["card_type"] in {"constitution", "floor", "boundary"} and (
                str(normalized["card_id"]) != str(item["card_id"])
                or int(normalized["revision"]) != int(history_revision)
            ):
                errors.append(
                    f"card revision snapshot identity mismatch: {item['card_id']}@{history_revision}"
                )
            if str(history_digest) != digest_json(normalized):
                errors.append(f"card revision digest mismatch: {item['card_id']}@{history_revision}")

    if connection.execute("SELECT COUNT(*) FROM card_fts").fetchone()[0] != len(cards):
        errors.append("card FTS index does not cover every card")
    for card_type in ("constitution", "floor", "boundary"):
        count = connection.execute(
            "SELECT COUNT(*) FROM card WHERE status = 'active' AND card_type = ?", (card_type,)
        ).fetchone()[0]
        if count == 0:
            errors.append(f"at least one active {card_type} card is required")

    active_cards = connection.execute(
        "SELECT card_id, card_type FROM card WHERE status = 'active' ORDER BY card_id"
    ).fetchall()
    for card_id, card_type in active_cards:
        for responsibility_kind in ("owns", "excludes"):
            count = connection.execute(
                "SELECT COUNT(*) FROM card_responsibility WHERE card_id = ? AND responsibility_kind = ?",
                (card_id, responsibility_kind),
            ).fetchone()[0]
            if count == 0:
                errors.append(f"active card lacks {responsibility_kind} responsibility: {card_id}")
        for example_kind in ("valid", "invalid"):
            count = connection.execute(
                "SELECT COUNT(*) FROM card_example WHERE card_id = ? AND example_kind = ?",
                (card_id, example_kind),
            ).fetchone()[0]
            if count == 0:
                errors.append(f"active card lacks {example_kind} example: {card_id}")
        if card_type != "task":
            if connection.execute(
                "SELECT COUNT(*) FROM card_scope WHERE card_id = ?", (card_id,)
            ).fetchone()[0] == 0:
                errors.append(f"active card lacks a scope: {card_id}")
            if connection.execute(
                "SELECT COUNT(*) FROM card_source_reference WHERE card_id = ?", (card_id,)
            ).fetchone()[0] == 0:
                errors.append(f"active card lacks a source reference: {card_id}")
        if connection.execute(
            "SELECT COUNT(*) FROM card_evidence_requirement WHERE card_id = ?", (card_id,)
        ).fetchone()[0] == 0:
            errors.append(f"active card lacks an evidence requirement: {card_id}")
        if card_type in {"constitution", "floor", "boundary"}:
            for direction in ("input", "output"):
                if connection.execute(
                    "SELECT COUNT(*) FROM card_interface WHERE card_id = ? AND direction = ?",
                    (card_id, direction),
                ).fetchone()[0] == 0:
                    errors.append(f"normative card lacks {direction} interface: {card_id}")

    knowledge_cards = connection.execute(
        "SELECT card_id FROM card WHERE status = 'active' AND card_type = 'knowledge' ORDER BY card_id"
    ).fetchall()
    for (card_id,) in knowledge_cards:
        profile = connection.execute(
            "SELECT floor_card_id FROM knowledge_profile WHERE card_id = ?", (card_id,)
        ).fetchone()
        if profile is None:
            errors.append(f"knowledge card lacks a floor profile: {card_id}")
        explains = connection.execute(
            "SELECT COUNT(*) FROM card_relation WHERE source_card_id = ? AND relation_type = 'explains'",
            (card_id,),
        ).fetchone()[0]
        if explains != 1:
            errors.append(f"knowledge card must explain exactly one floor: {card_id}:{explains}")
        if connection.execute("SELECT COUNT(*) FROM rule WHERE card_id = ?", (card_id,)).fetchone()[0]:
            errors.append(f"knowledge card must not own normative rules: {card_id}")
        unanchored = connection.execute(
            "SELECT source_ref_id, anchor_algorithm, anchor_digest FROM card_source_reference "
            "WHERE card_id = ? AND (anchor_algorithm IS NULL OR anchor_digest IS NULL) "
            "ORDER BY source_ref_id",
            (card_id,),
        ).fetchall()
        errors.extend(
            f"knowledge source reference lacks a reviewed anchor: {card_id}:{row[0]}"
            for row in unanchored
        )
        invalid_anchors = connection.execute(
            "SELECT source_ref_id, anchor_digest FROM card_source_reference "
            "WHERE card_id = ? AND anchor_digest IS NOT NULL ORDER BY source_ref_id",
            (card_id,),
        ).fetchall()
        for source_ref_id, anchor_digest in invalid_anchors:
            if any(character not in "0123456789abcdef" for character in str(anchor_digest)):
                errors.append(
                    f"knowledge source reference anchor is not lowercase SHA-256: {card_id}:{source_ref_id}"
                )

    task_cards = connection.execute(
        "SELECT card_id FROM card WHERE status = 'active' AND card_type = 'task' ORDER BY card_id"
    ).fetchall()
    for (card_id,) in task_cards:
        for kind in ("goal", "allow", "forbid", "require_card", "check", "stop"):
            if connection.execute(
                "SELECT COUNT(*) FROM task_directive WHERE card_id = ? AND directive_kind = ?",
                (card_id, kind),
            ).fetchone()[0] == 0:
                errors.append(f"task card lacks {kind} directive: {card_id}")

    duplicate_scopes = connection.execute(
        "SELECT target_id, selector_kind, selector, COUNT(DISTINCT card_id) "
        "FROM card_scope WHERE polarity = 'include' AND ownership = 'primary' "
        "GROUP BY target_id, selector_kind, selector HAVING COUNT(DISTINCT card_id) > 1"
    ).fetchall()
    for target_id, selector_kind, selector, count in duplicate_scopes:
        errors.append(f"primary scope is owned by {count} cards: {target_id}:{selector_kind}:{selector}")

    unbound = connection.execute(
        "SELECT rule.rule_id FROM rule "
        "WHERE rule.severity IN ('blocker', 'error') AND NOT EXISTS ("
        "SELECT 1 FROM rule_check_binding AS binding "
        "JOIN checker ON checker.checker_id = binding.checker_id "
        "WHERE binding.rule_id = rule.rule_id AND binding.binding_mode = 'required' AND checker.enabled = 1) "
        "ORDER BY rule.rule_id"
    ).fetchall()
    errors.extend(f"strong rule has no enabled required checker: {row[0]}" for row in unbound)

    boundaries = connection.execute(
        "SELECT card_id FROM card WHERE card_type = 'boundary' AND status = 'active' ORDER BY card_id"
    ).fetchall()
    for (card_id,) in boundaries:
        for relation_type in ("has_producer", "has_consumer"):
            count = connection.execute(
                "SELECT COUNT(*) FROM card_relation AS relation "
                "JOIN card AS target ON target.card_id = relation.target_card_id "
                "WHERE relation.source_card_id = ? AND relation.relation_type = ? "
                "AND target.card_type = 'floor' AND target.status = 'active'",
                (card_id, relation_type),
            ).fetchone()[0]
            if count == 0:
                errors.append(f"active boundary card lacks an active floor {relation_type}: {card_id}")
        for direction in ("input", "output"):
            if connection.execute(
                "SELECT COUNT(*) FROM card_interface WHERE card_id = ? AND direction = ?",
                (card_id, direction),
            ).fetchone()[0] == 0:
                errors.append(f"active boundary card lacks a declared {direction}: {card_id}")
        if connection.execute(
            "SELECT COUNT(*) FROM card_contract_binding WHERE card_id = ? AND disposition = 'boundary'",
            (card_id,),
        ).fetchone()[0] == 0:
            errors.append(f"active boundary card lacks a product contract binding: {card_id}")

    for scenario_id, in connection.execute(
        "SELECT scenario_id FROM scenario WHERE status = 'active' ORDER BY scenario_id"
    ):
        if connection.execute(
            "SELECT COUNT(*) FROM scenario_checker_binding WHERE scenario_id = ? AND required = 1",
            (scenario_id,),
        ).fetchone()[0] == 0:
            errors.append(f"active scenario lacks a required checker: {scenario_id}")

    cycle = _dependency_cycle(connection)
    if cycle:
        errors.append("card dependency cycle: " + " -> ".join(cycle))

    if root is not None:
        root = root.resolve()
        for checker_id, entrypoint, enabled in connection.execute(
            "SELECT checker_id, entrypoint, enabled FROM checker ORDER BY checker_id"
        ):
            path = (root / entrypoint).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                errors.append(f"checker entrypoint escapes governance repository: {checker_id}")
                continue
            if enabled and not path.is_file():
                errors.append(f"enabled checker entrypoint does not exist: {checker_id}:{entrypoint}")
    return errors


def verify_database(path: Path) -> list[str]:
    if not path.is_file():
        return [f"governance database does not exist: {path}"]
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return verify_connection(connection, root=ROOT)
    except sqlite3.Error as exc:
        return [f"cannot verify governance database: {exc}"]
    finally:
        connection.close()


def export_database(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        metadata = dict(connection.execute("SELECT key, value FROM registry_metadata"))
        package: dict[str, Any] = {
            "schema": PUBLICATION_SCHEMA,
            "publication_id": metadata["publication_id"],
            "published_at": metadata["published_at"],
        }
        cards: list[dict[str, Any]] = []
        for row in connection.execute(
            "SELECT card.*, revision.change_summary FROM card "
            "LEFT JOIN card_revision AS revision ON revision.card_id = card.card_id "
            "AND revision.revision = card.revision ORDER BY card.card_id"
        ):
            item = dict(row)
            item.pop("content_digest")
            cards.append(item)
        package["cards"] = cards
        package["card_revisions"] = [
            {
                "card_id": str(row["card_id"]),
                "revision": int(row["revision"]),
                "published_at": str(row["published_at"]),
                "change_summary": str(row["change_summary"]),
                "snapshot": json.loads(str(row["snapshot_json"])),
            }
            for row in connection.execute(
                "SELECT * FROM card_revision ORDER BY card_id, revision"
            )
        ]
        table_exports = {
            "card_sections": ("card_section", "section_order, section_id"),
            "scopes": ("card_scope", "card_id, scope_id"),
            "responsibilities": ("card_responsibility", "card_id, responsibility_kind, item_order"),
            "relations": ("card_relation", "source_card_id, relation_type, target_card_id"),
            "checkers": ("checker", "checker_id"),
            "rules": ("rule", "card_id, rule_id"),
            "rule_check_bindings": ("rule_check_binding", "rule_id, checker_id"),
            "interfaces": ("card_interface", "card_id, direction, name"),
            "examples": ("card_example", "card_id, example_kind, title"),
            "evidence_requirements": ("card_evidence_requirement", "card_id, evidence_kind, requirement_id"),
            "source_references": ("card_source_reference", "card_id, target_id, reference"),
            "contract_bindings": ("card_contract_binding", "card_id, contract_id, version_constraint"),
            "knowledge_profiles": ("knowledge_profile", "card_id"),
            "task_directives": ("task_directive", "card_id, directive_kind, item_order"),
            "scenarios": ("scenario", "scenario_id"),
            "scenario_card_bindings": ("scenario_card_binding", "scenario_id, card_id, role"),
            "scenario_checker_bindings": ("scenario_checker_binding", "scenario_id, checker_id"),
        }
        for package_key, (table, order) in table_exports.items():
            package[package_key] = [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}")]
        return package
    finally:
        connection.close()


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _read_rows(path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(sql, params)]
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    subparsers = parser.add_subparsers(dest="command")
    publish = subparsers.add_parser("publish")
    publish.add_argument("package", type=Path)
    subparsers.add_parser("verify")
    subparsers.add_parser("summary")
    subparsers.add_parser("catalog")
    export = subparsers.add_parser("export")
    export.add_argument("--output", type=Path)
    card = subparsers.add_parser("card")
    card.add_argument("card_id")
    parser.set_defaults(command="verify")
    args = parser.parse_args()
    database = args.database.resolve()
    try:
        if args.command == "publish":
            package = json.loads(args.package.read_text(encoding="utf-8"))
            build_database(package, database)
            print(f"Published governance cards: {database}")
            return 0
        if args.command == "verify":
            errors = verify_database(database)
            if errors:
                print("Governance database verification failed:\n- " + "\n- ".join(errors))
                return 1
            print("Governance card database verified.")
            return 0
        if args.command == "summary":
            rows = _read_rows(database, "SELECT card_type, status, COUNT(*) AS count FROM card GROUP BY card_type, status ORDER BY card_type, status")
            _print({"database": str(database), "cards": rows})
            return 0
        if args.command == "catalog":
            _print(_read_rows(database, "SELECT * FROM card_catalog ORDER BY card_type, card_id"))
            return 0
        if args.command == "export":
            package = export_database(database)
            if args.output:
                output = args.output.resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"Exported temporary publication package: {output}")
            else:
                _print(package)
            return 0
        rows = _read_rows(database, "SELECT * FROM card WHERE card_id = ?", (args.card_id,))
        if not rows:
            print(f"Card not found: {args.card_id}")
            return 1
        _print(rows[0])
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error, GovernanceDatabaseError) as exc:
        print(f"Governance database command failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
