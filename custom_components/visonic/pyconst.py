
import os
import sys

# The defaults are set for use in Home Assistant.
#    If using CircuitPython then set these values in the settings.toml file
MicroPython = os.getenv("MICRO_PYTHON")

# Turn off auto code formatting when using black
# fmt: off

if MicroPython is not None:
    print("Py Libraries Using MicroPython")
    import adafruit_logging as logging
    from adafruit_datetime import datetime as datetime
    ABC = object
    Callable = object
    TypedDict = object

    class ABC:
        pass

    def abstractmethod(f):
        return f

else:
    import logging
    from abc import ABC, abstractmethod
    from typing import Callable, TypedDict
    from datetime import datetime

log = logging.getLogger(__name__)

PLUGIN_VERSION = "Not Implemented"
NO_DELAY_SET = "No Delay Set"

class AlIntEnum(int):
    ThisShouldNotHappen = "ThisShouldNotHappen"

    def __init__(self, d = 0):
        #super().__init__(value)
        self.myname = self.ThisShouldNotHappen

    def __str__(self) -> str:
        ''' return the string name '''
        if self.myname == self.ThisShouldNotHappen:
            raise ValueError(f"AlIntEnum not set correctly")
        return self.myname

    @property
    def name(self) -> str:
        ''' return the string name '''
        if self.myname == self.ThisShouldNotHappen:
            raise ValueError(f"AlIntEnum {self} not set correctly")
        return self.myname

    def setName(self, s):
        self.myname = s

# Base class for all enumeration types
class AlEnum:

    # A list of the variables & functions (that do not already have a leading underscore) to ignore.
    #     These are the local function names here
    exclusions = ['value_of', 'get_variables', 'exclusions', 'mydictionary', 'mytester']

    def __init__(self):  # Need an instance to create the dictionary - or could use a classmethod
        # This ensures that the constructor is only called once and raises an exception if not
        myname = self.__class__.__name__
        tester = getattr(self.__class__, "mytester", "default_tester")
        if tester != "default_tester":
            raise ValueError(f"'{myname}' constructor should only be called once")
        # set mytester to check at the start of this as the constructor should only be called once
        #    it won't matter if it is called multiple times but it is not necessary
        setattr(self.__class__, "mytester", "not_default_tester") # set it to anything except "default_tester"
        # get all the functions in this class. Circuitpython does not support vars()
        d = dir(self)
        myenums = { }
        for key in d:
            # exclude all the functions that start with underscore and the functions in this class (the exclusions)
            if key[0] != "_" and key not in AlEnum.exclusions:
                # Set -sys.maxsize to be the default
                val = getattr(self, key, -sys.maxsize)
                if val > -sys.maxsize and key not in myenums:
                    val.setName(key)
                    myenums[key] = val
                elif val == -sys.maxsize:
                    raise ValueError(f"'{myname}' enum key failed '{key}'")
                else:
                    raise ValueError(f"'{myname}' cannot repeat enum keys '{key}'")
        # check for uniqueness of the values
        flag = len(myenums) == len(set(myenums.values()))
        if not flag:
            raise ValueError(f"'{myname}' enum contains repeated values {myenums.values()}")
        # save the dictionary as a new variable i.e. create a new variable in the parent class so value_of can use it
        setattr(self.__class__, "mydictionary", myenums)

    def __members__(self):
        d = getattr(self.__class__, "mydictionary")
        log.debug(f"members d = {d}")
        return d

    def __getitem__(self, indexOrName):
        log.debug(f"getitem indexOrName = {indexOrName}")
        d = getattr(self.__class__, "mydictionary")
        if isinstance(indexOrName, str) and indexOrName in d:
            return d[indexOrName]
        else:
            log.debug(f"ERROR: In Enumeration")
            return ""

    @classmethod
    def get_variables(cls):
        return getattr(cls, "mydictionary")

    @classmethod
    def value_of(cls, value):
        ''' Get the enumeration from the string '''
        value = value.replace(" ", "_")
        # Get the dictionary
        d = getattr(cls, "mydictionary")
        myname = cls.__name__
        for key, val in d.items():
            if key == value:
                return val
        else:
            raise ValueError(f"'{cls.__name__}' enum not found for '{value}'")

