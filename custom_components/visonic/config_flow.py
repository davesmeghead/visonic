"""Config flow for Visonic integration."""
import logging
import copy

import voluptuous as vol
from collections import OrderedDict

from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant import config_entries, core
from homeassistant.const import (ATTR_CODE, ATTR_ARMED, EVENT_HOMEASSISTANT_STOP, CONF_HOST, CONF_PORT, CONF_PATH, CONF_DEVICE)

from .const import *
from .create_schema import create_schema_device, create_schema_ethernet, create_schema_usb, create_schema_parameters1, create_schema_parameters2, create_schema_parameters3, create_schema_parameters4

log = logging.getLogger(__name__)

# Common class handlers for the creation and editing the control flows
#
#  Creation sequence (using VisonicConfigFlow)
#     - Connection type ("device") so user picks ethernet or usb from a drop down list
#     - User then enters either ethernet or usb parameters
#     - Parameters1
#     - Parameters2
#     - Parameters3
#
#  Modify/Edit sequence (using VisonicOptionsFlowHandler)
#     - Parameters2
#     - Parameters3
#     If we achieve Standard Plus or Powerlink with the panel then self.powermaster will be set to False or True, depending on the panel type
#     - if self.powermaster
#     -     Parameters4

# Common handler for creation and edit
class MyHandlers:

    def __init__(self):
        """Initialize the config flow."""
        #log.info("MyHandlers init")
        self.powermaster = False
        self.config = {}

    def toList(self, l, n):
        if n in l:
            if isinstance(l[n], str):
                tmplist = ( l[n].split(",") if n in l and l[n] != "" else [] )
                self.config[n] = [item.strip().lower() for item in tmplist]
            else:
                self.config[n] = l[n]

    async def _show_form(self, step="device", placeholders=None, errors=None) -> None:
        """Show the form to the user."""
        #log.info("show_form %s %s %s", step, placeholders, errors)
        
        ds = None
        
        if step == "device":
            ds = create_schema_device()
        elif step == "ethernet":
            ds = create_schema_ethernet()
        elif step == "usb":
            ds = create_schema_usb()
        elif step == "parameters1":
            ds = create_schema_parameters1()
        elif step == "parameters2":
            ds = create_schema_parameters2()
        elif step == "parameters3":
            ds = create_schema_parameters3()
        elif step == "parameters4":
            ds = create_schema_parameters4()
        else: 
            return self.async_abort(reason="device_error")
            
        if ds is None:
            # The only way this could happen is one of the create functions have returned None
            log.Error(f"show_form ds is None, step is {step}")
            return self.async_abort(reason="device_error")

        #log.info("show_form ds = {0}".format(ds))
        return self.async_show_form(
            step_id=step,
            data_schema=ds,
            errors=errors if errors else {},
            description_placeholders=placeholders if placeholders else {},
        )

    async def async_step_parameters1(self, user_input=None):
        if user_input is not None:
            self.config.update(user_input)
        return await self._show_form(step="parameters2")
    
    async def async_step_parameters2(self, user_input=None):
        if user_input is not None:
            self.config.update(user_input)
        return await self._show_form(step="parameters3")
    
    async def async_step_parameters3(self, user_input=None):
        import custom_components.visonic.pyvisonic as visonicApi   # Connection to python Library

        if user_input is not None:
            self.config.update(user_input)
        
        #log.info("async_step_parameters3 {0}".format(self.config))
        
        if self.powermaster:
            log.info("[config] Detected a powermaster so asking about B0 parameters")
            return await self._show_form(step="parameters4")

        log.info("[config] Detected a powermax so not asking about B0 parameters")
        return await self.processcomplete()
    
    async def async_step_parameters4(self, user_input=None):
        """Handle the input processing of the config flow."""
        # add parameters to config
        self.config.update(user_input)
        return await self.processcomplete()

    async def validate_input(self, data):
        """Validate the input"""
        return {"title": "Alarm Panel"}

    async def processcomplete(self):
        try:
            info = await self.validate_input(self.config)
            if info is not None:
                # convert comma separated string to a list
                if CONF_SIREN_SOUNDING in self.config:
                    self.toList(self.config, CONF_SIREN_SOUNDING)

                if CONF_EXCLUDE_SENSOR in self.config:
                    self.toList(self.config, CONF_EXCLUDE_SENSOR)
                    # convert string list to integer list
                    self.config[CONF_EXCLUDE_SENSOR] = [int(i) for i in self.config[CONF_EXCLUDE_SENSOR]] 
                
                if CONF_EXCLUDE_X10 in self.config:
                    self.toList(self.config, CONF_EXCLUDE_X10)
                    # convert string list to integer list
                    self.config[CONF_EXCLUDE_X10] = [int(i) for i in self.config[CONF_EXCLUDE_X10]] 
                
                return self.async_create_entry(title=info["title"], data=self.config)
        except Exception:  # pylint: disable=broad-except
            log.exception("Unexpected exception")
            errors["base"] = "unknown"
        return self.async_abort(reason="device_error")
    

