"""Constants for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

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
NOTIFICATION_TITLE = "Visonic Panel Setup"

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
DEFAULT_DEVICE_PORT = 30000
DEFAULT_DEVICE_USB = "/dev/ttyUSB1"
DEFAULT_DEVICE_BAUD = 9600

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