# This class represents the reasons that could trigger an alarm
#     These could be set even if the siren is not sounding, depending on the panel settings
class AlAlarmType(AlEnum):
    UNKNOWN = AlIntEnum(0)
    NONE = AlIntEnum(1)
    INTRUDER = AlIntEnum(2)
    TAMPER = AlIntEnum(3)
    PANIC = AlIntEnum(4)
    FIRE = AlIntEnum(5)
    EMERGENCY = AlIntEnum(6)
    GAS = AlIntEnum(7)
    FLOOD = AlIntEnum(8)
a = AlAlarmType()

# the set of configuration parameters in to this client class
class AlConfiguration(AlEnum):
    DownloadCode = AlIntEnum(0)           # 4 digit string or ""
    AutoEnroll = AlIntEnum(1)             # Boolean
    AutoSyncTime = AlIntEnum(2)           # Boolean
    PluginLanguage = AlIntEnum(3)         # String "EN", "FR", "NL"
    MotionOffDelay = AlIntEnum(4)         # Integer (seconds)
    SirenTriggerList = AlIntEnum(5)       # A list of strings
    ForceStandard = AlIntEnum(6)          # Boolean
    EEPROMAttributes = AlIntEnum(10)      # Boolean
    DisableAllCommands = AlIntEnum(11)    # Boolean
    CompleteReadOnly = AlIntEnum(12)      # Boolean
a = AlConfiguration()

# The set of panel modes
class AlPanelMode(AlEnum):
    UNKNOWN = AlIntEnum(0)
    PROBLEM = AlIntEnum(1)
    STARTING = AlIntEnum(2)
    STANDARD = AlIntEnum(3)
    STANDARD_PLUS = AlIntEnum(4)
    POWERLINK = AlIntEnum(5)
    DOWNLOAD = AlIntEnum(6)
    STOPPED = AlIntEnum(7)
    MONITOR_ONLY = AlIntEnum(8)
    COMPLETE_READONLY = AlIntEnum(9)
a = AlPanelMode()

# The set of panel states
class AlPanelStatus(AlEnum):
    UNKNOWN = AlIntEnum(0)
    DISARMED = AlIntEnum(1)
    ARMING_HOME = AlIntEnum(2)
    ARMING_AWAY = AlIntEnum(3)
    ENTRY_DELAY = AlIntEnum(4)
    ARMED_HOME = AlIntEnum(5)
    ARMED_AWAY = AlIntEnum(6)
    SPECIAL = AlIntEnum(7)
    DOWNLOADING = AlIntEnum(8)
a = AlPanelStatus()

# The set of commands that can be used to arm and disarm the panel
class AlPanelCommand(AlEnum):
    # Include all case variations for the alarm_panel_command HA service
    #   The values used in the code have to be first
    DISARM = AlIntEnum(0)
    ARM_HOME = AlIntEnum(1)
    ARM_AWAY = AlIntEnum(2)
    ARM_HOME_INSTANT = AlIntEnum(3)
    ARM_AWAY_INSTANT = AlIntEnum(4)
    MUTE = AlIntEnum(5)
    TRIGGER = AlIntEnum(6)
    FIRE = AlIntEnum(7)
    EMERGENCY = AlIntEnum(8)
    PANIC = AlIntEnum(9)
    CHANGE_BAUD = AlIntEnum(20)
a = AlPanelCommand()

# The set of commands that can be used to mute and trigger the siren
#class AlSirenCommand(AlEnum):
#    # Include all case variations for the alarm_siren_command HA service
#    #   The values used in the code have to be first#
#
#a = AlSirenCommand()

# The set of switch commands
class AlX10Command(AlEnum):
    OFF = AlIntEnum(0)
    ON = AlIntEnum(1)
    DIM = AlIntEnum(2)
    BRIGHTEN = AlIntEnum(3)
a = AlX10Command()

# The result of using the set of commands
class AlCommandStatus(AlEnum):
    SUCCESS = AlIntEnum(0)
    FAIL_DOWNLOAD_IN_PROGRESS = AlIntEnum(1)
    FAIL_INVALID_CODE = AlIntEnum(2)
    FAIL_USER_CONFIG_PREVENTED = AlIntEnum(3)
    FAIL_INVALID_STATE = AlIntEnum(4)
    FAIL_X10_PROBLEM = AlIntEnum(5)
    FAIL_PANEL_CONFIG_PREVENTED = AlIntEnum(6)
    FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED = AlIntEnum(7)
    FAIL_PANEL_NO_CONNECTION = AlIntEnum(8)
