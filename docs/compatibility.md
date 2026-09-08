<!-- SPDX-License-Identifier: Apache-2.0 -->

# Compatibility

Version 1.0.3 admits the public Core 1.1.0 dependency closure on Python 3.12–3.14.
Runtime requirements describe public API compatibility; they are separate from
exact deployment selections and the reproducible validation lock.

| Dependency | Requirement | Reason for lower and upper bounds |
| --- | --- | --- |
| Core | `>=1.1.0,<2` | Released contract/provenance separation, existing runtime and plugin SPI 1.x. |
| Semantics | `>=2.0.1,<3` | Repaired public dependency closure and existing 2.x logical Schema model. |
| Evidence | `>=1.0.2,<2` | Core 1.1 closure, mapping-first Evidence and OTel bridge contracts. |
| Query | `>=1.0.3,<2` | Repaired closure, existing query format, projection and deterministic fingerprints. |
| ClickHouse (test only) | `>=1.1.1,<2` | Server/contract separation plus corrected physical cursor boundaries. |

The major upper bounds preserve the consumed API families. The lower bounds
select the first repaired public releases used by this package's conformance.
They do not establish behavior for every version inside those ranges. Untested
combinations remain **unverified**. OpenTelemetry API, SDK and exporters remain
one exact 1.44.0 set because this repair does not change that integration contract.

`requirements-validation.txt` is an exact universal validation dependency lock,
including transitive dependencies and public archive SHA-256 hashes. Install it
with `pip install --require-hashes -r requirements-validation.txt`, then install
`.[test]` normally and run `pip check`. This does not override package metadata.
CI retains its actual installation report and real server observation separately.
The packaged `compatibility.json` records the tested Meridian wheel hashes and
API requirements; it is evidence, never a runtime release allowlist.

The required CI matrix independently selects Python 3.12/3.13/3.14 and ClickHouse
25.3/25.8 images locked to SHA-256 digests in the workflow. The tests record the
actual `SELECT version()` response independently from the selected image/release.
They exercise exported OTel context, Resource and typed payload preservation,
log/span/metric storage reads through public adapters and plugin query helpers,
idempotent append, bounded cursor pages and tenant isolation. Missing engines
fail the suite. The fixture is standalone; replicated/Keeper, restart and backup
acceptance belongs to the released ClickHouse adapter and is not newly claimed
as plugin conformance. This fixture models normalized Collector output; it does
not claim a deployed Collector end-to-end test.

Migration from 1.0.2 requires a fresh normal dependency resolution and deployment
lock. No public helper, plugin contract, Catalog name, OTel policy, Schema format
or query golden fingerprint changes. Historical exact recipes in old releases
remain reproducible evidence and are no longer compiled acceptance gates.
