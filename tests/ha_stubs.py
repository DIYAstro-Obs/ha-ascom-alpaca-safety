"""Minimal stand-ins for the Home Assistant modules the integration imports.

The tests exercise the safety logic without a Home Assistant installation:
only ``pytest`` is needed. Importing this module installs the stubs and puts
``custom_components`` on ``sys.path``.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from pathlib import Path

CUSTOM_COMPONENTS = Path(__file__).resolve().parents[1] / "custom_components"


def _mod(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


class FakeState:
    def __init__(self, entity_id, state, attributes=None):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes or {}
        self.last_updated = datetime.now(timezone.utc)
        self.last_reported = self.last_updated


class Event:
    def __init__(self, data):
        self.data = data


class FakeStates:
    def __init__(self):
        self._states = {}

    def set(self, entity_id, state, attributes=None):
        self._states[entity_id] = FakeState(entity_id, state, attributes)

    def get(self, entity_id):
        return self._states.get(entity_id)


class FakeHass:
    def __init__(self):
        self.states = FakeStates()
        self.bus = types.SimpleNamespace(
            async_listen_once=lambda event, cb: (lambda: None)
        )


def install() -> None:
    if "homeassistant" in sys.modules:
        return

    ha = _mod("homeassistant")
    _mod("homeassistant.const", EVENT_HOMEASSISTANT_STARTED="homeassistant_started")
    _mod(
        "homeassistant.core",
        CALLBACK_TYPE=object,
        Event=Event,
        HomeAssistant=object,
        State=FakeState,
        callback=lambda func: func,
    )
    helpers = _mod("homeassistant.helpers")
    helpers.event = _mod(
        "homeassistant.helpers.event",
        async_call_later=None,
        async_track_state_change_event=None,
        async_track_time_interval=None,
    )
    helpers.entity = _mod(
        "homeassistant.helpers.entity",
        DeviceInfo=dict,
        EntityCategory=types.SimpleNamespace(CONFIG="config"),
    )
    helpers.entity_platform = _mod(
        "homeassistant.helpers.entity_platform", AddEntitiesCallback=object
    )
    helpers.entity_registry = _mod(
        "homeassistant.helpers.entity_registry",
        async_get=None,
        async_entries_for_config_entry=None,
    )
    ha.helpers = helpers

    _mod("homeassistant.config_entries", ConfigEntry=object)

    util = _mod("homeassistant.util")
    util.dt = _mod(
        "homeassistant.util.dt", utcnow=lambda: datetime.now(timezone.utc)
    )
    ha.util = util

    components = _mod("homeassistant.components")
    components.persistent_notification = _mod(
        "homeassistant.components.persistent_notification",
        async_create=lambda *args, **kwargs: None,
        async_dismiss=lambda *args, **kwargs: None,
    )

    class BinarySensorEntity:
        pass

    components.binary_sensor = _mod(
        "homeassistant.components.binary_sensor",
        BinarySensorDeviceClass=types.SimpleNamespace(SAFETY="safety"),
        BinarySensorEntity=BinarySensorEntity,
    )
    ha.components = components

    sys.path.insert(0, str(CUSTOM_COMPONENTS))


install()
