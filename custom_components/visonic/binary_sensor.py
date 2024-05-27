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

from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import Entity
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .pyconst import AlSensorDevice, AlSensorType, AlSensorCondition
from .client import VisonicClient
from .const import DOMAIN, SensorEntityFeature, DOMAINCLIENT, PANEL_ATTRIBUTE_NAME, DEVICE_ATTRIBUTE_NAME, BINARY_SENSOR_STR, IMAGE_SENSOR_STR

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Visonic Alarm Binary Sensors."""

    #_LOGGER.debug("************* binary sensor async_setup_entry **************")

    if DOMAIN in hass.data:
        #_LOGGER.debug("In binary sensor async_setup_entry")
        client = hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id]
        if not client.isDisableAllCommands():
            sensors = [
                VisonicBinarySensor(hass, client, device, entry) for device in hass.data[DOMAIN][entry.entry_id][BINARY_SENSOR_STR]
            ]
            # empty the list as we have copied the entries so far in to sensors
            hass.data[DOMAIN][entry.entry_id][BINARY_SENSOR_STR] = list()
            if len(sensors) > 0:
                async_add_entities(sensors, True)


#   Each Sensor in Visonic Alarms can be Armed/Bypassed individually
class VisonicBinarySensor(BinarySensorEntity):
    """Representation of a Visonic Sensor."""

    image_task_log = {}

    def __init__(self, hass, client: VisonicClient, sensor: AlSensorDevice, entry: ConfigEntry):
        """Initialize the sensor."""
        #_LOGGER.debug("   In binary sensor VisonicSensor initialisation")
        self.hass = hass
        self.client = client
        self.entry = entry

        self._visonic_device = sensor

        self._dname = sensor.createFriendlyName()
        pname = client.getMyString()
        self._name = pname.lower() + self._dname.lower()
        # _LOGGER.debug("   In binary sensor VisonicSensor friendlyname : " + str(self._name))
        
        self._panel = client.getPanelID()
  
        # Append device id to prevent name clashes in HA.
        self._current_value = (self._visonic_device.isTriggered() or self._visonic_device.isOpen())
        self._is_available = self._visonic_device.isEnrolled()
        
        self.checking_for_camera_type = True
        if sensor.getSensorType() == AlSensorType.CAMERA:
           VisonicBinarySensor.createImageEntity(self.hass, self.entry, self.client, sensor)           
           self.checking_for_camera_type = False

        self._visonic_device.onChange(self.onChange)

    # This static method creates a new image entity for a Camera Sensor
    # I did think about doing this in the client but it made more sense here
    @staticmethod
    def createImageEntity(hass: HomeAssistant, entry: ConfigEntry, client: VisonicClient, sensor: AlSensorDevice):
        from .image import VisonicImage

        platform_images = None
        
        platforms = entity_platform.async_get_platforms(hass, "visonic")
        for p in platforms:
            if p.config_entry.entry_id == entry.entry_id:
                #_LOGGER.debug(f"createImageEntity platform is {p}")
                if p.domain == IMAGE_SENSOR_STR:
                    platform_images = p
         
        if platform_images is not None:
            hass.async_create_task(platform_images.async_add_entities([ VisonicImage(hass, client, sensor) ], False))
            #_LOGGER.debug(f"createImageEntity platform is {platform_images} created add entities task")
        else:
            hass.data[DOMAIN][entry.entry_id][IMAGE_SENSOR_STR].append(sensor)
            #_LOGGER.debug(f"createImageEntity creating async_forward_entry_setup task {len(hass.data[DOMAIN][entry.entry_id][IMAGE_SENSOR_STR])}")
            image_task = None
            if entry.entry_id in VisonicBinarySensor.image_task_log:
                image_task = VisonicBinarySensor.image_task_log[entry.entry_id]
            #_LOGGER.debug(f"createImageEntity creating async_forward_entry_setup task {len(hass.data[DOMAIN][entry.entry_id][IMAGE_SENSOR_STR])}    {entry.entry_id}     {image_task}")
            if image_task is None or image_task.done():
                image_task = hass.async_create_task(hass.config_entries.async_forward_entry_setup(entry, IMAGE_SENSOR_STR))
                VisonicBinarySensor.image_task_log[entry.entry_id] = image_task
                #_LOGGER.debug(f"createImageEntity created async_forward_entry_setup task         {entry.entry_id}     {image_task}")
 
    # Called when an entity is about to be removed from Home Assistant. Example use: disconnect from the server or unsubscribe from updates.
    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        self._visonic_device = None
        self._is_available = False
        self.client = None
        _LOGGER.debug("binary sensor async_will_remove_from_hass")

    def onChange(self, sensor : AlSensorDevice, s : AlSensorCondition):
        """Call on any change to the sensor."""
        # the sensor parameter is the same as self._visonic_device, but it's a generic callback handler that cals this function
        # Update the current value based on the device state
        #_LOGGER.debug(f"   In binary sensor VisonicSensor onchange {self._visonic_device}   self.checking_for_camera_type={self.checking_for_camera_type}")
        if self._visonic_device is not None:
            if self.checking_for_camera_type and sensor.getSensorType() == AlSensorType.CAMERA:
                VisonicBinarySensor.createImageEntity(self.hass, self.entry, self.client, sensor) 
                self.checking_for_camera_type = False
        
            self._current_value = (self._visonic_device.isTriggered() or self._visonic_device.isOpen())
            self._is_available = self._visonic_device.isEnrolled()
            #_LOGGER.debug(f"   In binary sensor VisonicSensor onchange self._is_available = {self._is_available}    self._current_value = {self._current_value}")
            # Ask HA to schedule an update
            self.schedule_update_ha_state()
        else:
            _LOGGER.debug("changeHandler: binary sensor on change called but sensor is not defined")
        
    @property
    def should_poll(self):
        """Get polling requirement from visonic device."""
        # Polling would be a waste of time so we turn off polling and onChange callback is called when the sensor changes state
        # I found that allowing it to poll caused delays in showing the sensor state in the frontend
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
    def is_on(self):
        """Return true if the binary sensor is on."""
        return self._current_value

    @property
    def device_info(self):
        """Return information about the device."""
        if self._visonic_device is not None:
            return {
                "manufacturer": "Visonic",
                "identifiers": {(DOMAIN, self._name)},
                "name": f"Visonic Sensor ({self._dname})",
                "model": self._visonic_device.getSensorModel(),
            }
        return { 
                 "manufacturer": "Visonic", 
            }

    @property
    def supported_features(self) -> int:
        return SensorEntityFeature.BYPASS_FEATURE | SensorEntityFeature.ARMED_FEATURE

    #    # Called when an entity has their entity_id and hass object assigned, before it is written to the state machine for the first time.
    #    #     Example uses: restore the state, subscribe to updates or set callback/dispatch function/listener.
    #    async def async_added_to_hass(self):
    #        await super().async_added_to_hass()
    #        _LOGGER.debug('binary sensor async_added_to_hass')

    @property
    def device_class(self):
        """Return the class of this sensor."""
        if self._visonic_device is not None:
            stype = self._visonic_device.getSensorType()
            #_LOGGER.debug(f"   In binary sensor VisonicSensor device_class self._is_available = {self._is_available}    self._current_value = {self._current_value}   stype = {stype}")
            if stype is not None:                
                if stype == AlSensorType.MOTION or stype == AlSensorType.CAMERA:
                    return BinarySensorDeviceClass.MOTION
                if stype == AlSensorType.MAGNET:
                    return BinarySensorDeviceClass.WINDOW
                if stype == AlSensorType.WIRED:
                    return BinarySensorDeviceClass.DOOR
                if stype == AlSensorType.SMOKE:
                    return BinarySensorDeviceClass.SMOKE
                if stype == AlSensorType.FLOOD:
                    return BinarySensorDeviceClass.MOISTURE
                if stype == AlSensorType.GAS:
                    return BinarySensorDeviceClass.GAS
                if stype == AlSensorType.VIBRATION or stype == AlSensorType.SHOCK:
                    return BinarySensorDeviceClass.VIBRATION
                if stype == AlSensorType.TEMPERATURE:
                    return BinarySensorDeviceClass.HEAT
                if stype == AlSensorType.SOUND:
                    return BinarySensorDeviceClass.SOUND
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        #_LOGGER.debug(f"   In binary sensor VisonicSensor available self._is_available = {self._is_available}    self._current_value = {self._current_value}")
        return self._is_available

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        # _LOGGER.debug("in device_state_attributes")
        if self._visonic_device is not None:
            attr = {}
            attr["device name"] = self._dname
            if self._visonic_device.isZoneTamper() is None:
                attr["zone tamper"] = "Undefined"
            else:
                attr["zone tamper"] = "Yes" if self._visonic_device.isZoneTamper() else "No"
            if self._visonic_device.isTamper() is None:
                attr["device tamper"] = "Undefined"
            else:
                attr["device tamper"] = "Yes" if self._visonic_device.isTamper() else "No"
            attr["zone open"] = "Yes" if self._visonic_device.isOpen() else "No"
            
            if self._visonic_device.getSensorType() != AlSensorType.UNKNOWN:
                attr["sensor type"] = str(self._visonic_device.getSensorType())
            elif self._visonic_device.getRawSensorIdentifier() is not None:
                attr["sensor type"] = "Undefined " + str(self._visonic_device.getRawSensorIdentifier())
            else:
                attr["sensor type"] = "Unknown"

            #attr["zone type"] = self.ztype
            attr["zone name"] = self._visonic_device.getZoneLocation()
            attr["zone type"] = self._visonic_device.getZoneType()
            attr["zone chime"] = self._visonic_device.getChimeType()
            
            if self._visonic_device.getMotionDelayTime() is not None and len(str(self._visonic_device.getMotionDelayTime())) > 0:
                attr["zone motion off time"] = self._visonic_device.getMotionDelayTime()

            attr[DEVICE_ATTRIBUTE_NAME] = self._visonic_device.getDeviceID()

            attr[ATTR_TRIPPED] = "True" if self._visonic_device.isTriggered() else "False"
            stype = self._visonic_device.getSensorType()
            if stype is not None and stype != AlSensorType.WIRED:
                attr[ATTR_BATTERY_LEVEL] = 0 if self._visonic_device.isLowBattery() else 100
            attr[ATTR_ARMED] = "False" if self._visonic_device.isBypass() else "True"
            if self._visonic_device.getLastTriggerTime() is None:
                attr[ATTR_LAST_TRIP_TIME] = None
            else:
                tm = self._visonic_device.getLastTriggerTime().isoformat()
                # miss off the decimal hundredths seconds onwards
                tm = tm.replace("T", " ")[0:21]
                attr[ATTR_LAST_TRIP_TIME] = tm
                # attr[ATTR_LAST_TRIP_TIME] = self.pmTimeFunctionStr(self.triggertime)
            attr[PANEL_ATTRIBUTE_NAME] = self._panel
            return attr
            
        return { }
