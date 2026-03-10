from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UKSNClient, UKSNAuthError
from .const import DOMAIN, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class UKSNCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: UKSNClient,
        selected_addresses: list[int],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_coordinator",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.client = client
        self.selected_addresses = selected_addresses

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            result: dict[str, Any] = {
                "addresses_by_id": {},
                "bills_by_address": {},
                "counters_by_address": {},
            }

            # Addresses
            addresses = await self.client.get_addresses()
            for a in addresses:
                aid = a.get("address_id") or a.get("id")
                if aid is not None:
                    result["addresses_by_id"][str(aid)] = a

            # For selected addresses: bills + counters
            for address_id in self.selected_addresses:
                aid = str(address_id)
                result["bills_by_address"][aid] = await self.client.get_bill_detail(address_id)
                result["counters_by_address"][aid] = await self.client.get_counters(address_id)

            return result

        except UKSNAuthError as err:
            _LOGGER.warning("Authorization lost during update: %s", err)
            raise UpdateFailed(f"Unauthorized: {err}") from err
        except Exception as err:
            raise UpdateFailed(str(err)) from err