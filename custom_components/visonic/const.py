"""Constants for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

import re
from typing import Final

from homeassistant.const import Platform

# The client version and domain information for the integration
DOMAIN: Final = "visonic"
CLIENT_VERSION: Final = "0.13.0.6"
MANUFACTURER: Final = "Visonic"
VISONIC_UNIQUE_NAME: Final = "Visonic Alarm"
VISONIC_TRANSLATION_KEY: Final = "alarm_panel_key"
VISONIC_CLOUD_SERVER: Final = "Visonic Cloud Server"

# The HA bus events that this integration can generate
ALARM_PANEL_CHANGE_EVENT: Final = f"{DOMAIN}_alarm_panel_state"
ALARM_SENSOR_CHANGE_EVENT: Final = f"{DOMAIN}_alarm_sensor_state"
ALARM_COMMAND_EVENT: Final = f"{DOMAIN}_alarm_command_to_panel"
ALARM_PANEL_LOG_FILE_COMPLETE: Final = f"{DOMAIN}_alarm_panel_event_log_complete"
ALARM_PANEL_LOG_FILE_ENTRY: Final = f"{DOMAIN}_alarm_panel_event_log_entry"
CAMERA_CLIP_EVENT: Final = f"{DOMAIN}_camera_clip"  # a PIR capture finished rendering

# Template for partition names
PARTITION_NAME_TEMPLATE: Final = "{panel_ident} Partition {partition_index}"

# The HA Services.  These strings match the content of the services.yaml file
ALARM_PANEL_COMMAND: Final = "alarm_panel_command"
ALARM_PANEL_SWITCH: Final = "alarm_panel_switch"
ALARM_PANEL_EVENTLOG: Final = "alarm_panel_eventlog"
ALARM_PANEL_RECONNECT: Final = "alarm_panel_reconnect"
ALARM_PANEL_ZONEINFO: Final = "alarm_panel_zoneinfo"
ALARM_SENSOR_BYPASS: Final = "alarm_sensor_bypass"
ALARM_SENSOR_IMAGE: Final = "alarm_sensor_image"

PANEL_ATTRIBUTE_NAME: Final = "panel"
DEVICE_ATTRIBUTE_NAME: Final = "visonic_device"

# Default connection details (connection can be one of Ethernet, Serial, RS232)
DEFAULT_DEVICE_HOST: Final = "127.0.0.1"
DEFAULT_DEVICE_PORT: Final = 30000
DEFAULT_DEVICE_SERV_HOST: Final = "0.0.0.0"
DEFAULT_DEVICE_SERV_PORT: Final = 5001
DEFAULT_DEVICE_SERIAL: Final = "/dev/ttyUSB0"
DEFAULT_DEVICE_BAUD: Final = 9600
DEFAULT_PANEL_USER_CODE: Final = "1111"
DEFAULT_CLOUD_SCAN_INTERVAL: Final = 20
DEFAULT_IMAGE_MEDIA_PATH: Final = "visonic"  # sub-path under HA's media dir (media_dirs); absolute values used as-is
IMAGE_SEQUENCE_GAP: Final = 90.0
IMAGE_SEQUENCE_MAX_FRAMES: Final = 15
IMAGE_FRAME_DURATION_MS: Final = 500
IMAGE_DOWNLOAD_TIMEOUT: Final = 60.0
IMAGE_DOWNLOAD_MAX: Final = 300.0  # hard cap so the download-active state can't stick on during a long retransmit loop
CONF_IMAGE_QUEUE_MAX: Final = "image_queue_max"
DEFAULT_IMAGE_QUEUE_MAX: Final = 5  # requests queued behind an active download; further presses ignored

# Text strings for entity attributes
TEXT_DISCONNECTION_COUNT: Final = "Disconnection Count"
TEXT_CLIENT_VERSION: Final = "Client Version"
TEXT_LAST_EVENT_NAME: Final = "lasteventname"
TEXT_LAST_EVENT_TIME: Final = "lasteventtime"
TEXT_LAST_EVENT_PARTITION: Final = "lasteventpartition"
TEXT_LAST_EVENT_ACTION: Final = "lasteventaction"

TEXT_XML_LOG_FILE_TEMPLATE: Final = "visonic_template.xml"

# These are the translation strings for the various abort and error indications to the user for the configuration
TRANSLATE_ABORT_ALREADY_CONFIGURED: Final = "already_configured"
TRANSLATE_ABORT_INVALID_DEVICE_TYPE: Final = "device_error"
TRANSLATE_ABORT_CANNOT_CONFIG_DISCOVERED: Final = "cannot_configure_tcp_discovered"
TRANSLATE_ABORT_EMULATION_MODE: Final = "emulation_mode_error"
TRANSLATE_ABORT_UNKNOWN: Final = "unknown"
TRANSLATE_ABORT_CANNOT_EDIT_SERVER: Final = "cannot_edit_tcp_server"
TRANSLATE_ERROR_SETTINGS_MISSING: Final = "settings_missing"
TRANSLATE_ERROR_EMAIL_INVALID: Final = "email_invalid"
TRANSLATE_ERROR_ETHERNET_SERVER_OR_SERIAL: Final = "ethernet_server_or_serial"
TRANSLATE_ERROR_CONNECTION_TIMEOUT: Final = "cannot_connect_timeout"
TRANSLATE_ERROR_CONNECTION_REFUSED: Final = "cannot_connect_refused"
TRANSLATE_ERROR_SETTINGS_INVALID: Final = "settings_invalid"
TRANSLATE_ERROR_EXCLUSIONS_INVALID: Final = "exclusion_list_invalid"
TRANSLATE_ERROR_DL_CODE_INVALID: Final = "download_code_invalid"
TRANSLATE_ERROR_SELECT_INVALID: Final = "select_entity_invalid"

# Translation exceptions throughout the integration, maintained here to ensure consistency in language translation
TRANSLATE_EXCEPTION_NO_PANEL_CONNECTION: Final = "no_panel_connection"
TRANSLATE_EXCEPTION_INVALID_ARM_STATE: Final = "invalid_arm_state"  # Not Used ###################################
TRANSLATE_EXCEPTION_INVALID_ARM_STATE_NO_OPTION: Final = "invalid_arm_state_no_option"
TRANSLATE_EXCEPTION_NUMBER_NOT_UNIQUE: Final = "number_in_config_not_unique"
TRANSLATE_EXCEPTION_INITIAL_CONNECTION_FAILURE: Final = "panel_initial_connection_failure"
TRANSLATE_EXCEPTION_NO_UNIQUE_NUMBER_IN_CONFIG: Final = "no_unique_number_in_config"
TRANSLATE_EXCEPTION_SERVICE_NO_ENTITY_SPECIFIED: Final = "no_entity_specified_in_service"
TRANSLATE_EXCEPTION_SERVICE_ENTITY_NOT_IN_DEVICE: Final = "entity_not_attached_to_device"
TRANSLATE_EXCEPTION_SERVICE_ENTITY_NOT_IN_REGISTRY: Final = "entity_not_in_registry"
TRANSLATE_EXCEPTION_SERVICE_INVALID_DEVICE_FOR_ENTITY: Final = "invalid_device_for_entity"
TRANSLATE_EXCEPTION_SERVICE_DEVICE_NO_IN_CONFIG: Final = "device_not_linked_to_configuration"
TRANSLATE_EXCEPTION_SERVICE_CONFIG_ENTRY_NOT_FOUND: Final = "config_entry_not_found"

# Supplement the HA attributes with a bypass, this is for individual sensors in the service call. It is used as a boolean.
ATTR_BYPASS: Final = "bypass"
ATTR_DURATION: Final = "duration"

PARTITION_ID_WHEN_BASE: Final = -1

# These are the control flow names for the config variables.
#   These are set by the user (with a few more from HA const.py)
# settings that are used for creation
CONF_PANEL_NUMBER: Final = "panel_number"
CONF_SERVER_NUMBER: Final = "server_number"
CONF_ESPHOME_ENTITY_SELECT: Final = "esphome_entity_select"
CONF_EXCLUDE_SENSOR: Final = "exclude_sensor"
CONF_EXCLUDE_SWITCH: Final = "exclude_switch"
CONF_DOWNLOAD_CODE: Final = "download_code"
CONF_USER_CODE_SLOT: Final = "user_code_slot"
CONF_EMULATION_MODE: Final = "emulation_mode"
CONF_SWITCH_COMMAND: Final = "switch_command"
CONF_USAGE: Final = "usage"

# The baud rate for serial connections is managed internally and not set by the user
# For a new connection, the default (9600) is used, the working baud is saved to entry.data
# If the baud is set in entry.data then it is tried first
CONF_DEVICE_BAUD: Final = "baud"

# settings than can be modified
CONF_ENABLE_REMOTE_ARM: Final = "allow_remote_arm"
CONF_ENABLE_REMOTE_DISARM: Final = "allow_remote_disarm"
CONF_ENABLE_SENSOR_BYPASS: Final = "allow_sensor_bypass"
CONF_ARM_CODE_AUTO: Final = "arm_without_usercode"
CONF_FORCE_KEYPAD: Final = "force_numeric_keypad"
CONF_ARM_HOME_ENABLED: Final = "arm_home_enabled"
CONF_ARM_NIGHT_ENABLED: Final = "arm_night_enabled"
CONF_INSTANT_ARM_AWAY: Final = "arm_away_instant"
CONF_INSTANT_ARM_HOME: Final = "arm_home_instant"
CONF_MOTION_OFF_DELAY: Final = "motion_off_delay"
CONF_MAGNET_CLOSED_DELAY: Final = "magnet_closed_delay"
CONF_EMER_OFF_DELAY: Final = "emergency_off_delay"
CONF_SIREN_SOUNDING: Final = "siren_sounding"
CONF_RETRY_CONNECTION_COUNT: Final = "retry_connection_count"
CONF_RETRY_CONNECTION_DELAY: Final = "retry_connection_delay"
CONF_EPROM_ATTRIBUTES: Final = "show_eeprom_attributes"  # leave as eeprom as this will change the config params in HA
CONF_LOG_EVENT: Final = "panellog_logentry_event"
CONF_LOG_CSV_TITLE: Final = "panellog_csv_add_title_row"
CONF_LOG_XML_FN: Final = "panellog_xml_filename"
CONF_LOG_CSV_FN: Final = "panellog_csv_filename"
CONF_IMAGE_MEDIA_PATH: Final = "image_media_path"
CONF_IMAGE_SINGLE_FRAME: Final = "image_single_frame"
CONF_LOG_DONE: Final = "panellog_complete_event"
CONF_LOG_REVERSE: Final = "panellog_reverse_order"
CONF_LOG_MAX_ENTRIES: Final = "panellog_max_entries"
CONF_SERVER_HOST: Final = "server_host"
CONF_SERVER_PORT: Final = "server_port"
CONF_PANEL_SERIAL: Final = "panel_serial"
CONF_CLOUD_APP_ID: Final = "cloud_app_id"
CONF_CLOUD_USER_TOKEN: Final = "cloud_user_token"
CONF_CLOUD_SESSION_TOKEN: Final = "cloud_session_token"

TEXT_TITLE: Final = "title"
TEXT_PANEL_MODEL = "panel_model"

PIN_REGEX: Final = re.compile(r"^[0-9]{4}$")

PLATFORMS: Final = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SIREN,
    Platform.SWITCH,
]

"""Mappings."""
# Constants for sending a persistent notification to the frontend when there is a fault
NOTIFICATION_ID: Final = f"{DOMAIN}_notification"
NOTIFICATION_TITLE: Final = "Visonic Alarm Panel"

# What notifications to send to the HA Frontend
CONF_ALARM_NOTIFICATIONS: Final = "panel_state_notifications"
MAX_CLIENT_LOG_ENTRIES: Final = 1000

PE_PARTITION: Final = "partition"
PE_TIME: Final = "time"
PE_EVENT: Final = "event"
PE_NAME: Final = "name"

PANELS: Final = "PANELS"
SERVERS: Final = "SERVERS"
DISCOVERIES: Final = "DISCOVERIES"

# The main configuration forms
#   These need to be in the language translation file
FORM_DEVICE: Final = "device"
FORM_ETHERNET: Final = "form_ethernet"
FORM_SERIAL: Final = "form_serial"
FORM_CLOUD: Final = "form_cloud"
FORM_TCP_SERVER: Final = "form_tcp_server"
FORM_TCP_DISCOVERED: Final = "form_tcp_discovered"
FORM_POWERLINK: Final = "form_powerlink"
FORM_PARAM10: Final = "parameters10"
FORM_PARAM11: Final = "parameters11"
FORM_PARAM12: Final = "parameters12"
FORM_PARAM13: Final = "parameters13"
FORM_PARAM14: Final = "parameters14"
