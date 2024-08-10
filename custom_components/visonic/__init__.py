"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System."""

from __future__ import annotations

import logging
import asyncio
import requests.exceptions
import voluptuous as vol

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.components import persistent_notification
from homeassistant.util.hass_dict import HassKey, HassEntryKey

from homeassistant.const import (
    Platform,
    ATTR_CODE,
    ATTR_ENTITY_ID,
    SERVICE_RELOAD,
)

from .pyconst import AlPanelCommand, AlX10Command
from .client import VisonicClient
from .const import (
    DOMAIN,
    ALARM_PANEL_EVENTLOG,
    ALARM_PANEL_RECONNECT,
    ALARM_PANEL_COMMAND,
    ALARM_PANEL_X10,
    ALARM_SENSOR_BYPASS,
    ALARM_SENSOR_IMAGE,
    ATTR_BYPASS,
    CONF_PANEL_NUMBER,
    CONF_ALARM_NOTIFICATIONS,
    CONF_MOTION_OFF_DELAY,
    CONF_MAGNET_CLOSED_DELAY,
    CONF_EMER_OFF_DELAY,
    PANEL_ATTRIBUTE_NAME,
    NOTIFICATION_ID,
    NOTIFICATION_TITLE,
    CONF_EMULATION_MODE,
    CONF_SENSOR_EVENTS,
    CONF_COMMAND,
    CONF_X10_COMMAND,
    available_emulation_modes,
    AvailableNotifications
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# the 6 schemas for the HA service calls
ALARM_SCHEMA_EVENTLOG = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_CODE, default=""): cv.string,
    }
)

ALARM_SCHEMA_COMMAND = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_COMMAND) : vol.In([x.lower().replace("_"," ").title() for x in list(AlPanelCommand.get_variables().keys())]),
        vol.Optional(ATTR_CODE, default=""): cv.string,
    }
)

ALARM_SCHEMA_X10 = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_X10_COMMAND) : vol.In([x.lower().replace("_"," ").title() for x in list(AlX10Command.get_variables().keys())]),
    }
)

ALARM_SCHEMA_RECONNECT = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    }
)

ALARM_SCHEMA_BYPASS = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_BYPASS, default=False): cv.boolean,
        vol.Optional(ATTR_CODE, default=""): cv.string,
    }
)

ALARM_SCHEMA_IMAGE = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    }
)

update_version_panel_number = 0

# Create the types for the Configuration Parameter Entry
VisonicConfigKey: HassEntryKey["VisonicConfigData"] = HassEntryKey(DOMAIN)
type VisonicConfigEntry = ConfigEntry[VisonicConfigData]

@dataclass
class VisonicConfigData:
    client: VisonicClient
    sensors: list()
    # Made it a class just in case I want to include more parameters in future

def findClient(hass, panel : int):
    """Look through all the configuration entries looking for the panel."""
    data = hass.data[VisonicConfigKey]
    for entry_id in data:
        # Cycle through the entry IDs. Get the VisonicConfigData Entry
        e = hass.data[VisonicConfigKey][entry_id]
        if e.client is not None:
            if panel == e.client.getPanelID():
                #_LOGGER.info(f"findClient success, found client and panel")
                return e.client
    return None

async def combineSettings(entry):
    """Combine the old settings from data and the new from options."""
    # convert python map to dictionary
    conf = {}
    # the entry.data dictionary contains all the old data used on creation and is a complete set
    for k in entry.data:
        conf[k] = entry.data[k]
    # the entry.config dictionary contains the latest/updated values but may not be a complete set
    for k in entry.options:
        conf[k] = entry.options[k]
    return conf

