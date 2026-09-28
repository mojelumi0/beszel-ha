"""Regression tests for Beszel sensor behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.const import (
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
)

from custom_components.beszel_api.binary_sensor import (
    BeszelSmartBinarySensor,
    BeszelStatusBinarySensor,
    BeszelZFSHealthBinarySensor,
)
from custom_components.beszel_api.binary_sensor import (
    async_setup_entry as async_setup_binary_sensor_entry,
)
from custom_components.beszel_api.const import DOMAIN
from custom_components.beszel_api.sensor import (
    BeszelBandwidthSensor,
    BeszelBatterySensor,
    BeszelCPUSensor,
    BeszelDiskSensor,
    BeszelDiskTotalSensor,
    BeszelEFSDiskSensor,
    BeszelFanSensor,
    BeszelGPUSensor,
    BeszelLoadAverageSensor,
    BeszelNamedBatterySensor,
    BeszelNetworkReceiveSensor,
    BeszelNetworkSendSensor,
    BeszelRAMSensor,
    BeszelRAMTotalSensor,
    BeszelServiceCountSensor,
    BeszelTemperatureSensor,
    BeszelZFSPoolSensor,
)
from custom_components.beszel_api.sensor import (
    async_setup_entry as async_setup_sensor_entry,
)


def _system(*, status: str = "up", info: dict | None = None) -> SimpleNamespace:
    system_info = {
        "bb": 2 * 1024**2,
        "cpu": 10,
        "dp": 25,
        "dt": 42,
        "mp": 30,
        "u": 600,
    }
    if info:
        system_info.update(info)
    return SimpleNamespace(
        id="system-1",
        name="Server",
        status=status,
        info=system_info,
    )


def _coordinator(system, stats=None, smart_devices=None):
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {
        "systems": [system],
        "stats": {system.id: stats or {}},
        "smart_devices": {system.id: smart_devices or []},
    }
    return coordinator


def test_agent_offline_keeps_only_connectivity_status_available() -> None:
    """Stale measurements must disappear while Hub still reports agent down."""
    system = _system(status="down")
    smart = {"id": "disk-1", "name": "/dev/sda", "state": "PASSED"}
    coordinator = _coordinator(system, smart_devices=[smart])

    status = BeszelStatusBinarySensor(coordinator, system)
    bandwidth = BeszelBandwidthSensor(coordinator, system)
    smart_status = BeszelSmartBinarySensor(coordinator, system, smart)

    assert status.available is True
    assert status.is_on is False
    assert bandwidth.available is False
    assert smart_status.available is False


def test_agent_offline_hides_stale_disk_diagnostics() -> None:
    """Root and additional disk attributes must disappear while offline."""
    system = _system(status="down")
    coordinator = _coordinator(
        system,
        stats={
            "d": 100,
            "du": 25,
            "dio": [1024**2, 2 * 1024**2],
            "efs": {
                "data": {
                    "d": 50,
                    "du": 10,
                    "rb": 1024**2,
                    "wb": 2 * 1024**2,
                }
            },
        },
    )

    assert BeszelDiskSensor(coordinator, system).extra_state_attributes == {}
    assert (
        BeszelEFSDiskSensor(
            coordinator,
            system,
            "data",
        ).extra_state_attributes
        == {}
    )


def test_hub_offline_marks_status_unavailable() -> None:
    """Connectivity status itself is unavailable when the Hub update failed."""
    system = _system()
    coordinator = _coordinator(system)
    coordinator.last_update_success = False

    assert BeszelStatusBinarySensor(coordinator, system).available is False
    assert BeszelBandwidthSensor(coordinator, system).available is False


def test_cpu_core_usage_is_exposed_as_attributes() -> None:
    """Per-core usage should be available on the existing CPU entity."""
    system = _system()
    coordinator = _coordinator(system, stats={"cpus": [12, 34, 0, 100]})

    sensor = BeszelCPUSensor(coordinator, system)

    assert sensor.extra_state_attributes == {
        "cpu_core_count": 4,
        "cpu_core_0": 12,
        "cpu_core_1": 34,
        "cpu_core_2": 0,
        "cpu_core_3": 100,
    }


def test_missing_or_invalid_cpu_core_data_is_safe() -> None:
    """Missing and malformed per-core payloads must not raise errors."""
    system = _system()

    for stats in ({}, {"cpus": None}, {"cpus": "invalid"}, {"cpus": []}):
        sensor = BeszelCPUSensor(_coordinator(system, stats=stats), system)
        assert sensor.extra_state_attributes == {}


def test_cpu_core_attributes_are_hidden_while_agent_is_offline() -> None:
    """Stale per-core measurements must not remain visible while offline."""
    system = _system(status="down")
    coordinator = _coordinator(system, stats={"cpus": [12, 34]})

    sensor = BeszelCPUSensor(coordinator, system)

    assert sensor.extra_state_attributes == {}


def test_invalid_individual_cpu_core_values_are_skipped() -> None:
    """Only numeric per-core values should become Home Assistant attributes."""
    system = _system()
    coordinator = _coordinator(
        system,
        stats={"cpus": [10, "invalid", False, 40.5]},
    )

    sensor = BeszelCPUSensor(coordinator, system)

    assert sensor.extra_state_attributes == {
        "cpu_core_count": 4,
        "cpu_core_0": 10,
        "cpu_core_3": 40.5,
    }


def test_cpu_breakdown_is_exposed_as_attributes() -> None:
    """Detailed CPU categories should explain the aggregate CPU state."""
    system = _system()
    coordinator = _coordinator(
        system,
        stats={"cpub": [20, 10, 5, 1, 60]},
    )

    sensor = BeszelCPUSensor(coordinator, system)

    assert sensor.extra_state_attributes == {
        "cpu_user_percent": 20,
        "cpu_system_percent": 10,
        "cpu_iowait_percent": 5,
        "cpu_steal_percent": 1,
        "cpu_idle_percent": 60,
    }


def test_ram_details_include_cache_and_zfs_arc() -> None:
    """Official Beszel memory categories should be available as RAM details."""
    system = _system()
    coordinator = _coordinator(
        system,
        stats={"m": 16, "mu": 8, "mb": 3.5, "mz": 1.25},
    )

    sensor = BeszelRAMSensor(coordinator, system)

    assert sensor.extra_state_attributes["ram_buffer_cache_gib"] == 3.5
    assert sensor.extra_state_attributes["ram_zfs_arc_gib"] == 1.25


def test_load_average_sensors_use_official_array() -> None:
    """The 1, 5, and 15 minute values should be exposed separately."""
    system = _system()
    coordinator = _coordinator(system, stats={"la": [0.25, 0.5, 0.75]})

    sensors = [
        BeszelLoadAverageSensor(coordinator, system, index, label)
        for index, label in enumerate(("1m", "5m", "15m"))
    ]

    assert [sensor.native_value for sensor in sensors] == [0.25, 0.5, 0.75]
    assert all(sensor.available for sensor in sensors)
    assert all(sensor.entity_category is EntityCategory.DIAGNOSTIC for sensor in sensors)
    assert all(sensor.entity_registry_enabled_default is False for sensor in sensors)


def test_systemd_service_counts_are_diagnostic() -> None:
    """Failed services should be enabled while total services stay optional."""
    system = _system(info={"sv": [42, 2]})
    coordinator = _coordinator(system)
    failed = BeszelServiceCountSensor(
        coordinator,
        system,
        1,
        "failed",
        enabled_default=True,
    )
    total = BeszelServiceCountSensor(
        coordinator,
        system,
        0,
        "total",
        enabled_default=False,
    )

    assert failed.native_value == 2
    assert failed.entity_registry_enabled_default is True
    assert total.native_value == 42
    assert total.entity_registry_enabled_default is False
    assert failed.entity_category is EntityCategory.DIAGNOSTIC


def test_fan_speed_sensor_uses_rpm() -> None:
    """Fan readings from Beszel 0.18.8+ should use the RPM unit."""
    system = _system()
    coordinator = _coordinator(system, stats={"f": {"cpu_fan": 1450}})

    sensor = BeszelFanSensor(coordinator, system, "cpu_fan")

    assert sensor.available is True
    assert sensor.native_value == 1450
    assert sensor.native_unit_of_measurement == REVOLUTIONS_PER_MINUTE


def test_zfs_pool_sensor_exposes_usage_health_and_io() -> None:
    """Beszel 0.19+ ZFS pool metrics should use one compact entity."""
    system = _system()
    coordinator = _coordinator(
        system,
        stats={
            "z": {
                "tank": {
                    "n": "Storage",
                    "d": 10,
                    "du": 2.5,
                    "rb": 1024**2,
                    "wb": 2 * 1024**2,
                    "h": "ONLINE",
                }
            }
        },
    )

    sensor = BeszelZFSPoolSensor(coordinator, system, "tank")

    assert sensor.available is True
    assert sensor.name == "Server ZFS Storage"
    assert sensor.native_value == 25
    assert sensor.extra_state_attributes == {
        "pool_name": "tank",
        "pool_total_gib": 10,
        "pool_used_gib": 2.5,
        "pool_health": "ONLINE",
        "pool_read_mib_s": 1,
        "pool_write_mib_s": 2,
    }


def test_zfs_zero_usage_is_valid_and_malformed_pool_is_safe() -> None:
    """An empty ZFS pool is 0%, while malformed pool data stays unavailable."""
    system = _system()
    coordinator = _coordinator(
        system,
        stats={"z": {"empty": {"d": 10, "du": 0}, "broken": None}},
    )

    empty = BeszelZFSPoolSensor(coordinator, system, "empty")
    broken = BeszelZFSPoolSensor(coordinator, system, "broken")

    assert empty.available is True
    assert empty.native_value == 0
    assert broken.available is False
    assert broken.extra_state_attributes == {}


def test_zfs_health_binary_sensor_reports_pool_problems() -> None:
    """A degraded ZFS pool should expose a native problem binary sensor."""
    system = _system()
    coordinator = _coordinator(
        system,
        stats={
            "z": {
                "tank": {
                    "n": "Storage",
                    "d": 10,
                    "du": 2,
                    "h": "DEGRADED",
                }
            }
        },
    )

    sensor = BeszelZFSHealthBinarySensor(coordinator, system, "tank")

    assert sensor.available is True
    assert sensor.name == "Server ZFS Storage Health"
    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {"health_state": "DEGRADED"}


def test_named_battery_sensor_supports_multiple_batteries() -> None:
    """Each entry in Beszel's multi-battery payload should be addressable."""
    system = _system()
    coordinator = _coordinator(
        system,
        stats={"bats": {"BAT0": 80, "ups": 55}},
    )

    sensor = BeszelNamedBatterySensor(coordinator, system, "ups")

    assert sensor.available is True
    assert sensor.native_value == 55


