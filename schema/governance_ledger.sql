PRAGMA application_id = 1128681557;
PRAGMA user_version = 1;
PRAGMA foreign_keys = ON;

CREATE TABLE ledger_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE route_run (
    route_run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'error')),
    goal TEXT NOT NULL,
    invocation_json TEXT NOT NULL CHECK (json_valid(invocation_json)),
    routing_state TEXT NOT NULL CHECK (routing_state IN ('all', 'precise', 'conservative')),
    fallback_reasons_json TEXT NOT NULL CHECK (json_valid(fallback_reasons_json)),
    source_publication_digest TEXT NOT NULL,
    governance_facts_digest TEXT NOT NULL,
    router_digest TEXT NOT NULL,
    target_config_digest TEXT NOT NULL,
    selected_closure_digest TEXT NOT NULL,
    check_plan_digest TEXT NOT NULL,
    footprint_complete INTEGER NOT NULL CHECK (footprint_complete IN (0, 1))
) STRICT;

CREATE TABLE check_run (
    run_id TEXT PRIMARY KEY,
    route_run_id TEXT NOT NULL REFERENCES route_run(route_run_id),
    checker_id TEXT NOT NULL,
    acceptance_stage TEXT NOT NULL CHECK (acceptance_stage IN ('static', 'floor', 'boundary', 'scenario')),
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'error', 'skipped')),
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
    exit_code INTEGER,
    command_json TEXT NOT NULL CHECK (json_valid(command_json)),
    stdout_text TEXT NOT NULL DEFAULT '',
    stderr_text TEXT NOT NULL DEFAULT '',
    selection_reason TEXT NOT NULL,
    checker_digest TEXT NOT NULL,
    output_contract TEXT NOT NULL
) STRICT;

CREATE TABLE rule_result (
    result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES check_run(run_id),
    rule_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'error', 'skipped')),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    content_digest TEXT NOT NULL
) STRICT;

CREATE TABLE check_diagnostic (
    diagnostic_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES check_run(run_id),
    rule_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    artifact_id TEXT,
    reason TEXT NOT NULL,
    expected TEXT NOT NULL,
    actual TEXT NOT NULL,
    boundary_card_id TEXT,
    details_json TEXT NOT NULL CHECK (json_valid(details_json))
) STRICT;

CREATE TABLE evidence_dependency (
    dependency_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES check_run(run_id),
    dependency_kind TEXT NOT NULL CHECK (dependency_kind IN (
        'card', 'scope', 'relation', 'artifact', 'contract', 'contract-binding',
        'scenario-binding', 'checker', 'checker-config', 'router',
        'context-compiler', 'target-config', 'selected-closure', 'check-plan',
        'source-global', 'index-global'
    )),
    subject_id TEXT NOT NULL,
    observed_digest TEXT NOT NULL,
    freshness_role TEXT NOT NULL CHECK (freshness_role IN ('exact', 'conservative', 'informational')),
    selection_reason TEXT NOT NULL,
    UNIQUE (run_id, dependency_kind, subject_id)
) STRICT;

CREATE TABLE acceptance_result (
    acceptance_id TEXT PRIMARY KEY,
    route_run_id TEXT NOT NULL REFERENCES route_run(route_run_id),
    acceptance_kind TEXT NOT NULL CHECK (acceptance_kind IN ('static', 'floor', 'boundary', 'scenario', 'complete')),
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'error', 'not-run')),
    required_checker_count INTEGER NOT NULL CHECK (required_checker_count >= 0),
    passed_checker_count INTEGER NOT NULL CHECK (passed_checker_count >= 0),
    details_json TEXT NOT NULL CHECK (json_valid(details_json)),
    content_digest TEXT NOT NULL,
    UNIQUE (route_run_id, acceptance_kind)
) STRICT;

CREATE TABLE knowledge_sync_event (
    event_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    card_id TEXT NOT NULL,
    floor_card_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_digest TEXT,
    after_digest TEXT NOT NULL,
    actor TEXT NOT NULL,
    source_refs_json TEXT NOT NULL CHECK (json_valid(source_refs_json)),
    content_digest TEXT NOT NULL
) STRICT;

CREATE INDEX route_run_finished_idx ON route_run(finished_at, status);
CREATE INDEX check_run_route_idx ON check_run(route_run_id, acceptance_stage, checker_id);
CREATE INDEX dependency_run_idx ON evidence_dependency(run_id, dependency_kind, subject_id);
CREATE INDEX acceptance_route_idx ON acceptance_result(route_run_id, acceptance_kind, status);
CREATE INDEX knowledge_sync_card_idx ON knowledge_sync_event(card_id, occurred_at);

CREATE VIEW latest_acceptance AS
SELECT result.*
FROM acceptance_result AS result
JOIN route_run AS route ON route.route_run_id = result.route_run_id
WHERE route.finished_at = (SELECT MAX(latest.finished_at) FROM route_run AS latest);

CREATE TRIGGER route_run_no_update BEFORE UPDATE ON route_run BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER route_run_no_delete BEFORE DELETE ON route_run BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER check_run_no_update BEFORE UPDATE ON check_run BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER check_run_no_delete BEFORE DELETE ON check_run BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER rule_result_no_update BEFORE UPDATE ON rule_result BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER rule_result_no_delete BEFORE DELETE ON rule_result BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER diagnostic_no_update BEFORE UPDATE ON check_diagnostic BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER diagnostic_no_delete BEFORE DELETE ON check_diagnostic BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER dependency_no_update BEFORE UPDATE ON evidence_dependency BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER dependency_no_delete BEFORE DELETE ON evidence_dependency BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER acceptance_no_update BEFORE UPDATE ON acceptance_result BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER acceptance_no_delete BEFORE DELETE ON acceptance_result BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER knowledge_sync_no_update BEFORE UPDATE ON knowledge_sync_event BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER knowledge_sync_no_delete BEFORE DELETE ON knowledge_sync_event BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
