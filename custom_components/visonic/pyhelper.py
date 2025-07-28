
import os

# The defaults are set for use in Home Assistant.
#    If using MocroPython / CircuitPython then set these values in the environment
MicroPython = os.getenv("MICRO_PYTHON")

# Turn off auto code formatting when using black
# fmt: off

if MicroPython is not None:
    #import time as datetime
    from adafruit_datetime import datetime, timedelta
    import adafruit_logging as logging
    ABC = object
    Callable = object
    TypedDict = object
    List = object

    class ABC:
        pass

    def abstractmethod(f):
        return f

    # get the current date and time
    def _getUTCTime() -> datetime:
        return datetime.now() # UTC

    mylog = logging.getLogger(__name__)
    mylog.setLevel(logging.DEBUG)

else:
    import logging
    import datetime
    from abc import abstractmethod
    from datetime import datetime, timedelta, timezone
    from typing import Callable, List, TypedDict

    # get the current date and time
    def _getUTCTime() -> datetime:
        return datetime.now(tz=timezone.utc)

    #if DontUseLogger is None:
    mylog = logging.getLogger(__name__)

import sys
import time
import math
import json
import asyncio
import re
import inspect
from inspect import currentframe, getframeinfo, stack
import collections
from collections import namedtuple

try:
    from .pyconst import (AlIntEnum, NO_DELAY_SET, PanelConfig, AlPanelMode, AlPanelCommand, AlPanelStatus, AlTroubleType, AlPanelEventData,
                          AlAlarmType, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlPanelInterface, AlSensorDevice, 
                          AlLogPanelEvent, AlSensorType, AlSwitchDevice, TEXT_PANEL_MODEL, TEXT_POWER_MASTER)
    from .pyenum import EVENT_TYPE, EventDataEnum
except:
    from pyconst import (AlIntEnum, NO_DELAY_SET, PanelConfig, AlPanelMode, AlPanelCommand, AlPanelStatus, AlTroubleType, AlPanelEventData,
                         AlAlarmType, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlPanelInterface, AlSensorDevice, 
                         AlLogPanelEvent, AlSensorType, AlSwitchDevice, TEXT_PANEL_MODEL, TEXT_POWER_MASTER)
    from pyenum import EVENT_TYPE, EventDataEnum


# The reasons to cancel the siren
pmPanelCancelSet = ( EVENT_TYPE.DISARM, EVENT_TYPE.ALARM_CANCEL, EVENT_TYPE.GENERAL_RESTORE, EVENT_TYPE.FIRE_RESTORE, EVENT_TYPE.GAS_ALERT_RESTORE, EVENT_TYPE.GAS_TROUBLE_RESTORE )
# The reasons to ignore (not cancel) the siren
pmPanelIgnoreSet = ( EVENT_TYPE.ALARM_DELAY_RESTORE, EVENT_TYPE.CONFIRM_ALARM, EVENT_TYPE.ALARM_INTERIOR_RESTORE, EVENT_TYPE.ALARM_PERIMETER_RESTORE )

#pmPanelTamperSet = ( EVENT_TYPE.TAMPER_SENSOR, EVENT_TYPE.TAMPER_PANEL, EVENT_TYPE.TAMPER_ALARM_A, EVENT_TYPE.TAMPER_ALARM_B)

# These 2 dictionaries are subsets of pmLogEvent_t
pmPanelAlarmType_t = {
   EVENT_TYPE.NONE              : AlAlarmType.NONE,
   EVENT_TYPE.ALARM_INTERIOR    : AlAlarmType.INTRUDER,
   EVENT_TYPE.ALARM_PERIMETER   : AlAlarmType.INTRUDER,
   EVENT_TYPE.ALARM_DELAY       : AlAlarmType.INTRUDER,
   EVENT_TYPE.ALARM_SILENT_24H  : AlAlarmType.INTRUDER,
   EVENT_TYPE.ALARM_AUDIBLE_24H : AlAlarmType.INTRUDER,
   EVENT_TYPE.TAMPER_SENSOR     : AlAlarmType.TAMPER,
   EVENT_TYPE.TAMPER_PANEL      : AlAlarmType.TAMPER,
   EVENT_TYPE.TAMPER_ALARM_A    : AlAlarmType.TAMPER,
   EVENT_TYPE.TAMPER_ALARM_B    : AlAlarmType.TAMPER,
   EVENT_TYPE.PANIC_KEYFOB      : AlAlarmType.PANIC,
   EVENT_TYPE.PANIC_PANEL       : AlAlarmType.PANIC,
   EVENT_TYPE.FIRE              : AlAlarmType.FIRE,
   EVENT_TYPE.EMERGENCY         : AlAlarmType.EMERGENCY,
   EVENT_TYPE.GAS_ALERT         : AlAlarmType.GAS,
   EVENT_TYPE.FLOOD_ALERT       : AlAlarmType.FLOOD,
#   0x75 : AlAlarmType.TAMPER
}

pmPanelTroubleType_t = {
   EVENT_TYPE.NONE                   : AlTroubleType.NONE,
   EVENT_TYPE.COMMUNICATION_LOSS     : AlTroubleType.COMMUNICATION,
   EVENT_TYPE.GENERAL_TROUBLE        : AlTroubleType.GENERAL,
   EVENT_TYPE.LOW_BATTERY            : AlTroubleType.BATTERY,
   EVENT_TYPE.AC_FAIL                : AlTroubleType.POWER,
   EVENT_TYPE.PANEL_LOW_BATTERY      : AlTroubleType.BATTERY,
   EVENT_TYPE.RF_JAMMING             : AlTroubleType.JAMMING,
   EVENT_TYPE.COMMUNICATION_FAILURE  : AlTroubleType.COMMUNICATION, 
   EVENT_TYPE.TELEPHONE_LINE_FAILURE : AlTroubleType.TELEPHONE,
   EVENT_TYPE.FUSE_FAILURE           : AlTroubleType.POWER,
   EVENT_TYPE.KEYFOB_LOW_BATTERY     : AlTroubleType.BATTERY,
   EVENT_TYPE.BATTERY_DISCONNECT     : AlTroubleType.BATTERY,
   EVENT_TYPE.KEYPAD_LOW_BATTERY     : AlTroubleType.BATTERY,
   EVENT_TYPE.LOW_BATTERY_ACK        : AlTroubleType.BATTERY,
   EVENT_TYPE.GENERAL_LOW_BATTERY    : AlTroubleType.BATTERY,
}

