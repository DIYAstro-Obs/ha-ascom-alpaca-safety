"""SafetyCoordinator – Core logic engine for ASCOM Alpaca Safety."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from datetime import datetime, timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from .const import (
    CONF_GROUPS,
    CONF_GROUP_ID,
    CONF_GROUP_LOGIC,
    CONF_GROUP_NAME,
    CONF_GROUP_SETTLE_TIME,
    CONF_RULE_ENTITY,
    CONF_RULE_OPERATOR,
    CONF_RULE_THRESHOLD,
    CONF_RULE_UNSAFE_DELAY,
    CONF_RULE_WATCHDOG_TIMEOUT,
    CONF_RULES,
    DEFAULT_SETTLE_TIME,
    DEFAULT_UNSAFE_DELAY,
    DOMAIN,
    LOGIC_AND,
    LOGIC_OR,
    OPERATOR_GT,
    STORAGE_KEY_FORCE_UNSAFE,
    STORAGE_VERSION,
    storage_key,
)
from .rules import default_watchdog_timeout, rule_triggered

_LOGGER = logging.getLogger(__name__)

# Repairs issue for a rule whose entity does not exist (issue id: prefix + entity id)
MISSING_ENTITY_ISSUE_PREFIX = "missing_entity_"


def _missing_entity_issue_ids(hass: HomeAssistant) -> set[str]:
    """The ids of the "entity not found" issues of this integration that exist now."""
    registry = ir.async_get(hass)
    return {
        issue_id
        for (domain, issue_id) in registry.issues
        if domain == DOMAIN and issue_id.startswith(MISSING_ENTITY_ISSUE_PREFIX)
    }


@callback
def delete_missing_entity_issues(hass: HomeAssistant) -> None:
    """Remove all "entity not found" issues (the integration is removed)."""
    for issue_id in _missing_entity_issue_ids(hass):
        ir.async_delete_issue(hass, DOMAIN, issue_id)


@dataclass
class RuleState:
    """Runtime state for a single rule."""

    entity_id: str
    operator_str: str
    threshold: Any  # Can be float or str
    unsafe_delay: float
    watchdog_timeout: float

    # Runtime
    is_triggered: bool = False
    is_unsafe: bool = False  # After delay consideration
    watchdog_expired: bool = False
    entity_available: bool = False
    entity_initialized: bool = False

    # Unsafe delay tracking
    _unsafe_delay_cancel: CALLBACK_TYPE | None = None

    # Current entity value (for descriptions)
    current_value: str | None = None

    def evaluate(self, state_value: str | None) -> bool:
        """Evaluate the rule against a state value. Returns True if UNSAFE triggered."""
        self.current_value = state_value

        if state_value is None:
            return True  # unavailable => unsafe

        result = rule_triggered(self.operator_str, self.threshold, state_value)
        if result is None:
            _LOGGER.warning(
                "Cannot perform numeric comparison %s %s %s for %s",
                state_value,
                self.operator_str,
                self.threshold,
                self.entity_id,
            )
            return True  # Can't evaluate => unsafe
        return result


@dataclass
class GroupState:
    """Runtime state for a safety group."""

    name: str
    logic: str
    settle_time: float
    rules: list[RuleState] = field(default_factory=list)
    group_id: str = ""  # Stable ID (entity unique_ids), independent of list position

    # Runtime
    is_unsafe: bool = True  # Start unsafe (boot guard)
    settle_timer_start: float | None = None
    _settle_cancel: CALLBACK_TYPE | None = None
    boot_guard_complete: bool = False

    @property
    def settle_remaining(self) -> float | None:
        """Return remaining settle time in seconds, or None."""
        if self.settle_timer_start is None:
            return None
        elapsed = time.monotonic() - self.settle_timer_start
        remaining = self.settle_time - elapsed
        return max(0.0, remaining) if remaining > 0 else 0.0

    @property
    def settle_ends_at(self) -> datetime | None:
        """When the settle timer ends (UTC), or None. The time does not change while it counts down."""
        remaining = self.settle_remaining
        if remaining is None:
            return None
        return dt_util.utcnow() + timedelta(seconds=remaining)

    @property
    def description(self) -> str:
        """Build a human-readable description of the group state."""
        if not self.rules:
            return f"Group {self.name}: UNSAFE -> no rules configured"

        if not self.boot_guard_complete:
            return f"Group {self.name}: Initializing (boot guard)"

        unsafe_rules = [r for r in self.rules if r.is_unsafe]
        if not unsafe_rules:
            remaining = self.settle_remaining
            if self.is_unsafe and remaining is not None and remaining > 0:
                # A time of day, not "N seconds remaining": the text is only rewritten on events
                ends_at = dt_util.as_local(self.settle_ends_at)
                return (
                    f"Group {self.name}: Settling until "
                    f"{ends_at:%H:%M:%S}"
                )
            return f"Group {self.name}: SAFE"

        parts = []
        for r in unsafe_rules:
            if r.watchdog_expired:
                parts.append(f"{r.entity_id} watchdog expired")
            elif not r.entity_available:
                parts.append(f"{r.entity_id} unavailable")
            elif not r.entity_initialized:
                parts.append(f"{r.entity_id} not initialized")
            else:
                parts.append(
                    f"{r.entity_id} {r.current_value} "
                    f"{r.operator_str} {r.threshold}"
                )

        joiner = " AND " if self.logic == LOGIC_AND else " OR "
        return f"Group {self.name}: UNSAFE -> {joiner.join(parts)}"


class SafetyCoordinator:
    """Central safety logic engine."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry

        self._is_safe = False
        self._description = "Initializing..."
        self._force_safe = False
        self._force_unsafe = False
        self._groups: list[GroupState] = []
        self._listeners: list[CALLBACK_TYPE] = []
        self._watchdog_interval_cancel: CALLBACK_TYPE | None = None
        self._update_callbacks: list[Callable[[], None]] = []

        # Force Unsafe (maintenance mode) has to survive a restart and the reload after an options change
        self._store: Store = Store(hass, STORAGE_VERSION, storage_key(entry.entry_id))

        # Master state
        self.is_connected: bool = False

    # --- Properties ---

    @property
    def is_safe(self) -> bool:
        """Return True if system is safe."""
        return self._is_safe

    @property
    def description(self) -> str:
        """Return human-readable description of current state."""
        return self._description

    @property
    def force_unsafe(self) -> bool:
        """Return True if force-unsafe is active."""
        return self._force_unsafe

    @property
    def groups(self) -> list[GroupState]:
        """Return all group states."""
        return self._groups

    # --- Setup / Teardown ---

    async def async_start(self) -> None:
        """Start the coordinator: build groups, subscribe to entities."""
        # Before the first evaluation, so the monitor is never safe for a moment while maintenance mode is on
        stored = await self._store.async_load()
        if isinstance(stored, dict) and stored.get(STORAGE_KEY_FORCE_UNSAFE):
            self._force_unsafe = True
            _LOGGER.warning("Force Unsafe (maintenance mode) is still active, restored from storage")

        self._build_groups()
        self._subscribe_entities()
        # Initial evaluation of all current states
        self._initial_evaluate()
        self._update_missing_entity_issues()

        # Re-evaluate after HA is fully started (entities may load late)
        @callback
        def _on_ha_started(event: Event) -> None:
            pending = [
                g.name for g in self._groups if not g.boot_guard_complete
            ]
            if pending:
                _LOGGER.info(
                    "HA started — re-evaluating pending groups: %s",
                    ", ".join(pending),
                )
                self._initial_evaluate()
            self._update_missing_entity_issues()

        cancel = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED, _on_ha_started
        )
        self._listeners.append(cancel)

        _LOGGER.info("SafetyCoordinator started with %d groups", len(self._groups))

    async def async_stop(self) -> None:
        """Stop and clean up all listeners and timers."""
        for cancel in self._listeners:
            cancel()
        self._listeners.clear()

        if self._watchdog_interval_cancel:
            self._watchdog_interval_cancel()
            self._watchdog_interval_cancel = None

        for group in self._groups:
            if group._settle_cancel is not None:
                group._settle_cancel()
                group._settle_cancel = None
            for rule in group.rules:
                if rule._unsafe_delay_cancel is not None:
                    rule._unsafe_delay_cancel()
                    rule._unsafe_delay_cancel = None

        self._update_callbacks.clear()

        _LOGGER.info("SafetyCoordinator stopped")

    def _build_groups(self) -> None:
        """Build internal group/rule structure from config entry options."""
        self._groups.clear()
        groups_config: list[dict[str, Any]] = self.entry.options.get(CONF_GROUPS, [])

        for index, g_conf in enumerate(groups_config):
            group = GroupState(
                group_id=str(g_conf.get(CONF_GROUP_ID) or index),
                name=g_conf.get(CONF_GROUP_NAME, "Unnamed"),
                logic=g_conf.get(CONF_GROUP_LOGIC, LOGIC_OR),
                settle_time=float(
                    g_conf.get(CONF_GROUP_SETTLE_TIME, DEFAULT_SETTLE_TIME)
                ),
            )

            rules_config: list[dict[str, Any]] = g_conf.get(CONF_RULES, [])
            for r_conf in rules_config:
                threshold_raw = str(r_conf.get(CONF_RULE_THRESHOLD, "0")).strip()
                try:
                    # Keep as float if possible, else keep as string
                    threshold = float(threshold_raw)
                except (ValueError, TypeError):
                    threshold = threshold_raw

                entity_id = r_conf.get(CONF_RULE_ENTITY, "")
                rule = RuleState(
                    entity_id=entity_id,
                    operator_str=r_conf.get(CONF_RULE_OPERATOR, OPERATOR_GT),
                    threshold=threshold,
                    unsafe_delay=float(
                        r_conf.get(CONF_RULE_UNSAFE_DELAY, DEFAULT_UNSAFE_DELAY)
                    ),
                    watchdog_timeout=float(
                        r_conf.get(
                            CONF_RULE_WATCHDOG_TIMEOUT,
                            default_watchdog_timeout(entity_id),
                        )
                    ),
                )
                group.rules.append(rule)

            self._groups.append(group)

    def _subscribe_entities(self) -> None:
        """Subscribe to state changes for all monitored entities."""
        entity_ids: list[str] = []
        for group in self._groups:
            for rule in group.rules:
                if rule.entity_id and rule.entity_id not in entity_ids:
                    entity_ids.append(rule.entity_id)

        if entity_ids:
            self._listeners.append(
                async_track_state_change_event(
                    self.hass, entity_ids, self._handle_state_change
                )
            )
            # Interval for robust watchdog check (Option 3)
            self._watchdog_interval_cancel = async_track_time_interval(
                self.hass, self._check_watchdogs, timedelta(seconds=30)
            )

    def _initial_evaluate(self) -> None:
        """Evaluate all rules against current entity states on startup."""
        for group in self._groups:
            all_initialized = True
            for rule in group.rules:
                state = self.hass.states.get(rule.entity_id)
                if state is None or state.state in ("unavailable", "unknown"):
                    rule.entity_available = state is not None and state.state != "unavailable"
                    rule.entity_initialized = False
                    rule.is_unsafe = True
                    rule.is_triggered = True
                    all_initialized = False
                else:
                    rule.entity_available = True
                    rule.entity_initialized = True
                    triggered = rule.evaluate(state.state)
                    rule.is_triggered = triggered
                    rule.is_unsafe = triggered  # No delay on initial eval

                # Check watchdog expiry (Option 3)
                self._update_watchdog_state(rule)

            if not group.rules:
                # A group without rules monitors nothing: UNSAFE (fail-safe, like "no groups")
                group.boot_guard_complete = True
                group.is_unsafe = True
            elif all_initialized:
                # All entities available — run proper group evaluation
                self._evaluate_group(group)
            # else: some entities missing, group stays unsafe (boot guard)

        self._recalculate()

    # --- State Change Handler ---

    @callback
    def _handle_state_change(self, event: Event) -> None:
        """Handle entity state changes."""
        entity_id = event.data.get("entity_id")
        new_state: State | None = event.data.get("new_state")

        if entity_id is None:
            return

        for group in self._groups:
            for rule in group.rules:
                if rule.entity_id != entity_id:
                    continue

                # Update availability
                if new_state is None or new_state.state == "unavailable":
                    rule.entity_available = False
                    rule.entity_initialized = False
                    self._set_rule_unsafe(rule, group)
                    continue

                if new_state.state == "unknown":
                    rule.entity_initialized = False
                    self._set_rule_unsafe(rule, group)
                    continue

                rule.entity_available = True
                rule.entity_initialized = True

                # Watchdog is now handled by periodic _check_watchdogs (Option 3)

                # Evaluate rule
                triggered = rule.evaluate(new_state.state)
                rule.is_triggered = triggered

                if triggered:
                    self._handle_rule_triggered(rule, group)
                else:
                    self._handle_rule_cleared(rule, group)

        self._recalculate()

    def _handle_rule_triggered(self, rule: RuleState, group: GroupState) -> None:
        """Handle a rule that just became triggered (potential UNSAFE)."""
        if rule.is_unsafe:
            return  # Already unsafe

        if rule.unsafe_delay > 0:
            if rule._unsafe_delay_cancel is not None:
                return  # Delay already running — keep waiting, don't restart or skip it

            # Start the unsafe delay timer
            @callback
            def _delay_done(_now: Any) -> None:
                rule._unsafe_delay_cancel = None
                if rule.is_triggered:
                    self._set_rule_unsafe(rule, group)

            rule._unsafe_delay_cancel = async_call_later(
                self.hass, rule.unsafe_delay, _delay_done
            )
        else:
            # Immediate unsafe
            self._set_rule_unsafe(rule, group)

    def _handle_rule_cleared(self, rule: RuleState, group: GroupState) -> None:
        """Handle a rule that just became safe."""
        # Cancel any pending unsafe delay
        if rule._unsafe_delay_cancel is not None:
            rule._unsafe_delay_cancel()
            rule._unsafe_delay_cancel = None

        if not rule.is_unsafe:
            return  # Already safe

        rule.is_unsafe = False
        rule.watchdog_expired = False

        # Re-evaluate group
        self._evaluate_group(group)

    def _set_rule_unsafe(self, rule: RuleState, group: GroupState) -> None:
        """Mark a rule as UNSAFE and cascade to group."""
        was_safe = not rule.is_unsafe
        rule.is_unsafe = True

        # Cancel settle timer — group can't be recovering
        if group._settle_cancel is not None:
            group._settle_cancel()
            group._settle_cancel = None
            group.settle_timer_start = None

        # Check Force Safe critical logic:
        # If force_safe is active and a NEW unsafe event fires, revert to auto
        if self._force_safe and was_safe:
            _LOGGER.warning(
                "New unsafe event on %s while Force Safe active — reverting to Auto",
                rule.entity_id,
            )
            self._force_safe = False

        self._evaluate_group(group)

    # --- Group Evaluation ---

    @staticmethod
    def _rules_unsafe(group: GroupState) -> bool:
        """Return True if the group's rules are unsafe according to its logic."""
        if group.logic == LOGIC_OR:
            return any(r.is_unsafe for r in group.rules)
        return all(r.is_unsafe for r in group.rules)  # AND

    def _evaluate_group(self, group: GroupState) -> None:
        """Evaluate whether a group is UNSAFE based on its logic type."""
        if not group.rules:
            # A group without rules monitors nothing: UNSAFE (fail-safe, like "no groups")
            group.is_unsafe = True
            group.boot_guard_complete = True
            self._recalculate()
            return

        # Check if all entities have reported in
        all_init = all(r.entity_initialized for r in group.rules)
        if not all_init:
            group.is_unsafe = True
            self._recalculate()
            return

        # All entities are initialized — boot guard is done
        if not group.boot_guard_complete:
            group.boot_guard_complete = True
            _LOGGER.info("Group '%s' boot guard complete", group.name)

        if self._rules_unsafe(group):
            # Group is unsafe — cancel any settle timer
            if group._settle_cancel is not None:
                group._settle_cancel()
                group._settle_cancel = None
                group.settle_timer_start = None
            # A group turning unsafe ends an active Force Safe, whatever the cause
            if not group.is_unsafe and self._force_safe:
                _LOGGER.warning(
                    "Group '%s' became unsafe while Force Safe active — reverting to Auto",
                    group.name,
                )
                self._force_safe = False
            group.is_unsafe = True
            self._recalculate()
        else:
            # All rules safe — start settle timer if not already running
            if group.is_unsafe:
                self._start_settle_timer(group)
            self._recalculate()

    def _start_settle_timer(self, group: GroupState) -> None:
        """Start (or restart) the settle timer for a group."""
        # Cancel existing
        if group._settle_cancel is not None:
            group._settle_cancel()
            group._settle_cancel = None

        # settle_time of 0 = immediate transition, no timer needed
        if group.settle_time <= 0:
            group.settle_timer_start = None
            group.is_unsafe = False
            group.boot_guard_complete = True
            _LOGGER.info("Group '%s' settled immediately (settle_time=0)", group.name)
            return

        group.settle_timer_start = time.monotonic()

        @callback
        def _settle_done(_now: Any) -> None:
            group._settle_cancel = None
            group.settle_timer_start = None
            group.is_unsafe = False
            group.boot_guard_complete = True
            _LOGGER.info("Group '%s' settle timer completed — now SAFE", group.name)
            self._recalculate()

        group._settle_cancel = async_call_later(
            self.hass, group.settle_time, _settle_done
        )
        _LOGGER.debug(
            "Settle timer started for group '%s' (%ss)", group.name, group.settle_time
        )

    # --- Watchdog (Option 3) ---

    # @callback: Home Assistant runs a plain function in a worker thread, but this one changes state,
    # starts timers and writes entity states, which all have to happen in the event loop.
    @callback
    def _check_watchdogs(self, _now: Any = None) -> None:
        """Periodic check for watchdog expiration on all rules."""
        any_changed = False
        for group in self._groups:
            for rule in group.rules:
                if self._update_watchdog_state(rule):
                    any_changed = True
                    # If just recovered, re-evaluate rule based on current state
                    if not rule.watchdog_expired:
                        state = self.hass.states.get(rule.entity_id)
                        if state:
                            triggered = rule.evaluate(state.state)
                            rule.is_triggered = triggered
                            if triggered:
                                self._handle_rule_triggered(rule, group)
                            else:
                                self._handle_rule_cleared(rule, group)
                    self._evaluate_group(group)
        
        if any_changed:
            self._recalculate()

        self._update_missing_entity_issues()

    @callback
    def _update_missing_entity_issues(self) -> None:
        """Show a Repairs issue for every rule whose entity does not exist (or is disabled).

        A registered, enabled entity always has a state (at worst "unavailable"), so "no state" means
        the entity is gone. Only checked once HA is running: entities of slow integrations appear
        during startup. Issues of rules that are fixed or deleted are removed again.
        """
        if not self.hass.is_running:
            return

        missing: dict[str, list[str]] = {}
        for group in self._groups:
            for rule in group.rules:
                if rule.entity_id and self.hass.states.get(rule.entity_id) is None:
                    names = missing.setdefault(rule.entity_id, [])
                    if group.name not in names:
                        names.append(group.name)

        wanted = {f"{MISSING_ENTITY_ISSUE_PREFIX}{eid}" for eid in missing}
        for issue_id in _missing_entity_issue_ids(self.hass) - wanted:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        for entity_id, group_names in missing.items():
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"{MISSING_ENTITY_ISSUE_PREFIX}{entity_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="missing_entity",
                translation_placeholders={
                    "entity_id": entity_id,
                    "groups": ", ".join(group_names),
                },
            )

    def _update_watchdog_state(self, rule: RuleState) -> bool:
        """Update a rule's watchdog state based on HA's last_reported timestamp.
        Returns True if the expired state changed.
        """
        if rule.watchdog_timeout <= 0:
            if rule.watchdog_expired:
                rule.watchdog_expired = False
                return True
            return False

        state = self.hass.states.get(rule.entity_id)
        if not state:
            return False

        # Use last_reported if available (HA 2023.6+), fallback to last_updated
        last_activity = getattr(state, "last_reported", state.last_updated)
        if last_activity is None:
            return False

        age = (dt_util.utcnow() - last_activity).total_seconds()
        is_expired = age > rule.watchdog_timeout

        if is_expired != rule.watchdog_expired:
            rule.watchdog_expired = is_expired
            if is_expired:
                _LOGGER.warning(
                    "Watchdog expired for %s (no update for %.1fs, timeout %.1fs)",
                    rule.entity_id, age, rule.watchdog_timeout
                )
                # When watchdog expires, we immediately force the rule to triggered/unsafe
                rule.is_triggered = True
                rule.is_unsafe = True
            else:
                _LOGGER.info("Watchdog recovered for %s", rule.entity_id)
                # We don't automatically set is_triggered=False here, 
                # a state change will handle the real evaluation.
            return True
        return False

    # --- Master Recalculation ---

    @callback
    def _recalculate(self) -> None:
        """Recalculate the master safety state."""
        # Force Unsafe override
        if self._force_unsafe:
            self._is_safe = False
            self._description = "UNSAFE: Force Unsafe (Maintenance Mode) active"
            self._notify_listeners()
            return

        # Check boot guard
        if self._groups and not all(g.boot_guard_complete for g in self._groups):
            self._is_safe = False
            pending = [g.name for g in self._groups if not g.boot_guard_complete]
            self._description = (
                f"UNSAFE: System Initializing — "
                f"waiting for groups: {', '.join(pending)}"
            )
            self._notify_listeners()
            return

        # Check if no groups configured — fail-safe: UNSAFE by default
        if not self._groups:
            self._is_safe = False
            self._description = "UNSAFE: No safety groups configured"
            self._notify_listeners()
            return

        # Normal evaluation. Force Safe only bypassed the settle timers of groups
        # that are not actually unsafe (see trigger_force_safe), so group state
        # alone decides here.
        unsafe_groups = [g for g in self._groups if g.is_unsafe]

        if not unsafe_groups:
            self._is_safe = True
            if self._force_safe:
                self._description = "SAFE: Force Safe override active (timers bypassed)"
            else:
                self._description = "SAFE: All groups report safe"
        else:
            self._is_safe = False
            descriptions = [g.description for g in unsafe_groups]
            self._description = "UNSAFE: " + " | ".join(descriptions)

        self._notify_listeners()

    # --- Override Controls ---

    @callback
    def set_force_unsafe(self, active: bool) -> None:
        """Enable or disable Force Unsafe (Maintenance Mode). The state is stored: it survives restarts."""
        self._force_unsafe = active
        if active:
            self._force_safe = False
        self._store.async_delay_save(self._data_to_store, 0)
        _LOGGER.info("Force Unsafe set to %s", active)
        self._recalculate()

    def _data_to_store(self) -> dict[str, Any]:
        """Data written to storage."""
        return {STORAGE_KEY_FORCE_UNSAFE: self._force_unsafe}

    @callback
    def trigger_force_safe(self) -> None:
        """Trigger a one-time Force Safe bypass."""
        if self._force_unsafe:
            _LOGGER.warning("Cannot Force Safe while Force Unsafe is active")
            return

        self._force_safe = True
        _LOGGER.info("Force Safe activated")

        # Cancel all settle timers — groups that are really safe go safe immediately.
        # Groups with missing data, without rules or with unsafe rules stay unsafe: Force Safe only
        # skips waiting, it never overrides an actual unsafe condition.
        for group in self._groups:
            if group._settle_cancel is not None:
                group._settle_cancel()
                group._settle_cancel = None
                group.settle_timer_start = None
            if (
                not group.rules
                or not all(r.entity_initialized for r in group.rules)
                or self._rules_unsafe(group)
            ):
                continue
            group.is_unsafe = False
            group.boot_guard_complete = True

        self._recalculate()

    # --- Listener Management ---

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Add a listener callback. Returns a function to remove it."""
        self._update_callbacks.append(cb)

        def _remove():
            if cb in self._update_callbacks:
                self._update_callbacks.remove(cb)

        return _remove

    @callback
    def _notify_listeners(self) -> None:
        """Notify all listeners of state change."""
        for cb in self._update_callbacks:
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in coordinator listener callback")
