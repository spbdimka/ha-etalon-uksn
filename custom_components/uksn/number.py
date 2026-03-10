from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import UKSNCoordinator
from .entity_base import device_info_for_address
from .sensor import _counter_name


def _round3(v: float) -> float:
    return float(Decimal(str(v)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


class UKSNReadingInput(CoordinatorEntity[UKSNCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_mode = "box"
    _attr_native_step = 0.001
    _attr_native_min_value = 0.0

    def __init__(self, coordinator: UKSNCoordinator, address_id: str, counter_id: str, name: str) -> None:
        super().__init__(coordinator)
        self.address_id = address_id
        self.counter_id = counter_id
        self._attr_unique_id = f"uksn_reading_input_{counter_id}"
        self._attr_name = f"{name}: ввод"
        self._value: float | None = None

    @property
    def device_info(self):
        return device_info_for_address(self.coordinator, self.address_id)

    @property
    def native_value(self) -> float | None:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = _round3(value)
        self.async_write_ha_state()


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data["uksn"][entry.entry_id]
    coordinator: UKSNCoordinator = data["coordinator"]

    entities = []
    for address_id, counters in coordinator.data.get("counters_by_address", {}).items():
        for c in counters:
            cid = str(c.get("counter_id"))
            if not cid:
                continue
            name = _counter_name(c)
            entities.append(UKSNReadingInput(coordinator, str(address_id), cid, name))

    async_add_entities(entities)