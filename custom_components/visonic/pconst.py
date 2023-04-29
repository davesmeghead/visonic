from enum import IntEnum
from abc import ABC, abstractmethod
from typing import Callable
from datetime import datetime

# the set of configuration parameters in to this client class
class PyConfiguration(IntEnum):
    DownloadCode = 0           # 4 digit string or ""
    ForceAutoEnroll = 1        # Boolean
    AutoSyncTime = 2           # Boolean
    PluginLanguage = 3         # String "EN", "FR", "NL"
    MotionOffDelay = 4         # Integer (seconds)
    SirenTriggerList = 5       # 
    B0_Enable = 6              # Boolean
    B0_Min_Interval_Time = 7   # Integer (seconds)
    B0_Max_Wait_Time = 8       # Integer (seconds)
    ForceStandard = 9          # Boolean

# The set of panel states
class PyPanelMode(IntEnum):
    UNKNOWN = 0  # Not used but here just in case of future use
    PROBLEM = 1
    STARTING = 2
    STANDARD = 3
    STANDARD_PLUS = 4
    POWERLINK = 5
    DOWNLOAD = 6
    STOPPED = 7

class PyPanelStatus(IntEnum):
    UNKNOWN = -1
    DISARMED = 0
    ARMING_HOME = 1
    ARMING_AWAY = 2
    ENTRY_DELAY = 3
    ARMED_HOME = 4
    ARMED_AWAY = 5
    SPECIAL = 6
    DOWNLOADING = 7

class PyPanelCommand(IntEnum):
    # Include all case variations for the alarm_panel_command HA service
    #   The values used in the code have to be first
    DISARM = 0
    ARM_HOME = 1
    ARM_AWAY = 2
    ARM_HOME_INSTANT = 3
    ARM_AWAY_INSTANT = 4
    disarm = 0
    Disarm = 0
    arm_home = 1
    arm_away = 2
    arm_home_instant = 3
    arm_away_instant = 4
    armhome = 1
    armaway = 2
    armhomeinstant = 3
    armawayinstant = 4
    Arm_Home = 1
    Arm_Away = 2
    Arm_Home_Instant = 3
    Arm_Away_Instant = 4
    ArmHome = 1
    ArmAway = 2
    ArmHomeInstant = 3
    ArmAwayInstant = 4

class PyX10Command(IntEnum):
    OFF = 0
    ON = 1
    DIM = 2
    BRIGHTEN = 3

class PyCommandStatus(IntEnum):
    SUCCESS = 0
    FAIL_DOWNLOAD_IN_PROGRESS = 1
    FAIL_INVALID_PIN = 2
    FAIL_USER_CONFIG_PREVENTED = 3
    FAIL_INVALID_STATE = 4
    FAIL_X10_PROBLEM = 5
    FAIL_PANEL_CONFIG_PREVENTED = 6
    FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED = 7
    FAIL_PANEL_NO_CONNECTION = 8

class PyCondition(IntEnum):   # Note that PanelCondition in client.py uses numbers 11 to 14.  Only 1 to 14 are output to HA as events.
    PUSH_CHANGE = 0
    ZONE_UPDATE = 1
    PANEL_UPDATE = 2
    PANEL_UPDATE_ALARM_ACTIVE = 3
    PANEL_RESET = 4
    PIN_REJECTED = 5
    PANEL_TAMPER_ALARM = 6
    DOWNLOAD_TIMEOUT = 7
    WATCHDOG_TIMEOUT_GIVINGUP = 8
    WATCHDOG_TIMEOUT_RETRYING = 9
    NO_DATA_FROM_PANEL = 10
    COMMAND_REJECTED = 15
    DOWNLOAD_SUCCESS = 16

class PySensorType(IntEnum):
    UNKNOWN = -1
    MOTION = 0
    MAGNET = 1
    CAMERA = 2
    WIRED = 3
    SMOKE = 4
    FLOOD = 5
    GAS = 6
    VIBRATION = 7
    SHOCK = 8
    TEMPERATURE = 9
    SOUND = 10
    def __str__(self):
        return str(self.name).title()


class PySensorDevice(ABC):

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
    def getLastTriggerTime(self) -> datetime:
        return None

    @abstractmethod
    def getDeviceName(self) -> str:
        return ""
        
    @abstractmethod
    def getSensorType(self) -> PySensorType:
        return PySensorType.UNKNOWN

    @abstractmethod
    def getAttributes(self) -> dict:
        return {}


