from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UKSNClient, UKSNAuthError
from .const import (
    DEFAULT_HISTORY_MAX_MONTHS,
    DOMAIN,
    HISTORY_STOP_AFTER_ERRORS,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    UPDATE_INTERVAL_SECONDS,
)
from .receipt_parser import parse_invoice_pdf

_LOGGER = logging.getLogger(__name__)


def _extract_aba_id_from_bill(bill_data: Any) -> str | None:
    if isinstance(bill_data, list):
        for item in bill_data:
            aba_id = item.get("aba_id")
            if aba_id:
                return str(aba_id)
    elif isinstance(bill_data, dict):
        aba_id = bill_data.get("aba_id")
        if aba_id:
            return str(aba_id)
    return None


def _month_needs_reparse(month_data: dict[str, Any]) -> bool:
    """Определяет, нужно ли перепарсить уже сохранённый месяц.

    Считаем месяц устаревшим, если:
    - нет services
    - services пустой
    - у услуг нет service_key
    - у услуг нет side
    - есть только правая часть, а левой нет
    """
    if not isinstance(month_data, dict):
        return True

    services = month_data.get("services")
    if not isinstance(services, list) or not services:
        return True

    has_left = False
    has_right = False

    for service in services:
        if not isinstance(service, dict):
            return True
        if not service.get("service_key"):
            return True
        if not service.get("side"):
            return True

        side = service.get("side")
        if side == "left":
            has_left = True
        elif side == "right":
            has_right = True

    # Если нет вообще правой части — явно плохо
    if not has_right:
        return True

    # Если нет левой части — считаем старым форматом и перепарсиваем,
    # потому что ты как раз хочешь подтянуть услуги по метражу.
    if not has_left:
        return True

    return False


class UKSNCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: UKSNClient,
        selected_addresses: list[int],
        entry_id: str,
        history_max_months: int = DEFAULT_HISTORY_MAX_MONTHS,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_coordinator",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.client = client
        self.selected_addresses = selected_addresses
        self.entry_id = entry_id
        self.history_max_months = history_max_months

        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry_id}",
        )
        self._history_loaded = False
        self.invoice_history: dict[str, Any] = {}
        self._bill_signatures: dict[str, str] = {}

        self._service_entity_listeners: list[Callable[[set[tuple[str, str]]], None]] = []
        self._known_service_pairs: set[tuple[str, str]] = set()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            if not self._history_loaded:
                await self._async_load_history()

            result: dict[str, Any] = {
                "addresses_by_id": {},
                "aba_by_address": {},
                "bills_by_address": {},
                "counters_by_address": {},
                "invoice_history_by_address": self.invoice_history,
            }

            addresses = await self.client.get_addresses()
            for a in addresses:
                aid = a.get("address_id") or a.get("id")
                if aid is None:
                    continue
                result["addresses_by_id"][str(aid)] = a

            for address_id in self.selected_addresses:
                aid = str(address_id)
                bill_data = await self.client.get_bill_detail(address_id)
                counters = await self.client.get_counters(address_id)

                result["bills_by_address"][aid] = bill_data
                result["counters_by_address"][aid] = counters

                aba_id = _extract_aba_id_from_bill(bill_data)
                if aba_id:
                    result["aba_by_address"][aid] = aba_id

            for address_id in self.selected_addresses:
                aid = str(address_id)
                bill = result["bills_by_address"].get(aid)
                if bill is None:
                    continue

                new_signature = self._bill_signature(bill)
                old_signature = self._bill_signatures.get(aid)
                self._bill_signatures[aid] = new_signature

                if old_signature is None:
                    continue

                if old_signature != new_signature:
                    aba_id = result["aba_by_address"].get(aid)
                    if aba_id:
                        _LOGGER.info(
                            "Bill signature changed for address_id=%s aba_id=%s, refreshing invoice history",
                            aid,
                            aba_id,
                        )
                        await self._async_fetch_invoice_history_for_address(
                            address_id=aid,
                            aba_id=aba_id,
                            max_months=self.history_max_months,
                        )

            result["invoice_history_by_address"] = self.invoice_history
            self._notify_new_service_pairs_if_any()
            return result

        except UKSNAuthError as err:
            _LOGGER.warning("Authorization lost during update: %s", err)
            raise UpdateFailed(f"Unauthorized: {err}") from err
        except Exception as err:
            raise UpdateFailed(str(err)) from err

    async def _async_load_history(self) -> None:
        data = await self._store.async_load()
        self.invoice_history = data or {}
        self._history_loaded = True
        self._known_service_pairs = self.collect_service_pairs()
        _LOGGER.debug(
            "Loaded invoice history store: addresses=%s service_pairs=%s",
            list(self.invoice_history.keys()),
            sorted(self._known_service_pairs),
        )

    async def _async_save_history(self) -> None:
        await self._store.async_save(self.invoice_history)
        _LOGGER.debug("Saved invoice history store for entry_id=%s", self.entry_id)

    @staticmethod
    def _bill_signature(bill_data: Any) -> str:
        try:
            return json.dumps(bill_data, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(bill_data)

    @staticmethod
    def _month_key(year: int, month: int) -> str:
        return f"{year:04d}-{month:02d}"

    @staticmethod
    def _prev_month(year: int, month: int) -> tuple[int, int]:
        if month == 1:
            return year - 1, 12
        return year, month - 1

    async def _async_parse_pdf_bytes(self, pdf_bytes: bytes) -> dict[str, Any]:
        def _parse() -> dict[str, Any]:
            fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(pdf_bytes)
                parsed = parse_invoice_pdf(tmp_path)
                return parsed
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        parsed = await self.hass.async_add_executor_job(_parse)
        _LOGGER.debug(
            "Parsed PDF: period=%s total=%s services=%s service_keys=%s sides=%s",
            parsed.get("period"),
            parsed.get("total_to_pay"),
            len(parsed.get("services", [])),
            [x.get("service_key") for x in parsed.get("services", [])],
            [x.get("side") for x in parsed.get("services", [])],
        )
        return parsed

    def collect_service_pairs(self) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for address_id, address_data in self.invoice_history.items():
            months = address_data.get("months", {})
            for month_data in months.values():
                for service in month_data.get("services", []):
                    service_key = service.get("service_key")
                    if service_key:
                        pairs.add((str(address_id), str(service_key)))
        return pairs

    def add_service_entity_listener(self, listener: Callable[[set[tuple[str, str]]], None]) -> None:
        self._service_entity_listeners.append(listener)
        _LOGGER.debug("Registered service entity listener, total=%s", len(self._service_entity_listeners))

    def _notify_new_service_pairs_if_any(self) -> None:
        current_pairs = self.collect_service_pairs()
        new_pairs = current_pairs - self._known_service_pairs

        _LOGGER.debug(
            "Service pairs check: known=%s current=%s new=%s",
            sorted(self._known_service_pairs),
            sorted(current_pairs),
            sorted(new_pairs),
        )

        if not new_pairs:
            return

        self._known_service_pairs = current_pairs
        _LOGGER.info("Discovered new service pairs: %s", sorted(new_pairs))

        for listener in self._service_entity_listeners:
            try:
                listener(new_pairs)
            except Exception as err:
                _LOGGER.warning("Service entity listener failed: %s", err)

    def get_history_months_sorted(self, address_id: str) -> list[tuple[str, dict[str, Any]]]:
        history = self.invoice_history.get(str(address_id), {})
        months = history.get("months", {})
        items = [(k, v) for k, v in months.items() if isinstance(v, dict)]
        items.sort(key=lambda x: x[0], reverse=True)
        return items

    def get_latest_service_record(self, address_id: str, service_key: str) -> dict[str, Any] | None:
        for month_key, month_data in self.get_history_months_sorted(address_id):
            for service in month_data.get("services", []):
                if str(service.get("service_key")) == str(service_key):
                    return {
                        "month": month_key,
                        "period": month_data.get("period"),
                        "service_raw": service.get("service_raw") or service.get("service"),
                        "service": service.get("service"),
                        "service_key": service.get("service_key"),
                        "unit": service.get("unit"),
                        "usage": service.get("usage"),
                        "tariff": service.get("tariff"),
                        "total": service.get("total"),
                        "side": service.get("side"),
                    }
        return None

    def get_service_history(self, address_id: str, service_key: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for month_key, month_data in self.get_history_months_sorted(address_id):
            for service in month_data.get("services", []):
                if str(service.get("service_key")) == str(service_key):
                    items.append(
                        {
                            "month": month_key,
                            "period": month_data.get("period"),
                            "usage": service.get("usage"),
                            "tariff": service.get("tariff"),
                            "total": service.get("total"),
                            "unit": service.get("unit"),
                            "service_raw": service.get("service_raw") or service.get("service"),
                            "side": service.get("side"),
                        }
                    )
                    break
        return items

    async def _async_fetch_invoice_history_for_address(
        self,
        *,
        address_id: str,
        aba_id: str,
        max_months: int,
    ) -> int:
        addr_store = self.invoice_history.setdefault(
            address_id,
            {
                "aba_id": str(aba_id),
                "months": {},
                "updated_at": None,
            },
        )
        addr_store["aba_id"] = str(aba_id)
        months_store: dict[str, Any] = addr_store.setdefault("months", {})

        loaded_count = 0
        checked_count = 0
        consecutive_errors = 0

        now = datetime.now()
        year = now.year
        month = now.month

        _LOGGER.debug(
            "Start invoice history fetch: address_id=%s aba_id=%s max_months=%s existing_months=%s",
            address_id,
            aba_id,
            max_months,
            sorted(months_store.keys()),
        )

        while checked_count < max_months and consecutive_errors < HISTORY_STOP_AFTER_ERRORS:
            key = self._month_key(year, month)
            _LOGGER.debug(
                "Checking invoice month: address_id=%s aba_id=%s month=%s checked=%s loaded=%s errors=%s",
                address_id,
                aba_id,
                key,
                checked_count,
                loaded_count,
                consecutive_errors,
            )

            existing = months_store.get(key)
            if existing and not _month_needs_reparse(existing):
                _LOGGER.debug("Month %s already exists in fresh format, skipping", key)
                consecutive_errors = 0
            else:
                if existing:
                    _LOGGER.debug("Month %s exists but needs reparse due to old/incomplete format", key)

                try:
                    meta = await self.client.get_invoice_pdf_meta(aba_id, month, year)
                    pdf_url = meta.get("url") if isinstance(meta, dict) else None

                    _LOGGER.debug("Invoice meta for %s: %s", key, meta)

                    if not pdf_url:
                        consecutive_errors += 1
                        _LOGGER.debug(
                            "No invoice PDF for address_id=%s aba_id=%s month=%s error=%s",
                            address_id,
                            aba_id,
                            key,
                            meta.get("error") if isinstance(meta, dict) else meta,
                        )
                    else:
                        _LOGGER.debug("Invoice PDF url for %s: %s", key, pdf_url)
                        pdf_bytes = await self.client.download_pdf_bytes(pdf_url)
                        _LOGGER.debug("Invoice PDF downloaded for %s bytes=%s", key, len(pdf_bytes) if pdf_bytes else 0)

                        parsed = await self._async_parse_pdf_bytes(pdf_bytes)

                        months_store[key] = {
                            "year": year,
                            "month": month,
                            "period": parsed.get("period"),
                            "total_to_pay": parsed.get("total_to_pay"),
                            "services": parsed.get("services", []),
                            "source_url": pdf_url,
                            "fetched_at": datetime.now().isoformat(),
                        }
                        loaded_count += 1
                        consecutive_errors = 0
                        _LOGGER.info(
                            "Loaded invoice history for address_id=%s aba_id=%s month=%s services=%s",
                            address_id,
                            aba_id,
                            key,
                            len(parsed.get("services", [])),
                        )
                except Exception as err:
                    consecutive_errors += 1
                    _LOGGER.warning(
                        "Failed loading invoice history for address_id=%s aba_id=%s month=%s: %s",
                        address_id,
                        aba_id,
                        key,
                        err,
                    )

            checked_count += 1
            year, month = self._prev_month(year, month)

        addr_store["updated_at"] = datetime.now().isoformat()
        await self._async_save_history()
        self._notify_new_service_pairs_if_any()

        _LOGGER.debug(
            "Finish invoice history fetch: address_id=%s loaded=%s checked=%s errors=%s months_now=%s",
            address_id,
            loaded_count,
            checked_count,
            consecutive_errors,
            sorted(months_store.keys()),
        )
        return loaded_count

    async def async_fetch_invoice_history(
        self,
        *,
        address_id: str | None = None,
        max_months: int | None = None,
    ) -> int:
        if not self._history_loaded:
            await self._async_load_history()

        addresses = await self.client.get_addresses()
        addresses_by_id: dict[str, Any] = {}
        for a in addresses:
            aid = a.get("address_id") or a.get("id")
            if aid is not None:
                addresses_by_id[str(aid)] = a

        target_ids: list[str]
        if address_id:
            target_ids = [str(address_id)]
        else:
            target_ids = [str(x) for x in self.selected_addresses]

        total_loaded = 0
        limit = max_months if max_months is not None else self.history_max_months

        _LOGGER.debug(
            "Manual invoice history fetch start: target_ids=%s limit=%s selected_addresses=%s",
            target_ids,
            limit,
            self.selected_addresses,
        )

        for aid in target_ids:
            if aid not in addresses_by_id:
                _LOGGER.warning("address_id=%s not found in addresses list, skipping invoice history", aid)
                continue

            try:
                bill_data = await self.client.get_bill_detail(int(aid))
                aba_id = _extract_aba_id_from_bill(bill_data)
                _LOGGER.debug("Resolved aba_id for address_id=%s -> %s", aid, aba_id)

                if not aba_id:
                    _LOGGER.warning("address_id=%s has no aba_id in bill detail, skipping invoice history", aid)
                    continue

                total_loaded += await self._async_fetch_invoice_history_for_address(
                    address_id=str(aid),
                    aba_id=str(aba_id),
                    max_months=limit,
                )
            except Exception as err:
                _LOGGER.warning("Failed fetching invoice history for address_id=%s: %s", aid, err)

        _LOGGER.debug("Manual invoice history fetch finish: total_loaded=%s", total_loaded)
        return total_loaded