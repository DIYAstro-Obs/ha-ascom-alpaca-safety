"""Tests for review package 2: settle end time, registration with the server, rule input choices,
group names and the repair issue for a rule whose entity does not exist."""

import asyncio
import importlib
import json
import pathlib
import types
from datetime import datetime, timedelta, timezone

import pytest

from ha_stubs import FakeHass, FakeIssues
from helpers import grp, rule

const = importlib.import_module("ascom_alpaca_safety.const")
rules = importlib.import_module("ascom_alpaca_safety.rules")
integration = importlib.import_module("ascom_alpaca_safety")
binary_sensor = importlib.import_module("ascom_alpaca_safety.binary_sensor")

COMPONENT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "ascom_alpaca_safety"
ISSUE = (const.DOMAIN, "missing_entity_sensor.gone")


# ---- B3: the settle end is a time of day, not a countdown that stands still --------------------------
def test_the_settle_timer_reports_its_end_time(make):
    _, coordinator, _ = make([grp("g", [rule("sensor.x", ">", 5)], settle=300)], {"sensor.x": "1"})
    group = coordinator.groups[0]
    assert group.is_unsafe  # settling

    ends_at = group.settle_ends_at
    assert ends_at is not None
    assert abs((ends_at - datetime.now(timezone.utc)).total_seconds() - 300) < 3
    assert abs(group.settle_ends_at - ends_at) < timedelta(seconds=1)  # stays put while time passes
    assert "Settling until" in group.description


def test_the_group_sensor_exposes_settle_ends_at_instead_of_remaining_seconds(make):
    _, coordinator, _ = make([grp("g", [rule("sensor.x", ">", 5)], settle=300)], {"sensor.x": "1"})
    sensor = binary_sensor.SafetyGroupSensor(
        coordinator, types.SimpleNamespace(entry_id="E"), coordinator.groups[0].group_id, "g"
    )
    attributes = sensor.extra_state_attributes
    assert "settle_remaining" not in attributes
    assert datetime.fromisoformat(attributes["settle_ends_at"]) > datetime.now(timezone.utc)

    _, no_settle, _ = make([grp("g", [rule("sensor.x", ">", 5)], settle=0)], {"sensor.x": "1"})
    sensor = binary_sensor.SafetyGroupSensor(
        no_settle, types.SimpleNamespace(entry_id="E"), no_settle.groups[0].group_id, "g"
    )
    assert sensor.extra_state_attributes["settle_ends_at"] is None


# ---- B7: registration with the Alpaca server -----------------------------------------------------------
def server_hass(register, with_entry=True):
    hass = FakeHass()
    hass.data = {
        const.DOMAIN: {"E": {}} if with_entry else {},
        const.ALPACA_SERVER_API_KEY: {"async_register_device": register},
    }
    return hass


ENTRY = types.SimpleNamespace(entry_id="E")


def try_register(hass):
    return asyncio.run(integration._try_register_with_server(hass, types.SimpleNamespace(), ENTRY))


def test_registration_is_stored_and_does_not_happen_twice():
    log = []

    async def register(device_type, device_name, handler):
        log.append("registered")
        return lambda: log.append("unregistered")

    hass = server_hass(register)
    assert try_register(hass) is True
    assert try_register(hass) is True
    assert log == ["registered"]
    assert const.DATA_SERVER_UNREGISTER in hass.data[const.DOMAIN]["E"]


def test_an_entry_that_is_unloaded_while_registering_leaves_no_device_behind():
    log = []

    async def register(device_type, device_name, handler):
        hass.data[const.DOMAIN].pop("E")  # the entry is unloaded while the registration runs
        log.append("registered")
        return lambda: log.append("unregistered")

    hass = server_hass(register)
    assert try_register(hass) is False
    assert log == ["registered", "unregistered"]


def test_no_registration_for_an_entry_that_is_gone():
    async def register(device_type, device_name, handler):
        raise AssertionError("must not be called")

    assert try_register(server_hass(register, with_entry=False)) is False


def listen_for_server(hass, monkeypatch):
    notifications = []
    monkeypatch.setattr(integration, "async_create", lambda *args, **kwargs: notifications.append(kwargs))
    integration._listen_for_server(hass, types.SimpleNamespace(), ENTRY)
    return notifications


def test_a_late_added_integration_without_a_server_says_so_at_once(monkeypatch):
    hass = server_hass(None)
    hass.data[const.ALPACA_SERVER_API_KEY] = {}
    hass.is_running = True
    notifications = listen_for_server(hass, monkeypatch)

    assert len(notifications) == 1
    assert notifications[0]["notification_id"] == f"{const.DOMAIN}_server_missing"
    assert [event for event, _ in hass.listeners] == ["component_loaded"]  # still waits for the server


def test_while_ha_starts_the_notification_waits_for_the_end_of_the_start(monkeypatch):
    hass = server_hass(None)
    hass.data[const.ALPACA_SERVER_API_KEY] = {}
    hass.is_running = False
    notifications = listen_for_server(hass, monkeypatch)

    assert notifications == []
    assert [event for event, _ in hass.listeners] == ["component_loaded", "homeassistant_started"]


def test_the_server_is_set_up_before_safety():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert "ascom_alpaca_server" in manifest["after_dependencies"]


