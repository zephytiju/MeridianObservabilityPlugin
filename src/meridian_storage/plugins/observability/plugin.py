# SPDX-License-Identifier: Apache-2.0
"""Meridian V1 plugin factory and service-facing composition facade."""

from __future__ import annotations

import os
from collections.abc import Mapping
from threading import Lock

from opentelemetry.sdk._logs.export import LogRecordExporter
from opentelemetry.sdk.metrics.export import MetricExporter
from opentelemetry.sdk.trace.export import SpanExporter

from meridian_storage import Meridian, RuntimeState
from meridian_storage.spi import PluginManifest

from ._version import __version__
from .config import (
    DeploymentTelemetryConfig,
    EvidenceResources,
    ServiceIdentity,
)
from .context import ContextPolicy
from .errors import InvalidObservabilityConfiguration, RuntimeNotReady, ShutdownFailed
from .instruments import (
    DatabasePack,
    HttpServerPack,
    MessagingPack,
    SafeMeter,
    SafeTracer,
    StructuredLogger,
)
from .providers import (
    ShutdownReport,
    build_provider_bundle,
    install_global_provider_bundle,
)
from .query import TelemetryQueries


class Observability:
    """Governed OpenTelemetry instrumentation and Evidence query entry point."""

    def __init__(
        self,
        meridian: Meridian,
        *,
        service_name: str,
        service_version: str,
        deployment_environment: str | None = None,
        resource_attributes: Mapping[str, str | bool | int | float] | None = None,
        deployment: DeploymentTelemetryConfig | None = None,
        evidence_resources: EvidenceResources | None = None,
        context_policy: ContextPolicy | None = None,
        _span_exporter: SpanExporter | None = None,
        _metric_exporter: MetricExporter | None = None,
        _log_exporter: LogRecordExporter | None = None,
    ) -> None:
        if not isinstance(meridian, Meridian) or meridian.state is not RuntimeState.READY:
            raise RuntimeNotReady
        startup_report = meridian.startup_report
        if startup_report is None:
            raise RuntimeNotReady
        selected_environment = (
            deployment_environment
            if deployment_environment is not None
            else os.environ.get("MERIDIAN_DEPLOYMENT_ENVIRONMENT", "")
        )
        identity = ServiceIdentity(
            service_name,
            service_version,
            selected_environment,
            resource_attributes or {},
        )
        selected_deployment = deployment or DeploymentTelemetryConfig.from_environment()
        self._meridian = meridian
        self._bundle = build_provider_bundle(
            identity,
            selected_deployment,
            span_exporter=_span_exporter,
            metric_exporter=_metric_exporter,
            log_exporter=_log_exporter,
            runtime_profile=startup_report.profile,
            runtime_config_fingerprint=startup_report.config_fingerprint,
        )
        self._evidence_resources = evidence_resources
        self._context_policy = context_policy or ContextPolicy()
        self._global = False
        self._meters: dict[tuple[str, str | None], SafeMeter] = {}
        self._meter_lock = Lock()

    @property
    def service_identity(self) -> ServiceIdentity:
        return self._bundle.identity

    @property
    def is_global(self) -> bool:
        return self._global

    def install_global_otel_provider(self) -> Observability:
        self._bundle = install_global_provider_bundle(self._bundle)
        self._global = True
        return self

    def tracer(self, name: str, version: str | None = None) -> SafeTracer:
        return SafeTracer(
            self._bundle.tracer_provider.get_tracer(name, version),
            self._context_policy,
        )

    def meter(self, name: str, version: str | None = None) -> SafeMeter:
        key = (name, version)
        with self._meter_lock:
            if key not in self._meters:
                self._meters[key] = SafeMeter(
                    self._bundle.meter_provider.get_meter(name, version),
                    self._context_policy,
                )
            return self._meters[key]

    def logger(self, name: str, version: str | None = None) -> StructuredLogger:
        return StructuredLogger(
            self._bundle.logger_provider.get_logger(name, version),
            self._context_policy,
        )

    def http_server(self, name: str, version: str | None = None) -> HttpServerPack:
        return HttpServerPack.create(self.meter(name, version))

    def messaging(self, name: str, version: str | None = None) -> MessagingPack:
        return MessagingPack.create(self.meter(name, version))

    def database(self, name: str, version: str | None = None) -> DatabasePack:
        return DatabasePack.create(self.meter(name, version))

    def queries(self, resources: EvidenceResources | None = None) -> TelemetryQueries:
        selected = resources or self._evidence_resources
        if selected is None:
            raise InvalidObservabilityConfiguration(
                "logical Evidence Resources are required for stored telemetry queries"
            )
        return TelemetryQueries(self._meridian, selected)

    def force_flush(self, timeout_millis: int = 30_000) -> tuple[bool, bool, bool]:
        return self._bundle.force_flush(timeout_millis)

    def close(self, timeout_millis: int = 30_000, *, require_clean: bool = False) -> ShutdownReport:
        report = self._bundle.shutdown(timeout_millis)
        if require_clean and not report.clean:
            raise ShutdownFailed
        return report


class ObservabilityPluginFactory:
    @property
    def plugin_id(self) -> str:
        return "observability"

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.plugin_id,
            plugin_version=__version__,
            plugin_contract_version="1.0.0",
            core_contract="1.x",
            extensions={
                "distribution": "meridian-storage-plugin-observability",
                "otel": "1.44.0",
                "catalog": "evidence",
            },
        )

    def create(self, meridian: Meridian) -> Observability:
        service_name = os.environ.get("OTEL_SERVICE_NAME", "")
        service_version = os.environ.get("MERIDIAN_SERVICE_VERSION", "")
        deployment_environment = os.environ.get("MERIDIAN_DEPLOYMENT_ENVIRONMENT", "")
        if not service_name or not service_version or not deployment_environment:
            raise InvalidObservabilityConfiguration(
                "plugin discovery requires deployment-rendered service identity variables"
            )
        resources: EvidenceResources | None
        try:
            resources = EvidenceResources.from_environment()
        except InvalidObservabilityConfiguration:
            resources = None
        return Observability(
            meridian,
            service_name=service_name,
            service_version=service_version,
            deployment_environment=deployment_environment,
            evidence_resources=resources,
        ).install_global_otel_provider()


__all__ = ["Observability", "ObservabilityPluginFactory"]
