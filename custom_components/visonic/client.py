"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""

import asyncio
from collections import defaultdict
import logging
from time import sleep
from typing import Union, Any
import re
import datetime
from datetime import datetime, timedelta

CLIENT_VERSION = "0.8.5.0"

from jinja2 import Environment, FileSystemLoader
from .pyvisonic import (
    async_create_tcp_visonic_connection,
    async_create_usb_visonic_connection,
)

from enum import IntEnum
from .pconst import PyConfiguration, PyPanelMode, PyPanelCommand, PyPanelStatus, PyCommandStatus, PyX10Command, PyCondition, PySensorDevice, PyLogPanelEvent, PySwitchDevice, PyPanelInterface
from requests import ConnectTimeout, HTTPError
import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
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
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser

from .const import (
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
    CONF_B0_ENABLE_MOTION_PROCESSING,
    CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT,
    CONF_B0_MIN_TIME_BETWEEN_TRIGGERS,
    CONF_DEVICE_BAUD,
    CONF_DEVICE_TYPE,
    CONF_DOWNLOAD_CODE,
    CONF_FORCE_AUTOENROLL,
    CONF_FORCE_STANDARD,
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
    DOMAIN,
    DOMAINDATA,
    NOTIFICATION_ID,
    NOTIFICATION_TITLE,
    VISONIC_UPDATE_STATE_DISPATCHER,
    CONF_ALARM_NOTIFICATIONS,
    PANEL_ATTRIBUTE_NAME,
    DEVICE_ATTRIBUTE_NAME,
    AvailableNotifications,
    PIN_REGEX,
)

class PanelCondition(IntEnum):
    CHECK_ARM_DISARM_COMMAND = 11
    CHECK_BYPASS_COMMAND = 12
    CHECK_EVENT_LOG_COMMAND = 13
    CHECK_X10_COMMAND = 14

MAX_CLIENT_LOG_ENTRIES = 100

_LOGGER = logging.getLogger(__name__)

# the schemas for the HA service calls
ALARM_SERVICE_EVENTLOG = vol.Schema(
    {
        vol.Optional(ATTR_CODE, default=""): cv.string,
    }
)

CONF_COMMAND = "command"
ALARM_SERVICE_COMMAND = vol.Schema(
    {
        vol.Required(CONF_COMMAND) : cv.enum(PyPanelCommand),
        vol.Optional(ATTR_CODE, default=""): cv.string,
    }
)

