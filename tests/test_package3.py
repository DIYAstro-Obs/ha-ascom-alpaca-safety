"""Tests for review package 3: attributes and lists in rules, grace time for unavailable entities,
the event on Safe/Unsafe changes, diagnostics and the reason sensor."""

import asyncio
import importlib
import json
import types
from datetime import datetime, timedelta, timezone

import pytest

from helpers import grp, push, rule

const = importlib.import_module("ascom_alpaca_safety.const")
rules = importlib.import_module("ascom_alpaca_safety.rules")
sensor = importlib.import_module("ascom_alpaca_safety.sensor")
diagnostics = importlib.import_module("ascom_alpaca_safety.diagnostics")


# ---- B13: the value a rule watches ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "state, attributes, attribute, expected",
    [
        ("sunny", {"wind_speed": 12.5}, "", "sunny"),
        ("sunny", {"wind_speed": 12.5}, "wind_speed", "12.5"),
        ("sunny", {"wind_speed": 0}, "wind_speed", "0"),
        ("sunny", {"flag": False}, "flag", "False"),
        ("sunny", {}, "wind_speed", None),
        ("sunny", None, "wind_speed", None),
        ("sunny", {"wind_speed": None}, "wind_speed", None),
    ],
)
def test_watched_value(state, attributes, attribute, expected):
    assert rules.watched_value(state, attributes, attribute) == expected


@pytest.mark.parametrize(
    "operator, threshold, value, expected",
    [
        ("in", "rainy, pouring, lightning", "pouring", True),
        ("in", "rainy,pouring", "Rainy", True),
        ("in", "rainy, pouring", "sunny", False),
        ("not in", "sunny, cloudy", "rainy", True),
        ("not in", "sunny, cloudy", "Sunny", False),
        ("in", "1, 2, 3", "2.0", True),  # numbers are compared as numbers
        ("in", 5.0, "5", True),  # a single number is stored as a float
        ("in", "a, b", "", False),
        ("in", ",,", "x", False),  # nothing in the list
        ("not in", ",,", "x", True),
    ],
)
def test_list_operators(operator, threshold, value, expected):
    assert rules.rule_triggered(operator, threshold, value) is expected


@pytest.mark.parametrize(
    "entity, operator, threshold, attribute, error",
    [
        ("weather.home", "in", "rainy, pouring", "", None),
        ("weather.home", "in", " , ", "", "list_empty"),
        ("weather.home", "not in", "", "", "list_empty"),
        ("binary_sensor.rain", "in", "on, off", "", "operator_not_for_on_off"),
        # an attribute of a binary entity can hold anything
        ("binary_sensor.rain", ">", "5", "temperature", None),
        ("binary_sensor.rain", "==", "high", "level", None),
        ("binary_sensor.rain", "in", "a, b", "level", None),
        ("light.lamp", ">", "text", "brightness", "threshold_not_numeric"),
        # without an attribute nothing has changed
        ("binary_sensor.rain", "==", "high", "", "threshold_not_on_off"),
        ("binary_sensor.rain", ">", "5", "", "operator_not_for_on_off"),
    ],
)
def test_threshold_error_with_attribute_and_list(entity, operator, threshold, attribute, error):
    assert rules.threshold_error(entity, operator, threshold, attribute) == error


def test_an_attribute_has_no_fixed_choices_and_all_operators():
    assert rules.threshold_choices("binary_sensor.rain", {}, "level") is None
    assert rules.threshold_choices("weather.home", {}, "wind_speed") is None
    assert rules.operators_for("binary_sensor.rain", "level") is None
    assert rules.operators_for("binary_sensor.rain") == ("==", "!=")


def test_attribute_choices_leave_out_the_attributes_every_entity_has():
    attributes = {"friendly_name": "Home", "icon": "mdi:x", "wind_speed": 3, "humidity": 80}
    assert rules.attribute_choices(attributes) == ["humidity", "wind_speed"]
    assert rules.attribute_choices({"friendly_name": "Home"}) == []
    assert rules.attribute_choices(None) == []
    # the attribute of the rule stays in the list although the entity does not show it now
    assert rules.attribute_choices({}, "wind_speed") == ["wind_speed"]