a = AlCommandStatus()

# This is used to update the HA frontend and send out an HA Event
# Note that PanelCondition in client.py uses numbers 11 to 14.
#   Only 1 to 14 are output to HA as events.
class AlCondition(AlEnum):
    PUSH_CHANGE = AlIntEnum(0)               # This causes the client to update the frontend etc but it does not send out an HA Event
    ZONE_UPDATE = AlIntEnum(1)
    PANEL_UPDATE = AlIntEnum(2)
    PANEL_UPDATE_ALARM_ACTIVE = AlIntEnum(3)
    PANEL_RESET = AlIntEnum(4)
    PIN_REJECTED = AlIntEnum(5)
    PANEL_TAMPER_ALARM = AlIntEnum(6)
    DOWNLOAD_TIMEOUT = AlIntEnum(7)
    WATCHDOG_TIMEOUT_GIVINGUP = AlIntEnum(8)
    WATCHDOG_TIMEOUT_RETRYING = AlIntEnum(9)
    NO_DATA_FROM_PANEL = AlIntEnum(10)
    COMMAND_REJECTED = AlIntEnum(15)
    DOWNLOAD_SUCCESS = AlIntEnum(16)        # In the client this triggers the setting of the string name in the Config settings to the panel type
a = AlCondition()

# This class represents the panels trouble state
class AlTroubleType(AlEnum):
    UNKNOWN = AlIntEnum(0)
    NONE = AlIntEnum(1)
    GENERAL = AlIntEnum(2)
    COMMUNICATION = AlIntEnum(3)
    BATTERY = AlIntEnum(4)
    POWER = AlIntEnum(5)
    JAMMING = AlIntEnum(6)
    TELEPHONE = AlIntEnum(7)
a = AlTroubleType()

# This is used for when AlCondition is set to ZONE_UPDATE to update the HA
#   frontend and send out an HA Event
class AlSensorCondition(AlEnum):
    RESET = AlIntEnum(0)      # This causes the client to update the frontend etc but it does not send out an HA Event
    STATE = AlIntEnum(1)
    TAMPER = AlIntEnum(2)
    BATTERY = AlIntEnum(3)
    BYPASS = AlIntEnum(4)
    PROBLEM = AlIntEnum(5)
    ENROLLED = AlIntEnum(6)
    FIRE = AlIntEnum(7)
    EMERGENCY = AlIntEnum(8)
    PANIC = AlIntEnum(9)
    CAMERA = AlIntEnum(10)
a = AlSensorCondition()

# List of sensor types
class AlSensorType(AlEnum):
    IGNORED = AlIntEnum(-2)
    UNKNOWN = AlIntEnum(-1)
    MOTION = AlIntEnum(0)
    MAGNET = AlIntEnum(1)
    CAMERA = AlIntEnum(2)
    WIRED = AlIntEnum(3)
    SMOKE = AlIntEnum(4)
    FLOOD = AlIntEnum(5)
    GAS = AlIntEnum(6)
    VIBRATION = AlIntEnum(7)
    SHOCK = AlIntEnum(8)
    TEMPERATURE = AlIntEnum(9)
    SOUND = AlIntEnum(10)
a = AlSensorType()

class AlLogPanelEvent:
    def __init__(self):
        self.current = None
        self.total = None
        self.partition = None
        self.time = None
        self.date = None
        self.zone = None
        self.event = None

    def __str__(self):
        strn = ""
        strn = strn + ("part=None" if self.partition is None else "part={0:<2}".format(self.partition))
        strn = strn + ("    current=None" if self.current is None else "    current={0:<2}".format(self.current))
        strn = strn + ("    total=None" if self.total is None else "    total={0:<2}".format(self.total))
        strn = strn + ("    time=None" if self.time is None else "    time={0:<2}".format(self.time))
        strn = strn + ("    date=None" if self.date is None else "    date={0:<2}".format(self.date))
        strn = strn + ("    zone=None" if self.zone is None else "    zone={0:<2}".format(self.zone))
        strn = strn + ("    event=None" if self.event is None else "    event={0:<2}".format(self.event))
        return strn