async def async_setup(hass: HomeAssistant, base_config: dict):
    """Set up the visonic component."""
    
    def sendHANotification(message: str):
        """Send a HA notification and output message to log file"""
        _LOGGER.info(message)
        persistent_notification.create(hass, message, title=NOTIFICATION_TITLE, notification_id=NOTIFICATION_ID)

    def getClient(call):
        #_LOGGER.debug(f"getClient called")        
        if isinstance(call.data, dict):
            #_LOGGER.debug(f"getClient called {call}")
            if ATTR_ENTITY_ID in call.data:
                eid = str(call.data[ATTR_ENTITY_ID])
                if valid_entity_id(eid):
                    mybpstate = hass.states.get(eid)
                    if mybpstate is not None:
                        if PANEL_ATTRIBUTE_NAME in mybpstate.attributes:
                            panel = mybpstate.attributes[PANEL_ATTRIBUTE_NAME]
                            client = findClient(hass, panel)
                            if client is not None:
                                #_LOGGER.debug(f"getClient success for panel {panel}")
                                return client, panel
                            else:
                                _LOGGER.warning(f"getClient - Panel found {panel} but Client Not Found")
                                return None, panel
        _LOGGER.warning(f"getClient - Client Not Found")
        return None, None

    async def service_panel_eventlog(call):
        """Handler for event log service"""
        _LOGGER.info("Event log called")
        
        client, panel = getClient(call)
        if client is not None:
            await client.service_panel_eventlog(call)
        elif panel is not None:
            sendHANotification(f"Event log failed - Panel {panel} not found")
        else:
            sendHANotification(f"Event log failed - Panel not found")
    
    async def service_panel_reconnect(call):
        """Handler for panel reconnect service"""
        _LOGGER.info("Service Panel reconnect called")
        client, panel = getClient(call)
        if client is not None:
            await client.service_panel_reconnect(call)
        elif panel is not None:
            sendHANotification(f"Service Panel reconnect failed - Panel {panel} not found")
        else:
            sendHANotification(f"Service Panel reconnect failed - Panel not found")
    
    async def service_panel_command(call):
        """Handler for panel command service"""
        _LOGGER.info(f"Service Panel command called")
        client, panel = getClient(call)
        if client is not None:
            await client.service_panel_command(call)
        elif panel is not None:
            sendHANotification(f"Service Panel command failed - Panel {panel} not found")
        else:
            sendHANotification(f"Service Panel command failed - Panel not found")

    async def service_panel_x10(call):
        """Handler for panel command service"""
        _LOGGER.info(f"Service Panel x10 called")
        client, panel = getClient(call)
        if client is not None:
            await client.service_panel_x10(call)
        elif panel is not None:
            sendHANotification(f"Service Panel x10 failed - Panel {panel} not found")
        else:
            sendHANotification(f"Service Panel x10 failed - Panel not found")
    
    async def service_sensor_bypass(call):
        """Handler for sensor bypass service"""
        _LOGGER.info("Service Panel sensor bypass called")
        client, panel = getClient(call)
        if client is not None:
            await client.service_sensor_bypass(call)
        elif panel is not None:
            sendHANotification(f"Service Panel sensor bypass failed - Panel {panel} not found")
        else:
            sendHANotification(f"Service Panel sensor bypass failed - Panel not found")
    
    async def service_sensor_image(call):
        """Handler for sensor image service"""
        _LOGGER.info("Service Panel sensor image update called")
        client, panel = getClient(call)
        if client is not None:
            await client.service_sensor_image(call)
        elif panel is not None:
            sendHANotification(f"Service sensor image update - Panel {panel} not found")
        else:
            sendHANotification(f"Service sensor image update failed - Panel not found")
 
    async def handle_reload(call) -> None: 
        """Handle reload service call."""
        _LOGGER.info("Domain {0} call {1} reload called: reloading integration".format(DOMAIN, call))
        current_entries = hass.config_entries.async_entries(DOMAIN)
        reload_tasks = [
            hass.config_entries.async_reload(entry.entry_id)
            for entry in current_entries
        ]
        await asyncio.gather(*reload_tasks)

    _LOGGER.info("Starting Visonic Component")
    hass.data[VisonicConfigKey] = {}

    # Install the 5 handlers for the HA service calls
    hass.services.async_register(
        domain = DOMAIN,
        service = ALARM_PANEL_EVENTLOG,
        service_func = service_panel_eventlog,
        schema = ALARM_SCHEMA_EVENTLOG,
    )
    hass.services.async_register(
        DOMAIN, 
        ALARM_PANEL_RECONNECT, 
        service_panel_reconnect, 
        schema=ALARM_SCHEMA_RECONNECT,
    )
    hass.services.async_register(
        DOMAIN,
        ALARM_PANEL_COMMAND,
        service_panel_command,
        schema=ALARM_SCHEMA_COMMAND,
    )
    hass.services.async_register(
        DOMAIN,
        ALARM_PANEL_X10,
        service_panel_x10,
        schema=ALARM_SCHEMA_X10,
    )
    hass.services.async_register(
        DOMAIN,
        ALARM_SENSOR_BYPASS,
        service_sensor_bypass,
        schema=ALARM_SCHEMA_BYPASS,
    )
    
    
