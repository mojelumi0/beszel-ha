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


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    entities = []

    try:
        # Get systems and stats from coordinator data
        systems = coordinator.data["systems"]
        stats_data = coordinator.data.get("stats", {})

        for system in systems:
            try:
                entities.append(BeszelCPUSensor(coordinator, system))
                entities.append(BeszelRAMSensor(coordinator, system))
                entities.append(BeszelRAMTotalSensor(coordinator, system))
                entities.append(BeszelDiskSensor(coordinator, system))
                entities.append(BeszelDiskTotalSensor(coordinator, system))
                entities.append(BeszelBandwidthSensor(coordinator, system))
                entities.append(BeszelNetworkReceiveSensor(coordinator, system))
                entities.append(BeszelNetworkSendSensor(coordinator, system))
                entities.append(BeszelUptimeSensor(coordinator, system))

                # Get stats for this system
                system_stats = stats_data.get(system.id, {})
                if not isinstance(system_stats, dict):
                    system_stats = {}

                raw_system_info = getattr(system, "info", None)
                system_info = (
                    raw_system_info if isinstance(raw_system_info, dict) else {}
                )
                if system_info.get("dt") is not None:
                    entities.append(BeszelTemperatureSensor(coordinator, system))

                if system_stats and "s" in system_stats:
                    entities.append(BeszelSWAPSensor(coordinator, system))

                load_average = system_stats.get("la")
                if not _is_numeric_sequence(load_average, 3):
                    load_average = system_info.get("la")
                if _is_numeric_sequence(load_average, 3):
                    for index, label in enumerate(("1m", "5m", "15m")):
                        entities.append(
                            BeszelLoadAverageSensor(
                                coordinator,
                                system,
                                index,
                                label,
                            )
                        )

                services = system_info.get("sv")
                if _is_numeric_sequence(services, 2):
                    entities.append(
                        BeszelServiceCountSensor(
                            coordinator,
                            system,
                            1,
                            "failed",
                            enabled_default=True,
                        )
                    )
                    entities.append(
                        BeszelServiceCountSensor(
                            coordinator,
                            system,
                            0,
                            "total",
                            enabled_default=False,
                        )
                    )

                if system_stats and isinstance(system_stats.get("g"), dict):
                    for gpu_key in system_stats["g"]:
                        entities.append(
                            BeszelGPUSensor(coordinator, system, gpu_key)
                        )

                # Create EFS sensors if EFS data is available
                if (
                    system_stats
                    and "efs" in system_stats
                    and isinstance(system_stats["efs"], dict)
                ):
                    for disk_name in system_stats["efs"]:
                        entities.append(
                            BeszelEFSDiskSensor(
                                coordinator,
                                system,
                                disk_name,
                            )
                        )
                        entities.append(
                            BeszelDiskTotalSensor(
                                coordinator,
                                system,
                                disk_name,
                            )
                        )
                        LOGGER.debug(
                            "Created EFS sensors for %s - %s",
                            system.name,
                            disk_name,
                        )

                fans = system_stats.get("f")
                if isinstance(fans, dict):
                    valid_fan_names = sorted(
                        name
                        for name, speed in fans.items()
                        if isinstance(name, str) and _is_number(speed)
                    )
                    for fan_name in valid_fan_names:
                        entities.append(
                            BeszelFanSensor(
                                coordinator,
                                system,
                                fan_name,
                            )
                        )

                named_batteries = system_stats.get("bats")
                valid_batteries = {}
                if isinstance(named_batteries, dict):
                    valid_batteries = {
                        name: level
                        for name, level in named_batteries.items()
                        if isinstance(name, str) and _is_number(level)
                    }

                legacy_battery = system_stats.get("bat")
                has_legacy_battery = _is_numeric_sequence(
                    legacy_battery,
                    2,
                )
                if has_legacy_battery:
                    # Keep the established primary-battery unique ID so an
                    # upgrade to Beszel's multi-battery payload does not leave
                    # the user's existing entity orphaned.
                    entities.append(
                        BeszelBatterySensor(
                            coordinator,
                            system,
                        )
                    )

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
                            BeszelNamedBatterySensor(
                                coordinator,
                                system,
                                battery_name,
                            )
                        )

            except Exception as err:  # noqa: BLE001
                system_name = getattr(system, "name", "unknown")
                LOGGER.error(
                    "Failed to create sensors for system %s: %s",
                    system_name,
                    err,
                )
                continue

        LOGGER.debug("Created %d sensors total", len(entities))
        async_add_entities(entities)
    except Exception as err:
        LOGGER.error("Failed to setup sensors: %s", err)
        raise


class BeszelBaseSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, system):
        super().__init__(coordinator)
        self._system_id = system.id
        self._system_cache = None

    @property
    def system(self):
        if self._system_cache is not None:
            return self._system_cache

        systems = self.coordinator.data.get("systems", [])
        for system in systems:
            if system.id == self._system_id:
                self._system_cache = system
                return system
        return None

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._system_cache = None
        super()._handle_coordinator_update()

    @property
    def stats_data(self):
        return self.coordinator.data.get("stats", {}).get(
            self._system_id,
            {},
        )

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
        system = self.system
        if system is None:
            return None

        raw_info = getattr(system, "info", None)
        info = raw_info if isinstance(raw_info, dict) else {}
        return {
            "identifiers": {(DOMAIN, system.id)},
            "name": system.name,
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
        return (
            f"beszel_{self._system_id}_load_average_{self._label}"
        )

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

    def __init__(
        self,
        coordinator,
        system,
        index,
        kind,
        enabled_default,
    ):
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
        return (
            f"beszel_{self._system_id}_services_{self._kind}"
        )

    @property
    def name(self):
        if not self.system:
            return None
        return (
            f"{self.system.name} Services {self._kind.title()}"
        )

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
        return (
            f"beszel_{self._system_id}_fan_{self._fan_name}"
        )

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
        gpu_usage = (
            self.gpu_data.get("u") if self.gpu_data else None
        )
        return gpu_usage is not None

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
        """Return detailed RAM values in GiB."""
        ram_used = self.stats_data.get("mu")
        ram_total = self.stats_data.get("m")
        attributes = {
            "ram_used_gib": ram_used,
            "ram_total_gib": ram_total,
            # Backward-compatible aliases for the 1.2.x release line.
            "ram_used_gb": ram_used,
            "ram_total_gb": ram_total,
        }

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
        return swap_total is not None and swap_total > 0

    @property
    def native_value(self):
        swap_used = self.stats_data.get("su", 0)
        swap_total = self.stats_data.get("s")
        if self.available:
            return swap_used / swap_total * 100
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
        """Return detailed SWAP values in GiB."""
        swap_used = self.stats_data.get("su", 0)
        swap_total = self.stats_data.get("s")
        return {
            "swap_used_gib": swap_used,
            "swap_total_gib": swap_total,
            "swap_used_gb": swap_used,
            "swap_total_gb": swap_total,
        }


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
        """Return detailed disk values in GiB."""
        disk_used = self.stats_data.get("du")
        disk_total = self.stats_data.get("d")
        return {
            "disk_used_gib": disk_used,
            "disk_total_gib": disk_total,
            "disk_used_gb": disk_used,
            "disk_total_gb": disk_total,
        }


class BeszelBandwidthSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_bandwidth"

    @property
    def name(self):
        return (
            f"{self.system.name} Bandwidth"
            if self.system
            else None
        )

    @property
    def icon(self):
        return "mdi:router-network"

    @property
    def available(self):
        if not super().available:
            return False
        bandwidth = self.system_info.get("bb")
        return bandwidth is not None

    @property
    def native_value(self):
        bandwidth = self.system_info.get("bb")
        return (
            bandwidth / (1024**2)
            if bandwidth is not None
            else None
        )

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
        return (
            f"{self.system.name} Network Receive"
            if self.system
            else None
        )

    @property
    def icon(self):
        return "mdi:download-network"

    @property
    def native_value(self):
        bandwidth = self.stats_data.get("b")
        if (
            not isinstance(bandwidth, (list, tuple))
            or len(bandwidth) < 2
        ):
            return None
        received = bandwidth[1]
        return (
            received / 1024
            if isinstance(received, (int, float))
            else None
        )

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


class BeszelNetworkSendSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_network_send"

    @property
    def name(self):
        return (
            f"{self.system.name} Network Send"
            if self.system
            else None
        )

    @property
    def icon(self):
        return "mdi:upload-network"

    @property
    def native_value(self):
        bandwidth = self.stats_data.get("b")
        if (
            not isinstance(bandwidth, (list, tuple))
            or len(bandwidth) < 2
        ):
            return None
        sent = bandwidth[0]
        return (
            sent / 1024
            if isinstance(sent, (int, float))
            else None
        )

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


class BeszelTemperatureSensor(BeszelBaseSensor):
    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_temperature"

    @property
    def name(self):
        return (
            f"{self.system.name} temperature"
            if self.system
            else None
        )

    @property
    def available(self):
        if not super().available:
            return False
        temperature = self.system_info.get("dt")
        return temperature is not None

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
        return (
            f"{self.system.name} uptime"
            if self.system
            else None
        )

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
        return (
            uptime_seconds / 60
            if uptime_seconds is not None
            else None
        )

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
        return (
            f"{self.system.name} EFS {self._disk_name}"
            if self.system
            else None
        )

    @property
    def icon(self):
        return "mdi:harddisk"

    @property
    def native_value(self):
        if not self.stats_data:
            return None

        efs_data = self.stats_data.get("efs", {})
        if not isinstance(efs_data, dict):
            return None

        disk_data = efs_data.get(self._disk_name, {})
        if not isinstance(disk_data, dict):
            return None

        total_space = disk_data.get("d")
        used_space = disk_data.get("du")

        if (
            total_space is not None
            and used_space is not None
            and total_space > 0
        ):
            return used_space / total_space * 100
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
        if not self.stats_data:
            return {}

        efs_data = self.stats_data.get("efs", {})
        if not isinstance(efs_data, dict):
            return {}

        disk_data = efs_data.get(self._disk_name, {})
        if not isinstance(disk_data, dict):
            return {}

        attributes = {
            "total_disk_space_gib": disk_data.get("d"),
            "disk_used_gib": disk_data.get("du"),
            "read_mb_s": disk_data.get("r"),
            "write_mb_s": disk_data.get("w"),
        }
        attributes["total_disk_space_gb"] = (
            attributes["total_disk_space_gib"]
        )
        attributes["disk_used_gb"] = attributes["disk_used_gib"]
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
        return (
            f"beszel_{self._system_id}_battery_"
            f"{self._battery_name}"
        )

    @property
    def name(self):
        if not self.system:
            return None
        return (
            f"{self.system.name} Battery {self._battery_name}"
        )

    @property
    def icon(self):
        level = self.battery_level
        if level is None:
            return "mdi:battery-unknown"
        return icon_for_battery_level(level, False)

    @property
    def available(self):
        return (
            super().available
            and self.battery_level is not None
        )

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
        battery = (
            self.stats_data.get("bat")
            if self.stats_data
            else None
        )
        if (
            not isinstance(battery, (list, tuple))
            or len(battery) < 2
        ):
            return None

        level, state = battery[0], battery[1]
        if not _is_number(level):
            return None
        return level, state

    @property
    def available(self):
        return (
            super().available
            and self.battery_data is not None
        )

    @property
    def unique_id(self):
        return f"beszel_{self._system_id}_battery"

    @property
    def name(self):
        return (
            f"{self.system.name} Battery"
            if self.system
            else None
        )

    @property
    def icon(self):
        battery = self.battery_data
        if battery is None:
            return "mdi:battery-unknown"

        level, state = battery
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
        return (
            f"{self.system.name} RAM Total"
            if self.system
            else None
        )

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
        suffix = (
            f"_{self._disk_name}"
            if self._disk_name
            else ""
        )
        return f"beszel_{self._system_id}_disk_total{suffix}"

    @property
    def name(self):
        label = (
            f" {self._disk_name}"
            if self._disk_name
            else ""
        )
        return (
            f"{self.system.name} Disk Total{label}"
            if self.system
            else None
        )

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
