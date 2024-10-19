"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System (Alarm Panel Control)."""

import logging
from enum import IntEnum

import homeassistant.components.alarm_control_panel as alarm
from homeassistant.util import slugify
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.alarm_control_panel import DOMAIN as ALARM_PANEL_DOMAIN
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import config_validation as cv, entity_platform

# Use the HA core attributes, alarm states and services
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    CodeFormat,
)
from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMED_HOME,
    STATE_ALARM_ARMING,
    STATE_ALARM_DISARMED,
    STATE_ALARM_PENDING,
    STATE_ALARM_TRIGGERED,
    STATE_UNKNOWN,
)

from . import VisonicConfigEntry
from .client import VisonicClient
from .pyconst import AlPanelCommand, AlPanelStatus
from .const import (
    DOMAIN,
    map_panel_status_to_ha_status,
    MANUFACTURER,
    PANEL_ATTRIBUTE_NAME,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: VisonicConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm control panel."""
    #_LOGGER.debug(f"alarm control panel async_setup_entry start")
    client: VisonicClient = entry.runtime_data.client

    @callback
    def async_add_alarm() -> None:
        """Add Visonic Alarm Panel."""
        entities: list[SwitchEntity] = []

        p = client.getPartitionsInUse()

        if p is None or (p is not None and len(p) == 1):
            entities.append(VisonicAlarm(hass, client))
        elif len(p) > 1:
            for i in p:
                if i != 0:
                    entities.append(VisonicAlarm(hass, client, i))

        _LOGGER.debug(f"alarm control panel adding entity for partition {p}")
        async_add_entities(entities, True)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_{entry.entry_id}_add_{ALARM_PANEL_DOMAIN}",
            async_add_alarm,
        )
    )
    #_LOGGER.debug("alarm control panel async_setup_entry exit")

class VisonicAlarm(alarm.AlarmControlPanelEntity):
    """Representation of a Visonic alarm control panel."""

#    _unrecorded_attributes = alarm.AlarmControlPanelEntity._unrecorded_attributes | frozenset({-})
    _attr_translation_key: str = "alarm_panel_key"
    #_attr_has_entity_name = True
    _entity_component_unrecorded_attributes = frozenset(
        {"Panel Model", "Watchdog Timeout (Total)", "Watchdog Timeout (Past 24 Hours)", "Download Timeout", 
          "Download Message Retries", "Panel Problem Count", "Last Panel Problem Time", "Client Version",
          "Exception Count", "Protocol Version", "Power Master" }
    )

    def __init__(self, hass : HomeAssistant, client : VisonicClient, partition : int = None):
        """Initialize a Visonic security alarm."""
        self.hass = hass
        self._client = client

        self._mystate = STATE_UNKNOWN
        self._device_state_attributes = {}
        self._users = {}
        self._doneUsers = False
        self._last_triggered = ""
        self._panel = client.getPanelID()

        if partition is None:
            self._partition = None           # When partitions are not used then we only use partition 1 for panel state
            self._partitionSet = {1, 2, 3}   # When partitions are not used then we command (Arm, Disarm etc) all partitions
            self._myname = client.getAlarmPanelUniqueIdent()
            _LOGGER.debug(f"Initialising alarm control panel {self._myname}    panel {self._panel}")
        else:
            self._partition = partition
            self._partitionSet = { partition }
            self._myname = client.getAlarmPanelUniqueIdent() + " Partition " + str(partition)
            _LOGGER.debug(f"Initialising alarm control panel {self._myname}    panel {self._panel}  Partition {self._partition}")

        client.onChange(callback = self.onClientChange, partition = partition, panel_entity_name = self._myname)

    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        self._client = None
        _LOGGER.debug(f"Removing alarm control panel {self._myname} panel {self._panel}")

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

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        #_LOGGER.debug(f"alarm control panel available {self.entity_id=}")
        if self._client is None:
            return False
        return self._client.isPanelConnected()

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        #_LOGGER.debug(f"alarm control panel unique_id {self.entity_id=}")
        return slugify(self._myname)

    @property
    def name(self):
        """Return the name of the alarm."""
        #_LOGGER.debug(f"alarm control panel name {self.entity_id=}")
        return self._myname

    @property
    def changed_by(self):
        """Last change triggered by."""
        #_LOGGER.debug(f"alarm control panel changed_by {self.entity_id=}")
        return self._last_triggered

    @property
    def device_info(self):
        """Return information about the device."""
        #_LOGGER.debug(f"alarm control panel device_info {self.entity_id=}")
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
            "manufacturer": "Visonic",
            "identifiers": {(DOMAIN, self._myname)},
            "name": f"{self._myname}",
            "model": None,
            # "model": "Alarm Panel",
            # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
        }

    @property
    def code_arm_required(self):
        """Whether the code is required for arm actions."""
        #_LOGGER.debug(f"alarm control panel code_arm_required {self.entity_id=}")
        if self._client is not None:
            return not self._client.isArmWithoutCode()
        return True

    def update(self):
        """Get the state of the device."""
        #_LOGGER.debug(f"alarm control panel update {self.entity_id=}")
        self._mystate = STATE_UNKNOWN
        self._device_state_attributes = {}

        if self._client is not None and self.isPanelConnected():
            isa, _ = self._client.isSirenActive()
            if isa:
                self._mystate = STATE_ALARM_TRIGGERED
            else:
                armcode = self._client.getPanelStatus(self._partition)
                if armcode is not None and armcode in map_panel_status_to_ha_status:
                    self._mystate = map_panel_status_to_ha_status[armcode]

            stat = self._client.getPanelStatusDict(self._partition)
            #_LOGGER.debug(f"stat {stat}")

            data = None
            if self._partition is None:
                data = self._client.getClientStatusDict()
                if "lasteventname" in stat and len(stat["lasteventname"]) > 2:
                    self._last_triggered = stat["lasteventname"]
            elif self._partition == 1:
                data = self._client.getClientStatusDict()

            #_LOGGER.debug(f"data {data}")
                
            if data is not None and stat is not None:
                self._device_state_attributes = {**stat, **data}
            elif stat is not None:
                self._device_state_attributes = {**stat}
            elif data is not None:
                self._device_state_attributes = {**data}

    @property
    def state(self):
        """Return the state of the device."""
        #_LOGGER.debug(f"alarm control panel state {self.entity_id=}")
        return self._mystate

    @property
    def extra_state_attributes(self):  #
        """Return the state attributes of the device."""
        #_LOGGER.debug(f"alarm control panel extra_state_attributes {self.entity_id=}")
        attr = self._device_state_attributes
        attr[PANEL_ATTRIBUTE_NAME] = self._panel
        return attr

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        #_LOGGER.debug(f"alarm control panel supported_features {self.entity_id=}")
        if self._client is None:
            return 0
        if self._client.isDisableAllCommands():
            return 0
        #_LOGGER.debug(f"[AlarmcontrolPanel] Getting Supported Features {self._client.isArmHome()} {self._client.isArmNight()}")
        retval = AlarmControlPanelEntityFeature.ARM_AWAY
        if self._client.isArmNight():
            #_LOGGER.debug("[AlarmcontrolPanel] Adding Night")
            retval = retval | AlarmControlPanelEntityFeature.ARM_NIGHT
        if self._client.isArmHome():
            #_LOGGER.debug("[AlarmcontrolPanel] Adding Home")
            retval = retval | AlarmControlPanelEntityFeature.ARM_HOME
        if self._client.isPowerMaster():
            #_LOGGER.debug("[AlarmcontrolPanel] Adding Trigger")
            retval = retval | AlarmControlPanelEntityFeature.TRIGGER
        return retval

    # DO NOT OVERRIDE state_attributes AS IT IS USED IN THE LOVELACE FRONTEND TO DETERMINE code_format
    
    @property
    def code_format(self):
        """Regex for code format or None if no code is required."""
        #_LOGGER.debug(f"alarm control panel code_format {self.entity_id=}")
        # Do not show the code panel if the integration is just starting up and 
        #    connecting to the panel
        if self._client is None:
            return None
        if self._client.isDisableAllCommands():
            return None
        if self.isPanelConnected():
            return CodeFormat.NUMBER if self._client.isCodeRequired() else None
        return None    

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
        _LOGGER.debug("Alarm Panel Custom Bypass Not Yet Implemented")
