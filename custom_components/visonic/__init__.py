""" Create a connection to a Visonic PowerMax or PowerMaster Alarm System """
import asyncio
import jinja2
import logging
import requests.exceptions
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import VisonicClient
from .const import (
    DOMAIN,
    DOMAINCLIENT,
    DOMAINALARM,
    PLATFORMS,
    DOMAINDATA,
)
from .create_schema import set_defaults, create_schema

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema(create_schema()),}, extra=vol.ALLOW_EXTRA)


def configured_hosts(hass):
    """Return a set of the configured hosts."""
    # use 'type' as the key i.e. ethernet or usb as that always has to be configured
    return set(entry.data["type"] for entry in hass.config_entries.async_entries(DOMAIN))


async def async_setup(hass: HomeAssistant, base_config: dict):
    """Set up the visonic component."""
    # initially empty the settings for this component
    hass.data[DOMAIN] = {}
    hass.data[DOMAIN][DOMAINDATA] = {}
    hass.data[DOMAIN][DOMAINCLIENT] = {}
    hass.data[DOMAIN][DOMAINALARM] = {}

    # if there are no configuration.yaml settings then terminate
    if DOMAIN not in base_config:
        return True

    # has there been a flow configured panel connection before
    configured = configured_hosts(hass)

    # if there is not a flow configured connection previously
    #   then create a flow connection from the configuration.yaml data
    if len(configured) == 0:
        # get the configuration.yaml settings and make a 'flow' task :)
        #   this will run 'async_step_import' in config_flow.py
        conf = base_config.get(DOMAIN)
        hass.async_create_task(hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=conf))
    return True


# This function is called with the flow data to create a client connection to the alarm panel
# From one of:
#    - the imported configuration.yaml values that have created a control flow
#    - the original control flow if it existed
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up visonic from a config entry."""

    _LOGGER.debug("************* create connection here **************")

    # convert python map to dictionary
    conf = {}
    # the entry.data dictionary contains all the old data used on creation and is a complete set
    for k in entry.data:
        conf[k] = entry.data[k]
    # the entry.config dictionary contains the latest/updated values but may not be a complete set
    for k in entry.options:
        conf[k] = entry.options[k]

    # push the merged data back in to HA
    hass.config_entries.async_update_entry(entry, options=conf)

    # save the parameters for control flow editing purposes
    set_defaults(conf)

    # create client and connect to the panel
    try:
        # create the client ready to connect to the panel
        client = VisonicClient(hass, conf, entry)
        # Save the client ref
        hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id] = client
        # connect to the panel
        await hass.async_add_executor_job(client.connect)
        # add update listener
        entry.add_update_listener(async_options_updated)
        # return true to indicate success
        return True
    except requests.exceptions.ConnectionError as error:
        _LOGGER.error("Visonic Panel could not be reached: [%s]", error)
        raise ConfigEntryNotReady
    return False


# This function is called to terminate a client connection to the alarm panel
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload visonic entry."""
    _LOGGER.debug("************* terminate connection here **************")

    client = hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id]

    # stop the comms to/from the panel
    await client.service_comms_stop(None)
    # stop all activity in the client
    await client.service_panel_stop(None)

    # Wait for all the platforms to unload
    unload_ok = all(await asyncio.gather(*[hass.config_entries.async_forward_entry_unload(entry, component) for component in PLATFORMS]))

    if not unload_ok:
        return False
    return True


# This function is called when there have been changes made to the parameters in the control flow
async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry):
    """Edit visonic entry."""

    _LOGGER.debug("************* update connection here **************")

    client = hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id]

    # convert python map to dictionary
    conf = {}
    # the entry.data dictionary contains all the old data used on creation and is a complete set
    for k in entry.data:
        conf[k] = entry.data[k]
    # the entry.config dictionary contains the latest/updated values but may not be a complete set
    for k in entry.options:
        conf[k] = entry.options[k]

    # save the parameters for control flow editing
    set_defaults(conf)

    # update the client parameter set
    client.updateConfig(conf)

    return True
