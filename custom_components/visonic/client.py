"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""
import asyncio
import logging
from typing import Callable, Any
import re
import socket
import datetime
from datetime import datetime, timedelta, timezone
from jinja2 import Environment, FileSystemLoader
from functools import partial
import threading
import collections
from collections import namedtuple

from enum import IntEnum
from requests import ConnectTimeout, HTTPError

from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.util import slugify
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
#from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    Platform,
    ATTR_CODE,
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PATH,
    CONF_PORT,
    CONF_LANGUAGE,
    CONF_USERNAME, 
    CONF_PASSWORD,
    EVENT_HOMEASSISTANT_STOP,
)

from homeassistant.helpers import entity_platform as ep
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from homeassistant.components import persistent_notification
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.image import DOMAIN as IMAGE_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.siren import DOMAIN as SIREN_DOMAIN
from homeassistant.components.alarm_control_panel import DOMAIN as ALARM_PANEL_DOMAIN
from homeassistant.util.thread import ThreadWithException

from .pyconst import (AlEnum, AlTransport, PanelConfig, AlConfiguration, AlPanelMode, AlPanelCommand, AlPanelStatus, AlTroubleType, AlSensorType,  
                      AlAlarmType, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlSensorDevice, AlLogPanelEvent, AlSwitchDevice, AlTerminationType,
                      PE_PARTITION, PE_EVENT, PE_NAME, PE_TIME)
from .pyvisonic import VisonicProtocol

from .const import (
    available_emulation_modes,
    ALARM_PANEL_CHANGE_EVENT,
    ALARM_SENSOR_CHANGE_EVENT,
    ALARM_COMMAND_EVENT,
    ALARM_PANEL_LOG_FILE_COMPLETE,
    ALARM_PANEL_LOG_FILE_ENTRY,
    ATTR_BYPASS,
    VISONIC_UNIQUE_NAME,
    CONF_EXCLUDE_SENSOR,
    CONF_EXCLUDE_X10,
    CONF_ENABLE_REMOTE_ARM,
    CONF_ENABLE_REMOTE_DISARM,
    CONF_ENABLE_SENSOR_BYPASS,
    CONF_ARM_CODE_AUTO,
    CONF_FORCE_KEYPAD,
    CONF_ARM_HOME_ENABLED,
    CONF_ARM_NIGHT_ENABLED,
    CONF_INSTANT_ARM_AWAY,
    CONF_INSTANT_ARM_HOME,
    CONF_EPROM_ATTRIBUTES,
    CONF_DEVICE_BAUD,
    CONF_DEVICE_TYPE,
    CONF_DOWNLOAD_CODE,
    CONF_EMULATION_MODE,
    CONF_MOTION_OFF_DELAY,
    CONF_MAGNET_CLOSED_DELAY,
    CONF_EMER_OFF_DELAY,
    CONF_SIREN_SOUNDING,
    CONF_LOG_CSV_FN,
    CONF_LOG_CSV_TITLE,
    CONF_LOG_DONE,
    CONF_LOG_EVENT,
    CONF_LOG_MAX_ENTRIES,
    CONF_LOG_REVERSE,
    CONF_LOG_XML_FN,
    CONF_RETRY_CONNECTION_COUNT,
    CONF_RETRY_CONNECTION_DELAY,
    CONF_COMMAND,
    CONF_X10_COMMAND,
    TEXT_DISCONNECTION_COUNT,
    TEXT_CLIENT_VERSION,
    TEXT_LAST_EVENT_NAME,
    TEXT_LAST_EVENT_TIME,
    TEXT_LAST_EVENT_ACTION,
    TEXT_XML_LOG_FILE_TEMPLATE,
    DOMAIN,
    PLATFORMS,
    NOTIFICATION_ID,
    NOTIFICATION_TITLE,
    CONF_ALARM_NOTIFICATIONS,
    PANEL_ATTRIBUTE_NAME,
    DEVICE_ATTRIBUTE_NAME,
    DEFAULT_DEVICE_BAUD,
    DEVICE_TYPE_ETHERNET,
    DEVICE_TYPE_USB,
    AvailableNotifications,
    PIN_REGEX,
    VisonicConfigEntry,
    VisonicConfigKey,
    VisonicConfigData,
)

CLIENT_VERSION = "0.12.0.0"

MAX_CLIENT_LOG_ENTRIES = 1000

_LOGGER = logging.getLogger(__name__)

messageDictReason = {
    AlCommandStatus.SUCCESS                             : "Success, sent Command to Panel",
    AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS           : "Failed to Send Command To Panel, not supported when downloading EPROM",
    AlCommandStatus.FAIL_INVALID_CODE                   : "Failed to Send Command To Panel, not allowed without valid pin",
    AlCommandStatus.FAIL_USER_CONFIG_PREVENTED          : "Failed to Send Command To Panel, disabled by user settings",
    AlCommandStatus.FAIL_INVALID_STATE                  : "Failed to Send Command To Panel, invalid state requested",
    AlCommandStatus.FAIL_X10_PROBLEM                    : "Failed to Send Command To Panel, general X10 Problem",
    AlCommandStatus.FAIL_PANEL_CONFIG_PREVENTED         : "Failed to Send Command To Panel, disabled by panel settings",
    AlCommandStatus.FAIL_ENTITY_INCORRECT               : "Failed to Send Command To Panel, entity not supported",
    AlCommandStatus.FAIL_PANEL_NO_CONNECTION            : "Failed to Send Command To Panel, no connection to panel",
    AlCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED : "Failed to Send Command To Panel, report error to integration author and send a log file"
}

class PanelCondition(IntEnum): # Start at 100 to make them unique for AlarmPanelEventActionList mixing with AlCondition
    CHECK_ARM_DISARM_COMMAND = 100
    CHECK_BYPASS_COMMAND = 101
    CHECK_EVENT_LOG_COMMAND = 102
    CHECK_X10_COMMAND = 103
    CONNECTION = 104
    PANEL_LOG_COMPLETE = 105
    PANEL_LOG_ENTRY = 106

HA_Event_Type = collections.namedtuple('HA_Event_Type', 'name action')  # If action is an empty string then it is not added
AlarmPanelEventActionList = {
    AlCondition.ZONE_UPDATE                 : HA_Event_Type(ALARM_SENSOR_CHANGE_EVENT,     ""),
    AlCondition.PANEL_UPDATE                : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "panelupdate"), 
    AlCondition.PANEL_RESET                 : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "panelreset"),
    AlCondition.PIN_REJECTED                : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "pinrejected"),
    AlCondition.DOWNLOAD_TIMEOUT            : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "timeoutdownload"), 
    AlCondition.WATCHDOG_TIMEOUT_GIVINGUP   : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "timeoutwaiting"), 
    AlCondition.WATCHDOG_TIMEOUT_RETRYING   : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "timeoutactive"), 
    AlCondition.NO_DATA_FROM_PANEL          : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "nopaneldata"), 
    PanelCondition.CONNECTION               : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "connection"),
    PanelCondition.PANEL_LOG_COMPLETE       : HA_Event_Type(ALARM_PANEL_LOG_FILE_COMPLETE, ""),
    PanelCondition.PANEL_LOG_ENTRY          : HA_Event_Type(ALARM_PANEL_LOG_FILE_ENTRY,    ""),
    PanelCondition.CHECK_ARM_DISARM_COMMAND : HA_Event_Type(ALARM_COMMAND_EVENT,           "armdisarm"), 
    PanelCondition.CHECK_BYPASS_COMMAND     : HA_Event_Type(ALARM_COMMAND_EVENT,           "bypass"), 
    PanelCondition.CHECK_EVENT_LOG_COMMAND  : HA_Event_Type(ALARM_COMMAND_EVENT,           "eventlog"), 
    PanelCondition.CHECK_X10_COMMAND        : HA_Event_Type(ALARM_COMMAND_EVENT,           "x10")
}

##############################################################################################################################################################################################################################################
##########################  Panel Event coordinator to manage A5, B0.24 and A7 panel state and event data ####################################################################################################################################
##############################################################################################################################################################################################################################################

class PanelEventCoordinator:
    
    def __init__(self, loop, callbackSender, ispm = False, logstate_debug = None):
        if logstate_debug is None:
            self.logstate_debug = self._dummy
        else:
            self.logstate_debug = logstate_debug
        if callbackSender is None:
            self.callbackSender = self._dummy
        else:
            self.callbackSender = callbackSender
        self.logstate_debug(f"[EC] Starting")
        self.loop = loop
        self.isPowerMaster = ispm
        self.init_vars()

    def init_vars(self):
        self.EventTime = 0
        self.EventName = 0
        self.EventAction = -100
        self.EventPartition = None
        self._event_timer_task = None
        self.timerAlreadySent = True

    def close(self):
        try:
            if self._event_timer_task is not None:
                self._event_timer_task.cancel()
            self.init_vars()
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            self.logstate_debug("[PanelEventCoordinator]     Close Caused an exception")
            self.logstate_debug(f"             {ex}")

    def _dummy(self, msg, *args, **kwargs):
        pass

    def _sendData(self):
        if self.EventAction >= 0:
            d = self._convert()
            self.logstate_debug(f"[EC] sending panel update {self.EventName=} {self.EventAction=} as data {d}")
            self.callbackSender(AlCondition.PANEL_UPDATE, d)
        else:
            self.logstate_debug(f"[EC] _sendData wont send blank data")

    def _convert(self) -> dict:
        from . import pmLogEvent_t, pmLogPowerMaxUser_t, pmLogPowerMasterUser_t
        d = {}
        # Set the name
        d[PE_NAME] = "Unknown"
        if self.isPowerMaster:
            d[PE_NAME] = pmLogPowerMasterUser_t[self.EventName] or "Unknown"
        else:
            d[PE_NAME] = pmLogPowerMaxUser_t[int(self.EventName & 0x7F)] or "Unknown"
        # Set the event
        d[PE_EVENT] = "Unknown"
        if 0 <= self.EventAction <= 151:
            if len(pmLogEvent_t[self.EventAction]) > 0:
                d[PE_EVENT] = pmLogEvent_t[self.EventAction]
        # Set the time
        d[PE_TIME] = self.EventTime
        if self.EventPartition is not None:
            d[PE_PARTITION] = self.EventPartition
        return d

    async def _event_timer(self):
        self.timerAlreadySent = False
        #self.logstate_debug(f"[EC] _event_timer started")
        await asyncio.sleep(0.4)
        #self.logstate_debug(f"[EC] _event_timer expired")
        self._sendData()
        self.timerAlreadySent = True
    
    def _send_and_replace(self, data : dict):
        if self._event_timer_task is not None:
            #self.logstate_debug("[EC] Cancelling _event_timer_task")
            try:
                self._event_timer_task.cancel()
            except Exception as ex:
                # Do not cause a full Home Assistant Exception, keep it local here
                self.logstate_debug("[_send_and_replace]     Caused an exception")
                self.logstate_debug(f"             {ex}")
        # send existing data
        if not self.timerAlreadySent:
            self._sendData()
        # save new data
        self.EventName = data[PE_NAME]
        self.EventAction = data[PE_EVENT]
        self.EventTime = data[PE_TIME]
        self.EventPartition = data[PE_PARTITION] if PE_PARTITION in data else None
        self.logstate_debug(f"[EC] _send_and_replace {data}     partition = {self._convert()[PE_PARTITION] if self.EventPartition is not None else "Not set as it is a panel"}    " + 
                 f"name = {self._convert()[PE_NAME]}    event = {self._convert()[PE_EVENT]}")  # e.g. {'name': 0, 'event': 28, 'time': '04/10/2024, 22:46:04'}
        self._event_timer_task = self.loop.create_task(self._event_timer())
    
    def setIsPowerMaster(self, pm):
        self.isPowerMaster = pm

    def addEvent(self, data : dict):
        if data is not None:
            #self.logstate_debug(f"[EC] addEvent {data}")
            
            if self.EventAction != data[PE_EVENT]:
                # If the action is not the same
                self._send_and_replace(data)
            else:
                # If the action is the same
                if self.EventName == data[PE_NAME]:   # exactly the same event as last time then do not send it
                    # Name is exactly the same as what we already have
                    #self.logstate_debug(f"[EC] Panel event data {data} is the same as last time so not sending event")
                    return
                if self.EventName != 0 and data[PE_NAME] == 0:
                    # Existing Name is better than new one
                    self.logstate_debug(f"[EC] Panel event data {data} is the same Event but I already have a better name")
                    return
                if self.EventName == 0 and data[PE_NAME] != 0:
                    # The existing name is 0 (i.e. system) and the new name is better so replace it
                    self.logstate_debug(f"[EC] Replacing 'system' with {data["name"]} but keeping original time {self.EventTime}")
                    self.EventName = data["name"]
                    #self.EventTime = data[PE_TIME]
                    return
                # Here when the existing name and the new name are different and both non-zero
                #   Send the previous and replace with the new
                self._send_and_replace(data)
                

