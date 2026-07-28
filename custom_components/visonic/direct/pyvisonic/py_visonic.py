"""Asyncio protocol implementation of Visonic PowerMaster/PowerMax.
Based on the DomotiGa and Vera implementation:

  Credits:
    Initial setup by Wouter Wolkers and Alexander Kuiper.
    Thanks to everyone who helped decode the data.

  Originally converted to Python module by Wouter Wolkers and David Field

  The Component now follows the new HA file structure and uses asyncio.
"""  # noqa: D205, D415

# ruff: noqa: G004, FURB171

#################################################################
# PowerMax/Master send and receive messages
#################################################################

import asyncio
from copy import deepcopy
import logging
from typing import Any

from .py_const import (
    LIBRARY_VERSION,
    MAX_PARTITIONS,
    NOBYPASSSTR,
    TEXT_DL_MESSAGE_RETRIES,
    TEXT_DOWNLOAD_TIMEOUT,
    TEXT_PANEL_MODEL,
    TEXT_POWER_MASTER,
    TEXT_PROTOCOL_VERSION,
    TEXT_WATCHDOG_TIMEOUT_DAY,
    TEXT_WATCHDOG_TIMEOUT_TOTAL,
)
from .py_enum import (
    EVENT_TYPE,
    AlAlarmType,
    AlCommandStatus,
    AlCondition,
    AlPanelCommand,
    AlPanelMode,
    AlPanelStatus,
    AlSwitchCommand,
    B0SubType,
    MessagePriority,
    Packet,
    PanelSetting,
    Send,
)
from .py_types_sending import pmSendMsgB0
from .py_utils import convert_bytearray, toString
from .py_visonic_message_handling import MessageHandling

log = logging.getLogger(__name__)

# Then we will create tree_class function
#def tree_class(cls, ind = 0):
#    """Logger tree class."""
#    # Then we will print the name of the class
#    print ('-' * ind, cls.__name__)
#
#    # now, we will iterate through the subclasses
#    for K in cls.__subclasses__():
#        tree_class(K, ind + 3)
#
# Data to embed in the MSG_ARM message
#  All values in HEX
#     1/2/3/7/8/9/A/11/12/13/17/18/19/1A/1B/21/22/23  Access Denied
#     6/16      User Test
#     B         Mute Siren
#     20        Probably disarm but not tested
#     0/10      Disarm (not sure whether 10 is Disarm Instant)
#     4/C/E/24  Arm Home
#     5/D/F/25  Arm Away
#     14/1C/1E  Arm Home Instant
#     15/1D/1F  Arm Away Instant
pmArmMode = {
    AlPanelCommand.DISARM : 0x00, AlPanelCommand.ARM_HOME : 0x04, AlPanelCommand.ARM_AWAY : 0x05, AlPanelCommand.ARM_HOME_INSTANT : 0x14, AlPanelCommand.ARM_AWAY_INSTANT : 0x15    # "usertest" : 0x06,
}

# Data to embed in the MSG_PM_SIREN_MODE message
# PowerMaster to command the siren mode
pmSirenMode = {
    AlPanelCommand.EMERGENCY : EVENT_TYPE.EMERGENCY, AlPanelCommand.FIRE : EVENT_TYPE.FIRE, AlPanelCommand.PANIC : EVENT_TYPE.PANIC_PANEL
}

# Data to embed in the MSG_SWITCH message
pmSwitchState = {
    AlSwitchCommand.OFF : 0x00, AlSwitchCommand.ON : 0x01, AlSwitchCommand.DIMMER : 0x0A, AlSwitchCommand.BRIGHTEN : 0x0B
}


###################################################################################
##########################  Code Start  ###########################################
###################################################################################

