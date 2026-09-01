"""Visonic Sensor integration for PowerMax/PowerMaster alarm system.

This module exposes 2 sensors:
VisonicAlarmSensor: a read-only sensor entity that reports alarm status only.
        It does NOT support arming, disarming, or other interactions with the panel.
VisonicFloatEntity: A generic floating point entity.
"""

#import logging

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alarm_base_logic import AlarmBaseLogic
from .const import DOMAIN, VISONIC_TRANSLATION_KEY
from .sensor_base_logic import VisonicBaseEntity
from .visonic_data_types import VisonicPanelData
from .visonic_entity_types import (
    FLOAT_SENSOR_DEFINITIONS,
    AlarmPanelData,
    FloatSensorData,
    FloatSensorDefinition,
    VisonicFloatSensorKey,
)

_LOGGER = logging.getLogger(__name__)

SensorData = AlarmPanelData | FloatSensorData

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Visonic sensor entities for a config entry."""

    @callback
    def async_add_sensor(
        data: SensorData | list[SensorData],
    ) -> None:
        """Create and add Visonic sensor entities (read-only)."""
        entities: list[VisonicAlarmSensor | VisonicFloatEntity] = []
        data_list = data if isinstance(data, list) else [data]

        vcd: VisonicPanelData = entry.runtime_data
        if vcd:
            for item in data_list:
                match item:
                    case FloatSensorData():
                        vbs = VisonicFloatEntity(entry, item.device_id, item.identifier, item.initial_state, item.sensor_definition)
                        entities.append(vbs)
                    case AlarmPanelData():
                        entities.extend(
                            e
                            for e in vcd.coordinator.alarm_and_sensor_common_setup(
                                entry=entry,
                                alarm=False,
                                ref=0,
                                piu=item.partitions,
                                identifier=item.identifier
                            )
                            if isinstance(e, VisonicAlarmSensor)
                        )
            if len(entities) > 0:
                async_add_entities(entities, True)

    # Register dispatcher so new partitions can be added dynamically
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{DOMAIN}_{entry.entry_id}_add_{Platform.SENSOR}", async_add_sensor
        )
    )


class VisonicAlarmSensor(AlarmBaseLogic, SensorEntity):
    """A Sensor that mimics an Alarm Panel for Minimal operation."""
    def __init__(self, entry: ConfigEntry, partition: int | None, identifier: str):
        """Initialise and pass on the mro."""
        super().__init__(entry=entry, partition=partition, identifier=identifier)

    # Implement the abstract class
    def update_local(self, entry: ConfigEntry):
        """Update the sensor's state from coordinator data."""
        self._attr_available = self.panel_state_data.connected #  and self.panel_state_data.panel_state not in [AlarmPanelStatus.UNKNOWN, AlarmPanelStatus.USER_TEST, AlarmPanelStatus.DOWNLOADING]
        self._attr_state = self.panel_state_data.alarm_state
        if self.panel_state_data.connected:
            self._attr_extra_state_attributes = self.panel_state_data.attributes
            self._attr_changed_by = self.panel_state_data.last_event_name


class VisonicFloatEntity(VisonicBaseEntity, SensorEntity):
    """Float sensor entity."""

    entity_description: FloatSensorDefinition

    def __init__(self, entry: ConfigEntry, sensor_id: int, identifier:str, initial_state: bool, definition: VisonicFloatSensorKey) -> None:
        """Initialize the sensor."""
        self.entity_description = FLOAT_SENSOR_DEFINITIONS[definition]
        if self.entity_description.translation_key is None:
            self._attr_translation_key = VISONIC_TRANSLATION_KEY
        super().__init__(entry, sensor_id, identifier, initial_state, self.entity_description)

    @property
    def native_value(self) -> float:
        """Return the state of the sensor."""
        return self.current_value