class MyTransport(AlTransport):

    def __init__(self, t):
        self._transport = t
    
    def write(self, b : bytearray):
        #_LOGGER.debug(f"Data Sent {b}")
        self._transport.write(b)

    def close(self):
        self._transport.close()

# This class joins the Protocol data stream to the visonic protocol handler.
#    transport needs to have 2 functions:   write(bytearray)  and  close()
class ClientVisonicProtocol(asyncio.Protocol):

    def __init__(self, vp : VisonicProtocol, client):
        #super().__init__(*args, **kwargs)
        #_LOGGER.debug(f"CVP Init")
        self._transport = None
        self.vp = vp
        self.client = client
        if client is not None:
            client.tellemaboutme(self)

    def data_received(self, data):
        #_LOGGER.debug(f"Received Data {data}")
        self.vp.data_received(data)

    def connection_made(self, transport):
        _LOGGER.debug(f"connection_made Whooooo")
        self._transport = MyTransport(transport)
        self.vp.setTransportConnection(self._transport)

    def _stop(self):
        _LOGGER.debug("stop called")
        self.client = None
        self.vp = None
        if self._transport is not None:
            _LOGGER.debug("stop called on protocol => closed")
            self._transport.close()
        self._transport = None
        _LOGGER.debug("stop finished")

    def connection_lost(self, exc):
        _LOGGER.debug(f"connection_lost Booooo")
        if self.client is not None:
            _LOGGER.debug(f"connection_lost    setup to reconnect, if allowed by the user config")
            self.client.hass.loop.create_task(self.client.async_reconnect_and_restart(force_reconnect = False, allow_restart = False)) # Try a simple reconnect but only if user config allows
        if self._transport is not None:
            self._stop()
        _LOGGER.debug("connection_lost finished")

    def close(self):
        #_LOGGER.debug(f"Connection Closed")
        _LOGGER.debug("close called on protocol")
        if self._transport is not None:
            self._stop()
        _LOGGER.debug("close finished")

    # This is needed so we can create the class instance before giving it to the protocol handlers
    def __call__(self):
        return self

class VisonicClient:
    """Set up for Visonic devices."""
    
    _LOGGER.debug(f"Initialising Client - Version {CLIENT_VERSION}")

    def __init__(self, hass: HomeAssistant, panelident: int, cf: dict, entry: VisonicConfigEntry):
        """Initialize the Visonic Client."""
        self.hass = hass
        self.entry = entry
        # Get the user defined config
        self.config = cf.copy()
        self.strlog = []
        self.panelident = panelident
        self.doingRestart = None
        self.logstate_debug(f"init panel {str(panelident)}  language {str(self.hass.config.language)}")
        self._initialise()
        self.logstate_debug(f"Exclude sensor list = {self.exclude_sensor_list}     Exclude x10 list = {self.exclude_x10_list}")
        self.panel_disconnection_counter = 0
        
    # get the current date and time
    def _getTimeFunction(self) -> datetime:
        return datetime.now(timezone.utc).astimezone()

    def _initialise(self):
        from . import pmLogEvent_t, pmLogPowerMaxUser_t
        # panel connection
        self.logstate_debug("reset client panel variables")
        
        self.visonic_sensor_setup_lock = asyncio.Lock()
        self.visonic_switch_setup_lock = asyncio.Lock()
        self.visonic_alarm_setup_lock = asyncio.Lock()

        self.alreadyDoingThisFunction = False
        
        self.cvp = None
        self.visonicCommsTask = None
        self.visonicProtocol : AlPanelInterface = None
        self.SystemStarted = False
        self._createdAlarmPanel = False

        # variables for creating the event log for csv and xml
        self.csvdata = None
        self.templatedata = None

        self.sensor_task = None
        self.select_task = None
        self.switch_task = None
        self.image_task = None
        
        self.rationalised_ha_devices = False
        
        #self.loaded_platforms = set()
        
        self.onChangeHandler = []
        
        self.panel_entity_name = {}

        self.sensor_list = list()
        self.image_list = list()
        self.x10_list = list()

        self.delayBetweenAttempts = 60.0
        self.totalAttempts = 0

        self.DisableAllCommands = False
        self.ForceStandardMode = False

        self._setupSensorDelays()

        self.myPanelEventCoordinator = None
        self.PanelLastEventName = pmLogPowerMaxUser_t[0]  # get the language translation for "Startup", entry 0 should be the same for all panel models so just use powermax
        self.PanelLastEventAction = pmLogEvent_t[0]       # get the language translation for "Normal"
        #self.logstate_debug(f"client panel variables {self.PanelLastEventName}  {self.PanelLastEventAction}")
        self.PanelLastEventTime = self._getTimeFunction() # .strftime("%d/%m/%Y, %H:%M:%S")

        # Process the exclude sensor list
        self.exclude_sensor_list = self.config.get(CONF_EXCLUDE_SENSOR, list())
        if self.exclude_sensor_list is None or len(self.exclude_sensor_list) == 0:
            self.exclude_sensor_list = []
        if (
            isinstance(self.exclude_sensor_list, str)
            and len(self.exclude_sensor_list) > 0
        ):
            self.exclude_sensor_list = [
                int(e) if e.isdigit() else e
                for e in self.exclude_sensor_list.split(",")
            ]
        # Process the exclude X10 list
        self.exclude_x10_list = self.config.get(CONF_EXCLUDE_X10, list())
        if self.exclude_x10_list is None or len(self.exclude_x10_list) == 0:
            self.exclude_x10_list = []
        if isinstance(self.exclude_x10_list, str) and len(self.exclude_x10_list) > 0:
            self.exclude_x10_list = [
                int(e) if e.isdigit() else e for e in self.exclude_x10_list.split(",")
            ]
        self.updateConfig()       # Set variables from the config


    def _setupSensorDelays(self):
        # Trigger Off delays to apply for each sensor type
        mc = int(self.config.get(CONF_MOTION_OFF_DELAY, 120))
        dw = int(self.config.get(CONF_MAGNET_CLOSED_DELAY, 120))
        em = int(self.config.get(CONF_EMER_OFF_DELAY, 120))
        
        self.TriggerOffDelayList = {
            BinarySensorDeviceClass.MOTION    : mc,
            BinarySensorDeviceClass.WINDOW    : dw,
            BinarySensorDeviceClass.DOOR      : dw,
            BinarySensorDeviceClass.SMOKE     : em,
            BinarySensorDeviceClass.MOISTURE  : em,
            BinarySensorDeviceClass.GAS       : em,
            BinarySensorDeviceClass.VIBRATION : em,
            BinarySensorDeviceClass.VIBRATION : em,
            BinarySensorDeviceClass.HEAT      : em,
            BinarySensorDeviceClass.SOUND     : em
        }

    def getSensorOnDelay(self, st : BinarySensorDeviceClass):
        if st is not None and st in self.TriggerOffDelayList:
            return self.TriggerOffDelayList[st]
        return 120

    def logstate_debug(self, msg, *args, **kwargs):
        s = "P" + str(self.getPanelID()) + "  " + (msg % args % kwargs)
        _LOGGER.debug(s)
        self.strlog.append(str(datetime.now(timezone.utc).astimezone()) + "  D " + s)
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)
            
    def logstate_info(self, msg, *args, **kwargs):
        s = "P" + str(self.getPanelID()) + "  " + (msg % args % kwargs)
        _LOGGER.info(" " + s)
        self.strlog.append(str(datetime.now(timezone.utc).astimezone()) + "  I " + s)
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)

    def logstate_warning(self, msg, *args, **kwargs):
        s = "P" + str(self.getPanelID()) + "  " + (msg % args % kwargs)
        _LOGGER.warning(s)
        self.strlog.append(str(datetime.now(timezone.utc).astimezone()) + "  W " + s)
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)

    def getStrLog(self):
        return self.strlog

    def getEntryID(self):
        return self.entry.entry_id if self.entry is not None else ""

    def getPanelID(self):
        return self.panelident

    def getMyString(self) -> str:
        if self.getPanelID() > 0:
            return "visonic_p" + str(self.panelident) + "_"
        return "visonic_"

    def getAlarmPanelUniqueIdent(self):
        if self.getPanelID() > 0:
            return VISONIC_UNIQUE_NAME + " " + str(self.getPanelID())
        return VISONIC_UNIQUE_NAME

    def createNotification(self, condition : AvailableNotifications, message: str):
        """Create a message in the log file and a notification on the HA Frontend."""
        notification_config = self.config.get(CONF_ALARM_NOTIFICATIONS, list() )
        
        self.logstate_debug(f"notification_config {notification_config}")
        
        if condition == AvailableNotifications.ALWAYS or condition.value in notification_config:
            # Create an info entry in the log file and an HA notification
            self.logstate_info(f"HA Notification: {condition}  {message}")
            persistent_notification.create(self.hass, message, title=NOTIFICATION_TITLE, notification_id=NOTIFICATION_ID)
        else:
            # Just create a log file entry (but indicate that it wasnt shown in the frontend to the user
            self.logstate_info(f"HA Notification (not shown in frontend due to user config), condition is {condition} message={message}")

    def dumpSensorsToStringList(self) -> list:
        if self.visonicProtocol is not None:
            return self.visonicProtocol.dumpSensorsToStringList()
        return []

    def dumpSwitchesToStringList(self) -> list:
        if self.visonicProtocol is not None:
            return self.visonicProtocol.dumpSwitchesToStringList()
        return []

    #def dumpStateToStringList(self) -> list:
    #    if self.visonicProtocol is not None:
    #        return self.visonicProtocol.dumpStateToStringList()
    #    return []

    def isSirenActive(self) -> (bool, AlSensorDevice | None):
        """Is the siren active."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isSirenActive()
        return (False, None)

    def isPanelReady(self, partition : int ) -> bool:
        """Is panel ready"""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isPanelReady(partition)
        return False

    def getPartitionsInUse(self) -> set | None:
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPartitionsInUse()
        return None

    def isPanelTrouble(self, partition : int ) -> bool:
        """Is panel trouble"""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isPanelTrouble(partition)
        return False

    def isForceKeypad(self) -> bool:
        """Force Keypad"""
        return self.toBool(self.config.get(CONF_FORCE_KEYPAD, False))

    def isDisableAllCommands(self):
        return self.DisableAllCommands

    def isPowerMaster(self) -> bool:
        if self.visonicProtocol is not None:
            if self.visonicProtocol.isPowerMaster():
                return True
        return False

    def getClientStatusDict(self):
        return { TEXT_DISCONNECTION_COUNT: self.panel_disconnection_counter }

    def isArmHome(self):
        return self.toBool(self.config.get(CONF_ARM_HOME_ENABLED, True))

    def isArmNight(self):
        return self.toBool(self.config.get(CONF_ARM_NIGHT_ENABLED, True))

    def isArmWithoutCode(self) -> bool:
        """Is Arm Without Use Code"""
        return self.toBool(self.config.get(CONF_ARM_CODE_AUTO, False))

    def isArmAwayInstant(self) -> bool:
        """Is Arm Away Instant"""
        return self.toBool(self.config.get(CONF_INSTANT_ARM_AWAY, False))

    def isArmHomeInstant(self) -> bool:
        """Is Arm Home Instant"""
        return self.toBool(self.config.get(CONF_INSTANT_ARM_HOME, False))

    def isRemoteArm(self) -> bool:
        """Is it Remote Arm"""
        return self.toBool(self.config.get(CONF_ENABLE_REMOTE_ARM, False))

    def isRemoteDisarm(self) -> bool:
        """Is it Remote Disarm"""
        return self.toBool(self.config.get(CONF_ENABLE_REMOTE_DISARM, False))

    def getPanelStatus(self, partition : int | None = None) -> AlPanelStatus:
        """Get the panel status code."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelStatus(partition)
        return AlPanelStatus.UNKNOWN

    def getPanelMode(self) -> AlPanelMode:
        """Get the panel mode."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelMode()
        return AlPanelMode.UNKNOWN

    def getPanelModel(self) -> str:
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelModel()
        return None

    def getPanelFixedDict(self) -> dict:
        """Get the panel status."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelFixedDict()
        return {}

    def getPanelStatusDict(self, partition : int | None = None, include_extended_status : bool = None) -> dict:
        """Get the panel status."""
        if self.visonicProtocol is not None:
            if include_extended_status is None:
                include_extended_status = self.toBool(self.config.get(CONF_EPROM_ATTRIBUTES, False))
            pd = self.visonicProtocol.getPanelStatusDict(partition, include_extended_status)
            if partition is None:
                #self.logstate_debug(f"Client Dict {pd}")
                #pd["lastevent"] = self.PanelLastEventName + "/" + self.PanelLastEventAction
                pd[TEXT_LAST_EVENT_NAME] = self.PanelLastEventName
                pd[TEXT_LAST_EVENT_ACTION] = self.PanelLastEventAction
                pd[TEXT_LAST_EVENT_TIME] = self.PanelLastEventTime
                pd[TEXT_CLIENT_VERSION] = CLIENT_VERSION
            elif partition == 1:
                pd[TEXT_CLIENT_VERSION] = CLIENT_VERSION
            return pd
        return {}

    def process_command(self, command: str):
        """Convert object into dict to maintain backward compatibility."""
        if self.visonicProtocol is not None:
            self.logstate_debug("Client command processing %s", command )
            self.visonicProtocol.process_command(command)
        else:
            self.logstate_warning("Client command processing not defined - is there a panel connection?")

    def _savePanelEventLogFiles(self, available, total):
        # create a new XML file with the results
        try:
            if len(self.config.get(CONF_LOG_XML_FN, "")) > 0:
                try:
                    self.logstate_debug(
                        "Panel Event Log - Starting xml save filename %s   file loader path %s",
                        str(self.config.get(CONF_LOG_XML_FN)),
                        str(self.hass.config.path()),
                    )
                    file_loader = FileSystemLoader(
                        [
                            self.hass.config.path() + "/templates",
                            self.hass.config.path() + "/xml",
                            self.hass.config.path() + "/www",
                            self.hass.config.path(),
                        ],
                        followlinks=True,
                    )
                    env = Environment(loader=file_loader)
                    self.logstate_debug("Panel Event Log - Setting up xml - getting the template")
                    template = env.get_template(TEXT_XML_LOG_FILE_TEMPLATE)
                    output = template.render(
                        entries=self.templatedata,
                        total=total,
                        available=f"{available}",
                    )
                    with open(self.config.get(CONF_LOG_XML_FN), "w") as f:
                        self.logstate_debug("Panel Event Log - Writing xml file")
                        f.write(output.rstrip())
                        self.logstate_debug("Panel Event Log - Closing xml file")
                        f.close()
                except (IOError, AttributeError, TypeError):
                    self.createNotification(
                        AvailableNotifications.EVENTLOG_PROBLEM,
                        "Panel Event Log - Failed to write XML file"
                    )

            if len(self.config.get(CONF_LOG_CSV_FN, "")) > 0:
                try:
                    self.logstate_debug(
                        "Panel Event Log - Starting csv save filename %s",
                        self.config.get(CONF_LOG_CSV_FN),
                    )
                    if self.toBool(self.config.get(CONF_LOG_CSV_TITLE, False)):
                        self.logstate_debug("Panel Event Log - Adding header to string")
                        self.csvdata = "current, total, partition, date, time, zone, event\n" + self.csvdata
                    self.logstate_debug("Panel Event Log - Opening csv file")
                    with open(self.config.get(CONF_LOG_CSV_FN), "w") as f:
                        self.logstate_debug("Panel Event Log - Writing csv file")
                        f.write(self.csvdata.rstrip())
                        self.logstate_debug("Panel Event Log - Closing csv file")
                        f.close()
                except (IOError, AttributeError, TypeError):
                    self.createNotification(
                        AvailableNotifications.EVENTLOG_PROBLEM,
                        "Panel Event Log - Failed to write CSV file"
                    )
        except Exception:
            # Do not cause a full Home Assistant Exception, keep it local here, just create a notification if its enabled
            self.createNotification(
                AvailableNotifications.EVENTLOG_PROBLEM,
                "Panel Event Log - Failed to Create Valid Event Log Files"
            )
