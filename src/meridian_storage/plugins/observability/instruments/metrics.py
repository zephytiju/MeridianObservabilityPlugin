# SPDX-License-Identifier: Apache-2.0
"""Declared metric instruments with bounded cardinality."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol, TypeVar

from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter

from ..context import ContextPolicy, correlation_attributes
from ..errors import InvalidInstrument
from ..policy import CardinalityGuard, InstrumentDefinition
from ..providers import instrumentation_suppressed


class _GaugeInstrument(Protocol):
    def set(self, amount: int | float, attributes: Mapping[str, Any] | None = None) -> None: ...


InstrumentT = TypeVar("InstrumentT")


def _measurement(value: object, *, non_negative: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidInstrument("metric measurements must be numeric")
    if isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise InvalidInstrument("integer metric measurements must fit signed 64-bit values")
    elif not math.isfinite(value):
        raise InvalidInstrument("floating-point metric measurements must be finite")
    if non_negative and value < 0:
        raise InvalidInstrument("counter measurements must be non-negative")
    return value


@dataclass(slots=True)
class _GuardedInstrument[InstrumentT]:
    _instrument: InstrumentT
    _guard: CardinalityGuard
    _context_policy: ContextPolicy

    def _attributes(self, values: Mapping[str, object] | None) -> dict[str, Any]:
        return self._guard.attributes(
            values,
            system_values=correlation_attributes(self._context_policy, include_request=False),
        )

    @property
    def series_count(self) -> int:
        return self._guard.series_count


class SafeCounter(_GuardedInstrument[Counter]):
    def add(self, amount: int | float, attributes: Mapping[str, object] | None = None) -> None:
        if not instrumentation_suppressed():
            self._instrument.add(
                _measurement(amount, non_negative=True), self._attributes(attributes)
            )


class SafeUpDownCounter(_GuardedInstrument[UpDownCounter]):
    def add(self, amount: int | float, attributes: Mapping[str, object] | None = None) -> None:
        if not instrumentation_suppressed():
            self._instrument.add(_measurement(amount), self._attributes(attributes))


class SafeHistogram(_GuardedInstrument[Histogram]):
    def record(self, amount: int | float, attributes: Mapping[str, object] | None = None) -> None:
        if not instrumentation_suppressed():
            self._instrument.record(_measurement(amount), self._attributes(attributes))


class SafeGauge(_GuardedInstrument[_GaugeInstrument]):
    def set(self, amount: int | float, attributes: Mapping[str, object] | None = None) -> None:
        if not instrumentation_suppressed():
            self._instrument.set(_measurement(amount), self._attributes(attributes))


class SafeMeter:
    def __init__(self, meter: Meter, context_policy: ContextPolicy) -> None:
        self._meter = meter
        self._context_policy = context_policy
        self._definitions: dict[str, tuple[str, InstrumentDefinition, CardinalityGuard]] = {}
        self._lock = Lock()

    @staticmethod
    def _definition(
        name: str,
        *,
        unit: str,
        description: str,
        allowed_attributes: tuple[str, ...],
        cardinality_limit: int,
    ) -> InstrumentDefinition:
        return InstrumentDefinition(
            name=name,
            unit=unit,
            description=description,
            allowed_attributes=allowed_attributes,
            cardinality_limit=cardinality_limit,
        )

    def _register(self, kind: str, definition: InstrumentDefinition) -> CardinalityGuard:
        with self._lock:
            existing = self._definitions.get(definition.name)
            if existing is not None:
                if existing[0] != kind or existing[1] != definition:
                    raise InvalidInstrument(
                        f"instrument {definition.name!r} was already declared differently"
                    )
                return existing[2]
            guard = CardinalityGuard(definition)
            self._definitions[definition.name] = (kind, definition, guard)
            return guard

    def create_counter(
        self,
        name: str,
        *,
        unit: str = "1",
        description: str = "",
        allowed_attributes: tuple[str, ...] = (),
        cardinality_limit: int = 2000,
    ) -> SafeCounter:
        definition = self._definition(
            name,
            unit=unit,
            description=description,
            allowed_attributes=allowed_attributes,
            cardinality_limit=cardinality_limit,
        )
        guard = self._register("counter", definition)
        instrument = self._meter.create_counter(name, unit=unit, description=description)
        return SafeCounter(instrument, guard, self._context_policy)

    def create_up_down_counter(
        self,
        name: str,
        *,
        unit: str = "1",
        description: str = "",
        allowed_attributes: tuple[str, ...] = (),
        cardinality_limit: int = 2000,
    ) -> SafeUpDownCounter:
        definition = self._definition(
            name,
            unit=unit,
            description=description,
            allowed_attributes=allowed_attributes,
            cardinality_limit=cardinality_limit,
        )
        guard = self._register("up-down-counter", definition)
        instrument = self._meter.create_up_down_counter(name, unit=unit, description=description)
        return SafeUpDownCounter(instrument, guard, self._context_policy)

    def create_histogram(
        self,
        name: str,
        *,
        unit: str = "1",
        description: str = "",
        allowed_attributes: tuple[str, ...] = (),
        cardinality_limit: int = 2000,
    ) -> SafeHistogram:
        definition = self._definition(
            name,
            unit=unit,
            description=description,
            allowed_attributes=allowed_attributes,
            cardinality_limit=cardinality_limit,
        )
        guard = self._register("histogram", definition)
        instrument = self._meter.create_histogram(name, unit=unit, description=description)
        return SafeHistogram(instrument, guard, self._context_policy)

    def create_gauge(
        self,
        name: str,
        *,
        unit: str = "1",
        description: str = "",
        allowed_attributes: tuple[str, ...] = (),
        cardinality_limit: int = 2000,
    ) -> SafeGauge:
        definition = self._definition(
            name,
            unit=unit,
            description=description,
            allowed_attributes=allowed_attributes,
            cardinality_limit=cardinality_limit,
        )
        guard = self._register("gauge", definition)
        instrument = self._meter.create_gauge(name, unit=unit, description=description)
        return SafeGauge(instrument, guard, self._context_policy)


__all__ = ["SafeCounter", "SafeGauge", "SafeHistogram", "SafeMeter", "SafeUpDownCounter"]
