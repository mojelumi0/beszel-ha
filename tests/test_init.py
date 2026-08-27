"""Tests for integration setup and teardown."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.beszel_api import async_unload_entry
from custom_components.beszel_api.const import DOMAIN


async def test_unload_awaits_shutdown_and_closes_client(hass) -> None:
    """Reloading an entry must not leak coordinators or HTTP connections."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="test-entry")
    coordinator = MagicMock()
    coordinator.async_shutdown = AsyncMock()
    hub_coordinator = MagicMock()
    hub_coordinator.async_shutdown = AsyncMock()
    client = MagicMock()
    hass.data[DOMAIN] = {
        entry.entry_id: {
            "coordinator": coordinator,
            "hub": hub_coordinator,
            "client": client,
        }
    }

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        assert await async_unload_entry(hass, entry) is True

    coordinator.async_shutdown.assert_awaited_once_with()
    hub_coordinator.async_shutdown.assert_awaited_once_with()
    client.close.assert_called_once_with()
    assert entry.entry_id not in hass.data[DOMAIN]