PanelArmedStatusCollection = collections.namedtuple('PanelArmedStatusCollection', 'disarmed armed entry state eventmapping')
pmPanelArmedStatus = {               # disarmed armed entry         state
   0x00 : PanelArmedStatusCollection(  True, False, False, AlPanelStatus.DISARMED           , 85),  # Disarmed
   0x01 : PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_HOME        , -1),  # Arming Home
   0x02 : PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_AWAY        , -1),  # Arming Away
   0x03 : PanelArmedStatusCollection( False,  True,  True, AlPanelStatus.ENTRY_DELAY        , -1),  # Entry Delay
   0x04 : PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_HOME         , 81),  # Armed Home
   0x05 : PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_AWAY         , 82),  # Armed Away
   0x06 : PanelArmedStatusCollection(  True, False, False, AlPanelStatus.USER_TEST          , -1),  # User Test  (assume can only be done when panel is disarmed)

   0x07 : PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.DOWNLOADING        , -1),  # Downloading
   0x08 : PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.INSTALLER          , -1),  # Programming
   0x09 : PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.INSTALLER          , -1),  # Installer
   0x0A : PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_HOME         , 81),  # Armed Home Bypass   AlPanelStatus.ARMED_HOME_BYPASS
   0x0B : PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_AWAY         , 82),  # Armed Away Bypass   AlPanelStatus.ARMED_AWAY_BYPASS
   0x0C : PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.DISARMED           , 85),  # Ready
   0x0D : PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.DISARMED           , 85),  # Not Ready  (assume can only be done when panel is disarmed)
   0x0E : PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.UNKNOWN            , 85),  # 
   0x0F : PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.UNKNOWN            , 85),  # 
   # I don't think that the B0 message can command higher than 15            
   0x10 : PanelArmedStatusCollection(  True, False, False, AlPanelStatus.DISARMED           , 85),  # Disarmed Instant
   0x11 : PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_HOME        , -1),  # Arming Home Last 10 Seconds             ####### armed was False
   0x12 : PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_AWAY        , -1),  # Arming Away Last 10 Seconds             ####### armed was False
   0x13 : PanelArmedStatusCollection( False,  True,  True, AlPanelStatus.ENTRY_DELAY_INSTANT, -1),  # Entry Delay Instant
   0x14 : PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_HOME_INSTANT , 81),  # Armed Home Instant
   0x15 : PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_AWAY_INSTANT , 82)   # Armed Away Instant
}

INVALID_PARTITION = None

def hexify(v : int) -> str:
    return f"{hex(v)[2:]}"

# Convert byte array to a string of hex values
def toString(array_alpha: bytearray, gap = " "):
    return ("".join(("%02x"+gap) % b for b in array_alpha))[:-len(gap)] if len(gap) > 0 else ("".join("%02x" % b for b in array_alpha))

def toBool(val) -> bool:
    if type(val) == bool:
        return val
    elif type(val) == int:
        return val != 0
    elif type(val) == str:
        v = val.lower()
        return not (v == "no" or v == "false" or v == "0")
    #print("Visonic unable to decode boolean value {val}    type is {type(val)}")
    return False

def capitalize(s):
    return s[0].upper() + s[1:].lower()

def titlecase(s):
    return re.sub(r"[A-Za-z]+('[A-Za-z]+)?", lambda word: capitalize(word.group(0)), s)

# get the current date and time
def getTimeFunction() -> datetime:
    return datetime.now(timezone.utc).astimezone()

class vloggerclass:
    def __init__(self, loggy, panel_id : int = -1, detail : bool = False):
        self.detail = detail
        self.loggy = loggy
        if panel_id is not None and panel_id >= 0:
            self.panel_id_str = f"P{panel_id} "
        else:
            self.panel_id_str = ""
    
    def _createPrefix(self) -> str:
        previous_frame = currentframe().f_back.f_back
        (
            filepath,
            line_number,
            function,
            lines,
            index,
        ) = inspect.getframeinfo(previous_frame)
        filename = filepath[filepath.rfind('/')+1:]
        return f"{line_number:<5} " + (f"{function:<30} " if self.detail else "")
    
    def debug(self, msg, *args, **kwargs):
        try:
            s = self.panel_id_str + self._createPrefix()
            self.loggy.debug(s + (msg % args % kwargs))
        except Exception as ex:
            self.loggy.error(f"[vloggerclass] Exception  {ex}")
            
    def info(self, msg, *args, **kwargs):
        try:
            s = self.panel_id_str + self._createPrefix()
            self.loggy.info(s + (msg % args % kwargs))
        except Exception as ex:
            self.loggy.error(f"[vloggerclass] Exception  {ex}")

    def warning(self, msg, *args, **kwargs):
        try:
            s = self.panel_id_str + self._createPrefix()
            self.loggy.warning(s + (msg % args % kwargs))
        except Exception as ex:
            self.loggy.error(f"[vloggerclass] Exception  {ex}")

    def error(self, msg, *args, **kwargs):
        try:
            s = self.panel_id_str + self._createPrefix()
            self.loggy.error(s + (msg % args % kwargs))
        except Exception as ex:
            self.loggy.error(f"[vloggerclass] Exception  {ex}")

log = mylog
#log = vloggerclass(mylog, 0, False)

