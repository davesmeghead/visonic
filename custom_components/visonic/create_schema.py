"""Schema for the user input for connection to a Visonic PowerMax or PowerMaster Alarm System."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Final, Protocol

import voluptuous as vol
from voluptuous.schema_builder import UNDEFINED as VOL_UNDEFINED

from homeassistant.const import (
    CONF_CODE,
    CONF_EMAIL,
    CONF_EXTERNAL_URL,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PATH,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TYPE,
)
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.selector import (
    EntitySelector,  # pyright: ignore[reportUnknownVariableType]
    EntitySelectorConfig,
    SerialPortSelector,
)

from .const import (
    CONF_ALARM_NOTIFICATIONS,
    CONF_ARM_CODE_AUTO,
    CONF_ARM_HOME_ENABLED,
    CONF_ARM_NIGHT_ENABLED,
    CONF_DOWNLOAD_CODE,
    CONF_EMER_OFF_DELAY,
    CONF_EMULATION_MODE,
    CONF_ENABLE_REMOTE_ARM,
    CONF_ENABLE_REMOTE_DISARM,
    CONF_ENABLE_SENSOR_BYPASS,
    CONF_EPROM_ATTRIBUTES,
    CONF_ESPHOME_ENTITY_SELECT,
    CONF_EXCLUDE_SENSOR,
    CONF_EXCLUDE_SWITCH,
    CONF_FORCE_KEYPAD,
    CONF_IMAGE_MEDIA_PATH,
    CONF_IMAGE_SINGLE_FRAME,
    CONF_INSTANT_ARM_AWAY,
    CONF_INSTANT_ARM_HOME,
    CONF_LOG_CSV_FN,
    CONF_LOG_CSV_TITLE,
    CONF_LOG_DONE,
    CONF_LOG_EVENT,
    CONF_LOG_MAX_ENTRIES,
    CONF_LOG_REVERSE,
    CONF_LOG_XML_FN,
    CONF_MAGNET_CLOSED_DELAY,
    CONF_MOTION_OFF_DELAY,
    CONF_PANEL_NUMBER,
    CONF_PANEL_SERIAL,
    CONF_RETRY_CONNECTION_COUNT,
    CONF_RETRY_CONNECTION_DELAY,
    CONF_SERVER_HOST,
    CONF_SERVER_PORT,
    CONF_SIREN_SOUNDING,
    CONF_USER_CODE_SLOT,
    DEFAULT_CLOUD_SCAN_INTERVAL,
    DEFAULT_DEVICE_HOST,
    DEFAULT_DEVICE_PORT,
    DEFAULT_DEVICE_SERV_HOST,
    DEFAULT_DEVICE_SERV_PORT,
    DEFAULT_IMAGE_MEDIA_PATH,
    DEFAULT_PANEL_USER_CODE,
    FORM_CLOUD,
    FORM_DEVICE,
    FORM_ETHERNET,
    FORM_PARAM10,
    FORM_PARAM11,
    FORM_PARAM12,
    FORM_PARAM13,
    FORM_PARAM14,
    FORM_PARAM15,
    FORM_POWERLINK,
    FORM_SERIAL,
    FORM_TCP_DISCOVERED,
    FORM_TCP_SERVER,
)
from .visonic_types import (
    AvailableNotifications,
    DeviceType,
    EmulationMode,
    TriggerAlarmType,
)

# fmt: off

# These are the Forms that are able to be produced to allow the user to enter configuration data.
#    Each form lists the items on the form, in order.
#    The same item may be on multiple forms to support the different configuration methods
# These forms include those needed for discovery and zeroconf
FormItems: dict[str, list[str]] = {
    # Main connection type
    #     parameters in entry.data
    FORM_DEVICE: [CONF_TYPE, CONF_PANEL_NUMBER],
    # The connection choices
    #     parameters in entry.data
    #     FORM_TCP_DISCOVERED is only used for reconnection, host and port cannot be edited
    #     For those that support CONF_EMULATION_MODE then FORM_POWERLINK is shown next if Powerlink Emulation Mode is selected
    FORM_ETHERNET: [CONF_HOST, CONF_PORT, CONF_ESPHOME_ENTITY_SELECT, CONF_EMULATION_MODE, CONF_EXCLUDE_SENSOR, CONF_EXCLUDE_SWITCH],
    FORM_SERIAL: [CONF_PATH, CONF_EMULATION_MODE, CONF_EXCLUDE_SENSOR, CONF_EXCLUDE_SWITCH],
    FORM_CLOUD: [CONF_EXTERNAL_URL, CONF_EMAIL, CONF_PASSWORD, CONF_CODE, CONF_PANEL_SERIAL, CONF_SCAN_INTERVAL, CONF_EXCLUDE_SENSOR, CONF_EXCLUDE_SWITCH],
    FORM_TCP_SERVER: [CONF_SERVER_HOST, CONF_SERVER_PORT],
    FORM_TCP_DISCOVERED: [CONF_ESPHOME_ENTITY_SELECT, CONF_EMULATION_MODE, CONF_EXCLUDE_SENSOR, CONF_EXCLUDE_SWITCH],
    FORM_POWERLINK: [CONF_DOWNLOAD_CODE, CONF_USER_CODE_SLOT, CONF_EPROM_ATTRIBUTES],
    # Supporting forms to get the config
    #     parameters in entry.options
    FORM_PARAM10: [CONF_SIREN_SOUNDING, CONF_ALARM_NOTIFICATIONS, CONF_RETRY_CONNECTION_COUNT, CONF_RETRY_CONNECTION_DELAY],
    FORM_PARAM11: [CONF_MOTION_OFF_DELAY, CONF_MAGNET_CLOSED_DELAY, CONF_EMER_OFF_DELAY],
    FORM_PARAM12: [CONF_ARM_CODE_AUTO, CONF_FORCE_KEYPAD, CONF_ARM_HOME_ENABLED, CONF_ARM_NIGHT_ENABLED, CONF_INSTANT_ARM_AWAY,
                   CONF_INSTANT_ARM_HOME, CONF_ENABLE_REMOTE_ARM, CONF_ENABLE_REMOTE_DISARM, CONF_ENABLE_SENSOR_BYPASS],
    FORM_PARAM13: [CONF_LOG_EVENT, CONF_LOG_DONE, CONF_LOG_REVERSE, CONF_LOG_CSV_TITLE, CONF_LOG_XML_FN, CONF_LOG_CSV_FN, CONF_LOG_MAX_ENTRIES],
    FORM_PARAM14: [CONF_SIREN_SOUNDING, CONF_ALARM_NOTIFICATIONS],
    FORM_PARAM15: [CONF_IMAGE_MEDIA_PATH, CONF_IMAGE_SINGLE_FRAME],
}

# fmt: on

Validator = Callable[[Any], Any] | type | vol.Schema | selector.SelectSelector | selector.NumberSelector

class Marker(Protocol):
    """Marker for vol creation."""
    def __call__(self, key: str, *, default: Any) -> vol.Required | vol.Optional:
        """Callable prototype."""
        ...

@dataclass(frozen=True)
class ConfigItem:
    """Individual Configuration Item."""
    marker: Marker
    validator: Validator
    default: Any

# ---- Helper functions ----
def req(key: str, *, default: Any):
    """Required."""
    if default is VOL_UNDEFINED:
        return vol.Required(key)
    return vol.Required(key, default=default)

def opt(key: str, *, default: Any):
    """Optional."""
    if default is VOL_UNDEFINED:
        return vol.Optional(key)
    return vol.Optional(key, default=default)

def build_config_items() -> dict[str, ConfigItem]:
    """Build the config_item dictionary."""

    strlist = [
        el.value
        for el in AvailableNotifications
        if el != AvailableNotifications.ALWAYS
    ]
    alarm_type_members = [
        str(el.name).lower()
        for el in TriggerAlarmType
        if el
        not in [TriggerAlarmType.NONE, TriggerAlarmType.UNKNOWN]  # Exclude UNKNOWN and NONE
    ]
    EMULATION_MODE_OPTIONS = [mode.value for mode in EmulationMode]  # noqa: N806

    return {
        CONF_TYPE: ConfigItem(
            marker=req,
            validator=vol.In(
                    [
                        DeviceType.ETHERNET.title(),
                        DeviceType.SERIAL.title(),
#                        DeviceType.TCP_SERVER.title(),    # Disabled as incomplete and untested
                        DeviceType.CLOUD.title(),
                    ]
            ),
            default=DeviceType.ETHERNET.title(),
        ),
        CONF_PANEL_NUMBER: ConfigItem(
            marker=req,
            validator=cv.positive_int,
            default=0,
        ),
        CONF_PATH: ConfigItem(
            marker=req,
            validator=SerialPortSelector(),
            default=VOL_UNDEFINED,
        ),
        CONF_HOST: ConfigItem(
            marker=req,
            validator=str,
            default=DEFAULT_DEVICE_HOST,
        ),
        CONF_PORT: ConfigItem(
            marker=req,
            validator=str,
            default=str(DEFAULT_DEVICE_PORT),
        ),
        CONF_ESPHOME_ENTITY_SELECT: ConfigItem(
            marker=opt,
            validator=EntitySelector(
                EntitySelectorConfig(domain=["select"], multiple=False)
            ),
            default=VOL_UNDEFINED,
        ),
        CONF_EXTERNAL_URL: ConfigItem(
            marker=req,
            validator=str,
            default="visonic.tycomonitor.com",
        ),
        CONF_EMAIL: ConfigItem(
            marker=req,
            validator=str,
            default="",
        ),
        CONF_PASSWORD: ConfigItem(
            marker=req,
            validator=str,
            default="",
        ),
        CONF_CODE: ConfigItem(
            marker=req,
            validator=str,
            default=DEFAULT_PANEL_USER_CODE,
        ),
        CONF_PANEL_SERIAL: ConfigItem(
            marker=opt,
            validator=str,
            default="",
        ),
        CONF_SERVER_HOST: ConfigItem(
            marker=req,
            validator=str,
            default=DEFAULT_DEVICE_SERV_HOST,
        ),
        CONF_SERVER_PORT: ConfigItem(
            marker=req,
            validator=str,
            default=str(DEFAULT_DEVICE_SERV_PORT),
        ),
        CONF_EXCLUDE_SENSOR: ConfigItem(
            marker=opt,
            validator=str,
            default="",
        ),
        CONF_EXCLUDE_SWITCH: ConfigItem(
            marker=opt,
            validator=str,
            default="",
        ),
        CONF_EMULATION_MODE: ConfigItem(
            marker=opt,
            validator=vol.In(EMULATION_MODE_OPTIONS),
            default=EmulationMode.POWERLINK.value,
        ),
        CONF_DOWNLOAD_CODE: ConfigItem(
            marker=opt,
            validator=str,
            default=VOL_UNDEFINED,
        ),
        CONF_USER_CODE_SLOT: ConfigItem(
            marker=req,
            validator=selector.NumberSelector
                    (
                        selector.NumberSelectorConfig(
                            min=1,
                            max=48,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
            default=1,
        ),
        CONF_EPROM_ATTRIBUTES: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_SCAN_INTERVAL: ConfigItem(
            marker=req,
            validator=cv.positive_int,
            default=DEFAULT_CLOUD_SCAN_INTERVAL,
        ),
        CONF_SIREN_SOUNDING: ConfigItem(
            marker=opt,
            validator=selector.SelectSelector
                (  # type: ignore[type-arg]
                    selector.SelectSelectorConfig(
                        options=alarm_type_members,
                        multiple=True,
                        sort=True,
                        translation_key=CONF_SIREN_SOUNDING,
                    ),
                ),
            default=["intruder"],
        ),
        CONF_ALARM_NOTIFICATIONS: ConfigItem(
            marker=opt,
            validator=selector.SelectSelector
                (  # type: ignore[type-arg]
                    selector.SelectSelectorConfig(
                        options=strlist,
                        multiple=True,
                        sort=True,
                        translation_key=CONF_ALARM_NOTIFICATIONS,
                    ),
                ),
            default=[AvailableNotifications.CONNECTION.value, AvailableNotifications.SIREN.value],
        ),
        CONF_RETRY_CONNECTION_COUNT: ConfigItem(
            marker=opt,
            validator=selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=1000000, mode=selector.NumberSelectorMode.BOX)
            ),
            default=1,
        ),
        CONF_RETRY_CONNECTION_DELAY: ConfigItem(
            marker=opt,
            validator=selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=1000, mode=selector.NumberSelectorMode.BOX)
            ),
            default=90,
        ),
        CONF_MOTION_OFF_DELAY: ConfigItem(
            marker=opt,
            validator=selector.NumberSelector
                (
                    selector.NumberSelectorConfig(
                        min=1, max=3000, mode=selector.NumberSelectorMode.BOX
                    )
                ),
            default=30,
        ),
        CONF_MAGNET_CLOSED_DELAY: ConfigItem(
            marker=opt,
            validator=selector.NumberSelector
                (
                    selector.NumberSelectorConfig(
                        min=0, max=3000, mode=selector.NumberSelectorMode.BOX
                    )
                ),  # type: ignore[type-arg]
            default=5,
        ),
        CONF_EMER_OFF_DELAY: ConfigItem(
            marker=opt,
            validator=selector.NumberSelector
                (
                    selector.NumberSelectorConfig(
                        min=1, max=3000, mode=selector.NumberSelectorMode.BOX
                    )
                ),  # type: ignore[type-arg]
            default=30,
        ),
        CONF_ARM_CODE_AUTO: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_FORCE_KEYPAD: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_ARM_HOME_ENABLED: ConfigItem(
            marker=opt,
            validator=bool,
            default=True,
        ),
        CONF_ARM_NIGHT_ENABLED: ConfigItem(
            marker=opt,
            validator=bool,
            default=True,
        ),
        CONF_INSTANT_ARM_AWAY: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_INSTANT_ARM_HOME: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_ENABLE_REMOTE_ARM: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_ENABLE_REMOTE_DISARM: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_ENABLE_SENSOR_BYPASS: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_LOG_EVENT: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_LOG_DONE: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_LOG_REVERSE: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_LOG_CSV_TITLE: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
        CONF_LOG_XML_FN: ConfigItem(
            marker=opt,
            validator=str,
            default="visonic_log_file.xml",
        ),
        CONF_LOG_CSV_FN: ConfigItem(
            marker=opt,
            validator=str,
            default="visonic_log_file.csv",
        ),
        CONF_LOG_MAX_ENTRIES: ConfigItem(
            marker=opt,
            validator=cv.positive_int,
            default=10000,
        ),
        CONF_IMAGE_MEDIA_PATH: ConfigItem(
            marker=opt,
            validator=str,
            default=DEFAULT_IMAGE_MEDIA_PATH,
        ),
        CONF_IMAGE_SINGLE_FRAME: ConfigItem(
            marker=opt,
            validator=bool,
            default=False,
        ),
    }

# Build the config items once and then use them by just updating the default/values
_CONFIG_ITEMS = build_config_items()

class VisonicSchema:
    """Schema for the Visonic component."""

    def __init__(self) -> None:
        """Initialize the schema."""
        self._config_items = _CONFIG_ITEMS
        # Set all options to their defaults
        self._options = {
            key: deepcopy(config_item.default)
            for key, config_item in self._config_items.items()
            if config_item.default is not VOL_UNDEFINED
        }

    def _merge_options(self, overrides: dict[str, Any] | None) -> dict[str, Any]:
        base = deepcopy(self._options)
        return {**base, **(overrides or {})}

    def _build_schema(self, keys: list[str], overrides: dict[str, Any] | None = None) -> vol.Schema:
        """Build a voluptuous schema from config item definitions."""
        merged = self._merge_options(overrides)
        schema: dict[Any, Any] = {}

        for key in keys:
            if key not in self._config_items:
                raise KeyError(f"Unknown schema key: {key}")
            config_item = self._config_items[key]
            default = merged.get(key, config_item.default)
            marker = config_item.marker(key, default=default)
            schema[marker] = config_item.validator

        return vol.Schema(schema)

    def set_base_options(self, options: dict[str, Any] | None):
        """Set schema defaults."""
        if options:
            self._options = {**self._options, **options}

    def get_options(self) -> dict[str, Any]:
        """Get the current configuration as a dictionary."""
        return deepcopy(self._options)

    def create_schema(
        self,
        item: str,
        overrides: dict[str, Any] | None = None,
    ) -> vol.Schema:
        """Create a schema for the given menu item."""
        if item not in FormItems:
            raise ValueError(f"Unknown menu item: {item}")
        return self._build_schema(FormItems[item], overrides)

    def is_valid(self, item: str) -> bool:
        """Validate a menu item."""
        return item in FormItems