def test_malformed_temperature_details_are_ignored() -> None:
    """Unexpected temperature payloads must not break the entity."""
    system = _system()
    coordinator = _coordinator(system, stats={"t": "invalid"})

    sensor = BeszelTemperatureSensor(coordinator, system)

    assert sensor.extra_state_attributes == {}


def test_malformed_optional_metric_maps_are_safe() -> None:
    """Malformed GPU and filesystem payloads must not crash existing entities."""
    system = _system()
    coordinator = _coordinator(system, stats={"efs": "invalid", "g": "invalid"})

    gpu = BeszelGPUSensor(coordinator, system, "gpu-1")
    efs = BeszelEFSDiskSensor(coordinator, system, "data")
    efs_total = BeszelDiskTotalSensor(coordinator, system, "data")

    assert gpu.available is False
    assert gpu.extra_state_attributes == {"gpu_vram_mb": None}
    assert efs.native_value is None
    assert efs.extra_state_attributes == {}
    assert efs_total.native_value is None


def test_malformed_optional_numeric_values_are_safe() -> None:
    """Unexpected scalar types must not raise from existing entities."""
    system = _system(
        info={"bb": "invalid", "dt": "invalid", "u": "invalid"}
    )
    coordinator = _coordinator(
        system,
        stats={
            "b": [False, "invalid"],
            "efs": {"data": {"d": "invalid", "du": 1}},
            "g": {"gpu-1": {"u": "invalid"}},
            "s": "invalid",
        },
    )

    assert BeszelNetworkReceiveSensor(coordinator, system).native_value is None
    assert BeszelNetworkSendSensor(coordinator, system).native_value is None
    assert BeszelEFSDiskSensor(coordinator, system, "data").native_value is None
    assert BeszelGPUSensor(coordinator, system, "gpu-1").available is False
    assert BeszelTemperatureSensor(coordinator, system).available is False
    bandwidth = BeszelBandwidthSensor(coordinator, system)
    assert bandwidth.available is False
    assert bandwidth.native_value is None


