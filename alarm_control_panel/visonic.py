"""
Support for visonic partitions control when used with a connection to a Visonic Alarm Panel.
Currently, there is only support for a single partition

  Initial setup by David Field

"""
import logging

import custom_components.pyvisonic as visonicApi   # Connection to python Library

from homeassistant.components.alarm_control_panel import AlarmControlPanel
from homeassistant.components.canary import DATA_CANARY
from homeassistant.const import STATE_ALARM_DISARMED, STATE_ALARM_ARMED_AWAY, STATE_ALARM_ARMED_NIGHT, STATE_ALARM_ARMED_HOME, STATE_ALARM_PENDING, STATE_ALARM_ARMING
from custom_components.visonic import (VISONIC_PLATFORM, VISONIC_SENSORS)

DEPENDENCIES = ['visonic']

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Canary alarms."""

    #data = hass.data[VISONIC_PLATFORM]
    usercode = ""
    queue = None
    if VISONIC_PLATFORM in hass.data:
        if "usercode" in hass.data[VISONIC_PLATFORM]:
            usercode = hass.data[VISONIC_PLATFORM]["usercode"]
        if "command_queue" in hass.data[VISONIC_PLATFORM]:
            queue = hass.data[VISONIC_PLATFORM]["command_queue"]

    va = VisonicAlarm(hass, 1, usercode, queue)    
    
    # Listener to handle fired events
    def handle_event_alarm_panel(event):
        _LOGGER.info('alarm control panel received update event')
        va.doUpdate()
        
    hass.bus.listen('alarm_panel_state_update', handle_event_alarm_panel)
    
    devices = []

    #for location in data.locations:
    devices.append(va)

    add_devices(devices, True)   
    

class VisonicAlarm(AlarmControlPanel):
    """Representation of a Canary alarm control panel."""

    def __init__(self, hass, partition_id, usercode, queue):
        """Initialize a Canary security camera."""
        #self._data = data
        self.partition_id = partition_id
        self.usercode = usercode
        self.queue = queue
        # Listen for when my_cool_event is fired

    def doUpdate(self):    
        self.schedule_update_ha_state(True)
        
    @property
    def name(self):
        """Return the name of the alarm."""
        return "Visonic Alarm"  # partition 1 but eventually differtiate partitions

    @property
    def state(self):
        """Return the state of the device."""
        #isArmed = visonicApi.PanelStatus["PanelArmed"]
        
        armcode = visonicApi.PanelStatus["PanelStatusCode"]
        
        # 0   Disarmed
        # 1   Exit Delay Arm Home
        # 2   Exit Delay Arm Away
        # 4   Armed Home
        # 5   Armed Away
        
        if armcode == 1:
            return STATE_ALARM_PENDING
        elif armcode == 2:
            return STATE_ALARM_ARMING
        elif armcode == 4:
            return STATE_ALARM_ARMED_HOME
        elif armcode == 5:
            return STATE_ALARM_ARMED_AWAY
        
        return STATE_ALARM_DISARMED

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        #location = self._data.get_location(self._location_id)
        return {
            'private': False,
            "Panel Mode"  : visonicApi.PanelStatus["Mode"],
            "Panel Ready" : visonicApi.PanelStatus["PanelReady"]
        }


    # RequestArm
    #       state is one of: "Disarmed", "Stay", "Armed", "UserTest", "StayInstant", "ArmedInstant", "Night", "NightInstant"
    #        we need to add "log" and "bypass"
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    #          if len(self.usercode) > 0
    # call in to pyvisonic in an async way this function : def RequestArm(state, pin = ""):

    def alarm_disarm(self, code=""):
        """Send disarm command."""
        #location = self._data.get_location(self._location_id)
        #self._data.set_location_mode(self._location_id, location.mode.name, True)
        if self.queue is not None:
            _LOGGER.info("alarm disarm")        
            self.queue.put_nowait(["Disarmed", self.usercode if len(code) != 4 else code])

    def alarm_arm_home(self, code=""):
        """Send arm home command."""
        #from canary.api import LOCATION_MODE_HOME
        #self._data.set_location_mode(self._location_id, LOCATION_MODE_HOME)
        if self.queue is not None:
            _LOGGER.info("alarm arm home")
            self.queue.put_nowait(["Stay", self.usercode if len(code) != 4 else code])

    def alarm_arm_away(self, code=""):
        """Send arm away command."""
        #from canary.api import LOCATION_MODE_AWAY
        #self._data.set_location_mode(self._location_id, LOCATION_MODE_AWAY)
        if self.queue is not None:
            _LOGGER.info("alarm arm away")
            self.queue.put_nowait(["Armed", self.usercode if len(code) != 4 else code])

    def alarm_arm_night(self, code=""):
        """Send arm night command."""
        #from canary.api import LOCATION_MODE_NIGHT
        #self._data.set_location_mode(self._location_id, LOCATION_MODE_NIGHT)
        if self.queue is not None:
            _LOGGER.info("alarm night")
            self.queue.put_nowait(["Night", self.usercode if len(code) != 4 else code])
