"""Visonic Coordinator class."""

import asyncio
from typing import Any

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import slugify

from ..const import (  # noqa: TID252
    CONF_EMULATION_MODE,
    CONF_ENABLE_REMOTE_ARM,
    CONF_ENABLE_REMOTE_DISARM,
    PARTITION_ID_WHEN_BASE,
    TEXT_DISCONNECTION_COUNT,
)
from ..coordinator_base import VisonicCoordinator  # noqa: TID252
from ..exceptions import VisonicException  # noqa: TID252
from ..log_events import logEvents  # noqa: TID252
from ..server import ServerProtocol  # noqa: TID252
from ..utils import getAlarmPanelUniqueIdent, to_bool  # noqa: TID252
from ..visonic_types import (  # noqa: TID252
    AlarmCommandStatus,
    AlarmPanelCommand,
    AlarmPanelStatus,
    AlarmSwitchCommand,
    AvailableNotifications,
    CommandResult,
    EmulationMode,
    PanelStateData,
    VisonicConfigEntry,
    VisonicCoordinatorData,
)
from .client_visonic_client import VisonicClient

#_LOGGER = logging.getLogger(__name__)

# ARM/DISARM commands
ARM_DISARM_COMMANDS = {
    AlarmPanelCommand.DISARM,
    AlarmPanelCommand.ARM_HOME,
    AlarmPanelCommand.ARM_AWAY,
    AlarmPanelCommand.ARM_HOME_INSTANT,
    AlarmPanelCommand.ARM_AWAY_INSTANT,
    AlarmPanelCommand.ARM_HOME_BYPASS,
    AlarmPanelCommand.ARM_AWAY_BYPASS,
}

POWERMASTER_COMMANDS = {
    AlarmPanelCommand.MUTE,
    AlarmPanelCommand.TRIGGER,
    AlarmPanelCommand.FIRE,
    AlarmPanelCommand.EMERGENCY,
    AlarmPanelCommand.PANIC,
}

class VisonicDirectCoordinator(VisonicCoordinator):
    """Visonic Coordinator."""

    def __init__(
        self, hass: HomeAssistant, entry: VisonicConfigEntry, panel_id: int, event_logger: logEvents
    ) -> None:
        """Initialse."""
        # Manually update (async_handle_client_update) for significant changes in state and some attributes
        #       i.e. push data in to HA
        # Use auto (_async_update_data) for minor updates to entity attributes every 60 seconds
        # Also, for both manual and auto, only call the handlers when data changes

        super().__init__(hass, entry, panel_id, event_logger, 60, False, self.state_changed_callback)

        v = EmulationMode(entry.data.get(CONF_EMULATION_MODE, EmulationMode.POWERLINK))
        self.force_standard_mode = v == EmulationMode.STANDARD
        self.disable_all_panel_commands = v == EmulationMode.MINIMAL
        # If disable all commands then force standard is set to True
        if self.disable_all_panel_commands:
            self.force_standard_mode = True

        self._client: VisonicClient = VisonicClient(
            hass = hass,
            entry = entry,
            diagnostics = event_logger,
            force_standard_mode = self.force_standard_mode,
            disable_all_panel_commands = self.disable_all_panel_commands,
            platform_manager = self.platform_manager,
            panelident = panel_id,
            state_callback = self.state_changed_callback,
        )
        self._event_logger = event_logger
        self._prev_panel_connected = False

    def ive_been_created(self):
        """Called when certain entities are first initialised to make sure they get the latest data."""
        try:
            data = self.get_state_snapshot()
            super().async_set_updated_data(data)
        except Exception as err:
            raise VisonicException(
                "Client not created properly",
                code=300,
                original_exception=err,
            ) from err

    def hasStarted(self) -> bool:
        """Has the system started?"""
        return self._client.hasStarted()

    async def _async_update_data(self) -> VisonicCoordinatorData:
        """Override the parent function."""
        try:
            self._service_image_queue()
            return self.get_state_snapshot()
        except Exception as err:
            raise UpdateFailed(str(err)) from err

    def _service_image_queue(self) -> None:
        """On (re)connect drop stale image state; while idle and connected, dispatch the next queued request."""
        connected = bool(self._client and self._client.is_panel_connected())
        if connected and not self._prev_panel_connected:
            self.platform_manager.reset_image_state()
        self._prev_panel_connected = connected
        if connected and not self.platform_manager.image_download_active():
            nxt = self.platform_manager.pop_image_request()
            if nxt is not None:
                # mark active now so a re-poll before the send task runs can't dispatch a second request
                self.platform_manager.mark_image_request(nxt[0], nxt[2])
                self.hass.async_create_task(self.send_get_sensor_image(nxt[0], nxt[1], nxt[2]))