# ---- D3: input choices that follow the entity -----------------------------------------------------------
@pytest.mark.parametrize(
    "entity, attributes, expected",
    [
        ("binary_sensor.rain", {}, (["on", "off"], False)),
        ("switch.pump", {}, (["on", "off"], False)),
        ("weather.home", {}, (list(rules.WEATHER_STATES), True)),
        ("input_select.mode", {"options": ["Day", "Night"]}, (["Day", "Night"], True)),
        ("sensor.phase", {"options": ["a", "b"], "device_class": "enum"}, (["a", "b"], True)),
        ("sensor.wind", {"unit_of_measurement": "km/h"}, None),
        ("sensor.wind", None, None),
        ("sensor.empty", {"options": []}, None),
    ],
)
def test_threshold_choices(entity, attributes, expected):
    assert rules.threshold_choices(entity, attributes) == expected


def test_binary_entities_only_offer_equal_and_not_equal():
    assert rules.operators_for("binary_sensor.rain") == ("==", "!=")
    assert rules.operators_for("sensor.wind") is None


@pytest.mark.parametrize(
    "operator, threshold, state, expected",
    [
        (">", 5, "7", True),
        (">", 5, "3", False),
        ("<=", "10", "10", True),
        ("==", "on", "on", True),
        ("==", "ON", "on", True),
        ("!=", "on", "off", True),
        ("==", 5, "5.0", True),
        (">", 5, "windy", None),  # text for a numeric comparison cannot be evaluated
        ("==", "rainy", "Rainy", True),
    ],
)
def test_rule_triggered(operator, threshold, state, expected):
    assert rules.rule_triggered(operator, threshold, state) is expected


# ---- D4: group names ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name, others, error",
    [
        ("Weather", [], None),
        ("Weather", ["Moon"], None),
        ("", [], "Name is required"),
        ("   ", ["Weather"], "Name is required"),
        ("weather", ["Weather"], "A group with this name already exists"),
        (" Weather ", ["Weather", "Moon"], "A group with this name already exists"),
    ],
)
def test_group_name_error(name, others, error):
    assert rules.group_name_error(name, others) == error


# ---- D7: a rule on an entity that does not exist raises a repair issue -----------------------------------
def test_a_rule_on_a_missing_entity_raises_a_repair_issue(make):
    make([grp("Weather", [rule("sensor.gone", ">", 5)])], {})
    assert ISSUE in FakeIssues.issues
    assert FakeIssues.issues[ISSUE]["translation_placeholders"] == {
        "entity_id": "sensor.gone",
        "groups": "Weather",
    }
    assert FakeIssues.issues[ISSUE]["translation_key"] == "missing_entity"


def test_the_issue_names_every_group_that_uses_the_entity(make):
    make(
        [
            grp("Weather", [rule("sensor.gone", ">", 5)], gid="a"),
            grp("Moon", [rule("sensor.gone", "<", 1)], gid="b"),
        ],
        {},
    )
    assert FakeIssues.issues[ISSUE]["translation_placeholders"]["groups"] == "Weather, Moon"


def test_the_issue_disappears_when_the_entity_exists_again(make):
    hass, coordinator, _ = make([grp("Weather", [rule("sensor.gone", ">", 5)])], {})
    assert ISSUE in FakeIssues.issues

    hass.states.set("sensor.gone", "1")
    coordinator._check_watchdogs()  # the periodic check
    assert ISSUE not in FakeIssues.issues


def test_the_issue_of_a_rule_that_was_deleted_is_removed_after_the_reload(make):
    make([grp("Weather", [rule("sensor.gone", ">", 5)])], {})
    assert ISSUE in FakeIssues.issues

    make([grp("Weather", [rule("sensor.ok", ">", 5)])], {"sensor.ok": "1"})  # reload with the other rule
    assert ISSUE not in FakeIssues.issues


def test_no_issue_while_home_assistant_is_still_starting(make):
    make([grp("Weather", [rule("sensor.gone", ">", 5)])], {}, running=False)
    assert not FakeIssues.issues  # entities of slow integrations appear during the start


def test_an_existing_entity_raises_no_issue(make):
    make([grp("Weather", [rule("sensor.ok", ">", 5)])], {"sensor.ok": "1"})
    assert not FakeIssues.issues


def test_removing_the_integration_removes_the_issues(make):
    hass, _, _ = make([grp("Weather", [rule("sensor.gone", ">", 5)])], {})
    assert FakeIssues.issues

    asyncio.run(integration.async_remove_entry(hass, ENTRY))
    assert not FakeIssues.issues


# ---- D6: the master sensor is named for what it is (on = unsafe) ---------------------------------------------------
def test_the_master_sensor_is_called_safety_not_safe(make):
    """The name decides the entity id: "Observatory Safe" with the state "Unsafe" was misleading."""
    _, coordinator, _ = make([grp("g", [rule("sensor.x", ">", 5)])], {"sensor.x": "1"})
    sensor = binary_sensor.SafetyMasterSensor(coordinator, types.SimpleNamespace(entry_id="E"))
    assert sensor._attr_name == "Observatory Safety"


# ---- the SafetyMonitor does not disappear without a group ------------------------------------------------------
def test_without_a_group_the_safety_monitor_is_registered_and_reports_unsafe(make):
    """"No groups" is UNSAFE, not "not there": astronomy software still sees the monitor."""
    registered = []

    async def register(device_type, device_name, handler):
        registered.append((device_type, device_name, handler))
        return lambda: None

    hass, coordinator, _ = make([], {})
    hass.data = {const.DOMAIN: {"E": {}}, const.ALPACA_SERVER_API_KEY: {"async_register_device": register}}

    assert asyncio.run(integration._try_register_with_server(hass, coordinator, ENTRY)) is True
    assert [(kind, name) for kind, name, _ in registered] == [("SafetyMonitor", "ASCOM Alpaca Safety")]
    assert asyncio.run(registered[0][2]("issafe", {})) == {"Value": False}
    assert "No safety groups configured" in coordinator.description
