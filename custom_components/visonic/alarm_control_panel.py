"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System (Alarm Panel Control)."""

import logging

from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    CodeFormat,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alarm_base_logic import AlarmBaseLogic
from .const import DOMAIN, TEXT_CLIENT_VERSION, TEXT_DISCONNECTION_COUNT
from .visonic_entity_types import AlarmPanelData
from .visonic_types import (
    AlarmPanelCommand,
    AlarmPanelStatus,
    VisonicConfigData,
    VisonicCoordinatorData,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm control panel."""

    @callback
    def async_add_alarm(alarm_data: AlarmPanelData) -> None:
        """Add Visonic Alarm Panel."""
        # Call the classmethod to create the Entities
        entities: list[VisonicAlarm] = [
            e
            for e in AlarmBaseLogic.alarm_and_sensor_common_setup(
                entry=entry, alarm=True, piu=alarm_data.partitions, identifier=alarm_data.identifier
            )
            if isinstance(e, VisonicAlarm)  # They should be but this makes certain
        ]
        if entities:
            async_add_entities(entities, True)

    vce: VisonicConfigData = entry.runtime_data
    vce.dispatchers[Platform.ALARM_CONTROL_PANEL] = async_dispatcher_connect(
        hass, f"{DOMAIN}_{entry.entry_id}_add_{Platform.ALARM_CONTROL_PANEL}", async_add_alarm
    )


class VisonicAlarm(
    AlarmBaseLogic, AlarmControlPanelEntity
):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Representation of a Visonic alarm control panel."""

    # This class only contains specific functions for the alarm entity, common alarm/sensor functions are in AlarmBaseLogic
    #    The method resolution order is important here, the init in AlarmBaseLogic will overwrite variable set in the init of AlarmControlPanelEntity
    #       i.e. the __init__ of AlarmControlPanelEntity will run first and then the __init__ of AlarmBaseLogic, updating settings as needed
    # I also override available but it is in both Entity and Coordinator parent class with different @property / @cached_property so gives a pyright error

    _unrecorded_attributes = frozenset(
        {
            TEXT_DISCONNECTION_COUNT,
            TEXT_CLIENT_VERSION,
        }
    )

    def __init__(self, entry: ConfigEntry, partition: int | None, identifier: str):
        """Initialise and pass on the mro."""
        super().__init__(entry=entry, partition=partition, identifier=identifier)

    # Respond to the user commands

    def log_state(self, message: str):
        """Log state."""
        self.coordinator.log.logstate_info(
            "[alarm control panel] partition %s, %s %s",
            self._partition_set,
            message,
            self.entity_id,
        )

    async def async_alarm_disarm(self, code: str | None = None):
        """Send disarm command."""
        self.log_state("disarm")
        await self.coordinator.send_command(
            self._name, AlarmPanelCommand.DISARM, code, self._partition_set
        )

    async def async_alarm_arm_night(self, code: str | None = None):
        """Send arm night command (Same as arm home)."""
        self.log_state("arm night")
        await self.coordinator.send_command(
            self._name, AlarmPanelCommand.ARM_HOME_INSTANT, code, self._partition_set
        )

    async def async_alarm_arm_home(self, code: str | None = None):
        """Send arm home command."""
        command = (
            AlarmPanelCommand.ARM_HOME_INSTANT
            if self.isarmhomeinstant
            else AlarmPanelCommand.ARM_HOME
        )
        self.log_state(command.name.lower())
        await self.coordinator.send_command(
            self._name, command, code, self._partition_set
        )

    async def async_alarm_arm_away(self, code: str | None = None):
        """Send arm away command."""
        command = (
            AlarmPanelCommand.ARM_AWAY_INSTANT
            if self.isarmawayinstant
            else AlarmPanelCommand.ARM_AWAY
        )
        self.log_state(command.name.lower())
        await self.coordinator.send_command(
            self._name, command, code, self._partition_set
        )

    async def async_alarm_trigger(self, code: str | None = None):
        """Send alarm trigger command."""
        self.log_state("trigger")
        data: VisonicCoordinatorData = self.coordinator.data
        if data.ispowermaster:
            await self.coordinator.send_command(
                self._name, AlarmPanelCommand.TRIGGER, code, self._partition_set
            )

    async def async_alarm_arm_vacation(self, code: str | None = None):
        """Send arm vacation command."""
        _LOGGER.debug("Alarm Panel Vacation Mode Not Yet Implemented")

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        """Bypass Panel."""
        _LOGGER.debug("Alarm Panel Custom Bypass Not Yet Implemented")

    # Implement the abstract class
    #   Update local variables accordingly, self.async_write_ha_state() is called outside
    def update_local(self, entry: ConfigEntry):
        """Update Panel Settings for Alarms (called automatically by base logic)."""

        # Update HA attributes from PanelStateData
        self._attr_extra_state_attributes = self.panel_state_data.attributes
        self._attr_alarm_state = self.panel_state_data.alarm_state
        self._attr_available = self.panel_state_data.connected and self.panel_state_data.panel_state not in [AlarmPanelStatus.UNKNOWN, AlarmPanelStatus.USER_TEST, AlarmPanelStatus.DOWNLOADING]
        #_LOGGER.info(f"[alarm control panel update]  _attr_available {self._attr_available}    _attr_alarm_state {self._attr_alarm_state}")  # noqa: G004

        if self.panel_state_data.connected and not self.disable_all_panel_commands:
            self._attr_code_arm_required = self.panel_state_data.code_arm_required
            self._attr_code_format = CodeFormat.NUMBER if self.panel_state_data.show_keypad else None

            #sf = AlarmControlPanelEntityFeature(0)
            sf = AlarmControlPanelEntityFeature.ARM_AWAY        # add this unconditionally
            sf |= AlarmControlPanelEntityFeature.ARM_HOME if self.isarmhome else 0
            sf |= AlarmControlPanelEntityFeature.ARM_NIGHT if self.isarmnight else 0
            sf |= AlarmControlPanelEntityFeature.TRIGGER if self.panel_state_data.is_power_master else 0
            self._attr_supported_features = sf

            if (
                self.panel_state_data.last_event_name is not None
                and self._attr_changed_by != self.panel_state_data.last_event_name
            ):
                self._attr_changed_by = self.panel_state_data.last_event_name

            #_LOGGER.info(f"[alarm control panel update]  code format {self._attr_code_format}")  # noqa: G004
            #_LOGGER.info(f"[alarm control panel update]  code arm reqd {self._attr_code_arm_required}")  # noqa: G004

        else:
            self._attr_supported_features = AlarmControlPanelEntityFeature(0)
            self._attr_code_format = CodeFormat.NUMBER

    # The following 4 functions are copied from the alarm control panel to change cached_property to property

    @property
    def code_format(self) -> CodeFormat | None:
        """Code format or None if no code is required."""
        #_LOGGER.info(f"[alarm control panel func]  code format {self._attr_code_format}")  # noqa: G004
        return self._attr_code_format

    @property
    def changed_by(self) -> str | None:
        """Last change triggered by."""
        return self._attr_changed_by

    @property
    def code_arm_required(self) -> bool:
        """Whether the code is required for arm actions."""
        #_LOGGER.info(f"[alarm control panel func]  code arm reqd {self._attr_code_arm_required}")  # noqa: G004
        return self._attr_code_arm_required

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        """Return the list of supported features."""
        return self._attr_supported_features
