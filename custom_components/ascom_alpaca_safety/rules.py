"""Rule logic without Home Assistant imports: evaluation, checks, defaults and input choices.

Kept free of Home Assistant so that it can be unit-tested and used by the coordinator (runtime) and
the options flow (input) alike.
"""

from __future__ import annotations

import operator as op
from typing import Any, Callable, Mapping

from .const import (
    DEFAULT_WATCHDOG_TIMEOUT,
    OPERATOR_EQ,
    OPERATOR_GT,
    OPERATOR_GTE,
    OPERATOR_IN,
    OPERATOR_LT,
    OPERATOR_LTE,
    OPERATOR_NEQ,
    OPERATOR_NOT_IN,
)

OPERATOR_MAP: dict[str, Callable[[float, float], bool]] = {
    OPERATOR_GT: op.gt,
    OPERATOR_LT: op.lt,
    OPERATOR_GTE: op.ge,
    OPERATOR_LTE: op.le,
    OPERATOR_EQ: op.eq,
    OPERATOR_NEQ: op.ne,
}

NUMERIC_OPERATORS = (OPERATOR_GT, OPERATOR_LT, OPERATOR_GTE, OPERATOR_LTE)
EQUALITY_OPERATORS = (OPERATOR_EQ, OPERATOR_NEQ)
LIST_OPERATORS = (OPERATOR_IN, OPERATOR_NOT_IN)

# States of an entity that has no value (the rule counts as unsafe)
UNAVAILABLE_STATES = ("unavailable", "unknown")

# Attributes that only describe the entity or the unit of a value (name, icon, device class, "unit_of_measurement",
# and the "..._unit" attributes of a weather entity): nothing to watch there
_UNINTERESTING_ATTRIBUTES = (
    "friendly_name",
    "icon",
    "entity_picture",
    "supported_features",
    "attribution",
    "device_class",
    "state_class",
    "unit_of_measurement",
)

# Entities that only know the states "on" and "off"
ON_OFF_DOMAINS = ("binary_sensor", "switch", "input_boolean", "light")

# Entities that write their state again and again (a measurement, a forecast): only for them
# "no report for N seconds" means the data is stale. A binary sensor or a switch only reports when
# it changes, so an unchanged value is not stale.
PERIODIC_DOMAINS = ("sensor", "weather")

# The states of a weather entity (Home Assistant weather condition)
WEATHER_STATES = (
    "clear-night",
    "cloudy",
    "exceptional",
    "fog",
    "hail",
    "lightning",
    "lightning-rainy",
    "partlycloudy",
    "pouring",
    "rainy",
    "snowy",
    "snowy-rainy",
    "sunny",
    "windy",
    "windy-variant",
)


def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def is_on_off_entity(entity_id: str) -> bool:
    """The entity only reports on or off."""
    return _domain(entity_id) in ON_OFF_DOMAINS


# --- Evaluation ---------------------------------------------------------------------------------


def watched_value(
    state_value: str, attributes: Mapping[str, Any] | None, attribute: str = ""
) -> str | None:
    """The value a rule looks at: the state text, or the attribute chosen for the rule.

    None when the attribute is missing: the rule cannot be evaluated then (unsafe).
    """
    if not attribute:
        return state_value
    value = (attributes or {}).get(attribute)
    return None if value is None else str(value)


def _same(value: Any, other: Any) -> bool:
    """Equal as numbers if both are numbers, else as text without regard to case and surrounding spaces."""
    try:
        return float(value) == float(other)
    except (ValueError, TypeError):
        return str(value).strip().casefold() == str(other).strip().casefold()


def list_items(threshold: Any) -> list[str]:
    """The entries of a comma separated list ("rainy, pouring"), without empty ones."""
    return [item.strip() for item in str(threshold).split(",") if item.strip()]


