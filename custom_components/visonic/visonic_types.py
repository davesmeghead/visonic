"""Global Visonic Types."""

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from enum import Enum, IntEnum, StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self, TypedDict, cast

from homeassistant.components.alarm_control_panel.const import AlarmControlPanelState
from homeassistant.config_entries import ConfigEntry
from homeassistant.util.hass_dict import HassEntryKey

from .const import DOMAIN

if TYPE_CHECKING:
    # Imports used purely to define a type for type checking:
    #    This ensures no cyclic imports
    from .alarm_control_panel import VisonicAlarm
    from .coordinator_base import VisonicCoordinator
    from .sensor import VisonicAlarmSensor
    from .server import ServerProtocol, TCPServerConnection
    from .visonic_entity_types import DeviceState, PanelState, SensorState, SwitchState

########################################################################################################################################
# These 2 classes define the connection to the panel:  DeviceType, EmulationMode
#
#  DeviceType
#    ETHERNET         A direct TCP connection is made to a device in the panel.
#                     This device must be a TCP Server that translates data to/from the panel
#                          An ESPHome device can be configured do to this
#                     Note that zeroconf is supported, using "_visonic-direct._tcp.local."
#                          The emulation mode can also be provided in the zeroconf
#    SERIAL           A direct serial connection is made to a device in the panel.
#    CLOUD            A REST API connection is made to an external Visonic Server (with login details and user code)
#                          The Visonic Go App can be used in conjunction with this
#                     The panel must be PowerMaster with a Powerlink 3 hardware module
#                     As this is a polled mechanism (and not a direct connection to the panel) this has delayed updates
#    TCP_SERVER       A TCP_SERVER is created to listen on port 5001 (by default), when a panel connects then
#    TCP_DISCOVERED        an HA discovery sequence is triggered.
#                     The TCP_SERVER appears in the list of HUBs as a server but it does not have a panel or entities.
#                          The connection (panel) that connects to the server is then spawned as a TCP_DISCOVERED client (HUB)
#                     These are therefore used in combination.
#                     These are not currently supported. It is still in development
#
#  EmulationMode is only applicable to ETHERNET, SERIAL
########################################################################################################################################
class DeviceType(StrEnum):
    """Device Type."""
    ETHERNET = "ethernet"
    SERIAL = "serial"
    CLOUD = "visonic_cloud_server"
    TCP_DISCOVERED = "server_discovered"  # Not currently supported, in development
    TCP_SERVER = "local_powerlink_server"  # Not currently supported, in development

    def title(self) -> str:
        """Make a title case with no underscores."""
        return self.value.replace("_", " ").title()

    @classmethod
    def from_title(cls, text: str) -> DeviceType:
        """String to DeviceType. Do the opposite of title above."""
        normalized = text.strip().lower().replace(" ", "_")
        return cls(normalized)

class EmulationMode(StrEnum):
    """Main emulation mode for direct connections."""
    # These are only relevant when DeviceType is ETHERNET, SERIAL
    MINIMAL = "Minimal Interaction (data only sent to obtain panel state)"
    STANDARD = "Standard"
    POWERLINK = "Powerlink Emulation"

    @classmethod
    def parse(cls, raw: str | None) -> EmulationMode:
        """Parse emulation mode safely."""
        if not raw:
            return EmulationMode.POWERLINK
        norm = raw.strip().upper().replace("_", "-").replace(" ", "-")
        mapping = {
            "MIN": EmulationMode.MINIMAL,
            "MINIMAL": EmulationMode.MINIMAL,
            "STANDARD": EmulationMode.STANDARD,
            "STD": EmulationMode.STANDARD,
            "POWERLINK": EmulationMode.POWERLINK,
            "POWER-LINK": EmulationMode.POWERLINK,
        }
        return mapping.get(norm, EmulationMode.POWERLINK)

    @classmethod
    def usage(cls, em: EmulationMode) -> str:
        """Return a usage string for the language files."""
        match(em):
            case EmulationMode.POWERLINK:
                return "full use (powerlink emulation mode)"
            case EmulationMode.STANDARD:
                return "standard emulation mode"
            case EmulationMode.MINIMAL:
                return "minimal emulation mode"
        return "this should not happen!!!"


