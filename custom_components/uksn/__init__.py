from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UKSNClient
from .const import (
    DOMAIN,
    CONF_PHONE,
    CONF_PASSWORD,
    CONF_BRAND_CODE,
    CONF_AUTH_TOKEN,
    CONF_VITE_APP_X,
    SERVICE_REAUTH,
)
from .coordinator import UKSNCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "number", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    client = UKSNClient(session=session)

    # creds for auto reauth
    client.phone = entry.data.get(CONF_PHONE)
    client.password = entry.data.get(CONF_PASSWORD)
    client.brand_code = entry.data.get(CONF_BRAND_CODE)

    # restore saved auth token / vite
    auth_token = entry.data.get(CONF_AUTH_TOKEN)
    if auth_token:
        client.auth_token = auth_token

    vite_x = entry.data.get(CONF_VITE_APP_X)
    if vite_x:
        client.vite_app_x = vite_x

    selected = entry.data.get("selected_addresses", [])

    coordinator = UKSNCoordinator(
        hass=hass,
        client=client,
        selected_addresses=[int(x) for x in selected],
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "entry": entry,
    }

    async def _handle_reauth(call: ServiceCall) -> None:
        _LOGGER.info("Manual reauth requested")
        await client.auth_login(client.phone or "", client.password or "", client.brand_code or "ETALON")
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_AUTH_TOKEN: client.auth_token, CONF_VITE_APP_X: client.vite_app_x})
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REAUTH, _handle_reauth)

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok