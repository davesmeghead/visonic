"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System (Alarm Panel Control)."""

import logging
from enum import IntEnum

import homeassistant.components.alarm_control_panel as alarm
from homeassistant.util import slugify
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.alarm_control_panel import DOMAIN as ALARM_PANEL_DOMAIN
from homeassistant.helpers.dispatcher import async_dispatcher_connect

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
        entities.append(VisonicAlarm(hass, client, 1))
        _LOGGER.debug(f"alarm control panel adding entity")
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

    def __init__(self, hass: HomeAssistant, client: VisonicClient, partition_id: int):
        """Initialize a Visonic security alarm."""
        self.hass = hass
        self._client = client
        client.onChange(self.onChange)
        self._partition_id = partition_id
        self._mystate = STATE_UNKNOWN
        self._myname = client.getAlarmPanelUniqueIdent()
        self._device_state_attributes = {}
        self._users = {}
        self._doneUsers = False
        self._last_triggered = ""
        self._panel = client.getPanelID()
        _LOGGER.debug(f"Initialising alarm control panel {self._myname} panel {self._panel}")

    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        #await super().async_will_remove_from_hass()
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
    def onChange(self, event_id: IntEnum, datadictionary: dict):
        """HA Event Callback."""
        #_LOGGER.debug(f"alarm control panel onChange {self.entity_id=}   {self.available=}")
        if self.entity_id is not None:
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
        return slugify(self._myname) + "_partition_" + str(self._partition_id)

    @property
    def name(self):
        """Return the name of the alarm."""
        #_LOGGER.debug(f"alarm control panel name {self.entity_id=}")
        return self._myname  # partition 1 but eventually differentiate partitions

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

    @property
    def code_arm_required(self):
        """Whether the code is required for arm actions."""
        #_LOGGER.debug(f"alarm control panel code_arm_required {self.entity_id=}")
        if self._client is not None:
            return not self._client.isArmWithoutCode()
        return True

    def update(self):
        """Get the state of the device."""
        #_LOGGER.debug(f"alarm control update available {self.entity_id=}")
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

            # Currently may only contain "Exception Count"
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
        if self._client.isDisableAllCommands():
            return None
        if self.isPanelConnected():
            return CodeFormat.NUMBER if self._client.isCodeRequired() else None
        return None    

    def alarm_disarm(self, code=None):
        """Send disarm command."""
        #_LOGGER.debug(f"alarm control panel alarm_disarm {self.entity_id=}")
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration {self._myname} not connected to panel.")
        self._client.sendCommand("Disarm", AlPanelCommand.DISARM, code)

    def alarm_arm_night(self, code=None):
        """Send arm night command (Same as arm home)."""
        #_LOGGER.debug(f"alarm control panel alarm_arm_night {self.entity_id=}")
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration {self._myname} not connected to panel.")
        if self._client.isArmNight():
            self._client.sendCommand("Arm Night", AlPanelCommand.ARM_HOME_INSTANT, code)

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        #_LOGGER.debug(f"alarm control panel alarm_arm_home {self.entity_id=}")
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration {self._myname} not connected to panel.")
        if self._client.isArmHome():
            command = AlPanelCommand.ARM_HOME_INSTANT if self._client.isArmHomeInstant() else AlPanelCommand.ARM_HOME
            self._client.sendCommand("Arm Home", command, code)

    def alarm_arm_away(self, code=None):
        """Send arm away command."""
        #_LOGGER.debug(f"alarm control panel alarm_arm_away {self.entity_id=}")
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration {self._myname} not connected to panel.")
        command = AlPanelCommand.ARM_AWAY_INSTANT if self._client.isArmAwayInstant() else AlPanelCommand.ARM_AWAY
        self._client.sendCommand("Arm Away", command, code)

    def alarm_trigger(self, code=None):
        """Send alarm trigger command."""
        #_LOGGER.debug(f"alarm control panel alarm_trigger {self.entity_id=}")
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration {self._myname} not connected to panel.")
        if self._client.isPowerMaster():
            self._client.sendCommand("Trigger Siren", AlPanelCommand.TRIGGER , code)
            #self._client.sendCommand("Arm Away", command, code)

    def alarm_arm_custom_bypass(self, data=None):
        """Bypass Panel."""
        _LOGGER.debug("Alarm Panel Custom Bypass Not Yet Implemented")
