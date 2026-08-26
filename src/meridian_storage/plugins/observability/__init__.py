# SPDX-License-Identifier: Apache-2.0
"""Meridian V1 governed observability plugin."""

from ._version import __version__
from .config import (
    BatchPolicy,
    DeploymentTelemetryConfig,
    EvidenceResources,
    OTLPProtocol,
    ServiceIdentity,
    Signal,
)
from .context import ContextPolicy
from .errors import (
    CardinalityExceeded,
    InvalidInstrument,
    InvalidObservabilityConfiguration,
    InvalidResourceIdentity,
    InvalidTelemetryQuery,
    ObservabilityError,
    ObservabilityErrorCode,
    ProviderConflict,
    RuntimeNotReady,
    ShutdownFailed,
)
from .instruments import (
    DatabasePack,
    HttpServerPack,
    MessagingPack,
    SafeCounter,
    SafeGauge,
    SafeHistogram,
    SafeMeter,
    SafeSpan,
    SafeTracer,
    SafeUpDownCounter,
    StructuredLogger,
)
from .plugin import Observability, ObservabilityPluginFactory
from .providers import ShutdownReport
from .query import (
    DerivedTelemetryQuery,
    ServiceHealthSnapshot,
    TelemetryQueries,
    TelemetryQuery,
    TelemetryQueryResult,
)

__all__ = [
    "BatchPolicy",
    "CardinalityExceeded",
    "ContextPolicy",
    "DatabasePack",
    "DeploymentTelemetryConfig",
    "DerivedTelemetryQuery",
    "EvidenceResources",
    "HttpServerPack",
    "InvalidInstrument",
    "InvalidObservabilityConfiguration",
    "InvalidResourceIdentity",
    "InvalidTelemetryQuery",
    "MessagingPack",
    "OTLPProtocol",
    "Observability",
    "ObservabilityError",
    "ObservabilityErrorCode",
    "ObservabilityPluginFactory",
    "ProviderConflict",
    "RuntimeNotReady",
    "SafeCounter",
    "SafeGauge",
    "SafeHistogram",
    "SafeMeter",
    "SafeSpan",
    "SafeTracer",
    "SafeUpDownCounter",
    "ServiceHealthSnapshot",
    "ServiceIdentity",
    "ShutdownFailed",
    "ShutdownReport",
    "Signal",
    "StructuredLogger",
    "TelemetryQueries",
    "TelemetryQuery",
    "TelemetryQueryResult",
    "__version__",
]