class AlSensorDeviceHelper(AlSensorDevice):

    def __init__(self, **kwargs):
        self._callback = []
        self.id = kwargs.get("id", -1)  # int   device id
        self.stype = kwargs.get("stype", AlSensorType.UNKNOWN)  # AlSensorType  sensor type
        self.ztypeName = kwargs.get("ztypeName", None)  # str   Zone Type Name
        self.sid = kwargs.get("sid", 0)  # int   sensor id
        self.ztype = kwargs.get("ztype", 0)  # int   zone type
        self.zname = kwargs.get("zname", "Unknown")  # str   zone name
        self.zpanelname = kwargs.get("zpanelname", "")  # str   zone name
        self.zchime = kwargs.get("zchime", "Unknown")  # str   zone chime
        self.zchimeref = kwargs.get("zchimeref", {})  # set   partition set (could be in more than one partition)
        self.partition = kwargs.get("partition", {})  # set   partition set (could be in more than one partition)
        self.bypass = kwargs.get("bypass", False)  # bool  if bypass is set on this sensor
        self.lowbatt = kwargs.get("lowbatt", False)  # bool  if this sensor has a low battery
        self.status = kwargs.get("status", False)  # bool  status, as returned by the A5 message
        self.tamper = kwargs.get("tamper", False)  # bool  tamper, as returned by the A5 message
        self.ztamper = kwargs.get("ztamper", False)  # bool  zone tamper, as returned by the A5 message
        self.ztrip = kwargs.get("ztrip", False)  # bool  zone trip, as returned by the A5 message
        self.enrolled = kwargs.get("enrolled", False)  # bool  enrolled, as returned by the A5 message
        self.triggered = kwargs.get("triggered", False)  # bool  triggered, as returned by the A5 message
        self.triggertime = None     # datetime  This is used to time stamp in local time the occurance of the trigger
        self.model = kwargs.get("model", "Unknown")  # str   device model
        self.motiondelaytime = kwargs.get("motiondelaytime", None)  # int   device model
        self.temperature = None
        self.luminance = None
        self.hasJPG = False
        self.jpg_data = None
        self.jpg_time = None
        self.problem = "none"
        self.missing = False
        self.one_way = False
        self.inactive = False
        #self.timelog = []
        self.statuslog = None

    def __str__(self):
        pt = ""
        for i in self.partition:
            pt = pt + str(i) + " "
        #stypestr = ""
        #if self.stype is not None and self.stype != AlSensorType.UNKNOWN:
        #    stypestr = titlecase(str(self.stype).replace("_"," "))
        #elif self.sid is not None:
        #    stypestr = "Unk " + str(self.sid)
        #else:
        #    stypestr = "Unknown"
        strn = ""
        strn = strn + ("id=None" if self.id == None else f"id={self.id:<2}")
        #strn = strn + (f" Type={stypestr:<8}")
        strn = strn + (" partition=None   "    if self.partition == None else f" partition={pt:<7}")
        strn = strn + (" bypass=- "            if self.bypass == None else    f" bypass={self.bypass:<2}")
        strn = strn + (" lowbatt=- "           if self.lowbatt == None else   f" lowbatt={self.lowbatt:<2}")
        strn = strn + (" status=- "            if self.status == None else    f" status={self.status:<2}")
        strn = strn + (" tamper=- "            if self.tamper == None else    f" tamper={self.tamper:<2}")
        strn = strn + (" enrolled=- "          if self.enrolled == None else  f" enrolled={self.enrolled:<2}")
        strn = strn + (" triggered=- "         if self.triggered == None else f" triggered={self.triggered:<2}")
        strn = strn + (" ztamper=- "           if self.ztamper == None else   f" ztamper={self.ztamper:<2}")
        strn = strn + (" ztrip=- "             if self.ztrip == None else     f" ztrip={self.ztrip:<2}")
        strn = strn + (" stype=Undefined"      if self.stype == None else     f" stype={str(self.stype):<12}")
        strn = strn + (" model=None"           if self.model == None else     f" model={self.model[:12]:<12}")
        strn = strn + (" sid=None"             if self.sid == None else       f" sid={self.sid:<3}")
        strn = strn + (" ztype=None"           if self.ztype == None else     f" ztype={self.ztype:<2}")
        strn = strn + (" Loc=None          "   if self.zname == None else     f" Loc={self.zname[:14]:<14}")
        strn = strn + (" ztypeName=None      " if self.ztypeName == None else f" ztypeName={self.ztypeName[:10]:<10}")
        strn = strn + (" zchime=None         " if self.zchime == None else    f" zchime={self.zchime:<13}")
        strn = strn + ("" if self.temperature == None else f" temperature={self.temperature:<3}")
        strn = strn + ("" if self.luminance == None else f" luminance={self.luminance:<3}")

        if self.motiondelaytime is not None and (self.stype == AlSensorType.MOTION or self.stype == AlSensorType.CAMERA):
            strn = strn + f" delay={'Not Set' if self.motiondelaytime == 0xFFFF else str(self.motiondelaytime):<7}"

        return strn

    def __eq__(self, other):
        if other is None:
            return False
        if self is None:
            return False
        return (
            self.id == other.id
            and self.stype == other.stype
            and self.sid == other.sid
            and self.model == other.model
            and self.ztype == other.ztype
            and self.zname == other.zname
            and self.zpanelname == other.zpanelname
            and self.zchime == other.zchime
            and self.partition == other.partition
            and self.bypass == other.bypass
            and self.lowbatt == other.lowbatt
            and self.status == other.status
            and self.tamper == other.tamper
            and self.ztypeName == other.ztypeName
            and self.ztamper == other.ztamper
            and self.ztrip == other.ztrip
            and self.enrolled == other.enrolled
            and self.triggered == other.triggered
            and self.hasJPG == other.hasJPG
            #and self.triggertime == other.triggertime
            and self.motiondelaytime == other.motiondelaytime
        )

    def setProblem(self, s):
        self.problem = s

    def getProblem(self) -> str:
        return self.problem

    def __ne__(self, other):
        return not self.__eq__(other)

    def onChange(self, callback : Callable = None):
        if callback is None:
            self._callback = []
        else:
            self._callback.append(callback)

    def getPartition(self):
        return self.partition

    def pushChange(self, s : AlSensorCondition):
        for cb in self._callback:
            cb(self, s)

    def getDeviceID(self):
        return self.id

    def getSensorModel(self) -> str:
        if self.model is not None:
            return self.model
        return "Unknown"

    def getSensorType(self) -> AlSensorType:
        return self.stype

    def getLastTriggerTime(self) -> datetime:
        return self.triggertime

    def getZoneLocation(self) -> (str, str):
        return (self.zname, self.zpanelname)

    def getZoneType(self) -> str:
        return self.ztypeName

    def getChimeType(self) -> str:
        return self.zchime

    def hasJPG(self) -> bool:
        return self.hasJPG

    def isTriggered(self) -> bool:
        return self.triggered

    def isOpen(self) -> bool:
        return self.status

    def isEnrolled(self) -> bool:
        return self.enrolled

    def isBypass(self) -> bool:
        return self.bypass

    def isLowBattery(self) -> bool:
        return self.lowbatt

    # Not abstract but implement if possible
    def isTamper(self) -> bool:
        return self.tamper

    # Not abstract but implement if possible
    def isMissing(self) -> bool:
        return self.missing

    # Not abstract but implement if possible
    def isInactive(self) -> bool:
        return self.inactive

    # Not abstract but implement if possible
    def isOneWay(self) -> bool:
        return self.one_way

    # Not abstract but implement if possible
    def isZoneTamper(self) -> bool:
        return self.ztamper

    # Not abstract but implement if possible
    def getRawSensorIdentifier(self) -> int:
        return self.sid

    # Not abstract but implement if possible
    #    This is only applicable to PowerMaster Panels. It is the motion off time per sensor.
    def getMotionDelayTime(self) -> str:
        if self.motiondelaytime is not None and (self.getSensorType() == AlSensorType.MOTION or self.getSensorType() == AlSensorType.CAMERA):
            return NO_DELAY_SET if self.motiondelaytime == 0xFFFF else str(self.motiondelaytime)
        return NO_DELAY_SET

    def _updateContactSensor(self, status = None, trigger = None):
        #log.debug(f"[UpdateContactSensor]   Sensor {self.id}   before")
        #self._dumpSensorsToLogFile()
        if trigger is not None and trigger:
            # If trigger is set then the caller is confident that it is a motion or camera sensor
            log.debug(f"[UpdateContactSensor]   Sensor {self.id}   triggered to True")
            self.triggered = True
            self.triggertime = getTimeFunction()
            self.pushChange(AlSensorCondition.STATE)
        elif status is not None and self.status != status:
            # The current setting is different
            if status:
                log.debug(f"[UpdateContactSensor]   Sensor {self.id}   triggered to True")
                self.triggered = True
                self.triggertime = getTimeFunction()
            if self.getSensorType() != AlSensorType.MOTION and self.getSensorType() != AlSensorType.CAMERA:
                # Not a motion or camera to set status
                log.debug(f"[UpdateContactSensor]   Sensor {self.id}   status from {self.status} to {status}")
                self.status = status
                #if status is not None and not status:
                #    self.SensorList[sensor].pushChange(AlSensorCondition.RESET)
            # Push change as status has toggled
            self.pushChange(AlSensorCondition.STATE)
        # The pushchange function calls the sensors onchange function so it should have already seen triggered and status values, so we can reset triggered
        self.triggered = False

    def updateLux(self, val) -> bool:
        if val is not None and self.luminance != val:
            log.debug(f"[updateLux]   Lux old {self.luminance}   new {val}")
            self.luminance = val
            self.pushChange(AlSensorCondition.LUX)
            return True # The value has changed
        return False # The value has not changed

    def updateTemperature(self, val) -> bool:
        if val is not None and self.temperature != val:
            # log.debug(f"[updateTemperature]   Temperature old {self.temperature}   new {val}")
            self.temperature = val
            self.pushChange(AlSensorCondition.TEMPERATURE)
            return True # The value has changed
        return False # The value has not changed

    def getLux(self):
        return self.luminance

    def getTemperature(self):
        return self.temperature

    def do_status(self, stat):
        self._updateContactSensor(status = stat)

    def do_trigger(self, trig):
        self._updateContactSensor(trigger = trig)

    def do_enrolled(self, val : bool) -> bool:
        if val is not None and self.enrolled != val:
            self.enrolled = val
            if self.enrolled:
                self.pushChange(AlSensorCondition.ENROLLED)
            else:
                self.pushChange(AlSensorCondition.RESET)
            return True # The value has changed
        return False # The value has not changed

    def do_bypass(self, val : bool) -> bool:
        if val is not None and self.bypass != val:
            self.bypass = val
            if self.bypass:
                self.pushChange(AlSensorCondition.BYPASS)
            else:
                self.pushChange(AlSensorCondition.ARMED)
            return True # The value has changed
        return False # The value has not changed

    def do_missing(self, val : bool) -> bool:
        if val is not None and self.missing != val:
            self.missing = val
            if self.missing:
                self.pushChange(AlSensorCondition.STATE)
            return True # The value has changed
        return False # The value has not changed

    def do_inactive(self, val : bool) -> bool:
        if val is not None and self.inactive != val:
            self.inactive = val
            if self.inactive:
                self.pushChange(AlSensorCondition.STATE)
            return True # The value has changed
        return False # The value has not changed

    def do_oneway(self, val : bool) -> bool:
        if val is not None and self.one_way != val:
            self.one_way = val
            if self.one_way:
                self.pushChange(AlSensorCondition.STATE)
            return True # The value has changed
        return False # The value has not changed

    def do_ztrip(self, val : bool) -> bool:
        if val is not None and self.ztrip != val:
            self.ztrip = val
            if self.ztrip: # I can't remember seeing this from the panel
                self.pushChange(AlSensorCondition.STATE)
            #else:
            #    self.pushChange(AlSensorCondition.RESET)
            return True # The value has changed
        return False # The value has not changed

    def do_ztamper(self, val : bool) -> bool:
        if val is not None and self.ztamper != val:
            self.ztamper = val
            if self.ztamper:
                self.pushChange(AlSensorCondition.TAMPER)
            else:
                self.pushChange(AlSensorCondition.RESTORE)
            return True # The value has changed
        return False # The value has not changed

    def do_battery(self, val : bool) -> bool:
        if val is not None and self.lowbatt != val:
            self.lowbatt = val
            if self.lowbatt:
                self.pushChange(AlSensorCondition.BATTERY)
            #else:
            #    self.pushChange(AlSensorCondition.RESET)
            return True # The value has changed
        return False # The value has not changed

    def do_tamper(self, val : bool) -> bool:
        if val is not None and self.tamper != val:
            self.tamper = val
            if self.tamper:
                self.pushChange(AlSensorCondition.TAMPER)
            else:
                self.pushChange(AlSensorCondition.RESTORE)
            return True # The value has changed
        return False # The value has not changed

