"""Sensors for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

import logging
import asyncio
import re

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.core import HomeAssistant, cached_property, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.util import slugify
from homeassistant.const import (
    ATTR_ARMED,
    ATTR_BATTERY_LEVEL,
    ATTR_LAST_TRIP_TIME,
    ATTR_TRIPPED,
    EntityCategory,
)

from . import VisonicConfigEntry
from .pyconst import AlSensorDevice, AlSensorType, AlSensorCondition
from .client import VisonicClient
from .const import (
    DOMAIN,
    VISONIC_TRANSLATION_KEY,
    SensorEntityFeature,
    PANEL_ATTRIBUTE_NAME,
    MANUFACTURER,
    DEVICE_ATTRIBUTE_NAME,
)

_LOGGER = logging.getLogger(__name__)

# Dictionary mapping between the Pyvisonic sensor type and the HA Sensor Class
_stype_to_ha_sensor_class = {
    AlSensorType.IGNORED     : None,
    AlSensorType.UNKNOWN     : None,
    AlSensorType.MOTION      : BinarySensorDeviceClass.MOTION,
    AlSensorType.CAMERA      : BinarySensorDeviceClass.MOTION,
    AlSensorType.MAGNET      : BinarySensorDeviceClass.WINDOW,
    AlSensorType.WIRED       : BinarySensorDeviceClass.DOOR,
    AlSensorType.SMOKE       : BinarySensorDeviceClass.SMOKE,
    AlSensorType.FLOOD       : BinarySensorDeviceClass.MOISTURE,
    AlSensorType.GAS         : BinarySensorDeviceClass.GAS,
    AlSensorType.VIBRATION   : BinarySensorDeviceClass.VIBRATION, 
    AlSensorType.SHOCK       : BinarySensorDeviceClass.VIBRATION,
    AlSensorType.TEMPERATURE : BinarySensorDeviceClass.HEAT,
    AlSensorType.SOUND       : BinarySensorDeviceClass.SOUND,
    AlSensorType.GLASS_BREAK : BinarySensorDeviceClass.VIBRATION,
}

async def async_setup_entry(
    hass: HomeAssistant,
    entry: VisonicConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Visonic Alarm Binary Sensors."""
    #_LOGGER.debug(f"[async_setup_entry] start")

    @callback
    def async_add_binary_sensor(device: AlSensorDevice) -> None:
        """Add Visonic Binary Sensor."""
        _LOGGER.debug(f"[async_setup_entry] adding {device.getDeviceID()}")
        vbs = VisonicBinarySensor(hass, entry.runtime_data.client, device, entry)
        entities: list[BinarySensorEntity] = []
        entities.append(vbs)
        # Separate diagnostic 'problem' binary sensors, one per condition
        client = entry.runtime_data.client
        for key, label, pm_only in _PROBLEM_CONDITIONS:
            if pm_only and not client.isPowerMaster():
                continue
            entities.append(VisonicConditionBinarySensor(hass, client, device, key, label))
        # Shock/vibration sensors are also magnetic contacts; expose their open/closed
        #   state as its own door/window binary sensor on the same device.
        if device.getSensorType() in _OPENING_TYPES:
            entities.append(VisonicOpeningBinarySensor(hass, client, device))
        async_add_entities(entities)
        entry.runtime_data.sensors.append(vbs)

    entry.runtime_data.dispatchers[BINARY_SENSOR_DOMAIN] = async_dispatcher_connect( hass, f"{DOMAIN}_{entry.entry_id}_add_{BINARY_SENSOR_DOMAIN}", async_add_binary_sensor )
    #_LOGGER.debug("[async_setup_entry] exit")


