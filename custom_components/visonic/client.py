"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""
import asyncio
import logging
from typing import Callable, Any
import re
import socket
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from functools import partial
import threading

from enum import IntEnum
from requests import ConnectTimeout, HTTPError

from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.util import slugify
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    Platform,
    ATTR_CODE,
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PATH,
    CONF_PORT,
    CONF_USERNAME, 
    CONF_PASSWORD,
    EVENT_HOMEASSISTANT_STOP,
)

# The following 3 are only used in def printAllEntities which is only for debug
from homeassistant.helpers import entity_platform as ep
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from homeassistant.components import persistent_notification
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.image import DOMAIN as IMAGE_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.alarm_control_panel import DOMAIN as ALARM_PANEL_DOMAIN
from homeassistant.util.thread import ThreadWithException

from .pyconst import (AlEnum, AlTransport, PanelConfig, AlConfiguration, AlPanelMode, AlPanelCommand, AlPanelStatus, AlTroubleType, AlSensorType,  
                      AlAlarmType, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlSensorDevice, AlLogPanelEvent, AlSwitchDevice)
from .pyvisonic import VisonicProtocol
from .create_schema import AvailableSensorEvents

from .const import (
    available_emulation_modes,
    ALARM_PANEL_CHANGE_EVENT,
    ALARM_SENSOR_CHANGE_EVENT,
    ALARM_COMMAND_EVENT,
    ALARM_PANEL_LOG_FILE_COMPLETE,
    ALARM_PANEL_LOG_FILE_ENTRY,
#    ALARM_PANEL_COMMAND,
#    ALARM_PANEL_EVENTLOG,
#    ALARM_PANEL_RECONNECT,
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
#    CONF_AUTO_SYNC_TIME,
    CONF_EEPROM_ATTRIBUTES,
    CONF_DEVICE_BAUD,
    CONF_DEVICE_TYPE,
    CONF_DOWNLOAD_CODE,
#    CONF_FORCE_AUTOENROLL,
    CONF_EMULATION_MODE,
    CONF_LANGUAGE,
    CONF_MOTION_OFF_DELAY,
    CONF_MAGNET_CLOSED_DELAY,
    CONF_EMER_OFF_DELAY,
    CONF_SIREN_SOUNDING,
    CONF_SENSOR_EVENTS,
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
    DOMAIN,
    NOTIFICATION_ID,
    NOTIFICATION_TITLE,
    CONF_ALARM_NOTIFICATIONS,
    PANEL_ATTRIBUTE_NAME,
    DEVICE_ATTRIBUTE_NAME,
    AvailableNotifications,
    PIN_REGEX,
)

#BASE_PRELOAD_PLATFORMS = [
#    "config",
#    "config_flow",
#    "diagnostics",
#    "energy",
#    "group",
#    "logbook",
#    "hardware",
#    "intent",
#    "media_source",
#    "recorder",
#    "repairs",
#    "system_health",
#    "trigger",
#]

CLIENT_VERSION = "0.9.6.22"

MAX_CLIENT_LOG_ENTRIES = 300

_LOGGER = logging.getLogger(__name__)

class PanelCondition(IntEnum): # Start at 100 to make them unique for AlarmPanelEventActionList mixing with AlCondition
    CHECK_ARM_DISARM_COMMAND = 100
    CHECK_BYPASS_COMMAND = 101
    CHECK_EVENT_LOG_COMMAND = 102
    CHECK_X10_COMMAND = 103
    CONNECTION = 104
    PANEL_LOG = 105

messageDict = {
    AlCommandStatus.SUCCESS                     : "Success, sent Command to Panel",
    AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS   : "Failed to Send Command To Panel, not supported when downloading EPROM",
    AlCommandStatus.FAIL_INVALID_CODE           : "Failed to Send Command To Panel, not allowed without valid pin",
    AlCommandStatus.FAIL_USER_CONFIG_PREVENTED  : "Failed to Send Command To Panel, disabled by user settings",
    AlCommandStatus.FAIL_INVALID_STATE          : "Failed to Send Command To Panel, invalid state requested",
    AlCommandStatus.FAIL_X10_PROBLEM            : "Failed to Send Command To Panel, general X10 Problem",
    AlCommandStatus.FAIL_PANEL_CONFIG_PREVENTED : "Failed to Send Command To Panel, disabled by panel settings"
}

ValidEvents = [
    ALARM_SENSOR_CHANGE_EVENT,
    ALARM_PANEL_CHANGE_EVENT,
    ALARM_PANEL_LOG_FILE_ENTRY,
    ALARM_PANEL_LOG_FILE_COMPLETE,
    ALARM_COMMAND_EVENT
]
  
AlarmPanelEventActionList = {
   AlCondition.ZONE_UPDATE                 : "",
   AlCondition.PANEL_UPDATE                : "panelupdate", 
   AlCondition.PANEL_RESET                 : "panelreset",
   AlCondition.PIN_REJECTED                : "pinrejected",
   AlCondition.DOWNLOAD_TIMEOUT            : "timeoutdownload", 
   AlCondition.WATCHDOG_TIMEOUT_GIVINGUP   : "timeoutwaiting", 
   AlCondition.WATCHDOG_TIMEOUT_RETRYING   : "timeoutactive", 
   AlCondition.NO_DATA_FROM_PANEL          : "nopaneldata", 
   PanelCondition.CONNECTION               : "connection",
   PanelCondition.PANEL_LOG                : "",
   PanelCondition.CHECK_ARM_DISARM_COMMAND : "armdisarm", 
   PanelCondition.CHECK_BYPASS_COMMAND     : "bypass", 
   PanelCondition.CHECK_EVENT_LOG_COMMAND  : "eventlog", 
   PanelCondition.CHECK_X10_COMMAND        : "x10"
}

class MyTransport(AlTransport):

    def __init__(self, t):
        self._transport = t
    
    def write(self, b : bytearray):
        self._transport.write(b)

    def close(self):
        self._transport.close()

    def changeSerialBaud(self, baud : int):
        print(f"[MyTransport] A, {self._transport.serial.baudrate} {type(self._transport.serial.baudrate)}")
        self._transport.serial.baudrate = baud
        print(f"[MyTransport] B, {self._transport.serial.baudrate} {type(self._transport.serial.baudrate)}")

