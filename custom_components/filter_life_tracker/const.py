"""Constants for Filter Life Tracker."""

from datetime import timedelta

DOMAIN = "filter_life_tracker"

# Entry types
ENTRY_TYPE_DEVICE = "device"
ENTRY_TYPE_TOTAL = "total"

# Source types
SOURCE_TYPE_DURATION = "duration"
SOURCE_TYPE_COUNT = "count"

# Config keys
CONF_ENTRY_TYPE = "entry_type"
CONF_SOURCE_ENTITY = "source_entity"
CONF_TARGET_STATE = "target_state"
CONF_SOURCE_TYPE = "source_type"
CONF_COEFFICIENT = "coefficient"
CONF_DEBOUNCE = "debounce"
CONF_DOWNSTREAM_ENTRIES = "downstream_entries"
CONF_FILTERS = "filters"
CONF_ENABLE_LEVEL2 = "enable_level2"

# Filter config keys
CONF_TEMPLATE = "template"
CONF_RATED_TIME_DAYS = "rated_time_days"
CONF_RATED_USAGE = "rated_usage"
CONF_WARN_THRESHOLD = "warn_threshold"
CONF_CASCADE_FACTOR = "cascade_factor"

# Persisted state keys
STATE_INSTALL_DATE = "install_date"
STATE_ACCUMULATED_USAGE = "accumulated_usage"

# Templates
TEMPLATE_PP_COTTON = "pp_cotton"
TEMPLATE_ACTIVATED_CARBON = "activated_carbon"
TEMPLATE_CUSTOM = "custom"
TEMPLATES = [TEMPLATE_PP_COTTON, TEMPLATE_ACTIVATED_CARBON, TEMPLATE_CUSTOM]

TEMPLATE_DEFAULTS = {
    TEMPLATE_PP_COTTON: {
        CONF_RATED_TIME_DAYS: 180,
        "rated_usage_duration": 500.0,  # hours
        "rated_usage_count": 500.0,  # times
    },
    TEMPLATE_ACTIVATED_CARBON: {
        CONF_RATED_TIME_DAYS: 180,
        "rated_usage_duration": 200.0,
        "rated_usage_count": 200.0,
    },
    TEMPLATE_CUSTOM: {
        CONF_RATED_TIME_DAYS: 180,
        "rated_usage_duration": 500.0,
        "rated_usage_count": 500.0,
    },
}

# Defaults
DEFAULT_COEFFICIENT = 1.0
MIN_COEFFICIENT = 0.1
MAX_COEFFICIENT = 3.0
DEFAULT_DEBOUNCE = 10
MIN_DEBOUNCE = 1
MAX_DEBOUNCE = 300
DEFAULT_WARN_THRESHOLD = 20.0
DEFAULT_CASCADE_FACTOR = 1.5

# Storage
STORAGE_KEY = f"{DOMAIN}.storage"
STORAGE_VERSION = 1
CONFIG_ENTRY_VERSION = 1

# Intervals (ADR-007: batched flush)
FLUSH_INTERVAL = timedelta(minutes=10)
SENSOR_REFRESH_INTERVAL = timedelta(seconds=60)

# Signals
SIGNAL_INCREMENT = f"{DOMAIN}_increment_{{}}"  # per downstream entry_id
SIGNAL_UPDATED = f"{DOMAIN}_updated_{{}}"  # per entry_id

SECONDS_PER_DAY = 86400
SECONDS_PER_HOUR = 3600
