""" Schema for the connection to a Visonic PowerMax or PowerMaster Alarm System """

import logging
import voluptuous as vol
from homeassistant.util.yaml.objects import NodeListClass

from homeassistant.helpers import config_validation as cv

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PATH, CONF_DEVICE
from .const import *

_LOGGER = logging.getLogger(__name__)

# These are the options that the user entered
options = {}

# Create a default value for the parameter using the previous value that the user entered
def create_default(options, key, default):
    if options is not None and key in options:
        if type(options[key]) is not type(default):
            # create_default types are different for = siren_sounding <class 'list'> <class 'set'> ['intruder', 'panic', 'gas'] {'intruder'}
            _LOGGER.debug(
                "create_default types are different for = %s %s %s %s %s", key, type(options[key]), options[key], type(default), default
            )
        if isinstance(options[key], list) or isinstance(options[key], NodeListClass):
            # _LOGGER.debug("      its a list")
            if CONF_SIREN_SOUNDING == key:
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
def create_parameters1(options):
    # Panel settings - can only be set on creation
    return {
        vol.Required(CONF_LANGUAGE, default=create_default(options, CONF_LANGUAGE, "EN")): str,
        vol.Optional(CONF_EXCLUDE_SENSOR, default=create_default(options, CONF_EXCLUDE_SENSOR, ""),): str,
        vol.Optional(CONF_EXCLUDE_X10, default=create_default(options, CONF_EXCLUDE_X10, "")): str,
        vol.Optional(CONF_DOWNLOAD_CODE, default=create_default(options, CONF_DOWNLOAD_CODE, "")): str,
        vol.Optional(CONF_FORCE_STANDARD, default=create_default(options, CONF_FORCE_STANDARD, False),): bool,
        vol.Optional(CONF_FORCE_AUTOENROLL, default=create_default(options, CONF_FORCE_AUTOENROLL, False),): bool,
        vol.Optional(CONF_AUTO_SYNC_TIME, default=create_default(options, CONF_AUTO_SYNC_TIME, True),): bool,
    }


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


def create_parameters2(options):
    # Panel settings - can be modified/edited
    return {
        vol.Optional(CONF_MOTION_OFF_DELAY, default=create_default(options, CONF_MOTION_OFF_DELAY, 120),): int,
        vol.Optional(CONF_SIREN_SOUNDING, default=create_default(options, CONF_SIREN_SOUNDING, ["intruder"]),): cv.multi_select(
            available_siren_values
        ),
        vol.Optional(CONF_OVERRIDE_CODE, default=create_default(options, CONF_OVERRIDE_CODE, "")): str,
        vol.Optional(CONF_ARM_CODE_AUTO, default=create_default(options, CONF_ARM_CODE_AUTO, False),): bool,
        vol.Optional(CONF_FORCE_KEYPAD, default=create_default(options, CONF_FORCE_KEYPAD, False)): bool,
        vol.Optional(CONF_INSTANT_ARM_AWAY, default=create_default(options, CONF_INSTANT_ARM_AWAY, False),): bool,
        vol.Optional(CONF_INSTANT_ARM_HOME, default=create_default(options, CONF_INSTANT_ARM_HOME, False),): bool,
        vol.Optional(CONF_ENABLE_REMOTE_ARM, default=create_default(options, CONF_ENABLE_REMOTE_ARM, False),): bool,
        vol.Optional(CONF_ENABLE_REMOTE_DISARM, default=create_default(options, CONF_ENABLE_REMOTE_DISARM, False),): bool,
        vol.Optional(CONF_ENABLE_SENSOR_BYPASS, default=create_default(options, CONF_ENABLE_SENSOR_BYPASS, False),): bool,
    }


def create_parameters3(options):
    # Log file parameters
    return {
        vol.Optional(CONF_LOG_EVENT, default=create_default(options, CONF_LOG_EVENT, False)): bool,
        vol.Optional(CONF_LOG_DONE, default=create_default(options, CONF_LOG_DONE, False)): bool,
        vol.Optional(CONF_LOG_REVERSE, default=create_default(options, CONF_LOG_REVERSE, False)): bool,
        vol.Optional(CONF_LOG_CSV_TITLE, default=create_default(options, CONF_LOG_CSV_TITLE, False),): bool,
        vol.Optional(CONF_LOG_XML_FN, default=create_default(options, CONF_LOG_XML_FN, "")): str,
        vol.Optional(CONF_LOG_CSV_FN, default=create_default(options, CONF_LOG_CSV_FN, "")): str,
        vol.Optional(CONF_LOG_MAX_ENTRIES, default=create_default(options, CONF_LOG_MAX_ENTRIES, 10000),): int,
    }


