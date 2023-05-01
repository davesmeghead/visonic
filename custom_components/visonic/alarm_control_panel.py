"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System (Alarm Panel Control)."""

from datetime import timedelta
import logging
import re 
import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
from .pconst import PyPanelCommand, PyPanelStatus
import homeassistant.components.alarm_control_panel as alarm
from homeassistant.components.alarm_control_panel.const import (
    #SUPPORT_ALARM_ARM_AWAY,
    #SUPPORT_ALARM_ARM_HOME,
    #SUPPORT_ALARM_ARM_NIGHT,
    AlarmControlPanelEntityFeature,
    CodeFormat,
)
from homeassistant.config_entries import ConfigEntry

# Use the HA core attributes, alarm states and services
from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMED_HOME,
    STATE_ALARM_ARMING,
    STATE_ALARM_DISARMED,
    STATE_ALARM_PENDING,
    STATE_ALARM_TRIGGERED,
    STATE_UNKNOWN,
)

from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import entity_platform, service
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import VisonicClient
from .const import (
    DOMAIN,
    DOMAINCLIENT,
    DOMAINDATA,
    NOTIFICATION_ID,
    NOTIFICATION_TITLE,
    PANEL_ATTRIBUTE_NAME,
    AvailableNotifications,
    PIN_REGEX,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the alarm control panel."""
    # _LOGGER.debug("alarm control panel async_setup_entry called")
    if DOMAIN in hass.data:
        # Get the client
        client = hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id]
        # Create the alarm controlpanel
        va = VisonicAlarm(client, 1)
        # Add it to HA
        devices = [va]
        async_add_entities(devices, True)

    platform = entity_platform.async_get_current_platform()
    _LOGGER.debug("alarm control panel async_setup_entry called {0}".format(platform))


class VisonicAlarm(alarm.AlarmControlPanelEntity):
    """Representation of a Visonic alarm control panel."""

    def __init__(self, client: VisonicClient, partition_id: int):
        """Initialize a Visonic security alarm."""
        self._client = client
        self._partition_id = partition_id
        self._mystate = STATE_UNKNOWN
        self._myname = client.getAlarmPanelUniqueIdent()
        _LOGGER.debug("Initialising alarm control panel {0}".format(self._myname))
        self._device_state_attributes = {}
        self._users = {}
        self._doneUsers = False
        self._last_triggered = ""
        self._dispatcher = client.getDispatcher()
        self._panel = client.getPanelID()

    async def async_added_to_hass(self):
        """Register callbacks."""
        # Register for dispatcher calls to update the state
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._dispatcher, self.onChange
            )
        )

    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        self._client = None
        _LOGGER.debug("alarm control panel async_will_remove_from_hass")

    def isPanelConnected(self) -> bool:
        """Are we connected to the Alarm Panel."""
        # If we are starting up then assume we need a valid code
        if self._client is None:
            return False
        # Are we just starting up
        return self._client.isPanelConnected()

    def onChange(self, event_id: int, datadictionary: dict):
        """HA Event Callback."""
        self.schedule_update_ha_state(True)

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
            ps = self._client.getPanelStatus(True)
            if "Panel Model" in ps:
                pm = ps["Panel Model"]
                #_LOGGER.debug("Model is = {0}  type {1}".format(pm, type(pm)))
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
        if self._client is not None:
            return not self._client.isArmWithoutCode()
        return True

    def update(self):
        """Get the state of the device."""
        self._mystate = STATE_UNKNOWN
        self._device_state_attributes = {}

        if self.isPanelConnected():
            if self._client.isSirenActive():
                self._mystate = STATE_ALARM_TRIGGERED
            else:
                armcode = self._client.getPanelStatusCode()

                # _LOGGER.debug("alarm armcode is %s", str(armcode))
                if armcode == PyPanelStatus.DISARMED or armcode == PyPanelStatus.SPECIAL or armcode == PyPanelStatus.DOWNLOADING:
                    self._mystate = STATE_ALARM_DISARMED
                elif armcode == PyPanelStatus.ENTRY_DELAY:
                    self._mystate = STATE_ALARM_PENDING
                elif armcode == PyPanelStatus.ARMING_HOME or armcode == PyPanelStatus.ARMING_AWAY:
                    self._mystate = STATE_ALARM_ARMING
                elif armcode == PyPanelStatus.ARMED_HOME:
                    self._mystate = STATE_ALARM_ARMED_HOME
                elif armcode == PyPanelStatus.ARMED_AWAY:
                    self._mystate = STATE_ALARM_ARMED_AWAY

            # Currently may only contain self.hass.data[DOMAIN][DOMAINDATA]["Exception Count"]
            data = self.hass.data[DOMAIN][DOMAINDATA][self._client.getEntryID()]
            stat = self._client.getPanelStatus(True)

            if data is not None and stat is not None:
                self._device_state_attributes = {**stat, **data}
            elif stat is not None:
                self._device_state_attributes = stat
            elif data is not None:
                self._device_state_attributes = data
            
            if "Panel Last Event" in self._device_state_attributes and self._device_state_attributes["Panel Last Event"] is not None:
                s = self._device_state_attributes["Panel Last Event"]
                pos = s.find("/")
                self._last_triggered = (s[pos+1:]).strip()

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

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        #_LOGGER.debug("[AlarmcontrolPanel] Getting Supported Features {0} {1}".format(self._client.isArmHome(), self._client.isArmNight()))
        retval = AlarmControlPanelEntityFeature.ARM_AWAY
        if self._client.isArmNight():
            #_LOGGER.debug("[AlarmcontrolPanel] Adding Night")
            retval = retval | AlarmControlPanelEntityFeature.ARM_NIGHT
        if self._client.isArmHome():
            #_LOGGER.debug("[AlarmcontrolPanel] Adding Home")
            retval = retval | AlarmControlPanelEntityFeature.ARM_HOME
        return retval

    # DO NOT OVERRIDE state_attributes AS IT IS USED IN THE LOVELACE FRONTEND TO DETERMINE code_format
    
    @property
    def code_format(self):
        """Regex for code format or None if no code is required."""
        # Do not show the code panel if the integration is just starting up and 
        #    connecting to the panel
        if self.isPanelConnected():
            return CodeFormat.NUMBER if self._client.isCodeRequired() else None
        return None    

    def alarm_disarm(self, code=None):
        """Send disarm command."""
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration {self._myname} not connected to panel.")
        self._client.sendCommand("Disarm", PyPanelCommand.DISARM, code)

    def alarm_arm_night(self, code=None):
        """Send arm night command (Same as arm home)."""
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration {self._myname} not connected to panel.")
        if self._client.isArmNight():
            self._client.sendCommand("Arm Night", PyPanelCommand.ARM_HOME_INSTANT, code)

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration {self._myname} not connected to panel.")
        if self._client.isArmHome():
            command = PyPanelCommand.ARM_HOME_INSTANT if self._client.isArmHomeInstant() else PyPanelCommand.ARM_HOME
            self._client.sendCommand("Arm Home", command, code)

    def alarm_arm_away(self, code=None):
        """Send arm away command."""
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration {self._myname} not connected to panel.")
        command = PyPanelCommand.ARM_AWAY_INSTANT if self._client.isArmAwayInstant() else PyPanelCommand.ARM_AWAY
        self._client.sendCommand("Arm Away", command, code)

    def alarm_trigger(self, code=None):
        """Send alarm trigger command."""
        raise NotImplementedError()


    def alarm_arm_custom_bypass(self, data=None):
        """Bypass Panel."""
        _LOGGER.debug("Alarm Panel Custom Bypass Not Yet Implemented")