#   Each Sensor in Visonic Alarms can be Armed/Bypassed individually
class VisonicBinarySensor(BinarySensorEntity):
    """Representation of a Visonic Sensor."""

    def __init__(self, hass, client: VisonicClient, sensor: AlSensorDevice, entry: VisonicConfigEntry):
        """Initialize the sensor."""
        #_LOGGER.debug("[VisonicBinarySensor]   In binary sensor VisonicSensor initialisation")
        self.hass = hass
        self._client = client
        self.entry = entry

        self._visonic_device = sensor
        self.timerTask = None

        self._dname = sensor.createFriendlyName()
        pname = client.getMyString()
        self._name = str(pname + self._dname).lower()
        _LOGGER.debug(f"[VisonicBinarySensor] friendlyname : {self._name}")
        self._panel = client.getPanelID()
        # Append device id to prevent name clashes in HA.
        self._current_value = self._computeActive()
        self._is_available = self._visonic_device.isEnrolled()
        self._visonic_device.onChange(self.onChange)
        self._attr_unique_id = slugify(f"{self._name}_sensor")
        self._attr_should_poll = False
        self._attr_translation_key = VISONIC_TRANSLATION_KEY
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self._name)})

    # Called when an entity is about to be removed from Home Assistant. Example use: disconnect from the server or unsubscribe from updates.
    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        _LOGGER.debug(f"[async_will_remove_from_hass] id = {self.unique_id}")
        if self.timerTask is not None:
            _LOGGER.debug(f"[async_will_remove_from_hass] id = {self.unique_id} killing timer task")
            try:
                self.timerTask.cancel()
            except Exception as ex:
                # Do not cause a full Home Assistant Exception, keep it local here
                _LOGGER.debug("[async_will_remove_from_hass]...........             Caused an exception killing timer task")
                _LOGGER.debug(f"[async_will_remove_from_hass]                           {ex}")   
        self._visonic_device.onChange(None)
        self._visonic_device = None
        self._is_available = False
        self._client = None
        await super().async_will_remove_from_hass()

    async def _retainStateTimout(self):
        timeout = self._client.getSensorOnDelay(self.device_class)
        _LOGGER.debug(f"[_retainStateTimout] in   id = {self.unique_id}   timeout = {timeout}    dc={self.device_class}")
        await asyncio.sleep(timeout) 
        if self._visonic_device is not None:
            self._current_value = self._computeActive()
            self._is_available = self._visonic_device.isEnrolled()
        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state()
        _LOGGER.debug(f"[_retainStateTimout] out  id = {self.unique_id}   timeout = {timeout}    current = {self._current_value}")
        self.timerTask = None

    def onChange(self, sensor : AlSensorDevice, s : AlSensorCondition):
        """Call on any change to the sensor."""
        # the sensor parameter is the same as self._visonic_device, but it's a generic callback handler that cals this function
        # Update the current value based on the device state
        #_LOGGER.debug(f"[onChange]   In binary sensor VisonicSensor onchange {self._visonic_device}   self.checking_for_camera_type={self.checking_for_camera_type}")
        if self.hass is not None and self._visonic_device is not None:

            if self.timerTask is None:
                newval = self._computeActive()
                if newval and not self._current_value:
                    # kick off timer
                    self.timerTask = self.hass.loop.create_task(self._retainStateTimout())
                self._current_value = newval

            self._is_available = self._visonic_device.isEnrolled()
            _LOGGER.debug(f"[onChange] id = {self.unique_id}   self._is_available = {self._is_available}    self._current_value = {self._current_value}")
            # Ask HA to schedule an update
            if self.entity_id is not None:
                self.schedule_update_ha_state()
        else:
            _LOGGER.debug("[onChange] called but sensor is not defined")

    def _computeActive(self) -> bool:
        """Return the on/off value for this entity.

        Shock/vibration zones are also magnetic contacts. Their open/closed state
        is exposed as a separate opening binary sensor, so this entity reflects
        only shock/trigger activity and not the contact being open.
        """
        d = self._visonic_device
        if d.getSensorType() in _OPENING_TYPES:
            return bool(d.isTriggered())
        return d.isTriggered() or d.isOpen()

    def getDeviceID(self) -> int:
        if self._visonic_device is not None:
            return self._visonic_device.getDeviceID()
        return 0

    @cached_property
    def name(self) -> str | None:
        """Name."""
        return "Zone"

    @property
    def has_entity_name(self) -> bool:
        """Prevent HA adding the device name to the start of the entity name."""
        return False

    @property
    def is_on(self):
        """Return true if the binary sensor is on."""
        # Shock/vibration zones report their contact state via a separate opening
        # sensor, so report shock/trigger activity live rather than the retained value.
        if self._visonic_device is not None and self._visonic_device.getSensorType() in _OPENING_TYPES:
            return bool(self._visonic_device.isTriggered())
        return self._current_value

    @property
    def supported_features(self) -> int:
        return SensorEntityFeature.BYPASS_FEATURE | SensorEntityFeature.ARMED_FEATURE

    @property
    def device_class(self):
        """Return the class of this sensor."""
        if self._visonic_device is not None:
            stype = self._visonic_device.getSensorType()
            #_LOGGER.debug(f"[device_class] device_class self._is_available = {self._is_available}    self._current_value = {self._current_value}   stype = {stype}")
            if stype is not None and stype in _stype_to_ha_sensor_class:                
                return _stype_to_ha_sensor_class[stype]
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        #_LOGGER.debug(f"   In binary sensor VisonicSensor available self._is_available = {self._is_available}    self._current_value = {self._current_value}")
        return self._is_available

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        # _LOGGER.debug("in extra_state_attributes")
        if self._client is not None and self._visonic_device is not None:
            stype = self._visonic_device.getSensorType()

            attr = {}
            attr["device_name"] = self._dname

            if (t := self._visonic_device.isTamper()) is None:
                attr["device_tamper"] = "undefined"
            else:
                attr["device_tamper"] = t

            attr[ATTR_ARMED] = not self._visonic_device.isBypass()

            attr[ATTR_TRIPPED] = self._visonic_device.isTriggered()
            
            if self._visonic_device.getLastTriggerTime() is None:
                attr[ATTR_LAST_TRIP_TIME] = "unknown"
            else:
                tm = self._visonic_device.getLastTriggerTime() # .strftime("%d/%m/%Y, %H:%M:%S")
                #tm = self._visonic_device.getLastTriggerTime().isoformat()
                # miss off the decimal hundredths seconds onwards
                #tm = tm.replace("T", " ")[0:21]
                attr[ATTR_LAST_TRIP_TIME] = tm
                # attr[ATTR_LAST_TRIP_TIME] = self.pmTimeFunctionStr(self.triggertime)
            
            if stype != AlSensorType.MOTION and stype != AlSensorType.CAMERA:
                attr["zone_open"] = self._visonic_device.isOpen()

            if (t := self._visonic_device.isZoneTamper()) is None:
                attr["zone_tamper"] = "undefined"
            else:
                attr["zone_tamper"] = t
            
            #attr["zone type"] = self.ztype
            zn = self._visonic_device.getZoneLocation()
            if len(zn) == 2:
                attr["zone_name"] = zn[0]
                attr["zone_name_panel"] = "Unknown" if zn[1] is None else zn[1]

            attr["zone_type"] = self._visonic_device.getZoneType()
            attr["zone_chime"] = self._visonic_device.getChimeType()
            attr["zone_trouble"] = self._visonic_device.getProblem()
            if self._client.isPowerMaster():
                attr["zone_missing"] = self._visonic_device.isMissing()
                attr["zone_oneway"] = self._visonic_device.isOneWay()
                attr["zone_inactive"] = self._visonic_device.isInactive()
            
            if (l := self._visonic_device.getLux()) is not None:
                attr["zone_lux"] = l

            if (t := self._visonic_device.getTemperature()) is not None:
                attr["zone_temperature"] = t
            
            if self._client.isPowerMaster() and self._visonic_device.getMotionDelayTime() is not None and len(str(self._visonic_device.getMotionDelayTime())) > 0:
                attr["zone_motion_off_time"] = self._visonic_device.getMotionDelayTime()

            attr[DEVICE_ATTRIBUTE_NAME] = self._visonic_device.getDeviceID()

            if stype != AlSensorType.UNKNOWN:
                attr["sensor_type"] = str(stype).lower()
            elif self._visonic_device.getRawSensorIdentifier() is not None:
                attr["sensor_type"] = "Undefined " + str(self._visonic_device.getRawSensorIdentifier())
            else:
                attr["sensor_type"] = "unknown"

            if stype is not None and stype != AlSensorType.WIRED:
                attr[ATTR_BATTERY_LEVEL] = 0 if self._visonic_device.isLowBattery() else 100

            if self._client.getPartitionsInUse() is not None:   # Returns None when partitions not in use
                if (p := self._visonic_device.getPartition()) is not None:
                    attr["partition"] = list(p)

            attr[PANEL_ATTRIBUTE_NAME] = self._panel
            return attr

        return { }


