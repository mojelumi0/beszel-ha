"""Shared fixtures for Beszel API tests."""

from __future__ import annotations

import pytest

try:
    import pytest_homeassistant_custom_component
except ImportError:
    pytest_homeassistant_custom_component = None


if pytest_homeassistant_custom_component is not None:

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Enable loading custom integrations in Home Assistant tests."""
        yield
