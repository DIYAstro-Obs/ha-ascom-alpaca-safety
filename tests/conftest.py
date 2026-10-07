import asyncio
import importlib
import types

import pytest

import ha_stubs
from helpers import Timers

coordinator_mod = importlib.import_module("ascom_alpaca_safety.coordinator")


@pytest.fixture(autouse=True)
def clean_storage():
    """The fake storage is class-level ("the disk"): every test starts with an empty one."""
    ha_stubs.FakeStore.saved.clear()
    ha_stubs.FakeIssues.issues.clear()
    yield
    ha_stubs.FakeStore.saved.clear()
    ha_stubs.FakeIssues.issues.clear()


@pytest.fixture
def make(monkeypatch):
    """Build and start a coordinator from group dicts and initial entity states."""
    timers = Timers()
    monkeypatch.setattr(coordinator_mod, "async_call_later", timers.call_later)
    monkeypatch.setattr(
        coordinator_mod,
        "async_track_state_change_event",
        lambda hass, ids, cb: (lambda: None),
    )
    monkeypatch.setattr(
        coordinator_mod, "async_track_time_interval", timers.track_interval
    )

    def _make(groups, initial, running=True):
        hass = ha_stubs.FakeHass()
        hass.is_running = running
        for eid, value in initial.items():
            hass.states.set(eid, value)
        entry = types.SimpleNamespace(entry_id="E", options={"groups": groups})
        coordinator = coordinator_mod.SafetyCoordinator(hass, entry)
        asyncio.run(coordinator.async_start())
        return hass, coordinator, timers

    return _make
