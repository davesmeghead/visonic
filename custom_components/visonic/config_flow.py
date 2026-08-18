"""Configuration flow for the user input for connecting to Visonic PowerMax and PowerMaster alarm systems.

Home Assistant config and options flow handling for Visonic PowerMax/PowerMaster alarm panel connections.
"""
import logging
import re
import traceback
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import (
    CONN_CLASS_LOCAL_POLL,
    HANDLERS,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_CODE,
    CONF_DEVICE,
    CONF_EMAIL,
    CONF_EXTERNAL_URL,
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PATH,
    CONF_PORT,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import SerialPortSelector
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.helpers.typing import DiscoveryInfoType

from .connection_test import ConnectionTest
from .const import (
    CONF_ARM_HOME_ENABLED,
    CONF_CLOUD_APP_ID,
    CONF_DOWNLOAD_CODE,
    CONF_EMULATION_MODE,
    CONF_ENABLE_REMOTE_ARM,
    CONF_ENABLE_REMOTE_DISARM,
    CONF_ENABLE_SENSOR_BYPASS,
    CONF_ESPHOME_ENTITY_SELECT,
    CONF_EXCLUDE_SENSOR,
    CONF_EXCLUDE_SWITCH,
    CONF_PANEL_NUMBER,
    CONF_PANEL_SERIAL,
    CONF_SERVER_HOST,
    CONF_SERVER_NUMBER,
    CONF_SERVER_PORT,
    CONF_USAGE,
    CONF_USER_CODE_SLOT,
    DOMAIN,
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
    TEXT_TITLE,
    TRANSLATE_ABORT_ALREADY_CONFIGURED,
    TRANSLATE_ABORT_CANNOT_CONFIG_DISCOVERED,
    TRANSLATE_ABORT_CANNOT_EDIT_SERVER,
    TRANSLATE_ABORT_EMULATION_MODE,
    TRANSLATE_ABORT_INVALID_DEVICE_TYPE,
    TRANSLATE_ABORT_UNKNOWN,
    TRANSLATE_ERROR_DL_CODE_INVALID,
    TRANSLATE_ERROR_EMAIL_INVALID,
    TRANSLATE_ERROR_ETHERNET_SERVER_OR_SERIAL,
    TRANSLATE_ERROR_EXCLUSIONS_INVALID,
    TRANSLATE_ERROR_SELECT_INVALID,
    TRANSLATE_ERROR_SETTINGS_INVALID,
    TRANSLATE_ERROR_SETTINGS_MISSING,
    TRANSLATE_EXCEPTION_NO_UNIQUE_NUMBER_IN_CONFIG,
    VISONIC_CLOUD_SERVER,
    VISONIC_UNIQUE_NAME,
)
from .create_schema import FormItems, VisonicSchema
from .exceptions import VisonicException
from .utils import parse_int_list
from .visonic_types import DeviceType, EmulationMode

_LOGGER = logging.getLogger(__name__)

SERIAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE): SerialPortSelector(),
    }
)


DEFAULT_TITLE = "Visonic Security System"
VISONIC_CONFIG_VERSION = 6

