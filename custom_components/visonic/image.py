"""Support for Visonic PIR Camera image."""
import logging
from datetime import timedelta

from homeassistant.components.image import ImageEntity
from homeassistant.components.image import DOMAIN as IMAGE_DOMAIN
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import slugify
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .pyconst import AlSensorDevice, AlSensorCondition
from .client import VisonicClient
from .const import DOMAIN, MANUFACTURER, PANEL_ATTRIBUTE_NAME, DEVICE_ATTRIBUTE_NAME, VISONIC_TRANSLATION_KEY
from . import VisonicConfigEntry

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: VisonicConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Visonic Image Entity for Camera PIRs"""
    #_LOGGER.debug(f"image async_setup_entry start")

    @callback
    def async_add_image(device: AlSensorDevice) -> None:
        """Add Visonic Image Sensor."""
        entities: list[ImageEntity] = []
        entities.append(VisonicImage(hass, entry.runtime_data.client, device))
        #_LOGGER.debug(f"image adding {device.getDeviceID()}")
        async_add_entities(entities)

    entry.runtime_data.dispatchers[IMAGE_DOMAIN] = async_dispatcher_connect(hass, f"{DOMAIN}_{entry.entry_id}_add_{IMAGE_DOMAIN}", async_add_image)
    #_LOGGER.debug("image async_setup_entry exit")


class VisonicImage(ImageEntity):
    """A class to let you visualize the image from a PIR sensors camera."""

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
        dname = visonic_device.createFriendlyName()
        pname = client.getMyString()
        self._name = str(pname + dname).lower()
        self._panel = client.getPanelID()
        self._attr_translation_key = VISONIC_TRANSLATION_KEY
        self._sensor_image = None
        #_LOGGER.debug(f"************* image init ************** Sensor ID {self._dname}     Sensor Type {visonic_device.getSensorType()}")
        self._attr_unique_id = slugify(self._name + "_image")
        self._attr_name = "Image"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self._name)})

    # Called when an entity is about to be removed from Home Assistant. Example use: disconnect from the server or unsubscribe from updates.
    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        _LOGGER.debug(f"[async_will_remove_from_hass] id = {self.unique_id}")
        self._visonic_device = None
        self._is_available = False
        await super().async_will_remove_from_hass()

    def onChange(self, sensor : AlSensorDevice, s : AlSensorCondition):
        """Call on any change to the sensor."""
        # the sensor parameter is the same as self._visonic_device, but it's a generic callback handler that calls this function
        # Update the current value based on the device state
        #_LOGGER.debug(f"   In Image VisonicSensor onchange {self._visonic_device}")
        if self._visonic_device is not None:
            if s == AlSensorCondition.CAMERA and self._visonic_device.hasJPG:              # Camera update
                interval = timedelta(seconds=2)
                if self._attr_image_last_updated is not None:
                    interval = self._visonic_device.jpg_time - self._attr_image_last_updated
                if interval > timedelta(seconds=1):
                    _LOGGER.debug("[onChange] updating image")
                    self._sensor_image = self._visonic_device.jpg_data
                    self._attr_image_last_updated = self._visonic_device.jpg_time
                    # Ask HA to schedule an update
                    if self.hass is not None and self.entity_id is not None:
                        self.schedule_update_ha_state()
        else:
            _LOGGER.debug("changeHandler: image on change called but sensor is not defined")

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        attr = {}
        attr[PANEL_ATTRIBUTE_NAME] = self._panel
        attr[DEVICE_ATTRIBUTE_NAME] = self._visonic_device.getDeviceID()
        return attr

    async def async_image(self) -> bytes | None:
        """Update the image if it is not cached."""
        return self._sensor_image

    @property
    def has_entity_name(self) -> bool:
        """Prevent HA adding the device name to the start of the entity name."""
        return False
