"""Direct connection common types."""
# What entities to create for each specific sensor
import dataclasses
import logging
from typing import Any, Final

from ..utils import hexify  # noqa: TID252
from ..visonic_entity_types import (  # noqa: TID252
    AlarmSensorType,
    SensorOnTimeout,
    SensorState,
    VisonicBinarySensorKey,
    VisonicFloatSensorKey,
    ZoneSensorDetails,
)

_LOGGER = logging.getLogger(__name__)

###################################################################################
######## These define what entities to create for each sensor type ################
###################################################################################

# fmt: off

# These are used to create the lists below
TAMPER_NO_TIMEOUT   = (VisonicBinarySensorKey.ZONE_TAMPER,   SensorOnTimeout.NO_TIMEOUT)
PROBLEM_NO_TIMEOUT  = (VisonicBinarySensorKey.ZONE_PROBLEM,  SensorOnTimeout.NO_TIMEOUT)
MISSING_NO_TIMEOUT  = (VisonicBinarySensorKey.ZONE_MISSING,  SensorOnTimeout.NO_TIMEOUT)
ONEWAY_NO_TIMEOUT   = (VisonicBinarySensorKey.ZONE_ONEWAY,   SensorOnTimeout.NO_TIMEOUT)
INACTIVE_NO_TIMEOUT = (VisonicBinarySensorKey.ZONE_INACTIVE, SensorOnTimeout.NO_TIMEOUT)
BATTERY_NO_TIMEOUT  = (VisonicBinarySensorKey.ZONE_BATTERY,  SensorOnTimeout.NO_TIMEOUT)
STATUS_TIMEOUT      = (VisonicBinarySensorKey.ZONE_STATUS,   SensorOnTimeout.STATE)
CONTACT_TIMEOUT     = (VisonicBinarySensorKey.ZONE_CONTACT,  SensorOnTimeout.STATE)
TRIGGER_MOTION      = (VisonicBinarySensorKey.ZONE_TRIGGER,  SensorOnTimeout.MOTION)
TRIGGER_OTHER       = (VisonicBinarySensorKey.ZONE_TRIGGER,  SensorOnTimeout.OTHER)
TEMP_NO_TIMEOUT     = (VisonicFloatSensorKey.ZONE_TEMP,      SensorOnTimeout.NO_TIMEOUT)
LUX_NO_TIMEOUT      = (VisonicFloatSensorKey.ZONE_LUX,       SensorOnTimeout.NO_TIMEOUT)

# Create the lists of entities used for each sensor type.  This gives flexibility as sensors are added to define the HA entities.
#   All sensors have TAMPER and PROBLEM entities (so these are not in the variable names)
#   Trigger and Status cannot appear in the same setting row (as they are both called "Zone")
#   Trigger and Contact are in the same row for SHOCK sensors that have both trigger and state, CONTACT is used as a different name (i.e. not "Zone")
BASIC_STATUS               = []
BATTERY_AND_STATUS_TIMEOUT = [BATTERY_NO_TIMEOUT, STATUS_TIMEOUT]
BATTERY_AND_TRIGGER_MOTION = [BATTERY_NO_TIMEOUT, TRIGGER_MOTION]
BATTERY_AND_TRIGGER_OTHER  = [BATTERY_NO_TIMEOUT, TRIGGER_OTHER]
#BATTERY_TEMP_LUX           = [BATTERY_NO_TIMEOUT, TRIGGER_MOTION, TEMP_NO_TIMEOUT, LUX_NO_TIMEOUT]  # Used for camera entities
BATTERY_TRIGGER_STATUS     = [BATTERY_NO_TIMEOUT, TRIGGER_OTHER, CONTACT_TIMEOUT]                   # Shock and Contact for SHOCK sensors
STATUS_ONLY_TIMEOUT        = [STATUS_TIMEOUT]
BATTERY_AND_TEMP           = [BATTERY_NO_TIMEOUT, TEMP_NO_TIMEOUT]

