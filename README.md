# CartridgeFlow Governance Cards

This repository is the detachable governance scaffold for CartridgeFlow. It
observes the product and Desktop Runner repositories, publishes scoped cards to
an independent SQLite source, and produces diagnostics without becoming a
runtime or build dependency of either product.

The current catalog contains 23 cards: one constitution, five real dependency
floors, seven cross-floor boundaries, nine reusable knowledge cards, and one
task template. The locked product registry is observed rather than adopted as
governance authority: all 75 active clean-v1 contracts have an explicit
`boundary` or `knowledge` disposition. The published source commit, v4 product
lock, clean Base, and separate product-owned runtime compatibility catalog are
verified together by product conformance.

## Dependency Rule

```text
CartridgeFlow Governance -> reads CartridgeFlow and Desktop Runner
CartridgeFlow / Desktop Runner -X-> Governance
```

Removing this repository must not change product build, startup, runtime, or
delivery behavior.

## Databases

- `governance-source.sqlite`: reviewed cards, scopes, relations, rules,
  checker bindings, and scenarios. This is the authoritative governance source.
- `.data/governance-index.sqlite`: generated source observations, coverage,
  symbols, product contracts, Knowledge source-anchor state, findings, and
  deterministic context chunks. It can be discarded and rebuilt.
- `governance-ledger.sqlite`: append-only routing runs, check plans and results,
  five-state acceptance snapshots, exact evidence footprints, diagnostics, and
  `knowledge_sync_event` records. Index rebuilds never remove Ledger events.

Normative `constitution`, `floor`, and `boundary` cards have independent
revision histories. A `knowledge` card is intentionally current-only: it has no
revision number, owns no rules, explains exactly one floor, and records reusable
architecture knowledge rather than timelines, work logs, or pipeline history.
Task cards are current work envelopes and likewise have no revision history.

The existing CartridgeFlow `protocol-source.sqlite` is intentionally not used
as the card store. Product contracts and governance cards have independent
identities and lifecycles.

## Commands

```powershell
python scripts/governance_db.py verify
python scripts/governance_db.py summary
python scripts/governance_db.py catalog
python scripts/governance_db.py export --output .data/card-publication.json
python scripts/check_detachability.py --snapshot
python -m pip install -r requirements-scanner.txt
python scripts/build_governance_index.py build
python scripts/build_governance_index.py check
python scripts/build_governance_index.py summary
python scripts/sync_knowledge_anchors.py
python scripts/governance_ledger.py init
python scripts/governance_ledger.py verify
python scripts/governance_ledger.py freshness --index .data/governance-index.sqlite --targets targets.json
python scripts/run_governance_checks.py --changed
python scripts/check_handoff_e2e.py
python scripts/check_removability.py
python scripts/compile_context.py --path cartridgeflow:src/backend/main.py
python scripts/launch_card_browser.py --no-browser
python -m unittest discover -s tests -v
python scripts/test_card_browser_e2e.py
```

`build` always publishes the observed facts, including open findings. `check`
is the enforcement entrypoint: it exits nonzero when findings meet its severity
threshold (warning by default), so an AI worker receives a deterministic failure
without losing the diagnostic index.

`run_governance_checks.py` is the normal acceptance entrypoint. With no scope it
runs every enabled authoritative checker; `--changed` or repeated `--path`
arguments select checkers through the affected cards and required rule bindings.
Every run appends its route decision, exact command, checker digest, rule-level
result, bounded output, and exact dependency footprint to the independent
Ledger. Footprints include the cards, scopes, relations, artifacts, contracts,
checker configuration, router, context compiler, target configuration, selected
closure, and check plan actually used. An unrelated Knowledge change no longer
invalidates evidence for another autonomous region.

The CLI and browser report `static`, `floor`, `boundary`, `scenario`, and
`complete` separately. A scoped run leaves unexecuted stages as `not-run`.
Complete acceptance requires all four underlying stages to pass. The product's
official protocol lock audit and conformance suite are a blocker-level floor
check. The published clean-v1 source, embedded Registry, runtime compatibility
catalog, Base, and v4 lock currently agree; any digest or commit mismatch still
fails the floor and complete states closed.

The dependency scanner uses Python's standard AST, each frontend package's
declared TypeScript compiler, and the pinned Tree-sitter Go grammar. Resolved
local imports are checked against active `depends_on` card relations, and every
parser version is recorded in the index. A configured parser or compiler may
not be missing or silently degrade to text matching.

## Context Compilation

The context compiler accepts exact `target-id:relative/path` inputs,
`target-id:contract-id@version` contract inputs, or `--changed`. It selects primary owner cards, adds only knowledge cards whose
scope matches the selected files, follows explicit `depends_on` relations,
prefers task-goal-matched boundaries when several boundaries join the same
floors, and includes findings attached to the selected files.

An uncovered or ambiguous artifact produces `routing.state=conservative` and
expands validation to the affected target. A public contract routes through its
exact Contract Binding to the Boundary, then proactively expands its producer,
consumer, and bound scenario.

Every active Knowledge source reference carries a reviewed deterministic
artifact-set digest. The generated index reports it as `current`, `stale`, or
`unknown`. A selected stale or unknown Knowledge card cannot narrow work: the
compiler enters conservative mode and adds the affected target floors. This is
source-drift detection, not a claim that tooling understands arbitrary natural
language. Each Knowledge card also carries at least one reviewed, restricted
machine assertion (`artifact_exists`, `text_contains`, or
`json_pointer_equals`). A conflict or indeterminate result produces a finding
and the same conservative expansion. Assertions never execute card-provided
commands and their rule remains owned by the global constitution.
`sync_knowledge_anchors.py` is the explicit review-and-publish operation; each
changed card appends a separate Ledger event.

```powershell
python scripts/compile_context.py `
  --contract cartridgeflow:cartridgeflow.distribution.envelope@1.0.0 `
  --goal "Review the cartridge handoff" `
  --format markdown `
  --output .data/task-context.md
```

Output is deterministic for the same card publication, governance facts, goal,
and paths. Compilation rejects a stale index and fails when the rendered context
exceeds `--max-chars`; semantic similarity is not used for normative selection.

## Card Browser

The local browser reads the source, generated index, and append-only Ledger in
SQLite read-only mode. It exposes no write routes and binds to `127.0.0.1`.

```powershell
python -m pip install -r requirements-browser.txt
python scripts/launch_card_browser.py --port 8041
```

The first screen is the governance dashboard. The compact manager catalog links
every card, rule, relationship, checker, and scenario without loading card
bodies. Separate views cover cards, ownership/dependency/impact relations,
source coverage and dependencies, symbols, all classified product contracts,
findings, check evidence, exact impact queries, and deterministic task contexts.
Knowledge details say `current-only` rather than displaying a fake revision.
Lexical related-card suggestions are visibly advisory and never affect checks.

`publish` accepts a reviewed JSON publication package and atomically creates a
fresh database image:

```powershell
python scripts/governance_db.py publish <package.json>
```

Use `export` to create a temporary ignored publication package for review and
editing, then publish it back atomically. Card content is read from SQLite after
publication. The package carries revision history only for normative cards;
verification rejects any knowledge-card history or rule binding, as well as
missing, non-contiguous, identity-mismatched, or digest-mismatched normative
snapshots. Do not create a parallel committed Markdown card tree.

## Retrieval

Deterministic scope and relation queries select required cards. FTS5 may help
humans find text. Embeddings are deferred and, when introduced, may suggest
related cards only; they can never create formal relations or compliance
decisions.
