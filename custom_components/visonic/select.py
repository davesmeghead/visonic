"""Support for Visonic Sensors Armed Select."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import slugify
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import VisonicConfigEntry
from .pyconst import AlSensorDevice, AlCommandStatus, AlSensorCondition
from .const import DOMAIN, PANEL_ATTRIBUTE_NAME, DEVICE_ATTRIBUTE_NAME, AvailableNotifications
from .client import VisonicClient

_LOGGER = logging.getLogger(__name__)

BYPASS = "Bypass"
ARMED = "Armed"

async def async_setup_entry(
    hass: HomeAssistant,
    entry: VisonicConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Visonic Alarm Bypass/Arm Select"""
    #_LOGGER.debug(f"select async_setup_entry start")
    client: VisonicClient = entry.runtime_data.client

    @callback
    def async_add_select(device: AlSensorDevice) -> None:
        """Add Visonic Select Sensor."""
        entities: list[SelectEntity] = []
        entities.append(VisonicSelect(hass, client, device))
        _LOGGER.debug(f"select adding {device.getDeviceID()}")
        async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_{entry.entry_id}_add_{SELECT_DOMAIN}",
            async_add_select,
        )
    )
    #_LOGGER.debug("select async_setup_entry exit")


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
        if self.entity_id is not None:
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
        if self.entity_id is not None:
            self.schedule_update_ha_state(True)

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        attr = {}
        attr[PANEL_ATTRIBUTE_NAME] = self._panel
        attr[DEVICE_ATTRIBUTE_NAME] = self._visonic_device.getDeviceID()
        return attr
