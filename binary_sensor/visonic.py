"""
Support for visonic sensors when used with a connection to a Visonic Alarm Panel.

  Initial setup by David Field

"""
import logging
import datetime
from datetime import timedelta

from homeassistant.util import convert, slugify
from homeassistant.components.binary_sensor import BinarySensorDevice
#from homeassistant.helpers.entity import Entity
from homeassistant.components.sensor import ENTITY_ID_FORMAT
from homeassistant.const import (ATTR_ARMED, ATTR_BATTERY_LEVEL, ATTR_LAST_TRIP_TIME, ATTR_TRIPPED)
from custom_components.visonic import VISONIC_SENSORS
#from homeassistant.const import (STATE_ON, STATE_OFF)

DEPENDENCIES = ['visonic']

_LOGGER = logging.getLogger(__name__)

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the visonic controller devices."""
    _LOGGER.info("In setup_platform the sensor config file")
    
    add_devices(
        VisonicSensor(device)
        for device in hass.data[VISONIC_SENSORS]['binary_sensor'])

#   Each Sensor in Visonic Alarms can be Armed/Bypassed individually
class VisonicSensor(BinarySensorDevice):
    """Representation of a Visonic Sensor."""

    def __init__(self, visonic_device):
        """Initialize the sensor."""
        _LOGGER.info("In setup_platform in binary sensor")
        self.visonic_device = visonic_device
        self._name = "Visonic " + self.visonic_device.dname
        # Append device id to prevent name clashes in HA.
        self.visonic_id = slugify(self._name) # VISONIC_ID_FORMAT.format( slugify(self._name), visonic_device.getDeviceID())
        #self.update()
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
    def should_poll(self):
        """Get polling requirement from visonic device."""
        return False # self.visonic_device.should_poll

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self.visonic_id

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def is_on(self):
        """Return true if the binary sensor is on."""
        return self.current_value != "-"

#    @property
#    def state(self) -> bool:
#        """Return the state of the sensor."""
#        #return STATE_ON if (self.current_value == "T" or self.current_value == "O") else STATE_OFF
#        return self.current_value
        
    @property
    def device_info(self):
        """Return information about the device."""
        return {
            'manufacturer': 'Visonic',
        }

    @property
    def device_class(self):
        """Return the class of this sensor."""
        if self.visonic_device is not None:
            if self.visonic_device.stype is not None:
                if self.visonic_device.stype.lower() == 'motion' or self.visonic_device.stype.lower() == 'camera':
                    return 'motion'
                if self.visonic_device.stype.lower() == 'magnet':
                    return 'window'
                if self.visonic_device.stype.lower() == 'wired':
                    return 'door'
                if self.visonic_device.stype.lower() == 'smoke':
                    return 'smoke'
                if self.visonic_device.stype.lower() == 'gas':
                    return 'gas'
        # The only other one is from a PowerMaster and it is a temperature sensor. Do not use this yet (and it isnt a binary sensor)
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if self.visonic_device is not None:
            return self.visonic_device.enrolled
        return False;

    # convert the date and time to a string
    def pmTimeFunctionStr(self, d : datetime.datetime) -> str:
        return d.strftime("%a %d/%m/%Y at %H:%M:%S")

    @property
    def device_state_attributes(self):
        """Return device specific state attributes. Implemented by platform classes. """
        return None

    @property
    def state_attributes(self):
        """Return the state attributes of the device."""
        #_LOGGER.warning("in state_attributes")
        attr = {}

        attr[ATTR_TRIPPED] = "Yes" if self.visonic_device.triggered else "No"
        attr[ATTR_BATTERY_LEVEL] = 'Low' if self.visonic_device.lowbatt else 'Normal'
        attr[ATTR_ARMED] = 'Bypass' if self.visonic_device.bypass else 'Armed'
        if self.visonic_device.triggertime is None:
            attr[ATTR_LAST_TRIP_TIME] = ""
        else:
            attr[ATTR_LAST_TRIP_TIME] = self.pmTimeFunctionStr(self.visonic_device.triggertime)
        attr["Device Name"] = self.visonic_device.dname
        #attr["Sensor Type"] = self.visonic_device.stype  commented out as this is returned as the device_class now
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

