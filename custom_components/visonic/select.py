"""Support for Visonic Sensors Armed Select with safe pending-state handling."""

import asyncio
from collections.abc import Mapping
from datetime import datetime
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import CALLBACK_TYPE, HassJob, async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import Any, slugify

from .const import (
    DEVICE_ATTRIBUTE_NAME,
    DOMAIN,
    MANUFACTURER,
    PANEL_ATTRIBUTE_NAME,
    TRANSLATE_EXCEPTION_INVALID_ARM_STATE_NO_OPTION,
    TRANSLATE_EXCEPTION_NO_PANEL_CONNECTION,
    VISONIC_TRANSLATION_KEY,
)
from .coordinator_base import VisonicCoordinator
from .visonic_entity_types import SensorState, ZoneSensorData
from .visonic_types import AlarmCommandStatus, VisonicConfigData, VisonicCoordinatorData

_LOGGER = logging.getLogger(__name__)

BYPASS = "bypass"
ARMED = "armed"
PENDING_TIMEOUT_SECONDS = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Visonic Alarm Select entities for the given config entry."""

    @callback
    def async_add_select(sensor_data: ZoneSensorData) -> None:
        """Add a Visonic Select entity to Home Assistant."""
        async_add_entities([VisonicSelect(entry=entry, sensor_id=sensor_data.device_id, identifier=sensor_data.identifier)])

    vce: VisonicConfigData = entry.runtime_data
    vce.dispatchers[Platform.SELECT] = async_dispatcher_connect(
        hass, f"{DOMAIN}_{entry.entry_id}_add_{Platform.SELECT}", async_add_select
    )


class VisonicSelect(CoordinatorEntity[VisonicCoordinator], SelectEntity):
    """Representation of a Visonic arm/bypass select entity.

    Handles pending state safely, with automatic timeout and async lock to prevent race conditions.
    """

    def __init__(self, entry: ConfigEntry, sensor_id: int, identifier: str) -> None:
        """Initialize the select entity with panel info, pending-state tracking, and async lock."""
        vce: VisonicConfigData = entry.runtime_data
        super().__init__(vce.coordinator)

        self._entry = entry
        self._sensor_id = sensor_id
        self._panel_id = vce.panel_id
        self._attr_name = "Arm Mode"
        self._attr_should_poll = False
        self._attr_translation_key = VISONIC_TRANSLATION_KEY
        self._attr_unique_id = slugify(identifier + "_select")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
        )
        self._attr_available = False
        self._is_armed = False
        self._pending_state_is_armed = None
        self._pending_lock = asyncio.Lock()
        self._pending_task: CALLBACK_TYPE | None = None
        #_LOGGER.debug("[__init__] Creating Select, identifier: %s", str(identifier))

    def _cancel_pending_timer(self):
        if self._pending_task is not None:
            self._pending_task()
            self._pending_task = None

    def _get_sensor(self) -> SensorState | None:
        """Return the sensor dictionary from the coordinator data."""
        if not self.coordinator or not self.coordinator.data:
            return None

        cd: VisonicCoordinatorData = self.coordinator.data
        return cd.zones.get(self._sensor_id, None)
        # return self.coordinator.data.get("zones", {}).get(self._sensor_id, )

    async def async_will_remove_from_hass(self):
        """Clean up any pending task when the entity is removed from Home Assistant."""
        # Cancel the timer task if it exists
        self._cancel_pending_timer()
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update entity state when the coordinator has new data."""
        sensor: SensorState | None = self._get_sensor()
        if sensor is None:
            return

        self._attr_available = sensor.enrolled
        self._is_armed = not sensor.bypass

        if (
            self._pending_state_is_armed is not None
            and self._pending_state_is_armed == self._is_armed
        ):
            _LOGGER.info(
                "[_handle_coordinator_update] sensor %s implemented in panel, _is_armed=%s",
                self._sensor_id,
                self._is_armed,
            )
            self._pending_state_is_armed = None
            self._cancel_pending_timer()

        self.async_write_ha_state()

    @property
    def options(self) -> list[str]:
        """Return the selectable options for this entity."""
        return [BYPASS, ARMED]

    @property
    def current_option(self) -> str | None:
        """Return the current selected option based on armed state."""
        return ARMED if self._is_armed else BYPASS

    @property
    def icon(self) -> str | None:
        """Return the icon depending on armed/pending state."""
        if self._pending_state_is_armed is not None:
            return "mdi:alarm-snooze"
        if self._is_armed:
            return "mdi:alarm"
        return "mdi:alarm-off"

    def _update_sensor_state(self):
        self._pending_state_is_armed = None
        sensor: SensorState = self._get_sensor()
        if sensor:
            self._is_armed = not sensor.bypass
            self._attr_available = sensor.enrolled
        self.async_write_ha_state()

    async def _clear_pending_state(self, now: datetime | None = None) -> None:
        """Automatically clear the pending state after a timeout."""
        async with self._pending_lock:
            # Only clear if still pending
            if self._pending_state_is_armed is None:
                return
            self._update_sensor_state()

    async def async_select_option(self, option: str) -> None:
        """Send a bypass/armed command to the panel.

        Ensures only one command is pending at a time.
        Clears any old pending state automatically before sending a new command.
        """
        async with self._pending_lock:
            # Clear any old pending state before processing a new request
            if self._pending_task is not None:
                self._cancel_pending_timer()
                self._update_sensor_state()

            connected = self.coordinator.data.connected

            if (
                self.entity_id is None or not connected
            ):  # pyright: ignore[reportUnnecessaryComparison]
                self.coordinator.log.logstate_error(
                    "Attempt to command the panel without a panel connection."
                )
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key=TRANSLATE_EXCEPTION_NO_PANEL_CONNECTION,
                    translation_placeholders={"myname": self._name},
                )

            if option not in self.options:
                self.coordinator.log.logstate_error("Invalid Select Arm State Option.")
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key=TRANSLATE_EXCEPTION_INVALID_ARM_STATE_NO_OPTION,
                    translation_placeholders={"options": str(self.options)},
                )

            sensor: SensorState | None = self._get_sensor()
            if sensor is None:
                return

            # Send the command to the panel
            result = await self.coordinator.send_bypass(
                self._sensor_id, option == BYPASS, ""
            )

            if result.status == AlarmCommandStatus.SUCCESS:
                self._pending_state_is_armed = option == ARMED

                self._pending_task = async_call_later(
                    self.hass,
                    PENDING_TIMEOUT_SECONDS,
                    HassJob(self._clear_pending_state, cancel_on_shutdown=True),
                )
                self.async_write_ha_state()
            else:
                self.coordinator.log.logstate_warning(
                    "[select_option] Command %s not sent to panel %s", option, result
                )

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return additional state attributes for the entity."""
        sensor: SensorState | None = self._get_sensor()
        if sensor is not None:
            return {
                PANEL_ATTRIBUTE_NAME: self._panel_id,
                DEVICE_ATTRIBUTE_NAME: self._sensor_id,
            }
        return None
