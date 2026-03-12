from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo


def _address_name_from_coordinator(coordinator, address_id: str) -> str:
    data = coordinator.data or {}
    addr = data.get("addresses_by_id", {}).get(str(address_id), {})
    return (
        addr.get("full_name")
        or addr.get("_address")
        or f"Адрес {address_id}"
    )


def _address_manufacturer_from_coordinator(coordinator, address_id: str) -> str:
    data = coordinator.data or {}
    addr = data.get("addresses_by_id", {}).get(str(address_id), {})
    return addr.get("cnt_name") or "cab.uksn.ru"


def device_info_for_address(coordinator, address_id: str) -> DeviceInfo:
    name = _address_name_from_coordinator(coordinator, address_id)
    manufacturer = _address_manufacturer_from_coordinator(coordinator, address_id)

    return DeviceInfo(
        identifiers={("uksn", f"address_{address_id}")},
        name=name,
        manufacturer=manufacturer,
        model="UKSN Address",
        configuration_url="https://cab.uksn.ru",
    )


def device_info_for_invoice_group(coordinator, address_id: str, group: str) -> DeviceInfo:
    name = _address_name_from_coordinator(coordinator, address_id)
    manufacturer = _address_manufacturer_from_coordinator(coordinator, address_id)

    suffix_map = {
        "usage": "Показания квитанций",
        "tariff": "Тарифы квитанций",
        "total": "Итоги квитанций",
    }

    suffix = suffix_map.get(group, group)

    return DeviceInfo(
        identifiers={("uksn", f"address_{address_id}_invoice_{group}")},
        name=f"{name} — {suffix}",
        manufacturer=manufacturer,
        model="UKSN Invoice Group",
        configuration_url="https://cab.uksn.ru",
        via_device=("uksn", f"address_{address_id}"),
    )