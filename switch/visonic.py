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
from homeassistant.util import convert
from homeassistant.const import (ATTR_ARMED, ATTR_BATTERY_LEVEL, ATTR_LAST_TRIP_TIME, ATTR_TRIPPED)
from homeassistant.components.switch import SwitchDevice

DEPENDENCIES = ['visonic']
#REQUIREMENTS = ['pyvisonic==0.0.1']

_LOGGER = logging.getLogger(__name__)

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the visonic controller devices."""
    
    va = VisonicAlarm(hass, 1)
    
    # Listener to handle fired events
    def handle_event_switch_status(event):
        _LOGGER.debug('alarm state panel received update event')
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
        self.schedule_update_ha_state(True)
        
    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def should_poll(self):
        """Get polling requirement from visonic device."""
        return True # self.visonic_device.should_poll

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._address
        
    @property
    def state(self):
        """Return the name of the sensor."""
        return self.current_value

    #def entity_picture(self):
    #    return "/config/myimages/20160807_183340.jpg"
        
    @property
    def state_attributes(self):  #device_
        """Return the state attributes of the device."""
        # maybe should filter rather than sending them all
        return visonicApi.PanelStatus
