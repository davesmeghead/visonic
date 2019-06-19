"""
This component is used to create a connection to a Visonic Power Max or PowerMaster Alarm SystemError
Currently, there is only support for a single partition

The Connection can be made using Ethernet TCP, USB (connection to RS232) or directly by RS232

  Initial setup by David Field

"""

import logging
import voluptuous as vol
#import homeassistant.helpers.entity_registry
import asyncio

from collections import defaultdict
from homeassistant.util.dt import utc_from_timestamp
from homeassistant.util import convert, slugify
from homeassistant.helpers import discovery
from homeassistant.helpers import config_validation as cv
from homeassistant.const import (ATTR_ARMED, EVENT_HOMEASSISTANT_STOP, CONF_HOST, CONF_PORT, CONF_PATH, CONF_DEVICE)
from homeassistant.helpers.entity import Entity
from requests import ConnectTimeout, HTTPError
from time import sleep

# Visonic has Motion Sensors (PIR and Magnetic contact mainly) and X10 devices
VISONIC_PLATFORM = 'visonic_platform'

from custom_components.visonic.switch import VISONIC_X10
from custom_components.visonic.binary_sensor import VISONIC_SENSORS

REQUIREMENTS = ['pyserial', 'pyserial_asyncio', 'datetime']

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
CONF_AUTO_SYNC_TIME = "sync_time"
CONF_ENABLE_REMOTE_ARM = "allow_remote_arm"
CONF_ENABLE_REMOTE_DISARM = "allow_remote_disarm"
CONF_ENABLE_SENSOR_BYPASS = "allow_sensor_bypass"
CONF_OVERRIDE_CODE = "override_code"
CONF_DOWNLOAD_CODE = "download_code"
CONF_ARM_CODE_AUTO = "arm_without_usercode"

CONF_EXCLUDE_SENSOR = "exclude_sensor"
CONF_EXCLUDE_X10 = "exclude_x10"

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
        vol.Optional(CONF_MOTION_OFF_DELAY,     120 ) : cv.positive_int,
        vol.Optional(CONF_OVERRIDE_CODE,        "" )  : cv.string,
        vol.Optional(CONF_DOWNLOAD_CODE,        "" )  : cv.string,
        vol.Optional(CONF_LANGUAGE,             "EN" ): cv.string,
        vol.Optional(CONF_ARM_CODE_AUTO,        False): cv.boolean,
        vol.Optional(CONF_FORCE_STANDARD,       False): cv.boolean,   #        '0', 'false', 'no', 'off', 'disable'
        vol.Optional(CONF_AUTO_SYNC_TIME,       True ): cv.boolean,
        vol.Optional(CONF_ENABLE_REMOTE_ARM,    False): cv.boolean,
        vol.Optional(CONF_ENABLE_REMOTE_DISARM, False): cv.boolean,
        vol.Optional(CONF_ENABLE_SENSOR_BYPASS, False): cv.boolean
    }),
}, extra=vol.ALLOW_EXTRA)

# We only have 2 components, sensors and switches
VISONIC_COMPONENTS = [
    'binary_sensor', 'switch'   # keep switches here to eventually support X10 devices
]

_LOGGER = logging.getLogger(__name__)
#level = logging.getLevelName('INFO')  # INFO
#_LOGGER.setLevel(level)
     
command_queue = asyncio.Queue()
panel_reset_counter = 0
     