def test_malformed_smart_attributes_are_ignored() -> None:
    """Malformed raw S.M.A.R.T. attributes must not break the binary sensor."""
    system = _system()
    smart = {
        "id": "disk-1",
        "name": None,
        "model": 123,
        "state": "PASSED",
        "attributes": [None, "invalid", {"n": "Reallocated_Sector_Ct", "rv": 0}],
    }
    coordinator = _coordinator(system, smart_devices=[smart])
    sensor = BeszelSmartBinarySensor(coordinator, system, smart)

    assert sensor.name == "Server Disk S.M.A.R.T."
    assert sensor.extra_state_attributes["smart_reallocated_sectors"] == 0


async def test_setup_discovers_new_optional_metrics() -> None:
    """Setup should discover metrics while preserving the primary battery ID."""
    system = _system(info={"sv": [42, 2]})
    coordinator = _coordinator(
        system,
        stats={
            "bat": [80, 2],
            "bats": {"BAT0": 80, "ups": 55},
            "f": {"cpu_fan": 1450},
            "la": [0.25, 0.5, 0.75],
            "z": {"tank": {"d": 10, "du": 2, "h": "ONLINE"}},
        },
    )
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": {"coordinator": coordinator}}})
    async_add_entities = MagicMock()

    await async_setup_sensor_entry(
        hass,
        SimpleNamespace(entry_id="entry-1"),
        async_add_entities,
    )

    entities = async_add_entities.call_args.args[0]
    assert len(
        [entity for entity in entities if isinstance(entity, BeszelLoadAverageSensor)]
    ) == 3
    assert len(
        [entity for entity in entities if isinstance(entity, BeszelServiceCountSensor)]
    ) == 2
    assert len([entity for entity in entities if isinstance(entity, BeszelFanSensor)]) == 1
    assert len(
        [entity for entity in entities if isinstance(entity, BeszelNamedBatterySensor)]
    ) == 1
    assert len(
        [entity for entity in entities if isinstance(entity, BeszelBatterySensor)]
    ) == 1
    assert len(
        [entity for entity in entities if isinstance(entity, BeszelZFSPoolSensor)]
    ) == 1


