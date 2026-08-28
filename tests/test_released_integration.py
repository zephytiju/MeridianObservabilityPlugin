# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import ReadyMeridian
from meridian_storage import ResourceRef
from meridian_storage.adapters.clickhouse import (
    ClickHouseSettings,
    ColumnLayout,
    RecordProfile,
    ResourceLayout,
    Topology,
)
from meridian_storage.adapters.clickhouse.configuration import Endpoint
from meridian_storage.adapters.clickhouse.query import compile_simple_query
from meridian_storage.evidence import OTelEvidenceBridge
from meridian_storage.plugins.observability import TelemetryQueries
from meridian_storage.query import CursorSigner, TranslationContext

_FP = "sha256:" + "a" * 64
_START = datetime(2026, 8, 25, 12, tzinfo=UTC)


def test_released_evidence_bridge_preserves_otel_correlation() -> None:
    bridge = OTelEvidenceBridge(resource="evidence:telemetry.logs", capacity=2)
    accepted = bridge.log(
        severity="ERROR",
        body={"event": "failed"},
        event_time=_START,
        observed_time=_START,
        otel_resource={"service.name": "integration"},
        instrumentation_scope={"name": "tests"},
        attributes={"result": "failed"},
        trace_id="a" * 32,
        span_id="b" * 16,
    )
    assert accepted
    expression = bridge.drain()[0]
    assert expression.catalog == "evidence"
    assert expression.method == "append"
    assert expression.fingerprint == (
        "sha256:a5ffd99671853bfd8ba91d3bd18174b3270e1b04b8fd090c8f9d910d2869c0ea"
    )
    assert expression.arguments["idempotencyKey"] == (
        "sha256:d31cb7e5b15a3d4474155af9fe76ddb5e59b15ca4feed96fc6c61db4543d0bf1"
    )
    record = expression.arguments["data"]
    assert record["traceId"] == "a" * 32  # type: ignore[index]
    assert bridge.health().exported == 1
    with bridge.suppress_instrumentation():
        assert not bridge.log(
            severity="INFO",
            body="suppressed",
            event_time=_START,
            observed_time=_START,
            otel_resource={"service.name": "integration"},
            instrumentation_scope={"name": "tests"},
        )
    assert bridge.health().suppressed == 1


def test_generated_mapping_first_query_compiles_with_released_clickhouse_adapter(
    evidence_resources: object,
) -> None:
    query = (
        TelemetryQueries(ReadyMeridian(), evidence_resources)
        .logs(  # type: ignore[arg-type]
            start=_START,
            end=_START + timedelta(minutes=15),
            min_severity="ERROR",
            trace_id="c" * 32,
        )
        .selecting("evidenceId", "observedTime", "severity", "traceId")
        .page(limit=37)
    )
    assert query.expression.arguments["select"] == (
        "evidenceId",
        "observedTime",
        "severity",
        "traceId",
    )
    assert query.logical_plan.to_dict()["result"]["projection"] == [
        {"expression": {"kind": "field", "name": "evidenceId"}},
        {"expression": {"kind": "field", "name": "observedTime"}},
        {"expression": {"kind": "field", "name": "severity"}},
        {"expression": {"kind": "field", "name": "traceId"}},
    ]
    assert query.fingerprint == (
        "sha256:cdeee33e2c0a1cf69a65eb6c6d60605088227b76e5146c02d4fa98a943234554"
    )
    resource = ResourceRef("evidence", "telemetry", "logs")
    columns = (
        ColumnLayout("evidenceId", "evidence_id", "string", "String"),
        ColumnLayout("observedTime", "observed_at", "timestamp", "DateTime64(9, 'UTC')"),
        ColumnLayout("severity", "severity", "string", "String"),
        ColumnLayout("traceId", "trace_id", "string", "String"),
    )
    layout = ResourceLayout(
        resource=resource,
        table="telemetry_logs",
        record_profile=RecordProfile.LOG,
        schema_version="1.0.0",
        resource_fingerprint=_FP,
        schema_fingerprint=_FP,
        columns=columns,
        timestamp_field="observedTime",
        identity_fields=("evidenceId",),
        dimension_fields=("severity", "traceId"),
        topology=Topology.STANDALONE,
    )
    settings = ClickHouseSettings(
        database="telemetry",
        topology=Topology.STANDALONE,
        endpoint=Endpoint("clickhouse.internal", 8443, True),
        layouts={resource.canonical: layout},
        max_batch_rows=10_000,
        max_batch_bytes=16_777_216,
        max_time_range_seconds=86_400,
        retry_window_seconds=86_400,
        cursor_ttl_seconds=900,
        insert_quorum=1,
        required_functions=("count", "quantile", "sum"),
        operation_timeout_ms=30_000,
        max_result_bytes=16_777_216,
    )
    context = TranslationContext(
        binding_id="evidence-clickhouse",
        plan_fingerprint=_FP,
        registry_fingerprint=_FP,
        schema_fingerprints={resource.canonical: _FP},
        scope_fingerprint=_FP,
        deadline_ms=30_000,
    )
    compiled = compile_simple_query(
        "query",
        query.expression.arguments,
        layout,
        context,
        settings,
        CursorSigner({"test": b"x" * 32}, active_key_id="test"),
    )
    sql = compiled.command["sql"]  # type: ignore[index]
    assert "_meridian_scope_fingerprint" in sql
    assert "observed_at" in sql
    assert "c" * 32 not in sql
    assert "c" * 32 in compiled.parameters.values()
