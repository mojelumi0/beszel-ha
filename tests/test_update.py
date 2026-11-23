"""Regression tests for the Beszel Hub update entity."""

from unittest.mock import MagicMock

from custom_components.beszel_api.update import (
    BeszelHubUpdate,
)


def _device_fields(entity):
    """Read fields without deprecated DeviceInfo mapping access."""
    info = entity.device_info
    return info.as_dict() if hasattr(info, "as_dict") else info


def test_missing_hub_data_is_safe():
    coordinator = MagicMock()
    coordinator.data = None

    entity = BeszelHubUpdate(
        coordinator,
        "entry-1",
        "https://beszel.example/",
    )

    assert entity.installed_version is None
    assert entity.latest_version is None
    assert entity.release_url is None
    assert _device_fields(entity)["sw_version"] is None


def test_hub_update_data_is_exposed():
    coordinator = MagicMock()
    coordinator.data = {
        "hub_version": "0.18.8",
        "latest_version": "0.19.0",
        "latest_release_url": (
            "https://example.test/release"
        ),
    }

    entity = BeszelHubUpdate(
        coordinator,
        "entry-1",
        "https://beszel.example",
    )

    assert entity.installed_version == "0.18.8"
    assert entity.latest_version == "0.19.0"
    assert (
        entity.release_url
        == "https://example.test/release"
    )
    assert (
        _device_fields(entity)["sw_version"]
        == "0.18.8"
    )
