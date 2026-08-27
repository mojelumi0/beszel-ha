"""Regression tests for Beszel sensor behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.const import UnitOfDataRate, UnitOfInformation

from custom_components.beszel_api.binary_sensor import (
    BeszelSmartBinarySensor,
    BeszelStatusBinarySensor,
)
from custom_components.beszel_api.sensor import (
    BeszelBandwidthSensor,
    BeszelBatterySensor,
    BeszelEFSDiskSensor,
    BeszelNetworkReceiveSensor,
    BeszelRAMTotalSensor,
)


def _system(*, status: str = "up") -> SimpleNamespace:
    return SimpleNamespace(
        id="system-1",
        name="Server",
        status=status,
        info={
            "bb": 2 * 1024**2,
            "cpu": 10,
            "dp": 25,
            "dt": 42,
            "mp": 30,
            "u": 600,
        },
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


def test_hub_offline_marks_status_unavailable() -> None:
    """Connectivity status itself is unavailable when the Hub update failed."""
    system = _system()
    coordinator = _coordinator(system)
    coordinator.last_update_success = False

    assert BeszelStatusBinarySensor(coordinator, system).available is False
    assert BeszelBandwidthSensor(coordinator, system).available is False


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


def test_smart_unknown_is_not_reported_as_a_problem() -> None:
    """Unknown S.M.A.R.T. health is unknown rather than a false alarm."""
    system = _system()
    smart = {"id": "disk-1", "name": "/dev/sda", "state": "UNKNOWN"}
    coordinator = _coordinator(system, smart_devices=[smart])

    sensor = BeszelSmartBinarySensor(coordinator, system, smart)

    assert sensor.available is True
    assert sensor.is_on is None
