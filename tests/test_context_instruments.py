# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from opentelemetry import baggage, context, trace

from meridian_storage import OperationContext, bind_context
from meridian_storage.plugins.observability import (
    CardinalityExceeded,
    ContextPolicy,
    InvalidInstrument,
    InvalidObservabilityConfiguration,
)
from meridian_storage.plugins.observability.context import ContextPolicy as PublicContextPolicy
from meridian_storage.plugins.observability.context import correlation_attributes
from meridian_storage.plugins.observability.providers import suppress_instrumentation


def test_usage_capture_public_imports_preserve_correlation_fields() -> None:
    assert PublicContextPolicy is ContextPolicy
    span = trace.NonRecordingSpan(
        trace.SpanContext(trace_id=int("a" * 32, 16), span_id=int("b" * 16, 16), is_remote=False)
    )
    with (
        bind_context(
            OperationContext(
                principal_ref="principal:private",
                request_id="request-usage",
                tenant="private-tenant",
                correlation_id="correlation-usage",
                scope={"private": "never-copy"},
            )
        ),
        trace.use_span(span),
    ):
        assert correlation_attributes(PublicContextPolicy()) == {
            "trace_id": "a" * 32,
            "span_id": "b" * 16,
            "meridian.request.id": "request-usage",
            "meridian.correlation.id": "correlation-usage",
        }
        assert correlation_attributes(PublicContextPolicy(), include_request=False) == {}


def test_context_correlation_excludes_principal_tenant_and_unlisted_scope() -> None:
    policy = ContextPolicy(baggage_allowlist=("release",), scope_allowlist=("region",))
    otel_context = baggage.set_baggage("release", "stable")
    token = context.attach(otel_context)
    try:
        with bind_context(
            OperationContext(
                principal_ref="principal:secret",
                request_id="request-7",
                tenant="tenant-secret",
                correlation_id="correlation-3",
                scope={"region": "us-west", "private": "never-copy"},
            )
        ):
            attributes = correlation_attributes(policy)
    finally:
        context.detach(token)
    assert attributes["meridian.request.id"] == "request-7"
    assert attributes["meridian.correlation.id"] == "correlation-3"
    assert attributes["meridian.scope.region"] == "us-west"
    assert attributes["baggage.release"] == "stable"
    assert "principal" not in repr(attributes)
    assert "tenant-secret" not in repr(attributes)
    assert "private" not in repr(attributes)
    with pytest.raises(InvalidObservabilityConfiguration, match="invalid key"):
        ContextPolicy(baggage_allowlist=("access_token",))
    with pytest.raises(InvalidObservabilityConfiguration, match="24 entries"):
        ContextPolicy(scope_allowlist=tuple(f"scope{index}" for index in range(25)))


def test_tracer_records_safe_exception_class_and_correlation(
    observability: tuple[object, ...],
) -> None:
    plugin, span_exporter, _, _ = observability
    tracer = plugin.tracer("tests.tracing")  # type: ignore[union-attr]
    with (
        bind_context(
            OperationContext(
                principal_ref="principal:test",
                request_id="request-1",
                correlation_id="correlation-1",
            )
        ),
        pytest.raises(RuntimeError, match="do-not-export"),
        tracer.start_as_current_span("jobs.run"),
    ):
        raise RuntimeError("do-not-export")
    plugin.force_flush()  # type: ignore[union-attr]
    span = span_exporter.get_finished_spans()[0]  # type: ignore[union-attr]
    assert span.attributes["meridian.request.id"] == "request-1"
    assert span.status.status_code.name == "ERROR"
    exception_event = span.events[0]
    assert exception_event.attributes["exception.type"] == "builtins.RuntimeError"
    assert "do-not-export" not in repr(exception_event.attributes)


