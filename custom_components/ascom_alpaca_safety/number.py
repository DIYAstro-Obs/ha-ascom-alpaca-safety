"""Number platform for ASCOM Alpaca Safety – how long the Manual Safe override lasts."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    INTEGRATION_NAME,
    MAX_MANUAL_SAFE_HOURS,
    UNIQUE_ID_MANUAL_SAFE_DURATION,
)
from .coordinator import SafetyCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""
    coordinator: SafetyCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    async_add_entities([ManualSafeDurationNumber(coordinator, entry)], True)


class ManualSafeDurationNumber(NumberEntity):
    """Hours the next Manual Safe override lasts (0 = until it is switched off)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_MANUAL_SAFE_HOURS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "h"

    def __init__(
        self, coordinator: SafetyCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the duration number."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{UNIQUE_ID_MANUAL_SAFE_DURATION}"
        self._attr_name = "Manual Safe Duration"
        self._attr_icon = "mdi:timer-outline"
        self._remove_listener = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=INTEGRATION_NAME,
        )

    @property
    def native_value(self) -> float:
        """The hours of the next override."""
        return self._coordinator.manual_safe_hours

    async def async_set_native_value(self, value: float) -> None:
        """Set the hours of the next override (a running one keeps its end)."""
        self._coordinator.set_manual_safe_hours(value)

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self._coordinator.async_add_listener(
            self._handle_update
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
