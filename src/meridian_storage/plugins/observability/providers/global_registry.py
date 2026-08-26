# SPDX-License-Identifier: Apache-2.0
"""Idempotent and conflict-detecting process-wide provider installation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import Lock

from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger_provider, set_logger_provider

from ..errors import ProviderConflict
from .factory import ProviderBundle


@dataclass(frozen=True, slots=True)
class _Installation:
    fingerprint: str
    bundle: ProviderBundle


_lock = Lock()
_installation: _Installation | None = None


def _fingerprint(bundle: ProviderBundle) -> str:
    content = {
        "endpoint": bundle.config.endpoint,
        "protocol": bundle.config.protocol.value,
        "signals": sorted(item.value for item in bundle.config.signals),
        "batch": {
            "maxQueueSize": bundle.config.batch.max_queue_size,
            "maxExportBatchSize": bundle.config.batch.max_export_batch_size,
            "scheduleDelayMillis": bundle.config.batch.schedule_delay_millis,
            "exportTimeoutMillis": bundle.config.batch.export_timeout_millis,
        },
        "traceSampleRatio": bundle.config.trace_sample_ratio,
        "metricExportIntervalMillis": bundle.config.metric_export_interval_millis,
        "service": bundle.identity.name,
        "serviceVersion": bundle.identity.version,
        "environment": bundle.identity.deployment_environment,
        "attributes": dict(bundle.identity.attributes),
        "runtimeProfile": bundle.runtime_profile,
        "runtimeConfigFingerprint": bundle.runtime_config_fingerprint,
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _is_proxy(value: object) -> bool:
    return "Proxy" in value.__class__.__name__ and value.__class__.__module__.startswith(
        "opentelemetry"
    )


def install_global_provider_bundle(bundle: ProviderBundle) -> ProviderBundle:
    """Install once, reuse an identical installation, and fail on any conflict."""

    global _installation  # noqa: PLW0603
    fingerprint = _fingerprint(bundle)
    with _lock:
        if _installation is not None:
            if _installation.fingerprint != fingerprint:
                bundle.shutdown()
                raise ProviderConflict
            if _installation.bundle is not bundle:
                bundle.shutdown()
            return _installation.bundle
        current = (
            trace.get_tracer_provider(),
            metrics.get_meter_provider(),
            get_logger_provider(),
        )
        if any(not _is_proxy(provider) for provider in current):
            bundle.shutdown()
            raise ProviderConflict
        trace.set_tracer_provider(bundle.tracer_provider)
        metrics.set_meter_provider(bundle.meter_provider)
        set_logger_provider(bundle.logger_provider)
        _installation = _Installation(fingerprint, bundle)
        return bundle


__all__ = ["install_global_provider_bundle"]
