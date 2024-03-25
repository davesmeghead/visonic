"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""
import asyncio
from collections import defaultdict
import logging
import traceback
from typing import Callable, List, Union, Any
import re
import socket
from datetime import datetime, timedelta
from jinja2 import Environment, FileSystemLoader
from .pyconst import AlEnum, AlTransport, PanelConfig, AlConfiguration, AlPanelMode, AlPanelCommand, AlPanelStatus, AlTroubleType, AlAlarmType, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlSensorDevice, AlLogPanelEvent, AlSensorType, AlSwitchDevice
from .pyvisonic import VisonicProtocol
from functools import partial

from enum import IntEnum
from requests import ConnectTimeout, HTTPError
import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.helpers import entity_platform
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_CODE,
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PATH,
    CONF_PORT,
    CONF_USERNAME, 
    CONF_PASSWORD,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.helpers import config_validation as cv
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser

from .const import (
    available_emulation_modes,
    ALARM_PANEL_CHANGE_EVENT,
    ALARM_PANEL_ENTITY,
    ALARM_PANEL_LOG_FILE_COMPLETE,
    ALARM_PANEL_LOG_FILE_ENTRY,
    ALARM_PANEL_COMMAND,
    ALARM_PANEL_EVENTLOG,
    ALARM_PANEL_RECONNECT,
    ATTR_BYPASS,
    VISONIC_UNIQUE_NAME,
    CONF_EXCLUDE_SENSOR,
    CONF_EXCLUDE_X10,
    CONF_ENABLE_REMOTE_ARM,
    CONF_ENABLE_REMOTE_DISARM,
    CONF_ENABLE_SENSOR_BYPASS,
    CONF_ARM_CODE_AUTO,
    CONF_OVERRIDE_CODE,
    CONF_FORCE_KEYPAD,
    CONF_ARM_HOME_ENABLED,
    CONF_ARM_NIGHT_ENABLED,
    CONF_INSTANT_ARM_AWAY,
    CONF_INSTANT_ARM_HOME,
    CONF_AUTO_SYNC_TIME,
    CONF_EEPROM_ATTRIBUTES,
    CONF_DEVICE_BAUD,
    CONF_DEVICE_TYPE,
    CONF_DOWNLOAD_CODE,
    CONF_FORCE_AUTOENROLL,
    CONF_EMULATION_MODE,
    CONF_LANGUAGE,
    CONF_MOTION_OFF_DELAY,
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
    DOMAIN,
    DOMAINDATA,
    NOTIFICATION_ID,
    NOTIFICATION_TITLE,
    #VISONIC_UPDATE_STATE_DISPATCHER,
    CONF_ALARM_NOTIFICATIONS,
    PANEL_ATTRIBUTE_NAME,
    DEVICE_ATTRIBUTE_NAME,
    AvailableNotifications,
    PIN_REGEX,
    BINARY_SENSOR_STR,
    IMAGE_SENSOR_STR,
    MONITOR_SENSOR_STR,
    SWITCH_STR,
    SELECT_STR,
)

class PanelCondition(IntEnum):
    CHECK_ARM_DISARM_COMMAND = 11
    CHECK_BYPASS_COMMAND = 12
    CHECK_EVENT_LOG_COMMAND = 13
    CHECK_X10_COMMAND = 14

CLIENT_VERSION = "0.9.1.1"

MAX_CLIENT_LOG_ENTRIES = 100

_LOGGER = logging.getLogger(__name__)

ActionList = (
   "connection", "zoneupdate", "panelupdate", "sirenactive", "panelreset", "pinrejected", "paneltamper", "timeoutdownload", 
   "timeoutwaiting", "timeoutactive", "nopaneldata", "armdisarm", "bypass", "eventlog", "x10", "", "", ""
)

class MyTransport(AlTransport):
 
    def __init__(self, t):
        self.transport = t
    
    def write(self, b : bytearray):
        self.transport.write(b)

    def close(self):
        self.transport.close()

