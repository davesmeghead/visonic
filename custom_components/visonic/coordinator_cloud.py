"""Visonic Coordinator Cloud class."""

import asyncio
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta
import logging
import traceback
from typing import Any
import uuid

from pyvisonicalarm.alarm import AlarmSystem, PanelInfo
from pyvisonicalarm.classes import Alarm, Event, Partition
from pyvisonicalarm.core import API
from pyvisonicalarm.device_definitions import get_device_definition
from pyvisonicalarm.devices import (
    CameraDevice,
    ContactDevice,
    Device,
    FloodDevice,
    KeyFobDevice,
    KeypadDevice,
    MotionDevice,
    PanelDevice,
    PGMDevice,
    RepeaterDevice,
    ShockDevice,
    SirenDevice,
    SmokeDevice,
)
from pyvisonicalarm.exceptions import (
    Error,
    UnauthorizedError,
    WrongUsernameOrPasswordError,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_CODE,
    CONF_EMAIL,
    CONF_EXTERNAL_URL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr  # , entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import (
    CONF_ARM_HOME_ENABLED,
    CONF_CLOUD_APP_ID,
    CONF_ENABLE_REMOTE_ARM,
    CONF_ENABLE_REMOTE_DISARM,
    CONF_ENABLE_SENSOR_BYPASS,
    # CONF_FORCE_KEYPAD,
    CONF_PANEL_SERIAL,
    DEFAULT_CLOUD_SCAN_INTERVAL,
    PARTITION_ID_WHEN_BASE,
    PIN_REGEX,
    TEXT_DISCONNECTION_COUNT,
    TEXT_LAST_ALARM_TRIGGER_SOURCES,
    TEXT_LAST_EVENT_ACTION,
    TEXT_LAST_EVENT_NAME,
    TEXT_LAST_EVENT_PARTITION,
    TEXT_LAST_EVENT_TIME,
    TEXT_PANEL_MODEL,
    VISONIC_CLOUD_SERVER,
)
from .coordinator_base import VisonicCoordinator
from .exceptions import VisonicAuthException, VisonicException
from .log_events import logEvents
from .utils import (
    get_utc_time,
    print_partition,
    to_bool,
    update_config_entry_threadsafe,
)
from .visonic_data_types import VisonicCoordinatorData
from .visonic_entity_types import (
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
from .visonic_types import (
    AlarmCommandStatus,
    AlarmPanelCommand,
    AlarmPanelStatus,
    AlarmSwitchCommand,
    AvailableNotifications,
    CommandResult,
    PanelCondition,
    TriggerAlarmType,
)

_LOGGER = logging.getLogger(__name__)
PANEL_INFO_REFRESH_INTERVAL = timedelta(minutes=30)
CLOUD_DIAGNOSTIC_REFRESH_INTERVAL = timedelta(minutes=5)


def _normalize_trouble(value: bool | int | str | None) -> str:
    """Normalize a cloud trouble value while preserving active trouble codes."""
    return str(value) if to_bool(value) else "none"


MAP_ALARM_STATUS = {
    "DISARM": AlarmPanelStatus.DISARMED,
    "AWAY": AlarmPanelStatus.ARMED_AWAY,
    "HOME": AlarmPanelStatus.ARMED_HOME,
    "EXIT_AWAY": AlarmPanelStatus.ARMING_AWAY,
    "EXIT_HOME": AlarmPanelStatus.ARMING_HOME,
    "ENTRY_DELAY": AlarmPanelStatus.ENTRY_DELAY,
    "ENTRYDELAY": AlarmPanelStatus.ENTRY_DELAY,
    "PROGRAMMING": AlarmPanelStatus.INSTALLER,
    "ALARM": AlarmPanelStatus.TRIGGERED,  # TODO: check these
}

MAP_ALARM_TYPE = {
    "EMERGENCY": TriggerAlarmType.EMERGENCY,
    "FIRE": TriggerAlarmType.FIRE,
    "PANIC": TriggerAlarmType.PANIC,
    "ALARM_IN_MEMORY": TriggerAlarmType.INTRUDER,
}

# These are used to create the lists below
TAMPER_NO_TIMEOUT = (VisonicBinarySensorKey.ZONE_TAMPER, SensorOnTimeout.NO_TIMEOUT)
PROBLEM_NO_TIMEOUT = (VisonicBinarySensorKey.ZONE_PROBLEM, SensorOnTimeout.NO_TIMEOUT)
BATTERY_NO_TIMEOUT = (VisonicBinarySensorKey.ZONE_BATTERY, SensorOnTimeout.NO_TIMEOUT)
STATUS_TIMEOUT = (VisonicBinarySensorKey.ZONE_STATUS, SensorOnTimeout.STATE)
CONTACT_TIMEOUT = (VisonicBinarySensorKey.ZONE_CONTACT, SensorOnTimeout.STATE)
TRIGGER_MOTION = (VisonicBinarySensorKey.ZONE_TRIGGER, SensorOnTimeout.MOTION)
TRIGGER_OTHER = (VisonicBinarySensorKey.ZONE_TRIGGER, SensorOnTimeout.OTHER)
TEMP_NO_TIMEOUT = (VisonicFloatSensorKey.ZONE_TEMP, SensorOnTimeout.NO_TIMEOUT)
LUX_NO_TIMEOUT = (VisonicFloatSensorKey.ZONE_LUX, SensorOnTimeout.NO_TIMEOUT)

# Create the lists of entities used for each sensor type.  This gives flexibility as sensors are added to define the HA entities.
#   All sensors have TAMPER and PROBLEM entities (so these are not in the variable names)
#   Trigger and Status cannot appear in the same setting row (as they are both called "Zone")
#   Trigger and Contact are in the same row for SHOCK sensors that have both trigger and state, CONTACT is used as a different name (i.e. not "Zone")
BASIC_STATUS = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT]
BATTERY_NO_TRIGGER = [
    TAMPER_NO_TIMEOUT,
    PROBLEM_NO_TIMEOUT,
    BATTERY_NO_TIMEOUT,
]
BATTERY_AND_STATUS_TIMEOUT = [
    TAMPER_NO_TIMEOUT,
    PROBLEM_NO_TIMEOUT,
    BATTERY_NO_TIMEOUT,
    STATUS_TIMEOUT,
]
BATTERY_AND_TRIGGER_MOTION = [
    TAMPER_NO_TIMEOUT,
    PROBLEM_NO_TIMEOUT,
    BATTERY_NO_TIMEOUT,
    TRIGGER_MOTION,
]
BATTERY_AND_TRIGGER_OTHER = [
    TAMPER_NO_TIMEOUT,
    PROBLEM_NO_TIMEOUT,
    BATTERY_NO_TIMEOUT,
    TRIGGER_OTHER,
]
# BATTERY_TEMP_LUX              = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT, BATTERY_NO_TIMEOUT, TRIGGER_MOTION, TEMP_NO_TIMEOUT, LUX_NO_TIMEOUT]  # used for camera entries
BATTERY_TRIGGER_STATUS = [
    TAMPER_NO_TIMEOUT,
    PROBLEM_NO_TIMEOUT,
    BATTERY_NO_TIMEOUT,
    TRIGGER_OTHER,
    CONTACT_TIMEOUT,
]  # Trigger and State for SHOCK sensors
STATUS_ONLY_TIMEOUT = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT, STATUS_TIMEOUT]
BATTERY_AND_TEMP = [
    TAMPER_NO_TIMEOUT,
    PROBLEM_NO_TIMEOUT,
    BATTERY_NO_TIMEOUT,
    TEMP_NO_TIMEOUT,
]


