"""Visonic API constants."""

from enum import IntEnum, StrEnum

DEFAULT_REST_VERSION = "9.0" # "14.0"
DEFAULT_INSTALLER_VERSION = "8.0" # "12.0"

TEXT_UNKNOWN = "Unknown"
#TEXT_OPEN = "Open"
TEXT_OPENED = "OPENED"
#TEXT_CLOSED = "Closed"
TEXT_STATUS_HOME = "HOME"
TEXT_STATUS_AWAY = "AWAY"
TEXT_STATUS_HOME_INSTANT = "HOME_INSTANT"
TEXT_STATUS_AWAY_INSTANT = "AWAY_INSTANT"
TEXT_STATUS_DISARM = "DISARM"

class RequestType(StrEnum):
    """HTTP request type."""
    GET = "GET"
    POST = "POST"

# List of sensor types
class SensorGroup(IntEnum):
    """Enumeration of sensor types."""
    IGNORED = 1
    UNKNOWN = 2
    MOTION = 3
    MAGNET = 4
    CAMERA = 5
    WIRED = 6
    SMOKE = 7
    FLOOD = 8
    GAS = 9
    VIB = 10
    SHOCK = 11
    TEMP = 12
    SOUND = 13
    GLASS = 14
    PANEL = 100
    COMMS = 101
    TOKEN = 102
    SIREN = 103
    SWITCH = 200

class VisonicURL:
    """URL paths."""

    BASE = "https://{}/rest_api"  # 'https://[hostname]/rest_api/[rest_version]

    ACCESS_GRANT = "access/grant"
    ACCESS_REVOKE = "access/revoke"
    ACTIVATE_SIREN = "activate_siren"
    ALARMS = "alarms"
    ALERTS = "alerts"
    APP_TYPE = "apptype"
    AUTH = "auth"
    CAMERAS = "cameras"
    DEVICES = "devices"
    DISABLE_SIREN = "disable_siren"
    EVENTS = "events"
    FEATURE_SET = "feature_set"
    HOME_AUTOMATION_DEVICES = "home_automation_devices"
    LOCATIONS = "locations"
    MAKE_VIDEO = "make_video"
    NOTIFICATIONS_EMAIL = "notifications/email"
    PANEL_LOGIN = "panel/login"
    PANEL_ACCESS_INFO = "panel_access_info"
    PANEL_ADD = "panel/add"
    PANEL_INFO = "panel_info"
    PANEL_RENAME = "panel/rename"
    PANEL_UNLINK = "panel/unlink"
    PANELS = "panels"
    PASSWORD_RESET = "password/reset"
    PASSWORD_RESET_COMPLETE = "password/reset/complete"
    PROCESS_STATUS = "process_status?process_tokens={}"
    SET_BYPASS_ZONE = "set_bypass_zone"
    SET_NAME = "set_name"
    SET_STATE = "set_state"
    SET_USER_CODE = "set_user_code"
    SMART_DEVICES = "smart_devices"
    SMART_DEVICES_SETTINGS = "smart_devices/settings"
    STATUS = "status"
    TROUBLES = "troubles"
    USERS = "users"
    VERSION = "version"
    WAKEUP_SMS = "wakeup_sms"
