"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""

import asyncio
from collections import defaultdict
import logging
from time import sleep
from typing import Union, Any

from jinja2 import Environment, FileSystemLoader
from .pyvisonic import (
    PYVConst,
    LogPanelEvent,
    SensorDevice,
    X10Device,
    async_create_tcp_visonic_connection,
    async_create_usb_visonic_connection,
)
from .pconst import (
    CONF_ARM_CODE_AUTO,
    CONF_AUTO_SYNC_TIME,
    CONF_B0_ENABLE_MOTION_PROCESSING,
    CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT,
    CONF_B0_MIN_TIME_BETWEEN_TRIGGERS,
    CONF_DEVICE_BAUD,
    CONF_DEVICE_TYPE,
    CONF_DOWNLOAD_CODE,
    CONF_ENABLE_REMOTE_ARM,
    CONF_ENABLE_REMOTE_DISARM,
    CONF_ENABLE_SENSOR_BYPASS,
    CONF_EXCLUDE_SENSOR,
    CONF_EXCLUDE_X10,
    CONF_FORCE_AUTOENROLL,
    CONF_FORCE_KEYPAD,
    CONF_FORCE_STANDARD,
    CONF_INSTANT_ARM_AWAY,
    CONF_INSTANT_ARM_HOME,
    CONF_LANGUAGE,
    CONF_MOTION_OFF_DELAY,
    CONF_OVERRIDE_CODE,
    CONF_SIREN_SOUNDING,
)
from requests import ConnectTimeout, HTTPError
import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_CODE,
    CONF_HOST,
    CONF_PATH,
    CONF_PORT,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import dispatcher_send

from .const import (
    ALARM_PANEL_CHANGE_EVENT,
    ALARM_PANEL_ENTITY,
    ALARM_PANEL_LOG_FILE_COMPLETE,
    ALARM_PANEL_LOG_FILE_ENTRY,
    CONF_LOG_CSV_FN,
    CONF_LOG_CSV_TITLE,
    CONF_LOG_DONE,
    CONF_LOG_EVENT,
    CONF_LOG_MAX_ENTRIES,
    CONF_LOG_REVERSE,
    CONF_LOG_XML_FN,
    DOMAIN,
    DOMAINDATA,
    NOTIFICATION_ID,
    NOTIFICATION_TITLE,
    VISONIC_UPDATE_STATE_DISPATCHER,
    CONF_ALARM_NOTIFICATIONS,
    AvailableNotifications,
)

CLIENT_VERSION = "0.6.4.1"

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
        vol.Required(CONF_COMMAND, default="Armed"): cv.string,
        vol.Optional(ATTR_CODE, default=""): cv.string,
    }
)


