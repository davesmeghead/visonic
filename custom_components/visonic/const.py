"""Constants for the connection to a Visonic PowerMax or PowerMaster Alarm System."""
from enum import Enum, IntFlag

# The domain for the integration
DOMAIN = "visonic"

# The platforms in the integration
ALARM_PANEL_ENTITY = "alarm_control_panel"
BINARY_SENSOR_STR = "binary_sensor"
IMAGE_SENSOR_STR = "image"
MONITOR_SENSOR_STR = "sensor"
SWITCH_STR = "switch"
SELECT_STR = "select"

#PLATFORMS = [ALARM_PANEL_ENTITY, BINARY_SENSOR_STR, IMAGE_SENSOR_STR, SWITCH_STR, SELECT_STR]

VISONIC_UNIQUE_NAME = "Visonic Alarm"

#from enum import IntFlag
class SensorEntityFeature(IntFlag):
    """Supported features of the zone sensor entity."""
    BYPASS_FEATURE = 1
    ARMED_FEATURE = 2

# Constants for storing data in hass[DOMAIN]
DOMAINCLIENT = f"{DOMAIN}_client"
DOMAINDATA = f"{DOMAIN}_data"
DOMAINCLIENTTASK = f"{DOMAIN}_client_task"

# Constants for sending a persistent notification to the frontend when there is a fault
NOTIFICATION_ID = f"{DOMAIN}_notification"
NOTIFICATION_TITLE = "Visonic Alarm Panel"

# update listener
VISONIC_UPDATE_LISTENER = f"{DOMAIN}_update_listener"

# The HA bus events that this integration can generate
ALARM_PANEL_CHANGE_EVENT = f"{DOMAIN}_alarm_panel_state_update"
ALARM_PANEL_LOG_FILE_COMPLETE = f"{DOMAIN}_alarm_panel_event_log_complete"
ALARM_PANEL_LOG_FILE_ENTRY = f"{DOMAIN}_alarm_panel_event_log_entry"

# The HA Services.  These strings match the content of the services.yaml file
ALARM_PANEL_COMMAND = "alarm_panel_command"
ALARM_PANEL_EVENTLOG = "alarm_panel_eventlog"
ALARM_PANEL_RECONNECT = "alarm_panel_reconnect"
ALARM_SENSOR_BYPASS = "alarm_sensor_bypass"
ALARM_SENSOR_IMAGE = "alarm_sensor_image"

PANEL_ATTRIBUTE_NAME = "panel"
DEVICE_ATTRIBUTE_NAME = "visonic device"

# Default connection details (connection can be one of Ethernet, USB, RS232)
DEFAULT_DEVICE_HOST = "127.0.0.1"
DEFAULT_DEVICE_PORT = "30000"
DEFAULT_DEVICE_TOPIC = "visonic/panel"
DEFAULT_DEVICE_USB = "/dev/ttyUSB1"
DEFAULT_DEVICE_BAUD = "9600"

# Event processing for the log files from the panel. These are the control flow names for the config variables.
CONF_LOG_EVENT = "panellog_logentry_event"
CONF_LOG_CSV_TITLE = "panellog_csv_add_title_row"
CONF_LOG_XML_FN = "panellog_xml_filename"
CONF_LOG_CSV_FN = "panellog_csv_filename"
CONF_LOG_DONE = "panellog_complete_event"
CONF_LOG_REVERSE = "panellog_reverse_order"
CONF_LOG_MAX_ENTRIES = "panellog_max_entries"

# Supplement the HA attributes with a bypass, this is for individual sensors in the service call. It is used as a boolean.
ATTR_BYPASS = "bypass"

# What notifications to send to the HA Frontend
CONF_ALARM_NOTIFICATIONS = "panel_state_notifications"

# settings that are used for creation
CONF_PANEL_NUMBER = "panel_number"
CONF_DEVICE_TYPE = "type"
CONF_DEVICE_BAUD = "baud"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PATH = "path"
CONF_EXCLUDE_SENSOR = "exclude_sensor"
CONF_EXCLUDE_X10 = "exclude_x10"
CONF_DOWNLOAD_CODE = "download_code"
CONF_FORCE_AUTOENROLL = "force_autoenroll"
CONF_AUTO_SYNC_TIME = "sync_time"
CONF_LANGUAGE = "language"
CONF_EMULATION_MODE = "emulation_mode"
CONF_COMMAND = "command"

# settings than can be modified
CONF_ENABLE_REMOTE_ARM = "allow_remote_arm"
CONF_ENABLE_REMOTE_DISARM = "allow_remote_disarm"
CONF_ENABLE_SENSOR_BYPASS = "allow_sensor_bypass"
CONF_OVERRIDE_CODE = "override_code"
CONF_ARM_CODE_AUTO = "arm_without_usercode"
CONF_FORCE_KEYPAD = "force_numeric_keypad"
CONF_ARM_HOME_ENABLED = "arm_home_enabled"
CONF_ARM_NIGHT_ENABLED = "arm_night_enabled"
CONF_INSTANT_ARM_AWAY = "arm_away_instant"
CONF_INSTANT_ARM_HOME = "arm_home_instant"
CONF_MOTION_OFF_DELAY = "motion_off"
CONF_SIREN_SOUNDING = "siren_sounding"
CONF_RETRY_CONNECTION_COUNT = "retry_connection_count"
CONF_RETRY_CONNECTION_DELAY = "retry_connection_delay"
CONF_EEPROM_ATTRIBUTES = "show_eeprom_attributes"

PIN_REGEX = "^[0-9]{4}$"

class AvailableNotifications(str, Enum):
    ALWAYS = 'always'
    SIREN = 'sirensounding'
    TAMPER = 'paneltamper'
    RESET = 'panelreset'
    INVALID_PIN = 'invalidpin'
    PANEL_OPERATION = 'paneloperation'
    CONNECTION_PROBLEM = 'connectionproblem'
    BYPASS_PROBLEM = 'bypassproblem'
    IMAGE_PROBLEM = 'imageproblem'
    EVENTLOG_PROBLEM = 'eventlogproblem'
    COMMAND_NOT_SENT = 'commandnotsent'

AvailableNotificationConfig = {
    AvailableNotifications.SIREN : "Siren Sounding",
    AvailableNotifications.TAMPER : "Panel Tamper",
    AvailableNotifications.RESET : "Panel System Reset",
    AvailableNotifications.INVALID_PIN : "Code Rejected By Panel",
    AvailableNotifications.PANEL_OPERATION : "Panel Operation",
    AvailableNotifications.CONNECTION_PROBLEM : "Connection Problems",
    AvailableNotifications.BYPASS_PROBLEM : "Sensor Bypass Problems",
    AvailableNotifications.EVENTLOG_PROBLEM : "Event Log Problems",
    AvailableNotifications.IMAGE_PROBLEM : "Image Retrieval Problems",
    AvailableNotifications.COMMAND_NOT_SENT : "Command Not Sent To Panel"
}

available_emulation_modes = [
    "Powerlink Emulation",
    "Force Standard Mode",
    "Minimal Interaction (data only sent to obtain panel state)",
    "Passive Monitor (no data sent to Alarm Panel)"
]

