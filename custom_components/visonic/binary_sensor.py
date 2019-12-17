"""
Support for visonic sensors when used with a connection to a Visonic Alarm Panel.
Currently, there is only support for a single partition

  Initial setup by David Field

"""
import logging

from collections import defaultdict
from homeassistant.util import slugify
from homeassistant.components.binary_sensor import BinarySensorDevice
from homeassistant.components.sensor import ENTITY_ID_FORMAT
from homeassistant.const import (ATTR_ARMED, ATTR_BATTERY_LEVEL, ATTR_LAST_TRIP_TIME, ATTR_TRIPPED)

DEPENDENCIES = ['visonic']

VISONIC_SENSORS = 'visonic_sensors'

_LOGGER = logging.getLogger(__name__)

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the visonic controller devices."""
    _LOGGER.info("In setup_platform the sensor config file")
    
    if VISONIC_SENSORS in hass.data:
        _LOGGER.info("   In binary sensor setup_platform and there are VISONIC_SENSORS {0}".format(hass.data[VISONIC_SENSORS]['binary_sensor']))
        add_devices(
            VisonicSensor(device)
            for device in hass.data[VISONIC_SENSORS]['binary_sensor'])
        hass.data[VISONIC_SENSORS] = defaultdict(list)

#   Each Sensor in Visonic Alarms can be Armed/Bypassed individually
class VisonicSensor(BinarySensorDevice):
    """Representation of a Visonic Sensor."""

    def __init__(self, visonic_device):
        """Initialize the sensor."""
        _LOGGER.info("In setup_platform in binary sensor {0}".format(visonic_device.dname))
        self.visonic_device = visonic_device
        self._name = "Visonic " + self.visonic_device.dname
        # Append device id to prevent name clashes in HA.
        self.visonic_id = slugify(self._name) # VISONIC_ID_FORMAT.format( slugify(self._name), visonic_device.getDeviceID())
        self.entity_id = ENTITY_ID_FORMAT.format(self.visonic_id)
        self.current_value = self.visonic_device.triggered or self.visonic_device.status
        self.visonic_device.install_change_handler(self.onChange)
    
    def onChange(self):
        self.current_value = self.visonic_device.triggered or self.visonic_device.status
        self.schedule_update_ha_state()

    @property
    def should_poll(self):
        """Get polling requirement from visonic device."""
        return False

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
        return self.current_value

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
                if self.visonic_device.stype.lower() == 'vibration' or self.visonic_device.stype.lower() == 'shock':
                    return 'vibration'
                if self.visonic_device.stype.lower() == 'temperature':
                    return 'heat'
                if self.visonic_device.stype.lower() == 'shock':
                    return 'vibration'
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if self.visonic_device is not None:
            return self.visonic_device.enrolled
        return False;

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        #_LOGGER.warning("in device_state_attributes")
        attr = {}

        attr[ATTR_TRIPPED] = 'True' if self.visonic_device.triggered else 'False'
        attr[ATTR_BATTERY_LEVEL] = 0 if self.visonic_device.lowbatt else 100
        attr[ATTR_ARMED] = 'False' if self.visonic_device.bypass else 'True'
        if self.visonic_device.triggertime is None:
            attr[ATTR_LAST_TRIP_TIME] = None
        else:
            attr[ATTR_LAST_TRIP_TIME] = self.visonic_device.triggertime.isoformat()
            #attr[ATTR_LAST_TRIP_TIME] = self.pmTimeFunctionStr(self.visonic_device.triggertime)
        
        attr["device name"] = self.visonic_device.dname

        if self.visonic_device.stype is not None:
            attr["sensor type"] = self.visonic_device.stype
        else:
            attr["sensor type"] = "Undefined"
       
        attr["zone type"] = self.visonic_device.ztype
        attr["zone name"] = self.visonic_device.zname
        attr["zone type name"] = self.visonic_device.ztypeName
        attr["zone chime"] = self.visonic_device.zchime
        attr["zone tripped"] = "Yes" if self.visonic_device.ztrip else "No"
        attr["zone tamper"] = "Yes" if self.visonic_device.ztamper else "No"
        attr["device tamper"] = "Yes" if self.visonic_device.tamper else "No"
        attr["zone open"] = "Yes" if self.visonic_device.status else "No"
        attr['visonic device'] = self.visonic_device.id
        
        # Not added
        #    self.partition = kwargs.get('partition', None)  # set   partition set (could be in more than one partition)
        return attr