class AlSwitchDeviceHelper(AlSwitchDevice):

    def __init__(self, **kwargs):
        self._callback = []
        self.enabled = True #kwargs.get("enabled", False)  # bool  enabled
        self.id = kwargs.get("id", None)  # int   device id
        #self.name = kwargs.get("name", None)  # str   name
        self.type = kwargs.get("type", None)  # str   type
        self.location = kwargs.get("location", None)  # str   location
        self.state = False

    def __str__(self):
        strn = ""
        strn = strn + ("id=None" if self.id == None else f"id={self.id:<2}")
        #strn = strn + (" name=None" if self.name == None else f" name={self.name:<4}")
        strn = strn + (" Type=None           " if self.type == None else f" Type={self.type:<15}")
        strn = strn + (" Loc=None          " if self.location == None else f" Loc={self.location:<14}")
        strn = strn + (" enabled=None" if self.enabled == None else f" enabled={self.enabled:<2}")
        strn = strn + (" state=None" if self.state == None else f" state={self.state:<8}")
        return strn

    def __eq__(self, other):
        if other is None:
            return False
        if self is None:
            return False
        return (
            self.id == other.id
            and self.enabled == other.enabled
            #and self.name == other.name
            and self.type == other.type
            and self.location == other.location
        )

    def __ne__(self, other):
        return not self.__eq__(other)

    def onChange(self, callback : Callable = None):
        if callback is None:
            # If any call is None then completely clear the list
            self._callback = []
        else:
            self._callback.append(callback)

    def pushChange(self):
        for cb in self._callback:
            cb(self)

    def getDeviceID(self):
        return self.id

    def isEnabled(self):
        return self.enabled

    def getType(self) -> str:
        return self.type

    def getLocation(self) -> str:
        return self.location

    def isOn(self) -> bool:
        return self.state #

"""
    def fromJSON(self, decode):
        if "enabled" in decode:
            self.enabled = toBool(decode["enabled"])
        if "type" in decode:
            self.type = titlecase(decode["type"])
        if "location" in decode:
            self.location = titlecase(decode["location"])
        if "state" in decode:
            s = AlX10Command.value_of(decode["state"].upper())
            self.state = (s == AlX10Command.ON or s == AlX10Command.BRIGHTEN or s == AlX10Command.DIMMER)

    def toJSON(self) -> dict:
        dd=json.dumps({
             #"name": str(switch.createFriendlyName()),
             "id": self.getDeviceID(),
             "enabled": self.isEnabled(),
             "type": str(self.getType()),
             "location": str(self.getLocation()),
             "state":  "On" if self.state else "Off" })  # , ensure_ascii=True
        return dd
"""

class ImageRecord:
    # The details of an individual image
    
    def __init__(self, zone, image_id, size, next_seq, lastimage, parent):
        self.image_id = image_id              # The image_id is the image number from the panel.  The panel outputs a sequence of images.
        self.size = size                      # The size of the image in bytes
        self.buffer = bytearray(size)         # Data buffer
        self.lastimage = lastimage            # Boolean, is this the last image that the panel is sending
        self.current = 0                      # current position in the buffer as if gets filled with data
        self.next_sequence = next_seq         # The panel sends the data in a series of messages that are sequenced
        self.ongoing = True                   # Are we creating the image or have we finished
        self.last = _getUTCTime()             # Date/Time of the last data from the panel, used for timeouts
        self.zone = zone                      # The zone that the image is from
        self.parent = parent                  # The parent ImageZoneClass
        
    def addBufferData(self, databuffer, sequence) -> bool:
        if self.ongoing and self.next_sequence is not None and sequence == self.next_sequence:
            self.next_sequence = (self.next_sequence + 0x10) & 0xFF
            self.last = _getUTCTime()
            datalen = len(databuffer)
            self.buffer[self.current : self.current+datalen] = databuffer
            self.current = self.current + datalen
            if self.current == self.size:
                self.ongoing = False
            log.debug(f"[handle_msgtypeF4]         current position {self.current}    next sequence = {self.next_sequence}       ongoing = {self.ongoing}")
            return True
        log.debug("[handle_msgtypeF4]       ERROR: Attempt to add image data and the record has not been created")
        return False
        
    def isImageComplete(self) -> bool:
        return not self.ongoing

    def isOngoing(self) -> bool:
        return self.ongoing and self.current > 0

