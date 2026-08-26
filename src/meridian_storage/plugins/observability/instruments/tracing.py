# SPDX-License-Identifier: Apache-2.0
"""Safe tracer facade preserving correlation without raw payload capture."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer

from ..context import ContextPolicy, correlation_attributes
from ..policy import AttributeValue, safe_name, sanitize_attributes
from ..providers import instrumentation_suppressed


class SafeSpan:
    def __init__(self, span: Span) -> None:
        self._span = span

    @property
    def is_recording(self) -> bool:
        return self._span.is_recording()

    @property
    def trace_id(self) -> str | None:
        context = self._span.get_span_context()
        return format(context.trace_id, "032x") if context.is_valid else None

    @property
    def span_id(self) -> str | None:
        context = self._span.get_span_context()
        return format(context.span_id, "016x") if context.is_valid else None

    def set_attribute(self, key: str, value: object) -> SafeSpan:
        attributes = sanitize_attributes({key: value})
        self._span.set_attribute(key, attributes[key])
        return self

    def add_event(
        self,
        name: str,
        attributes: Mapping[str, object] | None = None,
    ) -> SafeSpan:
        self._span.add_event(safe_name(name, "event name"), sanitize_attributes(attributes))
        return self

    def record_exception(self, exception: BaseException) -> SafeSpan:
        """Record only exception class, never message, stack, or local variables."""

        attributes = sanitize_attributes(
            {"exception.type": (f"{type(exception).__module__}.{type(exception).__qualname__}")}
        )
        self._span.add_event(
            "exception",
            attributes,
        )
        self._span.set_status(Status(StatusCode.ERROR))
        return self

    def set_ok(self) -> SafeSpan:
        self._span.set_status(Status(StatusCode.OK))
        return self

    def end(self, end_time: int | None = None) -> None:
        self._span.end(end_time=end_time)


class SafeTracer:
    def __init__(self, tracer: Tracer, context_policy: ContextPolicy) -> None:
        self._tracer = tracer
        self._context_policy = context_policy

    def _attributes(self, attributes: Mapping[str, object] | None) -> dict[str, AttributeValue]:
        result = sanitize_attributes(attributes)
        correlation = correlation_attributes(self._context_policy)
        correlation.pop("trace_id", None)
        correlation.pop("span_id", None)
        result.update(correlation)
        return result

    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Mapping[str, object] | None = None,
        start_time: int | None = None,
    ) -> SafeSpan:
        if instrumentation_suppressed():
            return SafeSpan(trace.INVALID_SPAN)
        return SafeSpan(
            self._tracer.start_span(
                safe_name(name, "span name"),
                kind=kind,
                attributes=self._attributes(attributes),
                start_time=start_time,
                record_exception=False,
                set_status_on_exception=False,
            )
        )

    @contextmanager
    def start_as_current_span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Mapping[str, object] | None = None,
        start_time: int | None = None,
        end_on_exit: bool = True,
    ) -> Iterator[SafeSpan]:
        if instrumentation_suppressed():
            yield SafeSpan(trace.INVALID_SPAN)
            return
        with self._tracer.start_as_current_span(
            safe_name(name, "span name"),
            kind=kind,
            attributes=self._attributes(attributes),
            start_time=start_time,
            record_exception=False,
            set_status_on_exception=False,
            end_on_exit=end_on_exit,
        ) as span:
            safe_span = SafeSpan(span)
            try:
                yield safe_span
            except BaseException as exc:
                safe_span.record_exception(exc)
                raise


__all__ = ["SafeSpan", "SafeTracer"]
