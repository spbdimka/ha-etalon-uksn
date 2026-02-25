from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import UKSNCoordinator


def _counter_name(counter: dict[str, Any]) -> str:
    # service_str + factory_num обычно достаточно читабельно
    svc = (counter.get("service_str") or "").strip()
    fn = (counter.get("factory_num") or "").strip()
    if svc and fn:
        return f"{svc} ({fn})"
    return svc or fn or f"Counter {counter.get('counter_id')}"


def _counter_value(counter: dict[str, Any]) -> Any:
    # берем current_val, иначе last_val
    v = counter.get("current_val")
    if v is None:
        v = counter.get("last_val")
    return v


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: UKSNCoordinator = data["coordinator"]

    entities: list[SensorEntity] = []

    # создаём сущности по данным первого refresh
    counters_by_address = coordinator.data.get("counters_by_address", {})
    for address_id, counters in counters_by_address.items():
        for c in counters:
            counter_id = str(c.get("counter_id"))
            if not counter_id:
                continue
            entities.append(UKSNCounterSensor(coordinator, address_id=str(address_id), counter_id=counter_id))

    async_add_entities(entities)


@dataclass(frozen=True)
class _Key:
    address_id: str
    counter_id: str


class UKSNCounterSensor(CoordinatorEntity[UKSNCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: UKSNCoordinator, address_id: str, counter_id: str) -> None:
        super().__init__(coordinator)
        self._key = _Key(address_id=address_id, counter_id=counter_id)
        self._attr_unique_id = f"uksn_counter_{counter_id}"
        self._attr_name = f"Счётчик {counter_id}"

    def _find_counter(self) -> dict[str, Any] | None:
        counters = self.coordinator.data.get("counters_by_address", {}).get(self._key.address_id, [])
        for c in counters:
            if str(c.get("counter_id")) == self._key.counter_id:
                return c
        return None

    @property
    def available(self) -> bool:
        return super().available and self._find_counter() is not None

    @property
    def native_value(self) -> Any:
        c = self._find_counter()
        if not c:
            return None
        return _counter_value(c)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self._find_counter() or {}
        # сюда уже складываем “паспортные” поля из списка /counter/{address}
        return {
            "address_id": self._key.address_id,
            "counter_id": self._key.counter_id,
            "service_str": c.get("service_str"),
            "factory_num": c.get("factory_num"),
            "verify_date": c.get("verify_date"),
            "account_num": c.get("account_num"),
            "aba_id": c.get("aba_id"),
            "is_take": c.get("is_take"),
            "is_less_val_counter": c.get("is_less_val_counter"),
            "current_val_date": c.get("current_val_date"),
            "last_val": c.get("last_val"),
            "last_val_date": c.get("last_val_date"),
        }

    @property
    def name(self) -> str:
        c = self._find_counter()
        if c:
            return _counter_name(c)
        return super().name