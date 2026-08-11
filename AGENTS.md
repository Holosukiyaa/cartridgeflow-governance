# Governance Repository Guide

## Boundary

This repository is an external, removable governance scaffold. Target product
repositories must not import its code, read its databases, or require its
services to build or run. Scanners and checks may read targets but must not
modify them.

## Authority

`governance-source.sqlite` is the only authoritative card source. The SQL schema
and tooling define structure and verification behavior; do not add a parallel
committed card document tree. Rebuildable facts belong in ignored
`.data/governance-index.sqlite`; durable route, check, acceptance, and knowledge
sync events belong in the append-only `governance-ledger.sqlite`.

## Rules

- Exact scopes and explicit relations govern impact. Semantic search is advisory.
- Every blocker or error rule requires an enabled deterministic checker.
- Checker entrypoints are reviewed repository-relative files. Never execute a
  command copied from card prose or generated model output.
- Card revisions are independent. Updating one card does not version its parent,
  neighbor, or the global catalog.
- Global source and index digests protect only their dedicated database integrity
  checks. Floor, boundary, and scenario evidence expires from its exact dependency
  footprint, not from an unrelated global catalog change.
- Knowledge cards are reusable, floor-scoped current knowledge. They never carry
  revisions, timelines, work logs, normative rules, or checker bindings. Each
  knowledge card explains exactly one floor and should be loaded only when its
  exact scope is relevant to the task.
- The browser is read-only and consumes published SQLite snapshots.
- Product protocol databases are targets or references, never this repository's
  storage backend.
- Do not silence a finding by widening a scope or dependency relation until the
  product responsibility and dependency direction have been confirmed.

## Validation

Run the governance loop in this order:

```powershell
python scripts/governance_db.py
python scripts/check_detachability.py
python scripts/build_governance_index.py build
python scripts/build_governance_index.py
python scripts/run_governance_checks.py --changed
python scripts/check_handoff_e2e.py
python scripts/check_removability.py
python scripts/compile_context.py --changed --output .data/changed-context.md
python -m unittest discover -s tests -v
python scripts/test_card_browser_e2e.py
```

The index check and unified check runner exit nonzero while open findings meet
the default warning threshold. Treat that result as a diagnostic gate, inspect
its `rule_id`, `card_id`, artifact, and checker evidence, and fix or explicitly
review the underlying ownership or architecture decision. A passed run is not
valid evidence when the browser marks it stale.