def test_structured_logger_preserves_trace_ids_without_exception_message(
    observability: tuple[object, ...],
) -> None:
    plugin, _, _, log_exporter = observability
    tracer = plugin.tracer("tests.logging")  # type: ignore[union-attr]
    logger = plugin.logger("tests.logging")  # type: ignore[union-attr]
    with (
        bind_context(OperationContext(principal_ref="principal:test", request_id="request-9")),
        tracer.start_as_current_span("work"),
    ):
        logger.error(
            "failed safely",
            attributes={"result": "failed"},
            exception=ValueError("secret"),
        )
    plugin.force_flush()  # type: ignore[union-attr]
    record = log_exporter.get_finished_logs()[0].log_record  # type: ignore[union-attr]
    assert record.attributes["result"] == "failed"
    assert record.attributes["meridian.request.id"] == "request-9"
    assert record.attributes["exception.type"] == "builtins.ValueError"
    assert record.trace_id != 0
    assert "secret" not in repr(record.attributes)


def test_metric_declarations_and_recursion_suppression(observability: tuple[object, ...]) -> None:
    plugin, span_exporter, metric_exporter, log_exporter = observability
    meter = plugin.meter("tests.metrics")  # type: ignore[union-attr]
    counter = meter.create_counter(
        "jobs.completed",
        allowed_attributes=("result",),
        cardinality_limit=2,
    )
    counter.add(1, {"result": "ok"})
    with suppress_instrumentation():
        counter.add(100, {"result": "ignored"})
        plugin.logger("tests").info("ignored")  # type: ignore[union-attr]
        with plugin.tracer("tests").start_as_current_span("ignored"):  # type: ignore[union-attr]
            pass
    plugin.force_flush()  # type: ignore[union-attr]
    assert counter.series_count == 1
    assert metric_exporter.exports  # type: ignore[union-attr]
    assert not span_exporter.get_finished_spans()  # type: ignore[union-attr]
    assert not log_exporter.get_finished_logs()  # type: ignore[union-attr]


def test_repeated_metric_declaration_shares_cardinality_budget(
    observability: tuple[object, ...],
) -> None:
    plugin = observability[0]
    meter = plugin.meter("tests.shared-budget")  # type: ignore[union-attr]
    first = meter.create_counter(
        "jobs.shared",
        allowed_attributes=("result",),
        cardinality_limit=1,
    )
    second = meter.create_counter(
        "jobs.shared",
        allowed_attributes=("result",),
        cardinality_limit=1,
    )
    first.add(1, {"result": "ok"})
    assert second.series_count == 1
    with pytest.raises(CardinalityExceeded):
        second.add(1, {"result": "failed"})
    with pytest.raises(InvalidInstrument, match="non-negative"):
        first.add(-1, {"result": "ok"})
    with pytest.raises(InvalidInstrument, match="finite"):
        meter.create_histogram("jobs.invalid").record(float("inf"))


def test_manual_span_log_levels_and_synchronous_metric_instruments(
    observability: tuple[object, ...],
) -> None:
    plugin, span_exporter, metric_exporter, log_exporter = observability
    tracer = plugin.tracer("tests.manual")  # type: ignore[union-attr]
    span = tracer.start_span("manual.work")
    assert span.is_recording
    assert span.trace_id is not None
    assert span.span_id is not None
    span.set_attribute("result", "ok").add_event("checkpoint", {"attempt": 1}).set_ok().end()

    logger = plugin.logger("tests.levels")  # type: ignore[union-attr]
    logger.trace("trace")
    logger.debug("debug")
    logger.warning("warning")
    logger.critical("critical")
    with pytest.raises(InvalidInstrument, match="severity"):
        logger.emit("unknown", "invalid")

    meter = plugin.meter("tests.synchronous")  # type: ignore[union-attr]
    assert meter is plugin.meter("tests.synchronous")  # type: ignore[union-attr]
    up_down = meter.create_up_down_counter("jobs.active")
    histogram = meter.create_histogram("jobs.duration", unit="s")
    gauge = meter.create_gauge("jobs.queue_depth")
    up_down.add(1)
    histogram.record(0.5)
    gauge.set(7)
    plugin.force_flush()  # type: ignore[union-attr]
    assert span_exporter.get_finished_spans()[0].name == "manual.work"  # type: ignore[union-attr]
    assert len(log_exporter.get_finished_logs()) == 4  # type: ignore[union-attr]
    assert metric_exporter.exports  # type: ignore[union-attr]
