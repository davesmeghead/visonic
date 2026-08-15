"""Binary Sensors for the connection to a Visonic PowerMax or PowerMaster Alarm System.

A Data Driven Binary Sensor Class for Visonic Sensors
"""
import asyncio
import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, MANUFACTURER, VISONIC_TRANSLATION_KEY
from .coordinator_base import VisonicCoordinator
from .sensor_base_logic import VisonicBaseEntity
from .utils import kill_asyncio_task
from .visonic_entity_types import (
    BINARY_SENSOR_DEFINITIONS,
    STYPE_TO_HA_SENSOR_MAP,
    BinaryImageDownloadData,
    BinarySensorData,
    BinarySensorDefinition,
    DeviceState,
    PanelState,
    SensorOnTimeout,
    SensorState,
    VisonicBinarySensorKey,
)
from .visonic_types import VisonicConfigData

_LOGGER = logging.getLogger(__name__)

SensorData = BinaryImageDownloadData | BinarySensorData

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Visonic Alarm Binary Sensors."""
    #_LOGGER.debug("[async_setup_entry] start")

    @callback
    def async_add_binary_sensor(sensor_data: SensorData | list[SensorData]) -> None:
        """Add Visonic Binary Sensor."""

        if isinstance(sensor_data, SensorData):
            sensor_data = [sensor_data] # make it a list of 1

        entities: list[VisonicBinaryEntity | VisonicImageDownloadBinarySensor] = []

        for sensor in sensor_data:
            if isinstance(sensor, BinarySensorData):
                vbs = VisonicBinaryEntity(entry, sensor.device_id, sensor.identifier, sensor.initial_state, sensor.sensor_definition, sensor.timeout_type)
                entities.append(vbs)
            elif isinstance(sensor, BinaryImageDownloadData):
                vbs = VisonicImageDownloadBinarySensor(entry, sensor.identifier)
                entities.append(vbs)

        if len(entities) > 0:
            async_add_entities(entities)

    vce: VisonicConfigData = entry.runtime_data
    vce.dispatchers[Platform.BINARY_SENSOR] = async_dispatcher_connect( hass, f"{DOMAIN}_{entry.entry_id}_add_{Platform.BINARY_SENSOR}", async_add_binary_sensor )


class VisonicImageDownloadBinarySensor(CoordinatorEntity[VisonicCoordinator], BinarySensorEntity):
    """Panel-level indicator: on while the panel is downloading a PIR camera image sequence.

    Reflects platform_manager.image_download_active(); resets to off on (re)connect (the coordinator
    clears the download state then). While on, image request presses are queued rather than sent.
    """

    _attr_has_entity_name = True
    _attr_name = "Image download active"
    _attr_icon = "mdi:camera-timer"

    def __init__(self, entry: ConfigEntry, identifier: str) -> None:
        """Initialize the panel image-download indicator."""
        vce: VisonicConfigData = entry.runtime_data
        super().__init__(vce.coordinator)
        self._attr_available = True
        self._attr_translation_key = VISONIC_TRANSLATION_KEY
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
        )
        self._attr_unique_id = slugify(f"{identifier}_image_download_active")

    @property
    def is_on(self) -> bool:
        """Return True while a panel image download/retransmit is in progress."""
        #self._attr_available = self.coordinator.is_power_master()
        return self.coordinator.image_download_active()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which camera the panel is sending, and how many requests are waiting behind it."""
        #self._attr_available = self.coordinator.is_power_master()
        return self.coordinator.image_download_data()


