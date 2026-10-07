"""Shared helpers of the safety tests."""

from ha_stubs import Event


class Timers:
    """Collects timers requested via async_call_later so tests can fire them."""

    def __init__(self):
        self.items = []
        # actions registered with async_track_time_interval (the watchdog check)
        self.intervals = []

    def call_later(self, hass, delay, cb):
        item = {"delay": delay, "cb": cb, "cancelled": False, "fired": False}
        self.items.append(item)

        def cancel():
            item["cancelled"] = True

        return cancel

    def track_interval(self, hass, cb, interval):
        self.intervals.append({"cb": cb, "interval": interval})
        return lambda: None

    def pending(self):
        return [i for i in self.items if not i["cancelled"] and not i["fired"]]

    def fire(self, item):
        item["fired"] = True
        item["cb"](None)


def rule(eid, op, thr, delay=0, watchdog=0):
    return {
        "entity_id": eid,
        "operator": op,
        "threshold": thr,
        "unsafe_delay": delay,
        "watchdog_timeout": watchdog,
    }


def grp(name, rules, logic="OR", settle=900, gid=None):
    group = {
        "group_name": name,
        "group_logic": logic,
        "settle_time": settle,
        "rules": rules,
    }
    if gid is not None:
        group["group_id"] = gid
    return group


def push(hass, coordinator, eid, value):
    hass.states.set(eid, value)
    coordinator._handle_state_change(
        Event({"entity_id": eid, "new_state": hass.states.get(eid)})
    )
