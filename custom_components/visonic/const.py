"""Constants for the connection to a Visonic PowerMax or PowerMaster Alarm System."""
from enum import Enum, IntFlag
from .pyconst import AlPanelStatus
from homeassistant.components.alarm_control_panel import AlarmControlPanelState

#from homeassistant.const import (
#    STATE_ALARM_ARMED_AWAY,
#    STATE_ALARM_ARMED_HOME,
#    STATE_ALARM_ARMING,
#    STATE_ALARM_DISARMED,
#    STATE_ALARM_PENDING,
#    STATE_ALARM_TRIGGERED,
#    STATE_UNKNOWN,
#)


# The domain for the integration
DOMAIN = "visonic"
MANUFACTURER = "Visonic"
VISONIC_UNIQUE_NAME = "Visonic Alarm"

#from enum import IntFlag
class SensorEntityFeature(IntFlag):
    """Supported features of the zone sensor entity."""
    BYPASS_FEATURE = 1
    ARMED_FEATURE = 2

# Constants for sending a persistent notification to the frontend when there is a fault
NOTIFICATION_ID = f"{DOMAIN}_notification"
NOTIFICATION_TITLE = "Visonic Alarm Panel"

# The HA bus events that this integration can generate
ALARM_PANEL_CHANGE_EVENT = f"{DOMAIN}_alarm_panel_state"
ALARM_SENSOR_CHANGE_EVENT = f"{DOMAIN}_alarm_sensor_state"
ALARM_COMMAND_EVENT = f"{DOMAIN}_alarm_command_to_panel"
ALARM_PANEL_LOG_FILE_COMPLETE = f"{DOMAIN}_alarm_panel_event_log_complete"
ALARM_PANEL_LOG_FILE_ENTRY = f"{DOMAIN}_alarm_panel_event_log_entry"

# The HA Services.  These strings match the content of the services.yaml file
ALARM_PANEL_COMMAND = "alarm_panel_command"
ALARM_PANEL_X10 = "alarm_panel_x10"
ALARM_PANEL_EVENTLOG = "alarm_panel_eventlog"
ALARM_PANEL_RECONNECT = "alarm_panel_reconnect"
ALARM_SENSOR_BYPASS = "alarm_sensor_bypass"
ALARM_SENSOR_IMAGE = "alarm_sensor_image"

PANEL_ATTRIBUTE_NAME = "panel"
DEVICE_ATTRIBUTE_NAME = "visonic_device"

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
#CONF_FORCE_AUTOENROLL = "force_autoenroll"
#CONF_AUTO_SYNC_TIME = "sync_time"
CONF_LANGUAGE = "language"
CONF_EMULATION_MODE = "emulation_mode"
CONF_COMMAND = "command"
CONF_X10_COMMAND = "x10command"

# settings than can be modified
CONF_ENABLE_REMOTE_ARM = "allow_remote_arm"
CONF_ENABLE_REMOTE_DISARM = "allow_remote_disarm"
CONF_ENABLE_SENSOR_BYPASS = "allow_sensor_bypass"
CONF_ARM_CODE_AUTO = "arm_without_usercode"
CONF_FORCE_KEYPAD = "force_numeric_keypad"
CONF_ARM_HOME_ENABLED = "arm_home_enabled"
CONF_ARM_NIGHT_ENABLED = "arm_night_enabled"
CONF_INSTANT_ARM_AWAY = "arm_away_instant"
CONF_INSTANT_ARM_HOME = "arm_home_instant"
CONF_MOTION_OFF_DELAY = "motion_off_delay"
CONF_MAGNET_CLOSED_DELAY = "magnet_closed_delay"
CONF_EMER_OFF_DELAY = "emergency_off_delay"
CONF_SIREN_SOUNDING = "siren_sounding"
CONF_SENSOR_EVENTS = "sensor_event_list"
CONF_RETRY_CONNECTION_COUNT = "retry_connection_count"
CONF_RETRY_CONNECTION_DELAY = "retry_connection_delay"
CONF_EEPROM_ATTRIBUTES = "show_eeprom_attributes"

PIN_REGEX = "^[0-9]{4}$"

class AvailableNotifications(str, Enum):
    ALWAYS = 'always'
    SIREN = 'siren_sounding'
    RESET = 'panel_reset'
    INVALID_PIN = 'invalid_pin'
    PANEL_OPERATION = 'panel_operation'
    CONNECTION_PROBLEM = 'connection_problem'
    BYPASS_PROBLEM = 'bypass_problem'
    IMAGE_PROBLEM = 'image_problem'
    EVENTLOG_PROBLEM = 'eventlog_problem'
    COMMAND_NOT_SENT = 'command_not_sent'
    X10_PROBLEM = 'x10_problem'

available_emulation_modes = [
    "Powerlink Emulation",
    "Standard",
    "Minimal Interaction (data only sent to obtain panel state)"
]

# For alarm_control_panel and sensor, map the alarm panel states across to the Home Assistant states
map_panel_status_to_ha_status = {
    AlPanelStatus.UNKNOWN             : AlarmControlPanelState.DISARMED,
    AlPanelStatus.DISARMED            : AlarmControlPanelState.DISARMED,
    AlPanelStatus.ARMING_HOME         : AlarmControlPanelState.ARMING,
    AlPanelStatus.ARMING_AWAY         : AlarmControlPanelState.ARMING,
    AlPanelStatus.ENTRY_DELAY         : AlarmControlPanelState.PENDING,
    AlPanelStatus.ENTRY_DELAY_INSTANT : AlarmControlPanelState.PENDING,
    AlPanelStatus.ARMED_HOME          : AlarmControlPanelState.ARMED_HOME,
    AlPanelStatus.ARMED_AWAY          : AlarmControlPanelState.ARMED_AWAY,
    AlPanelStatus.ARMED_HOME_BYPASS   : AlarmControlPanelState.ARMED_HOME,
    AlPanelStatus.ARMED_AWAY_BYPASS   : AlarmControlPanelState.ARMED_AWAY,
    AlPanelStatus.ARMED_HOME_INSTANT  : AlarmControlPanelState.ARMED_HOME,
    AlPanelStatus.ARMED_AWAY_INSTANT  : AlarmControlPanelState.ARMED_AWAY,
    AlPanelStatus.USER_TEST           : AlarmControlPanelState.DISARMED,
    AlPanelStatus.DOWNLOADING         : AlarmControlPanelState.DISARMED,
    AlPanelStatus.INSTALLER           : AlarmControlPanelState.DISARMED
}
