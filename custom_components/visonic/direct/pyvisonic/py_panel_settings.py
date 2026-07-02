"""Panel settings."""
from collections.abc import Callable
import logging
from typing import Any, Final, NamedTuple

from .py_const import NOBYPASSSTR
from .py_enum import EPROM, EVENT_TYPE, B0SubType, IndexName, PanelSetting
from .py_utils import toString

log = logging.getLogger(__name__)

###################################################################################
###  Panel Data to Retrieve using a combination of EPROM and                    ###
### (for PowerMaster Panels) B0 message data                                    ###
###################################################################################
# pmPanelSettingCodes represents the ways that we can get data to populate the PanelSettings
#   A PowerMax Panel only has 1 way and that is to download the EPROM = PMaxEPROM
#   A PowerMaster Panel has 3 ways:
#        1. Download the EPROM = PMasterEPROM
#        2. Ask the panel for a B0 panel settings message 0x51 e.g. 0x0800 sends the user codes  = PMasterB035Panel
#        3. Ask the panel for a B0 data message = PMasterB0Mess PMasterB0Index

# Zone names are translated using the language translation file. These need to match the keys in the translations.
pmZoneName = [
    "attic", "back_door", "basement", "bathroom", "bedroom", "child_room",
    "conservatory", "play_room", "dining_room", "downstairs",
    "emergency", "fire", "front_door", "garage", "garage_door",
    "guest_room", "hall", "kitchen", "laundry_room", "living_room",
    "master_bathroom", "master_bedroom", "office", "upstairs",
    "utility_room", "yard", "custom_1", "custom_2", "custom_3",
    "custom_4", "custom_5", "not_installed"
]

# These are conversion to string functions
def psc_lba(p):   # p = a list of bytearrays
    """Convert a list of bytearrays to a string with spaces."""
    s = ""
    for ba in p:
        s = s + toString(ba, "") + " "
    return s[:-1] if len(s) > 0 else s

def psc_dummy(p):
    """Dummpy print."""
    return p


class PanelSettingCodesType(NamedTuple):
    """Visonic Panel Settings Mapping Definition."""
    item: int | None
    mandatory: bool
    PMaxEPROM: EPROM | None       # Offset/Enum for PowerMax
    PMasterEPROM: EPROM | None    # Offset/Enum for PowerMaster
    PMasterB035Panel: int | None
    PMasterB042Panel: int | None
    PMasterB0Mess: B0SubType | None
    PMasterB0Index: IndexName | None
    tostring: Callable[[Any], Any]
    default: Any

# PanelSettingCodesType = collections.namedtuple('PanelSettingCodesType', 'item mandatory PMaxEPROM PMasterEPROM PMasterB035Panel PMasterB042Panel PMasterB0Mess PMasterB0Index tostring default')
# For PMasterB0Mess there is an assumption that the message type is 0x03, and this is the subtype
#       PMasterB0Index index 3 is Sensor data, I should have an enum for this
#       mandatory : When True, this setting means that the data is mandatory before creating sensors when trying for Powerlink emulation mode
# These are used to create the self.PanelSettings dictionary to create a common set of settings across the different ways of obtaining them
pmPanelSettingCodes : Final[dict[PanelSetting, PanelSettingCodesType]] = {
                       #                                  item mandatory PMaxEPROM            PMasterEPROM        PMasterB035Panel PMasterB042Panel PMasterB0Mess          PMasterB0Index   tostring       default
    PanelSetting.UserCodes        : PanelSettingCodesType( None,  True, EPROM.USERCODE_MAX,   EPROM.USERCODE_MAS,   None  ,         0x0008,         None,                   None,             toString ,     bytearray([0,0]) ),
    PanelSetting.PartitionData    : PanelSettingCodesType( None,  True, EPROM.PART_ZONE_DATA, EPROM.PART_ZONE_DATA, None  ,         0x0036,         None,                   None,             toString ,     bytearray()),
    PanelSetting.ZoneNames        : PanelSettingCodesType( None,  True, EPROM.ZONENAME_MAX,   EPROM.ZONENAME_MAS,   None  ,         None  ,         B0SubType.ZONE_NAMES,   IndexName.ZONES,  toString ,     bytearray()),
    PanelSetting.ZoneNameString   : PanelSettingCodesType( None, False, EPROM.ZONE_STR_NAM,   EPROM.ZONE_STR_NAM,   None  ,         0x000D,         None,                   None,             psc_dummy,     [] ), # pmZoneName[0:21] ),       # The string names themselves
    PanelSetting.ZoneCustNameStr  : PanelSettingCodesType( None, False, EPROM.ZONE_STR_EXT,   EPROM.ZONE_STR_EXT,   None  ,         0x0042,         None,                   None,             psc_dummy,     [] ), # pmZoneName[21:31] ),      # The string names themselves
    PanelSetting.ZoneTypes        : PanelSettingCodesType( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.ZONE_TYPES,   IndexName.ZONES,  toString ,     bytearray()),
    PanelSetting.ZoneExt          : PanelSettingCodesType( None,  True, None,                 EPROM.ZONEEXT_MAS,    None  ,         None  ,         None,                   None,             toString ,     bytearray()),
    PanelSetting.DeviceTypesZones : PanelSettingCodesType( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.DEVICE_TYPES, IndexName.ZONES,  toString ,     bytearray()),
    PanelSetting.DeviceTypesSirens: PanelSettingCodesType( None, False, None,                 None,                 None  ,         None  ,         B0SubType.DEVICE_TYPES, IndexName.SIRENS, toString ,     bytearray()),
    PanelSetting.HasPGM           : PanelSettingCodesType( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.SYSTEM_CAP,   IndexName.PGM,    psc_dummy,     []),
    PanelSetting.ZoneDelay        : PanelSettingCodesType( None,  True, None,                 EPROM.ZONE_DEL_MAS,   None  ,         None  ,         None,                   None,             toString ,     bytearray(64)),     # Initialise to 0s so it passes the I've got it from the panel test until I know how to get this using B0 data
    PanelSetting.ZoneData         : PanelSettingCodesType( None,  True, EPROM.ZONEDATA_MAX,   EPROM.ZONEDATA_MAS,   None  ,         None  ,         None,                   None,             toString ,     bytearray()),
    PanelSetting.ZoneEnrolled     : PanelSettingCodesType( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.SENSOR_ENROL, IndexName.ZONES,  psc_dummy,     [] ),               # Powermax relies on EPROM data or A5 message to provide sensor enrol
    PanelSetting.PanelBypass      : PanelSettingCodesType( 0,     True, EPROM.PANEL_BYPASS,   EPROM.PANEL_BYPASS,   None  ,         None  ,         None,                   None,             psc_dummy,     [NOBYPASSSTR]),
    PanelSetting.PanelDownload    : PanelSettingCodesType( None, False, EPROM.INSTALDLCODE,   EPROM.INSTALDLCODE,   None  ,         0x000f,         None,                   None,             psc_dummy,     bytearray()),
    PanelSetting.PanelSerial      : PanelSettingCodesType( None, False, EPROM.PANEL_SERIAL,   EPROM.PANEL_SERIAL,   None  ,         0x0002,         None,                   None,             toString,      bytearray() ),
    PanelSetting.PanelName        : PanelSettingCodesType( None, False, None,                 None,                 None  ,         0x003C,         None,                   None,             toString ,     bytearray() ),
    PanelSetting.PartitionEnabled : PanelSettingCodesType( None,  True, EPROM.PART_ENABLED,   EPROM.PART_ENABLED,   None  ,         0x0030,         None,                   None,             toString ,     bytearray() ),
    PanelSetting.ZoneChime        : PanelSettingCodesType( None,  True, None,                 None,                 None  ,         0x0033,         None,                   None,             toString ,     bytearray() )
}

