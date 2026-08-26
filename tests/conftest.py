# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult, MetricsData
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from meridian_storage import Meridian, OperationResult, ResourceRef, RuntimeState, StartupReport
from meridian_storage.plugins.observability import (
    BatchPolicy,
    DeploymentTelemetryConfig,
    EvidenceResources,
    Observability,
)

_FINGERPRINT = "sha256:" + "1" * 64


def operation_result(data: Any) -> OperationResult:
    resource = ResourceRef("evidence", "telemetry", "logs")
    return OperationResult(
        data=data,
        catalog="evidence",
        operation_contract="meridian.evidence.query",
        operation_version="1.0.0",
        resources=(resource,),
        request_id="request-1",
        execution_id="execution-1",
        operation_fingerprint=_FINGERPRINT,
        registry_fingerprint=_FINGERPRINT,
        capability_fingerprint=_FINGERPRINT,
        provenance={"adapter": "released-test-double"},
    )


class ReadyMeridian(Meridian):
    def __init__(self, result: OperationResult | None = None) -> None:
        self.result = result or operation_result({"items": []})
        self.expressions: list[Any] = []

    @property
    def state(self) -> RuntimeState:
        return RuntimeState.READY

    @property
    def startup_report(self) -> StartupReport:
        now = datetime(2026, 8, 26, tzinfo=UTC)
        return StartupReport(
            profile="test",
            config_fingerprint=_FINGERPRINT,
            registry_revision=1,
            registry_fingerprint=_FINGERPRINT,
            started_at=now,
            ready_at=now,
            bindings=(),
            adapters=(),
            catalogs=("evidence",),
            schema_providers=(),
            resources=(),
            plugins=("observability",),
            evidence=(),
        )

    def execute(self, expression: Any) -> OperationResult:
        self.expressions.append(expression)
        return self.result


class InMemoryMetricExporter(MetricExporter):
    def __init__(self) -> None:
        super().__init__()
        self.exports: list[MetricsData] = []
        self.stopped = False

    def export(
        self,
        metrics_data: MetricsData,
        _timeout_millis: float = 10_000,
        **_kwargs: Any,
    ) -> MetricExportResult:
        self.exports.append(metrics_data)
        return MetricExportResult.SUCCESS

    def force_flush(self, _timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, _timeout_millis: float = 30_000, **_kwargs: Any) -> None:
        self.stopped = True


@pytest.fixture
def ready_meridian() -> ReadyMeridian:
    return ReadyMeridian()


@pytest.fixture
def evidence_resources() -> EvidenceResources:
    return EvidenceResources(
        ResourceRef("evidence", "telemetry", "logs"),
        ResourceRef("evidence", "telemetry", "spans"),
        ResourceRef("evidence", "telemetry", "metrics"),
    )


@pytest.fixture
def observability(
    ready_meridian: ReadyMeridian,
    evidence_resources: EvidenceResources,
) -> Iterator[
    tuple[Observability, InMemorySpanExporter, InMemoryMetricExporter, InMemoryLogRecordExporter]
]:
    spans = InMemorySpanExporter()
    metrics = InMemoryMetricExporter()
    logs = InMemoryLogRecordExporter()
    plugin = Observability(
        ready_meridian,
        service_name="test-service",
        service_version="1.0.0",
        deployment_environment="test",
        deployment=DeploymentTelemetryConfig(
            "http://collector.invalid:4317",
            batch=BatchPolicy(schedule_delay_millis=300_000),
        ),
        evidence_resources=evidence_resources,
        _span_exporter=spans,
        _metric_exporter=metrics,
        _log_exporter=logs,
    )
    yield plugin, spans, metrics, logs
    plugin.close()
