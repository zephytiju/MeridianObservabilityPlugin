<!-- SPDX-License-Identifier: Apache-2.0 -->

# Compatibility

The compatibility candidate supports Python 3.12 through 3.14 and pins Core
1.0.1, Semantics 2.0.0, Query 1.0.2, and Evidence 1.0.1. ClickHouse 1.0.1 is a
test-only integration dependency. OpenTelemetry API, SDK, and OTLP exporters
are pinned to 1.44.0 as one coherent instrumentation set.

The machine-readable ledger is packaged at
`meridian_storage.plugins.observability/compatibility.json`. It records the
resolved release commits and downloaded wheel SHA-256 values. CI verifies the
pins from installed distribution metadata rather than importing sibling source.

Design baseline: HLD 56, Catalogs and Public Interfaces 70, Engine Adapters 24,
Kafka Adapter 6, MeridianConstructs 45, Shared Platform Foundations 38, and
Observability LLD 37.
The registry remains exactly `structured`, `object`, `cache`, `evidence`, and
`streaming`; this package owns none of them. NativeQuery is post-V1.