# Event handling and externally callable client functions (plus updatestatus)
class VisonicProtocol(MessageHandling):
    """Event Handling."""

    ###############################################################################
    ################# The following functions are called from the client ##########
    ###############################################################################

    def __init__(self, loop, force_standard_mode : bool, disable_all_commands : bool, download_code : str, user_code_slot: int, logger = None) -> None:
        """Initialize class."""
        super().__init__(loop=loop, force_standard_mode=force_standard_mode, disable_all_commands=disable_all_commands, download_code=download_code, user_code_slot=user_code_slot, logger=logger)
        self.reset_full()

    def shutdown(self):
        """Shutdown the connection to the panel."""
        if not self.suspendAllOperations:
            super()._shutdown()
            # empty the panel settings data when stopped
            self.suspendAllOperations = True
            self.reset_full()
            log.debug("[Controller] ********************************************************************************")
            log.debug("[Controller] ****************************** Operations Suspended ****************************")
            log.debug("[Controller] ********************************************************************************")

    def reset_full(self):
        """Reset all non-permanent variables."""
        super()._reset_full()
        self.reset_connection()

    def reset_connection(self):
        """Reset the variables needed to make a new connection."""
        super()._reset_connection()
        self.send_panel_update(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend

    def set_log_events(self, logevents : list[str]) -> None:
        """Set the Log Event List for A7 message processing."""
        self.logEventList = logevents

    def handle_msgtype_testing(self, packet) -> bool:
        """Only used for message testing, not to be used in the full integration."""
        return self._processReceivedPacket(packet, True, True, True, True)   # process any of the messages for testing

    def get_partition_status_dict(self, partition : int) -> dict:
        """Get a dictionary representing the panel status."""
        a = {}
        if partition is not None:
            if 0 <= partition < MAX_PARTITIONS:
                a = deepcopy(self.PartitionState[partition].getPartitionData())
                piu = self.get_partitions_in_use()
                if isinstance(piu, set) and len(piu) > 1:
                    a["partition"] = partition + 1
        return a

    # A dictionary that is used to add to the attribute list of the Alarm Control Panel
    #     If this is overridden then please include the items in the dictionary defined here by using super()
    def get_panel_status_dict(self, include_extended_status : bool | None = None) -> dict[str, Any]:
        """Get a dictionary representing the panel status."""

        def getPanelFixedDict() -> dict[str, Any]:
            """Get the panel fixed dictionary."""
            pm = "Unknown"
            if self.PowerMaster is not None:
                if self.PowerMaster: # PowerMaster models
                    pm = "Yes"
                else:
                    pm = "No"
            return {
                TEXT_PANEL_MODEL: self.get_panel_model(),
                TEXT_POWER_MASTER: pm
            }

        # Take a deepcopy of the panel data and then add to it
        a: dict[str, Any] = deepcopy(self.PartitionState[0].getPanelData())
        a |= getPanelFixedDict()
        a |= { TEXT_PROTOCOL_VERSION: LIBRARY_VERSION,
              "emulationmode": self.PanelMode.name.lower(),
              TEXT_WATCHDOG_TIMEOUT_TOTAL: self.WatchdogTimeoutCounter,
              TEXT_WATCHDOG_TIMEOUT_DAY: self.WatchdogTimeoutPastDay
             }
        if not self.ForceStandardMode:
            a |= {
                TEXT_DOWNLOAD_TIMEOUT: max(0, self.DownloadCounter - 1),      # This is the number of download attempts and it would normally be 1 so subtract 1 off => the number of retries
                TEXT_DL_MESSAGE_RETRIES: self.pmDownloadRetryCount            # This is for individual 3F download failures
            }
        if include_extended_status and len(self.PanelStatus) > 0:
            a |= self.PanelStatus
        return a

    def get_sensor_bypass_state(self):
        """Request sensor bypass update."""
        if self.is_power_master():
            # Request the bypass status from the panel to update the sensors
            #     Instead of delaying the request, do it immediate
            #self.B0_Wanted.add(B0SubType.ZONE_BYPASS)
            s = self._create_B0_Data_Request(taglist = {pmSendMsgB0[B0SubType.ZONE_BYPASS].data})
            self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)
        else:
            # PowerMax
            # Request the bypass status from the panel to update the sensors
            self.add_message_to_send_queue(Send.BYPASSTAT, priority = MessagePriority.IMMEDIATE)


    def _createPin(self, pin : str | None):
        # Pin is None when either we can perform the action without a code OR we're in Powerlink/StandardPlus and have the pin code to use
        # Other cases, the pin must be set
        if pin is None:
            bpin = self._get_user_code() # defaults to 0000
        elif len(pin) == 4:
            bpin = convert_bytearray(pin[0:2] + " " + pin[2:4])
        else:
            # default to setting it to "0000" and see what happens when its sent to the panel
            bpin = bytearray([0,0])
        return bpin

    # panel_command
    #       state is PanelCommand
    #       optional code, if not provided then try to use the EPROM downloaded pin if in powerlink
    def panel_command(self, state : AlPanelCommand, code : str | None, partitions : set | None ) -> AlCommandStatus:
        """Send a request to the panel to Arm/Disarm."""

        if self.pmDownloadMode:
            return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

        if self.PanelMode not in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
            return AlCommandStatus.FAIL_INVALID_STATE

        bpin = self._createPin(code)
        # Ensure that the state is valid
        if state in pmArmMode:
            arm_code = bytearray()
            # Retrieve the code to send to the panel
            arm_code.append(pmArmMode[state])
            if partitions is None:
                partitions = {1,2,3}
            partition = 0
            for i in partitions:
                partition = partition | (1 << i)
            if partition == 0:
                partition = 1
            self.add_message_to_send_queue(Send.ARM, priority = MessagePriority.IMMEDIATE, options=[ [3, arm_code], [4, bpin], [6, partition] ])
            self._fetch_panel_status(priority = MessagePriority.IMMEDIATE)
            return AlCommandStatus.SUCCESS
        if self.is_power_master():
            # Powermaster panels support these additional commands
            if state == AlPanelCommand.MUTE:
                self.add_message_to_send_queue(Send.MUTE_SIREN, priority = MessagePriority.IMMEDIATE, options=[ [4, bpin] ])
                self._fetch_panel_status(priority = MessagePriority.IMMEDIATE)
                return AlCommandStatus.SUCCESS

            if state == AlPanelCommand.TRIGGER:
                self.add_message_to_send_queue(Send.PM_SIREN, priority = MessagePriority.IMMEDIATE, options=[ [4, bpin] ])
                self._fetch_panel_status(priority = MessagePriority.IMMEDIATE)
                return AlCommandStatus.SUCCESS

            if state in pmSirenMode:
                siren_code = bytearray()
                # Retrieve the code to send to the panel
                siren_code.append(pmSirenMode[state])
                self.add_message_to_send_queue(Send.PM_SIREN_MODE, priority = MessagePriority.IMMEDIATE, options=[ [4, bpin], [11, siren_code] ])
                self._fetch_panel_status(priority = MessagePriority.IMMEDIATE)
                return AlCommandStatus.SUCCESS

        return AlCommandStatus.FAIL_INVALID_STATE

    def send_switch(self, device : int, state : AlSwitchCommand) -> AlCommandStatus:
        """Send an Switch command to the panel."""
        # Send.SWITCH      : VisonicCommand(convert_bytearray('A4 00 00 00 00 00 99 99 99 00 00 43'), None  , False, "Switch Data" ),
        #log.debug(f"[SendSwitchCommand] Processing {device} {type(device)}")
        if self.pmDownloadMode:
            return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS
        if self.PanelMode not in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
            return AlCommandStatus.FAIL_SWITCH_PROBLEM
        if device < 0 or device > 15:
            return AlCommandStatus.FAIL_ENTITY_INCORRECT
        if state not in pmSwitchState:
            return AlCommandStatus.FAIL_INVALID_STATE
        log.debug(f"[SendSwitchCommand]  Send Switch Command : id = {device}  state = {state}")
        calc = 1 << device
        byte1 = calc & 0xFF
        byte2 = (calc >> 8) & 0xFF
        what = pmSwitchState[state]
        self.add_message_to_send_queue(Send.SWITCH, priority = MessagePriority.IMMEDIATE, options=[ [6, what], [7, byte1], [8, byte2] ])
        self.add_message_to_send_queue(Send.STATUS_SEN, priority = MessagePriority.IMMEDIATE)
        if self.is_power_master():
            self.B0_Wanted.add(B0SubType.PANEL_STATE_1)        # 24
        return AlCommandStatus.SUCCESS

    def get_sensor_image(self, device : int, count : int) -> AlCommandStatus:
        """Get the jpg camera image."""
        if self.pmDownloadMode:
            return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS
        if self.PanelMode not in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
            return AlCommandStatus.FAIL_INVALID_STATE
        if device - 1 in self.SensorList:
            # Clear anything left over from an abandoned capture before deciding. Both of these
            # only reset when the panel sends a fresh F4-03, and it will not send one while it is
            # stuck resending an image whose data we are discarding - so they latch, and every
            # later request is refused with no way back short of restarting HA. A user asking for
            # images now is exactly the right moment to let go of that state.
            if self.ignoreF4DataMessages or device in self.image_ignore:
                log.debug(f"[get_sensor_image] Clearing stale ignore state for zone {device} before requesting images")
                self.image_ignore.discard(device)
                self.ignoreF4DataMessages = False
            count = 3
            if self.image_manager.create(device, count):   # This makes sure that there isn't an ongoing image retrieval for this sensor
                self.add_message_to_send_queue(Send.GET_IMAGE, options=[ [3, count], [2, device] ])
                return AlCommandStatus.SUCCESS
        return AlCommandStatus.FAIL_ENTITY_INCORRECT

    async def set_panel_baud(self, baudrate : int) -> AlCommandStatus:
        """Set the panel baudrate."""
        if self.pmDownloadMode:
            return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS
        if self.is_power_master() and self.PanelMode in [AlPanelMode.POWERLINK]:                  # Only do this for powermaster panels and in powerlink mode

            bpin = self._createPin(None)                       # bytearray pin

            y1, y2 = (baudrate & 0xFFFF).to_bytes(2, "little")
            baud_data = bytearray([y2, y1])

            self.add_message_to_send_queue(Send.PM_SETBAUD, priority = MessagePriority.VITAL, options=[ [4, bpin], [13, baud_data] ])

            while not self._is_send_queue_empty(priority = MessagePriority.VITAL):
                log.debug("    Waiting for baud to be sent")
                await asyncio.sleep(0.1)

            return AlCommandStatus.SUCCESS
        return AlCommandStatus.FAIL_INVALID_STATE

    # Individually or as a set, arm/disarm the sensors
    #   This sets/clears the bypass for each sensor
    #       sensor is the zone number 1 to 31 or 1 to 64
    #       bypassValue is a boolean ( True then Bypass, False then Arm )
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink  (only used for PowerMax)
    #   Return : success or not
    #
    def bypass_command(self, sensor : int | set, bypassValue : bool, code : str = "") -> AlCommandStatus:
        """Set or Clear Sensor Bypass."""

        def createBypassB0Message(bypass : bool, zone_data : bytearray, pin : bytearray) -> bytearray:

            pmaster_request_data = convert_bytearray("00 ff 01 03 08") + zone_data
            ll = len(pmaster_request_data) + 2

            pmaster_request_start = convert_bytearray(f'b0 {"00" if bypass else "04"} 19') + bytearray([ll]) + pin
            pmaster_request_end   = bytearray([Packet.POWERLINK_TERMINAL])

            pmaster_data = pmaster_request_start + pmaster_request_data + pmaster_request_end

            checksum = self._calculateCRC(pmaster_data)   # returns a bytearray with a single byte
            to_send = bytearray([Packet.HEADER]) + pmaster_data + checksum + bytearray([Packet.FOOTER])

            log.debug(f"[_createBypassB0Message] Returning {toString(to_send)}")
            return to_send

        if self.pmDownloadMode:
            return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS
        if self.PanelMode not in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
            return AlCommandStatus.FAIL_PANEL_CONFIG_PREVENTED
        if self.PanelSettings[PanelSetting.PanelBypass] is None or self.PanelSettings[PanelSetting.PanelBypass] == NOBYPASSSTR:
            return AlCommandStatus.FAIL_INVALID_STATE

        bypassint = 0
        if isinstance(sensor, int) and (sensor - 1) in self.SensorList:
            bypassint = 1 << (sensor - 1)
        elif isinstance(sensor, set):
            for s in sensor:
                if s - 1 in self.SensorList:
                    bypassint = bypassint | (1 << (s - 1))
        if bypassint == 0:
            return AlCommandStatus.FAIL_ENTITY_INCORRECT

        # There is something to do
        if self.is_power_master():
            #log.debug(f"[SensorArmState]  bypass_command {hexify(bypassint)}")
            y1, y2, y3, y4, y5, y6, y7, y8 = (bypassint & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")
            bypass_data = bytearray([y1, y2, y3, y4, y5, y6, y7, y8])
            log.debug(f"[SensorArmState]  bypass_command data = {toString(bypass_data)}")

            if len(bypass_data) == 8:
                s = createBypassB0Message(bypassValue, bypass_data, convert_bytearray(self.DownloadCode))
                self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)
                # Request the bypass status from the panel to update the sensors
                #     Instead of delaying the request, do it immediate
                #self.B0_Wanted.add(B0SubType.ZONE_BYPASS)
                s = self._create_B0_Data_Request(taglist = {pmSendMsgB0[B0SubType.ZONE_BYPASS].data})
                self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)
                return AlCommandStatus.SUCCESS
        else:
            # PowerMax
            # The MSG_BYPASSEN and MSG_BYPASSDI commands are the same i.e. command is A1
            #      byte 0 is the command A1
            #      bytes 1 and 2 are the pin
            #      bytes 3 to 6 are the Enable bits for the 32 zones
            #      bytes 7 to 10 are the Disable bits for the 32 zones
            #      byte 11 is Packet.POWERLINK_TERMINAL
            bpin = self._createPin(code)
            #log.debug(f"[SensorArmState]  bypass_command {hexify(bypassint)}")
            y1, y2, y3, y4 = (bypassint & 0xFFFFFFFF).to_bytes(4, "little")
            bypass_data = bytearray([y1, y2, y3, y4])
            log.debug(f"[SensorArmState]  bypass_command data = {toString(bypass_data)}")

            if len(bypass_data) == 4:
                if bypassValue:
                    self.add_message_to_send_queue(Send.BYPASSEN, priority = MessagePriority.IMMEDIATE, options=[ [1, bpin], [3, bypass_data] ])
                else:
                    self.add_message_to_send_queue(Send.BYPASSDI, priority = MessagePriority.IMMEDIATE, options=[ [1, bpin], [7, bypass_data] ])
                # Request the bypass status from the panel to update the sensors
                self.add_message_to_send_queue(Send.BYPASSTAT, priority = MessagePriority.IMMEDIATE)
                return AlCommandStatus.SUCCESS
        return AlCommandStatus.FAIL_INVALID_STATE

    def get_panel_model(self) -> str:
        """Get the panel model."""
        return self.PanelModel

    def get_panel_mode(self) -> AlPanelMode:
        """Get the panel mode."""
        if self.suspendAllOperations:
            self.PanelMode = AlPanelMode.STOPPED
        return self.PanelMode

    def is_siren_active(self, partition: int) -> tuple[bool, int, AlAlarmType]:
        """Get the siren active state."""
        if not self.suspendAllOperations:
            # If specific partition requested, check just that one
            if partition is not None and 0 <= partition < MAX_PARTITIONS:
                p_state = self.PartitionState[partition]
                if p_state.SirenActive:
                    device_id = p_state.SirenActiveDeviceTrigger.id if p_state.SirenActiveDeviceTrigger else 0
                    return True, device_id, AlAlarmType.INTRUDER if p_state.PanelIntruderStatus else p_state.PanelAlarmStatus

        return False, 0, AlAlarmType.NONE

    def get_partition_status(self, partition: int) -> AlPanelStatus:
        """Get the panel status."""
        if not self.suspendAllOperations:
            if 0 <= partition < MAX_PARTITIONS:
                return self.PartitionState[partition].PanelStateData
        return AlPanelStatus.UNKNOWN

    def is_panel_ready(self, partition: int) -> bool:
        """Get the panel ready state."""
        if not self.suspendAllOperations:
            if 0 <= partition < MAX_PARTITIONS:
                return self.PartitionState[partition].PanelReady
        return False

    # Get the Event Log
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    def get_event_log(self, code : None | str = "") -> AlCommandStatus:
        """Get Panel Event Log."""
        if self.pmDownloadMode:
            return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS
        if self.PanelMode not in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
            return AlCommandStatus.FAIL_INVALID_STATE
        log.debug("get_event_log")
        self.eventCount = 0
        #if self.is_power_master():
        #    self.B0_Wanted.add(B0SubType.EVENT_LOG)
        #else:
        bpin = self._createPin(code)
        self.add_message_to_send_queue(Send.EVENTLOG, priority = MessagePriority.URGENT, options=[ [4, bpin] ])
        return AlCommandStatus.SUCCESS
