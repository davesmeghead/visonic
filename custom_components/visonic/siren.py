"""Visonic Siren entity for Home Assistant - reports status only."""

from collections.abc import Mapping
from typing import Any

from homeassistant.components.alarm_control_panel.const import AlarmControlPanelState
from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    CONF_SIREN_SOUNDING,
    DOMAIN,
    MANUFACTURER,
    PANEL_ATTRIBUTE_NAME,
    PARTITION_ID_WHEN_BASE,
    VISONIC_TRANSLATION_KEY,
)
from .coordinator_base import VisonicCoordinator
from .exceptions import VisonicException
from .utils import capitalize, create_sensor_unique_id
from .visonic_entity_types import AlarmPanelData
from .visonic_types import PanelStateData, TriggerAlarmType, VisonicConfigData

PARALLEL_UPDATES = 1
SUPPORT_FLAGS = SirenEntityFeature.TURN_OFF | SirenEntityFeature.TURN_ON


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Visonic siren entity."""

    @callback
    def async_add_siren(siren_data: AlarmPanelData) -> None:
        """Add Visonic Siren entity."""
        async_add_entities([VisonicSiren(entry=entry, siren_id=siren_data.siren_id, name=siren_data.siren_name, identifier=siren_data.identifier)])

    vce: VisonicConfigData = entry.runtime_data
    vce.dispatchers[Platform.SIREN] = async_dispatcher_connect(
        hass, f"{DOMAIN}_{entry.entry_id}_add_{Platform.SIREN}", async_add_siren
    )


class VisonicSiren(CoordinatorEntity[VisonicCoordinator], SirenEntity):
    """Representation of a Visonic siren device."""

    def __init__(self, entry: ConfigEntry, siren_id: int, name: str, identifier: str) -> None:
        """Initialize the siren entity."""
        vce: VisonicConfigData = entry.runtime_data
        vc: VisonicCoordinator = vce.coordinator
        if vc is None:
            raise VisonicException("Alarm has been given invalid coordinator", 101)
        super().__init__(vce.coordinator)
        self.siren_id = siren_id
        self._name = "Siren"
        self._panel_id = vce.panel_id
        self._entry = entry
        self._mystate = False
        self.external = False
        self.trigger = ""
        self.alarmReason = ""
        self.set_siren_sounding_filter(entry)
        self._attr_supported_features = SUPPORT_FLAGS
        self._attr_available_tones = None
        self._attr_name = "Siren"
        self._attr_should_poll = False
        self._attr_translation_key = VISONIC_TRANSLATION_KEY
        self._attr_unique_id = slugify(name + "_" + self._name)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
        )
        self.update()

    def set_siren_sounding_filter(self, entry: ConfigEntry):
        """Convert a list of strings in to the AlarmType enumeration."""
        # The siren sounding options are stored as a list of strings
        strings = entry.options.get(CONF_SIREN_SOUNDING, [])
        if strings is None:
            self.siren_sounding_list = []
        else:
            self.siren_sounding_list = [
                m
                for s in strings
                if isinstance(s, str) and (m := TriggerAlarmType.from_name(s)) is not None
            ]

    async def _handle_entry_update(
        self, _hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Handle updates from options."""
        self._entry = entry
        self.set_siren_sounding_filter(entry)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register update listener on entity added."""
        await super().async_added_to_hass()
        self.async_on_remove(self._entry.add_update_listener(self._handle_entry_update))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update entity state from coordinator."""
        self.update()
        self.async_write_ha_state()

    def update(self):
        """Fetch the siren state from the coordinator."""
        oldstate = self._mystate
        self._mystate = False
        self.alarmReason = ""

#        if not self.coordinator or not hasattr(self.coordinator, "get_panel_and_partition_state"):
#            self.coordinator.log.logstate_warning(
#                "Coordinator not ready for siren %s", self._name
#            )
#            return
        state: PanelStateData = self.coordinator.get_panel_and_partition_state(PARTITION_ID_WHEN_BASE)

        # Update siren state
        dev = -1
        if state.alarm_state == AlarmControlPanelState.TRIGGERED:
            dev, alarm_type = state.trigger_device
            if alarm_type in self.siren_sounding_list:
                self._mystate = True
                self.alarmReason = capitalize(alarm_type.name)
                self.coordinator.log.logstate_info("Siren Active (%s)", self.alarmReason)
            else:
                dev = -1

        if not self._mystate:
            self.trigger = ""
        elif dev is not None and dev >= 0 and not oldstate:
            self.trigger = create_sensor_unique_id(self._panel_id, dev+1)

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the siren on (external)."""
        self.external = True
        self.schedule_update_ha_state(True)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the siren off (external)."""
        self.external = False
        self.schedule_update_ha_state(True)

    @property
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return True if the siren is available."""
        return bool(
            self.coordinator
            and self.coordinator.data
            and self.coordinator.data.connected
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if siren is on."""
        return self._mystate or self.external

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return only minimal attributes for Home Assistant."""
        return {
            "alarm": (
                "external"
                if self.external
                else (self.alarmReason if self._mystate else "none")
            ),
            "trigger": self.trigger,
            PANEL_ATTRIBUTE_NAME: self._panel_id,
        }
