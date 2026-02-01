"""Config flow for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

from typing import Any
from copy import deepcopy
import logging
import voluptuous as vol
import socket

from .const import (
    CONF_DEVICE_BAUD,
    CONF_DEVICE_TYPE,
    CONF_EXCLUDE_SENSOR,
    CONF_EXCLUDE_X10,
    CONF_SIREN_SOUNDING,
    CONF_PANEL_NUMBER,
    CONF_EMULATION_MODE,
    CONF_ESPHOME_ENTITY_SELECT,
    CONF_ALARM_NOTIFICATIONS,
    CONF_NAME,
    CONF_ENABLE_REMOTE_ARM,
    CONF_ENABLE_REMOTE_DISARM,
    CONF_ENABLE_SENSOR_BYPASS,
    CONF_ARM_HOME_ENABLED,
    available_emulation_modes,
    DOMAIN, 
    VISONIC_UNIQUE_NAME,
    DEFAULT_DEVICE_BAUD,
    DEVICE_TYPE_ETHERNET,
    DEVICE_TYPE_USB,
)

from homeassistant.helpers import config_validation as cv
from homeassistant import data_entry_flow
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    CONN_CLASS_LOCAL_POLL,
    HANDLERS,
)
from homeassistant.const import CONF_DEVICE, CONF_HOST, CONF_PATH, CONF_PORT
from homeassistant.core import callback

from .create_schema import VisonicSchema

_LOGGER = logging.getLogger(__name__)

# These are the translation strings for the various abort and error indications to the user
TRANSLATE_ERROR_ALREADY_CONFIGURED = "already_configured"
TRANSLATE_ERROR_DEVICE = "device_error"
TRANSLATE_ERROR_SETTINGS_MISSING = "settings_missing"
TRANSLATE_ERROR_ETHERNET_OR_USB = "eth_or_usb"
TRANSLATE_ERROR_EMULATION_MODE = "emulation_mode_error"
TRANSLATE_ERROR_UNKNOWN = "unknown"
TRANSLATE_ERROR_CONNECTION_TIMEOUT = "cannot_connect_timeout"
TRANSLATE_ERROR_CONNECTION_REFUSED = "cannot_connect_refused"

class MyHandlers(data_entry_flow.FlowHandler):
    """My generic handler for config flow ConfigFlow and OptionsFlow."""

    def __init__(self):
        """Initialize the config flow."""
        # Do not call the parents init function
        self.PowerlinkRequested = False
        self.myschema = VisonicSchema()
        self.config = {}
        self.step_sequence = []
        self.current_pos = -1

    def create_parameters_sequence(self, s : str) -> list:
        step_sequence = []
        if s == available_emulation_modes[0]:
            step_sequence = [10,11,12,13]       
        elif s == available_emulation_modes[1]:
            step_sequence = [10,11,12,13] 
        elif s == available_emulation_modes[2]:
            step_sequence = [10,11] 
        return step_sequence

    def toList(self, lst, cfg):
        """Convert to a list."""
        if cfg in lst:
            if isinstance(lst[cfg], str):
                tmplist = lst[cfg].split(",") if lst[cfg] != "" else []
                self.config[cfg] = [item.strip().lower() for item in tmplist]
            else:
                self.config[cfg] = lst[cfg]

    def _show_form(self, step: str = "device", placeholders=None, errors=None, defaults=None):
        """Show the form to the user."""
        _LOGGER.debug(f"show_form start {step=} {placeholders=} {errors=} {defaults=}")

        ds = None

        if step == "device":
            ds = self.myschema.create_schema_device(defaults)
        elif step == "myethernet":
            ds = self.myschema.create_schema_ethernet(defaults)
        elif step == "myusb":
            ds = self.myschema.create_schema_usb(defaults)
        elif step == "parameters1":
            ds = self.myschema.create_schema_parameters1(defaults)
        elif step == "parameters10":
            ds = self.myschema.create_schema_parameters10(defaults)
        elif step == "parameters11":
            ds = self.myschema.create_schema_parameters11(defaults, self.PowerlinkRequested)
        elif step == "parameters12":
            ds = self.myschema.create_schema_parameters12(defaults)
        elif step == "parameters13":
            ds = self.myschema.create_schema_parameters13(defaults)
        else:
            return self.async_abort(reason=TRANSLATE_ERROR_DEVICE)

        if ds is None:
            # The only way this could happen is one of the create functions have returned None
            _LOGGER.debug(f"show_form ds is None, {step=}")
            return self.async_abort(reason=TRANSLATE_ERROR_DEVICE)

        _LOGGER.debug(f"doing show_form  {step=}   {ds=}")
        return self.async_show_form(
            step_id=step,
            data_schema=ds,
            errors=errors if errors else {},
            #description_placeholders=placeholders if placeholders else {},
        )
        
    def gotonext(self, user_input=None):
        if user_input is not None:
            self.config.update(user_input)
        self.current_pos += 1
        if self.current_pos == len(self.step_sequence):
            return self.processcomplete()
        return self._show_form(step="parameters"+str(self.step_sequence[self.current_pos]))

    async def async_step_parameters10(self, user_input=None):
        """Config flow step 10."""
        #_LOGGER.debug(f"show_form step is 10 - {self.current_pos} {user_input=}")
        return self.gotonext(user_input)

    async def async_step_parameters11(self, user_input=None):
        """Config flow step 11."""
        #_LOGGER.debug(f"show_form step is 11 - {self.current_pos} {user_input=}")
        return self.gotonext(user_input)

    async def async_step_parameters12(self, user_input=None):
        """Config flow step 12."""
        #_LOGGER.debug(f"show_form step is 12 - {self.current_pos} {user_input=}")
        return self.gotonext(user_input)

    async def async_step_parameters13(self, user_input=None):
        """Config flow step 13."""
        #_LOGGER.debug(f"show_form step is 13 - {self.current_pos} {user_input=}")
        return self.gotonext(user_input)

    def validate_input(self, data: dict):
        """Validate the input."""
        # Validation to be implemented
        # return a temporary title to use
        return {"title": "Alarm Panel"}

    def processcomplete(self):
        """Config flow process complete."""
        try:
            #_LOGGER.debug('processcomplete')
            info = self.validate_input(self.config)
            if info is not None:
                # convert comma separated string to a list
                if CONF_SIREN_SOUNDING in self.config:
                    self.toList(self.config, CONF_SIREN_SOUNDING)

                if CONF_ALARM_NOTIFICATIONS in self.config:
                    self.toList(self.config, CONF_ALARM_NOTIFICATIONS)

                if CONF_EXCLUDE_SENSOR in self.config:
                    self.toList(self.config, CONF_EXCLUDE_SENSOR)
                    # convert string list to integer list
                    self.config[CONF_EXCLUDE_SENSOR] = [
                        int(i) for i in self.config[CONF_EXCLUDE_SENSOR]
                    ]

                if CONF_EXCLUDE_X10 in self.config:
                    self.toList(self.config, CONF_EXCLUDE_X10)
                    # convert string list to integer list
                    self.config[CONF_EXCLUDE_X10] = [
                        int(i) for i in self.config[CONF_EXCLUDE_X10]
                    ]
                #_LOGGER.debug(f"[processcomplete] in flow  {self.config}")
                return self.async_create_entry(title=info["title"], data=self.config)
        except Exception as er:  # pylint: disable=broad-except
            _LOGGER.debug("Unexpected exception in config flow  %s", str(er))
            # errors["base"] = TRANSLATE_ERROR_UNKNOWN
        return self.async_abort(reason=TRANSLATE_ERROR_DEVICE)

@HANDLERS.register(DOMAIN)
class VisonicConfigFlow(ConfigFlow, MyHandlers, domain=DOMAIN):
    """Handle a Visonic flow."""

    VERSION = 5
    CONNECTION_CLASS = CONN_CLASS_LOCAL_POLL

    def __init__(self):
        """Initialize options flow."""
        MyHandlers.__init__(self)
        ConfigFlow.__init__(self)
        _LOGGER.debug("Visonic ConfigFlow init")

    def validate_visonic_connection(self, host: str, port: int) -> str:
        """Attempt to open a socket to the ethernet/thread device. Returns error key or None."""
        try:
            # Detect family
            family = socket.AF_INET6 if ":" in host else socket.AF_INET

            _LOGGER.debug(f"validate_visonic_connection, family {family.name}   host {host}   port {port}")
            
            # We use a short 3s timeout for the UI check to keep it snappy
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            
            # Attempt the connection
            sock.connect((host, port))
            sock.close()
            return None  # Success!
            
        except socket.timeout:
            return TRANSLATE_ERROR_CONNECTION_TIMEOUT
        except socket.error:
            return TRANSLATE_ERROR_CONNECTION_REFUSED
        return TRANSLATE_ERROR_UNKNOWN

    def dumpMyState(self):
        """Output state to the log file."""
        if self._async_current_entries():
            entries = self._async_current_entries()

            if not entries:
                _LOGGER.debug("No Entries found")

            cur_entry = entries[0]
            #is_loaded = cur_entry.state == ENTRY_STATE_LOADED

            #_LOGGER.debug("Is loaded %s", is_loaded)
        # else:
        #    _LOGGER.debug("Invalid List")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry : ConfigEntry): #-> OptionsFlowHandler
        """Get the options flow for this handler."""
        #_LOGGER.debug("Visonic async_get_options_flow")
        return VisonicOptionsFlowHandler()

    # ask the user: ethernet or usb
    async def async_step_device(self, user_input=None):
        """Handle the input processing of the config flow."""
        _LOGGER.debug("async_step_device %s", user_input)
        #self.dumpMyState()
        if user_input is not None and CONF_DEVICE_TYPE in user_input and CONF_PANEL_NUMBER in user_input:
            panel_num = max(0, int(user_input[CONF_PANEL_NUMBER]))
            await self.async_set_unique_id(f"{VISONIC_UNIQUE_NAME}_panel_{panel_num}".lower())
            #await self.async_set_unique_id(VISONIC_UNIQUE_NAME + " Panel " + str(panel_num))
            self._abort_if_unique_id_configured()
            self.config[CONF_PANEL_NUMBER] = panel_num
            self.config[CONF_DEVICE_TYPE] = user_input[CONF_DEVICE_TYPE].lower()
            if self.config[CONF_DEVICE_TYPE] == DEVICE_TYPE_ETHERNET:
                return self._show_form(step="myethernet")
            elif self.config[CONF_DEVICE_TYPE] == DEVICE_TYPE_USB:
                return self._show_form(step="myusb")
        errors = {}
        errors["base"] = TRANSLATE_ERROR_ETHERNET_OR_USB
        return self._show_form(step="device", errors=errors)

    def select_entity_or_empty(self, value):
        """Return a validator that checks entity is empty or a valid select entity."""
        if not value or value == "":
            return ""  # allow empty
        entity = cv.entity_id(value)
        if not entity.startswith("select."):
            raise vol.Invalid("Entity must be from the select domain")

        # Get current entity
        state_obj = self.hass.states.get(entity)
        if state_obj is None:
            raise vol.Invalid(f"Entity {entity} not found")

        # Get available options
        options = state_obj.attributes.get("options", [])
        if not options:
            raise vol.Invalid(f"No options found for selected entity")

        # Check if the available options are valid
        if "9600" not in options or "38400" not in options:
            raise vol.Invalid(f"Invalid options for {entity}, options: {options}")

        return entity

    # ask for the ethernet settings
    async def async_step_myethernet(self, user_input=None):
        """Handle the input processing of the Ethernet config flow."""
        errors = {}

        if user_input:
            try:
                select_entity = user_input.get(CONF_ESPHOME_ENTITY_SELECT, "")
                # Use your validator
                self.select_entity_or_empty(select_entity)
            except vol.Invalid as e:
                errors[CONF_ESPHOME_ENTITY_SELECT] = str(e)

            if not errors:
                host = user_input.get(CONF_HOST, "")
                port = user_input.get(CONF_PORT, "0")
                error_key = self.validate_visonic_connection(host, int(port))
                
                if error_key is None:
                    self.config.update(user_input)
                    self.config[CONF_PATH] = ""
                    self.config[CONF_DEVICE_BAUD] = DEFAULT_DEVICE_BAUD

                    return self._show_form(step="parameters1")
                
                return self._show_form(step="myethernet", errors={"base": error_key}) # keep the old user settings in the interface

        _LOGGER.debug(f"async_step_myethernet, select entity validation errors {errors}")
        
        return self._show_form(step="myethernet", errors=errors)

    # ask for the usb settings
    async def async_step_myusb(self, user_input=None):
        """Handle the input processing of the USB config flow."""
        self.config.update(user_input)
        self.config[CONF_HOST] = ""
        self.config[CONF_PORT] = ""
        self.config[CONF_ESPHOME_ENTITY_SELECT] = ""
        return self._show_form(step="parameters1")

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo):
        """Handle discovery from Zeroconf."""

        def _get(properties, prop, default=None):
            value = properties.get(prop, default)
            if isinstance(value, bytes):
                return value.decode("utf-8")
            return default if value is None else str(value)

        _LOGGER.debug("[async_step_zeroconf] Visonic Alarm device found via zeroconf: %s", discovery_info)
        properties = discovery_info.properties or {}

        # Convert to integer with a fallback/default of 0
        try:
            panel_num = max(0, int(_get(properties, "panel", "0")))
        except (ValueError, TypeError):
            panel_num = 0
        
        #_LOGGER.debug(f"[async_step_zeroconf] resolved down to {panel_num=}   ...   checking for unique panel identifier")
        
        # If here then the panel number is unique
        host = discovery_info.host
        port = discovery_info.port
        hostname = discovery_info.hostname
        self.name = discovery_info.name.removesuffix("._visonic._tcp.local.")
        index = hostname.find(".")
        baud_entity = _get(properties, "baud_entity", "").lower().strip()

        # Set the unique id for this hub
        existing_config_entry = await self.async_set_unique_id(f"{VISONIC_UNIQUE_NAME}_panel_{panel_num}".lower()) # VISONIC_UNIQUE_NAME + " Panel " + str(panel_num)

        # Sometimes, devices send an invalid zeroconf message with multiple addresses
        # and one of them, which could end up being in discovery_info.host, is from a
        # different device. If any of the discovery_info.ip_addresses matches the
        # existing host, don't update the host.
        if (
            existing_config_entry
            # Ignored entries don't have host
            and CONF_HOST in existing_config_entry.data
            and len(discovery_info.ip_addresses) > 1
        ):
            existing_host = existing_config_entry.data[CONF_HOST]
            if existing_host != self.host:
                if existing_host in [str(ip_address) for ip_address in discovery_info.ip_addresses]:
                    host = existing_host
                    _LOGGER.debug(f"[async_step_zeroconf] Resolved new host from an existing configuration for panel {panel_num}  host is {host}")

        # Abort if it's already been configured (for this panel number)
        #self._abort_if_unique_id_configured()
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: host, CONF_PORT: str(port)}
        )

        # Need to set all the parameters that the first few GUIs would have completed
        self.config.update({
            CONF_DEVICE_TYPE: DEVICE_TYPE_ETHERNET,
            CONF_HOST: host,
            CONF_PORT: str(port),
            CONF_ESPHOME_ENTITY_SELECT: baud_entity,
            CONF_PANEL_NUMBER: panel_num,
            CONF_PATH: "",
            CONF_DEVICE_BAUD: DEFAULT_DEVICE_BAUD,
        })

        # You can now pre-fill the configuration form
        self.context["title_placeholders"] = {"name": f"Visonic Security - Panel {panel_num} ({self.name}) Device Detected"}

        _LOGGER.debug(f"[async_step_zeroconf] {self.name=}    {host=}    {port=}    {hostname=}    {panel_num=}    {baud_entity=}")

        return await self.async_step_zeroconf_confirm()
        #return self._show_form(step="parameters1", defaults=self.config)

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initiated by zeroconf."""
        if user_input is not None:
            _LOGGER.debug(f"[async_step_zeroconf_confirm] Start")
            # This check should always pass because the device has sent us its IP address
            if self.validate_visonic_connection(self.config[CONF_HOST], int(self.config[CONF_PORT])) is not None:
                _LOGGER.debug(f"[async_step_zeroconf_confirm] Aborting - TRANSLATE_ERROR_CONNECTION_REFUSED")
                return self.async_abort(reason=TRANSLATE_ERROR_CONNECTION_REFUSED)

            # As the schema has not been used this will return a deepcopy of default values
            c = self.myschema.getConfig()

            # Override some defaults to make the config 'full use' as it says in the string message to the user
            c[CONF_ENABLE_REMOTE_ARM] = True
            c[CONF_ENABLE_REMOTE_DISARM] = True
            c[CONF_ENABLE_SENSOR_BYPASS] = True
            c[CONF_ARM_HOME_ENABLED] = True

            # Merge in user_input and the config values from the zeroconf function
            c.update(user_input)
            c.update(self.config)

            # Check that all keys are strings
            for k,v in c.items():
                if not isinstance(k, str):
                    _LOGGER.debug(f"[async_step_zeroconf_confirm] Not a string  {type(k)}   {k}")
            
            info = self.validate_input(c)
            if info is not None:
                _LOGGER.debug(f"[async_step_zeroconf_confirm] Creating Hub Entry")
                return self.async_create_entry(title=info["title"], data=c)
            _LOGGER.debug(f"[async_step_zeroconf_confirm] Aborting - TRANSLATE_ERROR_DEVICE")
            return self.async_abort(reason=TRANSLATE_ERROR_DEVICE)
        # Show a simple form to the user asking for confirmation
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={CONF_NAME: self.name},
        )

    async def async_step_parameters1(self, user_input=None):
        errors = {}

        if user_input:
            """Config flow step 1."""
            _LOGGER.debug(f"async_step_parameters1,  step is 1 - {self.current_pos}    {user_input=}")
            self.config.update(user_input)

            self.current_pos = -1

            if CONF_EMULATION_MODE in user_input:
                self.step_sequence = self.create_parameters_sequence(user_input[CONF_EMULATION_MODE])
                if len(self.step_sequence) == 0:
                    _LOGGER.debug(f"********************* ERROR : CONF_EMULATION_MODE set to {user_input[CONF_EMULATION_MODE]} **********************************")
                    return self.async_abort(reason=TRANSLATE_ERROR_EMULATION_MODE)
            else:
                _LOGGER.debug(f"********************* ERROR : CONF_EMULATION_MODE not in user_input **********************************")
                return self.async_abort(reason=TRANSLATE_ERROR_EMULATION_MODE)
            #_LOGGER.debug(f"async_step_parameters1 {user_input}")
            return self.gotonext(user_input)
        return self._show_form(step="parameters1", errors=errors)


    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle a user config flow."""
        # is this a raw configuration (not called from importing yaml)
        if not user_input:
            #_LOGGER.debug("Visonic in async_step_user - trigger user input")
            return self._show_form(step="device")

        #_LOGGER.debug("Visonic async_step_user - importing a yaml config setup")

        # importing a yaml config setup
        info = self.validate_input(user_input)
        if info is not None:
            return self.async_create_entry(title=info["title"], data=user_input)
        return self.async_abort(reason=TRANSLATE_ERROR_DEVICE)

    # this is run to import the configuration.yaml parameters
    async def async_step_import(self, import_config):
        """Import a config entry from configuration.yaml."""
        # _LOGGER.debug("Visonic in async_step_import in %s", import_config)
        #self.dumpMyState()

        # convert the yaml file format for the device (ethernet or usb) settings to a flat dictionary structure
        data = {}
        try:
            for k in import_config:
                if k == CONF_DEVICE:
                    # flatten out the structure so the data variable is a simple dictionary
                    device_type = import_config.get(CONF_DEVICE, "")  # This must be set so default to an invalid setting
                    if device_type[CONF_DEVICE_TYPE] == DEVICE_TYPE_ETHERNET:
                        data[CONF_DEVICE_TYPE] = DEVICE_TYPE_ETHERNET
                        data[CONF_HOST] = device_type[CONF_HOST]
                        data[CONF_PORT] = device_type[CONF_PORT]
                        data[CONF_ESPHOME_ENTITY_SELECT] = device_type[CONF_ESPHOME_ENTITY_SELECT]
                        data[CONF_PATH] = ""
                        data[CONF_DEVICE_BAUD] = DEFAULT_DEVICE_BAUD
                    elif device_type[CONF_DEVICE_TYPE] == DEVICE_TYPE_USB:
                        data[CONF_DEVICE_TYPE] = DEVICE_TYPE_USB
                        data[CONF_PATH] = device_type[CONF_PATH]
                        if CONF_DEVICE_BAUD in device_type:
                            data[CONF_DEVICE_BAUD] = device_type[CONF_DEVICE_BAUD]
                        else:
                            data[CONF_DEVICE_BAUD] = DEFAULT_DEVICE_BAUD
                        data[CONF_HOST] = ""
                        data[CONF_PORT] = ""
                        data[CONF_ESPHOME_ENTITY_SELECT] = ""
                else:
                    data[k] = import_config.get(k)
        except Exception as er:
            _LOGGER.debug(
                "Importing settings from configuration.yaml but something went wrong or some essential data is missing %s",
                str(er),
            )
            # _LOGGER.debug("     The current data is %s", import_config)
            return self.async_abort(reason=TRANSLATE_ERROR_SETTINGS_MISSING)

        return await self.async_step_user(data)


class VisonicOptionsFlowHandler(OptionsFlow, MyHandlers):
    """Handle options."""

    VERSION = 5
    CONNECTION_CLASS = CONN_CLASS_LOCAL_POLL

    def __init__(self) -> None:
        """Initialize options flow."""
        MyHandlers.__init__(self)
        OptionsFlow.__init__(self)

    def combineSettings(self, entry):
        """Combine the old settings from data and the new from options."""
        conf = {}
        # the entry.data dictionary contains all the old data used on creation and is a complete set
        for k in entry.data:
            conf[k] = entry.data[k]
        # the entry.config dictionary contains the latest/updated values but may not be a complete set
        #     overwrite data with options i.e. overwrite the original settings on creation with the edited settings to get the latest
        for k in entry.options:
            conf[k] = entry.options[k]
        return conf
        
    def setConfigEntry(self, config_entry):
        if config_entry is not None:
            # convert python map to dictionary and set defaults for the options flow handler
            c = self.combineSettings(config_entry)
            self.myschema.update_options(options = c)
            if CONF_EMULATION_MODE in c:
                s = c[CONF_EMULATION_MODE]
                self.PowerlinkRequested = s == available_emulation_modes[0]

    # when editing an existing config, start from parameters10 as the previous settings are not editable after the connection has been made
    async def async_step_init(self, user_input=None):
        """Manage the options."""

        #_LOGGER.debug(f"Edit config option settings, user input = {user_input}")
        #_LOGGER.debug(f"Edit config option settings, data = {self.config_entry.data}")
        #_LOGGER.debug(f"Edit config option settings, options = {self.config_entry.options}")

        self.setConfigEntry(self.config_entry)
        self.config = deepcopy(dict(self.config_entry.options))

        if self.config is not None and CONF_DEVICE_TYPE in self.config:
            t = self.config[CONF_DEVICE_TYPE].lower()
            #_LOGGER.debug(f"type = {type(t)}   t = {t}")
            if t == DEVICE_TYPE_ETHERNET or t == DEVICE_TYPE_USB:

                self.current_pos = -1

                if CONF_EMULATION_MODE in self.config:
                    self.step_sequence = self.create_parameters_sequence(self.config[CONF_EMULATION_MODE])
                    if len(self.step_sequence) == 0:
                        _LOGGER.debug(f"********************* ERROR : CONF_EMULATION_MODE set to {self.config[CONF_EMULATION_MODE]} **********************************")
                        return self.async_abort(reason=TRANSLATE_ERROR_EMULATION_MODE)
                else:
                    _LOGGER.debug(f"********************* ERROR : CONF_EMULATION_MODE not in self.config **********************************")
                    return self.async_abort(reason=TRANSLATE_ERROR_EMULATION_MODE)

                return self.gotonext()

            else:
                _LOGGER.debug(f"Edit config option settings type = {t}, aborting")
        
        return self.async_abort(reason=TRANSLATE_ERROR_DEVICE)

        


