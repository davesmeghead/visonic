"""Visonic Coordinator Cloud class."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import logging
import traceback
from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_CODE,
    CONF_EMAIL,
    CONF_EXTERNAL_URL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr  #, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from ..const import (  # noqa: TID252
    CONF_ARM_HOME_ENABLED,
    CONF_CLOUD_APP_ID,
    CONF_ENABLE_REMOTE_ARM,
    CONF_ENABLE_REMOTE_DISARM,
    CONF_PANEL_SERIAL,
    DEFAULT_CLOUD_SCAN_INTERVAL,
    PARTITION_ID_WHEN_BASE,
    TEXT_DISCONNECTION_COUNT,
    TEXT_PANEL_MODEL,
    VISONIC_CLOUD_SERVER,
)
from ..coordinator_base import VisonicCoordinator  # noqa: TID252
from ..exceptions import VisonicAuthException, VisonicException  # noqa: TID252
from ..log_events import logEvents  # noqa: TID252
from ..utils import to_bool, update_config_entry_threadsafe  # noqa: TID252
from ..visonic_data_types import VisonicCoordinatorData  # noqa: TID252
from ..visonic_entity_types import (  # noqa: TID252  # noqa: TID252
    AlarmSensorType,
    DeviceState,
    PanelState,
    SensorOnTimeout,
    SensorState,
    SwitchState,
    VisonicBinarySensorKey,
    VisonicFloatSensorKey,
    ZoneSensorDetails,
)
from ..visonic_types import (  # noqa: TID252  # noqa: TID252
    AlarmCommandStatus,
    AlarmPanelCommand,
    AlarmPanelStatus,
    AlarmSwitchCommand,
    AvailableNotifications,
    CommandResult,
    TriggerAlarmType,
)
from .pyvisonicalarm.alarm import AlarmSystem, GenericDevice, PanelInfo
from .pyvisonicalarm.classes import Alarm, Event, Partition
from .pyvisonicalarm.core import API
from .pyvisonicalarm.devices import (
    CameraDevice,
    ContactDevice,
    Device,
    KeyFobDevice,
    MotionDevice,
    PanelDevice,
    PGMDevice,
    ShockDevice,
    SmokeDevice,
)
from .pyvisonicalarm.exceptions import UnauthorizedError, WrongUsernameOrPasswordError

_LOGGER = logging.getLogger(__name__)


MAP_ALARM_STATUS = {
    "DISARM" : AlarmPanelStatus.DISARMED,
    "AWAY" : AlarmPanelStatus.ARMED_AWAY,
    "HOME" : AlarmPanelStatus.ARMED_HOME,
    "EXIT_AWAY" : AlarmPanelStatus.ARMING_AWAY,
    "EXIT_HOME" : AlarmPanelStatus.ARMING_HOME,
    "ENTRY_DELAY" : AlarmPanelStatus.ENTRY_DELAY,
    "ENTRYDELAY" : AlarmPanelStatus.ENTRY_DELAY,
    "PROGRAMMING" : AlarmPanelStatus.INSTALLER,
    "ALARM" : AlarmPanelStatus.TRIGGERED,                   # TODO: check these
}

MAP_ALARM_TYPE = {
    "EMERGENCY" : TriggerAlarmType.EMERGENCY,
    "FIRE" : TriggerAlarmType.FIRE,
    "PANIC" : TriggerAlarmType.PANIC,
    "ALARM_IN_MEMORY" : TriggerAlarmType.INTRUDER,
}

PANEL_ARMED_LIST = [AlarmPanelStatus.ARMED_AWAY, AlarmPanelStatus.ARMED_AWAY_BYPASS, AlarmPanelStatus.ARMED_AWAY_INSTANT,
                    AlarmPanelStatus.ARMED_HOME, AlarmPanelStatus.ARMED_HOME_BYPASS, AlarmPanelStatus.ARMED_HOME_INSTANT ]

# These are used to create the lists below
TAMPER_NO_TIMEOUT  = (VisonicBinarySensorKey.ZONE_TAMPER,  SensorOnTimeout.NO_TIMEOUT)
PROBLEM_NO_TIMEOUT = (VisonicBinarySensorKey.ZONE_PROBLEM, SensorOnTimeout.NO_TIMEOUT)
BATTERY_NO_TIMEOUT = (VisonicBinarySensorKey.ZONE_BATTERY, SensorOnTimeout.NO_TIMEOUT)
STATUS_TIMEOUT     = (VisonicBinarySensorKey.ZONE_STATUS,  SensorOnTimeout.STATE)
CONTACT_TIMEOUT    = (VisonicBinarySensorKey.ZONE_CONTACT, SensorOnTimeout.STATE)
TRIGGER_MOTION     = (VisonicBinarySensorKey.ZONE_TRIGGER, SensorOnTimeout.MOTION)
TRIGGER_OTHER      = (VisonicBinarySensorKey.ZONE_TRIGGER, SensorOnTimeout.OTHER)
TEMP_NO_TIMEOUT    = (VisonicFloatSensorKey.ZONE_TEMP,     SensorOnTimeout.NO_TIMEOUT)
LUX_NO_TIMEOUT     = (VisonicFloatSensorKey.ZONE_LUX,      SensorOnTimeout.NO_TIMEOUT)

# Create the lists of entities used for each sensor type.  This gives flexibility as sensors are added to define the HA entities.
#   All sensors have TAMPER and PROBLEM entities (so these are not in the variable names)
#   Trigger and Status cannot appear in the same setting row (as they are both called "Zone")
#   Trigger and Contact are in the same row for SHOCK sensors that have both trigger and state, CONTACT is used as a different name (i.e. not "Zone")
BASIC_STATUS                  = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT]
BATTERY_AND_STATUS_TIMEOUT    = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT, BATTERY_NO_TIMEOUT, STATUS_TIMEOUT]
BATTERY_AND_TRIGGER_MOTION    = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT, BATTERY_NO_TIMEOUT, TRIGGER_MOTION]
BATTERY_AND_TRIGGER_OTHER     = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT, BATTERY_NO_TIMEOUT, TRIGGER_OTHER]
# BATTERY_TEMP_LUX              = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT, BATTERY_NO_TIMEOUT, TRIGGER_MOTION, TEMP_NO_TIMEOUT, LUX_NO_TIMEOUT]  # used for camera entries
BATTERY_TRIGGER_STATUS        = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT, BATTERY_NO_TIMEOUT, TRIGGER_OTHER, CONTACT_TIMEOUT]                   # Trigger and State for SHOCK sensors
STATUS_ONLY_TIMEOUT           = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT, STATUS_TIMEOUT]
BATTERY_AND_TEMP              = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT, BATTERY_NO_TIMEOUT, TEMP_NO_TIMEOUT]

class VisonicCloudCoordinator(VisonicCoordinator):
    """Class to manage fetching Visonic data from the cloud API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, panel_id: int, event_logger: logEvents):
        """Initialize the coordinator."""
        ui = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_CLOUD_SCAN_INTERVAL)
        super().__init__(hass, entry, panel_id=panel_id, lo=event_logger, update_interval=ui, always_update=True, state_changed_callback=self.state_changed_callback)

        self.panel_entity_name: dict[int, str] = {}
        self.partition_list : set[int] = set()
        self.siren_arm = False
        self.siren_disarm = False
        self.testing = False
        self.entry = entry

        self.first_time = True
        self.login_success = False
        self.device_registry = dr.async_get(hass)
        #self.saved_config = self._grab_config_from_entry()
        self._remove_dummy_listener = self.async_add_listener(self._dummy_listener)

        self._event_logger.logstate_info(f"update_interval={ui}")
        self.cloud_alarm: AlarmSystem | None = None

    def _dummy_listener(self):
        """Dummy callback to add at least 1 listener so it schedules updates."""
        #self._event_logger.logstate_info("Dummy listener called")

    def save_connection_entry(self):
        """Save connection entry so it is reused."""
        if self.cloud_alarm and self.cloud_alarm.api:
            appid = self.config_entry.data.get(CONF_CLOUD_APP_ID)
            # Save the appid if it doesn't exist or has changed
            if appid is None or appid != self.cloud_alarm.api.app_id:
                new_data = deepcopy(dict(self.config_entry.data))
                new_data[CONF_CLOUD_APP_ID] = self.cloud_alarm.api.app_id
                # This saves the app id token to HA storage so it can be reused
                update_config_entry_threadsafe(
                    hass = self.hass,
                    entry = self.config_entry,
                    data = new_data,
                )

    async def _async_update_data(self) -> VisonicCoordinatorData:
        """Override the parent function."""
        _state_snapshot = None
        try:
            self._service_image_queue()
            _state_snapshot = await self.create_state_snapshot()
            if self.state_changed_callback:
                self.state_changed_callback()
        except Exception as err:
            raise UpdateFailed(str(err)) from err
        else:
            return _state_snapshot

    def ive_been_created(self):
        """Called when certain entities are first initialised to make sure they get the latest data."""
        # Needs to be implemented as it gets called, but no action to take

    #def _grab_config_from_entry(self) -> list[str]:
    #    # These are the parameters that are used to authenticate and login to the remote server
    #    return [
    #        self.config_entry.data.get(CONF_PANEL_SERIAL),
    #        self.config_entry.data.get(CONF_EMAIL),
    #        self.config_entry.data.get(CONF_PASSWORD),
    #        self.config_entry.data.get(CONF_CODE),
    #    ]

    @property
    def update_interval(self) -> timedelta | None:
        """Interval between updates."""
        _update_interval = self.entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_CLOUD_SCAN_INTERVAL)
        self._update_interval = timedelta(seconds=_update_interval)
        self._update_interval_seconds = self._update_interval.total_seconds()
        return self._update_interval

    @update_interval.setter
    def update_interval(self, value: timedelta | None) -> None:
        """Set interval between updates."""
        self.partition_list : set[int] = set()
        self._update_interval = value
        self._update_interval_seconds = self._update_interval.total_seconds()

    async def authenticate(self):
        """Authenticate with the panel."""
        session = async_get_clientsession(self.hass)
        uuid_key = self.config_entry.data.get(CONF_CLOUD_APP_ID)
        if not uuid_key:
            # If an app ID has not been created then create one
            uuid_key = str(uuid.uuid4())
        api = API(session, self.config_entry.data.get(CONF_EXTERNAL_URL, ""), uuid_key)
        # Ensure the REST version is set so the URLs are valid
        await api.set_rest_version()
        if await api.authenticate_user(
            self.config_entry.data.get(CONF_EMAIL, ""),
            self.config_entry.data.get(CONF_PASSWORD, "")
        ):
            return api  # return the api
        return None

    def find_status(self, partition: int, status: dict) -> dict[str, Any]:
        """Find status."""
        for p in status["partition"]:
            if int(p.get("id")) == partition + 1:
                return p
        return {}

    async def async_panel_stop(self):
        """Disconnect from the cloud visonic server."""
        self.async_set_updated_data(None)  # clears update cycle
        if self._remove_dummy_listener:
            self._remove_dummy_listener()
            self._remove_dummy_listener = None
        if self.login_success and self.cloud_alarm:
            #await self.cloud_alarm.panel_logout()
            self.login_success = False

    async def _extract_model_and_type(self) -> str:
        panel_model = None
        panel_type = None
        panel_serial: str | None = self.config_entry.data.get(CONF_PANEL_SERIAL)
        panels = await self.cloud_alarm.api.get_panels()   # a list of dict, 1 dict per panel
        if self.panel_id < len(panels) and (not panel_serial or len(panel_serial) != 6):
            p: dict[str, Any] = panels[self.panel_id]
            panel_serial = p.get(CONF_PANEL_SERIAL)
        if len(panels) > 0 and len(panel_serial) == 6:
            # find the panel_serial and extract needed information
            for panel in panels:
                p: dict[str, Any] = panel
                if p.get(CONF_PANEL_SERIAL, "").upper() == panel_serial.upper():
                    panel_model = p.get(TEXT_PANEL_MODEL)
                    panel_type = p.get(CONF_TYPE)
                    new_data = deepcopy(dict(self.config_entry.data))
                    new_data[CONF_PANEL_SERIAL] = panel_serial
                    new_data[TEXT_PANEL_MODEL] = panel_model
                    # This saves the tokens to the hidden HA storage files
                    update_config_entry_threadsafe(
                        hass = self.hass,
                        entry = self.config_entry,
                        data = new_data,
                        title = f"{VISONIC_CLOUD_SERVER} - Panel {self.panel_id}, Serial {panel_serial}, {panel_model}",
                    )
                    break
        self.panel_model = "Unknown" if panel_model is None else panel_model
        self.panel_type = "Unknown" if panel_type is None else panel_type
        return panel_serial

    def get_diagnostic_data(self) -> dict[str, Any]:
        """Build and return the diagnostics data for this panel."""
        return {}

    def hasStarted(self) -> bool:
        """Has the system started?"""
        return True

    async def wait_for_process_status(self, process_token, attempts = 5) -> AlarmCommandStatus:
        """Check the process status for up to 5 seconds."""
        if process_token:
            while attempts > 0:
                attempts -= 1
                await asyncio.sleep(1.0)
                processes = await self.cloud_alarm.get_process_status(process_token)
                self._event_logger.logstate_info("          status %s", processes)
                for process in processes:
                    self._event_logger.logstate_info(f"          process {process}")
                    if process.status == "handled":
                        return AlarmCommandStatus.SUCCESS
                    if process.status == "failed":
                        return AlarmCommandStatus.FAIL_INVALID_STATE
                    self._event_logger.logstate_info("             process return status not failed or handled, it is %s   message %s   error %s", process.status, process.message, process.error)
                    return AlarmCommandStatus.FAIL_INVALID_RETURN
        return AlarmCommandStatus.FAIL_INVALID_PROCESS_TOKEN

    # the return value indicates whether any sensors needed to be bypassed
    async def send_command(
        self,
        name: str,
        command: AlarmPanelCommand,
        code: str | None,
        partition_set: set[int] | None,   # needs to already be 0 based
    ) -> CommandResult:
        """Common send command function."""
        acs = AlarmCommandStatus.FAIL_INVALID_RETURN
        part = PARTITION_ID_WHEN_BASE if partition_set is None or len(partition_set) == 3 or partition_set == self.partition_list else list(partition_set)[0] + 1
        did_bypass = False
        if self.cloud_alarm:
            conf_disarm = to_bool(self.config_entry.options.get(CONF_ENABLE_REMOTE_DISARM, False))
            conf_remote_arm = to_bool(self.config_entry.options.get(CONF_ENABLE_REMOTE_ARM, False))
            conf_arm_home = to_bool(self.config_entry.options.get(CONF_ARM_HOME_ENABLED, False))
            process_token = None
            user_settings_prevented = False
            panel_settings_prevented = False
            match command:
                case AlarmPanelCommand.ARM_AWAY_BYPASS:
                    if conf_remote_arm:
                        result = await self.bypass_open_zones(part)
                        if result.status != AlarmCommandStatus.SUCCESS:
                            return result
                        did_bypass = True
                        process_token = await self.cloud_alarm.arm_away(part)
                    else:
                        user_settings_prevented = True
                case AlarmPanelCommand.ARM_HOME_BYPASS:
                    if conf_remote_arm and conf_arm_home:
                        result = await self.bypass_open_zones(part)
                        if result.status != AlarmCommandStatus.SUCCESS:
                            return result
                        did_bypass = True
                        process_token = await self.cloud_alarm.arm_home(part)
                    else:
                        user_settings_prevented = True
                case AlarmPanelCommand.DISARM:
                    if conf_disarm:
                        process_token = await self.cloud_alarm.disarm(part)
                    else:
                        user_settings_prevented = True
                case AlarmPanelCommand.ARM_AWAY:
                    if conf_remote_arm:
                        process_token = await self.cloud_alarm.arm_away(part)
                    else:
                        user_settings_prevented = True
                case AlarmPanelCommand.ARM_HOME:
                    if conf_remote_arm and conf_arm_home:
                        process_token = await self.cloud_alarm.arm_home(part)
                    else:
                        user_settings_prevented = True
                case AlarmPanelCommand.ARM_AWAY_INSTANT:
                    if conf_remote_arm:
                        process_token = await self.cloud_alarm.arm_away_instant(part)
                    else:
                        user_settings_prevented = True
                case AlarmPanelCommand.ARM_HOME_INSTANT:
                    if conf_remote_arm and conf_arm_home:
                        process_token = await self.cloud_alarm.arm_home_instant(part)
                    else:
                        user_settings_prevented = True
                case AlarmPanelCommand.TRIGGER | AlarmPanelCommand.FIRE | AlarmPanelCommand.EMERGENCY | AlarmPanelCommand.PANIC:
                    if self.siren_arm:
                        process_token = await self.cloud_alarm.activate_siren()
                    else:
                        panel_settings_prevented = True
                case AlarmPanelCommand.MUTE:
                    if self.siren_disarm:
                        process_token = await self.cloud_alarm.disable_siren()
                    else:
                        panel_settings_prevented = True

            if user_settings_prevented:
                acs = AlarmCommandStatus.FAIL_USER_CONFIG_PREVENTED
            elif panel_settings_prevented:
                acs = AlarmCommandStatus.FAIL_PANEL_CONFIG_PREVENTED
            elif process_token:
                acs = await self.wait_for_process_status(process_token)

        return CommandResult(
            acs,
            AvailableNotifications.COMMAND,
            message=f"Sent Command success {command}   name {name}" if acs == AlarmCommandStatus.SUCCESS else f"Failed to send command {command}   name {name}",
            partitions=part,
            did_bypass=did_bypass
        )

    async def send_bypass(
        self,
        devid: int,
        bypass: bool,
        code: str | None,
    ) -> CommandResult:
        """Send bypass command."""
        acs = AlarmCommandStatus.FAIL_INVALID_RETURN
        if self.cloud_alarm:
            process_token = await self.cloud_alarm.set_bypass_zone(devid, bypass)
            if process_token:
                acs = await self.wait_for_process_status(process_token)
        return CommandResult(
            acs,
            AvailableNotifications.BYPASS,
            message=f"Sent Bypass success, zone {devid}   bypass {bypass}" if acs == AlarmCommandStatus.SUCCESS else f"Failed to send bypass zone {devid}   bypass {bypass}"
        )

    async def send_switch(self, devid: int, command: AlarmSwitchCommand) -> CommandResult:
        """Set the Switch/PGM switch."""
        self._event_logger.create_ha_notification(
            AvailableNotifications.SWITCH,
            f"Failed Attempt to set switch device for panel {self.panel_id}, device {devid} Not supported by interface",
        )
        return CommandResult(
            AlarmCommandStatus.FAIL_INVALID_STATE, AvailableNotifications.SWITCH, f"Send SWITCH {command} to device {devid}"
        )