@config_entries.HANDLERS.register(DOMAIN)
class VisonicConfigFlow(config_entries.ConfigFlow, MyHandlers, domain=DOMAIN):
    """Handle a Visonic flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return VisonicOptionsFlowHandler(config_entry)

    def __init__(self):
        """Initialize the config flow."""
        MyHandlers.__init__(self)
        #log.info("Visonic ConfigFlow init")

    # ask the user, ethernet or usb
    async def async_step_device(self, user_input=None):
        """Handle the input processing of the config flow."""
        log.info("async_step_device {0}".format(user_input))
        if user_input is not None and CONF_DEVICE_TYPE in user_input:
            self.config[CONF_DEVICE_TYPE] = user_input[CONF_DEVICE_TYPE].lower()
            if self.config[CONF_DEVICE_TYPE] == "ethernet":
                return await self._show_form(step="ethernet")
            elif self.config[CONF_DEVICE_TYPE] == "usb":
                return await self._show_form(step="usb")
        errors = {}
        errors["base"] = "eth_or_usb"
        return await self._show_form(step="device", errors=errors)    

    # ask for the ethernet settings
    async def async_step_ethernet(self, user_input=None):
        """Handle the input processing of the config flow."""
        self.config.update(user_input)
        return await self._show_form(step="parameters1")

    # ask for the usb settings
    async def async_step_usb(self, user_input=None):
        """Handle the input processing of the config flow."""
        self.config.update(user_input)
        return await self._show_form(step="parameters1")

    async def async_step_user(self, user_input=None):
        """Handle a user config flow."""
        # determine if a panel connection has already been made and stop a second connection
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        # is this a raw configuration (not called from importint yaml)
        if not user_input:
            log.info("Visonic in async_step_user - trigger user input")
            return await self._show_form(step="device")

        # importing a yaml config setup            
        info = await self.validate_input(user_input)
        if info is not None:
            return self.async_create_entry(title=info["title"], data=user_input)
        return self.async_abort(reason="device_error")

    # this is run to import the configuration.yaml parameters
    async def async_step_import(self, import_config):
        """Import a config entry from configuration.yaml."""
        #log.info("Visonic in async_step_import in  {0}".format(import_config))

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
                    elif device_type[CONF_DEVICE_TYPE] == "usb":
                        data[CONF_DEVICE_TYPE] = "usb"
                        data[CONF_PATH] = device_type[CONF_PATH]
                        data[CONF_DEVICE_BAUD] = device_type[CONF_DEVICE_BAUD]
                else:
                    data[k] = import_config.get(k)
        except:
            log.info("Importing settings from configuration.yaml but something went wrong or some essential data is missing.")
            log.info("                     The current data is {0}".format(import_config))
            return self.async_abort(reason="settings_missing")

        return await self.async_step_user(data)

class VisonicOptionsFlowHandler(config_entries.OptionsFlow, MyHandlers):
    """Handle Plex options."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self, config_entry):
        """Initialize Plex options flow."""
        MyHandlers.__init__(self)
        self.config = dict(config_entry.options)
        #log.info("init {0}".format(self.config))
        
    # when editing an existing config, start from parameters2 as the previous settings are not editable after the connection has been made
    async def async_step_init(self, user_input=None):
        """Manage the Plex options."""
        # Get the client
        if self.hass is not None:
            client = self.hass.data[DOMAIN][DOMAINCLIENT][VISONIC_UNIQUE_ID]
            if client is not None:
                # From the client, is it a PowerMaster panel (this assumes that the EPROM has been downloaded, or at least the 0x3C data)"
                self.powermaster = client.isPowerMaster()
        log.info(f"Edit config option settings, powermaster = {self.powermaster}")
        return await self._show_form(step="parameters2")

