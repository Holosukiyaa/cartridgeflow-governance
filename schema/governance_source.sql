PRAGMA application_id = 1128681555;
PRAGMA user_version = 2;
PRAGMA foreign_keys = ON;

CREATE TABLE registry_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE card (
    card_id TEXT PRIMARY KEY,
    card_type TEXT NOT NULL CHECK (card_type IN ('constitution', 'floor', 'boundary', 'knowledge', 'task')),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'retired')),
    authority TEXT NOT NULL CHECK (authority IN ('normative', 'descriptive', 'task')),
    revision INTEGER,
    body_markdown TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    CHECK (
        (card_type IN ('constitution', 'floor', 'boundary') AND revision IS NOT NULL AND revision > 0)
        OR (card_type IN ('knowledge', 'task') AND revision IS NULL)
    ),
    CHECK (
        (card_type IN ('constitution', 'floor', 'boundary') AND authority = 'normative')
        OR (card_type = 'knowledge' AND authority = 'descriptive')
        OR (card_type = 'task' AND authority = 'task')
    )
) STRICT;

CREATE TABLE card_revision (
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision > 0),
    content_digest TEXT NOT NULL,
    published_at TEXT NOT NULL,
    change_summary TEXT NOT NULL,
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    PRIMARY KEY (card_id, revision)
) STRICT;

CREATE TABLE card_section (
    section_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    section_order INTEGER NOT NULL CHECK (section_order >= 0),
    heading TEXT NOT NULL,
    content TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    UNIQUE (card_id, section_order)
) STRICT;

CREATE TABLE card_responsibility (
    responsibility_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    responsibility_kind TEXT NOT NULL CHECK (responsibility_kind IN ('owns', 'excludes')),
    item_order INTEGER NOT NULL CHECK (item_order >= 0),
    statement TEXT NOT NULL,
    UNIQUE (card_id, responsibility_kind, item_order)
) STRICT;

CREATE TABLE card_scope (
    scope_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    target_id TEXT NOT NULL,
    selector_kind TEXT NOT NULL CHECK (selector_kind IN ('path_glob', 'symbol', 'api', 'artifact')),
    selector TEXT NOT NULL,
    polarity TEXT NOT NULL CHECK (polarity IN ('include', 'exclude')),
    ownership TEXT NOT NULL CHECK (ownership IN ('primary', 'supporting', 'reference')),
    rationale TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE card_relation (
    relation_id TEXT PRIMARY KEY,
    source_card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (relation_type IN (
        'depends_on', 'governs', 'has_producer', 'has_consumer', 'related_to', 'impacts', 'explains'
    )),
    target_card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    required INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0, 1)),
    rationale TEXT NOT NULL DEFAULT '',
    CHECK (source_card_id <> target_card_id),
    UNIQUE (source_card_id, relation_type, target_card_id)
) STRICT;

CREATE TABLE card_interface (
    interface_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK (direction IN ('input', 'output')),
    name TEXT NOT NULL,
    contract_ref TEXT,
    counterparty_card_id TEXT REFERENCES card(card_id),
    description TEXT NOT NULL,
    UNIQUE (card_id, direction, name)
) STRICT;

CREATE TABLE card_example (
    example_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    example_kind TEXT NOT NULL CHECK (example_kind IN ('valid', 'invalid')),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    fixture_ref TEXT,
    expected_rule_id TEXT REFERENCES rule(rule_id),
    expected_outcome TEXT NOT NULL,
    UNIQUE (card_id, example_kind, title)
) STRICT;

CREATE TABLE card_evidence_requirement (
    requirement_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('source', 'check', 'scenario', 'revision')),
    statement TEXT NOT NULL,
    UNIQUE (card_id, evidence_kind, statement)
) STRICT;

CREATE TABLE card_source_reference (
    source_ref_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    target_id TEXT NOT NULL,
    reference_kind TEXT NOT NULL CHECK (reference_kind IN ('path', 'symbol', 'api', 'artifact')),
    reference TEXT NOT NULL,
    purpose TEXT NOT NULL,
    UNIQUE (card_id, target_id, reference_kind, reference)
) STRICT;

CREATE TABLE card_contract_binding (
    binding_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    target_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    version_constraint TEXT NOT NULL,
    binding_role TEXT NOT NULL CHECK (binding_role IN ('governs', 'producer', 'consumer', 'reference')),
    disposition TEXT NOT NULL CHECK (disposition IN ('boundary', 'knowledge', 'legacy-review')),
    required INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0, 1)),
    rationale TEXT NOT NULL,
    UNIQUE (card_id, target_id, contract_id, version_constraint, binding_role)
) STRICT;

CREATE TABLE knowledge_profile (
    card_id TEXT PRIMARY KEY REFERENCES card(card_id) ON DELETE CASCADE,
    floor_card_id TEXT NOT NULL REFERENCES card(card_id),
    audience TEXT NOT NULL,
    applicability TEXT NOT NULL,
    non_goals TEXT NOT NULL,
    CHECK (card_id <> floor_card_id)
) STRICT;

CREATE TABLE task_directive (
    directive_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    directive_kind TEXT NOT NULL CHECK (directive_kind IN (
        'goal', 'allow', 'forbid', 'require_card', 'check', 'stop'
    )),
    item_order INTEGER NOT NULL CHECK (item_order >= 0),
    value TEXT NOT NULL,
    UNIQUE (card_id, directive_kind, item_order)
) STRICT;

CREATE TABLE checker (
    checker_id TEXT PRIMARY KEY,
    checker_kind TEXT NOT NULL CHECK (checker_kind IN ('python', 'powershell', 'executable')),
    entrypoint TEXT NOT NULL,
    description TEXT NOT NULL,
    checker_stage TEXT NOT NULL CHECK (checker_stage IN ('source', 'floor', 'boundary', 'scenario')),
    output_contract TEXT NOT NULL DEFAULT 'text' CHECK (output_contract IN ('text', 'diagnostic-json-v1')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
) STRICT;

CREATE TABLE rule (
    rule_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    severity TEXT NOT NULL CHECK (severity IN ('blocker', 'error', 'warning', 'info')),
    statement TEXT NOT NULL,
    failure_message TEXT NOT NULL
) STRICT;

CREATE TABLE rule_check_binding (
    rule_id TEXT NOT NULL REFERENCES rule(rule_id) ON DELETE CASCADE,
    checker_id TEXT NOT NULL REFERENCES checker(checker_id) ON DELETE CASCADE,
    binding_mode TEXT NOT NULL CHECK (binding_mode IN ('required', 'advisory')),
    PRIMARY KEY (rule_id, checker_id)
) STRICT;

CREATE TABLE scenario (
    scenario_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'retired'))
) STRICT;

CREATE TABLE scenario_card_binding (
    scenario_id TEXT NOT NULL REFERENCES scenario(scenario_id) ON DELETE CASCADE,
    card_id TEXT NOT NULL REFERENCES card(card_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('governs', 'producer', 'consumer', 'participant')),
    PRIMARY KEY (scenario_id, card_id, role)
) STRICT;

CREATE TABLE scenario_checker_binding (
    scenario_id TEXT NOT NULL REFERENCES scenario(scenario_id) ON DELETE CASCADE,
    checker_id TEXT NOT NULL REFERENCES checker(checker_id) ON DELETE CASCADE,
    required INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0, 1)),
    PRIMARY KEY (scenario_id, checker_id)
) STRICT;

CREATE VIRTUAL TABLE card_fts USING fts5(
    card_id UNINDEXED,
    title,
    summary,
    body_markdown,
    tokenize = 'unicode61'
);

CREATE INDEX card_type_status_idx ON card(card_type, status, card_id);
CREATE INDEX card_scope_target_idx ON card_scope(target_id, selector_kind, selector, polarity, ownership);
CREATE INDEX card_relation_source_idx ON card_relation(source_card_id, relation_type);
CREATE INDEX card_relation_target_idx ON card_relation(target_card_id, relation_type);
CREATE INDEX rule_card_idx ON rule(card_id, severity, rule_id);
CREATE INDEX card_contract_idx ON card_contract_binding(target_id, contract_id, version_constraint);
CREATE INDEX card_source_ref_idx ON card_source_reference(target_id, reference_kind, reference);
CREATE INDEX card_example_card_idx ON card_example(card_id, example_kind);

CREATE VIEW card_catalog AS
SELECT card.card_id, card.card_type, card.title, card.summary, card.status,
       card.authority, card.revision, card.content_digest,
       COUNT(DISTINCT scope.scope_id) AS scope_count,
       COUNT(DISTINCT rule.rule_id) AS rule_count,
       COUNT(DISTINCT contract.binding_id) AS contract_count,
       COUNT(DISTINCT knowledge.source_ref_id) AS source_reference_count,
       COUNT(DISTINCT outgoing.relation_id) AS outgoing_relation_count,
       COUNT(DISTINCT incoming.relation_id) AS incoming_relation_count
FROM card
LEFT JOIN card_scope AS scope ON scope.card_id = card.card_id
LEFT JOIN rule ON rule.card_id = card.card_id
LEFT JOIN card_contract_binding AS contract ON contract.card_id = card.card_id
LEFT JOIN card_source_reference AS knowledge ON knowledge.card_id = card.card_id
LEFT JOIN card_relation AS outgoing ON outgoing.source_card_id = card.card_id
LEFT JOIN card_relation AS incoming ON incoming.target_card_id = card.card_id
GROUP BY card.card_id;

CREATE VIEW rule_catalog AS
SELECT rule.rule_id, rule.card_id, card.title AS card_title, rule.severity,
       rule.statement, rule.failure_message,
       COUNT(DISTINCT binding.checker_id) AS checker_count
FROM rule
JOIN card ON card.card_id = rule.card_id
LEFT JOIN rule_check_binding AS binding ON binding.rule_id = rule.rule_id
GROUP BY rule.rule_id;

CREATE VIEW relation_catalog AS
SELECT relation.relation_id, relation.source_card_id,
       source.title AS source_title, relation.relation_type,
       relation.target_card_id, target.title AS target_title,
       relation.required, relation.rationale
FROM card_relation AS relation
JOIN card AS source ON source.card_id = relation.source_card_id
JOIN card AS target ON target.card_id = relation.target_card_id;