class ImageZoneClass:
    def __init__(self):
        self.start = _getUTCTime()             # Start time
        self.count = 0                         # How many images did the user ask for, this defaults to 11 as we can't set this to the panel and 11 is how many the panel sends anyway
        self.totalimages = 255                 # After the first image, the panel tells us how many images
        self.unique_id = -1                    # Each sequence has a unique id
        self.current_image = None              # The current image being built
        self.images = { }                      # Image Store, images are replaced when a new one is sent
    
    def isImageComplete(self) -> bool:
        return self.current_image.isImageComplete() if self.current_image is not None else False

    def isOngoing(self) -> bool:
        return self.current_image.isOngoing() if self.current_image is not None else False

class AlImageManager:
    def __init__(self):
        self.ImageZone = {}                     # Zone and Image Store
        self.current_zone = None                # when not None then building an image for this zone number
        self.last_image = None                  # A shortcut to the last successfully built image

    def _current_image(self):
        return self.ImageZone[self.current_zone].current_image if self.current_zone is not None and self.current_zone in self.ImageZone else None

    def isImageDataInProgress(self) -> bool:
        for zone, value in self.ImageZone.items():
            if value.isOngoing():
                return True
        return False

    def terminateIfExceededTimeout(self, seconds):
        img = self._current_image()
        if img is not None:
            interval = _getUTCTime() - img.last
            if interval is not None and interval >= timedelta(seconds=seconds):
                if img.isOngoing():
                    self.terminateImage()
                else:
                    self.ImageZone[self.current_zone].current_image = None
                    self.current_zone = None

    def create(self, zone, count) -> bool:
        # set up an entry in ImageZone with no images
        #    count is the number of images that the user asked for
        if zone not in self.ImageZone:
            self.ImageZone[zone] = ImageZoneClass()
        if self.ImageZone[zone].isOngoing():
            return False
        self.last_image = None
        self.ImageZone[zone].count = count
        log.debug(f'[AlImageManager]  Create JPG : zone = {zone}   start time = {self.ImageZone[zone].start}   count = {self.ImageZone[zone].count}')
        return True

    def hasStartedSequence(self):
        return self._current_image() is not None

    def setCurrent(self, zone, unique_id, image_id, size, sequence, lastimage, totalimages) -> bool:
        if self.hasStartedSequence():
            return False
        self.current_zone = zone
        if zone not in self.ImageZone:
            return False
#            log.debug("[AlImageManager]         Warning: creating empty image record to receive an image")
#            self.create(zone, 11)          # default to 11  
        
        self.ImageZone[zone].unique_id = unique_id
        self.ImageZone[zone].totalimages = totalimages

        # Always replace the existing ImageRecord if one already exists
        record = ImageRecord(zone = zone, image_id = image_id, size = size, lastimage = lastimage, next_seq = (sequence + 0x10) & 0xFF, parent = self.ImageZone[zone])

        if image_id in self.ImageZone[zone].images:
            del self.ImageZone[zone].images[image_id]
        self.ImageZone[zone].images[image_id] = record
        self.ImageZone[zone].current_image = record

        log.debug(f'[AlImageManager]  setCurrent zone = {self.current_zone}  unique_id = {hex(unique_id)}    image_id = {image_id}')
        log.debug(f"[AlImageManager]             total filesize {record.size}    next sequence = {hex(record.next_sequence)}     lastimage = {record.lastimage}    totalimages = {totalimages}")
        self.last_image = None
        return True
    
    def addData(self, databuffer, sequence) -> bool:
        img = self._current_image()
        if img is not None:
            insequence = img.addBufferData(databuffer, sequence)
            if img.isImageComplete():
                self.last_image = self.ImageZone[self.current_zone].current_image
                self.ImageZone[self.current_zone].current_image = None
                self.current_zone = None
            return insequence
        return False
    
#    def currentZone(self) -> int:
#        return self.current_zone
        
    def isImageComplete(self):
        return self.last_image is not None
    
    def getLastImageRecord(self):
        if self.last_image is not None:
            if self.last_image.parent is not None:
                return self.last_image.zone, self.last_image.parent.unique_id, self.last_image.image_id, self.last_image.parent.totalimages, self.last_image.buffer, self.last_image.lastimage
        return -1, -1, -1, -1, None, False

    def isValidImage(self, zone, image) -> bool:
        return zone in self.ImageZone and image in self.ImageZone[zone].images
    
    def isValidZone(self, zone) -> bool:
        return zone in self.ImageZone
    
    def getImage(self, zone, image):
        return self.ImageZone[zone].images[image].buffer if self.isValidImage(zone, image) else None
    
    def getImageList(self, zone) -> []:
        return list(self.ImageZone[zone].images) if self.isValidZone(zone) else list()
    
    def terminateImage(self):
        img = self._current_image()
        if img is not None:
            if img.image_id in self.ImageZone[self.current_zone].images:
                del self.ImageZone[self.current_zone].images[img.image_id]
            self.ImageZone[self.current_zone].current_image = None
        self.current_zone = None
        self.last_image = None


