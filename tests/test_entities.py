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
)
from custom_components.beszel_api.const import DOMAIN
from custom_components.beszel_api.sensor import (
    BeszelBandwidthSensor,
    BeszelBatterySensor,
    BeszelCPUSensor,
    BeszelDiskTotalSensor,
    BeszelEFSDiskSensor,
    BeszelFanSensor,
    BeszelGPUSensor,
    BeszelLoadAverageSensor,
    BeszelNamedBatterySensor,
    BeszelNetworkReceiveSensor,
    BeszelRAMSensor,
    BeszelRAMTotalSensor,
    BeszelServiceCountSensor,
    BeszelTemperatureSensor,
)
from custom_components.beszel_api.sensor import (
    async_setup_entry as async_setup_sensor_entry,
)


def _system(
    *,
    status: str = "up",
    info: dict | None = None,
) -> SimpleNamespace:
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
        "smart_devices": {
            system.id: smart_devices or [],
        },
    }
    return coordinator


def test_agent_offline_keeps_only_connectivity_status_available():
    system = _system(status="down")
    smart = {
        "id": "disk-1",
        "name": "/dev/sda",
        "state": "PASSED",
    }
    coordinator = _coordinator(
        system,
        smart_devices=[smart],
    )

    status = BeszelStatusBinarySensor(coordinator, system)
    bandwidth = BeszelBandwidthSensor(coordinator, system)
    smart_status = BeszelSmartBinarySensor(
        coordinator,
        system,
        smart,
    )

    assert status.available is True
    assert status.is_on is False
    assert bandwidth.available is False
    assert smart_status.available is False


def test_hub_offline_marks_status_unavailable():
    system = _system()
    coordinator = _coordinator(system)
    coordinator.last_update_success = False

    assert (
        BeszelStatusBinarySensor(coordinator, system).available
        is False
    )
    assert (
        BeszelBandwidthSensor(coordinator, system).available
        is False
    )


def test_cpu_core_usage_is_exposed_as_attributes():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={"cpus": [12, 34, 0, 100]},
    )

    sensor = BeszelCPUSensor(coordinator, system)

    assert sensor.extra_state_attributes == {
        "cpu_core_count": 4,
        "cpu_core_0": 12,
        "cpu_core_1": 34,
        "cpu_core_2": 0,
        "cpu_core_3": 100,
    }


def test_missing_or_invalid_cpu_core_data_is_safe():
    system = _system()

    invalid_values = (
        {},
        {"cpus": None},
        {"cpus": "invalid"},
        {"cpus": []},
    )
    for stats in invalid_values:
        sensor = BeszelCPUSensor(
            _coordinator(system, stats=stats),
            system,
        )
        assert sensor.extra_state_attributes == {}


def test_cpu_core_attributes_are_hidden_while_agent_is_offline():
    system = _system(status="down")
    coordinator = _coordinator(
        system,
        stats={"cpus": [12, 34]},
    )

    sensor = BeszelCPUSensor(coordinator, system)

    assert sensor.extra_state_attributes == {}


def test_invalid_individual_cpu_core_values_are_skipped():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={
            "cpus": [10, "invalid", False, 40.5],
        },
    )

    sensor = BeszelCPUSensor(coordinator, system)

    assert sensor.extra_state_attributes == {
        "cpu_core_count": 4,
        "cpu_core_0": 10,
        "cpu_core_3": 40.5,
    }


def test_cpu_breakdown_is_exposed_as_attributes():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={
            "cpub": [20, 10, 5, 1, 60],
        },
    )

    sensor = BeszelCPUSensor(coordinator, system)

    assert sensor.extra_state_attributes == {
        "cpu_user_percent": 20,
        "cpu_system_percent": 10,
        "cpu_iowait_percent": 5,
        "cpu_steal_percent": 1,
        "cpu_idle_percent": 60,
    }


def test_ram_details_include_cache_and_zfs_arc():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={
            "m": 16,
            "mu": 8,
            "mb": 3.5,
            "mz": 1.25,
        },
    )

    sensor = BeszelRAMSensor(coordinator, system)

    assert (
        sensor.extra_state_attributes[
            "ram_buffer_cache_gib"
        ]
        == 3.5
    )
    assert (
        sensor.extra_state_attributes["ram_zfs_arc_gib"]
        == 1.25
    )


def test_load_average_sensors_use_official_array():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={"la": [0.25, 0.5, 0.75]},
    )

    sensors = [
        BeszelLoadAverageSensor(
            coordinator,
            system,
            index,
            label,
        )
        for index, label in enumerate(
            ("1m", "5m", "15m")
        )
    ]

    assert [
        sensor.native_value for sensor in sensors
    ] == [0.25, 0.5, 0.75]
    assert all(sensor.available for sensor in sensors)
    assert all(
        sensor.entity_category
        is EntityCategory.DIAGNOSTIC
        for sensor in sensors
    )
    assert all(
        sensor.entity_registry_enabled_default is False
        for sensor in sensors
    )


