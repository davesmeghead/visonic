""" Constants for the connection to a Visonic PowerMax or PowerMaster Alarm System """

DOMAIN = "visonic"
PLATFORMS = ["alarm_control_panel", "binary_sensor", "switch"]

DOMAINCLIENT = f"{DOMAIN}_client"
DOMAINDATA = f"{DOMAIN}_data"
DOMAINALARM = f"{DOMAIN}_alarm"

NOTIFICATION_ID = "visonic_notification"
NOTIFICATION_TITLE = "Visonic Panel Setup"

VISONIC_UNIQUE_NAME = "Visonic Alarm"

CONF_DEVICE_TYPE = "type"
CONF_DEVICE_BAUD = "baud"
DEFAULT_DEVICE_HOST = "127.0.0.1"
DEFAULT_DEVICE_PORT = 30000
DEFAULT_DEVICE_USB = "/dev/ttyUSB1"
DEFAULT_DEVICE_BAUD = 9600

# settings that are used for creation
CONF_LANGUAGE = "language"
CONF_FORCE_STANDARD = "force_standard"
CONF_FORCE_AUTOENROLL = "force_autoenroll"
CONF_AUTO_SYNC_TIME = "sync_time"
CONF_DOWNLOAD_CODE = "download_code"
CONF_EXCLUDE_SENSOR = "exclude_sensor"
CONF_EXCLUDE_X10 = "exclude_x10"

# settings than can be modified
CONF_MOTION_OFF_DELAY = "motion_off"
CONF_ENABLE_REMOTE_ARM = "allow_remote_arm"
CONF_ENABLE_REMOTE_DISARM = "allow_remote_disarm"
CONF_ENABLE_SENSOR_BYPASS = "allow_sensor_bypass"
CONF_OVERRIDE_CODE = "override_code"
CONF_ARM_CODE_AUTO = "arm_without_usercode"
CONF_FORCE_KEYPAD = "force_numeric_keypad"
CONF_SIREN_SOUNDING = "siren_sounding"
CONF_INSTANT_ARM_AWAY = "arm_away_instant"
CONF_INSTANT_ARM_HOME = "arm_home_instant"

# Event processing for the log files from the panel
CONF_LOG_EVENT = "panellog_logentry_event"
CONF_LOG_CSV_TITLE = "panellog_csv_add_title_row"
CONF_LOG_XML_FN = "panellog_xml_filename"
CONF_LOG_CSV_FN = "panellog_csv_filename"
CONF_LOG_DONE = "panellog_complete_event"
CONF_LOG_REVERSE = "panellog_reverse_order"
CONF_LOG_MAX_ENTRIES = "panellog_max_entries"

# Temporary B0 Config Items
CONF_B0_ENABLE_MOTION_PROCESSING = "b0_enable_motion_processing"
CONF_B0_MIN_TIME_BETWEEN_TRIGGERS = "b0_min_time_between_triggers"
CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT = "b0_max_time_for_trigger_event"

# Supplement the HA attributes with a bypass
ATTR_BYPASS = "bypass"
