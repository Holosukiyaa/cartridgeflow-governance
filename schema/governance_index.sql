PRAGMA application_id = 1128681556;
PRAGMA user_version = 3;
PRAGMA foreign_keys = ON;

CREATE TABLE registry_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE target_revision (
    target_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    repository_path TEXT NOT NULL,
    repository_remote TEXT NOT NULL,
    git_head TEXT NOT NULL,
    dirty_path_count INTEGER NOT NULL CHECK (dirty_path_count >= 0),
    artifact_count INTEGER NOT NULL CHECK (artifact_count >= 0),
    content_digest TEXT NOT NULL
) STRICT;

CREATE TABLE observed_artifact (
    artifact_id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL REFERENCES target_revision(target_id) ON DELETE CASCADE,
    artifact_path TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    content_digest TEXT NOT NULL,
    worktree_state TEXT NOT NULL CHECK (worktree_state IN ('tracked', 'modified', 'untracked')),
    UNIQUE (target_id, artifact_path)
) STRICT;

CREATE TABLE observed_symbol (
    symbol_id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL REFERENCES target_revision(target_id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES observed_artifact(artifact_id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    symbol_kind TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    line_start INTEGER NOT NULL CHECK (line_start > 0),
    line_end INTEGER NOT NULL CHECK (line_end >= line_start),
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'internal', 'unknown')),
    UNIQUE (artifact_id, language, symbol_kind, qualified_name, line_start)
) STRICT;

CREATE TABLE scope_match (
    artifact_id TEXT NOT NULL REFERENCES observed_artifact(artifact_id) ON DELETE CASCADE,
    card_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    ownership TEXT NOT NULL CHECK (ownership IN ('primary', 'supporting', 'reference')),
    PRIMARY KEY (artifact_id, card_id, scope_id)
) STRICT;

CREATE TABLE scope_coverage (
    artifact_id TEXT PRIMARY KEY REFERENCES observed_artifact(artifact_id) ON DELETE CASCADE,
    primary_owner_count INTEGER NOT NULL CHECK (primary_owner_count >= 0),
    primary_card_ids_json TEXT NOT NULL CHECK (json_valid(primary_card_ids_json)),
    coverage_status TEXT NOT NULL CHECK (coverage_status IN ('covered', 'uncovered', 'ambiguous'))
) STRICT;

CREATE TABLE observed_dependency (
    dependency_id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL REFERENCES target_revision(target_id) ON DELETE CASCADE,
    source_artifact_id TEXT NOT NULL REFERENCES observed_artifact(artifact_id) ON DELETE CASCADE,
    dependency_kind TEXT NOT NULL,
    target_reference TEXT NOT NULL,
    resolved_artifact_id TEXT REFERENCES observed_artifact(artifact_id),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('resolved', 'external', 'unresolved'))
) STRICT;

CREATE TABLE observed_contract (
    contract_key TEXT PRIMARY KEY,
    target_id TEXT NOT NULL REFERENCES target_revision(target_id) ON DELETE CASCADE,
    contract_id TEXT NOT NULL,
    version TEXT NOT NULL,
    generation TEXT NOT NULL,
    lifecycle TEXT NOT NULL,
    layer INTEGER NOT NULL,
    domain TEXT NOT NULL,
    visibility TEXT NOT NULL,
    display_name TEXT NOT NULL,
    release_key TEXT NOT NULL,
    definition_artifact_path TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    UNIQUE (target_id, contract_id, version)
) STRICT;

CREATE TABLE observed_contract_usage (
    usage_id TEXT PRIMARY KEY,
    contract_key TEXT NOT NULL REFERENCES observed_contract(contract_key) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    actor TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('producer', 'consumer')),
    notes TEXT NOT NULL,
    UNIQUE (contract_key, stage, actor, direction)
) STRICT;

CREATE TABLE card_contract_match (
    binding_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    contract_key TEXT REFERENCES observed_contract(contract_key) ON DELETE CASCADE,
    match_status TEXT NOT NULL CHECK (match_status IN ('matched', 'missing', 'ambiguous')),
    details_json TEXT NOT NULL CHECK (json_valid(details_json))
) STRICT;

