"""Support for Visonic Sensors Armed Select."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN

from homeassistant.exceptions import HomeAssistantError
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import slugify
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import VisonicConfigEntry
from .pyconst import AlSensorDevice, AlCommandStatus, AlSensorCondition
from .const import DOMAIN, PANEL_ATTRIBUTE_NAME, DEVICE_ATTRIBUTE_NAME
from .client import VisonicClient

_LOGGER = logging.getLogger(__name__)

BYPASS = "bypass"
ARMED = "armed"

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
        #_LOGGER.debug(f"select adding {device.getDeviceID()}")
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

    _attr_translation_key: str = "alarm_panel_key"
    #_attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, client: VisonicClient, visonic_device: AlSensorDevice):
        """Initialize the visonic binary sensor arm/bypass select entity."""
        SelectEntity.__init__(self)
        self.hass = hass
        self._client = client
        self._visonic_device = visonic_device
        self._visonic_device.onChange(self.onChange)
        dname = visonic_device.createFriendlyName()
        pname = client.getMyString()
        self._name = pname.lower() + dname.lower()  # this must match the binary_sensor self._name so that device_info associates them
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
        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state(True)

    # To link this entity to the device, this property must return an identifiers
    #      value matching that used in the binary sensor, but no other information such as name. 
    #           If name is returned, this entity will then also become a device in the HA UI.
    @property
    def device_info(self):
        """Return information to link this entity with the correct device."""
        return {"identifiers": {(DOMAIN, self._name)}}

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

    def isPanelConnected(self) -> bool:
        """Are we connected to the Alarm Panel."""
        # If we are starting up or have been removed then assume we need a valid code
        #_LOGGER.debug(f"alarm control panel isPanelConnected {self.entity_id=}")
        if self._client is None:
            return False
        return self._client.isPanelConnected()

    def select_option(self, option: str) -> None:
        """Change the visonic sensor armed state"""
        if not self.isPanelConnected():
            raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="no_panel_connection",
                    translation_placeholders={
                        "myname": self._client.getAlarmPanelUniqueIdent() if self._client is not None else "<******>"
                    }
                )

        if self._pending_state_is_armed is not None:
            _LOGGER.debug(f"Currently Pending {self.unique_id} so ignoring request to select option")
        elif option is not None and option in self.options:
            #_LOGGER.debug(f"Sending Option {option} to {self.unique_id}")
            result = self._client.sendBypass(self._visonic_device.getDeviceID(), option == BYPASS, "") # pin code to "" to use default if set
            if result == AlCommandStatus.SUCCESS:
                self._pending_state_is_armed = (option == ARMED)
            else:
                # Command not sent to panel
                _LOGGER.debug(f"Sensor Bypass: Command not sent to panel {result}")
        elif option is None:
            raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_arm_state_no_option",
                    translation_placeholders={
                        "options": str(self.options)
                    }
                )
        else:
            raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_arm_state",
                    translation_placeholders={
                        "option": option,
                        "options": str(self.options)
                    }
                )

        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state(True)

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        attr = {}
        attr[PANEL_ATTRIBUTE_NAME] = self._panel
        attr[DEVICE_ATTRIBUTE_NAME] = self._visonic_device.getDeviceID()
        return attr
