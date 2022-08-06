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

from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from .pconst import PySensorDevice, PySensorType, PyCommandStatus
from .const import DOMAIN, DOMAINCLIENT, PANEL_ATTRIBUTE_NAME, DEVICE_ATTRIBUTE_NAME, AvailableNotifications

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

    _LOGGER.debug("************* select async_setup_entry **************")

    if DOMAIN in hass.data:
        _LOGGER.debug("   In select async_setup_entry")
        client = hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id]
        sensors = [
            VisonicSelect(hass, client, device) for device in hass.data[DOMAIN]["select"]
        ]
        # empty the list as we have copied the entries so far in to sensors
        hass.data[DOMAIN]["select"] = list()
        async_add_entities(sensors, True)


class VisonicSelect(SelectEntity):
    """Representation of a visonic arm/bypass select entity."""

    def __init__(self, hass: HomeAssistant, client: VisonicClient, visonic_device: PySensorDevice):
        """Initialize the visonic binary sensor arm/bypass select entity."""
        SelectEntity.__init__(self)
        #_LOGGER.debug("Creating select entity for %s",visonic_device.getDeviceName())
        self.hass = hass
        self._client = client
        self._visonic_device = visonic_device
        self._panel = client.getPanelID()
        if self._panel > 0:
            self._name = "visonic_p" + str(self._panel) + "_" + visonic_device.getDeviceName().lower()
        else:
            self._name = "visonic_" + visonic_device.getDeviceName().lower()
        self._is_available = self._visonic_device.isEnrolled()
        self._is_armed = not self._visonic_device.isBypass()
        self._pending_state_is_armed = None
        self._dispatcher = client.getDispatcher()

    async def async_added_to_hass(self):
        """Register callbacks."""
        # Register for dispatcher calls to update the state
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._dispatcher, self.onChange
            )
        )

    # Called when an entity is about to be removed from Home Assistant. Example use: disconnect from the server or unsubscribe from updates.
    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        self._visonic_device = None
        _LOGGER.debug("select async_will_remove_from_hass")

    def onChange(self, event_id: int, datadictionary: dict):
        """Call on any change to the sensor."""
        #_LOGGER.debug("Select Sensor onchange %s", str(self._name))
        # Update the current value based on the device state
        if self._visonic_device is not None:
            self._is_available = self._visonic_device.isEnrolled()
            self._is_armed = not self._visonic_device.isBypass()
        else:
            _LOGGER.debug("Select on change called but sensor is not defined")

        if self._pending_state_is_armed is not None and self._pending_state_is_armed == self._is_armed:
            _LOGGER.debug("Change Implemented in panel")
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
            if result == PyCommandStatus.SUCCESS:
                self._pending_state_is_armed = (option == ARMED)
            else:
                # Command not sent to panel
                _LOGGER.debug("Sensor Bypass: Command not sent to panel")
                message = "Command not sent to panel"
                if result == PyCommandStatus.FAIL_PANEL_CONFIG_PREVENTED:
                    message = "Sensor Bypass: Please check your panel settings and enable sensor bypass"
                elif result == PyCommandStatus.FAIL_USER_CONFIG_PREVENTED:
                    message = "Sensor Bypass: Please check your HA Configuration settings for this Integration and enable sensor bypass"
                elif result == PyCommandStatus.FAIL_INVALID_PIN:
                    message = "Sensor Bypass: Invalid PIN"
                elif result == PyCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS:
                    message = "Sensor Bypass: EPROM Download is in progress, please try again after this is complete"
                self._client.sendHANotification(AvailableNotifications.ALWAYS, message)
        else:
            raise ValueError(f"Can't set the armed state to {option}. Allowed states are: {self.options}")
        self.schedule_update_ha_state(True)

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        attr = {}
        #attr["name"] = self._visonic_device.getDeviceName()
        attr[PANEL_ATTRIBUTE_NAME] = self._panel
        attr[DEVICE_ATTRIBUTE_NAME] = self._visonic_device.getDeviceID()
        return attr