class VisonicClient:
    """Set up for Visonic devices."""

    _LOGGER.debug("Initialising Client - Version {0}".format(CLIENT_VERSION))

    def __init__(self, hass: HomeAssistant, cf: dict, entry: ConfigEntry):
        """Initialize the Visonic Client."""
        self.hass = hass
        self.entry = entry
        # Get the user defined config
        self.config = cf.copy()

        _LOGGER.debug("init self.config = %s  %s", PYVConst.DownloadCode, self.config)

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
        self.visonicProtocol = None
        self.SystemStarted = False

        # variables for creating the event log for csv and xml
        self.csvdata = None
        self.templatedata = None

        # Create empty lists
        self.hass.data[DOMAIN]["binary_sensor"] = list()
        self.hass.data[DOMAIN]["switch"] = list()
        self.hass.data[DOMAIN]["alarm_control_panel"] = list()

        _LOGGER.debug(
            "Exclude sensor list = %s     Exclude x10 list = %s",
            self.exclude_sensor_list,
            self.exclude_x10_list,
        )

    def sendHANotification(self, condition : AvailableNotifications, message: str):
        notification_config = self.config.get(CONF_ALARM_NOTIFICATIONS, [] )
        #_LOGGER.debug("condition is {0}    notification_config {1}".format(condition, notification_config))
        if condition == AvailableNotifications.ALWAYS or condition in notification_config:
            #_LOGGER.debug("   condition {0} is in there".format(condition))
            self.hass.components.persistent_notification.create(
                message, title=NOTIFICATION_TITLE, notification_id=NOTIFICATION_ID
            )

    def createWarningMessage(self, condition : AvailableNotifications, message: str):
        """Create a Warning message in the log file and a notification on the HA Frontend."""
        _LOGGER.warning(message)
        self.sendHANotification(condition, message)

    def isSirenActive(self) -> bool:
        """Is the siren active."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isSirenActive()
        return False

    def isPowerMaster(self) -> bool:
        """Is it a PowerMaster panel."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isPowerMaster()
        return False

    def isForceKeypad(self) -> bool:
        """Is it a PowerMaster panel."""
        return self.toBool(self.config.get(CONF_FORCE_KEYPAD, False))

    def isArmWithoutCode(self) -> bool:
        """Is it a PowerMaster panel."""
        return self.toBool(self.config.get(CONF_ARM_CODE_AUTO, False))

    def isArmAwayInstant(self) -> bool:
        """Is it a PowerMaster panel."""
        return self.toBool(self.config.get(CONF_INSTANT_ARM_AWAY, False))

    def isArmHomeInstant(self) -> bool:
        """Is it a PowerMaster panel."""
        return self.toBool(self.config.get(CONF_INSTANT_ARM_HOME, False))

    def isRemoteArm(self) -> bool:
        """Is it a PowerMaster panel."""
        return self.toBool(self.config.get(CONF_ENABLE_REMOTE_ARM, False))

    def isRemoteDisarm(self) -> bool:
        """Is it a PowerMaster panel."""
        return self.toBool(self.config.get(CONF_ENABLE_REMOTE_DISARM, False))

    def getPanelStatusCode(self) -> int:
        """Get the panel status code."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelStatusCode()
        return -1

    def getPanelMode(self) -> str:
        """Get the panel mode."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelMode()
        return "Not Connected"

    def getPanelStatus(self) -> dict:
        """Get the panel status."""
        if self.visonicProtocol is not None:
            pd = self.visonicProtocol.getPanelStatus()
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
            _LOGGER.debug(
                "client process_command called %s   type is %s",
                command,
                type(self.visonicProtocol),
            )
            self.visonicProtocol.process_command(command)
        else:
            _LOGGER.warning("[VisonicClient] The pyvisonic command is None")

    def process_panel_event_log(self, event_log_entry: LogPanelEvent):
        """Process a sequence of panel log events."""
        # import custom_components.visonic.pyvisonic as visonicApi  # Connection to python Library

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
                    "current": current,
                    "total": total,
                    "date": event_log_entry.date,
                    "time": event_log_entry.time,
                    "partition": event_log_entry.partition,
                    "zone": event_log_entry.zone,
                    "event": event_log_entry.event,
                },
            )
        _LOGGER.debug("Panel Event - fired Single Item event")
        # Write out to an xml file
        if event_log_entry.current == 1:
            self.templatedata = []
            self.csvdata = ""

        if self.csvdata is not None:
            _LOGGER.debug("Panel Event - Saving csv data")
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

            _LOGGER.debug("Panel Event - Saving xml data")
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
                _LOGGER.debug(
                    "Panel Event - Received last entry  reverse=%s  xmlfilenamelen=%s csvfilenamelen=%s",
                    str(reverse),
                    len(self.config.get(CONF_LOG_XML_FN)),
                    len(self.config.get(CONF_LOG_CSV_FN)),
                )
                # create a new XML file with the results
                if len(self.config.get(CONF_LOG_XML_FN)) > 0:
                    _LOGGER.debug(
                        "Panel Event - Starting xml save filename %s",
                        str(self.config.get(CONF_LOG_XML_FN)),
                    )
                    if reverse:
                        self.templatedata.reverse()
                    try:
                        _LOGGER.debug(
                            "Panel Event - Setting up xml file loader - path = %s",
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
                        _LOGGER.debug(
                            "Panel Event - Setting up xml - getting the template"
                        )
                        template = env.get_template("visonic_template.xml")
                        output = template.render(
                            entries=self.templatedata,
                            total=total,
                            available="{0}".format(event_log_entry.total),
                        )

                        with open(self.config.get(CONF_LOG_XML_FN), "w") as f:
                            _LOGGER.debug("Panel Event - Writing xml file")
                            f.write(output.rstrip())
                            _LOGGER.debug("Panel Event - Closing xml file")
                            f.close()
                    except (IOError, AttributeError, TypeError):
                        self.createWarningMessage(
                            AvailableNotifications.EVENTLOG_PROBLEM,
                            "Panel Event Log - Failed to write XML file"
                        )

                _LOGGER.debug("Panel Event - CSV File Creation")

                if len(self.config.get(CONF_LOG_CSV_FN)) > 0:
                    try:
                        _LOGGER.debug(
                            "Panel Event - Starting csv save filename %s",
                            self.config.get(CONF_LOG_CSV_FN),
                        )
                        if self.toBool(self.config.get(CONF_LOG_CSV_TITLE)):
                            _LOGGER.debug("Panel Event - Adding header to string")
                            self.csvdata = (
                                "current, total, partition, date, time, zone, event\n"
                                + self.csvdata
                            )
                        _LOGGER.debug("Panel Event - Opening csv file")
                        with open(self.config.get(CONF_LOG_CSV_FN), "w") as f:
                            _LOGGER.debug("Panel Event - Writing csv file")
                            f.write(self.csvdata.rstrip())
                            _LOGGER.debug("Panel Event - Closing csv file")
                            f.close()
                    except (IOError, AttributeError, TypeError):
                        self.createWarningMessage(
                            AvailableNotifications.EVENTLOG_PROBLEM,
                            "Panel Event Log - Failed to write CSV file"
                        )

                _LOGGER.debug("Panel Event - Clear data ready for next time")
                self.csvdata = None
                if self.toBool(self.config.get(CONF_LOG_DONE)):
                    _LOGGER.debug("Panel Event - Firing Completion Event")
                    self.hass.bus.fire(
                        ALARM_PANEL_LOG_FILE_COMPLETE,
                        {"total": total, "available": event_log_entry.total},
                    )
                _LOGGER.debug("Panel Event - Complete")

    def addSensor(self, dev: SensorDevice) -> bool:
        """Process a new sensor."""
        changedlist = False
        if dev.getDeviceID() is None:
            _LOGGER.debug("     Sensor ID is None")
        else:
            _LOGGER.debug("     Sensor %s", str(dev))
            if dev.getDeviceID() not in self.exclude_sensor_list:
                if dev not in self.hass.data[DOMAIN]["binary_sensor"]:
                    # _LOGGER.debug("       Added to dispatcher")
                    # async_dispatcher_send(self.hass, "visonic_new_binary_sensor", dev)
                    changedlist = True
                    self.hass.data[DOMAIN]["binary_sensor"].append(dev)
                else:
                    _LOGGER.debug(
                        "       Sensor %s already in the list", dev.getDeviceID()
                    )
            else:
                _LOGGER.debug("       Sensor %s in exclusion list", dev.getDeviceID())
        return changedlist

    def addX10(self, dev: X10Device) -> bool:
        """Process a new x10."""
        changedlist = False
        # _LOGGER.debug("VS: X10 Switch list %s", dev)
        if dev.enabled and dev.getDeviceID() not in self.exclude_x10_list:
            if dev not in self.hass.data[DOMAIN]["switch"]:
                changedlist = True
                self.hass.data[DOMAIN]["switch"].append(dev)
            else:
                _LOGGER.debug("      X10 %s already in the list", dev.getDeviceID())
        return changedlist

    def process_new_devices(self, visonic_devices: defaultdict):
        """Process new devices (sensors and x10)."""
        #_LOGGER.debug(
        #    "Exclude sensor list = %s     Exclude x10 list = %s",
        #    self.exclude_sensor_list,
        #    self.exclude_x10_list,
        #)
        # Process new sensors
        if len(visonic_devices["sensor"]) > 0:
            changedlist = False
            for dev in visonic_devices["sensor"]:
                tmp = self.addSensor(dev)
                changedlist = changedlist or tmp
            _LOGGER.debug(
                "Visonic: %s binary sensors",
                len(self.hass.data[DOMAIN]["binary_sensor"]),
            )
            if changedlist:
                self.hass.async_create_task(
                    self.hass.config_entries.async_forward_entry_setup(
                        self.entry, "binary_sensor"
                    )
                )

        # Process new X10 switches
        if len(visonic_devices["switch"]) > 0:
            changedlist = False
            for dev in visonic_devices["switch"]:
                tmp = self.addX10(dev)
                changedlist = changedlist or tmp
            _LOGGER.debug("Visonic: %s switches", len(self.hass.data[DOMAIN]["switch"]))
            if changedlist:
                self.hass.async_create_task(
                    self.hass.config_entries.async_forward_entry_setup(
                        self.entry, "switch"
                    )
                )

    def generate_ha_bus_event(self, event_id: int, datadictionary: dict):
        """Generate HA Bus Event."""
        # The event_id is in the range 0 to 14 inclusive
        #   When it is set to 0, any of the possible changes have been made in the sensors/X10 devices
        #   So use any value of event_id to fire an HA event to get the sensors to update themselves
        dispatcher_send(
            self.hass, VISONIC_UPDATE_STATE_DISPATCHER, event_id, datadictionary
        )

        # Send an event on the event bus for conditions 1 to 14.  Ignore 0 as this is used for any sensor change.
        # General update trigger
        #    1 is a zone update,
        #    2 is a panel update AND the alarm is not active,
        #    3 is a panel update AND the alarm is active,
        #    4 is the panel has been reset,
        #    5 is pin rejected,
        #    6 is tamper triggered
        #    7 is download timer expired
        #    8 is watchdog timer expired, give up trying to achieve a better mode, resorting to Standard Mode
        #    9 is watchdog timer expired, going to try again to get a better mode
        #   10 is a comms problem, we have received no data so plugin has suspended itself (following starting up)
        #   11 is local feedback on arm/disarm (this is not from the visonic panel)
        #   12 is local feedback on bypass (this is not from the visonic panel)
        #   13 is local feedback on event log (this is not from the visonic panel)
        #   14 is local feedback on X10 (this is not from the visonic panel)
        #   15 is something other than the pin code has been rejected by the panel (see 5 above)
        tmp = int(event_id)
        if 1 <= tmp <= 14: # do not send 0 or 15 as an HA event

            tmpdict = {}
            if datadictionary is not None:
                tmpdict = datadictionary.copy()

            tmpdict["condition"] = tmp
            _LOGGER.debug("Visonic update event %s %s", tmp, tmpdict)
            self.hass.bus.fire(ALARM_PANEL_CHANGE_EVENT, tmpdict)

#        if tmp == 0: 
#            self.sendHANotification(AvailableNotifications.ALWAYS, "Connection Not Established - There may be a problem with the Integration or its Configuration (Ethernet or USB settings)" )
        if tmp == 3: 
            self.sendHANotification(AvailableNotifications.SIREN, "Siren is Sounding, Alarm has been Activated" )
        elif tmp == 4:
            self.sendHANotification(AvailableNotifications.RESET, "The Panel has been Reset" )
        elif tmp == 5:
            self.sendHANotification(AvailableNotifications.INVALID_PIN, "The Pin Code has been Rejected By the Panel" )
        elif tmp == 6:
            self.sendHANotification(AvailableNotifications.TAMPER, "The Panel has been Tampered" )
        elif tmp == 7:
            self.sendHANotification(AvailableNotifications.PANEL_OPERATION, "Panel Data download timeout, StandardMode Selected" )
        elif tmp == 8 or tmp == 9:
            self.sendHANotification(AvailableNotifications.CONNECTION_PROBLEM, "Communication Timeout - Perhaps there is a problem with the connection to the Alarm Panel" )
        elif tmp == 10:
            self.sendHANotification(AvailableNotifications.CONNECTION_PROBLEM, "Failed to connect to your Visonic Alarm. We have not received any data from the panel" )
        elif tmp == 15:
            self.sendHANotification(AvailableNotifications.ALWAYS, "Operation Rejected By Panel (tell the Integration Author and upload a debug log file if you're able to)" )

    def visonic_event_callback_handler(
        self,
        visonic_devices: Union[
            None, LogPanelEvent, SensorDevice, X10Device, defaultdict, int
        ],
        datadictionary: dict,
    ):
        """Visonic callback function called from the pyvisonic library."""
        #  There are several reasons when this is called:
        #     When a new sensor or X10 switch is detected/created.
        #         it adds it to the list of devices and then calls discovery to fully create it in HA
        #         remember that all the sensors may not be created at the same time
        #     For log file creation and processing
        #     To create an ha event on the event bus

        # Check to ensure variables are set correctly
        if self.hass is None:
            _LOGGER.warning("Visonic attempt to add device when hass is undefined")
            return
        if visonic_devices is None:
            _LOGGER.warning("Visonic attempt to add device when sensor is undefined")
            return

        #if not self.SystemStarted:
        # Removed as we want to send HA events and call the HA dispatcher whether the panel is connected or not
        #    _LOGGER.warning("Visonic Panel Request to callback handler when system not started {0} {1}".format(type(visonic_devices), visonic_devices))
        #    # Try to get the asyncio Coroutine within the Task to shutdown the serial link connection properly
        #    if self.visonicProtocol is not None:
        #        _LOGGER.debug("     Shutting Down Protocol")
        #        self.visonicProtocol.ShutdownOperation()
        #        self.visonicProtocol = None
        #    return

        # Is the passed in data a dictionary full of X10 switches and sensors
        if type(visonic_devices) == defaultdict:
            # a set of sensors and/or switches.
            # _LOGGER.debug("Visonic got new sensors %s", visonic_devices["sensor"] )
            self.process_new_devices(visonic_devices)

        # elif type(visonic_devices) == SensorDevice:
        #    # This is an update of an existing sensor device
        #    _LOGGER.debug("Individual Sensor update %s not yet included", visonic_devices)

        # elif type(visonic_devices) == X10Device:
        #    # This is an update of an existing x10 device
        #    _LOGGER.debug("Individual X10 update %s not yet included", visonic_devices)

        elif type(visonic_devices) == LogPanelEvent:
            # This is an event log
            _LOGGER.debug("Panel Event Log %s", visonic_devices)
            self.process_panel_event_log(visonic_devices)

        elif type(visonic_devices) == int:
            self.generate_ha_bus_event(visonic_devices, datadictionary)

        else:
            _LOGGER.warning(
                "Visonic attempt to add device with type %s  device is %s",
                type(visonic_devices),
                visonic_devices,
            )

    def toBool(self, val: Any) -> bool:
        """Convert value to boolean."""
        if type(val) == bool:
            return val
        elif type(val) == int:
            return val != 0
        elif type(val) == str:
            v = val.lower()
            return not (v == "no" or v == "false" or v == "0")
        _LOGGER.warning(
            "Visonic unable to decode boolean value %s    type is %s", val, type(val)
        )
        return False

    def getConfigData(self) -> dict:
        """Create a dictionary full of the configuration data."""
        return {
            PYVConst.DownloadCode: self.config.get(CONF_DOWNLOAD_CODE, ""),
            PYVConst.ForceStandard: self.toBool(
                self.config.get(CONF_FORCE_STANDARD, False)
            ),
            PYVConst.ForceAutoEnroll: self.toBool(
                self.config.get(CONF_FORCE_AUTOENROLL, True)
            ),
            PYVConst.AutoSyncTime: self.toBool(
                self.config.get(CONF_AUTO_SYNC_TIME, True)
            ),
            PYVConst.PluginLanguage: self.config.get(CONF_LANGUAGE, "EN"),
            PYVConst.MotionOffDelay: self.config.get(CONF_MOTION_OFF_DELAY, 120),
            PYVConst.SirenTriggerList: self.config.get(
                CONF_SIREN_SOUNDING, ["Intruder"]
            ),
            PYVConst.B0_Enable: self.toBool(
                self.config.get(CONF_B0_ENABLE_MOTION_PROCESSING, False)
            ),
            PYVConst.B0_Min_Interval_Time: self.config.get(
                CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, 5
            ),
            PYVConst.B0_Max_Wait_Time: self.config.get(
                CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, 30
            ),
        }

    def updateConfig(self, conf: dict = None):
        """Update the dictionary full of configuration data."""
        if conf is not None:
            self.config.update(conf)
        if self.visonicProtocol is not None:
            self.visonicProtocol.updateSettings(self.getConfigData())

    async def connect_to_alarm(self) -> bool:
        """Create the connection to the alarm panel."""
        # import pyvisonic as visonicApi  # Connection to python Library

        # Is the system already running and connected
        if self.SystemStarted:
            return False

        # remove any existing visonic related sensors (so we don't get entity id already exists exceptions on a restart)
        # sensor_list = self.hass.states.async_entity_ids("binary_sensor")
        # if sensor_list is not None:
        #    for x in sensor_list:
        #        _LOGGER.debug("Checking HA Entity Sensor ID: %s", x)
        #        if x.lower().startswith("binary_sensor.visonic_z"):
        #            _LOGGER.debug("   Removed existing HA Entity Sensor ID: %s", x)
        #            self.hass.add_job(self.hass.states.async_remove(x))

        # remove any existing visonic related switches (so we don't get entity id already exists exceptions on a restart)
        # switch_list = self.hass.states.async_entity_ids("switch")
        # if switch_list is not None:
        #    for x in switch_list:
        #        _LOGGER.debug("Checking HA Entity Switch ID: %s", x)
        #        if x.lower().startswith("switch.visonic_x"):
        #            _LOGGER.debug("   Removed existing HA Entity Switch ID: %s", x)
        #            self.hass.add_job(self.hass.states.async_remove(x))

        # Empty out the lists
        self.hass.data[DOMAIN]["binary_sensor"] = list()
        self.hass.data[DOMAIN]["switch"] = list()
        self.hass.data[DOMAIN]["alarm_control_panel"] = list()

        # set up config parameters in the visonic library
        self.hass.data[DOMAIN][DOMAINDATA][
            "Exception Count"
        ] = self.panel_exception_counter

        _LOGGER.debug("connect_to_alarm self.config = %s", self.config)

        # Get Visonic specific configuration.
        device_type = self.config.get(CONF_DEVICE_TYPE)

        _LOGGER.debug(
            "Visonic Connection Device Type is %s %s", device_type, self.getConfigData()
        )

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
                event_callback=self.visonic_event_callback_handler,
                disconnect_callback=self.disconnect_callback,
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
                event_callback=self.visonic_event_callback_handler,
                disconnect_callback=self.disconnect_callback,
                loop=self.hass.loop,
            )

        if self.visonicTask is not None and self.visonicProtocol is not None:
            # Connection to the panel has been initially successful
            # Record that we have started the system
            self.SystemStarted = True
            return True

        self.createWarningMessage(
            AvailableNotifications.CONNECTION_PROBLEM,
            "Failed to connect into Visonic Alarm. Check Settings."
        )

        if self.visonicTask is not None:
            _LOGGER.debug("          ........... Closing down Current Task")
            self.visonicTask.cancel()

        if self.visonicProtocol is not None:
            _LOGGER.debug("          ........... Shutting Down Protocol")
            self.visonicProtocol.ShutdownOperation()

        self.visonicTask = None
        self.visonicProtocol = None
        self.SystemStarted = False
        
        await asyncio.sleep(0.5)
        return False

    async def service_comms_stop(self):
        """Service call to close down the current serial connection, we need to reset the whole connection."""
        if not self.SystemStarted:
            _LOGGER.debug("Request to Stop the Comms and it is already stopped")
            return

        # Try to get the asyncio Coroutine within the Task to shutdown the serial link connection properly
        if self.visonicProtocol is not None:
            self.visonicProtocol.ShutdownOperation()
        self.visonicProtocol = None
        await asyncio.sleep(0.5)
        # not a mistake, wait a bit longer to make sure it's closed as we get no feedback (we only get the fact that the queue is empty)

    async def service_panel_stop(self):
        """Service call to stop the connection."""
        if not self.SystemStarted:
            _LOGGER.debug(
                "Request to Stop the HA alarm_control_panel and it is already stopped"
            )
            return

        # stop the usb/ethernet comms with the panel
        await self.service_comms_stop()

        # unload the alarm, sensors and the switches
        await self.hass.config_entries.async_forward_entry_unload(
            self.entry, "binary_sensor"
        )
        await self.hass.config_entries.async_forward_entry_unload(
            self.entry, "alarm_control_panel"
        )
        await self.hass.config_entries.async_forward_entry_unload(self.entry, "switch")

        # cancel the task from within HA
        if self.visonicTask is not None:
            _LOGGER.debug("          ........... Closing down Current Task")
            self.visonicTask.cancel()
            await asyncio.sleep(2.0)
            if self.visonicTask.done():
                _LOGGER.debug("          ........... Current Task Done")
            else:
                _LOGGER.debug("          ........... Current Task Not Done")
        else:
            _LOGGER.debug("          ........... Current Task not set")
        self.visonicTask = None
        self.SystemStarted = False

    async def service_panel_start(self):
        """Service call to start the connection."""
        if self.SystemStarted:
            _LOGGER.warning(
                "Request to Start the HA alarm_control_panel and it is already running"
            )
            return

        self.visonicTask = None
        self.visonicProtocol = None

        # _LOGGER.debug("........... attempting connection")

        if await self.connect_to_alarm():
            # if not alarm_entity_exists:
            self.hass.async_create_task(
                self.hass.config_entries.async_forward_entry_setup(
                    self.entry, "alarm_control_panel"
                )
            )

    async def service_panel_reconnect(self, call):
        """Service call to re-connect the connection."""
        if call.context.user_id:
            user = await self.hass.auth.async_get_user(call.context.user_id)

            if user is None:
                raise UnknownUser(
                    context=call.context,
                    entity_id=ALARM_PANEL_ENTITY,
                    permission=POLICY_CONTROL,
                )

            if not user.permissions.check_entity(ALARM_PANEL_ENTITY, POLICY_CONTROL):
                raise Unauthorized(
                    context=call.context,
                    entity_id=ALARM_PANEL_ENTITY,
                    permission=POLICY_CONTROL,
                )

        _LOGGER.debug("User has requested visonic panel reconnection")
        await self.service_panel_stop()
        await self.service_panel_start()

    async def disconnect_callback_async(self):
        """Service call to disconnect."""
        _LOGGER.debug(" ........... terminating connection and setting up reconnection")
        await asyncio.sleep(1.0)
        await self.service_panel_stop()
        await asyncio.sleep(3.0)
        _LOGGER.debug(" ........... attempting reconnection")
        await self.service_panel_start()

    def stop_subscription(self, event):
        """Shutdown Visonic subscriptions and subscription thread on exit."""
        _LOGGER.debug("Shutting down subscriptions")
        asyncio.ensure_future(self.service_panel_stop(), loop=self.hass.loop)

    def disconnect_callback(self, excep):
        """Disconnection Callback for connection disruption to the panel."""
        if excep is None:
            _LOGGER.debug(
                "PyVisonic has caused an exception, no exception information is available"
            )
        else:
            _LOGGER.debug("PyVisonic has caused an exception %s", str(excep))

        # General update trigger
        #    0 is a disconnect and (hopefully) reconnect from an exception (probably comms related)
        self.hass.bus.fire(ALARM_PANEL_CHANGE_EVENT, {"condition": 0})

        self.panel_exception_counter = self.panel_exception_counter + 1
        asyncio.ensure_future(self.disconnect_callback_async(), loop=self.hass.loop)

    # pmGetPin: Convert a PIN given as 4 digit string in the PIN PDU format as used in messages to powermax
    def pmGetPin(self, pin: str, forcedKeypad: bool, doingBypass : bool):
        """Get pin code."""
        #_LOGGER.debug("Getting Pin Start")
        if pin is None or pin == "" or len(pin) != 4:
            psc = self.getPanelStatusCode()
            panelmode = self.getPanelMode()
            #_LOGGER.debug("Getting Pin")
            if self.isArmWithoutCode() and psc == 0 and self.hasValidOverrideCode():
                # Panel currently disarmed, arm without user code, override is set and valid
                #_LOGGER.debug("Here A")
                return True, self.config.get(CONF_OVERRIDE_CODE, "")
            elif not doingBypass and self.isArmWithoutCode() and psc == 0:
                # Panel currently disarmed, arm without user code, so use any code
                #_LOGGER.debug("Here B")
                return True, "0000"
            elif forcedKeypad:
                # this is used to catch the condition that the keypad is used but an invalid
                #     number of digits has been entered
                #_LOGGER.debug("Here D")
                return False, None
            elif self.hasValidOverrideCode():
                # The override is set and valid
                #_LOGGER.debug("Here C")
                return True, self.config.get(CONF_OVERRIDE_CODE, "")
            elif panelmode == "Powerlink" or panelmode == "Standard Plus":
                # Powerlink or StdPlus and so we downloaded the pin codes
                #_LOGGER.debug("Here E")
                return True, None
            elif not doingBypass and self.isArmWithoutCode():
                # Here to prevent the warning to the log file
                #_LOGGER.debug("Here F")
                return False, None
            else:
                _LOGGER.warning("Warning: Valid 4 digit PIN not found")
                return False, None
        return True, pin

    messageDict = {
        0: "Success",
        1: "Not supported when downloading EPROM",
        2: "Not allowed without valid pin",
        3: "Disabled by user settings",
        4: "Invalid state requested",
        5: "General X10 Problem",
        6: "Disabled by panel settings",
    }

    def generateBusEventReason(
        self, event_id: int, reason: int, command: str, message: str
    ):
        """Generate an HA Bus Event with a Reason Code."""
        datadict = {}
        if self.visonicProtocol is not None:
            datadict = self.visonicProtocol.PopulateDictionary(command)
        datadict["Reason"] = reason
        datadict["Message"] = message + " " + self.messageDict[reason]

        self.generate_ha_bus_event(event_id, datadict)

        _LOGGER.debug("[" + message + "]  " + str(reason) + "  " + self.messageDict[reason])

        if reason > 0:
            self.sendHANotification(AvailableNotifications.COMMAND_NOT_SENT, "" + message + " " + self.messageDict[reason])

    def sendCommand(self, command: str, code: str) -> bool:
        """Send a command to the panel."""
        vp = self.visonicProtocol
        if vp is not None:
            command = command.lower()
            _LOGGER.debug("sendCommand to Visonic Alarm Panel: %s", command)

            isValidPL, pin = self.pmGetPin(pin = code, forcedKeypad = self.isForceKeypad(), doingBypass = False)

            # Do this test here as it is not applicable to pin settings for bypass and event log
            if not isValidPL and self.isArmWithoutCode() and command != "disarmed":
                # if we dont have pin codes and we can arm without a code and we're arming and arming is allowed
                isValidPL = True
                pin = "0000"

            if isValidPL:
                if (command == "disarmed" and self.isRemoteDisarm()) or (
                    command != "disarmed" and self.isRemoteArm()
                ):
                    success = vp.RequestArm(command, pin)
                    self.generateBusEventReason(11, success, command, "Request Arm/Disarm")
                else:
                    self.generateBusEventReason(11, 3, command, "Request Arm/Disarm")
            else:
                self.generateBusEventReason(11, 2, command, "Request Arm/Disarm")
            return True
        else:
            return False

    def sendBypass(self, devid: int, bypass: bool, code: str) -> bool:
        """Send the bypass command to the panel."""
        vp = self.visonicProtocol
        if vp is not None:
            if self.toBool(self.config.get(CONF_ENABLE_SENSOR_BYPASS, False)):
                isValidPL, pin = self.pmGetPin(pin = code, forcedKeypad = False, doingBypass = True)
                if isValidPL:
                    success = vp.SetSensorArmedState(devid, bypass, pin)
                    self.generateBusEventReason(12, success, "Bypass", "Sensor Arm State")
                    # success is an integer that is 0 on sending the command to the panel
                    return success == 0
                else:
                    self.generateBusEventReason(12, 2, "Bypass", "Sensor Arm State")
            else:
                self.generateBusEventReason(12, 3, "Bypass", "Sensor Arm State")
        return False

    def setX10(self, ident: int, state: str):
        """Send an X10 command to the panel."""
        # ident in range 0 to 15, state can be one of "off", "on", "dim", "brighten"
        if self.visonicProtocol is not None:
            success = self.visonicProtocol.setX10(ident, state)
            self.generateBusEventReason(14, success, "X10", "Send X10 Command")

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
        # Are we just starting up
        armcode = self.getPanelStatusCode()
        if armcode is None or armcode == -1:
            _LOGGER.debug("isPanelConnected: code format none as armcode is none (panel starting up?)")
            return False
        return True

    def isCodeRequired(self) -> bool:
        """Determine if a user code is required given the panel mode and user settings."""
        # try powerlink or standard plus mode first, then it already has the user codes
        panelmode = self.getPanelMode()
        # _LOGGER.debug("code format panel mode %s", panelmode)
        if not self.isForceKeypad() and panelmode is not None:
            if panelmode == "Powerlink" or panelmode == "Standard Plus":
                _LOGGER.debug("No Key Panel as powerlink or std plus ********")
                return False

        armcode = self.getPanelStatusCode()
        if armcode is None or armcode == -1:
            return True

        # If currently Disarmed and user setting to not show panel to arm
        if armcode == 0 and self.isArmWithoutCode():
            _LOGGER.debug("No Key Panel as disarmed and user arm without code")
            return False

        if self.isForceKeypad():
            _LOGGER.debug("Key Panel as force numeric keypad set in config")
            return True

        if self.hasValidOverrideCode():
            _LOGGER.debug("No Key Panel as code set in config")
            return False

        _LOGGER.debug("Key Panel")
        return True

    async def service_panel_eventlog(self, call):
        """Service call to retrieve the event log from the panel. This currently just gets dumped in the HA log file."""
        _LOGGER.debug("alarm control panel received event log request")
        if call.context.user_id:
            user = await self.hass.auth.async_get_user(call.context.user_id)

            if user is None:
                raise UnknownUser(
                    context=call.context,
                    entity_id=ALARM_PANEL_ENTITY,
                    permission=POLICY_READ,
                )

            if not user.permissions.check_entity(ALARM_PANEL_ENTITY, POLICY_READ):
                raise Unauthorized(
                    context=call.context,
                    entity_id=ALARM_PANEL_ENTITY,
                    permission=POLICY_READ,
                )

        _LOGGER.debug("alarm control panel received event log request - user approved")
        if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>":
            code = ""
            if ATTR_CODE in call.data:
                code = call.data[ATTR_CODE]
            _LOGGER.debug("alarm control panel making event log request")

            if self.visonicProtocol is not None:
                success = self.visonicProtocol.GetEventLog(self.decode_code(code))
                self.generateBusEventReason(13, success, "EventLog", "Event Log")

        else:
            _LOGGER.debug(
                "alarm control panel not making event log request %s %s",
                type(call.data),
                call.data,
            )

    async def service_panel_command(self, call):
        """Service call to send a commandto the panel."""
        _LOGGER.debug("alarm control panel received command request")
        if call.context.user_id:
            user = await self.hass.auth.async_get_user(call.context.user_id)

            if user is None:
                raise UnknownUser(
                    context=call.context,
                    entity_id=ALARM_PANEL_ENTITY,
                    permission=POLICY_CONTROL,
                )

            # _LOGGER.debug("user={0}".format(user))
            if not user.permissions.check_entity(ALARM_PANEL_ENTITY, POLICY_CONTROL):
                raise Unauthorized(
                    context=call.context,
                    entity_id=ALARM_PANEL_ENTITY,
                    permission=POLICY_CONTROL,
                )

        _LOGGER.debug("alarm control panel received command request - user approved")
        if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>":
            code = ""
            if ATTR_CODE in call.data:
                code = call.data[ATTR_CODE]
            command = call.data[CONF_COMMAND]
            # _LOGGER.debug('alarm control panel got command %s', command)
            self.sendCommand(command, self.decode_code(code))
        else:
            _LOGGER.debug(
                "alarm control panel not making command request %s %s",
                type(call.data),
                call.data,
            )

    async def connect(self):
        """Connect to the alarm panel using the pyvisonic library."""
        try:
            # Establish a callback to stop the component when the stop event occurs
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self.stop_subscription
            )

            self.hass.services.async_register(
                DOMAIN, "alarm_panel_reconnect", self.service_panel_reconnect
            )
            self.hass.services.async_register(
                DOMAIN,
                "alarm_panel_eventlog",
                self.service_panel_eventlog,
                schema=ALARM_SERVICE_EVENTLOG,
            )
            self.hass.services.async_register(
                DOMAIN,
                "alarm_panel_command",
                self.service_panel_command,
                schema=ALARM_SERVICE_COMMAND,
            )
            await self.service_panel_start()

        except (ConnectTimeout, HTTPError) as ex:
            createWarningMessage(
                AvailableNotifications.CONNECTION_PROBLEM,
                "Visonic Panel Connection Error: {}<br />"
                "You will need to restart hass after fixing."
                "".format(ex))
            #_LOGGER.debug("Unable to connect to Visonic Alarm Panel: %s", str(ex))
            #self.hass.components.persistent_notification.create(
            #    "Error: {}<br />"
            #    "You will need to restart hass after fixing."
            #    "".format(ex),
            #    title=NOTIFICATION_TITLE,
            #    notification_id=NOTIFICATION_ID,
            #)

        return False
