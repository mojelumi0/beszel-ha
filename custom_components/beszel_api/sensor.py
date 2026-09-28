from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers.icon import icon_for_battery_level
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOGGER


def _is_number(value):
    """Return whether a value is a non-boolean number."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_numeric_sequence(value, minimum_length=1):
    """Return whether a value contains enough numeric sequence items."""
    return (
        isinstance(value, (list, tuple))
        and len(value) >= minimum_length
        and all(_is_number(item) for item in value[:minimum_length])
    )


def _network_interface_details(stats_data, rate_index, total_index):
    """Return normalized per-interface rate and total values."""
    interfaces = stats_data.get("ni")
    if not isinstance(interfaces, dict):
        return {}

    details = {}
    valid_names = sorted(name for name in interfaces if isinstance(name, str))
    for name in valid_names:
        values = interfaces[name]
        if not _is_numeric_sequence(values, 4):
            continue
        details[name] = {
            "rate_kib_s": values[rate_index] / 1024,
            "total_gib": values[total_index] / (1024**3),
        }
    return details


def _build_system_sensors(coordinator, system, stats_data):
    """Build all currently supported sensors for one system."""
    entities = [
        BeszelCPUSensor(coordinator, system),
        BeszelRAMSensor(coordinator, system),
        BeszelRAMTotalSensor(coordinator, system),
        BeszelDiskSensor(coordinator, system),
        BeszelDiskTotalSensor(coordinator, system),
        BeszelBandwidthSensor(coordinator, system),
        BeszelNetworkReceiveSensor(coordinator, system),
        BeszelNetworkSendSensor(coordinator, system),
        BeszelUptimeSensor(coordinator, system),
    ]

    system_stats = stats_data.get(system.id, {})
    if not isinstance(system_stats, dict):
        system_stats = {}

    raw_system_info = getattr(system, "info", None)
    system_info = raw_system_info if isinstance(raw_system_info, dict) else {}
    if _is_number(system_info.get("dt")):
        entities.append(BeszelTemperatureSensor(coordinator, system))

    if _is_number(system_stats.get("s")):
        entities.append(BeszelSWAPSensor(coordinator, system))

    load_average = system_stats.get("la")
    if not _is_numeric_sequence(load_average, 3):
        load_average = system_info.get("la")
    if _is_numeric_sequence(load_average, 3):
        for index, label in enumerate(("1m", "5m", "15m")):
            entities.append(
                BeszelLoadAverageSensor(coordinator, system, index, label)
            )

    services = system_info.get("sv")
    if _is_numeric_sequence(services, 2):
        entities.extend(
            (
                BeszelServiceCountSensor(
                    coordinator,
                    system,
                    1,
                    "failed",
                    enabled_default=True,
                ),
                BeszelServiceCountSensor(
                    coordinator,
                    system,
                    0,
                    "total",
                    enabled_default=False,
                ),
            )
        )

    gpu_data = system_stats.get("g")
    if isinstance(gpu_data, dict):
        for gpu_key, gpu_values in gpu_data.items():
            if isinstance(gpu_key, str) and isinstance(gpu_values, dict):
                entities.append(BeszelGPUSensor(coordinator, system, gpu_key))

    efs_data = system_stats.get("efs")
    if isinstance(efs_data, dict):
        for disk_name, disk_values in efs_data.items():
            if not isinstance(disk_name, str) or not isinstance(disk_values, dict):
                continue
            entities.append(BeszelEFSDiskSensor(coordinator, system, disk_name))
            entities.append(BeszelDiskTotalSensor(coordinator, system, disk_name))

    fans = system_stats.get("f")
    if isinstance(fans, dict):
        valid_fan_names = sorted(
            name
            for name, speed in fans.items()
            if isinstance(name, str) and _is_number(speed)
        )
        for fan_name in valid_fan_names:
            entities.append(BeszelFanSensor(coordinator, system, fan_name))

    named_batteries = system_stats.get("bats")
    valid_batteries = {}
    if isinstance(named_batteries, dict):
        valid_batteries = {
            name: level
            for name, level in named_batteries.items()
            if isinstance(name, str) and _is_number(level)
        }

    legacy_battery = system_stats.get("bat")
    has_legacy_battery = _is_numeric_sequence(legacy_battery, 2)
    if has_legacy_battery:
        # Keep the established primary-battery unique ID so an upgrade to
        # Beszel's multi-battery payload does not orphan the existing entity.
        entities.append(BeszelBatterySensor(coordinator, system))

    primary_battery_name = None
    if has_legacy_battery:
        primary_matches = [
            name
            for name, level in valid_batteries.items()
            if level == legacy_battery[0]
        ]
        if len(primary_matches) == 1:
            primary_battery_name = primary_matches[0]

    for battery_name in sorted(valid_batteries):
        if battery_name != primary_battery_name:
            entities.append(
                BeszelNamedBatterySensor(coordinator, system, battery_name)
            )

    zfs_pools = system_stats.get("z")
    if isinstance(zfs_pools, dict):
        for pool_name, pool_data in zfs_pools.items():
            if isinstance(pool_name, str) and isinstance(pool_data, dict):
                entities.append(
                    BeszelZFSPoolSensor(coordinator, system, pool_name)
                )

    return entities


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up sensors and discover new optional metrics on later updates."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    known_unique_ids = set()

    def discover_entities():
        coordinator_data = coordinator.data
        if not isinstance(coordinator_data, dict):
            return []

        systems = coordinator_data.get("systems", [])
        stats_data = coordinator_data.get("stats", {})
        if not isinstance(systems, (list, tuple)):
            return []
        if not isinstance(stats_data, dict):
            stats_data = {}

        new_entities = []
        for system in systems:
            if not getattr(system, "id", None):
                continue
            try:
                candidates = _build_system_sensors(coordinator, system, stats_data)
            except Exception as err:  # noqa: BLE001
                LOGGER.warning(
                    "Failed to discover sensors for system %s: %s",
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
    LOGGER.debug("Created %d sensors total", len(initial_entities))
    async_add_entities(initial_entities)

    def discover_new_entities():
        new_entities = discover_entities()
        if new_entities:
            LOGGER.debug("Discovered %d new Beszel sensors", len(new_entities))
            async_add_entities(new_entities)

    remove_listener = coordinator.async_add_listener(discover_new_entities)
    async_on_unload = getattr(entry, "async_on_unload", None)
    if callable(async_on_unload):
        async_on_unload(remove_listener)

class BeszelBaseSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, system):
        super().__init__(coordinator)
        self._system_id = system.id
        self._system_cache = None

    @property
    def system(self):
        if self._system_cache is not None:
            return self._system_cache

        coordinator_data = self.coordinator.data
        if not isinstance(coordinator_data, dict):
            return None
        systems = coordinator_data.get("systems", [])
        if not isinstance(systems, (list, tuple)):
            return None
        for s in systems:
            if getattr(s, "id", None) == self._system_id:
                self._system_cache = s
                return s
        return None

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._system_cache = None
        super()._handle_coordinator_update()

    @property
    def stats_data(self):
        coordinator_data = self.coordinator.data
        if not isinstance(coordinator_data, dict):
            return {}
        stats_data = coordinator_data.get("stats", {})
        if not isinstance(stats_data, dict):
            return {}
        system_stats = stats_data.get(self._system_id, {})
        return system_stats if isinstance(system_stats, dict) else {}

    @property
    def system_info(self):
        """Return the current system info payload as a dictionary."""
        info = getattr(self.system, "info", None) if self.system else None
        return info if isinstance(info, dict) else {}

    @property
    def available(self):
        """Only expose measurements while both Hub and agent are online."""
        return (
            self.coordinator.last_update_success
            and self.system is not None
            and getattr(self.system, "status", None) == "up"
        )

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

class BeszelCPUSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_cpu"

    @property
    def name(self):
        return f"{self.system.name} CPU" if self.system else None

    @property
    def icon(self):
        return "mdi:memory"

    @property
    def native_value(self):
        return self.system_info.get("cpu")

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def extra_state_attributes(self):
        """Return detailed CPU usage reported by Beszel."""
        if not self.available:
            return {}

        attributes = {}

        cores = self.stats_data.get("cpus")
        if isinstance(cores, (list, tuple)) and cores:
            attributes["cpu_core_count"] = len(cores)
            for index, usage in enumerate(cores):
                if _is_number(usage):
                    attributes[f"cpu_core_{index}"] = usage

        breakdown = self.stats_data.get("cpub")
        if _is_numeric_sequence(breakdown, 5):
            labels = ("user", "system", "iowait", "steal", "idle")
            for label, usage in zip(labels, breakdown, strict=False):
                attributes[f"cpu_{label}_percent"] = usage

        return attributes


class BeszelLoadAverageSensor(BeszelBaseSensor):
    """Load average for one of Beszel's 1, 5, or 15 minute windows."""

    def __init__(self, coordinator, system, index, label):
        super().__init__(coordinator, system)
        self._index = index
        self._label = label

    @property
    def load_average(self):
        values = self.stats_data.get("la")
        if not _is_numeric_sequence(values, self._index + 1):
            values = self.system_info.get("la")
        if not _is_numeric_sequence(values, self._index + 1):
            return None
        return values[self._index]

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_load_average_{self._label}"

    @property
    def name(self):
        if not self.system:
            return None
        return f"{self.system.name} Load Average {self._label}"

    @property
    def icon(self):
        return "mdi:chart-line"

    @property
    def available(self):
        return super().available and self.load_average is not None

    @property
    def native_value(self):
        return self.load_average

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def suggested_display_precision(self):
        return 2

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC

    @property
    def entity_registry_enabled_default(self):
        return False


