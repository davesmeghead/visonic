"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System and Create a Simple Entity to Report Status only."""

import logging
import voluptuous as vol
from enum import IntEnum
from typing import Callable, List
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import Entity
from homeassistant.core import HomeAssistant

# Use the standard HA core attributes, alarm states and services to report status
from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMED_HOME,
    STATE_ALARM_ARMING,
    STATE_ALARM_DISARMED,
    STATE_ALARM_PENDING,
    STATE_ALARM_TRIGGERED,
    STATE_UNKNOWN,
)

from .client import VisonicClient

from .const import (
    DOMAIN,
    DOMAINCLIENT,
    DOMAINDATA,
    PANEL_ATTRIBUTE_NAME,
    MONITOR_SENSOR_STR,
)

from .pyconst import AlPanelStatus

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Callable[[List[Entity], bool], None],
) -> None:
    """Set up the Visonic Alarm Sensors for Monitor."""
    _LOGGER.debug("************* sensor async_setup_entry **************")
    if DOMAIN in hass.data:
        #_LOGGER.debug("   In binary sensor async_setup_entry")
        client = hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id]
        va = VisonicSensor(hass, client, 1)
        # Add it to HA
        devices = [va]
        async_add_entities(devices, True)

class VisonicSensor(Entity):
    """Representation of a Visonic alarm control panel as a simple sensor for monitor."""

    def __init__(self, hass: HomeAssistant, client: VisonicClient, partition_id: int):
        """Initialize a Visonic security alarm."""
        self._client = client
        self.hass = hass
        client.onChange(self.onChange)
        self._partition_id = partition_id
        self._mystate = STATE_UNKNOWN
        self._myname = client.getAlarmPanelUniqueIdent()
        self._device_state_attributes = {}
        self._last_triggered = ""
        self._panel = client.getPanelID()
        _LOGGER.debug(f"Initialising alarm panel sensor {self._myname} panel {self._panel}")

    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        self._client = None
        _LOGGER.debug(f"Removing alarm panel sensor {self._myname} panel {self._panel}")

    def isPanelConnected(self) -> bool:
        """Are we connected to the Alarm Panel."""
        # If we are starting up or have been removed then assume we need a valid code
        if self._client is None:
            return False
        return self._client.isPanelConnected()

    # The callback handler from the client. All we need to do is schedule an update.
    def onChange(self, event_id: IntEnum, datadictionary: dict):
        """HA Event Callback."""
        self.schedule_update_ha_state(False)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._myname + "_" + str(self._partition_id)

    @property
    def name(self):
        """Return the name of the alarm."""
        return self._myname  # partition 1 but eventually differentiate partitions

    @property
    def changed_by(self):
        """Last change triggered by."""
        return self._last_triggered

    @property
    def device_info(self):
        """Return information about the device."""
        if self._client is not None:
            pm = self._client.getPanelModel()
            if pm is not None:
                if pm.lower() != "unknown":
                    return {
                        "manufacturer": "Visonic",
                        "identifiers": {(DOMAIN, self._myname)},
                        "name": f"Visonic Alarm Panel {self._panel} (Partition {self._partition_id})",
                        "model": pm,
                        # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
                    }
        return {
            "manufacturer": "Visonic",
            "identifiers": {(DOMAIN, self._myname)},
            "name": f"Visonic Alarm Panel {self._panel} (Partition {self._partition_id})",
            "model": None,
            # "model": "Alarm Panel",
            # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
        }

    def update(self):
        """Get the state of the device."""
        self._mystate = STATE_UNKNOWN
        self._device_state_attributes = {}

        if self.isPanelConnected():
            if self._client.isSirenActive():
                self._mystate = STATE_ALARM_TRIGGERED
            else:
                armcode = self._client.getPanelStatus()

                # _LOGGER.debug("alarm armcode is %s", str(armcode))
                if armcode == AlPanelStatus.DISARMED or armcode == AlPanelStatus.SPECIAL or armcode == AlPanelStatus.DOWNLOADING:
                    self._mystate = STATE_ALARM_DISARMED
                elif armcode == AlPanelStatus.ENTRY_DELAY:
                    self._mystate = STATE_ALARM_PENDING
                elif armcode == AlPanelStatus.ARMING_HOME or armcode == AlPanelStatus.ARMING_AWAY:
                    self._mystate = STATE_ALARM_ARMING
                elif armcode == AlPanelStatus.ARMED_HOME:
                    self._mystate = STATE_ALARM_ARMED_HOME
                elif armcode == AlPanelStatus.ARMED_AWAY:
                    self._mystate = STATE_ALARM_ARMED_AWAY

            # Currently may only contain self.hass.data[DOMAIN][DOMAINDATA]["Exception Count"]
            data = self.hass.data[DOMAIN][DOMAINDATA][self._client.getEntryID()]
            #_LOGGER.debug("data {data}")
            stat = self._client.getPanelStatusDict()
            #_LOGGER.debug("stat {stat}")

            if data is not None and stat is not None:
                self._device_state_attributes = {**stat, **data}
            elif stat is not None:
                self._device_state_attributes = stat
            elif data is not None:
                self._device_state_attributes = data
            
            if "count" in self._device_state_attributes and "name" in self._device_state_attributes:
                count = self._device_state_attributes["count"]
                if count > 0:
                    name = self._device_state_attributes["name"]                                    
                    self._last_triggered = name[0]

    @property
    def state(self):
        """Return the state of the device."""
        return self._mystate

    @property
    def extra_state_attributes(self):  #
        """Return the state attributes of the device."""
        attr = self._device_state_attributes
        attr[PANEL_ATTRIBUTE_NAME] = self._panel
        return attr