async def test_later_coordinator_updates_discover_optional_sensors_once() -> None:
    """New metrics should appear without reloading the integration."""
    system = _system()
    coordinator = _coordinator(system, stats={})
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": {"coordinator": coordinator}}})
    async_add_entities = MagicMock()

    await async_setup_sensor_entry(
        hass,
        SimpleNamespace(entry_id="entry-1"),
        async_add_entities,
    )

    discover = coordinator.async_add_listener.call_args.args[0]
    async_add_entities.reset_mock()
    coordinator.data["stats"][system.id] = {
        "f": {"case": 900},
        "z": {"tank": {"d": 10, "du": 2}},
    }

    discover()

    new_entities = async_add_entities.call_args.args[0]
    assert len([entity for entity in new_entities if isinstance(entity, BeszelFanSensor)]) == 1
    assert len(
        [entity for entity in new_entities if isinstance(entity, BeszelZFSPoolSensor)]
    ) == 1

    discover()
    assert async_add_entities.call_count == 1


async def test_later_coordinator_updates_discover_new_system_once() -> None:
    """Systems added in Beszel should appear without reloading the integration."""
    first_system = _system()
    coordinator = _coordinator(first_system, stats={})
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": {"coordinator": coordinator}}})
    async_add_entities = MagicMock()

    await async_setup_sensor_entry(
        hass,
        SimpleNamespace(entry_id="entry-1"),
        async_add_entities,
    )

    discover = coordinator.async_add_listener.call_args.args[0]
    async_add_entities.reset_mock()
    second_system = SimpleNamespace(
        id="system-2",
        name="Backup",
        status="up",
        info={"cpu": 5, "mp": 10, "dp": 15, "u": 60, "bb": 0},
    )
    coordinator.data["systems"].append(second_system)
    coordinator.data["stats"][second_system.id] = {}

    discover()

    new_entities = async_add_entities.call_args.args[0]
    assert any(
        isinstance(entity, BeszelCPUSensor)
        and entity.unique_id == "beszel_system-2_cpu"
        for entity in new_entities
    )

    discover()
    assert async_add_entities.call_count == 1


