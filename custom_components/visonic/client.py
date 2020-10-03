""" Create a connection to a Visonic PowerMax or PowerMaster Alarm System """
import logging
import voluptuous as vol
import asyncio
import jinja2

from jinja2 import Environment, FileSystemLoader
from collections import defaultdict

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.util.dt import utc_from_timestamp
from homeassistant.util import convert, slugify
from homeassistant.helpers import discovery
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.const import __version__
from homeassistant.const import (
    ATTR_CODE,
    ATTR_ARMED,
    EVENT_HOMEASSISTANT_STOP,
    CONF_HOST,
    CONF_PORT,
    CONF_PATH,
    CONF_DEVICE,
)
from homeassistant.helpers.entity import Entity
from homeassistant.core import HomeAssistant

from requests import ConnectTimeout, HTTPError
from time import sleep

from .const import *
from .pyvisonic import PYVConst

# Try to get the dispatcher working
# from homeassistant.helpers.dispatcher import (
#    async_dispatcher_connect,
#    async_dispatcher_send,
# )

_LOGGER = logging.getLogger(__name__)

# the schemas for the HA service calls
ALARM_SERVICE_EVENTLOG = vol.Schema({vol.Optional(ATTR_CODE, default=""): cv.string,})

CONF_COMMAND = "command"
ALARM_SERVICE_COMMAND = vol.Schema(
    {vol.Required(CONF_COMMAND, default="Armed"): cv.string, vol.Optional(ATTR_CODE, default=""): cv.string,}
)


