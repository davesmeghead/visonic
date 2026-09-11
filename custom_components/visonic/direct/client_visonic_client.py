"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""

# This child/parent class build up incorporates the interaction/interface to the low level pyvisonic library

from collections.abc import Callable
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from ..const import CLIENT_VERSION, CONF_ENABLE_SENSOR_BYPASS  # noqa: TID252
from ..log_events import logEvents  # noqa: TID252  # noqa: TID252
from ..platform_manager import PlatformManager  # noqa: TID252
from ..utils import to_bool  # noqa: TID252
from ..visonic_data_types import VisonicConfigEntry  # noqa: TID252
from ..visonic_types import (  # noqa: TID252  # noqa: TID252
    AlarmCommandStatus,  # AlCommandStatus  # AlCommandStatus
    AlarmPanelCommand,
    AlarmPanelStatus,
    AlarmSwitchCommand,  # AlSwitchCommand  # AlSwitchCommand
    AvailableNotifications,
    CommandResult,
)
from .client_manage_connection import ManageConnection
from .pyvisonic.py_abstract_classes import AlPanelInterface
from .pyvisonic.py_enum import AlCommandStatus, AlPanelCommand, AlSwitchCommand

_LOGGER = logging.getLogger(__name__)

TIME_DELTA_BETWEEN_IMAGES = 0.6