#        result: CommandResult = await self.__client.send_switch(devid, command)
#        if result.status != AlarmCommandStatus.SUCCESS:         # AlarmCommandStatus
#            self._event_logger.create_ha_notification(
#                result.notify,
#                f"Failed Attempt to set switch device for panel {self.panel_id}, device {devid} {result.message}",
#            )
#        return result

    def get_panel_pin_code_simple(self, code: str | None):
        """Get code code."""
        if code is None or len(code) != 4:
            return True, self.cloud_alarm.get_user_code()
        return True, code

    async def async_panel_connect(self) -> bool:
        """Connect to the cloud visonic server."""
        # Initialize API
        try:
            api = await self.authenticate()
            if api:
                self._event_logger.logstate_info("panel auth success")
                self.cloud_alarm = AlarmSystem(api)
                self.panel_serial = await self._extract_model_and_type()
                # Login to the Panel
                if await self.cloud_alarm.panel_login(self.panel_serial, self.config_entry.data.get(CONF_CODE, "")):
                    self._event_logger.logstate_info("panel login success")
                    self.login_success = True
                    # The login details are new so save them
                    self.save_connection_entry()
                    #self.async_update_listeners()
                    await self.async_request_refresh()
                    return True
            # If authentication fails then fail to load this hub
            self._event_logger.logstate_info("panel connection failure")
        except WrongUsernameOrPasswordError as ex:
            raise VisonicAuthException("Cloud Authentication Error - panel connection failure") from ex
        except Exception as ex:
            raise VisonicException("Cloud Connection Error - panel connection failure") from ex
        return False

    async def async_service_panel_reconnect(self, call: ServiceCall | None):
        """Service call to re-connect the comms connection."""
        await self.async_panel_connect()

    async def send_get_event_log(
        self, isValidPL: bool, code: str | None
    ) -> CommandResult:
        """Send get event log."""
        events: list[Event] = await self.cloud_alarm.get_events()
        success = True

        for count, event in enumerate(events, start=1):
            try:
                self.platform_manager.panel_event_log.process_panel_event_log(
                    total=len(events),
                    l_current=count,
                    partition_val=event.partitions,
                    dateandtime=datetime.strptime(event.datetime, '%Y-%m-%d %H:%M:%S'),
                    zoneStr=event.appointment,
                    eventStr=event.description,
                )
            except (ValueError, OSError, KeyError, TypeError):
                success = False
                continue
        return CommandResult(
            AlarmCommandStatus.SUCCESS if success else AlarmCommandStatus.FAIL_INVALID_RETURN,
            AvailableNotifications.EVENTLOG,
            "Send get event log"
        )

    async def send_command_sensor_image(self, devid: int | None, eid: str | None, duration: int) -> AlarmCommandStatus:
        """Send the command to the panel to get a camera image."""
        acs = AlarmCommandStatus.FAIL_INVALID_RETURN
        _LOGGER.warning("Sensor Images are not currently implemented in the Visonic integration.")
        #if self.cloud_alarm:
        #    process_token = await self.cloud_alarm.make_video(devid)
        #    acs = await self.wait_for_process_status(process_token)
        return acs

    def set_partition_name(
        self, partition: int | None = None, panel_entity_name: str | None = None
    ):
        """Set the partition naming for the alarm panel entities."""
        if (
            panel_entity_name is not None
            and partition is not None
            and 0 <= partition <= 2
        ):
            self.panel_entity_name[partition] = panel_entity_name

    def get_state_snapshot(self) -> VisonicCoordinatorData:
        """Return complete snapshot of current state."""
        return self.data

    def _determine_armcode(self, p : Partition) -> AlarmPanelStatus:
        # p.status can be "EXIT" or "" I think
        key = p.status + "_" + p.state if len(p.status) > 0 else p.state
        return MAP_ALARM_STATUS.get(key, AlarmPanelStatus.UNKNOWN)

    def _determine_siren_state(self, ac: AlarmPanelStatus, al: TriggerAlarmType, zone: int):
        if ac in PANEL_ARMED_LIST and al == TriggerAlarmType.INTRUDER:
            return (True, zone, TriggerAlarmType.INTRUDER)
        if al != TriggerAlarmType.INTRUDER:
            return (al != TriggerAlarmType.NONE, zone, al)
        return (False, -1, TriggerAlarmType.NONE)

    async def create_state_snapshot(self) -> VisonicCoordinatorData:
        """Fetch data from Visonic API with auto-reauth."""

        def determine_most_recent(alarms: list[Alarm]) -> Alarm:
            retval = None
            for alarm in alarms:
                if retval is None or retval.date_time < alarm.date_time:
                    retval = alarm
            return retval


        def construct_partition_data(p: Partition) -> dict[str, Any]:
            """Construct partition data."""
            return {
                "state": self._determine_armcode(p).name.lower(),
                "ready": p.ready,
                "partition": p.id,
                #"tamper": ,
                #"memory": ,
                #"siren": ,
                #"bypass": ,
                #"alarm": ,
                #"trouble": ,
                #"battery_level": ,
            }

        try:

            if self.cloud_alarm is None:
                return VisonicCoordinatorData(
                    connected=False,
                    mode="unknown",
                    #ident=getAlarmPanelUniqueIdent(self.panel_id)
                )

            # Notes:
            ### alerts: Empty list when I tried
            ###         alerts = await self.cloud_alarm.get_alerts()
            ### alarms: Empty list when I tried
            ###         alarms = await self.cloud_alarm.get_alarms()
            ### cameras: More detail than Devices get_devices()
            ###         Will probably need it to get images

