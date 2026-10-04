"""Button platform for ASCOM Alpaca Safety – Force Safe (one-time bypass)."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    INTEGRATION_NAME,
    UNIQUE_ID_FORCE_SAFE,
)
from .coordinator import SafetyCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    coordinator: SafetyCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    async_add_entities([ForceSafeButton(coordinator, entry)], True)


class ForceSafeButton(ButtonEntity):
    """Button to trigger a one-time Force Safe bypass of settle timers."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: SafetyCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the force safe button."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{UNIQUE_ID_FORCE_SAFE}"
        self._attr_name = "Force Safe (Expert Override)"
        self._attr_icon = "mdi:shield-check"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=INTEGRATION_NAME,
        )

    async def async_press(self) -> None:
        """Handle button press - trigger force safe."""
        _LOGGER.warning("Force Safe triggered by user!")
        self._coordinator.trigger_force_safe()
