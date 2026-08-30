"""UI configuration for one exact sdsctl App live-audio bridge."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import (
    SdsctlAppClient,
    SdsctlAuthenticationError,
    SdsctlCompatibilityError,
    SdsctlConnectionError,
    normalize_app_host,
    normalize_app_port,
    normalize_bridge_key,
)
from .const import (
    CONF_APP_HOST,
    CONF_APP_PORT,
    CONF_BRIDGE_KEY,
    DEFAULT_APP_PORT,
    DOMAIN,
)

_UNIQUE_ID = "sdsctl-live-scanner-audio"


class SdsctlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure the explicit internal App alias and private bridge key."""

    VERSION = 1

    @override
    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        normalized: dict[str, Any] | None = None
        if user_input is not None:
            try:
                normalized = {
                    CONF_APP_HOST: normalize_app_host(user_input[CONF_APP_HOST]),
                    CONF_APP_PORT: normalize_app_port(user_input[CONF_APP_PORT]),
                    CONF_BRIDGE_KEY: normalize_bridge_key(user_input[CONF_BRIDGE_KEY]),
                }
                client = SdsctlAppClient(
                    async_get_clientsession(self.hass),
                    normalized[CONF_APP_HOST],
                    normalized[CONF_APP_PORT],
                    normalized[CONF_BRIDGE_KEY],
                )
                await client.async_check_compatibility()
            except (KeyError, ValueError):
                errors["base"] = "invalid_input"
            except SdsctlAuthenticationError:
                errors["base"] = "invalid_auth"
            except SdsctlCompatibilityError:
                errors["base"] = "incompatible"
            except SdsctlConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(_UNIQUE_ID)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="sdsctl live scanner audio",
                    data=normalized,
                )

        defaults = normalized or user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_APP_HOST,
                        default=defaults.get(CONF_APP_HOST, "local-sds200"),
                    ): str,
                    vol.Required(
                        CONF_APP_PORT,
                        default=defaults.get(CONF_APP_PORT, DEFAULT_APP_PORT),
                    ): int,
                    vol.Required(CONF_BRIDGE_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> ConfigFlowResult:
        """Start an explicit bridge-key rotation flow."""

        del entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Validate and replace only the rejected private bridge key."""

        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                bridge_key = normalize_bridge_key(user_input[CONF_BRIDGE_KEY])
                client = SdsctlAppClient(
                    async_get_clientsession(self.hass),
                    entry.data[CONF_APP_HOST],
                    entry.data[CONF_APP_PORT],
                    bridge_key,
                )
                await client.async_check_compatibility()
            except (KeyError, ValueError, SdsctlAuthenticationError):
                errors["base"] = "invalid_auth"
            except SdsctlCompatibilityError:
                errors["base"] = "incompatible"
            except SdsctlConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={**entry.data, CONF_BRIDGE_KEY: bridge_key},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )
