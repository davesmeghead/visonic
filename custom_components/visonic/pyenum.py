
from enum import StrEnum, IntEnum, Enum, auto, unique

class PanelTypeEnum(Enum):
    POWER_MAX = auto()
    POWER_MASTER = auto()

class CFG(Enum):
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
    X10 = auto()
    PGM = auto()
    AUTO_ENROL = auto()
    AUTO_SYNCTIME = auto()
    POWERMASTER = auto()
    INIT_SUPPORT = auto()
    EPROM_DOWNLOAD = auto()

@unique
class SEQUENCE(IntEnum):
    SUB = 2
    MAIN = 3
    UNDEFINED = 1000

@unique
class RAW(IntEnum):
    BITS = 1
    BYTES = 8
    WORDS = 16
    FIVE_BYTE = 40
    TEN_BYTE = 80
    UNDEFINED = 1000

@unique
class IndexName(IntEnum):
    # Index name.
    # This came from b0 35 51 01 on Powermater-10
    REPEATERS = 0
    PANIC_BUTTONS = 1
    SIRENS = 2
    ZONES = 3
    KEYPADS = 4
    KEYFOBS = 5
    USERS = 6
    X10_DEVICES = 7
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
    MIXED = 255
    UNDEFINED = 1000

# These are the panel settings to keep a track of, most come from pmPanelSettingCodes and the EPROM/B0 
@unique
class PanelSetting(IntEnum):
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
    IMMEDIATE = 0
    ACK       = 1
    URGENT    = 2
    NORMAL    = 3

# The text strings for the getEventData dictionary
@unique
class EventDataEnum(StrEnum):
    STATE   = "state"
    READY   = "ready"
    TAMPER  = "tamper"
    MEMORY  = "memory"
    SIREN   = "siren"
    BYPASS  = "bypass"
    ALARM   = "alarm" 
    TROUBLE = "trouble"
    BATTERY = "battery"

@unique
class DataType(IntEnum):
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
    def validate(i) -> bool:
        return (i >= 0 and i <= 4) or i == 6 or i == 8 or i == 10

    def __str__(self):
        return str(self._name_)

@unique
class EVENT_TYPE(IntEnum):
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
    X10_TROUBLE = 0x4F
    X10_TROUBLE_RESTORE = 0x50

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
    HEADER = 0x0D
    FOOTER = 0x0A
    POWERLINK_TERMINAL = 0x43

# The list of text strings that appear in the getPanelStatusDict extended status attributes
@unique
class PANEL_STATUS(StrEnum):
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
    X10PGM = auto()
    ZONETYPE = auto()
    BYPASSEN = auto()
    BYPASSDI = auto()
    GETTIME = auto()
    ALIVE = auto()
    RESTORE = auto()
    ENROL = auto()
    IMAGE_FB = auto()
    INIT = auto()
    X10NAMES = auto()
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
    DUMMY_MESSAGE     = 0x00     
    ACKNOWLEDGE       = 0x02     
    TIMEOUT           = 0x06     
    UNKNOWN_07        = 0x07     
    ACCESS_DENIED     = 0x08     
    LOOPBACK_TEST     = 0x0B
    EXIT_DOWNLOAD     = 0x0F     
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
    X10_NAMES         = 0xAC     
    IMAGE_MGMT        = 0xAD     
    POWERMASTER       = 0xB0     
    REDIRECT          = 0xC0 
    PROXY             = 0xE0  
    UNKNOWN_F1        = 0xF1
    IMAGE_DATA        = 0xF4

# EProm Settings that are actively used in the integration
#     (there are others that are used purely for "Full Attributes" in the alarm entity)
class EPROM(Enum):
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
    # X10                         
    X10_LOCKOUT = auto()
    X10_HOUSECODE = auto()
    X10_BYARMAWAY = auto()
    X10_BYARMHOME = auto()
    X10_BYDISARM = auto()
    X10_BYDELAY = auto()
    X10_BYMEMORY = auto()
    X10_BYKEYFOB = auto()
    X10_ACTZONEA = auto()
    X10_ACTZONEB = auto()
    X10_ACTZONEC = auto()
    X10_PULSETIME = auto()
    X10_ZONE = auto()
    X10_ZONENAMES = auto()

class B0SubType(Enum):
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
    PANEL_STATE = auto()           # Seems to send panel state with zone data, but zone data is weird
    PANEL_STATE_2 = auto()         # Seems to send panel state without zone data
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