# ---- B13: rules on an attribute in the coordinator ---------------------------------------------------------
WEATHER = "weather.home"


def wind_group(settle=0):
    return [grp("Wind", [rule(WEATHER, ">", 40, attribute="wind_speed")], settle=settle)]


def test_a_rule_can_watch_an_attribute(make):
    hass, coordinator, _ = make(wind_group(), {WEATHER: ("sunny", {"wind_speed": 10})})
    assert coordinator.is_safe

    push(hass, coordinator, WEATHER, "sunny", {"wind_speed": 55})
    assert not coordinator.is_safe
    assert "weather.home[wind_speed] 55 > 40.0" in coordinator.description

    push(hass, coordinator, WEATHER, "sunny", {"wind_speed": 20})
    assert coordinator.is_safe


def test_a_rule_on_an_attribute_ignores_the_state_text(make):
    hass, coordinator, _ = make(wind_group(), {WEATHER: "windy"})  # no attribute at all yet
    push(hass, coordinator, WEATHER, "windy", {"wind_speed": 5})
    assert coordinator.is_safe


def test_a_missing_attribute_is_unsafe_and_says_so(make):
    hass, coordinator, _ = make(wind_group(), {WEATHER: "sunny"})
    assert not coordinator.is_safe
    assert "weather.home[wind_speed] missing" in coordinator.description

    push(hass, coordinator, WEATHER, "sunny", {"wind_speed": 5})
    assert coordinator.is_safe

    push(hass, coordinator, WEATHER, "sunny", {})  # the attribute disappears
    assert not coordinator.is_safe


def test_the_initial_evaluation_reads_the_attribute(make):
    _, coordinator, _ = make(wind_group(), {WEATHER: ("sunny", {"wind_speed": 99})})
    assert not coordinator.is_safe
    assert coordinator.groups[0].rules[0].current_value == "99"


def test_a_rule_with_a_list_of_conditions(make):
    group = [grp("Sky", [rule(WEATHER, "in", "rainy, pouring, lightning")], settle=0)]
    hass, coordinator, _ = make(group, {WEATHER: "sunny"})
    assert coordinator.is_safe
    push(hass, coordinator, WEATHER, "pouring")
    assert not coordinator.is_safe
    push(hass, coordinator, WEATHER, "cloudy")
    assert coordinator.is_safe


def test_a_list_of_numbers_in_a_stored_rule_stays_text(make):
    group = [grp("Levels", [rule("sensor.level", "in", "1, 3")], settle=0)]
    hass, coordinator, _ = make(group, {"sensor.level": "2"})
    assert coordinator.is_safe
    push(hass, coordinator, "sensor.level", "3")
    assert not coordinator.is_safe


# ---- B4: grace time for unavailable entities ---------------------------------------------------------------
def grace_group(grace, settle=0):
    return [grp("g", [rule("sensor.x", ">", 5, grace=grace)], settle=settle)]


def pending_delays(timers):
    return [item["delay"] for item in timers.pending()]


def test_without_a_grace_time_unavailable_is_unsafe_at_once(make):
    hass, coordinator, _ = make(grace_group(0), {"sensor.x": "1"})
    assert coordinator.is_safe
    push(hass, coordinator, "sensor.x", "unavailable")
    assert not coordinator.is_safe


def test_an_entity_that_comes_back_within_the_grace_time_never_turns_unsafe(make):
    hass, coordinator, timers = make(grace_group(60), {"sensor.x": "1"})
    push(hass, coordinator, "sensor.x", "unavailable")
    assert coordinator.is_safe
    assert pending_delays(timers) == [60]

    push(hass, coordinator, "sensor.x", "2")
    assert coordinator.is_safe
    assert pending_delays(timers) == []  # the timer is cancelled


def test_an_entity_that_stays_unavailable_turns_unsafe_when_the_grace_time_is_over(make):
    hass, coordinator, timers = make(grace_group(60), {"sensor.x": "1"})
    push(hass, coordinator, "sensor.x", "unavailable")
    [item] = timers.pending()
    timers.fire(item)

    assert not coordinator.is_safe
    assert "sensor.x unavailable" in coordinator.description


