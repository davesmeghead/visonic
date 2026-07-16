"""Support for Visonic PIR Camera image."""

import asyncio
from collections.abc import Mapping
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_IDLE, STATE_OK, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    DEVICE_ATTRIBUTE_NAME,
    DOMAIN,
    MANUFACTURER,
    PANEL_ATTRIBUTE_NAME,
    VISONIC_TRANSLATION_KEY,
)
from .coordinator_base import VisonicCoordinator
from .visonic_entity_types import SensorState, ZoneSensorData
from .visonic_types import VisonicConfigData, VisonicCoordinatorData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Visonic Image Entity for Camera PIRs."""

    @callback
    def async_add_image(sensor_data: ZoneSensorData) -> None:
        """Add Visonic Image Sensor."""
        async_add_entities(
            [VisonicImage(hass=hass, entry=entry, sensor_id=sensor_data.device_id, identifier=sensor_data.identifier)]
        )

    vce: VisonicConfigData = entry.runtime_data
    vce.dispatchers[Platform.IMAGE] = async_dispatcher_connect(
        hass, f"{DOMAIN}_{entry.entry_id}_add_{Platform.IMAGE}", async_add_image
    )


class VisonicImage(CoordinatorEntity[VisonicCoordinator], ImageEntity):
    """A class to let you visualize the image from a PIR sensors camera."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, sensor_id: int, identifier: str
    ) -> None:
        """Initialize the image entity."""
        vce: VisonicConfigData = entry.runtime_data
        CoordinatorEntity.__init__(self, coordinator=vce.coordinator)  # type: ignore[arg-type]
        ImageEntity.__init__(self, hass)
        self._sensor_id = sensor_id
        self._panel_id = vce.panel_id
        self._attr_unique_id = slugify(identifier + "_sensor_image")
        self._attr_name = "Image"
        self._attr_should_poll = False
        self._attr_translation_key = VISONIC_TRANSLATION_KEY

        self._panel = vce.panel_id
        self._attr_image_last_updated = None
        self._attr_image_last_retrieved = None
        self._cached_image = None
        # self._attr_image_url = None
        self._attr_content_type = "image/gif"
        self._image_data = None
        self._image_data_time = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
        )

        self._attr_available = False
        self._has_image: bool = False
        self._attr_state = STATE_IDLE  # STATE_IDLE
        self._image_lock = asyncio.Lock()

    def _get_sensor(self) -> SensorState | None:
        """Get the sensor dictionary associated with this entity."""
        if not self.coordinator or not self.coordinator.data:
            return None
        vcd: VisonicCoordinatorData = self.coordinator.data
        return vcd.zones.get(self._sensor_id)  # Could be None if not present

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Update the current value based on the device state
        if (sensor := self._get_sensor()) is None:
            return

        self._attr_available = sensor.enrolled
        self._has_image = sensor.has_image

        if self._has_image:
            image_time = sensor.image_time
            if image_time != self._attr_image_last_updated:
                self._attr_image_last_updated = image_time
                self._attr_state: StateType = STATE_OK
                self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return the state attributes of the device."""
        if self._get_sensor() is None:
            return None
        attr: Mapping[str, Any] = {}
        attr[PANEL_ATTRIBUTE_NAME] = self._panel_id
        attr[DEVICE_ATTRIBUTE_NAME] = self._sensor_id
        return attr

    async def async_image(self) -> bytes | None:
        """Return bytes of image on-demand."""
        if (sensor := self._get_sensor()) is None:
            return None
        if not self._has_image:
            return None

        image_time = sensor.image_time

        async with self._image_lock:
            if image_time != self._image_data_time or self._image_data is None:
                self._image_data_time = image_time
                # Fetch from the client; use async if possible
                self._image_data: bytearray | None = (
                    await self.coordinator.get_cached_image(self._sensor_id)
                )
        return self._image_data