def setup(hass, base_config):
    """Set up for Visonic devices."""
    
    import custom_components.visonic.pyvisonic as visonicApi   # Connection to python Library
    
    hass = hass
    base_config = base_config
    # Get the user defined config
    config = base_config.get(DOMAIN)

    def stop_subscription(event):
        """Shutdown Visonic subscriptions and subscription thread on exit."""
        _LOGGER.info("Shutting down subscriptions")

    # This is a callback function, called from the visonic library when a new sensor is detected/created
    #  it adds it to the list of devices and then calls discovery to fully create it in HA
    #  remember that all the sensors may not be created at the same time
    def visonic_event_callback_handler(visonic_devices):
        
        exclude_sensor_list = config.get(CONF_EXCLUDE_SENSOR)
        exclude_x10_list = config.get(CONF_EXCLUDE_X10)
        
        _LOGGER.info("Exclude sensor list = {0}     Exclude x10 list = {1}".format(exclude_sensor_list, exclude_x10_list))
        
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
            sensor_devices = defaultdict(list)
            for dev in visonic_devices["sensor"]:
                if dev.getDeviceID() not in exclude_sensor_list:
                    sensor_devices["binary_sensor"].append(dev)                
            
            hass.data[VISONIC_SENSORS] = sensor_devices

            #_LOGGER.info("Dumping X10 Devices")
            x10_devices = defaultdict(list)
            for dev in visonic_devices["switch"]:
                #_LOGGER.info("VS: X10 Switch list {0}".format(dev))
                if dev.enabled and dev.getDeviceID() not in exclude_x10_list:
                    x10_devices["switch"].append(dev)
                
            hass.data[VISONIC_X10] = x10_devices    
                
            #_LOGGER.info("VS: Sensor list {0}".format(hass.data[VISONIC_SENSORS]))
                
            # trigger discovery which will add the sensors and set up a new device
            #    this discovers new sensors, existing ones will remain and are not removed
            discovery.load_platform(hass, "binary_sensor", DOMAIN, {}, base_config)            
            discovery.load_platform(hass, "switch", DOMAIN, {}, base_config)
            
        elif type(visonic_devices) == visonicApi.SensorDevice:
            # This is an update of an existing sensor device
            _LOGGER.info("Sensor update {0} not yet included".format( visonic_devices ))
            
        elif type(visonic_devices) == visonicApi.X10Device:
            # This is an update of an existing x10 device
            _LOGGER.info("X10 update {0} not yet included".format( visonic_devices ))
            
        elif type(visonic_devices) == visonicApi.LogPanelEvent:
            # This is an update of the event log
            _LOGGER.info("Event Log update {0} not yet implemented".format( visonic_devices ))
            
        elif visonic_devices >= 1 and visonic_devices <= 10:   
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
            _LOGGER.info("Visonic update event {0}".format(visonic_devices ))
            hass.bus.fire('alarm_panel_state_update', { 'condition': visonic_devices })

        else:
            _LOGGER.warning("Visonic attempt to add device with type {0}  device is {1}".format(type(visonic_devices), visonic_devices ))


    def disconnect_callback(excep):
        global panel_reset_counter
        if excep is None:
            _LOGGER.error("PyVisonic has caused an exception, no exception information is available")
        else:
            _LOGGER.error("PyVisonic has caused an exception {0}".format(excep))
        sleep(10.0)
        panel_reset_counter = panel_reset_counter + 1
        _LOGGER.error(" ........... attempting reconnection")
        if connect_to_alarm():
            discovery.load_platform(hass, "switch", DOMAIN, {}, base_config)   
            discovery.load_platform(hass, "alarm_control_panel", DOMAIN, {}, base_config)   
        
#    def get_entity(name):
#        # Take 'foo.bar.baz' and return self.entities.foo.bar.baz.
#        # This makes it easy to convert a string to an arbitrary entity.
#        elems = name.split(".")
#        obj = hass.entities
#        for e in elems:
#            obj = getattr(obj, e)
#        return obj

    def connect_to_alarm():
        global panel_reset_counter

        # remove any existing visonic related sensors (so we don't get entity id already exists exceptions on a restart)
        retval = hass.states.async_remove('switch.visonic_alarm_panel')
        if retval:
            _LOGGER.info("Removed existing HA Entity ID: switch.visonic_alarm_panel")
        retval = hass.states.async_remove('alarm_control_panel.visonic_alarm')
        if retval:
            _LOGGER.info("Removed existing HA Entity ID: alarm_control_panel.visonic_alarm")
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
        visonicApi.setConfig("AutoSyncTime", config.get(CONF_AUTO_SYNC_TIME))
        visonicApi.setConfig("EnableRemoteArm", config.get(CONF_ENABLE_REMOTE_ARM))
        visonicApi.setConfig("EnableRemoteDisArm", config.get(CONF_ENABLE_REMOTE_DISARM))
        visonicApi.setConfig("EnableSensorBypass", config.get(CONF_ENABLE_SENSOR_BYPASS))
        visonicApi.setConfig("OverrideCode", config.get(CONF_OVERRIDE_CODE))
        visonicApi.setConfig("DownloadCode", config.get(CONF_DOWNLOAD_CODE))
        visonicApi.setConfig("ArmWithoutCode", config.get(CONF_ARM_CODE_AUTO))
        visonicApi.setConfig("ResetCounter", panel_reset_counter)

        # Get Visonic specific configuration.
        device_type = config.get(CONF_DEVICE)
        
        hass.data[VISONIC_PLATFORM]["command_queue"] = command_queue
        hass.data[VISONIC_PLATFORM]["arm_without_code"] = config.get(CONF_ARM_CODE_AUTO)
        
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
            notused = hass.loop.create_task(comm)
            return True

        message = 'Failed to connect into Visonic Alarm. Check Settings.'
        _LOGGER.error(message)
        hass.components.persistent_notification.create(
            message,
            title=NOTIFICATION_TITLE,
            notification_id=NOTIFICATION_ID)
        return False
                
                
                
    # start of main function
    try:
        hass.data[VISONIC_PLATFORM] = {}
        
        # Establish a callback to stop the component when the stop event occurs
        hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, stop_subscription)

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