# Per-condition diagnostic 'problem' binary sensors created for each zone.
#   (key, label, powermaster_only)
_PROBLEM_CONDITIONS = [
    ("trouble", "Trouble", False),
    ("missing", "Missing", True),
    ("inactive", "Inactive", True),
    ("oneway", "One-Way", True),
]

# Sensor types that are shock/vibration detectors but also report a magnetic contact
# open/closed state, for which a separate door/window binary sensor is created.
_OPENING_TYPES = (AlSensorType.SHOCK, AlSensorType.VIBRATION)


class VisonicConditionBinarySensor(BinarySensorEntity):
    """A diagnostic 'problem' binary sensor for a single Visonic zone condition.

    One instance is created per condition (trouble / missing / inactive / one-way)
    so each fault surfaces as its own entity.
    """

    _attr_translation_key: str = VISONIC_TRANSLATION_KEY
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass, client: VisonicClient, sensor: AlSensorDevice, key: str, label: str):
        """Initialize a per-condition problem binary sensor."""
        self.hass = hass
        self._client = client
        self._visonic_device = sensor
        self._key = key
        self._label = label
        self._dname = sensor.createFriendlyName()
        pname = client.getMyString()
        # Match VisonicBinarySensor._name so device_info links to the same HA device
        self._name = pname.lower() + self._dname.lower()
        self._panel = client.getPanelID()
        self._is_available = sensor.isEnrolled()
        sensor.onChange(self.onChange)

    def onChange(self, sensor: AlSensorDevice = None, s: AlSensorCondition = None):
        """Call on any change to the sensor."""
        if self._visonic_device is not None:
            self._is_available = self._visonic_device.isEnrolled()
        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state()

    async def async_will_remove_from_hass(self):
        """Remove from hass. Do not clear the shared device callback list here."""
        self._visonic_device = None
        self._client = None
        self._is_available = False
        await super().async_will_remove_from_hass()

    @property
    def should_poll(self):
        return False

    @property
    def unique_id(self) -> str:
        return slugify(self._name + "_" + self._key)

    @property
    def name(self):
        return self._name + " " + self._label

    @property
    def available(self) -> bool:
        return self._is_available

    @property
    def device_info(self):
        """Link this entity to the same device as the zone binary sensor."""
        return {"identifiers": {(DOMAIN, slugify(self._name))}}

    @property
    def is_on(self):
        """Return true if this specific condition is present on the device."""
        d = self._visonic_device
        if d is None:
            return None
        if self._key == "trouble":
            prob = d.getProblem()
            return prob is not None and str(prob).lower() not in ("none", "")
        if self._key == "missing":
            return bool(d.isMissing())
        if self._key == "inactive":
            return bool(d.isInactive())
        if self._key == "oneway":
            return bool(d.isOneWay())
        return None

    @property
    def extra_state_attributes(self):
        d = self._visonic_device
        if d is None:
            return {}
        attr = {DEVICE_ATTRIBUTE_NAME: d.getDeviceID(), PANEL_ATTRIBUTE_NAME: self._panel}
        if self._key == "trouble":
            attr["zone_trouble"] = d.getProblem()
        return attr


