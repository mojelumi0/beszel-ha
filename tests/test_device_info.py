"""Keep device identities and metadata compatible across Home Assistant versions."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.beszel_api.binary_sensor import BeszelStatusBinarySensor
from custom_components.beszel_api.const import DOMAIN
from custom_components.beszel_api.sensor import BeszelCPUSensor
from custom_components.beszel_api.update import BeszelHubUpdate


def _device_fields(entity):
    """Read public fields from both legacy and dataclass DeviceInfo objects."""
    info = entity.device_info
    return info.as_dict() if hasattr(info, "as_dict") else info


def _system(name="Server", info=None):
    return SimpleNamespace(id="system-1", name=name, status="up", info=info)


def _coordinator(system):
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {"systems": [system], "stats": {}}
    return coordinator


def test_sensor_and_binary_sensor_describe_the_same_device():
    """Both platforms must keep grouping an agent under its Beszel system ID."""
    system = _system(info={"m": "Server model", "v": "0.21.1", "k": "6.12"})
    coordinator = _coordinator(system)
    sensor = BeszelCPUSensor(coordinator, system)
    status = BeszelStatusBinarySensor(coordinator, system)

    sensor_info = _device_fields(sensor)
    assert sensor_info == _device_fields(status)
    assert sensor_info["identifiers"] == {(DOMAIN, "system-1")}
    assert sensor_info["name"] == "Server"
    assert sensor_info["model"] == "Server model"
    assert sensor_info["sw_version"] == "0.21.1"
    assert sensor_info["hw_version"] == "6.12"
    assert sensor.unique_id == "beszel_system-1_cpu"
    assert status.unique_id == "beszel_system-1_status"


@pytest.mark.parametrize("entity_type", [BeszelCPUSensor, BeszelStatusBinarySensor])
def test_device_identity_survives_refresh_and_missing_system(entity_type):
    """Renames, partial metadata and disappearance must not invent a new device."""
    system = _system(info={"m": "Old model", "v": "0.21.1", "k": "6.12"})
    coordinator = _coordinator(system)
    entity = entity_type(coordinator, system)
    identity = _device_fields(entity)["identifiers"]
    unique_id = entity.unique_id

    coordinator.data["systems"] = [_system(name="Renamed server")]
    with patch.object(entity, "async_write_ha_state"):
        entity._handle_coordinator_update()

    refreshed = _device_fields(entity)
    assert refreshed["identifiers"] == identity
    assert refreshed["name"] == "Renamed server"
    assert refreshed["model"] is None
    assert refreshed["sw_version"] is None
    assert refreshed["hw_version"] is None
    assert entity.unique_id == unique_id

    coordinator.data["systems"] = []
    with patch.object(entity, "async_write_ha_state"):
        entity._handle_coordinator_update()

    assert entity.device_info is None
    assert entity.unique_id == unique_id


def test_hub_device_identity_survives_version_refresh():
    """Hub metadata updates must retain the config entry's existing device ID."""
    coordinator = MagicMock()
    coordinator.data = {"hub_version": "0.21.1"}
    entity = BeszelHubUpdate(coordinator, "entry-1", "https://beszel.example")

    original = _device_fields(entity)
    assert original["identifiers"] == {(DOMAIN, "entry-1")}
    assert original["sw_version"] == "0.21.1"
    assert entity.unique_id == "beszel_hub_update_entry-1"

    coordinator.data = {"hub_version": "0.21.2"}
    refreshed = _device_fields(entity)
    assert refreshed["identifiers"] == original["identifiers"]
    assert refreshed["sw_version"] == "0.21.2"

    coordinator.data = None
    missing = _device_fields(entity)
    assert missing["identifiers"] == original["identifiers"]
    assert missing["sw_version"] is None
    assert entity.unique_id == "beszel_hub_update_entry-1"
