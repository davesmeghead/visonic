"""Enums for the Pyenum Pyeprom Pyhelper and Pyvisonic custom component."""
from enum import Enum, IntEnum, StrEnum, auto, unique
from typing import Self


class PanelTypeEnum(Enum):
    """Panel Types supported by the integration."""
    POWER_MAX = auto()
    POWER_MASTER = auto()

class CFG(Enum):
    """Configuration options for the panel."""
    SUPPORTED = auto()
    KEEPALIVE = auto()
    AB_SUPPORTED = auto()
    DLCODE_1 = auto()
    DLCODE_2 = auto()
    DLCODE_3 = auto()
    PARTITIONS = auto()
    EVENTS = auto()
    KEYFOBS = auto()
    ONE_WKEYPADS = auto()
    TWO_WKEYPADS = auto()
    SIRENS = auto()
    USERCODES = auto()
    REPEATERS = auto()
    PROXTAGS = auto()
    ZONECUSTOM = auto()
    DEV_ZONE_TYPES = auto()
    WIRELESS = auto()
    WIRED = auto()
    SWITCH = auto()
    PGM = auto()
    AUTO_ENROL = auto()
    AUTO_SYNCTIME = auto()
    POWERMASTER = auto()
    INIT_SUPPORT = auto()
    EPROM_DOWNLOAD = auto()

@unique
class SEQUENCE(IntEnum):
    """B0 sequence types."""
    SUB = 2
    MAIN = 3
    UNDEFINED = 1000

@unique
class RAW(IntEnum):
    """Raw data sizes."""
    BITS = 1
    BYTE = 8
    WORD = 16
    LONG_WORD = 32
    FIVE_BYTE = 40
    SIX_BYTE = 48
    TEN_BYTE = 80
    UNDEFINED = 1000

@unique
class IndexName(IntEnum):
    """Index types for various device categories."""
    # Index name.
    # This came from b0 35 51 01 on Powermater-10
    REPEATERS = 0
    PANIC_BUTTONS = 1
    SIRENS = 2
    ZONES = 3
    KEYPADS_TWO_WAY = 4
    KEYFOBS = 5
    USERS = 6
    SWITCHES = 7
    GSM_MODULES = 8
    POWERLINK = 9
    PROXTAGS = 10
    PGM = 11
    PANEL = 12
    GUARDS = 13
    PARTITIONS = 14
    UNK15 = 15
    UNK16 = 16
    EXPANDER_33 = 17
    IOV = 18
    UNK19 = 19
    UNK20 = 20
    KEYPADS_ONE_WAY = 21 # Powermax panels only
    MIXED = 255
    UNDEFINED = 1000

# These are the panel settings to keep a track of, most come from pmPanelSettingCodes and the EPROM/B0
@unique
class PanelSetting(IntEnum):
    """Panel Settings used in EPROM and B0 SubType messages."""
    UserCodes          = 1
    PanelSerial        = 2
    Keypad_1Way        = 3
    Keypad_2Way        = 4
    KeyFob             = 5
    Sirens             = 6
    AlarmLED           = 7
    PartitionData      = 8
    ZoneChime          = 9
    ZoneNames          = 10
    ZoneTypes          = 11
    ZoneExt            = 12
    ZoneDelay          = 13
    ZoneSignal         = 14
    ZoneData           = 15
    ZoneEnrolled       = 16
    PanicAlarm         = 17
    PanelBypass        = 18
    PanelModel         = 19
    PanelDownload      = 20
    DeviceTypesZones   = 21
    ZoneNameString     = 22
    PartitionEnabled   = 23
    ZoneCustNameStr    = 24
    PanelName          = 25
    SirenEnrolled      = 26
    DeviceTypesSirens  = 27
    HasPGM             = 28
    TestTest           = 200

@unique
class MessagePriority(IntEnum):
    """Message Priority levels."""
    DELETE_ALL = -1
    VITAL      = 0
    IMMEDIATE  = 1
    ACK        = 2
    URGENT     = 3
    NORMAL     = 4