class VisonicClient:

    """Set up for Visonic devices."""

    def __init__(self, hass: HomeAssistant, cf, entry: ConfigEntry):
        """Initialize the Visonic Client."""
        self.hass = hass
        self.entry = entry
        # Get the user defined config
        self.config = cf

        _LOGGER.debug("init self.config = %s  %s", PYVConst.DownloadCode, self.config)

        # Process the exclude sensor list
        self.exclude_sensor_list = self.config.get(CONF_EXCLUDE_SENSOR)
        if self.exclude_sensor_list is None or len(self.exclude_sensor_list) == 0:
            self.exclude_sensor_list = []
        if isinstance(self.exclude_sensor_list, str) and len(self.exclude_sensor_list) > 0:
            self.exclude_sensor_list = [int(e) if e.isdigit() else e for e in self.exclude_sensor_list.split(",")]

        # Process the exclude X10 list
        self.exclude_x10_list = self.config.get(CONF_EXCLUDE_X10)
        if self.exclude_x10_list is None or len(self.exclude_x10_list) == 0:
            self.exclude_x10_list = []
        if isinstance(self.exclude_x10_list, str) and len(self.exclude_x10_list) > 0:
            self.exclude_x10_list = [int(e) if e.isdigit() else e for e in self.exclude_x10_list.split(",")]

        self.visonic_event_name = "alarm_panel_state_update"
        self.panel_exception_counter = 0
        self.visonicTask = None
        self.SystemStarted = False

        # variables for creating the event log for csv and xml
        self.csvdata = None
        self.templatedata = None
        self.visprotocol = None

        # Create empty lists
        self.hass.data[DOMAIN]["binary_sensor"] = list()
        self.hass.data[DOMAIN]["switch"] = list()
        self.hass.data[DOMAIN]["alarm_control_panel"] = list()

        _LOGGER.debug("Exclude sensor list = %s     Exclude x10 list = %s", self.exclude_sensor_list, self.exclude_x10_list)

    def createWarningMessage(self, message):
        """ Create a Warning message in the log file and a notification on the HA Frontend. """
        _LOGGER.warning(message)
        self.hass.components.persistent_notification.create(message, title=NOTIFICATION_TITLE, notification_id=NOTIFICATION_ID)

    def isSirenActive(self) -> bool:
        """ Is the siren active. """
        if self.visprotocol is not None:
            return self.visprotocol.isSirenActive()
        return False

    def isPowerMaster(self) -> bool:
        """ Is it a PowerMaster panel. """
        if self.visprotocol is not None:
            return self.visprotocol.isPowerMaster()
        return False

    def getPanelStatusCode(self) -> int:
        """ Get the panel status code. """
        if self.visprotocol is not None:
            return self.visprotocol.getPanelStatusCode()
        return -1

    def getPanelMode(self) -> str:
        """ Get the panel mode. """
        if self.visprotocol is not None:
            return self.visprotocol.getPanelMode()
        return "Not Connected"

    def getPanelStatus(self) -> dict:
        """ Get the panel status. """
        if self.visprotocol is not None:
            return self.visprotocol.getPanelStatus()
        return {}

    def hasValidOverrideCode(self) -> bool:
        """ Is there a valid override code. """
        if self.visprotocol is not None:
            return self.visprotocol.hasValidOverrideCode()
        return False

    def setPyVisonic(self, pyvis):
        """ Set the pyvisonic connection. This is called from the library. """
        self.visprotocol = pyvis

    def process_command(self, command):
        """Convert object into dict to maintain backward compatibility."""
        if self.visprotocol is not None:
            _LOGGER.debug("client process_command called %s   type is %s", command, type(self.visprotocol))
            self.visprotocol.process_command(command)
        else:
            _LOGGER.warning("[VisonicClient] The pyvisonic command is None")

    def process_panel_event_log(self, event_log_entry ):
        """ Process a sequence of panel log events """
        #import custom_components.visonic.pyvisonic as visonicApi  # Connection to python Library

        reverse = self.toBool(self.config.get(CONF_LOG_REVERSE))
        total = min(event_log_entry.total, self.config.get(CONF_LOG_MAX_ENTRIES))
        current = event_log_entry.current  # only used for output and not for logic
        if reverse:
            current = total + 1 - event_log_entry.current
        # Fire event visonic_alarm_panel_event_log
        if self.toBool(self.config.get(CONF_LOG_EVENT)) and event_log_entry.current <= total:
            self.hass.bus.fire(
                "visonic_alarm_panel_event_log_entry",
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
                self.csvdata = self.csvdata + "{0}, {1}, {2}, {3}, {4}, {5}, {6}\n".format(
                    current,
                    total,
                    event_log_entry.partition,
                    event_log_entry.date,
                    event_log_entry.time,
                    event_log_entry.zone,
                    event_log_entry.event,
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
                _LOGGER.debug("Panel Event - Received last entry  reverse={0}  xmlfilenamelen={1} csvfilenamelen={2} ".format(reverse, len(self.config.get(CONF_LOG_XML_FN)), len(self.config.get(CONF_LOG_CSV_FN)) ) )
                # create a new XML file with the results
                if len(self.config.get(CONF_LOG_XML_FN)) > 0:
                    _LOGGER.debug("Panel Event - Starting xml save filename {0}".format(self.config.get(CONF_LOG_XML_FN)))
                    if reverse:
                        self.templatedata.reverse()
                    try:
                        _LOGGER.debug("Panel Event - Setting up xml file loader")
                        file_loader = FileSystemLoader(
                            [
                                "./templates",
                                self.hass.config.path() + "/templates",
                                "./xml",
                                self.hass.config.path() + "/xml",
                                "./www",
                                self.hass.config.path() + "/www",
                                ".",
                                self.hass.config.path(),
                                "./custom_components/visonic",
                                self.hass.config.path() + "/custom_components/visonic",
                            ],
                            followlinks=True,
                        )
                        env = Environment(loader=file_loader)
                        _LOGGER.debug("Panel Event - Setting up xml - getting the template")
                        template = env.get_template("visonic_template.xml")
                        output = template.render(entries=self.templatedata, total=total, available="{0}".format(event_log_entry.total),)
                        _LOGGER.debug("Panel Event - Opening xml file")
                        with open(self.config.get(CONF_LOG_XML_FN), "w") as f:
                            _LOGGER.debug("Panel Event - Writing xml file")
                            f.write(output.rstrip())
                            _LOGGER.debug("Panel Event - Closing xml file")
                            f.close()
                    except:
                        self.createWarningMessage("Panel Event Log - Failed to write XML file")
                
                _LOGGER.debug("Panel Event - CSV File Creation")
                
                if len(self.config.get(CONF_LOG_CSV_FN)) > 0:
                    try:
                        _LOGGER.debug("Panel Event - Starting csv save filename {0}".format(self.config.get(CONF_LOG_CSV_FN)))
                        if self.toBool(self.config.get(CONF_LOG_CSV_TITLE)):
                            _LOGGER.debug("Panel Event - Adding header to string")
                            self.csvdata = "current, total, partition, date, time, zone, event\n" + self.csvdata
                        _LOGGER.debug("Panel Event - Opening csv file")
                        with open(self.config.get(CONF_LOG_CSV_FN), "w") as f:
                            _LOGGER.debug("Panel Event - Writing csv file")
                            f.write(self.csvdata.rstrip())
                            _LOGGER.debug("Panel Event - Closing csv file")
                            f.close()
                    except:
                        self.createWarningMessage("Panel Event Log - Failed to write CSV file")
                
                _LOGGER.debug("Panel Event - Clear data ready for next time")
                self.csvdata = None
                if self.toBool(self.config.get(CONF_LOG_DONE)):
                    _LOGGER.debug("Panel Event - Firing Completion Event")
                    self.hass.bus.fire(
                        "visonic_alarm_panel_event_log_complete", {"total": total, "available": event_log_entry.total},
                    )
                _LOGGER.debug("Panel Event - Complete")
    

    def process_new_devices(self, visonic_devices : defaultdict):
        """ Process new devices (sensors and x10) """
        # Process new sensors
        if len(visonic_devices["sensor"]) > 0:
            changedlist = False
            for dev in visonic_devices["sensor"]:
                if dev.getDeviceID() is None:
                    _LOGGER.debug("     Sensor ID is None")
                else:
                    _LOGGER.debug("     Sensor %s", str(dev))
                    if dev.getDeviceID() not in self.exclude_sensor_list:
                        if dev not in self.hass.data[DOMAIN]["binary_sensor"]:
                            # _LOGGER.debug("     Added to dispatcher")
                            # async_dispatcher_send(self.hass, "visonic_new_binary_sensor", dev)
                            changedlist = True
                            self.hass.data[DOMAIN]["binary_sensor"].append(dev)
                        else:
                            _LOGGER.debug("      Sensor %s already in the list", dev.getDeviceID())
            _LOGGER.debug("Visonic: %s binary sensors", len(self.hass.data[DOMAIN]["binary_sensor"]))
            if changedlist:
                self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, "binary_sensor"))

        # Process new X10 switches
        if len(visonic_devices["switch"]) > 0:
            changedlist = False
            for dev in visonic_devices["switch"]:
                # _LOGGER.debug("VS: X10 Switch list %s", dev)
                if dev.enabled and dev.getDeviceID() not in self.exclude_x10_list:
                    if dev not in self.hass.data[DOMAIN]["switch"]:
                        changedlist = True
                        self.hass.data[DOMAIN]["switch"].append(dev)
                    else:
                        _LOGGER.debug("      X10 %s already in the list", dev.getDeviceID())
            _LOGGER.debug("Visonic: %s switches", len(self.hass.data[DOMAIN]["switch"]))
            if changedlist:
                self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, "switch"))


    def generate_ha_bus_event(self, event_id : int, datadictionary):
        # Trigger an update to the Alarm Panel Frontend
        if self.entry.entry_id in self.hass.data[DOMAIN][DOMAINALARM]:
            va = self.hass.data[DOMAIN][DOMAINALARM][self.entry.entry_id]
            if va is not None:
                va.schedule_update_ha_state(False)

        # Send an event on the event bus for conditions 1 to 14
        tmp = int(event_id)
        if 1 <= tmp <= 14:
            # General update trigger
            #    1 is a zone update,
            #    2 is a panel update AND the alarm is not active,
            #    3 is a panel update AND the alarm is active,
            #    4 is the panel has been reset,
            #    5 is pin rejected,
            #    6 is tamper triggered
            #    7 is download timer expired
            #    8 is watchdog timer expired, give up trying to achieve a better mode
            #    9 is watchdog timer expired, going to try again to get a better mode
            #   10 is a comms problem, we have received no data so plugin has suspended itself
            tmpdict = datadictionary.copy()

            tmpdict["condition"] = tmp
            _LOGGER.debug("Visonic update event %s %s", tmp, tmpdict)
            self.hass.bus.fire(self.visonic_event_name, tmpdict)

            if tmp == 10:
                self.createWarningMessage(
                    "Failed to connect to your Visonic Alarm. We have not received any data from the panel at all, not one single byte."
                )


    def visonic_event_callback_handler(self, visonic_devices, datadictionary):
        """ This is a callback function, called from the visonic library. """
        #  There are several reasons when this is called:
        #     When a new sensor or X10 switch is detected/created.
        #         it adds it to the list of devices and then calls discovery to fully create it in HA
        #         remember that all the sensors may not be created at the same time
        #     For log file creation and processing
        #     To create an ha event on the event bus

        import custom_components.visonic.pyvisonic as visonicApi  # Connection to python Library

        # Check to ensure variables are set correctly
        if self.hass == None:
            _LOGGER.warning("Visonic attempt to add device when hass is undefined")
            return
        if visonic_devices == None:
            _LOGGER.warning("Visonic attempt to add device when sensor is undefined")
            return

        # Is the passed in data a dictionary full of X10 switches and sensors
        if type(visonic_devices) == defaultdict:
            # a set of sensors and/or switches.
            # _LOGGER.debug("Visonic got new sensors %s", visonic_devices["sensor"] )
            self.process_new_devices(visonic_devices)

        elif type(visonic_devices) == visonicApi.SensorDevice:
            # This is an update of an existing sensor device
            _LOGGER.debug("Individual Sensor update %s not yet included", visonic_devices)

        elif type(visonic_devices) == visonicApi.X10Device:
            # This is an update of an existing x10 device
            _LOGGER.debug("Individual X10 update %s not yet included", visonic_devices)

        elif type(visonic_devices) == visonicApi.LogPanelEvent:
            # This is an event log
            _LOGGER.debug("Panel Event Log %s", visonic_devices)
            self.process_panel_event_log(visonic_devices)

        elif type(visonic_devices) == int:
            self.generate_ha_bus_event(visonic_devices, datadictionary)

        else:
            _LOGGER.warning("Visonic attempt to add device with type %s  device is %s", type(visonic_devices), visonic_devices)

    def toBool(self, val) -> bool:
        if type(val) == bool:
            return val
        elif type(val) == int:
            return val != 0
        elif type(val) == str:
            v = val.lower()
            return not (v == "no" or v == "false" or v == "0")
        _LOGGER.warning("Visonic unable to decode boolean value %s    type is %s", val, type(val))
        return False

    def getConfigData(self) -> dict:
        """ Create a dictionary full of the configuration data. """
        return {
            PYVConst.DownloadCode: self.config.get(CONF_DOWNLOAD_CODE, ""),
            PYVConst.ForceStandard: self.toBool(self.config.get(CONF_FORCE_STANDARD, False)),
            PYVConst.ForceAutoEnroll: self.toBool(self.config.get(CONF_FORCE_AUTOENROLL, True)),
            PYVConst.AutoSyncTime: self.toBool(self.config.get(CONF_AUTO_SYNC_TIME, True)),
            PYVConst.PluginLanguage: self.config.get(CONF_LANGUAGE, "EN"),
            PYVConst.EnableRemoteArm: self.toBool(self.config.get(CONF_ENABLE_REMOTE_ARM, False)),
            PYVConst.EnableRemoteDisArm: self.toBool(self.config.get(CONF_ENABLE_REMOTE_DISARM, False)),
            PYVConst.EnableSensorBypass: self.toBool(self.config.get(CONF_ENABLE_SENSOR_BYPASS, False)),
            PYVConst.MotionOffDelay: self.config.get(CONF_MOTION_OFF_DELAY, 120),
            PYVConst.OverrideCode: self.config.get(CONF_OVERRIDE_CODE, ""),
            PYVConst.ForceKeypad: self.toBool(self.config.get(CONF_FORCE_KEYPAD, False)),
            PYVConst.ArmWithoutCode: self.toBool(self.config.get(CONF_ARM_CODE_AUTO, False)),
            PYVConst.SirenTriggerList: self.config.get(CONF_SIREN_SOUNDING, ["Intruder"]),
            PYVConst.B0_Enable: self.toBool(self.config.get(CONF_B0_ENABLE_MOTION_PROCESSING, False)),
            PYVConst.B0_Min_Interval_Time: self.config.get(CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, 5),
            PYVConst.B0_Max_Wait_Time: self.config.get(CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, 30),
        }

    def updateConfig(self, conf=None):
        """ Update the dictionary full of configuration data. """
        if conf is not None:
            self.config.update(conf)
        if self.visprotocol is not None:
            self.visprotocol.updateSettings(self.getConfigData())
        # else:
        #    _LOGGER.warning("Visonic link is not set")
        # make the changes to the platform parameters (used in alarm_control_panel)
        #    the original idea was to keep these separate for multiple partitions but now i'm not so sure its necessary

        self.hass.data[DOMAIN]["arm_without_code"] = self.toBool(self.config.get(CONF_ARM_CODE_AUTO, False))
        self.hass.data[DOMAIN]["force_keypad"] = self.toBool(self.config.get(CONF_FORCE_KEYPAD, False))
        self.hass.data[DOMAIN]["arm_away_instant"] = self.toBool(self.config.get(CONF_INSTANT_ARM_AWAY, False))
        self.hass.data[DOMAIN]["arm_home_instant"] = self.toBool(self.config.get(CONF_INSTANT_ARM_HOME, False))

        _LOGGER.debug("[Settings] Log Max Entries   %s", self.config.get(CONF_LOG_MAX_ENTRIES))
        _LOGGER.debug("[Settings] Log Reverse       %s", self.config.get(CONF_LOG_REVERSE))
        _LOGGER.debug("[Settings] Log Create Event  %s", self.config.get(CONF_LOG_EVENT))
        _LOGGER.debug("[Settings] Log Final Event   %s", self.config.get(CONF_LOG_DONE))
        _LOGGER.debug("[Settings] Log XML Filename  %s", self.config.get(CONF_LOG_XML_FN))
        _LOGGER.debug("[Settings] Log CSV Filename  %s", self.config.get(CONF_LOG_CSV_FN))
        _LOGGER.debug("[Settings] Log CSV title Row %s", self.config.get(CONF_LOG_CSV_TITLE))

    def connect_to_alarm(self) -> bool:
        """ Create the connection to the alarm panel """
        import custom_components.visonic.pyvisonic as visonicApi  # Connection to python Library

        # Is the system already running and connected
        if self.SystemStarted:
            return False

        # remove any existing visonic related sensors (so we don't get entity id already exists exceptions on a restart)
        sensor_list = self.hass.states.async_entity_ids("binary_sensor")
        if sensor_list is not None:
            for x in sensor_list:
                _LOGGER.debug("Checking HA Entity ID: %s", x)
                if x.lower().startswith("binary_sensor.visonic_z"):
                    # device, entity = self.split_entity(x)
                    # self.entities[device][entity]
                    _LOGGER.debug("   Removed existing HA Entity ID: %s", x)
                    self.hass.add_job(self.hass.states.async_remove(x))

        # set up config parameters in the visonic library
        self.hass.data[DOMAIN][DOMAINDATA]["Exception Count"] = self.panel_exception_counter

        _LOGGER.debug("connect_to_alarm self.config = %s", self.config)

        # Get Visonic specific configuration.
        device_type = self.config.get(CONF_DEVICE_TYPE)

        _LOGGER.debug("Visonic Connection Device Type is %s %s", device_type, self.getConfigData())

        # update config parameters (local in hass[DOMAIN] mainly)
        self.updateConfig()

        self.comm = None

        # Connect in the way defined by the user in the config file, ethernet or usb
        if device_type == "ethernet":
            host = self.config.get(CONF_HOST)
            port = self.config.get(CONF_PORT)

            self.comm = visonicApi.create_tcp_visonic_connection(
                address=host,
                port=port,
                client=self,
                panelConfig=self.getConfigData(),
                event_callback=self.visonic_event_callback_handler,
                disconnect_callback=self.disconnect_callback,
                loop=self.hass.loop,
            )

        elif device_type == "usb":
            path = self.config.get(CONF_PATH)
            baud = self.config.get(CONF_DEVICE_BAUD)

            self.comm = visonicApi.create_usb_visonic_connection(
                path=path,
                baud=baud,
                client=self,
                panelConfig=self.getConfigData(),
                event_callback=self.visonic_event_callback_handler,
                disconnect_callback=self.disconnect_callback,
                loop=self.hass.loop,
            )

        if self.comm is not None:
            # Connection to the panel has beeninitially successful, create the task to progress the connection
            self.visonicTask = self.hass.loop.create_task(self.comm)
            # Record that we have started the system
            self.SystemStarted = True
            return True

        self.visonicTask = None
        self.createWarningMessage("Failed to connect into Visonic Alarm. Check Settings.")
        return False

    async def service_comms_stop(self, call):
        """ Service call to close down the current serial connection, we need to reset the whole connection!!!! """
        if not self.SystemStarted:
            _LOGGER.debug("Request to Stop the Comms and it is already stopped")
            return

        # Try to get the asyncio Coroutine within the Task to shutdown the serial link connection properly
        if self.visprotocol is not None:
            self.visprotocol.ShutdownOperation()
        await asyncio.sleep(0.5)
        # not a mistake, wait a bit longer to make sure it's closed as we get no feedback (we only get the fact that the queue is empty)

    async def service_panel_stop(self, call):
        """ Service call to stop the connection """
        if not self.SystemStarted:
            _LOGGER.debug("Request to Stop the HA alarm_control_panel and it is already stopped")
            return
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
        self.SystemStarted = False

    async def service_panel_start(self, call):
        """ Service call to start the connection """
        if self.SystemStarted:
            _LOGGER.warning("Request to Start the HA alarm_control_panel and it is already running")
            return

        # re-initialise global variables, do not re-create the queue as we can't pass it to the alarm control panel. There's no need to create it again anyway
        self.visonicTask = None

        _LOGGER.debug("........... attempting connection")

        alarm_entity_exists = False
        alarm_list = self.hass.states.async_entity_ids("alarm_control_panel")
        if alarm_list is not None:
            _LOGGER.debug("Found existing HA alarm_control_panel %s", alarm_list)
            for x in alarm_list:
                _LOGGER.debug("    Checking HA Alarm ID: %s", x)
                if x.lower().startswith("alarm_control_panel.visonic_alarm"):
                    _LOGGER.debug("       ***** Matched - Alarm Control Panel already exists so keep it ***** : %s", x)
                    alarm_entity_exists = True

        if self.connect_to_alarm():
            if not alarm_entity_exists:
                self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, "alarm_control_panel"))

    async def service_panel_reconnect(self, call):
        """ Service call to re-connect the connection """
        _LOGGER.debug("User has requested visonic panel reconnection")
        await self.service_comms_stop(call)
        await self.service_panel_stop(call)
        await self.service_panel_start(call)

    async def disconnect_callback_async(self, excep):
        """ Service call to disconnect """
        _LOGGER.debug(" ........... attempting reconnection")
        await self.service_panel_stop(excep)
        await self.service_panel_start(excep)

    def stop_subscription(self, event):
        """ Shutdown Visonic subscriptions and subscription thread on exit."""
        _LOGGER.debug("Shutting down subscriptions")
        asyncio.ensure_future(self.service_panel_stop(event), loop=self.hass.loop)

    def disconnect_callback(self, excep):
        """ Callback when the connection to the panel is disrupted """
        if excep is None:
            _LOGGER.debug("PyVisonic has caused an exception, no exception information is available")
        else:
            _LOGGER.debug("PyVisonic has caused an exception %s", str(excep))
        # General update trigger
        #    0 is a disconnect and (hopefully) reconnect from an exception (probably comms related)
        self.hass.bus.fire(self.visonic_event_name, {"condition": 0})
        sleep(5.0)
        _LOGGER.debug(" ........... setting up reconnection")
        self.panel_exception_counter = self.panel_exception_counter + 1
        asyncio.ensure_future(self.disconnect_callback_async(excep), loop=self.hass.loop)

    def decode_code(self, data) -> str:
        """ Decode the code """
        if data is not None:
            if type(data) == str:
                if len(data) == 4:
                    return data
            elif type(data) is dict:
                if "code" in data:
                    if len(data["code"]) == 4:
                        return data["code"]
        return ""

    def service_panel_eventlog(self, call):
        """ Service call to retrieve the event log from the panel. This currently just gets dumped in the HA log file """
        _LOGGER.debug("alarm control panel received event log request")
        if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>":
            code = ""
            if ATTR_CODE in call.data:
                code = call.data[ATTR_CODE]
            _LOGGER.debug("alarm control panel making event log request")
            ##self.hass.data[DOMAIN]["command_queue"].put_nowait(["eventlog", self.decode_code(code)])
            if self.visprotocol is not None:
                self.visprotocol.GetEventLog(self.decode_code(code))
            # self.process_command(["eventlog", self.decode_code(code)])
        else:
            _LOGGER.debug("alarm control panel not making event log request %s %s", type(call.data), call.data)

    def service_panel_command(self, call):
        """ Service call to send a commandto the panel """
        _LOGGER.debug("alarm control panel received command request")
        if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>":
            code = ""
            if ATTR_CODE in call.data:
                code = call.data[ATTR_CODE]
            command = call.data[CONF_COMMAND]
            # _LOGGER.debug('alarm control panel got command %s', command)
            self.sendCommand(command, self.decode_code(code))
        else:
            _LOGGER.debug("alarm control panel not making command request %s %s", type(call.data), call.data)

    async def service_panel_download(self, call):
        """ Service call to download the panels EPROM """
        if self.visprotocol is not None:
            await self.visprotocol.startDownloadAgain()

    def sendCommand(self, command, code):
        """ Send a command to the panel """
        if self.visprotocol is not None:
            self.visprotocol.RequestArm(command.lower(), code)

    def sendBypass(self, devid, bypass, code):
        """ Send the bypass command to the panel """
        if self.visprotocol is not None:
            self.visprotocol.SetSensorArmedState(devid, bypass, code)

    def setX10(self, ident, state):
        """ Send an X10 command to the panel """
        if self.visprotocol is not None:
            self.visprotocol.setX10(ident, state)

    def connect(self):
        """ Main function to connect to the panel """
        try:
            # Establish a callback to stop the component when the stop event occurs
            self.hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, self.stop_subscription)

            self.hass.services.async_register(DOMAIN, "alarm_panel_reconnect", self.service_panel_reconnect)
            self.hass.services.async_register(
                DOMAIN, "alarm_panel_eventlog", self.service_panel_eventlog, schema=ALARM_SERVICE_EVENTLOG,
            )
            self.hass.services.async_register(
                DOMAIN, "alarm_panel_command", self.service_panel_command, schema=ALARM_SERVICE_COMMAND,
            )
            self.hass.services.async_register(DOMAIN, "alarm_panel_download", self.service_panel_download)

            success = self.connect_to_alarm()

            if success:
                # Create "alarm control panel"
                #   eventually there will be an "alarm control panel" for each partition but we only support 1 partition at the moment
                self.hass.async_create_task(self.hass.config_entries.async_forward_entry_setup(self.entry, "alarm_control_panel"))
                return True

        except (ConnectTimeout, HTTPError) as ex:
            _LOGGER.debug("Unable to connect to Visonic Alarm Panel: %s", str(ex))
            self.hass.components.persistent_notification.create(
                "Error: {}<br />" "You will need to restart hass after fixing." "".format(ex),
                title=NOTIFICATION_TITLE,
                notification_id=NOTIFICATION_ID,
            )

        return False
