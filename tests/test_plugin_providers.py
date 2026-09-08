# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import subprocess
import sys
from importlib import metadata

import pytest
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from conftest import ReadyMeridian
from meridian_storage import RuntimeState
from meridian_storage.plugins.observability import (
    DeploymentTelemetryConfig,
    InvalidObservabilityConfiguration,
    Observability,
    ObservabilityPluginFactory,
    RuntimeNotReady,
    ServiceIdentity,
    Signal,
)
from meridian_storage.plugins.observability.providers import build_provider_bundle
from meridian_storage.plugins.observability.providers import factory as provider_factory
from meridian_storage.spi import PluginFactory


class NewMeridian(ReadyMeridian):
    @property
    def state(self) -> RuntimeState:
        return RuntimeState.NEW


def test_plugin_manifest_and_installed_entry_point_match_released_core_contract() -> None:
    factory = ObservabilityPluginFactory()
    assert isinstance(factory, PluginFactory)
    manifest = factory.manifest()
    assert manifest.plugin_id == "observability"
    assert manifest.plugin_version == "1.0.3"
    assert manifest.core_contract == "1.x"
    assert manifest.extensions["distribution"] == "meridian-plugin-observability"
    points = metadata.entry_points().select(group="meridian_storage.plugins")
    point = next(item for item in points if item.name == "observability")
    assert point.dist is not None
    assert point.dist.name == "meridian-plugin-observability"
    assert isinstance(point.load()(), PluginFactory)


def test_observability_requires_ready_runtime_and_logical_query_resources() -> None:
    with pytest.raises(RuntimeNotReady):
        Observability(
            NewMeridian(),
            service_name="test",
            service_version="1.0.0",
            deployment_environment="test",
        )
    with pytest.raises(InvalidObservabilityConfiguration):
        ObservabilityPluginFactory().create(ReadyMeridian())


def test_domain_packs_and_shutdown_report(observability: tuple[object, ...]) -> None:
    plugin = observability[0]
    assert plugin.service_identity.name == "test-service"  # type: ignore[union-attr]
    assert not plugin.is_global  # type: ignore[union-attr]
    assert plugin.queries()  # type: ignore[union-attr]
    http = plugin.http_server("tests.http")  # type: ignore[union-attr]
    http.requests.add(
        1,
        {
            "http.request.method": "GET",
            "http.response.status_code": 200,
            "http.route": "/orders/{id}",
        },
    )
    messaging = plugin.messaging("tests.messaging")  # type: ignore[union-attr]
    messaging.messages.add(
        1,
        {"messaging.system": "kafka", "messaging.operation.name": "process"},
    )
    database = plugin.database("tests.database")  # type: ignore[union-attr]
    database.operations.add(
        1,
        {"db.system.name": "postgresql", "db.operation.name": "SELECT"},
    )
    report = plugin.close()  # type: ignore[union-attr]
    assert report.clean
    assert report.to_dict()["clean"] is True
    assert plugin.close() is report  # type: ignore[union-attr]
    with pytest.raises(InvalidObservabilityConfiguration, match="timeout_millis"):
        plugin.force_flush(0)  # type: ignore[union-attr]


def test_process_global_installation_is_idempotent_and_conflict_detecting() -> None:
    script = r"""
from meridian_storage.plugins.observability.config import DeploymentTelemetryConfig, ServiceIdentity
from meridian_storage.plugins.observability.errors import ProviderConflict
from meridian_storage.plugins.observability.providers import (
    build_provider_bundle,
    install_global_provider_bundle,
)

config = DeploymentTelemetryConfig("http://collector.invalid:4317")
first = build_provider_bundle(ServiceIdentity("global-test", "1.0.0", "test"), config)
assert install_global_provider_bundle(first) is first
same = build_provider_bundle(ServiceIdentity("global-test", "1.0.0", "test"), config)
assert install_global_provider_bundle(same) is first
conflict = build_provider_bundle(ServiceIdentity("other-service", "1.0.0", "test"), config)
try:
    install_global_provider_bundle(conflict)
except ProviderConflict:
    conflict.shutdown()
else:
    raise AssertionError("provider conflict was not detected")
first.shutdown()
print("global-provider-conformance-ok")
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "global-provider-conformance-ok"


def test_disabled_signals_do_not_allocate_unused_default_exporters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans = InMemorySpanExporter()

    def default_spans(_config: DeploymentTelemetryConfig) -> SpanExporter:
        return spans

    def unexpected(_config: DeploymentTelemetryConfig) -> None:
        raise AssertionError("disabled signal exporter was allocated")

    monkeypatch.setattr(provider_factory, "_default_span_exporter", default_spans)
    monkeypatch.setattr(provider_factory, "_default_metric_exporter", unexpected)
    monkeypatch.setattr(provider_factory, "_default_log_exporter", unexpected)
    bundle = build_provider_bundle(
        ServiceIdentity("signal-test", "1.0.0", "test"),
        DeploymentTelemetryConfig(
            "http://collector.invalid:4317",
            signals=frozenset({Signal.TRACES}),
        ),
    )
    assert bundle.shutdown().clean