# This class joins the Protocol data stream to the visonic protocol handler.
#    transport needs to have 2 functions:   write(bytearray)  and  close()
class ClientVisonicProtocol(asyncio.Protocol, VisonicProtocol):

    def __init__(self, client = None, *args, **kwargs):
        _LOGGER.debug("Initialising ClientVisonicProtocol A")
        super().__init__(*args, **kwargs)
        _LOGGER.debug("Initialising ClientVisonicProtocol B")
        if client is not None:
            _LOGGER.debug("Initialising ClientVisonicProtocol C")
            client.tellemaboutme(self)
            _LOGGER.debug("Initialising ClientVisonicProtocol D")
        _LOGGER.debug("Initialising ClientVisonicProtocol E")    

    def data_received(self, data):
        super().vp_data_received(data)

    def connection_made(self, transport):
        self.trans = MyTransport(t=transport)
        super().vp_connection_made(self.trans)

    def connection_lost(self, exc):
        super().vp_connection_lost(exc)

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
        self.createdAlarmPanel = False
        # Get the user defined config
        self.config = cf.copy()
        
        self.sensor_task = None
        self.select_task = None
        self.switch_task = None
        
        self.onChangeHandler = []

        self.sensor_list = list()
        self.x10_list = list()
        
        self.DisableAllCommands = False

        self.strlog = []
        self.logstate_debug(f"init panel {str(panelident)}  language {str(self.hass.config.language)}   self.config = {self.config}")
        
        self.panelident = panelident

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

        # panel connection
        self.panel_exception_counter = 0
        self.visonicTask = None
        self.visonicProtocol : AlPanelInterface = None
        self.SystemStarted = False

        # variables for creating the event log for csv and xml
        self.csvdata = None
        self.templatedata = None

        self.logstate_info(f"Exclude sensor list = {self.exclude_sensor_list}     Exclude x10 list = {self.exclude_x10_list}")
        
    def logstate_debug(self, msg, *args, **kwargs):
        _LOGGER.debug((msg % args % kwargs))
        self.strlog.append(str(datetime.utcnow()) + "   " + (msg % args % kwargs))
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)
            
    def logstate_info(self, msg, *args, **kwargs):
        _LOGGER.info((msg % args % kwargs))
        self.strlog.append(str(datetime.utcnow()) + " I " + (msg % args % kwargs))
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)

    def logstate_warning(self, msg, *args, **kwargs):
        _LOGGER.warning((msg % args % kwargs))
        self.strlog.append(str(datetime.utcnow()) + " W " + (msg % args % kwargs))
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
        if condition == AvailableNotifications.ALWAYS or condition in notification_config:
            # Create an info entry in the log file and an HA notification
            self.logstate_info(f"HA Notification: {message}")
            self.hass.components.persistent_notification.create(
                message, title=NOTIFICATION_TITLE, notification_id=NOTIFICATION_ID
            )
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

    def getPanelStatusDict(self) -> dict:
        """Get the panel status."""
        if self.visonicProtocol is not None:
            pd = self.visonicProtocol.getPanelStatusDict()
            #self.logstate_debug("Client Dict {0}".format(pd))
            pd["Client Version"] = CLIENT_VERSION
            return pd
        return {}

    def hasValidOverrideCode(self) -> bool:
        """Is there a valid override code."""
        tmpOCode = self.config.get(CONF_OVERRIDE_CODE, "")
        if type(tmpOCode) == str and len(tmpOCode) == 4 and tmpOCode.isdigit():
            return True
        return False

    def process_command(self, command: str):
        """Convert object into dict to maintain backward compatibility."""
        if self.visonicProtocol is not None:
            self.logstate_debug("Client command processing %s", command )
            self.visonicProtocol.process_command(command)
        else:
            self.logstate_warning("Client command processing not defined - is there a panel connection?")

    def process_panel_event_log(self, event_log_entry: AlLogPanelEvent):
        """Process a sequence of panel log events."""
        reverse = self.toBool(self.config.get(CONF_LOG_REVERSE))
        total = 0
        if event_log_entry.total is not None and self.config.get(CONF_LOG_MAX_ENTRIES) is not None:
            total = min(event_log_entry.total, self.config.get(CONF_LOG_MAX_ENTRIES))
        elif event_log_entry.total is not None:
            total = event_log_entry.total
        elif self.config.get(CONF_LOG_MAX_ENTRIES) is not None:
            total = self.config.get(CONF_LOG_MAX_ENTRIES)
        current = event_log_entry.current  # only used for output and not for logic
        if reverse:
            current = total + 1 - event_log_entry.current
        # Fire event visonic_alarm_panel_event_log
        if (
            self.toBool(self.config.get(CONF_LOG_EVENT))
            and event_log_entry.current <= total
        ):        
            self.hass.bus.fire(
                ALARM_PANEL_LOG_FILE_ENTRY,
                {
                    PANEL_ATTRIBUTE_NAME: self.getPanelID(),
                    "current": current,
                    "total": total,
                    "date": event_log_entry.date,
                    "time": event_log_entry.time,
                    "partition": event_log_entry.partition,
                    "zone": event_log_entry.zone,
                    "event": event_log_entry.event,
                },
            )
            
        #self.logstate_debug("Panel Event Log - fired Single Item event")
        # Write out to an xml file
        if event_log_entry.current == 1:
            self.templatedata = []
            self.csvdata = ""

        if self.csvdata is not None:
            #self.logstate_debug("Panel Event Log - Saving csv data")
            if reverse:
                self.csvdata = (
                    "{0}, {1}, {2}, {3}, {4}, {5}, {6}\n".format(
                        current,
                        total,
                        event_log_entry.partition,
                        event_log_entry.date,
                        event_log_entry.time,
                        event_log_entry.zone,
                        event_log_entry.event,
                    )
                    + self.csvdata
                )
            else:
                self.csvdata = (
                    self.csvdata
                    + "{0}, {1}, {2}, {3}, {4}, {5}, {6}\n".format(
                        current,
                        total,
                        event_log_entry.partition,
                        event_log_entry.date,
                        event_log_entry.time,
                        event_log_entry.zone,
                        event_log_entry.event,
                    )
                )

            #self.logstate_debug("Panel Event Log - Saving xml data")
            datadict = {
                "partition": "{0}".format(event_log_entry.partition),
                "current": "{0}".format(current),
                "date": "{0}".format(event_log_entry.date),
                "time": "{0}".format(event_log_entry.time),
                "zone": "{0}".format(event_log_entry.zone),
                "event": "{0}".format(event_log_entry.event),
            }

            self.templatedata.append(datadict)

            if event_log_entry.current == total:
                self.logstate_debug(
                    "Panel Event Log - Received last entry  reverse=%s  xmlfilenamelen=%s csvfilenamelen=%s",
                    str(reverse),
                    len(self.config.get(CONF_LOG_XML_FN)),
                    len(self.config.get(CONF_LOG_CSV_FN)),
                )
                # create a new XML file with the results
                if len(self.config.get(CONF_LOG_XML_FN)) > 0:
                    self.logstate_debug(
                        "Panel Event Log - Starting xml save filename %s",
                        str(self.config.get(CONF_LOG_XML_FN)),
                    )
                    if reverse:
                        self.templatedata.reverse()
                    try:
                        self.logstate_debug(
                            "Panel Event Log - Setting up xml file loader - path = %s",
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
                        self.logstate_debug(
                            "Panel Event Log - Setting up xml - getting the template"
                        )
                        template = env.get_template("visonic_template.xml")
                        output = template.render(
                            entries=self.templatedata,
                            total=total,
                            available="{0}".format(event_log_entry.total),
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

                self.logstate_debug("Panel Event Log - CSV File Creation")

                if len(self.config.get(CONF_LOG_CSV_FN)) > 0:
                    try:
                        self.logstate_debug(
                            "Panel Event Log - Starting csv save filename %s",
                            self.config.get(CONF_LOG_CSV_FN),
                        )
                        if self.toBool(self.config.get(CONF_LOG_CSV_TITLE)):
                            self.logstate_debug("Panel Event Log - Adding header to string")
                            self.csvdata = (
                                "current, total, partition, date, time, zone, event\n"
                                + self.csvdata
                            )
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

                self.logstate_debug("Panel Event Log - Clear data ready for next time")
                self.csvdata = None
                if self.toBool(self.config.get(CONF_LOG_DONE)):
                    self.logstate_debug("Panel Event Log - Firing Completion Event")
                    self.hass.bus.fire(
                        ALARM_PANEL_LOG_FILE_COMPLETE,
                        {PANEL_ATTRIBUTE_NAME: self.getPanelID(), "total": total, "available": event_log_entry.total},
                    )
                self.logstate_debug("Panel Event Log - Complete")

    def onNewSwitch(self, dev: AlSwitchDevice): 
        """Process a new x10."""
        # Check to ensure variables are set correctly
        if self.hass is None:
            self.logstate_warning("Attempt to add X10 switch when hass is undefined")
            return
        if dev is None:
            self.logstate_warning("Attempt to add X10 switch when sensor is undefined")
            return
        self.logstate_debug("VS: X10 Switch list %s", dev)
        if dev.isEnabled() and dev.getDeviceID() not in self.exclude_x10_list:
            dev.onChange(self.onSwitchChange)
            if dev not in self.hass.data[DOMAIN][self.entry.entry_id][SWITCH_STR]:
                self.hass.data[DOMAIN][self.entry.entry_id][SWITCH_STR].append(dev)
                self.x10_list.append(dev)
                if self.switch_task is None or self.switch_task.done():
                     self.switch_task = self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, SWITCH_STR))
                #self.logstate_debug(f"Visonic: {len(self.hass.data[DOMAIN][self.entry.entry_id][SWITCH_STR])} switches")
            else:
                self.logstate_debug(f"X10 Device {dev.getDeviceID()} already in the list")

    def setupAlarmPanel(self):
        if self.DisableAllCommands:
            self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, MONITOR_SENSOR_STR))
        else:
            self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, ALARM_PANEL_ENTITY))

    def onNewSensor(self, sensor: AlSensorDevice):
        """Process a new sensor."""
        from .binary_sensor import VisonicBinarySensor
        from .select import VisonicSelect
        
        # Check to ensure variables are set correctly
        if self.hass is None:
            self.logstate_warning("Visonic attempt to add sensor when hass is undefined")
            return
        if not self.createdAlarmPanel:
            self.createdAlarmPanel = True
            self.setupAlarmPanel()
            
        if sensor is None:
            self.logstate_warning("Visonic attempt to add sensor when sensor is undefined")
            return
        if sensor.getDeviceID() is None:
            self.logstate_warning("Sensor callback but Sensor Device ID is None")
            return
        if sensor.getDeviceID() not in self.exclude_sensor_list:
            sensor.onChange(self.onSensorChange)
            if sensor not in self.sensor_list:
                self.sensor_list.append(sensor)
                #self.logstate_warning(f"Adding Sensor to list {sensor}")

                platform_bin_sen = None
                platform_select = None
                
                platforms = entity_platform.async_get_platforms(self.hass, "visonic")
                for p in platforms:
                    if p.config_entry.entry_id == self.entry.entry_id:
                        #_LOGGER.debug(f"onNewSensor platform is {p}")
                        if p.domain == BINARY_SENSOR_STR:
                            platform_bin_sen = p
                        if p.domain == SELECT_STR:
                            platform_select = p
                
                #if self.sensor_task is not None and self.sensor_task.done():
                if platform_bin_sen is not None and platform_select is not None:
                    # We have already called for the config to set up the first set of sensors so these are manually added on discovery
                    self.hass.async_create_task(platform_bin_sen.async_add_entities([ VisonicBinarySensor(self.hass, self, sensor, self.entry) ], False))
                    if not self.DisableAllCommands: # If all commands are disabled then the use is not able to call the service to retrieve an image
                        self.hass.async_create_task(platform_select.async_add_entities([ VisonicSelect(self.hass, self, sensor) ], False))
                    
                else:
                    # This triggers the platform config to be setup with the initial set of sensors
                    self.hass.data[DOMAIN][self.entry.entry_id][BINARY_SENSOR_STR].append(sensor)
                    self.hass.data[DOMAIN][self.entry.entry_id][SELECT_STR].append(sensor)
         
                    if self.sensor_task is None or self.sensor_task.done():
                        self.sensor_task = self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, BINARY_SENSOR_STR))
                    if self.select_task is None or self.select_task.done():
                        if not self.DisableAllCommands:
                            self.select_task = self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, SELECT_STR))
            else:
                self.logstate_debug(f"Sensor {sensor.getDeviceID()} already in the lists")
        else:
            self.logstate_debug(f"Sensor {sensor.getDeviceID()} in exclusion list")

    def fireHAEvent(self, ev: dict):
        ev[PANEL_ATTRIBUTE_NAME] = self.getPanelID()
        self.logstate_debug("Client: Sending HA Event {0}".format(ev))
        #self.logstate_debug("Firing HA event, panel={0}  event={1}".format(self.getPanelID(),ev)
        self.hass.bus.fire(ALARM_PANEL_CHANGE_EVENT, ev)

    def onChange(self, fn : Callable):
        self.onChangeHandler.append(fn)

    def commonBit(self, event_id: AlEnum, datadictionary: dict):
        # Check to ensure variables are set correctly
        if self.hass is None:
            self.logstate_warning("Attempt to generate HA event when hass is undefined")
            return
        if event_id is None:
            self.logstate_warning("Attempt to generate HA event when sensor is undefined")
            return
        if not self.createdAlarmPanel:
            self.createdAlarmPanel = True
            self.setupAlarmPanel()

        # The event_id is in the range 0 to 16 inclusive
        #   When it is set to 0, any of the possible changes have been made in the sensors/X10 devices
        #   So use any value of event_id to fire an HA event to get the sensors to update themselves
        for cb in self.onChangeHandler:
            if self.hass is not None:
                cb(event_id, datadictionary)
            else:
                self.logstate_debug("Client: its None")
            
        # Send an event on the event bus for conditions 1 to 14.  Ignore 0 as this is used for any sensor change.
        #  0 is just used for the dispatcher above so the frontend is updated, no HA event is fired
        #  15 is something other than the pin code has been rejected by the panel (see 5 above)
        #  16 is download success
        tmp = int(event_id)

        if 2 <= tmp <= 14: # do not send 0 or 15 or 16 as an HA event   Also do not send zone updates (1)
            a = {}
            a["condition"] = tmp
            a["action"] = ActionList[tmp]
            b = { }
            if datadictionary is not None:
                b = datadictionary.copy()
            dd = {**a, **b}
            self.fireHAEvent( dd )

    def onSensorChange(self, sensor : AlSensorDevice, s : AlSensorCondition):
        #_LOGGER.debug("onSensorChange {0} {1}".format(s.name, sensor) )
        datadict = {}
        datadict["zone"] = sensor.getDeviceID()
        datadict["event"] = str(s)
        if s != AlSensorCondition.RESET:
            self.commonBit(AlCondition.ZONE_UPDATE, datadict)
    
    def onSwitchChange(self, switch : AlSwitchDevice):
        #_LOGGER.debug("onSwitchChange {0}".format(switch))
        pass

    # This can be called from this module but it is also the callback handler for the connection
    def onPanelChangeHandler(self, event_id: AlCondition, data : dict = None):
        """Generate HA Bus Event."""
        if data is not None:
            self.commonBit(event_id, data)
        elif self.visonicProtocol is not None:
            self.commonBit(event_id, self.visonicProtocol.setLastEventData())
        else:
            self.commonBit(event_id, {})

        if event_id == AlCondition.DOWNLOAD_SUCCESS:        # download success        
            # Update the friendly name of the control flow
            pm = self.getPanelModel()
            s = "Panel " + str(self.getPanelID()) + " (" + ("Unknown" if pm is None else pm) + ")"
            # update the title
            self.hass.config_entries.async_update_entry(self.entry, title=s)

        if event_id == AlCondition.PANEL_UPDATE_ALARM_ACTIVE: 
            self.createNotification(AvailableNotifications.SIREN, "Siren is Sounding, Alarm has been Activated" )
        elif event_id == AlCondition.PANEL_RESET:
            self.createNotification(AvailableNotifications.RESET, "The Panel has been Reset" )
        elif event_id == AlCondition.PANEL_TAMPER_ALARM:
            self.createNotification(AvailableNotifications.TAMPER, "The Panel has been Tampered" )
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
        self.CompleteReadOnly = v == available_emulation_modes[3]

        if self.CompleteReadOnly:
            self.DisableAllCommands = True
        if self.DisableAllCommands:
            self.ForceStandardMode = True
        # By the time we get here there are 4 combinations of self.CompleteReadOnly, self.DisableAllCommands and self.ForceStandardMode
        #     All 3 are False --> Try to get to Powerlink 
        #     self.ForceStandardMode is True --> Force Standard Mode, the panel can still be armed and disarmed
        #     self.ForceStandardMode and self.DisableAllCommands are True --> The integration interacts with the panel but commands such as arm/disarm/log/bypass are not allowed
        #     All 3 are True  --> Full readonly, no data sent to the panel
        # The 2 if statements above ensure these are the only supported combinations.

        self.logstate_debug(f"Emulation Mode {self.config.get(CONF_EMULATION_MODE)}   so setting    ForceStandard to {self.ForceStandardMode}     DisableAllCommands to {self.DisableAllCommands}     CompleteReadOnly to {self.CompleteReadOnly}")

        return {
            AlConfiguration.DownloadCode: self.config.get(CONF_DOWNLOAD_CODE, ""),
            AlConfiguration.ForceStandard: self.ForceStandardMode,
            AlConfiguration.DisableAllCommands: self.DisableAllCommands,
            AlConfiguration.CompleteReadOnly: self.CompleteReadOnly,
            AlConfiguration.AutoEnroll: self.toBool(
                self.config.get(CONF_FORCE_AUTOENROLL, True)
            ),
            AlConfiguration.AutoSyncTime: self.toBool(
                self.config.get(CONF_AUTO_SYNC_TIME, True)
            ),
            AlConfiguration.PluginLanguage: self.config.get(CONF_LANGUAGE, "EN"),
            AlConfiguration.MotionOffDelay: self.config.get(CONF_MOTION_OFF_DELAY, 120),
            AlConfiguration.SirenTriggerList: self.config.get(
                CONF_SIREN_SOUNDING, ["Intruder"]
            ),
            AlConfiguration.EEPROMAttributes: self.toBool(
                self.config.get(CONF_EEPROM_ATTRIBUTES, False)
            ),
        }

    async def checkUserPermission(self, call, perm, entity):
        user = await self.hass.auth.async_get_user(call.context.user_id)

        if user is None:
            raise UnknownUser(
                context=call.context,
                entity_id=entity,
                permission=perm,
            )

        # self.logstate_debug("user={0}".format(user))
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

    # Create a connection using asyncio using an ip and port
    async def async_create_tcp_visonic_connection(self, address, port, panelConfig : PanelConfig = None, loop=None):
        """Create Visonic manager class, returns tcp transport coroutine."""
        loop = loop if loop else asyncio.get_event_loop()
        
        #self.logstate_debug("Setting address and port")
        address = address
        port = int(port)

        sock = None
        try:
            self.logstate_debug("Setting TCP socket Options")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setblocking(1)  # Set blocking to on, this is the default but just make sure
            sock.settimeout(1.0)  # set timeout to 1 second to flush the receive buffer
            sock.connect((address, port))

            # Flush the buffer, receive any data and dump it
            try:
                dummy = sock.recv(10000)  # try to receive 100 bytes
                self.logstate_debug("Buffer Flushed and Received some data!")
            except socket.timeout:  # fail after 1 second of no activity
                #self.logstate_debug("Buffer Flushed and Didn't receive data! [Timeout]")
                pass

            # set the timeout to infinite
            sock.settimeout(None)

            vp = ClientVisonicProtocol(panelConfig=panelConfig, loop=loop)

            # create the connection to the panel as an asyncio protocol handler and then set it up in a task
            coro = loop.create_connection(vp, sock=sock)

            self.logstate_debug("The coro type is " + str(type(coro)) + "   with value " + str(coro))
            visonicTask = loop.create_task(coro)

            return visonicTask, vp

        except socket.error as err:
            #err = _
            self.logstate_debug("Setting TCP socket Options Exception {0}".format(err))
            if sock is not None:
                sock.close()
        return None, None

    def tellemaboutme(self, thisisme):
        self.logstate_debug(f"Here This is me {thisisme}")
        self.vp = thisisme

    # Create a connection using asyncio through a linux port (usb or rs232)
    async def async_create_usb_visonic_connection(self, path, baud="9600", panelConfig : PanelConfig = None, loop=None):
        """Create Visonic manager class, returns rs232 transport coroutine."""
        from serial_asyncio import create_serial_connection

        self.logstate_debug("Here AA")
        loop=loop if loop else asyncio.get_event_loop()

        self.logstate_debug("Setting USB Options")
        
        # use default protocol if not specified
        protocol = partial(
            ClientVisonicProtocol,
            client=self,
            panelConfig=panelConfig,
            loop=loop,
        )

        self.logstate_debug("Here BB")

        # setup serial connection
        path = path
        baud = int(baud)
        try:
            self.logstate_debug(f"Here CC {path} {baud}")
            self.vp = None
            # create the connection to the panel as an asyncio protocol handler and then set it up in a task
            conn = create_serial_connection(loop, protocol, path, baud)
            self.logstate_debug("The coro type is " + str(type(conn)) + "   with value " + str(conn))
            visonicTask = loop.create_task(conn)
            self.logstate_debug("Here DD")
            ctr = 0
            while self.vp is None and ctr < 20:     # 20 with a sleep of 0.1 is approx 2 seconds. Wait up to 2 seconds for this to start.
                self.logstate_debug("Here EE")
                await asyncio.sleep(0.1)            # This should only happen once while the Protocol Handler starts up and calls tellemaboutme to set self.vp
                ctr = ctr + 1
            if self.vp is not None:
                self.logstate_debug("Here FF")
                return visonicTask, self.vp
            self.logstate_debug("Here GG")
        except Exception as ex:
            self.logstate_debug(f"Setting USB Options Exception {ex}")
        return None, None

    async def connect_to_alarm(self) -> bool:
        """Create the connection to the alarm panel."""
        # Is the system already running and connected
        if self.SystemStarted:
            return False

        # set up config parameters in the visonic library
        self.hass.data[DOMAIN][DOMAINDATA][self.getEntryID()]["Exception Count"] = self.panel_exception_counter

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
            baud = self.config.get(CONF_DEVICE_BAUD)
            (
                self.visonicTask,
                self.visonicProtocol,
            ) = await self.async_create_usb_visonic_connection(
                path=path,
                baud=baud,
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

        self.visonicTask = None
        self.visonicProtocol = None
        self.SystemStarted = False
        self.createdAlarmPanel = False
        
        return False

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

    async def service_panel_stop(self):
        """Service call to stop the connection."""
        if not self.SystemStarted:
            self.logstate_debug("Request to Stop the HA alarm_control_panel and it is already stopped")
            return

        # stop the usb/ethernet comms with the panel
        await self.service_comms_stop()

        if len(self.sensor_list) > 0:
            self.logstate_debug("Unloading Sensors")
            # unload the select and sensors
            if not self.DisableAllCommands:
                await self.hass.config_entries.async_forward_entry_unload(self.entry, SELECT_STR)
            await self.hass.config_entries.async_forward_entry_unload(self.entry, BINARY_SENSOR_STR)
            #await self.hass.config_entries.async_forward_entry_unload(self.entry, IMAGE_SENSOR_STR)
        else:
            self.logstate_debug("No Sensors to Unload")
        
        if len(self.x10_list) > 0:
            self.logstate_debug("Unloading Switches")
            # unload the switches
            await self.hass.config_entries.async_forward_entry_unload(self.entry, SWITCH_STR)
        else:
            self.logstate_debug("No Switches to Unload")
        
        self.sensor_list = list()
        self.x10_list = list()
        
        if self.DisableAllCommands:
            self.logstate_debug("Unloading Alarm Panel Sensor")
            # unload the alarm panel
            await self.hass.config_entries.async_forward_entry_unload(self.entry, MONITOR_SENSOR_STR)
        else:
            self.logstate_debug("Unloading Alarm Control Panel")
            # unload the alarm panel
            await self.hass.config_entries.async_forward_entry_unload(self.entry, ALARM_PANEL_ENTITY)

        # cancel the task from within HA
        if self.visonicTask is not None:
            self.logstate_debug("........... Closing down Current Task")
            self.visonicTask.cancel()
            await asyncio.sleep(2.0)
            if self.visonicTask.done():
                self.logstate_debug("........... Current Task Done")
            else:
                self.logstate_debug("........... Current Task Not Done")
        else:
            self.logstate_debug("........... Current Task not set")
        self.visonicTask = None
        self.SystemStarted = False
        self.createdAlarmPanel = False

    async def service_panel_start(self, force : bool):
        """Service call to start the connection."""
        # force is set True on initial connection so the totalAttempts (for the number of reconnections) can be set to 0. 
        #    It is forced to try at least once on integration start (or reload)
        if self.SystemStarted:
            self.logstate_warning("Request to Start and the integraion is already running and connected")
            return

        #self.logstate_debug(f"service_panel_start, connecting   force = {force}")

        delayBetweenAttempts = 60.0
        totalAttempts = 1

        if CONF_RETRY_CONNECTION_DELAY in self.config:
            delayBetweenAttempts = float(self.config.get(CONF_RETRY_CONNECTION_DELAY))   # seconds

        if CONF_RETRY_CONNECTION_COUNT in self.config:
            totalAttempts = int(self.config.get(CONF_RETRY_CONNECTION_COUNT))
        
        attemptCounter = 0
        #self.logstate_debug(f"     {attemptCounter} of {totalAttempts}")
        while force or attemptCounter < totalAttempts:
            self.logstate_debug("........... connection attempt {0} of {1}".format(attemptCounter + 1, totalAttempts))
            self.visonicTask = None
            self.visonicProtocol = None
            if await self.connect_to_alarm():
                self.logstate_debug("........... connection made")
                self.fireHAEvent( {"condition": 0, "action": ActionList[0], "state": "connected", "attempt": attemptCounter + 1} )
                return
            self.fireHAEvent( {"condition": 0, "action": ActionList[0], "state": "failedattempt", "attempt": attemptCounter + 1} )
            attemptCounter = attemptCounter + 1
            force = False
            if attemptCounter < totalAttempts:
                self.logstate_debug("........... connection attempt delay {0} seconds".format(delayBetweenAttempts))
                await asyncio.sleep(delayBetweenAttempts)

        self.createNotification(
            AvailableNotifications.CONNECTION_PROBLEM,
            f"Failed to connect into Visonic Alarm Panel {self.getPanelID()}. Check Your Network and the Configuration Settings."
        )

        #self.logstate_debug("Giving up on trying to connect, sorry")

    async def service_panel_reconnect(self, call):
        """Service call to re-connect the connection."""
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration not connected to panel {self.getPanelID()}.")

        if call.context.user_id:
            await self.checkUserPermission(call, POLICY_CONTROL, ALARM_PANEL_ENTITY + "." + self.getAlarmPanelUniqueIdent())

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

    def stop_subscription(self, event):
        """Shutdown Visonic subscriptions and subscription thread on exit."""
        self.logstate_debug("Shutting down subscriptions")
        asyncio.ensure_future(self.service_panel_stop(), loop=self.hass.loop)

    def onDisconnect(self, reason : str, excep = None):
        """Disconnection Callback for connection disruption to the panel."""
        if excep is None:
            self.logstate_debug("Visonic has caused an exception, reason=%s, no exception information is available", reason)
        else:
            self.logstate_debug("Visonic has caused an exception, reason=%s %s", reason, str(excep))

        # General update trigger
        #    0 is a disconnect, state="disconnected" means initial disconnection and (hopefully) reconnect from an exception (probably comms related)
        self.fireHAEvent( {"condition": 0, "action": ActionList[0], "state": "disconnected", "reason": reason} )

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
            
            override_code = self.config.get(CONF_OVERRIDE_CODE, "")
            
            if panelmode == AlPanelMode.STANDARD:
                if psc == AlPanelStatus.DISARMED:
                    if self.isArmWithoutCode():  # 
                        #self.logstate_debug("Here B")
                        return True, "0000"        # If the panel can arm without a usercode then we can use 0000 as the usercode
                    elif self.hasValidOverrideCode():
                        return True, override_code
                    return False, None             # use keypad so invalidate the return, there should be a valid 4 code code
                else:
                    if self.hasValidOverrideCode() and not forcedKeypad:
                        return True, override_code
                    return False, None             # use keypad so invalidate the return, there should be a valid 4 code code
            elif panelmode == AlPanelMode.POWERLINK or panelmode == AlPanelMode.STANDARD_PLUS:  # 
                if psc == AlPanelStatus.DISARMED and self.isArmWithoutCode() and forcedKeypad:
                    return True, override_code if self.hasValidOverrideCode() else None   # Override code or usercode
                if forcedKeypad:
                    return False, None   # use keypad so invalidate the return, there should be a valid 4 code code
                if self.hasValidOverrideCode():
                    return True, override_code
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
            if self.hasValidOverrideCode():
                # The override is set and valid
                return True, self.config.get(CONF_OVERRIDE_CODE, "")
            elif panelmode == AlPanelMode.POWERLINK or panelmode == AlPanelMode.STANDARD_PLUS:
                # Powerlink or StdPlus and so we downloaded the code codes
                return True, None
            else:
                self.logstate_warning("Warning: [pmGetPinSimple] Valid 4 digit PIN not found, panelmode is {0}".format(panelmode))
                return False, None
        return True, code

    messageDict = {
        AlCommandStatus.SUCCESS                     : "Success",
        AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS   : "Not supported when downloading EPROM",
        AlCommandStatus.FAIL_INVALID_CODE           : "Not allowed without valid pin",
        AlCommandStatus.FAIL_USER_CONFIG_PREVENTED  : "Disabled by user settings",
        AlCommandStatus.FAIL_INVALID_STATE          : "Invalid state requested",
        AlCommandStatus.FAIL_X10_PROBLEM            : "General X10 Problem",
        AlCommandStatus.FAIL_PANEL_CONFIG_PREVENTED : "Disabled by panel settings",
    }

    def populateSensorDictionary(self) -> dict:
        datadict = {}
        datadict["ready"] = self.isPanelReady()
        datadict["open"] = []
        datadict["bypass"] = []
        datadict["tamper"] = []
        datadict["zonetamper"] = []
        
        for s in self.sensor_list:
            entname = BINARY_SENSOR_STR + "." + self.getMyString() + s.createFriendlyName().lower()
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

    # This should only be called from within this module
    def generateBusEventReason(self, event_id: PanelCondition, reason: AlCommandStatus, command: str, message: str):
        """Generate an HA Bus Event with a Reason Code."""
        datadict = self.populateSensorDictionary()
        #if self.visonicProtocol is not None:
        datadict["command"] = command           
        datadict["reason"] = int(reason)
        datadict["message"] = message + " " + self.messageDict[reason]

        self.onPanelChangeHandler(event_id, datadict)

        #self.logstate_debug("[" + message + "] " + self.messageDict[reason])

        if reason != AlCommandStatus.SUCCESS:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, message + " " + self.messageDict[reason])

    def sendCommand(self, message : str, command : AlPanelCommand, code : str):
        if not self.DisableAllCommands:
            codeRequired = self.isCodeRequired()
            if (codeRequired and code is not None) or not codeRequired:
                pcode = self.decode_code(code) if codeRequired or (code is not None and len(code) > 0) else ""
                vp = self.visonicProtocol
                if vp is not None:
                    isValidPL, code = self.pmGetPin(code = pcode, forcedKeypad = self.isForceKeypad())

                    if command == AlPanelCommand.DISARM or command == AlPanelCommand.ARM_HOME or command == AlPanelCommand.ARM_AWAY or command == AlPanelCommand.ARM_HOME_INSTANT or command == AlPanelCommand.ARM_AWAY_INSTANT:

                        self.logstate_debug("Send command to Visonic Alarm Panel: %s", command)

                        if isValidPL:
                            if (command == AlPanelCommand.DISARM and self.isRemoteDisarm()) or (
                                command != AlPanelCommand.DISARM and self.isRemoteArm()):
                                retval = vp.requestPanelCommand(command, code)
                                self.generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, retval, command.name, "Request Arm/Disarm")
                            else:
                                self.generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, AlCommandStatus.FAIL_USER_CONFIG_PREVENTED , command.name, "Request Arm/Disarm")
                        else:
                            self.generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, AlCommandStatus.FAIL_INVALID_CODE, command.name, "Request Arm/Disarm")

                    elif vp.isPowerMaster() and (command == AlPanelCommand.MUTE or command == AlPanelCommand.TRIGGER or command == AlPanelCommand.FIRE or command == AlPanelCommand.EMERGENCY or command == AlPanelCommand.PANIC):
                        if isValidPL:
                            self.logstate_debug("Send command to Visonic Alarm Panel: %s", command)
                            retval = vp.requestPanelCommand(command, code)
                            self.generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, retval, command.name, "Request PowerMaster Panel Command")
                        else:
                            self.generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, AlCommandStatus.FAIL_INVALID_CODE, command.name, "Request PowerMaster Panel Command")
                    else:
                        self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, not sent to panel")
                else:
                    self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, not sent to panel")
            else:
                self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, an alarm code is required")
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")

    def sendBypass(self, devid: int, bypass: bool, code: str) -> AlCommandStatus:
        """Send the bypass command to the panel."""
        if not self.DisableAllCommands:
            vp = self.visonicProtocol
            if vp is not None:
                if self.toBool(self.config.get(CONF_ENABLE_SENSOR_BYPASS, False)):
                    dpin = self.decode_code(code)
                    isValidPL, code = self.pmGetPinSimple(code = dpin)
                    if isValidPL:
                        # The device id in the range 1 to N
                        retval = vp.setSensorBypassState(devid, bypass, code)
                        #retval = AlCommandStatus.FAIL_INVALID_CODE
                    else:
                        retval = AlCommandStatus.FAIL_INVALID_CODE
                else:
                    retval = AlCommandStatus.FAIL_USER_CONFIG_PREVENTED
            else:
                retval = AlCommandStatus.FAIL_PANEL_NO_CONNECTION

            self.generateBusEventReason(PanelCondition.CHECK_BYPASS_COMMAND, retval, "Bypass", "Sensor Arm State")
            return retval
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")
        return AlCommandStatus.FAIL_USER_CONFIG_PREVENTED

    def setX10(self, ident: int, state: AlX10Command):
        """Send an X10 command to the panel."""
        if not self.DisableAllCommands:
            # ident in range 0 to 15, state can be one of "off", "on", "dim", "brighten"
            if self.visonicProtocol is not None:
                retval = self.visonicProtocol.setX10(ident, state)
                self.generateBusEventReason(PanelCondition.CHECK_X10_COMMAND, retval, "X10", "Send X10 Command")
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")

    #def getJPG(self, ident: int, count : int):
    #    """Send a request to get the jpg images from a camera """
    #    if not self.DisableAllCommands:
    #        # ident in range 1 to 64
    #        if self.visonicProtocol is not None:
    #            retval = self.visonicProtocol.getJPG(ident, count)
    #    else:
    #        self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")

    def decode_code(self, data) -> str:
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
#        if self.hasValidOverrideCode():
#            self.logstate_debug("No Code Required as code set in config")
#            return False
#
#        self.logstate_debug("Code Required")
#        return True

    async def service_panel_eventlog(self, call):
        """Service call to retrieve the event log from the panel. This currently just gets dumped in the HA log file."""
        if not self.DisableAllCommands:
            self.logstate_info("Received event log request")
            if not self.isPanelConnected():
                raise HomeAssistantError(f"Visonic Integration not connected to panel {self.getPanelID()}.")

            if call.context.user_id:
                await self.checkUserPermission(call, POLICY_READ, ALARM_PANEL_ENTITY + "." + self.getAlarmPanelUniqueIdent())

            self.logstate_debug("Received event log request - user approved")
            #if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>" or str(type(call.data)) == "<class 'homeassistant.util.read_only_dict.ReadOnlyDict'>":
            if isinstance(call.data, dict):
                if self.visonicProtocol is not None:
                    code = None
                    if ATTR_CODE in call.data:
                        code = call.data[ATTR_CODE]
                        # If the code is defined then it must be a 4 digit string
                        if len(code) > 0 and not re.search(PIN_REGEX, code):
                            code = "0000"
                            
                    pcode = self.decode_code(code)
                    isValidPL, code = self.pmGetPinSimple(code = pcode)
                    if isValidPL:
                        self.logstate_debug("Sending event log request to panel")
                        retval = self.visonicProtocol.getEventLog(code)
                        self.generateBusEventReason(PanelCondition.CHECK_EVENT_LOG_COMMAND, retval, "EventLog", "Event Log Request")
                    else:
                        self.generateBusEventReason(PanelCondition.CHECK_EVENT_LOG_COMMAND, AlCommandStatus.FAIL_INVALID_CODE, "EventLog", "Event Log Request")
                else:
                    self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending Event Log Request Command, not sent to panel")
            else:
                self.logstate_warning(f"Not making event log request {type(call.data)} {call.data}")
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")

    # Service alarm_control_panel.alarm_sensor_bypass
    # {"entity_id": "binary_sensor.visonic_z01", "bypass":"True", "code":"1234" }
    def sensor_bypass(self, eid : str, bypass : bool, code : str):
        """Bypass individual sensors."""
        if not self.DisableAllCommands:
            # This function concerns itself with bypassing a sensor and the visonic panel interaction

            armcode = self.getPanelStatus()
            if armcode is None or armcode == AlPanelStatus.UNKNOWN:
                self.createNotification(AvailableNotifications.CONNECTION_PROBLEM, "Attempt to bypass sensor, check panel connection")
            else:
                if armcode == AlPanelStatus.DISARMED:
                    # If currently Disarmed
                    mybpstate = self.hass.states.get(eid)
                    if mybpstate is not None:
                        if DEVICE_ATTRIBUTE_NAME in mybpstate.attributes and PANEL_ATTRIBUTE_NAME in mybpstate.attributes:
                            devid = mybpstate.attributes[DEVICE_ATTRIBUTE_NAME]
                            #self.logstate_debug("Attempt to bypass sensor mybpstate.attributes = {0}".format(mybpstate.attributes))
                            panel = mybpstate.attributes[PANEL_ATTRIBUTE_NAME]
                            if panel == self.getPanelID(): # This should be done in _init_ but check again to make sure as its a critical operation
                                if devid >= 1 and devid <= 64:
                                    if bypass:
                                        self.logstate_debug("Attempt to bypass sensor device id = %s", str(devid))
                                    else:
                                        self.logstate_debug("Attempt to restore (arm) sensor device id = %s", str(devid))
                                    self.sendBypass(devid, bypass, code)
                                else:
                                    self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, incorrect device {str(devid)} for entity {eid}")
                            else:
                                self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, device {str(devid)} but entity {eid} not connected to this panel")
                        else:
                            self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, incorrect entity {eid}")
                    else:
                        self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, unknown device state for entity {eid}")
                else:
                    self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Visonic Alarm Panel: Attempt to bypass sensor for panel {self.getPanelID()}, panel needs to be in the disarmed state")
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")

    # Service alarm_control_panel.alarm_sensor_bypass
    # {"entity_id": "binary_sensor.visonic_z01", "bypass":"True", "code":"1234" }
    def sensor_update_image(self, eid : str):
        """Bypass individual sensors."""
        self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Command to get image is Disabled")
        #if not self.DisableAllCommands:
        #    # This function concerns itself with bypassing a sensor and the visonic panel interaction
        #    mybpstate = self.hass.states.get(eid)
        #    if mybpstate is not None:
        #        if DEVICE_ATTRIBUTE_NAME in mybpstate.attributes and PANEL_ATTRIBUTE_NAME in mybpstate.attributes:
        #            devid = mybpstate.attributes[DEVICE_ATTRIBUTE_NAME]
        #            #self.logstate_debug("Attempt to bypass sensor mybpstate.attributes = {0}".format(mybpstate.attributes))
        #            panel = mybpstate.attributes[PANEL_ATTRIBUTE_NAME]
        #            if panel == self.getPanelID(): # This should be done in _init_ but check again to make sure as its a critical operation
        #                if devid >= 1 and devid <= 64:
        #                    self.getJPG(devid, 4)  # The 4 is the number of images to retrieve but it doesnt work
        #                else:
        #                    self.createNotification(AvailableNotifications.IMAGE_PROBLEM, f"Attempt to retrieve sensor image for panel {self.getPanelID()}, incorrect device {str(devid)} for entity {eid}")
        #            else:
        #                self.createNotification(AvailableNotifications.IMAGE_PROBLEM, f"Attempt to retrieve sensor image for panel {self.getPanelID()}, device {str(devid)} but entity {eid} not connected to this panel")
        #        else:
        #            self.createNotification(AvailableNotifications.IMAGE_PROBLEM, f"Attempt to retrieve sensor image for panel {self.getPanelID()}, incorrect entity {eid}")
        #    else:
        #        self.createNotification(AvailableNotifications.IMAGE_PROBLEM, f"Attempt to retrieve sensor image for panel {self.getPanelID()}, unknown device state for entity {eid}")
        #else:
        #    self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")


    def dump_dict(self, d):
        for key in d:
            self.logstate_debug(f"  {key} = {d[key]}")

    async def service_sensor_image(self, call):
        """Service call to bypass a sensor in the panel."""
        self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Command to get image is Disabled")
