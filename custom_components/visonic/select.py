"""Support for Visonic Sensros Armed Select."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify
from homeassistant.const import (
    ATTR_ARMED,
)

#from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from .pyconst import AlSensorDevice, AlSensorType, AlCommandStatus, AlSensorCondition
from .const import DOMAIN, DOMAINCLIENT, PANEL_ATTRIBUTE_NAME, DEVICE_ATTRIBUTE_NAME, AvailableNotifications, SELECT_STR

from .client import VisonicClient

_LOGGER = logging.getLogger(__name__)

BYPASS = "Bypass"
ARMED = "Armed"

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Callable[[List[Entity], bool], None],
) -> None:
    """Set up the Visonic Alarm Bypass/Arm Select"""

    #_LOGGER.debug("************* select async_setup_entry **************")

    if DOMAIN in hass.data:
        # _LOGGER.debug("   In select async_setup_entry")
        client = hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id]
        if not client.isDisableAllCommands():
            sensors = [
                VisonicSelect(hass, client, device) for device in hass.data[DOMAIN][entry.entry_id][SELECT_STR]
            ]
            # empty the list as we have copied the entries so far in to sensors
            hass.data[DOMAIN][entry.entry_id][SELECT_STR] = list()
            if len(sensors) > 0:
                async_add_entities(sensors, True)


class VisonicSelect(SelectEntity):
    """Representation of a visonic arm/bypass select entity."""

    def __init__(self, hass: HomeAssistant, client: VisonicClient, visonic_device: AlSensorDevice):
        """Initialize the visonic binary sensor arm/bypass select entity."""
        SelectEntity.__init__(self)
        self.hass = hass
        self._client = client
        self._visonic_device = visonic_device
        self._visonic_device.onChange(self.onChange)
        
        dname = visonic_device.createFriendlyName()
        pname = client.getMyString()
        self._name = pname.lower() + dname.lower()

        self._panel = client.getPanelID()
        
        self._is_available = self._visonic_device.isEnrolled()
        self._is_armed = not self._visonic_device.isBypass()
        self._pending_state_is_armed = None

    # Called when an entity is about to be removed from Home Assistant. Example use: disconnect from the server or unsubscribe from updates.
    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        self._visonic_device = None
        self._client = None
        self._is_available = False
        _LOGGER.debug("select async_will_remove_from_hass")

    def onChange(self, sensor : AlSensorDevice, s : AlSensorCondition):
        """Call on any change to the sensor."""
        # Update the current value based on the device state
        if self._visonic_device is not None:
            self._is_available = self._visonic_device.isEnrolled()
            self._is_armed = not self._visonic_device.isBypass()
        else:
            _LOGGER.debug("Select on change called but sensor is not defined")

        if self._pending_state_is_armed is not None and self._pending_state_is_armed == self._is_armed:
            #_LOGGER.debug("Change Implemented in panel")
            self._pending_state_is_armed = None

        # Ask HA to schedule an update
        self.schedule_update_ha_state(True)

    @property
    def options(self) -> list[str]:
        """Return a set of selectable options."""
        return [BYPASS, ARMED]

    @property
    def current_option(self) -> str | None:
        """Return the selected entity option to represent the entity state."""
        return ARMED if self._is_armed else BYPASS

    @property
    def should_poll(self):
        """Get polling requirement from visonic device."""
        return False

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._is_available

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return slugify(self._name)

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, if any."""
        if self._pending_state_is_armed is not None:
            return "mdi:alarm-snooze"
        elif self._is_armed:
            return "mdi:alarm"
        return "mdi:alarm-off"

    def select_option(self, option: str) -> None:
        """Change the visonic sensor armed state"""
        if self._pending_state_is_armed is not None:
            _LOGGER.debug("Currently Pending {0} so ignoring request to select option".format(self.unique_id))
            self._client.sendHANotification(AvailableNotifications.ALWAYS, "Sensor Bypass: Change in arm/bypass already in progress, please try again")
        elif option in self.options:
            #_LOGGER.debug("Sending Option {0} to {1}".format(option, self.unique_id))
            result = self._client.sendBypass(self._visonic_device.getDeviceID(), option == BYPASS, "") # pin code to "" to use default if set
            if result == AlCommandStatus.SUCCESS:
                self._pending_state_is_armed = (option == ARMED)
            else:
                # Command not sent to panel
                _LOGGER.debug("Sensor Bypass: Command not sent to panel")
                message = "Command not sent to panel"
                if result == AlCommandStatus.FAIL_PANEL_CONFIG_PREVENTED:
                    message = "Sensor Bypass: Please check your panel settings and enable sensor bypass"
                elif result == AlCommandStatus.FAIL_USER_CONFIG_PREVENTED:
                    message = "Sensor Bypass: Please check your HA Configuration settings for this Integration and enable sensor bypass"
                elif result == AlCommandStatus.FAIL_INVALID_CODE:
                    message = "Sensor Bypass: Invalid PIN"
                elif result == AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS:
                    message = "Sensor Bypass: EPROM Download is in progress, please try again after this is complete"
                self._client.sendHANotification(AvailableNotifications.ALWAYS, message)
        else:
            raise ValueError(f"Can't set the armed state to {option}. Allowed states are: {self.options}")
        self.schedule_update_ha_state(True)

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        attr = {}
        attr[PANEL_ATTRIBUTE_NAME] = self._panel
        attr[DEVICE_ATTRIBUTE_NAME] = self._visonic_device.getDeviceID()
        return attr