#   PanelSetting.TestTest         : PanelSettingCodesType( None, None,               None,                       None  ,         0x0031,         None,           None,            toString ,     bytearray() ),
#   PanelSetting.Keypad_1Way      : PanelSettingCodesType( None, EPROM.KEYPAD_1_MAX, None,                       None  ,         None  ,         None,           None,            toString ,     bytearray()),      # PowerMaster Panels do not have 1 way keypads
#   PanelSetting.Keypad_2Way      : PanelSettingCodesType( None, EPROM.KEYPAD_2_MAX, EPROM.KEYPAD_MAS,           None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.KeyFob           : PanelSettingCodesType( None, "KeyFobsPMax",      "",                         None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.Sirens           : PanelSettingCodesType( None, EPROM.SIRENS_MAX,   EPROM.SIRENS_MAS,           None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.AlarmLED         : PanelSettingCodesType( None, None,               "AlarmLED",                 None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.ZoneSignal       : PanelSettingCodesType( None, "ZoneSignalPMax",   "",                         None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.PanicAlarm       : PanelSettingCodesType( 0,    "panicAlarm",       "panicAlarm",               None  ,         None  ,         None,           None,            psc_dummy,     [False]),
#   PanelSetting.PanelModel       : PanelSettingCodesType( 0,    EPROM.PANEL_MODEL_CODE, EPROM.PANEL_MODEL_CODE, None  ,         None  ,         None,           None,            psc_lba  ,     [bytearray([0,0,0,0])]),


###################################################################################
##########################  Known Sensor Types ####################################
###################################################################################

# Default Sensor Zone Types
pmZoneTypeKey = ( "non-alarm", "emergency", "flood", "gas", "delay_1", "delay_2", "interior_follow", "perimeter", "perimeter_follow",
                "24_hours_silent", "24_hours_audible", "fire", "interior", "home_delay", "temperature", "outdoor", "undefined" )

# Map them to Events. When a sensor is triggered, we can use the sensor/zone type to decide what Event to trigger. This is used for B0 B0SubType.PANEL_STATE_3 messages.
pmMapZoneType = {
    pmZoneTypeKey[0]  : EVENT_TYPE.NONE,
    pmZoneTypeKey[1]  : EVENT_TYPE.EMERGENCY,
    pmZoneTypeKey[2]  : EVENT_TYPE.FLOOD_ALERT,
    pmZoneTypeKey[3]  : EVENT_TYPE.GAS_ALERT,
    pmZoneTypeKey[4]  : EVENT_TYPE.ALARM_PERIMETER,
    pmZoneTypeKey[5]  : EVENT_TYPE.ALARM_PERIMETER,
    pmZoneTypeKey[6]  : EVENT_TYPE.ALARM_INTERIOR,
    pmZoneTypeKey[7]  : EVENT_TYPE.ALARM_PERIMETER,
    pmZoneTypeKey[8]  : EVENT_TYPE.ALARM_PERIMETER,
    pmZoneTypeKey[9]  : EVENT_TYPE.NONE,
    pmZoneTypeKey[10] : EVENT_TYPE.NONE,
    pmZoneTypeKey[11] : EVENT_TYPE.FIRE,
    pmZoneTypeKey[12] : EVENT_TYPE.ALARM_INTERIOR,
    pmZoneTypeKey[13] : EVENT_TYPE.NONE,
    pmZoneTypeKey[14] : EVENT_TYPE.NONE,
    pmZoneTypeKey[15] : EVENT_TYPE.NONE,
    pmZoneTypeKey[16] : EVENT_TYPE.NONE
}

# Default Sensor Chime
pmZoneChimeKey = ("chime_off", "melody_chime", "zone_name_chime")
