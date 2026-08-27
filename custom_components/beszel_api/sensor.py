from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers.icon import icon_for_battery_level
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOGGER


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    entities = []

    try:
        # Get systems and stats from coordinator data
        systems = coordinator.data['systems']
        stats_data = coordinator.data.get('stats', {})

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

                system_info = getattr(system, "info", None) or {}
                if system_info.get("dt") is not None:
                    entities.append(BeszelTemperatureSensor(coordinator, system))

                if system_stats and 's' in system_stats:
                    entities.append(BeszelSWAPSensor(coordinator, system))

                if system_stats and isinstance(system_stats.get('g'), dict):
                    for gpu_key in system_stats['g']:
                        entities.append(BeszelGPUSensor(coordinator, system, gpu_key))

                # Create EFS sensors if EFS data is available
                if system_stats and 'efs' in system_stats and isinstance(system_stats['efs'], dict):
                    for disk_name in system_stats['efs']:
                        entities.append(BeszelEFSDiskSensor(coordinator, system, disk_name))
                        entities.append(BeszelDiskTotalSensor(coordinator, system, disk_name))
                        LOGGER.debug(
                            "Created EFS sensors for %s - %s",
                            system.name,
                            disk_name,
                        )

                # Create battery sensor if data is available
                if system_stats and 'bat' in system_stats and isinstance(system_stats['bat'], list):
                    entities.append(BeszelBatterySensor(coordinator, system))

            except Exception as e:  # noqa: BLE001
                LOGGER.error(f"Failed to create sensors for system {system.name if hasattr(system, 'name') else 'unknown'}: {e}")
                continue

        LOGGER.debug("Created %d sensors total", len(entities))
        async_add_entities(entities)
    except Exception as e:
        LOGGER.error(f"Failed to setup sensors: {e}")
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

        systems = self.coordinator.data.get('systems', [])
        for s in systems:
            if s.id == self._system_id:
                self._system_cache = s
                return s
        return None

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._system_cache = None
        super()._handle_coordinator_update()

    @property
    def stats_data(self):
        return self.coordinator.data.get('stats', {}).get(self._system_id, {})

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
        info = getattr(sys, "info", None) or {}
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
        """Return per-core CPU usage reported by Beszel."""
        if not self.available:
            return {}

        cores = self.stats_data.get("cpus")
        if not isinstance(cores, (list, tuple)) or not cores:
            return {}

        attributes = {"cpu_core_count": len(cores)}
        for index, usage in enumerate(cores):
            if isinstance(usage, (int, float)) and not isinstance(usage, bool):
                attributes[f"cpu_core_{index}"] = usage
        return attributes


class BeszelGPUSensor(BeszelBaseSensor):
    def __init__(self, coordinator, system, gpu_key):
        super().__init__(coordinator, system)
        self._gpu_key = gpu_key

    @property
    def gpu_data(self):
        gpu_stats = self.stats_data.get("g", {})
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
        """Total and Used RAM in GB"""

        attributes = {}
        ram_used = self.stats_data.get("mu")
        ram_total = self.stats_data.get("m")
        attributes['ram_used_gib'] = ram_used
        attributes['ram_total_gib'] = ram_total
        # Backward-compatible aliases retained for the 1.2.x release line.
        attributes['ram_used_gb'] = ram_used
        attributes['ram_total_gb'] = ram_total

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

        attributes = {}
        disk_used = self.stats_data.get("du")
        disk_total = self.stats_data.get("d")
        attributes['disk_used_gib'] = disk_used
        attributes['disk_total_gib'] = disk_total
        attributes['disk_used_gb'] = disk_used
        attributes['disk_total_gb'] = disk_total

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
        return bandwidth is not None

    @property
    def native_value(self):
        bandwidth = self.system_info.get("bb")
        return bandwidth / (1024**2) if bandwidth is not None else None

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
        return received / 1024 if isinstance(received, (int, float)) else None

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
        return sent / 1024 if isinstance(sent, (int, float)) else None

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
        return f"{self.system.name} temperature" if self.system else None
    
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
        if temperatures:
            for key, value in temperatures.items():
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
        return uptime_seconds / 60 if uptime_seconds is not None else None

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
        disk_data = efs_data.get(self._disk_name, {})

        total_space = disk_data.get('d')
        used_space = disk_data.get('du')

        # Calculate disk usage percentage
        if total_space is not None and used_space is not None and total_space > 0:
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
        if not self.stats_data:
            return {}

        efs_data = self.stats_data.get('efs', {})
        disk_data = efs_data.get(self._disk_name, {})

        attributes = {
            "total_disk_space_gib": disk_data.get('d'),
            "disk_used_gib": disk_data.get('du'),
            "read_mb_s": disk_data.get('r'),
            "write_mb_s": disk_data.get('w'),
        }
        attributes["total_disk_space_gb"] = attributes["total_disk_space_gib"]
        attributes["disk_used_gb"] = attributes["disk_used_gib"]
        return attributes


class BeszelBatterySensor(BeszelBaseSensor):
    @property
    def battery_data(self):
        """Return a validated (level, state) battery tuple."""
        battery = self.stats_data.get("bat") if self.stats_data else None
        if not isinstance(battery, (list, tuple)) or len(battery) < 2:
            return None
        level, state = battery[0], battery[1]
        if not isinstance(level, (int, float)):
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
            disk_data = self.stats_data.get("efs", {}).get(self._disk_name, {})
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
