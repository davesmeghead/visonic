"""Support for requesting a Visonic PIR Camera image."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, MANUFACTURER
from .coordinator_base import VisonicCoordinator
from .visonic_entity_types import ZoneSensorData
from .visonic_types import VisonicConfigData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Visonic image request button for Camera PIRs."""

    @callback
    def async_add_button(sensor_data: ZoneSensorData) -> None:
        """Add Visonic image request button."""
        async_add_entities(
            [VisonicImageRequestButton(entry=entry, sensor_id=sensor_data.device_id, identifier=sensor_data.identifier)]
        )

    vce: VisonicConfigData = entry.runtime_data
    vce.dispatchers[Platform.BUTTON] = async_dispatcher_connect(
        hass, f"{DOMAIN}_{entry.entry_id}_add_{Platform.BUTTON}", async_add_button
    )


class VisonicImageRequestButton(CoordinatorEntity[VisonicCoordinator], ButtonEntity):
    """A button to request an image from a camera PIR sensor."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:camera-iris"

    def __init__(self, entry: ConfigEntry, sensor_id: int, identifier: str) -> None:
        """Initialize the button entity."""
        vce: VisonicConfigData = entry.runtime_data
        CoordinatorEntity.__init__(self, coordinator=vce.coordinator)  # type: ignore[arg-type]
        self._sensor_id = sensor_id
        self._attr_unique_id = slugify(identifier + "_request_image")
        self._attr_name = "Request image"
        self._attr_should_poll = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
        )

    async def async_press(self) -> None:
        """Ask the panel to send an image for this camera sensor."""
        await self.coordinator.send_get_sensor_image(self._sensor_id, self.entity_id)