#    hass.services.async_register(
#        DOMAIN,
#        ALARM_SENSOR_IMAGE,
#        service_sensor_image,
#        schema=ALARM_SCHEMA_IMAGE,
#    )
    
    # Install the reload handler
    #    commented out as it reloads all panels, the default in the frontend only reloads the instance
    #async_register_admin_service(hass, DOMAIN, SERVICE_RELOAD, handle_reload)
    return True


# This function is called with the flow data to create a client connection to the alarm panel
# From one of:
#    - the imported configuration.yaml values that have created a control flow
#    - the original control flow if it existed
async def async_setup_entry(hass: HomeAssistant, entry: VisonicConfigEntry) -> bool:
    """Set up visonic from a config entry."""

    def configured_hosts(hass):
        """Return a set of the configured hosts."""
        return len(hass.config_entries.async_entries(DOMAIN))

    _LOGGER.debug(f"[Visonic Setup] ************************************ create connection ************************************")
    #_LOGGER.debug(f"[Visonic Setup]       Entry data={entry.data}   options={entry.options}")
    _LOGGER.debug(f"[Visonic Setup]       Entry id={entry.entry_id} in a total of {configured_hosts(hass)} previously configured panels")

    # combine and convert python settings map to dictionary
    conf = await combineSettings(entry)

    panel_id = 0
    if CONF_PANEL_NUMBER in conf:
        panel_id = int(conf[CONF_PANEL_NUMBER])
        #_LOGGER.debug(f"[Visonic Setup] Panel Config has panel number {panel_id}")
    else:
        _LOGGER.debug("[Visonic Setup] CONF_PANEL_NUMBER not in configuration, stopping configuration with an error")
        return False

    # Check for unique panel ids or HA gets really confused and we end up make a big mess in the config files.
    if findClient(hass, panel_id) is not None:
        _LOGGER.warning(f"[Visonic Setup] The Panel Number {panel_id} is not Unique, you already have a Panel with this Number")
        return False

    # When here, panel_id should be unique in the panels configured so far.
    _LOGGER.debug(f"[Visonic Setup]       Panel Ident {panel_id}")
    
    # push the merged data back in to HA and update the title
    hass.config_entries.async_update_entry(entry, title=f"Panel {panel_id}", options=conf)

    # create client and connect to the panel
    try:
        # create the client ready to connect to the panel, this will initialse the client but nothing more
        client = VisonicClient(hass, panel_id, conf, entry)

        # save the client and its task
        hass.data.setdefault(VisonicConfigKey, {})[entry.entry_id] = entry.runtime_data = VisonicConfigData(client, list())

        # make the client connection to the panel        
        await client.connect()

        _LOGGER.debug(f"[Visonic Setup] Setting client ID for entry id {entry.entry_id}")

        # add update listener to unload.  The update listener is used when the user edits an existing configuration.
        entry.async_on_unload(entry.add_update_listener(update_listener))

        _LOGGER.debug(f"[Visonic Setup] Returning True for entry id {entry.entry_id}")
        # return true to indicate success
        return True
    except requests.exceptions.ConnectionError as error:
        _LOGGER.error("[Visonic Setup] Visonic Panel could not be reached: [%s]", error)
        raise ConfigEntryNotReady
    return False
 
 
