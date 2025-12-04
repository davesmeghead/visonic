"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System (Alarm Panel Control)."""

import logging
from enum import IntEnum
#from propcache.api import cached_property
from copy import deepcopy

#import homeassistant.components.alarm_control_panel as alarm
from homeassistant.util import slugify
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import Entity
from homeassistant.components.alarm_control_panel import DOMAIN as ALARM_PANEL_DOMAIN
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity, AlarmControlPanelState
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import config_validation as cv, entity_platform

# Use the HA core attributes, alarm states and services
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    CodeFormat,
)

from . import VisonicConfigEntry
from .client import VisonicClient
from .pyconst import AlPanelCommand, AlPanelStatus, AlPanelMode
from .pyconst import (
    TEXT_PANEL_MODEL,
    TEXT_WATCHDOG_TIMEOUT_TOTAL,
    TEXT_WATCHDOG_TIMEOUT_DAY,
    TEXT_DOWNLOAD_TIMEOUT,
    TEXT_DL_MESSAGE_RETRIES,
    TEXT_PROTOCOL_VERSION,
    TEXT_POWER_MASTER,
)
from .const import (
    DOMAIN,
    VISONIC_TRANSLATION_KEY,
    map_panel_status_to_ha_status,
    MANUFACTURER,
    TEXT_LAST_EVENT_NAME,
    TEXT_DISCONNECTION_COUNT,
    TEXT_CLIENT_VERSION,
    PANEL_ATTRIBUTE_NAME,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: VisonicConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm control panel."""
    #_LOGGER.debug(f"[async_setup_entry] start")

    @callback
    def async_add_alarm(main_one : bool = False) -> None:
        """Add Visonic Alarm Panel."""
        entities: list[Entity] = []
        client: VisonicClient = entry.runtime_data.client

        p = client.getPartitionsInUse()

        if main_one and entry.runtime_data.alarm_entity is None: #  or p is None or (p is not None and len(p) == 1):
            entry.runtime_data.alarm_entity = VisonicAlarm(hass = hass, client = client, partition = None)
            entities.append(entry.runtime_data.alarm_entity)
            _LOGGER.debug(f"[async_setup_entry] adding main entity for panel {client.getPanelID()}")
        elif entry.runtime_data.alarm_entity is not None and p is not None and len(p) > 1:
            _LOGGER.debug(f"[async_setup_entry] updating main alarm control panel entity for partition set {p}")
            entry.runtime_data.alarm_entity.resetPartition(0)
            for i in p:
                if i != 0:
                    entities.append(VisonicAlarm(hass = hass, client = client, partition = i))
            _LOGGER.debug(f"[async_setup_entry] adding alarm control panel entities for partition set {p}")

        if len(entities) > 0:
            async_add_entities(entities, True)

    entry.runtime_data.dispatchers[ALARM_PANEL_DOMAIN] = async_dispatcher_connect(hass, f"{DOMAIN}_{entry.entry_id}_add_{ALARM_PANEL_DOMAIN}", async_add_alarm )
    #_LOGGER.debug("[async_setup_entry] exit")

class VisonicAlarm(AlarmControlPanelEntity):
    """Representation of a Visonic alarm control panel."""

    _attr_translation_key: str = VISONIC_TRANSLATION_KEY

    _unrecorded_attributes = frozenset(
        {TEXT_PANEL_MODEL, TEXT_WATCHDOG_TIMEOUT_TOTAL, TEXT_WATCHDOG_TIMEOUT_DAY, TEXT_DOWNLOAD_TIMEOUT, 
          TEXT_DL_MESSAGE_RETRIES, TEXT_DISCONNECTION_COUNT, TEXT_CLIENT_VERSION,
          TEXT_PROTOCOL_VERSION, TEXT_POWER_MASTER }
    )

    def __init__(self, hass : HomeAssistant, client : VisonicClient, partition : int = None):
        """Initialize a Visonic security alarm."""
        self.hass = hass
        self._client = client
        self._mystate = AlarmControlPanelState.DISARMED
        self._last_triggered = None
        self.resetPartition(partition)
        self._client.onChange(callback = self.onClientChange)

    def resetPartition(self, partition : int | None):
        if partition is None:
            self._partition = None           # When partitions are not used then we only use partition 1 for panel state
            self._partitionSet = {1, 2, 3}   # When partitions are not used then we command (Arm, Disarm etc) all partitions
            self._myname = self._client.getAlarmPanelUniqueIdent()
            _LOGGER.debug(f"[VisonicAlarm] Setting primary alarm control panel {self._myname}    panel {self._client.getPanelID()}")
        elif partition == 0:                 # EXPERIMENTAL
            self._partition = 0              # When partitions are not used then we only use partition 0 for panel state
            self._partitionSet = {1, 2, 3}   # When partitions are not used then we command (Arm, Disarm etc) all partitions
            self._myname = self._client.getAlarmPanelUniqueIdent()
            _LOGGER.debug(f"[VisonicAlarm] Setting alarm control panel {self._myname}    panel {self._client.getPanelID()}")
        else:
            self._partition = partition
            self._partitionSet = { partition }
            self._myname = self._client.getAlarmPanelUniqueIdent() + " Partition " + str(partition)
            _LOGGER.debug(f"[VisonicAlarm] Setting alarm control panel {self._myname}    panel {self._client.getPanelID()}  Partition {self._partition}")
        self._client.setPartitionNaming(partition = partition, panel_entity_name = self._myname)
        self._attr_unique_id = slugify(self._myname)
        self._attr_name = self._myname

    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        _LOGGER.debug(f"[async_will_remove_from_hass] panel {self._client.getPanelID()}, name {self._myname}")
        self._client = None
        await super().async_will_remove_from_hass()

    def isPanelConnected(self) -> bool:
        """Are we connected to the Alarm Panel."""
        # If we are starting up or have been removed then assume we need a valid code
        #_LOGGER.debug(f"alarm control panel isPanelConnected {self.entity_id=}")
        if self._client is None:
            return False
        return self._client.isPanelConnected()

    # The callback handler from the client. All we need to do is schedule an update.
    def onClientChange(self):
        """HA Event Callback."""
        #_LOGGER.debug(f"alarm control panel onChange {self.entity_id=}   {self.available=}")
        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state(True)

    def update(self) -> None:
        """Get the state of the device."""
        #_LOGGER.debug(f"[update]")
        self._mystate = AlarmControlPanelState.DISARMED
        dsa = {}
        av = False
        car = False
        cf = None
        sf = AlarmControlPanelEntityFeature(0)
        di = {
            "manufacturer": "Visonic",
            "identifiers": {(DOMAIN, self._myname)},
            "name": f"{self._myname}",
            "model": None,
            # "model": "Alarm Panel",
            # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
        }
        
        if self._client is not None and self.isPanelConnected():
            isa, dev = self._client.isSirenActive(self._partition)
            #_LOGGER.debug(f"[update] {self._partition=}  {isa=}   {dev=}")
            if isa:
                self._mystate = AlarmControlPanelState.TRIGGERED
            else:
                armcode = self._client.getPanelStatus(self._partition)
                if armcode is not None and armcode in map_panel_status_to_ha_status:
                    self._mystate = map_panel_status_to_ha_status[armcode]

            stat = self._client.getPanelStatusDict(self._partition)
            #_LOGGER.debug(f"[update] stat {stat}")

            data = None
            if self._partition is None or self._partition == 0:
                data = self._client.getClientStatusDict()
                if TEXT_LAST_EVENT_NAME in stat and len(stat[TEXT_LAST_EVENT_NAME]) > 2:
                    self._last_triggered = stat[TEXT_LAST_EVENT_NAME]
                    #_LOGGER.debug(f"[update] {self._last_triggered=}")

            #_LOGGER.debug(f"[update] {self._mystate=}")
                
            if data is not None and stat is not None:
                dsa = {**stat, **data}
            elif stat is not None:
                dsa = {**stat}
            elif data is not None:
                dsa = {**data}

            dsa[PANEL_ATTRIBUTE_NAME] = self._client.getPanelID() if self.isPanelConnected() else "Unknown"

            av = self._client.isPanelConnected()

            pm = self._client.getPanelModel()
            if pm is not None:
                if pm.lower() != "unknown":
                    di = {
                        "manufacturer": MANUFACTURER,
                        "identifiers": {(DOMAIN, self._myname)},
                        "name": f"{self._myname}",
                        "model": pm,
                        # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
                    }

            car = False
            cf = None
            
            if not self._client.isDisableAllCommands():

                isValidPL, code, showKeypad, car = self._client.pmGetPin(code = self._alarm_control_panel_option_default_code, partition = self._partition)
                #_LOGGER.debug(f"[update] getpin {self._alarm_control_panel_option_default_code=} {isValidPL=} {code=} {showKeypad=} {car=}")

                cf = CodeFormat.NUMBER if showKeypad else None
                sf = AlarmControlPanelEntityFeature.ARM_AWAY
                if self._client.isArmNight():
                    #_LOGGER.debug("[update]  Adding Night")
                    sf = sf | AlarmControlPanelEntityFeature.ARM_NIGHT
                if self._client.isArmHome():
                    #_LOGGER.debug("[update]  Adding Home")
                    sf = sf | AlarmControlPanelEntityFeature.ARM_HOME
                if self._client.isPowerMaster():
                    #_LOGGER.debug("[update]  Adding Trigger")
                    sf = sf | AlarmControlPanelEntityFeature.TRIGGER
                #_LOGGER.debug(f"[update] alarm control panel supported_features {sf=}")
            
        if not hasattr(self, "_attr_extra_state_attributes"):
            self._attr_extra_state_attributes = deepcopy(dsa)
        elif self._attr_extra_state_attributes != dsa:
            _LOGGER.debug(f"[update] changing extra state attributes to {dsa}")
            self._attr_extra_state_attributes = deepcopy(dsa)
        
        if self._attr_available != av:
            _LOGGER.debug(f"[update] changing available to {av}")
            self._attr_available = av                        # writing to this calls the "setter" to refresh the cached property

        if self._attr_supported_features != sf:
            _LOGGER.debug(f"[update] changing supported features to {sf}")
            self._attr_supported_features = sf
        
        if self._attr_device_info != di:
            _LOGGER.debug(f"[update] changing device info to {di}")
            self._attr_device_info = di                     # writing to this calls the "setter" to refresh the cached property
            
        if self._attr_code_format != cf:
            _LOGGER.debug(f"[update] changing code format to {cf}")
            self._attr_code_format = cf                     # writing to this calls the "setter" to refresh the cached property
            
        if self._attr_code_arm_required != car:
            _LOGGER.debug(f"[update] changing code arm required to {car}")
            self._attr_code_arm_required = car               # writing to this calls the "setter" to refresh the cached property
            
        if self._last_triggered is not None and self._attr_changed_by != self._last_triggered:
            _LOGGER.debug(f"[update] changing last triggered to {self._last_triggered}")
            self._attr_changed_by = self._last_triggered     # writing to this calls the "setter" to refresh the cached property

        if self._attr_alarm_state != self._mystate:
            _LOGGER.debug(f"[update] changing alarm state to {self._mystate}")
            self._attr_alarm_state = self._mystate           # writing to this calls the "setter" to refresh the cached property


    def alarm_disarm(self, code=None):
        """Send disarm command."""
        #_LOGGER.debug(f"alarm control panel alarm_disarm {self.entity_id=}")
        if not self.isPanelConnected():
            raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="no_panel_connection",
                    translation_placeholders={
                        "myname": self._myname
                    }
                )
        if self._client is not None:
            self._client.sendCommand("Disarm", AlPanelCommand.DISARM, code, self._partitionSet)

    def alarm_arm_night(self, code=None):
        """Send arm night command (Same as arm home)."""
        #_LOGGER.debug(f"alarm control panel alarm_arm_night {self.entity_id=}")
        if not self.isPanelConnected():
            raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="no_panel_connection",
                    translation_placeholders={
                        "myname": self._myname
                    }
                )
        if self._client is not None and self._client.isArmNight():
            self._client.sendCommand("Arm Night", AlPanelCommand.ARM_HOME_INSTANT, code, self._partitionSet)

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        #_LOGGER.debug(f"alarm control panel alarm_arm_home {self.entity_id=}")
        if not self.isPanelConnected():
            raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="no_panel_connection",
                    translation_placeholders={
                        "myname": self._myname
                    }
                )
        if self._client is not None and self._client.isArmHome():
            command = AlPanelCommand.ARM_HOME_INSTANT if self._client.isArmHomeInstant() else AlPanelCommand.ARM_HOME
            self._client.sendCommand("Arm Home", command, code, self._partitionSet)

    def alarm_arm_away(self, code=None):
        """Send arm away command."""
        #_LOGGER.debug(f"alarm control panel alarm_arm_away {self.entity_id=}")
        if not self.isPanelConnected():
            raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="no_panel_connection",
                    translation_placeholders={
                        "myname": self._myname
                    }
                )
        if self._client is not None:
            command = AlPanelCommand.ARM_AWAY_INSTANT if self._client.isArmAwayInstant() else AlPanelCommand.ARM_AWAY
            self._client.sendCommand("Arm Away", command, code, self._partitionSet)

    def alarm_trigger(self, code=None):
        """Send alarm trigger command."""
        #_LOGGER.debug(f"alarm control panel alarm_trigger {self.entity_id=}")
        if not self.isPanelConnected():
            raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="no_panel_connection",
                    translation_placeholders={
                        "myname": self._myname
                    }
                )
        if self._client is not None and self._client.isPowerMaster():
            self._client.sendCommand("Trigger Siren", AlPanelCommand.TRIGGER , code, None)

    def alarm_arm_custom_bypass(self, data=None):
        """Bypass Panel."""
        _LOGGER.warning("Alarm Panel Custom Bypass Not Yet Implemented")
