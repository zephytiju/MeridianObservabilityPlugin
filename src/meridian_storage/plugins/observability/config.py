# SPDX-License-Identifier: Apache-2.0
"""Deployment-owned configuration and immutable service identity."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import urlsplit

from meridian_storage import ResourceRef
from meridian_storage import __version__ as meridian_version

from .errors import InvalidObservabilityConfiguration, InvalidResourceIdentity
from .policy import is_denied_key

_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,254}$")
_ATTRIBUTE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_PROTECTED_ATTRIBUTES = frozenset(
    {
        "service.name",
        "service.version",
        "deployment.environment.name",
        "telemetry.sdk.name",
        "telemetry.sdk.language",
        "telemetry.sdk.version",
        "meridian.sdk.version",
    }
)


class OTLPProtocol(StrEnum):
    GRPC = "grpc"
    HTTP_PROTOBUF = "http/protobuf"


class Signal(StrEnum):
    TRACES = "traces"
    METRICS = "metrics"
    LOGS = "logs"


def _bounded_name(value: object, name: str) -> str:
    if not isinstance(value, str) or _SAFE_NAME.fullmatch(value) is None:
        raise InvalidResourceIdentity(f"{name} must be a bounded logical name")
    return value


def _bounded_value(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise InvalidResourceIdentity(f"{name} must be a bounded printable string")
    return value


def _positive_int(value: object, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise InvalidObservabilityConfiguration(f"{name} must be between 1 and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    name: str
    version: str
    deployment_environment: str
    attributes: Mapping[str, str | bool | int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _bounded_name(self.name, "service_name"))
        object.__setattr__(self, "version", _bounded_value(self.version, "service_version"))
        object.__setattr__(
            self,
            "deployment_environment",
            _bounded_name(self.deployment_environment, "deployment_environment"),
        )
        if len(self.attributes) > 64:
            raise InvalidResourceIdentity("resource attributes may contain at most 64 entries")
        normalized: dict[str, str | bool | int | float] = {}
        for key, value in self.attributes.items():
            if not isinstance(key, str) or _ATTRIBUTE_KEY.fullmatch(key) is None:
                raise InvalidResourceIdentity("resource attributes contain an invalid key")
            if is_denied_key(key):
                raise InvalidResourceIdentity(f"resource attribute {key!r} is denied by policy")
            if key in _PROTECTED_ATTRIBUTES:
                raise InvalidResourceIdentity(f"resource attribute {key!r} is protected")
            if not isinstance(value, str | bool | int | float):
                raise InvalidResourceIdentity("resource attribute values must be scalar")
            if isinstance(value, str) and len(value.encode()) > 1024:
                raise InvalidResourceIdentity(
                    "resource attribute strings are limited to 1024 bytes"
                )
            normalized[key] = value
        object.__setattr__(self, "attributes", MappingProxyType(dict(sorted(normalized.items()))))

    def resource_attributes(self) -> dict[str, str | bool | int | float]:
        return {
            **self.attributes,
            "service.name": self.name,
            "service.version": self.version,
            "deployment.environment.name": self.deployment_environment,
            "meridian.sdk.version": meridian_version,
        }


@dataclass(frozen=True, slots=True)
class BatchPolicy:
    max_queue_size: int = 2048
    max_export_batch_size: int = 512
    schedule_delay_millis: int = 5000
    export_timeout_millis: int = 30_000

    def __post_init__(self) -> None:
        queue = _positive_int(self.max_queue_size, "max_queue_size", 1_000_000)
        batch = _positive_int(self.max_export_batch_size, "max_export_batch_size", queue)
        if batch > queue:
            raise InvalidObservabilityConfiguration(
                "max_export_batch_size must not exceed max_queue_size"
            )
        _positive_int(self.schedule_delay_millis, "schedule_delay_millis", 300_000)
        _positive_int(self.export_timeout_millis, "export_timeout_millis", 300_000)


@dataclass(frozen=True, slots=True)
class DeploymentTelemetryConfig:
    """Configuration rendered by deployment/IaC; never a consumer backend handle."""

    endpoint: str
    protocol: OTLPProtocol = OTLPProtocol.GRPC
    signals: frozenset[Signal] = frozenset(Signal)
    batch: BatchPolicy = field(default_factory=BatchPolicy)
    trace_sample_ratio: float = 1.0
    metric_export_interval_millis: int = 60_000

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, str) or not self.endpoint or len(self.endpoint) > 2048:
            raise InvalidObservabilityConfiguration("a bounded Collector endpoint is required")
        try:
            parsed_endpoint = urlsplit(self.endpoint)
            hostname = parsed_endpoint.hostname
            # Accessing ``port`` performs urllib's range and syntax validation.
            _ = parsed_endpoint.port
        except ValueError as exc:
            raise InvalidObservabilityConfiguration(
                "Collector endpoint must be a valid bounded URL"
            ) from exc
        if parsed_endpoint.scheme not in {"http", "https"} or hostname is None:
            raise InvalidObservabilityConfiguration("Collector endpoint must use http or https")
        if (
            parsed_endpoint.username is not None
            or parsed_endpoint.password is not None
            or parsed_endpoint.query
            or parsed_endpoint.fragment
        ):
            raise InvalidObservabilityConfiguration(
                "Collector endpoint must not contain credentials, query parameters, or fragments"
            )
        try:
            object.__setattr__(self, "protocol", OTLPProtocol(self.protocol))
            object.__setattr__(self, "signals", frozenset(Signal(item) for item in self.signals))
        except ValueError as exc:
            raise InvalidObservabilityConfiguration("invalid OTLP protocol or signal") from exc
        if not self.signals:
            raise InvalidObservabilityConfiguration("at least one telemetry signal is required")
        if (
            isinstance(self.trace_sample_ratio, bool)
            or not isinstance(self.trace_sample_ratio, int | float)
            or not 0 <= self.trace_sample_ratio <= 1
        ):
            raise InvalidObservabilityConfiguration("trace_sample_ratio must be between 0 and 1")
        _positive_int(
            self.metric_export_interval_millis,
            "metric_export_interval_millis",
            3_600_000,
        )

    def endpoint_for(self, signal: Signal) -> str:
        suffix = {Signal.TRACES: "v1/traces", Signal.METRICS: "v1/metrics", Signal.LOGS: "v1/logs"}
        if self.protocol is OTLPProtocol.HTTP_PROTOBUF:
            return f"{self.endpoint.rstrip('/')}/{suffix[signal]}"
        return self.endpoint

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> DeploymentTelemetryConfig:
        values = os.environ if environment is None else environment
        endpoint = values.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        protocol_value = values.get("OTEL_EXPORTER_OTLP_PROTOCOL", OTLPProtocol.GRPC.value)
        disabled = {
            signal
            for signal in Signal
            if values.get(f"OTEL_{signal.value.upper()}_EXPORTER", "otlp").lower() == "none"
        }
        try:
            ratio = float(values.get("OTEL_TRACES_SAMPLER_ARG", "1.0"))
            interval = int(values.get("OTEL_METRIC_EXPORT_INTERVAL", "60000"))
        except ValueError as exc:
            raise InvalidObservabilityConfiguration(
                "deployment telemetry numeric settings are invalid"
            ) from exc
        try:
            protocol = OTLPProtocol(protocol_value)
        except ValueError as exc:
            raise InvalidObservabilityConfiguration("invalid OTLP protocol or signal") from exc
        return cls(
            endpoint=endpoint,
            protocol=protocol,
            signals=frozenset(set(Signal) - disabled),
            trace_sample_ratio=ratio,
            metric_export_interval_millis=interval,
        )


@dataclass(frozen=True, slots=True)
class EvidenceResources:
    logs: ResourceRef
    spans: ResourceRef
    metrics: ResourceRef

    def __post_init__(self) -> None:
        for name in ("logs", "spans", "metrics"):
            value = getattr(self, name)
            try:
                parsed = ResourceRef.parse(value, catalog="evidence")
            except (TypeError, ValueError) as exc:
                raise InvalidObservabilityConfiguration(
                    f"{name} must be a logical evidence Resource reference"
                ) from exc
            object.__setattr__(self, name, parsed)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> EvidenceResources:
        values = os.environ if environment is None else environment
        required = {
            "logs": "MERIDIAN_OBSERVABILITY_LOGS_RESOURCE",
            "spans": "MERIDIAN_OBSERVABILITY_SPANS_RESOURCE",
            "metrics": "MERIDIAN_OBSERVABILITY_METRICS_RESOURCE",
        }
        missing = [variable for variable in required.values() if not values.get(variable)]
        if missing:
            raise InvalidObservabilityConfiguration(
                "deployment must provide logical Evidence Resources for stored telemetry queries"
            )
        try:
            parsed = {
                name: ResourceRef.parse(values[variable], catalog="evidence")
                for name, variable in required.items()
            }
        except (TypeError, ValueError) as exc:
            raise InvalidObservabilityConfiguration(
                "stored telemetry resources must be logical Evidence Resource references"
            ) from exc
        return cls(parsed["logs"], parsed["spans"], parsed["metrics"])


__all__ = [
    "BatchPolicy",
    "DeploymentTelemetryConfig",
    "EvidenceResources",
    "OTLPProtocol",
    "ServiceIdentity",
    "Signal",
]
