"""Regression coverage for Beszel 0.21.1 memory, CPU and current Wi-Fi data."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfInformation,
)

from custom_components.beszel_api.const import DOMAIN
from custom_components.beszel_api.sensor import (
    BeszelCPUSensor,
    BeszelRAMSensor,
    BeszelRAMTotalSensor,
    BeszelSWAPSensor,
    BeszelWiFiSignalSensor,
    async_setup_entry,
)

GIB = 1024**3


def _system(info=None):
    return SimpleNamespace(
        id="system-1", name="Server", status="up", info=info or {"cpu": 40, "mp": 50}
    )


def _coordinator(system, stats=None):
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {"systems": [system], "stats": {system.id: stats or {}}}
    return coordinator


async def _setup(system, stats=None):
    coordinator = _coordinator(system, stats)
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": {"coordinator": coordinator}}})
    entry = SimpleNamespace(entry_id="entry-1", async_on_unload=MagicMock())
    add_entities = MagicMock()
    await async_setup_entry(hass, entry, add_entities)
    return coordinator, entry, add_entities


@pytest.mark.parametrize("include_legacy", [False, True])
def test_ram_prefers_bytes_and_preserves_units_attributes_and_ids(include_legacy):
    """New bytes work alone and take precedence over conflicting old values."""
    system = _system()
    stats = {"mem": [16 * GIB, 8 * GIB, 3.5 * GIB, 1.25 * GIB]}
    if include_legacy:
        stats.update({"m": 1, "mu": 2, "mb": 3, "mz": 4})
    coordinator = _coordinator(system, stats)
    ram = BeszelRAMSensor(coordinator, system)
    total = BeszelRAMTotalSensor(coordinator, system)

    assert ram.unique_id == "beszel_system-1_ram"
    assert total.unique_id == "beszel_system-1_ram_total"
    assert ram.native_value == 50
    assert total.native_value == 16
    assert total.native_unit_of_measurement == UnitOfInformation.GIBIBYTES
    assert ram.extra_state_attributes == {
        "ram_used_gib": 8,
        "ram_total_gib": 16,
        "ram_used_gb": 8,
        "ram_total_gb": 16,
        "ram_buffer_cache_gib": 3.5,
        "ram_zfs_arc_gib": 1.25,
    }


@pytest.mark.parametrize(
    "new_memory",
    [
        None,
        [],
        "invalid",
        {"0": 1},
        [True, False, None, "bad"],
        [float("nan"), float("inf"), -1, float("-inf")],
    ],
)
def test_malformed_new_ram_fields_fall_back_to_legacy(new_memory):
    system = _system()
    coordinator = _coordinator(
        system, {"mem": new_memory, "m": 16, "mu": 8, "mb": 3.5, "mz": 1.25}
    )
    ram = BeszelRAMSensor(coordinator, system)
    assert ram.extra_state_attributes["ram_used_gib"] == 8
    assert ram.extra_state_attributes["ram_total_gib"] == 16
    assert ram.extra_state_attributes["ram_buffer_cache_gib"] == 3.5
    assert ram.extra_state_attributes["ram_zfs_arc_gib"] == 1.25
    assert BeszelRAMTotalSensor(coordinator, system).native_value == 16


def test_partial_ram_array_falls_back_per_field_and_preserves_byte_precision():
    system = _system()
    coordinator = _coordinator(system, {"mem": [16 * GIB + 1], "m": 1, "mu": 8})
    ram = BeszelRAMSensor(coordinator, system)
    assert ram.extra_state_attributes["ram_total_gib"] == (16 * GIB + 1) / GIB
    assert ram.extra_state_attributes["ram_used_gib"] == 8


def test_zero_ram_byte_fields_are_not_replaced_with_legacy_values():
    system = _system()
    coordinator = _coordinator(
        system, {"mem": [0, 0, 0, 0], "m": 16, "mu": 8, "mb": 3, "mz": 1}
    )
    assert set(
        BeszelRAMSensor(coordinator, system).extra_state_attributes.values()
    ) == {0}
    assert BeszelRAMTotalSensor(coordinator, system).native_value == 0


@pytest.mark.parametrize(
    ("stats", "expected_used", "expected_percent"),
    [
        ({"swap": [4 * GIB, GIB], "s": 8, "su": 6}, 1, 25),
        ({"swap": [4 * GIB, GIB]}, 1, 25),
        ({"s": 4, "su": 1}, 1, 25),
        ({"swap": [4 * GIB, 0], "s": 8, "su": 6}, 0, 0),
        ({"s": 4}, 0, 0),
        ({"swap": "invalid", "s": 4, "su": 1}, 1, 25),
        ({"swap": [True, None], "s": 4, "su": 1}, 1, 25),
        ({"swap": [float("nan"), -1], "s": 4, "su": 1}, 1, 25),
    ],
)
def test_swap_uses_bytes_or_legacy_without_changing_attributes(
    stats, expected_used, expected_percent
):
    system = _system()
    sensor = BeszelSWAPSensor(_coordinator(system, stats), system)
    assert sensor.unique_id == "beszel_system-1_swap"
    assert sensor.available is True
    assert sensor.native_value == expected_percent
    assert sensor.extra_state_attributes == {
        "swap_used_gib": expected_used,
        "swap_total_gib": 4,
        "swap_used_gb": expected_used,
        "swap_total_gb": 4,
    }


@pytest.mark.parametrize(
    "stats",
    [
        {},
        {"swap": [0, 0], "s": 4, "su": 1},
        {"s": 0},
        {"s": None},
        {"s": True},
        {"s": 4, "su": "bad"},
        {"swap": [float("inf"), None]},
    ],
)
def test_missing_zero_or_malformed_swap_is_unavailable(stats):
    system = _system()
    sensor = BeszelSWAPSensor(_coordinator(system, stats), system)
    assert sensor.available is False
    assert sensor.native_value is None


async def test_swap_is_discovered_with_only_the_new_byte_fields():
    _, _, add_entities = await _setup(_system(), {"swap": [4 * GIB, 0]})
    swaps = [
        entity
        for entity in add_entities.call_args.args[0]
        if isinstance(entity, BeszelSWAPSensor)
    ]
    assert len(swaps) == 1
    assert swaps[0].available is True
    assert swaps[0].native_value == 0


def test_extended_cpu_breakdown_preserves_existing_cpu_entity_and_attributes():
    system = _system()
    sensor = BeszelCPUSensor(
        _coordinator(system, {"cpub": [20, 10, 5, 1, 60, 1, 2, 1], "cpus": [0, 40]}),
        system,
    )
    assert sensor.unique_id == "beszel_system-1_cpu"
    assert sensor.native_value == 40
    assert sensor.extra_state_attributes == {
        "cpu_core_count": 2,
        "cpu_core_0": 0,
        "cpu_core_1": 40,
        "cpu_user_percent": 20,
        "cpu_system_percent": 10,
        "cpu_iowait_percent": 5,
        "cpu_steal_percent": 1,
        "cpu_idle_percent": 60,
        "cpu_irq_percent": 1,
        "cpu_softirq_percent": 2,
        "cpu_nice_percent": 1,
    }


@pytest.mark.parametrize(
    "extra", [[], [0], [0, 2], [0, 2, 3], [True, float("nan"), "bad"]]
)
def test_short_and_malformed_cpu_extensions_keep_legacy_attributes(extra):
    system = _system()
    sensor = BeszelCPUSensor(
        _coordinator(system, {"cpub": [20, 10, 5, 1, 60] + extra}), system
    )
    attributes = sensor.extra_state_attributes
    assert attributes["cpu_user_percent"] == 20
    assert attributes["cpu_idle_percent"] == 60
    expected_extra = {
        "cpu_irq_percent": 0,
        "cpu_softirq_percent": 2,
        "cpu_nice_percent": 3,
    }
    for index, (key, value) in enumerate(expected_extra.items()):
        if index < len(extra) and not isinstance(extra[0], bool):
            assert attributes[key] == value
        else:
            assert key not in attributes
    system.status = "down"
    assert sensor.extra_state_attributes == {}


def test_wifi_signal_uses_native_dbm_and_current_ssid():
    system = _system({"wf": {"wlan0": {"s": "Home", "r": -55.5}}})
    sensor = BeszelWiFiSignalSensor(_coordinator(system), system, "wlan0")
    assert sensor.unique_id == "beszel_system-1_wifi_signal_wlan0"
    assert sensor.name == "Server Wi-Fi Signal wlan0"
    assert sensor.available is True
    assert sensor.native_value == -55.5
    assert sensor.device_class == SensorDeviceClass.SIGNAL_STRENGTH
    assert sensor.native_unit_of_measurement == SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert sensor.extra_state_attributes == {"interface": "wlan0", "ssid": "Home"}
    system.info["wf"]["wlan0"] = {"s": "Guest", "r": -60}
    assert sensor.unique_id == "beszel_system-1_wifi_signal_wlan0"
    assert sensor.extra_state_attributes["ssid"] == "Guest"
    assert sensor.native_value == -60


@pytest.mark.parametrize(
    "wifi",
    [
        None,
        [],
        "bad",
        {},
        {"wlan0": None},
        {"wlan0": {}},
        {"wlan0": {"s": "Home"}},
        {"wlan0": {"r": None}},
        {"wlan0": {"r": True}},
        {"wlan0": {"r": "-55"}},
        {"wlan0": {"r": float("nan")}},
        {"wlan0": {"r": float("inf")}},
        {"": {"r": -55}},
        {42: {"r": -55}},
    ],
)
async def test_wifi_is_not_created_without_a_valid_current_signal(wifi):
    """Historical wf values must never invent a currently connected sensor."""
    system = _system({"wf": wifi})
    _, _, add_entities = await _setup(system, {"wf": {"wlan0": -55}})
    assert not any(
        isinstance(entity, BeszelWiFiSignalSensor)
        for entity in add_entities.call_args.args[0]
    )
    sensor = BeszelWiFiSignalSensor(_coordinator(system), system, "wlan0")
    assert sensor.available is False
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


async def test_absent_wifi_field_and_historical_stats_do_not_create_a_sensor():
    _, _, add_entities = await _setup(_system(), {"wf": {"wlan0": -55}})
    assert not any(
        isinstance(entity, BeszelWiFiSignalSensor)
        for entity in add_entities.call_args.args[0]
    )


async def test_wifi_discovery_retains_interfaces_and_recovers_without_duplicates():
    system = _system()
    coordinator, entry, add_entities = await _setup(system)
    discover = coordinator.async_add_listener.call_args.args[0]
    entry.async_on_unload.assert_called_once_with(
        coordinator.async_add_listener.return_value
    )
    add_entities.reset_mock()

    system.info["wf"] = {
        "wlan0": {"s": "Home", "r": -55},
        "en0": {"r": -70},
        "Wi-Fi": {"s": None, "r": 0},
        "missing": {"s": "Guest"},
    }
    discover()
    entities = add_entities.call_args.args[0]
    assert {entity.unique_id for entity in entities} == {
        "beszel_system-1_wifi_signal_wlan0",
        "beszel_system-1_wifi_signal_en0",
        "beszel_system-1_wifi_signal_Wi-Fi",
    }
    sensors = {
        entity.extra_state_attributes["interface"]: entity for entity in entities
    }
    assert sensors["Wi-Fi"].native_value == 0
    assert sensors["en0"].extra_state_attributes == {"interface": "en0"}
    discover()
    assert add_entities.call_count == 1

    coordinator.data["stats"][system.id] = {"wf": {"wlan0": -55}}
    del system.info["wf"]["wlan0"]
    discover()
    assert sensors["wlan0"].available is False
    assert sensors["wlan0"].native_value is None
    assert sensors["wlan0"].extra_state_attributes == {}
    assert sensors["en0"].available is True
    assert add_entities.call_count == 1

    system.info["wf"] = {"wlan0": {"s": "Guest", "r": -48}, "wlan1": {"r": -80}}
    discover()
    assert add_entities.call_count == 2
    assert [entity.unique_id for entity in add_entities.call_args.args[0]] == [
        "beszel_system-1_wifi_signal_wlan1"
    ]
    assert sensors["wlan0"].available is True
    assert sensors["wlan0"].native_value == -48
    assert sensors["wlan0"].extra_state_attributes["ssid"] == "Guest"
    assert sensors["en0"].native_value is None
    discover()
    assert add_entities.call_count == 2


@pytest.mark.parametrize("outage", ["hub", "agent", "system", "signal", "wifi"])
def test_wifi_hides_stale_values_and_metadata_during_outages(outage):
    system = _system({"wf": {"wlan0": {"s": "Home", "r": -55}}})
    coordinator = _coordinator(system, {"wf": {"wlan0": -55}})
    sensor = BeszelWiFiSignalSensor(coordinator, system, "wlan0")
    if outage == "hub":
        coordinator.last_update_success = False
    elif outage == "agent":
        system.status = "down"
    elif outage == "system":
        coordinator.data["systems"] = []
    elif outage == "signal":
        system.info["wf"]["wlan0"].pop("r")
    else:
        system.info.pop("wf")
    assert sensor.available is False
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


@pytest.mark.parametrize(
    "new_info", [{}, {"wf": {"wlan0": {"s": "Home"}}}, {"wf": {"wlan0": {"r": -42}}}]
)
def test_wifi_refresh_discards_the_previous_system_snapshot(new_info):
    """PocketBase replaces system objects on each coordinator refresh."""
    system = _system({"wf": {"wlan0": {"s": "Home", "r": -55}}})
    coordinator = _coordinator(system, {"wf": {"wlan0": -55}})
    sensor = BeszelWiFiSignalSensor(coordinator, system, "wlan0")
    assert sensor.native_value == -55
    replacement = _system()
    replacement.info = new_info
    coordinator.data["systems"] = [replacement]
    with patch(
        "homeassistant.helpers.update_coordinator.CoordinatorEntity._handle_coordinator_update"
    ):
        sensor._handle_coordinator_update()
    expected = new_info.get("wf", {}).get("wlan0", {}).get("r")
    assert sensor.native_value == expected
    assert sensor.available is (expected is not None)
    if expected is None:
        assert sensor.extra_state_attributes == {}


async def test_same_wifi_interface_on_different_systems_is_discovered_separately():
    first = _system({"wf": {"wlan0": {"r": -55}}})
    coordinator, _, add_entities = await _setup(first)
    second = _system({"wf": {"wlan0": {"r": -70}}})
    second.id = "system-2"
    second.name = "Backup"
    coordinator.data["systems"].append(second)
    add_entities.reset_mock()
    coordinator.async_add_listener.call_args.args[0]()
    signals = [
        entity
        for entity in add_entities.call_args.args[0]
        if isinstance(entity, BeszelWiFiSignalSensor)
    ]
    assert len(signals) == 1
    assert signals[0].unique_id == "beszel_system-2_wifi_signal_wlan0"
    assert signals[0].native_value == -70


@pytest.mark.parametrize(
    "new_swap",
    [
        [4 * GIB],
        [4 * GIB, None],
        [4 * GIB, True],
        [4 * GIB, "bad"],
        [4 * GIB, -1],
        [4 * GIB, float("nan")],
        [4 * GIB, float("inf")],
        [None, GIB],
        [4 * GIB, 6 * GIB],
        [0, GIB],
    ],
)
def test_invalid_new_swap_pair_falls_back_to_the_entire_legacy_pair(new_swap):
    """Never combine a new 4 GiB total with an old 6 GiB used value."""
    system = _system()
    sensor = BeszelSWAPSensor(
        _coordinator(system, {"swap": new_swap, "s": 8, "su": 6}), system
    )
    assert sensor.available is True
    assert sensor.native_value == 75
    assert sensor.extra_state_attributes == {
        "swap_total_gib": 8,
        "swap_used_gib": 6,
        "swap_total_gb": 8,
        "swap_used_gb": 6,
    }


@pytest.mark.parametrize("new_swap", [[4 * GIB], [4 * GIB, None], [None, GIB]])
def test_legacy_swap_fallback_keeps_missing_su_as_zero(new_swap):
    system = _system()
    sensor = BeszelSWAPSensor(_coordinator(system, {"swap": new_swap, "s": 8}), system)
    assert sensor.available is True
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes["swap_total_gib"] == 8
    assert sensor.extra_state_attributes["swap_used_gib"] == 0


@pytest.mark.parametrize(
    "stats",
    [
        {"swap": [4 * GIB]},
        {"swap": [4 * GIB, None]},
        {"swap": [4 * GIB, True]},
        {"swap": [4 * GIB, "bad"]},
        {"swap": [4 * GIB, -1]},
        {"swap": [4 * GIB, float("nan")]},
        {"swap": [4 * GIB, float("inf")]},
        {"swap": [4 * GIB], "su": 6},
        {"swap": [4 * GIB, 6 * GIB]},
        {"s": 4, "su": 6},
        {"swap": [4 * GIB, None], "s": 8, "su": 9},
        {"swap": [None, GIB], "s": 8, "su": None},
    ],
)
def test_swap_without_any_valid_pair_is_unavailable_and_has_no_fake_attributes(stats):
    system = _system()
    sensor = BeszelSWAPSensor(_coordinator(system, stats), system)
    assert sensor.available is False
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {
        "swap_total_gib": None,
        "swap_used_gib": None,
        "swap_total_gb": None,
        "swap_used_gb": None,
    }


async def test_swap_discovery_waits_for_a_complete_pair_and_keeps_the_entity_on_loss():
    system = _system()
    coordinator, _, add_entities = await _setup(system, {"swap": [4 * GIB]})
    assert not any(
        isinstance(entity, BeszelSWAPSensor)
        for entity in add_entities.call_args.args[0]
    )
    discover = coordinator.async_add_listener.call_args.args[0]
    add_entities.reset_mock()
    coordinator.data["stats"][system.id] = {"swap": [4 * GIB, GIB]}
    discover()
    swaps = [
        entity
        for entity in add_entities.call_args.args[0]
        if isinstance(entity, BeszelSWAPSensor)
    ]
    assert len(swaps) == 1
    sensor = swaps[0]
    assert sensor.native_value == 25
    coordinator.data["stats"][system.id] = {"swap": [4 * GIB, None]}
    discover()
    assert sensor.available is False
    assert sensor.native_value is None
    coordinator.data["stats"][system.id] = {"s": 8, "su": 6}
    discover()
    assert sensor.available is True
    assert sensor.native_value == 75
    assert add_entities.call_count == 1