#                self._exc_info = sys.exc_info()
        finally:
            # Ensure that these are set back to None to indicate not collecting data so we can start again
            self.csvdata = None
            self.templatedata = None

    def process_panel_event_log(self, entry: AlLogPanelEvent):
        """Process a sequence of panel log events."""
        from . import pmLogEvent_t, pmLogPowerMaxUser_t, pmLogPowerMasterUser_t

        #self._exc_info = None
        #finish_event = asyncio.Event()

        reverse = self.toBool(self.config.get(CONF_LOG_REVERSE, False))
        total = 0
        if entry.total is not None and self.config.get(CONF_LOG_MAX_ENTRIES,1) is not None:
            total = min(entry.total, self.config.get(CONF_LOG_MAX_ENTRIES, 1))
        elif entry.total is not None:
            total = entry.total
        elif self.config.get(CONF_LOG_MAX_ENTRIES, 1) is not None:
            total = self.config.get(CONF_LOG_MAX_ENTRIES, 1)
        current = entry.current  # only used for output and not for logic
        if reverse:
            current = total + 1 - entry.current
        # Fire event visonic_alarm_panel_event_log

        # Initialise values
        if entry.current == 1:
            self.templatedata = []
            self.csvdata = ""
            self.logstate_debug(f"Panel Event Log - Processing")

        eventStr = "Unknown"
        if 0 <= entry.event <= 151:
            if len(pmLogEvent_t[entry.event]) > 0:
                eventStr = pmLogEvent_t[entry.event]
            else:
                self.logstate_debug(f"[process_panel_event_log] Found unknown log event {entry.event}")

        if self.isPowerMaster(): # PowerMaster models
            zoneStr = pmLogPowerMasterUser_t[entry.zone] if entry.zone in pmLogPowerMasterUser_t else "Unknown"
        else:
            zoneStr = pmLogPowerMaxUser_t[entry.zone] if entry.zone in pmLogPowerMaxUser_t else "Unknown"

        if (
            self.toBool(self.config.get(CONF_LOG_EVENT, False))
            and entry.current <= total
        ):  
            datadictionary = {"current": current,
                              "total": total,
                              "date": entry.dateandtime,
                              #"time": entry.time,
                              "partition": entry.partition,
                              "zone": zoneStr,
                              "event": eventStr,
            }
            self._fireHAEvent(event_id = PanelCondition.PANEL_LOG_ENTRY, datadictionary = datadictionary)
            #self.logstate_debug(f"    Event Log {entry.current} of {entry.total}   event {datadictionary}")
        
        if self.csvdata is not None and self.templatedata is not None:
            # Accumulating CSV Data
            csvtemp = (f"{current}, {total}, {entry.partition}, {entry.dateandtime}, {zoneStr}, {eventStr}\n")
            if reverse:
                self.csvdata = csvtemp + self.csvdata
            else:
                self.csvdata = self.csvdata + csvtemp

            # Accumulating Data for the XML generation
            dd = {
                "partition": f"{entry.partition}",
                "current": f"{current}",
                "date": f"{entry.dateandtime}",
                #"time": f"{entry.time}",
                "zone": f"{zoneStr}",
                "event": f"{eventStr}",
            }

            self.templatedata.append(dd)

            if entry.current == total:
                self.logstate_debug(
                    "Panel Event Log - Received last entry  reverse=%s  xmlfilenamelen=%s csvfilenamelen=%s",
                    str(reverse),
                    len(self.config.get(CONF_LOG_XML_FN, "")),
                    len(self.config.get(CONF_LOG_CSV_FN, "")),
                )

                if reverse:
                    self.templatedata.reverse()

                x = threading.Thread(target=self._savePanelEventLogFiles, args=(entry.total, total), name=f"VisonicSaveEventLog{self.getPanelID()}",)
                x.start()
                x.join()

                if self.toBool(self.config.get(CONF_LOG_DONE, False)):
                    self.logstate_debug("Panel Event Log - Firing Completion Event")
                    self._fireHAEvent(event_id = PanelCondition.PANEL_LOG_COMPLETE, datadictionary = {"total": total, "available": entry.total})
                self.logstate_debug("Panel Event Log - Complete")

    # This is not called from anywhere, use it for debug purposes and/or to clear all entities from HA
    def printAllEntities(self, delete_as_well : bool = False):
        entity_reg = er.async_get(self.hass)
        entity_entries = er.async_entries_for_config_entry(entity_reg, self.getEntryID())
        for damn in entity_entries:
            _LOGGER.debug(f"         entity {damn}")
            if delete_as_well:
                entity_reg.async_remove(damn.entity_id)

        # clear out all devices from the registry to recreate them, if the user has added/removed devices then this ensures that its a clean start
        device_reg = dr.async_get(self.hass)
        device_entries = dr.async_entries_for_config_entry(device_reg, self.getEntryID())
        for damn in device_entries:
            _LOGGER.debug(f"         device {damn}")
            if delete_as_well:
                device_reg.async_remove_device(damn.id)

        # The platforms do not initially exist, but after a reload they already exist
        platforms = ep.async_get_platforms(self.hass, DOMAIN)
        _LOGGER.debug(f"         platforms {platforms}")
   
    async def _setupVisonicEntity(self, sensor_domain, param = None):
        """Setup a platform and add an entity using the dispatcher."""
        if param is None:
            async_dispatcher_send( self.hass, f"{DOMAIN}_{self.getEntryID()}_add_{sensor_domain}" )
        else:
            async_dispatcher_send( self.hass, f"{DOMAIN}_{self.getEntryID()}_add_{sensor_domain}", param )

    def onNewSwitch(self, create : bool, dev: AlSwitchDevice): 
        self.hass.loop.create_task(self.async_onNewSwitch(create, dev))

    async def async_onNewSwitch(self, create : bool, dev: AlSwitchDevice): 
        """Process a new x10."""
        # Check to ensure variables are set correctly
        if self.hass is None:
            self.logstate_warning("Attempt to add X10 switch when hass is undefined")
            return
        #if not self._createdAlarmPanel:
        #    await self._async_setupAlarmPanel()
        if dev is None:
            self.logstate_warning("Attempt to add X10 switch when sensor is undefined")
            return
        if dev.getDeviceID() is None:
            self.logstate_warning("Switch callback but Switch Device ID is None")
            return
        if dev.isEnabled() and dev.getDeviceID() not in self.exclude_x10_list:
            dev.onChange(self.onSwitchChange)
            async with self.visonic_switch_setup_lock:
                if create and dev not in self.x10_list:
                    self.logstate_debug(f"X10 Switch list {dev.getDeviceID()=}")
                    self.x10_list.append(dev)
                    await self._setupVisonicEntity(SWITCH_DOMAIN, dev)
                elif not create and dev in self.x10_list:
                    # delete
                    self.x10_list.remove(dev)
                    self.logstate_debug(f"X10 Device {dev.getDeviceID()} to be deleted but not yet implemented")
                else:
                    self.logstate_debug(f"X10 Device {dev.getDeviceID()} already in the list")

    def _setupAlarmPanel(self):
        self.hass.loop.create_task(self._async_setupAlarmPanel())

    async def _async_setupAlarmPanel(self):
        # This sets up the Alarm Panel, or the Sensor to represent a panel state
        #   It is called from multiple places, the first one wins
        async with self.visonic_alarm_setup_lock:
            if not self._createdAlarmPanel:
                self._createdAlarmPanel = True
                if self.DisableAllCommands:
                    self.logstate_debug("Creating Sensor for Alarm indications")
                    await self._setupVisonicEntity(SENSOR_DOMAIN)
                else:
                    self.logstate_debug("Creating Alarm Panel Entity")
                    await self._setupVisonicEntity(ALARM_PANEL_DOMAIN)
                    await self._setupVisonicEntity(SIREN_DOMAIN)

    def onNewSensor(self, create : bool, sensor: AlSensorDevice):
        self.hass.loop.create_task(self.async_onNewSensor(create, sensor))

    async def async_onNewSensor(self, create : bool, sensor: AlSensorDevice):
        """Process a new sensor."""
        # Check to ensure variables are set correctly
        if self.hass is None:
            self.logstate_warning("Visonic attempt to add sensor when hass is undefined")
            return
        #if not self._createdAlarmPanel:
        #    await self._async_setupAlarmPanel()
        if sensor is None:
            self.logstate_warning("Visonic attempt to add sensor when sensor is undefined")
            return
        if sensor.getDeviceID() is None:
            self.logstate_warning("Sensor callback but Sensor Device ID is None")
            return
        if sensor.getDeviceID() not in self.exclude_sensor_list:
            async with self.visonic_sensor_setup_lock:
                sensor.onChange(self.onSensorChange)
                if create and sensor not in self.sensor_list:
                    self.logstate_debug("Adding Sensor %s", sensor)
                    self.sensor_list.append(sensor)
                    await self._setupVisonicEntity(BINARY_SENSOR_DOMAIN, sensor)
                    # If not Standard Mode (i.e. Powerlink) and the user has allowed sensors to be bypassed, then create select entities
                    if not self.ForceStandardMode and self.toBool(self.config.get(CONF_ENABLE_SENSOR_BYPASS, False)):
                        # The connection to the panel allows interaction with the sensor, including the arming/bypass of the sensors
                        await self._setupVisonicEntity(SELECT_DOMAIN, sensor)
                elif not create and sensor in self.sensor_list:
                    # delete
                    self.sensor_list.remove(sensor)
                    self.logstate_debug(f"Sensor {sensor.getDeviceID()} to be deleted but not yet implemented, also need to delete the select entity if it was created")
                else:
                    self.logstate_debug(f"Sensor {sensor.getDeviceID()} already in the lists")
            if not self.DisableAllCommands and sensor.getDeviceID() not in self.image_list and sensor.getSensorType() == AlSensorType.CAMERA:
                await self.create_image_entity(sensor)
        else:
            self.logstate_debug(f"Sensor {sensor.getDeviceID()} in exclusion list")

    async def create_image_entity(self, sensor):
        # The issue is that PIR Sensors could be detected and created without knowing that it's a Camera PIR Sensor until too late
        # We might not know the sensor type when we first startup, could be standard mode or whatever
        self.logstate_debug("Adding Sensor Image %s", sensor)
        async with self.visonic_sensor_setup_lock:
            if sensor.getDeviceID() not in self.image_list and sensor.getSensorType() == AlSensorType.CAMERA:
                self.image_list.append(sensor.getDeviceID())
                # The connection to the panel allows interaction with the sensor, including asking to get the image from a camera
                await self._setupVisonicEntity(IMAGE_DOMAIN, sensor)

    def onChange(self, callback : Callable, partition : int | None = None, panel_entity_name : str | None = None):
        if panel_entity_name is not None:
            if partition is None:
                partition = 1
            self.panel_entity_name[partition] = panel_entity_name
        self.onChangeHandler.append(callback)

    def _fireHAEvent(self, event_id: AlCondition | PanelCondition, datadictionary: dict):
        # Check to ensure variables are set correctly
        if self.hass is None:
            self.logstate_warning("Attempt to generate HA event when hass is undefined")
            return

        #if not self._createdAlarmPanel:
        #    self._setupAlarmPanel()

        if event_id is None:
            self.logstate_warning("Attempt to generate HA event when Event Type is undefined")
            return

        # Call all the registered client change handlers
        for cb in self.onChangeHandler:
            cb()

        if event_id in AlarmPanelEventActionList: # Event must be in the list to send out
            name = AlarmPanelEventActionList[event_id].name
            a = {}
            a[PANEL_ATTRIBUTE_NAME] = self.getPanelID()
            
            if len(AlarmPanelEventActionList[event_id].action) > 0:       # name == ALARM_PANEL_CHANGE_EVENT or name == ALARM_COMMAND_EVENT:
                a["action"] = AlarmPanelEventActionList[event_id].action

            if datadictionary is not None:
                piu = self.getPartitionsInUse()
                self.logstate_debug(f"Client [_fireHAEvent]  Partitions in use {piu}")
                
                if piu is None and PE_PARTITION in datadictionary:
                    # if no used partitions and PE_PARTITION in datadictionary then remove it
                    del datadictionary[PE_PARTITION]

                if piu is not None and len(piu) > 0 and PE_PARTITION not in datadictionary:
                    # if partitions in use and PE_PARTITION is not in the datadictionary then add the first partition 
                    datadictionary[PE_PARTITION] = list(piu)[0]
                
                if PE_PARTITION in datadictionary:
                    if datadictionary[PE_PARTITION] in self.panel_entity_name:
                        a["panel_id"] = Platform.ALARM_CONTROL_PANEL + "." + slugify(self.panel_entity_name[datadictionary[PE_PARTITION]])   # Platform.ALARM_CONTROL_PANEL + "." + slugify(self.getAlarmPanelUniqueIdent())
                    else:
                        a["panel_id"] = Platform.ALARM_CONTROL_PANEL + "." + slugify(self.getAlarmPanelUniqueIdent())
                    self.logstate_debug(f"Client [_fireHAEvent]      Setting entity id in the event {a['panel_id']}")
                else:
                    a["panel_id"] = Platform.ALARM_CONTROL_PANEL + "." + slugify(self.getAlarmPanelUniqueIdent())

                b = datadictionary.copy()
                dd = {**a, **b}
                self.logstate_debug(f"Client [_fireHAEvent]  Sending HA Event {name}  {dd}")
                self.hass.bus.fire( name, dd )
            else:
                self.logstate_debug(f"Client [_fireHAEvent]  Sending HA Event {name}  {a}")
                self.hass.bus.fire( name, a )

    def onSensorChange(self, sensor : AlSensorDevice, c : AlSensorCondition):
        self.logstate_debug(f"onSensorChange {c.name} {sensor}")
        # Check to make sure we have an image entity created for this sensor
        if not self.DisableAllCommands and sensor.getDeviceID() not in self.image_list and sensor.getSensorType() == AlSensorType.CAMERA:
            self.hass.loop.create_task(self.create_image_entity(sensor))
    
    def onSwitchChange(self, switch : AlSwitchDevice):
        #self.logstate_debug(f"onSwitchChange {switch}")
        pass

    def rationalise_ha_devices(self):

        def buildEntitySet() -> set:
            entname = slugify(self.getAlarmPanelUniqueIdent())
            retval = set()
            retval.add(entname)
            for sensor in self.sensor_list:
                entname = self.getMyString() + sensor.createFriendlyName().lower()
                retval.add(entname)
            for switch in self.x10_list:
                entname = self.getMyString() + switch.createFriendlyName().lower()
                retval.add(entname)
            return retval
 
        def filterEntitybyPanelIdent( entities : list, p : int ) -> list:
            retval = []
            reg = f"{self.getMyString()}[xz]\\d\\d"
            for e in entities:
                if re.search(reg, e.unique_id):
                    retval.append(e)
            return retval                
            
        def filterDevicebyPanelIdent( devices : list, p : int ) -> list:
            retval = []
            reg = f"{self.getMyString()}[xz]\\d\\d"
            for d in devices:
                for i in d.identifiers:
                    if re.search(reg, i[1] ):
                        retval.append(d)
            return retval                

        # Get the set of sensors and switches created by this panel (a set contains unique items, no duplication)
        my_entities = buildEntitySet()
        self.logstate_debug(f"     Set of Current Devices From Panel {my_entities}")

        # Get entity and device registry
        entity_reg = er.async_get(self.hass)
        device_reg = dr.async_get(self.hass)

        # Get a list of Home Assistant Visonic devices asociated with this config
        device_entries = dr.async_entries_for_config_entry(device_reg, self.getEntryID())

        #for device in device_entries:
        #    self.logstate_debug(f"        HA Device BEFORE {device}")

        # Filter the devices for this panel ID (so we dont remove devices for other panels that may still be valid)
        #    As we get the list of devices for this config we shouldn't need to do this but just in case
        device_entries = filterDevicebyPanelIdent(device_entries, self.getPanelID())
        
        # Clear out all devices not created by this panel
        for device in device_entries:
            self.logstate_debug(f"        HA Device        {device}")
            # Get the list of Entities associated with this Device
            entity_entries = er.async_entries_for_device(entity_reg, device.id, True)
            #for entity in entity_entries:
            #    self.logstate_debug(f"             has entity {entity}")
            for ident in device.identifiers:
                # This is important, it has to match the identifiers return in each entity device_info
                #    as "identifiers": {(DOMAIN, slugify(self._name))},
                if ident[1] not in my_entities:     
                    self.logstate_debug(f"               Deleting this device from HA")
                    # Delete the entities in this device first
                    for entity in entity_entries:
                        self.logstate_debug(f"                     Deleting this entity from HA {entity}")
                        entity_reg.async_remove(entity.entity_id)
                    # Delete this device
                    device_reg.async_remove_device(device.id)

        # Get the entities that are associated with this config
        entity_entries = er.async_entries_for_config_entry(entity_reg, self.getEntryID())

        #for entity in entity_entries:
        #    self.logstate_debug(f"        HA Entity BEFORE {entity}")

        # Filter the entities for this panel ID (so we dont remove entities for other panels that may still be valid)
        entity_entries = filterEntitybyPanelIdent(entity_entries, self.getPanelID())

        # Clear out all entities not created by this panel
        for entity in entity_entries:
            self.logstate_debug(f"        HA Entity        {entity}")
            if entity.unique_id not in my_entities:
                self.logstate_debug(f"               Deleting this entity from HA")
                entity_reg.async_remove(entity.entity_id)

        # The platforms do not initially exist, but after a reload they already exist
        #platforms = ep.async_get_platforms(self.hass, DOMAIN)
        #self.logstate_debug(f"         platforms {platforms}")

    def sendEvent(self, event_id: AlCondition | PanelCondition, data : dict):

        if event_id == AlCondition.PANEL_UPDATE and data is not None and len(data) == 3:
            self.PanelLastEventName = data[PE_NAME]
            self.PanelLastEventAction = data[PE_EVENT]
            self.PanelLastEventTime = data[PE_TIME]
            
        self._fireHAEvent(event_id = event_id, datadictionary = data if data is not None else {} )

        if event_id == AlCondition.DOWNLOAD_SUCCESS:        # download success        
            # Update the friendly name of the control flow
            pm = self.getPanelModel()
            s = "Panel " + str(self.getPanelID()) + " (" + ("Unknown" if pm is None else pm) + ")"
            # update the title
            self.hass.config_entries.async_update_entry(self.entry, title=s)

        if event_id == AlCondition.STARTUP_SUCCESS:        # Startup Success
            if not self.rationalised_ha_devices:
                if self.getPanelMode() in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD_PLUS]:
                    self.rationalised_ha_devices = True
                    self.rationalise_ha_devices()
            
            if (p := self.getPartitionsInUse()) is not None:
                self.logstate_debug(f"   Startup Complete, number of partitions in panel = {len(p)}   they are {p}")
            else:
                self.logstate_debug(f"   Startup Complete, no partitions in panel")
            if not self._createdAlarmPanel:
                self._setupAlarmPanel()
            
        #if event_id == AlCondition.PANEL_UPDATE and self.getPanelMode() == AlPanelMode.POWERLINK:
        #    # Powerlink Mode
        #    self.printAllEntities()

        if event_id == AlCondition.PANEL_UPDATE and self.visonicProtocol is not None and self.visonicProtocol.isSirenActive()[0]:
            self.createNotification(AvailableNotifications.SIREN, "Siren is Sounding, Alarm has been Activated" )
        elif event_id == AlCondition.PANEL_RESET:
            self.createNotification(AvailableNotifications.RESET, "The Panel has been Reset" )
        elif event_id == AlCondition.PIN_REJECTED:
            self.createNotification(AvailableNotifications.INVALID_PIN, "The Pin Code has been Rejected By the Panel" )
        elif event_id == AlCondition.DOWNLOAD_TIMEOUT:
            self.createNotification(AvailableNotifications.PANEL_OPERATION, "Panel Data download timeout, Standard Mode Selected" )
        elif event_id == AlCondition.WATCHDOG_TIMEOUT_GIVINGUP:
            if self.getPanelMode() == AlPanelMode.POWERLINK or self.getPanelMode() == AlPanelMode.POWERLINK_BRIDGED:
                self.createNotification(AvailableNotifications.CONNECTION_PROBLEM, "Communication Timeout - Watchdog Timeout too many times within 24 hours. Dropping out of Powerlink" )
            else:
                self.createNotification(AvailableNotifications.CONNECTION_PROBLEM, "Communication Timeout - Watchdog Timeout too many times within 24 hours." )
        elif event_id == AlCondition.WATCHDOG_TIMEOUT_RETRYING:
            self.createNotification(AvailableNotifications.PANEL_OPERATION, "Communication Timeout - Watchdog Timeout, restoring panel connection" )
        elif event_id == AlCondition.NO_DATA_FROM_PANEL:
            self.createNotification(AvailableNotifications.CONNECTION_PROBLEM, "Connection Problem - No data from the panel" )
        elif event_id == AlCondition.COMMAND_REJECTED:
            self.createNotification(AvailableNotifications.ALWAYS, "Operation Rejected By Panel" )

    # This can be called from this module but it is also the callback handler for the connection
    def onPanelChangeHandler(self, event_id: AlCondition | PanelCondition, data : dict):
        """Generate HA Bus Event and Send Notification to Frontend."""
        
        if event_id == AlCondition.PANEL_UPDATE:
            
            if data is not None and PE_NAME in data and data[PE_NAME] >= 0:
                if len(data) == 4 and PE_PARTITION in data:
                    # The panel has partitions
                    partition = data[PE_PARTITION]
                    
                    self.logstate_debug(f"[onPanelChangeHandler] {type(self.myPanelEventCoordinator)}   set to {self.myPanelEventCoordinator}   {partition=}")
                    
                    if self.myPanelEventCoordinator is None:
                        # initialise as a dict, the partition is the key
                        self.myPanelEventCoordinator = {}

                    if not isinstance(self.myPanelEventCoordinator, dict):
                        # if it's the incorrect type then empty it. initialise as a dict, the partition is the key
                        self.myPanelEventCoordinator = {}

                    if partition not in self.myPanelEventCoordinator:
                        self.myPanelEventCoordinator[partition] = PanelEventCoordinator(loop = self.hass.loop, callbackSender = self.sendEvent, logstate_debug = self.logstate_debug)
                    self.myPanelEventCoordinator[partition].setIsPowerMaster(self.isPowerMaster())
                    self.myPanelEventCoordinator[partition].addEvent(data)
            
                elif len(data) == 3 and not isinstance(self.myPanelEventCoordinator, dict):
                    # The panel does not have partitions
                    self.logstate_debug(f"[onPanelChangeHandler] {type(self.myPanelEventCoordinator)}   set to {self.myPanelEventCoordinator}   no partitions")
                    if self.myPanelEventCoordinator is None:
                        self.myPanelEventCoordinator = PanelEventCoordinator(loop = self.hass.loop, callbackSender = self.sendEvent, logstate_debug = self.logstate_debug)
                    self.myPanelEventCoordinator.setIsPowerMaster(self.isPowerMaster())
                    self.myPanelEventCoordinator.addEvent(data)

                elif len(data) == 3 and isinstance(self.myPanelEventCoordinator, dict):
                    self.logstate_debug(f"[onPanelChangeHandler] {type(self.myPanelEventCoordinator)}   set to {self.myPanelEventCoordinator}   nothing done as message length indicates a single partition but we know there's multiple")
                    
                else:
                    self.logstate_warning(f"[onPanelChangeHandler] Cannot translate panel event log data {data}")
            else:
                self.logstate_warning(f"[onPanelChangeHandler] Cannot translate panel event log data {data}")
        else:
            self.sendEvent(event_id, data)

    def toBool(self, val: Any) -> bool:
        """Convert value to boolean."""
        if type(val) == bool:
            return val
        elif type(val) == int:
            return val != 0
        elif type(val) == str:
            v = val.lower()
            return not (v == "no" or v == "false" or v == "0")
        self.logstate_warning(f"Unable to decode boolean value {val}    type is {type(val)}")
        return False

    def getSirenTriggerList(self) -> []:
        return self.config.get(CONF_SIREN_SOUNDING, ["Intruder"])

    def getConfigData(self) -> PanelConfig:
        """Create a dictionary full of the configuration data."""

        v = self.config.get(CONF_EMULATION_MODE, available_emulation_modes[0])        
        self.ForceStandardMode = v == available_emulation_modes[1]
        self.DisableAllCommands = v == available_emulation_modes[2]

        if self.DisableAllCommands:
            self.ForceStandardMode = True
        # By the time we get here there are 3 combinations of DisableAllCommands and ForceStandardMode
        #     Both are False --> Try to get to Powerlink 
        #     ForceStandardMode is True --> Force Standard Mode, the panel can still be armed and disarmed
        #     ForceStandardMode and DisableAllCommands are True --> The integration interacts with the panel but commands such as arm/disarm/log/bypass are not allowed
        # The if statement above ensure these are the only supported combinations.

        self.logstate_debug(f"[getConfigData] Emulation Mode {v} so setting ForceStandard to {self.ForceStandardMode}, DisableAllCommands to {self.DisableAllCommands}")

        return {
            AlConfiguration.DownloadCode: self.config.get(CONF_DOWNLOAD_CODE, ""),
            AlConfiguration.ForceStandard: self.ForceStandardMode,
            AlConfiguration.DisableAllCommands: self.DisableAllCommands
            #AlConfiguration.SirenTriggerList: self.config.get(CONF_SIREN_SOUNDING, ["Intruder"])
        }

    async def _checkUserPermission(self, call, perm, entity):
        user = await self.hass.auth.async_get_user(call.context.user_id)
        #self.logstate_debug(f"User check {call.context.user_id=} user={user=}")

        if user is None:
            raise UnknownUser(
                context=call.context,
                entity_id=entity,
                permission=perm,
            )

        if not user.permissions.check_entity(entity, perm):
            raise Unauthorized(
                context=call.context,
                entity_id=entity,
                permission=perm,
            )
    
    def updateConfig(self, conf: dict = None):
        """Update the dictionary full of configuration data."""
        if conf is not None:
            self.config.update(conf)
        cd = self.getConfigData()
        if self.visonicProtocol is not None:
            self.visonicProtocol.updateSettings(cd)
        self._setupSensorDelays()
        self.delayBetweenAttempts = float(self.config.get(CONF_RETRY_CONNECTION_DELAY, 1.0))   # seconds
        self.totalAttempts = int(self.config.get(CONF_RETRY_CONNECTION_COUNT, 1))

    def onProblem(self, termination : AlTerminationType):
        """Problem Callback for connection disruption to the panel."""

        actionmap = {
            AlTerminationType.EXTERNAL_TERMINATION               : PanelCondition.CONNECTION,
            AlTerminationType.SAME_PACKET_ERROR                  : PanelCondition.CONNECTION,
            AlTerminationType.CRC_ERROR                          : PanelCondition.CONNECTION,
            AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED : AlCondition.NO_DATA_FROM_PANEL,
            AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED    : AlCondition.NO_DATA_FROM_PANEL,
            AlTerminationType.NO_POWERLINK_FOR_PERIOD            : PanelCondition.CONNECTION
        }

        statemap = {
            AlTerminationType.EXTERNAL_TERMINATION               : "disconnected",
            AlTerminationType.SAME_PACKET_ERROR                  : "disconnected",
            AlTerminationType.CRC_ERROR                          : "disconnected",
            AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED : "neverconnected",
            AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED    : "disconnected",
            AlTerminationType.NO_POWERLINK_FOR_PERIOD            : "unknown"
        }

        reasonmap = {
            AlTerminationType.EXTERNAL_TERMINATION               : "termination",
            AlTerminationType.SAME_PACKET_ERROR                  : "samepacketerror",
            AlTerminationType.CRC_ERROR                          : "crcerror",
            AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED : None,
            AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED    : None,
            AlTerminationType.NO_POWERLINK_FOR_PERIOD            : "powerlinkperiodexpired"
        }

        action = actionmap[termination]
        state  = statemap[termination]
        reason = reasonmap[termination]

        # General update trigger
        #    0 is a disconnect, state="disconnected" means initial disconnection and (hopefully) reconnect from an exception (probably comms related)
        if reason is not None:
            self.logstate_debug(f"Visonic has responded to a disconnection, action={action}, state={state} reason={reason}")
            self._fireHAEvent(event_id = action, datadictionary = {"state": state, "reason": reason})
        else:
            self.logstate_debug(f"Visonic has responded to a disconnection, action={action}, state={state}")
            self._fireHAEvent(event_id = action, datadictionary = {"state": state})

        self.onPanelChangeHandler(event_id = AlCondition.PUSH_CHANGE, data = {} )  # push through a panel update to the HA Frontend of any changes
        
        if self.totalAttempts == 0:                                                                   # If the user says 0 restart attempts then do not restart at all
            self.logstate_debug(f"    User config explicitly prevents any reconnection attempts, stopping the connection")
            self.hass.loop.create_task(self.async_panel_stop())                                       # stop, do not restart
        else:                                                               # Are we already in the middle of a restart or reconnection
            self.panel_disconnection_counter = self.panel_disconnection_counter + 1
            self.hass.loop.create_task(self.async_reconnect_and_restart(force_reconnect = False, allow_restart = True))    # Try a reconnect first and if it fails then do the restart sequence (X attempts every Y seconds)

    # pmGetPin: Convert a PIN given as 4 digit string in the PIN PDU format as used in messages to powermax
    def pmGetPin(self, code: str, forcedKeypad: bool, partition : int):
        """Get code code."""
        #self.logstate_debug("Getting Pin Start")
        if code is None or code == "" or len(code) != 4:
            psc = self.getPanelStatus(partition)
            panelmode = self.getPanelMode()
            #self.logstate_debug("Getting Pin")
            
            # Avoid the panel codes that we're not interested in, if these are set then we have no business doing any of the functions
            #    After this we can simply use DISARMED and not DISARMED for all the armed states
            if psc == AlPanelStatus.UNKNOWN or psc == AlPanelStatus.USER_TEST or psc == AlPanelStatus.DOWNLOADING:
                return False, None   # Return invalid as panel not in correct state to do anything
            
            if panelmode == AlPanelMode.STANDARD:
                if psc == AlPanelStatus.DISARMED:
                    if self.isArmWithoutCode():  # 
                        #self.logstate_debug("Here B")
                        return True, "0000"        # If the panel can arm without a usercode then we can use 0000 as the usercode
                    return False, None             # use keypad so invalidate the return, there should be a valid 4 code code
                else:
                    return False, None             # use keypad so invalidate the return, there should be a valid 4 code code
            elif panelmode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD_PLUS]:  # 
                if psc == AlPanelStatus.DISARMED and self.isArmWithoutCode() and forcedKeypad:
                    return True, None    
                if forcedKeypad:
                    return False, None   # use keypad so invalidate the return, there should be a valid 4 code code
                return True, None    # Usercode
            elif panelmode == AlPanelMode.DOWNLOAD or panelmode == AlPanelMode.STARTING:  # No need to output to log file when starting or downloading EPROM as this is normal operation
                return False, None # Return invalid as panel downloading EPROM
            else:
                # If the panel mode is UNKNOWN, PROBLEM.
                self.logstate_warning(f"Warning: Valid 4 digit PIN not found, panelmode is {panelmode}")
                return False, None # Return invalid as panel not in correct state to do anything
        return True, code

    # pmGetPinSimple: Convert a PIN given as 4 digit string in the PIN PDU format as used in messages to powermax
    #   This is used from the bypass command and the get event log command
    def pmGetPinSimple(self, code: str):
        """Get code code."""
        #self.logstate_debug("Getting Pin Start")
        if code is None or code == "" or len(code) != 4:
            panelmode = self.getPanelMode()
            if panelmode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD_PLUS]:
                # Powerlink or StdPlus and so we downloaded the code codes
                return True, None
            else:
                self.logstate_warning(f"Warning: [pmGetPinSimple] Valid 4 digit PIN not found, panelmode is {panelmode}")
                return False, None
        return True, code

    def _populateSensorDictionary(self) -> dict:
        datadict = {}
        #["ready"] = self.isPanelReady(partition)
        datadict["open"] = []
        datadict["bypass"] = []
        datadict["tamper"] = []
        datadict["zonetamper"] = []
        
        for s in self.sensor_list:
            entname = Platform.BINARY_SENSOR + "." + self.getMyString() + s.createFriendlyName().lower()
            if s.isOpen():
                datadict["open"].append(entname)
            if s.isBypass():
                datadict["bypass"].append(entname)
            if s.isTamper() is not None:
                if s.isTamper():
                    datadict["tamper"].append(entname)
            if s.isZoneTamper() is not None:
                if s.isZoneTamper():
                    datadict["zonetamper"].append(entname)
        return datadict

    # This should only be called from within this module.
    #     This is Data Set C
    def _generateBusEventReason(self, event_id: PanelCondition, reason: AlCommandStatus, command: str, message: str):
        """Generate an HA Bus Event with a Reason Code."""
        datadict = self._populateSensorDictionary()
        #if self.visonicProtocol is not None:
        datadict["command"] = command.title()           
        datadict["reason"] = int(reason)
        datadict["reason_str"] = reason.name.title()
        datadict["message"] = message + " " + messageDictReason[reason]

        self.onPanelChangeHandler(event_id = event_id, data = datadict)

        #self.logstate_debug("[" + message + "] " + messageDictReason[reason])

        if reason != AlCommandStatus.SUCCESS:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, message + " " + messageDictReason[reason])