def create_parameters4(options):
    # B0 related parameters (PowerMaster only)
    return {
        vol.Optional(CONF_B0_ENABLE_MOTION_PROCESSING, default=create_default(options, CONF_B0_ENABLE_MOTION_PROCESSING, False),): bool,
        vol.Optional(CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, default=create_default(options, CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, 5),): int,
        vol.Optional(CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, default=create_default(options, CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, 30),): int,
    }


# These are only used on creation of the component
def create_parameters1cv(options):
    # Panel settings - can only be set on creation
    return {
        vol.Required(CONF_LANGUAGE, default=create_default(options, CONF_LANGUAGE, "EN")): cv.string,
        vol.Optional(CONF_EXCLUDE_SENSOR, default=create_default(options, CONF_EXCLUDE_SENSOR, ""),): cv.string,
        vol.Optional(CONF_EXCLUDE_X10, default=create_default(options, CONF_EXCLUDE_X10, "")): cv.string,
        vol.Optional(CONF_DOWNLOAD_CODE, default=create_default(options, CONF_DOWNLOAD_CODE, "")): cv.string,
        vol.Optional(CONF_FORCE_STANDARD, default=create_default(options, CONF_FORCE_STANDARD, False),): cv.boolean,
        vol.Optional(CONF_FORCE_AUTOENROLL, default=create_default(options, CONF_FORCE_AUTOENROLL, False),): cv.boolean,
        vol.Optional(CONF_AUTO_SYNC_TIME, default=create_default(options, CONF_AUTO_SYNC_TIME, True),): cv.boolean,
    }


def create_parameters2cv(options):
    # Panel settings - can be modified/edited
    return {
        vol.Optional(CONF_MOTION_OFF_DELAY, default=create_default(options, CONF_MOTION_OFF_DELAY, 120),): cv.positive_int,
        vol.Optional(CONF_SIREN_SOUNDING, default=create_default(options, CONF_SIREN_SOUNDING, ["intruder"]),): cv.multi_select(
            available_siren_values
        ),
        vol.Optional(CONF_OVERRIDE_CODE, default=create_default(options, CONF_OVERRIDE_CODE, "")): cv.string,
        vol.Optional(CONF_ARM_CODE_AUTO, default=create_default(options, CONF_ARM_CODE_AUTO, False),): cv.boolean,
        vol.Optional(CONF_FORCE_KEYPAD, default=create_default(options, CONF_FORCE_KEYPAD, False)): cv.boolean,
        vol.Optional(CONF_INSTANT_ARM_AWAY, default=create_default(options, CONF_INSTANT_ARM_AWAY, False),): cv.boolean,
        vol.Optional(CONF_INSTANT_ARM_HOME, default=create_default(options, CONF_INSTANT_ARM_HOME, False),): cv.boolean,
        vol.Optional(CONF_ENABLE_REMOTE_ARM, default=create_default(options, CONF_ENABLE_REMOTE_ARM, False),): cv.boolean,
        vol.Optional(CONF_ENABLE_REMOTE_DISARM, default=create_default(options, CONF_ENABLE_REMOTE_DISARM, False),): cv.boolean,
        vol.Optional(CONF_ENABLE_SENSOR_BYPASS, default=create_default(options, CONF_ENABLE_SENSOR_BYPASS, False),): cv.boolean,
    }