#            if not self.testing:
#                self.saved_cameras = await self.cloud_alarm.get_cameras()
#                self.testing = True
#                process_token = await self.cloud_alarm.make_video(1)
#                acs = None
#                if process_token:
#                    acs = await self.wait_for_process_status(process_token)
#            else:
#                cameras = await self.cloud_alarm.get_cameras()
#                if cameras != self.saved_cameras:
#                    self._event_logger.logstate_info(f"Diffed {cameras}")


            ### feature_set: a bit more data than PanelInfo features but not worth it
            ###         feature_set = await self.cloud_alarm.get_feature_set()
            ### locations: A list of 30 locations, Hall, Kitchen etc and whether they are editable
            ###         locations = await self.cloud_alarm.get_locations()
            ### users: includes stuff like email etc but not worth it
            ###         users = await self.cloud_alarm.get_users()
            ### troubles: a bit more detail than PanelDevice warnings, but basically the same
            ###         troubles = await self.cloud_alarm.get_troubles()

            # Call this and save to xml and csv files.
            #events = await self.cloud_alarm.get_events(timestamp_hour_offset=1)

            # Attempt to get status using current session
            status = await self.cloud_alarm.get_status()
            self._event_logger.logstate_info(f"Updating data - visonic cloud {"is" if status.connected else "is not"} connected to panel")
            if not status.connected:
                return VisonicCoordinatorData(
                    connected=False,
                    mode="unknown",
                    #ident=getAlarmPanelUniqueIdent(self.panel_id)
                )

            panel_info: PanelInfo = await self.cloud_alarm.get_panel_info()
            partition_list: set[int] = set()
            if panel_info.multi_partitions:
                for partition in panel_info.partitions:
                    if partition.active and partition.id > 0:  # 1,2,3,-1   -1 is "ALL" partitions
                        partition_list.add(partition.id - 1)
            else:
                partition_list = None

            devices: list[Device] = await self.cloud_alarm.get_devices()
            panelstate: PanelState = self.process_devices(devices=devices, partition_list=partition_list)

            alarms: list[Alarm] = await self.cloud_alarm.get_alarms()
            #troubles: list[Trouble] = await self.cloud_alarm.get_troubles()
            #alerts = await self.cloud_alarm.get_alerts()
            #events: list[Event] = await self.cloud_alarm.get_events()
            #troubles: list[Trouble] = await self.cloud_alarm.get_troubles()
            #sd = await self.cloud_alarm.get_smart_devices()
            #sds = await self.cloud_alarm.get_smart_devices_settings()
            #users = await self.cloud_alarm.get_users()
            #pai = await self.cloud_alarm.api.get_panel_access_info(self.panel_serial)
            #tst = await self.cloud_alarm.api.do_testing(self.panel_serial)
            #fs: FeatureSet = await self.cloud_alarm.get_feature_set()
            #if fs.home_automation_devices_enabled:
            #    ad = await self.cloud_alarm.get_auto_devices()


            indices = range(3)
            show_keypad: dict[int, bool] = {}
            code_arm_required: dict[int, bool] = {}
            for i in indices:
                show_keypad[i] = False
                code_arm_required[i] = False

            self.siren_arm = panel_info.features.enabling_siren
            self.siren_disarm = panel_info.features.disabling_siren
            partition_armcode: dict[int, AlarmPanelStatus] = {}
            partition_dict: dict[int, dict[str, Any]] = {}
            partition_siren = dict.fromkeys(indices, (False, -1, TriggerAlarmType.NONE))

            most_recent_alarm = None
            if len(alarms) > 0:
                most_recent_alarm: Alarm = determine_most_recent(alarms)