class BeszelServiceCountSensor(BeszelBaseSensor):
    """Count total or failed systemd services reported by Beszel."""

    def __init__(self, coordinator, system, index, kind, enabled_default):
        super().__init__(coordinator, system)
        self._index = index
        self._kind = kind
        self._enabled_default = enabled_default

    @property
    def service_count(self):
        services = self.system_info.get("sv")
        if not _is_numeric_sequence(services, self._index + 1):
            return None
        return services[self._index]

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_services_{self._kind}"

    @property
    def name(self):
        if not self.system:
            return None
        return f"{self.system.name} Services {self._kind.title()}"

    @property
    def icon(self):
        if self._kind == "failed":
            return "mdi:alert-circle-outline"
        return "mdi:cog"

    @property
    def available(self):
        return super().available and self.service_count is not None

    @property
    def native_value(self):
        return self.service_count

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC

    @property
    def entity_registry_enabled_default(self):
        return self._enabled_default


class BeszelFanSensor(BeszelBaseSensor):
    """Fan speed reported by a recent Beszel agent."""

    def __init__(self, coordinator, system, fan_name):
        super().__init__(coordinator, system)
        self._fan_name = fan_name

    @property
    def fan_speed(self):
        fans = self.stats_data.get("f")
        if not isinstance(fans, dict):
            return None
        speed = fans.get(self._fan_name)
        return speed if _is_number(speed) else None

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_fan_{self._fan_name}"

    @property
    def name(self):
        if not self.system:
            return None
        return f"{self.system.name} Fan {self._fan_name}"

    @property
    def icon(self):
        return "mdi:fan"

    @property
    def available(self):
        return super().available and self.fan_speed is not None

    @property
    def native_value(self):
        return self.fan_speed

    @property
    def native_unit_of_measurement(self):
        return REVOLUTIONS_PER_MINUTE

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC


class BeszelZFSPoolSensor(BeszelBaseSensor):
    """Usage and health details for one ZFS pool reported by Beszel."""

    def __init__(self, coordinator, system, pool_name):
        super().__init__(coordinator, system)
        self._pool_name = pool_name

    @property
    def pool_data(self):
        pools = self.stats_data.get("z")
        if not isinstance(pools, dict):
            return {}
        data = pools.get(self._pool_name)
        return data if isinstance(data, dict) else {}

    @property
    def usage_percent(self):
        total = self.pool_data.get("d")
        used = self.pool_data.get("du")
        if not _is_number(total) or not _is_number(used) or total <= 0:
            return None
        return used / total * 100

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_zfs_pool_{self._pool_name}"

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
        return f"{self.system.name} ZFS {pool_label}"

    @property
    def icon(self):
        health = self.pool_data.get("h")
        if isinstance(health, str) and health.upper() not in {"", "ONLINE"}:
            return "mdi:database-alert"
        return "mdi:database"

    @property
    def available(self):
        return super().available and self.usage_percent is not None

    @property
    def native_value(self):
        return self.usage_percent

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def suggested_display_precision(self):
        return 2

    @property
    def entity_category(self):
        return EntityCategory.DIAGNOSTIC

    @property
    def extra_state_attributes(self):
        if not self.available:
            return {}

        data = self.pool_data
        attributes = {
            "pool_name": self._pool_name,
            "pool_total_gib": data.get("d"),
            "pool_used_gib": data.get("du"),
        }
        health = data.get("h")
        if isinstance(health, str) and health:
            attributes["pool_health"] = health

        read_bytes = data.get("rb")
        if _is_number(read_bytes):
            attributes["pool_read_mib_s"] = read_bytes / (1024**2)

        write_bytes = data.get("wb")
        if _is_number(write_bytes):
            attributes["pool_write_mib_s"] = write_bytes / (1024**2)

        return attributes