###################################################################################################
# This set of classes define the data that is saved in the HASS config entry and run_time data
###################################################################################################
@dataclass
class VisonicConfigData:
    """The class that is saved as Home Assistant runtime data (for clients)."""
    # Made it a class just in case I want to include more parameters in future
    # Coordinator
    coordinator: VisonicCoordinator
    # panel identifier
    panel_id: int
    # This is the alarm control entity that is first created.
    #      For multi partiton panels, this is changed to be the overall control entity.
    #      For Basic Emulation Mode this is the sensor, otherwise it's the alarm_control_panel
    alarm_entity: VisonicAlarmSensor | VisonicAlarm | None
    # A dictionary of dispatchers so I can terminate them all correctly
    dispatchers: dict[str, Callable[..., None]]
    # A list of functions to call to cleanup on unload
    #cleanup_callbacks: list[Callable[..., None]]

@dataclass
class VisonicServerData:
    """The class that is saved as Home Assistant runtime data (for servers)."""
    server: TCPServerConnection
    # server identifier
    server_id: int
    lock: asyncio.Lock

@dataclass
class VisonicDiscoveryData:
    """The class that is saved as Home Assistant runtime data (discoveries)."""
    # panel identifier
    panel_id: int
    account: str
    panel: str
    protocol: ServerProtocol
    transport: asyncio.Transport

# This class is the data that is saved in hass.data[VisonicEntryKey]
#    PANELS:       These are the various client entries in the configuration
#    SERVERS:      These are the various tcp server entries in the configuration
#    DISCOVERIES:  These are the discoveries made by the tcp server
class VisonicDomainData(TypedDict):
    """Visonic domain data."""
    PANELS: dict[str, VisonicConfigData]         # Panels
    SERVERS: dict[str, VisonicServerData]        # TCP Servers
    DISCOVERIES: dict[str, VisonicDiscoveryData] # TCP Discoveries from TCP Servers

# Create the types for the Configuration Parameter Entry
VisonicEntryKey: HassEntryKey[VisonicDomainData] = HassEntryKey(DOMAIN)
type VisonicConfigEntry = ConfigEntry[VisonicConfigData]

# Data class that the coordinator uses to manage the data passed to the entities
@dataclass(frozen=True, slots=True)
class VisonicCoordinatorData:
    """Coordinator data for passing to entities. Make it frozen i.e. immutable."""

    # This is the data that is created as part of the coordinator data capture activities and used by all entities

    connected: bool = False      # Is the lower level connected to the panel
    ispowermaster: bool = False  # Is the panel confirmed as a powermaster
    mode: str = ""               # A string shown as an attribute to tell the user the status of the connection
    model: str | None = None     # The reported panel model
    statusdict: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    panelstate: PanelState | None = None  # Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    partition_armcode: Mapping[int, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    partition_show_keypad: Mapping[int, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    partition_code_arm_required: Mapping[int, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    partition_siren: Mapping[int, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    partition_dict: Mapping[int, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    zones: Mapping[int, SensorState] = field(default_factory=lambda: MappingProxyType({}))
    switch: Mapping[int, SwitchState] = field(default_factory=lambda: MappingProxyType({}))
    device: Mapping[int, DeviceState] = field(default_factory=lambda: MappingProxyType({}))

    def _convert_recursive(self, convert_to_name: bool, obj: Any) -> Any:
        """Recursively convert Mappings to dicts and sets to lists."""
        if obj is None:
            return None
        if convert_to_name and isinstance(obj, Enum):
            return obj.name.capitalize()
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Mapping):
            mapping_obj = cast(Mapping[object, object], obj)
            return {
                k: self._convert_recursive(convert_to_name, v)
                for k, v in mapping_obj.items()
            }
        if isinstance(obj, (set, list, tuple)):
            iterable_obj = cast(Iterable[object], obj)
            return [self._convert_recursive(convert_to_name, v) for v in iterable_obj]
        return obj

    def as_dict(self, convert_to_name: bool = False) -> dict[str, Any]:
        """Return a fully mutable dictionary representation."""
        return {
            f.name: self._convert_recursive(convert_to_name, getattr(self, f.name))
            for f in fields(self)
        }

class CVP_Status(IntEnum):
    """Status of the comms_visonic_protocol i.e. CVP connection."""
    # Used in _connection_status callback
    DISCONNECTED = 1
    CONNECTED = 2
    NO_CONNECTION_MADE = 3
    CONNECTION_PENDING = 4
    EXCEPTION = 5

class Connection_Status(IntEnum):
    """Track state in the client."""
    DISCONNECTED = 1
    CONNECTED = 2
    NO_CONNECTION_MADE = 3
    CONNECTION_PENDING = 4
    EXCEPTION = 5
    READY_TO_START = 100
    BAUD_CHANGE = 101
    BAUD_CHANGE_RESET_PROTOCOL = 102
    INITIAL_CREATE_PROTOCOL = 103
    INITIAL_CREATE_TRANSPORT = 104
    RETRY_CREATE_TRANSPORT = 105
    NO_OPERATION = 106
    CLOSE_CONNECTION = 107
    STOP = 108
    RESTART = 109

    @classmethod
    def as_set(cls):
        """Return a set of all members."""
        return set(cls)

