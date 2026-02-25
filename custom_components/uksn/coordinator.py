from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UKSNClient
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
            result: dict[str, Any] = {"counters_by_address": {}}

            for address_id in self.selected_addresses:
                counters = await self.client.get_counters(address_id)
                result["counters_by_address"][str(address_id)] = counters

            return result

        except Exception as err:
            raise UpdateFailed(str(err)) from err