@unique
class DataType(IntEnum):
    """Data Types used in EPROM and B0 SubType messages."""
    # Command 0x35 and 0x42 Message data data types.
    ZERO_PADDED_STRING = 0
    DIRECT_MAP_STRING = 1
    FF_PADDED_STRING = 2
    DOUBLE_LE_INT = 3
    INTEGER = 4
    UNDEFINED_1 = 5
    STRING = 6
    SPACE_PADDED_STRING = 8
    SPACE_PADDED_STRING_LIST = 10

    @staticmethod
    def validate(i: int) -> bool:
        """Validate if the integer is a valid DataType."""
        return i in DataType._value2member_map_

@unique
class EVENT_TYPE(IntEnum):
    """Event Types from the panel."""
    # A single value is in the A7 message that denotes the alarm / trouble status.  There could be up to 4 messages in A7.
    NOT_DEFINED = -1
    NONE = 0x00

    ALARM_INTERIOR = 0x01
    ALARM_PERIMETER = 0x02
    ALARM_DELAY = 0x03
    ALARM_SILENT_24H = 0x04
    ALARM_AUDIBLE_24H = 0x05
    TAMPER_SENSOR = 0x06
    TAMPER_PANEL = 0x07
    TAMPER_ALARM_A = 0x08
    TAMPER_ALARM_B = 0x09
    COMMUNICATION_LOSS = 0x0A

    PANIC_KEYFOB = 0x0B
    PANIC_PANEL = 0x0C
    DURESS = 0x0D
    CONFIRM_ALARM = 0x0E
    GENERAL_TROUBLE = 0x0F
    GENERAL_TROUBLE_RESTORE = 0x10

    ALARM_INTERIOR_RESTORE = 0x11
    ALARM_PERIMETER_RESTORE = 0x12
    ALARM_DELAY_RESTORE = 0x13
    ALARM_SILENT_24H_RESTORE = 0x14
    ALARM_AUDIBLE_24H_RESTORE = 0x15
    TAMPER_SENSOR_RESTORE = 0x16
    TAMPER_PANEL_RESTORE = 0x17
    TAMPER_ALARM_A_RESTORE = 0x18
    TAMPER_ALARM_B_RESTORE = 0x19
    COMMUNICATION_LOSS_RESTORE = 0x1A

    GENERAL_RESTORE = 0x1B
    ALARM_CANCEL = 0x1C
    TROUBLE_RESTORE = 0x1D

    FIRE = 0x20
    FIRE_RESTORE = 0x21
    EMERGENCY = 0x23
    EMERGENCY_RESTORE = 0x24            # Unconfirmed
    LOW_BATTERY = 0x29
    LOW_BATTERY_RESTORE = 0x2A
    AC_FAIL = 0x2B
    AC_FAIL_RESTORE = 0x2C
    PANEL_LOW_BATTERY = 0x2D
    PANEL_LOW_BATTERY_RESTORE = 0x2E
    RF_JAMMING = 0x2F
    RF_JAMMING_RESTORE = 0x30
    COMMUNICATION_FAILURE = 0x31
    COMMUNICATION_FAILURE_RESTORE = 0x32
    TELEPHONE_LINE_FAILURE = 0x33
    TELEPHONE_LINE_FAILURE_RESTORE = 0x34
    FUSE_FAILURE = 0x36
    FUSE_FAILURE_RESTORE = 0x37
    KEYFOB_LOW_BATTERY = 0x38
    KEYFOB_LOW_BATTERY_RESTORE = 0x39
    ENGINEER_RESET = 0x3A
    BATTERY_DISCONNECT = 0x3B
    KEYPAD_LOW_BATTERY = 0x3C
    KEYPAD_LOW_BATTERY_RESTORE = 0x3D
    LOW_BATTERY_ACK = 0x40
    GENERAL_LOW_BATTERY = 0x43

    GAS_ALERT = 0x49
    GAS_ALERT_RESTORE = 0x4A
    GAS_TROUBLE = 0x4B
    GAS_TROUBLE_RESTORE = 0x4C

    FLOOD_ALERT = 0x4D
    FLOOD_ALERT_RESTORE = 0x4E
    SWITCH_TROUBLE = 0x4F
    SWITCH_TROUBLE_RESTORE = 0x50

    ARMED_HOME = 0x51
    ARMED_AWAY = 0x52
    QUICK_ARMED_HOME = 0x53
    QUICK_ARMED_AWAY = 0x54
    DISARM = 0x55

    FORCE_ARM = 0x59
    SYSTEM_RESET = 0x60
    INSTALLER_PROGRAMMING = 0x61