class MyChecksumCalc:

    def __init__(self, logger = None) -> None:
        """Initialize class."""
        pass
        #if logger is not None:
        #    log = logger

    # This is used for debugging from command line
    def setLogger(self, loggy):
        log = loggy

    # check the checksum of received messages
    def _validatePDU(self, packet: bytearray) -> bool:
        """Verify if packet is valid.
        >>> Packets start with a preamble (\x0D) and end with postamble (\x0A)
        """
        # Validate a received message
        # Does it start with a header
        if packet[:1] != b"\x0D":
            return False
        # Does it end with a footer
        if packet[-1:] != b"\x0A":
            return False

        # Check the CRC
        if packet[-2:-1] == self._calculateCRC(packet[1:-2]):
            # log.debug("[_validatePDU] VALID CRC PACKET!")
            return True

        # Check the CRC
        if packet[-2:-1] == self._calculateCRCAlt(packet[1:-2]):
            # log.debug("[_validatePDU] VALID ALT CRC PACKET!")
            return True

        if packet[-2:-1][0] == self._calculateCRC(packet[1:-2])[0] + 1:
            log.debug(f"[_validatePDU] Validated a Packet with a checksum that is 1 more than the actual checksum!!!! {toString(packet)} and {hex(self._calculateCRC(packet[1:-2])[0]).upper()} alt calc is {hex(self._calculateCRCAlt(packet[1:-2])[0]).upper()}")
            return True

        if packet[-2:-1][0] == self._calculateCRC(packet[1:-2])[0] - 1:
            log.debug(f"[_validatePDU] Validated a Packet with a checksum that is 1 less than the actual checksum!!!! {toString(packet)} and {hex(self._calculateCRC(packet[1:-2])[0]).upper()} alt calc is {hex(self._calculateCRCAlt(packet[1:-2])[0]).upper()}")
            return True

        log.debug("[_validatePDU] Not valid packet, CRC failed, may be ongoing and not final 0A")
        return False

    # alternative to calculate the checksum for sending and receiving messages
    def _calculateCRCAlt(self, msg: bytearray):
        """ Calculate CRC Checksum """
        # log.debug("[_calculateCRC] Calculating for: %s", toString(msg))
        # Calculate the checksum
        checksum = 0
        for char in msg[0 : len(msg)]:
            checksum += char
        # 29/8/2022
        #      This works for both my panels and always validates exactly (never using the +1 or -1 code in _validatePDU)
        #      It also matches the checksums that the Powerlink 3.1 module generates.
        checksum = 256 - (checksum % 255)
        if checksum == 256:
            checksum = 1
        # log.debug("[_calculateCRC] Calculating for: {toString(msg)}     calculated CRC is: {toString(bytearray([checksum]))}")
        return bytearray([checksum])

    # calculate the checksum for sending and receiving messages
    def _calculateCRC(self, msg: bytearray):
        """ Calculate CRC Checksum """
        # log.debug("[_calculateCRC] Calculating for: %s", toString(msg))
        # Calculate the checksum
        checksum = 0
        for char in msg[0 : len(msg)]:
            checksum += char
        checksum = 0xFF - (checksum % 0xFF)
        if checksum == 0xFF:
            checksum = 0x00
        # log.debug("[_calculateCRC] Calculating for: {toString(msg)}     calculated CRC is: {toString(bytearray([checksum]))}")
        return bytearray([checksum])

class PartitionStateClass:

    def __init__(self):
        """Initialize class."""
        self.Reset()

    def Reset(self):
        self.PanelState = AlPanelStatus.UNKNOWN
        self.PanelReady = False
        self.PanelTamper = False
        self.PanelBattery = False
        self.PanelAlertInMemory = False
        self.PanelBypass = False
        self.SirenActive = False
        self.SirenActiveDeviceTrigger = None
        self.PanelAlarmStatus = AlAlarmType.NONE
        self.PanelTroubleStatus = AlTroubleType.NONE

    def statelist(self) -> list:
        # These are the values that are used to determine if the panel state has been changed
        return [self.SirenActive, self.PanelState, self.PanelReady, self.PanelTroubleStatus, self.PanelAlarmStatus, self.PanelBypass]

    def getEventData(self, FullSet) -> dict:
        datadict = {}
        datadict[EventDataEnum.STATE]   = "triggered" if self.SirenActive else self.PanelState.name.lower()
        datadict[EventDataEnum.READY]   = self.PanelReady
        datadict[EventDataEnum.MEMORY]  = self.PanelAlertInMemory
        datadict[EventDataEnum.TROUBLE] = self.PanelTroubleStatus.name.lower()
        datadict[EventDataEnum.BYPASS]  = self.PanelBypass
        if FullSet:
            datadict[EventDataEnum.TAMPER]  = self.PanelTamper
            datadict[EventDataEnum.BATTERY] = self.PanelBattery
            datadict[EventDataEnum.ALARM]   = self.PanelAlarmStatus.name.lower()
        return datadict

    def UpdatePanelState(self, et : EVENT_TYPE, sensor):
        # Update tamper status
        self.PanelTamper = (et == EVENT_TYPE.TAMPER_PANEL)
        # Update trouble status
        self.PanelTroubleStatus = pmPanelTroubleType_t[et] if et in pmPanelTroubleType_t else AlTroubleType.NONE
        # Update alarm status
        self.PanelAlarmStatus = pmPanelAlarmType_t[et] if et in pmPanelAlarmType_t else AlAlarmType.NONE
            
        # no clauses as if siren gets true again then keep updating self.SirenActive sensor
        if self.PanelAlarmStatus == AlAlarmType.INTRUDER:
            self.SirenActive = True
            self.SirenActiveDeviceTrigger = None if sensor is None else sensor
            log.debug("[UpdatePanelState]            ******************** Alarm Active *******************")
        elif self.SirenActive:
            if et in pmPanelCancelSet:  # Cancel Alarm
                # cancel alarm and the alarm has been triggered
                self.SirenActive = False
                self.SirenActiveDeviceTrigger = None
                log.debug("[UpdatePanelState]            ******************** Alarm Cancelled ****************")
            # Siren has been active but it is no longer active (probably timed out and has then been disarmed)
            elif et not in pmPanelIgnoreSet:  # Alarm Timed Out ????
                self.SirenActive = False
                self.SirenActiveDeviceTrigger = None
                log.debug("[UpdatePanelState]            ******************** Event not in Ignore Set, Cancelling Alarm Indication ****************")

        log.debug(f"[UpdatePanelState]         System message eventType={et} i.e. {et.name}   self.PanelTamper={self.PanelTamper}   self.PanelAlarmStatus={self.PanelAlarmStatus}" +
                  f"    self.PanelTroubleStatus={self.PanelTroubleStatus}    self.SirenActive={self.SirenActive}   siren={self.PanelAlarmStatus == AlAlarmType.INTRUDER}")


    def ProcessPanelStateUpdate(self, sysStatus, sysFlags, PanelMode) -> AlPanelEventData | None:
        
        retval = None
        
        sysStatus = sysStatus & 0x1F     # Mark-Mills with a PowerMax Complete Part, sometimes this has the 0x20 bit set and I'm not sure why
        
        if sysStatus in pmPanelArmedStatus:
            disarmed = pmPanelArmedStatus[sysStatus].disarmed
            armed    = pmPanelArmedStatus[sysStatus].armed
            entry    = pmPanelArmedStatus[sysStatus].entry
            self.PanelState = pmPanelArmedStatus[sysStatus].state

            if pmPanelArmedStatus[sysStatus].eventmapping >= 0:
                #log.debug(f"[PanelStateUpdate]             self.PanelState is {self.PanelState}      using event mapping {pmPanelArmedStatus[sysStatus].eventmapping} for event data")
                retval = AlPanelEventData(name = 0, action = pmPanelArmedStatus[sysStatus].eventmapping) # use partiton set to -1 as a dummy
                
        else:
            log.debug(f"[PanelStateUpdate]             Unknown state {hexify(sysStatus)}, assuming Panel state of Unknown")
            disarmed = None
            armed = None
            entry = False
            self.PanelState = AlPanelStatus.UNKNOWN  # UNKNOWN

        if PanelMode == AlPanelMode.DOWNLOAD:
            self.PanelState = AlPanelStatus.DOWNLOADING  # Downloading

        log.debug(f"[PanelStateUpdate]             sysFlags=0x{hexify(sysFlags)}    sysStatus=0x{hexify(sysStatus)}    log: {self.PanelState.name}, {disarmed=}  {armed=}")

        self.PanelReady = sysFlags & 0x01 != 0
        self.PanelAlertInMemory = sysFlags & 0x02 != 0

        if (sysFlags & 0x04 != 0):                   # Trouble
            if self.PanelTroubleStatus == AlTroubleType.NONE:       # if set to NONE then set it to GENERAL, if it's already set from A& then that is more specific
                log.debug(f"[PanelStateUpdate]                 Panel Indicates Trouble Set")
                self.PanelTroubleStatus = AlTroubleType.GENERAL
        else:
            if self.PanelTroubleStatus == AlTroubleType.GENERAL:       # if set to NONE then set it to GENERAL, if it's already set from A& then that is more specific
                log.debug(f"[PanelStateUpdate]                 Panel Indicates Trouble Cleared")
            self.PanelTroubleStatus = AlTroubleType.NONE

        self.PanelBypass = sysFlags & 0x08 != 0
        
        if sysFlags & 0x10 != 0:
            log.debug(f"[PanelStateUpdate]                 sysFlags bit 4 set --> Should be last 10 seconds of entry/exit")
            
        if sysFlags & 0x20 != 0:
            log.debug(f"[PanelStateUpdate]                 sysFlags bit 5 set --> Should be Zone Event")
            
        if sysFlags & 0x40 != 0:
            log.debug(f"[PanelStateUpdate]                 sysFlags bit 6 set --> Should be Status Changed")
            
        #if sysFlags & 0x10 != 0:  # last 10 seconds of entry/exit
        #    self.PanelArmed = sarm == "Arming"
        #else:
        #     self.PanelArmed = sarm == "Armed"
        PanelAlarmEvent = sysFlags & 0x80 != 0

        if PanelMode not in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
            # if the system status has the panel armed and there has been an alarm event, assume that the alarm is sounding
            #        and that the sensor that triggered it isn't an entry delay
            #   Normally this would only be directly available in Powerlink mode with A7 messages, but an assumption is made here
            if armed is not None and armed and not entry and PanelAlarmEvent:
                log.debug("[PanelStateUpdate]                     Alarm Event Assumed while in Standard Mode")
                # Alarm Event
                self.SirenActive = True

        # Clear any alarm event if the panel alarm has been triggered before (while armed) but now that the panel is disarmed (in all modes)
        if self.SirenActive and disarmed is not None and disarmed:
            log.debug("[PanelStateUpdate] ******************** Alarm Not Sounding (Disarmed) ****************")
            self.SirenActive = False
            self.SirenActiveDeviceTrigger = None
        
        return retval
    

