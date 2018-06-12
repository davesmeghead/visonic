"""
Support for visonic sensors when used with a connection to a Visonic Alarm Panel.

  Initial setup by David Field

"""
import logging
import datetime
from datetime import timedelta

from homeassistant.util import convert, slugify
from homeassistant.components.binary_sensor import BinarySensorDevice
from homeassistant.components.sensor import ENTITY_ID_FORMAT
from homeassistant.const import (ATTR_ARMED, ATTR_BATTERY_LEVEL, ATTR_LAST_TRIP_TIME, ATTR_TRIPPED)
from custom_components.visonic import VISONIC_SENSORS

DEPENDENCIES = ['visonic']
#REQUIREMENTS = ['pyvisonic==0.0.1']

_LOGGER = logging.getLogger(__name__)

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the visonic controller devices."""
    _LOGGER.info("In setup_platform the sensor config file")
    
    add_devices(
        VisonicSensor(device)
        for device in hass.data[VISONIC_SENSORS]['sensor'])

#   Each Sensor in Visonic Alarms can be Armed/Bypassed individually
class VisonicSensor(BinarySensorDevice):
    """Representation of a Visonic Sensor."""

    def __init__(self, visonic_device):
        """Initialize the sensor."""
        self.visonic_device = visonic_device
        self._name = "Visonic " + self.visonic_device.dname
        # Append device id to prevent name clashes in HA.
        self.visonic_id = slugify(self._name) # VISONIC_ID_FORMAT.format( slugify(self._name), visonic_device.getDeviceID())
        self.update()
        self.entity_id = ENTITY_ID_FORMAT.format(self.visonic_id)
        self.current_value = "T" if self.visonic_device.triggered else "O" if self.visonic_device.status else "-"
        self.visonic_device.install_change_handler(self.onChange)
    
    def onChange(self):
        self.current_value = "T" if self.visonic_device.triggered else "O" if self.visonic_device.status else "-"
        self.schedule_update_ha_state()
        # Fire event my_cool_event with event data answer=42
        #self.hass.bus.fire('alarm_sensor', {
        #    self._name: self.current_value
        #})        

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def should_poll(self):
        """Get polling requirement from visonic device."""
        return False # self.visonic_device.should_poll

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self.visonic_id

    @property
    def state(self) -> bool:
        """Return the name of the sensor."""
        return self.current_value

    @property
    def icon(self):
        """Return the icon of this device."""
        if self.visonic_device.triggered:
            return 'mdi:magnet-on'
        return 'mdi:magnet'

    # convert the date and time to a string
    def pmTimeFunctionStr(self, d : datetime.datetime) -> str:
        return d.strftime("%a %d/%m/%Y at %H:%M:%S")
        
    @property
    def state_attributes(self): #device_
        """Return the state attributes of the device."""
        #_LOGGER.warning("in device_state_attributes")
        attr = {}

        attr[ATTR_TRIPPED] = "Yes" if self.visonic_device.triggered else "No"
        attr[ATTR_BATTERY_LEVEL] = 'Low' if self.visonic_device.lowbatt else 'Normal'
        attr[ATTR_ARMED] = 'Bypass' if self.visonic_device.bypass else 'Armed'
        if self.visonic_device.triggertime is None:
            attr[ATTR_LAST_TRIP_TIME] = ""
        else:
            attr[ATTR_LAST_TRIP_TIME] = self.pmTimeFunctionStr(self.visonic_device.triggertime)
        attr["Device Name"] = self.visonic_device.dname
        attr["Sensor Type"] = self.visonic_device.stype
        attr["Zone Type"] = self.visonic_device.ztype
        attr["Zone Name"] = self.visonic_device.zname
        attr["Zone Type Name"] = self.visonic_device.ztypeName
        attr["Zone Chime"] = self.visonic_device.zchime
        attr["Zone Tamper"] = "Yes" if self.visonic_device.tamper else "No"
        attr["Zone Open"] = "Yes" if self.visonic_device.status else "No"
        attr['Visonic Device Id'] = self.visonic_device.id
        
        # Not added
        #    self.partition = kwargs.get('partition', None)  # set   partition set (could be in more than one partition)
        return attr

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.visonic_device.enrolled