async def test_later_coordinator_updates_discover_smart_devices_once() -> None:
    """New S.M.A.R.T. devices should appear without an integration reload."""
    system = _system()
    coordinator = _coordinator(system)
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": {"coordinator": coordinator}}})
    async_add_entities = MagicMock()

    await async_setup_binary_sensor_entry(
        hass,
        SimpleNamespace(entry_id="entry-1"),
        async_add_entities,
    )

    discover = coordinator.async_add_listener.call_args.args[0]
    async_add_entities.reset_mock()
    coordinator.data["smart_devices"][system.id] = [
        {"id": "disk-1", "name": "/dev/sda", "state": "PASSED"}
    ]
    coordinator.data["stats"][system.id] = {
        "z": {"tank": {"d": 10, "du": 2, "h": "ONLINE"}}
    }

    discover()

    new_entities = async_add_entities.call_args.args[0]
    assert len(
        [entity for entity in new_entities if isinstance(entity, BeszelSmartBinarySensor)]
    ) == 1
    assert len(
        [
            entity
            for entity in new_entities
            if isinstance(entity, BeszelZFSHealthBinarySensor)
        ]
    ) == 1

    discover()
    assert async_add_entities.call_count == 1


def test_efs_zero_percent_is_a_valid_value() -> None:
    """An empty additional filesystem should report 0%, not unknown."""
    system = _system()
    coordinator = _coordinator(
        system,
        stats={"efs": {"data": {"d": 100, "du": 0}}},
    )

    sensor = BeszelEFSDiskSensor(coordinator, system, "data")

    assert sensor.available is True
    assert sensor.native_value == 0


def test_malformed_battery_data_does_not_raise() -> None:
    """Incomplete battery arrays must make the entity unavailable safely."""
    system = _system()
    coordinator = _coordinator(system, stats={"bat": [80]})
    sensor = BeszelBatterySensor(coordinator, system)

    assert sensor.available is False
    assert sensor.native_value is None
    assert sensor.icon == "mdi:battery-unknown"


def test_units_and_network_conversions_are_binary() -> None:
    """Values divided by powers of 1024 must use IEC Home Assistant units."""
    system = _system()
    coordinator = _coordinator(system, stats={"b": [1024, 2048], "m": 8})

    bandwidth = BeszelBandwidthSensor(coordinator, system)
    receive = BeszelNetworkReceiveSensor(coordinator, system)
    ram_total = BeszelRAMTotalSensor(coordinator, system)

    assert bandwidth.native_value == 2
    assert bandwidth.native_unit_of_measurement == UnitOfDataRate.MEBIBYTES_PER_SECOND
    assert receive.native_value == 2
    assert receive.native_unit_of_measurement == UnitOfDataRate.KIBIBYTES_PER_SECOND
    assert ram_total.native_unit_of_measurement == UnitOfInformation.GIBIBYTES


