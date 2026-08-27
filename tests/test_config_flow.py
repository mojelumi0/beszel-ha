"""Tests for the Beszel config and reauthentication flows."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries, data_entry_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.beszel_api.api import BeszelInvalidAuth
from custom_components.beszel_api.config_flow import InvalidUrl, _validate_url
from custom_components.beszel_api.const import (
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
)

ENTRY_DATA = {
    CONF_URL: "https://beszel.example",
    CONF_USERNAME: "user@example.test",
    CONF_PASSWORD: "old-password",
    CONF_UPDATE_INTERVAL: 120,
    CONF_VERIFY_SSL: True,
}


@pytest.mark.parametrize(
    "url",
    [
        "beszel.example",
        "ftp://beszel.example",
        "https://user:pass@beszel.example",
        "https://beszel.example?token=secret",
        "https://beszel.example#fragment",
    ],
)
def test_invalid_urls_are_rejected(url: str) -> None:
    """Only clean HTTP(S) Beszel base URLs should be accepted."""
    with pytest.raises(InvalidUrl):
        _validate_url(url)


async def test_reauth_updates_existing_entry(hass) -> None:
    """Successful reauth updates credentials without creating another entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="beszel.example",
        data=ENTRY_DATA,
        entry_id="test-entry",
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.beszel_api.config_flow.BeszelApiClient.get_systems",
            return_value=[],
        ),
        patch("custom_components.beszel_api.config_flow.BeszelApiClient.close"),
        patch.object(
            hass.config_entries,
            "async_schedule_reload",
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "new@example.test",
                CONF_PASSWORD: "new-password",
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_USERNAME] == "new@example.test"
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reauth_rejects_invalid_credentials(hass) -> None:
    """Invalid replacement credentials keep the reauth form open."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="beszel.example",
        data=ENTRY_DATA,
        entry_id="test-entry",
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.beszel_api.config_flow.BeszelApiClient.get_systems",
            side_effect=BeszelInvalidAuth,
        ),
        patch("custom_components.beszel_api.config_flow.BeszelApiClient.close"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "user@example.test",
                CONF_PASSWORD: "wrong",
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
