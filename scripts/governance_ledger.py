"""Create, verify, and append durable governance ledger events."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .governance_db import DEFAULT_DATABASE, canonical_json, verify_database
except ImportError:  # Direct execution: python scripts/governance_ledger.py
    from governance_db import DEFAULT_DATABASE, canonical_json, verify_database


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "governance-ledger.sqlite"
SCHEMA_PATH = ROOT / "schema" / "governance_ledger.sql"
LEDGER_SCHEMA = "cartridgeflow.governance.ledger.v1"


class GovernanceLedgerError(RuntimeError):
    """Raised when durable governance evidence cannot be trusted or appended."""


def digest_payload(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def initialize_ledger(path: Path = DEFAULT_LEDGER) -> None:
    path = path.resolve()
    if path.exists():
        errors = verify_ledger(path)
        if errors:
            raise GovernanceLedgerError("existing ledger is invalid:\n- " + "\n- ".join(errors))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix="governance-ledger-", suffix=".sqlite", dir=path.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        metadata = {
            "schema": LEDGER_SCHEMA,
            "schema_version": "1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "event_policy": "append-only",
        }
        connection.executemany("INSERT INTO ledger_metadata VALUES (?, ?)", sorted(metadata.items()))
        connection.commit()
        connection.close()
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def verify_ledger(path: Path = DEFAULT_LEDGER) -> list[str]:
    if not path.is_file():
        return [f"ledger does not exist: {path}"]
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    errors: list[str] = []
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            errors.append(f"SQLite integrity check failed: {integrity}")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            errors.append("SQLite foreign key check failed")
        metadata = dict(connection.execute("SELECT key, value FROM ledger_metadata"))
        if metadata.get("schema") != LEDGER_SCHEMA:
            errors.append(f"ledger schema must be {LEDGER_SCHEMA}")
        if metadata.get("schema_version") != "1":
            errors.append("ledger schema_version must be 1")
        if metadata.get("event_policy") != "append-only":
            errors.append("ledger event policy must be append-only")
        for table, id_column in (
            ("rule_result", "result_id"),
            ("acceptance_result", "acceptance_id"),
            ("knowledge_sync_event", "event_id"),
        ):
            for row_id, payload_json, content_digest in connection.execute(
                f"SELECT {id_column}, "
                + ("payload_json" if table == "rule_result" else "details_json" if table == "acceptance_result" else "source_refs_json")
                + f", content_digest FROM {table} ORDER BY {id_column}"
            ):
                try:
                    payload = json.loads(str(payload_json))
                except json.JSONDecodeError as exc:
                    errors.append(f"invalid JSON in {table}:{row_id}:{exc}")
                    continue
                if table != "knowledge_sync_event" and digest_payload(payload) != str(content_digest):
                    errors.append(f"content digest mismatch: {table}:{row_id}")
        for row in connection.execute("SELECT * FROM knowledge_sync_event ORDER BY event_id"):
            (
                event_id, occurred_at, card_id, floor_card_id, reason, before_digest,
                after_digest, actor, source_refs_json, content_digest,
            ) = row
            payload = {
                "actor": actor,
                "after_digest": after_digest,
                "before_digest": before_digest,
                "card_id": card_id,
                "floor_card_id": floor_card_id,
                "occurred_at": occurred_at,
                "reason": reason,
                "source_refs": json.loads(str(source_refs_json)),
            }
            if digest_payload(payload) != str(content_digest):
                errors.append(f"content digest mismatch: knowledge_sync_event:{event_id}")
    except sqlite3.Error as exc:
        errors.append(f"cannot inspect ledger: {exc}")
    finally:
        connection.close()
    return errors


def _row_digest(row: sqlite3.Row | dict[str, Any]) -> str:
    return digest_payload(dict(row))


def evidence_freshness(
    ledger_path: Path,
    source_path: Path,
    index_path: Path,
    targets_path: Path,
    *,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Compare recorded exact dependencies with their current authoritative values."""
    ledger = sqlite3.connect(f"{ledger_path.resolve().as_uri()}?mode=ro", uri=True)
    source = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    index = sqlite3.connect(f"{index_path.resolve().as_uri()}?mode=ro", uri=True)
    ledger.row_factory = sqlite3.Row
    source.row_factory = sqlite3.Row
    index.row_factory = sqlite3.Row
    try:
        if run_id:
            runs = ledger.execute("SELECT * FROM check_run WHERE run_id = ?", (run_id,)).fetchall()
        else:
            runs = ledger.execute(
                "SELECT run.* FROM check_run AS run JOIN ("
                "SELECT checker_id, MAX(finished_at) AS finished_at FROM check_run GROUP BY checker_id"
                ") AS latest ON latest.checker_id = run.checker_id AND latest.finished_at = run.finished_at "
                "ORDER BY run.checker_id"
            ).fetchall()
        source_metadata = dict(source.execute("SELECT key, value FROM registry_metadata"))
        index_metadata = dict(index.execute("SELECT key, value FROM registry_metadata"))
        results: list[dict[str, Any]] = []
        for run in runs:
            mismatches: list[dict[str, str]] = []
            dependencies = ledger.execute(
                "SELECT * FROM evidence_dependency WHERE run_id = ? ORDER BY dependency_kind, subject_id",
                (run["run_id"],),
            ).fetchall()
            for dependency in dependencies:
                kind = str(dependency["dependency_kind"])
                subject_id = str(dependency["subject_id"])
                current: str | None
                if kind == "card":
                    row = source.execute("SELECT content_digest FROM card WHERE card_id = ?", (subject_id,)).fetchone()
                    current = str(row[0]) if row else None
                elif kind == "scope":
                    row = source.execute("SELECT * FROM card_scope WHERE scope_id = ?", (subject_id,)).fetchone()
                    current = _row_digest(row) if row else None
                elif kind == "relation":
                    row = source.execute("SELECT * FROM card_relation WHERE relation_id = ?", (subject_id,)).fetchone()
                    current = _row_digest(row) if row else None
                elif kind == "artifact":
                    row = index.execute("SELECT content_digest FROM observed_artifact WHERE artifact_id = ?", (subject_id,)).fetchone()
                    current = str(row[0]) if row else None
                elif kind == "contract":
                    row = index.execute("SELECT content_digest FROM observed_contract WHERE contract_key = ?", (subject_id,)).fetchone()
                    current = str(row[0]) if row else None
                elif kind == "contract-binding":
                    row = source.execute("SELECT * FROM card_contract_binding WHERE binding_id = ?", (subject_id,)).fetchone()
                    current = _row_digest(row) if row else None
                elif kind == "scenario-binding":
                    scenario_id, _, checker_id = subject_id.partition(":")
                    row = source.execute(
                        "SELECT scenario_id, checker_id, required FROM scenario_checker_binding "
                        "WHERE scenario_id = ? AND checker_id = ?",
                        (scenario_id, checker_id),
                    ).fetchone()
                    current = _row_digest(row) if row else None
                elif kind == "checker":
                    row = source.execute("SELECT entrypoint FROM checker WHERE checker_id = ?", (subject_id,)).fetchone()
                    entrypoint = ROOT / str(row[0]) if row else None
                    current = hashlib.sha256(entrypoint.read_bytes()).hexdigest() if entrypoint and entrypoint.is_file() else None
                elif kind == "checker-config":
                    row = source.execute("SELECT * FROM checker WHERE checker_id = ?", (subject_id,)).fetchone()
                    bindings = [
                        dict(item)
                        for item in source.execute(
                            "SELECT * FROM rule_check_binding WHERE checker_id = ? ORDER BY rule_id",
                            (subject_id,),
                        )
                    ]
                    current = _row_digest({"checker": dict(row), "bindings": bindings}) if row else None
                elif kind == "router":
                    current = hashlib.sha256((ROOT / "scripts" / "run_governance_checks.py").read_bytes()).hexdigest()
                elif kind == "context-compiler":
                    current = hashlib.sha256((ROOT / "scripts" / "compile_context.py").read_bytes()).hexdigest()
                elif kind == "target-config":
                    current = hashlib.sha256(targets_path.read_bytes()).hexdigest()
                elif kind == "source-global":
                    current = source_metadata.get("publication_digest")
                elif kind == "index-global":
                    current = index_metadata.get("governance_facts_digest")
                else:
                    current = str(dependency["observed_digest"])
                if current != str(dependency["observed_digest"]):
                    mismatches.append(
                        {
                            "dependency_kind": kind,
                            "subject_id": subject_id,
                            "expected": str(dependency["observed_digest"]),
                            "actual": current or "missing",
                        }
                    )
            results.append(
                {
                    "run_id": str(run["run_id"]),
                    "checker_id": str(run["checker_id"]),
                    "status": "stale" if mismatches else "current",
                    "dependency_count": len(dependencies),
                    "mismatches": mismatches,
                }
            )
        return results
    finally:
        ledger.close()
        source.close()
        index.close()


