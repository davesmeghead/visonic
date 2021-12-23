"""Support for Visonic Sensros Armed Select."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from .pconst import PySensorDevice, PySensorType, PyCommandStatus
from .const import DOMAIN, DOMAINCLIENT, VISONIC_UPDATE_STATE_DISPATCHER, AvailableNotifications

from .client import VisonicClient

_LOGGER = logging.getLogger(__name__)

BYPASS = "Bypass"
ARMED = "Armed"
PENDING = "Pending"

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Callable[[List[Entity], bool], None],
) -> None:
    """Set up the Visonic Alarm Bypass/Arm Select"""

    _LOGGER.debug("************* select async_setup_entry **************")

    if DOMAIN in hass.data:
        _LOGGER.debug("   In select async_setup_entry")
        client = hass.data[DOMAIN][entry.entry_id][DOMAINCLIENT]
        sensors = [
            VisonicSelect(hass, client, device) for device in hass.data[DOMAIN]["select"]
        ]
        # empty the list as we have copied the entries so far in to sensors
        hass.data[DOMAIN]["select"] = list()
        async_add_entities(sensors, True)


class VisonicSelect(SelectEntity):
    """Representation of a visonic arm/bypass select entity."""

    _attr_options = [BYPASS, ARMED, PENDING]

    def __init__(self, hass: HomeAssistant, client: VisonicClient, visonic_device: PySensorDevice):
        """Initialize the visonic binary sensor arm/bypass select entity."""
        SelectEntity.__init__(self)
        #_LOGGER.debug("Creating select entity for %s",visonic_device.getDeviceName())
        self.hass = hass
        self._client = client
        self._visonic_device = visonic_device
        self._name = "visonic_" + self._visonic_device.getDeviceName().lower()
        self._visonic_id = slugify(self._name)
        self._is_available = self._visonic_device.isEnrolled()
        self._is_armed = not self._visonic_device.isBypass()
        self._pending_state_is_armed = None
        self._lastSendOfCommand = None

    async def async_added_to_hass(self):
        """Register callbacks."""
        # Register for dispatcher calls to update the state
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, VISONIC_UPDATE_STATE_DISPATCHER, self.onChange
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
        #_LOGGER.debug("Select Sensor onchange %s", str(self._visonic_id))
        # Update the current value based on the device state
        if self._visonic_device is not None:
            #self._current_value = (self._visonic_device.isTriggered() or self._visonic_device.isOpen())
            self._is_available = self._visonic_device.isEnrolled()
            self._is_armed = not self._visonic_device.isBypass()
            # Ask HA to schedule an update
            self.schedule_update_ha_state()
        else:
            _LOGGER.debug("select on change called but sensor is not defined")

    @property
    def should_poll(self):
        """Get polling requirement from visonic device."""
        return True

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._is_available

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._visonic_id

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def options(self) -> list[str]:
        """Return a set of selectable options."""
        if self._pending_state_is_armed is not None and self._pending_state_is_armed != self._is_armed:
            return [PENDING]
        return [BYPASS, ARMED]

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, if any."""
        if self._pending_state_is_armed is not None and self._pending_state_is_armed != self._is_armed:
            return "mdi:alarm-snooze"
        elif self._is_armed:
            return "mdi:alarm"
        return "mdi:alarm-off"

    # get the current date and time
    def _getTimeFunction(self) -> datetime:
        return datetime.now()

    @property
    def current_option(self) -> str:
        """Return the visonic sensor armed state.
            The setting must represent the panel state for sensor armed/bypass so may take a few seconds to update"""
        if self._visonic_device is not None:
            self._is_armed = not self._visonic_device.isBypass()
    
        if self._pending_state_is_armed is not None and self._pending_state_is_armed != self._is_armed:
            interval = self._getTimeFunction() - self._lastSendOfCommand
            # log.debug("Checking last receive time {0}".format(interval))
            if interval <= timedelta(seconds=3):
                #_LOGGER.debug("Current Wait {0}".format(self.unique_id))
                return PENDING
            else:
                _LOGGER.debug("Sensor Bypass: Timeout, Panel state was not changed {0}".format(self.unique_id))
                client.sendHANotification(AvailableNotifications.ALWAYS, "Sensor Bypass: Timeout, Panel state was not changed")
        self._pending_state_is_armed = None
        if self._is_armed:
            #_LOGGER.debug("Current Armed {0}".format(self.unique_id))
            return ARMED
        #_LOGGER.debug("Current Bypassed {0}".format(self.unique_id))
        return BYPASS

    def select_option(self, option: str) -> None:
        """Change the visonic sensor armed state"""
        if self._pending_state_is_armed is not None and self._pending_state_is_armed != self._is_armed:
            _LOGGER.debug("Currently Pending {0} so ignoring request to select option".format(self.unique_id))
        elif option in self.options:
            if option != PENDING:
                _LOGGER.debug("Sending Option {0} to {1}".format(option, self.unique_id))
                result = self._client.sendBypass(self._visonic_device.getDeviceID(), option == BYPASS, "") # pin code to "" to use default if set
                if result == PyCommandStatus.SUCCESS:
                    self._pending_state_is_armed = (option != BYPASS)
                    self.schedule_update_ha_state()
                    self._lastSendOfCommand = self._getTimeFunction()
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
                    client.sendHANotification(AvailableNotifications.ALWAYS, message)
            else:
                # Option set to pending by the user, there must be an ongoing command to the panel.  Note that this should never happen, only if things have gone horribly wrong
                client.sendHANotification(AvailableNotifications.ALWAYS, "Sensor Bypass: Change in arm/bypass already in progress, please try again")
        else:
            raise ValueError(f"Can't set the armed state to {option}. Allowed states are: {self.options}")
        self.schedule_update_ha_state()