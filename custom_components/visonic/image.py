"""Support for Visonic Camera image."""
import asyncio
import io
import logging
from datetime import datetime, timedelta
from typing import Callable, List

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.dt as dt_util
from .pyconst import AlSensorDevice, AlSensorType, AlSensorCondition
from .client import VisonicClient
from .const import DOMAIN, SensorEntityFeature, DOMAINCLIENT, IMAGE_SENSOR_STR, PANEL_ATTRIBUTE_NAME, DEVICE_ATTRIBUTE_NAME

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Visonic Image Entity for Camera PIRs"""

    #_LOGGER.debug("************* image async_setup_entry **************")

    if DOMAIN in hass.data:
        #_LOGGER.debug("   In binary sensor async_setup_entry")
        client = hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id]
        images = [
            VisonicImage(hass, client, device) for device in hass.data[DOMAIN][entry.entry_id][IMAGE_SENSOR_STR]
        ]
        # empty the list as we have copied the entries so far in to sensors
        #hass.data[DOMAIN][entry.entry_id][IMAGE_SENSOR_STR] = list()
        async_add_entities(images, True)


class VisonicImage(ImageEntity):
    """A class to let you visualize the image from a PIR sensors camera."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, client: VisonicClient, visonic_device: AlSensorDevice):
        #super().__init__(self, hass)
        ImageEntity.__init__(self, hass)
        self._attr_image_last_updated = None
        self._cached_image = None
        self._attr_image_url = None
        self._attr_content_type = "image/jpeg"
        self._attr_should_poll = False

        self._visonic_device = visonic_device
        self._visonic_device.onChange(self.onChange)

        self._dname = visonic_device.createFriendlyName()
        pname = client.getMyString()
        self._name = pname.lower() + self._dname.lower()
        
        self._panel = client.getPanelID()
        
        self._sensor_image = None
        #_LOGGER.debug(f"************* image init ************** Sensor ID {self._dname}     Sensor Type {visonic_device.getSensorType()}")

    # Called when an entity is about to be removed from Home Assistant. Example use: disconnect from the server or unsubscribe from updates.
    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        self._visonic_device = None
        self._is_available = False
        _LOGGER.debug("image async_will_remove_from_hass")

    def onChange(self, sensor : AlSensorDevice, s : AlSensorCondition):
        """Call on any change to the sensor."""
        # the sensor parameter is the same as self._visonic_device, but it's a generic callback handler that cals this function
        # Update the current value based on the device state
        #_LOGGER.debug(f"   In Image VisonicSensor onchange {self._visonic_device}")
        if self._visonic_device is not None:
            if s == AlSensorCondition.CAMERA and self._visonic_device.hasJPG:              # Camera update
                interval = timedelta(seconds=2)
                if self._attr_image_last_updated is not None:
                    interval = self._visonic_device.jpg_time - self._attr_image_last_updated
                if interval > timedelta(seconds=1):
                    _LOGGER.debug("image updating image")
                    self._sensor_image = self._visonic_device.jpg_data
                    self._attr_image_last_updated = self._visonic_device.jpg_time
                    # Ask HA to schedule an update
                    self.schedule_update_ha_state()
        else:
            _LOGGER.debug("changeHandler: image on change called but sensor is not defined")
        
    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return slugify(self._name)

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        attr = {}
        attr[PANEL_ATTRIBUTE_NAME] = self._panel
        attr[DEVICE_ATTRIBUTE_NAME] = self._visonic_device.getDeviceID()
        return attr

    @property
    def should_poll(self):
        """Get polling requirement from visonic device."""
        # Polling would be a waste of time so we turn off polling and onChange callback is called when the sensor changes state
        return False

    async def async_image(self) -> bytes | None:
        """Update the image if it is not cached."""
        return self._sensor_image
