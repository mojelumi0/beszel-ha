from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOGGER, SMART_CURATED_ATTRIBUTES


def _build_system_binary_sensors(
    coordinator,
    system,
    smart_devices_data,
    stats_data,
):
    """Build status, S.M.A.R.T., and ZFS health entities for one system."""
    entities = [BeszelStatusBinarySensor(coordinator, system)]
    system_smart_devices = smart_devices_data.get(system.id, [])
    if isinstance(system_smart_devices, (list, tuple)):
        for device in system_smart_devices:
            if not isinstance(device, dict) or not device.get("id"):
                continue
            entities.append(BeszelSmartBinarySensor(coordinator, system, device))

    system_stats = stats_data.get(system.id, {})
    if not isinstance(system_stats, dict):
        return entities
    zfs_pools = system_stats.get("z")
    if not isinstance(zfs_pools, dict):
        return entities
    for pool_name, pool_data in zfs_pools.items():
        if (
            isinstance(pool_name, str)
            and isinstance(pool_data, dict)
            and isinstance(pool_data.get("h"), str)
            and pool_data["h"]
        ):
            entities.append(
                BeszelZFSHealthBinarySensor(coordinator, system, pool_name)
            )
    return entities


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up binary sensors and discover later S.M.A.R.T. devices."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    known_unique_ids = set()

    def discover_entities():
        coordinator_data = coordinator.data
        if not isinstance(coordinator_data, dict):
            return []

        systems = coordinator_data.get("systems", [])
        smart_devices_data = coordinator_data.get("smart_devices", {})
        stats_data = coordinator_data.get("stats", {})
        if not isinstance(systems, (list, tuple)):
            return []
        if not isinstance(smart_devices_data, dict):
            smart_devices_data = {}
        if not isinstance(stats_data, dict):
            stats_data = {}

        new_entities = []
        for system in systems:
            if not getattr(system, "id", None):
                continue
            try:
                candidates = _build_system_binary_sensors(
                    coordinator,
                    system,
                    smart_devices_data,
                    stats_data,
                )
            except Exception as err:  # noqa: BLE001
                LOGGER.warning(
                    "Failed to discover binary sensors for system %s: %s",
                    getattr(system, "name", "unknown"),
                    err,
                    exc_info=True,
                )
                continue

            for entity in candidates:
                unique_id = entity.unique_id
                if unique_id in known_unique_ids:
                    continue
                known_unique_ids.add(unique_id)
                new_entities.append(entity)

        return new_entities

    initial_entities = discover_entities()
    LOGGER.debug("Created %d binary sensors total", len(initial_entities))
    async_add_entities(initial_entities)

    def discover_new_entities():
        new_entities = discover_entities()
        if new_entities:
            LOGGER.debug(
                "Discovered %d new Beszel binary sensors",
                len(new_entities),
            )
            async_add_entities(new_entities)

    remove_listener = coordinator.async_add_listener(discover_new_entities)
    async_on_unload = getattr(entry, "async_on_unload", None)
    if callable(async_on_unload):
        async_on_unload(remove_listener)


class BeszelBaseBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Base class for Beszel binary sensors"""
    def __init__(self, coordinator, system):
        super().__init__(coordinator)
        self._system_id = system.id

    @property
    def system(self):
        coordinator_data = self.coordinator.data
        if not isinstance(coordinator_data, dict):
            return None
        systems = coordinator_data.get("systems", [])
        if not isinstance(systems, (list, tuple)):
            return None
        for s in systems:
            if getattr(s, "id", None) == self._system_id:
                return s
        return None

    @property
    def available(self):
        """Return availability based on the Hub update and system presence."""
        return self.coordinator.last_update_success and self.system is not None

    @property
    def device_info(self):
        sys = self.system
        if sys is None:
            return None
        raw_info = getattr(sys, "info", None)
        info = raw_info if isinstance(raw_info, dict) else {}
        return {
            "identifiers": {(DOMAIN, sys.id)},
            "name": sys.name,
            "manufacturer": "Beszel",
            "model": info.get("m"),
            "sw_version": info.get("v"),
            "hw_version": info.get("k"),
        }


class BeszelStatusBinarySensor(BeszelBaseBinarySensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_status"

    @property
    def name(self):
        return f"{self.system.name} Status" if self.system else None

    @property
    def is_on(self):
        return self.system.status == "up" if self.system else False

    @property
    def device_class(self):
        return BinarySensorDeviceClass.CONNECTIVITY


class BeszelZFSHealthBinarySensor(BeszelBaseBinarySensor):
    """Report whether a ZFS pool is in a non-ONLINE health state."""

    def __init__(self, coordinator, system, pool_name):
        super().__init__(coordinator, system)
        self._pool_name = pool_name

    @property
    def pool_data(self):
        coordinator_data = self.coordinator.data
        if not isinstance(coordinator_data, dict):
            return {}
        stats_data = coordinator_data.get("stats", {})
        if not isinstance(stats_data, dict):
            return {}
        system_stats = stats_data.get(self._system_id, {})
        if not isinstance(system_stats, dict):
            return {}
        pools = system_stats.get("z")
        if not isinstance(pools, dict):
            return {}
        pool_data = pools.get(self._pool_name)
        return pool_data if isinstance(pool_data, dict) else {}

    @property
    def health(self):
        health = self.pool_data.get("h")
        return health if isinstance(health, str) and health else None

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_zfs_pool_{self._pool_name}_health"

    @property
    def name(self):
        if not self.system:
            return None
        display_name = self.pool_data.get("n")
        pool_label = (
            display_name
            if isinstance(display_name, str) and display_name
            else self._pool_name
        )
        return f"{self.system.name} ZFS {pool_label} Health"

    @property
    def available(self):
        return (
            super().available
            and getattr(self.system, "status", None) == "up"
            and self.health is not None
        )

    @property
    def is_on(self):
        health = self.health
        return health.upper() != "ONLINE" if health is not None else None

    @property
    def device_class(self):
        return BinarySensorDeviceClass.PROBLEM

    @property
    def icon(self):
        return "mdi:database-alert" if self.is_on else "mdi:database-check"

    @property
    def extra_state_attributes(self):
        return {"health_state": self.health} if self.health is not None else {}


class BeszelSmartBinarySensor(BeszelBaseBinarySensor):
    """Binary sensor for disk S.M.A.R.T. status with all data in attributes"""
    
    def __init__(self, coordinator, system, device_data):
        super().__init__(coordinator, system)
        self._device_id = device_data.get("id", "")
        device_name = device_data.get("name", "")
        self._device_name = device_name if isinstance(device_name, str) else ""
        
        # Create clean disk name for entity ID (remove /dev/ prefix)
        self._disk_name = self._device_name.replace("/dev/", "") or "Disk"

    @property
    def _smart_device_data(self):
        """Get current S.M.A.R.T. data for this device from coordinator"""
        coordinator_data = self.coordinator.data
        if not isinstance(coordinator_data, dict):
            return {}
        smart_devices = coordinator_data.get("smart_devices", {})
        if not isinstance(smart_devices, dict):
            return {}
        system_devices = smart_devices.get(self._system_id, [])
        if not isinstance(system_devices, (list, tuple)):
            return {}
        for device in system_devices:
            if isinstance(device, dict) and device.get("id") == self._device_id:
                return device
        return {}

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_{self._device_id}_smart"

    @property
    def available(self):
        """Hide stale S.M.A.R.T. data while the monitored agent is offline."""
        return (
            super().available
            and getattr(self.system, "status", None) == "up"
            and bool(self._smart_device_data)
        )

    @property
    def name(self):
        device_data = self._smart_device_data
        model = device_data.get('model', self._disk_name)
        if not isinstance(model, str):
            model = self._disk_name
        # Use short model name if available
        if model:
            # Take first part of model name
            short_model = model.split()[0] if ' ' in model else model
            return f"{self.system.name} {short_model} S.M.A.R.T." if self.system else None
        return f"{self.system.name} {self._disk_name} S.M.A.R.T." if self.system else None

    @property
    def is_on(self):
        """Return True if there's a problem (device_class PROBLEM shows 'on' when problem)"""
        device_data = self._smart_device_data
        if not device_data:
            return None
        
        state = str(device_data.get("state", "")).upper()
        if state == "PASSED":
            return False
        if state in {"WARNING", "FAILED"}:
            return True
        return None

    @property
    def device_class(self):
        return BinarySensorDeviceClass.PROBLEM

    @property
    def icon(self):
        """Return icon based on status and disk type"""
        device_data = self._smart_device_data
        disk_type = device_data.get('type', '')
        
        if self.is_on:
            return "mdi:harddisk-remove"
        
        # Different icons for SSD vs HDD
        if 'nvme' in self._disk_name.lower() or disk_type == 'nvme':
            return "mdi:expansion-card"
        return "mdi:harddisk"

    @property
    def extra_state_attributes(self):
        """Return all S.M.A.R.T. data as attributes"""
        device_data = self._smart_device_data
        if not device_data:
            return {}

        attributes = {}
        
        # Temperature
        temp = device_data.get('temp')
        if temp is not None:
            attributes['temperature'] = temp
            attributes['temperature_unit'] = '°C'
        
        # Keep the established (historically binary) GB/TB attributes for
        # compatibility and add correctly named IEC equivalents.
        capacity = device_data.get('capacity', 0)
        if capacity:
            attributes['capacity_gb'] = round(capacity / (1024**3), 2)
            attributes['capacity_tb'] = round(capacity / (1024**4), 2)
            attributes['capacity_gib'] = round(capacity / (1024**3), 2)
            attributes['capacity_tib'] = round(capacity / (1024**4), 2)
        
        # Power on hours
        hours = device_data.get('hours')
        if hours is not None:
            attributes['power_on_hours'] = hours
            attributes['power_on_days'] = round(hours / 24, 1)
        
        # Power cycles
        cycles = device_data.get('cycles')
        if cycles is not None:
            attributes['power_cycles'] = cycles
        
        # Device info
        model = device_data.get('model')
        if model:
            attributes['model'] = model
        
        serial = device_data.get('serial')
        if serial:
            attributes['serial'] = serial
        
        firmware = device_data.get('firmware')
        if firmware:
            attributes['firmware'] = firmware
        
        disk_type = device_data.get('type')
        if disk_type:
            attributes['type'] = disk_type
        
        # Device path
        attributes['device'] = self._device_name
        
        # Health state
        state = device_data.get('state', '')
        attributes['health_state'] = state

        # Curated raw S.M.A.R.T. attributes (reallocated sectors, wear level,
        # CRC errors, etc.) plus a list of attribute names Beszel has flagged
        # as failing, so this stays useful without needing new entities.
        failed_attributes = []
        raw_attributes = device_data.get("attributes")
        if not isinstance(raw_attributes, (list, tuple)):
            raw_attributes = []
        for raw_attr in raw_attributes:
            if not isinstance(raw_attr, dict):
                continue
            name = raw_attr.get('n')
            if not name:
                continue
            if raw_attr.get('wf'):
                failed_attributes.append(name)
            ha_key = SMART_CURATED_ATTRIBUTES.get(name)
            if ha_key is None:
                continue
            # Prefer the human-readable raw string (e.g. "7344 (253d 8h)"),
            # fall back to the raw numeric value.
            value = raw_attr.get('rs') or raw_attr.get('rv')
            if value not in (None, ''):
                attributes[ha_key] = value
        if failed_attributes:
            attributes['smart_failed_attributes'] = failed_attributes

        return attributes
