"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System (Alarm Panel Control)."""

from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
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
from homeassistant.exceptions import Unauthorized, UnknownUser
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .client import VisonicClient
from .const import (
    #    ALARM_PANEL_CHANGE_EVENT,
    ATTR_BYPASS,
    DOMAIN,
    DOMAINCLIENT,
    DOMAINDATA,
    VISONIC_UNIQUE_NAME,
    VISONIC_UPDATE_STATE_DISPATCHER,
)

SCAN_INTERVAL = timedelta(seconds=30)

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
        # Save it
        # hass.data[DOMAIN][DOMAINALARM][entry.entry_id] = va
        # Add it to HA
        devices = [va]
        async_add_entities(devices, True)


class VisonicAlarm(alarm.AlarmControlPanelEntity):
    """Representation of a Visonic alarm control panel."""

    def __init__(self, client: VisonicClient, partition_id: int):
        """Initialize a Visonic security camera."""
        # self._data = data
        # self.hass = hass
        self.client = client
        self.partition_id = partition_id
        self.mystate = STATE_UNKNOWN
        self.myname = VISONIC_UNIQUE_NAME
        self.users = {}
        self.doneUsers = False

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
        # Add a listener to the visonic events for any change in the panel state
        # hass.bus.async_listen(ALARM_PANEL_CHANGE_EVENT, self.handle_event_alarm_panel)

    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        self.hass.services.async_remove(
            DOMAIN,
            "alarm_sensor_bypass",
        )
        self.client = None
        _LOGGER.debug("alarm control panel async_will_remove_from_hass")

    def onChange(self, event_id: int, datadictionary: dict):
        """HA Event Callback."""
        self.schedule_update_ha_state(False)

    async def service_sensor_bypass(self, call):
        """Service call to bypass individual sensors."""
        entity_id = call.data["entity_id"]
        if call.context.user_id:
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

        self.sensor_bypass(call.data)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self.myname + "_" + str(self.partition_id)

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "manufacturer": "Visonic",
            "identifiers": {(DOMAIN, self.myname)},
            "name": f"Visonic Alarm Panel (Partition {self.partition_id})",
            "model": "Alarm Panel",
            # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
        }

    # async def async_remove_entry(self, hass, entry) -> None:
    #    """Handle removal of an entry."""
    #    _LOGGER.debug("alarm control panel async_remove_entry")

    @property
    def name(self):
        """Return the name of the alarm."""
        return self.myname  # partition 1 but eventually differentiate partitions

    @property
    def should_poll(self):
        """Return should poll."""
        return False

    @property
    def code_arm_required(self):
        """Whether the code is required for arm actions."""
        return not self.client.isArmWithoutCode()

    @property
    def device_state_attributes(self):  #
        """Return the state attributes of the device."""
        data = self.hass.data[DOMAIN][
            DOMAINDATA
        ]  # Currently may only contain self.hass.data[DOMAIN][DOMAINDATA]["Exception Count"]
        stat = self.client.getPanelStatus()
        if data is not None and stat is not None:
            if (
                isinstance(data, dict)
                and isinstance(stat, dict)
                and len(stat) > 0
                and len(data) > 0
            ):
                return {**stat, **data}

        if stat is not None:
            return stat
        if data is not None:
            return data
        return None

    # DO NOT OVERRIDE state_attributes AS IT IS USED IN THE LOVELACE FRONTEND TO DETERMINE code_format

    # async def populateUsers(self):
    #    self.users = await self.hass.auth.async_get_users()

    @property
    def code_format_per_user(self):
        """List of users and their panel permissions."""
        # if self.client.isForceKeypad():
        #    _LOGGER.debug("code format number as force numeric keypad set in config file")
        #    return alarm.FORMAT_NUMBER
        valid_users = {}
        # if len(self.users) == 0:
        #    if not self.doneUsers:
        #        self.hass.async_create_task(self.populateUsers())
        #        self.doneUsers = True
        # else:
        #    valid_users["1"] = alarm.FORMAT_NUMBER
        #    for user in self.users:
        #        if user.is_active and not user.system_generated and len(user.name) > 0:
        #            valid_users[user.id] = None
        #        _LOGGER.debug("   user {0}\n".format(user))

        valid_users["cacacf490fe7401385538e9ebba48f48"] = alarm.FORMAT_NUMBER  # Bill
        valid_users["dd9b2167a42a43dba197ce7eb01d8a26"] = None  # David
        valid_users["908f1cd371e44e609ef4ebac28cb520f"] = alarm.FORMAT_TEXT  # Ted

        return valid_users

    @property
    def code_format(self):
        """Regex for code format or None if no code is required."""

        if self.client is None:
            return None

        # try powerlink or standard plus mode first, then it already has the user codes
        panelmode = self.client.getPanelMode()
        # _LOGGER.debug("code format panel mode %s", panelmode)
        if not self.client.isForceKeypad() and panelmode is not None:
            if panelmode == "Powerlink" or panelmode == "Standard Plus":
                _LOGGER.debug("code format none as powerlink or standard plus ********")
                return None

        armcode = self.client.getPanelStatusCode()

        # Are we just starting up
        if armcode is None or armcode == -1:
            _LOGGER.debug("code format none as armcode is none (panel starting up?)")
            return None

        # If currently Disarmed and user setting to not show panel to arm
        if armcode == 0 and self.client.isArmWithoutCode():
            _LOGGER.debug(
                "code format none, armcode is zero and user arm without code is true"
            )
            return None

        if self.client.isForceKeypad():
            _LOGGER.debug(
                "code format number as force numeric keypad set in config file"
            )
            return alarm.FORMAT_NUMBER

        if self.client.hasValidOverrideCode():
            _LOGGER.debug("code format none as code set in config file")
            return None

        _LOGGER.debug("code format number")
        return alarm.FORMAT_NUMBER

    @property
    def state(self):
        """Return the state of the device."""
        if self.client is None:
            return STATE_UNKNOWN

        if self.client.isSirenActive():
            self.mystate = STATE_ALARM_TRIGGERED
            return STATE_ALARM_TRIGGERED

        armcode = self.client.getPanelStatusCode()

        # armcode values
        # -1  Not yet defined
        # 0   Disarmed (Also includes 0x0A "Home Bypass", 0x0B "Away Bypass", 0x0C "Ready", 0x0D "Not Ready" and 0x10 "Disarmed Instant")
        # 1   Home Exit Delay  or  Home Instant Exit Delay
        # 2   Away Exit Delay  or  Away Instant Exit Delay
        # 3   Entry Delay
        # 4   Armed Home  or  Home Bypass  or  Entry Delay Instant  or  Armed Home Instant
        # 5   Armed Away  or  Away Bypass  or  Armed Away Instant
        # 6   User Test  or  Downloading  or  Programming  or  Installer

        # _LOGGER.debug("alarm armcode is %s", str(armcode))

        if armcode is None:
            self.mystate = STATE_UNKNOWN
        elif armcode == 0 or armcode == 6:
            self.mystate = STATE_ALARM_DISARMED
        elif (
            armcode == 1 or armcode == 3
        ):  # Exit delay home or entry delay. This should allow user to enter code
            self.mystate = STATE_ALARM_PENDING
        elif armcode == 2:
            self.mystate = STATE_ALARM_ARMING
        elif armcode == 4:
            self.mystate = STATE_ALARM_ARMED_HOME
        elif armcode == 5:
            self.mystate = STATE_ALARM_ARMED_AWAY
        else:
            self.mystate = STATE_UNKNOWN

        return self.mystate

    def decode_code(self, data) -> str:
        """Decode the panel code."""
        if data is not None:
            if type(data) == str:
                if len(data) == 4:
                    return data
            elif type(data) is dict:
                if "code" in data:
                    if len(data["code"]) == 4:
                        return data["code"]
        return ""

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        return SUPPORT_ALARM_ARM_HOME | SUPPORT_ALARM_ARM_AWAY | SUPPORT_ALARM_ARM_NIGHT

    # For the function call self.client.sendCommand
    #       state is one of: "Disarmed", "Stay", "Armed", "UserTest", "StayInstant", "ArmedInstant"
    #       optional code, if not provided then try to use the EPROM downloaded pin if in powerlink
    # call in to pyvisonic in an async way this function : def self.client.sendCommand(state, pin = ""):

    def alarm_disarm(self, code=None):
        """Send disarm command."""
        self.client.sendCommand("disarmed", self.decode_code(code))
        # _LOGGER.debug("Alarm Disarm code is {0}".format(code))

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        # _LOGGER.debug("Alarm Arm code is {0}".format(code))
        if self.client.isArmHomeInstant():
            self.client.sendCommand("stayinstant", self.decode_code(code))
        else:
            self.client.sendCommand("stay", self.decode_code(code))

    def alarm_arm_away(self, code=None):
        """Send arm away command."""
        # _LOGGER.debug("alarm arm away %s", self.decode_code(code))
        if self.client.isArmAwayInstant():
            self.client.sendCommand("armedinstant", self.decode_code(code))
        else:
            self.client.sendCommand("armed", self.decode_code(code))

    def alarm_arm_night(self, code=None):
        """Send arm night command (Same as arm home)."""
        _LOGGER.debug("alarm night called, calling Stay instant")
        self.client.sendCommand("stayinstant", self.decode_code(code))

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
    def sensor_bypass(self, data=None):
        """Bypass individual sensors."""
        _LOGGER.debug("Custom visonic alarm sensor bypass %s", str(type(data)))
        # if type(data) is str:
        #    _LOGGER.debug("  Sensor_bypass = String %s", str(data) )
        if type(data) is dict or str(type(data)) == "<class 'mappingproxy'>":
            # _LOGGER.debug("  Sensor_bypass = %s", str(type(data)))
            # self.dump_dict(data)

            if ATTR_ENTITY_ID in data:
                eid = str(data[ATTR_ENTITY_ID])
                if not eid.startswith("binary_sensor."):
                    eid = "binary_sensor." + eid
                if valid_entity_id(eid):
                    mybpstate = self.hass.states.get(eid)
                    if mybpstate is not None:
                        devid = mybpstate.attributes["visonic device"]
                        code = ""
                        if ATTR_CODE in data:
                            code = data[ATTR_CODE]
                        bypass = False
                        if ATTR_BYPASS in data:
                            bypass = data[ATTR_BYPASS]
                        if devid >= 1 and devid <= 64:
                            if bypass:
                                _LOGGER.debug(
                                    "Attempt to bypass sensor device id = %s",
                                    str(devid),
                                )
                            else:
                                _LOGGER.debug(
                                    "Attempt to restore (arm) sensor device id = %s",
                                    str(devid),
                                )
                            self.client.sendBypass(
                                devid, bypass, self.decode_code(code)
                            )
                        else:
                            _LOGGER.warning(
                                "Attempt to bypass sensor with incorrect parameters device id = %s",
                                str(devid),
                            )

    def alarm_arm_custom_bypass(self, data=None):
        """Bypass Panel."""
        _LOGGER.debug("Alarm Panel Custom Bypass Not Yet Implemented")