# Packet creation parameters
@unique
class Packet(IntEnum):
    """Packet structure constants."""
    HEADER = 0x0D
    FOOTER = 0x0A
    POWERLINK_TERMINAL = 0x43

# The list of text strings that appear in the getPanelStatusDict extended status attributes
@unique
class PANEL_STATUS(StrEnum):
    """Panel status attribute names."""
    SIRENS = "Sirens"
    REPEATERS = "Repeaters"
    PANIC_BUTTONS = "Panic Buttons"
    KEYPADS = "Keypads"
    KEYFOBS = "Keyfobs"
    PROXTAGS = "Proxtags"
    DEVICES = "Devices"
    #PANEL_NAME = "Panel Name"
    DOOR_ZONES = "Door Zones"
    MOTION_ZONES = "Motion Zones"
    SMOKE_ZONES = "Smoke Zones"
    OTHER_ZONES = "Other Zones"

# Messages that we send to the panel
class Send(Enum):
    """Send message types."""
    BUMP = auto()
    START = auto()
    STOP = auto()
    EXIT = auto()
    DOWNLOAD_DL = auto()
    DOWNLOAD_TIME = auto()
    PANEL_DETAILS = auto()
    WRITE = auto()
    DL = auto()
    SETTIME = auto()
    SER_TYPE = auto()
    EVENTLOG = auto()
    ARM = auto()
    MUTE_SIREN = auto()
    STATUS = auto()
    STATUS_SEN = auto()
    BYPASSTAT = auto()
    ZONENAME = auto()
    SWITCH = auto()
    ZONETYPE = auto()
    BYPASSEN = auto()
    BYPASSDI = auto()
    GETTIME = auto()
    ALIVE = auto()
    RESTORE = auto()
    ENROL = auto()
    IMAGE_FB = auto()
    INIT = auto()
    SWITCH_NAMES = auto()
    GET_IMAGE = auto()
    ACK = auto()
    ACK_PLINK = auto()
    PM_REQUEST = auto()
    PM_REQUEST54 = auto()
    PM_REQUEST58 = auto()
    PM_SIREN_MODE = auto()
    PM_SIREN = auto()
    PL_BRIDGE = auto()
    PM_SETBAUD = auto()
    MSG4 = auto()
    MSGC = auto()
    UNKNOWN_0E = auto()
    MSGE = auto()
    PM_KEEPALIVE = auto()

# Messages that we receive from the panel
@unique
class Receive(IntEnum):
    """Receive message types."""
    DUMMY_MESSAGE     = 0x00
    ACKNOWLEDGE       = 0x02
    TIMEOUT           = 0x06
    UNKNOWN_07        = 0x07
    ACCESS_DENIED     = 0x08
    LOOPBACK_TEST     = 0x0B
    EXIT_DOWNLOAD     = 0x0F
    UNKNOWN_1F        = 0x1F
    NOT_USED          = 0x22
    DOWNLOAD_RETRY    = 0x25
    DOWNLOAD_SETTINGS = 0x33
    PANEL_INFO        = 0x3C
    DOWNLOAD_BLOCK    = 0x3F
    EVENT_LOG         = 0xA0
    ZONE_NAMES        = 0xA3
    STATUS_UPDATE     = 0xA5
    ZONE_TYPES        = 0xA6
    PANEL_STATUS      = 0xA7
    POWERLINK         = 0xAB
    SWITCH_NAMES      = 0xAC
    IMAGE_MGMT        = 0xAD
    POWERMASTER       = 0xB0
    REDIRECT          = 0xC0
    PROXY             = 0xE0
    PROXY_COMMAND     = 0xE1      # This is sent to the panel so if we get this back then it's a ringback of some kind
    UNKNOWN_F1        = 0xF1
    IMAGE_DATA        = 0xF4