def record_knowledge_sync(
    ledger_path: Path,
    source_path: Path,
    *,
    card_id: str,
    reason: str,
    actor: str,
    before_digest: str | None,
) -> str:
    source_errors = verify_database(source_path)
    if source_errors:
        raise GovernanceLedgerError("card source verification failed:\n- " + "\n- ".join(source_errors))
    source = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        card = source.execute(
            "SELECT card.card_id, card.card_type, card.content_digest, profile.floor_card_id "
            "FROM card LEFT JOIN knowledge_profile AS profile ON profile.card_id = card.card_id "
            "WHERE card.card_id = ?",
            (card_id,),
        ).fetchone()
        if card is None or card["card_type"] != "knowledge" or not card["floor_card_id"]:
            raise GovernanceLedgerError(f"knowledge sync requires a current Knowledge card: {card_id}")
        source_refs = [
            dict(row)
            for row in source.execute(
                "SELECT target_id, reference_kind, reference, purpose FROM card_source_reference "
                "WHERE card_id = ? ORDER BY source_ref_id",
                (card_id,),
            )
        ]
        floor_card_id = str(card["floor_card_id"])
        after_digest = str(card["content_digest"])
    finally:
        source.close()

    initialize_ledger(ledger_path)
    event_id = str(uuid.uuid4())
    occurred_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "actor": actor,
        "after_digest": after_digest,
        "before_digest": before_digest,
        "card_id": card_id,
        "floor_card_id": floor_card_id,
        "occurred_at": occurred_at,
        "reason": reason,
        "source_refs": source_refs,
    }
    connection = sqlite3.connect(ledger_path)
    try:
        connection.execute(
            "INSERT INTO knowledge_sync_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                occurred_at,
                card_id,
                floor_card_id,
                reason,
                before_digest,
                after_digest,
                actor,
                canonical_json(source_refs),
                digest_payload(payload),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return event_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    subparsers.add_parser("verify")
    subparsers.add_parser("summary")
    freshness = subparsers.add_parser("freshness")
    freshness.add_argument("--source", type=Path, default=DEFAULT_DATABASE)
    freshness.add_argument("--index", type=Path, required=True)
    freshness.add_argument("--targets", type=Path, required=True)
    freshness.add_argument("--run-id")
    sync = subparsers.add_parser("knowledge-sync")
    sync.add_argument("card_id")
    sync.add_argument("--source", type=Path, default=DEFAULT_DATABASE)
    sync.add_argument("--reason", required=True)
    sync.add_argument("--actor", default="codex")
    sync.add_argument("--before-digest")
    args = parser.parse_args()
    ledger = args.ledger.resolve()
    try:
        if args.command == "init":
            initialize_ledger(ledger)
            print(f"Initialized governance ledger: {ledger}")
            return 0
        if args.command == "verify":
            errors = verify_ledger(ledger)
            if errors:
                raise GovernanceLedgerError("ledger verification failed:\n- " + "\n- ".join(errors))
            print(f"Governance ledger verified: {ledger}")
            return 0
        if args.command == "knowledge-sync":
            event_id = record_knowledge_sync(
                ledger,
                args.source.resolve(),
                card_id=args.card_id,
                reason=args.reason,
                actor=args.actor,
                before_digest=args.before_digest,
            )
            print(f"Recorded knowledge sync event: {event_id}")
            return 0
        if args.command == "freshness":
            initialize_ledger(ledger)
            results = evidence_freshness(
                ledger,
                args.source.resolve(),
                args.index.resolve(),
                args.targets.resolve(),
                run_id=args.run_id,
            )
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 1 if any(item["status"] != "current" for item in results) else 0
        initialize_ledger(ledger)
        connection = sqlite3.connect(ledger)
        try:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("route_run", "check_run", "acceptance_result", "knowledge_sync_event")
            }
        finally:
            connection.close()
        print(json.dumps({"schema": LEDGER_SCHEMA, **counts}, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, sqlite3.Error, GovernanceLedgerError) as exc:
        print(f"Governance ledger operation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
