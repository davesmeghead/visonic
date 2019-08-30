"""
Support for visonic partitions control when used with a connection to a Visonic Alarm Panel.
Currently, there is only support for a single partition

  Initial setup by David Field

"""
import logging
import asyncio
import homeassistant.helpers.config_validation as cv
import homeassistant.components.alarm_control_panel as alarm
import voluptuous as vol

from datetime import timedelta
from homeassistant.helpers.entity_component import EntityComponent
from custom_components.visonic import DOMAIN

from homeassistant.const import (
    ATTR_CODE, ATTR_CODE_FORMAT, ATTR_ENTITY_ID, SERVICE_ALARM_TRIGGER,
    SERVICE_ALARM_DISARM, SERVICE_ALARM_ARM_HOME, SERVICE_ALARM_ARM_AWAY,
    SERVICE_ALARM_ARM_NIGHT, SERVICE_ALARM_ARM_CUSTOM_BYPASS)

from homeassistant.const import (STATE_UNKNOWN, STATE_ALARM_DISARMED, STATE_ALARM_ARMED_AWAY, 
    STATE_ALARM_ARMED_NIGHT, STATE_ALARM_ARMED_HOME, STATE_ALARM_PENDING, STATE_ALARM_ARMING, STATE_ALARM_TRIGGERED)

from custom_components.visonic import VISONIC_PLATFORM
from homeassistant.core import valid_entity_id, split_entity_id

DEPENDENCIES = ['visonic']

SCAN_INTERVAL = timedelta(seconds=30)

ATTR_BYPASS = 'bypass'

ALARM_SERVICE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    vol.Optional(ATTR_BYPASS, default=False): cv.boolean,
    vol.Optional(ATTR_CODE, default=""): cv.string,
})

