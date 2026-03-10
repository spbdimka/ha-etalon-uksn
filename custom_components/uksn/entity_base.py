from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import UKSNCoordinator


def device_info_for_address(coordinator: UKSNCoordinator, address_id: str) -> DeviceInfo:
    addr = (coordinator.data or {}).get("addresses_by_id", {}).get(address_id, {})
    name = addr.get("full_name") or addr.get("_address") or f"Адрес {address_id}"
    manufacturer = addr.get("cnt_name") or addr.get("partner_name") or "UKSN"
    return DeviceInfo(
        identifiers={(DOMAIN, address_id)},
        name=name,
        manufacturer=manufacturer,
        model="cab.uksn.ru",
    )