class BeszelGPUSensor(BeszelBaseSensor):
    def __init__(self, coordinator, system, gpu_key):
        super().__init__(coordinator, system)
        self._gpu_key = gpu_key

    @property
    def gpu_data(self):
        gpu_stats = self.stats_data.get("g", {})
        if not isinstance(gpu_stats, dict):
            return {}
        data = gpu_stats.get(self._gpu_key, {})
        return data if isinstance(data, dict) else {}

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_gpu_{self._gpu_key}"

    @property
    def name(self):
        gpu_name = self.gpu_data.get("n")
        return gpu_name if gpu_name else f"GPU {self._gpu_key}"

    @property
    def icon(self):
        return "mdi:expansion-card"
    
    @property
    def available(self):
        if not super().available:
            return False
        gpu_usage = self.gpu_data.get("u") if self.gpu_data else None
        return _is_number(gpu_usage)

    @property
    def native_value(self):
        return self.gpu_data.get("u")

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def extra_state_attributes(self):
        attributes = {
            "gpu_vram_mb": self.gpu_data.get("mt"),
        }

        gpu_memory_used = self.gpu_data.get("mu")
        if gpu_memory_used is not None:
            attributes["gpu_memory_used_mb"] = gpu_memory_used

        gpu_power = self.gpu_data.get("p")
        if gpu_power is not None:
            attributes["gpu_power_w"] = gpu_power

        package_power = self.gpu_data.get("pp")
        if _is_number(package_power):
            attributes["gpu_package_power_w"] = package_power

        engines = self.gpu_data.get("e")
        if isinstance(engines, dict):
            valid_engines = {
                name: usage
                for name, usage in engines.items()
                if isinstance(name, str) and _is_number(usage)
            }
            if valid_engines:
                attributes["gpu_engines_percent"] = valid_engines

        return attributes


class BeszelRAMSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_ram"

    @property
    def name(self):
        return f"{self.system.name} RAM" if self.system else None

    @property
    def icon(self):
        return "mdi:chip"

    @property
    def native_value(self):
        return self.system_info.get("mp")

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def extra_state_attributes(self):
        """Total and Used RAM in GB"""

        attributes = {}
        ram_used = self.stats_data.get("mu")
        ram_total = self.stats_data.get("m")
        attributes['ram_used_gib'] = ram_used
        attributes['ram_total_gib'] = ram_total
        # Backward-compatible aliases retained for the 1.2.x release line.
        attributes['ram_used_gb'] = ram_used
        attributes['ram_total_gb'] = ram_total

        ram_buffer_cache = self.stats_data.get("mb")
        if _is_number(ram_buffer_cache):
            attributes["ram_buffer_cache_gib"] = ram_buffer_cache

        ram_zfs_arc = self.stats_data.get("mz")
        if _is_number(ram_zfs_arc):
            attributes["ram_zfs_arc_gib"] = ram_zfs_arc

        return attributes

class BeszelSWAPSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_swap"

    @property
    def name(self):
        return f"{self.system.name} SWAP" if self.system else None

    @property
    def icon(self):
        return "mdi:chip"
    
    @property
    def available(self):
        if not super().available:
            return False
        swap_total = self.stats_data.get("s")
        return _is_number(swap_total) and swap_total > 0

    @property
    def native_value(self):
        swap_used = self.stats_data.get("su", 0)
        swap_total = self.stats_data.get("s")
        if self.available and _is_number(swap_used):
            return (swap_used / swap_total * 100)
        return None

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE
    
    @property
    def suggested_display_precision(self):
        return 2

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def extra_state_attributes(self):
        """Total and Used SWAP in GB"""

        attributes = {}
        swap_used = self.stats_data.get("su", 0)
        swap_total = self.stats_data.get("s")
        attributes['swap_used_gib'] = swap_used
        attributes['swap_total_gib'] = swap_total
        attributes['swap_used_gb'] = swap_used
        attributes['swap_total_gb'] = swap_total

        return attributes


class BeszelDiskSensor(BeszelBaseSensor):

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_disk"

    @property
    def name(self):
        return f"{self.system.name} Disk" if self.system else None

    @property
    def icon(self):
        return "mdi:harddisk"

    @property
    def native_value(self):
        return self.system_info.get("dp")

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def extra_state_attributes(self):
        """Total and Used DISK in GB"""

        if not self.available:
            return {}

        attributes = {}
        disk_used = self.stats_data.get("du")
        disk_total = self.stats_data.get("d")
        attributes['disk_used_gib'] = disk_used
        attributes['disk_total_gib'] = disk_total
        attributes['disk_used_gb'] = disk_used
        attributes['disk_total_gb'] = disk_total

        root_disk_name = self.system_info.get("rdn")
        if isinstance(root_disk_name, str) and root_disk_name:
            attributes["disk_name"] = root_disk_name

        disk_io = self.stats_data.get("dio")
        if _is_numeric_sequence(disk_io, 2):
            attributes["disk_read_mib_s"] = disk_io[0] / (1024**2)
            attributes["disk_write_mib_s"] = disk_io[1] / (1024**2)

        disk_io_total = self.stats_data.get("diot")
        if _is_numeric_sequence(disk_io_total, 2):
            attributes["disk_read_gib_total"] = disk_io_total[0] / (1024**3)
            attributes["disk_write_gib_total"] = disk_io_total[1] / (1024**3)

        disk_io_stats = self.stats_data.get("dios")
        if _is_numeric_sequence(disk_io_stats, 6):
            labels = (
                "disk_read_time_percent",
                "disk_write_time_percent",
                "disk_io_utilization_percent",
                "disk_read_await_ms",
                "disk_write_await_ms",
                "disk_weighted_io_percent",
            )
            attributes.update(zip(labels, disk_io_stats, strict=False))

        return attributes


class BeszelBandwidthSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_bandwidth"

    @property
    def name(self):
        return f"{self.system.name} Bandwidth" if self.system else None

    @property
    def icon(self):
        return "mdi:router-network"
    
    @property
    def available(self):
        if not super().available:
            return False
        bandwidth = self.system_info.get("bb")
        return _is_number(bandwidth)

    @property
    def native_value(self):
        bandwidth = self.system_info.get("bb")
        return bandwidth / (1024**2) if _is_number(bandwidth) else None

    @property
    def device_class(self):
        return SensorDeviceClass.DATA_RATE

    @property
    def native_unit_of_measurement(self):
        return UnitOfDataRate.MEBIBYTES_PER_SECOND

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT
    
    @property
    def suggested_display_precision(self):
        return 3


class BeszelNetworkReceiveSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_network_receive"

    @property
    def name(self):
        return f"{self.system.name} Network Receive" if self.system else None

    @property
    def icon(self):
        return "mdi:download-network"

    @property
    def native_value(self):
        b_data = self.stats_data.get("b")
        if not isinstance(b_data, (list, tuple)) or len(b_data) < 2:
            return None
        received = b_data[1]
        return received / 1024 if _is_number(received) else None

    @property
    def device_class(self):
        return SensorDeviceClass.DATA_RATE

    @property
    def native_unit_of_measurement(self):
        return UnitOfDataRate.KIBIBYTES_PER_SECOND

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def suggested_display_precision(self):
        return 2

    @property
    def extra_state_attributes(self):
        if not self.available:
            return {}
        interfaces = _network_interface_details(self.stats_data, 1, 3)
        return {"interfaces": interfaces} if interfaces else {}
        
class BeszelNetworkSendSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_network_send"

    @property
    def name(self):
        return f"{self.system.name} Network Send" if self.system else None

    @property
    def icon(self):
        return "mdi:upload-network"

    @property
    def native_value(self):
        b_data = self.stats_data.get("b")
        if not isinstance(b_data, (list, tuple)) or len(b_data) < 2:
            return None
        sent = b_data[0]
        return sent / 1024 if _is_number(sent) else None

    @property
    def device_class(self):
        return SensorDeviceClass.DATA_RATE

    @property
    def native_unit_of_measurement(self):
        return UnitOfDataRate.KIBIBYTES_PER_SECOND

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def suggested_display_precision(self):
        return 2

    @property
    def extra_state_attributes(self):
        if not self.available:
            return {}
        interfaces = _network_interface_details(self.stats_data, 0, 2)
        return {"interfaces": interfaces} if interfaces else {}

class BeszelTemperatureSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_temperature"

    @property
    def name(self):
        return f"{self.system.name} temperature" if self.system else None
    
    @property
    def available(self):
        if not super().available:
            return False
        temperature = self.system_info.get("dt")
        return _is_number(temperature)

    @property
    def native_value(self):
        return self.system_info.get("dt")

    @property
    def device_class(self):
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        return UnitOfTemperature.CELSIUS

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def extra_state_attributes(self):
        temperatures = self.stats_data.get("t")

        attributes = {}
        if isinstance(temperatures, dict):
            for key, value in temperatures.items():
                if _is_number(value):
                    attributes[f"temperature_{key}"] = value

        return attributes


class BeszelUptimeSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_uptime"

    @property
    def name(self):
        return f"{self.system.name} uptime" if self.system else None

    @property
    def icon(self):
        return "mdi:sort-clock-descending"

    @property
    def device_class(self):
        return SensorDeviceClass.DURATION

    @property
    def native_value(self):
        if not self.system:
            return None
        uptime_seconds = self.system_info.get("u")
        return uptime_seconds / 60 if _is_number(uptime_seconds) else None

    @property
    def suggested_display_precision(self):
        return 2

    @property
    def state_class(self):
        return SensorStateClass.TOTAL_INCREASING

    @property
    def native_unit_of_measurement(self):
        return UnitOfTime.MINUTES

class BeszelEFSDiskSensor(BeszelBaseSensor):
    def __init__(self, coordinator, system, disk_name):
        super().__init__(coordinator, system)
        self._disk_name = disk_name

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_efs_{self._disk_name}"

    @property
    def name(self):
        return f"{self.system.name} EFS {self._disk_name}" if self.system else None

    @property
    def icon(self):
        return "mdi:harddisk"

    @property
    def native_value(self):
        if not self.stats_data:
            return None

        efs_data = self.stats_data.get('efs', {})
        if not isinstance(efs_data, dict):
            return None
        disk_data = efs_data.get(self._disk_name, {})
        if not isinstance(disk_data, dict):
            return None

        total_space = disk_data.get('d')
        used_space = disk_data.get('du')

        # Calculate disk usage percentage
        if (
            _is_number(total_space)
            and _is_number(used_space)
            and total_space > 0
        ):
            return (used_space / total_space) * 100
        return None

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE
    
    @property
    def suggested_display_precision(self):
        return 2

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def extra_state_attributes(self):
        """Return additional state attributes for the EFS disk."""
        if not self.available or not self.stats_data:
            return {}

        efs_data = self.stats_data.get('efs', {})
        if not isinstance(efs_data, dict):
            return {}
        disk_data = efs_data.get(self._disk_name, {})
        if not isinstance(disk_data, dict):
            return {}

        attributes = {
            "total_disk_space_gib": disk_data.get('d'),
            "disk_used_gib": disk_data.get('du'),
            "read_mb_s": disk_data.get('r'),
            "write_mb_s": disk_data.get('w'),
        }
        attributes["total_disk_space_gb"] = attributes["total_disk_space_gib"]
        attributes["disk_used_gb"] = attributes["disk_used_gib"]

        read_bytes = disk_data.get("rb")
        if _is_number(read_bytes):
            attributes["read_mib_s"] = read_bytes / (1024**2)

        write_bytes = disk_data.get("wb")
        if _is_number(write_bytes):
            attributes["write_mib_s"] = write_bytes / (1024**2)

        total_read = disk_data.get("tr")
        if _is_number(total_read):
            attributes["read_gib_total"] = total_read / (1024**3)

        total_write = disk_data.get("tw")
        if _is_number(total_write):
            attributes["write_gib_total"] = total_write / (1024**3)

        disk_io_stats = disk_data.get("dios")
        if _is_numeric_sequence(disk_io_stats, 6):
            labels = (
                "read_time_percent",
                "write_time_percent",
                "io_utilization_percent",
                "read_await_ms",
                "write_await_ms",
                "weighted_io_percent",
            )
            attributes.update(zip(labels, disk_io_stats, strict=False))
        return attributes


class BeszelNamedBatterySensor(BeszelBaseSensor):
    """One battery from Beszel's multi-battery payload."""

    def __init__(self, coordinator, system, battery_name):
        super().__init__(coordinator, system)
        self._battery_name = battery_name

    @property
    def battery_level(self):
        batteries = self.stats_data.get("bats")
        if not isinstance(batteries, dict):
            return None
        level = batteries.get(self._battery_name)
        return level if _is_number(level) else None

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_battery_{self._battery_name}"

    @property
    def name(self):
        if not self.system:
            return None
        return f"{self.system.name} Battery {self._battery_name}"

    @property
    def icon(self):
        level = self.battery_level
        if level is None:
            return "mdi:battery-unknown"
        return icon_for_battery_level(level, False)

    @property
    def available(self):
        return super().available and self.battery_level is not None

    @property
    def device_class(self):
        return SensorDeviceClass.BATTERY

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.battery_level

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE


class BeszelBatterySensor(BeszelBaseSensor):
    @property
    def battery_data(self):
        """Return a validated (level, state) battery tuple."""
        battery = self.stats_data.get("bat") if self.stats_data else None
        if not isinstance(battery, (list, tuple)) or len(battery) < 2:
            return None
        level, state = battery[0], battery[1]
        if not _is_number(level):
            return None
        return level, state

    @property
    def available(self):
        return super().available and self.battery_data is not None

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_battery"

    @property
    def name(self):
        return f"{self.system.name} Battery" if self.system else None

    @property
    def icon(self):
        battery = self.battery_data
        if battery is None:
            return "mdi:battery-unknown"
        level, state = battery
        # https://github.com/henrygd/beszel/blob/4d05bfdff0ec90b68e820ad5dc32a5c4bccf8f0f/internal/site/src/lib/enums.ts#L41-L48
        charging = state == 3

        return icon_for_battery_level(level, charging)

    @property
    def device_class(self):
        return SensorDeviceClass.BATTERY

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        battery = self.battery_data
        return battery[0] if battery is not None else None

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE


class BeszelRAMTotalSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_ram_total"

    @property
    def name(self):
        return f"{self.system.name} RAM Total" if self.system else None

    @property
    def icon(self):
        return "mdi:chip"

    @property
    def native_value(self):
        if not self.stats_data:
            return None
        return self.stats_data.get("m")

    @property
    def device_class(self):
        return SensorDeviceClass.DATA_SIZE

    @property
    def native_unit_of_measurement(self):
        return UnitOfInformation.GIBIBYTES

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT


class BeszelDiskTotalSensor(BeszelBaseSensor):
    def __init__(self, coordinator, system, disk_name=None):
        super().__init__(coordinator, system)
        self._disk_name = disk_name

    @property
    def unique_id(self):
        suffix = f"_{self._disk_name}" if self._disk_name else ""
        return f"beszel_{self._system_id}_disk_total{suffix}"

    @property
    def name(self):
        label = f" {self._disk_name}" if self._disk_name else ""
        return f"{self.system.name} Disk Total{label}" if self.system else None

    @property
    def icon(self):
        return "mdi:harddisk"

    @property
    def native_value(self):
        if not self.stats_data:
            return None

        if self._disk_name:
            efs_data = self.stats_data.get("efs", {})
            if not isinstance(efs_data, dict):
                return None
            disk_data = efs_data.get(self._disk_name, {})
            if isinstance(disk_data, dict):
                return disk_data.get("d")
            return None

        return self.stats_data.get("d")

    @property
    def device_class(self):
        return SensorDeviceClass.DATA_SIZE

    @property
    def native_unit_of_measurement(self):
        return UnitOfInformation.GIBIBYTES

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT
