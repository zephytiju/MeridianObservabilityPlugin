# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from conftest import ReadyMeridian, operation_result
from meridian_storage.evidence import EvidenceQueryResult
from meridian_storage.plugins.observability import (
    InvalidTelemetryQuery,
    TelemetryQueries,
    TelemetryQueryResult,
)

START = datetime(2026, 8, 25, 12, tzinfo=UTC)
END = START + timedelta(minutes=15)


def test_log_query_emits_evidence_expression_and_released_logical_plan(
    evidence_resources: object,
) -> None:
    runtime = ReadyMeridian(
        operation_result(
            {
                "items": [{"severity": "ERROR", "body": "safe"}],
                "cursor": "opaque-next",
            }
        )
    )
    query = TelemetryQueries(runtime, evidence_resources).logs(  # type: ignore[arg-type]
        start=START,
        end=END,
        min_severity="ERROR",
        trace_id="a" * 32,
    )
    assert query.expression.catalog == "evidence"
    assert query.expression.method == "query"
    assert query.logical_plan.catalog == "evidence"
    assert query.logical_plan.consistency == "eventual"
    assert "adapter" not in repr(query.logical_plan.to_dict()).lower()
    result = query.execute()
    assert result.items[0]["severity"] == "ERROR"
    assert result.cursor == "opaque-next"
    assert runtime.expressions == [query.expression]


def test_query_bounds_fields_and_cursor_fail_closed(evidence_resources: object) -> None:
    queries = TelemetryQueries(ReadyMeridian(), evidence_resources)  # type: ignore[arg-type]
    with pytest.raises(InvalidTelemetryQuery):
        queries.logs(start=END, end=START)
    with pytest.raises(InvalidTelemetryQuery):
        queries.logs(start=START, end=START + timedelta(days=2))
    with pytest.raises(InvalidTelemetryQuery):
        queries.logs(start=START, end=END, trace_id="invalid")
    with pytest.raises(InvalidTelemetryQuery):
        queries.logs(start=START, end=END, where={"db.statement": "select secret"})
    with pytest.raises(InvalidTelemetryQuery):
        queries.logs(start=START, end=END).page(limit=501)
    with pytest.raises(InvalidTelemetryQuery):
        queries.logs(start=START, end=END, where={"severity": float("nan")})
    with pytest.raises(InvalidTelemetryQuery):
        queries.logs(start=START, end=END, where={1: "invalid"})  # type: ignore[dict-item]
    with pytest.raises(InvalidTelemetryQuery):
        queries.logs(start=START, end=END).page(cursor="invalid\ncursor")


def test_evidence_results_cannot_exceed_requested_page(evidence_resources: object) -> None:
    runtime = ReadyMeridian(operation_result({"items": [{"id": 1}, {"id": 2}]}))
    query = (
        TelemetryQueries(runtime, evidence_resources)
        .logs(  # type: ignore[arg-type]
            start=START,
            end=END,
        )
        .page(limit=1)
    )
    with pytest.raises(InvalidTelemetryQuery, match="page size"):
        query.execute()


def _telemetry_result(items: list[dict[str, object]]) -> TelemetryQueryResult:
    core_result = operation_result({"items": items})
    return TelemetryQueryResult(
        tuple(items),
        None,
        EvidenceQueryResult.from_operation_result(core_result),
    )


def test_derived_metric_helpers_are_deterministic(evidence_resources: object) -> None:
    query = TelemetryQueries(ReadyMeridian(), evidence_resources).metric_series(  # type: ignore[arg-type]
        "jobs.completed", start=START, end=END
    )
    result = _telemetry_result(
        [
            {"value": 10, "endTime": "2026-08-25T12:00:00Z", "region": "a"},
            {"value": 14, "endTime": "2026-08-25T12:00:02Z", "region": "a"},
            {"value": 20, "endTime": "2026-08-25T12:00:04Z", "region": "b"},
        ]
    )
    assert query.delta().transform(result) == 10
    assert query.rate().transform(result) == 2.5
    assert query.histogram_quantile(0.5).transform(result) == 14
    assert query.group_by("region", aggregation="avg").transform(result) == {
        "a": 12.0,
        "b": 20.0,
    }


def test_service_health_helper_uses_bounded_metric_query(evidence_resources: object) -> None:
    runtime = ReadyMeridian(
        operation_result(
            {
                "items": [
                    {"name": "http.server.requests", "value": 100},
                    {"name": "http.server.errors", "value": 4},
                    {"name": "http.server.duration", "value": 0.2},
                    {"name": "http.server.duration", "value": 0.8},
                ]
            }
        )
    )
    snapshot = (
        TelemetryQueries(runtime, evidence_resources)
        .service_health(  # type: ignore[arg-type]
            start=START, end=END
        )
        .execute()
    )
    assert snapshot.requests == 100
    assert snapshot.errors == 4
    assert snapshot.error_rate == 0.04
    assert snapshot.latency_p95 == 0.8


def test_query_shape_and_empty_derived_values(evidence_resources: object) -> None:
    queries = TelemetryQueries(ReadyMeridian(), evidence_resources)  # type: ignore[arg-type]
    query = queries.spans(start=START, end=END).selecting("traceId", "spanId").page(limit=10)
    assert query.logical_plan.page.size == 10
    assert len(query.logical_plan.result.projection) == 2
    assert query.fingerprint.startswith("sha256:")
    empty = _telemetry_result([])
    assert query.delta().transform(empty) is None
    assert query.rate().transform(empty) is None
    assert query.histogram_quantile(0.99).transform(empty) is None
    malformed = _telemetry_result([{"value": 1, "endTime": "not-a-timestamp"}])
    with pytest.raises(InvalidTelemetryQuery, match="metric timestamp"):
        query.rate().transform(malformed)
    with pytest.raises(InvalidTelemetryQuery):
        query.histogram_quantile(2)
    with pytest.raises(InvalidTelemetryQuery):
        query.group_by("region", aggregation="median")
    with pytest.raises(InvalidTelemetryQuery):
        queries.logs(start=START, end=END, where={"severity": {"in": "ERROR"}})
