"""
Support for visonic partitions when used with a connection to a Visonic Alarm Panel.
Currently, there is only support for a single partition

  Initial setup by David Field

"""
#ExitDelay_ArmHome(Home Exit Delay)
import logging
import asyncio
import custom_components.pyvisonic as visonicApi

from datetime import timedelta
from homeassistant.helpers.entity import Entity
from homeassistant.const import ATTR_ARMED, ATTR_BATTERY_LEVEL, ATTR_LAST_TRIP_TIME, ATTR_TRIPPED, ATTR_CODE, STATE_STANDBY, STATE_ALARM_DISARMED, STATE_ALARM_ARMED_AWAY, STATE_ALARM_DISARMING, STATE_ALARM_ARMED_NIGHT, STATE_ALARM_ARMED_HOME, STATE_ALARM_PENDING, STATE_ALARM_ARMING, STATE_ALARM_TRIGGERED
from homeassistant.util import convert
from homeassistant.components.switch import SwitchDevice

DEPENDENCIES = ['visonic']

_LOGGER = logging.getLogger(__name__)

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the visonic controller devices."""
    
    va = VisonicAlarm(hass, 1)
    
    # Listener to handle fired events
    def handle_event_switch_status(event):
        _LOGGER.info('alarm state panel received update event')
        if va is not None:
            va.doUpdate()

    hass.bus.listen('alarm_panel_state_update', handle_event_switch_status)
    
    devices = []
    
    # currently only supports partition 1
    devices.append(va)

    add_devices(devices, True)


class VisonicAlarm(SwitchDevice):
    """Representation of a Visonic Panel."""

    def __init__(self, hass, partition):
        """Initialise a Visonic device."""
        self._name = "Visonic Alarm Panel"
        self._address = "Visonic_Partition_" + str(partition)     # the only thing that is available on startup, eventually need to start all partitions
        self.current_value = self._name

    def doUpdate(self):    
        self.schedule_update_ha_state(False)
        
    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def should_poll(self):
        """Get polling requirement from visonic device."""
        return False # self.visonic_device.should_poll

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._address
        
    @property
    def state(self):    
        """Return the state of the device."""
        #isArmed = visonicApi.PanelStatus["Panel Armed"]
        
        armcode = visonicApi.PanelStatus["Panel Status Code"]
        sirenActive = visonicApi.PanelStatus["Panel Siren Active"]
        
        # -1  Not yet defined
        # 0   Disarmed
        # 1   Exit Delay Arm Home
        # 2   Exit Delay Arm Away
        # 3   Entry Delay
        # 4   Armed Home
        # 5   Armed Away
        # 6   Special ("User Test", "Downloading", "Programming", "Installer")
        
        #_LOGGER.warning("alarm armcode is " + str(armcode))
        
        if sirenActive == 'Yes':
            return STATE_ALARM_TRIGGERED
        elif armcode == 0 or armcode == 6:
            return STATE_ALARM_DISARMED
        elif armcode == 1:
            return STATE_ALARM_PENDING
        elif armcode == 2:
            return STATE_ALARM_ARMING
        elif armcode == 3:
            return STATE_ALARM_DISARMING
        elif armcode == 4:
            return STATE_ALARM_ARMED_HOME
        elif armcode == 5:
            return STATE_ALARM_ARMED_AWAY
        
        return STATE_STANDBY

    #def entity_picture(self):
    #    return "/config/myimages/20160807_183340.jpg"
        
    @property
    def state_attributes(self):  #
        """Return the state attributes of the device."""
        # maybe should filter rather than sending them all
        return visonicApi.PanelStatus
        
    @property
    def device_state_attributes(self):  #
        """Return the state attributes of the device."""
        # maybe should filter rather than sending them all
        return visonicApi.PanelStatus
