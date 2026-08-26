<!-- SPDX-License-Identifier: Apache-2.0 -->

# Architecture

The package owns one plugin facade and no Catalog. A started Meridian runtime
is mandatory. Consumer calls flow through governed tracer, meter, logger, and
query helpers:

```text
service -> observability facade -> OTel SDK -> OTLP -> Constructs-owned Collector
service -> telemetry query helper -> evidence.query -> Meridian Core -> selected Adapter
```

The provider layer installs process-wide providers once. An identical
service/profile/config fingerprint reuses the installed bundle; a different
fingerprint fails with `OBSERVABILITY_PROVIDER_CONFLICT`. Export callbacks run
under a `ContextVar` suppression guard so exporter health or transport work
cannot instrument itself. Provider shutdown flushes all three signals and
returns a structured report.

Resource attributes always contain service name, service version, deployment
environment, and Meridian SDK version. Those fields cannot be overridden.
Operation context contributes request and correlation IDs to logs/spans,
allowlisted scope labels, and allowlisted baggage. Principal and tenant values
are never copied. Metrics use active-span exemplars plus only explicitly
allowlisted dimensions and a per-instrument cardinality budget.

Stored helpers use only logical `evidence` Resources. They expose both the
mapping-first `evidence.query` Expression executed by Core and the corresponding
`meridian.operation.query.v1` plan for inspection/conformance. Neither surface
accepts an Adapter, Engine, endpoint, credential, SQL, native query, or raw
payload.

The task-owned distribution name is `meridian-plugin-observability`. The locked
LLD revision 17 retains the earlier working label
`meridian-storage-plugin-observability`; this release follows the repository and
distribution identity explicitly assigned by [02m-14] while preserving the
locked import namespace and plugin contract.
