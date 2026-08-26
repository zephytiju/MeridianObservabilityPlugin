# SPDX-License-Identifier: Apache-2.0
"""Create isolated SDK providers that export only to the deployment Collector."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import cast

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter as GrpcLogExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter as GrpcMetricExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter as HttpLogExporter,
)
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter as HttpMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpSpanExporter,
)
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogRecordExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from ..config import DeploymentTelemetryConfig, OTLPProtocol, ServiceIdentity, Signal
from ..errors import InvalidObservabilityConfiguration
from .suppression import (
    SuppressedLogExporter,
    SuppressedMetricExporter,
    SuppressedSpanExporter,
    suppress_instrumentation,
)


@dataclass(frozen=True, slots=True)
class ShutdownReport:
    traces_flushed: bool
    metrics_flushed: bool
    logs_flushed: bool
    traces_shutdown: bool
    metrics_shutdown: bool
    logs_shutdown: bool
    duration_millis: int

    @property
    def clean(self) -> bool:
        return all(
            (
                self.traces_flushed,
                self.metrics_flushed,
                self.logs_flushed,
                self.traces_shutdown,
                self.metrics_shutdown,
                self.logs_shutdown,
            )
        )

    def to_dict(self) -> dict[str, bool | int]:
        return {
            "tracesFlushed": self.traces_flushed,
            "metricsFlushed": self.metrics_flushed,
            "logsFlushed": self.logs_flushed,
            "tracesShutdown": self.traces_shutdown,
            "metricsShutdown": self.metrics_shutdown,
            "logsShutdown": self.logs_shutdown,
            "durationMillis": self.duration_millis,
            "clean": self.clean,
        }


@dataclass(slots=True)
class ProviderBundle:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider
    config: DeploymentTelemetryConfig
    identity: ServiceIdentity
    runtime_profile: str = ""
    runtime_config_fingerprint: str = ""
    _shutdown_report: ShutdownReport | None = None

    @staticmethod
    def _timeout(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 300_000:
            raise InvalidObservabilityConfiguration(
                "provider timeout_millis must be between 1 and 300000"
            )
        return value

    @staticmethod
    def _safe_bool(call: Callable[[], object]) -> bool:
        try:
            return bool(call())
        except Exception:
            return False

    @staticmethod
    def _safe_shutdown(call: Callable[[], object]) -> bool:
        try:
            call()
        except Exception:
            return False
        return True

    def force_flush(self, timeout_millis: int = 30_000) -> tuple[bool, bool, bool]:
        timeout = self._timeout(timeout_millis)
        if self._shutdown_report is not None:
            return (
                self._shutdown_report.traces_flushed,
                self._shutdown_report.metrics_flushed,
                self._shutdown_report.logs_flushed,
            )
        with suppress_instrumentation():
            traces = self._safe_bool(lambda: self.tracer_provider.force_flush(timeout))
            metrics = self._safe_bool(lambda: self.meter_provider.force_flush(timeout))
            logs = self._safe_bool(lambda: self.logger_provider.force_flush(timeout))
        return traces, metrics, logs

    def shutdown(self, timeout_millis: int = 30_000) -> ShutdownReport:
        timeout = self._timeout(timeout_millis)
        if self._shutdown_report is not None:
            return self._shutdown_report
        started = monotonic()
        traces, metrics, logs = self.force_flush(timeout)
        with suppress_instrumentation():
            logs_shutdown = self._safe_shutdown(self.logger_provider.shutdown)
            metrics_shutdown = self._safe_shutdown(lambda: self.meter_provider.shutdown(timeout))
            traces_shutdown = self._safe_shutdown(self.tracer_provider.shutdown)
        self._shutdown_report = ShutdownReport(
            traces_flushed=traces,
            metrics_flushed=metrics,
            logs_flushed=logs,
            traces_shutdown=traces_shutdown,
            metrics_shutdown=metrics_shutdown,
            logs_shutdown=logs_shutdown,
            duration_millis=max(0, round((monotonic() - started) * 1000)),
        )
        return self._shutdown_report


def _default_span_exporter(config: DeploymentTelemetryConfig) -> SpanExporter:
    timeout = config.batch.export_timeout_millis / 1000
    if config.protocol is OTLPProtocol.GRPC:
        return GrpcSpanExporter(endpoint=config.endpoint_for(Signal.TRACES), timeout=timeout)
    return HttpSpanExporter(endpoint=config.endpoint_for(Signal.TRACES), timeout=timeout)


def _default_metric_exporter(config: DeploymentTelemetryConfig) -> MetricExporter:
    timeout = config.batch.export_timeout_millis / 1000
    if config.protocol is OTLPProtocol.GRPC:
        return GrpcMetricExporter(endpoint=config.endpoint_for(Signal.METRICS), timeout=timeout)
    return HttpMetricExporter(endpoint=config.endpoint_for(Signal.METRICS), timeout=timeout)


def _default_log_exporter(config: DeploymentTelemetryConfig) -> LogRecordExporter:
    timeout = config.batch.export_timeout_millis / 1000
    if config.protocol is OTLPProtocol.GRPC:
        return GrpcLogExporter(endpoint=config.endpoint_for(Signal.LOGS), timeout=timeout)
    return HttpLogExporter(
        endpoint=config.endpoint_for(Signal.LOGS),
        timeout=timeout,
    )


def build_provider_bundle(
    identity: ServiceIdentity,
    config: DeploymentTelemetryConfig,
    *,
    span_exporter: SpanExporter | None = None,
    metric_exporter: MetricExporter | None = None,
    log_exporter: LogRecordExporter | None = None,
    runtime_profile: str = "",
    runtime_config_fingerprint: str = "",
) -> ProviderBundle:
    """Build providers; exporter injection exists for isolated tests and embedders."""

    if Signal.TRACES in config.signals and span_exporter is None:
        span_exporter = _default_span_exporter(config)
    if Signal.METRICS in config.signals and metric_exporter is None:
        metric_exporter = _default_metric_exporter(config)
    if Signal.LOGS in config.signals and log_exporter is None:
        log_exporter = _default_log_exporter(config)
    resource = Resource.create(identity.resource_attributes())
    tracer_provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(float(config.trace_sample_ratio))),
        shutdown_on_exit=False,
    )
    meter_readers: list[PeriodicExportingMetricReader] = []
    if Signal.METRICS in config.signals:
        meter_readers.append(
            PeriodicExportingMetricReader(
                SuppressedMetricExporter(cast(MetricExporter, metric_exporter)),
                export_interval_millis=config.metric_export_interval_millis,
                export_timeout_millis=config.batch.export_timeout_millis,
            )
        )
    meter_provider = MeterProvider(
        metric_readers=meter_readers,
        resource=resource,
        shutdown_on_exit=False,
    )
    logger_provider = LoggerProvider(resource=resource, shutdown_on_exit=False)
    batch = config.batch
    if Signal.TRACES in config.signals:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                SuppressedSpanExporter(cast(SpanExporter, span_exporter)),
                max_queue_size=batch.max_queue_size,
                max_export_batch_size=batch.max_export_batch_size,
                schedule_delay_millis=batch.schedule_delay_millis,
                export_timeout_millis=batch.export_timeout_millis,
                meter_provider=meter_provider,
            )
        )
    if Signal.LOGS in config.signals:
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                SuppressedLogExporter(cast(LogRecordExporter, log_exporter)),
                max_queue_size=batch.max_queue_size,
                max_export_batch_size=batch.max_export_batch_size,
                schedule_delay_millis=batch.schedule_delay_millis,
                export_timeout_millis=batch.export_timeout_millis,
                meter_provider=meter_provider,
            )
        )
    return ProviderBundle(
        tracer_provider,
        meter_provider,
        logger_provider,
        config,
        identity,
        runtime_profile,
        runtime_config_fingerprint,
    )


__all__ = ["ProviderBundle", "ShutdownReport", "build_provider_bundle"]
