"""Config flow for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

from typing import Any
import logging

from .const import (
    CONF_DEVICE_BAUD,
    CONF_DEVICE_TYPE,
    CONF_EXCLUDE_SENSOR,
    CONF_EXCLUDE_X10,
    CONF_SIREN_SOUNDING,
    CONF_SENSOR_EVENTS,
    CONF_PANEL_NUMBER,
    CONF_EMULATION_MODE,
    available_emulation_modes,
    DOMAIN, 
    VISONIC_UNIQUE_NAME,
    CONF_ALARM_NOTIFICATIONS
)

from homeassistant import data_entry_flow
from homeassistant.data_entry_flow import FlowResult
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    CONN_CLASS_LOCAL_POLL,
    HANDLERS,
#    ENTRY_STATE_LOADED,
)
from homeassistant.const import CONF_DEVICE, CONF_HOST, CONF_PATH, CONF_PORT
from homeassistant.core import callback

from .create_schema import VisonicSchema

_LOGGER = logging.getLogger(__name__)

class MyHandlers(data_entry_flow.FlowHandler):
    """My generic handler for config flow ConfigFlow and OptionsFlow."""

    def __init__(self, config_entry = None):
        """Initialize the config flow."""
        # Do not call the parents init function
        self.myschema = VisonicSchema()
        self.config = {}
        self.step_sequence = []
        self.current_pos = -1
        if config_entry is not None:
            # convert python map to dictionary and set defaults for the options flow handler
            c = self.combineSettings(config_entry)
            self.myschema.set_default_options(options = c)

    def create_parameters_sequence(self, s : str) -> list:
        step_sequence = []
        if s == available_emulation_modes[0]:
            step_sequence = [2,10,11,12]       
        elif s == available_emulation_modes[1]:
            step_sequence = [10,11] 
        elif s == available_emulation_modes[2]:
            step_sequence = [10] 
        return step_sequence

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
        
    def toList(self, lst, cfg):
        """Convert to a list."""
        if cfg in lst:
            if isinstance(lst[cfg], str):
                tmplist = lst[cfg].split(",") if lst[cfg] != "" else []
                self.config[cfg] = [item.strip().lower() for item in tmplist]
            else:
                self.config[cfg] = lst[cfg]

    async def _show_form(self, step: str = "device", placeholders=None, errors=None):
        """Show the form to the user."""
        #_LOGGER.debug(f"show_form start {step} {placeholders} {errors}")

        ds = None

        if step == "device":
            ds = self.myschema.create_schema_device()
        elif step == "myethernet":
            ds = self.myschema.create_schema_ethernet()
        elif step == "myusb":
            ds = self.myschema.create_schema_usb()
        elif step == "parameters1":
            ds = self.myschema.create_schema_parameters1()
        elif step == "parameters2":
            ds = self.myschema.create_schema_parameters2()
        elif step == "parameters10":
            ds = self.myschema.create_schema_parameters10()
        elif step == "parameters11":
            ds = self.myschema.create_schema_parameters11()
        elif step == "parameters12":
            ds = self.myschema.create_schema_parameters12()
        else:
            return self.async_abort(reason="device_error")

        if ds is None:
            # The only way this could happen is one of the create functions have returned None
            _LOGGER.debug("show_form ds is None, step is %s", step)
            return self.async_abort(reason="device_error")

        #_LOGGER.debug(f"doing show_form step = {step}   ds = {ds}")
        return self.async_show_form(
            step_id=step,
            data_schema=ds,
            errors=errors if errors else {},
            #description_placeholders=placeholders if placeholders else {},
        )

    async def gotonext(self, user_input=None):
        if user_input is not None:
            self.config.update(user_input)
        self.current_pos = self.current_pos + 1
        if self.current_pos == len(self.step_sequence):
            return await self.processcomplete()
        return await self._show_form(step="parameters"+str(self.step_sequence[self.current_pos]))

    async def async_step_parameters2(self, user_input=None):
        """Config flow step 2."""
        #_LOGGER.debug(f"show_form step is 2 - {self.current_pos}")
        return await self.gotonext(user_input)

    async def async_step_parameters10(self, user_input=None):
        """Config flow step 10."""
        #_LOGGER.debug(f"show_form step is 10 - {self.current_pos}")
        return await self.gotonext(user_input)

    async def async_step_parameters11(self, user_input=None):
        """Config flow step 11."""
        #_LOGGER.debug(f"show_form step is 11 - {self.current_pos}")
        return await self.gotonext(user_input)

    async def async_step_parameters12(self, user_input=None):
        """Config flow step 12."""
        #_LOGGER.debug(f"show_form step is 12 - {self.current_pos}")
        return await self.gotonext(user_input)

    async def validate_input(self, data: dict):
        """Validate the input."""
        # Validation to be implemented
        # return a temporary title to use
        return {"title": "Alarm Panel"}

    async def processcomplete(self):
        """Config flow process complete."""
        try:
            #_LOGGER.debug('processcomplete')
            info = await self.validate_input(self.config)
            if info is not None:
                # convert comma separated string to a list
                if CONF_SIREN_SOUNDING in self.config:
                    self.toList(self.config, CONF_SIREN_SOUNDING)

                if CONF_SENSOR_EVENTS in self.config:
                    self.toList(self.config, CONF_SENSOR_EVENTS)

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

                return self.async_create_entry(title=info["title"], data=self.config)
        except Exception as er:  # pylint: disable=broad-except
            _LOGGER.debug("Unexpected exception in config flow  %s", str(er))
            # errors["base"] = "unknown"
        return self.async_abort(reason="device_error")


@HANDLERS.register(DOMAIN)
class VisonicConfigFlow(ConfigFlow, MyHandlers, domain=DOMAIN):
    """Handle a Visonic flow."""

    VERSION = 4
    CONNECTION_CLASS = CONN_CLASS_LOCAL_POLL

    def __init__(self):
        """Initialize options flow."""
        MyHandlers.__init__(self)
        ConfigFlow.__init__(self)
        _LOGGER.debug("Visonic ConfigFlow init")

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
    def async_get_options_flow(config_entry : ConfigEntry):
        """Get the options flow for this handler."""
        #_LOGGER.debug("Visonic async_get_options_flow")
        return VisonicOptionsFlowHandler(config_entry)

    # ask the user, ethernet or usb
    async def async_step_device(self, user_input=None):
        """Handle the input processing of the config flow."""
        #_LOGGER.debug("async_step_device %s", user_input)
        #self.dumpMyState()
        if user_input is not None and CONF_DEVICE_TYPE in user_input and CONF_PANEL_NUMBER in user_input:
            panel_num = max(0, int(user_input[CONF_PANEL_NUMBER]))
            await self.async_set_unique_id(VISONIC_UNIQUE_NAME + " Panel " + str(panel_num))
            self._abort_if_unique_id_configured()
            self.config[CONF_PANEL_NUMBER] = panel_num
            self.config[CONF_DEVICE_TYPE] = user_input[CONF_DEVICE_TYPE].lower()
            if self.config[CONF_DEVICE_TYPE] == "ethernet":
                return await self._show_form(step="myethernet")
            elif self.config[CONF_DEVICE_TYPE] == "usb":
                return await self._show_form(step="myusb")
        errors = {}
        errors["base"] = "eth_or_usb"
        return await self._show_form(step="device", errors=errors)

    # ask for the ethernet settings
    async def async_step_myethernet(self, user_input=None):
        """Handle the input processing of the config flow."""
        self.config.update(user_input)
        self.config[CONF_PATH] = ""
        self.config[CONF_DEVICE_BAUD] = int(9600)
        return await self._show_form(step="parameters1")

    # ask for the usb settings
    async def async_step_myusb(self, user_input=None):
        """Handle the input processing of the config flow."""
        self.config.update(user_input)
        self.config[CONF_HOST] = ""
        self.config[CONF_PORT] = ""
        return await self._show_form(step="parameters1")

    async def async_step_parameters1(self, user_input=None):
        """Config flow step 1."""
        #_LOGGER.debug(f"async_step_parameters1,  step is 1 - {self.current_pos}")
        self.config.update(user_input)
        
        self.current_pos = -1

        if CONF_EMULATION_MODE in user_input:
            self.step_sequence = self.create_parameters_sequence(user_input[CONF_EMULATION_MODE])
            if len(self.step_sequence) == 0:
                _LOGGER.debug(f"********************* ERROR : CONF_EMULATION_MODE set to {user_input[CONF_EMULATION_MODE]} **********************************")
                return self.async_abort(reason="emulation_mode_error")
        else:
            _LOGGER.debug(f"********************* ERROR : CONF_EMULATION_MODE not in user_input **********************************")
            return self.async_abort(reason="emulation_mode_error")
        #_LOGGER.debug(f"async_step_parameters1 {user_input}")
        return await self.gotonext(user_input)


    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle a user config flow."""
        # determine if a panel connection has already been made and stop a second connection
        # _LOGGER.debug("Visonic async_step_user")
        #if self._async_current_entries():
            #return self.async_abort(reason="already_configured")
        #self.dumpMyState()

        # is this a raw configuration (not called from importing yaml)
        if not user_input:
            #_LOGGER.debug("Visonic in async_step_user - trigger user input")
            return await self._show_form(step="device")

        #_LOGGER.debug("Visonic async_step_user - importing a yaml config setup")

        # importing a yaml config setup
        info = await self.validate_input(user_input)
        if info is not None:
            return self.async_create_entry(title=info["title"], data=user_input)
        return self.async_abort(reason="device_error")

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
                    device_type = import_config.get(CONF_DEVICE)
                    if device_type[CONF_DEVICE_TYPE] == "ethernet":
                        data[CONF_DEVICE_TYPE] = "ethernet"
                        data[CONF_HOST] = device_type[CONF_HOST]
                        data[CONF_PORT] = device_type[CONF_PORT]
                        data[CONF_PATH] = ""
                        data[CONF_DEVICE_BAUD] = int(9600)
                    elif device_type[CONF_DEVICE_TYPE] == "usb":
                        data[CONF_DEVICE_TYPE] = "usb"
                        data[CONF_PATH] = device_type[CONF_PATH]
                        if CONF_DEVICE_BAUD in device_type:
                            data[CONF_DEVICE_BAUD] = device_type[CONF_DEVICE_BAUD]
                        else:
                            data[CONF_DEVICE_BAUD] = int(9600)
                        data[CONF_HOST] = ""
                        data[CONF_PORT] = ""
                else:
                    data[k] = import_config.get(k)
        except Exception as er:
            _LOGGER.debug(
                "Importing settings from configuration.yaml but something went wrong or some essential data is missing %s",
                str(er),
            )
            # _LOGGER.debug("     The current data is %s", import_config)
            return self.async_abort(reason="settings_missing")

        return await self.async_step_user(data)


