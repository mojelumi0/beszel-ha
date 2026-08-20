from __future__ import annotations

from typing import Any, Mapping, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .api import BeszelApiClient, BeszelCannotConnect, BeszelInvalidAuth
from .const import (
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
)

MIN_UPDATE_INTERVAL = 10
MAX_UPDATE_INTERVAL = 3600


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class InvalidUrl(HomeAssistantError):
    """Error to indicate an invalid URL format."""


class InvalidUpdateInterval(HomeAssistantError):
    """Error to indicate invalid update interval range."""


def _validate_url(url: str) -> str:
    """Validate URL format."""
    normalized_url = url.strip()
    if not normalized_url.startswith(("http://", "https://")):
        raise InvalidUrl
    return normalized_url


def _validate_update_interval(update_interval: int) -> int:
    """Validate update interval bounds."""
    if update_interval < MIN_UPDATE_INTERVAL or update_interval > MAX_UPDATE_INTERVAL:
        raise InvalidUpdateInterval
    return update_interval


async def _async_validate_input(hass, user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Validate config flow input and test connectivity."""
    validated_data = dict(user_input)
    validated_data[CONF_URL] = _validate_url(str(validated_data[CONF_URL]))
    validated_data[CONF_UPDATE_INTERVAL] = _validate_update_interval(
        int(validated_data.get(CONF_UPDATE_INTERVAL, 120))
    )

    client = BeszelApiClient(
        validated_data[CONF_URL],
        validated_data.get(CONF_USERNAME),
        validated_data.get(CONF_PASSWORD),
        bool(validated_data.get(CONF_VERIFY_SSL, True)),
    )

    try:
        await hass.async_add_executor_job(client.get_systems)
    except BeszelInvalidAuth as err:
        raise InvalidAuth from err
    except BeszelCannotConnect as err:
        raise CannotConnect from err

    return validated_data


def _build_schema(defaults: Optional[Mapping[str, Any]] = None) -> vol.Schema:
    """Build schema for user/options flows."""
    data = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=data.get(CONF_URL, "")): str,
            vol.Optional(CONF_USERNAME, default=data.get(CONF_USERNAME, "")): str,
            vol.Optional(CONF_PASSWORD, default=data.get(CONF_PASSWORD, "")): str,
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=data.get(CONF_UPDATE_INTERVAL, 120),
            ): int,
            vol.Optional(CONF_VERIFY_SSL, default=data.get(CONF_VERIFY_SSL, True)): bool,
        }
    )


class BeszelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 3

    async def async_step_user(self, user_input: Optional[dict[str, Any]] = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                validated_data = await _async_validate_input(self.hass, user_input)
            except InvalidUrl:
                errors["base"] = "invalid_url"
            except InvalidUpdateInterval:
                errors["base"] = "invalid_update_interval"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title="Beszel API", data=validated_data)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return BeszelOptionsFlow(config_entry)


class BeszelOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__()
        self._config_entry = config_entry

    async def async_step_init(self, user_input: Optional[dict[str, Any]] = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                merged_data = {**self._config_entry.data, **user_input}
                validated_data = await _async_validate_input(self.hass, merged_data)
            except InvalidUrl:
                errors["base"] = "invalid_url"
            except InvalidUpdateInterval:
                errors["base"] = "invalid_update_interval"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={**self._config_entry.data, **validated_data},
                )
                await self.hass.config_entries.async_reload(self._config_entry.entry_id)
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema({**self._config_entry.data, **(user_input or {})}),
            errors=errors,
        )