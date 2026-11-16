"""Constants for the Xiaomi Robot Vacuum Map integration."""

from __future__ import annotations

DOMAIN = "xiaomi_robot_map"
NAME = "Xiaomi Robot Vacuum Map"
MANUFACTURER = "Xiaomi"

PLATFORMS: list[str] = ["camera", "sensor"]

# --- configuration keys ---------------------------------------------------

CONF_COUNTRY = "country"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_HOST = "host"
CONF_TOKEN = "token"
CONF_NAME = "name"
CONF_MODEL = "model"
CONF_DEVICE_ID = "device_id"
CONF_USER_ID = "user_id"
CONF_MAP_NAME = "map_name"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_IDLE_INTERVAL = "idle_interval"
CONF_THEME = "theme"
CONF_LAYERS = "layers"
CONF_SCALE = "scale"
CONF_ROTATE = "rotate"
CONF_TRIM = "trim"
CONF_STORE_RAW_MAP = "store_raw_map"
CONF_ROOM_NAMES = "room_names"

# --- map sources ----------------------------------------------------------

SOURCE_CLOUD = "cloud"
SOURCE_LOCAL = "local"
CONF_SOURCE = "source"
CONF_LOCAL_KIND = "local_kind"
CONF_URL = "url"
CONF_BEARER_TOKEN = "bearer_token"
CONF_CAPTCHA_CODE = "captcha_code"
CONF_CLIENT_DEVICE_ID = "client_device_id"

LOCAL_KIND_VALETUDO = "valetudo"
LOCAL_KIND_RRMAP = "rrmap"
LOCAL_KINDS = [LOCAL_KIND_VALETUDO, LOCAL_KIND_RRMAP]

DEFAULT_VALETUDO_PATH = "/api/v2/robot/state/map"
LOCAL_TIMEOUT = 25

# --- defaults -------------------------------------------------------------

DEFAULT_NAME = "Robot Vacuum Map"
DEFAULT_COUNTRY = "de"
DEFAULT_SCAN_INTERVAL = 10
DEFAULT_IDLE_INTERVAL = 300
DEFAULT_THEME = "dark"
DEFAULT_SCALE = 1.0
DEFAULT_ROTATE = 0
DEFAULT_ROBOT_RADIUS = 7

MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 600
MIN_IDLE_INTERVAL = 60
MAX_IDLE_INTERVAL = 3600

AVAILABLE_COUNTRIES = ["cn", "de", "us", "ru", "tw", "sg", "in", "i2"]

# Models served by the RRMap (Roborock) cloud API.
SUPPORTED_MODEL_PREFIXES = ("roborock.", "rockrobo.")

# --- layers that can be drawn on the map image ---------------------------

LAYER_ROBOT = "robot"
LAYER_CHARGER = "charger"
LAYER_PATH = "path"
LAYER_GOTO_PATH = "goto_path"
LAYER_PREDICTED_PATH = "predicted_path"
LAYER_VIRTUAL_WALLS = "virtual_walls"
LAYER_ZONES = "zones"
LAYER_NO_GO_AREAS = "no_go_areas"
LAYER_NO_MOPPING_AREAS = "no_mopping_areas"
LAYER_NO_CARPET_AREAS = "no_carpet_areas"
LAYER_OBSTACLES = "obstacles"
LAYER_ROOM_NUMBERS = "room_numbers"

DEFAULT_LAYERS = [
    LAYER_ROBOT,
    LAYER_CHARGER,
    LAYER_PATH,
    LAYER_PREDICTED_PATH,
    LAYER_VIRTUAL_WALLS,
    LAYER_ZONES,
    LAYER_NO_GO_AREAS,
    LAYER_NO_MOPPING_AREAS,
]

ALL_LAYERS = [
    LAYER_ROBOT,
    LAYER_CHARGER,
    LAYER_PATH,
    LAYER_GOTO_PATH,
    LAYER_PREDICTED_PATH,
    LAYER_VIRTUAL_WALLS,
    LAYER_ZONES,
    LAYER_NO_GO_AREAS,
    LAYER_NO_MOPPING_AREAS,
    LAYER_NO_CARPET_AREAS,
    LAYER_OBSTACLES,
    LAYER_ROOM_NUMBERS,
]

THEMES = ["dark", "light"]

# --- services -------------------------------------------------------------

SERVICE_GET_MAP = "get_map"
SERVICE_REFRESH = "refresh"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

# --- misc -----------------------------------------------------------------

STORAGE_RAW_DIR = "xiaomi_robot_map"
