"""
This component is used to create a connection to a Visonic Power Max or PowerMaster Alarm SystemError
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
from homeassistant.util.dt import utc_from_timestamp
from homeassistant.util import convert, slugify
from homeassistant.helpers import discovery
from homeassistant.helpers import config_validation as cv
from homeassistant.const import (ATTR_CODE, ATTR_ARMED, EVENT_HOMEASSISTANT_STOP, CONF_HOST, CONF_PORT, CONF_PATH, CONF_DEVICE)
from homeassistant.helpers.entity import Entity
from requests import ConnectTimeout, HTTPError
from time import sleep

# Visonic has Motion Sensors (PIR and Magnetic contact mainly) and X10 devices
VISONIC_PLATFORM = 'visonic_platform'

from custom_components.visonic.switch import VISONIC_X10
from custom_components.visonic.binary_sensor import VISONIC_SENSORS

REQUIREMENTS = ['pyserial', 'pyserial_asyncio', 'datetime', 'jinja2']

DOMAIN = 'visonic'

NOTIFICATION_ID = 'visonic_notification'
NOTIFICATION_TITLE = 'Visonic Panel Setup'

# Config file variables
VISONIC_ID_LIST_SCHEMA = vol.Schema([int])
CONF_DEVICE_TYPE = 'type'
CONF_DEVICE_BAUD = 'baud'
DEFAULT_DEVICE_HOST = '127.0.0.1'
DEFAULT_DEVICE_PORT = 30000
DEFAULT_DEVICE_USB = '/dev/ttyUSB1'
DEFAULT_DEVICE_BAUD = 9600

CONF_MOTION_OFF_DELAY = "motion_off"
CONF_LANGUAGE = "language"
CONF_FORCE_STANDARD = "force_standard"
CONF_FORCE_AUTOENROLL = "force_autoenroll"
CONF_AUTO_SYNC_TIME = "sync_time"
CONF_ENABLE_REMOTE_ARM = "allow_remote_arm"
CONF_ENABLE_REMOTE_DISARM = "allow_remote_disarm"
CONF_ENABLE_SENSOR_BYPASS = "allow_sensor_bypass"
CONF_OVERRIDE_CODE = "override_code"
CONF_DOWNLOAD_CODE = "download_code"
CONF_ARM_CODE_AUTO = "arm_without_usercode"
CONF_FORCE_KEYPAD = "force_numeric_keypad"

CONF_EXCLUDE_SENSOR = "exclude_sensor"
CONF_EXCLUDE_X10 = "exclude_x10"

# Event processing for the log files from the panel
CONF_LOG_EVENT        = "panellog_logentry_event"
CONF_LOG_CSV_TITLE    = "panellog_csv_add_title_row"
CONF_LOG_XML_FN       = "panellog_xml_filename"
CONF_LOG_CSV_FN       = "panellog_csv_filename"
CONF_LOG_DONE         = "panellog_complete_event"
CONF_LOG_REVERSE      = "panellog_reverse_order"
CONF_LOG_MAX_ENTRIES  = "panellog_max_entries"

# Temporary B0 Config Items
CONF_B0_ENABLE_MOTION_PROCESSING   = "b0_enable_motion_processing"
CONF_B0_MIN_TIME_BETWEEN_TRIGGERS  = "b0_min_time_between_triggers"
CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT = "b0_max_time_for_trigger_event"

# Schema for config file parsing and access
DEVICE_SOCKET_SCHEMA = vol.Schema({
    vol.Required(CONF_DEVICE_TYPE): 'ethernet',
    vol.Optional(CONF_HOST, default=DEFAULT_DEVICE_HOST): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_DEVICE_PORT): cv.port})

DEVICE_USB_SCHEMA = vol.Schema({
    vol.Required(CONF_DEVICE_TYPE): 'usb',
    vol.Optional(CONF_PATH, default=DEFAULT_DEVICE_USB): cv.string,
    vol.Optional(CONF_DEVICE_BAUD, default=DEFAULT_DEVICE_BAUD): cv.string})

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_DEVICE): vol.Any( DEVICE_SOCKET_SCHEMA, DEVICE_USB_SCHEMA),
        vol.Optional(CONF_EXCLUDE_SENSOR,       default=[]): VISONIC_ID_LIST_SCHEMA,
        vol.Optional(CONF_EXCLUDE_X10,          default=[]): VISONIC_ID_LIST_SCHEMA,
        vol.Optional(CONF_MOTION_OFF_DELAY,     default=120 )  : cv.positive_int,
        vol.Optional(CONF_OVERRIDE_CODE,        default="" )   : cv.string,
        vol.Optional(CONF_DOWNLOAD_CODE,        default="" )   : cv.string,
        vol.Optional(CONF_LANGUAGE,             default="EN" ) : cv.string,
        vol.Optional(CONF_ARM_CODE_AUTO,        default=False) : cv.boolean,
        vol.Optional(CONF_FORCE_STANDARD,       default=False) : cv.boolean,   #        '0', 'false', 'no', 'off', 'disable'
        vol.Optional(CONF_FORCE_AUTOENROLL,     default=True)  : cv.boolean,   #        '0', 'false', 'no', 'off', 'disable'
        vol.Optional(CONF_FORCE_KEYPAD,         default=False) : cv.boolean,   #        '0', 'false', 'no', 'off', 'disable'
        vol.Optional(CONF_AUTO_SYNC_TIME,       default=True ) : cv.boolean,
        vol.Optional(CONF_ENABLE_REMOTE_ARM,    default=False) : cv.boolean,
        vol.Optional(CONF_ENABLE_REMOTE_DISARM, default=False) : cv.boolean,
        vol.Optional(CONF_ENABLE_SENSOR_BYPASS, default=False) : cv.boolean,
        vol.Optional(CONF_LOG_EVENT,            default=False) : cv.boolean,
        vol.Optional(CONF_LOG_DONE,             default=False) : cv.boolean,
        vol.Optional(CONF_LOG_REVERSE,          default=False) : cv.boolean,
        vol.Optional(CONF_LOG_CSV_TITLE,        default=False) : cv.boolean,
        vol.Optional(CONF_LOG_XML_FN,           default="")    : cv.string,
        vol.Optional(CONF_LOG_CSV_FN,           default="")    : cv.string,
        vol.Optional(CONF_LOG_MAX_ENTRIES,      default=10000) : cv.positive_int,
        vol.Optional(CONF_B0_ENABLE_MOTION_PROCESSING, default=False): cv.boolean,
        vol.Optional(CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT,  default=5 ): cv.positive_int,
        vol.Optional(CONF_B0_MIN_TIME_BETWEEN_TRIGGERS,  default=30 ): cv.positive_int
    }),
}, extra=vol.ALLOW_EXTRA)

ALARM_SERVICE_EVENTLOG = vol.Schema({
    vol.Optional(ATTR_CODE, default=""): cv.string,
})

# We only have 2 components, sensors and switches
VISONIC_COMPONENTS = [
    'binary_sensor', 'switch'   # keep switches here to eventually support X10 devices
]

_LOGGER = logging.getLogger(__name__)
#level = logging.getLevelName('INFO')  # INFO
#_LOGGER.setLevel(level)

visonic_event_name = 'alarm_panel_state_update'
command_queue = asyncio.Queue()
panel_reset_counter = 0
myTask = None
SystemStarted = False

# variables for creating the event log for csv and xml
csvdata = None
templatedata = None
     
def setup(hass, base_config):
    """Set up for Visonic devices."""
    
    import custom_components.visonic.pyvisonic as visonicApi   # Connection to python Library
    
    hass = hass
    base_config = base_config
    # Get the user defined config
    config = base_config.get(DOMAIN)

    exclude_sensor_list = config.get(CONF_EXCLUDE_SENSOR)
    exclude_x10_list = config.get(CONF_EXCLUDE_X10)
    
    _LOGGER.info("Exclude sensor list = {0}     Exclude x10 list = {1}".format(exclude_sensor_list, exclude_x10_list))
        
    # This is a callback function, called from the visonic library when a new sensor is detected/created
    #  it adds it to the list of devices and then calls discovery to fully create it in HA
    #  remember that all the sensors may not be created at the same time
    def visonic_event_callback_handler(visonic_devices):
        global csvdata
        global templatedata
        
        # Check to ensure variables are set correctly
        if hass == None:
            _LOGGER.warning("Visonic attempt to add device when hass is undefined")
            return
        if visonic_devices == None:
            _LOGGER.warning("Visonic attempt to add device when sensor is undefined")
            return
        if type(visonic_devices) == defaultdict:  
            # a set of sensors and/or switches. 
            _LOGGER.info("Visonic got new sensors/switches {0}".format( visonic_devices ))
            
            if VISONIC_SENSORS not in hass.data:
                hass.data[VISONIC_SENSORS] = defaultdict(list)
            
            for dev in visonic_devices["sensor"]:
                if dev.getDeviceID() is None:
                    _LOGGER.info("     Sensor ID is None")
                else:
                    #    _LOGGER.info("   Sensor ID {0} full details {1}".format( dev.getDeviceID(), str(dev) ))
                    if dev.getDeviceID() not in exclude_sensor_list:
                        if dev not in hass.data[VISONIC_SENSORS]["binary_sensor"]:
                            hass.data[VISONIC_SENSORS]["binary_sensor"].append(dev)   
                        else:
                            _LOGGER.debug("      Sensor Already in the list")
            
            if VISONIC_X10 not in hass.data:
                hass.data[VISONIC_X10] = defaultdict(list)
            
            for dev in visonic_devices["switch"]:
                #_LOGGER.info("VS: X10 Switch list {0}".format(dev))
                if dev.enabled and dev.getDeviceID() not in exclude_x10_list:
                    if dev not in hass.data[VISONIC_X10]["switch"]:
                        hass.data[VISONIC_X10]["switch"].append(dev)
                    else:
                        _LOGGER.debug("      X10 Already in the list")
                
            #_LOGGER.info("VS: Sensor list {0}".format(hass.data[VISONIC_SENSORS]))
                
            # trigger discovery which will add the sensors and set up a new device
            #    this discovers new sensors, existing ones will remain and are not removed
            discovery.load_platform(hass, "binary_sensor", DOMAIN, {}, base_config)            
            discovery.load_platform(hass, "switch", DOMAIN, {}, base_config)
            
        elif type(visonic_devices) == visonicApi.SensorDevice:
            # This is an update of an existing sensor device
            _LOGGER.info("Individual Sensor update {0} not yet included".format( visonic_devices ))
            
        elif type(visonic_devices) == visonicApi.X10Device:
            # This is an update of an existing x10 device
            _LOGGER.info("Individual X10 update {0} not yet included".format( visonic_devices ))
            
        elif type(visonic_devices) == visonicApi.LogPanelEvent:
            # This is an event log
            _LOGGER.debug("Panel Event Log {0}".format( visonic_devices ))
            reverse = config.get(CONF_LOG_REVERSE)
            total = min(visonic_devices.total, config.get(CONF_LOG_MAX_ENTRIES))
            current = visonic_devices.current   # only used for output and not for logic
            if reverse:
                current = total + 1 - visonic_devices.current
            # Fire event visonic_alarm_panel_event_log
            if config.get(CONF_LOG_EVENT) and visonic_devices.current <= total:
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
                templatedata = []
                csvdata = ""

            if csvdata is not None:
                if reverse:
                    csvdata = "{0}, {1}, {2}, {3}, {4}, {5}, {6}\n".format(current, total, visonic_devices.partition, visonic_devices.date, visonic_devices.time, visonic_devices.zone, visonic_devices.event ) + csvdata
                else:
                    csvdata = csvdata + "{0}, {1}, {2}, {3}, {4}, {5}, {6}\n".format(current, total, visonic_devices.partition, visonic_devices.date, visonic_devices.time, visonic_devices.zone, visonic_devices.event )
                
                datadict = {	
                  "partition" : "{0}".format(visonic_devices.partition),
                  "current"   : "{0}".format(current),
                  "date"      : "{0}".format(visonic_devices.date),
                  "time"      : "{0}".format(visonic_devices.time),
                  "zone"      : "{0}".format(visonic_devices.zone),
                  "event"     : "{0}".format(visonic_devices.event)
                }
                
                templatedata.append(datadict)
                
                if visonic_devices.current == total:
                    # create a new XML file with the results
                    if len(config.get(CONF_LOG_XML_FN)) > 0:
                        if reverse:
                            templatedata.reverse()
                        try:
                            file_loader = FileSystemLoader(['./templates', hass.config.path()+'/templates', './xml', hass.config.path()+'/xml', './www', hass.config.path()+'/www', '.', hass.config.path(), './custom_components/visonic', hass.config.path()+'/custom_components/visonic'], followlinks=True)
                            env = Environment(loader=file_loader)
                            template = env.get_template('visonic_template.xml')
                            output = template.render(entries=templatedata, total=total, available="{0}".format(visonic_devices.total))
                            with open(config.get(CONF_LOG_XML_FN), "w") as f:
                                f.write(output.rstrip())
                                f.close()
                        except:
                            _LOGGER.debug("Panel Event Log - Failed to write XML file")
                    if len(config.get(CONF_LOG_CSV_FN)) > 0:
                        try:
                            if config.get(CONF_LOG_CSV_TITLE):
                                csvdata = "current, total, partition, date, time, zone, event\n" + csvdata
                            with open(config.get(CONF_LOG_CSV_FN), "w") as f:
                                f.write(csvdata.rstrip())
                                f.close()
                        except:
                            _LOGGER.debug("Panel Event Log - Failed to write CSV file")
                    csvdata = None
                    if config.get(CONF_LOG_DONE):
                        hass.bus.fire('visonic_alarm_panel_event_log_complete', {
                            'total': total,
                            'available': visonic_devices.total,
                        })            
            
        elif type(visonic_devices) == int:
            tmp = int(visonic_devices)
            if 1 <= tmp <= 10:   
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
                _LOGGER.info("Visonic update event {0}".format(tmp))
                hass.bus.fire(visonic_event_name, { 'condition': tmp})
                if tmp == 10:
                    message = 'Failed to connect to your Visonic Alarm. We have not received any data from the panel at all, not one single byte.'
                    _LOGGER.error(message)
                    hass.components.persistent_notification.create(
                        message,
                        title=NOTIFICATION_TITLE,
                        notification_id=NOTIFICATION_ID)
                

        else:
            _LOGGER.warning("Visonic attempt to add device with type {0}  device is {1}".format(type(visonic_devices), visonic_devices ))

    def connect_to_alarm():
        global SystemStarted
        
        if SystemStarted:
            return

        global panel_reset_counter
        global command_queue
        global myTask
        
        # remove any existing visonic related sensors (so we don't get entity id already exists exceptions on a restart)
        sensor_list = hass.states.async_entity_ids("binary_sensor")
        if sensor_list is not None:
            for x in sensor_list:
                _LOGGER.info("Checking HA Entity ID: {0}".format(x))
                if x.lower().startswith( 'binary_sensor.visonic_z' ):
                    #device, entity = self.split_entity(x)
                    #self.entities[device][entity]
                    _LOGGER.info("   Removed existing HA Entity ID: {0}".format(x))
#                    entity_object = get_entity(x)
                    hass.add_job(hass.states.async_remove(x))
        
        # Set the Sensors list as empty
        hass.data[VISONIC_SENSORS] = {}
        for domain in VISONIC_COMPONENTS:
            hass.data[VISONIC_SENSORS][domain] = []
        
        # set up config parameters in the visonic library
        visonicApi.setConfig("MotionOffDelay", config.get(CONF_MOTION_OFF_DELAY))
        visonicApi.setConfig("PluginLanguage", config.get(CONF_LANGUAGE))
        visonicApi.setConfig("ForceStandard", config.get(CONF_FORCE_STANDARD))
        visonicApi.setConfig("ForceAutoEnroll", config.get(CONF_FORCE_AUTOENROLL))
        visonicApi.setConfig("AutoSyncTime", config.get(CONF_AUTO_SYNC_TIME))
        visonicApi.setConfig("EnableRemoteArm", config.get(CONF_ENABLE_REMOTE_ARM))
        visonicApi.setConfig("EnableRemoteDisArm", config.get(CONF_ENABLE_REMOTE_DISARM))
        visonicApi.setConfig("EnableSensorBypass", config.get(CONF_ENABLE_SENSOR_BYPASS))
        visonicApi.setConfig("OverrideCode", config.get(CONF_OVERRIDE_CODE))
        visonicApi.setConfig("DownloadCode", config.get(CONF_DOWNLOAD_CODE))
        visonicApi.setConfig("ArmWithoutCode", config.get(CONF_ARM_CODE_AUTO))
        visonicApi.setConfig("ResetCounter", panel_reset_counter)
        visonicApi.setConfig("ForceKeypad", config.get(CONF_FORCE_KEYPAD))

        visonicApi.setConfig("B0_Enable", config.get(CONF_B0_ENABLE_MOTION_PROCESSING))
        visonicApi.setConfig("B0_Min_Interval_Time", config.get(CONF_B0_MIN_TIME_BETWEEN_TRIGGERS))
        visonicApi.setConfig("B0_Max_Wait_Time", config.get(CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT))

        # Get Visonic specific configuration.
        device_type = config.get(CONF_DEVICE)
        
        hass.data[VISONIC_PLATFORM]["command_queue"] = command_queue
        hass.data[VISONIC_PLATFORM]["arm_without_code"] = config.get(CONF_ARM_CODE_AUTO)
        hass.data[VISONIC_PLATFORM]["force_keypad"] = config.get(CONF_FORCE_KEYPAD)
        
        _LOGGER.info("Visonic Connection Device Type is {0}".format(device_type))

        comm = None
        
        # Connect in the way defined by the user in the config file, ethernet or usb
        if device_type["type"] == "ethernet":
            host = device_type[CONF_HOST]
            port = device_type[CONF_PORT]
           
            comm = visonicApi.create_tcp_visonic_connection(address = host, port = port, event_callback = visonic_event_callback_handler, command_queue = command_queue,
                                                            disconnect_callback = disconnect_callback, loop = hass.loop)
        elif device_type["type"] == "usb":
            path = device_type[CONF_PATH]
            baud = device_type[CONF_DEVICE_BAUD]
           
            comm = visonicApi.create_usb_visonic_connection(port = path, baud = baud, event_callback = visonic_event_callback_handler, command_queue = command_queue,
                                                            disconnect_callback = disconnect_callback, loop = hass.loop)

        if comm is not None:
            #wibble = hass.states.entity_ids()
            #for x in wibble:
            #    _LOGGER.info("Wibble is {0}".format(x))            
            SystemStarted = True
            myTask = hass.loop.create_task(comm)
            return True

        myTask = None
        message = 'Failed to connect into Visonic Alarm. Check Settings.'
        _LOGGER.error(message)
        hass.components.persistent_notification.create(
            message,
            title=NOTIFICATION_TITLE,
            notification_id=NOTIFICATION_ID)
        return False
                
    # Service call to close down the current serial connection and re-establish it, we need to reset the whole connection!!!!
    async def service_comms_stop(call):
        global SystemStarted
        global command_queue
        
        if not SystemStarted:
            _LOGGER.warning("Request to Stop the Comms and it is already stopped")
            return

        _LOGGER.info("........... Current Task - Closing Down Current Serial Connection, Queue size is {0}".format(command_queue.qsize()))
        
        # Try to get the asyncio Coroutine within the Task to shutdown the serial link connection properly
        command_queue.put_nowait(["shutdown"])
        while command_queue.qsize() > 0:
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.2)   # not a mistake, wait a bit longer to make sure it's closed as we get no feedback (we only get the fact that the queue is empty)
        
    # Service call to close down the current serial connection and re-establish it, we need to reset the whole connection!!!!
    async def service_panel_stop(call):
        global SystemStarted
        global myTask
        
        if not SystemStarted:
            _LOGGER.warning("Request to Stop the HA alarm_control_panel and it is already stopped")
            return
        # cancel the task from within HA
        if myTask is not None:
            _LOGGER.info("          ........... Closing down Current Task")
            myTask.cancel()
            await asyncio.sleep(2.0)
            if myTask.done():
                _LOGGER.info("          ........... Current Task Done")
            else:
                _LOGGER.info("          ........... Current Task Not Done")
        else:
            _LOGGER.info("          ........... Current Task not set")
        SystemStarted = False

    # Service call to close down the current serial connection and re-establish it, we need to reset the whole connection!!!!
    async def service_panel_start(call):
        global command_queue
        global myTask
        global SystemStarted
        
        if SystemStarted:
            _LOGGER.warning("Request to Start the HA alarm_control_panel and it is already running")
            return

        # re-initialise global variables, do not re-create the queue as we can't pass it to the alarm control panel. There's no need to create it again anyway
        myTask = None

        _LOGGER.info("........... attempting connection")

        hass.data[VISONIC_PLATFORM] = {}

        alarm_entity_exists = False
        alarm_list = hass.states.async_entity_ids("alarm_control_panel")
        if alarm_list is not None:
            _LOGGER.info("Found existing HA alarm_control_panel {0}".format(alarm_list))
            for x in alarm_list:
                _LOGGER.info("    Checking HA Alarm ID: {0}".format(x))
                if x.lower().startswith( 'alarm_control_panel.visonic_alarm' ):
                    _LOGGER.info("       ***** Matched - Alarm Control Panel already exists so keep it ***** : {0}".format(x))
                    alarm_entity_exists = True
        #retval = hass.states.async_remove('alarm_control_panel.visonic_alarm')
        #if retval:
        #    _LOGGER.info("Removed existing HA Entity ID: alarm_control_panel.visonic_alarm")

        if connect_to_alarm():
            discovery.load_platform(hass, "switch", DOMAIN, {}, base_config)
            if not alarm_entity_exists:            
                discovery.load_platform(hass, "alarm_control_panel", DOMAIN, {}, base_config)

    # Service call to close down the current serial connection and re-establish it, we need to reset the whole connection!!!!
    async def service_panel_reconnect(call):
        _LOGGER.warning("User has requested visonic panel reconnection")
        await service_comms_stop(call)
        await service_panel_stop(call)
        await service_panel_start(call)

    async def disconnect_callback_async(excep):
        _LOGGER.error(" ........... attempting reconnection")
        await service_panel_stop(excep)
        await service_panel_start(excep)

    def stop_subscription(event):
        """Shutdown Visonic subscriptions and subscription thread on exit."""
        _LOGGER.info("Shutting down subscriptions")
        asyncio.ensure_future(service_panel_stop(), loop=hass.loop)        

    def disconnect_callback(excep):
        global panel_reset_counter
        if excep is None:
            _LOGGER.error("PyVisonic has caused an exception, no exception information is available")
        else:
            _LOGGER.error("PyVisonic has caused an exception {0}".format(excep))
        # General update trigger
        #    0 is a disconnect and (hopefully) reconnect from an exception (probably comms related)
        hass.bus.fire(visonic_event_name, { 'condition': 0})
        sleep(5.0)
        _LOGGER.error(" ........... setting up reconnection")
        panel_reset_counter = panel_reset_counter + 1
        asyncio.ensure_future(disconnect_callback_async(), loop=hass.loop)        
        
    def decode_code(data) -> str:
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
    def service_panel_eventlog(call):
        global command_queue
        _LOGGER.info('alarm control panel received event log request')
        if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>":
            code = ''
            if ATTR_CODE in call.data:
                code = call.data[ATTR_CODE]
            _LOGGER.info('alarm control panel making event log request')
            command_queue.put_nowait(["eventlog", decode_code(code)])
        else:
            _LOGGER.info('alarm control panel not making event log request {0} {1}'.format(type(call.data), call.data))

#    def get_entity(name):
#        # Take 'foo.bar.baz' and return self.entities.foo.bar.baz.
#        # This makes it easy to convert a string to an arbitrary entity.
#        elems = name.split(".")
#        obj = hass.entities
#        for e in elems:
#            obj = getattr(obj, e)
#        return obj

    # start of main function
    try:
        hass.data[VISONIC_PLATFORM] = {}
        
        # Establish a callback to stop the component when the stop event occurs
        hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, stop_subscription)

        hass.services.async_register(DOMAIN, 'alarm_panel_reconnect', service_panel_reconnect)
        hass.services.register(DOMAIN, 'alarm_panel_eventlog', service_panel_eventlog, schema=ALARM_SERVICE_EVENTLOG)
                
        success = connect_to_alarm()
        
        if success:
            # these 2 calls will create a partition "alarm control panel" and a switch that represents the panel information
            #   eventually there will be an "alarm control panel" for each partition but we only support 1 partition at the moment
            discovery.load_platform(hass, "switch", DOMAIN, {}, base_config)   
            discovery.load_platform(hass, "alarm_control_panel", DOMAIN, {}, base_config)  
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
