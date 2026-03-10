from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import UKSNClient
from .coordinator import UKSNCoordinator
from .entity_base import device_info_for_address
from .sensor import _counter_name


class UKSNSendReadingButton(CoordinatorEntity[UKSNCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: UKSNCoordinator, client: UKSNClient, address_id: str, counter_id: str, name: str) -> None:
        super().__init__(coordinator)
        self.client = client
        self.address_id = address_id
        self.counter_id = counter_id
        self._attr_unique_id = f"uksn_send_reading_{counter_id}"
        self._attr_name = f"{name}: отправить"

    @property
    def device_info(self):
        return device_info_for_address(self.coordinator, self.address_id)

    def _find_input_entity_id(self) -> str | None:
        reg = er.async_get(self.hass)
        # unique_id number = uksn_reading_input_<counter_id>
        unique = f"uksn_reading_input_{self.counter_id}"
        for ent in reg.entities.values():
            if ent.platform == "number" and ent.unique_id == unique:
                return ent.entity_id
        return None

    async def async_press(self) -> None:
        input_eid = self._find_input_entity_id()
        if not input_eid:
            return

        st = self.hass.states.get(input_eid)
        if st is None or st.state in ("unknown", "unavailable", "", None):
            return

        # отправляем строго одно значение
        await self.client.set_counter_value(self.counter_id, st.state)
        await self.coordinator.async_request_refresh()


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data["uksn"][entry.entry_id]
    coordinator: UKSNCoordinator = data["coordinator"]
    client: UKSNClient = data["client"]

    entities = []
    for address_id, counters in coordinator.data.get("counters_by_address", {}).items():
        for c in counters:
            cid = str(c.get("counter_id"))
            if not cid:
                continue
            name = _counter_name(c)
            entities.append(UKSNSendReadingButton(coordinator, client, str(address_id), cid, name))

    async_add_entities(entities)