class PyLogPanelEvent:
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
        strn = strn + ("part=None" if self.partition == None else "part={0:<2}".format(self.partition))
        strn = strn + ("    current=None" if self.current == None else "    current={0:<2}".format(self.current))
        strn = strn + ("    total=None" if self.total == None else "    total={0:<2}".format(self.total))
        strn = strn + ("    time=None" if self.time == None else "    time={0:<2}".format(self.time))
        strn = strn + ("    date=None" if self.date == None else "    date={0:<2}".format(self.date))
        strn = strn + ("    zone=None" if self.zone == None else "    zone={0:<2}".format(self.zone))
        strn = strn + ("    event=None" if self.event == None else "    event={0:<2}".format(self.event))
        return strn


class PySwitchDevice(ABC):
    
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
    def getName(self) -> str:
        return ""

    @abstractmethod
    def getType(self) -> str:
        return ""

    @abstractmethod
    def getLocation(self) -> str:
        return ""

    @abstractmethod
    def isOn(self) -> bool:
        return False
    
        
class PyPanelInterface(ABC):
    
    @abstractmethod
    def updateSettings(self, newdata: dict):
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
    def getPanelStatusCode(self) -> PyPanelStatus:
        """ Get the panel state i.e. Disarmed, Arming Home etc. """
        return PyPanelStatus.UNKNOWN

    @abstractmethod
    def isPowerMaster(self) -> bool:
        """ Is the panel a PowerMaster. """
        return False

    @abstractmethod
    def getPanelMode(self) -> PyPanelMode:
        """ Get the panel Mode e.g. Standard, Powerlink etc. """
        return PyPanelMode.UNKNOWN

    # Retrieve the sensor or None if error
    #    Do not make changes to the SensorDevice
    #    sensor in range 1 to 31 for PowerMax and 1 to 63 for PowerMaster (inclusive)
    @abstractmethod
    def getSensor(self, sensor) -> PySensorDevice:
        """ Return the sensor."""
        return None

    # This is used to populate a dictionary when an event is sent on the HA Event Bus
    @abstractmethod
    def populateDictionary(self) -> dict:
        """ Populate a dictionary when an event is sent on the HA Event Bus. """
        return {}

    # A dictionary that is used to add to the attribute list of the Alarm Control Panel
    @abstractmethod
    def getPanelStatus(self, full : bool) -> dict:
        """ Get a dictionary representing the panel status. """
        return {}

    # Arm / Disarm the Panel
    # state is the command to set the panel state i.e. disarm, arm_away etc
    # Set pin to:
    #    None when we are in Powerlink or Standard Plus and to use the pin code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this may work depending on the panel type for arming, but not for disarming)
    @abstractmethod
    def requestArm(self, state : PyPanelCommand, pin : str = "") -> PyCommandStatus:
        """ Send a request to the panel to Arm/Disarm """
        return PyCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    # device in range 0 to 15 (inclusive)
    # state is the X10 state to set the switch
    @abstractmethod
    def setX10(self, device : int, state : PyX10Command) -> PyCommandStatus:
        """ Se the state of an X10 switch. """
        return PyCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    # sensor in range 1 to 31 for PowerMax and 1 to 63 for PowerMaster (inclusive)
    # bypassValue is False to Arm the Sensor and True to Bypass the sensor
    # Set pin to:
    #    None when we are in Powerlink or Standard Plus and to use the pin code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this is unlikely to work on any panel)
    @abstractmethod
    def setSensorBypassState(self, sensor : int, bypassValue : bool, pin : str = "") -> PyCommandStatus:
        """ Set or Clear Sensor Bypass """
        return PyCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    # Set pin to:
    #    None when we are in Powerlink or Standard Plus and to use the pin code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this is unlikely to work on any panel)
    @abstractmethod
    def getEventLog(self, pin : str = "") -> PyCommandStatus:
        """ Get Panel Event Log """
        return PyCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED

    @abstractmethod
    def setCallbackHandlers(self, 
            event_callback : Callable = None,             # event_callback ( event_id : PyCondition, datadictionary : dict )
            disconnect_callback : Callable = None,        # disconnect_callback ( exception or string or None )
            new_sensor_callback : Callable = None,        # new_sensor_callback ( device : PySensorDevice )
            new_switch_callback : Callable = None,        # new_switch_callback ( sensor : PySwitchDevice )
            panel_event_log_callback : Callable = None):  # panel_event_log_callback ( event_log_entry : PyLogPanelEvent )
        """ Install the callback handlers for the various actions. """
        pass

