from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UKSNClient, UKSNAuthError
from .const import (
    DOMAIN,
    CONF_PHONE,
    CONF_PASSWORD,
    CONF_PROVIDER_ID,
    CONF_BRAND_CODE,
    CONF_SELECTED_ADDRESSES,
    CONF_AUTH_TOKEN,
    CONF_VITE_APP_X,
    DEFAULT_PROVIDER_ID,
    DEFAULT_BRAND_CODE,
)

_LOGGER = logging.getLogger(__name__)


class UKSNConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._phone: str | None = None
        self._password: str | None = None
        self._provider_id: str | None = None
        self._brand_code: str = DEFAULT_BRAND_CODE
        self._client: UKSNClient | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        schema = vol.Schema(
            {
                vol.Required(CONF_PHONE): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_PROVIDER_ID, default=DEFAULT_PROVIDER_ID): int,
                vol.Optional(CONF_BRAND_CODE, default=DEFAULT_BRAND_CODE): str,
            }
        )

        if user_input:
            self._phone = user_input[CONF_PHONE]
            self._password = user_input[CONF_PASSWORD]
            self._provider_id = str(user_input.get(CONF_PROVIDER_ID, DEFAULT_PROVIDER_ID))
            self._brand_code = (user_input.get(CONF_BRAND_CODE, DEFAULT_BRAND_CODE) or DEFAULT_BRAND_CODE).strip()

            session = async_get_clientsession(self.hass)
            client = UKSNClient(session=session)
            client.phone = self._phone
            client.password = self._password
            client.brand_code = self._brand_code

            try:
                _LOGGER.debug("STEP user: login start phone=%s brand=%s", self._phone, self._brand_code)
                resp = await client.auth_login(self._phone, self._password, self._brand_code)
                _LOGGER.debug("STEP user: auth_login resp=%s", resp)
                _LOGGER.debug(
                    "STEP user: auth_token=%s vite_x=%s",
                    "present" if client.auth_token else "missing",
                    "present" if client.vite_app_x else "missing",
                )

                if not client.auth_token:
                    errors["base"] = "invalid_auth"
                    return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

                self._client = client
                return await self.async_step_select_addresses()

            except UKSNAuthError as err:
                _LOGGER.warning("STEP user: unauthorized: %s", err)
                errors["base"] = "invalid_auth"
            except Exception as err:
                _LOGGER.exception("STEP user: login failed: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_select_addresses(self, user_input: dict[str, Any] | None = None):
        client = self._client
        if client is None:
            return await self.async_step_user()

        errors: dict[str, str] = {}

        try:
            _LOGGER.debug("STEP addresses: fetch start")
            addresses = await client.get_addresses()
            _LOGGER.debug("STEP addresses: fetched %d", len(addresses))
        except UKSNAuthError as err:
            _LOGGER.warning("STEP addresses: unauthorized (back to user): %s", err)
            return await self.async_step_user()
        except Exception as err:
            _LOGGER.exception("STEP addresses: failed: %s", err)
            addresses = []
            errors["base"] = "cannot_connect"

        options: dict[str, str] = {}
        for a in addresses:
            aid = a.get("address_id") or a.get("id")
            if aid is None:
                continue
            title = a.get("full_name") or a.get("_address") or a.get("address_str") or f"Address {aid}"
            options[str(aid)] = str(title)

        if not options and not errors.get("base"):
            errors["base"] = "no_addresses"

        if user_input:
            selected_raw = user_input.get(CONF_SELECTED_ADDRESSES, {})

            # multi_select может вернуть dict или list — поддерживаем оба
            if isinstance(selected_raw, dict):
                selected_keys = [k for k, v in selected_raw.items() if v]
            elif isinstance(selected_raw, list):
                selected_keys = selected_raw
            else:
                selected_keys = []

            selected = [int(k) for k in selected_keys]

            title = f"UKSN {self._phone}"
            data = {
                CONF_PHONE: self._phone,
                CONF_PASSWORD: self._password,
                CONF_PROVIDER_ID: self._provider_id,
                CONF_BRAND_CODE: self._brand_code,
                CONF_SELECTED_ADDRESSES: selected,
                CONF_AUTH_TOKEN: client.auth_token,
                CONF_VITE_APP_X: client.vite_app_x,
            }
            return self.async_create_entry(title=title, data=data)

        schema = vol.Schema({vol.Required(CONF_SELECTED_ADDRESSES): cv.multi_select(options)})
        description_placeholders = {"count": str(len(options))}
        return self.async_show_form(
            step_id="select_addresses",
            data_schema=schema,
            errors=errors,
            description_placeholders=description_placeholders,
        )