ALL_ENTITIES               = [TAMPER_NO_TIMEOUT, PROBLEM_NO_TIMEOUT]
POWERMASTER_WIRELESS       = [*ALL_ENTITIES, MISSING_NO_TIMEOUT, ONEWAY_NO_TIMEOUT, INACTIVE_NO_TIMEOUT]

#0x75 : ZoneSensorType("Next+ K9-85 MCW", AlarmSensorType.MOTION ), # Jan
#0x86 : ZoneSensorType("MCT-426", AlarmSensorType.SMOKE ), # Jan

pmZoneMax: Final[dict[int, ZoneSensorDetails]] = {
    0x6D : ZoneSensorDetails("MCX-601 Rptr", AlarmSensorType.COMMS,   ALL_ENTITIES + BASIC_STATUS),
    0x08 : ZoneSensorDetails("MCT-302"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x09 : ZoneSensorDetails("MCT-302"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x1A : ZoneSensorDetails("MCW-K980"    , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0x6A : ZoneSensorDetails("MCT-550"     , AlarmSensorType.FLOOD,   ALL_ENTITIES + BATTERY_AND_TRIGGER_OTHER),
    0x74 : ZoneSensorDetails("Next+ K9-85" , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0x75 : ZoneSensorDetails("MCT-302"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x76 : ZoneSensorDetails("MCT-302"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x7A : ZoneSensorDetails("MCT-550"     , AlarmSensorType.FLOOD,   ALL_ENTITIES + BATTERY_AND_TRIGGER_OTHER),
    0x85 : ZoneSensorDetails("MCT-302"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x86 : ZoneSensorDetails("MCT-302"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x87 : ZoneSensorDetails("MCT-302"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x8A : ZoneSensorDetails("MCT-550"     , AlarmSensorType.FLOOD,   ALL_ENTITIES + BATTERY_AND_TRIGGER_OTHER),
    0x93 : ZoneSensorDetails("Next MCW"    , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0x95 : ZoneSensorDetails("MCT-302"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x96 : ZoneSensorDetails("MCT-302"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x97 : ZoneSensorDetails("MCT-302"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x9A : ZoneSensorDetails("MCT-425"     , AlarmSensorType.SMOKE,   ALL_ENTITIES + BATTERY_AND_TRIGGER_OTHER),
    0xA3 : ZoneSensorDetails("Disc MCW"    , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xB3 : ZoneSensorDetails("Clip MCW"    , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xC0 : ZoneSensorDetails("Next K9-85"  , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xC3 : ZoneSensorDetails("Clip MCW"    , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xC4 : ZoneSensorDetails("Clip MCW"    , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xD3 : ZoneSensorDetails("Next MCW"    , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xD4 : ZoneSensorDetails("Next K9-85"  , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xD5 : ZoneSensorDetails("Next K9"     , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xE4 : ZoneSensorDetails("Next MCW"    , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xE5 : ZoneSensorDetails("Next K9-85"  , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xF3 : ZoneSensorDetails("MCW-K980"    , AlarmSensorType.MOTION,  ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xF5 : ZoneSensorDetails("MCT-302"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0xF9 : ZoneSensorDetails("MCT-100"     , AlarmSensorType.MAGNET,  ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0xFA : ZoneSensorDetails("MCT-427"     , AlarmSensorType.SMOKE,   ALL_ENTITIES + BATTERY_AND_TRIGGER_OTHER),
    0xFF : ZoneSensorDetails("Wired"       , AlarmSensorType.WIRED,   ALL_ENTITIES + STATUS_ONLY_TIMEOUT),
}

# SMD-426 PG2 (photoelectric smoke detector)
# SMD-427 PG2 (heat and photoelectric smoke detector)
# SMD-429 PG2 (Smoke and Heat Detector)
pmZoneMaster: Final[dict[int, ZoneSensorDetails]] = {
    0x01 : ZoneSensorDetails("Next PG2"      , AlarmSensorType.MOTION, POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_MOTION),
    0x03 : ZoneSensorDetails("Clip PG2"      , AlarmSensorType.MOTION, POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_MOTION),
    0x04 : ZoneSensorDetails("Next CAM PG2"  , AlarmSensorType.CAMERA, POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_MOTION),
    0x05 : ZoneSensorDetails("GB-502 PG2"    , AlarmSensorType.SOUND,  POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_OTHER),
    0x06 : ZoneSensorDetails("TOWER-32AM PG2", AlarmSensorType.MOTION, POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_MOTION),
    0x07 : ZoneSensorDetails("TOWER-32AMK9"  , AlarmSensorType.MOTION, POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_MOTION),
    0x08 : ZoneSensorDetails("TOWER-20AM PG2", AlarmSensorType.MOTION, POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_MOTION),
    0x0A : ZoneSensorDetails("TOWER CAM PG2" , AlarmSensorType.CAMERA, POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_MOTION),
    0x0B : ZoneSensorDetails("GB-502 PG2"    , AlarmSensorType.GLASS,  POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_OTHER),
    0x0C : ZoneSensorDetails("MP-802 PG2"    , AlarmSensorType.MOTION, POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_MOTION + [TEMP_NO_TIMEOUT]),
    0x0F : ZoneSensorDetails("MP-902 PG2"    , AlarmSensorType.MOTION, POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_MOTION),
    0x15 : ZoneSensorDetails("SMD-426 PG2"   , AlarmSensorType.SMOKE,  POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_OTHER),
    0x16 : ZoneSensorDetails("SMD-429 PG2"   , AlarmSensorType.SMOKE,  POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_OTHER),
    0x18 : ZoneSensorDetails("GSD-442 PG2"   , AlarmSensorType.SMOKE,  POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_OTHER),
    0x19 : ZoneSensorDetails("FLD-550 PG2"   , AlarmSensorType.FLOOD,  POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_OTHER),
    0x1A : ZoneSensorDetails("TMD-560 PG2"   , AlarmSensorType.TEMP,   POWERMASTER_WIRELESS + BATTERY_AND_TEMP),
    0x1C : ZoneSensorDetails("FLD-550 PG2"   , AlarmSensorType.FLOOD,  POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_OTHER),
    0x1E : ZoneSensorDetails("SMD-429 PG2"   , AlarmSensorType.SMOKE,  POWERMASTER_WIRELESS + BATTERY_AND_TRIGGER_OTHER + [TEMP_NO_TIMEOUT]),
    0x29 : ZoneSensorDetails("MC-302V PG2"   , AlarmSensorType.MAGNET, POWERMASTER_WIRELESS + BATTERY_AND_STATUS_TIMEOUT),
    0x2A : ZoneSensorDetails("MC-302 PG2"    , AlarmSensorType.MAGNET, POWERMASTER_WIRELESS + BATTERY_AND_STATUS_TIMEOUT),
    0x2C : ZoneSensorDetails("MC-303V PG2"   , AlarmSensorType.MAGNET, POWERMASTER_WIRELESS + BATTERY_AND_STATUS_TIMEOUT),
    0x2D : ZoneSensorDetails("MC-302V PG2"   , AlarmSensorType.MAGNET, POWERMASTER_WIRELESS + BATTERY_AND_STATUS_TIMEOUT),
    0x35 : ZoneSensorDetails("SD-304 PG2"    , AlarmSensorType.SHOCK,  POWERMASTER_WIRELESS + BATTERY_TRIGGER_STATUS),
    0xFA : ZoneSensorDetails("MC-302E PG2"   , AlarmSensorType.MAGNET, POWERMASTER_WIRELESS + BATTERY_AND_STATUS_TIMEOUT),
    0xFE : ZoneSensorDetails("Wired"         , AlarmSensorType.WIRED,  ALL_ENTITIES + STATUS_ONLY_TIMEOUT),
}

# Default Sensor Types if not found in the dictionaries above, this works for powermax but not sure about powermaster
pmZoneGeneric: Final[dict[int, ZoneSensorDetails]] = {
    0x0 : ZoneSensorDetails("Unknown", AlarmSensorType.VIB,    ALL_ENTITIES + BATTERY_AND_TRIGGER_OTHER),
    0x2 : ZoneSensorDetails("Unknown", AlarmSensorType.SHOCK,  ALL_ENTITIES + BATTERY_TRIGGER_STATUS),
    0x3 : ZoneSensorDetails("Unknown", AlarmSensorType.MOTION, ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0x4 : ZoneSensorDetails("Unknown", AlarmSensorType.MOTION, ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0x5 : ZoneSensorDetails("Unknown", AlarmSensorType.MAGNET, ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x6 : ZoneSensorDetails("Unknown", AlarmSensorType.MAGNET, ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x7 : ZoneSensorDetails("Unknown", AlarmSensorType.MAGNET, ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x8 : ZoneSensorDetails("Unknown", AlarmSensorType.MAGNET, ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0x9 : ZoneSensorDetails("Unknown", AlarmSensorType.MAGNET, ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT),
    0xA : ZoneSensorDetails("Unknown", AlarmSensorType.SMOKE,  ALL_ENTITIES + BATTERY_AND_TRIGGER_OTHER),
    0xB : ZoneSensorDetails("Unknown", AlarmSensorType.GAS,    ALL_ENTITIES + BATTERY_AND_TRIGGER_OTHER),
    0xC : ZoneSensorDetails("Unknown", AlarmSensorType.MOTION, ALL_ENTITIES + BATTERY_AND_TRIGGER_MOTION),
    0xF : ZoneSensorDetails("Unknown", AlarmSensorType.WIRED,  ALL_ENTITIES + STATUS_ONLY_TIMEOUT),
}

# fmt: on

class SensorStateExt(SensorState):
    """Extended SensorState with conversion function for direct connections."""

    @classmethod
    def from_dict(cls, is_power_master: bool, data: dict[str, Any]) -> SensorStateExt:
        """From a dict, fill in known fields. This function includes debug."""
        state = super().from_dict(data)   # dict
        return dataclasses.replace(
            state,
            enabled=data.get("enrolled"),
            sensor_type=cls._get_sensor_details(
                state.sensor_type_id,
                is_power_master,
            ),
        )

    @classmethod
    def _get_sensor_details(cls, sensor_id: int, is_power_master: bool) -> ZoneSensorDetails:
        """Convert the raw sensor type id to a ZoneSensorDetails."""

        #sensor_type = ZoneSensorDetails()
        tmpid = sensor_id & 0x0F

        if is_power_master: # PowerMaster models
            if sensor_id in pmZoneMaster:
                return pmZoneMaster[sensor_id]
            if tmpid in pmZoneGeneric:                 # Try this although it might just be for PowerMax panels
                _LOGGER.debug("[get_sensor_details] Found unknown powermaster sensor type %s, using pmZoneGeneric.", hexify(sensor_id))
                return pmZoneGeneric.get(tmpid)
            _LOGGER.debug("[get_sensor_details] Found unknown powermaster sensor type %s, defaulting to a simple state based sensor.", hexify(sensor_id))
            return ZoneSensorDetails("Unknown" , AlarmSensorType.UNKNOWN, POWERMASTER_WIRELESS + BATTERY_AND_STATUS_TIMEOUT)
        # PowerMax models
        if sensor_id in pmZoneMax:
            return pmZoneMax[sensor_id]
        if tmpid in pmZoneGeneric:
            _LOGGER.debug("[get_sensor_details] Found unknown powermax sensor type %s, using pmZoneGeneric.", hexify(sensor_id))
            return pmZoneGeneric.get(tmpid)
        _LOGGER.debug("[get_sensor_details] Found unknown powermax sensor type %s, defaulting to a simple state based sensor.", sensor_id)
        return ZoneSensorDetails("Unknown" , AlarmSensorType.UNKNOWN, ALL_ENTITIES + BATTERY_AND_STATUS_TIMEOUT)