ActionList = (
   "connection", "zoneupdate", "panelupdate", "sirenactive", "panelreset", "pinrejected", "paneltamper", "timeoutdownload", 
   "timeoutwaiting", "timeoutactive", "nopaneldata", "armdisarm", "bypass", "eventlog", "x10", "", "", ""
)

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

        self.hasSensor = False
        self.hasSwitch = False

        self.strlog = []
        #self.logstate_debug("init panel %s   self.config = %s  %s", str(panelident), PyConfiguration.DownloadCode, self.config)
        
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
        self.visonicProtocol : PyPanelInterface = None
        self.SystemStarted = False

        # variables for creating the event log for csv and xml
        self.csvdata = None
        self.templatedata = None

        self.logstate_info(
            "Exclude sensor list = %s     Exclude x10 list = %s",
            self.exclude_sensor_list,
            self.exclude_x10_list,
        )
        
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

    def getDispatcher(self):
        # This just needs to be unique within HA so use the domain name and panel number
        return VISONIC_UPDATE_STATE_DISPATCHER + "_p" + str(self.getPanelID())

    def getAlarmPanelUniqueIdent(self):
        if self.getPanelID() > 0:
            return VISONIC_UNIQUE_NAME + " Panel " + str(self.getPanelID())
        return VISONIC_UNIQUE_NAME

    def sendHANotification(self, condition : AvailableNotifications, message: str) -> bool:
        notification_config = self.config.get(CONF_ALARM_NOTIFICATIONS, [] )
        if condition == AvailableNotifications.ALWAYS or condition in notification_config:
            self.logstate_info("HA Notification: {0}".format(message))
            self.hass.components.persistent_notification.create(
                message, title=NOTIFICATION_TITLE, notification_id=NOTIFICATION_ID
            )
            return True
        return False

    def createWarningMessage(self, condition : AvailableNotifications, message: str):
        """Create a Warning message in the log file and a notification on the HA Frontend."""
        if not self.sendHANotification(condition, message):
            self.logstate_warning("HA Warning (not shown in frontend due to user config), condition is {0} message={1}".format(condition, message))

    def dumpSensorsToStringList(self) -> list:
        if self.visonicProtocol is not None:
            return self.visonicProtocol.dumpSensorsToStringList()
        return []

    def dumpSwitchesToStringList(self) -> list:
        if self.visonicProtocol is not None:
            return self.visonicProtocol.dumpSwitchesToStringList()
        return []

    def dumpStateToStringList(self) -> list:
        if self.visonicProtocol is not None:
            return self.visonicProtocol.dumpStateToStringList()
        return []

    def isSirenActive(self) -> bool:
        """Is the siren active."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isSirenActive()
        return False

    def isPowerMaster(self) -> bool:
        """Is it a PowerMaster panel"""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isPowerMaster()
        return False

    def isForceKeypad(self) -> bool:
        """Force Keypad"""
        return self.toBool(self.config.get(CONF_FORCE_KEYPAD, False))

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

    def getPanelStatusCode(self) -> PyPanelStatus:
        """Get the panel status code."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelStatusCode()
        return PyPanelStatus.UNKNOWN

    def getPanelMode(self) -> PyPanelMode:
        """Get the panel mode."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelMode()
        return PyPanelMode.UNKNOWN

    def getPanelStatus(self, full : bool) -> dict:
        """Get the panel status."""
        if self.visonicProtocol is not None:
            pd = self.visonicProtocol.getPanelStatus(full)
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

    def process_panel_event_log(self, event_log_entry: PyLogPanelEvent):
        """Process a sequence of panel log events."""
        reverse = self.toBool(self.config.get(CONF_LOG_REVERSE))
        total = min(event_log_entry.total, self.config.get(CONF_LOG_MAX_ENTRIES))
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
                        self.createWarningMessage(
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
                        self.createWarningMessage(
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

    def new_switch_callback(self, dev: PySwitchDevice): 
        """Process a new x10."""
        # Check to ensure variables are set correctly
        if self.hass is None:
            self.logstate_warning("Attempt to add X10 switch when hass is undefined")
            return
        if dev is None:
            self.logstate_warning("Attempt to add X10 switch when sensor is undefined")
            return
        # self.logstate_debug("VS: X10 Switch list %s", dev)
        if dev.isEnabled() and dev.getDeviceID() not in self.exclude_x10_list:
            if dev not in self.hass.data[DOMAIN]["switch"]:
                self.hass.data[DOMAIN]["switch"].append(dev)
                self.hasSwitch = True
                if self.switch_task is None or self.switch_task.done():
                     self.switch_task = self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, "switch"))
                #self.logstate_debug("Visonic: %s switches", len(self.hass.data[DOMAIN]["switch"]))
            else:
                self.logstate_debug("X10 Device %s already in the list", dev.getDeviceID())

    def setupAlarmPanel(self):
        self.hass.async_create_task(
            self.hass.config_entries.async_forward_entry_setup(
                self.entry, "alarm_control_panel"
            )
        )

    def new_sensor_callback(self, sensor: PySensorDevice):
        """Process a new sensor."""
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
        else:
            #self.logstate_debug("Sensor %s", str(sensor))
            if sensor.getDeviceID() not in self.exclude_sensor_list:
                if sensor not in self.hass.data[DOMAIN]["binary_sensor"]:
                    self.hasSensor = True
                    self.hass.data[DOMAIN]["binary_sensor"].append(sensor)
                    if self.sensor_task is None or self.sensor_task.done():
                        self.sensor_task = self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, "binary_sensor"))
                if sensor not in self.hass.data[DOMAIN]["select"]:
                    self.hass.data[DOMAIN]["select"].append(sensor)
                    if self.select_task is None or self.select_task.done():
                        self.select_task = self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, "select"))
                else:
                    self.logstate_debug("Sensor %s already in the list", sensor.getDeviceID())
            else:
                self.logstate_debug("Sensor %s in exclusion list", sensor.getDeviceID())

    def fireHAEvent(self, ev: dict):
        ev[PANEL_ATTRIBUTE_NAME] = self.getPanelID()
        self.hass.bus.fire(ALARM_PANEL_CHANGE_EVENT, ev)

    def generate_ha_event(self, event_id: IntEnum, datadictionary: dict):
        """Generate HA Bus Event."""
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
        dispatcher_send(
            self.hass, self.getDispatcher(), event_id, datadictionary
        )

        # Send an event on the event bus for conditions 1 to 14.  Ignore 0 as this is used for any sensor change.
        #  0 is just used for the dispatcher above so the frontend is updated, no HA event is fired
        #  15 is something other than the pin code has been rejected by the panel (see 5 above)
        #  16 is download success
        tmp = int(event_id)

        if event_id == PyCondition.DOWNLOAD_SUCCESS:        # download success        
            # Update the friendly name of the control flow
            d = self.getPanelStatus(True)
            #self.logstate_debug("     DOWNLOAD_SUCCESS. Current Data dict = {0}".format(d))
            
            if 'Panel Model' in d and 'Panel Type' in d and 'Model Type' in d and 'Panel Serial' in d:
                pm = str(d['Panel Model'])
                #pt = str(d['Panel Type'])
                #mt = str(d['Model Type'])
                ps = str(d['Panel Serial'])
                #s = "Panel " + str(self.getPanelID()) + " (" + pm + ", " + pt + ", " + mt + ", " + ps + ")"
                #s = "Panel " + str(self.getPanelID()) + " (" + pm + ", " + ps + ")"
                s = "Panel " + str(self.getPanelID()) + " (" + pm + ")"
            
                # update the title
                self.hass.config_entries.async_update_entry(self.entry, title=s)

        if 1 <= tmp <= 14: # do not send 0 or 15 or 16 as an HA event
            tmpdict = {}
            if datadictionary is not None:
                tmpdict = datadictionary.copy()
            tmpdict["condition"] = tmp
            tmpdict["action"] = ActionList[tmp]
            self.logstate_debug("Firing HA event, panel=%d  event=%s %s", self.getPanelID(), tmp, tmpdict)
            self.fireHAEvent( tmpdict )

        if event_id == PyCondition.PANEL_UPDATE_ALARM_ACTIVE: 
            self.sendHANotification(AvailableNotifications.SIREN, "Siren is Sounding, Alarm has been Activated" )
        elif event_id == PyCondition.PANEL_RESET:
            self.sendHANotification(AvailableNotifications.RESET, "The Panel has been Reset" )
        elif event_id == PyCondition.PIN_REJECTED:
            self.sendHANotification(AvailableNotifications.INVALID_PIN, "The Pin Code has been Rejected By the Panel" )
        elif event_id == PyCondition.PANEL_TAMPER_ALARM:
            self.sendHANotification(AvailableNotifications.TAMPER, "The Panel has been Tampered" )
        elif event_id == PyCondition.DOWNLOAD_TIMEOUT:
            self.sendHANotification(AvailableNotifications.PANEL_OPERATION, "Panel Data download timeout, Standard Mode Selected" )
        elif event_id == PyCondition.WATCHDOG_TIMEOUT_GIVINGUP:
            if self.getPanelMode() == PyPanelMode.POWERLINK:
                self.sendHANotification(AvailableNotifications.CONNECTION_PROBLEM, "Communication Timeout - Watchdog Timeout too many times within 24 hours. Dropping out of Powerlink" )
            else:
                self.sendHANotification(AvailableNotifications.CONNECTION_PROBLEM, "Communication Timeout - Watchdog Timeout too many times within 24 hours." )
        elif tmp == PyCondition.WATCHDOG_TIMEOUT_RETRYING:
            self.sendHANotification(AvailableNotifications.PANEL_OPERATION, "Communication Timeout - Watchdog Timeout, restoring panel connection" )
        elif tmp == PyCondition.NO_DATA_FROM_PANEL:
            self.sendHANotification(AvailableNotifications.CONNECTION_PROBLEM, "Connection Problem - No data from the panel" )
            asyncio.ensure_future(self.service_panel_stop(), loop=self.hass.loop)
        elif tmp == PyCondition.COMMAND_REJECTED:
            self.sendHANotification(AvailableNotifications.ALWAYS, "Operation Rejected By Panel (tell the Integration Author and upload a debug log file if you're able to)" )

    def toBool(self, val: Any) -> bool:
        """Convert value to boolean."""
        if type(val) == bool:
            return val
        elif type(val) == int:
            return val != 0
        elif type(val) == str:
            v = val.lower()
            return not (v == "no" or v == "false" or v == "0")
        self.logstate_warning("Unable to decode boolean value %s    type is %s", val, type(val))
        return False

    def getConfigData(self) -> dict:
        """Create a dictionary full of the configuration data."""
        return {
            PyConfiguration.DownloadCode: self.config.get(CONF_DOWNLOAD_CODE, ""),
            PyConfiguration.ForceStandard: self.toBool(
                self.config.get(CONF_FORCE_STANDARD, False)
            ),
            PyConfiguration.ForceAutoEnroll: self.toBool(
                self.config.get(CONF_FORCE_AUTOENROLL, True)
            ),
            PyConfiguration.AutoSyncTime: self.toBool(
                self.config.get(CONF_AUTO_SYNC_TIME, True)
            ),
            PyConfiguration.PluginLanguage: self.config.get(CONF_LANGUAGE, "EN"),
            PyConfiguration.MotionOffDelay: self.config.get(CONF_MOTION_OFF_DELAY, 120),
            PyConfiguration.SirenTriggerList: self.config.get(
                CONF_SIREN_SOUNDING, ["Intruder"]
            ),
            PyConfiguration.B0_Enable: self.toBool(
                self.config.get(CONF_B0_ENABLE_MOTION_PROCESSING, False)
            ),
            PyConfiguration.B0_Min_Interval_Time: self.config.get(
                CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, 5
            ),
            PyConfiguration.B0_Max_Wait_Time: self.config.get(
                CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, 30
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

    async def connect_to_alarm(self) -> bool:
        """Create the connection to the alarm panel."""
        # Is the system already running and connected
        if self.SystemStarted:
            return False

        # set up config parameters in the visonic library
        self.hass.data[DOMAIN][DOMAINDATA][self.getEntryID()]["Exception Count"] = self.panel_exception_counter

        #self.logstate_debug("connect_to_alarm self.config = %s", self.config)

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
            ) = await async_create_tcp_visonic_connection(
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
            ) = await async_create_usb_visonic_connection(
                path=path,
                baud=baud,
                panelConfig=self.getConfigData(),
                loop=self.hass.loop,
            )

        if self.visonicTask is not None and self.visonicProtocol is not None:
            # Connection to the panel has been initially successful
            # Record that we have started the system
            self.visonicProtocol.setCallbackHandlers(
                    event_callback=self.generate_ha_event,
                    panel_event_log_callback=self.process_panel_event_log,
                    disconnect_callback=self.disconnect_callback, 
                    new_sensor_callback = self.new_sensor_callback,
                    new_switch_callback = self.new_switch_callback)
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

        if self.hasSensor:
            self.logstate_debug("Unloading Sensors")
            # unload the select and sensors
            await self.hass.config_entries.async_forward_entry_unload(self.entry, "select")
            await self.hass.config_entries.async_forward_entry_unload(self.entry, "binary_sensor")
        else:
            self.logstate_debug("No Sensors to Unload")
        
        if self.hasSwitch:
            self.logstate_debug("Unloading Switches")
            # unload the switches
            await self.hass.config_entries.async_forward_entry_unload(self.entry, "switch")
        else:
            self.logstate_debug("No Switches to Unload")
        
        self.hasSensor = False
        self.hasSwitch = False
        
        self.logstate_debug("Unloading Alarm Control Panel")
        # unload the alarm panel
        await self.hass.config_entries.async_forward_entry_unload(self.entry, "alarm_control_panel")

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

        delayBetweenAttempts = self.config.get(CONF_RETRY_CONNECTION_DELAY)   # seconds
        totalAttempts = int(self.config.get(CONF_RETRY_CONNECTION_COUNT))
        
        attemptCounter = 0
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

        self.createWarningMessage(
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

    def disconnect_callback(self, reason : str, excep = None):
        """Disconnection Callback for connection disruption to the panel."""
        if excep is None:
            self.logstate_debug("PyVisonic has caused an exception, reason=%s, no exception information is available", reason)
        else:
            self.logstate_debug("PyVisonic has caused an exception, reason=%s %s", reason, str(excep))

        # General update trigger
        #    0 is a disconnect, state="disconnected" means initial disconnection and (hopefully) reconnect from an exception (probably comms related)
        self.fireHAEvent( {"condition": 0, "action": ActionList[0], "state": "disconnected", "reason": reason} )

        self.panel_exception_counter = self.panel_exception_counter + 1
        asyncio.ensure_future(self.disconnect_callback_async(), loop=self.hass.loop)

    # pmGetPin: Convert a PIN given as 4 digit string in the PIN PDU format as used in messages to powermax
    def pmGetPin(self, pin: str, forcedKeypad: bool):
        """Get pin code."""
        #self.logstate_debug("Getting Pin Start")
        if pin is None or pin == "" or len(pin) != 4:
            psc = self.getPanelStatusCode()
            panelmode = self.getPanelMode()
            #self.logstate_debug("Getting Pin")
            
            # Avoid the panel codes that we're not interested in, if these are set then we have no business doing any of the functions
            #    After this we can simply use DISARMED and not DISARMED for all the armed states
            if psc == PyPanelStatus.UNKNOWN or psc == PyPanelStatus.SPECIAL or psc == PyPanelStatus.DOWNLOADING:
                return False, None   # Return invalid as panel not in correct state to do anything
            
            override_code = self.config.get(CONF_OVERRIDE_CODE, "")
            
            if panelmode == PyPanelMode.STANDARD:
                if psc == PyPanelStatus.DISARMED:
                    if self.isArmWithoutCode():  # 
                        #self.logstate_debug("Here B")
                        return True, "0000"        # If the panel can arm without a usercode then we can use 0000 as the usercode
                    elif self.hasValidOverrideCode():
                        return True, override_code
                    return False, None             # use keypad so invalidate the return, there should be a valid 4 pin code
                else:
                    if self.hasValidOverrideCode() and not forcedKeypad:
                        return True, override_code
                    return False, None             # use keypad so invalidate the return, there should be a valid 4 pin code
            elif panelmode == PyPanelMode.POWERLINK or panelmode == PyPanelMode.STANDARD_PLUS:  # 
                if psc == PyPanelStatus.DISARMED and self.isArmWithoutCode() and forcedKeypad:
                    return True, override_code if self.hasValidOverrideCode() else None   # Override code or usercode
                if forcedKeypad:
                    return False, None   # use keypad so invalidate the return, there should be a valid 4 pin code
                if self.hasValidOverrideCode():
                    return True, override_code
                return True, None    # Usercode
            elif panelmode == PyPanelMode.DOWNLOAD or panelmode == PyPanelMode.STARTING:  # No need to output to log file when starting or downloading EEPROM as this is normal operation
                return False, None # Return invalid as panel downloading EEPROM
            else:
                # If the panel mode is UNKNOWN, PROBLEM.
                self.logstate_warning("Warning: Valid 4 digit PIN not found, panelmode is {0}".format(panelmode))
                return False, None # Return invalid as panel not in correct state to do anything
        return True, pin

    # pmGetPinSimple: Convert a PIN given as 4 digit string in the PIN PDU format as used in messages to powermax
    #   This is used from the bypass command and the get event log command
    def pmGetPinSimple(self, pin: str):
        """Get pin code."""
        #self.logstate_debug("Getting Pin Start")
        if pin is None or pin == "" or len(pin) != 4:
            panelmode = self.getPanelMode()
            if self.hasValidOverrideCode():
                # The override is set and valid
                return True, self.config.get(CONF_OVERRIDE_CODE, "")
            elif panelmode == PyPanelMode.POWERLINK or panelmode == PyPanelMode.STANDARD_PLUS:
                # Powerlink or StdPlus and so we downloaded the pin codes
                return True, None
            else:
                self.logstate_warning("Warning: [pmGetPinSimple] Valid 4 digit PIN not found, panelmode is {0}".format(panelmode))
                return False, None
        return True, pin

    messageDict = {
        PyCommandStatus.SUCCESS                     : "Success",
        PyCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS   : "Not supported when downloading EPROM",
        PyCommandStatus.FAIL_INVALID_PIN            : "Not allowed without valid pin",
        PyCommandStatus.FAIL_USER_CONFIG_PREVENTED  : "Disabled by user settings",
        PyCommandStatus.FAIL_INVALID_STATE          : "Invalid state requested",
        PyCommandStatus.FAIL_X10_PROBLEM            : "General X10 Problem",
        PyCommandStatus.FAIL_PANEL_CONFIG_PREVENTED : "Disabled by panel settings",
    }

    def generateBusEventReason(self, event_id: IntEnum, reason: PyCommandStatus, command: str, message: str):
        """Generate an HA Bus Event with a Reason Code."""
        datadict = {}
        if self.visonicProtocol is not None:
            datadict = self.visonicProtocol.populateDictionary()
        datadict["Command"] = command           
        datadict["Reason"] = int(reason)
        datadict["Message"] = message + " " + self.messageDict[reason]

        self.generate_ha_event(event_id, datadict)

        #self.logstate_debug("[" + message + "] " + self.messageDict[reason])

        if reason != PyCommandStatus.SUCCESS:
            self.sendHANotification(AvailableNotifications.COMMAND_NOT_SENT, "" + message + " " + self.messageDict[reason])

    def sendCommand(self, message : str, command : PyPanelCommand, code : str):
        codeRequired = self.isCodeRequired()
        if (codeRequired and code is not None) or not codeRequired:
            pcode = self.decode_code(code) if codeRequired or (code is not None and len(code) > 0) else ""
            vp = self.visonicProtocol
            if vp is not None:
                self.logstate_debug("Send command to Visonic Alarm Panel: %s", command)

                isValidPL, pin = self.pmGetPin(pin = pcode, forcedKeypad = self.isForceKeypad())

                #if not isValidPL and self.isArmWithoutCode() and command != PyPanelCommand.DISARM:
                #    # if we dont have pin codes and we can arm without a code and we're arming and arming is allowed
                #    isValidPL = True
                #    pin = "0000"

                if isValidPL:
                    if (command == PyPanelCommand.DISARM and self.isRemoteDisarm()) or (
                        command != PyPanelCommand.DISARM and self.isRemoteArm()):
                        retval = vp.requestArm(command, pin)
                        self.generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, retval, command.name, "Request Arm/Disarm")
                    else:
                        self.generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, PyCommandStatus.FAIL_USER_CONFIG_PREVENTED , command.name, "Request Arm/Disarm")
                else:
                    self.generateBusEventReason(PanelCondition.CHECK_ARM_DISARM_COMMAND, PyCommandStatus.FAIL_INVALID_PIN, command.name, "Request Arm/Disarm")
            else:
                self.createWarningMessage(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, not sent to panel")
        else:
            self.createWarningMessage(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending {message} Command, an alarm code is required")

    def sendBypass(self, devid: int, bypass: bool, code: str) -> PyCommandStatus:
        """Send the bypass command to the panel."""
        vp = self.visonicProtocol
        if vp is not None:
            if self.toBool(self.config.get(CONF_ENABLE_SENSOR_BYPASS, False)):
                dpin = self.decode_code(code)
                isValidPL, pin = self.pmGetPinSimple(pin = dpin)
                if isValidPL:
                    # The device id in the range 1 to N
                    retval = vp.setSensorBypassState(devid, bypass, pin)
                else:
                    retval = PyCommandStatus.FAIL_INVALID_PIN
            else:
                retval = PyCommandStatus.FAIL_USER_CONFIG_PREVENTED
        else:
            retval = PyCommandStatus.FAIL_PANEL_NO_CONNECTION

        self.generateBusEventReason(PanelCondition.CHECK_BYPASS_COMMAND, retval, "Bypass", "Sensor Arm State")
        return retval

    def setX10(self, ident: int, state: PyX10Command):
        """Send an X10 command to the panel."""
        # ident in range 0 to 15, state can be one of "off", "on", "dim", "brighten"
        if self.visonicProtocol is not None:
            retval = self.visonicProtocol.setX10(ident, state)
            self.generateBusEventReason(PanelCondition.CHECK_X10_COMMAND, retval, "X10", "Send X10 Command")

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
        armcode = self.getPanelStatusCode()
        panelmode = self.getPanelMode()
        if armcode is None or armcode == PyPanelStatus.UNKNOWN or panelmode == PyPanelMode.UNKNOWN:
            # self.logstate_debug("isPanelConnected: code format none as armcode is none (panel starting up or is there a problem?)")
            return False
        return True

    def isCodeRequired(self) -> bool:
        """Determine if a user code is required given the panel mode and user settings."""
        isValidPL, pin = self.pmGetPin(pin = None, forcedKeypad = self.isForceKeypad())
        return not isValidPL;

#    def isCodeRequiredBackup(self) -> bool:
#        """Determine if a user code is required given the panel mode and user settings."""
#        # try powerlink or standard plus mode first, then it already has the user codes
#        panelmode = self.getPanelMode()
#        # self.logstate_debug("code format panel mode %s", panelmode)
#        if not self.isForceKeypad() and panelmode is not None:
#            if panelmode == PyPanelMode.POWERLINK or panelmode == PyPanelMode.STANDARD_PLUS:
#                self.logstate_debug("No Code Required as powerlink or std plus ********")
#                return False
#
#        armcode = self.getPanelStatusCode()
#        if armcode is None or armcode == PyPanelStatus.UNKNOWN:
#            return True
#
#        # If currently Disarmed and user setting to not show panel to arm
#        if armcode == PyPanelStatus.DISARMED and self.isArmWithoutCode():
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
        self.logstate_info("Received event log request")
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration not connected to panel {self.getPanelID()}.")

        if call.context.user_id:
            await self.checkUserPermission(call, POLICY_READ, ALARM_PANEL_ENTITY + "." + self.getAlarmPanelUniqueIdent())

        self.logstate_debug("Received event log request - user approved")
        #if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>" or str(type(call.data)) == "<class 'homeassistant.util.read_only_dict.ReadOnlyDict'>":
        if isinstance(call.data, dict):
            if self.visonicProtocol is not None:
                code = ""
                if ATTR_CODE in call.data:
                    code = call.data[ATTR_CODE]
                    # If the code is defined then it must be a 4 digit string
                    if len(code) > 0 and not re.search(PIN_REGEX, code):
                        code = "0000"
                        
                pcode = self.decode_code(code)
                isValidPL, pin = self.pmGetPinSimple(pin = pcode)
                if isValidPL:
                    self.logstate_debug("Sending event log request to panel")
                    retval = self.visonicProtocol.getEventLog(pin)
                    self.generateBusEventReason(PanelCondition.CHECK_EVENT_LOG_COMMAND, retval, "EventLog", "Event Log Request")
                else:
                    self.generateBusEventReason(PanelCondition.CHECK_EVENT_LOG_COMMAND, PyCommandStatus.FAIL_INVALID_PIN, "EventLog", "Event Log Request")
            else:
                self.createWarningMessage(AvailableNotifications.COMMAND_NOT_SENT, f"Visonic Alarm Panel: Error in sending Event Log Request Command, not sent to panel")
        else:
            self.logstate_warning("Not making event log request %s %s", type(call.data), call.data)

    # Service alarm_control_panel.alarm_sensor_bypass
    # {"entity_id": "binary_sensor.visonic_z01", "bypass":"True", "code":"1234" }
    def sensor_bypass(self, eid : str, bypass : bool, code : str):
        """Bypass individual sensors."""
        # This function concerns itself with bypassing a sensor and the visonic panel interaction

        armcode = self.getPanelStatusCode()
        if armcode is None or armcode == PyPanelStatus.UNKNOWN:
            self.createWarningMessage(AvailableNotifications.CONNECTION_PROBLEM, "Attempt to bypass sensor, check panel connection")
        else:
            if armcode == PyPanelStatus.DISARMED:
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
                                self.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, incorrect device {str(devid)} for entity {eid}")
                        else:
                            self.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, device {str(devid)} but entity {eid} not connected to this panel")
                    else:
                        self.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, incorrect entity {eid}")
                else:
                    self.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, unknown device state for entity {eid}")
            else:
                self.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Visonic Alarm Panel: Attempt to bypass sensor for panel {self.getPanelID()}, panel needs to be in the disarmed state")

    async def service_sensor_bypass(self, call):
        """Service call to bypass a sensor in the panel."""
        self.logstate_debug("Received sensor bypass request")
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration not connected to panel {self.getPanelID()}.")

        # if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>" or str(type(call.data)) == "<class 'homeassistant.util.read_only_dict.ReadOnlyDict'>":
        if isinstance(call.data, dict):
            # self.logstate_debug("  Sensor_bypass = %s", str(type(call.data)))
            # self.dump_dict(call.data)
            if ATTR_ENTITY_ID in call.data:
                eid = str(call.data[ATTR_ENTITY_ID])
                if not eid.startswith("binary_sensor."):
                    eid = "binary_sensor." + eid
                if valid_entity_id(eid):
                    if call.context.user_id:
                        await self.checkUserPermission(call, POLICY_CONTROL, call.data[ATTR_ENTITY_ID])
                    
                    bypass: boolean = False
                    if ATTR_BYPASS in call.data:
                        bypass = call.data[ATTR_BYPASS]

                    code = ""
                    if ATTR_CODE in call.data:
                        code = call.data[ATTR_CODE]
                        # If the code is defined then it must be a 4 digit string
                        if len(code) > 0 and not re.search(PIN_REGEX, code):
                            code = "0000"

                    self.sensor_bypass(eid, bypass, code)
                else:
                    self.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()}, invalid entity {eid}")
            else:
                self.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()} but entity not defined")
        else:
            self.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor for panel {self.getPanelID()} but entity not defined")


    async def service_panel_command(self, call):
        """Service call to send a command to the panel."""
        self.logstate_info("Received command request")
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration not connected to panel {self.getPanelID()}.")

        if call.context.user_id:
            await self.checkUserPermission(call, POLICY_CONTROL, ALARM_PANEL_ENTITY + "." + self.getAlarmPanelUniqueIdent())

        self.logstate_debug("Received command request - user approved")
        #if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>" or str(type(call.data)) == "<class 'homeassistant.util.read_only_dict.ReadOnlyDict'>":
        if isinstance(call.data, dict):
            command = call.data[CONF_COMMAND]
            code = ""
            if ATTR_CODE in call.data:
                code = call.data[ATTR_CODE]
                # If the code is defined then it must be a 4 digit string
                if len(code) > 0 and not re.search(PIN_REGEX, code):
                    code = "0000"
            try:
                self.sendCommand("Alarm Service Call " + str(command), command, code)
            except Exception as ex:
                self.logstate_warning("Not making command request {0} {1}  Exception {2}".format(type(call.data), call.data, ex) )
                #self.logstate_debug(ex)
        else:
            self.logstate_debug("Not making command request %s %s", type(call.data), call.data )

    async def connect(self):
        """Connect to the alarm panel using the pyvisonic library."""
        try:
            # Establish a callback to stop the component when the stop event occurs
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self.stop_subscription
            )
            await self.service_panel_start(True)

        except (ConnectTimeout, HTTPError) as ex:
            createWarningMessage(
                AvailableNotifications.CONNECTION_PROBLEM,
                "Visonic Panel Connection Error: {}<br />"
                "You will need to restart hass after fixing."
                "".format(ex))
            #self.logstate_debug("Unable to connect to Visonic Alarm Panel: %s", str(ex))
            #self.hass.components.persistent_notification.create(
            #    "Error: {}<br />"
            #    "You will need to restart hass after fixing."
            #    "".format(ex),
            #    title=NOTIFICATION_TITLE,
            #    notification_id=NOTIFICATION_ID,
            #)

        return False
