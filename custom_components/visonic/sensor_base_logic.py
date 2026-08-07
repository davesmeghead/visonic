"""Visonic Sensor Base logic.

This class supports the VisonicFloatEntity sensor (int/float) and VisonicBinaryEntity binarysensor (bool) classes
"""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    CONF_EMER_OFF_DELAY,
    CONF_MAGNET_CLOSED_DELAY,
    CONF_MOTION_OFF_DELAY,
    DOMAIN,
    MANUFACTURER,
    PANEL_ATTRIBUTE_NAME,
    VISONIC_TRANSLATION_KEY,
)
from .coordinator_base import VisonicCoordinator
from .visonic_entity_types import (
    DeviceState,
    EntityDataType,
    PanelState,
    SensorState,
    VisonicSensorDefinition,
)
from .visonic_types import VisonicConfigData, VisonicCoordinatorData

_LOGGER = logging.getLogger(__name__)

###################################################################################
##############  Base class for data driven binary_sensor and sensor class #########
###################################################################################

class VisonicBaseEntity(CoordinatorEntity[VisonicCoordinator]):
    """Generic base entity to support binary and float entities."""

    def __init__(self, entry: ConfigEntry, sensor_id: int, identifier:str, initial_state: bool | float | None, definition: VisonicSensorDefinition) -> None:
        """Initialize the sensor."""
        #_LOGGER.debug("[VisonicBaseSensor]   In base sensor VisonicSensor initialisation")
        vce: VisonicConfigData = entry.runtime_data
        super().__init__(vce.coordinator)
        self.definition = definition
        self._entry = entry
        self._attr_available = False
        self._attr_should_poll = False
        self._attr_translation_key = VISONIC_TRANSLATION_KEY
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
        )
        self._attr_name = definition.friendly_name
        self._attr_unique_id = slugify(f"{identifier}_{definition.unique_extension}")

        self.sensor_id = sensor_id
        self._panel_id = vce.panel_id
        self.current_value = initial_state
        self.esa = None
        self._update_sensor_delays()

    def update_local(self, state: SensorState | DeviceState | PanelState | None):
        """Use simple update of self.current_value for this class. Override me for more."""
        data_raw = getattr(state, self.definition.data_key.value, None)
        self.current_value = self.definition.value_fn(data_raw)

    def _update_sensor_delays(self):
        # Trigger Off delays to apply for each sensor type
        self.motion_timeout = int(self._entry.options.get(CONF_MOTION_OFF_DELAY, 120))
        self.magnet_timeout = int(self._entry.options.get(CONF_MAGNET_CLOSED_DELAY, 120))
        self.other_timeout = int(self._entry.options.get(CONF_EMER_OFF_DELAY, 120))

    async def _handle_entry_update(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        # Re-read options
        self._entry = entry
        # Grab the timeouts here
        self._update_sensor_delays()
        if self._update():
            self.async_schedule_update_ha_state(True)

    async def async_added_to_hass(self) -> None:
        """Called when this entity has been added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(self._entry.add_update_listener(self._handle_entry_update))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Update the current value based on the device state
        if self._update():
            self.async_schedule_update_ha_state(True)

    def _get_data(self) -> SensorState | PanelState | DeviceState | None:
        """Get the dictionary associated with this entity."""
        if not self.coordinator or not self.coordinator.data:
            _LOGGER.debug("[VisonicBinaryEntity] Coordinator invalid")
            return None
        if not self.coordinator.data.connected:
            #_LOGGER.debug("[VisonicBinaryEntity] Not connected to panel %s", self.coordinator.data)
            return None
        vcd: VisonicCoordinatorData = self.coordinator.data
        match self.definition.source:
            case EntityDataType.PANEL:
                return vcd.panelstate
            case EntityDataType.ZONE:
                return vcd.zones.get(self.sensor_id)
            case EntityDataType.DEVICE:
                return vcd.device.get(self.sensor_id)
        # Allow an f-string here as this is an error
        _LOGGER.error(f"[VisonicBinaryEntity] Invalid description source {self.definition.source}")  # noqa: G004
        return None

    def _update(self) -> bool:
        if self.hass is None:
            return False
        state: SensorState | DeviceState | PanelState | None = self._get_data()
        if state is None:
            return False

        # Update child class data, including self.current_value
        self.update_local(state)

        # Create the extra_state_attributes, adding panel id
        self.esa = self.definition.attributes_fn(state)
        self.esa[PANEL_ATTRIBUTE_NAME] = self._panel_id

        self._attr_available = self.coordinator.is_connected()

        return True

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        return self.esa
