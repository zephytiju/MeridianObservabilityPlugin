# SPDX-License-Identifier: Apache-2.0
"""Stable, redaction-safe public errors for the observability plugin."""

from __future__ import annotations

from enum import StrEnum


class ObservabilityErrorCode(StrEnum):
    CONFIG_INVALID = "OBSERVABILITY_CONFIG_INVALID"
    RUNTIME_NOT_READY = "OBSERVABILITY_RUNTIME_NOT_READY"
    PROVIDER_CONFLICT = "OBSERVABILITY_PROVIDER_CONFLICT"
    RESOURCE_INVALID = "OBSERVABILITY_RESOURCE_INVALID"
    INSTRUMENT_INVALID = "OBSERVABILITY_INSTRUMENT_INVALID"
    CARDINALITY_EXCEEDED = "OBSERVABILITY_CARDINALITY_EXCEEDED"
    QUERY_INVALID = "OBSERVABILITY_QUERY_INVALID"
    SHUTDOWN_FAILED = "OBSERVABILITY_SHUTDOWN_FAILED"


class ObservabilityError(Exception):
    """Base exception whose message is safe to return at an API boundary."""

    def __init__(self, code: ObservabilityErrorCode, message: str) -> None:
        self.code = code
        self.safe_message = message
        super().__init__(f"{code.value}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.safe_message}


class InvalidObservabilityConfiguration(ObservabilityError):
    def __init__(self, message: str) -> None:
        super().__init__(ObservabilityErrorCode.CONFIG_INVALID, message)


class RuntimeNotReady(ObservabilityError):
    def __init__(self) -> None:
        super().__init__(
            ObservabilityErrorCode.RUNTIME_NOT_READY,
            "Observability requires a started Meridian runtime",
        )


class ProviderConflict(ObservabilityError):
    def __init__(self) -> None:
        super().__init__(
            ObservabilityErrorCode.PROVIDER_CONFLICT,
            "an incompatible process-wide OpenTelemetry provider is already installed",
        )


class InvalidResourceIdentity(ObservabilityError):
    def __init__(self, message: str) -> None:
        super().__init__(ObservabilityErrorCode.RESOURCE_INVALID, message)


class InvalidInstrument(ObservabilityError):
    def __init__(self, message: str) -> None:
        super().__init__(ObservabilityErrorCode.INSTRUMENT_INVALID, message)


class CardinalityExceeded(ObservabilityError):
    def __init__(self, instrument_name: str) -> None:
        super().__init__(
            ObservabilityErrorCode.CARDINALITY_EXCEEDED,
            f"metric cardinality budget exhausted for {instrument_name!r}",
        )


class InvalidTelemetryQuery(ObservabilityError):
    def __init__(self, message: str) -> None:
        super().__init__(ObservabilityErrorCode.QUERY_INVALID, message)


class ShutdownFailed(ObservabilityError):
    def __init__(self) -> None:
        super().__init__(
            ObservabilityErrorCode.SHUTDOWN_FAILED,
            "one or more OpenTelemetry providers did not shut down cleanly",
        )


__all__ = [
    "CardinalityExceeded",
    "InvalidInstrument",
    "InvalidObservabilityConfiguration",
    "InvalidResourceIdentity",
    "InvalidTelemetryQuery",
    "ObservabilityError",
    "ObservabilityErrorCode",
    "ProviderConflict",
    "RuntimeNotReady",
    "ShutdownFailed",
]
