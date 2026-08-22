import asyncio
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.config_entries import ConfigEntry
from .const import DOMAIN, CONF_URL, CONF_USERNAME, CONF_PASSWORD, CONF_VERIFY_SSL, CONF_UPDATE_INTERVAL, LOGGER
from .api import BeszelApiClient, BeszelUpdateApi

PLATFORMS = ["sensor", "binary_sensor", "update"]

async def async_setup_entry(hass, entry):
    hass.data.setdefault(DOMAIN, {})

    url = entry.data[CONF_URL]
    username = entry.data.get(CONF_USERNAME, None)
    password = entry.data.get(CONF_PASSWORD, None)
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, True)
    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, 120)
    client = BeszelApiClient(url, username, password, verify_ssl)
    LOGGER.debug(
        "Setting up Beszel entry %s (url=%s, verify_ssl=%s, update_interval=%ss)",
        entry.entry_id,
        url,
        verify_ssl,
        update_interval,
    )

    async def async_update_data():
        try:
            systems = await hass.async_add_executor_job(client.get_systems)
            system_names = [getattr(system, "name", system.id) for system in systems]
            LOGGER.debug(
                "Fetched %d systems from Beszel: %s",
                len(systems),
                ", ".join(system_names) if system_names else "none",
            )

            if not systems:
                LOGGER.warning("No systems found in Beszel API")
                return {"systems": [], "stats": {}}

            # Fetch stats for all systems concurrently instead of one by one
            async def _fetch_stats(system):
                system_name = getattr(system, "name", system.id)
                try:
                    LOGGER.debug("Fetching stats for system %s (%s)", system_name, system.id)
                    stats = await hass.async_add_executor_job(client.get_system_stats, system.id)
                    data = stats.stats if stats and hasattr(stats, "stats") else {}
                    LOGGER.debug(
                        "Received stats for system %s (%s) with keys: %s",
                        system_name,
                        system.id,
                        ", ".join(data.keys()) if data else "none",
                    )
                    return system.id, data
                except Exception as e:
                    LOGGER.warning("Failed to fetch stats for system %s: %s", system.id, e, exc_info=True)
                    return system.id, {}

            results = await asyncio.gather(*(_fetch_stats(system) for system in systems))
            stats_data = dict(results)

            # Fetch S.M.A.R.T. devices data
            smart_devices = {}
            try:
                all_smart = await hass.async_add_executor_job(client.get_smart_devices)
                for device in all_smart:
                    system_id = getattr(device, 'system', None)
                    if system_id:
                        if system_id not in smart_devices:
                            smart_devices[system_id] = []
                        smart_devices[system_id].append({
                            'id': device.id,
                            'name': getattr(device, 'name', ''),
                            'model': getattr(device, 'model', ''),
                            'state': getattr(device, 'state', ''),
                            'temp': getattr(device, 'temp', None),
                            'capacity': getattr(device, 'capacity', 0),
                            'hours': getattr(device, 'hours', 0),
                            'cycles': getattr(device, 'cycles', 0),
                            'type': getattr(device, 'type', ''),
                            'serial': getattr(device, 'serial', ''),
                            'firmware': getattr(device, 'firmware', ''),
                            'attributes': getattr(device, 'attributes', None) or [],
                        })
                LOGGER.debug(
                    "Loaded S.M.A.R.T. data for %d devices across %d systems",
                    len(all_smart),
                    len(smart_devices),
                )
            except Exception as e:
                LOGGER.warning("Failed to fetch S.M.A.R.T. devices: %s", e, exc_info=True)

            LOGGER.info(
                "Beszel update successful: systems=%d stats=%d smart_devices=%d systems_with_smart=%d",
                len(systems),
                len(stats_data),
                sum(len(devices) for devices in smart_devices.values()),
                len(smart_devices),
            )
            return {"systems": systems, "stats": stats_data, "smart_devices": smart_devices}
        except Exception as err:
            LOGGER.error("Error fetching systems: %s", err, exc_info=True)
            raise UpdateFailed(f"Error fetching systems: {err}")

    coordinator = DataUpdateCoordinator(
        hass,
        LOGGER,
        name="Beszel API",
        update_method=async_update_data,
        update_interval=timedelta(seconds=update_interval),
    )

    coordinator_hub = None
    
    update_api = BeszelUpdateApi(client)
    
    async def async_update_hub():
        try:
            return await hass.async_add_executor_job(update_api.get_update_info)
        except Exception as err:
            LOGGER.error("Error fetching hub update info: %s", err, exc_info=True)
            raise UpdateFailed(f"Error fetching hub update info: {err}")
    coordinator_hub = DataUpdateCoordinator(
        hass,
        LOGGER,
        name="Beszel Hub",
        update_method=async_update_hub,
        update_interval=timedelta(hours=1),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
        if coordinator_hub is not None:
            await coordinator_hub.async_config_entry_first_refresh()
    except Exception as e:
        LOGGER.error("Failed to initialize coordinator for entry %s: %s", entry.entry_id, e, exc_info=True)
        raise

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "hub": coordinator_hub,
    }

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as e:
        LOGGER.error("Failed to setup platforms for entry %s: %s", entry.entry_id, e, exc_info=True)
        raise
    LOGGER.debug("Successfully set up Beszel platforms for entry %s", entry.entry_id)
    return True

async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    # Close coordinator to stop updates and cleanup connections
    data = hass.data[DOMAIN].get(entry.entry_id)
    if data:
        coordinator = data.get("coordinator")
        if coordinator:
            coordinator.async_shutdown()
        hub_coordinator = data.get("hub")
        if hub_coordinator:
            hub_coordinator.async_shutdown()
    
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

async def async_migrate_entry(hass, config_entry: ConfigEntry):
    """Migrate old entry to the new version."""
    if config_entry.version <= 2:
        new_data = {**config_entry.data}
        if CONF_VERIFY_SSL not in new_data:
            new_data[CONF_VERIFY_SSL] = True
        if CONF_UPDATE_INTERVAL not in new_data:
            new_data[CONF_UPDATE_INTERVAL] = 120

        hass.config_entries.async_update_entry(config_entry, data=new_data, version=3)

    return True