#    def setX10(self, ident: int, state: AlX10Command):
#        """Send an X10 command to the panel."""
#        if not self.DisableAllCommands:
#            # ident in range 0 to 15, state can be one of "off", "on", "dimmer", "brighten"
#            if self.visonicProtocol is not None:
#                retval = self.visonicProtocol.setX10(ident, state)
#                self._generateBusEventReason(PanelCondition.CHECK_X10_COMMAND, retval, "X10", "Send X10 Command")
#        else:
#            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")


    def isPanelConnected(self) -> bool:
        """Are we connected to the Alarm Panel."""
        # If we are starting up then assume we need a valid code
        #  This is the opposite of code_format as we want to prevent operation during startup
        # Are we just starting up or has there been a problem  and we are disconnected?
        armcode = self.getPanelStatus()
        panelmode = self.getPanelMode()
        if armcode is None or armcode == AlPanelStatus.UNKNOWN or panelmode == AlPanelMode.UNKNOWN:
            # self.logstate_debug("isPanelConnected: code format none as armcode is none (panel starting up or is there a problem?)")
            return False
        return True

    def isCodeRequired(self) -> bool:
        """Determine if a user code is required given the panel mode and user settings."""
        isValidPL, code = self.pmGetPin(code = None, forcedKeypad = self.isForceKeypad(), partition = 1)
        return not isValidPL;


    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================
    # ======== Functions below this are the service calls and the Frontend controls from Home Assistant =====
    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================

    async def is_panel_status_set_to(self, call, st : AlPanelStatus, message : str, an : AvailableNotifications):
        armcode = self.getPanelStatus()
        if armcode is None or armcode == AlPanelStatus.UNKNOWN:
            self.createNotification(AvailableNotifications.CONNECTION_PROBLEM, f"Attempt to {message}, check panel connection")
        elif armcode == st:
            return True
        self.createNotification(an, f"Visonic Alarm Panel: Attempt to {message} for panel {self.getPanelID()}, panel needs to be in the {st} state")
        return False

    async def check_the_basics(self, call, message : str) -> bool:
        """Common Service call."""
        if not self.DisableAllCommands:
            # Commands are enabled
            self.logstate_debug(f"Received {message} request")
            if self.isPanelConnected():
                # The panel is connected and is in a known state
                if call.context.user_id:
                    #self.logstate_debug(f"Checking user information for permissions: {call.context.user_id}")
                    # Check security permissions (that this user has access to the alarm panel entity)
                    await self._checkUserPermission(call, POLICY_READ, Platform.ALARM_CONTROL_PANEL + "." + slugify(self.getAlarmPanelUniqueIdent()))
                self.logstate_debug(f"Received {message} request - user approved")
                if isinstance(call.data, dict):
                    # call data is a dictionary
                    return True
                else:
                    self.logstate_warning(f"Not making {message} request {type(call.data)} {call.data}")
                    self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")
            else:
                self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, not sent to panel")
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")
        return False

    def decode_code_from_call_data(self, call, message : str, cond : PanelCondition) -> (bool , str):
        code = None
        if ATTR_CODE in call.data:
            code = call.data[ATTR_CODE]
            # If the code is defined then it must be a 4 digit string
            if len(code) > 0 and not re.search(PIN_REGEX, code):
                code = "0000"
        pcode = self.decode_code_from_dict_or_str(code)
        isValidPL, code = self.pmGetPinSimple(code = pcode)
        if isValidPL:
            return True, code
        self._generateBusEventReason(cond, AlCommandStatus.FAIL_INVALID_CODE, message, f"{message} Request")
        return False, ""

    def decode_code_from_dict_or_str(self, data : str | dict | None) -> str:
        """Decode the alarm code."""
        if data is not None:
            if type(data) == str:
                if len(data) == 4:
                    return data
            elif type(data) is dict:
                if "code" in data:
                    if len(data["code"]) == 4:
                        return data["code"]
        return ""

    def dump_dict(self, d):
        for key in d:
            self.logstate_debug(f"  {key} = {d[key]}")

    async def decode_entity(self, call, ent_type : str, message : str, an : AvailableNotifications) -> (int | None , str | None):
        # Get the Entity from the call
        if ATTR_ENTITY_ID in call.data:
            eid = str(call.data[ATTR_ENTITY_ID])
            if not eid.startswith(ent_type + "."):
                eid = ent_type + "." + eid
            if valid_entity_id(eid):
                # Its a valid entity
                if call.context.user_id:
                    #self.logstate_debug(f"Checking user information for permissions: {call.context.user_id}")
                    # Check security permissions (that this user has access to the alarm panel entity)
                    await self._checkUserPermission(call, POLICY_CONTROL, call.data[ATTR_ENTITY_ID])
                mybpstate = self.hass.states.get(eid)
                if mybpstate is not None:
                    # Get the 2 attributes of the entity: panel number and device number
                    if DEVICE_ATTRIBUTE_NAME in mybpstate.attributes and PANEL_ATTRIBUTE_NAME in mybpstate.attributes:
                        devid = mybpstate.attributes[DEVICE_ATTRIBUTE_NAME]
                        panel = mybpstate.attributes[PANEL_ATTRIBUTE_NAME]
                        if panel == self.getPanelID(): # This should be done in __init__ but check again to make sure as its a critical operation
                            return devid, eid
                        else:
                            self.createNotification(an, f"Attempt to {message} for panel {self.getPanelID()}, device {devid} but entity {eid} not connected to this panel")
                    else:
                        self.createNotification(an, f"Attempt to {message} for panel {self.getPanelID()}, incorrect entity {eid}")
                else:
                    self.createNotification(an, f"Attempt to {message} for panel {self.getPanelID()}, unknown device state for entity {eid}")
            else:
                self.createNotification(an, f"Attempt to {message} for panel {self.getPanelID()}, invalid entity {eid}")
        else:
            self.createNotification(an, f"Attempt to {message} for panel {self.getPanelID()} but entity not defined")
        return None, None

    async def async_service_panel_eventlog(self, call):
        """Service call to retrieve the event log from the panel. This currently just gets dumped in the HA log file."""
        if self.visonicProtocol is not None:
            if await self.check_the_basics(call, "event log"):
                isValidPL, code = self.decode_code_from_call_data(call, "EventLog", PanelCondition.CHECK_EVENT_LOG_COMMAND)
                if isValidPL:
                    self.logstate_debug("Sending event log request to panel")
                    retval = self.visonicProtocol.getEventLog(code)
                    self._generateBusEventReason(PanelCondition.CHECK_EVENT_LOG_COMMAND, retval, "EventLog", "Event Log Request")
            # The check_the_basics and decode_code_from_call_data functions send a failure notification so no need to here

    def getJPG(self, ident: int, count : int):
        """Send a request to get the jpg images from a camera """
        if not self.DisableAllCommands:
            # ident in range 1 to 64
            if self.visonicProtocol is not None:
                retval = self.visonicProtocol.getJPG(ident, count)
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")

    async def service_sensor_image(self, call):
        """Service call to bypass a sensor in the panel."""
        if await self.check_the_basics(call, "sensor image"):
            devid, eid = await self.decode_entity(call, Platform.IMAGE, "retrieve sensor image", AvailableNotifications.IMAGE_PROBLEM)
            if devid is not None and devid >= 1 and devid <= 64:
                self.getJPG(devid, 11)  # The 11 is the number of images to retrieve but it doesnt work
            elif eid is not None:
                self.createNotification(AvailableNotifications.IMAGE_PROBLEM, f"Attempt to retrieve sensor image for panel {self.getPanelID()}, entity {eid} not found")
            else:
                self.createNotification(AvailableNotifications.IMAGE_PROBLEM, f"Attempt to retrieve sensor image for panel {self.getPanelID()}, entity not found")
        # The check_the_basics function sends a failure notification so no need to here

    def sendBypass(self, devid: int, bypass: bool, code: str) -> AlCommandStatus:
        """Send the bypass command to the panel."""
        if not self.DisableAllCommands:
            if self.visonicProtocol is not None:
                if self.toBool(self.config.get(CONF_ENABLE_SENSOR_BYPASS, False)):
                    dpin = self.decode_code_from_dict_or_str(code)
                    isValidPL, code = self.pmGetPinSimple(code = dpin)
                    if isValidPL:
                        # The device id in the range 1 to N
                        retval = self.visonicProtocol.setSensorBypassState(devid, bypass, code)
                        #retval = AlCommandStatus.FAIL_INVALID_CODE
                    else:
                        retval = AlCommandStatus.FAIL_INVALID_CODE
                else:
                    retval = AlCommandStatus.FAIL_USER_CONFIG_PREVENTED
            else:
                retval = AlCommandStatus.FAIL_PANEL_NO_CONNECTION

            self._generateBusEventReason(PanelCondition.CHECK_BYPASS_COMMAND, retval, "Bypass" if bypass else "Re-Arm", f"Sensor { "Bypass" if bypass else "Re-Arm" } State")
            return retval
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")
        return AlCommandStatus.FAIL_USER_CONFIG_PREVENTED

    def sendX10(self, devid: int, command : AlX10Command) -> AlCommandStatus:
        """Send the x10 command to the panel."""
        if not self.DisableAllCommands:
            if self.visonicProtocol is not None:
                retval = self.visonicProtocol.setX10(devid, command)
            else:
                retval = AlCommandStatus.FAIL_PANEL_NO_CONNECTION
            self._generateBusEventReason(PanelCondition.CHECK_X10_COMMAND, retval, "X10", "Send X10 Command")
            return retval
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")
        return AlCommandStatus.FAIL_USER_CONFIG_PREVENTED

    async def async_service_sensor_bypass(self, call):
        """Service call to bypass a sensor in the panel."""
        if await self.check_the_basics(call, "sensor bypass"):
            if await self.is_panel_status_set_to(call, AlPanelStatus.DISARMED, "sensor bypass", AvailableNotifications.BYPASS_PROBLEM):
                isValidPL, code = self.decode_code_from_call_data(call, "SensorBypass", PanelCondition.CHECK_BYPASS_COMMAND)
                if isValidPL:
                    devid, eid = await self.decode_entity(call, Platform.BINARY_SENSOR, "bypass a sensor", AvailableNotifications.BYPASS_PROBLEM)
                    if devid is not None and devid >= 1 and devid <= 64:
                        bypass: boolean = False
                        if ATTR_BYPASS in call.data:
                            bypass = call.data[ATTR_BYPASS]

                        if bypass:
                            self.logstate_debug("Attempting to bypass sensor device id = %s", str(devid))
                        else:
                            self.logstate_debug("Attempting to restore (arm) sensor device id = %s", str(devid))
                        self.sendBypass(devid, bypass, code)
                    else:
                        self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, incorrect device {devid} for entity {eid}")
        # The check_the_basics, is_panel_status_set_to and decode_code_from_call_data functions send a failure notification so no need to here

    async def async_service_panel_zoneinfo(self, call):
        """Service call get open zones in the panel."""
        sensors = []
        x10 = []
        open = []
        bypass = []
        battery = []
        status = False
        if await self.check_the_basics(call, "panel open zones"):
            status = True
            for sensor in self.sensor_list:
                entname = self.getMyString() + sensor.createFriendlyName().lower()
                sensors.append(BINARY_SENSOR_DOMAIN + "." + entname)
                if sensor.isOpen():
                    open.append(BINARY_SENSOR_DOMAIN + "." + entname)
                if sensor.isBypass():
                    bypass.append(BINARY_SENSOR_DOMAIN + "." + entname)
                if sensor.isLowBattery():
                    battery.append(BINARY_SENSOR_DOMAIN + "." + entname)
            for x in self.x10_list:
                entname = self.getMyString() + x.createFriendlyName().lower()
                x10.append(SWITCH_DOMAIN + "." + entname)
            self.logstate_debug(f"Get Panel zones: {open=}    {bypass=}")
        return { "valid": status,
                 "sensors": sensors,
                 "batterylow" : battery,
                 "open" : open,
                 "bypass": bypass,
                 "switches": x10
               }

    def sendCommand(self, message : str, command : AlPanelCommand, code : str, partitions : set = {1,2,3}) -> bool:   # the return value indicates whether any sensors needed to be bypassed
        if not self.DisableAllCommands:
            codeRequired = self.isCodeRequired()
            if (codeRequired and code is not None) or not codeRequired:
                pcode = self.decode_code_from_dict_or_str(code) if codeRequired or (code is not None and len(code) > 0) else ""
                if self.visonicProtocol is not None:
                    isValidPL, code = self.pmGetPin(code = pcode, forcedKeypad = self.isForceKeypad(), partition = 1)

                    if command in [AlPanelCommand.DISARM, AlPanelCommand.ARM_HOME, AlPanelCommand.ARM_AWAY, AlPanelCommand.ARM_HOME_INSTANT, \
                                   AlPanelCommand.ARM_AWAY_INSTANT, AlPanelCommand.ARM_HOME_BYPASS, AlPanelCommand.ARM_AWAY_BYPASS]:

                        self.logstate_debug(f"Send command to Visonic Alarm Panel: {command}")

                        if isValidPL:
                            if (command == AlPanelCommand.DISARM and self.isRemoteDisarm()) or (
                                command != AlPanelCommand.DISARM and self.isRemoteArm()):
                                didBypassSensor = False
                                if command in [AlPanelCommand.ARM_HOME_BYPASS, AlPanelCommand.ARM_AWAY_BYPASS]:
                                    command = AlPanelCommand.ARM_HOME if command == AlPanelCommand.ARM_HOME_BYPASS else AlPanelCommand.ARM_AWAY
                                    # determine which sensors are open and not already bypassed (and in this partiton if partitions are enabled)
                                    sl = set()
                                    if partitions is None or self.getPartitionsInUse() is None:
                                        self.logstate_debug(f"         Checking sensor bypass for single panel")
                                        for s in self.sensor_list:
                                            # if the sensor is not already bypassed, and is currently open
                                            if not s.isBypass() and s.isOpen():
                                                sl.add(s.getDeviceID())   # sl is a set so no repetition
                                    else:
                                        part = partitions & self.getPartitionsInUse() # set intersection
                                        self.logstate_debug(f"         Checking sensor bypass for partition {part}")
                                        for p in part:
                                            for s in self.sensor_list:
                                                # if the sensor is in the partition p, and not already bypassed, and is currently open
                                                self.logstate_debug(f"              Checking sensor bypass {p=} {s.getPartition()=} {s.isBypass()=} {s.isOpen()=}")
                                                if p in s.getPartition() and not s.isBypass() and s.isOpen():
                                                    sl.add(s.getDeviceID())   # sl is a set so no repetition

                                    if len(sl) > 0:
                                        self.logstate_debug(f"         Attempting to first bypass this sensor list: {sl}")
                                        retval = self.visonicProtocol.setSensorBypassState(sl, True, code)
                                        if retval != AlCommandStatus.SUCCESS:
                                            self._generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, retval , command.name, "Request Arm/Disarm")
                                            return False
                                        didBypassSensor = True
                                    else:
                                        self.logstate_debug(f"         No sensors to bypass so not sending bypass command first")
                                    
                                retval = self.visonicProtocol.requestPanelCommand(command, code, partitions)

                                # Arming and Disarming may change the bypass state of the sensors, so get an update
                                self.logstate_debug(f"         Requesting sensor bypass update")
                                self.visonicProtocol.requestSensorBypassStateUpdate()

                                self._generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, retval, command.name, "Request Arm/Disarm")
                                return didBypassSensor
                            else:
                                self._generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, AlCommandStatus.FAIL_USER_CONFIG_PREVENTED , command.name, "Request Arm/Disarm")
                        else:
                            self._generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, AlCommandStatus.FAIL_INVALID_CODE, command.name, "Request Arm/Disarm")

                    elif self.visonicProtocol.isPowerMaster() and (command in [AlPanelCommand.MUTE, AlPanelCommand.TRIGGER, AlPanelCommand.FIRE, AlPanelCommand.EMERGENCY, AlPanelCommand.PANIC]):
                        if isValidPL:
                            self.logstate_debug(f"Send command to Visonic Alarm Panel: {command}")
                            retval = self.visonicProtocol.requestPanelCommand(command, code, None)
                            self._generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, retval, command.name, "Request PowerMaster Panel Command")
                        else:
                            self._generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, AlCommandStatus.FAIL_INVALID_CODE, command.name, "Request PowerMaster Panel Command")
                    else:
                        self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, not sent to panel")
                else:
                    self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, not sent to panel")
            else:
                self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, either an alarm code is required or the panel is not in a valid mode")
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")
        return False       
       
    async def async_service_panel_command(self, call) -> bool:
        """Service call to send an arm/disarm command to the panel."""
        if await self.check_the_basics(call, "command"):
            isValidPL, code = self.decode_code_from_call_data(call, "PanelCommand", PanelCondition.CHECK_ARM_DISARM_COMMAND)
            if isValidPL:
                try:
                    if CONF_COMMAND in call.data:
                        command = call.data[CONF_COMMAND]
                        command_e = AlPanelCommand.value_of(command.upper());
                        self.logstate_debug(f"[service_panel_command]   Sending Command: {command_e}  from raw string: {command}")
                        didBypassSensor = False
                        if self.getPartitionsInUse() is None or ATTR_ENTITY_ID not in call.data:
                            didBypassSensor = self.sendCommand(f"Alarm Service Call {command_e}", command_e, code)  # No partition so default to all of them
                        else:
                            # Not ideal but parse the entity name to get the partition number on the end
                            eid = str(call.data[ATTR_ENTITY_ID])
                            if PE_PARTITION in eid:
                                p = int(eid[-1:])
                                didBypassSensor = self.sendCommand(f"Alarm Service Call {command_e}", command_e, code, { p } )  # set the partition
                            else:
                                # This is an error as there are partitions defined and so the word "partition" should be in the name
                                didBypassSensor = self.sendCommand(f"Alarm Service Call {command_e}", command_e, code)  # No partition so default to all of them
                        # only if the command has not included a possible bypass
                        return didBypassSensor
                    self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Attempt to send command to panel {self.getPanelID()}, command not set for entity {eid}")
                except Exception as ex:
                    self.logstate_warning(f"Not making command request. Exception {ex}")
                    self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Attempt to send command to panel {self.getPanelID()}, command not set for entity {eid} due to an Exception")
        # The check_the_basics and decode_code_from_call_data functions send a failure notification so no need to here
        return False

    def sendX10Command(self, devid: int, command : AlX10Command):
        """Send a request to set the X10 device """
        if not self.DisableAllCommands:
            self.sendX10(devid, command)
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")

    async def service_panel_x10(self, call):
        """Service call to set an x10 device in the panel."""
        if await self.check_the_basics(call, "x10 command"):
            devid, eid = await self.decode_entity(call, Platform.SWITCH, "x10 switch command", AvailableNotifications.X10_PROBLEM) # ************************************************************************************************
            if devid is not None and devid >= 1 and devid <= 16:
                if CONF_X10_COMMAND in call.data:
                    command = call.data[CONF_X10_COMMAND]
                    command_x = AlX10Command.value_of(command.upper());
                    self.logstate_debug(f"   X10 Command {command}   {command_x}")
                    self.sendX10Command(devid, command_x)
                else:
                    self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Attempt to set X10 device for panel {self.getPanelID()}, command not set for entity {eid}")
            else:
                self.createNotification(AvailableNotifications.X10_PROBLEM, f"Attempt to set X10 device for panel {self.getPanelID()}, incorrect device {devid} for entity {eid}")
        # The check_the_basics function sends a failure notification so no need to here

    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================
    # ======== Functions below this make the connection to the panel and manage restarts etc ================
    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================

    # Create a connection using asyncio using an ip and port
    async def async_create_tcp_visonic_connection(self, vp : VisonicProtocol, address, port):
        """Create Visonic manager class, returns tcp transport coroutine."""

        def createSocketConnection(address, port):
            """Create the Socket Connection to the Device in the Panel"""
            try:
                #self.logstate_debug(f"Setting TCP socket Options {address} {port}")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                sock.setblocking(1)  # Set blocking to on, this is the default but just make sure
                sock.settimeout(1.0)  # set timeout to 1 second to flush the receive buffer
                sock.connect((address, port))

                # Flush the buffer, receive any data and dump it
                try:
                    dummy = sock.recv(10000)  # try to receive 10000 bytes
                    self.logstate_debug("Buffer Flushed and Received some data!")
                except socket.timeout:  # fail after 1 second of no activity
                    #self.logstate_debug("Buffer Flushed and Didn't receive data! [Timeout]")
                    pass

                # set the timeout to infinite
                sock.settimeout(None)
                # return the socket
                return sock
                
            except socket.error as err:
                # Do not cause a full Home Assistant Exception, keep it local here
                self.logstate_debug(f"Setting TCP socket Options Exception {err}")
                if sock is not None:
                    sock.close()

            return None

        try:
            sock = createSocketConnection(address, int(port))
            if sock is not None:
                # Create the Protocol Handler for the Panel, also handle Powerlink connection inside this protocol handler
                cvp = ClientVisonicProtocol(vp=vp, client=self)
                # create the connection to the panel as an asyncio protocol handler and then set it up in a task
                coro = self.hass.loop.create_connection(cvp, sock=sock)
                self.logstate_debug("The coro type is " + str(type(coro)) + "   with value " + str(coro))
                # Wrap the coroutine in a task to add it to the asyncio loop
                vTask = self.hass.loop.create_task(coro)
                # Return the task and protocol
                return vTask, cvp

        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            pass
            
        return None, None

    def tellemaboutme(self, thisisme):
        """This function is here so that the coroutine can tell us the protocol handler"""
        self.tell_em = thisisme

    # Create a connection using asyncio through a linux port (usb or rs232)
    async def async_create_usb_visonic_connection(self, vp : VisonicProtocol, path, baud=str(DEFAULT_DEVICE_BAUD)):
        """Create Visonic manager class, returns rs232 transport coroutine."""
        from serial_asyncio import create_serial_connection

        self.logstate_debug("Setting USB Options")

        # use default protocol if not specified
        protocol = partial(
            ClientVisonicProtocol,
            vp=vp,
            client=self,
        )

        # setup serial connection
        path = path
        baud = int(baud)
        try:
            self.tell_em = None
            # create the connection to the panel as an asyncio protocol handler and then set it up in a task
            conn = create_serial_connection(self.hass.loop, protocol, path, baud)
            #self.logstate_debug("The coro type is " + str(type(conn)) + "   with value " + str(conn))
            vTask = self.hass.loop.create_task(conn)
            if vTask is not None:
                ctr = 0
                while self.tell_em is None and ctr < 40:     # 40 with a sleep of 0.05 is approx 2 seconds. Wait up to 2 seconds for this to start.
                    await asyncio.sleep(0.05)                # This should only happen once while the Protocol Handler starts up and calls tellemaboutme to set self.tell_em
                    ctr = ctr + 1
                if self.tell_em is not None:
                    # Return the task and protocol
                    return vTask, self.tell_em
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            self.logstate_debug(f"Setting USB Options Exception {ex}")
        return None, None

    async def _async_connect_comms(self) -> bool:
        """Create the comms connection to the alarm panel."""
        await self._stopCommsTask()
        # Connect in the way defined by the user in the config file, ethernet or usb
        retval = False
        if self.visonicProtocol is not None:
            self.visonicProtocol.resetVariablesForNewConnection()
            # Get Visonic specific configuration.
            device_type = self.config.get(CONF_DEVICE_TYPE, "")     # This must be set so default to an invalid setting
            self.logstate_debug("Comms Device Type is %s", device_type)
            self.cvp = None
            self.visonicCommsTask = None
            if device_type == DEVICE_TYPE_ETHERNET:
                host = self.config.get(CONF_HOST, "127.0.0.1")
                port = self.config.get(CONF_PORT, 0)
                (self.visonicCommsTask, self.cvp) = await self.async_create_tcp_visonic_connection(vp=self.visonicProtocol, address=host, port=port)
            elif device_type == DEVICE_TYPE_USB:
                path = self.config.get(CONF_PATH, "COM0")
                baud_rate = self.config.get(CONF_DEVICE_BAUD, DEFAULT_DEVICE_BAUD)
                (self.visonicCommsTask, self.cvp) = await self.async_create_usb_visonic_connection(vp=self.visonicProtocol, path=path, baud=baud_rate)
            retval = self.cvp is not None and self.visonicCommsTask is not None
        return retval

    async def _stopCommsTask(self):
        if self.visonicCommsTask is not None:
            self.logstate_debug("........... Closing down Current Comms Task (to close the rs232/socket connection)")
            # Close the protocol handler 
            if self.cvp is not None:
                self.cvp.close()
            # Stop the comms task
            try:
                self.visonicCommsTask.cancel()
            except Exception as ex:
                # Do not cause a full Home Assistant Exception, keep it local here
                self.logstate_debug("...........      Caused an exception")
                self.logstate_debug(f"                    {ex}")   
            # Make sure its all stopped
            await asyncio.sleep(0.5)
            if self.visonicCommsTask is not None and self.visonicCommsTask.done():
                self.logstate_debug("........... Current Comms Task Done")
            else:
                self.logstate_debug("........... Current Comms Task Not Done")
        # Indicate that both have been stopped
        self.visonicCommsTask = None
        self.cvp = None

    async def async_reconnect_and_restart(self, force_reconnect : bool, allow_restart : bool) -> bool:

        async def _async_panel_restart(): 
            try:
                # Deschedule point to allow other threads to complete
                await asyncio.sleep(0.0)
                # Deschedule point to allow other threads to complete
                await asyncio.sleep(0.0)
                if self.SystemStarted:
                    # If not already stopped, then stop the integrations connection to the panel
                    self.logstate_debug("........... _async_panel_restart, stopping panel interaction")
                    await self.async_panel_stop(killRestart = False)  # this should set self.SystemStarted to False

                self.logstate_debug("........... _async_panel_restart, attempting reconnection")
                await self.async_connect(force=False)
                
            except Exception as ex:
                # Do not cause a full Home Assistant Exception, keep it local here
                self.logstate_warning(f"........... _async_panel_restart, caused exception {ex}")

            self.doingRestart = None

        if not self.alreadyDoingThisFunction:
            epicfail = False
            self.alreadyDoingThisFunction = True
            if self.SystemStarted:
                if self.totalAttempts > 0 or force_reconnect:                  # If the user says 0 restart attempts then do not restart at all
                    if self.doingRestart is None:
                        self.logstate_debug(f"Setting up panel reconnection to Visonic Panel {self.getPanelID()}")
                        if await self._async_connect_comms():                  # Try a simple comms reconnect first
                            self.logstate_debug(f"Setting up panel reconnection success to Visonic Panel {self.getPanelID()}")
                            self._fireHAEvent(event_id = PanelCondition.CONNECTION, datadictionary = {"state": "connected", "attempt": 1})
                        elif self.doingRestart is None:                        # Check doingRestart again as it could have changed
                            if allow_restart:                                      # if the simple reconnect fails then optionally do a restart, 
                                self.logstate_debug(f"Setting up panel reconnection failed so doing a Restart to Visonic Panel {self.getPanelID()}")
                                self.doingRestart = self.hass.loop.create_task(_async_panel_restart())    # do restart 
                            else:
                                self.logstate_debug(f"Setting up panel reconnection failed to Visonic Panel {self.getPanelID()}. Restart not allowed in this context.")
                                epicfail = True
                        else:
                            self.logstate_debug(f"Setting up panel reconnection failed to Visonic Panel {self.getPanelID()}. And a Restart is in progress.")
                    else:
                        self.logstate_debug(f"Not Setting up panel reconnection, already doing Restart to Visonic Panel {self.getPanelID()}")
                        epicfail = True
                else:
                    self.logstate_info(f"Sorry, a simple Reconnection is not possible to Visonic Panel {self.getPanelID()} user specified 0 reconnect attempts")
                    epicfail = True
            else:
                self.logstate_info(f"Sorry, a simple Reconnection is not possible to Visonic Panel {self.getPanelID()} as system has stopped and lost all context, so please Reload")
                epicfail = True
            self.alreadyDoingThisFunction = False
            if epicfail:
                self.logstate_debug(f"   Epic Fail, stopping connection to Visonic Panel {self.getPanelID()}")
                await self.async_panel_stop()
                
            return True   # Did something
        return False      # Did nothing

    async def async_service_panel_reconnect(self, call = None):
        """Service call to re-connect the comms connection."""
        # This is callable from frontend and checks user permission
        try:
            if call is not None:
                if call.context.user_id:
                    #self.logstate_debug(f"Checking user information for permissions: {call.context.user_id}")
                    # Check security permissions (that this user has access to the alarm panel entity)
                    await self._checkUserPermission(call, POLICY_CONTROL, Platform.ALARM_CONTROL_PANEL + "." + slugify(self.getAlarmPanelUniqueIdent()))
            await self.async_reconnect_and_restart(force_reconnect = True, allow_restart = False)
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            self.logstate_warning(f"........... async_service_panel_reconnect, caused exception {ex}")

    async def async_panel_stop(self, killRestart : bool = True) -> bool:
        """Service call to stop the connection."""

        success = True
        try:
            if self.SystemStarted:
                # If there's an ongoing restart then terminate it
                if killRestart and self.doingRestart is not None:
                    self.logstate_debug("........... _kill_restart, there is already an ongoing restart so stopping it")
                    try:
                        self.doingRestart.cancel()
                        while not self.doingRestart.done():
                            await asyncio.sleep(0.0)
                    except Exception as ex:
                        self.logstate_debug("...........             Caused an exception")
                        self.logstate_debug(f"                           {ex}")   
                    self.doingRestart = None
                    self.logstate_debug("........... _kill_restart,                  ............... Ongoing Restart has been stopped")

                # stop the usb/ethernet comms with the panel
                await self._stopCommsTask()

                # Close down the tasks within the event coordinators
                if self.myPanelEventCoordinator is not None:
                    if isinstance(self.myPanelEventCoordinator, dict):
                        for key, value in self.myPanelEventCoordinator.items():
                            if isinstance(value, PanelEventCoordinator):
                                value.close()
                            else:
                                self.logstate_debug(f"........... async_panel_stop, self.myPanelEventCoordinator of unknown value type {type(value)}")
                    elif isinstance(self.myPanelEventCoordinator, PanelEventCoordinator):
                        self.myPanelEventCoordinator.close()
                    else:
                        self.logstate_debug(f"........... async_panel_stop, self.myPanelEventCoordinator of unknown type {type(self.myPanelEventCoordinator)}")

                self.logstate_debug(f"........... async_panel_stop, unloading platforms")
                success = await self.hass.config_entries.async_unload_platforms(self.entry, PLATFORMS)

                # Shutdown the protocol handler and any tasks it uses
                if self.visonicProtocol is not None:
                    self.visonicProtocol.shutdownOperation()
                    
                self.killMyDispatchers(self.entry)
            
            # Reset all variables, include setting self.SystemStarted to False
            self._initialise()
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            self.logstate_warning(f"........... async_panel_stop, caused exception {ex}")
        return success


    def killMyDispatchers(self, entry: VisonicConfigEntry):
        for p in PLATFORMS:
            d = entry.runtime_data.dispatchers.get(p, None)
            if d is not None:
                d()
                entry.runtime_data.dispatchers[p] = None
                _LOGGER.debug(f"[killMyDispatchers]  {p=}  Success")
            else:
                _LOGGER.debug(f"[killMyDispatchers]  {p=}  Not Done")

    async def async_connect(self, force=True) -> bool:
        """Connect to the alarm panel using the pyvisonic library."""

        async def _async_panel_start(force=False) -> bool:
            """Service call to start the connection."""
            self.logstate_debug(f"_async_panel_start, connecting   ... {force=}   {self.totalAttempts=}")

            try:
                attemptCounter = 0        
                #self.logstate_debug(f"     {attemptCounter} of {self.totalAttempts}")
                while force or attemptCounter < self.totalAttempts:
                    self.logstate_debug(f"........... connection attempt {attemptCounter + 1} of {1 if force else self.totalAttempts}{'     (with no future reconnections)' if force else ''}")
                    if await self._async_connect_comms():
                        # Connection to the panel has been initially successful
                        self.logstate_debug("........... connection made")
                        self._fireHAEvent(event_id = PanelCondition.CONNECTION, datadictionary = {"state": "connected", "attempt": attemptCounter + 1})
                        return True
                    # Failed so set up for next loop around
                    self._fireHAEvent(event_id = PanelCondition.CONNECTION, datadictionary = {"state": "failedattempt", "attempt": attemptCounter + 1})
                    attemptCounter = attemptCounter + 1
                    force = False
                    if attemptCounter < self.totalAttempts:
                        self.logstate_debug(f"........... connection attempt delay {self.delayBetweenAttempts} seconds")
                        try:
                            await asyncio.sleep(self.delayBetweenAttempts)
                        except:
                            self.logstate_debug(f"........... connection attempt delay exception")

                await self.async_panel_stop()
                # Set all variables to their defaults, this means that no connection has been made
                self._initialise()

                self.createNotification(
                    AvailableNotifications.CONNECTION_PROBLEM,
                    f"Failed to connect into Visonic Alarm Panel {self.getPanelID()}. Check Your Network and the Configuration Settings."
                )
                #self.logstate_debug("Giving up on trying to connect, sorry")
            except Exception as ex:
                # Do not cause a full Home Assistant Exception, keep it local here
                self.logstate_warning(f"........... _async_panel_start, caused exception {ex}")
                
            return False

        if self.SystemStarted:
            self.logstate_warning("Request to Start and the integraion is already running and connected")
        else:
            self.visonicProtocol = None
            try:
                #self.logstate_debug(f"[async_connect]       async_forward_entry_setups")
                # Call this before connecting to the panel to set up the platforms
                await self.hass.config_entries.async_forward_entry_setups( self.entry, PLATFORMS )
                #self.logstate_debug(f"[async_connect]       async_forward_entry_setups done")

                self.visonicProtocol = VisonicProtocol(panelConfig=self.getConfigData(), panel_id=self.panelident, loop=self.hass.loop)
                
                self.logstate_debug("Client connecting.....")
                if await _async_panel_start(force=force):
                    self.visonicProtocol.onPanelChange(self.onPanelChangeHandler)
                    self.visonicProtocol.onPanelLog(self.process_panel_event_log)
                    self.visonicProtocol.onProblem(self.onProblem)
                    self.visonicProtocol.onNewSensor(self.onNewSensor)
                    self.visonicProtocol.onNewSwitch(self.onNewSwitch)
                    ## Establish a callback to stop the component when the stop event occurs
                    self.hass.bus.async_listen_once(
                        EVENT_HOMEASSISTANT_STOP, self.async_panel_stop
                    )
                    # Record that we have started the system
                    self.SystemStarted = True
                    return True

                self.visonicProtocol = None
                _LOGGER.debug(f"........... connection unsuccessful, unloading platforms")
                self.killMyDispatchers(self.entry)
                unload_ok = await self.hass.config_entries.async_unload_platforms(self.entry, PLATFORMS)
                    
            except (ConnectTimeout, HTTPError) as ex:
                createNotification(
                    AvailableNotifications.CONNECTION_PROBLEM,
                    "Visonic Panel Connection Error: {ex}<br />"
                    "You will need to restart hass after fixing.")

        if not self.SystemStarted and self.visonicProtocol is not None:
            self.logstate_debug("........... Shutting Down Protocol")
            self.visonicProtocol.shutdownOperation()
            self.visonicProtocol = None
        return False