def rule_triggered(operator: str, threshold: Any, state_value: str) -> bool | None:
    """Evaluate one rule against a value: True = the unsafe condition is met, False = it is not.

    None means the comparison cannot be made (for example text with ">"); the caller treats that as
    unsafe. Numbers are compared as numbers; ``==``, ``!=``, ``in`` and ``not in`` fall back to text,
    without regard to upper or lower case and surrounding spaces ("ON" and "on" are the same state).
    ``in`` and ``not in`` take a comma separated list as the threshold.
    """
    if operator in LIST_OPERATORS:
        found = any(_same(state_value, item) for item in list_items(threshold))
        return found if operator == OPERATOR_IN else not found

    try:
        value = float(state_value)
        limit = float(threshold)
    except (ValueError, TypeError):
        pass
    else:
        func = OPERATOR_MAP.get(operator)
        if func is not None:
            return func(value, limit)

    if operator in EQUALITY_OPERATORS:
        equal = _same(state_value, threshold)
        return equal if operator == OPERATOR_EQ else not equal
    return None


# --- Checks and defaults ---------------------------------------------------------------------------


def threshold_error(
    entity_id: str, operator: str, threshold: str, attribute: str = ""
) -> str | None:
    """Return a translation key when the rule can never work as intended, else None.

    A rule that cannot match is not a safe rule: it would stay "safe" forever (typo in the
    threshold) or always "unsafe" (text for a numeric comparison). Both are caught when the rule is saved.
    Only the state of a binary entity knows nothing but on and off: an attribute can hold anything.
    """
    text = str(threshold).strip()
    on_off = is_on_off_entity(entity_id) and not attribute

    if operator in LIST_OPERATORS:
        if on_off:
            return "operator_not_for_on_off"
        return None if list_items(text) else "list_empty"

    if operator in NUMERIC_OPERATORS:
        if on_off:
            return "operator_not_for_on_off"
        try:
            float(text)
        except ValueError:
            return "threshold_not_numeric"
        return None

    if on_off and text.casefold() not in ("on", "off"):
        return "threshold_not_on_off"
    return None


def default_watchdog_timeout(entity_id: str) -> int:
    """Watchdog timeout in seconds when the user did not choose one (0 = off)."""
    return DEFAULT_WATCHDOG_TIMEOUT if _domain(entity_id) in PERIODIC_DOMAINS else 0


# --- Input choices (options flow) -----------------------------------------------------------------


def threshold_choices(
    entity_id: str, attributes: dict[str, Any] | None, attribute: str = ""
) -> tuple[list[str], bool] | None:
    """The states to offer as a threshold: ``(options, free_text_allowed)``, or None for a text field.

    on / off for entities that only know these two (no free text), the condition list of a weather entity,
    and the ``options`` attribute of selects, input selects and enum sensors; the last two also allow free text.
    The value of an attribute can be anything: a text field.
    """
    if attribute:
        return None
    if is_on_off_entity(entity_id):
        return ["on", "off"], False
    if _domain(entity_id) == "weather":
        return list(WEATHER_STATES), True
    options = (attributes or {}).get("options")
    if isinstance(options, (list, tuple)) and options:
        return [str(option) for option in options], True
    return None


def group_name_error(name: str, other_names: list[str]) -> str | None:
    """A group name has to be given and must not be used by another group (not case-sensitive)."""
    name = name.strip()
    if not name:
        return "Name is required"
    if any(other.strip().casefold() == name.casefold() for other in other_names):
        return "A group with this name already exists"
    return None


def operators_for(entity_id: str, attribute: str = "") -> tuple[str, ...] | None:
    """The operators that make sense for what the rule watches, or None for all of them."""
    return EQUALITY_OPERATORS if is_on_off_entity(entity_id) and not attribute else None


def attribute_choices(attributes: Mapping[str, Any] | None, current: str = "") -> list[str]:
    """The attributes a rule can watch (sorted), plus the one the rule has now, even if it is missing.

    Attributes that every entity has (name, icon, ...) are left out. An empty list means that there
    is nothing but the state to watch.
    """
    names = {
        name
        for name in (attributes or {})
        if name not in _UNINTERESTING_ATTRIBUTES and not name.endswith("_unit")
    }
    if current:
        names.add(current)
    return sorted(names)
