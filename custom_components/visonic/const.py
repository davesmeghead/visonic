"""Constants for the connection to a Visonic PowerMax or PowerMaster Alarm System."""
from enum import Enum

DOMAIN = "visonic"
PLATFORMS = ["alarm_control_panel", "binary_sensor", "switch"]

VISONIC_UNIQUE_NAME = "Visonic Alarm"
ALARM_PANEL_ENTITY = "alarm_control_panel.visonic_alarm"

# Constants for storing data in hass[DOMAIN]
DOMAINCLIENT = f"{DOMAIN}_client"
DOMAINDATA = f"{DOMAIN}_data"
DOMAINCLIENTTASK = f"{DOMAIN}_client_task"

# Constants for sending a persistent notification to the frontend when there is a fault
NOTIFICATION_ID = f"{DOMAIN}_notification"
NOTIFICATION_TITLE = "Visonic Alarm Panel"

# undo listener
UNDO_VISONIC_UPDATE_LISTENER = f"{DOMAIN}_undo_update_listener"

# Dispatcher name when the underlying pyvisonic library has got a panel, X10 or sensor change
VISONIC_UPDATE_STATE_DISPATCHER = f"{DOMAIN}_update_state_dispatcher"

# The HA bus events that this integration can generate
ALARM_PANEL_CHANGE_EVENT = f"{DOMAIN}_alarm_panel_state_update"
ALARM_PANEL_LOG_FILE_COMPLETE = f"{DOMAIN}_alarm_panel_event_log_complete"
ALARM_PANEL_LOG_FILE_ENTRY = f"{DOMAIN}_alarm_panel_event_log_entry"

# Default connection details (connection can be one of Ethernet, USB, RS232)
DEFAULT_DEVICE_HOST = "127.0.0.1"
DEFAULT_DEVICE_PORT = "30000"
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
CONF_FORCE_STANDARD = "force_standard"

# settings than can be modified
CONF_ENABLE_REMOTE_ARM = "allow_remote_arm"
CONF_ENABLE_REMOTE_DISARM = "allow_remote_disarm"
CONF_ENABLE_SENSOR_BYPASS = "allow_sensor_bypass"
CONF_OVERRIDE_CODE = "override_code"
CONF_ARM_CODE_AUTO = "arm_without_usercode"
CONF_FORCE_KEYPAD = "force_numeric_keypad"
CONF_INSTANT_ARM_AWAY = "arm_away_instant"
CONF_INSTANT_ARM_HOME = "arm_home_instant"
CONF_MOTION_OFF_DELAY = "motion_off"
CONF_SIREN_SOUNDING = "siren_sounding"

# Temporary B0 Config Items
CONF_B0_ENABLE_MOTION_PROCESSING = "b0_enable_motion_processing"
CONF_B0_MIN_TIME_BETWEEN_TRIGGERS = "b0_min_time_between_triggers"
CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT = "b0_max_time_for_trigger_event"

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
    AvailableNotifications.COMMAND_NOT_SENT : "Command Not Sent To Panel"
}

