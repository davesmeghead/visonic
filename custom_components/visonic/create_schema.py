"""Schema for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

import logging

#from .pconst import (
#)
import voluptuous as vol
from typing import Any

from homeassistant.const import CONF_DEVICE, CONF_HOST, CONF_PATH, CONF_PORT, CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers import config_validation as cv
from homeassistant.util.yaml.objects import NodeListClass

from .const import (
    CONF_EXCLUDE_SENSOR,
    CONF_EXCLUDE_X10,
    CONF_ENABLE_REMOTE_ARM,
    CONF_ENABLE_REMOTE_DISARM,
    CONF_ENABLE_SENSOR_BYPASS,
    CONF_ARM_CODE_AUTO,
    CONF_OVERRIDE_CODE,
    CONF_FORCE_KEYPAD,
    CONF_INSTANT_ARM_AWAY,
    CONF_INSTANT_ARM_HOME,
    CONF_AUTO_SYNC_TIME,
    CONF_B0_ENABLE_MOTION_PROCESSING,
    CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT,
    CONF_B0_MIN_TIME_BETWEEN_TRIGGERS,
    CONF_DEVICE_BAUD,
    CONF_PANEL_NUMBER,
    CONF_DEVICE_TYPE,
    CONF_DOWNLOAD_CODE,
    CONF_FORCE_AUTOENROLL,
    CONF_FORCE_STANDARD,
    CONF_LANGUAGE,
    CONF_MOTION_OFF_DELAY,
    CONF_SIREN_SOUNDING,
    CONF_LOG_CSV_FN,
    CONF_LOG_CSV_TITLE,
    CONF_LOG_DONE,
    CONF_LOG_EVENT,
    CONF_LOG_MAX_ENTRIES,
    CONF_LOG_REVERSE,
    CONF_LOG_XML_FN,
    CONF_ALARM_NOTIFICATIONS,
    DEFAULT_DEVICE_BAUD,
    DEFAULT_DEVICE_HOST,
    DEFAULT_DEVICE_PORT,
    DEFAULT_DEVICE_USB,
    AvailableNotifications,
    AvailableNotificationConfig,
)

_LOGGER = logging.getLogger(__name__)

available_siren_values = {
    "intruder": "Intruder",
    "tamper": "Tamper",
    "fire": "Fire",
    "emergency": "Emergency",
    "gas": "Gas",
    "flood": "Flood",
    "x10": "X10",
    "panic": "Panic",
}

#VISONIC_ID_LIST_SCHEMA = vol.Schema([int])
#VISONIC_STRING_LIST_SCHEMA = vol.Schema([str])

class VisonicSchema:

    def __init__(self):
        self.CONFIG_SCHEMA_DEVICE = {
            vol.Required(CONF_DEVICE_TYPE, default="Ethernet"): vol.In(["Ethernet", "USB"]),
            vol.Optional(CONF_PANEL_NUMBER, default=0): cv.positive_int,
        }

        self.CONFIG_SCHEMA_ETHERNET = {
            vol.Required(CONF_HOST, default=DEFAULT_DEVICE_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_DEVICE_PORT): str,
        }

        self.CONFIG_SCHEMA_USB = {
            vol.Required(CONF_PATH, default=DEFAULT_DEVICE_USB): str,
            vol.Optional(CONF_DEVICE_BAUD, default=DEFAULT_DEVICE_BAUD): str,
        }
        
        # These are the options that the user entered
        self.options = {}
        
        # initially populate the options data with the default values from all possible settings
        initialise = {
            **self.CONFIG_SCHEMA_DEVICE,
            **self.CONFIG_SCHEMA_ETHERNET,
            **self.CONFIG_SCHEMA_USB,
            **self.create_parameters1(self.options),
            **self.create_parameters2(self.options),
            **self.create_parameters3(self.options),
            **self.create_parameters4(self.options),
        }

        for key in initialise:
            d = key.default()
            self.options[key] = d


    def create_default(self, options: dict, key: str, default: Any):
        """Create a default value for the parameter using the previous value that the user entered."""
        if options is not None and key in options:
            # if type(options[key]) is not type(default):
            # # create_default types are different for = siren_sounding <class 'list'> <class 'set'> ['intruder', 'panic', 'gas'] {'intruder'}
            # _LOGGER.debug(
            #    "create_default types are different for = %s %s %s %s %s", key, type(options[key]), options[key], type(default), default
            # )
            if isinstance(options[key], list) or isinstance(options[key], NodeListClass):
                # _LOGGER.debug("      its a list")
                if CONF_SIREN_SOUNDING == key:
                    return list(options[key])
                if CONF_ALARM_NOTIFICATIONS == key:
                    return list(options[key])
                if len(options[key]) > 0:
                    my_string = ",".join(map(str, list(options[key])))
                    return my_string
                else:
                    return ""
            else:
                return options[key]
        else:
            return default


    # These are only used on creation of the component
    def create_parameters1(self, options: dict):
        """Create parameter set 1."""
        # Panel settings - can only be set on creation
        return {
            vol.Required(
                CONF_LANGUAGE, default=self.create_default(options, CONF_LANGUAGE, "EN")
            ): vol.In(["EN", "FR", "NL"]),
            vol.Optional(
                CONF_EXCLUDE_SENSOR,
                default=self.create_default(options, CONF_EXCLUDE_SENSOR, ""),
            ): str,
            vol.Optional(
                CONF_EXCLUDE_X10, default=self.create_default(options, CONF_EXCLUDE_X10, "")
            ): str,
            vol.Optional(
                CONF_DOWNLOAD_CODE, default=self.create_default(options, CONF_DOWNLOAD_CODE, "")
            ): str,
            vol.Optional(
                CONF_FORCE_STANDARD,
                default=self.create_default(options, CONF_FORCE_STANDARD, False),
            ): bool,
            vol.Optional(
                CONF_FORCE_AUTOENROLL,
                default=self.create_default(options, CONF_FORCE_AUTOENROLL, False),
            ): bool,
            vol.Optional(
                CONF_AUTO_SYNC_TIME,
                default=self.create_default(options, CONF_AUTO_SYNC_TIME, True),
            ): bool,
        }

    def create_parameters2(self, options: dict):
        """Create parameter set 2."""
        # Panel settings - can be modified/edited
        return {
            vol.Optional(
                CONF_MOTION_OFF_DELAY,
                default=self.create_default(options, CONF_MOTION_OFF_DELAY, 120),
            ): int,
            vol.Optional(
                CONF_SIREN_SOUNDING,
                default=self.create_default(options, CONF_SIREN_SOUNDING, ["intruder"]),
            ): cv.multi_select(available_siren_values),
            vol.Optional(
                CONF_ALARM_NOTIFICATIONS,
                default=self.create_default(options, CONF_ALARM_NOTIFICATIONS, [AvailableNotifications.CONNECTION_PROBLEM, AvailableNotifications.SIREN]),
            ): cv.multi_select(AvailableNotificationConfig),
            vol.Optional(
                CONF_OVERRIDE_CODE, default=self.create_default(options, CONF_OVERRIDE_CODE, "")
            ): str,
            vol.Optional(
                CONF_ARM_CODE_AUTO,
                default=self.create_default(options, CONF_ARM_CODE_AUTO, False),
            ): bool,
            vol.Optional(
                CONF_FORCE_KEYPAD, default=self.create_default(options, CONF_FORCE_KEYPAD, False)
            ): bool,
            vol.Optional(
                CONF_INSTANT_ARM_AWAY,
                default=self.create_default(options, CONF_INSTANT_ARM_AWAY, False),
            ): bool,
            vol.Optional(
                CONF_INSTANT_ARM_HOME,
                default=self.create_default(options, CONF_INSTANT_ARM_HOME, False),
            ): bool,
            vol.Optional(
                CONF_ENABLE_REMOTE_ARM,
                default=self.create_default(options, CONF_ENABLE_REMOTE_ARM, False),
            ): bool,
            vol.Optional(
                CONF_ENABLE_REMOTE_DISARM,
                default=self.create_default(options, CONF_ENABLE_REMOTE_DISARM, False),
            ): bool,
            vol.Optional(
                CONF_ENABLE_SENSOR_BYPASS,
                default=self.create_default(options, CONF_ENABLE_SENSOR_BYPASS, False),
            ): bool,
        }

    def create_parameters3(self, options: dict):
        """Create parameter set 3."""
        # Log file parameters
        return {
            vol.Optional(
                CONF_LOG_EVENT, default=self.create_default(options, CONF_LOG_EVENT, False)
            ): bool,
            vol.Optional(
                CONF_LOG_DONE, default=self.create_default(options, CONF_LOG_DONE, False)
            ): bool,
            vol.Optional(
                CONF_LOG_REVERSE, default=self.create_default(options, CONF_LOG_REVERSE, False)
            ): bool,
            vol.Optional(
                CONF_LOG_CSV_TITLE,
                default=self.create_default(options, CONF_LOG_CSV_TITLE, False),
            ): bool,
            vol.Optional(
                CONF_LOG_XML_FN,
                default=self.create_default(options, CONF_LOG_XML_FN, "visonic_log_file.xml"),
            ): str,
            vol.Optional(
                CONF_LOG_CSV_FN,
                default=self.create_default(options, CONF_LOG_CSV_FN, "visonic_log_file.csv"),
            ): str,
            vol.Optional(
                CONF_LOG_MAX_ENTRIES,
                default=self.create_default(options, CONF_LOG_MAX_ENTRIES, 10000),
            ): int,
        }


    def create_parameters4(self, options: dict):
        """Create parameter set 4."""
        # B0 related parameters (PowerMaster only)
        return {
            vol.Optional(
                CONF_B0_ENABLE_MOTION_PROCESSING,
                default=self.create_default(options, CONF_B0_ENABLE_MOTION_PROCESSING, False),
            ): bool,
            vol.Optional(
                CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT,
                default=self.create_default(options, CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, 5),
            ): int,
            vol.Optional(
                CONF_B0_MIN_TIME_BETWEEN_TRIGGERS,
                default=self.create_default(options, CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, 30),
            ): int,
        }

    def create_schema_device(self):
        """Create schema device."""
        return vol.Schema(self.CONFIG_SCHEMA_DEVICE)


    def create_schema_ethernet(self):
        """Create schema ethernet."""
        return vol.Schema(self.CONFIG_SCHEMA_ETHERNET)


    def create_schema_usb(self):
        """Create schema usb."""
        return vol.Schema(self.CONFIG_SCHEMA_USB)


    def create_schema_parameters1(self):
        """Create schema parameters 1."""
        return vol.Schema(self.create_parameters1(self.options))


    def create_schema_parameters2(self):
        """Create schema parameters 2."""
        return vol.Schema(self.create_parameters2(self.options))


    def create_schema_parameters3(self):
        """Create schema parameters 3."""
        return vol.Schema(self.create_parameters3(self.options))


    def create_schema_parameters4(self):
        """Create schema parameters 4."""
        return vol.Schema(self.create_parameters4(self.options))

    def set_default_options(self, options: dict):
        """Set schema defaults."""
        for key in options:
            self.options[key] = options[key]
