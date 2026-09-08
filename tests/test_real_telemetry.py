# SPDX-License-Identifier: Apache-2.0
"""Required installed-package telemetry regression against a real ClickHouse server."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import clickhouse_connect
import pytest

from conftest import operation_result
from meridian_storage import Operation, OperationContext, ResourceRef
from meridian_storage.adapters.clickhouse import (
    ClickHouseAdapterFactory,
    ClickHouseMigrator,
    ClickHouseSchemaCompiler,
    ClickHouseSettings,
    capability_manifest,
    plan_initial_migration,
)
from meridian_storage.plugins.observability import EvidenceResources, TelemetryQueries
from meridian_storage.runtime import BindingConfig
from meridian_storage.semantics import (
    PROFILE_EXTENSION_KEY,
    CatalogName,
    FieldDefinition,
    LogicalKind,
    LogicalType,
    SchemaDocument,
    SchemaReference,
    SemanticKind,
    TimeSeriesProfile,
    canonical_json_bytes,
)
from meridian_storage.spi import AdapterCreateContext, ExecutionRequest, SecretValue

_FP = "sha256:" + "1" * 64
_START = datetime(2026, 9, 7, tzinfo=UTC)
_DATABASE = "observability_conformance"


@pytest.fixture(scope="module")
def real_client() -> Iterator[Any]:
    client = clickhouse_connect.get_client(
        host="127.0.0.1",
        port=int(os.environ.get("CLICKHOUSE_PORT", "28123")),
        username="meridian",
        password="meridian-test",  # noqa: S106 - isolated test service only
    )
    client.command(f"CREATE DATABASE IF NOT EXISTS {_DATABASE}")
    observed = client.command("SELECT version()")
    assert client.command("SELECT timezone()") == "UTC"
    evidence = Path("build/evidence")
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "engine.json").write_text(
        json.dumps(
            {
                "selectedRelease": os.environ.get("CLICKHOUSE_SELECTED_RELEASE"),
                "selectedImage": os.environ.get("CLICKHOUSE_IMAGE"),
                "observedRelease": observed,
            },
            sort_keys=True,
        )
        + "\n"
    )
    try:
        yield client
    finally:
        client.command(f"DROP DATABASE {_DATABASE} SYNC")
        client.close()


def _binding(layout: Any) -> BindingConfig:
    raw = {
        "id": "telemetry",
        "adapterId": "meridian.storage.clickhouse",
        "adapterContract": "1.0.0",
        "engineProfile": "clickhouse-standalone",
        "engineVersion": os.environ.get("CLICKHOUSE_SELECTED_RELEASE", "25.8"),
        "endpoint": "http://127.0.0.1:" + os.environ.get("CLICKHOUSE_PORT", "28123"),
        "serviceRef": None,
        "physicalNamespace": _DATABASE,
        "tls": {
            "mode": "disabled",
            "serverName": None,
            "caRef": None,
            "clientCertificateRef": None,
        },
        "identityRef": {"provider": "test", "reference": "username"},
        "secretRef": {"provider": "test", "reference": "password"},
        "client": {
            "minSize": 0,
            "maxSize": 4,
            "acquireTimeoutMs": 5000,
            "idleTimeoutMs": 60000,
            "operationTimeoutMs": 30000,
            "maxResultBytes": 4194304,
            "iteratorLifetimeMs": 30000,
        },
        "requiredCapabilityFingerprint": _FP,
        "requiredPhysicalFingerprint": None,
        "compatibilityPins": {},
        "extensions": {},
        "settings": {
            "layouts": [layout.to_dict()],
            "maxBatchRows": 100,
            "maxBatchBytes": 1048576,
            "maxTimeRangeSeconds": 86400,
            "retryWindowSeconds": 86400,
            "cursorTtlSeconds": 900,
            "insertQuorum": 1,
            "requiredFunctions": ["count", "quantile", "sum"],
        },
    }
    initial = BindingConfig.from_mapping(raw, "bindings[0]")
    raw["requiredCapabilityFingerprint"] = capability_manifest(
        ClickHouseSettings.from_binding(initial), initial.engine_version
    ).fingerprint
    return BindingConfig.from_mapping(raw, "bindings[0]")


def _request(resource: ResourceRef, method: str, arguments: Any) -> ExecutionRequest:
    return ExecutionRequest(
        Operation(
            "evidence",
            "meridian.evidence." + method,
            "1.0.0",
            (resource,),
            arguments,
            read_only=method == "query",
            idempotent=True,
        ),
        OperationContext(
            "principal:test",
            request_id="telemetry",
            tenant="tenant-a",
            scope={"suite": "observability"},
            idempotency_key=resource.name,
        ),
        "telemetry",
        "execution",
        "telemetry",
        1,
        _FP,
        1,
    )


@pytest.mark.integration
@pytest.mark.parametrize("profile", ["log", "span", "metric"])
def test_exported_telemetry_queries_real_engine(
    real_client: Any,
    observability: Any,
    profile: str,
) -> None:
    plugin, spans, metrics, logs = observability
    with plugin.tracer("real-engine").start_as_current_span("checkout"):
        plugin.logger("real-engine").info("accepted", {"result": "accepted"})
        plugin.meter("real-engine").create_counter("requests").add(1)
    assert all(plugin.force_flush())
    exported_span = spans.get_finished_spans()[0]
    exported_log = logs.get_finished_logs()[0].log_record
    assert exported_log.trace_id == exported_span.context.trace_id
    assert metrics.exports
    payload = {
        "body": exported_log.body,
        "resource": dict(logs.get_finished_logs()[0].resource.attributes),
        "scope": {"name": logs.get_finished_logs()[0].instrumentation_scope.name},
        "metric": json.loads(metrics.exports[-1].to_json()),
        "span": json.loads(exported_span.to_json()),
        "traceId": f"{exported_span.context.trace_id:032x}",
        "spanId": f"{exported_span.context.span_id:016x}",
        "attributes": dict(exported_log.attributes),
    }
    resource = ResourceRef("evidence", "telemetry", profile)
    kinds = {
        "evidenceId": LogicalKind.STRING,
        "observedTime": LogicalKind.UTC_TIMESTAMP,
        "startTime": LogicalKind.UTC_TIMESTAMP,
        "endTime": LogicalKind.UTC_TIMESTAMP,
        "traceId": LogicalKind.STRING,
        "severity": LogicalKind.STRING,
        "name": LogicalKind.STRING,
        "value": LogicalKind.FLOAT64,
        "payload": LogicalKind.JSON,
    }
    schema = SchemaDocument(
        ref=SchemaReference(CatalogName("evidence"), "telemetry", profile, "1.0.0"),
        semantic_kind=SemanticKind.TIME_SERIES,
        fields=tuple(FieldDefinition(n, LogicalType(k)) for n, k in kinds.items()),
        identity=("evidenceId",),
        consistency="eventual",
        extensions={
            PROFILE_EXTENSION_KEY: TimeSeriesProfile(
                timestamp_field="observedTime",
                series_identity=("evidenceId",),
                dimensions=("traceId", "severity", "name"),
                measurements=("value",),
            ).to_dict()
        },
    )
    compilation = ClickHouseSchemaCompiler().compile(
        database=_DATABASE,
        resource=resource,
        resource_fingerprint=_FP,
        schema=schema,
        record_profile=profile,
    )
    binding = _binding(compilation.layout)
    ClickHouseMigrator(real_client, ClickHouseSettings.from_binding(binding)).apply(
        plan_initial_migration("telemetry-" + profile, (compilation,))
    )
    runtime = ClickHouseAdapterFactory().create(
        AdapterCreateContext(
            binding,
            SecretValue(b"meridian"),
            SecretValue(b"meridian-test"),
        )
    )
    runtime.open()
    session = runtime.open_session(transactional=False)
    try:
        records = [
            {
                "evidenceId": str(i),
                "observedTime": (_START + timedelta(seconds=i)).isoformat(),
                "startTime": (_START + timedelta(seconds=i)).isoformat(),
                "endTime": (_START + timedelta(seconds=i)).isoformat(),
                "traceId": payload["traceId"],
                "severity": "INFO",
                "name": "requests",
                "value": float(i),
                "payload": payload,
            }
            for i in range(3)
        ]
        # Exercise the FixedString cursor boundary deterministically: raw hexadecimal
        # values beginning with f sort after their Base64 representation. Arbitrary
        # legal payloads must not cause the last row to repeat on the next page.
        for nonce in range(256):
            records[1]["payload"] = {**payload, "cursorBoundaryCase": nonce}
            if hashlib.sha256(canonical_json_bytes(records[1])).hexdigest().startswith("f"):
                break
        else:
            pytest.fail("could not construct the deterministic cursor boundary fixture")
        append = _request(resource, "append", {"records": records})
        first = session.execute(append)
        assert session.execute(append).data["batchId"] == first.data["batchId"]

        class Executor:
            def execute(self, expression: Any) -> Any:
                result = session.execute(_request(resource, "query", expression.arguments))
                return replace(
                    operation_result(result.data),
                    resources=(resource,),
                    provenance=dict(result.provenance),
                )

        queries = TelemetryQueries(Executor(), EvidenceResources(resource, resource, resource))
        bounds = {"start": _START, "end": _START + timedelta(minutes=1)}
        query = (
            queries.logs(**bounds, min_severity="INFO", trace_id=payload["traceId"])
            if profile == "log"
            else queries.spans(**bounds, trace_id=payload["traceId"])
            if profile == "span"
            else queries.metric_series("requests", **bounds)
        )
        first_page = query.page(limit=2).execute()
        assert len(first_page.items) == 2
        assert first_page.cursor
        second_page = query.page(limit=2, cursor=first_page.cursor).execute()
        assert len(second_page.items) == 1, first_page.cursor
        assert {r["evidenceId"] for r in (*first_page.items, *second_page.items)} == {"0", "1", "2"}
        assert first_page.items[0]["payload"] == payload
        assert query.page(limit=3).delta().execute() == 2.0
        isolated = _request(resource, "query", query.expression.arguments)
        isolated = replace(isolated, context=replace(isolated.context, tenant="another-tenant"))
        assert not session.execute(isolated).data["items"]
    finally:
        session.close()
        runtime.close()