# The set of commands that can be used to arm and disarm the panel
class AlarmPanelCommand(IntEnum):
    """Enumeration of commands to arm and disarm the panel."""
    # Include all case variations for the panel_command HA service
    #   The values used in the code have to be first
    DISARM = 1
    ARM_HOME = 2
    ARM_AWAY = 3
    ARM_HOME_INSTANT = 4
    ARM_AWAY_INSTANT = 5
    MUTE = 6
    TRIGGER = 7
    FIRE = 8
    EMERGENCY = 9
    PANIC = 10
    ARM_HOME_BYPASS = 11
    ARM_AWAY_BYPASS = 12

    @classmethod
    def members(cls) -> list[str]:
        """Return enum members as lowercase names."""
        return [m.name.lower() for m in cls]

    @classmethod
    def from_name(cls, name: str) -> Self | None:
        """Get enum member from string name."""
        try:
            return cls[name.upper()]
        except KeyError:
            return None

# The result of using the set of commands
class AlarmCommandStatus(IntEnum):
    """Enumeration of command execution results."""
    SUCCESS = 1
    FAIL_DOWNLOAD_IN_PROGRESS = 2
    FAIL_INVALID_CODE = 3
    FAIL_USER_CONFIG_PREVENTED = 4
    FAIL_INVALID_STATE = 5
    FAIL_SWITCH_PROBLEM = 6
    FAIL_PANEL_CONFIG_PREVENTED = 7
    FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED = 8
    FAIL_PANEL_NO_CONNECTION = 9
    FAIL_ENTITY_INCORRECT = 10
    FAIL_INVALID_RETURN = 11

# The set of panel states, in order of importance for multiple partitions
class AlarmPanelStatus(IntEnum):
    """Enumeration of panel status states, ordered by importance for multiple partitions."""
    UNKNOWN = 1
    DISARMED = 2
    ARMING_HOME = 3
    ARMING_AWAY = 4
    ENTRY_DELAY = 5
    ARMED_HOME = 6
    ARMED_AWAY = 7
    ARMED_HOME_BYPASS = 8
    ARMED_AWAY_BYPASS = 9
    ARMED_HOME_INSTANT = 10
    ARMED_AWAY_INSTANT = 11
    ENTRY_DELAY_INSTANT = 12
    USER_TEST = 13
    DOWNLOADING = 14
    INSTALLER = 15
    TRIGGERED = 20                     # The panel does not report this directly, it is derived in the code

# List of sensor types
class AlarmSensorType(IntEnum):
    """Enumeration of sensor types."""
    IGNORED = 1
    UNKNOWN = 2
    MOTION = 3
    MAGNET = 4
    CAMERA = 5
    WIRED = 6
    SMOKE = 7
    FLOOD = 8
    GAS = 9
    VIB = 10
    SHOCK = 11
    TEMP = 12
    SOUND = 13
    GLASS = 14
    #POWER_LINK = 15
    PANEL = 100
    COMMS = 101
    TOKEN = 102
    SIREN = 103
    SWITCH = 200

class EnumType(IntEnum):
    """Common implementation of class methods."""

    @classmethod
    def members(cls) -> list[str]:
        """Return enum members as lowercase names."""
        return [m.name.lower() for m in cls]

    @classmethod
    def from_name(cls, name: str) -> Self | None:
        """Get enum member from string name."""
        try:
            return cls[name.upper()]
        except KeyError:
            return None

# The set of switch commands
class AlarmSwitchCommand(EnumType):
    """Enumeration of switch commands."""
    OFF = 1
    ON = 2
    DIMMER = 3
    BRIGHTEN = 4

# This class represents the reasons that could trigger an alarm
#     These could be set even if the siren is not sounding, depending on the panel settings
######### These need to match the "siren_sounding" selector in the language json file ##################
class TriggerAlarmType(EnumType):
    """Enumeration of alarm types that can trigger an alarm condition."""
    UNKNOWN = 1
    NONE = 2
    INTRUDER = 3
    TAMPER = 4
    PANIC = 5
    FIRE = 6
    EMERGENCY = 7
    GAS = 8
    FLOOD = 9
    SWITCH = 10

