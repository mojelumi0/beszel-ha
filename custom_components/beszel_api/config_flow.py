"""Config flow for the Beszel API integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    BeszelApiClient,
    BeszelApiError,
    BeszelCannotConnect,
    BeszelInvalidAuth,
)
from .const import (
    CONF_PASSWORD,
    CONF_SYSTEM_IDS,
    CONF_SYSTEM_SELECTION,
    CONF_UPDATE_INTERVAL,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
    LOGGER,
    SYSTEM_SELECTION_ALL,
    SYSTEM_SELECTION_SELECTED,
)

MIN_UPDATE_INTERVAL = 10
MAX_UPDATE_INTERVAL = 3600


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication data."""


class InvalidUrl(HomeAssistantError):
    """Error to indicate an invalid URL format."""


class InvalidUpdateInterval(HomeAssistantError):
    """Error to indicate an invalid update interval range."""


class InvalidSystemSelection(HomeAssistantError):
    """Error to indicate invalid system IDs or a selection mode."""


class NoSystemsSelected(HomeAssistantError):
    """Error to indicate an empty explicit selection."""


def _validate_url(url: str) -> str:
    """Validate and normalize a Beszel base URL."""
    normalized_url = url.strip().rstrip("/")
    try:
        parsed = urlsplit(normalized_url)
        _ = parsed.port
    except ValueError as err:
        raise InvalidUrl from err

    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise InvalidUrl
    return normalized_url


def _validate_update_interval(update_interval: int) -> int:
    """Validate update interval bounds."""
    if not MIN_UPDATE_INTERVAL <= update_interval <= MAX_UPDATE_INTERVAL:
        raise InvalidUpdateInterval
    return update_interval


async def _async_validate_input(
    hass, user_input: Mapping[str, Any]
) -> tuple[dict[str, Any], list[Any]]:
    """Validate config flow input and test authentication and connectivity."""
    validated_data = dict(user_input)
    validated_data[CONF_URL] = _validate_url(str(validated_data[CONF_URL]))
    try:
        update_interval = int(validated_data.get(CONF_UPDATE_INTERVAL, 120))
    except (TypeError, ValueError) as err:
        raise InvalidUpdateInterval from err
    validated_data[CONF_UPDATE_INTERVAL] = _validate_update_interval(update_interval)

    username = str(validated_data.get(CONF_USERNAME, "")).strip()
    password = str(validated_data.get(CONF_PASSWORD, ""))
    if not username or not password:
        raise InvalidAuth
    validated_data[CONF_USERNAME] = username
    validated_data[CONF_PASSWORD] = password

    client = BeszelApiClient(
        validated_data[CONF_URL],
        username,
        password,
        bool(validated_data.get(CONF_VERIFY_SSL, True)),
    )

    try:
        systems = await hass.async_add_executor_job(client.get_systems)
    except BeszelInvalidAuth as err:
        raise InvalidAuth from err
    except (BeszelCannotConnect, BeszelApiError) as err:
        raise CannotConnect from err
    finally:
        await hass.async_add_executor_job(client.close)

    return validated_data, systems


def _system_choices(systems: list[Any], defaults: Mapping[str, Any]) -> dict[str, str]:
    """Label systems by name, retaining previously selected IDs during outages."""
    choices = {}
    for system in systems:
        system_id = getattr(system, "id", None)
        if isinstance(system_id, str) and system_id:
            name = getattr(system, "name", None)
            choices[system_id] = f"{name} ({system_id})" if name else system_id
    previous = defaults.get(CONF_SYSTEM_IDS, [])
    if isinstance(previous, list):
        for system_id in previous:
            if isinstance(system_id, str) and system_id:
                choices.setdefault(system_id, system_id)
    return choices


def _system_schema(choices: dict[str, str], defaults: Mapping[str, Any]) -> vol.Schema:
    """Build translated selection controls shared by setup and options."""
    return vol.Schema(
        {
            vol.Required(
                CONF_SYSTEM_SELECTION,
                default=defaults.get(CONF_SYSTEM_SELECTION, SYSTEM_SELECTION_ALL),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[SYSTEM_SELECTION_ALL, SYSTEM_SELECTION_SELECTED],
                    translation_key="system_selection",
                )
            ),
            vol.Optional(
                CONF_SYSTEM_IDS, default=defaults.get(CONF_SYSTEM_IDS, [])
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        {"value": key, "label": label} for key, label in choices.items()
                    ],
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
        }
    )


