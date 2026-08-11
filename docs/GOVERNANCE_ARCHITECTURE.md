# CartridgeFlow External Card Governance

## Current Shape

The product lineage and the governance lineage are different axes:

```text
CartridgeFlow v0.6.0 common kernel
├── v0.6.0-SP Desktop Runner specialization
└── v0.7.0 semantic and workbench extension
```

The governance repository is a removable observer above those repositories. It
does not define a new product version and is not imported by either runtime.

The dependency scan supports five real floors. `src/core/protocol`,
`src/core/runtime`, and `src/core/studio` remain parts of the common kernel
because the source graph contains substantial bidirectional dependencies; they
are described by narrower knowledge cards rather than presented as fictitious
independent floors.

## Card Roles

| Type | Count | Authority | History | Purpose |
| --- | ---: | --- | --- | --- |
| Constitution | 1 | Normative | Independent revisions | Externality and project-wide invariants |
| Floor | 5 | Normative | Independent revisions | One real ownership and dependency domain |
| Boundary | 7 | Normative | Independent revisions | Cross-floor product handoff facts |
| Knowledge | 9 | Descriptive | None | Reusable current understanding for one floor and exact scope |
| Task | 1 | Task | None | Goal, allowed/forbidden scope, required cards, checks and stop conditions |

A knowledge card is a deeper, scoped counterpart to an `AGENTS.md` orientation
file. It explains purpose, stable concepts, navigation, working patterns and
pitfalls. It must not record what happened on a date, who changed something, a
sequence of past work, or pipeline history. It owns no compliance rule. Its
source references and content digest prove that the current explanation is
anchored to current implementation.

## Deterministic Control Loop

```text
exact task paths / Git diff       public contract id + version
              \                         /
               v                       v
 primary floor + scoped Knowledge    Contract Binding -> Boundary
               \                       |
                +---- explicit relations + producer + consumer + Scenario
                                      |
                                      v
                         required reviewed checkers
                                      |
                                      v
             static / floor / boundary / scenario / complete status
                                      |
                                      v
                         exact append-only Ledger footprint
```

The generated index currently covers 199 governed artifacts and 2,561 symbols.
It records Python AST, TypeScript Compiler API and Tree-sitter Go dependencies.
Semantic search does not participate in ownership, dependencies, compliance or
acceptance. Deterministic context chunks and FTS support retrieval; optional
embeddings remain deferred and advisory-only.

## Product Contract Classification

The existing product registry is observed evidence, not the card authority. All
59 releases and 146 usage records are visible in the browser and classified:

| Disposition | Releases | Meaning |
| --- | ---: | --- |
| `boundary` | 20 | Current cross-floor, cross-process, cross-language, cross-repository or release-package facts |
| `knowledge` | 8 | Current internal implementation concepts; useful locally but not global governance contracts |
| `legacy-review` | 31 | Archived old-generation releases retained as migration evidence |

Only `boundary` bindings are formal contracts of the new governance model. The
other entries remain visible so migration does not erase evidence or silently
misrepresent old contracts as current global rules.

## Removal Boundary

`check_removability.py` runs product and DR probes with the governance location
present and with a nonexistent governance path. Product API/base/catalog facts,
DR build digest and DR runtime status must be identical. The independent real
handoff scenario uses the Workbench authoring API to create, validate, certify
and package a temporary CF-CRE@2 cartridge, then uses the real DR CLI to trust,
install, expose and save a public setting, and run it. Missing input and tampered
packages must be rejected without mutating the active cartridge.

## Evidence And Acceptance

The storage model has three independent lifecycles:

| Database | Role | Rebuildable |
| --- | --- | --- |
| `governance-source.sqlite` | Reviewed current cards, scopes, relations, rules and bindings | No |
| `.data/governance-index.sqlite` | Current observed code, symbols, dependencies, contracts and findings | Yes |
| `governance-ledger.sqlite` | Append-only routes, plans, results, diagnostics, acceptance and knowledge sync events | No |

Evidence freshness is evaluated from the dependencies actually used by each
checker: cards, scopes, relations, artifacts, contracts and bindings, checker
configuration, router, context compiler, target configuration, selected closure,
and check plan. Global source and index digests are used only by the dedicated
database-integrity checks. They do not make an unrelated floor, boundary, or
scenario result stale.

The CLI and browser show five distinct states: `static`, `floor`, `boundary`,
`scenario`, and `complete`. Scoped runs leave omitted stages as `not-run`;
`complete` is calculated only by a full unscoped run. Static success never means
product success. The official product protocol audit and conformance entrypoint
are a blocker-level floor checker. In the current workspace they fail because
the embedded protocol source commit and database digest do not match the product
lock; governance reports that mismatch and does not rewrite either side.
