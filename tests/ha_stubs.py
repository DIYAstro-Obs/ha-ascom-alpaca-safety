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


def callback(func):
    """Like Home Assistant's @callback: marks a function as safe to run in the event loop.

    Home Assistant runs a function WITHOUT this mark in a worker thread (see is_callback).
    """
    func._hass_callback = True
    return func


def is_callback(func) -> bool:
    return getattr(func, "_hass_callback", False)


class FakeIssues:
    """In-memory stand-in for the issue registry (class-level data = "the registry")."""

    issues: dict = {}


def _async_create_issue(hass, domain, issue_id, **kwargs):
    FakeIssues.issues[(domain, issue_id)] = kwargs


def _async_delete_issue(hass, domain, issue_id):
    FakeIssues.issues.pop((domain, issue_id), None)


class FakeStore:
    """In-memory stand-in for homeassistant.helpers.storage.Store (class-level data = "the disk")."""

    saved: dict = {}

    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        return FakeStore.saved.get(self.key)

    def async_delay_save(self, data_func, delay=0):
        FakeStore.saved[self.key] = data_func()

    async def async_remove(self):
        FakeStore.saved.pop(self.key, None)


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
        self.is_running = True
        self.data = {}
        self.listeners = []  # (event, callback) of async_listen and async_listen_once
        self.bus = types.SimpleNamespace(
            async_listen=self._listen,
            async_listen_once=self._listen,
        )

    def _listen(self, event, cb):
        self.listeners.append((event, cb))
        return lambda: None


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
        callback=callback,
    )
    helpers = _mod("homeassistant.helpers")
    helpers.event = _mod(
        "homeassistant.helpers.event",
        async_call_later=None,
        async_track_state_change_event=None,
        async_track_time_interval=None,
    )
    helpers.storage = _mod("homeassistant.helpers.storage", Store=FakeStore)
    helpers.issue_registry = _mod(
        "homeassistant.helpers.issue_registry",
        async_create_issue=_async_create_issue,
        async_delete_issue=_async_delete_issue,
        async_get=lambda hass: types.SimpleNamespace(issues=FakeIssues.issues),
        IssueSeverity=types.SimpleNamespace(WARNING="warning"),
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
        "homeassistant.util.dt",
        utcnow=lambda: datetime.now(timezone.utc),
        as_local=lambda value: value,
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
