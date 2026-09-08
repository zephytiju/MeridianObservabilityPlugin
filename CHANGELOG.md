<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

All notable changes follow Semantic Versioning.

## 1.0.3 - 2026-09-08

- Replace historical exact Meridian recipes with justified public API bounds
  admitting Core 1.1.0 and the repaired Evidence/Query/Semantics releases.
- Keep an exact public dependency hash lock and tested artifact ledger; consume
  ClickHouse 1.1.1 with corrected physical cursor boundaries.
- Require real ClickHouse log/span/metric regressions across Python 3.12–3.14
  and two independently selected, digest-pinned server releases.
- Preserve OTel 1.44.0, provider/context policies and golden query fingerprints.

## 1.0.2 - 2026-09-07

- Restore the current design-owned `meridian-plugin-observability` PyPI identity;
  retain the import namespace, plugin entry point, and public helpers.
- Pin Core 1.0.1, Semantics 2.0.0, Query 1.0.2, Evidence 1.0.1, and test-only
  ClickHouse 1.0.1 with matching artifact compatibility evidence.
- Run conformance and release gates against installed packages while preserving
  provider/context policy and golden query fingerprints.

## 1.0.1 - 2026-08-28

- Correct the published distribution identity to
  `meridian-storage-plugin-observability` while preserving the import namespace
  and Core plugin entry point.
- Reconcile package metadata, compatibility evidence, contracts, examples,
  artifact verification, and CI release paths with the authoritative design.
- Add deterministic contract coverage for Core registration, Evidence writes,
  logical query projections, and fail-closed boundaries.

## 1.0.0 - 2026-08-26

- Add governed OTLP providers, structured logging, tracing, and metric instruments.
- Add resource/context policy, redaction, cardinality, recursion suppression, and shutdown health.
- Add bounded Evidence query helpers and Query 1.0.0 logical-plan serialization.
- Add Core plugin discovery, compatibility ledgers, conformance tests, deterministic builds, and CI release evidence.
