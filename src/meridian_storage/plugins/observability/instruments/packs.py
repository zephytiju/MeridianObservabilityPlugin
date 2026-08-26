# SPDX-License-Identifier: Apache-2.0
"""Small, governed domain packs built only from the plugin's public facades."""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import SafeCounter, SafeHistogram, SafeMeter, SafeUpDownCounter


@dataclass(frozen=True, slots=True)
class HttpServerPack:
    requests: SafeCounter
    active_requests: SafeUpDownCounter
    duration: SafeHistogram

    @classmethod
    def create(cls, meter: SafeMeter) -> HttpServerPack:
        dimensions = ("http.request.method", "http.response.status_code", "http.route")
        return cls(
            requests=meter.create_counter(
                "http.server.requests",
                unit="{request}",
                description="Completed inbound HTTP requests",
                allowed_attributes=dimensions,
            ),
            active_requests=meter.create_up_down_counter(
                "http.server.active_requests",
                unit="{request}",
                description="Active inbound HTTP requests",
                allowed_attributes=("http.request.method",),
            ),
            duration=meter.create_histogram(
                "http.server.duration",
                unit="s",
                description="Inbound HTTP request duration",
                allowed_attributes=dimensions,
            ),
        )


@dataclass(frozen=True, slots=True)
class MessagingPack:
    messages: SafeCounter
    duration: SafeHistogram

    @classmethod
    def create(cls, meter: SafeMeter) -> MessagingPack:
        dimensions = ("messaging.system", "messaging.operation.name", "error.type")
        return cls(
            messages=meter.create_counter(
                "messaging.client.messages",
                unit="{message}",
                description="Messaging operations",
                allowed_attributes=dimensions,
            ),
            duration=meter.create_histogram(
                "messaging.client.operation.duration",
                unit="s",
                description="Messaging operation duration",
                allowed_attributes=dimensions,
            ),
        )


@dataclass(frozen=True, slots=True)
class DatabasePack:
    operations: SafeCounter
    duration: SafeHistogram

    @classmethod
    def create(cls, meter: SafeMeter) -> DatabasePack:
        dimensions = ("db.system.name", "db.operation.name", "error.type")
        return cls(
            operations=meter.create_counter(
                "db.client.operations",
                unit="{operation}",
                description="Database client operations without query text",
                allowed_attributes=dimensions,
            ),
            duration=meter.create_histogram(
                "db.client.operation.duration",
                unit="s",
                description="Database client operation duration",
                allowed_attributes=dimensions,
            ),
        )


__all__ = ["DatabasePack", "HttpServerPack", "MessagingPack"]