# EProm Settings that are actively used in the integration
#     (there are others that are used purely for "Full Attributes" in the alarm entity)
class EPROM(Enum):
    """EPROM settings used in the integration."""
    PANEL_BYPASS = auto()
    PART_ZONE_DATA = auto()
    PART_ENABLED = auto()
    DISPLAY_NAME = auto()
    PANEL_TYPE_CODE = auto()
    PANEL_MODEL_CODE = auto()
    ZONE_STR_NAM = auto()
    ZONE_STR_EXT = auto()
    PANEL_SERIAL = auto()
    # Installer and Master
    MASTERCODE = auto()
    INSTALLERCODE = auto()
    MASTERDLCODE = auto()
    INSTALDLCODE = auto()
    # PowerMax specific
    SIRENS_MAX = auto()
    USERCODE_MAX = auto()
    ZONENAME_MAX = auto()
    ZONEDATA_MAX = auto()
    KEYFOB_MAX = auto()
    KEYPAD_1_MAX = auto()
    KEYPAD_2_MAX = auto()
    # PowerMaster specific
    USERCODE_MAS = auto()
    ZONENAME_MAS = auto()
    ZONEDATA_MAS = auto()
    ZONEEXT_MAS = auto()
    ZONE_DEL_MAS = auto()
    SIRENS_MAS = auto()
    KEYPAD_MAS = auto()
    # SWITCH
    SWITCH_LOCKOUT = auto()
    SWITCH_HOUSECODE = auto()
    SWITCH_BYARMAWAY = auto()
    SWITCH_BYARMHOME = auto()
    SWITCH_BYDISARM = auto()
    SWITCH_BYDELAY = auto()
    SWITCH_BYMEMORY = auto()
    SWITCH_BYKEYFOB = auto()
    SWITCH_ACTZONEA = auto()
    SWITCH_ACTZONEB = auto()
    SWITCH_ACTZONEC = auto()
    SWITCH_PULSETIME = auto()
    SWITCH_ZONE = auto()
    SWITCH_ZONENAMES = auto()

class B0SubType(Enum):
    """B0 message sub types."""
    INVALID_COMMAND = auto()

    WIRELESS_DEV_UPDATING = auto()
    WIRELESS_DEV_INACTIVE = auto()
    WIRELESS_DEV_CHANNEL = auto()
    WIRELESS_DEV_MISSING = auto()
    WIRELESS_DEV_ONEWAY = auto()

    TAMPER_ACTIVITY = auto()
    TAMPER_ALERT = auto()

    ZONE_STAT07 = auto()
    ZONE_OPENCLOSE = auto()
    ZONE_BYPASS = auto()
    ZONE_NAMES = auto()
    ZONE_TYPES = auto()

    SENSOR_ENROL = auto()
    SENSOR_UNKNOWN_1C = auto()
    SENSOR_UNKNOWN_30 = auto()
    SENSOR_UNKNOWN_32 = auto()
    SENSOR_UNKNOWN_34 = auto()

    DEVICE_TYPES = auto()
    TRIGGERED_ZONE = auto()
    ASSIGNED_PARTITION = auto()
    SYSTEM_CAP = auto()
    PANEL_STATE_1 = auto()         # Seems to send panel state with zone data, but zone data is weird
    PANEL_STATE_2 = auto()         # Seems to send panel state without zone data
    PANEL_STATE_3 = auto()         # Used for Panic, Emergency and Fire data from the panel
    PANEL_STATE_4 = auto()
    PANEL_STATE_5 = auto()
    PANEL_STATE_6 = auto()
    PANEL_SETTINGS_35 = auto()
    PANEL_SETTINGS_42 = auto()
    EVENT_LOG = auto()
    ASK_ME_1 = auto()
    ASK_ME_2 = auto()
    LEGACY_EVENT_LOG = auto()
    ZONE_TEMPERATURE = auto()
    ZONE_LUX = auto()
    ZONE_LAST_EVENT = auto()
    WIRED_STATUS_1 = auto()
    WIRED_STATUS_2 = auto()
    WIRED_DEVICES = auto()
    DEVICE_COUNTS = auto()
    TROUBLES = auto()
    REPEATERS_55 = auto()
    DEVICE_INFO = auto()
    GSM_STATUS = auto()
    KEYPADS = auto()
    DEVICES_5D = auto()
    SOFTWARE_VERSION = auto()
    SIRENS = auto()
    EPROM_AND_SW_VERSION = auto()
    KEEP_ALIVE = auto()
    SOME_LOG_75 = auto()
    IOVS = auto()
    TIMED_PGM_COMMAND = auto()  # for sending PGM on for timed period (secs) - 0d b0 00 7a 0b 31 80 01 ff 20 0b 04 00 01 3c 00 43 67 0a

# The result of using the set of commands
class AlCommandStatus(IntEnum):
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

