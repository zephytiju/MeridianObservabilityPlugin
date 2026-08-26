<!-- SPDX-License-Identifier: Apache-2.0 -->

# Configuration

MeridianConstructs/Platform renders deployment configuration. Required values:

- `OTEL_EXPORTER_OTLP_ENDPOINT`: Collector base endpoint (`http` or `https`).
- `OTEL_EXPORTER_OTLP_PROTOCOL`: `grpc` or `http/protobuf`; defaults to `grpc`.
- `MERIDIAN_DEPLOYMENT_ENVIRONMENT`: protected OTel resource identity.
- `OTEL_SERVICE_NAME` and `MERIDIAN_SERVICE_VERSION` when loaded through the
  Core plugin entry point.

Standard per-signal `OTEL_TRACES_EXPORTER`, `OTEL_METRICS_EXPORTER`, and
`OTEL_LOGS_EXPORTER` values of `none` disable that signal. Sampling uses
`OTEL_TRACES_SAMPLER_ARG`; metric interval uses
`OTEL_METRIC_EXPORT_INTERVAL`. The deployment may render standard OTel TLS and
header environment, but the plugin never returns those values through its
public objects, logs, errors, or health report.

Stored query helpers additionally require three logical Resource references:

```text
MERIDIAN_OBSERVABILITY_LOGS_RESOURCE=evidence:telemetry.logs
MERIDIAN_OBSERVABILITY_SPANS_RESOURCE=evidence:telemetry.spans
MERIDIAN_OBSERVABILITY_METRICS_RESOURCE=evidence:telemetry.metrics
```

These are logical registry addresses, not tables or backend locations.
Applications may instead receive an `EvidenceResources` value from their
composition root.
