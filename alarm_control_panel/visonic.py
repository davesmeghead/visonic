"""
Support for visonic partitions control when used with a connection to a Visonic Alarm Panel.
Currently, there is only support for a single partition

  Initial setup by David Field

"""
import logging

import custom_components.pyvisonic as visonicApi   # Connection to python Library

import homeassistant.components.alarm_control_panel as alarm

#from homeassistant.components.alarm_control_panel import AlarmControlPanel
from homeassistant.const import STATE_UNKNOWN, STATE_ALARM_DISARMED, STATE_ALARM_ARMED_AWAY, STATE_ALARM_ARMED_NIGHT, STATE_ALARM_ARMED_HOME, STATE_ALARM_PENDING, STATE_ALARM_ARMING, STATE_ALARM_TRIGGERED
from custom_components.visonic import VISONIC_PLATFORM

DEPENDENCIES = ['visonic']

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
        if va is not None:
            va.doUpdate()
    
    hass.bus.listen('alarm_panel_state_update', handle_event_alarm_panel)
    
    devices = []
    devices.append(va)
    
    add_devices(devices, True)   
    

class VisonicAlarm(alarm.AlarmControlPanel):
    """Representation of a Visonic alarm control panel."""

    def __init__(self, hass, partition_id, queue, uawc):
        """Initialize a Visonic security camera."""
        #self._data = data
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
        # maybe should filter rather than sending them all
        return None
        
    @property
    def state_attributes(self):  #
        """Return the state attributes of the device."""
        # maybe should filter rather than sending them all
        return visonicApi.PanelStatus

    @property
    def code_format(self):
        """Regex for code format or None if no code is required."""
        #_LOGGER.info("code format called *****************************") 

        # try powerlink mode first, if in powerlink then it already has the user codes
        panelmode = visonicApi.PanelStatus["Mode"]
        if panelmode is not None:
            if panelmode == "Powerlink":
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
        return ""

    def alarm_disarm(self, code = None):
        """Send disarm command."""
        if self.queue is not None:
            _LOGGER.info("alarm disarm code=" + self.decode_code(code))        
            self.queue.put_nowait(["Disarmed", self.decode_code(code)])

    def alarm_arm_home(self, code = None):
        """Send arm home command."""
        if self.queue is not None:
            _LOGGER.info("alarm arm home=" + self.decode_code(code))
            self.queue.put_nowait(["Stay", self.decode_code(code)])

    def alarm_arm_away(self, code = None):
        """Send arm away command."""
        if self.queue is not None:
            _LOGGER.info("alarm arm away=" + self.decode_code(code))
            self.queue.put_nowait(["Armed", self.decode_code(code)])

    def alarm_arm_night(self, code = None):
        """Send arm night command."""
        if self.queue is not None:
            _LOGGER.info("alarm night=" + self.decode_code(code))
            self.queue.put_nowait(["Night", self.decode_code(code)])