def test_systemd_service_counts_are_diagnostic():
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
    assert (
        failed.entity_registry_enabled_default is True
    )
    assert total.native_value == 42
    assert (
        total.entity_registry_enabled_default is False
    )
    assert (
        failed.entity_category
        is EntityCategory.DIAGNOSTIC
    )


def test_fan_speed_sensor_uses_rpm():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={"f": {"cpu_fan": 1450}},
    )

    sensor = BeszelFanSensor(
        coordinator,
        system,
        "cpu_fan",
    )

    assert sensor.available is True
    assert sensor.native_value == 1450
    assert (
        sensor.native_unit_of_measurement
        == REVOLUTIONS_PER_MINUTE
    )


def test_named_battery_sensor_supports_multiple_batteries():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={
            "bats": {
                "BAT0": 80,
                "ups": 55,
            },
        },
    )

    sensor = BeszelNamedBatterySensor(
        coordinator,
        system,
        "ups",
    )

    assert sensor.available is True
    assert sensor.native_value == 55


def test_malformed_temperature_details_are_ignored():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={"t": "invalid"},
    )

    sensor = BeszelTemperatureSensor(
        coordinator,
        system,
    )

    assert sensor.extra_state_attributes == {}


def test_malformed_optional_metric_maps_are_safe():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={
            "efs": "invalid",
            "g": "invalid",
        },
    )

    gpu = BeszelGPUSensor(
        coordinator,
        system,
        "gpu-1",
    )
    efs = BeszelEFSDiskSensor(
        coordinator,
        system,
        "data",
    )
    efs_total = BeszelDiskTotalSensor(
        coordinator,
        system,
        "data",
    )

    assert gpu.available is False
    assert gpu.extra_state_attributes == {
        "gpu_vram_mb": None,
    }
    assert efs.native_value is None
    assert efs.extra_state_attributes == {}
    assert efs_total.native_value is None


async def test_setup_discovers_new_optional_metrics():
    system = _system(info={"sv": [42, 2]})
    coordinator = _coordinator(
        system,
        stats={
            "bat": [80, 2],
            "bats": {
                "BAT0": 80,
                "ups": 55,
            },
            "f": {
                "cpu_fan": 1450,
            },
            "la": [0.25, 0.5, 0.75],
        },
    )
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": {
                    "coordinator": coordinator,
                },
            },
        },
    )
    async_add_entities = MagicMock()

    await async_setup_sensor_entry(
        hass,
        SimpleNamespace(entry_id="entry-1"),
        async_add_entities,
    )

    entities = async_add_entities.call_args.args[0]

    assert len(
        [
            entity
            for entity in entities
            if isinstance(
                entity,
                BeszelLoadAverageSensor,
            )
        ]
    ) == 3
    assert len(
        [
            entity
            for entity in entities
            if isinstance(
                entity,
                BeszelServiceCountSensor,
            )
        ]
    ) == 2
    assert len(
        [
            entity
            for entity in entities
            if isinstance(entity, BeszelFanSensor)
        ]
    ) == 1
    assert len(
        [
            entity
            for entity in entities
            if isinstance(
                entity,
                BeszelNamedBatterySensor,
            )
        ]
    ) == 1
    assert len(
        [
            entity
            for entity in entities
            if isinstance(
                entity,
                BeszelBatterySensor,
            )
        ]
    ) == 1


def test_efs_zero_percent_is_a_valid_value():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={
            "efs": {
                "data": {
                    "d": 100,
                    "du": 0,
                },
            },
        },
    )

    sensor = BeszelEFSDiskSensor(
        coordinator,
        system,
        "data",
    )

    assert sensor.available is True
    assert sensor.native_value == 0


def test_malformed_battery_data_does_not_raise():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={"bat": [80]},
    )
    sensor = BeszelBatterySensor(
        coordinator,
        system,
    )

    assert sensor.available is False
    assert sensor.native_value is None
    assert sensor.icon == "mdi:battery-unknown"


def test_units_and_network_conversions_are_binary():
    system = _system()
    coordinator = _coordinator(
        system,
        stats={
            "b": [1024, 2048],
            "m": 8,
        },
    )

    bandwidth = BeszelBandwidthSensor(
        coordinator,
        system,
    )
    receive = BeszelNetworkReceiveSensor(
        coordinator,
        system,
    )
    ram_total = BeszelRAMTotalSensor(
        coordinator,
        system,
    )

    assert bandwidth.native_value == 2
    assert (
        bandwidth.native_unit_of_measurement
        == UnitOfDataRate.MEBIBYTES_PER_SECOND
    )
    assert receive.native_value == 2
    assert (
        receive.native_unit_of_measurement
        == UnitOfDataRate.KIBIBYTES_PER_SECOND
    )
    assert (
        ram_total.native_unit_of_measurement
        == UnitOfInformation.GIBIBYTES
    )


def test_smart_unknown_is_not_reported_as_a_problem():
    system = _system()
    smart = {
        "id": "disk-1",
        "name": "/dev/sda",
        "state": "UNKNOWN",
    }
    coordinator = _coordinator(
        system,
        smart_devices=[smart],
    )

    sensor = BeszelSmartBinarySensor(
        coordinator,
        system,
        smart,
    )

    assert sensor.available is True
    assert sensor.is_on is None
