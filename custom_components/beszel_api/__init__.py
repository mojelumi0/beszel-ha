"""Set up the Beszel API integration."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BeszelApiClient,
    BeszelApiError,
    BeszelCannotConnect,
    BeszelInvalidAuth,
    BeszelUpdateApi,
)
from .const import (
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
    LOGGER,
)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.UPDATE]


def _system_is_online(system: Any) -> bool:
    """Return whether Beszel currently reports a system as online."""
    return getattr(system, "status", None) == "up"


async def async_setup_entry(hass, entry: ConfigEntry) -> bool:
    """Set up Beszel from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    url = entry.data[CONF_URL]
    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, True)
    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, 120)
    client = BeszelApiClient(url, username, password, verify_ssl)
    update_api = BeszelUpdateApi(client)

    LOGGER.debug(
        "Setting up Beszel entry %s (url=%s, verify_ssl=%s, update_interval=%ss)",
        entry.entry_id,
        url,
        verify_ssl,
        update_interval,
    )

    async def async_update_data() -> dict[str, Any]:
        """Fetch systems, current statistics, and S.M.A.R.T. data."""
        try:
            systems = await hass.async_add_executor_job(client.get_systems)

            # A valid restricted account may currently have no assigned
            # systems. Treat that as an empty result, not an auth failure.
            if not systems:
                return {"systems": [], "stats": {}, "smart_devices": {}}

            async def _fetch_stats(system: Any) -> tuple[str, dict[str, Any]]:
                stats = await hass.async_add_executor_job(
                    client.get_system_stats,
                    system.id,
                )
                stats_data = getattr(stats, "stats", None) if stats else None
                return system.id, stats_data if isinstance(stats_data, dict) else {}

            online_systems = [system for system in systems if _system_is_online(system)]
            stats_task = asyncio.gather(
                *(_fetch_stats(system) for system in online_systems)
            )
            smart_task = hass.async_add_executor_job(client.get_smart_devices)
            stats_results, all_smart = await asyncio.gather(stats_task, smart_task)

            stats_data = {system.id: {} for system in systems}
            stats_data.update(dict(stats_results))

            smart_devices: dict[str, list[dict[str, Any]]] = {}
            for device in all_smart:
                system_id = getattr(device, "system", None)
                if not system_id:
                    continue
                smart_devices.setdefault(system_id, []).append(
                    {
                        "id": device.id,
                        "name": getattr(device, "name", ""),
                        "model": getattr(device, "model", ""),
                        "state": getattr(device, "state", ""),
                        "temp": getattr(device, "temp", None),
                        "capacity": getattr(device, "capacity", 0),
                        "hours": getattr(device, "hours", 0),
                        "cycles": getattr(device, "cycles", 0),
                        "type": getattr(device, "type", ""),
                        "serial": getattr(device, "serial", ""),
                        "firmware": getattr(device, "firmware", ""),
                        "attributes": getattr(device, "attributes", None) or [],
                    }
                )

            LOGGER.debug(
                "Beszel update successful: systems=%d online=%d stats=%d smart=%d",
                len(systems),
                len(online_systems),
                len(stats_results),
                sum(len(devices) for devices in smart_devices.values()),
            )
            return {
                "systems": systems,
                "stats": stats_data,
                "smart_devices": smart_devices,
            }
        except BeszelInvalidAuth as err:
            raise ConfigEntryAuthFailed("Beszel authentication failed") from err
        except BeszelCannotConnect as err:
            raise UpdateFailed("Unable to connect to the Beszel Hub") from err
        except BeszelApiError as err:
            raise UpdateFailed(f"Beszel API error: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected Beszel update error: {err}") from err

    async def async_update_hub() -> dict[str, Any]:
        """Fetch Beszel Hub update information."""
        try:
            return await hass.async_add_executor_job(update_api.get_update_info)
        except BeszelInvalidAuth as err:
            raise ConfigEntryAuthFailed("Beszel authentication failed") from err
        except BeszelCannotConnect as err:
            raise UpdateFailed("Unable to connect to the Beszel Hub") from err
        except BeszelApiError as err:
            raise UpdateFailed(f"Unable to fetch Beszel Hub information: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected Beszel Hub update error: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        LOGGER,
        name="Beszel API",
        config_entry=entry,
        update_method=async_update_data,
        update_interval=timedelta(seconds=update_interval),
    )
    hub_coordinator = DataUpdateCoordinator(
        hass,
        LOGGER,
        name="Beszel Hub",
        config_entry=entry,
        update_method=async_update_hub,
        update_interval=timedelta(hours=1),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
        # Hub update metadata is optional and must not prevent monitoring from
        # loading if Beszel's update endpoint is temporarily unavailable.
        await hub_coordinator.async_refresh()

        hass.data[DOMAIN][entry.entry_id] = {
            "client": client,
            "coordinator": coordinator,
            "hub": hub_coordinator,
        }
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await coordinator.async_shutdown()
        await hub_coordinator.async_shutdown()
        await hass.async_add_executor_job(client.close)
        raise

    LOGGER.debug("Successfully set up Beszel platforms for entry %s", entry.entry_id)
    return True


async def async_unload_entry(hass, entry: ConfigEntry) -> bool:
    """Unload a config entry and close all associated resources."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data:
        await data["coordinator"].async_shutdown()
        await data["hub"].async_shutdown()
        await hass.async_add_executor_job(data["client"].close)
    return True


async def async_migrate_entry(hass, config_entry: ConfigEntry) -> bool:
    """Migrate old entries to the current data format."""
    if config_entry.version <= 2:
        new_data = {**config_entry.data}
        new_data.setdefault(CONF_VERIFY_SSL, True)
        new_data.setdefault(CONF_UPDATE_INTERVAL, 120)
        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            version=3,
        )

    return True
