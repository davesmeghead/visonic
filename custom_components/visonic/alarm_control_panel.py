""" Create a connection to a Visonic PowerMax or PowerMaster Alarm System """
import asyncio
import homeassistant.helpers.config_validation as cv
import homeassistant.components.alarm_control_panel as alarm
import logging
import voluptuous as vol

from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, valid_entity_id

from homeassistant.components.alarm_control_panel.const import (
    SUPPORT_ALARM_ARM_AWAY,
    SUPPORT_ALARM_ARM_HOME,
    SUPPORT_ALARM_ARM_NIGHT,
)

# Use the HA core attributes, alarm states and services
from homeassistant.const import (
    ATTR_CODE,
    ATTR_CODE_FORMAT,
    ATTR_ENTITY_ID,
    SERVICE_ALARM_TRIGGER,
    SERVICE_ALARM_DISARM,
    SERVICE_ALARM_ARM_HOME,
    SERVICE_ALARM_ARM_AWAY,
    SERVICE_ALARM_ARM_NIGHT,
    SERVICE_ALARM_ARM_CUSTOM_BYPASS,
    STATE_UNKNOWN,
    STATE_ALARM_DISARMED,
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMED_NIGHT,
    STATE_ALARM_ARMED_HOME,
    STATE_ALARM_PENDING,
    STATE_ALARM_ARMING,
    STATE_ALARM_TRIGGERED,
)

from .const import (
    DOMAIN,
    VISONIC_UNIQUE_NAME,
    DOMAINCLIENT,
    DOMAINDATA,
    DOMAINALARM,
    ATTR_BYPASS,
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """ Set up the alarm control panel."""
    # _LOGGER.debug("alarm control panel async_setup_entry called")
    # Get the client
    client = hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id]
    # Create the alarm controlpanel
    va = VisonicAlarm(hass, client, 1)
    # Save it
    hass.data[DOMAIN][DOMAINALARM][entry.entry_id] = va
    # Add it to HA
    devices = [va]
    async_add_entities(devices)


