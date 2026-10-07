"""Switch platform for ASCOM Alpaca Safety – Force Unsafe toggle."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    INTEGRATION_NAME,
    UNIQUE_ID_FORCE_UNSAFE,
    UNIQUE_ID_MANUAL_SAFE,
)
from .coordinator import SafetyCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities."""
    coordinator: SafetyCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    async_add_entities(
        [ForceUnsafeSwitch(coordinator, entry), ManualSafeSwitch(coordinator, entry)], True
    )


class ForceUnsafeSwitch(SwitchEntity):
    """Switch to force the monitor into permanent UNSAFE (Maintenance Mode)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: SafetyCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the force unsafe switch."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{UNIQUE_ID_FORCE_UNSAFE}"
        self._attr_name = "Force Unsafe (Maintenance)"
        self._attr_icon = "mdi:shield-lock"
        self._remove_listener = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=INTEGRATION_NAME,
        )

    @property
    def is_on(self) -> bool:
        """Return True if force unsafe is active."""
        return self._coordinator.force_unsafe

    async def async_turn_on(self, **kwargs) -> None:
        """Activate force unsafe."""
        self._coordinator.set_force_unsafe(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Deactivate force unsafe."""
        self._coordinator.set_force_unsafe(False)

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


class ManualSafeSwitch(SwitchEntity):
    """Switch for the Manual Safe override: the monitor reports SAFE whatever the rules say.

    For a failed sensor while the user watches the sky in person. It ends after the number of hours of
    "Manual Safe Duration" (0 = until it is switched off); Force Unsafe always wins.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: SafetyCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the manual safe switch."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{UNIQUE_ID_MANUAL_SAFE}"
        self._attr_name = "Manual Safe (Override)"
        self._attr_icon = "mdi:shield-alert-outline"
        self._remove_listener = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=INTEGRATION_NAME,
        )

    @property
    def is_on(self) -> bool:
        """Return True if the override is active."""
        return self._coordinator.manual_safe

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """When the override ends."""
        until = self._coordinator.manual_safe_until
        if not self._coordinator.manual_safe:
            return {}
        return {"until": until.isoformat() if until else "never"}

    async def async_turn_on(self, **kwargs) -> None:
        """Start the override."""
        self._coordinator.set_manual_safe(True)

    async def async_turn_off(self, **kwargs) -> None:
        """End the override."""
        self._coordinator.set_manual_safe(False)

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
