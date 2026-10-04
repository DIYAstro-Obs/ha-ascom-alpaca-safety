"""Integration setup for ASCOM Alpaca Safety."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.persistent_notification import (
    async_create,
    async_dismiss,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    ALPACA_SERVER_COMPONENT,
    ALPACA_SERVER_API_KEY,
    DATA_COORDINATOR,
    DATA_SERVER_UNREGISTER,
    DOMAIN,
    PLATFORMS,
    SAFETY_DEVICE_NAME,
    SAFETY_DEVICE_TYPE,
    SAFETY_DRIVER_VERSION,
)
from .coordinator import SafetyCoordinator

_LOGGER = logging.getLogger(__name__)

# Key for the component-loaded listener cancel callback
_DATA_COMPONENT_UNSUB = "component_unsub"
_DATA_STARTED_UNSUB = "started_unsub"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ASCOM Alpaca Safety from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create and start the safety coordinator
    coordinator = SafetyCoordinator(hass, entry)
    await coordinator.async_start()

    # Store coordinator reference
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
    }

    # Try to register with ASCOM Alpaca Server (handles race condition)
    if not await _try_register_with_server(hass, coordinator, entry):
        # Server not ready yet — listen for it to appear
        _listen_for_server(hass, coordinator, entry)

    # Forward platform setup
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options changes
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("ASCOM Alpaca Safety integration loaded successfully")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload ASCOM Alpaca Safety config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, {})
        coordinator: SafetyCoordinator | None = data.get(DATA_COORDINATOR)
        unregister = data.get(DATA_SERVER_UNREGISTER)

        # Cancel event listeners
        for key in (_DATA_COMPONENT_UNSUB, _DATA_STARTED_UNSUB):
            cancel = data.get(key)
            if cancel is not None:
                cancel()

        # Unregister from the Server
        if unregister is not None:
            try:
                unregister()
                _LOGGER.info("Unregistered Safety from ASCOM Alpaca Server")
            except Exception:
                _LOGGER.exception("Error unregistering from ASCOM Alpaca Server")

        if coordinator:
            await coordinator.async_stop()

    _LOGGER.info("ASCOM Alpaca Safety integration unloaded")
    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update — reload the integration."""
    _LOGGER.info("Options changed, reloading ASCOM Alpaca Safety")
    await hass.config_entries.async_reload(entry.entry_id)


# ---------------------------------------------------------------------------
# ASCOM Alpaca Server registration (with deferred retry)
# ---------------------------------------------------------------------------