_LOGGER = logging.getLogger(__name__)

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Visonic alarms."""

    queue = None
    uawc = False
    if VISONIC_PLATFORM in hass.data:
        if "command_queue" in hass.data[VISONIC_PLATFORM]:
            queue = hass.data[VISONIC_PLATFORM]["command_queue"]
        if "arm_without_code" in hass.data[VISONIC_PLATFORM]:
            uawc = hass.data[VISONIC_PLATFORM]["arm_without_code"]

    va = VisonicAlarm(hass, 1, queue, uawc)  

    # Listener to handle fired events
    def handle_event_alarm_panel(event):
        _LOGGER.info('alarm control panel received update event')
        # There is a "condition value in the data but we don't need it, just do an update for every change
        if va is not None:
            va.doUpdate()
    
    # Service call to bypass individual sensors
    def service_sensor_bypass(call):
        #_LOGGER.info('alarm control panel service sensor bypass')
        if va is not None:
            va.sensor_bypass(call.data)
    
    hass.bus.listen('alarm_panel_state_update', handle_event_alarm_panel)
    
    hass.services.register(DOMAIN, 'alarm_sensor_bypass', service_sensor_bypass, schema=ALARM_SERVICE_SCHEMA)

    devices = []
    devices.append(va)
    
    add_devices(devices, True)  
    

class VisonicAlarm(alarm.AlarmControlPanel):
    """Representation of a Visonic alarm control panel."""

    def __init__(self, hass, partition_id, queue, uawc):
        """Initialize a Visonic security camera."""
        #self._data = data
        self.hass = hass
        self.partition_id = partition_id
        self.queue = queue
        self.user_arm_without_code = uawc
        self.mystate = STATE_UNKNOWN
        self.myname = "Visonic Alarm"

    def doUpdate(self):    
        self.schedule_update_ha_state(False)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self.myname + "_" + str(self.partition_id)

    @property
    def name(self):
        """Return the name of the alarm."""
        return self.myname  # partition 1 but eventually differentiate partitions

    @property
    def should_poll(self):
        return False;

    @property
    def device_state_attributes(self):  #
        """Return the state attributes of the device."""
        import custom_components.visonic.pyvisonic as visonicApi   # Connection to python Library
 
        # maybe should filter rather than sending them all
        return visonicApi.PanelStatus

# DO NOT OVERRIDE state_attributes AS IT IS USED IN THE LOVELACE FRONTEND TO DETERMINE code_format
        
    @property
    def code_format(self):
        """Regex for code format or None if no code is required."""
        #_LOGGER.info("code format called *****************************") 
        import custom_components.visonic.pyvisonic as visonicApi   # Connection to python Library

        # try powerlink mode first, if in powerlink then it already has the user codes
        panelmode = visonicApi.PanelStatus["Mode"]
        if panelmode is not None:
            if panelmode == "Powerlink" or panelmode == "Standard Plus":
                #_LOGGER.info("code format none as powerlink *****************************") 
                return None
                
        # we aren't in powerlink
        
        # If currently Disarmed and user setting to not show panel to arm
        armcode = None
        if "Panel Status Code" in visonicApi.PanelStatus:
            armcode = visonicApi.PanelStatus["Panel Status Code"]
            
        if armcode is None:
            return None

        if armcode == 0 and self.user_arm_without_code:
            return None

        overridecode = visonicApi.PanelSettings["OverrideCode"]
        if overridecode is not None:
            if len(overridecode) == 4:
                #_LOGGER.info("code format none as code set in config file *****************************") 
                return None

        #_LOGGER.info("code format number *****************************") 
        return "number"

    @property
    def state(self):
        """Return the state of the device."""
        import custom_components.visonic.pyvisonic as visonicApi   # Connection to python Library
        sirenactive = 'No'
        if "Panel Siren Active" in visonicApi.PanelStatus:
            sirenactive = visonicApi.PanelStatus["Panel Siren Active"]

        if sirenactive == 'Yes':
            self.mystate = STATE_ALARM_TRIGGERED
            return STATE_ALARM_TRIGGERED
            
        armcode = None
        if "Panel Status Code" in visonicApi.PanelStatus:
            armcode = visonicApi.PanelStatus["Panel Status Code"]
        
        # -1  Not yet defined
        # 0   Disarmed
        # 1   Exit Delay Arm Home
        # 2   Exit Delay Arm Away
        # 3   Entry Delay
        # 4   Armed Home
        # 5   Armed Away
        # 10  Home Bypass
        # 11  Away Bypass
        # 20  Armed Home Instant
        # 21  Armed Away Instant
        #   "Disarmed", "Home Exit Delay", "Away Exit Delay", "Entry Delay", "Armed Home", "Armed Away", "User Test",
        #   "Downloading", "Programming", "Installer", "Home Bypass", "Away Bypass", "Ready", "Not Ready", "??", "??",
        #   "Disarmed Instant", "Home Instant Exit Delay", "Away Instant Exit Delay", "Entry Delay Instant", "Armed Home Instant",
        #   "Armed Away Instant"
        
        #_LOGGER.warning("alarm armcode is " + str(armcode))
        
        if armcode is None:
            self.mystate = STATE_UNKNOWN
        elif armcode == 0:
            self.mystate = STATE_ALARM_DISARMED
        elif armcode == 1 or armcode == 3:          # Exit delay home or entry delay. This should allow user to enter code
            self.mystate = STATE_ALARM_PENDING
        elif armcode == 2:
            self.mystate = STATE_ALARM_ARMING
        elif armcode == 4 or armcode == 10 or armcode == 20:
            self.mystate = STATE_ALARM_ARMED_HOME
        elif armcode == 5 or armcode == 11 or armcode == 21:
            self.mystate = STATE_ALARM_ARMED_AWAY
        else:
            self.mystate = STATE_UNKNOWN
            
        return self.mystate

    # RequestArm
    #       state is one of: "Disarmed", "Stay", "Armed", "UserTest", "StayInstant", "ArmedInstant", "Night", "NightInstant"
    #        we need to add "log" and "bypass"
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    # call in to pyvisonic in an async way this function : def RequestArm(state, pin = ""):

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

    def alarm_disarm(self, code = None):
        """Send disarm command."""  
        if self.queue is not None:
            #_LOGGER.info("alarm disarm code=" + self.decode_code(code))        
            self.queue.put_nowait(["Disarmed", self.decode_code(code)])

    def alarm_arm_home(self, code = None):
        """Send arm home command."""
        if self.queue is not None:
            #_LOGGER.info("alarm arm home=" + self.decode_code(code))
            self.queue.put_nowait(["Stay", self.decode_code(code)])

    def alarm_arm_away(self, code = None):
        """Send arm away command."""
        if self.queue is not None:
            #_LOGGER.info("alarm arm away=" + self.decode_code(code))
            self.queue.put_nowait(["Armed", self.decode_code(code)])

    def alarm_arm_night(self, code = None):
        """Send arm night command."""
        if self.queue is not None:
            #_LOGGER.info("alarm night=" + self.decode_code(code))
            self.queue.put_nowait(["Night", self.decode_code(code)])

    def alarm_trigger(self, code=None):
        """Send alarm trigger command."""
        raise NotImplementedError()
        
    def dump_dict(self, mykeys):
        for key, value in mykeys.items():
            _LOGGER.info(key + " has value " + str(value))

    def async_alarm_custom_sensor_bypass(hass, code=None, entity_id=None):
        return self.hass.async_add_job(self.alarm_custom_sensor_bypass, code, entity_id)

    # Service alarm_control_panel.alarm_sensor_bypass
    # {"entity_id": "binary_sensor.visonic_z01", "bypass":"True", "code":"1234" }
    def sensor_bypass(self, data=None):
        _LOGGER.info("Custom visonic alarm sensor bypass " + str(type(data)))
        #if type(data) is str:
        #    _LOGGER.info("  Sensor_bypass = String " + str(data) )
        if type(data) is dict or str(type(data)) == "<class 'mappingproxy'>":
            #_LOGGER.info("  Sensor_bypass = " + str(type(data)))
            self.dump_dict(data)

            if ATTR_ENTITY_ID in data:            
                eid = str(data[ATTR_ENTITY_ID])
                #_LOGGER.info("    A entity id = " + eid)
                if not eid.startswith('binary_sensor.'):
                    eid = 'binary_sensor.' + eid
                #_LOGGER.info("    B entity id = " + eid)
                if valid_entity_id(eid):
                    #_LOGGER.info("    C entity id = " + eid)
                    mystate = self.hass.states.get(eid)
                    if mystate is not None:
                        #_LOGGER.info("    alarm mystate5 = " + str(mystate))
                        #_LOGGER.info("    alarm mystate6 = " + str(mystate.as_dict()))
                        devid = mystate.attributes['visonic device']
                        code = ''
                        if ATTR_CODE in data:
                            code = data[ATTR_CODE]
                        bypass = False
                        if ATTR_BYPASS in data:
                            bypass = data[ATTR_BYPASS]
                        if devid >= 1 and devid <= 64:
                            #if bypass:
                            #    _LOGGER.info("Attempt to bypass sensor device id = " + str(devid))
                            #else:
                            #    _LOGGER.info("Attempt to restore (arm) sensor device id = " + str(devid))
                            self.queue.put_nowait(["bypass", devid, bypass, self.decode_code(code)])
                        else:
                            _LOGGER.warning("Attempt to bypass sensor with incorrect parameters device id = " + str(devid))

    def alarm_arm_custom_bypass(self, data=None):
        _LOGGER.info("Alarm Panel Custom Bypass Not Yet Implemented")
