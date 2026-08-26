# SPDX-License-Identifier: Apache-2.0
"""Mapping-first log, span, and metric queries with engine-neutral plans."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from statistics import fmean
from types import MappingProxyType
from typing import Protocol, TypeVar, cast

from meridian_storage import Expression, OperationResult, ResourceRef
from meridian_storage.evidence import (
    EvidenceProfile,
    EvidenceQuery,
    EvidenceQueryResult,
    QueryDirection,
    QueryOrder,
)
from meridian_storage.query import (
    BooleanExpression,
    PageSpec,
    Projection,
    QueryOperation,
    QueryTarget,
    ResultSpec,
    SafetyBudget,
    Sort,
    ValueExpression,
    field,
)

from ..config import EvidenceResources
from ..errors import InvalidTelemetryQuery
from ..policy import is_denied_key

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,255}$")
_SEVERITIES = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL")
_MAX_PAGE_SIZE = 500
_ALLOWED_OPERATORS = frozenset({"eq", "ne", "lt", "lte", "gt", "gte", "in", "notIn", "isNull"})


class MeridianExecutor(Protocol):
    def execute(self, expression: Expression) -> OperationResult: ...


def _timestamp(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidTelemetryQuery(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_field(value: object) -> str:
    if not isinstance(value, str) or _FIELD.fullmatch(value) is None or is_denied_key(value):
        raise InvalidTelemetryQuery("query fields must be safe bounded logical names")
    return value


def _predicate_scalar(value: object) -> str | bool | int | float:
    if not isinstance(value, str | bool | int | float):
        raise InvalidTelemetryQuery("query predicates must contain canonical scalar values")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 4096 or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise InvalidTelemetryQuery("query string values must be bounded and printable")
    elif isinstance(value, bool):
        return value
    elif isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise InvalidTelemetryQuery("query integer values must fit signed 64-bit values")
    elif not math.isfinite(value):
        raise InvalidTelemetryQuery("query floating-point values must be finite")
    return value


def _validate_operators(values: Mapping[object, object]) -> Mapping[str, object]:
    if not values or any(
        not isinstance(operator, str) or operator not in _ALLOWED_OPERATORS for operator in values
    ):
        raise InvalidTelemetryQuery("query predicate contains an unsupported operator")
    result: dict[str, object] = {}
    for raw_operator, candidate in values.items():
        if not isinstance(raw_operator, str):
            raise InvalidTelemetryQuery("query predicate contains an unsupported operator")
        if raw_operator in {"in", "notIn"}:
            if (
                not isinstance(candidate, Sequence)
                or isinstance(candidate, str | bytes | bytearray)
                or not 1 <= len(candidate) <= 100
            ):
                raise InvalidTelemetryQuery("membership predicates require 1 to 100 scalar values")
            result[raw_operator] = tuple(_predicate_scalar(item) for item in candidate)
        elif raw_operator == "isNull":
            if not isinstance(candidate, bool):
                raise InvalidTelemetryQuery("isNull predicates require a boolean")
            result[raw_operator] = candidate
        else:
            result[raw_operator] = _predicate_scalar(candidate)
    return MappingProxyType(dict(sorted(result.items())))


def _validate_where(where: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(where, Mapping):
        raise InvalidTelemetryQuery("query predicates must be a mapping")
    if len(where) > 32:
        raise InvalidTelemetryQuery("stored telemetry queries accept at most 32 predicates")
    result: dict[str, object] = {}
    for raw_key, value in where.items():
        key = _validate_field(raw_key)
        if isinstance(value, Mapping):
            result[key] = _validate_operators(value)
        else:
            result[key] = _predicate_scalar(value)
    return MappingProxyType(dict(sorted(result.items())))


def _logical_predicate(where: Mapping[str, object]) -> ValueExpression | None:
    predicates: list[ValueExpression] = []
    for name, value in sorted(where.items()):
        operand = field(name)
        if not isinstance(value, Mapping):
            predicates.append(operand.eq(value))
            continue
        for operator, candidate in sorted(value.items()):
            if operator == "in":
                predicates.append(operand.in_(cast(Sequence[object], candidate)))
            elif operator == "notIn":
                expression = operand.in_(cast(Sequence[object], candidate))
                predicates.append(type(expression)(expression.operand, expression.values, True))
            elif operator == "isNull":
                predicates.append(operand.is_null(cast(bool, candidate)))
            else:
                predicates.append(getattr(operand, operator)(candidate))
    if not predicates:
        return None
    if len(predicates) == 1:
        return predicates[0]
    return BooleanExpression("and", tuple(predicates))


def _items(data: object, *, maximum: int) -> tuple[Mapping[str, object], ...]:
    selected: object = data
    if isinstance(data, Mapping):
        for key in ("items", "records", "data"):
            if key in data:
                selected = data[key]
                break
    if not isinstance(selected, Sequence) or isinstance(selected, str | bytes | bytearray):
        raise InvalidTelemetryQuery("Evidence query returned an invalid record collection")
    if len(selected) > maximum:
        raise InvalidTelemetryQuery("Evidence query exceeded the requested page size")
    result: list[Mapping[str, object]] = []
    for item in selected:
        if not isinstance(item, Mapping):
            raise InvalidTelemetryQuery("Evidence query returned a non-record item")
        result.append(MappingProxyType(dict(item)))
    return tuple(result)


def _numeric(item: Mapping[str, object], name: str) -> float | None:
    candidate = item.get(name)
    if isinstance(candidate, int | float) and not isinstance(candidate, bool):
        return float(candidate)
    return None


@dataclass(frozen=True, slots=True)
class TelemetryQueryResult:
    items: tuple[Mapping[str, object], ...]
    cursor: str | None
    evidence: EvidenceQueryResult

    @property
    def provenance(self) -> Mapping[str, str]:
        return self.evidence.provenance


@dataclass(frozen=True, slots=True)
class TelemetryQuery:
    _meridian: MeridianExecutor
    resource: ResourceRef
    where: Mapping[str, object]
    select: tuple[str, ...] = ()
    order_by: tuple[QueryOrder, ...] = ()
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        try:
            resource = ResourceRef.parse(self.resource, catalog="evidence")
        except (TypeError, ValueError) as exc:
            raise InvalidTelemetryQuery(
                "stored telemetry queries require a logical Evidence Resource"
            ) from exc
        object.__setattr__(self, "resource", resource)
        object.__setattr__(self, "where", _validate_where(self.where))
        fields = tuple(_validate_field(item) for item in self.select)
        if len(set(fields)) != len(fields):
            raise InvalidTelemetryQuery("selected fields must be unique")
        object.__setattr__(self, "select", fields)
        order: list[QueryOrder] = []
        for item in self.order_by:
            if not isinstance(item, QueryOrder):
                raise InvalidTelemetryQuery("query ordering must use released QueryOrder values")
            try:
                direction = QueryDirection(item.direction)
            except (TypeError, ValueError) as exc:
                raise InvalidTelemetryQuery("query ordering direction is invalid") from exc
            order.append(QueryOrder(_validate_field(item.field), direction))
        object.__setattr__(self, "order_by", tuple(order))
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= _MAX_PAGE_SIZE
        ):
            raise InvalidTelemetryQuery(f"page size must be between 1 and {_MAX_PAGE_SIZE}")
        if self.cursor is not None and (
            not isinstance(self.cursor, str)
            or not self.cursor
            or len(self.cursor.encode("utf-8")) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in self.cursor)
        ):
            raise InvalidTelemetryQuery("cursor must be an opaque bounded token")

    @property
    def expression(self) -> Expression:
        return EvidenceQuery(
            resource=self.resource,
            where=self.where,
            select=self.select,
            order_by=self.order_by,
            limit=self.limit,
            cursor=self.cursor,
            profile=EvidenceProfile.TELEMETRY,
        ).expression()

    @property
    def logical_plan(self) -> QueryOperation:
        projections = tuple(Projection(field(name)) for name in self.select)
        sorts = tuple(Sort(field(item.field), item.direction.value) for item in self.order_by)
        return QueryOperation(
            catalog="evidence",
            targets=(QueryTarget(self.resource),),
            operation="scan",
            result=ResultSpec("records", projections),
            filter=_logical_predicate(self.where),
            order=sorts,
            page=PageSpec(self.limit, self.cursor),
            consistency="eventual",
            budget=SafetyBudget(max_result_values=self.limit),
        )

    @property
    def fingerprint(self) -> str:
        return self.logical_plan.fingerprint

    def page(self, *, limit: int = 50, cursor: str | None = None) -> TelemetryQuery:
        return replace(self, limit=limit, cursor=cursor)

    def selecting(self, *fields: str) -> TelemetryQuery:
        return replace(self, select=tuple(fields))

    def execute(self) -> TelemetryQueryResult:
        operation_result = self._meridian.execute(self.expression)
        evidence = EvidenceQueryResult.from_operation_result(operation_result)
        cursor: str | None = None
        if isinstance(evidence.data, Mapping):
            candidate = evidence.data.get("cursor")
            if candidate is not None:
                if not isinstance(candidate, str):
                    raise InvalidTelemetryQuery("Evidence query returned an invalid cursor")
                cursor = candidate
        return TelemetryQueryResult(_items(evidence.data, maximum=self.limit), cursor, evidence)

    def group_by(
        self, field_name: str, *, aggregation: str = "count", value_field: str = "value"
    ) -> DerivedTelemetryQuery[Mapping[str, float]]:
        key = _validate_field(field_name)
        value = _validate_field(value_field)
        if aggregation not in {"count", "sum", "avg", "min", "max"}:
            raise InvalidTelemetryQuery("aggregation must be count, sum, avg, min, or max")

        def transform(result: TelemetryQueryResult) -> Mapping[str, float]:
            groups: dict[str, list[float]] = defaultdict(list)
            for item in result.items:
                group = str(item.get(key, ""))
                if aggregation == "count":
                    groups[group].append(1.0)
                else:
                    candidate = _numeric(item, value)
                    if candidate is not None:
                        groups[group].append(candidate)
            reducers: dict[str, Callable[[Sequence[float]], float]] = {
                "count": lambda values: float(len(values)),
                "sum": sum,
                "avg": fmean,
                "min": min,
                "max": max,
            }
            return {
                name: reducers[aggregation](values) for name, values in groups.items() if values
            }

        return DerivedTelemetryQuery(self, transform)

    def delta(self, *, value_field: str = "value") -> DerivedTelemetryQuery[float | None]:
        value = _validate_field(value_field)

        def transform(result: TelemetryQueryResult) -> float | None:
            points = [
                candidate
                for item in result.items
                if (candidate := _numeric(item, value)) is not None
            ]
            return points[-1] - points[0] if len(points) >= 2 else None

        return DerivedTelemetryQuery(self, transform)

    def rate(
        self,
        *,
        value_field: str = "value",
        timestamp_field: str = "endTime",
    ) -> DerivedTelemetryQuery[float | None]:
        value = _validate_field(value_field)
        timestamp = _validate_field(timestamp_field)

        def transform(result: TelemetryQueryResult) -> float | None:
            points: list[tuple[datetime, float]] = []
            for item in result.items:
                candidate = _numeric(item, value)
                raw_time = item.get(timestamp)
                if candidate is not None and isinstance(raw_time, str):
                    try:
                        parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                    except (OverflowError, ValueError) as exc:
                        raise InvalidTelemetryQuery(
                            "Evidence query returned an invalid metric timestamp"
                        ) from exc
                    points.append((_timestamp(parsed, timestamp), candidate))
            points.sort(key=lambda item: item[0])
            if len(points) < 2:
                return None
            seconds = (points[-1][0] - points[0][0]).total_seconds()
            return None if seconds <= 0 else (points[-1][1] - points[0][1]) / seconds

        return DerivedTelemetryQuery(self, transform)

    def histogram_quantile(
        self, quantile: float, *, value_field: str = "value"
    ) -> DerivedTelemetryQuery[float | None]:
        if (
            isinstance(quantile, bool)
            or not isinstance(quantile, int | float)
            or not 0 <= quantile <= 1
        ):
            raise InvalidTelemetryQuery("quantile must be between 0 and 1")
        value = _validate_field(value_field)

        def transform(result: TelemetryQueryResult) -> float | None:
            values = sorted(
                candidate
                for item in result.items
                if (candidate := _numeric(item, value)) is not None
            )
            if not values:
                return None
            position = (len(values) - 1) * float(quantile)
            lower = math.floor(position)
            upper = math.ceil(position)
            if lower == upper:
                return values[lower]
            fraction = position - lower
            return values[lower] + (values[upper] - values[lower]) * fraction

        return DerivedTelemetryQuery(self, transform)


ResultT = TypeVar("ResultT")


@dataclass(frozen=True, slots=True)
class DerivedTelemetryQuery[ResultT]:
    base: TelemetryQuery
    transform: Callable[[TelemetryQueryResult], ResultT]

    @property
    def expression(self) -> Expression:
        return self.base.expression

    @property
    def logical_plan(self) -> QueryOperation:
        return self.base.logical_plan

    def execute(self) -> ResultT:
        return self.transform(self.base.execute())


@dataclass(frozen=True, slots=True)
class ServiceHealthSnapshot:
    requests: float
    errors: float
    error_rate: float | None
    latency_p95: float | None


class TelemetryQueries:
    def __init__(
        self,
        meridian: MeridianExecutor,
        resources: EvidenceResources,
        *,
        maximum_range: timedelta = timedelta(days=1),
    ) -> None:
        if (
            not isinstance(maximum_range, timedelta)
            or maximum_range <= timedelta(0)
            or maximum_range > timedelta(days=31)
        ):
            raise InvalidTelemetryQuery("maximum_range must be between zero and 31 days")
        self._meridian = meridian
        self._resources = resources
        self._maximum_range = maximum_range

    def _bounded(
        self,
        resource: ResourceRef,
        *,
        start: datetime,
        end: datetime,
        where: Mapping[str, object] | None = None,
        order_field: str = "observedTime",
    ) -> TelemetryQuery:
        lower = _timestamp(start, "start")
        upper = _timestamp(end, "end")
        if upper <= lower or upper - lower > self._maximum_range:
            raise InvalidTelemetryQuery("query interval must be positive and within maximum_range")
        predicates = dict(where or {})
        predicates["observedTime"] = {"gte": _iso(lower), "lt": _iso(upper)}
        return TelemetryQuery(
            self._meridian,
            resource,
            predicates,
            order_by=(QueryOrder(order_field, QueryDirection.ASCENDING),),
        )

    def logs(
        self,
        *,
        start: datetime,
        end: datetime,
        min_severity: str | None = None,
        trace_id: str | None = None,
        where: Mapping[str, object] | None = None,
    ) -> TelemetryQuery:
        predicates = dict(where or {})
        if min_severity is not None:
            normalized = min_severity.upper()
            if normalized not in _SEVERITIES:
                raise InvalidTelemetryQuery("min_severity is invalid")
            predicates["severity"] = {"in": list(_SEVERITIES[_SEVERITIES.index(normalized) :])}
        if trace_id is not None:
            if _TRACE_ID.fullmatch(trace_id) is None:
                raise InvalidTelemetryQuery("trace_id must be 32 lowercase hexadecimal characters")
            predicates["traceId"] = trace_id
        return self._bounded(self._resources.logs, start=start, end=end, where=predicates)

    def spans(
        self,
        *,
        start: datetime,
        end: datetime,
        trace_id: str | None = None,
        where: Mapping[str, object] | None = None,
    ) -> TelemetryQuery:
        predicates = dict(where or {})
        if trace_id is not None:
            if _TRACE_ID.fullmatch(trace_id) is None:
                raise InvalidTelemetryQuery("trace_id must be 32 lowercase hexadecimal characters")
            predicates["traceId"] = trace_id
        return self._bounded(
            self._resources.spans,
            start=start,
            end=end,
            where=predicates,
            order_field="startTime",
        )

    def trace(self, trace_id: str, *, start: datetime, end: datetime) -> TelemetryQuery:
        return self.spans(trace_id=trace_id, start=start, end=end)

    def related_logs(self, trace_id: str, *, start: datetime, end: datetime) -> TelemetryQuery:
        return self.logs(trace_id=trace_id, start=start, end=end)

    def metric_series(
        self,
        name: str,
        *,
        start: datetime,
        end: datetime,
        dimensions: Mapping[str, object] | None = None,
    ) -> TelemetryQuery:
        if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 255:
            raise InvalidTelemetryQuery("metric name must be bounded and non-empty")
        predicates: dict[str, object] = {"name": _predicate_scalar(name)}
        for key, value in (dimensions or {}).items():
            predicates[f"dimensions.{_validate_field(key)}"] = value
        return self._bounded(
            self._resources.metrics,
            start=start,
            end=end,
            where=predicates,
            order_field="endTime",
        )

    def service_health(
        self,
        *,
        start: datetime,
        end: datetime,
        request_metric: str = "http.server.requests",
        error_metric: str = "http.server.errors",
        latency_metric: str = "http.server.duration",
    ) -> DerivedTelemetryQuery[ServiceHealthSnapshot]:
        names: list[str] = []
        for name in (request_metric, error_metric, latency_metric):
            if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 255:
                raise InvalidTelemetryQuery("service health metric names must be bounded")
            names.append(cast(str, _predicate_scalar(name)))
        base = self._bounded(
            self._resources.metrics,
            start=start,
            end=end,
            where={"name": {"in": names}},
            order_field="endTime",
        ).page(limit=_MAX_PAGE_SIZE)

        def transform(result: TelemetryQueryResult) -> ServiceHealthSnapshot:
            requests = 0.0
            errors = 0.0
            latencies: list[float] = []
            for item in result.items:
                candidate = _numeric(item, "value")
                if candidate is None:
                    continue
                if item.get("name") == request_metric:
                    requests += candidate
                elif item.get("name") == error_metric:
                    errors += candidate
                elif item.get("name") == latency_metric:
                    latencies.append(candidate)
            latencies.sort()
            latency = None
            if latencies:
                latency = latencies[math.ceil(0.95 * len(latencies)) - 1]
            return ServiceHealthSnapshot(
                requests=requests,
                errors=errors,
                error_rate=None if requests <= 0 else errors / requests,
                latency_p95=latency,
            )

        return DerivedTelemetryQuery(base, transform)


__all__ = [
    "DerivedTelemetryQuery",
    "ServiceHealthSnapshot",
    "TelemetryQueries",
    "TelemetryQuery",
    "TelemetryQueryResult",
]
