"""Constants for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

DOMAIN = "visonic"
PLATFORMS = ["alarm_control_panel", "binary_sensor", "switch"]

DOMAINCLIENT = f"{DOMAIN}_client"
DOMAINDATA = f"{DOMAIN}_data"
DOMAINALARM = f"{DOMAIN}_alarm"

NOTIFICATION_ID = "visonic_notification"
NOTIFICATION_TITLE = "Visonic Panel Setup"

VISONIC_UNIQUE_NAME = "Visonic Alarm"
ALARM_PANEL_ENTITY = "alarm_control_panel.visonic_alarm"

DEFAULT_DEVICE_HOST = "127.0.0.1"
DEFAULT_DEVICE_PORT = 30000
DEFAULT_DEVICE_USB = "/dev/ttyUSB1"
DEFAULT_DEVICE_BAUD = 9600

# Event processing for the log files from the panel
CONF_LOG_EVENT = "panellog_logentry_event"
CONF_LOG_CSV_TITLE = "panellog_csv_add_title_row"
CONF_LOG_XML_FN = "panellog_xml_filename"
CONF_LOG_CSV_FN = "panellog_csv_filename"
CONF_LOG_DONE = "panellog_complete_event"
CONF_LOG_REVERSE = "panellog_reverse_order"
CONF_LOG_MAX_ENTRIES = "panellog_max_entries"

# Supplement the HA attributes with a bypass
ATTR_BYPASS = "bypass"