# This is used to update the HA frontend and send out an HA Event
#   Only 1 to 14 are output to HA as events.
class AlCondition(IntEnum):
    """Enumeration of conditions to update the Home Assistant frontend and send events."""
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

# This class represents the panels trouble state
class AlTroubleType(IntEnum):
    """Enumeration of panel trouble states."""
    UNKNOWN = 1
    NONE = 2
    GENERAL = 3
    COMMUNICATION = 4
    BATTERY = 5
    POWER = 6
    JAMMING = 7
    TELEPHONE = 8

# This is used for when AlCondition is set to ZONE_UPDATE to update the HA
#   frontend and send out an HA Event
class AlSensorCondition(Enum):
    """Enumeration of sensor conditions to update the Home Assistant frontend and send events."""
    RESET = auto()
    STATE = auto()
    TAMPER = auto()
    TRIGGER = auto()
    BATTERY = auto()
    BYPASS = auto()
    PROBLEM = auto()
    ENROLLED = auto()
    FIRE = auto()
    EMERGENCY = auto()
    PANIC = auto()
    CAMERA = auto()
    ARMED = auto()
    RESTORE = auto()
    TEMPERATURE = auto()
    LUX = auto()

# List of device types
class AlDeviceType(IntEnum):
    """Enumeration of device types."""
    IGNORED = 1
    UNKNOWN = 2
    INTERNAL = 3
    EXTERNAL = 4

# List of termination reasons
class AlTerminationType(IntEnum):
    """Enumeration of connection termination reasons."""
    NO_DATA_FROM_PANEL_NEVER_CONNECTED = 1
    NO_DATA_FROM_PANEL_DISCONNECTED = 2
    CRC_ERROR = 3
    SAME_PACKET_ERROR = 4
    EXTERNAL_TERMINATION = 5
    NO_POWERLINK_FOR_PERIOD = 6

# The set of panel modes
class AlPanelMode(IntEnum):
    """Enumeration of panel operating modes."""
    UNKNOWN = 1
    STARTING = 2
    STANDARD = 3
    STANDARD_PLUS = 4
    POWERLINK = 5
    DOWNLOAD = 6
    STOPPED = 7
    MINIMAL_ONLY = 8
    POWERLINK_BRIDGED = 9
    PAUSED = 10

class EnumUtils(IntEnum):
    """Base class for enums with helper methods."""

    @classmethod
    def members(cls) -> list[str]:
        """Return enum members as lowercase names."""
        return [m.name.lower() for m in cls]

    @classmethod
    def as_dict(cls) -> dict[str, int]:
        """Return enum as a dictionary {name: value}."""
        return {m.name: m.value for m in cls}

    @classmethod
    def from_name(cls, name: str) -> Self | None:
        """Get enum member from string name."""
        try:
            return cls[name.upper()]
        except KeyError:
            return None

    @classmethod
    def from_value(cls, value: int) -> Self | None:
        """Get enum member from integer value."""
        try:
            return cls(value)
        except ValueError:
            return None

    def __str__(self) -> str:
        """String representation as lowercase name."""
        return self.name.lower()

# This class represents the reasons that could trigger an alarm
#     These could be set even if the siren is not sounding, depending on the panel settings
######### These need to match the "siren_sounding" selector in the language json file ##################
class AlAlarmType(EnumUtils):
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

# The set of panel states, in order of importance for multiple partitions
class AlPanelStatus(EnumUtils):
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

# The set of commands that can be used to arm and disarm the panel
class AlPanelCommand(EnumUtils):
    """Enumeration of commands to arm and disarm the panel."""
    # Include all case variations for the alarm_panel_command HA service
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

# The set of switch commands
class AlSwitchCommand(EnumUtils):
    """Enumeration of switch commands."""
    OFF = 1
    ON = 2
    DIMMER = 3
    BRIGHTEN = 4

class PanelErrorStates(IntEnum):
    """The panel error states, used in the sequencer."""
    AllGood               = 0
    AccessDeniedDownload  = 1
    AccessDeniedPin       = 2
    AccessDeniedStop      = 3
    AccessDeniedCommand   = 4
    Exit                  = 5
    TimeoutReceived       = 6
    DownloadRetryReceived = 7
    DespatcherException   = 8
    BeeZeroInvalidCommand = 9
