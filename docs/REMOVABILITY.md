# Removability Acceptance

The governance scaffold passes both required modes:

- `enabled`: target processes receive the governance repository location.
- `absent`: target processes receive a verified nonexistent location and only
  target-local `PYTHONPATH` and binaries.

The current acceptance compares product Base identity, default protocol and 181
API routes; builds DR twice with `-trimpath`; compares the resulting binary
SHA-256 digests; and compares normalized DR runtime status. The observed binary
digest is `481fa9e9a119cb24b41f4a5bb9f5f395f4ddb6ecc5782483e74b5785861b1595`
in both modes.

The cross-repository delivery proof is a separate required scenario. It creates
a temporary Flow through the Workbench API, initializes and activates tuning,
validates compatibility, applies certification, produces a signed `CF-CRE@2`
package with settings and UI contracts, and projects a clean-v1 installation
request and plan. A temporary DR verifies, materializes and activates the
package, returns a product-validated clean-v1 result, consumes and persists a
public cartridge setting, rejects missing required input, completes a valid run
through a host-bound `remote_api` resource, serves the package's non-empty
passive UI, produces the expected delivery, and rejects a tampered package with a failed
clean-v1 result without changing active state. The scenario cleans the temporary
product and DR data.

Run both proofs through the authoritative entrypoint:

```powershell
python scripts/run_governance_checks.py --timeout 600
```

Machine-readable evidence is regenerated at
`.data/removability-report.json`, `.data/handoff-e2e-report.json`, and in the
append-only `governance-ledger.sqlite`. Rebuilding
`.data/governance-index.sqlite` cannot delete the route, check, acceptance,
diagnostic, or knowledge-sync events.
