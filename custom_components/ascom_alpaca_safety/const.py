"""Constants for the ASCOM Alpaca Safety integration."""

DOMAIN = "ascom_alpaca_safety"
INTEGRATION_NAME = "ASCOM Alpaca Safety"

# --- Server Connection ---
ALPACA_SERVER_API_KEY = "ascom_alpaca_server_api"  # hass.data key for the Server's API
ALPACA_SERVER_COMPONENT = "ascom_alpaca_server"  # Server's actual HA component domain

# --- Timer Defaults (seconds) ---
DEFAULT_SETTLE_TIME = 300  # 5 minutes (per group, 0 = no waiting)
DEFAULT_WATCHDOG_TIMEOUT = 300  # 5 minutes, for entities that report regularly (see rules.py)
DEFAULT_UNSAFE_DELAY = 0  # no delay by default
WATCHDOG_CHECK_INTERVAL = 30  # seconds between two watchdog checks (shorter for a short timeout)
DEFAULT_MANUAL_SAFE_HOURS = 12  # how long the Manual Safe override lasts (0 = until it is switched off)
MAX_MANUAL_SAFE_HOURS = 168  # one week

# --- Configuration Keys ---
CONF_GROUPS = "groups"
CONF_GROUP_ID = "group_id"
CONF_GROUP_NAME = "group_name"
CONF_GROUP_LOGIC = "group_logic"
CONF_GROUP_SETTLE_TIME = "settle_time"
CONF_RULES = "rules"
CONF_RULE_ENTITY = "entity_id"
CONF_RULE_OPERATOR = "operator"
CONF_RULE_THRESHOLD = "threshold"
CONF_RULE_UNSAFE_DELAY = "unsafe_delay"
CONF_RULE_WATCHDOG_TIMEOUT = "watchdog_timeout"
CONF_RULE_ATTRIBUTE = "attribute"  # empty: the rule watches the state of the entity
CONF_RULE_UNAVAILABLE_DELAY = "unavailable_delay"  # seconds an entity may be unavailable before it counts

# value of the "what to watch" choice that stands for the state of the entity (not an attribute name)
SOURCE_STATE = "__state__"

# --- Logic Types ---
LOGIC_OR = "OR"
LOGIC_AND = "AND"

# --- Operators ---
OPERATOR_GT = ">"
OPERATOR_LT = "<"
OPERATOR_GTE = ">="
OPERATOR_LTE = "<="
OPERATOR_EQ = "=="
OPERATOR_NEQ = "!="
OPERATOR_IN = "in"  # the value is one of a comma separated list
OPERATOR_NOT_IN = "not in"

OPERATORS = [
    OPERATOR_GT,
    OPERATOR_LT,
    OPERATOR_GTE,
    OPERATOR_LTE,
    OPERATOR_EQ,
    OPERATOR_NEQ,
    OPERATOR_IN,
    OPERATOR_NOT_IN,
]

OPERATOR_LABELS = {
    OPERATOR_GT: "Greater than (>)",
    OPERATOR_LT: "Less than (<)",
    OPERATOR_GTE: "Greater or equal (>=)",
    OPERATOR_LTE: "Less or equal (<=)",
    OPERATOR_EQ: "Equal (==)",
    OPERATOR_NEQ: "Not equal (!=)",
    OPERATOR_IN: "Is one of (a, b, c)",
    OPERATOR_NOT_IN: "Is none of (a, b, c)",
}

# --- Coordinator Data Keys ---
DATA_COORDINATOR = "coordinator"
DATA_SERVER_UNREGISTER = "server_unregister"

# --- Entity Unique ID Prefixes ---
UNIQUE_ID_MASTER = "master_safe"
UNIQUE_ID_GROUP_PREFIX = "group_"
UNIQUE_ID_FORCE_UNSAFE = "force_unsafe"
UNIQUE_ID_FORCE_SAFE = "force_safe"
UNIQUE_ID_MANUAL_SAFE = "manual_safe"
UNIQUE_ID_MANUAL_SAFE_DURATION = "manual_safe_duration"
UNIQUE_ID_REASON = "reason"

# --- Event for automations: fired when the monitor changes between safe and unsafe ---
EVENT_SAFETY_CHANGED = "ascom_alpaca_safety_changed"

# --- Storage (state that has to survive a restart or reload) ---
STORAGE_VERSION = 1
STORAGE_KEY_FORCE_UNSAFE = "force_unsafe"
STORAGE_KEY_MANUAL_SAFE = "manual_safe"
STORAGE_KEY_MANUAL_SAFE_UNTIL = "manual_safe_until"  # ISO time, None: until it is switched off
STORAGE_KEY_MANUAL_SAFE_HOURS = "manual_safe_hours"


def storage_key(entry_id: str) -> str:
    """Name of the storage file of a config entry."""
    return f"{DOMAIN}.{entry_id}"

# --- Platforms ---
PLATFORMS = ["binary_sensor", "sensor", "switch", "button", "number"]

# --- Device Info for Server Registration ---
SAFETY_DEVICE_TYPE = "SafetyMonitor"
SAFETY_DEVICE_NAME = "ASCOM Alpaca Safety"
SAFETY_DRIVER_VERSION = "0.20.0"
