"""Switches for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

import logging

from homeassistant.components.switch import ENTITY_ID_FORMAT, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import slugify
from .pyvisonic import X10Device
from .client import VisonicClient
from .const import DOMAIN, DOMAINCLIENT, VISONIC_UNIQUE_NAME, VISONIC_UPDATE_STATE_DISPATCHER

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Set up the Visonic Alarm Binary Sensors."""
    if DOMAIN in hass.data:
        client = hass.data[DOMAIN][entry.entry_id][DOMAINCLIENT]
        devices = [VisonicSwitch(client, device) for device in hass.data[DOMAIN]["switch"]]
        hass.data[DOMAIN]["switch"] = list()
        async_add_entities(devices, True)


class VisonicSwitch(SwitchEntity):
    """Representation of a Visonic X10 Switch."""

    def __init__(self, client: VisonicClient, visonic_device: X10Device):
        """Initialise a Visonic X10 Device."""
        # _LOGGER.debug("Creating X10 Switch %s", visonic_device.name)
        self.client = client
        self.visonic_device = visonic_device
        self.x10id = self.visonic_device.id
        self._name = "Visonic " + self.visonic_device.name
        # Append device id to prevent name clashes in HA.
        self.visonic_id = slugify(self._name)

        # VISONIC_ID_FORMAT.format( slugify(self._name), visonic_device.getDeviceID())
        self.entity_id = ENTITY_ID_FORMAT.format(self.visonic_id)
        self.current_value = self.visonic_device.state

    async def async_added_to_hass(self):
        """Register callbacks."""
        # Register for dispatcher calls to update the state
        self.async_on_remove(async_dispatcher_connect(self.hass, VISONIC_UPDATE_STATE_DISPATCHER, self.onChange))
        # self.visonic_device.install_change_handler(self.onChange)

    # Called when an entity is about to be removed from Home Assistant. Example use: disconnect from the server or unsubscribe from updates.
    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        # if self.visonic_device is not None:
        #    self.visonic_device.install_change_handler(None)
        self.visonic_device = None
        self.client = None
        _LOGGER.debug("switch async_will_remove_from_hass")

    # async def async_remove_entry(self, hass, entry) -> None:
    #    """Handle removal of an entry."""
    #    _LOGGER.debug("switch async_remove_entry")

    def onChange(self, event_id: int, datadictionary: dict):
        """Switch state has changed."""
        # _LOGGER.debug("Switch onchange %s", str(self._name))
        self.current_value = self.visonic_device.state
        self.schedule_update_ha_state()

    @property
    def should_poll(self):
        """Get polling requirement from visonic device."""
        return False

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self.visonic_id

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def assumed_state(self):
        """Return true if unable to access real state of entity."""
        return False

    @property
    def is_on(self):
        """Return true if device is on."""
        return self.current_value

    def turn_on(self, **kwargs):
        """Turn the device on."""
        self.turnmeonandoff("on")

    def turn_off(self, **kwargs):
        """Turn the device off."""
        self.turnmeonandoff("off")

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "manufacturer": "Visonic",
            "identifiers": {(DOMAIN, self._name)},
            "name": f"Visonic X10 ({self.visonic_device.name})",
            "model": self.visonic_device.type,
            "via_device": (DOMAIN, VISONIC_UNIQUE_NAME),
            # "sw_version": self._api.information.version_string,
        }

    # "off"  "on"  "dim"  "brighten"
    def turnmeonandoff(self, state):
        """Send disarm command."""
        self.client.setX10(self.x10id, state)

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        attr = {}

        attr["Location"] = self.visonic_device.location
        attr["Name"] = self.visonic_device.name
        attr["Type"] = self.visonic_device.type
        attr["Visonic Device"] = self.visonic_device.id
        #        attr["State"] = "Yes" if self.visonic_device.state else "No"
        return attr
