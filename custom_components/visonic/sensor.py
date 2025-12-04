"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System and Create a Simple Entity to Report Status only."""

import logging
from enum import IntEnum
from homeassistant.util import slugify
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import Entity
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.components.alarm_control_panel import AlarmControlPanelState

# Use the standard HA core attributes, alarm states and services to report status
from homeassistant.const import (
#    STATE_ALARM_ARMED_AWAY,
#    STATE_ALARM_ARMED_HOME,
#    STATE_ALARM_ARMING,
#    STATE_ALARM_DISARMED,
#    STATE_ALARM_PENDING,
#    STATE_ALARM_TRIGGERED,
    STATE_UNKNOWN,
)

from .client import VisonicClient
from . import VisonicConfigEntry
from .const import (
    DOMAIN,
    VISONIC_TRANSLATION_KEY,
    map_panel_status_to_ha_status,
    MANUFACTURER,
    TEXT_LAST_EVENT_NAME,
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
    #_LOGGER.debug(f"[async_setup_entry] start")

    @callback
    def async_add_sensor(main_one : bool = False) -> None:
        """Add Visonic Sensor (to behave instead of the alarm panel when all comms is prevented)."""
        entities: list[Entity] = []
        client: VisonicClient = entry.runtime_data.client

        p = client.getPartitionsInUse()

        if main_one and entry.runtime_data.alarm_entity is None: #  or p is None or (p is not None and len(p) == 1):
            entry.runtime_data.alarm_entity = VisonicSensor(hass, client)
            entities.append(entry.runtime_data.alarm_entity)
            _LOGGER.debug(f"[async_setup_entry] adding main entity for panel {client.getPanelID()}")
        elif entry.runtime_data.alarm_entity is not None and p is not None and len(p) > 1:
            _LOGGER.debug(f"[async_setup_entry] updating main alarm control panel entity for partition set {p}")
            entry.runtime_data.alarm_entity.resetPartition(0)
            for i in p:
                if i != 0:
                    entities.append(VisonicSensor(hass, client, i))
            _LOGGER.debug(f"[async_setup_entry] adding sensor panel entities for partition set {p}")

        if len(entities) > 0:
            async_add_entities(entities, True)

    entry.runtime_data.dispatchers[SENSOR_DOMAIN] = async_dispatcher_connect(hass, f"{DOMAIN}_{entry.entry_id}_add_{SENSOR_DOMAIN}", async_add_sensor)
    #_LOGGER.debug("[async_setup_entry] exit")

class VisonicSensor(Entity):
    """Representation of a Visonic alarm control panel as a simple sensor for minimal."""

    _attr_translation_key: str = VISONIC_TRANSLATION_KEY
    #_attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, client: VisonicClient, partition : int = None):
        """Initialize a Visonic security alarm."""
        self._client = client
        self.hass = hass
        self._attr_state = STATE_UNKNOWN
        self._last_triggered = ""
        self.resetPartition(partition)
        self._client.onChange(callback = self.onClientChange)
        #_LOGGER.debug(f"[VisonicSensor] Initialising alarm sensor {self._myname}")

    def resetPartition(self, partition : int | None):
        if partition is None:
            self._partition = None           # When partitions are not used then we only use partition 1 for panel state
            self._partitionSet = {1, 2, 3}   # When partitions are not used then we command (Arm, Disarm etc) all partitions
            self._myname = self._client.getAlarmPanelUniqueIdent()
            _LOGGER.debug(f"[VisonicAlarm] Setting primary sensor {self._myname}      {self.unique_id=}")
        elif partition == 0:                 # EXPERIMENTAL
            self._partition = 0              # When partitions are not used then we only use partition 0 for panel state
            self._partitionSet = {1, 2, 3}   # When partitions are not used then we command (Arm, Disarm etc) all partitions
            self._myname = self._client.getAlarmPanelUniqueIdent()
            _LOGGER.debug(f"[VisonicAlarm] Setting sensor {self._myname}      {self.unique_id=}")
        else:
            self._partition = partition
            self._partitionSet = { partition }
            self._myname = self._client.getAlarmPanelUniqueIdent() + " Partition " + str(partition)
            _LOGGER.debug(f"[VisonicAlarm] Setting alarm sensor {self._myname}      {self.unique_id=}")
        self._client.setPartitionNaming(partition = partition, panel_entity_name = self._myname)

    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        _LOGGER.debug(f"[async_will_remove_from_hass] Removing alarm panel sensor {self._myname} panel {self._client.getPanelID()}")
        self._client = None
        await super().async_will_remove_from_hass()

    def isPanelConnected(self) -> bool:
        """Are we connected to the Alarm Panel."""
        # If we are starting up or have been removed then assume we need a valid code
        if self._client is None:
            return False
        return self._client.isPanelConnected()

    # The callback handler from the client. All we need to do is schedule an update.
    def onClientChange(self):
        """HA Event Callback."""
        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state(True)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return slugify(self._myname)

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
                        "manufacturer": MANUFACTURER,
                        "identifiers": {(DOMAIN, self._myname)},
                        "name": f"{self._myname}",
                        "model": pm,
                        # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
                    }
        return {
            "manufacturer": MANUFACTURER,
            "identifiers": {(DOMAIN, self._myname)},
            "name": f"{self._myname}",
            "model": None,
            # "model": "Alarm Panel",
            # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
        }

    def update(self):
        """Get the state of the device."""
        #_LOGGER.debug(f"[update] before {self._attr_state=}")
        self._attr_state = STATE_UNKNOWN
        self._attr_extra_state_attributes = {}

        if self._client is not None and self.isPanelConnected():
            ptu = self._client.getPartitionsInUse()
            isa, _ = self._client.isSirenActive(None if ptu is None else 0)
            if isa:
                self._attr_state = AlarmControlPanelState.TRIGGERED
            else:
                armcode = self._client.getPanelStatus(self._partition)
                if armcode is not None and armcode in map_panel_status_to_ha_status:
                    self._attr_state = map_panel_status_to_ha_status[armcode]

            stat = self._client.getPanelStatusDict(self._partition)
            #_LOGGER.debug(f"[update] stat {stat}")

            data = None
            if self._partition is None or self._partition == 0:
                data = self._client.getClientStatusDict()
                if TEXT_LAST_EVENT_NAME in stat and len(stat[TEXT_LAST_EVENT_NAME]) > 2:
                    self._last_triggered = stat[TEXT_LAST_EVENT_NAME]

            if data is not None and stat is not None:
                self._attr_extra_state_attributes = {**stat, **data}
            elif stat is not None:
                self._attr_extra_state_attributes = stat
            elif data is not None:
                self._attr_extra_state_attributes = data
            
            self._attr_extra_state_attributes[PANEL_ATTRIBUTE_NAME] = self._client.getPanelID()
            #_LOGGER.debug(f"[update] _attr_extra_state_attributes {self._attr_extra_state_attributes=}")

        #_LOGGER.debug(f"[update] after {self._attr_state=}")