# This class joins the Protocol data stream to the visonic protocol handler.
#    transport needs to have 2 functions:   write(bytearray)  and  close()
class ClientVisonicProtocol(asyncio.Protocol, VisonicProtocol):

    def __init__(self, client = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if client is not None:
            client.tellemaboutme(self)

    def data_received(self, data):
        super().vp_data_received(data)

    def connection_made(self, transport):
        self._transport = transport
        super().vp_connection_made(MyTransport(t=transport))

    def connection_lost(self, exc):
        super().vp_connection_lost(exc)
        self._transport = None

    def changeSerialBaud(self, baud : int):
        print(f"[ClientVisonicProtocol] A, {self._transport.serial.baudrate} {type(self._transport.serial.baudrate)}")
        self._transport.serial.baudrate = baud
        print(f"[ClientVisonicProtocol] B, {self._transport.serial.baudrate} {type(self._transport.serial.baudrate)}")

    # This is needed so we can create the class instance before giving it to the protocol handlers
    def __call__(self):
        return self

class VisonicClient:
    """Set up for Visonic devices."""
    
    _LOGGER.debug("Initialising Client - Version {0}".format(CLIENT_VERSION))

    def __init__(self, hass: HomeAssistant, panelident: int, cf: dict, entry: ConfigEntry):
        """Initialize the Visonic Client."""
        self.hass = hass
        self.entry = entry
        # Get the user defined config
        self.config = cf.copy()
        self.strlog = []
        #self.logstate_debug(f"init panel {str(panelident)}  language {str(self.hass.config.language)}   self.config = {self.config}")
        self.panelident = panelident
        self._initialise()
        self.logstate_info(f"Exclude sensor list = {self.exclude_sensor_list}     Exclude x10 list = {self.exclude_x10_list}")
        
    def _initialise(self):
        # panel connection
        self.logstate_debug("reset client panel variables")
        
        self.vp = None

        self.visonic_sensor_setup_lock = asyncio.Lock()
        self.visonic_switch_setup_lock = asyncio.Lock()
        self.visonic_alarm_setup_lock = asyncio.Lock()

        self.panel_exception_counter = 0
        self.visonicTask = None
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
        
        self.loaded_platforms = set()
        
        self.onChangeHandler = []

        self.sensor_list = list()
        self.image_list = list()
        self.x10_list = list()

        self.baud_rate = 9600

        self.delayBetweenAttempts = 60.0
        self.totalAttempts = 1

        self.DisableAllCommands = False

        self._setupSensorDelays()

        # Process the exclude sensor list
        self.exclude_sensor_list = self.config.get(CONF_EXCLUDE_SENSOR)
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
        self.exclude_x10_list = self.config.get(CONF_EXCLUDE_X10)
        if self.exclude_x10_list is None or len(self.exclude_x10_list) == 0:
            self.exclude_x10_list = []
        if isinstance(self.exclude_x10_list, str) and len(self.exclude_x10_list) > 0:
            self.exclude_x10_list = [
                int(e) if e.isdigit() else e for e in self.exclude_x10_list.split(",")
            ]
 
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
        self.strlog.append(str(datetime.now()) + "  D " + s)
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)
            
    def logstate_info(self, msg, *args, **kwargs):
        s = "P" + str(self.getPanelID()) + "  " + (msg % args % kwargs)
        _LOGGER.info(" " + s)
        self.strlog.append(str(datetime.now()) + "  I " + s)
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)

    def logstate_warning(self, msg, *args, **kwargs):
        s = "P" + str(self.getPanelID()) + "  " + (msg % args % kwargs)
        _LOGGER.warning(s)
        self.strlog.append(str(datetime.now()) + "  W " + s)
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)

    def getStrLog(self):
        return self.strlog

    def getEntryID(self):
        return self.entry.entry_id

    def getPanelID(self):
        return self.panelident

    def getMyString(self) -> str:
        if self.getPanelID() > 0:
            return "visonic_p" + str(self.panelident) + "_"
        return "visonic_"

    def getAlarmPanelUniqueIdent(self):
        if self.getPanelID() > 0:
            return VISONIC_UNIQUE_NAME + " Panel " + str(self.getPanelID())
        return VISONIC_UNIQUE_NAME

    def createNotification(self, condition : AvailableNotifications, message: str):
        """Create a message in the log file and a notification on the HA Frontend."""
        notification_config = self.config.get(CONF_ALARM_NOTIFICATIONS, [] )
        
        self.logstate_debug(f"notification_config {notification_config}")
        
        if condition == AvailableNotifications.ALWAYS or condition.value in notification_config:
            # Create an info entry in the log file and an HA notification
            self.logstate_info(f"HA Notification: {message}")
            persistent_notification.create(self.hass, message, title=NOTIFICATION_TITLE, notification_id=NOTIFICATION_ID)
        else:
            # Just create a log file entry (but indicate that it wasnt shown in the frontend to the user
            self.logstate_info(f"HA Warning (not shown in frontend due to user config), condition is {condition} message={message}")

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

    def isSirenActive(self) -> bool:
        """Is the siren active."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isSirenActive()
        return False

    def isPanelReady(self) -> bool:
        """Is panel ready"""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isPanelReady()
        return False

    def isPanelTrouble(self) -> bool:
        """Is panel trouble"""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isPanelTrouble()
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
        return { "Exception Count": self.panel_exception_counter }

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

    def getPanelStatus(self) -> AlPanelStatus:
        """Get the panel status code."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelStatus()
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

    def getPanelStatusDict(self, include_extended_status : bool = None) -> dict:
        """Get the panel status."""
        if self.visonicProtocol is not None:
            if include_extended_status is None:
                include_extended_status = self.toBool(self.config.get(CONF_EEPROM_ATTRIBUTES, False))
            pd = self.visonicProtocol.getPanelStatusDict(include_extended_status)
            #self.logstate_debug("Client Dict {0}".format(pd))
            pd["Client Version"] = CLIENT_VERSION
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
            if len(self.config.get(CONF_LOG_XML_FN)) > 0:
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
                    template = env.get_template("visonic_template.xml")
                    output = template.render(
                        entries=self.templatedata,
                        total=total,
                        available="{0}".format(available),
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

            if len(self.config.get(CONF_LOG_CSV_FN)) > 0:
                try:
                    self.logstate_debug(
                        "Panel Event Log - Starting csv save filename %s",
                        self.config.get(CONF_LOG_CSV_FN),
                    )
                    if self.toBool(self.config.get(CONF_LOG_CSV_TITLE)):
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

        #self._exc_info = None
        #finish_event = asyncio.Event()

        self.logstate_debug(f"Panel Event Log - Processing {entry.current} of {entry.total}")
        reverse = self.toBool(self.config.get(CONF_LOG_REVERSE))
        total = 0
        if entry.total is not None and self.config.get(CONF_LOG_MAX_ENTRIES) is not None:
            total = min(entry.total, self.config.get(CONF_LOG_MAX_ENTRIES))
        elif entry.total is not None:
            total = entry.total
        elif self.config.get(CONF_LOG_MAX_ENTRIES) is not None:
            total = self.config.get(CONF_LOG_MAX_ENTRIES)
        current = entry.current  # only used for output and not for logic
        if reverse:
            current = total + 1 - entry.current
        # Fire event visonic_alarm_panel_event_log
        if (
            self.toBool(self.config.get(CONF_LOG_EVENT))
            and entry.current <= total
        ):  
            self._fireHAEvent(
                name = ALARM_PANEL_LOG_FILE_ENTRY, 
                event_id = PanelCondition.PANEL_LOG, 
                datadictionary = {"current": current,
                                  "total": total,
                                  "date": entry.date,
                                  "time": entry.time,
                                  "partition": entry.partition,
                                  "zone": entry.zone,
                                  "event": entry.event,
                }
            )
            #self.logstate_debug("Panel Event Log - fired Single Item event")
        
        # Initialise values
        if entry.current == 1:
            self.templatedata = []
            self.csvdata = ""

        if self.csvdata is not None and self.templatedata is not None:
            # Accumulating CSV Data
            csvtemp = (f"{current}, {total}, {entry.partition}, {entry.date}, {entry.time}, {entry.zone}, {entry.event}\n")
            if reverse:
                self.csvdata = csvtemp + self.csvdata
            else:
                self.csvdata = self.csvdata + csvtemp

            # Accumulating Data for the XML generation
            dd = {
                "partition": "{0}".format(entry.partition),
                "current": "{0}".format(current),
                "date": "{0}".format(entry.date),
                "time": "{0}".format(entry.time),
                "zone": "{0}".format(entry.zone),
                "event": "{0}".format(entry.event),
            }

            self.templatedata.append(dd)

            if entry.current == total:
                self.logstate_debug(
                    "Panel Event Log - Received last entry  reverse=%s  xmlfilenamelen=%s csvfilenamelen=%s",
                    str(reverse),
                    len(self.config.get(CONF_LOG_XML_FN)),
                    len(self.config.get(CONF_LOG_CSV_FN)),
                )

                if reverse:
                    self.templatedata.reverse()

                x = threading.Thread(target=self._savePanelEventLogFiles, args=(entry.total, total), name=f"VisonicSaveEventLog{self.getPanelID()}",)
                x.start()
                x.join()

                if self.toBool(self.config.get(CONF_LOG_DONE)):
                    self.logstate_debug("Panel Event Log - Firing Completion Event")
                    self._fireHAEvent(name = ALARM_PANEL_LOG_FILE_COMPLETE, event_id = PanelCondition.PANEL_LOG, datadictionary = {"total": total, "available": entry.total})
                self.logstate_debug("Panel Event Log - Complete")

    # This is not called from anywhere, use it for debug purposes and/or to clear all entities from HA
    def printAllEntities(self, delete_as_well : bool = False):
        entity_reg = er.async_get(self.hass)
        entity_entries = er.async_entries_for_config_entry(entity_reg, self.entry.entry_id)
        for damn in entity_entries:
            _LOGGER.debug(f"         entity {damn}")
            if delete_as_well:
                entity_reg.async_remove(damn.entity_id)

        # clear out all devices from the registry to recreate them, if the user has added/removed devices then this ensures that its a clean start
        device_reg = dr.async_get(self.hass)
        device_entries = dr.async_entries_for_config_entry(device_reg, self.entry.entry_id)
        for damn in device_entries:
            _LOGGER.debug(f"         device {damn}")
            if delete_as_well:
                device_reg.async_remove_device(damn.id)

        # The platforms do not initially exist, but after a reload they already exist
        platforms = ep.async_get_platforms(self.hass, DOMAIN)
        _LOGGER.debug(f"         platforms {platforms}")

   
    async def _setupVisonicEntity(self, platform, domain, param = None):
        """Setup a platform and add an entity using the dispatcher."""
        if platform not in self.loaded_platforms:
            self.loaded_platforms.add(platform)
            await self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setups( self.entry, [ platform ]  ) )
        if param is None:
            async_dispatcher_send( self.hass, f"{DOMAIN}_{self.entry.entry_id}_add_{domain}" )
        else:
            async_dispatcher_send( self.hass, f"{DOMAIN}_{self.entry.entry_id}_add_{domain}", param )

    def onNewSwitch(self, dev: AlSwitchDevice): 
        asyncio.ensure_future(self.async_onNewSwitch(dev), loop=self.hass.loop)

    async def async_onNewSwitch(self, dev: AlSwitchDevice): 
        """Process a new x10."""
        # Check to ensure variables are set correctly
        if self.hass is None:
            self.logstate_warning("Attempt to add X10 switch when hass is undefined")
            return
        if not self._createdAlarmPanel:
            await self._async_setupAlarmPanel()
        if dev is None:
            self.logstate_warning("Attempt to add X10 switch when sensor is undefined")
            return
        if dev.getDeviceID() is None:
            self.logstate_warning("Switch callback but Switch Device ID is None")
            return
        if dev.isEnabled() and dev.getDeviceID() not in self.exclude_x10_list:
            dev.onChange(self.onSwitchChange)
            async with self.visonic_switch_setup_lock:
                if dev.getDeviceID() not in self.x10_list:
                    self.logstate_debug(f"X10 Switch list {self.x10_list=}     {dev.getDeviceID()=}")
                    self.x10_list.append(dev.getDeviceID())
                    await self._setupVisonicEntity(Platform.SWITCH, SWITCH_DOMAIN, dev)
                else:
                    self.logstate_debug(f"X10 Device {dev.getDeviceID()} already in the list")

    def _setupAlarmPanel(self):
        asyncio.ensure_future(self._async_setupAlarmPanel(), loop=self.hass.loop)

    async def _async_setupAlarmPanel(self):
        # This sets up the Alarm Panel, or the Sensor to represent a panel state
        #   It is called from multiple places, the first one wins
        async with self.visonic_alarm_setup_lock:
            if not self._createdAlarmPanel:
                self._createdAlarmPanel = True
                if self.DisableAllCommands:
                    self.logstate_debug("Creating Sensor for Alarm indications")
                    await self._setupVisonicEntity(Platform.SENSOR, SENSOR_DOMAIN)
                else:
                    self.logstate_debug("Creating Alarm Panel Entity")
                    await self._setupVisonicEntity(Platform.ALARM_CONTROL_PANEL, ALARM_PANEL_DOMAIN)

    def onNewSensor(self, sensor: AlSensorDevice):
        asyncio.ensure_future(self.async_onNewSensor(sensor), loop=self.hass.loop)

    async def async_onNewSensor(self, sensor: AlSensorDevice):
        """Process a new sensor."""
        # Check to ensure variables are set correctly
        if self.hass is None:
            self.logstate_warning("Visonic attempt to add sensor when hass is undefined")
            return
        if not self._createdAlarmPanel:
            await self._async_setupAlarmPanel()
        if sensor is None:
            self.logstate_warning("Visonic attempt to add sensor when sensor is undefined")
            return
        if sensor.getDeviceID() is None:
            self.logstate_warning("Sensor callback but Sensor Device ID is None")
            return
        if sensor.getDeviceID() not in self.exclude_sensor_list:
            async with self.visonic_sensor_setup_lock:
                sensor.onChange(self.onSensorChange)
                if sensor not in self.sensor_list:
                    self.logstate_debug("Adding Sensor %s", sensor)
                    self.sensor_list.append(sensor)
                    await self._setupVisonicEntity(Platform.BINARY_SENSOR, BINARY_SENSOR_DOMAIN, sensor)
                    if not self.DisableAllCommands:
                        # The connection to the panel allows interaction with the sensor, including the arming/bypass of the sensors
                        await self._setupVisonicEntity(Platform.SELECT, SELECT_DOMAIN, sensor)
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
                await self._setupVisonicEntity(Platform.IMAGE, IMAGE_DOMAIN, sensor)

    def onChange(self, fn : Callable):
        self.onChangeHandler.append(fn)

    def _fireHAEvent(self, name: str, event_id: AlCondition | PanelCondition, datadictionary: dict):
        # Check to ensure variables are set correctly
        if self.hass is None:
            self.logstate_warning("Attempt to generate HA event when hass is undefined")
            return

        if not self._createdAlarmPanel:
            self._setupAlarmPanel()

        if event_id is None:
            self.logstate_warning("Attempt to generate HA event when Event Type is undefined")
            return

        if name not in ValidEvents:
            self.logstate_warning(f"Attempt to generate HA event but it is Invalid {name}")
            return

        # Call all the registered client change handlers
        for cb in self.onChangeHandler:
            cb()

        if event_id in AlarmPanelEventActionList: # Event must be in the list to send out
            a = {}
            a[PANEL_ATTRIBUTE_NAME] = self.getPanelID()
            a["panel_id"] = Platform.ALARM_CONTROL_PANEL + "." + slugify(self.getAlarmPanelUniqueIdent())

            if name == ALARM_PANEL_CHANGE_EVENT or name == ALARM_COMMAND_EVENT:
                e = AlarmPanelEventActionList[event_id]
                a["action"] = str(e)

            if datadictionary is not None:
                b = datadictionary.copy()
                dd = {**a, **b}
                self.logstate_debug(f"Client: Sending HA Event {name}  {dd}")
                self.hass.bus.fire( name, dd )
            else:
                self.logstate_debug(f"Client: Sending HA Event {name}  {a}")
                self.hass.bus.fire( name, a )

    def onSensorChange(self, sensor : AlSensorDevice, c : AlSensorCondition):
        _LOGGER.debug(f"onSensorChange {c.name} {sensor}")

        #_LOGGER.debug(f"onSensorChange event list = {self.config.get(CONF_SENSOR_EVENTS)=}")
        
        list_of_sensor_events = [(AvailableSensorEvents[k]) for k in AvailableSensorEvents if k in self.config.get(CONF_SENSOR_EVENTS)]

        #_LOGGER.debug(f"onSensorChange event list = {list_of_sensor_events=}")

        if c in list_of_sensor_events:
            datadict = {}
            datadict["zone"] = sensor.getDeviceID()
            datadict["event"] = str(c).title()
            datadict["entity_id"] = ""
            
            for s in self.entry.runtime_data.sensors:
                if s.getDeviceID() == sensor.getDeviceID():
                    #_LOGGER.debug(f"onSensorChange Got it {type(s)}  {s.unique_id}  {s.entity_id}")
                    datadict["entity_id"] = s.entity_id
                    break
            
            self._fireHAEvent(ALARM_SENSOR_CHANGE_EVENT, AlCondition.ZONE_UPDATE, datadict)

        # Check to make sure we have an image entity created for this sensor
        if not self.DisableAllCommands and sensor.getDeviceID() not in self.image_list and sensor.getSensorType() == AlSensorType.CAMERA:
            asyncio.ensure_future(self.create_image_entity(sensor), loop=self.hass.loop)
    
    def onSwitchChange(self, switch : AlSwitchDevice):
        #_LOGGER.debug("onSwitchChange {0}".format(switch))
        pass

    # This can be called from this module but it is also the callback handler for the connection
    def onPanelChangeHandler(self, event_id: AlCondition | PanelCondition, data : dict, event_name = ALARM_PANEL_CHANGE_EVENT):
        """Generate HA Bus Event and Send Notification to Frontend."""
        
        self._fireHAEvent(name = event_name, event_id = event_id, datadictionary = data if data is not None else {} )

        if event_id == AlCondition.DOWNLOAD_SUCCESS:        # download success        
            # Update the friendly name of the control flow
            pm = self.getPanelModel()
            s = "Panel " + str(self.getPanelID()) + " (" + ("Unknown" if pm is None else pm) + ")"
            # update the title
            self.hass.config_entries.async_update_entry(self.entry, title=s)

        #if event_id == AlCondition.PANEL_UPDATE and self.getPanelMode() == AlPanelMode.POWERLINK:
        #    # Powerlink Mode
        #    self.printAllEntities()

        if event_id == AlCondition.PANEL_UPDATE and self.visonicProtocol is not None and self.visonicProtocol.isSirenActive():
            self.createNotification(AvailableNotifications.SIREN, "Siren is Sounding, Alarm has been Activated" )
        elif event_id == AlCondition.PANEL_RESET:
            self.createNotification(AvailableNotifications.RESET, "The Panel has been Reset" )
        #elif event_id == AlCondition.PANEL_TAMPER_ALARM:
        #    self.createNotification(AvailableNotifications.TAMPER, "The Panel has been Tampered" )
        elif event_id == AlCondition.PIN_REJECTED:
            self.createNotification(AvailableNotifications.INVALID_PIN, "The Pin Code has been Rejected By the Panel" )
        elif event_id == AlCondition.DOWNLOAD_TIMEOUT:
            self.createNotification(AvailableNotifications.PANEL_OPERATION, "Panel Data download timeout, Standard Mode Selected" )
        elif event_id == AlCondition.WATCHDOG_TIMEOUT_GIVINGUP:
            if self.getPanelMode() == AlPanelMode.POWERLINK:
                self.createNotification(AvailableNotifications.CONNECTION_PROBLEM, "Communication Timeout - Watchdog Timeout too many times within 24 hours. Dropping out of Powerlink" )
            else:
                self.createNotification(AvailableNotifications.CONNECTION_PROBLEM, "Communication Timeout - Watchdog Timeout too many times within 24 hours." )
        elif event_id == AlCondition.WATCHDOG_TIMEOUT_RETRYING:
            self.createNotification(AvailableNotifications.PANEL_OPERATION, "Communication Timeout - Watchdog Timeout, restoring panel connection" )
        elif event_id == AlCondition.NO_DATA_FROM_PANEL:
            self.createNotification(AvailableNotifications.CONNECTION_PROBLEM, "Connection Problem - No data from the panel" )
            asyncio.ensure_future(self.service_panel_stop(), loop=self.hass.loop)
        elif event_id == AlCondition.COMMAND_REJECTED:
            self.createNotification(AvailableNotifications.ALWAYS, "Operation Rejected By Panel (tell the Integration Author and upload a debug log file if you're able to)" )

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

    def getConfigData(self) -> PanelConfig:
        """Create a dictionary full of the configuration data."""

        v = self.config.get(CONF_EMULATION_MODE, available_emulation_modes[0])        
        self.ForceStandardMode = v == available_emulation_modes[1]
        self.DisableAllCommands = v == available_emulation_modes[2]

        if self.DisableAllCommands:
            self.ForceStandardMode = True
        # By the time we get here there are 3 combinations of self.DisableAllCommands and self.ForceStandardMode
        #     Both are False --> Try to get to Powerlink 
        #     self.ForceStandardMode is True --> Force Standard Mode, the panel can still be armed and disarmed
        #     self.ForceStandardMode and self.DisableAllCommands are True --> The integration interacts with the panel but commands such as arm/disarm/log/bypass are not allowed
        # The if statement above ensure these are the only supported combinations.

        self.logstate_debug(f"Emulation Mode {self.config.get(CONF_EMULATION_MODE)}   so setting    ForceStandard to {self.ForceStandardMode}     DisableAllCommands to {self.DisableAllCommands}")

        return {
            AlConfiguration.DownloadCode: self.config.get(CONF_DOWNLOAD_CODE, ""),
            AlConfiguration.ForceStandard: self.ForceStandardMode,
            AlConfiguration.DisableAllCommands: self.DisableAllCommands,
            AlConfiguration.PluginLanguage: self.config.get(CONF_LANGUAGE, "EN"),
            AlConfiguration.SirenTriggerList: self.config.get(CONF_SIREN_SOUNDING, ["Intruder"])
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
        if self.visonicProtocol is not None:
            self.visonicProtocol.updateSettings(self.getConfigData())
        self._setupSensorDelays()

    def stop_subscription(self, event):
        """Shutdown Visonic subscriptions and subscription thread on exit."""
        #self.logstate_debug("Home Assistant is shutting down")
        if self.SystemStarted:
            asyncio.ensure_future(self.service_panel_stop(), loop=self.hass.loop)

    def onDisconnect(self, reason : str, excep = None):
        """Disconnection Callback for connection disruption to the panel."""
        if excep is None:
            self.logstate_debug("Visonic has caused an exception, reason=%s, no exception information is available", reason)
        else:
            self.logstate_debug("Visonic has caused an exception, reason=%s %s", reason, str(excep))

        # General update trigger
        #    0 is a disconnect, state="disconnected" means initial disconnection and (hopefully) reconnect from an exception (probably comms related)
        self._fireHAEvent(name = ALARM_PANEL_CHANGE_EVENT, event_id = PanelCondition.CONNECTION, datadictionary = {"state": "disconnected", "reason": reason})

        self.panel_exception_counter = self.panel_exception_counter + 1
        asyncio.ensure_future(self.disconnect_callback_async(), loop=self.hass.loop)

    # pmGetPin: Convert a PIN given as 4 digit string in the PIN PDU format as used in messages to powermax
    def pmGetPin(self, code: str, forcedKeypad: bool):
        """Get code code."""
        #self.logstate_debug("Getting Pin Start")
        if code is None or code == "" or len(code) != 4:
            psc = self.getPanelStatus()
            panelmode = self.getPanelMode()
            #self.logstate_debug("Getting Pin")
            
            # Avoid the panel codes that we're not interested in, if these are set then we have no business doing any of the functions
            #    After this we can simply use DISARMED and not DISARMED for all the armed states
            if psc == AlPanelStatus.UNKNOWN or psc == AlPanelStatus.SPECIAL or psc == AlPanelStatus.DOWNLOADING:
                return False, None   # Return invalid as panel not in correct state to do anything
            
            if panelmode == AlPanelMode.STANDARD:
                if psc == AlPanelStatus.DISARMED:
                    if self.isArmWithoutCode():  # 
                        #self.logstate_debug("Here B")
                        return True, "0000"        # If the panel can arm without a usercode then we can use 0000 as the usercode
                    return False, None             # use keypad so invalidate the return, there should be a valid 4 code code
                else:
                    return False, None             # use keypad so invalidate the return, there should be a valid 4 code code
            elif panelmode == AlPanelMode.POWERLINK or panelmode == AlPanelMode.STANDARD_PLUS:  # 
                if psc == AlPanelStatus.DISARMED and self.isArmWithoutCode() and forcedKeypad:
                    return True, None    
                if forcedKeypad:
                    return False, None   # use keypad so invalidate the return, there should be a valid 4 code code
                return True, None    # Usercode
            elif panelmode == AlPanelMode.DOWNLOAD or panelmode == AlPanelMode.STARTING:  # No need to output to log file when starting or downloading EEPROM as this is normal operation
                return False, None # Return invalid as panel downloading EEPROM
            else:
                # If the panel mode is UNKNOWN, PROBLEM.
                self.logstate_warning("Warning: Valid 4 digit PIN not found, panelmode is {0}".format(panelmode))
                return False, None # Return invalid as panel not in correct state to do anything
        return True, code

    # pmGetPinSimple: Convert a PIN given as 4 digit string in the PIN PDU format as used in messages to powermax
    #   This is used from the bypass command and the get event log command
    def pmGetPinSimple(self, code: str):
        """Get code code."""
        #self.logstate_debug("Getting Pin Start")
        if code is None or code == "" or len(code) != 4:
            panelmode = self.getPanelMode()
            if panelmode == AlPanelMode.POWERLINK or panelmode == AlPanelMode.STANDARD_PLUS:
                # Powerlink or StdPlus and so we downloaded the code codes
                return True, None
            else:
                self.logstate_warning("Warning: [pmGetPinSimple] Valid 4 digit PIN not found, panelmode is {0}".format(panelmode))
                return False, None
        return True, code

    def _populateSensorDictionary(self) -> dict:
        datadict = {}
        datadict["ready"] = self.isPanelReady()
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
        datadict["message"] = message + " " + messageDict[reason]

        self.onPanelChangeHandler(event_id = event_id, event_name = ALARM_COMMAND_EVENT, data = datadict)

        #self.logstate_debug("[" + message + "] " + messageDict[reason])

        if reason != AlCommandStatus.SUCCESS:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, message + " " + messageDict[reason])

    def setX10(self, ident: int, state: AlX10Command):
        """Send an X10 command to the panel."""
        if not self.DisableAllCommands:
            # ident in range 0 to 15, state can be one of "off", "on", "dimmer", "brighten"
            if self.visonicProtocol is not None:
                retval = self.visonicProtocol.setX10(ident, state)
                self._generateBusEventReason(PanelCondition.CHECK_X10_COMMAND, retval, "X10", "Send X10 Command")
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")


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
        isValidPL, code = self.pmGetPin(code = None, forcedKeypad = self.isForceKeypad())
        return not isValidPL;

