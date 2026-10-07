"""Binary sensor platform for ASCOM Alpaca Safety."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_GROUPS,
    CONF_GROUP_ID,
    CONF_GROUP_NAME,
    DATA_COORDINATOR,
    DOMAIN,
    INTEGRATION_NAME,
    SAFETY_DRIVER_VERSION,
    UNIQUE_ID_GROUP_PREFIX,
    UNIQUE_ID_MASTER,
)
from .coordinator import SafetyCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: SafetyCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]

    entities: list[BinarySensorEntity] = []

    # Master safety sensor
    entities.append(SafetyMasterSensor(coordinator, entry))

    # Per-group sensors, identified by the group's stable ID (not its position)
    groups_config = entry.options.get(CONF_GROUPS, [])
    group_ids: set[str] = set()
    for i, g_conf in enumerate(groups_config):
        group_id = str(g_conf.get(CONF_GROUP_ID) or i)
        name = g_conf.get(CONF_GROUP_NAME, f"Group {i}")
        group_ids.add(group_id)
        entities.append(SafetyGroupSensor(coordinator, entry, group_id, name))

    _remove_stale_group_sensors(hass, entry, group_ids)
    async_add_entities(entities, True)


@callback
def _remove_stale_group_sensors(
    hass: HomeAssistant, entry: ConfigEntry, group_ids: set[str]
) -> None:
    """Remove registry entries of group sensors whose group no longer exists."""
    ent_reg = er.async_get(hass)
    prefix = f"{entry.entry_id}_{UNIQUE_ID_GROUP_PREFIX}"
    keep = {f"{prefix}{group_id}" for group_id in group_ids}
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if (
            ent.domain == "binary_sensor"
            and ent.unique_id.startswith(prefix)
            and ent.unique_id not in keep
        ):
            ent_reg.async_remove(ent.entity_id)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return device info for the Safety device."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=INTEGRATION_NAME,
        manufacturer="ASCOM Alpaca Safety",
        model="Safety Monitor",
        sw_version=SAFETY_DRIVER_VERSION,
    )


class SafetyMasterSensor(BinarySensorEntity):
    """Master safety binary sensor. ON = UNSAFE."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SafetyCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the master sensor."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{UNIQUE_ID_MASTER}"
        self._attr_name = "Observatory Safety"
        self._remove_listener = None

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry)

    @property
    def is_on(self) -> bool:
        """Return True if UNSAFE (sensor ON = danger for safety class)."""
        return not self._coordinator.is_safe

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return additional attributes."""
        attrs: dict[str, str] = {
            "description": self._coordinator.description,
            "status": "SAFE" if self._coordinator.is_safe else "UNSAFE",
        }
        if self._coordinator.force_unsafe:
            attrs["override"] = "force_unsafe"
        return attrs

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self._remove_listener = self._coordinator.async_add_listener(
            self._handle_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from coordinator updates."""
        if self._remove_listener:
            self._remove_listener()

    @callback
    def _handle_update(self) -> None:
        """Handle coordinator state update."""
        self.async_write_ha_state()


class SafetyGroupSensor(BinarySensorEntity):
    """Per-group safety binary sensor. ON = UNSAFE."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SafetyCoordinator,
        entry: ConfigEntry,
        group_id: str,
        group_name: str,
    ) -> None:
        """Initialize a group sensor."""
        self._coordinator = coordinator
        self._entry = entry
        self._group_id = group_id
        self._group_name = group_name
        self._attr_unique_id = (
            f"{entry.entry_id}_{UNIQUE_ID_GROUP_PREFIX}{group_id}"
        )
        self._attr_name = f"Group {group_name}"
        self._remove_listener = None

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry)

    @property
    def _group_state(self):
        """Get the group state by its stable ID."""
        for group in self._coordinator.groups:
            if group.group_id == self._group_id:
                return group
        return None

    @property
    def is_on(self) -> bool:
        """Return True if group is UNSAFE."""
        gs = self._group_state
        return gs.is_unsafe if gs else True

    @property
    def available(self) -> bool:
        """Return True if group exists."""
        return self._group_state is not None

    @property
    def extra_state_attributes(self) -> dict[str, str | float | None]:
        """Return additional attributes."""
        gs = self._group_state
        if gs is None:
            return {}
        attrs: dict[str, str | float | None] = {
            "description": gs.description,
            "logic": gs.logic,
            # an end time instead of the remaining seconds: the state is only written on events,
            # so a countdown would stand still
            "settle_ends_at": (
                gs.settle_ends_at.isoformat() if gs.settle_ends_at else None
            ),
            "boot_guard_complete": str(gs.boot_guard_complete),
        }
        return attrs

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