def test_beszel_020_disk_and_interface_details_are_exposed() -> None:
    """New disk and per-interface values should be compact entity attributes."""
    system = _system(info={"rdn": "system"})
    coordinator = _coordinator(
        system,
        stats={
            "d": 100,
            "du": 25,
            "dio": [1024**2, 2 * 1024**2],
            "diot": [3 * 1024**3, 4 * 1024**3],
            "dios": [10, 20, 30, 4, 5, 40],
            "ni": {"eth0": [1024, 2048, 1024**3, 2 * 1024**3]},
        },
    )

    disk = BeszelDiskSensor(coordinator, system)
    receive = BeszelNetworkReceiveSensor(coordinator, system)
    send = BeszelNetworkSendSensor(coordinator, system)

    assert disk.extra_state_attributes["disk_read_mib_s"] == 1
    assert disk.extra_state_attributes["disk_write_mib_s"] == 2
    assert disk.extra_state_attributes["disk_read_gib_total"] == 3
    assert disk.extra_state_attributes["disk_write_gib_total"] == 4
    assert disk.extra_state_attributes["disk_io_utilization_percent"] == 30
    assert disk.extra_state_attributes["disk_name"] == "system"
    assert receive.extra_state_attributes == {
        "interfaces": {"eth0": {"rate_kib_s": 2, "total_gib": 2}}
    }
    assert send.extra_state_attributes == {
        "interfaces": {"eth0": {"rate_kib_s": 1, "total_gib": 1}}
    }


def test_beszel_020_extra_filesystem_io_details_are_exposed() -> None:
    """Additional filesystems should expose all official 0.20 I/O fields."""
    system = _system()
    coordinator = _coordinator(
        system,
        stats={
            "efs": {
                "data": {
                    "d": 100,
                    "du": 25,
                    "rb": 1024**2,
                    "wb": 2 * 1024**2,
                    "tr": 3 * 1024**3,
                    "tw": 4 * 1024**3,
                    "dios": [10, 20, 30, 4, 5, 40],
                }
            }
        },
    )

    attributes = BeszelEFSDiskSensor(
        coordinator,
        system,
        "data",
    ).extra_state_attributes

    assert attributes["read_mib_s"] == 1
    assert attributes["write_mib_s"] == 2
    assert attributes["read_gib_total"] == 3
    assert attributes["write_gib_total"] == 4
    assert attributes["io_utilization_percent"] == 30
    assert attributes["read_await_ms"] == 4
    assert attributes["write_await_ms"] == 5


def test_gpu_engine_and_package_power_details_are_exposed() -> None:
    """Beszel 0.20 GPU details should stay on the existing GPU entity."""
    system = _system()
    coordinator = _coordinator(
        system,
        stats={
            "g": {
                "gpu-1": {
                    "n": "GPU",
                    "u": 25,
                    "mt": 8192,
                    "pp": 42,
                    "e": {"render": 20, "compute": 0, "invalid": "bad"},
                }
            }
        },
    )

    sensor = BeszelGPUSensor(coordinator, system, "gpu-1")

    assert sensor.extra_state_attributes["gpu_package_power_w"] == 42
    assert sensor.extra_state_attributes["gpu_engines_percent"] == {
        "render": 20,
        "compute": 0,
    }


def test_smart_unknown_is_not_reported_as_a_problem() -> None:
    """Unknown S.M.A.R.T. health is unknown rather than a false alarm."""
    system = _system()
    smart = {"id": "disk-1", "name": "/dev/sda", "state": "UNKNOWN"}
    coordinator = _coordinator(system, smart_devices=[smart])

    sensor = BeszelSmartBinarySensor(coordinator, system, smart)

    assert sensor.available is True
    assert sensor.is_on is None
