# SPDX-License-Identifier: Apache-2.0
"""Translate current Meridian/OTel context without leaking authorization scope."""

from __future__ import annotations

import re
from dataclasses import dataclass

from opentelemetry import baggage, trace

from meridian_storage import current_context

from ..errors import InvalidObservabilityConfiguration
from ..policy import AttributeValue, is_denied_key, sanitize_attributes

_BAGGAGE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    baggage_allowlist: tuple[str, ...] = ()
    scope_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.baggage_allowlist) > 24 or len(self.scope_allowlist) > 24:
            raise InvalidObservabilityConfiguration(
                "context allowlists may contain at most 24 entries each"
            )
        for value in (*self.baggage_allowlist, *self.scope_allowlist):
            if (
                not isinstance(value, str)
                or _BAGGAGE_KEY.fullmatch(value) is None
                or is_denied_key(value)
            ):
                raise InvalidObservabilityConfiguration("context allowlists contain an invalid key")
        if len(set(self.baggage_allowlist)) != len(self.baggage_allowlist):
            raise InvalidObservabilityConfiguration("baggage_allowlist entries must be unique")
        if len(set(self.scope_allowlist)) != len(self.scope_allowlist):
            raise InvalidObservabilityConfiguration("scope_allowlist entries must be unique")


def correlation_attributes(
    policy: ContextPolicy,
    *,
    include_request: bool = True,
) -> dict[str, AttributeValue]:
    """Return non-secret correlation fields; principal and tenant are never copied."""

    values: dict[str, object] = {}
    context = current_context(required=False)
    if context is not None:
        if include_request and context.request_id is not None:
            values["meridian.request.id"] = context.request_id
        if include_request and context.correlation_id is not None:
            values["meridian.correlation.id"] = context.correlation_id
        for key in policy.scope_allowlist:
            if key in context.scope:
                values[f"meridian.scope.{key}"] = context.scope[key]
    for key in policy.baggage_allowlist:
        value = baggage.get_baggage(key)
        if isinstance(value, str | bool | int | float):
            values[f"baggage.{key}"] = value
    span_context = trace.get_current_span().get_span_context()
    if include_request and span_context.is_valid:
        values["trace_id"] = format(span_context.trace_id, "032x")
        values["span_id"] = format(span_context.span_id, "016x")
    return sanitize_attributes(values)