class AlSensorDevice(ABC):

    @abstractmethod
    def __str__(self) -> str:
        return ""

    @abstractmethod
    def getDeviceID(self) -> int:
        return self.id

    @abstractmethod
    def isTriggered(self) -> bool:
        return False

    @abstractmethod
    def isOpen(self) -> bool:
        return False

    @abstractmethod
    def isBypass(self) -> bool:
        return False

    @abstractmethod
    def isLowBattery(self) -> bool:
        return False

    @abstractmethod
    def isEnrolled(self) -> bool:
        return False

    @abstractmethod
    def hasJPG(self) -> bool:
        return False

    @abstractmethod
    def getLastTriggerTime(self) -> datetime:
        return None

    @abstractmethod
    def getSensorType(self) -> AlSensorType:
        return AlSensorType.UNKNOWN

    @abstractmethod
    def getZoneLocation(self) -> str:
        return ""

    @abstractmethod
    def getZoneType(self) -> str:
        return ""

    @abstractmethod
    def onChange(self, callback : Callable = None):
        pass

    # The following functions are not abstract but implement if possible
    def getChimeType(self) -> str:
        return "Unknown"

    def isTamper(self) -> bool:
        return False

    def isZoneTamper(self) -> bool:
        return False

    # This is only applicable to PowerMaster Panels. It is the motion off time per sensor.
    def getMotionDelayTime(self) -> str:
        return NO_DELAY_SET

    # Do not override me
    def createFriendlyName(self) -> str:
        return "Z{0:0>2}".format(self.getDeviceID())

    # Return the sensor mode.  This is a string such as "Visonic MTT-302" to show in the HA frontend
    def getSensorModel(self) -> str:
        return "Unknown"

    # Return the raw sensor identifier if obtained from the panels EEPROM. This is shown in the sensor attributes in HA
    #     Its main use is when getSensorType() returns AlSensorType.UNKNOWN
    def getRawSensorIdentifier(self) -> int:
        return None


class AlSwitchDevice(ABC):

    @abstractmethod
    def __str__(self):
        return ""

    @abstractmethod
    def getDeviceID(self) -> int:
        return self.id

    @abstractmethod
    def isEnabled(self) -> bool:
        return False

    @abstractmethod
    def getType(self) -> str:
        return ""

    @abstractmethod
    def getLocation(self) -> str:
        return ""

    @abstractmethod
    def isOn(self) -> bool:
        return False

    @abstractmethod
    def onChange(self, callback : Callable = None):
        pass

    # Do not override me
    def createFriendlyName(self) -> str:
        if self.getDeviceID() == 0:
            return "PGM"
        return "X{0:0>2}".format(self.getDeviceID())


class PanelConfig(TypedDict):
    AlConfiguration.ForceStandard:        bool
    AlConfiguration.AutoEnroll:           bool
    AlConfiguration.AutoSyncTime:         bool
    AlConfiguration.DisableAllCommands:   bool
    AlConfiguration.CompleteReadOnly:     bool
    AlConfiguration.DownloadCode:         str
    AlConfiguration.PluginLanguage:       str
    AlConfiguration.MotionOffDelay:       int
    AlConfiguration.SirenTriggerList:     list[str]
    AlConfiguration.EEPROMAttributes:     bool


class AlTransport(ABC):

    @abstractmethod
    def write(self, b : bytearray):
        pass

    @abstractmethod
    def close(self):
        pass

class AlPanelDataStream(ABC):

    @abstractmethod
    def vp_connection_made(self, transport : AlTransport):
        pass

    @abstractmethod
    def vp_data_received(self, data):
        pass

    def vp_eof_received(self):
        pass

    def vp_connection_lost(self, exc):
        pass


