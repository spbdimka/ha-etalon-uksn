from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UKSNClient, UKSNAuthError
from .const import (
    DOMAIN,
    CONF_PHONE,
    CONF_PASSWORD,
    CONF_PROVIDER_ID,
    CONF_DOMAIN_ID,
    CONF_BRAND_CODE,
    CONF_DEVICE_ID,
    CONF_SELECTED_ADDRESSES,
    DEFAULT_BRAND_CODE,
    DEFAULT_DOMAIN_ID,
    DEFAULT_PROVIDER_ID,
)

import logging
_LOGGER = logging.getLogger(__name__)

def _mk_device_id() -> str:
    # поле "i" выглядит как 32 hex
    return secrets.token_hex(16)


async def _fetch_addresses(hass: HomeAssistant, domain_id: int) -> list[dict[str, Any]]:
    session = async_get_clientsession(hass)
    client = UKSNClient(session=session)
    return await client.get_addresses(domain_id)


class UKSNConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._phone: str | None = None
        self._password: str | None = None
        self._provider_id: str | None = None
        self._domain_id: int = DEFAULT_DOMAIN_ID
        self._brand_code: str = DEFAULT_BRAND_CODE
        self._device_id: str = _mk_device_id()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input:
            self._phone = user_input[CONF_PHONE]
            self._password = user_input[CONF_PASSWORD]
            self._provider_id = str(user_input.get(CONF_PROVIDER_ID, DEFAULT_PROVIDER_ID))
            self._domain_id = int(user_input.get(CONF_DOMAIN_ID, DEFAULT_DOMAIN_ID))
            self._brand_code = user_input.get(CONF_BRAND_CODE, DEFAULT_BRAND_CODE).strip() or DEFAULT_BRAND_CODE

            session = async_get_clientsession(self.hass)
            client = UKSNClient(session=session)

            try:
                # стартуем сессию
                await client.get_temp_token()

                # логин: для кабинета пароль уходит в поле "password" этого эндпоинта
                await client.auth_login(
                    phone=self._phone or "",
                    password=self._password or "",
                    provider_id=str(self._provider_id),
                    device_id=self._device_id,
                    brand_code=self._brand_code,
                )

                return await self.async_step_select_addresses()

            except UKSNAuthError as err:
                _LOGGER.warning("Auth error: %s", err)
                errors["base"] = "invalid_auth"
            except Exception as err:
                _LOGGER.exception("Login failed: %s", err)
                errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(CONF_PHONE): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_PROVIDER_ID, default=DEFAULT_PROVIDER_ID): int,
                vol.Optional(CONF_DOMAIN_ID, default=DEFAULT_DOMAIN_ID): int,
                vol.Optional(CONF_BRAND_CODE, default=DEFAULT_BRAND_CODE): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_select_addresses(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        try:
            addresses = await _fetch_addresses(self.hass, self._domain_id)
        except Exception:
            addresses = []
            errors["base"] = "cannot_connect"

        options: dict[str, str] = {}
        for a in addresses:
            aid = a.get("address_id") or a.get("id")
            if aid is None:
                continue
            title = a.get("address_str") or a.get("address") or a.get("full_address") or f"Address {aid}"
            options[str(aid)] = str(title)

        if user_input:
            selected = user_input.get(CONF_SELECTED_ADDRESSES, [])
            title = f"UKSN {self._phone}"
            data = {
                CONF_PHONE: self._phone,
                CONF_PASSWORD: self._password,
                CONF_PROVIDER_ID: self._provider_id,
                CONF_DOMAIN_ID: self._domain_id,
                CONF_BRAND_CODE: self._brand_code,
                CONF_DEVICE_ID: self._device_id,
                CONF_SELECTED_ADDRESSES: selected,
            }
            return self.async_create_entry(title=title, data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_SELECTED_ADDRESSES): vol.All(
                    vol.Coerce(list),
                    [vol.In(list(options.keys()))],
                )
            }
        )

        description_placeholders = {"count": str(len(options))}
        return self.async_show_form(
            step_id="select_addresses",
            data_schema=schema,
            errors=errors,
            description_placeholders=description_placeholders,
        )