def test_unknown_is_treated_like_unavailable(make):
    hass, coordinator, timers = make(grace_group(60), {"sensor.x": "1"})
    push(hass, coordinator, "sensor.x", "unknown")
    assert coordinator.is_safe
    timers.fire(timers.pending()[0])
    assert not coordinator.is_safe
    assert "not initialized" in coordinator.description


def test_flipping_between_unavailable_and_unknown_does_not_restart_the_grace_time(make):
    hass, coordinator, timers = make(grace_group(60), {"sensor.x": "1"})
    push(hass, coordinator, "sensor.x", "unavailable")
    push(hass, coordinator, "sensor.x", "unknown")
    push(hass, coordinator, "sensor.x", "unavailable")
    assert pending_delays(timers) == [60]


def test_the_rule_keeps_its_last_result_during_the_grace_time(make):
    """A rule that was unsafe stays unsafe, one that was safe stays safe."""
    hass, coordinator, timers = make(grace_group(60), {"sensor.x": "9"})  # unsafe
    assert not coordinator.is_safe
    push(hass, coordinator, "sensor.x", "unavailable")
    assert not coordinator.is_safe
    push(hass, coordinator, "sensor.x", "1")  # back and safe
    assert coordinator.is_safe


def test_an_entity_that_is_unavailable_at_the_start_gets_no_grace_time(make):
    """Nothing is known about it yet: the boot guard holds the group unsafe at once."""
    _, coordinator, timers = make(grace_group(60), {"sensor.x": "unavailable"})
    assert not coordinator.is_safe
    assert pending_delays(timers) == []


def test_a_grace_timer_that_fires_after_the_entity_is_back_does_nothing(make):
    hass, coordinator, timers = make(grace_group(60), {"sensor.x": "1"})
    push(hass, coordinator, "sensor.x", "unavailable")
    [item] = timers.pending()
    hass.states.set("sensor.x", "2")  # back, without the event (should not happen, but must be harmless)
    timers.fire(item)
    assert coordinator.is_safe


def test_stopping_cancels_the_grace_timer(make):
    hass, coordinator, timers = make(grace_group(60), {"sensor.x": "1"})
    push(hass, coordinator, "sensor.x", "unavailable")
    asyncio.run(coordinator.async_stop())
    assert timers.pending() == []


# ---- B11: a recovered watchdog does not evaluate "unavailable" as a value -----------------------------------
def test_a_recovered_watchdog_does_not_compare_unavailable_with_the_threshold(make):
    hass, coordinator, _ = make(
        [grp("g", [rule("sensor.x", ">", 5, watchdog=60)], settle=0)], {"sensor.x": "1"}
    )
    state = hass.states.get("sensor.x")
    state.last_reported = datetime.now(timezone.utc) - timedelta(seconds=120)
    coordinator._check_watchdogs()
    assert coordinator.groups[0].rules[0].watchdog_expired

    hass.states.set("sensor.x", "unavailable")  # reports again, but has no value
    coordinator._check_watchdogs()
    assert not coordinator.groups[0].rules[0].watchdog_expired
    assert coordinator.groups[0].rules[0].current_value != "unavailable"


# ---- E5: event on Safe/Unsafe changes ---------------------------------------------------------------------------
def changes(hass):
    return [(data["is_safe"], data["reason"]) for event, data in hass.events if event == const.EVENT_SAFETY_CHANGED]


def test_the_monitor_sends_an_event_when_it_changes_between_safe_and_unsafe(make):
    hass, coordinator, _ = make([grp("g", [rule("sensor.x", ">", 5)], settle=0)], {"sensor.x": "1"})
    assert [safe for safe, _ in changes(hass)][-1] is True

    push(hass, coordinator, "sensor.x", "9")
    assert [safe for safe, _ in changes(hass)][-2:] == [True, False]
    safe, reason = changes(hass)[-1]
    assert safe is False
    assert "sensor.x 9" in reason  # the reason is the description of the state

    push(hass, coordinator, "sensor.x", "1")
    assert [safe for safe, _ in changes(hass)][-3:] == [True, False, True]


