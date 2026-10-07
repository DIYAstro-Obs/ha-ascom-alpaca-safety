"""Tests for the fixes of the code review: watchdog in the event loop, groups without rules,
Force Unsafe over a restart, rule checks, defaults and the Alpaca dispatcher."""

import asyncio
import importlib
import json
import pathlib
import types
from datetime import datetime, timedelta, timezone

import pytest

from ha_stubs import FakeStore, is_callback
from helpers import grp, push, rule

coordinator_mod = importlib.import_module("ascom_alpaca_safety.coordinator")
const = importlib.import_module("ascom_alpaca_safety.const")
rules = importlib.import_module("ascom_alpaca_safety.rules")
integration = importlib.import_module("ascom_alpaca_safety")

COMPONENT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "ascom_alpaca_safety"


# ---- A1: the watchdog check has to run in the event loop -----------------------------------------
def test_the_watchdog_check_runs_in_the_event_loop(make):
    """Home Assistant runs a function without @callback in a worker thread; this one writes entity states."""
    _, _, timers = make([grp("g", [rule("sensor.x", ">", 5, watchdog=60)])], {"sensor.x": "1"})
    [interval] = timers.intervals
    assert is_callback(interval["cb"])


def test_every_timer_callback_runs_in_the_event_loop(make):
    hass, coordinator, timers = make(
        [grp("g", [rule("sensor.x", ">", 5, delay=10)], settle=60)], {"sensor.x": "1"}
    )
    push(hass, coordinator, "sensor.x", "9")  # unsafe delay timer
    push(hass, coordinator, "sensor.x", "1")  # clears; the settle timer starts after the unsafe state
    assert timers.items
    assert all(is_callback(item["cb"]) for item in timers.items)


def test_watchdog_expiry_makes_the_group_unsafe_and_a_report_recovers_it(make):
    hass, coordinator, _ = make(
        [grp("g", [rule("sensor.x", "<", 10, watchdog=60)], settle=0)], {"sensor.x": "50"}
    )
    assert coordinator.is_safe

    long_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
    state = hass.states.get("sensor.x")
    state.last_reported = state.last_updated = long_ago
    coordinator._check_watchdogs()
    assert not coordinator.is_safe
    assert "watchdog expired" in coordinator.description

    push(hass, coordinator, "sensor.x", "50")  # the entity reports again
    assert coordinator.is_safe
    coordinator._check_watchdogs()
    assert coordinator.is_safe


# ---- A2: a group without rules monitors nothing ----------------------------------------------------
def test_a_group_without_rules_is_unsafe(make):
    _, coordinator, _ = make([grp("empty", [])], {})
    assert not coordinator.is_safe
    assert "no rules" in coordinator.description


def test_an_empty_group_keeps_the_whole_monitor_unsafe(make):
    _, coordinator, _ = make(
        [grp("ok", [rule("sensor.x", ">", 5)], settle=0), grp("empty", [])], {"sensor.x": "1"}
    )
    assert not coordinator.is_safe
    assert "Group empty" in coordinator.description


def test_force_safe_does_not_make_an_empty_group_safe(make):
    _, coordinator, _ = make([grp("empty", [])], {})
    coordinator.trigger_force_safe()
    assert not coordinator.is_safe


def test_deleting_the_last_rule_does_not_make_the_group_safe(make):
    """The options flow stores the group with an empty rule list; after the reload it must be unsafe."""
    hass, coordinator, _ = make([grp("g", [rule("sensor.x", ">", 5)], settle=0)], {"sensor.x": "1"})
    assert coordinator.is_safe
    _, reloaded, _ = make([grp("g", [])], {"sensor.x": "1"})
    assert not reloaded.is_safe


# ---- A3: Force Unsafe (maintenance mode) survives a restart ---------------------------------------
GROUPS = [grp("g", [rule("sensor.x", ">", 5)], settle=0)]


def test_force_unsafe_survives_a_restart_or_reload(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "1"})
    assert coordinator.is_safe
    coordinator.set_force_unsafe(True)
    assert not coordinator.is_safe

    _, restarted, _ = make(GROUPS, {"sensor.x": "1"})  # a new coordinator for the same entry
    assert restarted.force_unsafe
    assert not restarted.is_safe
    assert "Force Unsafe" in restarted.description

    restarted.set_force_unsafe(False)
    _, again, _ = make(GROUPS, {"sensor.x": "1"})
    assert not again.force_unsafe
    assert again.is_safe


def test_the_stored_maintenance_mode_is_deleted_with_the_integration(make):
    hass, coordinator, _ = make(GROUPS, {"sensor.x": "1"})
    coordinator.set_force_unsafe(True)
    assert FakeStore.saved

    asyncio.run(integration.async_remove_entry(hass, types.SimpleNamespace(entry_id="E")))
    assert not FakeStore.saved


