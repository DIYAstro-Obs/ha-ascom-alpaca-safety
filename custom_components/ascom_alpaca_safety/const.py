"""Constants for the ASCOM Alpaca Safety integration."""

DOMAIN = "ascom_alpaca_safety"
INTEGRATION_NAME = "ASCOM Alpaca Safety"

# --- Server Connection ---
ALPACA_SERVER_API_KEY = "ascom_alpaca_server_api"  # hass.data key for the Server's API
ALPACA_SERVER_COMPONENT = "ascom_alpaca_server"  # Server's actual HA component domain

# --- Timer Defaults (seconds) ---
DEFAULT_SETTLE_TIME = 900  # 15 minutes
DEFAULT_WATCHDOG_TIMEOUT = 300  # 5 minutes
DEFAULT_UNSAFE_DELAY = 0  # no delay by default

# --- Configuration Keys ---
CONF_GROUPS = "groups"
CONF_GROUP_NAME = "group_name"
CONF_GROUP_LOGIC = "group_logic"
CONF_GROUP_SETTLE_TIME = "settle_time"
CONF_RULES = "rules"
CONF_RULE_ENTITY = "entity_id"
CONF_RULE_OPERATOR = "operator"
CONF_RULE_THRESHOLD = "threshold"
CONF_RULE_UNSAFE_DELAY = "unsafe_delay"
CONF_RULE_WATCHDOG_TIMEOUT = "watchdog_timeout"

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

OPERATORS = [
    OPERATOR_GT,
    OPERATOR_LT,
    OPERATOR_GTE,
    OPERATOR_LTE,
    OPERATOR_EQ,
    OPERATOR_NEQ,
]

OPERATOR_LABELS = {
    OPERATOR_GT: "Greater than (>)",
    OPERATOR_LT: "Less than (<)",
    OPERATOR_GTE: "Greater or equal (>=)",
    OPERATOR_LTE: "Less or equal (<=)",
    OPERATOR_EQ: "Equal (==)",
    OPERATOR_NEQ: "Not equal (!=)",
}

# --- Coordinator Data Keys ---
DATA_COORDINATOR = "coordinator"
DATA_SERVER_UNREGISTER = "server_unregister"

# --- Entity Unique ID Prefixes ---
UNIQUE_ID_MASTER = "master_safe"
UNIQUE_ID_GROUP_PREFIX = "group_"
UNIQUE_ID_FORCE_UNSAFE = "force_unsafe"
UNIQUE_ID_FORCE_SAFE = "force_safe"

# --- Platforms ---
PLATFORMS = ["binary_sensor", "switch", "button"]

# --- Device Info for Server Registration ---
SAFETY_DEVICE_TYPE = "SafetyMonitor"
SAFETY_DEVICE_NAME = "ASCOM Alpaca Safety"
SAFETY_DRIVER_VERSION = "0.9.0"
