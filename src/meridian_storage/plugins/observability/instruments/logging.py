# SPDX-License-Identifier: Apache-2.0
"""Structured OTel logger with default-deny payload policy."""

from __future__ import annotations

from time import time_ns
from typing import Any, cast

from opentelemetry._logs import Logger, SeverityNumber

from ..context import ContextPolicy, correlation_attributes
from ..errors import InvalidInstrument
from ..policy import safe_name, sanitize_attributes, sanitize_body
from ..providers import instrumentation_suppressed

_SEVERITIES = {
    "TRACE": SeverityNumber.TRACE,
    "DEBUG": SeverityNumber.DEBUG,
    "INFO": SeverityNumber.INFO,
    "WARN": SeverityNumber.WARN,
    "ERROR": SeverityNumber.ERROR,
    "FATAL": SeverityNumber.FATAL,
}


class StructuredLogger:
    def __init__(self, logger: Logger, context_policy: ContextPolicy) -> None:
        self._logger = logger
        self._context_policy = context_policy

    def emit(
        self,
        severity: str,
        body: object,
        attributes: dict[str, object] | None = None,
        *,
        event_name: str | None = None,
        exception: BaseException | None = None,
    ) -> None:
        if instrumentation_suppressed():
            return
        if not isinstance(severity, str):
            raise InvalidInstrument("severity must be TRACE, DEBUG, INFO, WARN, ERROR, or FATAL")
        normalized_severity = severity.upper()
        number = _SEVERITIES.get(normalized_severity)
        if number is None:
            raise InvalidInstrument("severity must be TRACE, DEBUG, INFO, WARN, ERROR, or FATAL")
        safe_attributes = sanitize_attributes(attributes)
        safe_attributes.update(correlation_attributes(self._context_policy))
        if exception is not None:
            safe_attributes.update(
                sanitize_attributes(
                    {
                        "exception.type": (
                            f"{type(exception).__module__}.{type(exception).__qualname__}"
                        )
                    }
                )
            )
        self._logger.emit(
            timestamp=time_ns(),
            observed_timestamp=time_ns(),
            severity_number=number,
            severity_text=normalized_severity,
            body=cast(Any, sanitize_body(body)),
            attributes=cast(Any, safe_attributes),
            event_name=None if event_name is None else safe_name(event_name, "event name"),
        )

    def trace(self, body: object, attributes: dict[str, object] | None = None) -> None:
        self.emit("TRACE", body, attributes)

    def debug(self, body: object, attributes: dict[str, object] | None = None) -> None:
        self.emit("DEBUG", body, attributes)

    def info(self, body: object, attributes: dict[str, object] | None = None) -> None:
        self.emit("INFO", body, attributes)

    def warning(self, body: object, attributes: dict[str, object] | None = None) -> None:
        self.emit("WARN", body, attributes)

    warn = warning

    def error(
        self,
        body: object,
        attributes: dict[str, object] | None = None,
        *,
        exception: BaseException | None = None,
    ) -> None:
        self.emit("ERROR", body, attributes, exception=exception)

    def critical(
        self,
        body: object,
        attributes: dict[str, object] | None = None,
        *,
        exception: BaseException | None = None,
    ) -> None:
        self.emit("FATAL", body, attributes, exception=exception)


__all__ = ["StructuredLogger"]