@dataclass(frozen=True)
class PanelStateData:
    """Return class for update."""
    connected: bool = False
    show_keypad: bool = False
    code_arm_required: bool = True
    is_power_master: bool = False
    trigger_device: tuple[int, TriggerAlarmType] = (0, TriggerAlarmType.NONE)
    alarm_state: AlarmControlPanelState = AlarmControlPanelState.DISARMED
    panel_state: AlarmPanelStatus = AlarmPanelStatus.UNKNOWN
    attributes: dict[str, Any] = field(default_factory=dict)
    last_event_name: str | None = None

##############################################################################################################################################################################################################################################
##########################  Panel Event coordinator to manage A5, B0.24 and A7 panel state and event data ####################################################################################################################################
##############################################################################################################################################################################################################################################

class PanelCondition(IntEnum):
    """Panel condition codes for event handling."""
    # These match AlCondition (pyvisonic library) for easy cast.
    PUSH_CHANGE = 1               # This causes the client to update the frontend etc but it does not send out an HA Event
    ZONE_UPDATE = 2
    PANEL_UPDATE = 3
    PANEL_RESET = 4
    PIN_REJECTED = 5
    DOWNLOAD_SUCCESS = 6
    DOWNLOAD_TIMEOUT = 7
    WATCHDOG_TIMEOUT_GIVINGUP = 8
    WATCHDOG_TIMEOUT_RETRYING = 9
    NO_DATA_FROM_PANEL = 10
    COMMAND_REJECTED = 11
    STARTUP_SUCCESS = 12        # In the client this triggers the setting of the string name in the Config settings to the panel type
    IMAGE_UPDATE = 13
    # These start at 100 to ensure uniqueness when mixing with AlCondition (pyvisonic library).
    #  Used for AlarmPanelEventActionList and event dispatching.
    CHECK_ARM_DISARM_COMMAND = 100
    CHECK_BYPASS_COMMAND = 101
    CHECK_EVENT_LOG_COMMAND = 102
    CHECK_SWITCH_COMMAND = 103
    CONNECTION = 104
    PANEL_LOG_COMPLETE = 105
    PANEL_LOG_ENTRY = 106

class AvailableNotifications(StrEnum):
    """Available Notifications for Home Assistant front end."""

    # Enums changed but text remains to keep the same schema
    ALWAYS = "always"
    SIREN = "siren_sounding"
    RESET = "panel_reset"
    INVALID_PIN = "invalid_pin"
    PANEL = "panel_operation"
    CONNECTION = "connection_problem"
    BYPASS = "bypass_problem"
    IMAGE = "image_problem"
    EVENTLOG = "eventlog_problem"
    COMMAND = "command_not_sent"
    SWITCH = "switch_problem"

@dataclass(slots=True)
class CommandResult:
    """Client command result."""

    status: AlarmCommandStatus
    notify: AvailableNotifications
    message: str | None = None
    # These 2 are only used if status is SUCCESS
    partitions: set[int] | None = None
    did_bypass: bool = False

# Map the alarm panel states across to the Home Assistant states
PANEL_TO_HA_STATUS_MAP: dict[AlarmPanelStatus, AlarmControlPanelState] = {
    AlarmPanelStatus.UNKNOWN: AlarmControlPanelState.DISARMED,
    AlarmPanelStatus.DISARMED: AlarmControlPanelState.DISARMED,
    AlarmPanelStatus.ARMING_HOME: AlarmControlPanelState.ARMING,
    AlarmPanelStatus.ARMING_AWAY: AlarmControlPanelState.ARMING,
    AlarmPanelStatus.ENTRY_DELAY: AlarmControlPanelState.PENDING,
    AlarmPanelStatus.ENTRY_DELAY_INSTANT: AlarmControlPanelState.PENDING,
    AlarmPanelStatus.ARMED_HOME: AlarmControlPanelState.ARMED_HOME,
    AlarmPanelStatus.ARMED_AWAY: AlarmControlPanelState.ARMED_AWAY,
    AlarmPanelStatus.ARMED_HOME_BYPASS: AlarmControlPanelState.ARMED_HOME,
    AlarmPanelStatus.ARMED_AWAY_BYPASS: AlarmControlPanelState.ARMED_AWAY,
    AlarmPanelStatus.ARMED_HOME_INSTANT: AlarmControlPanelState.ARMED_HOME,
    AlarmPanelStatus.ARMED_AWAY_INSTANT: AlarmControlPanelState.ARMED_AWAY,
    AlarmPanelStatus.USER_TEST: AlarmControlPanelState.DISARMED,
    AlarmPanelStatus.DOWNLOADING: AlarmControlPanelState.DISARMED,
    AlarmPanelStatus.INSTALLER: AlarmControlPanelState.DISARMED,
}

