from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import UKSNCoordinator
from .entity_base import device_info_for_address


def _counter_name(counter: dict[str, Any]) -> str:
    svc = (counter.get("service_str") or "").strip()
    fn = (counter.get("factory_num") or "").strip()
    if svc and fn:
        return f"{svc} ({fn})"
    return svc or fn or f"Counter {counter.get('counter_id')}"


def _counter_value(counter: dict[str, Any]) -> Any:
    v = counter.get("current_val")
    if v is None:
        v = counter.get("last_val")
    return v


def _first_gku_bill(bills: list[dict[str, Any]]) -> dict[str, Any] | None:
    for b in bills or []:
        if str(b.get("_typeId")) == "1":  # ЖКУ
            return b
    return bills[0] if bills else None


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

    @property
    def device_info(self):
        return device_info_for_address(self.coordinator, self._key.address_id)

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
    def name(self) -> str:
        c = self._find_counter()
        return _counter_name(c) if c else super().name

    @property
    def native_value(self) -> Any:
        c = self._find_counter()
        if not c:
            return None
        return _counter_value(c)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self._find_counter() or {}
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


class _UKSNBillBase(CoordinatorEntity[UKSNCoordinator], SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "RUB"
    _attr_has_entity_name = True

    def __init__(self, coordinator: UKSNCoordinator, address_id: str) -> None:
        super().__init__(coordinator)
        self.address_id = address_id

    @property
    def device_info(self):
        return device_info_for_address(self.coordinator, self.address_id)

    def _bill(self) -> dict[str, Any] | None:
        bills = self.coordinator.data.get("bills_by_address", {}).get(self.address_id, [])
        return _first_gku_bill(bills)


class UKSNMonthlyChargeSensor(_UKSNBillBase):
    def __init__(self, coordinator: UKSNCoordinator, address_id: str) -> None:
        super().__init__(coordinator, address_id)
        self._attr_unique_id = f"uksn_bill_calc_{address_id}"
        self._attr_name = "Счёт за месяц"

    @property
    def native_value(self):
        b = self._bill() or {}
        v = b.get("calc_amount")
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    @property
    def extra_state_attributes(self):
        b = self._bill() or {}
        return {
            "bill_id": b.get("bill_id"),
            "month_str": b.get("month_str"),
            "bill_pay_date_str": b.get("bill_pay_date_str"),
            "account_num": b.get("account_num"),
            "aba_id": b.get("aba_id"),
            "address": b.get("_address"),
        }


class UKSNDebtSensor(_UKSNBillBase):
    def __init__(self, coordinator: UKSNCoordinator, address_id: str) -> None:
        super().__init__(coordinator, address_id)
        self._attr_unique_id = f"uksn_bill_total_{address_id}"
        self._attr_name = "Задолженность"

    @property
    def native_value(self):
        b = self._bill() or {}
        v = b.get("total_amount")
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    @property
    def extra_state_attributes(self):
        b = self._bill() or {}
        return {
            "bill_id": b.get("bill_id"),
            "month_str": b.get("month_str"),
            "start_amount": b.get("start_amount"),
            "pay_amount": b.get("pay_amount"),
            "calc_amount": b.get("calc_amount"),
            "account_num": b.get("account_num"),
            "aba_id": b.get("aba_id"),
            "address": b.get("_address"),
        }


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data["uksn"][entry.entry_id]
    coordinator: UKSNCoordinator = data["coordinator"]

    entities = []
    # bill sensors per address
    for address_id in coordinator.data.get("counters_by_address", {}).keys():
        entities.append(UKSNMonthlyChargeSensor(coordinator, str(address_id)))
        entities.append(UKSNDebtSensor(coordinator, str(address_id)))

    # counter sensors
    counters_by_address = coordinator.data.get("counters_by_address", {})
    for address_id, counters in counters_by_address.items():
        for c in counters:
            cid = str(c.get("counter_id"))
            if not cid:
                continue
            entities.append(UKSNCounterSensor(coordinator, str(address_id), cid))

    async_add_entities(entities)