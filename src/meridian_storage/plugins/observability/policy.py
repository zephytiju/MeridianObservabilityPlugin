# SPDX-License-Identifier: Apache-2.0
"""Bounded attribute, body, and metric-cardinality policy."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import cast

from .errors import CardinalityExceeded, InvalidInstrument

type AttributeScalar = str | bool | int | float
type AttributeArray = Sequence[str] | Sequence[bool] | Sequence[int] | Sequence[float]
type AttributeValue = AttributeScalar | AttributeArray

_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_INSTRUMENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-/]{0,254}$")
_DENIED_PARTS = frozenset(
    {
        "authorization",
        "credential",
        "credentials",
        "password",
        "query",
        "querytext",
        "raw",
        "requestbody",
        "responsebody",
        "secret",
        "sql",
        "statement",
        "token",
    }
)


def _normalized_key(key: str) -> str:
    return key.lower().replace("_", "").replace("-", "").replace(".", "")


def is_denied_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return any(part in normalized for part in _DENIED_PARTS)


def safe_name(value: object, field_name: str = "instrument name") -> str:
    if not isinstance(value, str) or _INSTRUMENT_RE.fullmatch(value) is None:
        raise InvalidInstrument(f"{field_name} must be a bounded OTel name")
    return value


def _scalar(value: object) -> AttributeScalar:
    if not isinstance(value, str | bool | int | float):
        raise InvalidInstrument("telemetry attribute values must be scalar or scalar arrays")
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) > 1024 or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise InvalidInstrument("telemetry attribute strings must be bounded and printable")
    elif isinstance(value, bool):
        return value
    elif isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise InvalidInstrument("telemetry integer attributes must fit signed 64-bit values")
    elif not math.isfinite(value):
        raise InvalidInstrument("telemetry floating-point attributes must be finite")
    return value


def sanitize_attributes(
    values: Mapping[str, object] | None,
    *,
    allowed: frozenset[str] | None = None,
    maximum: int = 64,
) -> dict[str, AttributeValue]:
    if values is None:
        return {}
    if not isinstance(values, Mapping) or len(values) > maximum:
        raise InvalidInstrument(f"telemetry attributes may contain at most {maximum} entries")
    result: dict[str, AttributeValue] = {}
    for key, value in values.items():
        if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
            raise InvalidInstrument("telemetry attributes contain an invalid key")
        if is_denied_key(key):
            raise InvalidInstrument(f"telemetry attribute {key!r} is denied by policy")
        if allowed is not None and key not in allowed:
            raise InvalidInstrument(f"telemetry attribute {key!r} is not declared")
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            if not 1 <= len(value) <= 32:
                raise InvalidInstrument("telemetry attribute arrays must contain 1 to 32 values")
            normalized = tuple(_scalar(item) for item in value)
            if len({type(item) for item in normalized}) != 1:
                raise InvalidInstrument("telemetry attribute arrays must be homogeneous")
            result[key] = cast(AttributeArray, normalized)
        else:
            result[key] = _scalar(value)
    return dict(sorted(result.items()))


def _sanitize_body_value(value: object, *, depth: int, entries: list[int]) -> object:
    if isinstance(value, Mapping):
        return _sanitize_body(value, depth=depth + 1, entries=entries)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        if len(value) > 32:
            raise InvalidInstrument("structured log arrays are limited to 32 entries")
        return [_scalar(item) for item in value]
    if value is None:
        return None
    return _scalar(value)


def _sanitize_body(body: object, *, depth: int, entries: list[int]) -> object:
    if isinstance(body, str):
        if len(body.encode("utf-8")) > 4096:
            raise InvalidInstrument("log body strings are limited to 4096 bytes")
        return body
    if not isinstance(body, Mapping):
        return _scalar(body)
    if depth >= 8:
        raise InvalidInstrument("structured log bodies may be nested at most 8 levels")
    if len(body) > 64:
        raise InvalidInstrument("structured log bodies may contain at most 64 entries")
    entries[0] += len(body)
    if entries[0] > 256:
        raise InvalidInstrument("structured log bodies may contain at most 256 total entries")
    result: dict[str, object] = {}
    for key, value in body.items():
        if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None or is_denied_key(key):
            raise InvalidInstrument("structured log body contains a denied or invalid key")
        result[key] = _sanitize_body_value(value, depth=depth, entries=entries)
    return dict(sorted(result.items()))


def sanitize_body(body: object) -> object:
    result = _sanitize_body(body, depth=0, entries=[0])
    if (
        isinstance(result, Mapping)
        and len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()) > 8192
    ):
        raise InvalidInstrument("structured log bodies are limited to 8192 bytes")
    return result


@dataclass(frozen=True, slots=True)
class InstrumentDefinition:
    name: str
    unit: str = "1"
    description: str = ""
    allowed_attributes: tuple[str, ...] = ()
    cardinality_limit: int = 2000

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", safe_name(self.name))
        if not isinstance(self.unit, str) or not self.unit or len(self.unit.encode()) > 63:
            raise InvalidInstrument("instrument unit must be a bounded non-empty string")
        if not isinstance(self.description, str) or len(self.description.encode()) > 1024:
            raise InvalidInstrument("instrument description is limited to 1024 bytes")
        allowed = tuple(sorted(set(self.allowed_attributes)))
        if len(allowed) != len(self.allowed_attributes) or len(allowed) > 32:
            raise InvalidInstrument("allowed metric attributes must be unique and bounded")
        for key in allowed:
            if _KEY_RE.fullmatch(key) is None or is_denied_key(key):
                raise InvalidInstrument("allowed metric attributes contain an invalid key")
        if (
            isinstance(self.cardinality_limit, bool)
            or not isinstance(self.cardinality_limit, int)
            or not 1 <= self.cardinality_limit <= 100_000
        ):
            raise InvalidInstrument("cardinality_limit must be between 1 and 100000")
        object.__setattr__(self, "allowed_attributes", allowed)


class CardinalityGuard:
    def __init__(self, definition: InstrumentDefinition) -> None:
        self._definition = definition
        self._series: set[tuple[tuple[str, object], ...]] = set()
        self._lock = Lock()

    def attributes(
        self,
        values: Mapping[str, object] | None,
        *,
        system_values: Mapping[str, object] | None = None,
    ) -> dict[str, AttributeValue]:
        normalized = sanitize_attributes(
            values,
            allowed=frozenset(self._definition.allowed_attributes),
            maximum=len(self._definition.allowed_attributes),
        )
        normalized.update(sanitize_attributes(system_values, maximum=32))
        identity = tuple(
            (key, tuple(value) if isinstance(value, list) else value)
            for key, value in normalized.items()
        )
        with self._lock:
            if (
                identity not in self._series
                and len(self._series) >= self._definition.cardinality_limit
            ):
                raise CardinalityExceeded(self._definition.name)
            self._series.add(identity)
        return normalized

    @property
    def series_count(self) -> int:
        with self._lock:
            return len(self._series)


__all__ = [
    "AttributeScalar",
    "AttributeValue",
    "CardinalityGuard",
    "InstrumentDefinition",
    "is_denied_key",
    "safe_name",
    "sanitize_attributes",
    "sanitize_body",
]