def test_no_event_while_the_state_stays_the_same(make):
    hass, coordinator, _ = make([grp("g", [rule("sensor.x", ">", 5)], settle=0)], {"sensor.x": "1"})
    count = len(changes(hass))
    push(hass, coordinator, "sensor.x", "2")
    push(hass, coordinator, "sensor.x", "3")
    assert len(changes(hass)) == count

    # still unsafe, but for another reason: not a change between safe and unsafe
    push(hass, coordinator, "sensor.x", "9")
    count = len(changes(hass))
    push(hass, coordinator, "sensor.x", "unavailable")
    assert len(changes(hass)) == count


def test_the_first_result_after_a_start_is_sent(make):
    """A start is always unsafe (see "Behaviour at start"): automations hear about it."""
    hass, _, _ = make([grp("g", [rule("sensor.x", ">", 5)], settle=300)], {"sensor.x": "1"})
    assert changes(hass)[0][0] is False


def test_force_unsafe_sends_an_event_with_its_reason(make):
    hass, coordinator, _ = make([grp("g", [rule("sensor.x", ">", 5)], settle=0)], {"sensor.x": "1"})
    coordinator.set_force_unsafe(True)
    assert changes(hass)[-1] == (False, "UNSAFE: Force Unsafe (Maintenance Mode) active")


# ---- E5: diagnostics -------------------------------------------------------------------------------------------
def test_the_diagnostics_show_every_group_and_rule_and_can_be_serialised(make):
    _, coordinator, _ = make(
        [
            grp("Wind", [rule(WEATHER, ">", 40, attribute="wind_speed", grace=30)], settle=300),
            grp("Rain", [rule("binary_sensor.rain", "==", "on")], settle=0),
        ],
        {WEATHER: ("sunny", {"wind_speed": 12}), "binary_sensor.rain": "off"},
    )

    result = coordinator.diagnostics()
    json.dumps(result)  # no objects that cannot be downloaded

    assert result["is_safe"] is False  # the wind group is still settling
    assert [g["name"] for g in result["groups"]] == ["Wind", "Rain"]
    wind, rain = result["groups"]
    assert wind["settle_ends_at"] is not None
    assert rain["settle_ends_at"] is None
    [wind_rule] = wind["rules"]
    assert wind_rule["attribute"] == "wind_speed"
    assert wind_rule["unavailable_delay"] == 30
    assert wind_rule["current_value"] == "12"
    assert wind_rule["is_triggered"] is False


def test_the_diagnostics_download_has_the_options_and_the_state(make):
    groups = [grp("Rain", [rule("binary_sensor.rain", "==", "on")], settle=0)]
    hass, coordinator, _ = make(groups, {"binary_sensor.rain": "off"})
    hass.data = {const.DOMAIN: {"E": {const.DATA_COORDINATOR: coordinator}}}
    entry = types.SimpleNamespace(entry_id="E", options={"groups": groups})

    result = asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, entry))
    assert result["options"] == {"groups": groups}
    assert result["state"]["groups"][0]["name"] == "Rain"
    json.dumps(result)


# ---- E5: the reason as an entity ---------------------------------------------------------------------------------
def reason_sensor(coordinator):
    return sensor.SafetyReasonSensor(coordinator, types.SimpleNamespace(entry_id="E"))


def test_the_reason_sensor_shows_the_description(make):
    hass, coordinator, _ = make([grp("g", [rule("sensor.x", ">", 5)], settle=0)], {"sensor.x": "9"})
    entity = reason_sensor(coordinator)
    assert entity.native_value == coordinator.description
    assert "sensor.x 9" in entity.native_value
    assert entity.extra_state_attributes == {"description": coordinator.description}

    push(hass, coordinator, "sensor.x", "1")
    assert reason_sensor(coordinator).native_value == "SAFE: All groups report safe"


def test_a_long_reason_is_cut_to_what_a_state_may_hold():
    long_text = "UNSAFE: " + "x" * 400
    assert len(sensor.state_text(long_text)) == 255
    assert sensor.state_text(long_text).endswith("…")
    assert sensor.state_text("short") == "short"
    assert len(sensor.state_text("y" * 255)) == 255


def test_the_integration_sets_up_the_sensor_platform():
    assert "sensor" in const.PLATFORMS