#                self._event_logger.logstate_info(f"Whooooo, received alarms from the panel {alarms}")

            if panel_info.multi_partitions:
                for part in status.partitions:
                    p : Partition = part
                    if p.id:
                        ac = self._determine_armcode(p)
                        partition_armcode[p.id-1] = ac
                        partition_dict[p.id-1] = construct_partition_data(p)
                        _LOGGER.info(f"Partition {p.id} is {ac.name}")  # noqa: G004
                        if most_recent_alarm is not None and p.id in most_recent_alarm.partitions:
                            al = MAP_ALARM_TYPE.get(most_recent_alarm.alarm_type, TriggerAlarmType.NONE)
                            zone = most_recent_alarm.zone if most_recent_alarm.zone is not None else -1
                            partition_siren[p.id-1] = self._determine_siren_state(ac, al, zone)
            else:
                # Does partition 0 represent the main panel when there are no partitions set?
                partition_armcode[0] = self._determine_armcode(status.partitions[0])
                if most_recent_alarm is not None:
                    al = MAP_ALARM_TYPE.get(most_recent_alarm.alarm_type, TriggerAlarmType.NONE)
                    zone = most_recent_alarm.zone if most_recent_alarm.zone is not None else -1
                    partition_siren[0] = self._determine_siren_state(partition_armcode[0], al, zone)
                    _LOGGER.info(f"     Setting Siren Data {partition_siren[0]}")  # noqa: G004
                _LOGGER.info(f"Main Panel is {partition_armcode[0].name}")  # noqa: G004
                partition_dict[0] = construct_partition_data(status.partitions[0])

            new_data = VisonicCoordinatorData(
                connected=status.connected,
                ispowermaster=True,
                mode="cloud",
                model=self.config_entry.title, # .panel_model,
                statusdict={
                    TEXT_DISCONNECTION_COUNT: 0
                },
                panelstate=panelstate,
                partition_show_keypad=show_keypad,
                partition_code_arm_required=code_arm_required,
                partition_armcode=partition_armcode,
                partition_siren=partition_siren,
                partition_dict=partition_dict,
                zones=self.platform_manager.sensor_state(),
                switch=self.platform_manager.switch_state(),
                device=self.platform_manager.device_state(),
            )
            if self.first_time:
                self._event_logger.logstate_info("Visonic setting up alarm panels")
                self.first_time = False
                self.async_set_updated_data(new_data)
                self.platform_manager.set_alarm_device_information(self.config_entry.title)  # (self.panel_model)
                await self.platform_manager.async_setup_alarm_panel(partition_list)
            else:
                self.platform_manager.rationalise_ha_devices(False)
            return new_data  # noqa: TRY300

        except UnauthorizedError:
            # Session expired, attempt to re-authenticate
            self._event_logger.logstate_info("Visonic session expired, re-authenticating...")
            await self.async_panel_connect()

        except Exception as err:
            raise UpdateFailed(f"Error communicating with Visonic Cloud: {err}") from err

        return VisonicCoordinatorData(
            connected=False,
            #ident=getAlarmPanelUniqueIdent(self.panel_id),
            mode="cloud"
        )

    def _as_sensor_state(self, device: Device) -> SensorState:
        loc = device.location.lower() if device.location else "undefined"
        zonetype = device.zone_type_name.lower() if device.zone_type_name else "undefined"

        triggered = False
        temperature = None
        luminance = None

        if isinstance(device, ContactDevice):
            sensor_type_id = 5
            if device.subtype == "HW_ZONE_CONNECTED_DIRECTLY_TO_THE_PANEL":
                # Wired, so no battery
                sensor_type = ZoneSensorDetails("Wired", AlarmSensorType.MAGNET, STATUS_ONLY_TIMEOUT )
            else:
                sensor_type = ZoneSensorDetails("Contact", AlarmSensorType.MAGNET, BATTERY_AND_STATUS_TIMEOUT )
            triggered = device.state

        elif isinstance(device, SmokeDevice):
            sensor_type_id = 10
            sensor_type = ZoneSensorDetails("Smoke", AlarmSensorType.SMOKE, BATTERY_AND_TRIGGER_OTHER )

        elif isinstance(device, CameraDevice):
            sensor_type_id = 16
            sensor_type = ZoneSensorDetails("Camera", AlarmSensorType.CAMERA, BATTERY_AND_TRIGGER_MOTION )

        elif isinstance(device, ShockDevice):
            sensor_type_id = 0x35
            sensor_type = ZoneSensorDetails("Shock", AlarmSensorType.SHOCK, BATTERY_TRIGGER_STATUS )

        elif isinstance(device, MotionDevice):
            sensor_type_id = 3
            sensor_type = ZoneSensorDetails("Motion", AlarmSensorType.MOTION, BATTERY_AND_TRIGGER_MOTION )
            temperature = device.temperature
            luminance = device.brightness
        else:
            sensor_type_id = 0
            sensor_type = ZoneSensorDetails("Unknown", AlarmSensorType.UNKNOWN, BASIC_STATUS )

        return SensorState(
            id=device.device_number,
            problem=device.trouble,
            sensor_type_id=sensor_type_id,
            #sensor=device.device_number,
            partition=device.partitions,
            location=(loc, loc),
            zonetype=zonetype,
            #model=device.name,
            chime="chime_off",
            bypass=device.bypass,
            low_battery=device.low_battery,
            status=device.state,
            tamper=device.tamper,
            enabled=not device.preenroll and bool(device.enrollment_id),  # fully enrolled and a valid id
            triggered=triggered,
            zonetamper=device.tamper,
            temperature=temperature,
            luminance=luminance,
            ismissing=None,
            isoneway=None,
            isinactive=None,
            #offtime=None,
            sensor_type = sensor_type,
            has_image=False,
            image_time=None,
            time=None,
        )

    def _as_switch_state(self, device: Device) -> SwitchState:
        loc = device.location.lower() if device.location else "undefined"
        return SwitchState(
            id=device.device_number,
            status=True,
            enabled=True,
            model="Undefined",
            location=loc,
        )

    def _as_device_state(self, device: Device) -> DeviceState:
        loc = device.location.lower() if device.location else "undefined"
        return DeviceState(
            id=device.device_number,
            enabled=True,
            model=f"{device.device_type} ({device.enrollment_id})",
            location=loc,
            status=device.state,
            low_battery=device.low_battery,
            trouble=device.trouble,
            tamper=device.tamper,
            partitions=device.partitions,
            bypass=device.bypass,
        )

    def _get_sensor_jpeg(self, sensor_id: int) -> bytearray | None:
        """Get the binary image data from a camera sensor."""
        return None

    def process_devices(self, devices: list[Device], partition_list: set[int] | None ) -> PanelState:
        """Process the devices from the panel."""
        try:
            # Set default values, att
            panelstate = PanelState(
                emulationmode="cloud",
                trouble="none",
                battery_level=100,
                tamper=False,
            )
            for device in devices:
                if isinstance(device, (ContactDevice, MotionDevice, SmokeDevice, CameraDevice)):
                    sensor: SensorState = self._as_sensor_state(device)
                    if sensor.enabled:
                        self.platform_manager.sensor_update_or_create(sensor)
                    else:
                        self._event_logger.logstate_info(f"Sensor Device Not Enrolled {device}")

                elif isinstance(device, GenericDevice):
                    self._event_logger.logstate_info(f"Generic Device {device.device_type}  {device}")

                elif isinstance(device, KeyFobDevice):
                    #self._event_logger.logstate_info(f"Keyfob Device {device.device_type}")
                    keyfob: DeviceState = self._as_device_state(device)
                    if keyfob.enabled:
                        self.platform_manager.device_update_or_create(keyfob)
                    else:
                        self._event_logger.logstate_info(f"Keyfob Device Not Enabled {device}")

                elif isinstance(device, ShockDevice):
                    sensor: SensorState = self._as_sensor_state(device)
                    if sensor.enabled:
                        self.platform_manager.sensor_update_or_create(sensor)
                    else:
                        self._event_logger.logstate_info(f"Shock Sensor Device Not Enrolled {device}")

                elif isinstance(device, PGMDevice):
                    self._event_logger.logstate_info(f"PGM Device {device.device_type}  {device}")
                # Can't control it so no point in creating it, there's no other entities
                #    switch: SwitchState = self._as_switch_state(device)
                #    if switch.enabled:
                #        self.platform_manager.switch_update_or_create(switch)
                #    else:
                #        self._event_logger.logstate_info(f"Switch Device Not Enabled {device}")

                elif isinstance(device, PanelDevice):
                    # use the current used partitions and not the total possible in the panel
                    panel: PanelDevice = device
                    #self._event_logger.logstate_debug(f"panel warnings {panel.warnings}")
                    panelstate.battery_level = 0 if panel.low_battery else 100
                    panelstate.tamper = panel.tamper
                    panelstate.trouble = panel.trouble
                    panelstate.partition = None if partition_list is None else {p + 1 for p in partition_list}
        except Exception as ex:  # noqa: BLE001
            tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
            self._event_logger.logstate_error("Exception in processing cloud device data. %s", tb_str)
        else:
            return panelstate
#        return {}
