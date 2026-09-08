<!-- SPDX-License-Identifier: Apache-2.0 -->

# Meridian Observability Plugin

[![CI](https://github.com/zephytiju/MeridianObservabilityPlugin/actions/workflows/ci.yml/badge.svg)](https://github.com/zephytiju/MeridianObservabilityPlugin/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12--3.14-blue.svg)](pyproject.toml)

`meridian-plugin-observability` is the single Apache-2.0 Python distribution in
this repository. It contributes `meridian_storage.plugins.observability` to the
PEP 420 `meridian_storage` namespace.

The plugin gives a started Meridian 1.x runtime one governed surface for:

- process-wide or isolated OpenTelemetry tracers, meters, and structured loggers;
- resource, context, redaction, cardinality, recursion, and shutdown policy;
- OTLP export to the Collector provisioned by MeridianConstructs; and
- bounded log, trace, and metric queries through the released `evidence` Catalog.

It does not define a telemetry or observability Catalog, instantiate ClickHouse,
accept backend credentials, start a Collector, or expose an Adapter/Engine API.

## Install

```console
python -m pip install meridian-plugin-observability==1.0.3
```

The release supports the public Core 1.1.0, Semantics 2.0.1, Evidence 1.0.2
and Query 1.0.3 closure with compatible major bounds. OpenTelemetry remains
1.44.0; ClickHouse 1.1.1 is test-only. See the compatibility rationale and exact
validation lock for the verified combination.

Deployment must render `OTEL_EXPORTER_OTLP_ENDPOINT`,
`MERIDIAN_DEPLOYMENT_ENVIRONMENT`, and any TLS/exporter environment required by
the Collector. Applications do not pass backend credentials or create a
Collector.

## Instrumentation

```python
from meridian_storage.plugins.observability import Observability

observability = Observability(
    meridian,
    service_name="investigation-api",
    service_version="1.4.0",
    deployment_environment="prod",
)
observability.install_global_otel_provider()

tracer = observability.tracer(__name__)
meter = observability.meter(__name__)
logger = observability.logger(__name__)

requests = meter.create_counter(
    "search.requests",
    unit="{request}",
    allowed_attributes=("result",),
)

with tracer.start_as_current_span("case.search"):
    requests.add(1, {"result": "accepted"})
    logger.info("case search accepted")
```

The composition root supplies only service identity and approved resource
attributes. Collector endpoint, OTLP protocol, signal enablement, TLS, queue,
batching, and sampling come from deployment-rendered Meridian configuration or
standard OTel deployment environment variables.

## Stored telemetry queries

```python
from datetime import UTC, datetime, timedelta

end = datetime.now(UTC)
start = end - timedelta(minutes=15)

trace = observability.queries().trace(trace_id, start=start, end=end).execute()
errors = (
    observability.queries()
    .logs(start=start, end=end, min_severity="ERROR")
    .page(limit=100)
    .execute()
)
```

Each helper exposes a released Query 1.0.0 logical plan and executes a normal
`evidence.query` Expression. Scope, capability, time bounds, page limits,
cursor validation, audit, and Adapter selection remain inside Meridian.
The three logical Resources can be supplied as `EvidenceResources` or rendered
through `MERIDIAN_OBSERVABILITY_LOGS_RESOURCE`,
`MERIDIAN_OBSERVABILITY_SPANS_RESOURCE`, and
`MERIDIAN_OBSERVABILITY_METRICS_RESOURCE`.

See [architecture](docs/architecture.md), [configuration](docs/configuration.md),
[compatibility](docs/compatibility.md), and [conformance](docs/conformance.md).

## Development

Start the digest-pinned ClickHouse service below before running the full suite.
The local credentials are only for this isolated test container.

```console
docker run -d --name observability-test -p 127.0.0.1:28123:8123 \
  -e CLICKHOUSE_USER=meridian -e CLICKHOUSE_PASSWORD=meridian-test \
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  clickhouse/clickhouse-server@sha256:0152dd511befe6a2c2ef53e930726179669b08116da78500b37c51c96ff5ee77
```

```console
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip==26.2
.venv/bin/python -m pip install --require-hashes -r requirements-validation.txt
.venv/bin/python -m pip install '.[test]'
.venv/bin/python -m pip check
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/python scripts/verify_contracts.py
.venv/bin/pytest
.venv/bin/python -m build --no-isolation
.venv/bin/python scripts/verify_artifacts.py dist
```

Stop the test service with `docker rm -f observability-test` after validation.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE).