#    @callback
#    def async_handle_client_update(self, data: VisonicCoordinatorData) -> None:
#        """Handle update. This function is passed in to VisonicClient when the client is created. The client calls this callback on data change."""
#        # Called by VisonicClient when data changes state
#        self.async_set_updated_data(data)

    def set_partition_name(
        self,
        partition: int | None = None,
        panel_entity_name: str | None = None,
    ):
        """Shortcut to set the partition name (used in HA events)."""
        self._client.set_partition_name(partition, panel_entity_name)

    async def get_cached_image(self, sensor_id: int) -> bytearray | None:
        """Get the cached image."""
        if self.platform_manager and hasattr(self.platform_manager, "async_get_jpg_image"):
            return await self.platform_manager.async_get_jpg_image(sensor_id)
        if self.platform_manager and hasattr(self.platform_manager, "get_jpg_image"):
            # run blocking sync code in executor
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self.platform_manager.get_jpg_image, sensor_id
            )
        return None

    def get_diagnostic_data(self) -> dict[str, Any]:
        """Build and return the diagnostics data for this panel."""
        if not self._client:
            return {
                "integration connected": "no",
                "panel connected": "no",
                "history": self._event_logger.get_str_log(),
            }
        try:
            data = self.get_state_snapshot()
            # Add sensors, switches, and logs
            return {
                "integration connected": "yes",
                "panel connected": (
                    "yes" if self._client.is_panel_connected() else "no"
                ),
                "visonic": data.as_dict(convert_to_name=True),
                "history": self._event_logger.get_str_log(),
            }
        except Exception as err:
            raise VisonicException(
                "Get Diagnostic data failed",
                code=400,
                original_exception=err,
            ) from err

    def update_t_p(self, transport: asyncio.Transport, protocol: ServerProtocol):
        """Update transport and protocol."""
        self._client.update_t_p(transport, protocol)

    async def async_server_connect(self, transport: asyncio.Transport, protocol: ServerProtocol) -> bool:
        """Server produced connection."""
        await self._client.async_server_connect(transport, protocol)

    # =======================================================================================================
    # ============ These 3 functions are panel connection commands : connect, reconnect and stop ============
    # =======================================================================================================

    async def async_panel_connect(self) -> bool:
        """Make the client connection to the panel."""
        # Refresh the data for the first pass
        await self.async_config_entry_first_refresh()
        self.platform_manager.setAlarmDeviceInformation()
        await asyncio.sleep(0.0)
        self.platform_manager.async_create_alarm_panel()
        return self._client.connect()

    async def async_service_panel_reconnect(self, call: ServiceCall | None):
        """Service call to re-connect the comms connection."""
        # This is callable from frontend and checks user permission
        try:
            if call is not None and call.context.user_id:
                # self._event_logger.logstate_debug(f"Checking user information for permissions: {call.context.user_id}")
                # Check security permissions (that this user has access to the alarm panel entity)
                await self.checkUserPermission(
                    call,
                    POLICY_CONTROL,
                    Platform.ALARM_CONTROL_PANEL
                    + "."
                    + slugify(getAlarmPanelUniqueIdent(self.panel_id)),
                )
            self._client.async_restart(True)
        except (OSError, TimeoutError) as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            self._event_logger.logstate_warning(
                "........... async_service_panel_reconnect, caused exception %s",
                ex,
            )

    async def async_panel_stop(self):
        """Redirector to stop and unload the hub."""
        self.async_set_updated_data(None)  # clears update cycle
        await self._client.async_panel_stop()

    # =======================================================================================================
    # ==== These 3 functions are commanded from alarm_control_panel and the 3 async service calls below =====
    # =======================================================================================================

    # the return value indicates whether any sensors needed to be bypassed
    async def send_command(
        self,
        name: str,
        command: AlarmPanelCommand,
        code: str | None,
        partition_set: set[int] | None,
    ) -> CommandResult:
        """Common send command function."""
        did_bypass = False
        if self._client.is_power_master() and command in POWERMASTER_COMMANDS:
            return await self._client.send_command(
                command, code, partition_set
            )
        if command in ARM_DISARM_COMMANDS:
            if not (
                (
                    command == AlarmPanelCommand.DISARM
                    and to_bool(
                        self.config_entry.options.get(CONF_ENABLE_REMOTE_DISARM, False)
                    )
                )
                or (
                    command != AlarmPanelCommand.DISARM
                    and to_bool(self.config_entry.options.get(CONF_ENABLE_REMOTE_ARM, False))
                )
            ):
                return CommandResult(
                    AlarmCommandStatus.FAIL_USER_CONFIG_PREVENTED,
                    AvailableNotifications.COMMAND,
                    "Request Arm/Disarm",
                )

            pl = self._client.get_partitions_in_use()
            part = PARTITION_ID_WHEN_BASE if partition_set is None or len(partition_set) == 3 or partition_set == pl else list(partition_set)[0] + 1
            if command in {AlarmPanelCommand.ARM_HOME_BYPASS, AlarmPanelCommand.ARM_AWAY_BYPASS}:
                result = await self.bypass_open_zones(part)
                if result.status != AlarmCommandStatus.SUCCESS:
                    return result
                command = (
                    AlarmPanelCommand.ARM_HOME
                    if command == AlarmPanelCommand.ARM_HOME_BYPASS
                    else AlarmPanelCommand.ARM_AWAY
                )
                did_bypass = True

            result: CommandResult = await self._client.send_command(command, code, partition_set)

            if did_bypass:
                self._client.get_sensor_bypass_state()

            if result.status != AlarmCommandStatus.SUCCESS:
                self._event_logger.create_ha_notification(
                    result.notify,
                    f"Failed Attempt on {name} to {command} panel {self.panel_id}  {result.message}",
                )
            else:
                result.did_bypass = did_bypass
        return result

    async def send_bypass(
        self,
        devid: int,
        bypass: bool,
        code: str | None,
    ) -> CommandResult:
        """Send bypass command."""
        partition_state: PanelStateData = self.get_panel_and_partition_state(PARTITION_ID_WHEN_BASE)
        result: CommandResult = await self._client.send_bypass(
            devid, bypass, code, partition_state.panel_state
        )
        if result.status != AlarmCommandStatus.SUCCESS:
            self._event_logger.create_ha_notification(
                result.notify,
                f"Failed Attempt to send bypass/arm to panel {self.panel_id}  {result.message}",
            )
        return result

    async def send_switch(self, devid: int, command: AlarmSwitchCommand) -> CommandResult:
        """Set the Switch/PGM switch."""
        result: CommandResult = await self._client.send_switch(devid, command)
        if result.status != AlarmCommandStatus.SUCCESS:
            self._event_logger.create_ha_notification(
                result.notify,
                f"Failed Attempt to set switch device for panel {self.panel_id}, device {devid} {result.message}",
            )
        return result

    # =======================================================================================================
    # ======== Functions below this are the service calls and the Frontend controls from Home Assistant =====
    # =======================================================================================================

    def get_panel_pin_code_simple(self, code: str | None):
        """Get code code."""
        return self._client.get_panel_pin_code_simple(code)

    async def send_get_event_log(
        self, isValidPL: bool, code: str | None
    ) -> CommandResult:
        """Send get event log."""
        await self._client.send_get_event_log(isValidPL, code)

    async def send_get_sensor_image(self, devid: int | None, eid: str | None, duration: int):
        """Send the command to the panel to get a camera image."""
        self.platform_manager.mark_image_request(devid, duration)
        await self._client.send_get_sensor_image(devid, eid, duration)
        self.async_update_listeners()

    def get_state_snapshot(self) -> VisonicCoordinatorData:
        """Return complete snapshot of current state."""

        partitions = self._client.get_partitions_in_use() or {}
        no_of_partitions = max(1, len(partitions))  # for single panels do partition 0
        indices = range(no_of_partitions)
        if not self._client.is_panel_connected():
            return VisonicCoordinatorData(
                connected=False,
                #ident=getAlarmPanelUniqueIdent(self.panel_id)
            )

        partition_armcode = {i: AlarmPanelStatus(self._client.get_partition_status(i).value) for i in indices}

        show_keypad: dict[int, bool] = {}
        code_arm_required: dict[int, bool] = {}
        for i in indices:
            _, _, sk, car = self._client.get_panel_pin_code(code=None, psc=partition_armcode[i])
            show_keypad[i] = sk
            code_arm_required[i] = car

        return VisonicCoordinatorData(
            connected=True,
            ispowermaster=self._client.is_power_master(),
            #ident=getAlarmPanelUniqueIdent(self.panel_id),
            mode=self._client.get_panel_mode().name.lower(),
            model=self._client.get_panel_model(),
            statusdict={TEXT_DISCONNECTION_COUNT: self._client.panel_disconnection_counter},
            panelstate=self._client.get_panel_status_dict(),
            #partition_in_use=self._client.get_partitions_in_use(),
            partition_armcode=partition_armcode,
            partition_show_keypad=show_keypad,
            partition_code_arm_required=code_arm_required,
            partition_siren={i: self._client.is_siren_active(i) for i in indices},
            partition_dict={i: self._client.get_partition_status_dict(i) for i in indices},
            zones=self.platform_manager.sensor_state(),
            switch=self.platform_manager.switch_state(),
            device=self.platform_manager.device_state(),
        )