class VisonicOpeningBinarySensor(BinarySensorEntity):
    """Door/window open-closed binary sensor for a shock/vibration zone.

    Shock and vibration sensors are also magnetic contacts. The main zone entity
    keeps its vibration device class; this exposes the contact open/closed state
    separately as an 'opening' binary sensor on the same device.
    """

    _attr_translation_key: str = VISONIC_TRANSLATION_KEY
    _attr_device_class = BinarySensorDeviceClass.OPENING

    def __init__(self, hass, client: VisonicClient, sensor: AlSensorDevice):
        """Initialize the opening binary sensor."""
        self.hass = hass
        self._client = client
        self._visonic_device = sensor
        self._dname = sensor.createFriendlyName()
        pname = client.getMyString()
        # Match VisonicBinarySensor._name so device_info links to the same HA device
        self._name = pname.lower() + self._dname.lower()
        self._panel = client.getPanelID()
        self._is_available = sensor.isEnrolled()
        sensor.onChange(self.onChange)

    def onChange(self, sensor: AlSensorDevice = None, s: AlSensorCondition = None):
        """Call on any change to the sensor."""
        if self._visonic_device is not None:
            self._is_available = self._visonic_device.isEnrolled()
        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state()

    async def async_will_remove_from_hass(self):
        """Remove from hass. Do not clear the shared device callback list here."""
        self._visonic_device = None
        self._client = None
        self._is_available = False
        await super().async_will_remove_from_hass()

    @property
    def should_poll(self):
        return False

    @property
    def unique_id(self) -> str:
        return slugify(self._name + "_opening")

    @property
    def name(self):
        return self._name + " Opening"

    @property
    def available(self) -> bool:
        return self._is_available

    @property
    def device_info(self):
        """Link this entity to the same device as the zone binary sensor."""
        return {"identifiers": {(DOMAIN, slugify(self._name))}}

    @property
    def is_on(self):
        """Return true if the contact is open."""
        if self._visonic_device is None:
            return None
        return self._visonic_device.isOpen()