def _validated_selection(user_input: Mapping[str, Any], choices: dict[str, str]):
    """Reject invalid selections and keep all-mode independent of old IDs."""
    mode = user_input.get(CONF_SYSTEM_SELECTION)
    if mode == SYSTEM_SELECTION_ALL:
        return {CONF_SYSTEM_SELECTION: mode, CONF_SYSTEM_IDS: []}
    if mode != SYSTEM_SELECTION_SELECTED:
        raise InvalidSystemSelection
    selected = user_input.get(CONF_SYSTEM_IDS, [])
    if not isinstance(selected, list) or any(
        not isinstance(value, str) or value not in choices for value in selected
    ):
        raise InvalidSystemSelection
    if not selected:
        raise NoSystemsSelected
    return {CONF_SYSTEM_SELECTION: mode, CONF_SYSTEM_IDS: list(dict.fromkeys(selected))}


def _build_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build the schema shared by setup and options flows."""
    data = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=data.get(CONF_URL, "")): str,
            vol.Required(CONF_USERNAME, default=data.get(CONF_USERNAME, "")): str,
            vol.Required(
                CONF_PASSWORD, default=data.get(CONF_PASSWORD, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=data.get(CONF_UPDATE_INTERVAL, 120),
            ): int,
            vol.Optional(
                CONF_VERIFY_SSL,
                default=data.get(CONF_VERIFY_SSL, True),
            ): bool,
        }
    )


def _build_reauth_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Build the credentials-only schema used for reauthentication."""
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


def _flow_error(err: Exception) -> str:
    """Map validation errors to translation keys."""
    if isinstance(err, InvalidUrl):
        return "invalid_url"
    if isinstance(err, InvalidUpdateInterval):
        return "invalid_update_interval"
    if isinstance(err, InvalidAuth):
        return "invalid_auth"
    if isinstance(err, CannotConnect):
        return "cannot_connect"
    if isinstance(err, InvalidSystemSelection):
        return "invalid_system_selection"
    if isinstance(err, NoSystemsSelected):
        return "no_systems_selected"
    LOGGER.exception("Unexpected error while validating Beszel configuration")
    return "unknown"


class BeszelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Beszel config flows."""

    VERSION = 3

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle initial setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                validated_data, systems = await _async_validate_input(
                    self.hass, user_input
                )
            except Exception as err:  # noqa: BLE001
                errors["base"] = _flow_error(err)
            else:
                self._async_abort_entries_match({CONF_URL: validated_data[CONF_URL]})
                self._validated_data = validated_data
                self._system_choices = _system_choices(systems, {})
                return await self.async_step_systems()

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(user_input),
            errors=errors,
        )

    async def async_step_systems(self, user_input: dict[str, Any] | None = None):
        """Choose all visible systems or a stable explicit set."""
        errors = {}
        if user_input is not None:
            try:
                selection = _validated_selection(user_input, self._system_choices)
            except (InvalidSystemSelection, NoSystemsSelected) as err:
                errors["base"] = _flow_error(err)
            else:
                data = {**self._validated_data, **selection}
                title = urlsplit(data[CONF_URL]).hostname or "Beszel API"
                return self.async_create_entry(title=title, data=data)
        return self.async_show_form(
            step_id="systems",
            data_schema=_system_schema(self._system_choices, user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Start reauthentication for an existing entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Validate replacement credentials and reload the existing entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            merged_data = {**entry.data, **user_input}
            try:
                validated_data, _systems = await _async_validate_input(
                    self.hass, merged_data
                )
            except Exception as err:  # noqa: BLE001
                errors["base"] = _flow_error(err)
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: validated_data[CONF_USERNAME],
                        CONF_PASSWORD: validated_data[CONF_PASSWORD],
                    },
                )

        defaults = {**entry.data, **(user_input or {})}
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_build_reauth_schema(defaults),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Return the options flow handler."""
        return BeszelOptionsFlow()


class BeszelOptionsFlow(config_entries.OptionsFlow):
    """Handle changes to Beszel connection settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Validate and save changed settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            merged_data = {**self.config_entry.data, **user_input}
            try:
                validated_data, systems = await _async_validate_input(
                    self.hass, merged_data
                )
            except Exception as err:  # noqa: BLE001
                errors["base"] = _flow_error(err)
            else:
                self._validated_data = validated_data
                self._system_choices = _system_choices(systems, self.config_entry.data)
                return await self.async_step_systems()

        defaults = {**self.config_entry.data, **(user_input or {})}
        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(defaults),
            errors=errors,
        )

    async def async_step_systems(self, user_input: dict[str, Any] | None = None):
        """Apply a selection only after both options steps have succeeded."""
        errors = {}
        if user_input is not None:
            try:
                selection = _validated_selection(user_input, self._system_choices)
            except (InvalidSystemSelection, NoSystemsSelected) as err:
                errors["base"] = _flow_error(err)
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data={**self._validated_data, **selection}
                )
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="systems",
            data_schema=_system_schema(
                self._system_choices, user_input or self.config_entry.data
            ),
            errors=errors,
        )
