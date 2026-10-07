"""Tests for the safety logic: Force Safe, unsafe delay and stable group IDs."""

import asyncio
import importlib
import types

import pytest

from ha_stubs import Event, FakeHass
from helpers import Timers, grp, push, rule

coordinator_mod = importlib.import_module("ascom_alpaca_safety.coordinator")


# ---- Force Safe -------------------------------------------------------------


def test_force_safe_does_not_mask_active_unsafe_rule(make):
    hass, c, t = make(
        [grp("Rain", [rule("binary_sensor.rain", "==", "on")])],
        {"binary_sensor.rain": "on"},
    )
    assert not c.is_safe
    c.trigger_force_safe()
    assert not c.is_safe, c.description


def test_force_safe_skips_settle_timer(make):
    hass, c, t = make(
        [grp("Rain", [rule("binary_sensor.rain", "==", "on")])],
        {"binary_sensor.rain": "off"},
    )
    assert not c.is_safe and len(t.pending()) == 1  # settling
    c.trigger_force_safe()
    assert c.is_safe
    assert "Force Safe" in c.description
    assert t.pending() == []


def test_force_safe_ends_when_group_turns_unsafe(make):
    hass, c, t = make(
        [grp("Rain", [rule("binary_sensor.rain", "==", "on")])],
        {"binary_sensor.rain": "off"},
    )
    c.trigger_force_safe()
    assert c.is_safe
    push(hass, c, "binary_sensor.rain", "on")
    assert not c.is_safe
    assert c._force_safe is False


def test_force_safe_keeps_boot_guard_for_unavailable_entity(make):
    hass, c, t = make(
        [grp("Rain", [rule("binary_sensor.rain", "==", "on")])],
        {"binary_sensor.rain": "unavailable"},
    )
    assert not c.is_safe
    c.trigger_force_safe()
    assert not c.is_safe, c.description


def test_force_safe_and_group_with_single_unsafe_rule(make):
    hass, c, t = make(
        [
            grp(
                "Both",
                [
                    rule("binary_sensor.a", "==", "on"),
                    rule("binary_sensor.b", "==", "on"),
                ],
                logic="AND",
            )
        ],
        {"binary_sensor.a": "on", "binary_sensor.b": "off"},
    )
    assert not c.is_safe  # settling, group is safe by AND logic
    c.trigger_force_safe()
    assert c.is_safe, c.description


def test_force_safe_only_bypasses_the_safe_group(make):
    hass, c, t = make(
        [
            grp("Rain", [rule("binary_sensor.rain", "==", "on")]),
            grp("Wind", [rule("sensor.wind", ">", "10")]),
        ],
        {"binary_sensor.rain": "off", "sensor.wind": "25"},
    )
    c.trigger_force_safe()
    assert not c.is_safe  # the wind group is really unsafe
    assert c.groups[0].is_unsafe is False
    assert c.groups[1].is_unsafe is True


# ---- Unsafe delay -----------------------------------------------------------


def test_unsafe_delay_not_bypassed_by_repeated_updates(make):
    hass, c, t = make(
        [grp("Wind", [rule("sensor.wind", ">", "10", delay=60)], settle=0)],
        {"sensor.wind": "5"},
    )
    assert c.is_safe
    push(hass, c, "sensor.wind", "12")
    assert c.is_safe and len(t.pending()) == 1
    push(hass, c, "sensor.wind", "13")
    assert c.is_safe, "second update must not skip the delay"
    assert len(t.pending()) == 1, "timer must not be restarted"
    t.fire(t.pending()[0])
    assert not c.is_safe


def test_unsafe_delay_cancelled_when_rule_clears(make):
    hass, c, t = make(
        [grp("Wind", [rule("sensor.wind", ">", "10", delay=60)], settle=0)],
        {"sensor.wind": "5"},
    )
    push(hass, c, "sensor.wind", "12")
    push(hass, c, "sensor.wind", "4")
    assert t.pending() == []
    assert c.is_safe


def test_no_delay_means_immediate_unsafe(make):
    hass, c, t = make(
        [grp("Wind", [rule("sensor.wind", ">", "10", delay=0)], settle=0)],
        {"sensor.wind": "5"},
    )
    push(hass, c, "sensor.wind", "12")
    assert not c.is_safe


# ---- Stable group IDs -------------------------------------------------------


def test_coordinator_group_ids(make):
    hass, c, t = make(
        [grp("A", [], gid="abc"), grp("B", []), grp("C", [], gid="zzz")], {}
    )
    assert [g.group_id for g in c.groups] == ["abc", "1", "zzz"]


def test_migration_assigns_position_ids_once():
    package = importlib.import_module("ascom_alpaca_safety")
    entry = types.SimpleNamespace(
        options={
            "groups": [
                {"group_name": "A"},
                {"group_name": "B", "group_id": "x"},
                {"group_name": "C"},
            ]
        }
    )
    calls = []

    def update(config_entry, options):
        calls.append(options)
        config_entry.options = options

    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_update_entry=update)
    )
    package._async_migrate_group_ids(hass, entry)
    assert [g["group_id"] for g in entry.options["groups"]] == ["0", "x", "2"]
    package._async_migrate_group_ids(hass, entry)
    assert len(calls) == 1, "second run must be a no-op"


def test_group_sensor_follows_group_not_position(make):
    binary_sensor = importlib.import_module("ascom_alpaca_safety.binary_sensor")
    entry = types.SimpleNamespace(entry_id="E", options={})

    hass, c1, t = make([grp("A", [], gid="a"), grp("B", [], gid="b")], {})
    sensor_b = binary_sensor.SafetyGroupSensor(c1, entry, "b", "B")
    assert sensor_b._group_state.name == "B"
    assert sensor_b._attr_unique_id == "E_group_b"

    # Group A deleted: B moves to position 0 but keeps its sensor identity
    hass, c2, t = make([grp("B", [], gid="b")], {})
    sensor_b2 = binary_sensor.SafetyGroupSensor(c2, entry, "b", "B")
    assert sensor_b2._group_state.name == "B"
    assert sensor_b2._attr_unique_id == "E_group_b"
    assert binary_sensor.SafetyGroupSensor(c2, entry, "a", "A").available is False


def test_remove_stale_group_sensors(monkeypatch):
    binary_sensor = importlib.import_module("ascom_alpaca_safety.binary_sensor")
    ns = types.SimpleNamespace

    class Registry:
        removed = []

        def async_remove(self, entity_id):
            self.removed.append(entity_id)

    registry = Registry()
    entries = [
        ns(domain="binary_sensor", unique_id="E_master_safe", entity_id="binary_sensor.master"),
        ns(domain="binary_sensor", unique_id="E_group_a", entity_id="binary_sensor.a"),
        ns(domain="binary_sensor", unique_id="E_group_old", entity_id="binary_sensor.old"),
        ns(domain="switch", unique_id="E_group_old", entity_id="switch.other_domain"),
    ]
    monkeypatch.setattr(binary_sensor.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(
        binary_sensor.er, "async_entries_for_config_entry", lambda reg, entry_id: entries
    )
    binary_sensor._remove_stale_group_sensors(object(), ns(entry_id="E"), {"a"})
    assert registry.removed == ["binary_sensor.old"]