MAP_DEVICE_TO_CONFIG_STEP: dict[DeviceType, str] = {
    DeviceType.ETHERNET: FORM_ETHERNET,
    DeviceType.TCP_DISCOVERED: FORM_TCP_DISCOVERED,
    DeviceType.SERIAL: FORM_SERIAL,
    DeviceType.TCP_SERVER: FORM_TCP_SERVER,
    DeviceType.CLOUD: FORM_CLOUD,
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class VisonicHandler:
    """Shared logic for ConfigFlow and OptionsFlow.

    Assumes subclasses implement Home Assistant flow methods:
    - async_show_form
    - async_abort
    - async_create_entry
    - add_suggested_values_to_schema

    """

    def __init__(self, *args, **kwargs) -> None:
        """Initialize the mixin logic."""
        super().__init__(*args, **kwargs)
        self.myschema = VisonicSchema()
        # Set some defaults
        self.config_data: dict[str, Any] = {}
        self.config_options: dict[str, Any] = {}
        self.step_sequence: list[str] = []
        self.current_pos = -1
        self.connection_tester = ConnectionTest()

    def parse_emulation_mode(self, raw: str | None) -> EmulationMode:
        """Parse emulation mode safely."""
        if not raw:
            return EmulationMode.POWERLINK
        norm = raw.strip().upper().replace("_", "-").replace(" ", "-")
        mapping = {
            "MIN": EmulationMode.MINIMAL,
            "MINIMAL": EmulationMode.MINIMAL,
            "STANDARD": EmulationMode.STANDARD,
            "POWERLINK": EmulationMode.POWERLINK,
            "POWER-LINK": EmulationMode.POWERLINK,
        }
        return mapping.get(norm, EmulationMode.POWERLINK)

    def baud_select_entity_or_empty(self, hass: HomeAssistant, value: str):
        """Validator that checks entity is empty or a valid select entity."""
        if not value or value == "":
            return ""  # allow empty

        entity = cv.entity_id(value)
        if not entity.startswith("select."):
            raise vol.Invalid("Entity must be from the select domain")

        # Get current entity
        state_obj = hass.states.get(entity)
        if state_obj is None:
            raise vol.Invalid(f"Entity {entity} not found")

        # Get available options
        options = state_obj.attributes.get("options", [])
        if not options:
            raise vol.Invalid("No options found for selected entity")

        # Check if the available options are valid
        required = {"9600", "38400"}
        if not required.issubset(set(options)):
            raise vol.Invalid(f"Invalid options for {entity}, options: {options}")
        return entity

    def validate_input(self, hass: HomeAssistant, data: dict[str, Any], dt: DeviceType | None = None) -> str | None:  # noqa: C901
        """Validate the 'data' input (but not the 'options')."""
        # This does not validate the prescence of CONF, if they are there then they are tested
        device_type = data.get(CONF_TYPE, dt)
        if device_type is not None:
            match DeviceType(device_type):
                case DeviceType.ETHERNET | DeviceType.TCP_DISCOVERED:
                    # There must be a host and port
                    if not data.get(CONF_HOST) or not str(data.get(CONF_PORT)).isdigit():
                        return TRANSLATE_ERROR_SETTINGS_MISSING
                case DeviceType.SERIAL:
                    # There must be a path to the serial device on this machine
                    if not data.get(CONF_PATH):
                        return TRANSLATE_ERROR_SETTINGS_MISSING
                case DeviceType.CLOUD:
                    # Must be login settings to cloud server
                    if not data.get(CONF_EXTERNAL_URL) or not data.get(CONF_EMAIL) or not data.get(CONF_PASSWORD) or not data.get(CONF_CODE):
                        return TRANSLATE_ERROR_SETTINGS_MISSING
                    em = data.get(CONF_EMAIL, "").strip()
                    if not _EMAIL_RE.fullmatch(em):  # check email address
                        return TRANSLATE_ERROR_EMAIL_INVALID
                    url = data.get(CONF_EXTERNAL_URL)
                    if '.' not in url:  # Must be a . in a url address
                        return TRANSLATE_ERROR_SETTINGS_INVALID
                    ps: str = data.get(CONF_PANEL_SERIAL, "")
                    if len(ps) != 0 and len(ps) != 6: # must be a length of 0 or 6
                        return TRANSLATE_ERROR_SETTINGS_INVALID
                    cd: str = data.get(CONF_CODE, "")
                    if len(cd) != 4 or not cd.isdigit():  # code must be 4 characters and be a number
                        return TRANSLATE_ERROR_SETTINGS_INVALID
                case DeviceType.TCP_SERVER:
                    # There must be a host and port
                    if not data.get(CONF_SERVER_HOST) or not str(data.get(CONF_SERVER_PORT)).isdigit():
                        return TRANSLATE_ERROR_SETTINGS_MISSING

        if CONF_DOWNLOAD_CODE in data:
            c = data.get(CONF_DOWNLOAD_CODE)
            if len(c) != 0 and len(c) != 4:
                return TRANSLATE_ERROR_DL_CODE_INVALID
            if len(c) == 4 and not c.isdigit():
                return TRANSLATE_ERROR_DL_CODE_INVALID

        if CONF_USER_CODE_SLOT in data:
            c: int = int(data.get(CONF_USER_CODE_SLOT))
            if c <= 0 or c > 48:  # Powermaster panels allow 48 usercodes
                return TRANSLATE_ERROR_DL_CODE_INVALID

        if CONF_EXCLUDE_SENSOR in data:
            try:
                parse_int_list(data.get(CONF_EXCLUDE_SENSOR, ""))
            except ValueError:
                return TRANSLATE_ERROR_EXCLUSIONS_INVALID

        if CONF_EXCLUDE_SWITCH in data:
            try:
                parse_int_list(data.get(CONF_EXCLUDE_SWITCH, ""))
            except ValueError:
                return TRANSLATE_ERROR_EXCLUSIONS_INVALID

        if CONF_EMULATION_MODE in data:
            try:
                _val = EmulationMode(data.get(CONF_EMULATION_MODE))
            except ValueError:
                return TRANSLATE_ABORT_EMULATION_MODE

        if CONF_ESPHOME_ENTITY_SELECT in data:
            try:
                select_entity = data.get(CONF_ESPHOME_ENTITY_SELECT, "")
                # Use as validator
                self.baud_select_entity_or_empty(hass, select_entity)
            except vol.Invalid:
                return TRANSLATE_ERROR_SELECT_INVALID

        return None

    def show_form(
        self,
        step: str = FORM_DEVICE,
        errors: dict[str, str] | None = None,
        description_placeholders: dict[str, str] | None = None,
        values: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show the requested form step."""
        values = values or {}
        if not isinstance(self, (VisonicConfigFlow | VisonicOptionsFlowHandler)):
            # This prevents things that should never happen: wrong types, but also helps validate the functions
            raise VisonicException
        if not self.myschema.is_valid(step):
            # This makes sure that create_schema will create a valid schema
            return self.async_abort(reason=TRANSLATE_ABORT_INVALID_DEVICE_TYPE)
        return self.async_show_form(
            step_id=step,
            data_schema=self.add_suggested_values_to_schema(
                self.myschema.create_schema(
                    step,
                    #values,  # I set the default values
                ),
                values, #  Set the suggested values
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    def _create_step_sequence(self, s: EmulationMode | None = None, exclude_powerlink: bool = False) -> list[str]:
        """Generate the step sequence based on the device type and emulation mode selected."""
        # This is used for the options dict
        self.current_pos = -1
        device_type = self.config_data.get(CONF_TYPE)
        if device_type is not None:
            match DeviceType(device_type):
                case DeviceType.ETHERNET | DeviceType.TCP_DISCOVERED | DeviceType.SERIAL:
                    match(s):
                        case EmulationMode.POWERLINK:
                            if exclude_powerlink:
                                return [FORM_PARAM10, FORM_PARAM11, FORM_PARAM12, FORM_PARAM13, FORM_PARAM15]
                            return [FORM_POWERLINK, FORM_PARAM10, FORM_PARAM11, FORM_PARAM12, FORM_PARAM13, FORM_PARAM15]
                        case EmulationMode.STANDARD:
                            return [FORM_PARAM10, FORM_PARAM11, FORM_PARAM12, FORM_PARAM13, FORM_PARAM15]
                        case EmulationMode.MINIMAL:
                            return [FORM_PARAM10, FORM_PARAM11]
                case DeviceType.CLOUD:
                    return [FORM_PARAM14, FORM_PARAM11, FORM_PARAM12, FORM_PARAM13]
                case DeviceType.TCP_SERVER:
                    # The tcp server does not have any options steps, only a single data step.
                    pass
        return []

    def _goto_next_step(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Advance to the next step in the sequence."""
        # This is used for the options dict
        if not isinstance(self, (VisonicConfigFlow | VisonicOptionsFlowHandler)):
            # This prevents things that should never happen: wrong types, but also helps validate the functions
            raise VisonicException
        if user_input is not None:
            self.config_options |= user_input
        self.current_pos += 1
        if self.current_pos >= len(self.step_sequence):
            try:
                # Complete the forms, validate the settings and either abort or create the entry
                if (err:=self.validate_input(self.hass, self.config_data)) is not None:
                    return self.async_abort(reason=err)
                final_config_data = dict(self.config_data).copy()
                final_config_options = dict(self.config_options).copy()
                title = final_config_data.get(TEXT_TITLE, DEFAULT_TITLE)
                if isinstance(self, VisonicConfigFlow):
                    return self.async_create_entry(title=title, data=final_config_data, options=final_config_options)
                return self.async_create_entry(title=title, data=final_config_options)
            except (ValueError, TypeError) as ex:
                _LOGGER.debug("Exception %s", ex)
                return self.async_abort(reason=TRANSLATE_ERROR_SETTINGS_MISSING)

        return self.show_form(step=self.step_sequence[self.current_pos], values=self.config_options)

    async def async_step_parameters10(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Parameters step 10."""
        return self._goto_next_step(user_input)

    async def async_step_parameters11(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Parameters step 11."""
        return self._goto_next_step(user_input)

    async def async_step_parameters12(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Parameters step 12."""
        return self._goto_next_step(user_input)

    async def async_step_parameters13(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Parameters step 13."""
        return self._goto_next_step(user_input)

    async def async_step_parameters14(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Parameters step 14."""
        return self._goto_next_step(user_input)

    async def async_step_parameters15(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Parameters step 15."""
        return self._goto_next_step(user_input)


@HANDLERS.register(DOMAIN)
class VisonicConfigFlow(VisonicHandler, ConfigFlow, domain=DOMAIN):
    """Handle a Visonic Config Flow."""
    # All functions here need to save user data in to the "data" config_entry and not the "options"

    VERSION = VISONIC_CONFIG_VERSION
    CONNECTION_CLASS = CONN_CLASS_LOCAL_POLL

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):  # -> OptionsFlowHandler
        """Get the options flow for this handler."""
        # _LOGGER.debug("Visonic async_get_options_flow")
        return VisonicOptionsFlowHandler()

    # The initial step for configuration, this is what HA first calls
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a user config flow."""
        # is this a raw configuration (not called from importing yaml)
        if user_input is None:
            # _LOGGER.debug("Visonic in async_step_user - trigger user input")
            return self.show_form(step=FORM_DEVICE, values=self.config_data)
        # importing a yaml config setup
        if (err:=self.validate_input(self.hass, user_input)) is not None:
            return self.async_abort(reason=err)
        title = self.config_data.get(TEXT_TITLE, DEFAULT_TITLE)
        return self.async_create_entry(title=title, data=self.config_data, options=self.config_options)

    # ask the user: ethernet, server, cloud or serial, and for the unique panel/server number
    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:  # TESTED
        """Handle the input processing of the config flow."""
        _LOGGER.debug("async_step_device %s", user_input)
        if (
            user_input is not None
            and CONF_TYPE in user_input
            and CONF_PANEL_NUMBER in user_input
        ):
            device_type: DeviceType = DeviceType.from_title(user_input[CONF_TYPE])
            if device_type == DeviceType.TCP_DISCOVERED:
                return self.async_abort(reason=TRANSLATE_ABORT_CANNOT_CONFIG_DISCOVERED)
            step = MAP_DEVICE_TO_CONFIG_STEP.get(device_type)
            if step is not None:
                panel_num = max(0, int(user_input.get(CONF_PANEL_NUMBER,0)))
                self.config_data[CONF_TYPE] = device_type
                # The servers and the panel each need unique names
                if device_type == DeviceType.TCP_SERVER:
                    # Although in user_input flow as CONF_PANEL_NUMBER, change to CONF_SERVER_NUMBER
                    self.config_data[CONF_SERVER_NUMBER] = panel_num
                    existing_entry = await self.async_set_unique_id(
                        unique_id = f"{VISONIC_UNIQUE_NAME.replace(" ", "_")}_server_{panel_num}".lower(),
                        raise_on_progress = False
                    )
                    if existing_entry:
                        raise AbortFlow(TRANSLATE_ABORT_ALREADY_CONFIGURED)
                else:
                    self.config_data[CONF_PANEL_NUMBER] = panel_num
                    existing_entry = await self.async_set_unique_id(
                        unique_id = f"{VISONIC_UNIQUE_NAME.replace(" ", "_")}_panel_{panel_num}".lower(),
                        raise_on_progress = False
                    )
                    if existing_entry:
                        raise AbortFlow(TRANSLATE_ABORT_ALREADY_CONFIGURED)
                #self._abort_if_unique_id_configured() not needed as existing_entry raises AbortFlow
                #return self.async_show_form(
                #    step_id=cf,
                #    data_schema=SERIAL_SCHEMA,
                #)
                return self.show_form(step=step, values=self.config_data)
        return self.show_form(step=FORM_DEVICE, errors={"base": TRANSLATE_ERROR_ETHERNET_SERVER_OR_SERIAL}, values=self.config_data)

    async def _common_stuff(
        self,
        device_type: DeviceType,
        user_input: dict[str, Any] | None = None,
        step_start: str = "",
    ) -> ConfigFlowResult:
        errors: dict[str, Any] = {}
        if user_input is not None:
            # Make sure all params are represented in the dict
            for conf in FormItems.get(step_start):
                if conf not in user_input:
                    user_input.setdefault(conf, "")
            # Validate the params e.g. DOWNLOAD_CODE is empty tring or 4 characters
            if (error_key:=self.validate_input(self.hass, user_input, device_type)) is None:
                # Test the connection if applicable
                if (error_key:=await self.connection_tester.test_connection(device_type, user_input=user_input)) is None:
                    # Merge in the data and show the next form in the sequence
                    self.config_data |= user_input
                    cem = EmulationMode(self.config_data.get(CONF_EMULATION_MODE, EmulationMode.POWERLINK))
                    self.step_sequence = self._create_step_sequence(cem)
                    if len(self.step_sequence) > 0:
                        return self._goto_next_step()
                    return self.async_abort(reason=TRANSLATE_ABORT_EMULATION_MODE)
            errors["base"] = error_key
        _LOGGER.info("device type %s, errors %s", device_type.name, errors)
        return self.show_form(step=step_start, errors=errors, values=self.config_data)

    # ask for the ethernet settings
    async def async_step_form_ethernet(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:  # TESTED
        """Handle the input processing of the Ethernet config flow."""
        p = self.config_data.get(CONF_PANEL_NUMBER, "Error")
        self.config_data[TEXT_TITLE] = f"Visonic Security System - Panel {p} (Ethernet)"
        if self.source == SOURCE_RECONFIGURE:
            # Push it back to the reconfigure function to process
            return await self.async_step_reconfigure(user_input=user_input)
        return await self._common_stuff(DeviceType.ETHERNET, user_input=user_input, step_start=FORM_ETHERNET)

    # ask for the tcp discovered settings, but only for reconfigure. The initial setup is from discovery
    #   Note that the host and port cannot be changed
    async def async_step_form_serial(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:  # TESTED
        """Handle the input processing of the Serial config flow."""
        p = self.config_data.get(CONF_PANEL_NUMBER, "Error")
        self.config_data[TEXT_TITLE] = f"Visonic Security System - Panel {p} (Serial)"
        if self.source == SOURCE_RECONFIGURE:
            # Push it back to the reconfigure function to process
            return await self.async_step_reconfigure(user_input=user_input)
        return await self._common_stuff(DeviceType.SERIAL, user_input=user_input, step_start=FORM_SERIAL)

    # ask for the serial settings
    async def async_step_form_tcp_discovered(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the input processing of the discovered config flow."""
        p = self.config_data.get(CONF_PANEL_NUMBER, "Error")
        self.config_data[TEXT_TITLE] = f"Visonic Security System - Panel {p} (Ethernet Discovered)"
        if self.source == SOURCE_RECONFIGURE:
            # Push it back to the reconfigure function to process
            return await self.async_step_reconfigure(user_input=user_input)
        raise VisonicException("TCP Discovered cannot be configured, only reconfigured.")

    # ask for the TCP Server settings
    async def async_step_form_tcp_server(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:  # TESTED
        """Handle the input processing of the TCP Server config flow."""
        p = self.config_data.get(CONF_SERVER_NUMBER, "Error")
        self.config_data[TEXT_TITLE] = f"Powerlink Hardware Discovery - Server {p}"
        if self.source == SOURCE_RECONFIGURE:
            # Push it back to the reconfigure function to process
            return await self.async_step_reconfigure(user_input=user_input)
        errors: dict[str, Any] = {}
        if user_input is not None:
            if (error_key:=self.validate_input(self.hass, user_input, DeviceType.TCP_SERVER)) is None:
                server_id = self.config_data.get(CONF_SERVER_NUMBER)
                if server_id is not None:
                    self.config_data |= user_input or {}
                    title = self.config_data.get(TEXT_TITLE, DEFAULT_TITLE)
                    return self.async_create_entry(title=title, data=self.config_data) #, options=self.config)
                return self.async_abort(reason=TRANSLATE_EXCEPTION_NO_UNIQUE_NUMBER_IN_CONFIG, translation_placeholders={"ident": "server"})
            errors["base"] = error_key
        return self.show_form(step=FORM_TCP_SERVER, errors=errors, values=self.config_data)

    # ask for the cloud settings
    async def async_step_form_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:  # TESTED
        """Handle the input processing of the cloud config flow."""
        p = self.config_data.get(CONF_PANEL_NUMBER, "Error")
        self.config_data[TEXT_TITLE] = f"Visonic Security System - Panel {p} (Visonic Cloud)"
        if self.source == SOURCE_RECONFIGURE:
            # Push it back to the reconfigure function to process
            return await self.async_step_reconfigure(user_input=user_input)
        errors: dict[str, Any] = {}
        if user_input is not None:
            if (error_key:=self.validate_input(self.hass, user_input, DeviceType.CLOUD)) is None:
                host = user_input.get(CONF_EXTERNAL_URL, "")
                # Check that we can connect to the external visonic server
                if (error_key:=await self.connection_tester.try_tcp_connection(host, 5001)) is None:
                    self.config_data |= user_input
                    self.config_data[CONF_TYPE] = DeviceType.CLOUD
                    if CONF_CLOUD_APP_ID not in self.config_data:
                        self.config_data[CONF_CLOUD_APP_ID] = str(uuid.uuid4()) # set the initial app ID
                    self.config_data[TEXT_TITLE] = VISONIC_CLOUD_SERVER
                    # Move on to next form
                    self.step_sequence = self._create_step_sequence()
                    return self._goto_next_step()
            errors["base"] = error_key
        return self.show_form(step=FORM_CLOUD, errors=errors, values=self.config_data | user_input or {})

    async def async_step_form_powerlink(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the input processing of the powerlink config flow."""
        if user_input is not None:
            if CONF_DOWNLOAD_CODE not in user_input:
                user_input[CONF_DOWNLOAD_CODE] = ""
            if CONF_USER_CODE_SLOT not in user_input:
                user_input[CONF_USER_CODE_SLOT] = 1
        if self.source == SOURCE_RECONFIGURE:
            if user_input is not None:
                return await self._finalise_reconfigure(step=FORM_POWERLINK, data=self.config_data | user_input)
            # Should not get here, but just in case
            return self.show_form(step=FORM_POWERLINK, errors={"base": TRANSLATE_ERROR_SETTINGS_MISSING}, values=self.config_data | user_input)
        # Put the settings in data and not options
        if user_input is not None:
            self.config_data |= user_input
        return self._goto_next_step(None)

    # Discovery - this is used by the TCP Server to initiate a TCP_DISCOVERY connection
    async def async_step_integration_discovery(self, discovery_info: DiscoveryInfoType
    ) -> ConfigFlowResult:
        """Handle a flow initialized by discovery."""
        def _get(properties: dict[str, Any], prop: str, default: str):
            value = properties.get(prop, default)
            if isinstance(value, bytes):
                return value.decode("utf-8")
            return default if value is None else str(value)

        # Called on first discovery, this function prepares for the user to add th hub.
        _LOGGER.debug(
            "[async_step_discovery] Visonic Security System, device found via async_step_discovery: %s",
            discovery_info,
        )

        # Convert to integer with a fallback/default of 0
        try:
            panel_num = max(0, int(_get(discovery_info, CONF_PANEL_NUMBER, "0")))
        except (ValueError, TypeError):
            panel_num = 0

        # Need to set all the parameters that the first few GUIs would have completed
        self.config_data |= {
            CONF_TYPE: DeviceType.TCP_DISCOVERED,
            CONF_PANEL_NUMBER: panel_num,  # Update the panel number
            CONF_EMULATION_MODE: EmulationMode.POWERLINK,  # Powerlink mode
        }
        # Copy in the discovery info in to the config
        self.config_options |= dict(discovery_info)

        # Pre-fill the configuration form
        self.context["title_placeholders"] = {
            "name": f"Visonic Panel {panel_num} Device Detected"
        }
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by discovery."""
        if user_input is not None:
            _LOGGER.info("[async_step_discovery_confirm] Start")

            # As the schema has not been used this will return a deepcopy of default values
            c = self.myschema.get_options()

            # Override some defaults to make the config 'full use' as it says in the string message to the user
            self.config_data[CONF_TYPE] = DeviceType.TCP_DISCOVERED
            self.config_options[CONF_ENABLE_REMOTE_ARM] = True
            self.config_options[CONF_ENABLE_REMOTE_DISARM] = True
            self.config_options[CONF_ENABLE_SENSOR_BYPASS] = True
            self.config_options[CONF_ARM_HOME_ENABLED] = True

            # Merge in user_input and the config values from the zeroconf function
            self.config_data |= user_input

            panel_num = c.get(CONF_PANEL_NUMBER, "0")
            await self.async_set_unique_id(
                f"{VISONIC_UNIQUE_NAME.replace(" ", "_")}_panel_{panel_num}".lower()
            )

            self._abort_if_unique_id_configured()

            if (err:=self.validate_input(self.hass, c)) is not None:
                _LOGGER.info("[async_step_discovery_confirm] Aborting - %s", err)
                return self.async_abort(reason=err)
            _LOGGER.info("[async_step_discovery_confirm] Creating Hub Entry")
            return self.async_create_entry(title=f"{DEFAULT_TITLE} - Panel {c.get(CONF_PANEL_NUMBER, "Undefined")}", data=c, options=c)
        # Show a simple form to the user asking for confirmation
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders=self.config_data, # it may use CONF_NAME and CONF_USAGE
        )

    # Zeroconf - 1 possible config for zeroconf, using Ethernet
    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo):
        """Handle discovery from Zeroconf."""

        def _get(properties: dict[str, Any], prop: str, default: str):
            value = properties.get(prop, default)
            if isinstance(value, bytes):
                return value.decode("utf-8")
            return default if value is None else str(value)

        # 1. Check to make sure it's zeroconf for visonic
        if "_visonic._tcp" not in discovery_info.name:
            _LOGGER.error("[async_step_zeroconf] ERROR - direct connection %s", discovery_info.name)
            return self.async_abort(reason=TRANSLATE_ABORT_INVALID_DEVICE_TYPE)

        # 2. Extract the parameters from the info
        hostname = discovery_info.hostname
        name = hostname.removesuffix(".local.")
        host = discovery_info.host
        emulation_mode = EmulationMode.parse(_get(discovery_info.properties, "emulation", "POWERLINK"))

        path = ""
        error = None
        port = discovery_info.port
        baud_entity = _get(discovery_info.properties, "baud_entity", "").lower().strip()
        # validate the ip:port connection
        error = await self.connection_tester.try_tcp_connection(host, port)

        if error is not None:
            return self.async_abort(reason=error)

        # 3. Get the panel number
        try:
            # Convert panel to integer with a fallback/default of 0
            panel_num = max(0, int(_get(discovery_info.properties, "panel", "0")))
        except (ValueError, TypeError):
            panel_num = 0

        # 4. Build the config update
        config_data = {
            CONF_TYPE: DeviceType.ETHERNET,
            CONF_HOST: host,
            CONF_PORT: str(port),
            CONF_ESPHOME_ENTITY_SELECT: baud_entity,
            CONF_PANEL_NUMBER: panel_num,
            CONF_PATH: path,
            CONF_EMULATION_MODE: emulation_mode,
            CONF_NAME: name,
            CONF_USAGE: EmulationMode.usage(emulation_mode),
        }
        # CONF_USAGE and CONF_NAME are only used as parameters in the language translation files

        # 5. Set the unique id for this hub
        # _LOGGER.debug(f"[async_step_zeroconf] resolved down to {panel_num=}   ...   checking for unique panel identifier")

        await self.async_set_unique_id(
            f"{VISONIC_UNIQUE_NAME.replace(" ", "_")}_panel_{panel_num}".lower()
        )

        # Abort if it's already been configured (for this panel number)
        self._abort_if_unique_id_configured()

        # If here then it's unique and not already configured

        # 6. Need to merge all the parameters that the first few GUIs would have completed
        self.config_data |= config_data

        # 7. Pre-fill the configuration form and go to next step
        self.context["title_placeholders"] = {
            "name": f"Visonic Security System - Panel {panel_num} ({name}) Device Detected"
        }

        _LOGGER.debug(
            "[async_step_zeroconf] type=Direct, name=%s, host=%s, port=%s, panel_num=%s, baud=%s",
            name,
            host,
            port,
            panel_num,
            baud_entity,
        )
        # 8. We're waiting for the user to click "Add" in the frontend
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by zeroconf."""
        #lst = await async_list_serial_ports(Platform.ESPHOME)
        if user_input is not None:
            _LOGGER.debug("[async_step_zeroconf_confirm] Start")

            # As the schema has not been used this will return a deepcopy of default values
            self.config_options = self.myschema.get_options()

            # Override some defaults to make the config 'full use' as it says in the string message to the user
            val = EmulationMode(self.config_data.get(CONF_EMULATION_MODE, EmulationMode.MINIMAL)) != EmulationMode.MINIMAL
            #   False if minimal, else True
            self.config_options[CONF_ENABLE_REMOTE_ARM] = val
            self.config_options[CONF_ENABLE_REMOTE_DISARM] = val
            self.config_options[CONF_ENABLE_SENSOR_BYPASS] = val
            self.config_options[CONF_ARM_HOME_ENABLED] = val

            # Merge in user_input and the config values from the zeroconf function
            self.config_options |= user_input

            if (err:=self.validate_input(self.hass, self.config_data)) is not None:
                _LOGGER.debug("[async_step_zeroconf_confirm] Aborting - %s", err)
                return self.async_abort(reason=err)
            _LOGGER.debug("[async_step_zeroconf_confirm] Creating Hub Entry")
            return self.async_create_entry(title=f"{DEFAULT_TITLE} - Panel {self.config_data.get(CONF_PANEL_NUMBER, "Undefined")}", data=self.config_data, options=self.config_options)

        # Show a simple form to the user asking for confirmation
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders=self.config_data, # it may use CONF_NAME and CONF_USAGE
        )

    # This is run to import the configuration.yaml parameters
    async def async_step_import(self, import_config: dict[str, Any]):
        """Import a config entry from configuration.yaml."""
        # _LOGGER.debug("Visonic in async_step_import in %s", import_config)

        # convert the yaml file format for the device (ethernet or serial) settings to a flat dictionary structure
        data: dict[str, Any] = {}
        try:
            for k in import_config:
                if k == CONF_DEVICE:
                    # flatten out the structure so the data variable is a simple dictionary
                    device_type: dict[str, Any] = import_config.get(CONF_DEVICE, {})  # This must be set so default to an empty {}
                    if device_type.get(CONF_TYPE) == DeviceType.ETHERNET:
                        data[CONF_TYPE] = DeviceType.ETHERNET
                        data[CONF_HOST] = device_type[CONF_HOST]
                        data[CONF_PORT] = device_type[CONF_PORT]
                        data[CONF_ESPHOME_ENTITY_SELECT] = device_type[
                            CONF_ESPHOME_ENTITY_SELECT
                        ]
                        data[CONF_PATH] = ""
                    elif device_type.get(CONF_TYPE) == DeviceType.SERIAL:
                        data[CONF_TYPE] = DeviceType.SERIAL
                        data[CONF_PATH] = device_type[CONF_PATH]
                        data[CONF_HOST] = ""
                        data[CONF_PORT] = ""
                        data[CONF_ESPHOME_ENTITY_SELECT] = ""
                else:
                    data[k] = import_config.get(k)
        except (KeyError, TypeError) as er:
            _LOGGER.debug(
                "Importing settings from configuration.yaml but something went wrong or some essential data is missing %s",
                str(er),
            )
            # _LOGGER.debug("     The current data is %s", import_config)
            return self.async_abort(reason=TRANSLATE_ERROR_SETTINGS_MISSING)

        return await self.async_step_user(data)

    async def _finalise_reconfigure(self, step: str, data: dict[str, Any] | None = None) -> ConfigFlowResult:
        #   e.g. host, port, esphome_entity_select, download_code
        # Check the content of the data dict
        ce: ConfigEntry = self._get_reconfigure_entry()
        device_type = ce.data.get(CONF_TYPE)
        if device_type in [DeviceType.ETHERNET, DeviceType.TCP_DISCOVERED, DeviceType.SERIAL, DeviceType.CLOUD]:
            if (error_key := self.validate_input(self.hass, data)) is not None:  # This should pick up hass from the parent
                return self.show_form(step=step, values = data, errors={"base": error_key})
        # Test the connection
        error = await self.connection_tester.test_connection(device_type=device_type, user_input=data)
        if error is not None:
            return self.show_form(step=step, errors={"base": error}, values=data)
        return self.async_update_reload_and_abort(
            entry = ce,
            data = data,
        )

    # Reconfigure - allow the user to change any "data" values, then restart the hub
    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of an existing Visonic device."""
        try:
            # Get the config entry for the reconfigure
            ce = self._get_reconfigure_entry()
            # Do some basic checks on ce.data
            if CONF_TYPE not in ce.data:
                return self.async_abort(reason=TRANSLATE_ABORT_UNKNOWN)
            device_type = ce.data.get(CONF_TYPE)
            if device_type == DeviceType.TCP_DISCOVERED:
                return self.async_abort(reason=TRANSLATE_ABORT_CANNOT_CONFIG_DISCOVERED)
            step = MAP_DEVICE_TO_CONFIG_STEP.get(device_type)
            if step is None:
                return self.async_abort(reason=TRANSLATE_ABORT_UNKNOWN)
            if user_input is not None:
                # If a CONF parameter is missing from user_input then set it to an empty string
                for conf in FormItems.get(step):
                    if conf not in user_input:
                        user_input.setdefault(conf, "")
                data = self.config_data | user_input
                cem = data.get(CONF_EMULATION_MODE)
                if cem is not None:
                    cem = EmulationMode(cem)
                    if cem == EmulationMode.POWERLINK:
                        # Ask user for download code and show eprom data
                        self.config_data = data
                        return self.show_form(step=FORM_POWERLINK, values=data)
                return await self._finalise_reconfigure(step=step, data=data)
            self.config_data = ce.data.copy()
            return self.show_form(step=step, values=ce.data)
        except AbortFlow:
            raise
        except Exception as ex:
            tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
            _LOGGER.exception("[ConfigFlow] Unexpected exception\n%s", tb_str)
        return self.async_abort(reason=TRANSLATE_ABORT_UNKNOWN)

class VisonicOptionsFlowHandler(VisonicHandler, OptionsFlow):
    """Handle Visonic options."""

    VERSION = VISONIC_CONFIG_VERSION
    CONNECTION_CLASS = CONN_CLASS_LOCAL_POLL

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_abort(reason=TRANSLATE_ABORT_UNKNOWN)

        self.config_data = self.config_entry.data.copy()
        self.config_options = self.config_entry.options.copy()

        # Before we start, check that all existing data is valid
        err = self.validate_input(self.hass, self.config_data)
        if err is not None:
            self.async_abort(reason=err)

        self.myschema.set_base_options(options=self.config_data | self.config_options)

        if CONF_TYPE in self.config_data:
            t = self.config_data.get(CONF_TYPE)
            # _LOGGER.debug(f"type = {type(t)}   t = {t}")

            if t == DeviceType.TCP_SERVER:
                return self.async_abort(reason=TRANSLATE_ABORT_CANNOT_EDIT_SERVER)

            if t == DeviceType.CLOUD:
                # Start the editing sequence of forms
                self.step_sequence = self._create_step_sequence()
                if len(self.step_sequence) > 0:
                    return self._goto_next_step()

            if t in (DeviceType.ETHERNET, DeviceType.SERIAL, DeviceType.TCP_DISCOVERED):
                # Make sure the emulation mode is set and valid
                cem = EmulationMode(self.config_data.get(CONF_EMULATION_MODE, EmulationMode.POWERLINK))
                if cem is None:
                    _LOGGER.debug("ERROR in config : CONF_EMULATION_MODE set to %s",cem)
                    return self.async_abort(reason=TRANSLATE_ABORT_EMULATION_MODE)
                # Start the editing sequence of forms
                self.step_sequence = self._create_step_sequence(cem, True)
                if len(self.step_sequence) > 0:
                    return self._goto_next_step()

            _LOGGER.debug("Edit config option settings type = %s, aborting", t)
        return self.async_abort(reason=TRANSLATE_ABORT_INVALID_DEVICE_TYPE)
