"""Switches for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import slugify
from .pconst import PyX10Command, PySwitchDevice
from .client import VisonicClient
from .const import (
    DOMAIN,
    DOMAINCLIENT,
    PANEL_ATTRIBUTE_NAME,
    DEVICE_ATTRIBUTE_NAME,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the Visonic Alarm Binary Sensors."""
    if DOMAIN in hass.data:
        client = hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id]
        devices = [
            VisonicSwitch(client, device) for device in hass.data[DOMAIN]["switch"]
        ]
        hass.data[DOMAIN]["switch"] = list()
        async_add_entities(devices, True)


class VisonicSwitch(SwitchEntity):
    """Representation of a Visonic X10 Switch."""

    def __init__(self, client: VisonicClient, visonic_device: PySwitchDevice):
        """Initialise a Visonic X10 Device."""
        # _LOGGER.debug("Creating X10 Switch %s", visonic_device.name)
        self._client = client
        self.visonic_device = visonic_device
        self._x10id = self.visonic_device.getDeviceID()

        self._panel = client.getPanelID()
        if self._panel > 0:
            self._name = "visonic_p" + str(self._panel) + "_" + self.visonic_device.getName().lower()
        else:
            self._name = "visonic_" + self.visonic_device.getName().lower()
        
        # VISONIC_ID_FORMAT.format( slugify(self._name), visonic_device.getDeviceID())
        # self._entity_id = ENTITY_ID_FORMAT.format(slugify(self._name))
        self._current_value = self.visonic_device.isOn()
        self._dispatcher = client.getDispatcher()


    async def async_added_to_hass(self):
        """Register callbacks."""
        # Register for dispatcher calls to update the state
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._dispatcher, self.onChange
            )
        )
        # self.visonic_device.install_change_handler(self.onChange)

    # Called when an entity is about to be removed from Home Assistant. Example use: disconnect from the server or unsubscribe from updates.
    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        self.visonic_device = None
        self._client = None
        _LOGGER.debug("switch async_will_remove_from_hass")

    def onChange(self, event_id: int, datadictionary: dict):
        """Switch state has changed."""
        # _LOGGER.debug("Switch onchange %s", str(self._name))
        self._current_value = self.visonic_device.isOn()
        self.schedule_update_ha_state()

    @property
    def should_poll(self):
        """Get polling requirement from visonic device."""
        return False

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return slugify(self._name)

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def assumed_state(self):
        """Return False if unable to access real state of entity."""
        return False

    @property
    def is_on(self):
        """Return true if device is on."""
        return self._current_value

    def turn_on(self, **kwargs):
        """Turn the device on."""
        self.turnmeonandoff(PyX10Command.ON)

    def turn_off(self, **kwargs):
        """Turn the device off."""
        self.turnmeonandoff(PyX10Command.OFF)

    @property
    def device_info(self):
        """Return information about the device."""
        if self.visonic_device is not None:
            return {
                "manufacturer": "Visonic",
                "identifiers": {(DOMAIN, self._name)},
                "name": f"Visonic X10 ({self.visonic_device.getName()})",
                "model": self.visonic_device.getType(),
                #"via_device": (DOMAIN, self._uniqueName),
                # "sw_version": self._api.information.version_string,
            }
        return { 
                 "manufacturer": "Visonic", 
                 #"via_device": (DOMAIN, self._uniqueName),
            }

    # "off"  "on"  "dim"  "brighten"
    def turnmeonandoff(self, state : PyX10Command):
        """Send disarm command."""
        self._client.setX10(self._x10id, state)

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        attr = {}

        attr["location"] = self.visonic_device.getLocation()
        attr["name"] = self.visonic_device.getName()
        attr["type"] = self.visonic_device.getType()
        attr[DEVICE_ATTRIBUTE_NAME] = self.visonic_device.getDeviceID()
        attr[PANEL_ATTRIBUTE_NAME] = self._panel
        #        attr["State"] = "on" if self.is_on() else "off"
        return attr