#        if not self.DisableAllCommands:
#            self.logstate_debug("Received sensor image update request")
#            if not self.isPanelConnected():
#                raise HomeAssistantError(f"Visonic Integration not connected to panel {self.getPanelID()}.")
#
#            # if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>" or str(type(call.data)) == "<class 'homeassistant.util.read_only_dict.ReadOnlyDict'>":
#            if isinstance(call.data, dict):
#                self.logstate_debug("  Sensor_image = %s", str(type(call.data)))
#                self.dump_dict(call.data)
#                if ATTR_ENTITY_ID in call.data:
#                    eid = str(call.data[ATTR_ENTITY_ID])
#                    if not eid.startswith(IMAGE_SENSOR_STR + "."):
#                        eid = IMAGE_SENSOR_STR + "." + eid
#                    if valid_entity_id(eid):
#                        if call.context.user_id:
#                            await self.checkUserPermission(call, POLICY_CONTROL, call.data[ATTR_ENTITY_ID])
#                        
#                        self.sensor_update_image(eid)
#                    else:
#                        self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, invalid entity {eid}")
#                else:
#                    self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()} but entity not defined")
#            else:
#                self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()} but entity not defined")
#        else:
#            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")


    async def service_sensor_bypass(self, call):
        """Service call to bypass a sensor in the panel."""
        if not self.DisableAllCommands:
            self.logstate_debug("Received sensor bypass request")
            if not self.isPanelConnected():
                raise HomeAssistantError(f"Visonic Integration not connected to panel {self.getPanelID()}.")

            # if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>" or str(type(call.data)) == "<class 'homeassistant.util.read_only_dict.ReadOnlyDict'>":
            if isinstance(call.data, dict):
                self.logstate_debug("  Sensor_bypass = %s", str(type(call.data)))
                self.dump_dict(call.data)
                if ATTR_ENTITY_ID in call.data:
                    eid = str(call.data[ATTR_ENTITY_ID])
                    if not eid.startswith(BINARY_SENSOR_STR + "."):
                        eid = BINARY_SENSOR_STR + "." + eid
                    if valid_entity_id(eid):
                        if call.context.user_id:
                            await self.checkUserPermission(call, POLICY_CONTROL, call.data[ATTR_ENTITY_ID])
                        
                        bypass: boolean = False
                        if ATTR_BYPASS in call.data:
                            bypass = call.data[ATTR_BYPASS]

                        code = None
                        if ATTR_CODE in call.data:
                            code = call.data[ATTR_CODE]
                            # If the code is defined then it must be a 4 digit string
                            if len(code) > 0 and not re.search(PIN_REGEX, code):
                                code = "0000"

                        self.sensor_bypass(eid, bypass, code)
                    else:
                        self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, invalid entity {eid}")
                else:
                    self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()} but entity not defined")
            else:
                self.createNotification(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()} but entity not defined")
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")


    async def service_panel_command(self, call):
        """Service call to send a command to the panel."""
        if not self.DisableAllCommands:
            self.logstate_info("Received command request")
            if not self.isPanelConnected():
                raise HomeAssistantError(f"Visonic Integration not connected to panel {self.getPanelID()}.")

            if call.context.user_id:
                await self.checkUserPermission(call, POLICY_CONTROL, ALARM_PANEL_ENTITY + "." + self.getAlarmPanelUniqueIdent())

            self.logstate_debug("Received command request - user approved")
            #if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>" or str(type(call.data)) == "<class 'homeassistant.util.read_only_dict.ReadOnlyDict'>":
            if isinstance(call.data, dict):
                command = call.data[CONF_COMMAND]
                self.logstate_debug(f"   Command {command}")
                code = None
                if ATTR_CODE in call.data:
                    code = call.data[ATTR_CODE]
                    # If the code is defined then it must be a 4 digit string
                    if len(code) > 0 and not re.search(PIN_REGEX, code):
                        code = "0000"
                try:
                    command_e = AlPanelCommand.value_of(command.upper());
                    self.sendCommand("Alarm Service Call " + str(command_e), command_e, code)
                except Exception as ex:
                    self.logstate_warning("Not making command request {0} {1}  Exception {2}".format(type(call.data), call.data, ex) )
                    #self.logstate_debug(ex)
            else:
                self.logstate_debug(f"Not making command request {type(call.data)} {call.data}")
        else:
            self.createNotification(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Panel Commands Disabled")

    async def connect(self):
        """Connect to the alarm panel using the pyvisonic library."""
        try:
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
        return False
