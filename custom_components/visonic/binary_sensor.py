""" Sensors for the connection to a Visonic PowerMax or PowerMaster Alarm System """
import logging

from collections import defaultdict
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ARMED,
    ATTR_BATTERY_LEVEL,
    ATTR_LAST_TRIP_TIME,
    ATTR_TRIPPED,
)
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.util import slugify

from .const import DOMAIN, VISONIC_UNIQUE_NAME

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Set up the Visonic Alarm Binary Sensors"""

    _LOGGER.debug("************* binary sensor async_setup_entry **************")

    # Try to get the dispatcher working
    #    @callback
    #    def async_add_binary_sensor(binary_sensor):
    #        """Add Visonic binary sensor."""
    #        _LOGGER.debug(f"   got device {binary_sensor.getDeviceID()}")
    #        async_add_entities([binary_sensor], True)
    #    async_dispatcher_connect(hass, "visonic_new_binary_sensor", async_add_binary_sensor)

    if DOMAIN in hass.data:
        _LOGGER.debug("   In binary sensor async_setup_entry")
        sensors = [VisonicSensor(device) for device in hass.data[DOMAIN]["binary_sensor"]]
        # empty the list as we have copied the entries so far in to sensors
        hass.data[DOMAIN]["binary_sensor"] = list()
        async_add_entities(sensors, True)


#   Each Sensor in Visonic Alarms can be Armed/Bypassed individually
class VisonicSensor(BinarySensorEntity):
    """Representation of a Visonic Sensor."""

    def __init__(self, visonic_device):
        """Initialize the sensor."""
        # _LOGGER.debug("Creating binary sensor %s",visonic_device.dname)
        self.visonic_device = visonic_device
        self._name = "visonic_" + self.visonic_device.dname.lower()
        # Append device id to prevent name clashes in HA.
        self.visonic_id = slugify(self._name)

        # VISONIC_ID_FORMAT.format( slugify(self._name), visonic_device.getDeviceID())

        self.entity_id = ENTITY_ID_FORMAT.format(self.visonic_id)
        self.current_value = self.visonic_device.triggered or self.visonic_device.status
        self.visonic_device.install_change_handler(self.onChange)

    def onChange(self):
        """Called on any change to the sensor."""
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
            "manufacturer": "Visonic",
            "identifiers": {(DOMAIN, self._name)},
            "name": f"Visonic Sensor ({self.visonic_device.dname})",
            "model": self.visonic_device.stype,
            "via_device": (DOMAIN, VISONIC_UNIQUE_NAME),
        }

    #    # Called when an entity has their entity_id and hass object assigned, before it is written to the state machine for the first time.
    #    #     Example uses: restore the state, subscribe to updates or set callback/dispatch function/listener.
    #    async def async_added_to_hass(self):
    #        await super().async_added_to_hass()
    #        _LOGGER.debug('binary sensor async_added_to_hass')

    # Called when an entity is about to be removed from Home Assistant. Example use: disconnect from the server or unsubscribe from updates.
    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        _LOGGER.debug("binary sensor async_will_remove_from_hass")

    async def async_remove_entry(self, hass, entry) -> None:
        """Handle removal of an entry."""
        await super().async_remove_entry()
        _LOGGER.debug("binary sensor async_remove_entry")

    @property
    def device_class(self):
        """Return the class of this sensor."""
        if self.visonic_device is not None:
            if self.visonic_device.stype is not None:
                if self.visonic_device.stype.lower() == "motion" or self.visonic_device.stype.lower() == "camera":
                    return "motion"
                if self.visonic_device.stype.lower() == "magnet":
                    return "window"
                if self.visonic_device.stype.lower() == "wired":
                    return "door"
                if self.visonic_device.stype.lower() == "smoke":
                    return "smoke"
                if self.visonic_device.stype.lower() == "gas":
                    return "gas"
                if self.visonic_device.stype.lower() == "vibration" or self.visonic_device.stype.lower() == "shock":
                    return "vibration"
                if self.visonic_device.stype.lower() == "temperature":
                    return "heat"
                if self.visonic_device.stype.lower() == "shock":
                    return "vibration"
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if self.visonic_device is not None:
            return self.visonic_device.enrolled
        return False

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        # _LOGGER.debug("in device_state_attributes")
        attr = {}

        attr[ATTR_TRIPPED] = "True" if self.visonic_device.triggered else "False"
        attr[ATTR_BATTERY_LEVEL] = 0 if self.visonic_device.lowbatt else 100
        attr[ATTR_ARMED] = "False" if self.visonic_device.bypass else "True"
        if self.visonic_device.triggertime is None:
            attr[ATTR_LAST_TRIP_TIME] = None
        else:
            attr[ATTR_LAST_TRIP_TIME] = self.visonic_device.triggertime.isoformat()
            # attr[ATTR_LAST_TRIP_TIME] = self.pmTimeFunctionStr(self.visonic_device.triggertime)

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
        attr["visonic device"] = self.visonic_device.id

        # Not added
        #    self.partition = kwargs.get('partition', None)  # set   partition set (could be in more than one partition)
        return attr
