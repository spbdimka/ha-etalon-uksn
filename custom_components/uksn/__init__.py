from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UKSNClient
from .const import (
    CONF_AUTH_TOKEN,
    CONF_BRAND_CODE,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_VITE_APP_X,
    DEFAULT_HISTORY_MAX_MONTHS,
    DOMAIN,
    SERVICE_FETCH_INVOICE_HISTORY,
    SERVICE_REAUTH,
)
from .coordinator import UKSNCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "number", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    client = UKSNClient(session=session)

    client.phone = entry.data.get(CONF_PHONE)
    client.password = entry.data.get(CONF_PASSWORD)
    client.brand_code = entry.data.get(CONF_BRAND_CODE)

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
        entry_id=entry.entry_id,
        history_max_months=DEFAULT_HISTORY_MAX_MONTHS,
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "entry": entry,
    }

    if not hass.services.has_service(DOMAIN, SERVICE_REAUTH):
        async def _handle_reauth(call: ServiceCall) -> None:
            for data in hass.data.get(DOMAIN, {}).values():
                one_client: UKSNClient = data["client"]
                one_coord: UKSNCoordinator = data["coordinator"]
                one_entry: ConfigEntry = data["entry"]

                _LOGGER.info("Manual reauth requested for entry_id=%s", one_entry.entry_id)
                await one_client.auth_login(
                    one_client.phone or "",
                    one_client.password or "",
                    one_client.brand_code or "ETALON",
                )
                hass.config_entries.async_update_entry(
                    one_entry,
                    data={
                        **one_entry.data,
                        CONF_AUTH_TOKEN: one_client.auth_token,
                        CONF_VITE_APP_X: one_client.vite_app_x,
                    },
                )
                await one_coord.async_request_refresh()

        hass.services.async_register(DOMAIN, SERVICE_REAUTH, _handle_reauth)

    if not hass.services.has_service(DOMAIN, SERVICE_FETCH_INVOICE_HISTORY):
        async def _handle_fetch_invoice_history(call: ServiceCall) -> None:
            address_id = call.data.get("address_id")
            max_months = call.data.get("max_months")
            entry_id = call.data.get("entry_id")

            targets = []
            for eid, data in hass.data.get(DOMAIN, {}).items():
                if entry_id and eid != entry_id:
                    continue
                targets.append(data)

            for data in targets:
                coordinator: UKSNCoordinator = data["coordinator"]
                loaded = await coordinator.async_fetch_invoice_history(
                    address_id=str(address_id) if address_id is not None else None,
                    max_months=int(max_months) if max_months is not None else None,
                )
                _LOGGER.info(
                    "Invoice history fetch finished for entry_id=%s loaded=%s address_id=%s max_months=%s",
                    data["entry"].entry_id,
                    loaded,
                    address_id,
                    max_months,
                )
                await coordinator.async_request_refresh()

        hass.services.async_register(DOMAIN, SERVICE_FETCH_INVOICE_HISTORY, _handle_fetch_invoice_history)

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)

        if not hass.data.get(DOMAIN):
            if hass.services.has_service(DOMAIN, SERVICE_REAUTH):
                hass.services.async_remove(DOMAIN, SERVICE_REAUTH)
            if hass.services.has_service(DOMAIN, SERVICE_FETCH_INVOICE_HISTORY):
                hass.services.async_remove(DOMAIN, SERVICE_FETCH_INVOICE_HISTORY)

    return unload_ok