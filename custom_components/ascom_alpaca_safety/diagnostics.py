"""Diagnostics for ASCOM Alpaca Safety (Settings -> Devices & services -> ... -> Download diagnostics)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """The configuration and the current state of every group and rule."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    return {
        "options": dict(entry.options),
        "state": coordinator.diagnostics(),
    }