# the underlying class implements these so you can call them
class AlPanelInterface(ABC):

    @abstractmethod
    def updateSettings(self, newdata: PanelConfig):
        pass

    @abstractmethod
    def shutdownOperation(self):
        """ Terminate the connection to the panel. """
        pass

    @abstractmethod
    def isSirenActive(self) -> bool:
        """ Is the siren active. """
        return False

    @abstractmethod
    def getPanelStatus(self) -> AlPanelStatus:
        """ Get the panel state i.e. Disarmed, Arming Home etc. """
        return AlPanelStatus.UNKNOWN

    @abstractmethod
    def getPanelMode(self) -> AlPanelMode:
        """ Get the panel Mode e.g. Standard, Powerlink etc. """
        return AlPanelMode.UNKNOWN

    def isPowerMaster(self) -> bool:
        """ Get the panel type, PowerMaster or not """
        return False

    @abstractmethod
    def getPanelModel(self) -> str:
        return "Unknown"

    @abstractmethod
    def isPanelReady(self) -> bool:
        """ Get the panel ready state """
        return False

    @abstractmethod
    def getPanelTrouble(self) -> AlTroubleType:
        """ Get the panel trouble state """
        return AlTroubleType.UNKNOWN

    @abstractmethod
    def isPanelBypass(self) -> bool:
        """ Get the panel bypass state """
        return False

    @abstractmethod
    def getPanelLastEvent(self) -> str:
        """ Return the panels last event string """
        return ""

    # @abstractmethod
    # def getPanelTroubleStatus(self) -> str:
    #    return ""

    # A dictionary that is used to add to the attribute list of the Alarm Control Panel
    #     If this is overridden then please include the items in the dictionary defined here by using super()
    @abstractmethod
    def getPanelStatusDict(self) -> dict:
        """ Get a dictionary representing the panel status. """
        return {}

    # A dictionary that is used to add to the attribute list of the Alarm Control Panel
    #     If this is overridden then please include the items in the dictionary defined here by using super()
    @abstractmethod
    def getPanelFixedDict(self) -> dict:
        """ Get a dictionary representing the panel status. """
        return {}

    # Arm / Disarm the Panel
    # state is the command to set the panel state i.e. disarm, arm_away etc
    # Set code to:
    #    None when we are in Powerlink or Standard Plus and to use the code code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this may work depending on the panel type for arming, but not for disarming)
    @abstractmethod
    def requestPanelCommand(self, state : AlPanelCommand, code : str = "") -> AlCommandStatus:
        """ Send a request to the panel to Arm/Disarm """
        return AlCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    # device in range 0 to 15 (inclusive), 0=PGM, 1 to 15 are X10 devices
    # state is the X10 state to set the switch
    @abstractmethod
    def setX10(self, device : int, state : AlX10Command) -> AlCommandStatus:
        """ Se the state of an X10 switch. """
        return AlCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    @abstractmethod
    def getJPG(self, device : int, count : int) -> AlCommandStatus:
        return AlCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    @abstractmethod
    def dumpSensorsToStringList(self) -> list:
        return []

    @abstractmethod
    def dumpSwitchesToStringList(self) -> list:
        return []

    # @abstractmethod
    # def dumpStateToStringList(self) -> list:
    #    return []

    # Set the Sensor Bypass to Arm/Bypass individual sensors
    # sensor in range 1 to 31 for PowerMax and 1 to 63 for PowerMaster (inclusive) depending on alarm
    # bypassValue is False to Arm the Sensor and True to Bypass the sensor
    # Set code to:
    #    None when we are in Powerlink or Standard Plus and to use the code code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this is unlikely to work on any panel)
    @abstractmethod
    def setSensorBypassState(self, sensor : int, bypassValue : bool, code : str = "") -> AlCommandStatus:
        """ Set or Clear Sensor Bypass """
        return AlCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    # Get the panels event log
    # Set code to:
    #    None when we are in Powerlink or Standard Plus and to use the code code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this is unlikely to work on any panel)
    @abstractmethod
    def getEventLog(self, code : str = "") -> AlCommandStatus:
        """ Get Panel Event Log """
        return AlCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    # Set the onPanelChange callback handlers
    @abstractmethod
    def onPanelChange(self, fn : Callable):             # onPanelChange ( event_id : AlCondition )
        pass

    # Set the onDisconnect callback handlers
    @abstractmethod
    def onDisconnect(self, fn : Callable):             # onDisconnect ( reason: str, ex : exception or None )
        pass

    # Set the onNewSensor callback handlers
    @abstractmethod
    def onNewSensor(self, fn : Callable):             # onNewSensor ( device : AlSensorDevice )
        pass

    # Set the onNewSwitch callback handlers
    @abstractmethod
    def onNewSwitch(self, fn : Callable):             # onNewSwitch ( sensor : AlSwitchDevice )
        pass

    # Set the onPanelLog callback handlers
    @abstractmethod
    def onPanelLog(self, fn : Callable):             # onPanelLog ( event_log_entry : AlLogPanelEvent )
        pass

# Turn on auto code formatting when using black
# fmt: on

