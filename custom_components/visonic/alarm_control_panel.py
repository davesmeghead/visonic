"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System (Alarm Panel Control)."""

from datetime import timedelta
import logging
import re 
import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
from .pconst import PyPanelCommand, PyPanelStatus
import homeassistant.components.alarm_control_panel as alarm
from homeassistant.components.alarm_control_panel.const import (
    SUPPORT_ALARM_ARM_AWAY,
    SUPPORT_ALARM_ARM_HOME,
    SUPPORT_ALARM_ARM_NIGHT,
)
from homeassistant.config_entries import ConfigEntry

# Use the HA core attributes, alarm states and services
from homeassistant.const import (
    ATTR_CODE,
    ATTR_ENTITY_ID,
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

from .client import VisonicClient
from .const import (
    ATTR_BYPASS,
    DOMAIN,
    DOMAINCLIENT,
    DOMAINDATA,
    VISONIC_UNIQUE_NAME,
    VISONIC_UPDATE_STATE_DISPATCHER,
    NOTIFICATION_ID,
    NOTIFICATION_TITLE,
    AvailableNotifications,
    PIN_REGEX,
)

# Schema for the 'alarm_sensor_bypass' HA service
ALARM_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_BYPASS, default=False): cv.boolean,
        vol.Optional(ATTR_CODE, default=""): cv.string,
    }
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the alarm control panel."""
    # _LOGGER.debug("alarm control panel async_setup_entry called")
    if DOMAIN in hass.data:
        # Get the client
        client = hass.data[DOMAIN][entry.entry_id][DOMAINCLIENT]
        # Create the alarm controlpanel
        va = VisonicAlarm(client, 1)
        # Add it to HA
        devices = [va]
        async_add_entities(devices, True)


class VisonicAlarm(alarm.AlarmControlPanelEntity):
    """Representation of a Visonic alarm control panel."""

    def __init__(self, client: VisonicClient, partition_id: int):
        """Initialize a Visonic security alarm."""
        self._client = client
        self._partition_id = partition_id
        self._mystate = STATE_UNKNOWN
        self._myname = VISONIC_UNIQUE_NAME
        self._device_state_attributes = {}
        self._users = {}
        self._doneUsers = False
        self._last_triggered = ""

    async def async_added_to_hass(self):
        """Register callbacks."""
        # Register for dispatcher calls to update the state
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, VISONIC_UPDATE_STATE_DISPATCHER, self.onChange
            )
        )
        # Register HA Service to bypass individual sensors
        self.hass.services.async_register(
            DOMAIN,
            "alarm_sensor_bypass",
            self.service_sensor_bypass,
            schema=ALARM_SERVICE_SCHEMA,
        )

    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        self.hass.services.async_remove(
            DOMAIN,
            "alarm_sensor_bypass",
        )
        self._client = None
        _LOGGER.debug("alarm control panel async_will_remove_from_hass")

    #def createWarningMessage(self, message: str):
    #    """Create a Warning message in the log file and a notification on the HA Frontend."""
    #    _LOGGER.warning(message)
    #    self.hass.components.persistent_notification.create(
    #        message, title=NOTIFICATION_TITLE, notification_id=NOTIFICATION_ID
    #    )

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
    def changed_by(self):
        """Last change triggered by."""
        return self._last_triggered

    @property
    def device_info(self):
        """Return information about the device."""
        if self._client is not None:
            ps = self._client.getPanelStatus()
            if "Panel Model" in ps:
                pm = ps["Panel Model"]
                _LOGGER.debug("Model is = {0}  type {1}".format(pm, type(pm)))
                if pm.lower() != "unknown":
                    return {
                        "manufacturer": "Visonic",
                        "identifiers": {(DOMAIN, self._myname)},
                        "name": f"Visonic Alarm Panel (Partition {self._partition_id})",
                        "model": pm,
                        # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
                    }
        return {
            "manufacturer": "Visonic",
            "identifiers": {(DOMAIN, self._myname)},
            "name": f"Visonic Alarm Panel (Partition {self._partition_id})",
            "model": None,
            # "model": "Alarm Panel",
            # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
        }

    @property
    def name(self):
        """Return the name of the alarm."""
        return self._myname  # partition 1 but eventually differentiate partitions

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
                elif armcode == PyPanelStatus.ARMING_HOME or armcode == PyPanelStatus.ENTRY_DELAY:
                    self._mystate = STATE_ALARM_PENDING
                elif armcode == PyPanelStatus.ARMING_AWAY:
                    self._mystate = STATE_ALARM_ARMING
                elif armcode == PyPanelStatus.ARMED_HOME:
                    self._mystate = STATE_ALARM_ARMED_HOME
                elif armcode == PyPanelStatus.ARMED_AWAY:
                    self._mystate = STATE_ALARM_ARMED_AWAY

            # Currently may only contain self.hass.data[DOMAIN][DOMAINDATA]["Exception Count"]
            data = self.hass.data[DOMAIN][DOMAINDATA]
            stat = self._client.getPanelStatus()

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
    def device_state_attributes(self):  #
        """Return the state attributes of the device."""
        return self._device_state_attributes

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        return SUPPORT_ALARM_ARM_HOME | SUPPORT_ALARM_ARM_AWAY | SUPPORT_ALARM_ARM_NIGHT

    # DO NOT OVERRIDE state_attributes AS IT IS USED IN THE LOVELACE FRONTEND TO DETERMINE code_format
    @property
    def code_format(self):
        """Regex for code format or None if no code is required."""
        # Do not show the code panel if the integration is just starting up and 
        #    connecting to the panel
        if self.isPanelConnected():
            return alarm.FORMAT_NUMBER if self._client.isCodeRequired() else None
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
        self._client.sendCommand("Arm Night", PyPanelCommand.ARM_HOME_INSTANT, code)

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration {self._myname} not connected to panel.")
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

    # def dump_dict(self, mykeys):
    #    for key, value in mykeys.items():
    #        _LOGGER.debug("%s has value %s", key, str(value))

    # def async_alarm_custom_sensor_bypass(hass, code=None, entity_id=None):
    #    return self.hass.async_add_job(self.alarm_custom_sensor_bypass, code, entity_id)

    # Service alarm_control_panel.alarm_sensor_bypass
    # {"entity_id": "binary_sensor.visonic_z01", "bypass":"True", "code":"1234" }
    def sensor_bypass(self, eid : str, bypass : bool, code : str):
        """Bypass individual sensors."""
        # This function concerns itself with bypassing a sensor and the visonic panel interaction

        armcode = self._client.getPanelStatusCode()
        if armcode is None or armcode == PyPanelStatus.UNKNOWN:
            self._client.createWarningMessage(AvailableNotifications.CONNECTION_PROBLEM, "Attempt to bypass sensor, check panel connection")
        else:
            if armcode == PyPanelStatus.DISARMED:
                # If currently Disarmed
                mybpstate = self.hass.states.get(eid)
                if mybpstate is not None:
                    if "visonic device" in mybpstate.attributes:
                        devid = mybpstate.attributes["visonic device"]
                        if devid >= 1 and devid <= 64:
                            if bypass:
                                _LOGGER.debug("Attempt to bypass sensor device id = %s", str(devid))
                            else:
                                _LOGGER.debug("Attempt to restore (arm) sensor device id = %s", str(devid))
                            self._client.sendBypass(devid, bypass, code)
                        else:
                            self._client.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor, incorrect device {str(devid)} for entity {eid}")
                    else:
                        self._client.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor, incorrect entity {eid}")
                else:
                    self._client.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor, unknown device state for entity {eid}")
            else:
                self._client.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, "Visonic Alarm Panel: Attempt to bypass sensor, panel needs to be in the disarmed state")


    def alarm_arm_custom_bypass(self, data=None):
        """Bypass Panel."""
        _LOGGER.debug("Alarm Panel Custom Bypass Not Yet Implemented")

    async def service_sensor_bypass(self, call):
        """Service call to bypass individual sensors."""
        # This function concerns itself with the service call and decoding the parameters for bypassing the sensor
        _LOGGER.debug("Custom visonic alarm sensor bypass %s", str(type(call.data)))

        if not self.isPanelConnected():
            raise HomeAssistantError(f"Visonic Integration {self._myname} not connected to panel.")

        if type(call.data) is dict or str(type(call.data)) == "<class 'mappingproxy'>":
            # _LOGGER.debug("  Sensor_bypass = %s", str(type(call.data)))
            # self.dump_dict(call.data)
            if ATTR_ENTITY_ID in call.data:
                eid = str(call.data[ATTR_ENTITY_ID])
                if not eid.startswith("binary_sensor."):
                    eid = "binary_sensor." + eid
                if valid_entity_id(eid):
                    if call.context.user_id:
                        entity_id = call.data[ATTR_ENTITY_ID]  # just in case it's not a string for raising the exceptions
                        user = await self.hass.auth.async_get_user(call.context.user_id)

                        if user is None:
                            raise UnknownUser(
                                context=call.context,
                                entity_id=entity_id,
                                permission=POLICY_CONTROL,
                            )

                        if not user.permissions.check_entity(entity_id, POLICY_CONTROL):
                            raise Unauthorized(
                                context=call.context,
                                entity_id=entity_id,
                                permission=POLICY_CONTROL,
                            )
                    
                    bypass = False
                    if ATTR_BYPASS in call.data:
                        bypass = call.data[ATTR_BYPASS]

                    code = ""
                    if ATTR_CODE in call.data:
                        code = call.data[ATTR_CODE]
                        # If the code is defined then it must be a 4 digit string
                        if len(code) > 0 and not re.search(PIN_REGEX, code):
                            code = "0000"

                    self.sensor_bypass(eid, bypass, code)
                else:
                    self._client.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor, invalid entity {eid}")
            else:
                self._client.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor but entity not defined")
        else:
            self._client.createWarningMessage(AvailableNotifications.BYPASS_PROBLEM, f"Attempt to bypass sensor but entity not defined")
