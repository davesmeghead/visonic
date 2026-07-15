"""Switches for the connection to a Visonic PowerMax or PowerMaster Alarm System."""

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, cached_property
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    DEVICE_ATTRIBUTE_NAME,
    DOMAIN,
    MANUFACTURER,
    PANEL_ATTRIBUTE_NAME,
    TRANSLATE_EXCEPTION_NO_PANEL_CONNECTION,
    VISONIC_TRANSLATION_KEY,
)
from .coordinator_base import VisonicCoordinator
from .utils import create_switch_unique_id
from .visonic_entity_types import SwitchState, ZoneSensorData
from .visonic_types import AlarmSwitchCommand, VisonicConfigData, VisonicCoordinatorData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Visonic Switch."""

    @callback
    def async_add_switch(switch_data: ZoneSensorData) -> None:
        """Add Visonic Switch."""
        async_add_entities([VisonicSwitch(entry=entry, switch_id=switch_data.device_id, identifier=switch_data.identifier)])

    vce: VisonicConfigData = entry.runtime_data
    vce.dispatchers[Platform.SWITCH] = async_dispatcher_connect(
        hass, f"{DOMAIN}_{entry.entry_id}_add_{Platform.SWITCH}", async_add_switch
    )


class VisonicSwitch(CoordinatorEntity[VisonicCoordinator], SwitchEntity):
    """Representation of a Visonic Switch."""

    def __init__(self, entry: ConfigEntry, switch_id: str, identifier:str) -> None:
        """Initialise a Visonic Device."""
        vce: VisonicConfigData = entry.runtime_data
        super().__init__(vce.coordinator)
        self.switch_id = switch_id
        self._panel_id = vce.panel_id
        self._attr_unique_id = slugify(identifier + "_switch")
        self._attr_name = None
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_should_poll = False
        self._attr_translation_key = VISONIC_TRANSLATION_KEY
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
        )
        _LOGGER.debug(
            "[VisonicSwitch] Creating Switch, identifier : %s", str(identifier)
        )

        self._current_value: bool = False
        self._available: bool = False

    def getSwitch(self) -> SwitchState | None:
        """Get the sensor dictionary associated with this entity."""
        if not self.coordinator or not self.coordinator.data:
            return None
        vcd: VisonicCoordinatorData = self.coordinator.data
        return vcd.switch.get(self.switch_id)  # Could return None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Switch state has changed."""
        switch : SwitchState | None = self.getSwitch()
        if switch is None:
            return
        self._available = switch.enabled
        self._current_value = switch.status
        # if self.coordinator and self.coordinator.data.connected:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return True if entity is available."""
        return False if self.getSwitch() is None else self._available

    @cached_property
    def assumed_state(self):
        """Return False if unable to access real state of entity."""
        return False

    @property
    def is_on(self):
        """Return true if device is on."""
        return self._current_value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""
        await self.turnmeonandoff(AlarmSwitchCommand.ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        await self.turnmeonandoff(AlarmSwitchCommand.OFF)

    # "off"  "on"  "dimmer"  "brighten"
    async def turnmeonandoff(self, state: AlarmSwitchCommand):
        """Send disarm command."""
        if not self.coordinator or not self.coordinator.data:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=TRANSLATE_EXCEPTION_NO_PANEL_CONNECTION,
                translation_placeholders={"myname": "<******>"},
            )

        if not self.coordinator.data.connected:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=TRANSLATE_EXCEPTION_NO_PANEL_CONNECTION,
                translation_placeholders={"myname": self._attr_unique_id},
            )

        await self.coordinator.send_switch(self.switch_id, state)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return the state attributes of the device."""
        if (switch := self.getSwitch()) is None:
            return {
                "name": create_switch_unique_id(self._panel_id, self.switch_id),
                DEVICE_ATTRIBUTE_NAME: self.switch_id,
                PANEL_ATTRIBUTE_NAME: self._panel_id,
            }
        return {
            "name": create_switch_unique_id(self._panel_id, self.switch_id),
            DEVICE_ATTRIBUTE_NAME: self.switch_id,
            PANEL_ATTRIBUTE_NAME: self._panel_id,
            "location": switch.location,
            "type": switch.model,
        }