def create_parameters3cv(options):
    # Log file parameters
    return {
        vol.Optional(CONF_LOG_EVENT, default=create_default(options, CONF_LOG_EVENT, False)): cv.boolean,
        vol.Optional(CONF_LOG_DONE, default=create_default(options, CONF_LOG_DONE, False)): cv.boolean,
        vol.Optional(CONF_LOG_REVERSE, default=create_default(options, CONF_LOG_REVERSE, False)): cv.boolean,
        vol.Optional(CONF_LOG_CSV_TITLE, default=create_default(options, CONF_LOG_CSV_TITLE, False),): cv.boolean,
        vol.Optional(CONF_LOG_XML_FN, default=create_default(options, CONF_LOG_XML_FN, "")): cv.string,
        vol.Optional(CONF_LOG_CSV_FN, default=create_default(options, CONF_LOG_CSV_FN, "")): cv.string,
        vol.Optional(CONF_LOG_MAX_ENTRIES, default=create_default(options, CONF_LOG_MAX_ENTRIES, 10000),): cv.positive_int,
    }


def create_parameters4cv(options):
    # B0 related parameters (PowerMaster only)
    return {
        vol.Optional(
            CONF_B0_ENABLE_MOTION_PROCESSING, default=create_default(options, CONF_B0_ENABLE_MOTION_PROCESSING, False),
        ): cv.boolean,
        vol.Optional(
            CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, default=create_default(options, CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, 5),
        ): cv.positive_int,
        vol.Optional(
            CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, default=create_default(options, CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, 30),
        ): cv.positive_int,
    }


CONFIG_SCHEMA_DEVICE = {vol.Required(CONF_DEVICE_TYPE, default="Ethernet"): vol.In(["Ethernet", "USB"])}

CONFIG_SCHEMA_ETHERNET = {
    vol.Required(CONF_HOST, default=DEFAULT_DEVICE_HOST): str,
    vol.Required(CONF_PORT, default=DEFAULT_DEVICE_PORT): str,
}

CONFIG_SCHEMA_USB = {
    vol.Required(CONF_PATH, default=DEFAULT_DEVICE_USB): str,
    vol.Optional(CONF_DEVICE_BAUD, default=DEFAULT_DEVICE_BAUD): str,
}

# Schema for config file parsing and access
DEVICE_SOCKET_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_TYPE): "ethernet",
        vol.Required(CONF_HOST, default=DEFAULT_DEVICE_HOST): cv.string,
        vol.Required(CONF_PORT, default=DEFAULT_DEVICE_PORT): cv.port,
    }
)

DEVICE_USB_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_TYPE): "usb",
        vol.Required(CONF_PATH, default=DEFAULT_DEVICE_USB): cv.string,
        vol.Optional(CONF_DEVICE_BAUD, default=DEFAULT_DEVICE_BAUD): cv.string,
    }
)

VISONIC_ID_LIST_SCHEMA = vol.Schema([int])
VISONIC_STRING_LIST_SCHEMA = vol.Schema([str])

CONFIG_SCHEMA = {vol.Required(CONF_DEVICE): vol.Any(DEVICE_SOCKET_SCHEMA, DEVICE_USB_SCHEMA)}


def create_schema_device():
    return vol.Schema(CONFIG_SCHEMA_DEVICE)


def create_schema_ethernet():
    return vol.Schema(CONFIG_SCHEMA_ETHERNET)


def create_schema_usb():
    return vol.Schema(CONFIG_SCHEMA_USB)


def create_schema_parameters1():
    global options
    return vol.Schema(create_parameters1(options))


def create_schema_parameters2():
    global options
    return vol.Schema(create_parameters2(options))


def create_schema_parameters3():
    global options
    return vol.Schema(create_parameters3(options))


def create_schema_parameters4():
    global options
    return vol.Schema(create_parameters4(options))


def create_schema():
    global options
    # Miss out CONFIG_SCHEMA_PARAMETERS1a and use CONFIG_SCHEMA
    dest = {
        **CONFIG_SCHEMA,
        **create_parameters1cv(options),
        **create_parameters2cv(options),
        **create_parameters3cv(options),
        **create_parameters4cv(options),
    }
    return dest


def set_defaults(opt):
    global options
    for key in opt:
        options[key] = opt[key]


# initially populate the options data with the default values from all possible settings

initialise = {
    **CONFIG_SCHEMA_DEVICE,
    **CONFIG_SCHEMA_ETHERNET,
    **CONFIG_SCHEMA_USB,
    **create_parameters1(options),
    **create_parameters2(options),
    **create_parameters3(options),
    **create_parameters4(options),
}

for key in initialise:
    d = key.default()
    options[key] = d

