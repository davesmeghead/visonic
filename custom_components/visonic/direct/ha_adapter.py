"""Adapt the standalone pyvisonic client to Home Assistant."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from pyvisonic.py_enum import (
    AlCondition,
    AlPanelCommand,
    AlPanelMode,
    AlPanelStatus,
    AlSensorCondition,
    AlSwitchCommand,
    AlTerminationType,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE,
    CONF_HOST,
    CONF_PORT,
    CONF_TYPE,
    EVENT_CORE_CONFIG_UPDATE,
)
from homeassistant.core import HomeAssistant
from pyvisonic import (
    ConnectionType,
    VisonicClient as PyVisonicClient,
    VisonicClientCallbacks,
    VisonicClientSettings,
)

from ..const import (  # noqa: TID252
    CLIENT_VERSION,
    CONF_DEVICE_BAUD,
    CONF_DOWNLOAD_CODE,
    CONF_ENABLE_SENSOR_BYPASS,
    CONF_EPROM_ATTRIBUTES,
    CONF_ESPHOME_ENTITY_SELECT,
    CONF_RETRY_CONNECTION_COUNT,
    CONF_RETRY_CONNECTION_DELAY,
    CONF_USER_CODE_SLOT,
    DEFAULT_DEVICE_BAUD,
    PE_EVENT,
    PE_NAME,
    PE_PARTITION,
    PE_TIME,
    TEXT_CLIENT_VERSION,
    TEXT_LAST_EVENT_ACTION,
    TEXT_LAST_EVENT_NAME,
    TEXT_LAST_EVENT_PARTITION,
    TEXT_LAST_EVENT_TIME,
    VISONIC_PANEL,
)
from ..log_events import logEvents  # noqa: TID252
from ..platform_manager import PlatformManager  # noqa: TID252
from ..utils import (  # noqa: TID252
    get_local_time,
    print_partition,
    to_bool,
    update_config_entry_threadsafe,
)
from ..visonic_entity_types import DeviceState, PanelState, SwitchState  # noqa: TID252
from ..visonic_types import (  # noqa: TID252
    AlarmCommandStatus,
    AlarmPanelCommand,
    AlarmPanelStatus,
    AlarmSensorType,
    AlarmSwitchCommand,
    AvailableNotifications,
    CommandResult,
    PanelCondition,
    TriggerAlarmType,
)
from .direct_types import SensorStateExt
from .language_decoder import LanguageDecoder
from .panel_event_coordinator import PanelEventCoordinator

_ACTION_MAP: dict[AlTerminationType, PanelCondition] = {
    AlTerminationType.EXTERNAL_TERMINATION: PanelCondition.CONNECTION,
    AlTerminationType.SAME_PACKET_ERROR: PanelCondition.CONNECTION,
    AlTerminationType.CRC_ERROR: PanelCondition.CONNECTION,
    AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED: PanelCondition.NO_DATA_FROM_PANEL,
    AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED: PanelCondition.NO_DATA_FROM_PANEL,
    AlTerminationType.NO_POWERLINK_FOR_PERIOD: PanelCondition.CONNECTION,
}

_STATE_MAP: dict[AlTerminationType, str] = {
    AlTerminationType.EXTERNAL_TERMINATION: "disconnected",
    AlTerminationType.SAME_PACKET_ERROR: "disconnected",
    AlTerminationType.CRC_ERROR: "disconnected",
    AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED: "connected",
    AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED: "disconnected",
    AlTerminationType.NO_POWERLINK_FOR_PERIOD: "unknown",
}

_REASON_MAP: dict[AlTerminationType, str | None] = {
    AlTerminationType.EXTERNAL_TERMINATION: "termination",
    AlTerminationType.SAME_PACKET_ERROR: "samepacketerror",
    AlTerminationType.CRC_ERROR: "crcerror",
    AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED: None,
    AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED: None,
    AlTerminationType.NO_POWERLINK_FOR_PERIOD: "powerlinkperiodexpired",
}

_STANDARD_NOTIFICATIONS: dict[PanelCondition, tuple[AvailableNotifications, str]] = {
    PanelCondition.PANEL_RESET: (
        AvailableNotifications.RESET,
        "The Panel has been Reset",
    ),
    PanelCondition.DOWNLOAD_TIMEOUT: (
        AvailableNotifications.PANEL,
        "Panel Data download timeout, Standard Mode Selected",
    ),
    PanelCondition.PIN_REJECTED: (
        AvailableNotifications.INVALID_PIN,
        "The Pin Code has been Rejected By the Panel",
    ),
    PanelCondition.WATCHDOG_TIMEOUT_RETRYING: (
        AvailableNotifications.PANEL,
        "Communication Timeout - Watchdog Timeout, restoring panel connection",
    ),
    PanelCondition.NO_DATA_FROM_PANEL: (
        AvailableNotifications.CONNECTION,
        "Connection Problem - No data from the panel",
    ),
    PanelCondition.COMMAND_REJECTED: (
        AvailableNotifications.ALWAYS,
        "Operation Rejected By Panel",
    ),
}


class HomeAssistantVisonicClient(PyVisonicClient):
    """Bridge pyvisonic callbacks and values to Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        diagnostics: logEvents | None,
        force_standard_mode: bool,
        disable_all_panel_commands: bool,
        platform_manager: PlatformManager,
        panelident: int,
        state_changed_callback: Callable[..., None],
    ) -> None:
        """Initialize the library client and Home Assistant callbacks."""
        self.hass = hass
        self.entry = entry
        self._event_logger = diagnostics
        self.platform_manager = platform_manager
        self.panel_ident_number = panelident
        self.state_changed_callback = state_changed_callback
        self.language_decoder = LanguageDecoder(hass)
        self.panel_event_log = platform_manager.panel_event_log
        self._panel_event_coordinator: (
            PanelEventCoordinator | dict[int, PanelEventCoordinator] | None
        ) = None
        self._serial_baud_rate = int(
            entry.data.get(CONF_DEVICE_BAUD, DEFAULT_DEVICE_BAUD)
        )
        self._last_achieved_powerlink = False
        self.panel_last_event_name = self.language_decoder.getPowerMaxEntry(0)
        self.panel_last_event_action = self.language_decoder.get_event_entry(0)
        self.panel_last_event_time = get_local_time()
        self.panel_last_event_partition = -1
        self._select_entity_id = entry.data.get(CONF_ESPHOME_ENTITY_SELECT, "")

        connection_type = entry.data[CONF_TYPE]
        library_connection_type = (
            ConnectionType.SERIAL
            if connection_type == "serial"
            else ConnectionType.ETHERNET
        )
        settings = VisonicClientSettings(
            connection_type=library_connection_type,
            host=entry.data.get(CONF_HOST, "127.0.0.1"),
            port=int(entry.data.get(CONF_PORT, 30000)),
            path=entry.data.get(CONF_DEVICE, "/dev/ttyUSB0"),
            baud=self._serial_baud_rate,
            force_standard_mode=force_standard_mode,
            disable_all_commands=disable_all_panel_commands,
            allow_sensor_bypass=to_bool(
                entry.options.get(CONF_ENABLE_SENSOR_BYPASS, False)
            ),
            download_code=str(entry.data.get(CONF_DOWNLOAD_CODE, "")),
            user_code_slot=int(entry.data.get(CONF_USER_CODE_SLOT, 1)) - 1,
            retry_count=int(entry.options.get(CONF_RETRY_CONNECTION_COUNT, 1)),
            retry_delay=float(entry.options.get(CONF_RETRY_CONNECTION_DELAY, 90)),
            panel_id=panelident if isinstance(panelident, int) else 0,
        )
        callbacks = VisonicClientCallbacks(
            state_change=state_changed_callback,
            panel_change=self._on_panel_change,
            baud_change=self._on_baud_change,
            problem=self._on_problem,
            sensor_discovery=self._on_sensor_discovery,
            sensor_change=self._on_sensor_change,
            switch_discovery=self._on_switch_discovery,
            switch_change=self._on_switch_change,
            device_discovery=self._on_device_discovery,
            device_change=self._on_device_change,
            event_log=self._on_event_log,
            transport_baud_change=self._on_transport_baud_change,
        )
        super().__init__(settings, callbacks)

        def handle_core_config_updated(_event: object) -> None:
            self.language_decoder.update()

        entry.async_on_unload(
            hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, handle_core_config_updated)
        )

    def hasStarted(self) -> bool:
        """Return whether the library completed panel startup."""
        return self.has_started()

    def get_panel_mode(self) -> AlPanelMode:
        """Return the current panel mode."""
        return self.panel_mode

    def get_panel_model(self) -> str:
        """Return the detected model."""
        return self.panel_model

    def get_panel_status_dict(
        self, include_extended_status: bool | None = None
    ) -> PanelState:
        """Build the Home Assistant panel status from the library status."""
        include_extended = (
            to_bool(self.entry.options.get(CONF_EPROM_ATTRIBUTES, False))
            if include_extended_status is None
            else include_extended_status
        )
        attributes = super().get_panel_status_dict(include_extended)
        attributes[TEXT_LAST_EVENT_NAME] = self.panel_last_event_name
        attributes[TEXT_LAST_EVENT_ACTION] = self.panel_last_event_action
        attributes[TEXT_LAST_EVENT_TIME] = self.panel_last_event_time
        if self.panel_last_event_partition >= 0:
            attributes[TEXT_LAST_EVENT_PARTITION] = self.panel_last_event_partition
        attributes[TEXT_CLIENT_VERSION] = CLIENT_VERSION
        return PanelState(
            attributes.get("emulationmode"),
            attributes.get("trouble"),
            attributes.get("battery_level"),
            attributes.get("tamper"),
            self.get_partitions_in_use(),
            attributes,
        )

    def is_siren_active(self, partition: int) -> tuple[bool, int, TriggerAlarmType]:
        """Return siren status with the integration alarm type."""
        active, zone, alarm_type = super().is_siren_active(partition)
        return active, zone, TriggerAlarmType(alarm_type)

    def is_any_siren_active(self) -> tuple[bool, int, TriggerAlarmType]:
        """Return the first active siren with the integration alarm type."""
        partitions = self.get_partitions_in_use()
        if partitions is None:
            return self.is_siren_active(0)
        for partition in partitions:
            state = self.is_siren_active(partition)
            if state[0]:
                return state
        return False, 0, TriggerAlarmType.NONE

    async def send_command(
        self,
        entity_id: str,
        command: AlarmPanelCommand,
        code: str | None,
        partitions: set[int] | None,
    ) -> CommandResult:
        """Send a command and adapt the library result for Home Assistant."""
        status = await super().send_command(
            AlPanelCommand(command.value), code, partitions
        )
        return CommandResult(
            self._to_alarm_status(status),
            AvailableNotifications.COMMAND,
            message="Command Result",
            eid=entity_id,
        )

    def get_sensor_bypass_state(self, entity_id: str) -> CommandResult:
        """Request an updated bypass state and adapt its result."""
        status = super().get_sensor_bypass_state()
        return CommandResult(
            self._to_alarm_status(status),
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
        """Send bypass command after converting the integration state."""
        library_state = (
            AlPanelStatus(alarm_state.value) if alarm_state is not None else None
        )
        status = await super().send_bypass(devid, bypass, code, library_state)
        return CommandResult(
            self._to_alarm_status(status),
            AvailableNotifications.BYPASS,
            message=f"{'Bypass' if bypass else 'Restore'} sensor {devid}",
            eid=entity_id,
        )

    async def send_switch(
        self, entity_id: str, devid: int, command: AlarmSwitchCommand
    ) -> CommandResult:
        """Send a switch command and adapt its result."""
        status = await super().send_switch(devid, AlSwitchCommand(command.value))
        return CommandResult(
            self._to_alarm_status(status),
            AvailableNotifications.SWITCH,
            message="Switch command",
            eid=entity_id,
        )

    async def send_get_event_log(self, code: str | None) -> CommandResult:
        """Request the event log and adapt its result."""
        status = await super().send_get_event_log(code)
        return CommandResult(
            self._to_alarm_status(status),
            AvailableNotifications.EVENTLOG,
            message="Send get event log",
        )

    async def send_client_get_sensor_image(
        self, devid: int | None, eid: str | None, duration: int
    ) -> AlarmCommandStatus:
        """Request camera images and adapt the library result."""
        status = await super().send_client_get_sensor_image(devid, duration)
        return self._to_alarm_status(status)

    @staticmethod
    def _to_alarm_status(status: Any) -> AlarmCommandStatus:
        try:
            return AlarmCommandStatus(status)
        except ValueError:
            return AlarmCommandStatus.FAIL_INVALID_RETURN

    async def async_panel_stop(self) -> None:
        """Stop the client and its Home Assistant event coordinators."""
        if isinstance(self._panel_event_coordinator, dict):
            for coordinator in self._panel_event_coordinator.values():
                await coordinator.close()
        elif self._panel_event_coordinator is not None:
            await self._panel_event_coordinator.close()
        await super().async_panel_stop()

    def _on_baud_change(self, baud: int) -> None:
        self._serial_baud_rate = baud
        data = dict(self.entry.data)
        if data.get(CONF_DEVICE_BAUD) == baud:
            return
        data[CONF_DEVICE_BAUD] = baud
        update_config_entry_threadsafe(self.hass, self.entry, data=data)

    async def _on_transport_baud_change(self, baud: int) -> None:
        if not self._select_entity_id:
            return
        state = self.hass.states.get(self._select_entity_id)
        if state is None or str(baud) not in state.attributes.get("options", []):
            self._event_logger.logstate_warning(
                "Cannot update ESPHome baud selector %s to %s",
                self._select_entity_id,
                baud,
            )
            return
        await self.hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": self._select_entity_id, "option": str(baud)},
            blocking=False,
        )

    def _on_sensor_discovery(self, create: bool, sensor: Any) -> None:
        state = SensorStateExt.from_dict(self.is_power_master(), sensor.as_dict())
        if state.sensor_type.type == AlarmSensorType.IGNORED:
            if not create:
                self.platform_manager.delete_sensor(sensor.id)
            return
        if create:
            if state.enabled:
                self.platform_manager.sensor_update_or_create(state)
        else:
            self.platform_manager.delete_sensor(state.id)
            if self.panel_mode not in (
                AlPanelMode.POWERLINK,
                AlPanelMode.POWERLINK_BRIDGED,
                AlPanelMode.STANDARD_PLUS,
            ):
                self.platform_manager.rationalise_ha_devices(True)

    def _on_sensor_change(self, sensor: Any, _condition: AlSensorCondition) -> None:
        state = SensorStateExt.from_dict(self.is_power_master(), sensor.as_dict())
        if state is not None:
            self.platform_manager.sensor_update_or_create(state)

    def _on_switch_discovery(self, create: bool, switch: Any) -> None:
        state = SwitchState.from_dict(switch.as_dict())
        if create:
            if state.enabled:
                self.platform_manager.switch_update_or_create(state)
        else:
            self.platform_manager.delete_switch(state.id)
            if self.panel_mode not in (
                AlPanelMode.POWERLINK,
                AlPanelMode.POWERLINK_BRIDGED,
                AlPanelMode.STANDARD_PLUS,
            ):
                self.platform_manager.rationalise_ha_devices(True)

    def _on_switch_change(self, switch: Any) -> None:
        self.platform_manager.switch_update_or_create(
            SwitchState.from_dict(switch.as_dict())
        )

    def _on_device_discovery(self, create: bool, device: Any) -> None:
        state = DeviceState.from_dict(device.as_dict())
        if create:
            if state.enabled:
                self.platform_manager.device_update_or_create(state)
        else:
            self.platform_manager.delete_device(state.device_uid)
            if self.panel_mode not in (
                AlPanelMode.POWERLINK,
                AlPanelMode.POWERLINK_BRIDGED,
                AlPanelMode.STANDARD_PLUS,
            ):
                self.platform_manager.rationalise_ha_devices(True)

    def _on_device_change(self, device: Any) -> None:
        self.platform_manager.device_update_or_create(
            DeviceState.from_dict(device.as_dict())
        )

    def _on_event_log(
        self,
        total: int,
        current: int,
        partitions: set[int],
        date_time: datetime,
        zone: int,
        event: int,
    ) -> None:
        self.panel_event_log.process_panel_event_log(
            total,
            current,
            partitions if self.get_partitions_in_use() else 0,
            date_time,
            self.language_decoder.get_zone_entry(self.is_power_master(), zone),
            self.language_decoder.get_event_entry(event),
        )

    def _on_problem(self, termination: AlTerminationType) -> None:
        event_id = _ACTION_MAP.get(termination)
        if event_id is not None:
            data: dict[str, Any] = {"state": _STATE_MAP[termination]}
            if reason := _REASON_MAP[termination]:
                data["reason"] = reason
            self._event_logger.create_ha_fire_event(
                event_id=event_id, datadictionary=data
            )
        self._on_panel_change(AlCondition.PUSH_CHANGE, {})

    def _on_panel_change(
        self, condition: AlCondition, data: dict[str, Any] | None
    ) -> None:
        try:
            event_id = PanelCondition(condition)
        except ValueError:
            return
        if event_id == PanelCondition.PANEL_UPDATE:
            if data is not None and PE_NAME in data and data[PE_NAME] >= 0:
                self._event_coordinator(data)
            else:
                self._event_logger.logstate_warning(
                    "Cannot translate panel event log data %s", data
                )
        else:
            self._send_event(event_id, data)

        if event_id == PanelCondition.STARTUP_SUCCESS:
            self.platform_manager.rationalise_ha_devices(False)
            partitions = self.get_partitions_in_use()
            self._event_logger.logstate_debug(
                "Startup complete; partitions are %s",
                print_partition(partitions) if partitions is not None else "none",
            )
            self.entry.async_create_task(
                self.hass,
                self.platform_manager.async_setup_alarm_panel(partitions),
                name=f"Setup alarm panel {self.panel_ident_number} entity",
            )
        elif event_id == PanelCondition.DOWNLOAD_SUCCESS:
            model = self.panel_model
            update_config_entry_threadsafe(
                self.hass,
                self.entry,
                title=f"Panel {self.panel_ident_number} ({model or 'Unknown'})",
            )
            self.platform_manager.set_alarm_device_information(model)

    def _send_event(
        self, event_id: PanelCondition, data: dict[str, Any] | None
    ) -> None:
        if (
            event_id == PanelCondition.PANEL_UPDATE
            and data is not None
            and len(data) >= 3
        ):
            self.panel_last_event_name = data.get(PE_NAME, "")
            self.panel_last_event_action = data.get(PE_EVENT, "")
            self.panel_last_event_partition = int(data.get(PE_PARTITION, -2)) + 1
            self.panel_last_event_time = data.get(PE_TIME, "")
        if data is not None and PE_PARTITION in data:
            partition = data[PE_PARTITION]
            if isinstance(partition, int):
                data[PE_PARTITION] = partition + 1
            elif isinstance(partition, set | list):
                data[PE_PARTITION] = [item + 1 for item in partition]
        self._event_logger.create_ha_fire_event(
            event_id=event_id, datadictionary=data if data is not None else {}
        )
        active, _, _ = self.is_any_siren_active()
        if event_id in _STANDARD_NOTIFICATIONS:
            notification, message = _STANDARD_NOTIFICATIONS[event_id]
            self._event_logger.create_ha_notification(
                notification, f"{VISONIC_PANEL} {self.panel_ident_number} - {message}"
            )
        elif active and event_id == PanelCondition.PANEL_UPDATE:
            self._event_logger.create_ha_notification(
                AvailableNotifications.SIREN,
                f"{VISONIC_PANEL} {self.panel_ident_number} - Siren is Sounding, Alarm has been Activated",
            )
        elif event_id == PanelCondition.WATCHDOG_TIMEOUT_GIVINGUP:
            powerlink = self.panel_mode in (
                AlPanelMode.POWERLINK,
                AlPanelMode.POWERLINK_BRIDGED,
            )
            suffix = (
                "Communication Timeout, Watchdog Timeout too many times within 24 hours. Dropping out of Powerlink"
                if powerlink
                else "Communication Timeout, Watchdog Timeout too many times within 24 hours."
            )
            self._event_logger.create_ha_notification(
                AvailableNotifications.CONNECTION,
                f"{VISONIC_PANEL} {self.panel_ident_number} - {suffix}",
            )

    def _event_coordinator(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        if len(data) == 4 and PE_PARTITION in data:
            partition = data[PE_PARTITION]
            if not isinstance(self._panel_event_coordinator, dict):
                self._panel_event_coordinator = {}
            if partition not in self._panel_event_coordinator:
                self._panel_event_coordinator[partition] = PanelEventCoordinator(
                    language_decoder=self.language_decoder,
                    hass=self.hass,
                    entry=self.entry,
                    callbackSender=self._send_event,
                    logger=self._event_logger,
                )
            self._panel_event_coordinator[partition].addEvent(
                pm=self.is_power_master(), data=data
            )
        elif len(data) == 3 and not isinstance(self._panel_event_coordinator, dict):
            if self._panel_event_coordinator is None:
                self._panel_event_coordinator = PanelEventCoordinator(
                    language_decoder=self.language_decoder,
                    hass=self.hass,
                    entry=self.entry,
                    callbackSender=self._send_event,
                    logger=self._event_logger,
                )
            self._panel_event_coordinator.addEvent(pm=self.is_power_master(), data=data)
