"""Sensor types."""


from enum import StrEnum
import logging
from typing import Any, Final, NamedTuple

from .py_const import (
    TEXT_AC_FAIL,
    TEXT_COMM_FAIL,
    TEXT_FUSE,
    TEXT_JAMMING,
    TEXT_LINE_FAIL,
    TEXT_NONE,
    TEXT_NOT_ACTIVE,
    TEXT_TAMPER,
)
from .py_enum import AlDeviceType, AlSensorCondition

log = logging.getLogger(__name__)
KeyfobType: Final[dict[int, str]] = {
    0x05: 'MCT-237',
    0x06: 'MCT-237',
    0x07: 'MCT-237',
    0x0a: 'MCT-234',
}

# These functions must exist inside the Sensor Class, they must only have a single parameter. NO_ACTION will fail and not make the call.
class ZoneFunctions(StrEnum):
    """Zone Functions that need to exist in the sensor."""
    NO_ACTION   = ""
    PUSH_CHANGE = "pushChange"
    DO_TAMPER   = "do_tamper"
    DO_STATUS   = "do_status"
    DO_BATTERY  = "do_battery"
    DO_TRIGGER  = "do_trigger"
    DO_ZTRIP    = "do_ztrip"
    DO_ZTAMPER  = "do_ztamper"
    DO_BYPASS   = "do_bypass"
    DO_INACTIVE = "do_inactive"
    DO_MISSING  = "do_missing"
    DO_ONEWAY   = "do_oneway"

# The func values are looked up in the Sensor Class for a function call
# The problem values are in the language json file file for zone_trouble
# The parameter values are sent in with the function call (as the only parameter)
class ZoneEventActionCollection(NamedTuple):
    """Visonic Zone Event Action Definition."""
    func: ZoneFunctions  # Assuming this is your Enum
    problem: str
    parameter: Any | None

pmZoneEventAction: Final[dict[int, ZoneEventActionCollection]] = {
     0 : ZoneEventActionCollection(ZoneFunctions.NO_ACTION,   TEXT_NONE,        None ),                        # "None",
     1 : ZoneEventActionCollection(ZoneFunctions.DO_TAMPER,   TEXT_TAMPER,      True ),                        # "Tamper Alarm",
     2 : ZoneEventActionCollection(ZoneFunctions.DO_TAMPER,   TEXT_NONE,        False ),                       # "Tamper Restore",
     3 : ZoneEventActionCollection(ZoneFunctions.DO_STATUS,   TEXT_NONE,        True ),                        # "Zone Open",
     4 : ZoneEventActionCollection(ZoneFunctions.DO_STATUS,   TEXT_NONE,        False ),                       # "Zone Closed",
     5 : ZoneEventActionCollection(ZoneFunctions.DO_TRIGGER,  TEXT_NONE,        True ),                        # "Zone Violated (Motion)",
     6 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_NONE,        AlSensorCondition.PANIC ),     # "Panic Alarm",
     7 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_JAMMING,     AlSensorCondition.PROBLEM ),   # "RF Jamming",
     8 : ZoneEventActionCollection(ZoneFunctions.DO_TAMPER,   TEXT_TAMPER,      True ),                        # "Tamper Open",
     9 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_COMM_FAIL,   AlSensorCondition.PROBLEM ),   # "Communication Failure",
    10 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_LINE_FAIL,   AlSensorCondition.PROBLEM ),   # "Line Failure",
    11 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_FUSE,        AlSensorCondition.PROBLEM ),   # "Fuse",
    12 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_NOT_ACTIVE , AlSensorCondition.PROBLEM ),   # "Not Active" ,
    13 : ZoneEventActionCollection(ZoneFunctions.DO_BATTERY,  TEXT_NONE,        True ),                        # "Low Battery",
    14 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_AC_FAIL,     AlSensorCondition.PROBLEM ),   # "AC Failure",
    15 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_NONE,        AlSensorCondition.FIRE ),      # "Fire Alarm",
    16 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_NONE,        AlSensorCondition.EMERGENCY ), # "Emergency",
    17 : ZoneEventActionCollection(ZoneFunctions.DO_TAMPER,   TEXT_TAMPER,      True ),                        # "Siren Tamper",
    18 : ZoneEventActionCollection(ZoneFunctions.DO_TAMPER,   TEXT_NONE,        False ),                       # "Siren Tamper Restore",
    19 : ZoneEventActionCollection(ZoneFunctions.DO_BATTERY,  TEXT_NONE,        True ),                        # "Siren Low Battery",
    20 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_AC_FAIL,     AlSensorCondition.PROBLEM ),   # "Siren AC Fail",
}

class ZoneDeviceType(NamedTuple):
    """Zone Device Type tuple."""
    name : str
    func : AlDeviceType

#ZoneDeviceType = collections.namedtuple("ZoneDeviceType", 'name func' )
pmSirenMaster : Final[dict[int, ZoneDeviceType]] = {
    0x01 : ZoneDeviceType("SR-730 PG2 Outdoor Siren", AlDeviceType.EXTERNAL ),
    0x02 : ZoneDeviceType("SR-720 PG2 Indoor Siren", AlDeviceType.INTERNAL )
}

pmKeyfobMaster : Final[dict[int, ZoneDeviceType]] = {
    0x01 : ZoneDeviceType("Keyfob", AlDeviceType.EXTERNAL ),
    0x02 : ZoneDeviceType("KF-235 PG2", AlDeviceType.EXTERNAL )
}

pmKeypadMaster : Final[dict[int, ZoneDeviceType]] = {
    0x05: ZoneDeviceType("KP-160 PG2", AlDeviceType.EXTERNAL)
}