class VisonicClient(ManageConnection):
    """Set up for Visonic devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: VisonicConfigEntry,
        diagnostics: logEvents | None,
        force_standard_mode,
        disable_all_panel_commands,
        platform_manager : PlatformManager,
        panelident: int,
        state_changed_callback: Callable[..., None],
    ) -> None:
        """Initialize."""
        super().__init__(hass, entry, diagnostics, force_standard_mode,
                         disable_all_panel_commands, platform_manager,
                         panelident, state_changed_callback)
        self.logger.logstate_debug(
            "Initialising Client - Version %s, panel %s language %s",
            CLIENT_VERSION,
            str(panelident),
            str(hass.config.language),
        )

    def _get_protocol_for_panel_command(self, entity_id: str | None = None) -> tuple[AlPanelInterface | None, CommandResult]:
        """Safely get the visonic protocol."""
        if self._visonic_protocol is None:
            return None, CommandResult(
                AlarmCommandStatus.FAIL_PANEL_NO_CONNECTION,
                AvailableNotifications.CONNECTION,
                message="Panel Disconnected",
                eid=entity_id
            )
        if self.disable_all_panel_commands:
            return None, CommandResult(
                AlarmCommandStatus.FAIL_USER_CONFIG_PREVENTED,
                AvailableNotifications.COMMAND,
                message="Panel Commands Disabled",
                eid=entity_id
            )
        return self._visonic_protocol, CommandResult(
            AlarmCommandStatus.SUCCESS,
            AvailableNotifications.ALWAYS,
            eid=entity_id
        )

    # This is not called from anywhere, use it for debug purposes and/or to clear all entities from HA
    def print_all_entities(self, delete_as_well: bool = False):
        """Print all entities and devices from the registry for this config entry."""
        entry_id = self.entry.entry_id
        entity_reg = er.async_get(self.hass)
        entity_entries = er.async_entries_for_config_entry(entity_reg, entry_id)
        for damn in entity_entries:
            if delete_as_well:
                entity_reg.async_remove(damn.entity_id)

        # clear out all devices from the registry to recreate them, if the user has added/removed devices then this ensures that its a clean start
        device_reg = dr.async_get(self.hass)
        device_entries = dr.async_entries_for_config_entry(device_reg, entry_id)
        for damn in device_entries:
            if delete_as_well:
                device_reg.async_remove_device(damn.id)

        # The platforms do not initially exist, but after a reload they already exist
        # platforms = ep.async_get_platforms(self.hass, DOMAIN)

    async def send_client_get_sensor_image(self, devid: int | None, eid: str | None, duration: int) -> AlarmCommandStatus:
        """Send the command to the panel to get a camera image."""
        protocol, result = self._get_protocol_for_panel_command(eid)
        if protocol is None or result.status != AlarmCommandStatus.SUCCESS:
            return AlarmCommandStatus.FAIL_PANEL_NO_CONNECTION
        # Convert duration in seconds to a number of images to request from the panel
        #    This is the number of jpg images, there is also an additional single audio image
        #    Limit to between 1 and 10 images, 10 is what a Powerlink 3.1 Hardware module asks for
        image_count = min(int(float(duration) / TIME_DELTA_BETWEEN_IMAGES) + 2 if duration > 0 else 1, 10)
        status: AlCommandStatus = protocol.get_sensor_image(devid, image_count)
        # This is the check for whether the command has succeeded
        return AlarmCommandStatus(status)

    def convert_to_alarm_status(self, value: AlCommandStatus) -> AlarmCommandStatus:
        """Convert between pyvisonic library and main integration."""
        return AlarmCommandStatus(value)

    def convert_to_alarm_command(self, value: AlarmPanelCommand) -> AlPanelCommand:
        """Convert between pyvisonic library and main integration."""
        return AlPanelCommand(value)

    def convert_to_switch_command(self, value: AlarmSwitchCommand) -> AlSwitchCommand:
        """Convert between pyvisonic library and main integration."""
        return AlSwitchCommand(value)

    async def send_command(
        self,
        entity_id: str,
        command: AlarmPanelCommand,
        code: str | None,
        partitions: set[int] | None,
    ) -> CommandResult:
        """Send a command to the panel."""
        protocol, result = self._get_protocol_for_panel_command(entity_id)
        if protocol is None or result.status != AlarmCommandStatus.SUCCESS:
            return result

        self.logger.logstate_debug(
            f"Send command to Visonic Alarm Panel: {command.name}"
        )
        status = protocol.panel_command(self.convert_to_alarm_command(command), code, partitions)
        return CommandResult(
            self.convert_to_alarm_status(status),
            AvailableNotifications.COMMAND,
            message="Command Result",
            eid=entity_id
        )

    def get_sensor_bypass_state(self, entity_id: str) -> CommandResult:
        """Get bypass update."""
        protocol, result = self._get_protocol_for_panel_command(entity_id)
        if protocol is None or result.status != AlarmCommandStatus.SUCCESS:
            return result
        protocol.get_sensor_bypass_state()
        return CommandResult(
            AlarmCommandStatus.SUCCESS,
            AvailableNotifications.BYPASS,
            message="Send Bypass",
            eid=entity_id,
        )

    async def send_bypass(
        self,
        entity_id: str,
        devid: int,
        bypass: bool,
        code: str | None,
        alarm_state: AlarmPanelStatus | None,
    ) -> CommandResult:
        """Send bypass."""
        if bypass:
            self.logger.logstate_debug(
                "Attempting to bypass sensor device id = %s",
                str(devid),
            )
        else:
            self.logger.logstate_debug(
                "Attempting to restore (arm) sensor device id = %s",
                str(devid),
            )
        # Get the visonic protocol low level library
        protocol, result = self._get_protocol_for_panel_command(entity_id)
        if protocol is None or result.status != AlarmCommandStatus.SUCCESS:
            return result
        # Is the panel Disarmed?
        if alarm_state is None or alarm_state == AlarmPanelStatus.UNKNOWN:
            return CommandResult(
                AlarmCommandStatus.FAIL_INVALID_STATE,
                AvailableNotifications.BYPASS,
                message=f"Panel {self.get_partition_status()} State",
                eid=entity_id,
            )
        if alarm_state != AlarmPanelStatus.DISARMED:
            return CommandResult(
                AlarmCommandStatus.FAIL_INVALID_STATE,
                AvailableNotifications.BYPASS,
                message=f"Panel {alarm_state} State",
                eid=entity_id,
            )
        # Has the user allowed bypass in the configuration
        text = "Bypass" if bypass else "Restore"
        esb = to_bool(self.entry.options.get(CONF_ENABLE_SENSOR_BYPASS))
        if not esb:
            return CommandResult(
                AlarmCommandStatus.FAIL_USER_CONFIG_PREVENTED,
                AvailableNotifications.BYPASS,
                message=f"Sensor {text} State",
                eid=entity_id,
            )
        status = protocol.bypass_command(devid, bypass, code)
        return CommandResult(
            self.convert_to_alarm_status(status),
            AvailableNotifications.BYPASS,
            message=f"{text} sensor {devid}",
            eid=entity_id,
        )

    async def send_switch(self, entity_id: str, devid: int, command: AlarmSwitchCommand) -> CommandResult:
        """Send Switch."""
        protocol, result = self._get_protocol_for_panel_command(entity_id)
        if protocol is None or result.status != AlarmCommandStatus.SUCCESS:
            return result
        status = protocol.send_switch(devid, self.convert_to_switch_command(command))
        return CommandResult(
            self.convert_to_alarm_status(status),
            AvailableNotifications.SWITCH,
            message=f"Send Switch {command} to device {devid}",
            eid=entity_id,
        )

    async def send_get_event_log(self, code: str | None) -> CommandResult:
        """Send get event log."""
        protocol, result = self._get_protocol_for_panel_command()
        if protocol is None or result.status != AlarmCommandStatus.SUCCESS:
            return result
        status = AlarmCommandStatus.FAIL_INVALID_STATE
        self.logger.logstate_debug("Sending event log request to panel")
        status = protocol.get_event_log(code)
        return CommandResult(
            self.convert_to_alarm_status(status),
            AvailableNotifications.EVENTLOG,
            message="EventLog Request",
        )