class VisonicOptionsFlowHandler(OptionsFlow, MyHandlers):
    """Handle options."""

    VERSION = 4
    CONNECTION_CLASS = CONN_CLASS_LOCAL_POLL

    def __init__(self, config_entry : ConfigEntry):
        """Initialize options flow."""
        MyHandlers.__init__(self, config_entry)
        OptionsFlow.__init__(self)
        self.config = dict(config_entry.options)
        self.entry_id = config_entry.entry_id
        #_LOGGER.debug(f"init {self.entry_id} {self.config}")

    # when editing an existing config, start from parameters10 as the previous settings are not editable after the connection has been made
    async def async_step_init(self, user_input=None):
        """Manage the options."""
        
        #_LOGGER.debug(f"Edit config option settings, data = {user_input}")

        if self.config is not None and CONF_DEVICE_TYPE in self.config:
            t = self.config[CONF_DEVICE_TYPE].lower()
            #_LOGGER.debug(f"type = {type(t)}   t = {t}")
            if t == "ethernet" or t == "usb":

                self.current_pos = -1

                if CONF_EMULATION_MODE in self.config:
                    self.step_sequence = self.create_parameters_sequence(self.config[CONF_EMULATION_MODE])
                    if 2 in self.step_sequence:
                        self.step_sequence.remove(2) # remove the init parameters and only include modifyable
                    if len(self.step_sequence) == 0:
                        _LOGGER.debug(f"********************* ERROR : CONF_EMULATION_MODE set to {self.config[CONF_EMULATION_MODE]} **********************************")
                        return await self.async_abort(reason="emulation_mode_error")
                else:
                    _LOGGER.debug(f"********************* ERROR : CONF_EMULATION_MODE not in self.config **********************************")
                    return await self.async_abort(reason="emulation_mode_error")
                return await self.gotonext(user_input)

                #self.current_pos = -1
                #self.step_sequence = [2,3,4]
                #return await self.gotonext(user_input)
            else:
                _LOGGER.debug(f"Edit config option settings type = {t}, aborting")
        
        return self.async_abort(reason="device_error")

        


