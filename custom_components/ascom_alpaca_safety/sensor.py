"""Sensor platform for ASCOM Alpaca Safety – the reason for the current state."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    INTEGRATION_NAME,
    UNIQUE_ID_REASON,
)
from .coordinator import SafetyCoordinator

_LOGGER = logging.getLogger(__name__)

# Home Assistant does not accept a state longer than this
MAX_STATE_LENGTH = 255


def state_text(text: str) -> str:
    """The text as a sensor state: cut to the length Home Assistant accepts."""
    if len(text) <= MAX_STATE_LENGTH:
        return text
    return text[: MAX_STATE_LENGTH - 1] + "…"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: SafetyCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    async_add_entities([SafetyReasonSensor(coordinator, entry)], True)


class SafetyReasonSensor(SensorEntity):
    """Why the monitor is safe or unsafe, as text (for dashboards and notifications)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SafetyCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the reason sensor."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{UNIQUE_ID_REASON}"
        self._attr_name = "Reason"
        self._attr_icon = "mdi:shield-search"
        self._remove_listener = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=INTEGRATION_NAME,
        )

    @property
    def native_value(self) -> str:
        """The master state in a few words (``UNSAFE: obs roof``): a card cuts a long state."""
        return state_text(self._coordinator.summary)

    @property
    def extra_state_attributes(self) -> dict[str, str | list[str]]:
        """The complete text (a state is limited to 255 characters, an attribute is not) and the unsafe groups."""
        return {
            "description": self._coordinator.description,
            "unsafe_groups": self._coordinator.unsafe_group_names,
        }

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