class AlPanelInterfaceHelper(AlPanelInterface):

    def __init__(self, panel_id, logger = None):
        """Initialize class."""
        super().__init__()

        #if logger is not None:
        #    log = logger

        # Class Variables
        #self.log = vloggerclass(panel_id=panel_id)
        self.suspendAllOperations = False
        self._initVars()

    def _initVars(self):
        # set the event callback handlers to None
        self.onPanelChangeHandler = None
        self.onNewSensorHandler = None
        self.onNewSwitchHandler = None
        self.onProblemHandler = None
        self.onPanelLogHandler = None
        
        ########################################################################
        # Global Variables that define the overall panel status
        ########################################################################
        self.PanelMode = AlPanelMode.UNKNOWN
        # Whether its a powermax or powermaster
        self.PowerMaster = None
        # Define model type to be unknown
        self.PanelModel = "Unknown"
        self.PanelType = None
        
        self.partitionsEnabled = False
        self.PartitionsInUse = set()  # this is a set so no repetitions allowed
        self.PartitionState = [PartitionStateClass(), PartitionStateClass(), PartitionStateClass()]    # Maximum of 3 partitions across all panel models
        
        self.lastPanelEvent = {}
        self.panelEventData = []

        # Keep a dict of the sensors so we know if its new or existing
        self.SensorList = {}
        # Keep a dict of the switches so we know if its new or existing
        self.SwitchList = {}
        
    # This is used for debugging from command line
    def setLogger(self, loggy):
        log = loggy

    def shutdownOperation(self):
        self._initVars()

    def getPartitionsInUse(self) -> set | None:
        # if partitions are enabled in the panel then return the partition set, 
        #     note that the set could only be a single partition (if that is what is set in the panel)
        if self.partitionsEnabled:
            return self.PartitionsInUse
        return None
        
    def _dumpSensorsToLogFile(self, incX10 = False):
        log.debug(" ================================================================================ Display Status ================================================================================")
        for key, sensor in self.SensorList.items():
            log.debug(f"     key {key:<2} Sensor {sensor}")
        if incX10:
            for key, device in self.SwitchList.items():
                log.debug(f"     key {key:<2} X10    {device}")
        
        pm = titlecase(self.PanelMode.name.replace("_"," ")) # str(AlPanelMode()[self.PanelMode]).replace("_"," ")
        log.debug(f"   Model {self.PanelModel: <18}     PowerMaster {'Yes' if self.PowerMaster else 'No': <10}     Mode   {pm: <18}     ")
        part = self.getPartitionsInUse()
        if part is not None:
            for piu in part:
                if 1 <= piu <= 3:
                    p = self.PartitionState[piu-1]
                    r = 'Yes' if p.PanelReady else 'No'
                    ts = titlecase(p.PanelTroubleStatus.name)                   # str(AlTroubleType()[self.PanelTroubleStatus]).replace("_"," ")
                    al = titlecase(p.PanelAlarmStatus.name)                     # str(AlAlarmType()[self.PanelAlarmStatus]).replace("_"," ")
                    pn = titlecase(p.PanelState.name)
                    log.debug(f"   Partition {piu:<1}    Ready {r: <13}  Status {pn: <18}      Trouble {ts: <13}      AlarmStatus {al: <12}")
                else:
                    log.debug(f"   Partition {piu:<1}    Invalid")
                    
        else:
            p = self.PartitionState[0]
            r = 'Yes' if p.PanelReady else 'No'
            ts = titlecase(p.PanelTroubleStatus.name)                   # str(AlTroubleType()[self.PanelTroubleStatus]).replace("_"," ")
            al = titlecase(p.PanelAlarmStatus.name)                     # str(AlAlarmType()[self.PanelAlarmStatus]).replace("_"," ")
            pn = titlecase(p.PanelState.name)
            log.debug(f"                                      Ready {r: <13}  Status {pn: <18}      Trouble {ts: <13}      AlarmStatus {al: <12}")
        log.debug(" ================================================================================================================================================================================")

    def getPanelModel(self):
        return self.PanelModel

    def getPanelMode(self) -> AlPanelMode:
        if not self.suspendAllOperations:
            return self.PanelMode
        return AlPanelMode.UNKNOWN

    def isSirenActive(self) -> (bool, AlSensorDevice | None):
        if not self.suspendAllOperations:
            if (p := self.getPartitionsInUse()) is not None:
                for piu in p:
                    if self.PartitionState[piu-1].SirenActive:
                        return (True, self.PartitionState[piu-1].SirenActiveDeviceTrigger)
            else:
                return (self.PartitionState[0].SirenActive, self.PartitionState[0].SirenActiveDeviceTrigger)
        return (False, None)

    def getPanelStatus(self, partition = INVALID_PARTITION) -> AlPanelStatus:
        if not self.suspendAllOperations:
            if partition is not None:
                if 1 <= partition <= 3:
                    return self.PartitionState[partition-1].PanelState
                elif partition == 0:
                    #log.debug(f"Partition is zero {self.getPartitionsInUse()}")
                    # Start retval at the lowest ranked setting
                    retval = AlPanelStatus.UNKNOWN
                    for p in self.getPartitionsInUse():
                        if self.PartitionState[p-1].PanelState > retval:
                            retval = self.PartitionState[p-1].PanelState
                    #log.debug(f"Partition is zero {self.getPartitionsInUse()}   returning {retval}")
                    return retval
            return self.PartitionState[0].PanelState
        return AlPanelStatus.UNKNOWN

    def isPanelReady(self, partition = INVALID_PARTITION) -> bool:
        """ Get the panel ready state """
        if not self.suspendAllOperations:
            if partition is not None and 1 <= partition <= 3:
                return self.PartitionState[partition-1].PanelReady
            return self.PartitionState[0].PanelReady
        return False

    def getPanelTrouble(self, partition = INVALID_PARTITION) -> AlTroubleType:
        """ Get the panel trouble state """
        if not self.suspendAllOperations:
            if partition is not None and 1 <= partition <= 3:
                return self.PartitionState[partition-1].PanelTroubleStatus
            return self.PartitionState[0].PanelTroubleStatus
        return AlTroubleType.UNKNOWN

    def isPanelBypass(self, partition = INVALID_PARTITION) -> bool:
        """ Get the panel bypass state """
        if not self.suspendAllOperations:
            if partition is not None and 1 <= partition <= 3:
                return self.PartitionState[partition-1].PanelBypass
            return self.PartitionState[0].PanelBypass
        return False

