"""Switches for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import slugify
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import VisonicConfigEntry
from .pyconst import AlX10Command, AlSwitchDevice
from .client import VisonicClient
from .const import (
    DOMAIN,
    PANEL_ATTRIBUTE_NAME,
    DEVICE_ATTRIBUTE_NAME,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: VisonicConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Visonic X10 Switch."""
    #_LOGGER.debug(f"switch async_setup_entry start")
    client: VisonicClient = entry.runtime_data.client

    @callback
    def async_add_switch(device: AlSwitchDevice) -> None:
        """Add Visonic Switch."""
        entities: list[SwitchEntity] = []
        entities.append(VisonicSwitch(hass, client, device))
        _LOGGER.debug(f"switch adding {device.getDeviceID()}")
        async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_{entry.entry_id}_add_{SWITCH_DOMAIN}",
            async_add_switch,
        )
    )
    #_LOGGER.debug("switch async_setup_entry exit")


class VisonicSwitch(SwitchEntity):
    """Representation of a Visonic X10 Switch."""

    def __init__(self, hass: HomeAssistant, client: VisonicClient, visonic_device: AlSwitchDevice):
        """Initialise a Visonic X10 Device."""
        #_LOGGER.debug("Creating X10 Switch %s", visonic_device.id)
        self._client = client
        self._visonic_device = visonic_device
        self._visonic_device.onChange(self.onChange)
        self._x10id = self._visonic_device.getDeviceID()
        self._dname = self._visonic_device.createFriendlyName()
        pname = client.getMyString()
        self._name = pname.lower() + self._dname.lower()
        self._panel = client.getPanelID()
        self._current_value = self._visonic_device.isOn()
        self._is_available = True

    # Called when an entity is about to be removed from Home Assistant. Example use: disconnect from the server or unsubscribe from updates.
    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        self._visonic_device = None
        self._is_available = False
        self._client = None
        _LOGGER.debug("switch async_will_remove_from_hass")

    def onChange(self, switch : AlSwitchDevice):
        """Switch state has changed."""
        # the switch parameter is the same as self._visonic_device, but it's a generic callback handler that cals this function
        _LOGGER.debug("Switch changeHandler %s", str(self._name))
        self._current_value = self._visonic_device.isOn()
        if self.entity_id is not None:
            self.schedule_update_ha_state()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        #_LOGGER.debug(f"   In binary sensor VisonicSensor available self._is_available = {self._is_available}    self._current_value = {self._current_value}")
        return self._is_available

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
        self.turnmeonandoff(AlX10Command.ON)

    def turn_off(self, **kwargs):
        """Turn the device off."""
        self.turnmeonandoff(AlX10Command.OFF)

    @property
    def device_info(self):
        """Return information about the device."""
        if self._visonic_device is not None:
            return {
                "manufacturer": "Visonic",
                "identifiers": {(DOMAIN, self._name)},
                "name": f"Visonic X10 ({self._dname})",
                "model": self._visonic_device.getType(),
                # "sw_version": self._api.information.version_string,
            }
        return { 
                 "manufacturer": "Visonic", 
            }

    # "off"  "on"  "dim"  "brighten"
    def turnmeonandoff(self, state : AlX10Command):
        """Send disarm command."""
        self._client.setX10(self._x10id, state)

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        attr = {}

        attr["location"] = self._visonic_device.getLocation()
        attr["name"] = self._dname
        attr["type"] = self._visonic_device.getType()
        attr[DEVICE_ATTRIBUTE_NAME] = self._visonic_device.getDeviceID()
        attr[PANEL_ATTRIBUTE_NAME] = self._panel
        #        attr["State"] = "on" if self.is_on() else "off"
        return attr