class VisonicAlarm(alarm.AlarmControlPanelEntity):
    """Representation of a Visonic alarm control panel."""

    def __init__(self, hass, client, partition_id):
        """Initialize a Visonic security camera."""
        # self._data = data
        self.hass = hass
        self.client = client
        self.partition_id = partition_id
        self.mystate = STATE_UNKNOWN
        self.myname = VISONIC_UNIQUE_NAME
        # Add a listener to the visonic events for any change in the panel state
        hass.bus.async_listen("alarm_panel_state_update", self.handle_event_alarm_panel)
        # Register HA Service to bypass individual sensors
        hass.services.async_register(
            DOMAIN, "alarm_sensor_bypass", self.service_sensor_bypass, schema=ALARM_SERVICE_SCHEMA,
        )

    def handle_event_alarm_panel(self, event):
        """Listener to handle fired events."""
        self.schedule_update_ha_state(False)

    def service_sensor_bypass(self, call):
        """Service call to bypass individual sensors."""
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

    async def async_remove_entry(self, hass, entry) -> None:
        """Handle removal of an entry."""
        _LOGGER.debug("alarm control panel async_remove_entry")

    @property
    def name(self):
        """Return the name of the alarm."""
        return self.myname  # partition 1 but eventually differentiate partitions

    @property
    def should_poll(self):
        """Return should poll."""
        return False

    @property
    def device_state_attributes(self):  #
        """Return the state attributes of the device."""
        data = self.hass.data[DOMAIN][DOMAINDATA]  ## Currently may only contain self.hass.data[DOMAIN][DOMAINDATA]["Exception Count"]
        stat = self.client.getPanelStatus()
        if data is not None and stat is not None:
            if isinstance(data, dict) and isinstance(stat, dict) and len(stat) > 0 and len(data) > 0:
                return {**stat, **data}

        if stat is not None:
            return stat
        if data is not None:
            return data
        return None

    # DO NOT OVERRIDE state_attributes AS IT IS USED IN THE LOVELACE FRONTEND TO DETERMINE code_format

    @property
    def code_format(self):
        """Regex for code format or None if no code is required."""

        # **************************************************************************************
        # I would like to get the current user data from the frontend and
        #        then decide whether to show the user the code format panel or not
        # **************************************************************************************
        # _LOGGER.debug("code format called")
        #        _LOGGER.debug("  get users")
        # users = await hass.auth.async_get_users()
        #        valid_users = {}
        #        users = asyncio.get_event_loop().run_until_complete(self.hass.auth.async_get_users())
        #        # await hass.auth.async_get_users()
        #        for user in users:
        #            if user.is_active and not user.system_generated and len(user.name) > 0:
        #                valid_users[user.name] = user.is_owner
        #        owner = asyncio.get_event_loop().run_until_complete(self.hass.auth.async_get_owner())
        #        _LOGGER.debug("           valid users  ***************************** {0} \n\n  owner = {1}\n\n".format(valid_users, owner))

        # try powerlink or standard plus mode first, then it already has the user codes
        panelmode = self.client.getPanelMode()
        # _LOGGER.debug("code format panel mode %s", panelmode)
        if not self.hass.data[DOMAIN]["force_keypad"] and panelmode is not None:
            if panelmode == "Powerlink" or panelmode == "Standard Plus":
                _LOGGER.debug("code format none as powerlink or standard plus ********")
                return None

        armcode = self.client.getPanelStatusCode()

        # Are we just starting up
        if armcode is None or armcode == -1:
            _LOGGER.debug("code format none as armcode is none (panel starting up?)")
            return None

        # If currently Disarmed and user setting to not show panel to arm
        if armcode == 0 and self.hass.data[DOMAIN]["arm_without_code"]:
            _LOGGER.debug("code format none, armcode is zero and user arm without code is true")
            return None

        if self.hass.data[DOMAIN]["force_keypad"]:
            _LOGGER.debug("code format number as force numeric keypad set in config file")
            return alarm.FORMAT_NUMBER

        if self.client.hasValidOverrideCode():
            _LOGGER.debug("code format none as code set in config file")
            return None

        _LOGGER.debug("code format number")
        return alarm.FORMAT_NUMBER

    @property
    def state(self):
        """Return the state of the device."""

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
        elif armcode == 0:
            self.mystate = STATE_ALARM_DISARMED
        elif armcode == 1 or armcode == 3:  # Exit delay home or entry delay. This should allow user to enter code
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

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        # _LOGGER.debug("alarm arm home %s", self.decode_code(code))
        if self.hass.data[DOMAIN]["arm_home_instant"]:
            self.client.sendCommand("stayinstant", self.decode_code(code))
        else:
            self.client.sendCommand("stay", self.decode_code(code))

    def alarm_arm_away(self, code=None):
        """Send arm away command."""
        # _LOGGER.debug("alarm arm away %s", self.decode_code(code))
        if self.hass.data[DOMAIN]["arm_away_instant"]:
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
                    mystate = self.hass.states.get(eid)
                    if mystate is not None:
                        devid = mystate.attributes["visonic device"]
                        code = ""
                        if ATTR_CODE in data:
                            code = data[ATTR_CODE]
                        bypass = False
                        if ATTR_BYPASS in data:
                            bypass = data[ATTR_BYPASS]
                        if devid >= 1 and devid <= 64:
                            if bypass:
                                _LOGGER.debug("Attempt to bypass sensor device id = %s", str(devid))
                            else:
                                _LOGGER.debug("Attempt to restore (arm) sensor device id = %s", str(devid))
                            self.client.sendBypass(devid, bypass, self.decode_code(code))
                        else:
                            _LOGGER.warning("Attempt to bypass sensor with incorrect parameters device id = %s", str(devid))

    def alarm_arm_custom_bypass(self, data=None):
        _LOGGER.debug("Alarm Panel Custom Bypass Not Yet Implemented")