#    def getPanelLastEvent(self) -> (str, str, str):
#        return (self.PanelLastEventName, self.PanelLastEventAction, self.PanelLastEventTime)

#    def requestPanelCommand(self, state : AlPanelCommand, code : str = "", partitions : set = {1,2,3}) -> AlCommandStatus:
#        """ Send a request to the panel to Arm/Disarm """
#        return AlCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    # device in range 0 to 15 (inclusive), 0=PGM, 1 to 15 are X10 devices
    # state is the X10 state to set the switch
    def setX10(self, device : int, state : AlX10Command) -> AlCommandStatus:
        """ Se the state of an X10 switch. """
        return AlCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    def getJPG(self, device : int, count : int) -> AlCommandStatus:
        return AlCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    # Set the Sensor Bypass to Arm/Bypass individual sensors
    # sensor in range 1 to 31 for PowerMax and 1 to 64 for PowerMaster (inclusive) depending on alarm
    # bypassValue is False to Arm the Sensor and True to Bypass the sensor
    # Set code to:
    #    None when we are in Powerlink or Standard Plus and to use the code code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this is unlikely to work on any panel)
    def setSensorBypassState(self, sensor : int | set, bypassValue : bool, code : str = "") -> AlCommandStatus:
        """ Set or Clear Sensor Bypass """
        return AlCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    # Get the panels event log
    # Set code to:
    #    None when we are in Powerlink or Standard Plus and to use the code code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this is unlikely to work on any panel)
    def getEventLog(self, code : str = "") -> AlCommandStatus:
        """ Get Panel Event Log """
        return AlCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    # get the current date and time
    def _getTimeFunction(self) -> datetime:
        return datetime.now(timezone.utc).astimezone()

    # get the current date and time
    def _getUTCTimeFunction(self) -> datetime:
        return _getUTCTime()

    def sendPanelEventData(self) -> bool:
        retval = False
        for ped in self.panelEventData:
            retval = True
            a = ped.asDict()
            log.debug(f"[PanelUpdate] ped = {ped}  event data = {a}")
            self.sendPanelUpdate(AlCondition.PANEL_UPDATE, a)
            self.lastPanelEvent = a
        self.panelEventData = [ ] # empty the list
        return retval

    def addPanelEventData(self, ped : AlPanelEventData):
        #log.debug(f"[addPanelEventData] {ped}")
        ped.time = self._getTimeFunction() # .strftime("%d/%m/%Y, %H:%M:%S")
        self.panelEventData.append(ped)  

    # Set the onProblem callback handlers
    def onProblem(self, fn : Callable):             # onProblem ( exception or string or None )
        self.onProblemHandler = fn

    # Set the onNewSensor callback handlers
    def onNewSensor(self, fn : Callable):             # onNewSensor ( device : AlSensorDevice )
        self.onNewSensorHandler = fn

    # Set the onNewSwitch callback handlers
    def onNewSwitch(self, fn : Callable):             # onNewSwitch ( sensor : AlSwitchDevice )
        self.onNewSwitchHandler = fn

    # Set the onPanelLog callback handlers
    def onPanelLog(self, fn : Callable):             # onPanelLog ( event_log_entry : AlLogPanelEvent )
        self.onPanelLogHandler = fn

    # Set the onPanelEvent callback handlers
    def onPanelChange(self, fn : Callable):             # onPanelChange ( datadictionary : dict )
        self.onPanelChangeHandler = fn

    def sendPanelUpdate(self, ev : AlCondition, d : dict = {} ):
        if self.onPanelChangeHandler is not None:
            self.onPanelChangeHandler(ev, d)

    def _searchDict(self, dict, v_search):
        for k, v in dict.items():
            if v == v_search:
                return k
        return None

    def merge(self, a : dict, b : dict, path=None, update=True):
        "http://stackoverflow.com/questions/7204805/python-dictionaries-of-dictionaries-merge"
        "merges b into a"
        if path is None: path = []
        for key in b:
            if key in a:
                if isinstance(a[key], dict) and isinstance(b[key], dict):
                    self.merge(a[key], b[key], path + [str(key)])
                elif a[key] == b[key]:
                    pass # same leaf value
                elif isinstance(a[key], list) and isinstance(b[key], list):
                    for idx, val in enumerate(b[key]):
                        a[key][idx] = self.merge(a[key][idx], b[key][idx], path + [str(key), str(idx)], update=update)
                elif update:
                    a[key] = b[key]
                else:
                    raise Exception('Conflict at %s' % '.'.join(path + [str(key)]))
            else:
                a[key] = b[key]
        return a

    def getPanelFixedDict(self) -> dict:
        pm = "Unknown"
        if self.PowerMaster is not None:
            if self.PowerMaster: # PowerMaster models
                pm = "Yes"
            else:
                pm = "No"
        return {
            TEXT_PANEL_MODEL: self.PanelModel,
            TEXT_POWER_MASTER: pm
            #"Model Type": self.ModelType
        }

    def dumpSensorsToStringList(self) -> list:
        retval = list()
        for key, sensor in self.SensorList.items():
            retval.append(f"key {key:<2} Sensor {sensor}")
        return retval

    def dumpSwitchesToStringList(self) -> list:
        retval = list()
        for key, switch in self.SwitchList.items():
            retval.append(f"key {key:<2} Switch {switch}")
        return retval


# Turn on auto code formatting when using black
# fmt: on
