"""
This component is used to create a connection to a Visonic Power Max or PowerMaster Alarm System
Currently, there is only support for a single partition

The Connection can be made using Ethernet TCP, USB (connection to RS232) or directly by RS232

  Initial setup by David Field

"""
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
from homeassistant.const import (ATTR_CODE, ATTR_ARMED, EVENT_HOMEASSISTANT_STOP, CONF_HOST, CONF_PORT, CONF_PATH, CONF_DEVICE)
from homeassistant.helpers.entity import Entity
from homeassistant.core import HomeAssistant

from requests import ConnectTimeout, HTTPError
from time import sleep
from .const import *
#from homeassistant.helpers.dispatcher import (
#    async_dispatcher_connect,
#    async_dispatcher_send,
#)

from .pyvisonic import EventHandling, PYVConst

_LOGGER = logging.getLogger(__name__)

# the schemas for the HA service calls
ALARM_SERVICE_EVENTLOG = vol.Schema({
    vol.Optional(ATTR_CODE, default=""): cv.string,
})

CONF_COMMAND = "command"
ALARM_SERVICE_COMMAND = vol.Schema({
    vol.Required(CONF_COMMAND, default="Armed" ) : cv.string,
    vol.Optional(ATTR_CODE, default=""): cv.string,
})

class VisonicClient:

    """Set up for Visonic devices."""
    def __init__(self, hass: HomeAssistant, cf, entry: ConfigEntry):
        """Initialize the config flow."""
        self.hass = hass
        self.entry = entry
        # Get the user defined config
        self.config = cf

        #_LOGGER.info("init self.config = {0} {1}".format( PYVConst.DownloadCode, self.config))

        self.exclude_sensor_list = self.config.get(CONF_EXCLUDE_SENSOR)
        if self.exclude_sensor_list is None:
            self.exclude_sensor_list = {}
            
        self.exclude_x10_list = self.config.get(CONF_EXCLUDE_X10)
        if self.exclude_x10_list is None:
            self.exclude_x10_list = {}

        self.visonic_event_name = 'alarm_panel_state_update'
        self.panel_exception_counter = 0
        self.myTask = None
        self.SystemStarted = False

        # variables for creating the event log for csv and xml
        self.csvdata = None
        self.templatedata = None
        self.visprotocol = None

        #self.hass.data[DOMAIN]["command_queue"] = asyncio.Queue()
        self.hass.data[DOMAIN]["binary_sensor"] = list()
        self.hass.data[DOMAIN]["switch"] = list()
        self.hass.data[DOMAIN]["alarm_control_panel"] = list()

        _LOGGER.info("Exclude sensor list = {0}     Exclude x10 list = {1}".format(self.exclude_sensor_list, self.exclude_x10_list))
        
    def isSirenActive(self) -> bool:
        return self.visprotocol.isSirenActive()

    def isPowerMaster(self) -> bool:
        return self.visprotocol.isPowerMaster()

    def getPanelStatusCode(self) -> int:
        return self.visprotocol.getPanelStatusCode()

    def getPanelMode(self) -> str:
        return self.visprotocol.getPanelMode()

    def getPanelStatus(self) -> dict:
        return self.visprotocol.getPanelStatus()

    def hasValidOverrideCode(self) -> bool:
        return self.visprotocol.hasValidOverrideCode()

    def setPyVisonic(self, pyvis):
        self.visprotocol = pyvis

    def process_command(self, command):
        """Convert object into dict to maintain backward compatibility."""
        if self.visprotocol is not None:
            _LOGGER.info("client process_command called {0}   type is {1}".format(command, type(self.visprotocol))) 
            self.visprotocol.process_command(command)
        else:
            _LOGGER.error("[VisonicClient] The pyvisonic command is None") 
   
    # This is a callback function, called from the visonic library when a new sensor is detected/created
    #  it adds it to the list of devices and then calls discovery to fully create it in HA
    #  remember that all the sensors may not be created at the same time
    def visonic_event_callback_handler(self, visonic_devices, datadictionary):
        
        import custom_components.visonic.pyvisonic as visonicApi   # Connection to python Library
    
        # Check to ensure variables are set correctly
        if self.hass == None:
            _LOGGER.warning("Visonic attempt to add device when hass is undefined")
            return
        if visonic_devices == None:
            _LOGGER.warning("Visonic attempt to add device when sensor is undefined")
            return
        if type(visonic_devices) == defaultdict:  
            # a set of sensors and/or switches. 
            #_LOGGER.info("Visonic got new sensors {0}".format( visonic_devices["sensor"] ))
            
            for dev in visonic_devices["sensor"]:
                if dev.getDeviceID() is None:
                    _LOGGER.info("     Sensor ID is None")
                else:
                    _LOGGER.info("     Sensor {0}".format( str(dev) ))
                    if dev.getDeviceID() not in self.exclude_sensor_list:
                        if dev not in self.hass.data[DOMAIN]["binary_sensor"]:
                            #_LOGGER.info("     Added to dispatcher")
                            #async_dispatcher_send(self.hass, "visonic_new_binary_sensor", dev)
                            self.hass.data[DOMAIN]["binary_sensor"].append(dev)   
                        else:
                            _LOGGER.debug("      Sensor Already in the list")

            #_LOGGER.info("Visonic got new switches {0}".format( visonic_devices["switch"] ))
            for dev in visonic_devices["switch"]:
                #_LOGGER.info("VS: X10 Switch list {0}".format(dev))
                if dev.enabled and dev.getDeviceID() not in self.exclude_x10_list:
                    if dev not in self.hass.data[DOMAIN]["switch"]:
                        self.hass.data[DOMAIN]["switch"].append(dev)
                    else:
                        _LOGGER.debug("      X10 Already in the list")
            
            self.hass.async_create_task( self.hass.config_entries.async_forward_entry_setup(self.entry, "binary_sensor") )
            self.hass.async_create_task( self.hass.config_entries.async_forward_entry_setup(self.entry, "switch") )
            
        elif type(visonic_devices) == visonicApi.SensorDevice:
            # This is an update of an existing sensor device
            _LOGGER.info("Individual Sensor update {0} not yet included".format( visonic_devices ))
            
        elif type(visonic_devices) == visonicApi.X10Device:
            # This is an update of an existing x10 device
            _LOGGER.info("Individual X10 update {0} not yet included".format( visonic_devices ))
            
        elif type(visonic_devices) == visonicApi.LogPanelEvent:
            # This is an event log
            _LOGGER.debug("Panel Event Log {0}".format( visonic_devices ))
            reverse = self.config.get(CONF_LOG_REVERSE)
            total = min(visonic_devices.total, self.config.get(CONF_LOG_MAX_ENTRIES))
            current = visonic_devices.current   # only used for output and not for logic
            if reverse:
                current = total + 1 - visonic_devices.current
            # Fire event visonic_alarm_panel_event_log
            if self.config.get(CONF_LOG_EVENT) and visonic_devices.current <= total:
                hass.bus.fire('visonic_alarm_panel_event_log_entry', {
                    'current': current,
                    'total': total,
                    'date': visonic_devices.date,
                    'time': visonic_devices.time,
                    'partition': visonic_devices.partition,
                    'zone': visonic_devices.zone,
                    'event': visonic_devices.event
                })            

            # Write out to an xml file
            if visonic_devices.current==1:
                self.templatedata = []
                self.csvdata = ""

            if self.csvdata is not None:
                if reverse:
                    self.csvdata = "{0}, {1}, {2}, {3}, {4}, {5}, {6}\n".format(current, total, visonic_devices.partition, visonic_devices.date, visonic_devices.time, visonic_devices.zone, visonic_devices.event ) + self.csvdata
                else:
                    self.csvdata = self.csvdata + "{0}, {1}, {2}, {3}, {4}, {5}, {6}\n".format(current, total, visonic_devices.partition, visonic_devices.date, visonic_devices.time, visonic_devices.zone, visonic_devices.event )
                
                datadict = {	
                  "partition" : "{0}".format(visonic_devices.partition),
                  "current"   : "{0}".format(current),
                  "date"      : "{0}".format(visonic_devices.date),
                  "time"      : "{0}".format(visonic_devices.time),
                  "zone"      : "{0}".format(visonic_devices.zone),
                  "event"     : "{0}".format(visonic_devices.event)
                }
                
                self.templatedata.append(datadict)
                
                if visonic_devices.current == total:
                    # create a new XML file with the results
                    if len(self.config.get(CONF_LOG_XML_FN)) > 0:
                        if reverse:
                            self.templatedata.reverse()
                        try:
                            file_loader = FileSystemLoader(['./templates', self.hass.config.path()+'/templates', './xml', self.hass.config.path()+'/xml', './www', self.hass.config.path()+'/www', '.', self.hass.config.path(), './custom_components/visonic', self.hass.config.path()+'/custom_components/visonic'], followlinks=True)
                            env = Environment(loader=file_loader)
                            template = env.get_template('visonic_template.xml')
                            output = template.render(entries=self.templatedata, total=total, available="{0}".format(visonic_devices.total))
                            with open(self.config.get(CONF_LOG_XML_FN), "w") as f:
                                f.write(output.rstrip())
                                f.close()
                        except:
                            _LOGGER.debug("Panel Event Log - Failed to write XML file")
                    if len(self.config.get(CONF_LOG_CSV_FN)) > 0:
                        try:
                            if self.config.get(CONF_LOG_CSV_TITLE):
                                self.csvdata = "current, total, partition, date, time, zone, event\n" + self.csvdata
                            with open(self.config.get(CONF_LOG_CSV_FN), "w") as f:
                                f.write(self.csvdata.rstrip())
                                f.close()
                        except:
                            _LOGGER.debug("Panel Event Log - Failed to write CSV file")
                    self.csvdata = None
                    if self.config.get(CONF_LOG_DONE):
                        self.hass.bus.fire('visonic_alarm_panel_event_log_complete', {
                            'total': total,
                            'available': visonic_devices.total,
                        })            
            
        elif type(visonic_devices) == int:
            tmp = int(visonic_devices)
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
                
                tmpdict['condition'] = tmp
                _LOGGER.info("Visonic update event {0} {1}".format(tmp, tmpdict))
                self.hass.bus.fire(self.visonic_event_name, tmpdict)
                
                if tmp == 10:
                    message = 'Failed to connect to your Visonic Alarm. We have not received any data from the panel at all, not one single byte.'
                    _LOGGER.error(message)
                    self.hass.components.persistent_notification.create(
                        message,
                        title=NOTIFICATION_TITLE,
                        notification_id=NOTIFICATION_ID)
        else:
            _LOGGER.warning("Visonic attempt to add device with type {0}  device is {1}".format(type(visonic_devices), visonic_devices ))

    def getConfigData(self) -> dict:
        return {
            PYVConst.DownloadCode         : self.config.get(CONF_DOWNLOAD_CODE, ""),
            PYVConst.ForceStandard        : self.config.get(CONF_FORCE_STANDARD, False),
            PYVConst.ForceAutoEnroll      : self.config.get(CONF_FORCE_AUTOENROLL, True),
            PYVConst.AutoSyncTime         : self.config.get(CONF_AUTO_SYNC_TIME, True),
            PYVConst.PluginLanguage       : self.config.get(CONF_LANGUAGE, "EN"),
            PYVConst.EnableRemoteArm      : self.config.get(CONF_ENABLE_REMOTE_ARM, False),
            PYVConst.EnableRemoteDisArm   : self.config.get(CONF_ENABLE_REMOTE_DISARM, False),
            PYVConst.EnableSensorBypass   : self.config.get(CONF_ENABLE_SENSOR_BYPASS, False),
            PYVConst.MotionOffDelay       : self.config.get(CONF_MOTION_OFF_DELAY, 120),
            PYVConst.OverrideCode         : self.config.get(CONF_OVERRIDE_CODE, ""),
            PYVConst.ForceKeypad          : self.config.get(CONF_FORCE_KEYPAD, False),
            PYVConst.ArmWithoutCode       : self.config.get(CONF_ARM_CODE_AUTO, False),
            PYVConst.SirenTriggerList     : self.config.get(CONF_SIREN_SOUNDING, ["Intruder"]),
            PYVConst.B0_Enable            : self.config.get(CONF_B0_ENABLE_MOTION_PROCESSING, False),
            PYVConst.B0_Min_Interval_Time : self.config.get(CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, 5),
            PYVConst.B0_Max_Wait_Time     : self.config.get(CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, 30),
        }

    def updateConfig(self, conf = None):
        if conf is not None:
            self.config.update(conf)
        if self.visprotocol is not None:
            self.visprotocol.updateSettings(self.getConfigData())
        #else:
        #    _LOGGER.warning("Visonic link is not set")
        # make the changes to the platform parameters (used in alarm_control_panel)
        #    the original idea was to keep these separate for multiple partitions but now i'm not so sure its necessary
        self.hass.data[DOMAIN]["arm_without_code"] = self.config.get(CONF_ARM_CODE_AUTO, False)
        self.hass.data[DOMAIN]["force_keypad"]     = self.config.get(CONF_FORCE_KEYPAD, False)
        self.hass.data[DOMAIN]["arm_away_instant"] = self.config.get(CONF_INSTANT_ARM_AWAY, False)
        self.hass.data[DOMAIN]["arm_home_instant"] = self.config.get(CONF_INSTANT_ARM_HOME, False)


    def connect_to_alarm(self) -> bool:
        import custom_components.visonic.pyvisonic as visonicApi   # Connection to python Library
    
        if self.SystemStarted:
            return

        # remove any existing visonic related sensors (so we don't get entity id already exists exceptions on a restart)
        sensor_list = self.hass.states.async_entity_ids("binary_sensor")
        if sensor_list is not None:
            for x in sensor_list:
                _LOGGER.info("Checking HA Entity ID: {0}".format(x))
                if x.lower().startswith( 'binary_sensor.visonic_z' ):
                    #device, entity = self.split_entity(x)
                    #self.entities[device][entity]
                    _LOGGER.info("   Removed existing HA Entity ID: {0}".format(x))
#                    entity_object = get_entity(x)
                    self.hass.add_job(self.hass.states.async_remove(x))

        # set up config parameters in the visonic library
        self.hass.data[DOMAIN][DOMAINDATA]["Exception Count"] = self.panel_exception_counter
        
        #_LOGGER.info("connect_to_alarm self.config = {1} {2} {0}".format(self.config, CONF_DOWNLOAD_CODE, self.config.get(CONF_DOWNLOAD_CODE)))

        # Get Visonic specific configuration.
        device_type = self.config.get(CONF_DEVICE_TYPE)
        
        #_LOGGER.info("Visonic Connection Device Type is {0}  {1}".format(device_type, self.getConfigData()))

        # update config parameters (local in hass[DOMAIN] mainly)
        self.updateConfig()
        
        self.comm = None
        
        # Connect in the way defined by the user in the config file, ethernet or usb
        if device_type == "ethernet":
            host = self.config.get(CONF_HOST)
            port = self.config.get(CONF_PORT)

            self.comm = visonicApi.create_tcp_visonic_connection(address = host, port = port, client = self, panelConfig = self.getConfigData(), 
                       event_callback = self.visonic_event_callback_handler, disconnect_callback = self.disconnect_callback, loop = self.hass.loop)
            
        elif device_type == "usb":
            path = self.config.get(CONF_PATH)
            baud = self.config.get(CONF_DEVICE_BAUD)
           
            self.comm = visonicApi.create_usb_visonic_connection(port = path, baud = baud, client = self, panelConfig = self.getConfigData(),
                       event_callback = self.visonic_event_callback_handler, disconnect_callback = self.disconnect_callback, loop = self.hass.loop)

        if self.comm is not None:
            self.myTask = self.hass.loop.create_task(self.comm)
            self.SystemStarted = True
            return True

        self.myTask = None
        message = 'Failed to connect into Visonic Alarm. Check Settings.'
        _LOGGER.error(message)
        self.hass.components.persistent_notification.create(
            message,
            title=NOTIFICATION_TITLE,
            notification_id=NOTIFICATION_ID)
        return False

    # Service call to close down the current serial connection, we need to reset the whole connection!!!!
    async def service_comms_stop(self, call):
        if not self.SystemStarted:
            _LOGGER.warning("Request to Stop the Comms and it is already stopped")
            return

        #_LOGGER.info("........... Current Task - Closing Down Current Serial Connection, Queue size is {0}".format(self.hass.data[DOMAIN]["command_queue"].qsize()))
        
        # Try to get the asyncio Coroutine within the Task to shutdown the serial link connection properly
        if self.visprotocol is not None:
            self.visprotocol.ShutdownOperation()
        await asyncio.sleep(0.5)   # not a mistake, wait a bit longer to make sure it's closed as we get no feedback (we only get the fact that the queue is empty)
        
    # Service call to close down the current serial connection, we need to reset the whole connection!!!!
    async def service_panel_stop(self, call):
        if not self.SystemStarted:
            _LOGGER.warning("Request to Stop the HA alarm_control_panel and it is already stopped")
            return
        # cancel the task from within HA
        if self.myTask is not None:
            _LOGGER.info("          ........... Closing down Current Task")
            self.myTask.cancel()
            await asyncio.sleep(2.0)
            if self.myTask.done():
                _LOGGER.info("          ........... Current Task Done")
            else:
                _LOGGER.info("          ........... Current Task Not Done")
        else:
            _LOGGER.info("          ........... Current Task not set")
        self.SystemStarted = False

    # Service call to close down the current serial connection and re-establish it, we need to reset the whole connection!!!!
    async def service_panel_start(self, call):
        if self.SystemStarted:
            _LOGGER.warning("Request to Start the HA alarm_control_panel and it is already running")
            return

        # re-initialise global variables, do not re-create the queue as we can't pass it to the alarm control panel. There's no need to create it again anyway
        self.myTask = None

        _LOGGER.info("........... attempting connection")

        alarm_entity_exists = False
        alarm_list = self.hass.states.async_entity_ids("alarm_control_panel")
        if alarm_list is not None:
            _LOGGER.info("Found existing HA alarm_control_panel {0}".format(alarm_list))
            for x in alarm_list:
                _LOGGER.info("    Checking HA Alarm ID: {0}".format(x))
                if x.lower().startswith( 'alarm_control_panel.visonic_alarm' ):
                    _LOGGER.info("       ***** Matched - Alarm Control Panel already exists so keep it ***** : {0}".format(x))
                    alarm_entity_exists = True

        if self.connect_to_alarm():
            if not alarm_entity_exists:
                self.hass.async_create_task( self.hass.config_entries.async_forward_entry_setup(self.entry, "alarm_control_panel") )            

    # Service call to close down the current serial connection and re-establish it, we need to reset the whole connection!!!!
    async def service_panel_reconnect(self, call):
        _LOGGER.warning("User has requested visonic panel reconnection")
        await self.service_comms_stop(call)
        await self.service_panel_stop(call)
        await self.service_panel_start(call)

    async def disconnect_callback_async(self, excep):
        _LOGGER.error(" ........... attempting reconnection")
        await self.service_panel_stop(excep)
        await self.service_panel_start(excep)

    def stop_subscription(self, event):
        """Shutdown Visonic subscriptions and subscription thread on exit."""
        _LOGGER.info("Shutting down subscriptions")
        asyncio.ensure_future(self.service_panel_stop(event), loop=self.hass.loop)        

    def disconnect_callback(self, excep):
        if excep is None:
            _LOGGER.error("PyVisonic has caused an exception, no exception information is available")
        else:
            _LOGGER.error("PyVisonic has caused an exception {0}".format(excep))
        # General update trigger
        #    0 is a disconnect and (hopefully) reconnect from an exception (probably comms related)
        self.hass.bus.fire(self.visonic_event_name, { 'condition': 0})
        sleep(5.0)
        _LOGGER.error(" ........... setting up reconnection")
        self.panel_exception_counter = self.panel_exception_counter + 1
        asyncio.ensure_future(self.disconnect_callback_async(excep), loop=self.hass.loop)        
        
    def decode_code(self, data) -> str:
        if data is not None:
            if type(data) == str:
                if len(data) == 4:                
                    return data
            elif type(data) is dict:
                if 'code' in data:
                    if len(data['code']) == 4:                
                        return data['code']
        return ""

    # Service call to retrieve the event log from the panel. This currently just gets dumped in the HA log file
    def service_panel_eventlog(self, call):
        _LOGGER.info('alarm control panel received event log request')
        if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>":
            code = ''
            if ATTR_CODE in call.data:
                code = call.data[ATTR_CODE]
            _LOGGER.info('alarm control panel making event log request')
            ##self.hass.data[DOMAIN]["command_queue"].put_nowait(["eventlog", self.decode_code(code)])
            if self.visprotocol is not None:
                self.visprotocol.GetEventLog(self.decode_code(code))
            #self.process_command(["eventlog", self.decode_code(code)])
        else:
            _LOGGER.info('alarm control panel not making event log request {0} {1}'.format(type(call.data), call.data))

    # Service call to retrieve the event log from the panel. This currently just gets dumped in the HA log file
    def service_panel_command(self, call):
        _LOGGER.info('alarm control panel received command request')
        if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>":
            code = ''
            if ATTR_CODE in call.data:
                code = call.data[ATTR_CODE]
            command = call.data[CONF_COMMAND]
            #_LOGGER.info('alarm control panel got command ' + command)
            self.sendCommand(command, self.decode_code(code))
        else:
            _LOGGER.info('alarm control panel not making command request {0} {1}'.format(type(call.data), call.data))

    # Service call to retrieve the event log from the panel. This currently just gets dumped in the HA log file
    async def service_panel_download(self, call):
        if self.visprotocol is not None:
            await self.visprotocol.startDownloadAgain()

    def sendCommand(self, command, code):
        if self.visprotocol is not None:
            self.visprotocol.RequestArm(command.lower(), code)
    
    def sendBypass(self, devid, bypass, code):
        if self.visprotocol is not None:
            self.visprotocol.SetSensorArmedState(devid, bypass, code)

    def setX10(self, ident, state):    
        if self.visprotocol is not None:
            self.visprotocol.setX10(ident, state)
    
    def connect(self):
        # start of main function
        try:
            # Establish a callback to stop the component when the stop event occurs
            self.hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, self.stop_subscription)

            self.hass.services.async_register(DOMAIN, 'alarm_panel_reconnect', self.service_panel_reconnect)
            self.hass.services.async_register(DOMAIN, 'alarm_panel_eventlog', self.service_panel_eventlog, schema=ALARM_SERVICE_EVENTLOG)
            self.hass.services.async_register(DOMAIN, 'alarm_panel_command', self.service_panel_command, schema=ALARM_SERVICE_COMMAND)
            self.hass.services.async_register(DOMAIN, 'alarm_panel_download', self.service_panel_download)
                    
            success = self.connect_to_alarm()
            
            if success:
                # Create "alarm control panel"
                #   eventually there will be an "alarm control panel" for each partition but we only support 1 partition at the moment
                self.hass.async_create_task( self.hass.config_entries.async_forward_entry_setup(self.entry, "alarm_control_panel") )
                return True
            
        except (ConnectTimeout, HTTPError) as ex:
            _LOGGER.error("Unable to connect to Visonic Alarm Panel: %s", str(ex))
            hass.components.persistent_notification.create(
                'Error: {}<br />'
                'You will need to restart hass after fixing.'
                ''.format(ex),
                title=NOTIFICATION_TITLE,
                notification_id=NOTIFICATION_ID)
        
        return False
