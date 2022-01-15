"""Sensors for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

import logging
from datetime import datetime
from typing import Callable, List

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ARMED,
    ATTR_BATTERY_LEVEL,
    ATTR_LAST_TRIP_TIME,
    ATTR_TRIPPED,
)

from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify
from .pconst import PySensorDevice, PySensorType
from .client import VisonicClient

from .const import DOMAIN, DOMAINCLIENT, VISONIC_UNIQUE_NAME, VISONIC_UPDATE_STATE_DISPATCHER

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Callable[[List[Entity], bool], None],
) -> None:
    """Set up the Visonic Alarm Binary Sensors."""

    _LOGGER.debug("************* binary sensor async_setup_entry **************")

    if DOMAIN in hass.data:
        _LOGGER.debug("   In binary sensor async_setup_entry")
        client = hass.data[DOMAIN][entry.entry_id][DOMAINCLIENT]
        sensors = [
            VisonicSensor(client, device) for device in hass.data[DOMAIN]["binary_sensor"]
        ]
        # empty the list as we have copied the entries so far in to sensors
        hass.data[DOMAIN]["binary_sensor"] = list()
        async_add_entities(sensors, True)


#   Each Sensor in Visonic Alarms can be Armed/Bypassed individually
class VisonicSensor(BinarySensorEntity):
    """Representation of a Visonic Sensor."""

    def __init__(self, client: VisonicClient, visonic_device: PySensorDevice):
        """Initialize the sensor."""
        #_LOGGER.debug("Creating binary sensor %s",visonic_device.getDeviceName())
        self.visonic_device = visonic_device
        self._name = "visonic_" + self.visonic_device.getDeviceName().lower()
        # Append device id to prevent name clashes in HA.
        self._visonic_id = slugify(self._name)
        self._current_value = (self.visonic_device.isTriggered() or self.visonic_device.isOpen())
        self._is_available = self.visonic_device.isEnrolled()

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
        self.visonic_device = None
        _LOGGER.debug("binary sensor async_will_remove_from_hass")

    def onChange(self, event_id: int, datadictionary: dict):
        """Call on any change to the sensor."""
        #_LOGGER.debug("Sensor onchange %s", str(self._visonic_id))
        # Update the current value based on the device state
        if self.visonic_device is not None:
            self._current_value = (self.visonic_device.isTriggered() or self.visonic_device.isOpen())
            self._is_available = self.visonic_device.isEnrolled()
            # Ask HA to schedule an update
            self.schedule_update_ha_state()
        else:
            _LOGGER.debug("binary sensor on change called but sensor is not defined")

    @property
    def should_poll(self):
        """Get polling requirement from visonic device."""
        # Polling would be a waste of time so we turn off polling and onChange callback is called when the sensor changes state
        # I found that allowing it to poll caused delays in showing the sensor state in the frontend
        return False

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._visonic_id

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def is_on(self):
        """Return true if the binary sensor is on."""
        return self._current_value

    @property
    def device_info(self):
        """Return information about the device."""
        if self.visonic_device is not None:
            return {
                "manufacturer": "Visonic",
                "identifiers": {(DOMAIN, self._name)},
                "name": f"Visonic Sensor ({self.visonic_device.getDeviceName()})",
                "model": self.visonic_device.getSensorModel(),
                "via_device": (DOMAIN, VISONIC_UNIQUE_NAME),
            }
        return { "manufacturer": "Visonic", "via_device": (DOMAIN, VISONIC_UNIQUE_NAME) }

    #    # Called when an entity has their entity_id and hass object assigned, before it is written to the state machine for the first time.
    #    #     Example uses: restore the state, subscribe to updates or set callback/dispatch function/listener.
    #    async def async_added_to_hass(self):
    #        await super().async_added_to_hass()
    #        _LOGGER.debug('binary sensor async_added_to_hass')

    @property
    def device_class(self):
        """Return the class of this sensor."""
        if self.visonic_device is not None:
            stype = self.visonic_device.getSensorType()
            if stype is not None:                
                if stype == PySensorType.MOTION or stype == PySensorType.CAMERA:
                    return BinarySensorDeviceClass.MOTION
                if stype == PySensorType.MAGNET:
                    return BinarySensorDeviceClass.WINDOW
                if stype == PySensorType.WIRED:
                    return BinarySensorDeviceClass.DOOR
                if stype == PySensorType.SMOKE:
                    return BinarySensorDeviceClass.SMOKE
                if stype == PySensorType.FLOOD:
                    return BinarySensorDeviceClass.MOISTURE
                if stype == PySensorType.GAS:
                    return BinarySensorDeviceClass.GAS
                if stype == PySensorType.VIBRATION or stype == PySensorType.SHOCK:
                    return BinarySensorDeviceClass.VIBRATION
                if stype == PySensorType.TEMPERATURE:
                    return BinarySensorDeviceClass.HEAT
                if stype == PySensorType.SOUND:
                    return BinarySensorDeviceClass.SOUND
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._is_available

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        # _LOGGER.debug("in device_state_attributes")
        if self.visonic_device is not None:
            attr = self.visonic_device.getAttributes()
            attr[ATTR_TRIPPED] = "True" if self.visonic_device.isTriggered() else "False"
            stype = self.visonic_device.getSensorType()
            if stype is not None and stype != PySensorType.WIRED:
                attr[ATTR_BATTERY_LEVEL] = 0 if self.visonic_device.isLowBattery() else 100
            attr[ATTR_ARMED] = "False" if self.visonic_device.isBypass() else "True"
            if self.visonic_device.getLastTriggerTime() is None:
                attr[ATTR_LAST_TRIP_TIME] = None
            else:
                tm = self.visonic_device.getLastTriggerTime().isoformat()
                # miss off the decimal hundredths seconds onwards
                tm = tm.replace("T", " ")[0:21]
                attr[ATTR_LAST_TRIP_TIME] = tm
                # attr[ATTR_LAST_TRIP_TIME] = self.pmTimeFunctionStr(self.triggertime)
            return attr
            
        return { }
