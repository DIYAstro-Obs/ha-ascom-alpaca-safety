"""The Manual Safe override: the monitor reports SAFE whatever the rules say, for N hours."""

import asyncio
import importlib
import json
import types
from datetime import datetime, timedelta, timezone

import pytest

from ha_stubs import FakeStore
from helpers import grp, push, rule

const = importlib.import_module("ascom_alpaca_safety.const")
integration = importlib.import_module("ascom_alpaca_safety")
binary_sensor = importlib.import_module("ascom_alpaca_safety.binary_sensor")
switch = importlib.import_module("ascom_alpaca_safety.switch")
number = importlib.import_module("ascom_alpaca_safety.number")
button = importlib.import_module("ascom_alpaca_safety.button")

GROUPS = [grp("g", [rule("sensor.x", ">", 5)], settle=0)]
STORE_KEY = const.storage_key("E")
HOURS = 3600


def pending_delays(timers):
    return [item["delay"] for item in timers.pending()]


def changes(hass):
    return [(data["is_safe"], data["reason"]) for event, data in hass.events if event == const.EVENT_SAFETY_CHANGED]


# ---- the override reports SAFE whatever the rules say ----------------------------------------------------------
def test_it_makes_an_unsafe_monitor_safe_and_the_groups_keep_telling_the_truth(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    assert not coordinator.is_safe

    coordinator.set_manual_safe(True)

    assert coordinator.is_safe
    assert "MANUAL OVERRIDE" in coordinator.description
    assert coordinator.groups[0].is_unsafe  # the group still says what it sees
    assert integration._dispatch_alpaca_action(coordinator, "issafe") == {"Value": True}


@pytest.mark.parametrize(
    "groups, initial",
    [
        (GROUPS, {"sensor.x": "unavailable"}),  # a failed sensor: the very reason for the override
        (GROUPS, {}),  # an entity that does not exist
        ([grp("empty", [])], {}),  # a group without rules
        ([], {}),  # no group at all
        ([grp("g", [rule("sensor.x", ">", 5)], settle=300)], {"sensor.x": "1"}),  # still settling
    ],
)
def test_it_works_whatever_the_reason_for_unsafe(make, groups, initial):
    _, coordinator, _ = make(groups, initial)
    assert not coordinator.is_safe
    coordinator.set_manual_safe(True)
    assert coordinator.is_safe


def test_switching_it_off_brings_the_real_state_back(make):
    _, coordinator, timers = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe(True)
    coordinator.set_manual_safe(False)

    assert not coordinator.manual_safe
    assert not coordinator.is_safe
    assert "sensor.x 9" in coordinator.description
    assert coordinator.manual_safe_until is None
    assert pending_delays(timers) == []  # the timer is gone


def test_switching_it_off_when_it_is_off_does_nothing(make):
    hass, coordinator, _ = make(GROUPS, {"sensor.x": "1"})
    count = len(changes(hass))
    coordinator.set_manual_safe(False)
    assert len(changes(hass)) == count


def test_the_real_state_is_followed_in_the_background(make):
    """While the override runs, the rules keep being evaluated: when it ends, the truth shows at once."""
    hass, coordinator, timers = make(GROUPS, {"sensor.x": "1"})
    coordinator.set_manual_safe(True)
    push(hass, coordinator, "sensor.x", "9")
    assert coordinator.is_safe  # the override

    timers.fire(timers.pending()[0])  # its time is over
    assert not coordinator.is_safe
    assert "sensor.x 9" in coordinator.description


# ---- the end ---------------------------------------------------------------------------------------------------------
def test_the_default_is_twelve_hours_and_it_ends_by_itself(make):
    _, coordinator, timers = make(GROUPS, {"sensor.x": "9"})
    assert coordinator.manual_safe_hours == 12
    coordinator.set_manual_safe(True)

    [delay] = pending_delays(timers)
    assert delay == pytest.approx(12 * HOURS, abs=5)
    assert abs((coordinator.manual_safe_until - datetime.now(timezone.utc)).total_seconds() - 12 * HOURS) < 5

    timers.fire(timers.pending()[0])
    assert not coordinator.manual_safe
    assert not coordinator.is_safe
    assert coordinator.manual_safe_until is None


def test_zero_hours_means_until_it_is_switched_off(make):
    _, coordinator, timers = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe_hours(0)
    coordinator.set_manual_safe(True)

    assert coordinator.is_safe
    assert coordinator.manual_safe_until is None
    assert pending_delays(timers) == []  # no timer
    assert "until it is switched off" in coordinator.description


def test_the_duration_is_limited(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe_hours(1000)
    assert coordinator.manual_safe_hours == const.MAX_MANUAL_SAFE_HOURS
    coordinator.set_manual_safe_hours(-5)
    assert coordinator.manual_safe_hours == 0
    coordinator.set_manual_safe_hours(2.5)
    assert coordinator.manual_safe_hours == 2.5


def test_a_new_duration_applies_to_the_next_override_only(make):
    _, coordinator, timers = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe(True)
    end = coordinator.manual_safe_until

    coordinator.set_manual_safe_hours(1)
    assert coordinator.manual_safe_until == end  # the running one keeps its end
    assert pending_delays(timers) == [pytest.approx(12 * HOURS, abs=5)]

    coordinator.set_manual_safe(False)
    coordinator.set_manual_safe(True)
    assert pending_delays(timers) == [pytest.approx(1 * HOURS, abs=5)]


def test_switching_it_on_again_renews_the_end(make):
    _, coordinator, timers = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe_hours(1)
    coordinator.set_manual_safe(True)
    coordinator.set_manual_safe_hours(5)
    coordinator.set_manual_safe(True)
    assert pending_delays(timers) == [pytest.approx(5 * HOURS, abs=5)]  # one timer, the new end


def test_stopping_the_coordinator_cancels_the_timer(make):
    _, coordinator, timers = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe(True)
    asyncio.run(coordinator.async_stop())
    assert timers.pending() == []


# ---- Force Unsafe wins ---------------------------------------------------------------------------------------------------
def test_force_unsafe_ends_the_override(make):
    _, coordinator, timers = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe(True)
    coordinator.set_force_unsafe(True)

    assert not coordinator.manual_safe
    assert not coordinator.is_safe
    assert "Force Unsafe" in coordinator.description
    assert pending_delays(timers) == []

    coordinator.set_force_unsafe(False)
    assert not coordinator.manual_safe  # it does not come back


def test_the_override_cannot_be_switched_on_during_force_unsafe(make):
    _, coordinator, timers = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_force_unsafe(True)
    updates = []
    coordinator.async_add_listener(lambda: updates.append(1))

    coordinator.set_manual_safe(True)

    assert not coordinator.manual_safe
    assert not coordinator.is_safe
    assert updates  # the switch is told to show "off" again
    assert pending_delays(timers) == []


# ---- it survives a restart and a reload -----------------------------------------------------------------------------------
def test_a_running_override_survives_a_restart_with_its_end(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe_hours(3)
    coordinator.set_manual_safe(True)
    end = coordinator.manual_safe_until

    _, restarted, timers = make(GROUPS, {"sensor.x": "9"})  # a new coordinator for the same entry
    assert restarted.manual_safe
    assert restarted.manual_safe_until == end
    assert restarted.manual_safe_hours == 3
    assert restarted.is_safe  # at once, before the rules have been looked at
    assert "MANUAL OVERRIDE" in restarted.description
    # the timer of the new coordinator (the fixture keeps the timer of the first one in the list too)
    assert pending_delays(timers)[-1] == pytest.approx(3 * HOURS, abs=5)


def test_an_override_without_an_end_survives_a_restart(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe_hours(0)
    coordinator.set_manual_safe(True)

    _, restarted, timers = make(GROUPS, {"sensor.x": "9"})
    assert restarted.manual_safe
    assert restarted.manual_safe_until is None
    assert pending_delays(timers) == []


def test_an_end_that_passed_while_home_assistant_was_off_is_dropped(make):
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    FakeStore.saved[STORE_KEY] = {"manual_safe": True, "manual_safe_until": past, "manual_safe_hours": 4}

    _, coordinator, timers = make(GROUPS, {"sensor.x": "9"})
    assert not coordinator.manual_safe
    assert not coordinator.is_safe
    assert coordinator.manual_safe_hours == 4  # the setting is kept
    assert FakeStore.saved[STORE_KEY]["manual_safe"] is False  # and the stored override is cleaned up
    assert pending_delays(timers) == []


def test_an_end_that_cannot_be_read_is_dropped(make):
    """Not knowing when it ends must not leave the monitor blind: fail-safe."""
    FakeStore.saved[STORE_KEY] = {"manual_safe": True, "manual_safe_until": "tomorrow, maybe"}
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    assert not coordinator.manual_safe
    assert not coordinator.is_safe
    assert FakeStore.saved[STORE_KEY]["manual_safe"] is False


def test_storage_written_before_the_override_existed_still_loads(make):
    FakeStore.saved[STORE_KEY] = {"force_unsafe": True}
    _, coordinator, _ = make(GROUPS, {"sensor.x": "1"})
    assert coordinator.force_unsafe
    assert not coordinator.manual_safe
    assert coordinator.manual_safe_hours == const.DEFAULT_MANUAL_SAFE_HOURS


@pytest.mark.parametrize("hours", ["many", None, -3, 100000])
def test_a_bad_stored_duration_falls_back_or_is_limited(make, hours):
    FakeStore.saved[STORE_KEY] = {"manual_safe_hours": hours}
    _, coordinator, _ = make(GROUPS, {"sensor.x": "1"})
    assert 0 <= coordinator.manual_safe_hours <= const.MAX_MANUAL_SAFE_HOURS


def test_switching_it_off_is_stored(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe(True)
    assert FakeStore.saved[STORE_KEY]["manual_safe"] is True
    coordinator.set_manual_safe(False)
    assert FakeStore.saved[STORE_KEY]["manual_safe"] is False

    _, restarted, _ = make(GROUPS, {"sensor.x": "9"})
    assert not restarted.manual_safe


def test_the_end_of_the_override_is_stored_too(make):
    _, coordinator, timers = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe(True)
    timers.fire(timers.pending()[0])
    assert FakeStore.saved[STORE_KEY]["manual_safe"] is False


# ---- what automations and the dashboard see ----------------------------------------------------------------------------
def test_the_event_tells_that_the_override_made_it_safe(make):
    hass, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe(True)
    safe, reason = changes(hass)[-1]
    assert safe is True
    assert "MANUAL OVERRIDE" in reason

    coordinator.set_manual_safe(False)
    assert changes(hass)[-1][0] is False


def master_attributes(coordinator):
    sensor = binary_sensor.SafetyMasterSensor(coordinator, types.SimpleNamespace(entry_id="E"))
    return sensor.extra_state_attributes


def test_the_master_sensor_shows_the_override(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    assert "override" not in master_attributes(coordinator)

    coordinator.set_manual_safe(True)
    attributes = master_attributes(coordinator)
    assert attributes["override"] == "manual_safe"
    assert datetime.fromisoformat(attributes["override_until"]) > datetime.now(timezone.utc)

    coordinator.set_manual_safe_hours(0)
    coordinator.set_manual_safe(True)
    assert master_attributes(coordinator)["override_until"] == "never"


def test_the_diagnostics_show_the_override(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    coordinator.set_manual_safe(True)
    result = coordinator.diagnostics()
    json.dumps(result)
    assert result["manual_safe"] is True
    assert result["manual_safe_until"] is not None
    assert result["manual_safe_hours"] == 12


# ---- the entities --------------------------------------------------------------------------------------------------------
ENTRY = types.SimpleNamespace(entry_id="E")


def test_the_switch_follows_the_override(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    entity = switch.ManualSafeSwitch(coordinator, ENTRY)
    assert entity.is_on is False
    assert entity.extra_state_attributes == {}

    asyncio.run(entity.async_turn_on())
    assert entity.is_on is True
    assert coordinator.is_safe
    assert "until" in entity.extra_state_attributes

    asyncio.run(entity.async_turn_off())
    assert entity.is_on is False
    assert not coordinator.is_safe


def test_the_duration_number(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    entity = number.ManualSafeDurationNumber(coordinator, ENTRY)

    assert entity.native_value == 12
    assert (entity._attr_native_min_value, entity._attr_native_max_value) == (0, 168)
    assert entity._attr_native_unit_of_measurement == "h"

    asyncio.run(entity.async_set_native_value(6))
    assert coordinator.manual_safe_hours == 6
    assert entity.native_value == 6


def test_the_entities_have_their_own_unique_ids(make):
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    ids = {
        switch.ManualSafeSwitch(coordinator, ENTRY)._attr_unique_id,
        switch.ForceUnsafeSwitch(coordinator, ENTRY)._attr_unique_id,
        number.ManualSafeDurationNumber(coordinator, ENTRY)._attr_unique_id,
        button.ForceSafeButton(coordinator, ENTRY)._attr_unique_id,
    }
    assert len(ids) == 4


def test_the_settle_button_has_a_name_that_says_what_it_does(make):
    """"Force Safe" sounded like a mode and was mixed up with the Manual Safe override."""
    _, coordinator, _ = make(GROUPS, {"sensor.x": "9"})
    assert button.ForceSafeButton(coordinator, ENTRY)._attr_name == "Skip Settle Time"


def test_the_integration_sets_up_the_number_platform():
    assert "number" in const.PLATFORMS
