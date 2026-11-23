"""Tests for integration setup and teardown."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.beszel_api import (
    _async_fetch_all_system_stats,
    _async_fetch_smart_devices,
    _async_fetch_system_stats,
    async_migrate_entry,
    async_unload_entry,
)
from custom_components.beszel_api.api import (
    BeszelApiError,
    BeszelCannotConnect,
    BeszelInvalidAuth,
)
from custom_components.beszel_api.const import (
    CONF_UPDATE_INTERVAL,
    CONF_VERIFY_SSL,
    DOMAIN,
)


def _executor_hass() -> MagicMock:
    """Return a mock that executes executor jobs immediately."""
    hass = MagicMock()

    async def async_add_executor_job(target, *args):
        return target(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=async_add_executor_job)
    return hass


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("configured", [False, True])
async def test_legacy_entry_migration_preserves_connection_settings(version, configured):
    """Keep upstream's version-3 migration and existing user choices intact."""
    data = {"url": "https://beszel.example", "username": "user", "password": "secret"}
    if configured:
        data.update({CONF_VERIFY_SSL: False, CONF_UPDATE_INTERVAL: 30})
    entry = MockConfigEntry(domain=DOMAIN, version=version, data=data)
    hass = MagicMock()

    assert await async_migrate_entry(hass, entry) is True
    expected = {CONF_VERIFY_SSL: True, CONF_UPDATE_INTERVAL: 120, **data}
    hass.config_entries.async_update_entry.assert_called_once_with(
        entry, data=expected, version=3
    )


async def test_current_entry_does_not_run_legacy_migration():
    """Current upstream entries must not be rewritten by the port."""
    entry = MockConfigEntry(domain=DOMAIN, version=3, data={"custom": "kept"})
    hass = MagicMock()
    assert await async_migrate_entry(hass, entry) is True
    hass.config_entries.async_update_entry.assert_not_called()


async def test_stats_are_loaded_for_offline_system_entity_discovery() -> None:
    """Offline systems still need their last stats to create optional entities."""
    online = SimpleNamespace(id="online", status="up")
    offline = SimpleNamespace(id="offline", status="down")
    client = MagicMock()
    client.get_system_stats.side_effect = lambda system_id: SimpleNamespace(
        stats={"source": system_id}
    )

    result = await _async_fetch_all_system_stats(
        _executor_hass(),
        client,
        [online, offline],
    )

    assert result == {
        "online": {"source": "online"},
        "offline": {"source": "offline"},
    }


async def test_one_system_stats_failure_is_isolated() -> None:
    """One invalid statistics response must not fail the other systems."""
    healthy = SimpleNamespace(id="healthy")
    broken = SimpleNamespace(id="broken")
    client = MagicMock()

    def get_system_stats(system_id: str) -> SimpleNamespace:
        if system_id == "broken":
            raise BeszelApiError("invalid stats")
        return SimpleNamespace(stats={"cpu": 10})

    client.get_system_stats.side_effect = get_system_stats

    assert await _async_fetch_all_system_stats(
        _executor_hass(),
        client,
        [healthy, broken],
    ) == {"healthy": {"cpu": 10}, "broken": {}}


async def test_optional_smart_failure_is_isolated() -> None:
    """Unavailable S.M.A.R.T. data must not break normal monitoring."""
    client = MagicMock()
    client.get_smart_devices.side_effect = BeszelApiError("not available")

    assert await _async_fetch_smart_devices(_executor_hass(), client) == []


@pytest.mark.parametrize("error", [BeszelInvalidAuth(), BeszelCannotConnect()])
async def test_auth_and_connection_failures_are_not_hidden(error: Exception) -> None:
    """Entry-level authentication and connection failures must still propagate."""
    system = SimpleNamespace(id="system-1")
    client = MagicMock()
    client.get_system_stats.side_effect = error

    with pytest.raises(type(error)):
        await _async_fetch_system_stats(_executor_hass(), client, system)


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
