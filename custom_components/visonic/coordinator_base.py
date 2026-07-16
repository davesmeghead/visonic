"""Coordinator Base/Common class.

This class contains abstract methods that the coordinator in "cloud" and "direct" must implement
"""

from abc import abstractmethod
from collections.abc import Callable
import copy
from copy import deepcopy
from datetime import timedelta
import logging
from typing import Any

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components.alarm_control_panel.const import AlarmControlPanelState
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_CODE, ATTR_ENTITY_ID, CONF_COMMAND, Platform
from homeassistant.core import HomeAssistant, ServiceCall, valid_entity_id
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import slugify

from .const import (
    ATTR_BYPASS,
    CONF_ENABLE_SENSOR_BYPASS,
    CONF_SWITCH_COMMAND,
    DEVICE_ATTRIBUTE_NAME,
    DOMAIN,
    PANEL_ATTRIBUTE_NAME,
    PARTITION_ID_WHEN_BASE,
    PIN_REGEX,
    TEXT_LAST_EVENT_NAME,
)
from .log_events import logEvents
from .platform_manager import PlatformManager
from .utils import (
    capitalize,
    decode_code_from_dict_or_str,
    getAlarmPanelUniqueIdent,
    print_partition,
    to_bool,
)
from .visonic_types import (
    PANEL_TO_HA_STATUS_MAP,
    AlarmCommandStatus,
    AlarmPanelCommand,
    AlarmPanelStatus,
    AlarmSwitchCommand,
    AvailableNotifications,
    CommandResult,
    PanelCondition,
    PanelStateData,
    TriggerAlarmType,
    VisonicCoordinatorData,
)

_LOGGER = logging.getLogger(__name__)
#_LOGGER.setLevel(logging.DEBUG)   # setting this enables the timing debug output from the HA coordinator

###################################################################################
##############  Common coordinator for direct and cloud connections ###############
###################################################################################