class VisonicCloudCoordinator(VisonicCoordinator):
    """Class to manage fetching Visonic data from the cloud API."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        panel_id: int,
        event_logger: logEvents,
    ):
        """Initialize the coordinator."""
        ui = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_CLOUD_SCAN_INTERVAL)
        super().__init__(
            hass,
            entry,
            panel_id=panel_id,
            lo=event_logger,
            update_interval=ui,
            always_update=True,
        )

        self.partition_list: set[int] = set()
        self.siren_arm = False
        self.siren_disarm = False
        self.testing = False
        self.partition_dict: dict[int, dict[str, Any]] = {}
        self._last_cloud_partition_states: dict[int, AlarmPanelStatus] = {}
        self.last_alarm_trigger_source: dict[str, Any] | None = None
        self._last_cloud_event_attributes: dict[str, Any] = {}
        self._logged_cloud_sensor_initial_state: set[int] = set()
        self._logged_cloud_device_type_pairs: set[tuple[str, str]] = set()
        self._cloud_device_type_inventory: dict[tuple[str, str], int] = {}
        self.first_time = True
        self.login_success = False
        self.device_registry = dr.async_get(hass)
        # self.saved_config = self._grab_config_from_entry()
        self._remove_dummy_listener: Callable[[], None] | None = None

        self._event_logger.logstate_info(f"update_interval={ui}")
        self.cloud_alarm: AlarmSystem | None = None
        self._panel_info: PanelInfo | None = None
        self._panel_info_refreshed_at: datetime | None = None
        self._cloud_diagnostic_data_refreshed_at: datetime | None = None
        # Initialise the connection time stamp
        self.last_connected_timestamp: datetime = get_utc_time()
        self.never_connected = True
        self.disconnection_counter = 0

    def _dummy_listener(self):
        """Dummy callback to add at least 1 listener so it schedules updates."""
        # self._event_logger.logstate_info("Dummy listener called")

    def start_polling(self) -> None:
        """Keep polling when no entities are listening to the coordinator."""
        if self._remove_dummy_listener is None:
            self._remove_dummy_listener = self.async_add_listener(self._dummy_listener)

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
                    hass=self.hass,
                    entry=self.config_entry,
                    data=new_data,
                )

    def ive_been_created(self):
        """Called when certain entities are first initialised to make sure they get the latest data."""
        # Needs to be implemented as it gets called, but no action to take

    # def _grab_config_from_entry(self) -> list[str]:
    #    # These are the parameters that are used to authenticate and login to the remote server
    #    return [
    #        self.config_entry.data.get(CONF_PANEL_SERIAL),
    #        self.config_entry.data.get(CONF_EMAIL),
    #        self.config_entry.data.get(CONF_PASSWORD),
    #        self.config_entry.data.get(CONF_CODE),
    #    ]

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
            self.config_entry.data.get(CONF_PASSWORD, ""),
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
            # await self.cloud_alarm.panel_logout()
            self.login_success = False

    async def _extract_model_and_type(self) -> str:
        if self.cloud_alarm is None:
            return ""
        panel_model = None
        panel_type = None
        panel_serial: str | None = self.config_entry.data.get(CONF_PANEL_SERIAL)
        panels = await self.cloud_alarm.api.get_panels()
        if not isinstance(panels, list):
            panels = []
        if self.panel_id < len(panels) and (not panel_serial or len(panel_serial) != 6):
            panel = panels[self.panel_id]
            if isinstance(panel, dict) and isinstance(panel.get("panel_serial"), str):
                panel_serial = panel["panel_serial"]
        if panel_serial and len(panel_serial) == 6:
            for panel in panels:
                if (
                    isinstance(panel, dict)
                    and isinstance(panel.get("panel_serial"), str)
                    and panel["panel_serial"].upper() == panel_serial.upper()
                ):
                    model = panel.get("model")
                    panel_model = model if isinstance(model, str) else None
                    panel_type_value = panel.get("type")
                    panel_type = (
                        panel_type_value if isinstance(panel_type_value, str) else None
                    )
                    new_data = deepcopy(dict(self.config_entry.data))
                    new_data[CONF_PANEL_SERIAL] = panel_serial
                    new_data[TEXT_PANEL_MODEL] = panel_model
                    # This saves the tokens to the hidden HA storage files
                    update_config_entry_threadsafe(
                        hass=self.hass,
                        entry=self.config_entry,
                        data=new_data,
                        title=f"{VISONIC_CLOUD_SERVER} - Panel {self.panel_id}, Serial {panel_serial}, {panel_model}",
                    )
                    break
        self.panel_model = "Unknown" if panel_model is None else panel_model
        self.panel_type = "Unknown" if panel_type is None else panel_type
        return panel_serial or ""

    async def get_diagnostic_data(self) -> dict[str, Any]:
        """Build and return the diagnostics data for this panel."""
        try:
            data = await self.create_state_snapshot()
            # Add sensors, switches, and logs
            return {
                "integration connected": "yes",
                "panel connected": ("yes" if data.connected else "no"),
                "visonic": data.as_dict(convert_to_name=True),
                "cloud_device_types": [
                    {
                        "device_type": device_type,
                        "subtype": subtype,
                        "last_observed_device_count": count,
                        "mapped": get_device_definition(device_type, subtype)
                        is not None,
                    }
                    for (device_type, subtype), count in sorted(
                        self._cloud_device_type_inventory.items()
                    )
                ],
                "history": self._event_logger.get_str_log(),
            }
        except Exception as err:
            raise VisonicException(
                "Get Diagnostic data failed",
                code=400,
                original_exception=err,
            ) from err

    def _record_cloud_device_types(self, devices: list[Device]) -> None:
        """Record anonymized cloud device type and subtype pairs."""
        counts = Counter((device.device_type, device.subtype) for device in devices)
        for (device_type, subtype), count in counts.items():
            self._cloud_device_type_inventory[(device_type, subtype)] = count
            if (device_type, subtype) in self._logged_cloud_device_type_pairs:
                continue
            self._logged_cloud_device_type_pairs.add((device_type, subtype))
            mapped = get_device_definition(device_type, subtype) is not None
            self._event_logger.logstate_debug(
                "Observed cloud device pair: device_type=%s subtype=%s mapped=%s "
                "device_count=%s",
                device_type,
                subtype,
                mapped,
                count,
            )

    def hasStarted(self) -> bool:
        """Has the system started?"""
        return True

    async def wait_for_process_status(
        self, process_token, attempts=5
    ) -> AlarmCommandStatus:
        """Check the process status for up to 5 seconds."""
        if self.cloud_alarm is None:
            return AlarmCommandStatus.FAIL_INVALID_RETURN
        if process_token:
            while attempts > 0:
                attempts -= 1
                await asyncio.sleep(1.0)
                processes = await self.cloud_alarm.get_process_status(process_token)
                self._event_logger.logstate_info(
                    "        process_token %s  response count %s",
                    process_token,
                    len(processes),
                )
                for process in processes:
                    self._event_logger.logstate_info(f"          process {process}")
                    if process.status == "succeeded":
                        return AlarmCommandStatus.SUCCESS
                    if process.status == "handled":
                        return AlarmCommandStatus.SUCCESS
                    if process.status == "failed":
                        return AlarmCommandStatus.FAIL_INVALID_STATE
                    if process.status == "error":
                        return AlarmCommandStatus.FAIL_INVALID_STATE
                    self._event_logger.logstate_info(
                        "             process return status not failed or handled, it is %s   message %s   error %s",
                        process.status,
                        process.message,
                        process.error,
                    )
            return AlarmCommandStatus.FAIL_INVALID_RETURN
        return AlarmCommandStatus.FAIL_INVALID_PROCESS_TOKEN

    def get_panel_pin_code(self, code: str | None) -> tuple[bool, str | None]:
        """Get code code."""
        if self.cloud_alarm is None:
            return False, None
        user_code = self.cloud_alarm.get_user_code()
        if not code or (PIN_REGEX.match(code) and code == user_code):
            return True, user_code
        return False, None

    # the return value indicates whether any sensors needed to be bypassed
    async def send_command(  # noqa: C901
        self,
        entity_id: str,
        command: AlarmPanelCommand,
        code: str | None,
        partition_set: set[int] | None,  # needs to already be 0 based
    ) -> CommandResult:
        """Common send command function."""
        acs = AlarmCommandStatus.FAIL_INVALID_RETURN

        is_valid, _ = self.get_panel_pin_code(code=code)
        if not is_valid:
            return CommandResult(
                AlarmCommandStatus.FAIL_INVALID_CODE,
                AvailableNotifications.COMMAND,
                message="Invalid code",
                panel=PanelCondition.CHECK_ARM_DISARM_COMMAND,
                partitions=partition_set,
                eid=entity_id,
            )

        part = (
            PARTITION_ID_WHEN_BASE
            if partition_set is None
            or len(partition_set) == 3
            or partition_set == self.partition_list
            else list(partition_set)[0] + 1
        )
        did_bypass = False
        if self.cloud_alarm:
            conf_disarm = to_bool(
                self.config_entry.options.get(CONF_ENABLE_REMOTE_DISARM, False)
            )
            conf_remote_arm = to_bool(
                self.config_entry.options.get(CONF_ENABLE_REMOTE_ARM, False)
            )
            conf_arm_home = to_bool(
                self.config_entry.options.get(CONF_ARM_HOME_ENABLED, False)
            )
            process_token = None
            user_settings_prevented = False
            panel_settings_prevented = False
            self._event_logger.logstate_info(f"Sending panel command {command}")
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
                case (
                    AlarmPanelCommand.TRIGGER
                    | AlarmPanelCommand.FIRE
                    | AlarmPanelCommand.EMERGENCY
                    | AlarmPanelCommand.PANIC
                ):
                    if self.siren_arm:
                        process_token = await self.cloud_alarm.activate_siren(
                            mode=command.name.lower()
                        )
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
            elif process_token is not None:
                acs = await self.wait_for_process_status(process_token)

        # schedule an update in 5 seconds time
        self.state_changed_callback(5.0)

        return CommandResult(
            acs,
            AvailableNotifications.COMMAND,
            message=f"Sent Command success {command}   entity_id {entity_id}"
            if acs == AlarmCommandStatus.SUCCESS
            else f"Failed to send command {command}   entity_id {entity_id}",
            partitions=partition_set,
            did_bypass=did_bypass,
            eid=entity_id,
        )

    async def send_bypass(
        self,
        entity_id: str,
        devid: int,
        bypass: bool,
        code: str | None,
    ) -> CommandResult:
        """Send bypass command."""
        acs = AlarmCommandStatus.FAIL_INVALID_RETURN
        esb = to_bool(self.config_entry.options.get(CONF_ENABLE_SENSOR_BYPASS))
        if not esb:
            text = "Bypass" if bypass else "Restore"
            return CommandResult(
                AlarmCommandStatus.FAIL_USER_CONFIG_PREVENTED,
                AvailableNotifications.BYPASS,
                message=f"Sensor {text} State",
                panel=PanelCondition.CHECK_BYPASS_COMMAND,
                eid=entity_id,
            )
        # is_code_valid
        is_valid, _ = self.get_panel_pin_code(code=code)
        if not is_valid:
            text = "Bypass" if bypass else "Restore"
            return CommandResult(
                AlarmCommandStatus.FAIL_INVALID_CODE,
                AvailableNotifications.BYPASS,
                message=f"Sensor {text} State",
                panel=PanelCondition.CHECK_BYPASS_COMMAND,
                eid=entity_id,
            )
        if self.cloud_alarm:
            try:
                self._event_logger.logstate_info(
                    f"Sending panel bypass {devid=} {bypass=}"
                )
                process_token = await self.cloud_alarm.set_bypass_zone(devid, bypass)
                if process_token:
                    acs = await self.wait_for_process_status(process_token)
            except Error as err:
                return CommandResult(
                    AlarmCommandStatus.FAIL_INVALID_RETURN,
                    AvailableNotifications.BYPASS,
                    message=f"Failed to send bypass:   zone={devid}   bypass={bypass}   detail={err}",
                    eid=entity_id,
                )

        # schedule an update in 5 seconds time
        self.state_changed_callback(5.0)
        return CommandResult(
            acs,
            AvailableNotifications.BYPASS,
            message=f"Sent Bypass success, zone {devid}   bypass {bypass}"
            if acs == AlarmCommandStatus.SUCCESS
            else f"Failed to send bypass:   zone={devid}   bypass={bypass}",
            eid=entity_id,
        )

    async def send_switch(
        self, entity_id: str, devid: int, command: AlarmSwitchCommand
    ) -> CommandResult:
        """Set the Switch/PGM switch."""
        # schedule an update in 5 seconds time
        self.state_changed_callback(5.0)
        return CommandResult(
            AlarmCommandStatus.FAIL_PANEL_CONFIG_PREVENTED,
            AvailableNotifications.SWITCH,
            message="Cloud panel switch commands are not supported",
            panel=PanelCondition.CHECK_SWITCH_COMMAND,
            eid=entity_id,
        )

    async def async_panel_connect(self) -> bool:
        """Connect to the cloud visonic server."""
        self._panel_info = None
        self._panel_info_refreshed_at = None
        self._cloud_diagnostic_data_refreshed_at = None
        # Initialize API
        try:
            api = await self.authenticate()
            if api:
                self._event_logger.logstate_info("panel auth success")
                self.cloud_alarm = AlarmSystem(api)
                self.panel_serial = await self._extract_model_and_type()
                # Login to the Panel
                if await self.cloud_alarm.panel_login(
                    self.panel_serial, self.config_entry.data.get(CONF_CODE, "")
                ):
                    self._event_logger.logstate_info("panel login success")
                    self.login_success = True
                    self.platform_manager.setup_cloud_connection_sensors()
                    # The login details are new so save them
                    self.save_connection_entry()
                    # self.async_update_listeners()
                    await self.async_request_refresh()
                    return True
            # If authentication fails then fail to load this hub
            self._event_logger.logstate_info("panel connection failure")
        except WrongUsernameOrPasswordError as ex:
            self.cloud_api_connected = False
            self.panel_cloud_connected = None
            raise VisonicAuthException(
                "Cloud Authentication Error - panel connection failure"
            ) from ex
        except Exception as ex:
            self.cloud_api_connected = False
            self.panel_cloud_connected = None
            raise VisonicException(
                "Cloud Connection Error - panel connection failure"
            ) from ex
        self.cloud_api_connected = False
        self.panel_cloud_connected = None
        return False

    async def async_service_panel_reconnect(self, call: ServiceCall | None):
        """Service call to re-connect the comms connection."""
        connected = False
        try:
            connected = await self.async_panel_connect()
        finally:
            if not connected and self.data is not None:
                self.async_set_updated_data(self.data)

    async def send_get_event_log(self, code: str | None) -> CommandResult:
        """Send get event log."""
        if self.cloud_alarm is None:
            return CommandResult(
                AlarmCommandStatus.FAIL_INVALID_RETURN,
                AvailableNotifications.EVENTLOG,
                message="Cloud connection is unavailable",
                panel=PanelCondition.CHECK_EVENT_LOG_COMMAND,
            )
        is_valid, _ = self.get_panel_pin_code(code=code)
        if not is_valid:
            return CommandResult(
                AlarmCommandStatus.FAIL_INVALID_CODE,
                AvailableNotifications.EVENTLOG,
                message="Invalid code",
                panel=PanelCondition.CHECK_EVENT_LOG_COMMAND,
            )

        self._event_logger.logstate_info("Sending panel get panel event log")
        events: list[Event] = await self.cloud_alarm.get_events()
        sensors = self.platform_manager.sensor_state()
        success = True
        for count, event in enumerate(events, start=1):
            sensor = sensors.get(event.zone) if event.zone is not None else None
            self._event_logger.logstate_info(
                "Cloud event log entry: id=%s type_id=%s label=%s description=%s "
                "device_type=%s zone=%s datetime=%s partitions=%s "
                "matched_sensor_location=%s matched_sensor_type=%s "
                "matched_sensor_status=%s matched_sensor_triggered=%s",
                event.id,
                event.type_id,
                event.label,
                event.description,
                event.device_type,
                event.zone,
                event.datetime,
                event.partitions,
                sensor.location if sensor is not None else None,
                sensor.zonetype if sensor is not None else None,
                sensor.status if sensor is not None else None,
                sensor.triggered if sensor is not None else None,
            )
            try:
                self.platform_manager.panel_event_log.process_panel_event_log(
                    total=len(events),
                    l_current=count,
                    partition_val=event.partitions,
                    dateandtime=datetime.strptime(event.datetime, "%Y-%m-%d %H:%M:%S"),
                    zoneStr=event.appointment,
                    eventStr=event.description,
                )
            except ValueError, OSError, KeyError, TypeError:
                success = False
                continue
        return CommandResult(
            AlarmCommandStatus.SUCCESS
            if success
            else AlarmCommandStatus.FAIL_INVALID_RETURN,
            AvailableNotifications.EVENTLOG,
            "Send get event log",
        )

    async def send_command_sensor_image(
        self, devid: int | None, eid: str | None, duration: int
    ) -> AlarmCommandStatus:
        """Send the command to the panel to get a camera image."""
        acs = AlarmCommandStatus.FAIL_INVALID_RETURN
        _LOGGER.warning(
            "Sensor Images are not currently implemented in the Visonic integration."
        )
        # if self.cloud_alarm:
        #    process_token = await self.cloud_alarm.make_video(devid)
        #    acs = await self.wait_for_process_status(process_token)
        return acs

    def _determine_armcode(self, p: Partition) -> AlarmPanelStatus:
        # p.status can be "EXIT" or "" I think
        state = p.state or ""
        status = p.status or ""
        key = f"{status}_{state}" if status else state
        return MAP_ALARM_STATUS.get(key, AlarmPanelStatus.UNKNOWN)

    def _determine_siren_state(
        self, ac: AlarmPanelStatus, al: TriggerAlarmType, zone: int
    ):
        if al == TriggerAlarmType.INTRUDER:
            if ac == AlarmPanelStatus.TRIGGERED:
                return (True, zone, al)
            return (False, -1, TriggerAlarmType.NONE)
        return (al != TriggerAlarmType.NONE, zone, al)

    def _update_last_alarm_trigger_sources(
        self, alarms: list[Alarm], devices: list[Device]
    ) -> None:
        """Update the most recent zone alarm sources from cloud data."""
        dated_zone_alarms = [
            alarm
            for alarm in alarms
            if alarm.date_time
            and alarm.device_type == "ZONE"
            and alarm.zone is not None
            and alarm.alarm_type in MAP_ALARM_TYPE
        ]
        if dated_zone_alarms:
            latest_zone_alarm = max(
                dated_zone_alarms, key=lambda alarm: alarm.date_time or ""
            )
            devices_by_zone = {
                device.device_number: device
                for device in devices
                if device.device_type == "ZONE"
            }
            device = devices_by_zone.get(latest_zone_alarm.zone)
            self.last_alarm_trigger_source = {
                "zone": latest_zone_alarm.zone,
                "location": latest_zone_alarm.location
                or (device.location if device is not None else None),
                "alarm_type": latest_zone_alarm.alarm_type,
                "datetime": latest_zone_alarm.date_time,
                "partitions": print_partition(latest_zone_alarm.partitions),
            }

        for partition_id, partition_data in self.partition_dict.items():
            partition_data.pop(TEXT_LAST_ALARM_TRIGGER_SOURCES, None)
            if self.last_alarm_trigger_source and str(
                partition_id + 1
            ) in self.last_alarm_trigger_source["partitions"].split(","):
                partition_data[TEXT_LAST_ALARM_TRIGGER_SOURCES] = (
                    self.last_alarm_trigger_source
                )

    async def create_state_snapshot(self) -> VisonicCoordinatorData:
        """Fetch data from Visonic API with auto-reauth."""

        def construct_partition_data(p: Partition) -> dict[str, Any]:
            """Construct partition data."""
            return {
                "state": self._determine_armcode(p).name.lower(),
                "ready": p.ready,
                "partition": p.id,
                # "tamper": ,
                # "memory": ,
                # "siren": ,
                # "bypass": ,
                # "alarm": ,
                # "trouble": ,
                # "battery_level": ,
            }

        try:
            if self.cloud_alarm is None:
                self.cloud_api_connected = False
                self.panel_cloud_connected = None
                self._event_logger.logstate_info(
                    "Updating data - Cloud connection has not been made"
                )
                await self.async_service_panel_reconnect(
                    None
                )  # This should reconnect to the cloud
                if self.cloud_alarm is None:
                    return VisonicCoordinatorData(
                        connected=False,
                        ispowermaster=True,
                        mode="cloud",
                        achieved_powerlink=True,
                        model=self.config_entry.title,  # .panel_model,
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
            # events = await self.cloud_alarm.get_events(timestamp_hour_offset=1)

            timenow = get_utc_time()
            # Attempt to get status using current session
            status = await self.cloud_alarm.get_status()
            response_received = status is not None
            panel_connected = status.connected if status is not None else None
            # A normal return means the authenticated API request succeeded, even
            # when the panel is offline or the response has no usable status.
            self.cloud_api_connected = True
            self.panel_cloud_connected = panel_connected
            self._event_logger.logstate_info(
                "Updating data - cloud status: request_succeeded=%s status_received=%s panel_connected=%s",
                True,
                response_received,
                panel_connected,
            )

            if status is None or not status.connected:
                timeout = min(60, 2.5 * self.update_interval.total_seconds())
                delta_time = timenow - self.last_connected_timestamp
                if self.never_connected or delta_time >= timedelta(seconds=timeout):
                    # not less than 60 seconds, but allow 2 tries at updating before it's considered a timeout
                    timeout = min(90, 4.5 * self.update_interval.total_seconds())
                    if delta_time > timedelta(seconds=timeout):
                        self._event_logger.logstate_info(
                            "Updating data - panel has not reported to the cloud within the timeout"
                        )
                    self._event_logger.logstate_info(
                        "Updating data - panel is not reporting to PowerManage"
                    )
                    return VisonicCoordinatorData(
                        connected=False,
                        ispowermaster=True,
                        mode="cloud",
                        achieved_powerlink=True,
                        model=self.config_entry.title,  # .panel_model,
                    )
                # Here when it's disconnected but within the timeout so return last times data
                d: VisonicCoordinatorData = self.data
                self._event_logger.logstate_info(
                    f"Updating data - returning deepcopy of existing data, connected set to {d.connected}"
                )
                return d

            self.last_connected_timestamp = timenow
            self.never_connected = False

            if (
                self._panel_info_refreshed_at is None
                or timenow - self._panel_info_refreshed_at
                >= PANEL_INFO_REFRESH_INTERVAL
            ):
                self._panel_info = await self.cloud_alarm.get_panel_info()
                self._panel_info_refreshed_at = timenow
            panel_info = self._panel_info
            partition_list: set[int] = set()
            multi_partitions = bool(panel_info and panel_info.multi_partitions)
            if multi_partitions:
                for partition in panel_info.partitions:
                    if (
                        partition.active
                        and partition.id is not None
                        and partition.id > 0
                    ):
                        partition_list.add(partition.id - 1)
            else:
                partition_list = None

            devices: list[Device] = await self.cloud_alarm.get_devices()
            self._record_cloud_device_types(devices)
            panelstate: PanelState = self.process_devices(
                devices=devices, partition_list=partition_list
            )

            alarms: list[Alarm] = await self.cloud_alarm.get_alarms()
            # sd = await self.cloud_alarm.get_smart_devices()
            # sds = await self.cloud_alarm.get_smart_devices_settings()
            # users = await self.cloud_alarm.get_users()
            # pai = await self.cloud_alarm.api.get_panel_access_info(self.panel_serial)
            # tst = await self.cloud_alarm.api.do_testing(self.panel_serial)
            # fs: FeatureSet = await self.cloud_alarm.get_feature_set()
            # if fs.home_automation_devices_enabled:
            #    ad = await self.cloud_alarm.get_auto_devices()

            indices = range(3)
            self.siren_arm = bool(panel_info and panel_info.features.enabling_siren)
            self.siren_disarm = bool(panel_info and panel_info.features.disabling_siren)
            partition_armcode: dict[int, AlarmPanelStatus] = {}
            partition_siren = dict.fromkeys(indices, (False, -1, TriggerAlarmType.NONE))

            dated_alarms = [alarm for alarm in alarms if alarm.date_time]
            most_recent_alarm = max(
                dated_alarms, key=lambda alarm: alarm.date_time or "", default=None
            )
            self._update_last_alarm_trigger_sources(alarms, devices)

            if most_recent_alarm is not None:
                self._event_logger.logstate_info(
                    f"Received alarms from the panel {alarms}"
                )
                sensors = self.platform_manager.sensor_state()
                for alarm in alarms:
                    sensor = sensors.get(alarm.zone) if alarm.zone is not None else None
                    self._event_logger.logstate_info(
                        "Cloud active alarm: datetime=%s alarm_type=%s event_id=%s "
                        "device_type=%s zone=%s zone_name=%s location=%s partitions=%s "
                        "matched_sensor_location=%s matched_sensor_type=%s "
                        "matched_sensor_status=%s matched_sensor_triggered=%s",
                        alarm.date_time,
                        alarm.alarm_type,
                        alarm.event_id,
                        alarm.device_type,
                        alarm.zone,
                        alarm.zone_name,
                        alarm.location,
                        alarm.partitions,
                        sensor.location if sensor is not None else None,
                        sensor.zonetype if sensor is not None else None,
                        sensor.status if sensor is not None else None,
                        sensor.triggered if sensor is not None else None,
                    )

            partitions = status.partitions
            if multi_partitions:
                for part in status.partitions:
                    p: Partition = part
                    if p.id is not None and p.id > 0:
                        ac = self._determine_armcode(p)
                        partition_armcode[p.id - 1] = ac
                        self.partition_dict[p.id - 1] = construct_partition_data(p)
                        _LOGGER.info(f"Partition {p.id} is {ac.name}")  # noqa: G004
                        if most_recent_alarm is not None and p.id in (
                            most_recent_alarm.partitions or []
                        ):
                            al = MAP_ALARM_TYPE.get(
                                most_recent_alarm.alarm_type, TriggerAlarmType.NONE
                            )
                            zone = (
                                most_recent_alarm.zone
                                if most_recent_alarm.zone is not None
                                else -1
                            )
                            partition_siren[p.id - 1] = self._determine_siren_state(
                                ac, al, zone
                            )
            else:
                if partitions:
                    partition_armcode[0] = self._determine_armcode(partitions[0])
                    self.partition_dict[0] = construct_partition_data(partitions[0])
                else:
                    partition_armcode[0] = AlarmPanelStatus.UNKNOWN
                partition_armcode[1] = AlarmPanelStatus.UNKNOWN
                partition_armcode[2] = AlarmPanelStatus.UNKNOWN
                if most_recent_alarm is not None:
                    al = MAP_ALARM_TYPE.get(
                        most_recent_alarm.alarm_type, TriggerAlarmType.NONE
                    )
                    zone = (
                        most_recent_alarm.zone
                        if most_recent_alarm.zone is not None
                        else -1
                    )
                    partition_siren[0] = self._determine_siren_state(
                        partition_armcode[0], al, zone
                    )
                    _LOGGER.info(f"     Setting Siren Data {partition_siren[0]}")  # noqa: G004
                _LOGGER.info(f"Main Panel is {partition_armcode[0].name}")  # noqa: G004

            state_transition_detected = self._update_cloud_partition_states(
                partition_armcode
            )
            if state_transition_detected:
                self._event_logger.logstate_debug(
                    "Cloud partition entered a stable armed or disarmed state; "
                    "refreshing events"
                )
                await self._refresh_cloud_events()
            await self._log_cloud_diagnostic_data(
                timenow, refresh_events=not state_transition_detected
            )

            statusdict: dict[str, Any] = {
                TEXT_DISCONNECTION_COUNT: self.disconnection_counter
            }
            statusdict.update(self._last_cloud_event_attributes)
            if self.last_alarm_trigger_source:
                statusdict[TEXT_LAST_ALARM_TRIGGER_SOURCES] = (
                    self.last_alarm_trigger_source
                )

            new_data = VisonicCoordinatorData(
                connected=True,
                ispowermaster=True,
                mode="cloud",
                achieved_powerlink=True,
                model=self.config_entry.title,  # .panel_model,
                statusdict=statusdict,
                panelstate=panelstate,
                partition_armcode=partition_armcode,
                partition_siren=partition_siren,
                partition_dict=self.partition_dict,
                zones=self.platform_manager.sensor_state(),
                switch=self.platform_manager.switch_state(),
                device=self.platform_manager.device_state(),
            )
            if self.first_time:
                self._event_logger.logstate_info("Visonic setting up alarm panels")
                self.first_time = False
                self.async_set_updated_data(new_data)
                self.platform_manager.set_alarm_device_information(
                    self.config_entry.title
                )  # (self.panel_model)
                await self.platform_manager.async_setup_alarm_panel(partition_list)
            else:
                self.platform_manager.rationalise_ha_devices(False)
            return new_data  # noqa: TRY300

        except UnauthorizedError:
            self.cloud_api_connected = False
            self.panel_cloud_connected = None
            # Session expired, attempt to re-authenticate
            self._event_logger.logstate_info(
                "Visonic session expired, re-authenticating..."
            )
            await self.async_panel_connect()

        except Exception as err:
            self.cloud_api_connected = False
            self.panel_cloud_connected = None
            raise UpdateFailed(
                f"Error communicating with Visonic Cloud: {err}"
            ) from err

        return VisonicCoordinatorData(
            connected=False,
            ispowermaster=True,
            mode="cloud",
            achieved_powerlink=True,
            model=self.config_entry.title,  # .panel_model,
        )

    async def _log_cloud_diagnostic_data(
        self, now: datetime, *, refresh_events: bool = True
    ) -> None:
        """Log optional cloud diagnostics on a slower cadence for API inspection."""
        if (
            self._cloud_diagnostic_data_refreshed_at is not None
            and now - self._cloud_diagnostic_data_refreshed_at
            < CLOUD_DIAGNOSTIC_REFRESH_INTERVAL
        ):
            return

        self._cloud_diagnostic_data_refreshed_at = now
        if self.cloud_alarm is None:
            return

        for name, getter in (
            ("troubles", self.cloud_alarm.get_troubles),
            ("alerts", self.cloud_alarm.get_alerts),
        ):
            try:
                data = await getter()
            except UnauthorizedError:
                raise
            except Error as err:
                self._event_logger.logstate_debug(
                    "Unable to fetch cloud %s: %s", name, err
                )
            else:
                self._event_logger.logstate_debug("Cloud %s: %s", name, data)

        if refresh_events:
            await self._refresh_cloud_events()

    async def _refresh_cloud_events(self) -> None:
        """Fetch cloud events and update the alarm panel's last-event attributes."""
        if self.cloud_alarm is None:
            return
        try:
            events: list[Event] = await self.cloud_alarm.get_events()
        except UnauthorizedError:
            raise
        except Error as err:
            self._event_logger.logstate_debug(
                "Unable to fetch cloud events: %s", err
            )
        else:
            self._update_last_cloud_event(events)
            self._event_logger.logstate_debug("Cloud events: %s", events)

    def _update_cloud_partition_states(
        self, partition_states: dict[int, AlarmPanelStatus]
    ) -> bool:
        """Track cloud panel states and report transitions to stable arm states."""
        stable_states = {
            AlarmPanelStatus.DISARMED,
            AlarmPanelStatus.ARMED_HOME,
            AlarmPanelStatus.ARMED_AWAY,
            AlarmPanelStatus.ARMED_HOME_BYPASS,
            AlarmPanelStatus.ARMED_AWAY_BYPASS,
            AlarmPanelStatus.ARMED_HOME_INSTANT,
            AlarmPanelStatus.ARMED_AWAY_INSTANT,
        }
        state_changed = False
        for partition, current_state in partition_states.items():
            previous_state = self._last_cloud_partition_states.get(partition)
            if (
                previous_state is not None
                and previous_state != current_state
                and current_state in stable_states
            ):
                state_changed = True
        self._last_cloud_partition_states.update(partition_states)
        return state_changed

    def _update_last_cloud_event(self, events: list[Event]) -> None:
        """Update the alarm panel's last-event attributes from cloud events."""
        dated_events: list[tuple[datetime, Event]] = []
        for event in events:
            try:
                event_time = datetime.strptime(event.datetime, "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                continue
            dated_events.append((event_time, event))

        if not dated_events:
            return

        latest_time = max(event_time for event_time, _event in dated_events)
        latest_events = [
            event for event_time, event in dated_events if event_time == latest_time
        ]
        latest_event = latest_events[0]
        partitions = sorted(
            {
                partition
                for event in latest_events
                if event.appointment == latest_event.appointment
                and event.description == latest_event.description
                and event.label == latest_event.label
                and event.device_type == latest_event.device_type
                and event.zone == latest_event.zone
                for partition in (event.partitions or [])
            }
        )
        self._last_cloud_event_attributes = {
            TEXT_LAST_EVENT_NAME: latest_event.appointment or latest_event.device_type,
            TEXT_LAST_EVENT_ACTION: latest_event.description or latest_event.label,
            TEXT_LAST_EVENT_TIME: latest_event.datetime,
        }
        if partitions:
            self._last_cloud_event_attributes[TEXT_LAST_EVENT_PARTITION] = (
                partitions[0]
                if len(partitions) == 1
                else ",".join(str(partition) for partition in partitions)
            )

    def _as_sensor_state(self, device: Device) -> SensorState:
        loc = device.location.lower() if device.location else "undefined"
        zonetype = (
            device.zone_type_name.lower() if device.zone_type_name else "undefined"
        )

        if device.device_number not in self._logged_cloud_sensor_initial_state:
            self._logged_cloud_sensor_initial_state.add(device.device_number)
            raw_data = getattr(device, "_data", {})
            warnings = raw_data.get("warnings", "<missing>")
            warnings_state = (
                "missing"
                if "warnings" not in raw_data
                else "null"
                if warnings is None
                else "empty list"
                if warnings == []
                else type(warnings).__name__
            )
            self._event_logger.logstate_debug(
                "Initial cloud sensor data: device=%s subtype=%s warnings_state=%s "
                "warnings=%s library_state=%s low_battery=%s tamper=%s trouble=%s",
                device.device_number,
                device.subtype,
                warnings_state,
                warnings,
                device.state,
                device.low_battery,
                device.tamper,
                device.trouble,
            )

        triggered = False
        temperature = None
        luminance = None

        if isinstance(device, ContactDevice):
            sensor_type_id = 5
            if device.subtype == "HW_ZONE_CONNECTED_DIRECTLY_TO_THE_PANEL":
                # Wired, so no battery
                sensor_type = ZoneSensorDetails(
                    "Wired", AlarmSensorType.MAGNET, STATUS_ONLY_TIMEOUT
                )
            else:
                sensor_type = ZoneSensorDetails(
                    "Contact", AlarmSensorType.MAGNET, BATTERY_AND_STATUS_TIMEOUT
                )
            triggered = to_bool(device.state)

        elif isinstance(device, SmokeDevice):
            sensor_type_id = 10
            sensor_type = ZoneSensorDetails(
                "Smoke", AlarmSensorType.SMOKE, BATTERY_AND_TRIGGER_OTHER
            )

        elif isinstance(device, FloodDevice):
            sensor_type_id = 0x19
            sensor_type = ZoneSensorDetails(
                "Flood", AlarmSensorType.FLOOD, BATTERY_AND_STATUS_TIMEOUT
            )

        elif isinstance(device, CameraDevice):
            sensor_type_id = 16
            sensor_type = ZoneSensorDetails(
                "Camera", AlarmSensorType.CAMERA, BATTERY_NO_TRIGGER
            )

        elif isinstance(device, ShockDevice):
            sensor_type_id = 0x35
            sensor_type = ZoneSensorDetails(
                "Shock", AlarmSensorType.SHOCK, BATTERY_TRIGGER_STATUS
            )

        elif isinstance(device, MotionDevice):
            sensor_type_id = 3
            sensor_type = ZoneSensorDetails(
                "Motion", AlarmSensorType.MOTION, BATTERY_NO_TRIGGER
            )
            temperature = device.temperature
            luminance = device.brightness
        else:
            sensor_type_id = 0
            sensor_type = ZoneSensorDetails(
                "Unknown", AlarmSensorType.UNKNOWN, BASIC_STATUS
            )

        return SensorState(
            id=device.device_number,
            problem=_normalize_trouble(device.trouble),
            sensor_type_id=sensor_type_id,
            # sensor=device.device_number,
            partition={
                partition - 1
                for partition in device.partitions
                if partition > 0
            },
            location=(loc, loc),
            zonetype=zonetype,
            # model=device.name,
            chime="chime_off",
            bypass=to_bool(device.bypass),
            low_battery=to_bool(device.low_battery),
            status=to_bool(device.state),
            tamper=to_bool(device.tamper),
            enabled=not device.preenroll
            and bool(device.enrollment_id),  # fully enrolled and a valid id
            triggered=triggered,
            zonetamper=to_bool(device.tamper),
            temperature=temperature,
            luminance=luminance,
            ismissing=None,
            isoneway=None,
            isinactive=None,
            # offtime=None,
            sensor_type=sensor_type,
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
            device_uid=device.device_uid,
            enabled=True,
            device_type=device.device_type or "",
            model=f"{device.device_type} ({device.enrollment_id})",
            location=loc,
            status=to_bool(device.state),
            low_battery=to_bool(device.low_battery),
            trouble=_normalize_trouble(device.trouble),
            tamper=to_bool(device.tamper),
            partitions=set(device.partitions),
            bypass=to_bool(device.bypass),
        )

    def _get_sensor_jpeg(self, sensor_id: int) -> bytearray | None:
        """Get the binary image data from a camera sensor."""
        return None

    def process_devices(
        self, devices: list[Device], partition_list: set[int] | None
    ) -> PanelState:
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
                if isinstance(
                    device,
                    (
                        ContactDevice,
                        MotionDevice,
                        SmokeDevice,
                        CameraDevice,
                        FloodDevice,
                    ),
                ):
                    sensor: SensorState = self._as_sensor_state(device)
                    if sensor.enabled:
                        self.platform_manager.sensor_update_or_create(sensor)
                    else:
                        self._event_logger.logstate_info(
                            f"Sensor Device Not Enrolled {device}"
                        )

                elif isinstance(device, KeypadDevice):
                    keypad = self._as_device_state(device)
                    if keypad.enabled:
                        self.platform_manager.device_update_or_create(keypad)
                    else:
                        self._event_logger.logstate_info(
                            f"Keypad Device Not Enabled {device}"
                        )

                elif isinstance(device, SirenDevice):
                    siren: SirenDevice = device
                    self._event_logger.logstate_info(
                        f"Siren Device {device.device_type} currently ignored: {siren}"
                    )

                elif isinstance(device, KeyFobDevice):
                    # self._event_logger.logstate_info(f"Keyfob Device {device.device_type}")
                    keyfob: DeviceState = self._as_device_state(device)
                    if keyfob.enabled:
                        self.platform_manager.device_update_or_create(keyfob)
                    else:
                        self._event_logger.logstate_info(
                            f"Keyfob Device Not Enabled {device}"
                        )

                elif isinstance(device, RepeaterDevice):
                    self.platform_manager.device_update_or_create(
                        self._as_device_state(device)
                    )

                elif isinstance(device, ShockDevice):
                    sensor: SensorState = self._as_sensor_state(device)
                    if sensor.enabled:
                        self.platform_manager.sensor_update_or_create(sensor)
                    else:
                        self._event_logger.logstate_info(
                            f"Shock Sensor Device Not Enrolled {device}"
                        )

                elif isinstance(device, PGMDevice):
                    # I think that this only has the PGM device but we already know we don't control it so just in case to list anything else
                    if device.device_type is not None and device.device_type != "PGM":
                        self._event_logger.logstate_info(
                            f"Device {device.device_type}  {device}"
                        )
                # Can't control it so no point in creating it, there's no other entities
                #    switch: SwitchState = self._as_switch_state(device)
                #    if switch.enabled:
                #        self.platform_manager.switch_update_or_create(switch)
                #    else:
                #        self._event_logger.logstate_info(f"Switch Device Not Enabled {device}")

                elif isinstance(device, PanelDevice):
                    # use the current used partitions and not the total possible in the panel
                    panel: PanelDevice = device
                    # self._event_logger.logstate_debug(f"panel warnings {panel.warnings}")
                    panelstate.battery_level = 0 if to_bool(panel.low_battery) else 100
                    panelstate.tamper = to_bool(panel.tamper)
                    panelstate.trouble = _normalize_trouble(panel.trouble)
                    panelstate.partition = partition_list

        except Exception as ex:  # noqa: BLE001
            tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
            self._event_logger.logstate_error(
                "Exception in processing cloud device data. %s", tb_str
            )
        else:
            return panelstate


#        return {}
