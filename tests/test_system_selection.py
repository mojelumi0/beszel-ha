"""System selection must preserve identity and filter actual coordinator work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.beszel_api import _filter_systems, async_setup_entry
from custom_components.beszel_api.config_flow import BeszelConfigFlow, BeszelOptionsFlow
from custom_components.beszel_api.const import (
    CONF_SYSTEM_IDS,
    CONF_SYSTEM_SELECTION,
    DOMAIN,
    SYSTEM_SELECTION_ALL,
    SYSTEM_SELECTION_SELECTED,
)

CONNECTION = {
    "url": "https://beszel.example",
    "username": "user",
    "password": "test-password",
    "update_interval": 120,
    "verify_ssl": True,
}


def _systems():
    return [
        SimpleNamespace(id="one", name="Server", status="up"),
        SimpleNamespace(id="two", name="Server", status="down"),
    ]


def _hass():
    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_entries.return_value = []
    hass.config_entries.async_reload = AsyncMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    async def execute(target, *args):
        return target(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=execute)
    return hass


@pytest.mark.parametrize("mode", [SYSTEM_SELECTION_ALL, SYSTEM_SELECTION_SELECTED])
async def test_setup_selects_systems_after_connection_validation(mode):
    """The real flow and selector schema distinguish duplicate system names."""
    flow = BeszelConfigFlow()
    flow.hass = _hass()
    client = MagicMock()
    client.get_systems.return_value = _systems()
    with patch(
        "custom_components.beszel_api.config_flow.BeszelApiClient", return_value=client
    ):
        form = await flow.async_step_user(CONNECTION)
    assert form["step_id"] == "systems"
    schema = form["data_schema"]
    selector = next(
        value for key, value in schema.schema.items() if key.schema == CONF_SYSTEM_IDS
    )
    assert selector.config["options"] == [
        {"value": "one", "label": "Server (one)"},
        {"value": "two", "label": "Server (two)"},
    ]
    submission = schema({CONF_SYSTEM_SELECTION: mode, CONF_SYSTEM_IDS: ["two"]})
    result = await flow.async_step_systems(submission)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_SYSTEM_SELECTION] == mode
    assert result["data"][CONF_SYSTEM_IDS] == (["two"] if mode == "selected" else [])
    assert result["data"]["url"] == CONNECTION["url"]
    client.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("submission", "error"),
    [
        (
            {CONF_SYSTEM_SELECTION: "selected", CONF_SYSTEM_IDS: []},
            "no_systems_selected",
        ),
        (
            {CONF_SYSTEM_SELECTION: "selected", CONF_SYSTEM_IDS: ["other"]},
            "invalid_system_selection",
        ),
        (
            {CONF_SYSTEM_SELECTION: "selected", CONF_SYSTEM_IDS: "one"},
            "invalid_system_selection",
        ),
        ({CONF_SYSTEM_SELECTION: "invalid"}, "invalid_system_selection"),
    ],
)
async def test_invalid_setup_selection_keeps_the_form_open(submission, error):
    flow = BeszelConfigFlow()
    flow.hass = _hass()
    client = MagicMock()
    client.get_systems.return_value = _systems()
    with patch(
        "custom_components.beszel_api.config_flow.BeszelApiClient", return_value=client
    ):
        await flow.async_step_user(CONNECTION)
    result = await flow.async_step_systems(submission)
    assert result["step_id"] == "systems"
    assert result["errors"] == {"base": error}


async def test_empty_hub_can_be_configured_in_all_mode():
    flow = BeszelConfigFlow()
    flow.hass = _hass()
    client = MagicMock()
    client.get_systems.return_value = []
    with patch(
        "custom_components.beszel_api.config_flow.BeszelApiClient", return_value=client
    ):
        form = await flow.async_step_user(CONNECTION)
    submission = form["data_schema"]({CONF_SYSTEM_SELECTION: "all"})
    result = await flow.async_step_systems(submission)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_SYSTEM_IDS] == []


@pytest.mark.parametrize("mode", [SYSTEM_SELECTION_ALL, SYSTEM_SELECTION_SELECTED])
async def test_options_preserve_unavailable_ids_until_selection_is_saved(mode):
    """Cancelling options cannot overwrite settings; missing IDs remain usable."""
    data = {
        **CONNECTION,
        CONF_SYSTEM_SELECTION: "selected",
        CONF_SYSTEM_IDS: ["missing"],
    }
    entry = MockConfigEntry(domain=DOMAIN, data=data, entry_id="entry-1")
    flow = BeszelOptionsFlow()
    flow.hass = _hass()
    flow.handler = entry.entry_id
    flow.hass.config_entries.async_get_known_entry.return_value = entry
    client = MagicMock()
    client.get_systems.return_value = _systems()
    with patch(
        "custom_components.beszel_api.config_flow.BeszelApiClient", return_value=client
    ):
        form = await flow.async_step_init({"update_interval": 60})
    assert form["step_id"] == "systems"
    defaults = form["data_schema"]({CONF_SYSTEM_SELECTION: "selected"})
    assert defaults[CONF_SYSTEM_IDS] == ["missing"]
    invalid = await flow.async_step_systems({CONF_SYSTEM_SELECTION: "selected"})
    assert invalid["errors"]["base"] == "no_systems_selected"
    flow.hass.config_entries.async_update_entry.assert_not_called()
    flow.hass.config_entries.async_reload.assert_not_called()

    result = await flow.async_step_systems(
        {CONF_SYSTEM_SELECTION: mode, CONF_SYSTEM_IDS: ["missing", "two", "two"]}
    )
    assert result["type"] == "create_entry"
    saved = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert saved[CONF_SYSTEM_IDS] == (["missing", "two"] if mode == "selected" else [])
    assert saved["update_interval"] == 60
    assert saved["password"] == CONNECTION["password"]
    assert entry.data == data
    flow.hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize(
    "selection",
    [
        {},
        {CONF_SYSTEM_SELECTION: "all"},
        {CONF_SYSTEM_SELECTION: "selected", CONF_SYSTEM_IDS: ["one"]},
    ],
)
async def test_coordinator_filters_stats_smart_devices_and_new_systems(selection):
    """Exercise the actual coordinator update, including later Hub changes."""
    hass = _hass()
    entry = MockConfigEntry(domain=DOMAIN, data={**CONNECTION, **selection})
    systems = _systems()
    client = MagicMock()
    client.get_systems.side_effect = lambda: list(systems)
    client.get_system_stats.return_value = SimpleNamespace(stats={"cpu": 10})
    client.get_smart_devices.return_value = [
        SimpleNamespace(id="disk-one", system="one"),
        SimpleNamespace(id="disk-two", system="two"),
    ]
    coordinators = []

    def coordinator(*args, **kwargs):
        instance = MagicMock()
        instance.update_method = kwargs["update_method"]
        instance.async_config_entry_first_refresh = AsyncMock()
        instance.async_refresh = AsyncMock()
        coordinators.append(instance)
        return instance

    with (
        patch("custom_components.beszel_api.BeszelApiClient", return_value=client),
        patch(
            "custom_components.beszel_api.DataUpdateCoordinator",
            side_effect=coordinator,
        ),
    ):
        assert await async_setup_entry(hass, entry) is True
    selected_only = selection.get(CONF_SYSTEM_SELECTION) == "selected"
    expected = ["one"] if selected_only else ["one", "two"]
    result = await coordinators[0].update_method()
    assert [s.id for s in result["systems"]] == expected
    assert list(result["stats"]) == expected
    assert list(result["smart_devices"]) == expected
    assert [call.args[0] for call in client.get_system_stats.call_args_list] == expected

    systems.append(SimpleNamespace(id="three", name="New server", status="up"))
    client.get_system_stats.reset_mock()
    result = await coordinators[0].update_method()
    expected = ["one"] if selected_only else ["one", "two", "three"]
    assert [s.id for s in result["systems"]] == expected
    assert [call.args[0] for call in client.get_system_stats.call_args_list] == expected


@pytest.mark.parametrize(
    "data",
    [
        {CONF_SYSTEM_SELECTION: "selected", CONF_SYSTEM_IDS: []},
        {CONF_SYSTEM_SELECTION: "selected", CONF_SYSTEM_IDS: "one"},
        {CONF_SYSTEM_SELECTION: "invalid"},
    ],
)
def test_invalid_saved_selection_does_not_silently_monitor_all_systems(data):
    assert _filter_systems(_systems(), data) == []


def test_system_renames_and_disappearance_preserve_explicit_identity():
    data = {CONF_SYSTEM_SELECTION: "selected", CONF_SYSTEM_IDS: ["one"]}
    system = _systems()[0]
    system.name = "Renamed server"
    assert _filter_systems([system], data) == [system]
    assert _filter_systems([], data) == []
    assert _filter_systems([system], data) == [system]
    assert data[CONF_SYSTEM_IDS] == ["one"]
