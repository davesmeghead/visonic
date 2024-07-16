"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System and Create a Simple Entity to Report Status only."""

import logging
from enum import IntEnum
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import Entity
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.helpers.dispatcher import async_dispatcher_connect

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
from . import VisonicConfigEntry
from .const import (
    DOMAIN,
    map_panel_status_to_ha_status,
    PANEL_ATTRIBUTE_NAME,
)

from .pyconst import AlPanelStatus

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: VisonicConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Visonic Alarm Sensors for Monitor."""
    #_LOGGER.debug(f"sensor async_setup_entry start")
    client: VisonicClient = entry.runtime_data.client

    @callback
    def async_add_sensor() -> None:
        """Add Visonic Sensor (to behave instead of the alarm panel when all comms is prevented)."""
        entities: list[Entity] = []
        entities.append(VisonicSensor(hass, client, 1))
        #_LOGGER.debug(f"sensor adding entity")
        async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_{entry.entry_id}_add_{SENSOR_DOMAIN}",
            async_add_sensor,
        )
    )
    #_LOGGER.debug("sensor async_setup_entry exit")

class VisonicSensor(Entity):
    """Representation of a Visonic alarm control panel as a simple sensor for minimal."""

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
        if self.entity_id is not None:
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
                if armcode is not None and armcode in map_panel_status_to_ha_status:
                    self._mystate = map_panel_status_to_ha_status[armcode]

                # _LOGGER.debug("alarm armcode is %s", str(armcode))

            # Currently may only contain Exception Count"
            data = self._client.getClientStatusDict()
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