# ---- A4: a rule that cannot match has to be refused ---------------------------------------------
@pytest.mark.parametrize(
    "state, operator, threshold, expected",
    [
        ("on", "==", "ON", True),
        ("ON", "==", "on", True),
        ("Rainy", "==", "rainy", True),
        ("on", "==", " on ", True),
        ("on", "!=", "ON", False),
        ("off", "!=", "ON", True),
        ("sunny", "==", "rainy", False),
    ],
)
def test_string_comparison_ignores_case_and_spaces(state, operator, threshold, expected):
    assert coordinator_mod.RuleState("x.y", operator, threshold, 0, 0).evaluate(state) is expected


def test_a_threshold_with_spaces_still_matches(make):
    hass, coordinator, _ = make(
        [grp("g", [rule("binary_sensor.rain", "==", "on ")], settle=0)], {"binary_sensor.rain": "off"}
    )
    assert coordinator.is_safe
    push(hass, coordinator, "binary_sensor.rain", "on")
    assert not coordinator.is_safe


@pytest.mark.parametrize(
    "entity, operator, threshold, error",
    [
        ("binary_sensor.rain", "==", "on", None),
        ("binary_sensor.rain", "!=", "OFF ", None),
        ("switch.pump", "==", "off", None),
        ("binary_sensor.rain", "==", "1", "threshold_not_on_off"),
        ("binary_sensor.rain", "!=", "true", "threshold_not_on_off"),
        ("input_boolean.maintenance", "==", "yes", "threshold_not_on_off"),
        ("binary_sensor.rain", ">", "1", "operator_not_for_on_off"),
        ("sensor.wind", ">", "5.5", None),
        ("sensor.wind", "<=", "10", None),
        ("sensor.wind", ">", "fast", "threshold_not_numeric"),
        ("sensor.wind", "<", "", "threshold_not_numeric"),
        ("sensor.mode", "==", "abc", None),
        ("weather.home", "==", "rainy", None),
    ],
)
def test_threshold_check(entity, operator, threshold, error):
    assert rules.threshold_error(entity, operator, threshold) == error


# ---- A5: the watchdog default depends on how the entity reports -----------------------------------
@pytest.mark.parametrize(
    "entity, expected",
    [
        ("sensor.wind", 300),
        ("weather.home", 300),
        ("binary_sensor.rain", 0),
        ("switch.pump", 0),
        ("input_boolean.maintenance", 0),
        ("light.panel", 0),
        ("sun.sun", 0),
    ],
)
def test_default_watchdog_timeout(entity, expected):
    assert rules.default_watchdog_timeout(entity) == expected


def test_a_stored_rule_without_a_watchdog_value_gets_the_default_of_its_entity(make):
    no_watchdog = {"entity_id": "sensor.x", "operator": ">", "threshold": "5"}
    no_watchdog_binary = {"entity_id": "binary_sensor.y", "operator": "==", "threshold": "on"}
    _, coordinator, _ = make(
        [grp("g", [no_watchdog, no_watchdog_binary])], {"sensor.x": "1", "binary_sensor.y": "off"}
    )
    sensor_rule, binary_rule = coordinator.groups[0].rules
    assert sensor_rule.watchdog_timeout == 300
    assert binary_rule.watchdog_timeout == 0


# ---- S1: settle time default ---------------------------------------------------------------------
def test_settle_time_defaults_to_five_minutes(make):
    assert const.DEFAULT_SETTLE_TIME == 300
    group_without_settle = {"group_name": "g", "rules": [rule("sensor.x", ">", 5)]}
    _, coordinator, _ = make([group_without_settle], {"sensor.x": "1"})
    assert coordinator.groups[0].settle_time == 300


# ---- E1: the version exists once (manifest) and the constant must follow it ------------------------
def test_driver_version_matches_the_manifest():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert const.SAFETY_DRIVER_VERSION == manifest["version"]


# ---- Alpaca dispatcher ---------------------------------------------------------------------------
def dispatch(coordinator, action, params=None):
    return integration._dispatch_alpaca_action(coordinator, action, params)


def test_issafe_follows_the_coordinator(make):
    hass, coordinator, _ = make(GROUPS, {"sensor.x": "1"})
    assert dispatch(coordinator, "IsSafe") == {"Value": True}
    push(hass, coordinator, "sensor.x", "9")
    assert dispatch(coordinator, "issafe") == {"Value": False}


def test_device_information(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "1"})
    assert dispatch(coordinator, "name")["Value"] == const.SAFETY_DEVICE_NAME
    assert dispatch(coordinator, "driverversion")["Value"] == const.SAFETY_DRIVER_VERSION
    assert dispatch(coordinator, "interfaceversion")["Value"] == 1
    assert dispatch(coordinator, "supportedactions")["Value"] == []


def test_issafe_answers_without_a_connection(make):
    """The server keeps Connected per client; a client that has not connected (for example after the
    reload that saving the options causes) still gets its answer instead of an error."""
    _, coordinator, _ = make(GROUPS, {"sensor.x": "1"})
    assert dispatch(coordinator, "issafe") == {"Value": True}


def test_an_unknown_action_is_an_error(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "1"})
    result = dispatch(coordinator, "nonsense")
    assert result["ErrorNumber"] == 0x400
    assert result["HttpStatus"] == 400