async def _try_register_with_server(
    hass: HomeAssistant,
    coordinator: SafetyCoordinator,
    entry: ConfigEntry,
) -> bool:
    """Try to register with the Server. Returns True if successful."""
    server_api = hass.data.get(ALPACA_SERVER_API_KEY)
    if server_api is None or "async_register_device" not in server_api:
        return False

    # Build the request handler callback
    async def handle_alpaca_request(
        action: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return _dispatch_alpaca_action(coordinator, action, params)

    try:
        register_fn = server_api["async_register_device"]
        unregister = await register_fn(
            device_type=SAFETY_DEVICE_TYPE,
            device_name=SAFETY_DEVICE_NAME,
            handler=handle_alpaca_request,
        )
        hass.data[DOMAIN][entry.entry_id][DATA_SERVER_UNREGISTER] = unregister
        _LOGGER.info("Registered SafetyMonitor with ASCOM Alpaca Server")

        # Dismiss any previous "server missing" notification
        async_dismiss(hass, f"{DOMAIN}_server_missing")
        return True
    except Exception:
        _LOGGER.exception("Failed to register with ASCOM Alpaca Server")
        return False


def _listen_for_server(
    hass: HomeAssistant,
    coordinator: SafetyCoordinator,
    entry: ConfigEntry,
) -> None:
    """Set up listeners to detect when the Server integration loads."""
    _LOGGER.info(
        "ASCOM Alpaca Server not yet available — waiting for it to load..."
    )

    @callback
    def _on_component_loaded(event: Event) -> None:
        """Called when any HA integration finishes loading."""
        component = event.data.get("component", "")
        if component != ALPACA_SERVER_COMPONENT:
            return

        _LOGGER.info("ASCOM Alpaca Server detected — attempting registration")
        _cancel_server_listeners(hass, entry)
        hass.async_create_task(
            _try_register_with_server(hass, coordinator, entry)
        )

    @callback
    def _on_ha_started(event: Event) -> None:
        """Called when HA has fully started."""
        # Only cancel the component listener; this listener auto-removes
        entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        comp_unsub = entry_data.pop(_DATA_COMPONENT_UNSUB, None)
        if comp_unsub is not None:
            comp_unsub()
        entry_data.pop(_DATA_STARTED_UNSUB, None)

        # Try one more time — Server may have loaded during startup
        server_api = hass.data.get(ALPACA_SERVER_API_KEY)
        if server_api is not None and "async_register_device" in server_api:
            _LOGGER.info("ASCOM Alpaca Server found at HA startup — registering now")
            hass.async_create_task(
                _try_register_with_server(hass, coordinator, entry)
            )
        else:
            _LOGGER.warning(
                "ASCOM Alpaca Server not found after HA startup. "
                "Safety runs in standalone mode."
            )
            async_create(
                hass,
                (
                    "ASCOM Alpaca Safety is running in **standalone mode**. "
                    "All Home Assistant entities work normally.\n\n"
                    "To expose the Safety Monitor via the ASCOM/Alpaca API, "
                    "install and configure the **ASCOM Alpaca Server** integration."
                ),
                title="ASCOM Alpaca Safety — Server Not Found",
                notification_id=f"{DOMAIN}_server_missing",
            )

    # Listen for component loads and HA started
    entry_data = hass.data[DOMAIN][entry.entry_id]
    entry_data[_DATA_COMPONENT_UNSUB] = hass.bus.async_listen(
        "component_loaded", _on_component_loaded
    )
    entry_data[_DATA_STARTED_UNSUB] = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STARTED, _on_ha_started
    )


@callback
def _cancel_server_listeners(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Cancel the Server-detection event listeners."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    for key in (_DATA_COMPONENT_UNSUB, _DATA_STARTED_UNSUB):
        cancel = entry_data.pop(key, None)
        if cancel is not None:
            cancel()


# ---------------------------------------------------------------------------
# Alpaca action dispatcher
# ---------------------------------------------------------------------------

def _dispatch_alpaca_action(
    coordinator: SafetyCoordinator, action: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Map an Alpaca action string to a coordinator response."""
    action_lower = action.lower()

    if action_lower == "issafe":
        return {"Value": coordinator.is_safe}

    if action_lower == "connected":
        # Handle PUT/POST to connected=True/False
        if params is not None:
            for k, v in params.items():
                if k.lower() == "connected":
                    if isinstance(v, str):
                        v_lower = v.lower()
                        if v_lower == "true":
                            coordinator.is_connected = True
                        elif v_lower == "false":
                            coordinator.is_connected = False
                        else:
                            return {
                                "Value": False,
                                "ErrorNumber": 0x400,
                                "ErrorMessage": f"Invalid boolean value: {v}",
                                "HttpStatus": 400,
                            }
                    else:
                        coordinator.is_connected = bool(v)
                    break
        return {"Value": coordinator.is_connected}

    if action_lower == "name":
        return {"Value": SAFETY_DEVICE_NAME}

    if action_lower == "description":
        return {"Value": "Home Assistant ASCOM Alpaca Safety Monitor — monitors HA sensor entities and groups to report observatory safety status."}

    if action_lower == "driverinfo":
        return {"Value": "Home Assistant ASCOM Alpaca Safety Monitor"}

    if action_lower == "driverversion":
        return {"Value": SAFETY_DRIVER_VERSION}

    if action_lower == "interfaceversion":
        return {"Value": 1}

    if action_lower == "supportedactions":
        return {"Value": []}

    return {
        "Value": None,
        "ErrorNumber": 0x400,
        "ErrorMessage": f"Action '{action}' is not supported",
        "HttpStatus": 400,
    }
