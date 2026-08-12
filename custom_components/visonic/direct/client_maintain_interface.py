"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""

# This child/parent class build up incorporates the interaction/interface to the low level pyvisonic library

from collections.abc import Callable
import logging

from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.util import Any

from ..const import (  # noqa: TID252  # noqa: TID252  # noqa: TID252
    CLIENT_VERSION,
    CONF_ARM_CODE_AUTO,
    CONF_DEVICE_BAUD,
    CONF_EPROM_ATTRIBUTES,
    CONF_ESPHOME_ENTITY_SELECT,
    CONF_FORCE_KEYPAD,
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
)
from ..exceptions import VisonicException  # noqa: TID252
from ..log_events import logEvents  # noqa: TID252
from ..panel_event_logger import PanelEventLogger  # noqa: TID252
from ..platform_manager import PlatformManager  # noqa: TID252
from ..utils import (  # noqa: TID252
    get_local_time,
    print_partition,
    to_bool,
    update_config_entry_threadsafe,
)
from ..visonic_entity_types import PanelState  # noqa: TID252
from ..visonic_types import (  # noqa: TID252
    PANEL_TO_HA_STATUS_MAP,
    AvailableNotifications,
    PanelCondition,
    TriggerAlarmType,  # AlAlarmType
    VisonicConfigEntry,
)
from .language_decoder import LanguageDecoder
from .panel_event_coordinator import PanelEventCoordinator
from .pyvisonic.py_abstract_classes import AlPanelInterface
from .pyvisonic.py_enum import (
    AlAlarmType,
    AlCondition,
    AlPanelMode,
    AlPanelStatus,
    AlTerminationType,
)

_LOGGER = logging.getLogger(__name__)

MAX_PARTITIONS = 3

#  There are 6 termination types, these 3 dicts define the event content that is sent to HA
actionmap: dict[AlTerminationType, PanelCondition] = {
    AlTerminationType.EXTERNAL_TERMINATION: PanelCondition.CONNECTION,
    AlTerminationType.SAME_PACKET_ERROR: PanelCondition.CONNECTION,
    AlTerminationType.CRC_ERROR: PanelCondition.CONNECTION,
    AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED: PanelCondition.NO_DATA_FROM_PANEL,
    AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED: PanelCondition.NO_DATA_FROM_PANEL,
    AlTerminationType.NO_POWERLINK_FOR_PERIOD: PanelCondition.CONNECTION,
}

statemap: dict[AlTerminationType, str] = {
    AlTerminationType.EXTERNAL_TERMINATION: "disconnected",
    AlTerminationType.SAME_PACKET_ERROR: "disconnected",
    AlTerminationType.CRC_ERROR: "disconnected",
    AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED: "connected",
    AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED: "disconnected",
    AlTerminationType.NO_POWERLINK_FOR_PERIOD: "unknown",
}

reasonmap: dict[AlTerminationType, str | None] = {
    AlTerminationType.EXTERNAL_TERMINATION: "termination",
    AlTerminationType.SAME_PACKET_ERROR: "samepacketerror",
    AlTerminationType.CRC_ERROR: "crcerror",
    AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED: None,
    AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED: None,
    AlTerminationType.NO_POWERLINK_FOR_PERIOD: "powerlinkperiodexpired",
}

standard_notifications: dict[PanelCondition, tuple[AvailableNotifications, str]] = {
    PanelCondition.PANEL_RESET: (AvailableNotifications.RESET, "The Panel has been Reset"),
    PanelCondition.DOWNLOAD_TIMEOUT: (AvailableNotifications.PANEL, "Panel Data download timeout, Standard Mode Selected"),
    PanelCondition.PIN_REJECTED: (AvailableNotifications.INVALID_PIN, "The Pin Code has been Rejected By the Panel"),
    PanelCondition.WATCHDOG_TIMEOUT_RETRYING: (AvailableNotifications.PANEL, "Communication Timeout - Watchdog Timeout, restoring panel connection"),
    PanelCondition.NO_DATA_FROM_PANEL: (AvailableNotifications.CONNECTION, "Connection Problem - No data from the panel"),
    PanelCondition.COMMAND_REJECTED: (AvailableNotifications.ALWAYS, "Operation Rejected By Panel"),
}

