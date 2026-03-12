from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import UKSNCoordinator
from .entity_base import device_info_for_address, device_info_for_invoice_group

_LOGGER = logging.getLogger(__name__)


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
        if str(b.get("_typeId")) == "1":
            return b
    return bills[0] if bills else None


def _history_months_sorted(coordinator: UKSNCoordinator, address_id: str) -> list[tuple[str, dict[str, Any]]]:
    return coordinator.get_history_months_sorted(address_id)


def _history_attrs(coordinator: UKSNCoordinator, address_id: str) -> dict[str, Any]:
    items = _history_months_sorted(coordinator, address_id)
    history = (coordinator.data or {}).get("invoice_history_by_address", {}).get(str(address_id), {})

    if not items:
        return {
            "invoice_history_count": 0,
            "invoice_history_months": [],
            "aba_id_history": history.get("aba_id"),
        }

    latest_key, latest = items[0]

    preview: list[dict[str, Any]] = []
    for key, item in items[:6]:
        preview.append(
            {
                "month_key": key,
                "total_to_pay": item.get("total_to_pay"),
            }
        )

    return {
        "invoice_history_count": len(items),
        "invoice_history_last_month": latest_key,
        "invoice_history_last_total_to_pay": latest.get("total_to_pay"),
        "invoice_history_months": [k for k, _ in items],
        "invoice_history_preview": preview,
        "aba_id_history": history.get("aba_id"),
    }


@dataclass(frozen=True)
class _CounterKey:
    address_id: str
    counter_id: str


@dataclass(frozen=True)
class _ServiceKey:
    address_id: str
    service_key: str
    metric: str


class UKSNCounterSensor(CoordinatorEntity[UKSNCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: UKSNCoordinator, address_id: str, counter_id: str) -> None:
        super().__init__(coordinator)
        self._key = _CounterKey(address_id=address_id, counter_id=counter_id)
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
            "counter_id": c.get("counter_id"),
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

    def _history_attrs(self) -> dict[str, Any]:
        return _history_attrs(self.coordinator, self.address_id)


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
            **self._history_attrs(),
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
            **self._history_attrs(),
        }


class UKSNServiceSensor(CoordinatorEntity[UKSNCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: UKSNCoordinator, address_id: str, service_key: str, metric: str) -> None:
        super().__init__(coordinator)
        self._key = _ServiceKey(address_id=address_id, service_key=service_key, metric=metric)
        self._attr_unique_id = f"uksn_invoice_{address_id}_{service_key}_{metric}"
        self._attr_name = self._build_name()

        _LOGGER.debug(
            "Create UKSNServiceSensor unique_id=%s address_id=%s service_key=%s metric=%s",
            self._attr_unique_id,
            address_id,
            service_key,
            metric,
        )

    def _build_name(self) -> str:
        return self._key.service_key

    @property
    def device_info(self):
        return device_info_for_invoice_group(self.coordinator, self._key.address_id, self._key.metric)

    def _record(self) -> dict[str, Any] | None:
        return self.coordinator.get_latest_service_record(self._key.address_id, self._key.service_key)

    @property
    def available(self) -> bool:
        return super().available and self._record() is not None

    @property
    def name(self) -> str:
        rec = self._record()
        if not rec:
            return self._attr_name

        return rec.get("service_raw") or rec.get("service") or self._key.service_key

    @property
    def native_unit_of_measurement(self) -> str | None:
        rec = self._record()
        if not rec:
            return None

        unit = rec.get("unit")

        if self._key.metric == "usage":
            return unit

        if self._key.metric == "tariff":
            return f"RUB/{unit}" if unit else "RUB"

        if self._key.metric == "total":
            return "RUB"

        return None

    @property
    def device_class(self) -> str | None:
        if self._key.metric == "total":
            return SensorDeviceClass.MONETARY
        return None

    @property
    def native_value(self):
        rec = self._record()
        if not rec:
            return None
        return rec.get(self._key.metric)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rec = self._record() or {}
        history = self.coordinator.get_service_history(self._key.address_id, self._key.service_key)

        history_preview = []
        for item in history[:24]:
            history_preview.append(
                {
                    "month_key": item.get("month"),
                    "value": item.get(self._key.metric),
                }
            )

        return {
            "address_id": self._key.address_id,
            "service_key": self._key.service_key,
            "service_raw": rec.get("service_raw"),
            "month_key": rec.get("month"),
            "unit": self.native_unit_of_measurement,
            "side": rec.get("side"),
            "history_count": len(history),
            "history_preview": history_preview,
        }


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data["uksn"][entry.entry_id]
    coordinator: UKSNCoordinator = data["coordinator"]

    entities: list[SensorEntity] = []
    existing_service_entities: set[tuple[str, str, str]] = set()

    # Основные адресные сущности + счётчики из API остаются на основном девайсе
    for address_id in coordinator.data.get("counters_by_address", {}).keys():
        entities.append(UKSNMonthlyChargeSensor(coordinator, str(address_id)))
        entities.append(UKSNDebtSensor(coordinator, str(address_id)))

    counters_by_address = coordinator.data.get("counters_by_address", {})
    for address_id, counters in counters_by_address.items():
        for c in counters:
            cid = str(c.get("counter_id"))
            if not cid:
                continue
            entities.append(UKSNCounterSensor(coordinator, str(address_id), cid))

    initial_pairs = coordinator.collect_service_pairs()
    _LOGGER.debug("Initial service pairs in sensor setup: %s", sorted(initial_pairs))

    for address_id, service_key in sorted(initial_pairs):
        rec = coordinator.get_latest_service_record(address_id, service_key)
        if not rec:
            continue

        metrics = ["tariff", "total"]

        if rec.get("usage") is not None:
            metrics.insert(0, "usage")

        for metric in metrics:
            existing_service_entities.add((address_id, service_key, metric))
            entities.append(UKSNServiceSensor(coordinator, address_id, service_key, metric))

    _LOGGER.debug("Adding initial sensor entities count=%s", len(entities))
    async_add_entities(entities)

    def _handle_new_service_pairs(new_pairs: set[tuple[str, str]]) -> None:
        _LOGGER.debug("Sensor listener received new_pairs=%s", sorted(new_pairs))
        new_entities: list[SensorEntity] = []
        for address_id, service_key in sorted(new_pairs):

            rec = coordinator.get_latest_service_record(address_id, service_key)
            if not rec:
                continue

            metrics = ["tariff", "total"]

            if rec.get("usage") is not None:
                metrics.insert(0, "usage")

            for metric in metrics:

                key = (address_id, service_key, metric)
                if key in existing_service_entities:
                    continue
                existing_service_entities.add(key)
                new_entities.append(UKSNServiceSensor(coordinator, address_id, service_key, metric))

        if new_entities:
            _LOGGER.debug("Adding new dynamic service entities count=%s", len(new_entities))
            async_add_entities(new_entities)
        else:
            _LOGGER.debug("No new dynamic service entities to add")

    coordinator.add_service_entity_listener(_handle_new_service_pairs)