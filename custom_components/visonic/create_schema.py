"""Schema for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

import logging

import voluptuous as vol
from typing import Any

from homeassistant.const import CONF_DEVICE, CONF_HOST, CONF_PATH, CONF_PORT, CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers import config_validation as cv
from homeassistant.util.yaml.objects import NodeListClass
from homeassistant.helpers import selector
from homeassistant.const import CONF_NAME, CONF_SOURCE, UnitOfTime

from homeassistant.helpers.selector import (
    ObjectSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    validate_selector,
)

from .const import (
    CONF_EXCLUDE_SENSOR,
    CONF_EXCLUDE_X10,
    CONF_ENABLE_REMOTE_ARM,
    CONF_ENABLE_REMOTE_DISARM,
    CONF_ENABLE_SENSOR_BYPASS,
    CONF_ARM_CODE_AUTO,
    CONF_FORCE_KEYPAD,
    CONF_ARM_HOME_ENABLED,
    CONF_ARM_NIGHT_ENABLED,
    CONF_INSTANT_ARM_AWAY,
    CONF_INSTANT_ARM_HOME,
    CONF_AUTO_SYNC_TIME,
    CONF_EEPROM_ATTRIBUTES,
    CONF_DEVICE_BAUD,
    CONF_PANEL_NUMBER,
    CONF_DEVICE_TYPE,
    CONF_DOWNLOAD_CODE,
    CONF_FORCE_AUTOENROLL,
    CONF_EMULATION_MODE,
    CONF_LANGUAGE,
    CONF_MOTION_OFF_DELAY,
    CONF_MAGNET_CLOSED_DELAY,
    CONF_EMER_OFF_DELAY,
    CONF_SIREN_SOUNDING,
    CONF_LOG_CSV_FN,
    CONF_LOG_CSV_TITLE,
    CONF_LOG_DONE,
    CONF_LOG_EVENT,
    CONF_LOG_MAX_ENTRIES,
    CONF_LOG_REVERSE,
    CONF_LOG_XML_FN,
    CONF_ALARM_NOTIFICATIONS,
    CONF_RETRY_CONNECTION_COUNT,
    CONF_RETRY_CONNECTION_DELAY,
    DEFAULT_DEVICE_BAUD,
    DEFAULT_DEVICE_HOST,
    DEFAULT_DEVICE_PORT,
    DEFAULT_DEVICE_TOPIC,
    DEFAULT_DEVICE_USB,
    AvailableNotifications,
    AvailableNotificationConfig,
    available_emulation_modes,
)

TIME_UNITS = [
    UnitOfTime.SECONDS,
    UnitOfTime.MINUTES,
    UnitOfTime.HOURS,
    UnitOfTime.DAYS,
]

_LOGGER = logging.getLogger(__name__)

available_siren_values = [
    "intruder",
    "tamper",
    "fire",
    "emergency",
    "gas",
    "flood",
    "x10",
    "panic"
]

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
            **self.create_parameters10(self.options),
            **self.create_parameters11(self.options),
            **self.create_parameters12(self.options),
        }
        for key in initialise:
            d = key.default()
            self.options[key] = d

    def create_default(self, options: dict, key: str, default: Any):
        """Create a default value for the parameter using the previous value that the user entered."""
        if options is not None and key in options:
            # if type(options[key]) is not type(default):
            # # create_default types are different for = siren_sounding <class 'list'> <class 'set'> ['intruder', 'panic', 'gas'] {'intruder'}
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
                CONF_EMULATION_MODE,
                default=self.create_default(options, CONF_EMULATION_MODE, available_emulation_modes[0]),
            ): vol.In(available_emulation_modes),
        }

    # These are only used on creation of the component and only for Powerlink
    def create_parameters2(self, options: dict):
        """Create parameter set 2."""
        # Panel settings - can only be set on creation
        return {
            vol.Optional(
                CONF_DOWNLOAD_CODE, default=self.create_default(options, CONF_DOWNLOAD_CODE, "")
            ): str,
            vol.Optional(
                CONF_FORCE_AUTOENROLL,
                default=self.create_default(options, CONF_FORCE_AUTOENROLL, False),
            ): bool,
            vol.Optional(
                CONF_AUTO_SYNC_TIME,
                default=self.create_default(options, CONF_AUTO_SYNC_TIME, True),
            ): bool,
            vol.Optional(
                CONF_EEPROM_ATTRIBUTES,
                default=self.create_default(options, CONF_EEPROM_ATTRIBUTES, False),
            ): bool,
        }

    def create_parameters10(self, options: dict):
        """Create parameter set 10."""
        # Panel settings - can be modified/edited
        return {
            vol.Optional(
                CONF_MOTION_OFF_DELAY,
                default=self.create_default(options, CONF_MOTION_OFF_DELAY, 120),
            ): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=3000, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(
                CONF_MAGNET_CLOSED_DELAY,
                default=self.create_default(options, CONF_MAGNET_CLOSED_DELAY, 5),
            ): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=3000, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(
                CONF_EMER_OFF_DELAY,
                default=self.create_default(options, CONF_EMER_OFF_DELAY, 120),
            ): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=3000, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(
                CONF_SIREN_SOUNDING, 
                default=self.create_default(options, CONF_SIREN_SOUNDING, ["intruder"]),
            ): selector.SelectSelector(selector.SelectSelectorConfig(options=available_siren_values, multiple=True, sort=True, translation_key=CONF_SIREN_SOUNDING)),
            #vol.Optional(
            #    CONF_SIREN_SOUNDING,
            #    default=self.create_default(options, CONF_SIREN_SOUNDING, ["intruder"]),
            #): cv.multi_select(available_siren_values),
            vol.Optional(
                CONF_ALARM_NOTIFICATIONS,
                default=self.create_default(options, CONF_ALARM_NOTIFICATIONS, [AvailableNotifications.CONNECTION_PROBLEM, AvailableNotifications.SIREN]),
            ): cv.multi_select(AvailableNotificationConfig),
            # https://developers.home-assistant.io/docs/data_entry_flow_index/#show-form
            vol.Optional(
                CONF_RETRY_CONNECTION_COUNT,
                default=self.create_default(options, CONF_RETRY_CONNECTION_COUNT, 1),
            ): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=1000, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(
                CONF_RETRY_CONNECTION_DELAY,
                default=self.create_default(options, CONF_RETRY_CONNECTION_DELAY, 90),
            ): selector.NumberSelector(selector.NumberSelectorConfig(min=5, max=1000, mode=selector.NumberSelectorMode.BOX)),
        }

    def create_parameters11(self, options: dict):
        """Create parameter set 11."""
        # Panel settings - can be modified/edited
        return {
            vol.Optional(
                CONF_ARM_CODE_AUTO,
                default=self.create_default(options, CONF_ARM_CODE_AUTO, False),
            ): bool,
            vol.Optional(
                CONF_FORCE_KEYPAD, default=self.create_default(options, CONF_FORCE_KEYPAD, False)
            ): bool,
            vol.Optional(
                CONF_ARM_HOME_ENABLED,
                default=self.create_default(options, CONF_ARM_HOME_ENABLED, True),
            ): bool,
            vol.Optional(
                CONF_ARM_NIGHT_ENABLED,
                default=self.create_default(options, CONF_ARM_NIGHT_ENABLED, True),
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

    def create_parameters12(self, options: dict):
        """Create parameter set 12."""
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

    def create_schema_parameters10(self):
        """Create schema parameters 10."""
        return vol.Schema(self.create_parameters10(self.options))

    def create_schema_parameters11(self):
        """Create schema parameters 11."""
        return vol.Schema(self.create_parameters11(self.options))

    def create_schema_parameters12(self):
        """Create schema parameters 12."""
        return vol.Schema(self.create_parameters12(self.options))

    def set_default_options(self, options: dict):
        """Set schema defaults."""
        for key in options:
            self.options[key] = options[key]