async def async_migrate_entry(hass: HomeAssistant, config_entry: VisonicConfigEntry) -> bool:
    """Migrate old schema configuration entry to new."""
    global update_version_panel_number
    # This function is called when I change VERSION in the ConfigFlow
    # If the config schema ever changes then use this function to convert from old to new config parameters
    version = config_entry.version

    _LOGGER.info(f"Migrating from version {version}")

    if version == 1:
        # Leave CONF_FORCE_STANDARD in place but use it to add CONF_EMULATION_MODE
        version = 2
        new = config_entry.data.copy()
        CONF_FORCE_STANDARD = "force_standard"
        
        _LOGGER.debug(f"   Migrating CONF_FORCE_STANDARD from {config_entry.data[CONF_FORCE_STANDARD]}")
        if isinstance(config_entry.data[CONF_FORCE_STANDARD], bool):
            _LOGGER.debug(f"   Migrating CONF_FORCE_STANDARD from {config_entry.data[CONF_FORCE_STANDARD]} and its boolean")
            if config_entry.data[CONF_FORCE_STANDARD]:
                _LOGGER.info(f"   Migration: Force standard set so using {available_emulation_modes[1]}")
                new[CONF_EMULATION_MODE] = available_emulation_modes[1]
            else:
                _LOGGER.info(f"   Migration: Force standard not set so using {available_emulation_modes[0]}")
                new[CONF_EMULATION_MODE] = available_emulation_modes[0]
        
        #del new[CONF_FORCE_STANDARD]  # decided to keep it
        hass.config_entries.async_update_entry(config_entry, data=new, options=new, version=version)
        _LOGGER.info(f"   Emulation mode set to {config_entry.data[CONF_EMULATION_MODE]}")

    if version == 2:
        version = 3
        new = config_entry.data.copy()
        
        CONF_FORCE_STANDARD = "force_standard"
        CONF_FORCE_AUTOENROLL = "force_autoenroll"
        CONF_AUTO_SYNC_TIME = "sync_time"
        if CONF_FORCE_STANDARD in new:
            del new[CONF_FORCE_STANDARD]       # decided to remove it
        if CONF_FORCE_AUTOENROLL in new:
            del new[CONF_FORCE_AUTOENROLL]
        if CONF_AUTO_SYNC_TIME in new:
            del new[CONF_AUTO_SYNC_TIME]
        _LOGGER.debug("   Updated config settings to remove unused data")
        
        if CONF_MOTION_OFF_DELAY in new:
            # Add the 2 new timeouts with the same values as the old setting
            new[CONF_MAGNET_CLOSED_DELAY] = new[CONF_MOTION_OFF_DELAY]
            new[CONF_EMER_OFF_DELAY] = new[CONF_MOTION_OFF_DELAY]
            _LOGGER.debug("   Added additional trigger delay settings")

        new[CONF_SENSOR_EVENTS] = list()
        _LOGGER.debug("   Sensor Event List created and set to empty")

        new[CONF_ALARM_NOTIFICATIONS] = [AvailableNotifications.CONNECTION_PROBLEM, AvailableNotifications.SIREN]
        hass.config_entries.async_update_entry(config_entry, data=new, options=new, version=version)
        _LOGGER.debug("   Alarm Notification list set to default")

    if version == 3:
        version = 4
        new = config_entry.data.copy()
        
        if CONF_PANEL_NUMBER not in new:
            # We have to assume that multiple panels will be updated at the same time, otherwise it gets complicated
            _LOGGER.debug(f"   Migrating Panel Number, using {update_version_panel_number}")
            new[CONF_PANEL_NUMBER] = update_version_panel_number
            update_version_panel_number = update_version_panel_number + 1
        else:
            _LOGGER.debug(f"   Panel Number already set to {new[CONF_PANEL_NUMBER]} so updating config version number only")
            
        hass.config_entries.async_update_entry(config_entry, data=new, options=new, version=version)

    _LOGGER.info("Migration to version %s successful", config_entry.version)
    return True


# This function is called to terminate a client connection to the alarm panel
async def async_unload_entry(hass: HomeAssistant, entry: VisonicConfigEntry):
    """Unload visonic entry."""
    _LOGGER.debug("************* terminating connection **************")

    data = entry.runtime_data
    if data.client is not None:
        p = data.client.getPanelID()
        # stop all activity in the client
        unload_ok = await data.client.service_panel_stop()

        if entry.entry_id in hass.data[VisonicConfigKey]:
            hass.data[VisonicConfigKey].pop(entry.entry_id)
        else:
            _LOGGER.debug(f"************* Panel {p} nothing to pop config key entry **********************")
        
        if unload_ok:
            _LOGGER.debug(f"************* Panel {p} terminate connection success **************")
        else:
            _LOGGER.debug(f"************* Panel {p} terminate connection success (with platform unloading problems) **************")
        return unload_ok
    else:
        _LOGGER.debug("************* terminate connection fail, no client **************")
    return False


# This function is called when there have been changes made to the parameters in the control flow
async def update_listener(hass: HomeAssistant, entry: VisonicConfigEntry):
    """Edit visonic entry."""

    _LOGGER.debug("************* update connection data **************")
    data = entry.runtime_data
    if data.client is not None:
        # combine and convert python settings map to dictionary
        conf = await combineSettings(entry)
        # update the client parameter set
        data.client.updateConfig(conf)
    return True
