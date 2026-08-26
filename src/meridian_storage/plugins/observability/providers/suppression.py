# SPDX-License-Identifier: Apache-2.0
"""Non-recursive exporter wrappers shared by all signals."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from opentelemetry.sdk._logs import ReadableLogRecord
from opentelemetry.sdk._logs.export import LogRecordExporter
from opentelemetry.sdk.metrics.export import MetricExporter, MetricsData
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter

_suppression_depth: ContextVar[int] = ContextVar(
    "meridian_observability_suppression_depth", default=0
)


def instrumentation_suppressed() -> bool:
    return _suppression_depth.get() > 0


@contextmanager
def suppress_instrumentation() -> Iterator[None]:
    token = _suppression_depth.set(_suppression_depth.get() + 1)
    try:
        yield
    finally:
        _suppression_depth.reset(token)


class SuppressedSpanExporter(SpanExporter):
    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> Any:
        with suppress_instrumentation():
            return self._delegate.export(spans)

    def shutdown(self) -> None:
        with suppress_instrumentation():
            self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        with suppress_instrumentation():
            return self._delegate.force_flush(timeout_millis)


class SuppressedLogExporter(LogRecordExporter):
    def __init__(self, delegate: LogRecordExporter) -> None:
        self._delegate = delegate

    def export(self, batch: Sequence[ReadableLogRecord]) -> Any:
        with suppress_instrumentation():
            return self._delegate.export(batch)

    def shutdown(self) -> None:
        with suppress_instrumentation():
            self._delegate.shutdown()  # type: ignore[no-untyped-call]

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        with suppress_instrumentation():
            return self._delegate.force_flush(timeout_millis)


class SuppressedMetricExporter(MetricExporter):
    def __init__(self, delegate: MetricExporter) -> None:
        super().__init__(
            preferred_temporality=delegate._preferred_temporality,
            preferred_aggregation=delegate._preferred_aggregation,
        )
        self._delegate = delegate

    def export(
        self,
        metrics_data: MetricsData,
        timeout_millis: float = 10_000,
        **kwargs: Any,
    ) -> Any:
        with suppress_instrumentation():
            return self._delegate.export(metrics_data, timeout_millis, **kwargs)

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        with suppress_instrumentation():
            return self._delegate.force_flush(timeout_millis)

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        with suppress_instrumentation():
            self._delegate.shutdown(timeout_millis, **kwargs)


__all__ = [
    "SuppressedLogExporter",
    "SuppressedMetricExporter",
    "SuppressedSpanExporter",
    "instrumentation_suppressed",
    "suppress_instrumentation",
]