class VisonicCoordinator(DataUpdateCoordinator[VisonicCoordinatorData]):
    """Abstract base class for coordinator, including common functions."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, panel_id: int,
                 lo: logEvents, update_interval: int, always_update: bool,
                 state_changed_callback: Callable[..., None] | None = None
    ):
        """Initialize the base coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{capitalize(DOMAIN)} {entry.title}",
            config_entry=entry,
            update_interval=timedelta(seconds=update_interval),
            #always_update=always_update,
        )

        self.panel_id = panel_id
        # do not use self.logger as it is defined in parent coordinator class
        self._event_logger = lo
        self.disable_all_panel_commands = False

        # Declare platform_manager using the base class so it can be used in this base class
        # Callback needed as this is a panel driven system
        #    All changes come bottom up
        self.platform_manager: PlatformManager = PlatformManager(
            hass=self.hass,
            panelident=panel_id,
            entry=entry,
            logger=lo,
            state_changed_callback=state_changed_callback,
        )

    def state_changed_callback(self):
        """Client calls this when the panel data has changed."""
        data = self.get_state_snapshot()
        self.async_set_updated_data(data)

    @property
    def log(self) -> logEvents:
        """Allow joined classes to log to the event log for diagnostics. Use it wisely."""
        return self._event_logger

    @abstractmethod
    def get_state_snapshot(self) -> VisonicCoordinatorData:
        """Get the state snapshot."""

    @abstractmethod
    def hasStarted(self) -> bool:
        """Has the system started?"""

    @abstractmethod
    async def get_cached_image(self, sensor_id: int) -> bytearray | None:
        """Get the cached image."""

    @abstractmethod
    def ive_been_created(self):
        """Called when certain entities are first initialised to make sure they get the latest data."""

    @abstractmethod
    def get_diagnostic_data(self) -> dict[str, Any]:
        """Build and return the diagnostics data for this panel."""

    @abstractmethod
    def set_partition_name(
        self,
        partition: int | None = None,
        panel_entity_name: str | None = None,
    ):
        """Shortcut to set the partition name (used in HA events)."""

    @abstractmethod
    async def async_panel_connect(self) -> bool:
        """Make the client connection to the panel."""

    @abstractmethod
    async def async_service_panel_reconnect(self, call: ServiceCall | None):
        """Service call to re-connect the comms connection."""

    @abstractmethod
    async def async_panel_stop(self):
        """Redirector to stop and unload the hub."""

    # the return value indicates whether any sensors needed to be bypassed
    @abstractmethod
    async def send_command(
        self,
        name: str,
        command: AlarmPanelCommand,
        code: str | None,
        partition_set: set[int] | None,
    ) -> CommandResult:
        """Common send command function."""

    @abstractmethod
    async def send_bypass(
        self,
        devid: int,
        bypass: bool,
        code: str | None,
    ) -> CommandResult:
        """Send bypass command."""

    @abstractmethod
    async def send_switch(self, devid: int, command: AlarmSwitchCommand) -> CommandResult:
        """Set the Switch/PGM switch."""

    async def async_service_panel_zoneinfo(self, call: ServiceCall) -> dict[str, Any]:
        """Service call get open zones in the panel."""
        valid = await self.check_the_basics(
            call, "panel zone info"
        )
        # Passing valid = False in returns an empty structure with the validity False
        return await self.platform_manager.async_get_zone_switch_info(valid)

    @abstractmethod
    def get_panel_pin_code_simple(self, code: str | None):
        """Get code code."""

    @abstractmethod
    async def send_get_event_log(
        self, isValidPL: bool, code: str | None
    ) -> CommandResult:
        """Send get event log."""

    @abstractmethod
    async def send_get_sensor_image(self, devid: int | None, eid: str | None):
        """Send the command to the panel to get a camera image."""

    def get_panel_and_partition_state(self, partition: int | None) -> PanelStateData:
        """Update the state of the entity based on device data. This is common to Alarm and Sensor Entity."""

        vcd: VisonicCoordinatorData = self.data
        if not vcd or not vcd.connected:
            return PanelStateData()

        _armcode = AlarmPanelStatus.UNKNOWN
        _mystate = AlarmControlPanelState.DISARMED

        if partition == PARTITION_ID_WHEN_BASE and vcd.panelstate.partition is None:
            # Check to make sure we have partitions, if not then set partition to None
            partition = None

        if partition is None:
            # A panel with no partitions and the siren data is for a single panel
            isa, dev, alarm = vcd.partition_siren.get(0, (False, 0, TriggerAlarmType.NONE))
            _armcode = vcd.partition_armcode.get(0, AlarmPanelStatus.UNKNOWN)
            stat: dict[str, Any] = deepcopy(vcd.panelstate.as_dict())
            stat |= vcd.partition_dict.get(0, {})
            stat.pop("partition", None)

        elif partition == PARTITION_ID_WHEN_BASE:
            # A panel with partitions and this is the base
            # Return the first tuple where the first element is True, or (False, 0) if none
            isa, dev, alarm = next(
                (t for t in vcd.partition_siren.values() if t[0]),
                (False, 0, TriggerAlarmType.NONE),
            )
            _armcode: AlarmPanelStatus = max(
                vcd.partition_armcode.values(), default=AlarmPanelStatus.UNKNOWN
            )
            stat: dict[str, Any] = deepcopy(vcd.panelstate.as_dict())
            if vcd.panelstate.partition is not None:
                # Update the partition numbers, list all partitions
                stat["partition"] = print_partition(vcd.panelstate.partition)

        else:
            # A panel with partitions
            stat = vcd.partition_dict.get(partition, {})
            #stat["partition"] = {p+1 for p in stat["partition"]}
            isa, dev, alarm = vcd.partition_siren.get(
                partition, (False, 0, TriggerAlarmType.NONE)
            )
            _armcode = vcd.partition_armcode.get(partition, AlarmPanelStatus.UNKNOWN)
            # Update the partition number, only identify the current partition
            stat["partition"] = print_partition(partition)

        if isa:
            _mystate = AlarmControlPanelState.TRIGGERED
        elif _armcode in PANEL_TO_HA_STATUS_MAP:
            _mystate = PANEL_TO_HA_STATUS_MAP[_armcode]

        statusdict: dict[str, Any] = (
            dict(vcd.statusdict) if partition in (None, PARTITION_ID_WHEN_BASE) else {}
        )

        if "state" in stat and isinstance(stat["state"], AlarmPanelStatus):
            stat["state"] = stat["state"].name.lower()

        _dsa: dict[str, Any] = {
            **(stat or {}),
            **(statusdict or {}),
            PANEL_ATTRIBUTE_NAME: str(self.panel_id),
        }

        for k,v in _dsa.items():
            if v is None:
                _dsa[k] = "none"

        last_event: str | None = stat.get(TEXT_LAST_EVENT_NAME)
        _last_event_name: str | None = (
            last_event if last_event and len(last_event) > 2 else None
        )

        part = 0 if partition is None or partition == PARTITION_ID_WHEN_BASE else partition
        return PanelStateData(
            connected=vcd.connected,
            show_keypad=vcd.partition_show_keypad.get(part, False),
            code_arm_required=vcd.partition_code_arm_required.get(part, False),
            is_power_master=vcd.ispowermaster,
            trigger_device=(dev, alarm),
            alarm_state=_mystate,
            panel_state=_armcode,
            attributes=copy.deepcopy(_dsa),
            last_event_name=_last_event_name,
        )

    async def _command_bypass_on_open_zones(self, sl: list[int]) -> CommandResult | None:
        if sl:
            # There is at least one zone needs bypassing so check the user setting whether it's allowed
            esb = to_bool(self.config_entry.options.get(CONF_ENABLE_SENSOR_BYPASS))
            if not esb:
                self.platform_manager.generate_event_output(
                    PanelCondition.CHECK_BYPASS_COMMAND,
                    AlarmCommandStatus.FAIL_USER_CONFIG_PREVENTED,
                    "Bypass",
                    "Sensor Bypass State",
                )
                return CommandResult(
                    AlarmCommandStatus.FAIL_USER_CONFIG_PREVENTED,
                    AvailableNotifications.COMMAND,
                    "Sensor Bypass State",
                )
            for s in sl:
                self._event_logger.logstate_debug(f"Attempting to bypass sensor: {s}")
                status: CommandResult = await self.send_bypass(s, True)
                if status != AlarmCommandStatus.SUCCESS:
                    return CommandResult(
                        status.status,
                        AvailableNotifications.BYPASS,
                        "Failed bypass",
                    )
        return None

    async def bypass_open_zones(self, part: set[int] | None) -> CommandResult:
        """Obtain and then bypass any open contact sensor / wired zones."""
        sl = list(self.platform_manager.get_sensors_to_bypass(part))
        if len(sl) > 0:
            # There are open zones to bypass
            if (cr := await self._command_bypass_on_open_zones(sl)) is not None:
                # Bypass was not successful, return error status
                return cr
        return CommandResult(
            AlarmCommandStatus.SUCCESS,
            AvailableNotifications.COMMAND
        )

    async def check_the_basics(self, call: ServiceCall, message: str) -> bool:
        """Common checker to check if panel is connected and user permissions."""
        if not self.disable_all_panel_commands:
            # Commands are enabled
            self._event_logger.logstate_debug(f"Received {message} request")
            if call.context.user_id:
                # self._event_logger.logstate_debug(f"Checking user information for permissions: {call.context.user_id}")
                # Check security permissions (that this user has access to the alarm panel entity)
                await self.checkUserPermission(
                    call,
                    POLICY_READ,
                    Platform.ALARM_CONTROL_PANEL
                    + "."
                    + slugify(getAlarmPanelUniqueIdent(self.panel_id)),
                )
            self._event_logger.logstate_debug(
                f"Received {message} request - user approved"
            )
            return True
        self._event_logger.create_ha_notification(
            AvailableNotifications.COMMAND,
            "Visonic Alarm Panel: Panel Commands Disabled",
        )
        return False

    async def checkUserPermission(self, call: ServiceCall, perm: str, entity: str):
        """Check that the use has permission to do the action."""
        if not isinstance(call.context.user_id, str):
            raise UnknownUser(
                context=call.context,
                entity_id=entity,
                permission=perm,
            )
        user = await self.hass.auth.async_get_user(call.context.user_id)
        if user is None:
            raise UnknownUser(
                context=call.context,
                entity_id=entity,
                permission=perm,
            )
        if not user.permissions.check_entity(entity, perm):
            raise Unauthorized(
                context=call.context,
                entity_id=entity,
                permission=perm,
            )

    def decode_code_from_call_data(
        self,
        call: ServiceCall,
    ) -> tuple[bool, str | None]:
        """Decode the alarm code from the call data."""
        code = call.data.get(ATTR_CODE)
        # If the code is defined then it must be a 4 digit string
        if code and not PIN_REGEX.match(code):
            code = "0000"
        pcode = decode_code_from_dict_or_str(code)
        is_valid, pin_code = self.get_panel_pin_code_simple(code=pcode)
        if is_valid:
            return True, pin_code
        return False, ""

    async def decode_entity(
        self,
        call: ServiceCall,
        ent_type: str,
        message: str,
        an: AvailableNotifications,
    ) -> tuple[int | None, str | None]:
        """Decode the entity from the call data using inline assignments."""

        def fail(msg: str) -> tuple[None, None]:
            self._event_logger.create_ha_notification(
                an,
                f"Attempt to {message} for panel {self.panel_id}, {msg}",
            )
            return None, None

        if ATTR_ENTITY_ID not in call.data:
            return fail("but entity not defined")

        eid: str | list[str] = call.data.get(ATTR_ENTITY_ID, "")

        if isinstance(eid, list):  # sometimes it is a list with 1 entry
            eid = eid[0]

        if len(eid) > 0:
            # Make sure it's a valid entity and that the user has permission for it, and that it is for this panel
            eid = eid if eid.startswith(f"{ent_type}.") else f"{ent_type}.{eid}"
            if not valid_entity_id(eid):
                return fail(f"invalid entity {eid}")
            if call.context.user_id:
                await self.checkUserPermission(call, POLICY_CONTROL, eid)
            if not (state := self.hass.states.get(eid)):
                return fail(f"unknown device state for entity {eid}")
            devid = state.attributes.get(DEVICE_ATTRIBUTE_NAME)
            panel = state.attributes.get(PANEL_ATTRIBUTE_NAME)
            if panel is None or devid is None:
                return fail(f"incorrect entity {eid}")
            if panel != self.panel_id:
                return fail(
                    f"device {devid} but entity {eid} not connected to this panel"
                )
            return devid, eid

        return fail(f"invalid entity type {eid}  {type(eid)}")

    async def async_service_panel_eventlog(self, call: ServiceCall):
        """Service call to retrieve the event log from the panel. This currently just gets dumped in the HA log file."""
        if not await self.check_the_basics(call, "event log"):
            return
        is_valid, code = self.decode_code_from_call_data(call)
        await self.send_get_event_log(is_valid, code)

    async def async_service_sensor_image(self, call: ServiceCall):
        """Service call to bypass a sensor in the panel."""
        if not await self.check_the_basics(call, "sensor image"):
            return
        devid, eid = await self.decode_entity(
            call,
            Platform.IMAGE,
            "retrieve sensor image",
            AvailableNotifications.IMAGE,
        )
        await self.send_get_sensor_image(devid, eid)

    async def async_service_sensor_bypass(self, call: ServiceCall):
        """Service call to bypass a sensor in the panel."""
        # These create notifications so no need to do anything else
        if not await self.check_the_basics(call, "sensor bypass"):
            return
        devid, _eid = await self.decode_entity(
            call,
            Platform.SELECT,
            "bypass a sensor",
            AvailableNotifications.BYPASS,
        )
        if not devid:  # This creates notifications so no need to do anything else
            return
        is_valid, code = self.decode_code_from_call_data(call)
        if not is_valid:
            self.platform_manager.generate_event_output(
                PanelCondition.CHECK_BYPASS_COMMAND,
                AlarmCommandStatus.FAIL_INVALID_CODE,
                "bypass a sensor",
                "bypass a sensor Request",
            )
            return
        bypass: bool = call.data.get(ATTR_BYPASS, False)
        await self.send_bypass(devid, bypass, code)

    async def async_service_panel_command(self, call: ServiceCall) -> bool:
        """Service call to send an arm/disarm command to the panel."""
        if not await self.check_the_basics(call, "command"):
            return False
        command_name: str | None = call.data.get(CONF_COMMAND, "")
        if not command_name:
            self._event_logger.create_ha_notification(
                AvailableNotifications.COMMAND,
                f"Attempt to send command to panel {self.panel_id}, command not set for entity",
            )
            return False
        if not (eid := call.data.get(ATTR_ENTITY_ID)):
            self._event_logger.create_ha_notification(
                AvailableNotifications.COMMAND,
                f"Attempt to send command to panel {self.panel_id}, entity not set",
            )
            return False
        is_valid, code = self.decode_code_from_call_data(call)
        if not is_valid:
            self.platform_manager.generate_event_output(
                PanelCondition.CHECK_ARM_DISARM_COMMAND,
                AlarmCommandStatus.FAIL_INVALID_CODE,
                "PanelCommand",
                "Panel Command Request",
            )
            return False

        command: AlarmPanelCommand | None = AlarmPanelCommand.from_name(command_name)
        if command is None:
            return False

        self._event_logger.logstate_debug(
            f"[service_panel_command]   Sending Command: {command}  from raw string: {command_name}"
        )

        state = self.hass.states.get(eid)
        attributes: dict[str, Any] = state.attributes if state else {}
        partition = attributes.get("partition")

        # Determine which partitions to send the command to, or the panel (all partitions)
        partition_set = {partition - 1} if partition is not None else set(self.data.partition_dict) # {0, 1, 2}
        result: CommandResult = await self.send_command(
            "Alarm Service Call",
            command,
            code,
            partition_set,
        )
        return result.did_bypass

    async def async_service_panel_switch(self, call: ServiceCall):
        """Service call to set an switch device in the panel."""
        # This creates notifications so no need to do anything else
        if not await self.check_the_basics(call, "switch command"):
            return
        devid, _eid = await self.decode_entity(
            call,
            Platform.SWITCH,
            "switch command",
            AvailableNotifications.SWITCH,
        )
        if devid is not None:
            command_name = call.data.get(CONF_SWITCH_COMMAND, "OFF")
            command: AlarmSwitchCommand | None = AlarmSwitchCommand.from_name(command_name)
            if command is not None:
                await self.send_switch(devid, command)