CREATE TABLE context_chunk (
    chunk_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('card', 'section')),
    source_id TEXT NOT NULL,
    chunk_order INTEGER NOT NULL CHECK (chunk_order >= 0),
    normative INTEGER NOT NULL CHECK (normative IN (0, 1)),
    content TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    UNIQUE (card_id, source_kind, source_id, chunk_order)
) STRICT;

CREATE TABLE finding (
    finding_id TEXT PRIMARY KEY,
    severity TEXT NOT NULL CHECK (severity IN ('blocker', 'error', 'warning', 'info')),
    finding_type TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    artifact_id TEXT REFERENCES observed_artifact(artifact_id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed'))
) STRICT;

CREATE INDEX artifact_target_path_idx ON observed_artifact(target_id, artifact_path);
CREATE INDEX scope_match_card_idx ON scope_match(card_id, ownership, artifact_id);
CREATE INDEX coverage_status_idx ON scope_coverage(coverage_status, artifact_id);
CREATE INDEX finding_status_idx ON finding(status, severity, finding_type);
CREATE INDEX dependency_source_idx ON observed_dependency(source_artifact_id, resolution_status);
CREATE INDEX dependency_resolved_idx ON observed_dependency(resolved_artifact_id, resolution_status);
CREATE INDEX symbol_artifact_idx ON observed_symbol(artifact_id, line_start, qualified_name);
CREATE INDEX symbol_name_idx ON observed_symbol(qualified_name, symbol_kind);
CREATE INDEX contract_identity_idx ON observed_contract(target_id, contract_id, version);
CREATE INDEX contract_usage_idx ON observed_contract_usage(contract_key, direction, actor);
CREATE INDEX contract_match_card_idx ON card_contract_match(card_id, match_status);
CREATE INDEX context_chunk_card_idx ON context_chunk(card_id, source_kind, source_id);

CREATE VIEW coverage_catalog AS
SELECT artifact.target_id, artifact.artifact_path, artifact.artifact_kind,
       artifact.worktree_state, coverage.primary_owner_count,
       coverage.primary_card_ids_json, coverage.coverage_status
FROM scope_coverage AS coverage
JOIN observed_artifact AS artifact ON artifact.artifact_id = coverage.artifact_id;

CREATE VIEW card_coverage AS
SELECT match.card_id, match.ownership,
       COUNT(DISTINCT match.artifact_id) AS artifact_count
FROM scope_match AS match
GROUP BY match.card_id, match.ownership;

CREATE VIEW finding_catalog AS
SELECT finding.finding_id, finding.severity, finding.finding_type,
       finding.rule_id, finding.card_id,
       artifact.target_id, artifact.artifact_path,
       finding.message, finding.details_json, finding.status
FROM finding
LEFT JOIN observed_artifact AS artifact ON artifact.artifact_id = finding.artifact_id;

CREATE VIEW dependency_catalog AS
SELECT dependency.dependency_id, dependency.target_id,
       source.artifact_path AS source_artifact_path,
       dependency.dependency_kind, dependency.target_reference,
       target.artifact_path AS resolved_artifact_path,
       dependency.resolution_status
FROM observed_dependency AS dependency
JOIN observed_artifact AS source ON source.artifact_id = dependency.source_artifact_id
LEFT JOIN observed_artifact AS target ON target.artifact_id = dependency.resolved_artifact_id;

CREATE VIEW symbol_catalog AS
SELECT symbol.symbol_id, symbol.target_id, artifact.artifact_path,
       symbol.language, symbol.symbol_kind, symbol.qualified_name,
       symbol.line_start, symbol.line_end, symbol.visibility,
       coverage.primary_card_ids_json
FROM observed_symbol AS symbol
JOIN observed_artifact AS artifact ON artifact.artifact_id = symbol.artifact_id
JOIN scope_coverage AS coverage ON coverage.artifact_id = symbol.artifact_id;

CREATE VIEW contract_catalog AS
SELECT contract.*, match.binding_id, match.card_id, match.match_status,
       match.details_json AS binding_details_json
FROM observed_contract AS contract
LEFT JOIN card_contract_match AS match ON match.contract_key = contract.contract_key;
