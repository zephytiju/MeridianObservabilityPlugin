# SPDX-License-Identifier: Apache-2.0
"""Safe Meridian context correlation and bounded baggage propagation."""

from .propagation import ContextPolicy, correlation_attributes

__all__ = ["ContextPolicy", "correlation_attributes"]