class VisonicBinaryEntity(VisonicBaseEntity, BinarySensorEntity):
    """Binary sensor entity."""

    entity_description: BinarySensorDefinition

    # Explain the algorithm, there are 3 ways to use this class:
    #  State based sensors - Simple sensors such as Battery, Tamper, Trouble:
    #      Input: The returned self.definition.value_fn is a boolean
    #             Uses various paramaters from the SensorState, SwitchState, PanelState classes
    #      There is no timer, the output directly reflects the input.
    #  State based sensors - Magnet/Wired:
    #      Input: The returned self.definition.value_fn is a boolean
    #             Uses "status" from the SensorState class
    #      This uses the STATE timeout configuration setting
    #      The timer is only initiated on False to True transitions, to hold the value True for the timeout period
    #          ignoring any transitions to False in that timeout period (including toggling changes between True/False)
    #      At the end of the timeout period, the output is set to the "live" latest value (True or False), so it could remain True
    #          The timer is then initiated on the next False to True transition
    #  Trigger based sensors - Motion/Camera/Gas/Shock etc:
    #      There are 2 timeout values that can be used: MOTION and OTHER
    #      Input: The returned self.definition.value_fn is not a boolean, currently it is an integer that changes on "trigger"
    #             Uses "trigger" from the SensorState class
    #                 (the current trigger implementation is a counter 0 to 99 that rolls over, but we cannot rely on this in this class)
    #      A change in value indicates a trigger i.e. to change the output from False to True
    #      The timer is only initiated on False to True transitions, to hold the output True for the timeout period
    #          ignoring any input value changes i.e. triggers, in that timeout period
    #      At the end of the timeout period, the output state is set to False, waiting for the next input change in value
    #          At the end of the timeout period the current input value is saved, waiting again for a change in value.

    def __init__(self, entry: ConfigEntry, sensor_id: int, identifier:str, initial_state: bool, definition: VisonicBinarySensorKey, timeout_type: SensorOnTimeout) -> None:
        """Initialize the sensor."""
        self.entity_description = BINARY_SENSOR_DEFINITIONS[definition]
        super().__init__(entry, sensor_id, identifier, initial_state, self.entity_description)
        self._attr_available = False
        self.initial_state = initial_state
        self.timeout_type = timeout_type
        self.timerTask = None
        self._reset_state()

    # Called when an entity is about to be removed from Home Assistant. Example use: disconnect from the server or unsubscribe from updates.
    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        if self.timerTask is not None:
            await kill_asyncio_task(self.timerTask)
        await super().async_will_remove_from_hass()

    def _reset_state(self):
        # reset
        if self.timerTask is not None:
            # Just cancel it, no need to wait
            self.timerTask.cancel()
        self.timerTask = None
        if self.current_value != self.initial_state:
            _LOGGER.debug("[binary sensor] reset data to %s", self.initial_state)
        self.current_value = self.initial_state
        self.save_state = self.initial_state

    def _set_current_data(self, state: SensorState | DeviceState | PanelState | None, force_non_bool_to_false: bool = False):
        # Create the current_value, with optional timer
        #   Get the data value and process it through the value function
        data_raw = getattr(state, self.definition.data_key.value, None)
        data = self.definition.value_fn(data_raw)
        # data may be a bool, int or float
        # If bool then use directly
        # If not bool then use a change of value
        from_value = self.current_value
        if data is None:
            self._reset_state()
#            self.current_value = None
        elif isinstance(data, bool):
            # Used for "state" to set the current_value from a bool
            self.current_value = data
        elif force_non_bool_to_false:
            # Used for post "trigger" to set values ready for next time
            self.current_value = False  # set the output to False
            self.save_state = data      # save the current input value
        else:
            # Used for "trigger"
            # Set the current_value from a float/int, has the data changed
            changed : bool = False if self.save_state is None else self.save_state != data
            self.current_value = changed
            self.save_state = data
        if from_value != self.current_value:
            _LOGGER.debug("[binary sensor] %s set data from %s to %s", self.unique_id, from_value, self.current_value)

    def update_local(self, state: SensorState | DeviceState | PanelState | None):
        """Set settings for this class i.e. binary entity."""
        if self.entity_description.device_class is None:
            self._attr_device_class = STYPE_TO_HA_SENSOR_MAP[state.sensor_type.type]

        if self.timerTask is None:
            # The timer is not currently running, if the timer is already running then ignore any updates
            old_value = self.current_value
            # Update data
            self._set_current_data(state=state)
            if old_value is not True and self.current_value and self.timeout_type != SensorOnTimeout.NO_TIMEOUT:
                # Transition from False/None to True, determine if a timeout is required
                timeout = -1.0 # No timeout
                match self.timeout_type:
                    case SensorOnTimeout.MOTION:
                        timeout = max(float(self.motion_timeout), 1.0) # at least 1 second for trigger based timeouts
                    case SensorOnTimeout.STATE:
                        timeout = max(float(self.magnet_timeout), 0.0) # For state based the timeout can be zero i.e. no timeout set
                    case SensorOnTimeout.OTHER:
                        timeout = max(float(self.other_timeout), 1.0) # at least 1 second for trigger based timeouts
                if timeout >= 0.01:
                    # kick off timer
                    self.timerTask = self._entry.async_create_task(self.hass, self._retain_state_task(timeout), name=f"sensor_{self.sensor_id}_timeout_task")

    async def _retain_state_task(self, timeout: float):
        _LOGGER.debug(f"[binary sensor] in   id = {self.unique_id}   timeout = {timeout}    dc={self.device_class}")  # noqa: G004
        await asyncio.sleep(timeout)
        state: SensorState | None = self._get_data()
        if state is None:
            self._reset_state()
        else:
            # Set the very latest data value (after the timeout)
            # State based then get latest value
            # Trigger based, then force to False, waiting for next trigger
            self._set_current_data(state=state, force_non_bool_to_false=True)
            _LOGGER.debug(f"[binary sensor] out  id = {self.unique_id}   timeout = {timeout}    current = {self.current_value}")  # noqa: G004
        self._attr_available = self.coordinator.is_connected()
        self.timerTask = None
        self.async_schedule_update_ha_state(True)

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return self.current_value