class MaintainInterface:
    """Create and maintain the transport/protocol interface to the hardware (using the pyvisonic library)."""

    # This is the client base class

    def __init__(
        self,
        hass: HomeAssistant,
        entry: VisonicConfigEntry,
        diagnostics: logEvents | None,
        platform_manager : PlatformManager,
        panelident: int,
        state_callback: Callable[..., None],
    ) -> None:
        """Initialize."""
        # These are variables used throughout this class and all child classes
        self.hass = hass
        self.entry = entry
        self.platform_manager : PlatformManager = platform_manager
        self.panel_event_log : PanelEventLogger = self.platform_manager.panel_event_log
        self._visonic_protocol: AlPanelInterface | None = None
        self.logger: logEvents = diagnostics
        self._select_entity_id = (
            self.entry.data.get(CONF_ESPHOME_ENTITY_SELECT, "")
        )
        self.panel_ident: int = panelident
        self._panel_event_coordinator: (
            PanelEventCoordinator | dict[int, PanelEventCoordinator] | None
        ) = None
        self.logger.logstate_debug(
            "Reset client panel variables, ESPHome Select Entity set to: %s",
            self._select_entity_id if len(self._select_entity_id) > 0 else "Not Defined",
        )
        self.language_decoder: LanguageDecoder = LanguageDecoder(hass)
        self.state_changed_callback: Callable[..., None] = state_callback

    def _initialise(self):
        """Initialise local variables to this class."""
        # For a panel with no partitions and a panel with partitions
        self._panel_event_coordinator: (
            PanelEventCoordinator | dict[int, PanelEventCoordinator] | None
        ) = None

        self._serial_baud_rate = int(self.entry.data.get(CONF_DEVICE_BAUD, DEFAULT_DEVICE_BAUD))
        if self._serial_baud_rate == 9600:
            self._connection_baud_list = [ 38400, 9600, 38400, 9600 ]   # Try these bauds in sequence, as each is tried then delete it, once the list is empty then give up
        else:
            self._connection_baud_list = [ 9600, 38400, 9600, 38400 ]   # Try these bauds in sequence, as each is tried then delete it, once the list is empty then give up
        self._baud_index = 0

        self._system_started = False
        self.panel_last_event_name = self.language_decoder.getPowerMaxEntry(0)
        # get the language translation for "Normal"
        self.panel_last_event_action = self.language_decoder.get_event_entry(0)
        self.panel_last_event_time = get_local_time()
        self.panel_last_event_partition = -1

    def reset_baud_list(self):
        """Reset the baud list to the default."""
        self._baud_index = 0

    def hasStarted(self) -> bool:
        """Has the system started?"""
        return self._system_started

    def get_panel_status_dict(
        self, include_extended_status: bool | None = None
    ) -> PanelState:
        """Get the panel status."""
        ies = (
            to_bool(self.entry.options.get(CONF_EPROM_ATTRIBUTES, False))
            if include_extended_status is None
            else include_extended_status
        )
        attributes = self._protocol.get_panel_status_dict(ies)
        emulationmode: str = attributes.get("emulationmode")
        trouble: str = attributes.get("trouble")
        battery_level: int = attributes.get("battery_level")
        tamper: bool = attributes.get("tamper")

        pd : PanelState = PanelState(emulationmode, trouble, battery_level, tamper, self.get_partitions_in_use(), attributes)

        # Only add these when there are no partitions at all
        #    The A7 data from the panel is not reliable enough, and I can't find an equivalent B0 message
        pd.attributes[TEXT_LAST_EVENT_NAME] = self.panel_last_event_name
        pd.attributes[TEXT_LAST_EVENT_ACTION] = self.panel_last_event_action
        pd.attributes[TEXT_LAST_EVENT_TIME] = self.panel_last_event_time
        if self.panel_last_event_partition >= 0:
            pd.attributes[TEXT_LAST_EVENT_PARTITION] = self.panel_last_event_partition
        pd.attributes[TEXT_CLIENT_VERSION] = CLIENT_VERSION
        return pd

    def get_partitions_in_use(self) -> set[int] | None:
        """Get the set of partitions in use."""
        if self._visonic_protocol is None:
            return None
        return self._visonic_protocol.get_partitions_in_use()

    def is_power_master(self) -> bool:
        """Is PowerMaster."""
        if self._visonic_protocol is None:
            return False
        return self._visonic_protocol.is_power_master()

    @property
    def _protocol(self) -> AlPanelInterface:
        # This is only used when it is 99% certain that self._visonic_protocol is set correctly
        if self._visonic_protocol is None:
            raise VisonicException("Protocol not initialised", code=200)
        return self._visonic_protocol

    def convert_to_alarm_type(self, value: AlAlarmType) -> TriggerAlarmType:
        """Convert between pyvisonic library and main integration."""
        return TriggerAlarmType(value)

    def is_siren_active(self, partition: int) -> tuple[bool, int, TriggerAlarmType]:
        """Is the siren active."""
        sa = self._protocol.is_siren_active(partition)
        return (sa[0], sa[1], self.convert_to_alarm_type(sa[2]))

    def is_any_siren_active(self) -> tuple[bool, int, TriggerAlarmType]:
        """Is any siren active."""
        piu = self.get_partitions_in_use()
        if piu is None:
            return self.is_siren_active(0)
        for p in piu:
            al = self.is_siren_active(p)
            # Return the first that is active
            if al[0]:
                return al
        return (False, 0, TriggerAlarmType.NONE)

    def get_partition_status(self, partition: int | None = None) -> AlPanelStatus:
        """Get the panel status code."""
        if partition is not None:
            return self._protocol.get_partition_status(partition)
        return AlPanelStatus.UNKNOWN

    def get_panel_mode(self) -> AlPanelMode:
        """Get the panel mode."""
        return self._protocol.get_panel_mode()

    def get_panel_model(self) -> str | None:
        """Get the panel model."""
        return self._protocol.get_panel_model()

    def get_partition_status_dict(self, partition: int) -> dict[str, Any]:
        """Get the panel status."""
        return self._protocol.get_partition_status_dict(partition)

    def is_panel_connected(self) -> bool:
        """Are we connected to the Alarm Panel."""
        try:
            partitions = self.get_partitions_in_use() or {0}
            statuses = [self.get_partition_status(p) for p in partitions]
            panelmode = self.get_panel_mode()

            return panelmode != AlPanelMode.UNKNOWN and any(
                status != AlPanelStatus.UNKNOWN for status in statuses
            )
        except VisonicException:
            return False

