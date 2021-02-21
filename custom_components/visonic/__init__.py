"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System."""

import logging

import requests.exceptions
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import VisonicClient
from .const import (
    DOMAIN,
    DOMAINCLIENT,
    DOMAINDATA,
    UNDO_VISONIC_UPDATE_LISTENER,
    DOMAINCLIENTTASK,
)
#from .create_schema import create_schema, set_defaults
from .create_schema import set_defaults

_LOGGER = logging.getLogger(__name__)

def configured_hosts(hass):
    """Return a set of the configured hosts."""
    # use 'type' as the key i.e. ethernet or usb as that always has to be configured
    return set(
        entry.data["type"] for entry in hass.config_entries.async_entries(DOMAIN)
    )


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


async def async_migrate_entry(hass, config_entry: ConfigEntry) -> bool:
    """Migrate old schema configuration entry to new."""
    # This function is called when I change VERSION in the ConfigFlow
    # If the config schema ever changes then use this function to convert from old to new config parameters
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        new = {**config_entry.data}
        # TODO: modify Config Entry data

        config_entry.data = {**new}
        config_entry.version = 2

    _LOGGER.info("Migration to version %s successful", config_entry.version)

    return True


async def async_setup(hass: HomeAssistant, base_config: dict):
    """Set up the visonic component."""
    return True


# This function is called with the flow data to create a client connection to the alarm panel
# From one of:
#    - the imported configuration.yaml values that have created a control flow
#    - the original control flow if it existed
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up visonic from a config entry."""

    # _LOGGER.debug("************* create connection here **************")
    hass.data[DOMAIN] = {}
    hass.data[DOMAIN][DOMAINDATA] = {}

    # initially empty the settings for this component
    hass.data[DOMAIN][entry.entry_id] = {}

    # combine and convert python settings map to dictionary
    conf = await combineSettings(entry)

    # push the merged data back in to HA
    hass.config_entries.async_update_entry(entry, options=conf)

    # save the parameters for control flow editing purposes
    set_defaults(conf)

    # create client and connect to the panel
    try:
        # create the client ready to connect to the panel
        client = VisonicClient(hass, conf, entry)
        # Save the client ref
        # connect to the panel
        clientTask = hass.async_create_task(client.connect())

        # save the client and its task
        hass.data[DOMAIN][entry.entry_id][DOMAINCLIENT] = client
        hass.data[DOMAIN][entry.entry_id][DOMAINCLIENTTASK] = clientTask
        # add update listener
        hass.data[DOMAIN][entry.entry_id][
            UNDO_VISONIC_UPDATE_LISTENER
        ] = entry.add_update_listener(async_options_updated)
        # return true to indicate success
        return True
    except requests.exceptions.ConnectionError as error:
        _LOGGER.error("Visonic Panel could not be reached: [%s]", error)
        raise ConfigEntryNotReady
    return False


# This function is called to terminate a client connection to the alarm panel
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload visonic entry."""
    # _LOGGER.debug("************* terminate connection here **************")

    client = hass.data[DOMAIN][entry.entry_id][DOMAINCLIENT]
    clientTask = hass.data[DOMAIN][entry.entry_id][DOMAINCLIENTTASK]
    updateListener = hass.data[DOMAIN][entry.entry_id][UNDO_VISONIC_UPDATE_LISTENER]

    # stop the comms to/from the panel
    await client.service_comms_stop()
    # stop all activity in the client
    await client.service_panel_stop()

    # Wait for all the platforms to unload.  Does this get called within core or do I need to doit?
    # all(await asyncio.gather(*[hass.config_entries.async_forward_entry_unload(entry, component) for component in PLATFORMS]))

    if updateListener is not None:
        updateListener()

    if clientTask is not None:
        clientTask.cancel()

    hass.data[DOMAIN][entry.entry_id] = {}

    # _LOGGER.debug("************* terminate connection success **************")
    return True


# This function is called when there have been changes made to the parameters in the control flow
async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry):
    """Edit visonic entry."""

    # _LOGGER.debug("************* update connection here **************")

    # get the visonic client
    client = hass.data[DOMAIN][entry.entry_id][DOMAINCLIENT]

    # combine and convert python settings map to dictionary
    conf = await combineSettings(entry)

    # save the parameters for control flow editing
    set_defaults(conf)

    # update the client parameter set
    client.updateConfig(conf)

    return True