#    def isCodeRequiredBackup(self) -> bool:
#        """Determine if a user code is required given the panel mode and user settings."""
#        # try powerlink or standard plus mode first, then it already has the user codes
#        panelmode = self.getPanelMode()
#        # self.logstate_debug("code format panel mode %s", panelmode)
#        if not self.isForceKeypad() and panelmode is not None:
#            if panelmode == AlPanelMode.POWERLINK or panelmode == AlPanelMode.STANDARD_PLUS:
#                self.logstate_debug("No Code Required as powerlink or std plus ********")
#                return False
#
#        armcode = self.getPanelStatus()
#        if armcode is None or armcode == AlPanelStatus.UNKNOWN:
#            return True
#
#        # If currently Disarmed and user setting to not show panel to arm
#        if armcode == AlPanelStatus.DISARMED and self.isArmWithoutCode():
#            self.logstate_debug("No Code Required as panel is disarmed and user arm without code is set in config")
#            return False
#
#        if self.isForceKeypad():
#            self.logstate_debug("Code Required as force numeric keypad set in config")
#            return True
#
#        self.logstate_debug("Code Required")
#        return True

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
                self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Request Command, not sent to panel")
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")
        return False

    async def decode_code_from_call_data(self, call, message : str, cond : PanelCondition) -> (bool , str):
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

    async def service_panel_eventlog(self, call):
        """Service call to retrieve the event log from the panel. This currently just gets dumped in the HA log file."""
        if self.visonicProtocol is not None:
            if await self.check_the_basics(call, "event log"):
                isValidPL, code = await self.decode_code_from_call_data(call, "EventLog", PanelCondition.CHECK_EVENT_LOG_COMMAND)
                if isValidPL:
                    self.logstate_debug("Sending event log request to panel")
                    retval = self.visonicProtocol.getEventLog(code)
                    self._generateBusEventReason(PanelCondition.CHECK_EVENT_LOG_COMMAND, retval, "EventLog", "Event Log Request")

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
            self._generateBusEventReason(PanelCondition.CHECK_X10_COMMAND, retval, "X10", "X10 Request")
            return retval
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")
        return AlCommandStatus.FAIL_USER_CONFIG_PREVENTED

    async def service_sensor_bypass(self, call):
        """Service call to bypass a sensor in the panel."""
        if await self.check_the_basics(call, "sensor bypass"):
            if await self.is_panel_status_set_to(call, AlPanelStatus.DISARMED, "sensor bypass", AvailableNotifications.BYPASS_PROBLEM):
                isValidPL, code = await self.decode_code_from_call_data(call, "SensorBypass", PanelCondition.CHECK_BYPASS_COMMAND)
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

    def sendCommand(self, message : str, command : AlPanelCommand, code : str):
        if not self.DisableAllCommands:
            codeRequired = self.isCodeRequired()
            if (codeRequired and code is not None) or not codeRequired:
                pcode = self.decode_code_from_dict_or_str(code) if codeRequired or (code is not None and len(code) > 0) else ""
                if self.visonicProtocol is not None:
                    isValidPL, code = self.pmGetPin(code = pcode, forcedKeypad = self.isForceKeypad())

                    if command == AlPanelCommand.DISARM or command == AlPanelCommand.ARM_HOME or command == AlPanelCommand.ARM_AWAY or command == AlPanelCommand.ARM_HOME_INSTANT or command == AlPanelCommand.ARM_AWAY_INSTANT:

                        self.logstate_debug("Send command to Visonic Alarm Panel: %s", command)

                        if isValidPL:
                            if (command == AlPanelCommand.DISARM and self.isRemoteDisarm()) or (
                                command != AlPanelCommand.DISARM and self.isRemoteArm()):
                                retval = self.visonicProtocol.requestPanelCommand(command, code)
                                self._generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, retval, command.name, "Request Arm/Disarm")
                            else:
                                self._generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, AlCommandStatus.FAIL_USER_CONFIG_PREVENTED , command.name, "Request Arm/Disarm")
                        else:
                            self._generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, AlCommandStatus.FAIL_INVALID_CODE, command.name, "Request Arm/Disarm")

                    elif self.visonicProtocol.isPowerMaster() and (command == AlPanelCommand.MUTE or command == AlPanelCommand.TRIGGER or command == AlPanelCommand.FIRE or command == AlPanelCommand.EMERGENCY or command == AlPanelCommand.PANIC):
                        if isValidPL:
                            self.logstate_debug("Send command to Visonic Alarm Panel: %s", command)
                            retval = self.visonicProtocol.requestPanelCommand(command, code)
                            self._generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, retval, command.name, "Request PowerMaster Panel Command")
                        else:
                            self._generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, AlCommandStatus.FAIL_INVALID_CODE, command.name, "Request PowerMaster Panel Command")
                    else:
                        self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, not sent to panel")
                else:
                    self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, not sent to panel")
            else:
                self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, an alarm code is required")
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")

    async def service_panel_command(self, call):
        """Service call to send an arm/disarm command to the panel."""
        if await self.check_the_basics(call, "command"):
            isValidPL, code = await self.decode_code_from_call_data(call, "PanelCommand", PanelCondition.CHECK_ARM_DISARM_COMMAND)
            if isValidPL:
                try:
                    if CONF_COMMAND in call.data:
                        command = call.data[CONF_COMMAND]
                        command_e = AlPanelCommand.value_of(command.upper());
                        self.logstate_debug(f"   Command {command}   {command_e}")
                        self.sendCommand("Alarm Service Call " + str(command_e), command_e, code)
                    else:
                        self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Attempt to send command to panel {self.getPanelID()}, command not set for entity {eid}")
                except Exception as ex:
                    self.logstate_warning(f"Not making command request. Exception {ex}")

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

    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================
    # ======== Functions below this make the connection to the panel and manage restarts etc ================
    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================

    def _createSocketConnection(self, address, port):
        try:
            #self.logstate_debug(f"Setting TCP socket Options {address} {port}")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
            
            return sock
            
        except socket.error as err:
            self.logstate_debug("Setting TCP socket Options Exception {0}".format(err))
            if sock is not None:
                sock.close()

        return None

    # Create a connection using asyncio using an ip and port
    async def async_create_tcp_visonic_connection(self, address, port, panelConfig : PanelConfig = None, powerlink_connected = False, powerlink_port = "30001", loop=None):
        """Create Visonic manager class, returns tcp transport coroutine."""
        loop = loop if loop else asyncio.get_event_loop()
        
        try:
            sock = self._createSocketConnection(address, int(port))
            if sock is not None:
                # Create the Protocol Handler for the Panel, also handle Powerlink connection inside this protocol handler
                self.vp = ClientVisonicProtocol(panelConfig=panelConfig, panel_id=self.panelident, loop=loop)

                # create the connection to the panel as an asyncio protocol handler and then set it up in a task
                coro = loop.create_connection(self.vp, sock=sock)

                #self.logstate_debug("The coro type is " + str(type(coro)) + "   with value " + str(coro))
                # Wrap the coroutine in a task to add it to the asyncio loop
                visonicTask = loop.create_task(coro)

                return visonicTask, self.vp

        except Exception as ex:
            pass
            
        return None, None

    def tellemaboutme(self, thisisme):
        self.vp = thisisme

    # Create a connection using asyncio through a linux port (usb or rs232)
    async def async_create_usb_visonic_connection(self, path, baud="9600", panelConfig : PanelConfig = None, loop=None):
        """Create Visonic manager class, returns rs232 transport coroutine."""
        from serial_asyncio import create_serial_connection

        loop=loop if loop else asyncio.get_event_loop()

        self.logstate_debug("Setting USB Options")
        
        # use default protocol if not specified
        protocol = partial(
            ClientVisonicProtocol,
            client=self,
            panelConfig=panelConfig,
            panel_id=self.panelident, 
            loop=loop,
        )

        # setup serial connection
        path = path
        baud = int(baud)
        try:
            self.vp = None
            
            # create the connection to the panel as an asyncio protocol handler and then set it up in a task
            conn = create_serial_connection(loop, protocol, path, baud)
            #self.logstate_debug("The coro type is " + str(type(conn)) + "   with value " + str(conn))
            visonicTask = loop.create_task(conn)
            ctr = 0
            while self.vp is None and ctr < 20:     # 20 with a sleep of 0.1 is approx 2 seconds. Wait up to 2 seconds for this to start.
                await asyncio.sleep(0.1)            # This should only happen once while the Protocol Handler starts up and calls tellemaboutme to set self.vp
                ctr = ctr + 1
            if self.vp is not None:
                return visonicTask, self.vp
        except Exception as ex:
            self.logstate_debug(f"Setting USB Options Exception {ex}")
        return None, None

    async def connect_to_alarm(self) -> bool:
        """Create the connection to the alarm panel."""
        # Is the system already running and connected
        if self.SystemStarted:
            return False

        self.logstate_debug("connect_to_alarm self.config = %s", self.config)

        # Get Visonic specific configuration.
        device_type = self.config.get(CONF_DEVICE_TYPE)
        
        self.logstate_debug("Connection Device Type is %s", device_type)

        # update config parameters (local in hass[DOMAIN] mainly)
        self.updateConfig()

        self.visonicTask = None
        self.visonicProtocol = None

        # Connect in the way defined by the user in the config file, ethernet or usb
        if device_type == "ethernet":
            host = self.config.get(CONF_HOST)
            port = self.config.get(CONF_PORT)
            (
                self.visonicTask,
                self.visonicProtocol,
            ) = await self.async_create_tcp_visonic_connection(
                address=host,
                port=port,
                panelConfig=self.getConfigData(),
                loop=self.hass.loop,
            )
            
        elif device_type == "usb":
            path = self.config.get(CONF_PATH)
            #baud = self.config.get(CONF_DEVICE_BAUD)
            (
                self.visonicTask,
                self.visonicProtocol,
            ) = await self.async_create_usb_visonic_connection(
                path=path,
                baud=self.baud_rate,
                panelConfig=self.getConfigData(),
                loop=self.hass.loop,
            )

        if self.visonicTask is not None and self.visonicProtocol is not None:
            # Connection to the panel has been initially successful
            #self.visonicProtocol.onPanelError(self.generate_ha_error)
            
            self.visonicProtocol.onPanelChange(self.onPanelChangeHandler)
            self.visonicProtocol.onPanelLog(self.process_panel_event_log)
            self.visonicProtocol.onDisconnect(self.onDisconnect)
            self.visonicProtocol.onNewSensor(self.onNewSensor)
            self.visonicProtocol.onNewSwitch(self.onNewSwitch)
            # Record that we have started the system
            self.SystemStarted = True
            return True

        if self.visonicTask is not None:
            self.logstate_debug("........... Closing down Current Task")
            self.visonicTask.cancel()

        if self.visonicProtocol is not None:
            self.logstate_debug("........... Shutting Down Protocol")
            self.visonicProtocol.shutdownOperation()
        
        self._initialise()
        
        return False

    async def service_panel_stop(self) -> bool:
        """Service call to stop the connection."""
        if not self.SystemStarted:
            self.logstate_debug("Request to Stop the HA alarm_control_panel and it is already stopped")
            return True

        # stop the usb/ethernet comms with the panel
        await self.service_comms_stop()

        self.logstate_debug(f"Unloading platforms {self.loaded_platforms=}   Entry id={self.entry.entry_id} ")
        #self.printAllEntities()
        unload_ok = await self.hass.config_entries.async_unload_platforms(self.entry, self.loaded_platforms)
        self.logstate_debug(f"Unloading complete {unload_ok=}")
        #self.printAllEntities()
        
        # cancel the task from within HA
        if self.visonicTask is not None:
            self.logstate_debug("........... Closing down Current Task")
            self.visonicTask.cancel()
            await asyncio.sleep(2.0)
            if self.visonicTask is not None and self.visonicTask.done():
                self.logstate_debug("........... Current Task Done")
            else:
                self.logstate_debug("........... Current Task Not Done")
        else:
            self.logstate_debug("........... Current Task not set")
        
        self._initialise()

        return unload_ok
        
    async def service_panel_start(self, force : bool):
        """Service call to start the connection."""
        # force is set True on initial connection so the self.totalAttempts (for the number of reconnections) can be set to 0. 
        #    It is forced to try at least once on integration start (or reload)
        if self.SystemStarted:
            self.logstate_warning("Request to Start and the integraion is already running and connected")
            return

        #self.logstate_debug(f"service_panel_start, connecting   force = {force}")

        attemptCounter = 0
        #self.logstate_debug(f"     {attemptCounter} of {self.totalAttempts}")
        while force or attemptCounter < self.totalAttempts:
            self.logstate_debug("........... connection attempt {0} of {1}".format(attemptCounter + 1, self.totalAttempts))
            if await self.connect_to_alarm():
                self.logstate_debug("........... connection made")
                self._fireHAEvent(name = ALARM_PANEL_CHANGE_EVENT, event_id = PanelCondition.CONNECTION, datadictionary = {"state": "connected", "attempt": attemptCounter + 1})
                return
            self._fireHAEvent(name = ALARM_PANEL_CHANGE_EVENT, event_id = PanelCondition.CONNECTION, datadictionary = {"state": "failedattempt", "attempt": attemptCounter + 1})
            attemptCounter = attemptCounter + 1
            force = False
            if attemptCounter < self.totalAttempts:
                self.logstate_debug("........... connection attempt delay {0} seconds".format(self.delayBetweenAttempts))
                await asyncio.sleep(self.delayBetweenAttempts)

        self.createNotification(
            AvailableNotifications.CONNECTION_PROBLEM,
            f"Failed to connect into Visonic Alarm Panel {self.getPanelID()}. Check Your Network and the Configuration Settings."
        )
        #self.logstate_debug("Giving up on trying to connect, sorry")

    async def service_comms_stop(self):
        """Service call to close down the current serial connection, we need to reset the whole connection."""
        if not self.SystemStarted:
            self.logstate_debug("Request to Stop the Comms and it is already stopped")
            return

        # Try to get the asyncio Coroutine within the Task to shutdown the serial link connection properly
        if self.visonicProtocol is not None:
            self.visonicProtocol.shutdownOperation()
        self.visonicProtocol = None
        await asyncio.sleep(0.5)
        # not a mistake, wait a bit longer to make sure it's closed as we get no feedback (we only get the fact that the queue is empty)

    async def service_panel_reconnect(self, call):
        """Service call to re-connect the connection."""
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration not connected to panel {self.getPanelID()}.")

        if call.context.user_id:
            #self.logstate_debug(f"Checking user information for permissions: {call.context.user_id}")
            # Check security permissions (that this user has access to the alarm panel entity)
            await self._checkUserPermission(call, POLICY_CONTROL, Platform.ALARM_CONTROL_PANEL + "." + slugify(self.getAlarmPanelUniqueIdent()))

        self.logstate_debug("User has requested visonic panel reconnection")
        await self.service_panel_stop()
        await asyncio.sleep(3.0)
        await self.service_panel_start(False)

    async def disconnect_callback_async(self):
        """Service call to disconnect."""
        self.logstate_debug("........... terminating connection")
        await asyncio.sleep(1.0)
        await self.service_panel_stop()
        await asyncio.sleep(3.0)
        #self.logstate_debug("........... attempting reconnection")
        await self.service_panel_start(False)

    async def connect(self):
        """Connect to the alarm panel using the pyvisonic library."""
        try:
            if CONF_DEVICE_BAUD in self.config:
                self.baud_rate = self.config.get(CONF_DEVICE_BAUD)

            if CONF_RETRY_CONNECTION_DELAY in self.config:
                self.delayBetweenAttempts = float(self.config.get(CONF_RETRY_CONNECTION_DELAY))   # seconds

            if CONF_RETRY_CONNECTION_COUNT in self.config:
                self.totalAttempts = int(self.config.get(CONF_RETRY_CONNECTION_COUNT))

            #self.logstate_info("Client connecting.....")
            # Establish a callback to stop the component when the stop event occurs
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self.stop_subscription
            )
            #self.logstate_info("Client connecting..........")
            await self.service_panel_start(True)

        except (ConnectTimeout, HTTPError) as ex:
            createNotification(
                AvailableNotifications.CONNECTION_PROBLEM,
                "Visonic Panel Connection Error: {}<br />"
                "You will need to restart hass after fixing."
                "".format(ex))
        #return False
