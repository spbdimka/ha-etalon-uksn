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
    CONF_PROVIDER_ID,
    CONF_DOMAIN_ID,
    CONF_BRAND_CODE,
    CONF_DEVICE_ID,
    CONF_SELECTED_ADDRESSES,
    DEFAULT_BRAND_CODE,
    DEFAULT_DOMAIN_ID,
)


def _mk_device_id() -> str:
    # в HAR поле "i" выглядит как 32 hex; делаем совместимо
    return secrets.token_hex(16)


async def _fetch_addresses(hass: HomeAssistant, domain_id: int) -> list[dict[str, Any]]:
    session = async_get_clientsession(hass)
    client = UKSNClient(session=session)
    return await client.get_addresses(domain_id)


class UKSNConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._phone: str | None = None
        self._provider_id: str | None = None
        self._domain_id: int = DEFAULT_DOMAIN_ID
        self._brand_code: str = DEFAULT_BRAND_CODE
        self._device_id: str = _mk_device_id()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input:
            self._phone = user_input[CONF_PHONE]
            self._provider_id = user_input[CONF_PROVIDER_ID]
            self._domain_id = int(user_input.get(CONF_DOMAIN_ID, DEFAULT_DOMAIN_ID))
            self._brand_code = user_input.get(CONF_BRAND_CODE, DEFAULT_BRAND_CODE).strip() or DEFAULT_BRAND_CODE

            # инициируем сессию и отправку кода
            session = async_get_clientsession(self.hass)
            client = UKSNClient(session=session)

            try:
                await client.get_temp_token()
                await client.auth_phone(self._phone, self._provider_id, self._device_id)
                return await self.async_step_code()
            except Exception:
                errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(CONF_PHONE): str,
                vol.Required(CONF_PROVIDER_ID): str,
                vol.Optional(CONF_DOMAIN_ID, default=DEFAULT_DOMAIN_ID): int,
                vol.Optional(CONF_BRAND_CODE, default=DEFAULT_BRAND_CODE): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_code(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input:
            code = user_input["code"]

            session = async_get_clientsession(self.hass)
            client = UKSNClient(session=session)

            try:
                # важно: используем те же phone/brand_code/device_id
                await client.get_temp_token()
                await client.auth_confirm(self._phone or "", code, self._brand_code)

                return await self.async_step_select_addresses()

            except UKSNAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"

        schema = vol.Schema({vol.Required("code"): str})
        return self.async_show_form(step_id="code", data_schema=schema, errors=errors)

    async def async_step_select_addresses(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        try:
            addresses = await _fetch_addresses(self.hass, self._domain_id)
        except Exception:
            addresses = []
            errors["base"] = "cannot_connect"

        # строим список опций: address_id -> человекочитаемое имя
        options: dict[str, str] = {}
        for a in addresses:
            aid = a.get("address_id") or a.get("id")
            if aid is None:
                continue
            title = (
                a.get("address_str")
                or a.get("address")
                or a.get("full_address")
                or f"Address {aid}"
            )
            options[str(aid)] = str(title)

        if user_input:
            selected = user_input.get(CONF_SELECTED_ADDRESSES, [])
            title = f"UKSN {self._phone}"
            data = {
                CONF_PHONE: self._phone,
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