# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from importlib.metadata import version

import pytest

from meridian_storage.plugins.observability import (
    BatchPolicy,
    CardinalityExceeded,
    DeploymentTelemetryConfig,
    EvidenceResources,
    InvalidInstrument,
    InvalidObservabilityConfiguration,
    InvalidResourceIdentity,
    OTLPProtocol,
    ServiceIdentity,
    Signal,
)
from meridian_storage.plugins.observability.errors import ObservabilityErrorCode, ShutdownFailed
from meridian_storage.plugins.observability.policy import (
    CardinalityGuard,
    InstrumentDefinition,
    sanitize_attributes,
    sanitize_body,
)


def test_service_identity_protects_required_resource_attributes() -> None:
    identity = ServiceIdentity("orders-api", "1.2.3", "prod", {"cloud.region": "us-west-2"})
    attributes = identity.resource_attributes()
    assert attributes["service.name"] == "orders-api"
    assert attributes["meridian.sdk.version"] == version("meridian-storage-core")
    assert attributes["cloud.region"] == "us-west-2"
    with pytest.raises(InvalidResourceIdentity):
        ServiceIdentity("orders", "1.0.0", "prod", {"service.name": "spoofed"})
    with pytest.raises(InvalidResourceIdentity):
        ServiceIdentity("orders", "1.0.0", "prod", {"db.statement": "select secret"})


def test_deployment_config_reads_standard_otel_environment() -> None:
    config = DeploymentTelemetryConfig.from_environment(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.internal:4318",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_LOGS_EXPORTER": "none",
            "OTEL_TRACES_SAMPLER_ARG": "0.25",
            "OTEL_METRIC_EXPORT_INTERVAL": "15000",
        }
    )
    assert config.protocol is OTLPProtocol.HTTP_PROTOBUF
    assert config.endpoint_for(Signal.TRACES).endswith("/v1/traces")
    assert Signal.LOGS not in config.signals
    assert config.trace_sample_ratio == 0.25


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "grpc://collector"},
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://user:secret@collector"},
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:invalid"},
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "invalid",
        },
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector", "OTEL_TRACES_SAMPLER_ARG": "x"},
    ],
)
def test_invalid_deployment_configuration_fails_closed(environment: dict[str, str]) -> None:
    with pytest.raises(InvalidObservabilityConfiguration):
        DeploymentTelemetryConfig.from_environment(environment)


def test_evidence_resources_are_logical_and_deployment_owned() -> None:
    resources = EvidenceResources.from_environment(
        {
            "MERIDIAN_OBSERVABILITY_LOGS_RESOURCE": "evidence:telemetry.logs",
            "MERIDIAN_OBSERVABILITY_SPANS_RESOURCE": "evidence:telemetry.spans",
            "MERIDIAN_OBSERVABILITY_METRICS_RESOURCE": "evidence:telemetry.metrics",
        }
    )
    assert resources.logs.canonical == "evidence:telemetry.logs"
    with pytest.raises(InvalidObservabilityConfiguration):
        EvidenceResources.from_environment({})
    with pytest.raises(InvalidObservabilityConfiguration):
        EvidenceResources.from_environment(
            {
                "MERIDIAN_OBSERVABILITY_LOGS_RESOURCE": "structured:telemetry.logs",
                "MERIDIAN_OBSERVABILITY_SPANS_RESOURCE": "evidence:telemetry.spans",
                "MERIDIAN_OBSERVABILITY_METRICS_RESOURCE": "evidence:telemetry.metrics",
            }
        )


def test_attribute_and_body_policy_denies_sensitive_payloads() -> None:
    assert sanitize_attributes({"result": "ok", "attempt": 2}) == {
        "attempt": 2,
        "result": "ok",
    }
    assert sanitize_body({"event": "accepted", "values": [1, 2]}) == {
        "event": "accepted",
        "values": [1, 2],
    }
    for denied in ("db.statement", "query_text", "authorization", "access_token"):
        with pytest.raises(InvalidInstrument):
            sanitize_attributes({denied: "secret"})
    with pytest.raises(InvalidInstrument):
        sanitize_body({"requestBody": "secret"})
    with pytest.raises(InvalidInstrument):
        sanitize_attributes({"values": [1, "mixed"]})
    with pytest.raises(InvalidInstrument):
        sanitize_attributes({"value": float("nan")})
    with pytest.raises(InvalidInstrument):
        sanitize_attributes({1: "invalid"})  # type: ignore[dict-item]
    nested: object = "value"
    for _ in range(9):
        nested = {"nested": nested}
    with pytest.raises(InvalidInstrument, match="nested"):
        sanitize_body(nested)


def test_cardinality_guard_is_deterministic_and_bounded() -> None:
    definition = InstrumentDefinition(
        "jobs.completed",
        allowed_attributes=("result",),
        cardinality_limit=2,
    )
    guard = CardinalityGuard(definition)
    guard.attributes({"result": "ok"})
    guard.attributes({"result": "ok"})
    guard.attributes({"result": "failed"})
    assert guard.series_count == 2
    with pytest.raises(CardinalityExceeded):
        guard.attributes({"result": "cancelled"})
    with pytest.raises(InvalidInstrument):
        guard.attributes({"undeclared": "value"})


def test_closed_configuration_values_and_safe_error_envelope() -> None:
    with pytest.raises(InvalidObservabilityConfiguration):
        BatchPolicy(max_queue_size=2, max_export_batch_size=3)
    with pytest.raises(InvalidObservabilityConfiguration):
        DeploymentTelemetryConfig("http://collector", signals=frozenset())
    with pytest.raises(InvalidResourceIdentity):
        ServiceIdentity("service", "bad\nversion", "prod")
    with pytest.raises(InvalidInstrument):
        InstrumentDefinition("metric", allowed_attributes=("result", "result"))
    error = ShutdownFailed()
    assert error.to_dict() == {
        "code": ObservabilityErrorCode.SHUTDOWN_FAILED.value,
        "message": "one or more OpenTelemetry providers did not shut down cleanly",
    }