#    def is_panel_connected_old(self) -> bool:
#        """Are we connected to the Alarm Panel."""
#        # If we are starting up then assume we need a valid code
#        #  This is the opposite of code_format as we want to prevent operation during startup
#        # Are we just starting up or has there been a problem  and we are disconnected?
#        armcode = AlPanelStatus.UNKNOWN
#        for p in range(MAX_PARTITIONS):
#            ps = self.get_partition_status(p)
#            armcode = max(armcode, ps)
#        panelmode = self.get_panel_mode()
#        return not (
#            armcode == AlPanelStatus.UNKNOWN or panelmode == AlPanelMode.UNKNOWN
#        )

    def get_panel_pin_code(
        self, code: str | None, psc: AlPanelStatus | None
    ) -> tuple[bool, str | None, bool, bool]:
        """Get code code."""
        # get_panel_pin_code: Convert a PIN given as 4 digit string in the PIN PDU format as used in messages to powermax
        # Return tuple:  IsCodeValid, code, showKeypad, code_arm_required
        alarm_state = (
            PANEL_TO_HA_STATUS_MAP[psc]
            if psc is not None and psc in PANEL_TO_HA_STATUS_MAP
            else None
        )
        panelmode = self.get_panel_mode()
        forced_keypad = to_bool(self.entry.options.get(CONF_FORCE_KEYPAD, False))
        mycode: str | None = (
            None if code is None or code == "" or len(code) != 4 else code
        )
        is_arm_without_code = to_bool(self.entry.options.get(CONF_ARM_CODE_AUTO, False))

        # IsCodeValid, code, showKeypad, code_arm_required
        if psc in [
            AlPanelStatus.UNKNOWN,
            AlPanelStatus.USER_TEST,
            AlPanelStatus.DOWNLOADING,
        ]:
            # Return invalid as panel not in correct state to do anything
            return (False, None, False, True)
        if panelmode in [
            AlPanelMode.UNKNOWN,
            AlPanelMode.DOWNLOAD,
            AlPanelMode.STOPPED,
            AlPanelMode.STARTING,
            AlPanelMode.MINIMAL_ONLY,
        ]:
            # Return invalid as panel downloading EPROM, stopped or starting
            return (False, None, False, True)
        if panelmode == AlPanelMode.STANDARD:
            if alarm_state == AlarmControlPanelState.DISARMED:
                if is_arm_without_code:
                    # If the panel can arm without a usercode then we can use 0000 as the usercode --> top row in standard Table
                    return (True, "0000", False, False)
            elif mycode is not None and forced_keypad:
                # Armed and force keypad --> bottom row in Standard Table
                # use keypad so invalidate the return, there should be a valid 4 code code
                return (True, mycode, True, True)

            if mycode is None:
                # use keypad to get code
                return (False, None, True, True)
            # code is valid so no keypad needed
            return (True, mycode, False, True)

        # Here when panelmode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]
        if forced_keypad:
            # Disarmed: depends on if panel can arm without a code.  Armed: Show keypad
            keypad = (
                not is_arm_without_code
                if alarm_state == AlarmControlPanelState.DISARMED
                else True
            )
            # Bottom 4 rows of Powerlink Table
            return (True, mycode, keypad, not is_arm_without_code)
        # Top 2 rows of Powerlink Table. No need for a keypad when in powerlink.
        return (True, mycode, False, False)

    def is_select_entity_valid(self, option: str | None = None) -> bool:
        """Is the HA Select entity valid?"""
        if self._select_entity_id is None or not valid_entity_id(self._select_entity_id):
            return False
        # Get current entity
        state_obj = self.hass.states.get(self._select_entity_id)
        if state_obj is None:
            #raise ValueError(f"Entity {self._select_entity_id} not found")
            self.logger.logstate_debug(f"Entity {self._select_entity_id} not found")
            return False
        # Get available options
        options = state_obj.attributes.get("options", [])
        if not options:
            #raise ValueError(f"No options found for {self._select_entity_id}")
            self.logger.logstate_debug(f"No options found for {self._select_entity_id}")
            return False
        # Check if the requested option is valid
        if option is not None and option not in options:
            #raise ValueError(f"Invalid option '{option}' for {self._select_entity_id}. Valid options: {options}")
            self.logger.logstate_debug(f"Invalid option '{option}' for {self._select_entity_id}. Valid options: {options}")
            return False
        return True

    def set_select_entity(self, option: str):
        """Safely set a select entity to the given option. :param option: The option value to select."""
        self.logger.logstate_debug(f"Setting select value {option}")
        # Call the service to select the option
        self.hass.loop.call_soon_threadsafe(
            lambda: self.entry.async_create_task(
                self.hass,
                self.hass.services.async_call(
                    domain="select",
                    service="select_option",
                    service_data={"entity_id": self._select_entity_id, "option": option},
                    blocking=False,
                ),
                name="set select entity",
            )
        )
        self.logger.logstate_debug("    Setting select value Done")

    async def _stop_panel_change_handler(self):
        # Close down the tasks within the event coordinators
        if self._panel_event_coordinator is not None:
            if isinstance(self._panel_event_coordinator, dict):
                for value in self._panel_event_coordinator.values():
                    await value.close()
            else:
                await self._panel_event_coordinator.close()

    def action_panel_termination(self, termination: AlTerminationType):
        """Action a problem from the underlying protocol."""
        action = actionmap[termination]
        state = statemap[termination]
        reason = reasonmap[termination]
        # General update trigger
        #    0 is a disconnect, state="disconnected" means initial disconnection and (hopefully) reconnect from an exception (probably comms related)
        data = {"state": state}
        if reason is not None:
            data["reason"] = reason
        self.logger.logstate_warning(
            "Visonic has responded to a disconnection, action=%s, data=%s",
            action,
            data,
        )
        self.platform_manager.create_ha_fire_event(event_id=action, datadictionary=data)

    def sensors_to_string_list(self) -> list[str]:
        """Dump sensors to string list."""
        if self._visonic_protocol is not None:
            return self._visonic_protocol.sensors_to_string_list()
        return []

    def switches_to_string_list(self) -> list[str]:
        """Dump switches to string list."""
        if self._visonic_protocol is not None:
            return self._visonic_protocol.switches_to_string_list()
        return []

    def update_baud(self):
        """Update baud, to be overridden."""
        raise NotImplementedError

    def send_event(
        self, event_id: AlCondition | PanelCondition, data: dict[str, Any] | None
    ):
        """Send an event to Home Assistant and create notifications as needed."""
        try:
            event_id = PanelCondition(event_id)
        except ValueError:
            # handle unknown values safely
            return

        if event_id == PanelCondition.PANEL_UPDATE and data is not None and len(data) >= 3:
            self.panel_last_event_name = data.get(PE_NAME, "")
            self.panel_last_event_action = data.get(PE_EVENT, "")
            self.panel_last_event_partition = int(data.get(PE_PARTITION, -2)) + 1
            self.panel_last_event_time = data.get(PE_TIME, "")

        # We can alter data as it is only used to create the fire event
        if data is not None and PE_PARTITION in data:
            if isinstance(data[PE_PARTITION], int):
                data[PE_PARTITION] = data[PE_PARTITION] + 1
            elif isinstance(data[PE_PARTITION], set | list):
                data[PE_PARTITION] = [p+1 for p in data[PE_PARTITION]]

        self.platform_manager.create_ha_fire_event(
            event_id=event_id, datadictionary=data if data is not None else {}
        )

        if event_id == PanelCondition.DOWNLOAD_SUCCESS:  # download success
            # We can't update the entry title directly here as we might be within a config update
            pm = self.get_panel_model()
            title = "Panel " + str(self.panel_ident) + " (" + ("Unknown" if pm is None else pm) + ")"
            # update the title
            update_config_entry_threadsafe(
                hass = self.hass,
                entry = self.entry,
                title = title,
            )
            # This will update the alarm device information with the model number (if available)
            self.platform_manager.set_alarm_device_information(pm)

        if event_id == PanelCondition.STARTUP_SUCCESS:  # Startup Success
            # set baud list back to default ready if there's a disconection
            self.reset_baud_list()
            self.platform_manager.rationalise_ha_devices(False)

            if (p := self.get_partitions_in_use()) is not None:
                self.logger.logstate_debug(
                    "Startup Complete, number of partitions in panel = %s   they are %s",
                    len(p),
                    print_partition(p)
                )
            else:
                self.logger.logstate_debug(
                    "Startup Complete, no partitions in panel"
                )

            self.setupAlarmPanel(self.get_partitions_in_use())
            self.state_changed_callback()
            # This will only succeed if in powerlink mode and the panel is a powermaster
            #   This also works for ethernet as self._serial_baud_rate should not have been changed
            if self._serial_baud_rate == 9600 and self.is_power_master() and self.get_panel_mode() == AlPanelMode.POWERLINK:
                self._serial_baud_rate = 38400
                self.update_baud()

        self.save_working_baud(self._serial_baud_rate)

        # if event_id == PanelCondition.PANEL_UPDATE and self.get_panel_mode() == AlPanelMode.POWERLINK:
        #    # Powerlink Mode
        #    self.print_all_entities()

        isa, _, _ = self.is_any_siren_active()
        if event_id in standard_notifications:
            value = standard_notifications[event_id]
            self.logger.create_ha_notification(value[0], value[1])
        elif event_id == PanelCondition.PANEL_UPDATE and isa:
            self.logger.create_ha_notification(
                AvailableNotifications.SIREN,
                "Siren is Sounding, Alarm has been Activated",
            )
        elif event_id == PanelCondition.WATCHDOG_TIMEOUT_GIVINGUP:
            if (
                self.get_panel_mode() == AlPanelMode.POWERLINK
                or self.get_panel_mode() == AlPanelMode.POWERLINK_BRIDGED
            ):
                self.logger.create_ha_notification(
                    AvailableNotifications.CONNECTION,
                    "Communication Timeout - Watchdog Timeout too many times within 24 hours. Dropping out of Powerlink",
                )
            else:
                self.logger.create_ha_notification(
                    AvailableNotifications.CONNECTION,
                    "Communication Timeout - Watchdog Timeout too many times within 24 hours.",
                )

    def setupAlarmPanel(self, piu: set[int] | None):
        """Setup the alarm panel.  This has to be done only when all partitions are known."""
        self.entry.async_create_task(self.hass, self.platform_manager.async_setup_alarm_panel(piu), name=f"Setup alarm panel {self.panel_ident} entity")

    def save_working_baud(self, baud: int) -> None:
        """Persist the detected working baud rate."""
        if baud not in (9600, 38400):
            return
        if self.entry.data.get(CONF_DEVICE_BAUD) == baud:
            return
        data = dict(self.entry.data)
        data[CONF_DEVICE_BAUD] = baud
        update_config_entry_threadsafe(
            hass = self.hass,
            entry = self.entry,
            data = data,
        )

    def event_coordinator(self, data: dict[str, Any] | None):
        """Coordinate the event to the panel and any partitions."""
        if len(data) == 4 and PE_PARTITION in data:
            # The panel has partitions
            partition = data[PE_PARTITION]

            if self._panel_event_coordinator is None:
                # initialise as a dict, the partition is the key
                self._panel_event_coordinator = {}

            if not isinstance(self._panel_event_coordinator, dict):
                # if it's the incorrect type then empty it. initialise as a dict, the partition is the key
                self._panel_event_coordinator = {}

            if partition not in self._panel_event_coordinator:
                self._panel_event_coordinator[partition] = (
                    PanelEventCoordinator(
                        language_decoder=self.language_decoder,
                        hass=self.hass,
                        entry=self.entry,
                        callbackSender=self.send_event,
                        logger=self.logger,
                    )
                )
            if self._panel_event_coordinator[partition].addEvent(
                pm=self.is_power_master(), data=data
            ):
                _LOGGER.debug(
                    "[on_panel_change_handler] partition %s    data %s",
                    partition,
                    data,
                )

        elif len(data) == 3 and not isinstance(self._panel_event_coordinator, dict):
            # The panel does not have partitions
            if self._panel_event_coordinator is None:
                self._panel_event_coordinator = PanelEventCoordinator(
                    language_decoder=self.language_decoder,
                    hass=self.hass,
                    entry=self.entry,
                    callbackSender=self.send_event,
                    logger=self.logger,
                )
            if self._panel_event_coordinator.addEvent(
                pm=self.is_power_master(), data=data
            ):
                _LOGGER.debug(
                    "[on_panel_change_handler] %s  set to %s   no partitions",
                    type(self._panel_event_coordinator),
                    self._panel_event_coordinator,
                )

        elif len(data) == 3 and isinstance(self._panel_event_coordinator, dict):
            # self.logger.logstate_debug(f"[on_panel_change_handler] {type(self._panel_event_coordinator)}   set to {self._panel_event_coordinator}   nothing done as message length indicates a single partition but we know there's multiple")
            for p in range(4):
                if p in self._panel_event_coordinator:
                    if self._panel_event_coordinator[p].addEvent(
                        pm=self.is_power_master(), data=data
                    ):
                        self.logger.logstate_debug(
                            "[on_panel_change_handler] processing event through %s",
                            p,
                        )
                    break
        else:
            self.logger.logstate_warning(
                "[on_panel_change_handler] Cannot translate panel event log data %s",
                data,
            )
