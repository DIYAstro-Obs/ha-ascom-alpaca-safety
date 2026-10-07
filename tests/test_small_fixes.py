"""Small fixes: re-evaluation at the start of HA, the watchdog description and the watchdog interval."""

from datetime import datetime, timedelta, timezone

import pytest

from helpers import grp, push, rule


def pending_delays(timers):
    return [item["delay"] for item in timers.pending()]


def ha_started(hass):
    [callback] = [cb for event, cb in hass.listeners if event == "homeassistant_started"]
    return callback


# ---- B5: at the start of HA only the groups that still wait are evaluated again ---------------------------------
def test_ha_started_leaves_a_group_that_is_done_alone(make):
    groups = [
        grp("A", [rule("sensor.a", ">", 5, delay=60)], settle=0, gid="a"),
        grp("B", [rule("sensor.b", ">", 5)], settle=0, gid="b"),
    ]
    hass, coordinator, timers = make(groups, {"sensor.a": "1"}, running=False)  # sensor.b is not there yet
    push(hass, coordinator, "sensor.a", "9")  # starts the unsafe delay of group A
    assert pending_delays(timers) == [60]

    hass.states.set("sensor.b", "1")  # appears late
    ha_started(hass)(None)

    group_a, group_b = coordinator.groups
    assert not group_a.rules[0].is_unsafe  # the delay is not skipped ...
    assert pending_delays(timers) == [60]  # ... and its timer still runs
    assert group_b.boot_guard_complete  # the group that waited is evaluated now
    assert coordinator.is_safe


def test_a_group_that_waits_starts_from_scratch_without_old_timers(make):
    groups = [
        grp(
            "A",
            [rule("sensor.x", ">", 5, delay=60), rule("sensor.y", ">", 5)],
            settle=0,
        )
    ]
    hass, coordinator, timers = make(groups, {"sensor.x": "1"}, running=False)  # sensor.y is missing
    push(hass, coordinator, "sensor.x", "9")
    assert pending_delays(timers) == [60]

    hass.states.set("sensor.y", "1")
    ha_started(hass)(None)

    assert pending_delays(timers) == []  # the old unsafe delay does not run on
    assert coordinator.groups[0].rules[0].is_unsafe  # the evaluation at the start has no delay


def test_ha_started_without_waiting_groups_changes_nothing(make):
    hass, coordinator, timers = make(
        [grp("A", [rule("sensor.a", ">", 5, delay=60)], settle=0)], {"sensor.a": "1"}, running=False
    )
    push(hass, coordinator, "sensor.a", "9")
    ha_started(hass)(None)
    assert pending_delays(timers) == [60]
    assert coordinator.is_safe


# ---- B9: an entity that reports again is not "watchdog expired" ----------------------------------------------------
def expire(hass, coordinator, eid):
    hass.states.get(eid).last_reported = datetime.now(timezone.utc) - timedelta(seconds=120)
    coordinator._check_watchdogs()


def test_a_report_ends_the_watchdog_expiry_in_the_description(make):
    hass, coordinator, _ = make(
        [grp("g", [rule("sensor.x", ">", 5, watchdog=60)], settle=0)], {"sensor.x": "9"}
    )
    expire(hass, coordinator, "sensor.x")
    assert "watchdog expired" in coordinator.description

    push(hass, coordinator, "sensor.x", "9")  # reports again, the value is still too high
    assert not coordinator.groups[0].rules[0].watchdog_expired
    assert "watchdog expired" not in coordinator.description
    assert "sensor.x 9" in coordinator.description
    assert not coordinator.is_safe


def test_a_report_with_a_safe_value_after_the_expiry_makes_the_group_safe(make):
    hass, coordinator, _ = make(
        [grp("g", [rule("sensor.x", ">", 5, watchdog=60)], settle=0)], {"sensor.x": "1"}
    )
    expire(hass, coordinator, "sensor.x")
    assert not coordinator.is_safe

    push(hass, coordinator, "sensor.x", "1")
    assert coordinator.is_safe


def test_an_expired_entity_that_does_not_report_stays_expired(make):
    hass, coordinator, _ = make(
        [grp("g", [rule("sensor.x", ">", 5, watchdog=60)], settle=0)], {"sensor.x": "1"}
    )
    expire(hass, coordinator, "sensor.x")
    expire(hass, coordinator, "sensor.x")
    assert coordinator.groups[0].rules[0].watchdog_expired
    assert "watchdog expired" in coordinator.description


# ---- B10: a short timeout is checked often enough ------------------------------------------------------------------
@pytest.mark.parametrize(
    "timeouts, seconds",
    [
        ([], 30),  # no watchdog at all
        ([300], 30),
        ([60], 30),
        ([40], 20),
        ([10], 5),
        ([300, 10], 5),  # the shortest counts
        ([1], 1),  # not more often than once a second
        ([0.5], 1),
    ],
)
def test_the_watchdog_interval_follows_the_shortest_timeout(make, timeouts, seconds):
    rules = [rule(f"sensor.s{i}", ">", 5, watchdog=timeout) for i, timeout in enumerate(timeouts)]
    rules.append(rule("sensor.plain", ">", 5))  # without a watchdog
    initial = {rule_["entity_id"]: "1" for rule_ in rules}
    _, _, timers = make([grp("g", rules)], initial)

    [interval] = timers.intervals
    assert interval["interval"] == timedelta(seconds=seconds)
