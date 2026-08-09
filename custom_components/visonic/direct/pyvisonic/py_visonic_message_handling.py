"""Process the messages from the panel."""

# ruff: noqa: G004, C901, BLE001

from collections.abc import Callable
from datetime import datetime
from enum import Enum, auto
import io
import logging
import os
import traceback
from typing import NamedTuple

from PIL import Image

from .py_const import (
    ABORTED,
    DEGRADED,
    DELAYED,
    DOWNLOAD_PDU_RETRY_COUNT,
    FAILED,
    OBFUS,
    SUCCESS,
    notknown,
)
from .py_enum import (
    EventType,
    RAW,
    AlCondition,
    AlPanelMode,
    AlPanelStatus,
    AlSensorCondition,
    B0SubType,
    IndexName,
    Packet,
    PanelSetting,
    Receive,
    Send,
)
from .py_generic_device import AlGenericDeviceHelper, GenericDeviceType
from .py_panel_settings import pmPanelSettingCodes, pmZoneTypeKey
from .py_partition_state import PartitionStateClass
from .py_sensor_types import ZoneFunctions
from .py_types import AlPanelEventData
from .py_types_receiving import Chunky
from .py_types_sending import pmSendMsgB0, pmSendMsgB0_reverseLookup
from .py_utils import b2i, convert_bytearray, get_local_time, hexify, toString
from .py_visonic_message_b0_chunk import MessageHandlingB0Data

AUDIO_IMAGE_ID = 0   # the panel closes a capture with its audio clip, always as image 0
IMAGE_GOOD = 0       # Used in F4-07 messages to the panel
IMAGE_BAD = 1        # Used in F4-07 messages to the panel

def _is_wav(buffer) -> bool:
    """Does this buffer look like a RIFF/WAVE clip rather than a JPEG frame."""
    return (buffer is not None and len(buffer) > 12
            and bytes(buffer[:4]) == b"RIFF" and bytes(buffer[8:12]) == b"WAVE")


def _is_capture_audio(record) -> bool:
    """Is this record the capture's audio clip.

    image_id comes from the F4-03 header, which has its own frame CRC, so it survives damage to
    the payload. The RIFF magic does not: corrupt the first byte and the clip stops looking like
    audio at all.
    """
    return record.image_id == AUDIO_IMAGE_ID or _is_wav(record.buffer)


log = logging.getLogger(__name__)

powermax_devices: dict[str, int] = {
    "System":    0,
    "Zone":      1,
    "Fob":      31,
    "User":     39,
    "1Pad":     47,
    "Siren":    55,
    "2Pad":     57,
    "Switch":   61,
    "PGM":      76,
    "GSM":      77,
    "P-LINK":   78,
    "PTag":     79,
    "Rptr":     -1,
    "Unknown":  87,
}

powermaster_devices: dict[str, int] = {
    "System":     0,
    "Zone":       1,
    "Fob":       65,
    "User":      97,
    "1Pad":     145,
    "Siren":    177,
    "2Pad":     185,
    "Switch":   189,
    "PGM":      204,
    "GSM":       -1,
    "P-LINK":   205,
    "PTag":     206,
    "Rptr":     238,
    "Unknown":  246,
}

###################################################################################
##########################  Data Driven Message Decode ############################
###################################################################################

class ProcessFlag(Enum):
    """Used for the 4 boolean flags."""
    AB = auto()
    NORMAL = auto()
    B0 = auto()
    DOWNLOAD = auto()

class DecodeMessage(NamedTuple):
    """Used in decoding messages from the panel i.e. _handle_msgtype_XX()."""
    flag : ProcessFlag | bool
    func : Callable[[bytearray], bool | None] | None
    payload_start: int    # All payloads currently start at offset 2
    payload_end: int      # All payloads currently end at offset -2, except for F4 which is -3 (footer and 2 checksum bytes)
    pushchange : bool
    message : str | None

class MessageHandling(MessageHandlingB0Data):
    """Message Handling. These are the individual messages handlers."""

    def __init__(self, loop, force_standard_mode : bool, disable_all_commands : bool, download_code : str, user_code_slot: int, logger = None) -> None:
        """Initialize class."""
        super().__init__(loop=loop, force_standard_mode=force_standard_mode, disable_all_commands=disable_all_commands, download_code=download_code, user_code_slot=user_code_slot, logger=logger)

        # Create the received message handler dict. The basic protocol messages are always processed.
        self.decode_message_handlers: dict[Receive, DecodeMessage] = {
            Receive.ACKNOWLEDGE       : DecodeMessage(                 True , self._handle_msgtype_02, 2, -2, False, None ),  # ACK
            Receive.TIMEOUT           : DecodeMessage(                 True , self._handle_msgtype_06, 2, -2, False, None ),  # Timeout
            Receive.UNKNOWN_07        : DecodeMessage(                 True , self._handle_msgtype_07, 2, -2, False, None ),  # No idea what this means
            Receive.ACCESS_DENIED     : DecodeMessage(                 True , self._handle_msgtype_08, 2, -2, False, None ),  # Access Denied
            Receive.LOOPBACK_TEST     : DecodeMessage(                 True , self._handle_msgtype_0B, 2, -2, False, None ),  # # LOOPBACK TEST, STOP (0x0B) IS THE FIRST COMMAND SENT TO THE PANEL WHEN THIS INTEGRATION STARTS
            Receive.EXIT_DOWNLOAD     : DecodeMessage(                 True , self._handle_msgtype_0F, 2, -2, False, None ),  # Exit
            Receive.UNKNOWN_1F        : DecodeMessage(                False , None                   , 2, -2, False, "WARNING: Message 0x1F is not decoded" ),
            Receive.UNKNOWN_22        : DecodeMessage(                False , None                   , 2, -2, False, "WARNING: Message 0x22 is not decoded, are you using an old Powermax Panel as this is not supported?" ),
            Receive.DOWNLOAD_RETRY    : DecodeMessage(                 True , self._handle_msgtype_25, 2, -2, False, None ),  # Download retry
            Receive.DOWNLOAD_SETTINGS : DecodeMessage( ProcessFlag.DOWNLOAD , self._handle_msgtype_33, 2, -2, False, "Received 33 Message, we are in the wrong mode (so I'm ignoring the message)"),  # Settings send after a MSGV_START
            Receive.PANEL_INFO        : DecodeMessage(                 True , self._handle_msgtype_3C, 2, -2, False, None ),  # Message when start the download
            Receive.DOWNLOAD_BLOCK    : DecodeMessage( ProcessFlag.DOWNLOAD , self._handle_msgtype_3F, 2, -2, False, "Received 3F Message, we are in the wrong mode (so I'm ignoring the message)"),  # Download information
            Receive.EVENT_LOG         : DecodeMessage(   ProcessFlag.NORMAL , self._handle_msgtype_A0, 2, -2, False, None ),  # Event log
            Receive.ZONE_NAMES        : DecodeMessage(   ProcessFlag.NORMAL , self._handle_msgtype_A3, 2, -2,  True, None ),  # Zone Names
            Receive.STATUS_UPDATE     : DecodeMessage(   ProcessFlag.NORMAL , self._handle_msgtype_A5, 2, -2,  True, None ),  # Zone Information/Update
            Receive.ZONE_TYPES        : DecodeMessage(   ProcessFlag.NORMAL , self._handle_msgtype_A6, 2, -2,  True, None ),  # Zone Types
            Receive.PANEL_STATUS      : DecodeMessage(   ProcessFlag.NORMAL , self._handle_msgtype_A7, 2, -2,  True, None ),  # Panel Information/Update
            Receive.POWERLINK         : DecodeMessage(       ProcessFlag.AB , self._handle_msgtype_AB, 2, -2,  True, "Received AB Message, we are in the wrong mode (so I'm ignoring the message)"),
            Receive.SWITCH_NAMES      : DecodeMessage(   ProcessFlag.NORMAL , self._handle_msgtype_AC, 2, -2,  True, None ),  # Switch Names
            Receive.IMAGE_MGMT        : DecodeMessage(   ProcessFlag.NORMAL , self._handle_msgtype_AD, 2, -2,  True, None ),  # No idea what this means, it might ...  send it just before transferring F4 video data ?????
            Receive.POWERMASTER       : DecodeMessage(       ProcessFlag.B0 , self._handle_msgtype_B0, 2, -2,  True, None ),
            Receive.IMAGE_DATA        : DecodeMessage(   ProcessFlag.NORMAL , self._handle_msgtype_F4, 2, -3, False, None ),  # F4 Message from a Powermaster, decode image and audio data. Footer and 2 checksum bytes.
            Receive.REDIRECT          : DecodeMessage(                 True , self._handle_msgtype_C0, 2, -2, False, None ),
            Receive.PROXY_COMMAND     : DecodeMessage(                 True , self._handle_msgtype_E1, 2, -2, False, None ),
            Receive.PROXY             : DecodeMessage(                 True , self._handle_msgtype_E0, 2, -2, False, None )
        }

    # This is abstract so implement the function
    def _processReceivedPacket(self, packet : bytearray, processAB : bool, processNormalData : bool, processB0 : bool, processDownload : bool) -> bool:

        if len(packet) < 4:
            # There must at least be a header, command, checksum and footer i.e. 4 bytes
            #    It is rare that this happens so checked after the creation of decode_message_handlers
            log.warning(f"[_processReceivedPacket] Received invalid packet structure, not processing it {toString(packet)}")
            return False

        try:
            msg_type : Receive = Receive(packet[1])
        except (OSError, KeyError, ValueError):
            log.info(f"[_processReceivedPacket] Unknown/Unhandled packet type from Visonic Panel, packet {toString(packet)}")
            return False

        try:
            log.debug(f"[_processReceivedPacket] {msg_type.name} {processAB=} {processB0=} {processNormalData=} {processDownload=}")

            # msg_type is a valid Receive type but also check to make sure it's in decode_message_handlers as a key
            dm = self.decode_message_handlers.get(msg_type)
            if dm is not None:
                # build the flags dict
                flags = {
                    ProcessFlag.AB: processAB,
                    ProcessFlag.NORMAL: processNormalData,
                    ProcessFlag.B0: processB0,
                    ProcessFlag.DOWNLOAD: processDownload,
                }
                # if dm.flag is bool then use it, else look up the value in the flags dict
                condition = dm.flag if isinstance(dm.flag, bool) else flags.get(dm.flag, False)

                if dm.func is not None and condition:
                    # There is a valid function and the condition is True so we process the packet
                    pc = dm.func(packet[dm.payload_start : dm.payload_end])    # Use the return value if the function returns
                    return pc if pc is not None and isinstance(pc,bool) else dm.pushchange
                if dm.message is not None:
                    log.debug(f"[_processReceivedPacket]     {dm.message}, data bytes are {toString(packet)}")
                else:
                    log.debug(f"[_processReceivedPacket]     Received data not processed, data bytes are {toString(packet)}")
            else:
                log.debug(f"[_processReceivedPacket] {msg_type} not in list of valid messages, packet {toString(packet)}")
        except (OSError, KeyError, ValueError):
            log.warning(f"[_processReceivedPacket] Visonic Panel Message Decoder General Exception, packet {toString(packet)}")
        return False

    def _handle_msgtype_02(self, data) -> None:  # ACK
        """Handle Acknowledges from the panel."""
        # Normal acknowledges have msgtype 0x02 but no data, when in powerlink the panel also sends data byte 0x43
        #    I have not found this on the internet, this is my hypothesis
        #log.debug(f"[handle_msgtype02] Ack Received  data = {toString(data)}")

        process_ab = not self.pmDownloadMode and self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK]
        self._reset_watchdog_timeout()
        if process_ab and len(data) > 0 and data[0] == Packet.POWERLINK_TERMINAL:
            self.receivedPowerlinkAcknowledge = True
            if self.allowAckToTriggerRestore:
                log.debug(f"[handle_msgtype02]        Received a powerlink acknowledge, I am in {self.PanelMode.name} mode and sending Message {'RESTORE' if self.ABMessageSupported else 'STATUS'}") #  and not self.PowerLinkBridgeConnected
                #self.add_message_to_send_queue(Send.RESTORE if self.ABMessageSupported else Send.STATUS)   # and not self.PowerLinkBridgeConnected
                self._trigger_restore_status()     # Clear message buffers and send a Restore (if in Powerlink or standard plus) or Status (not in Powerlink) to the Panel
                self.allowAckToTriggerRestore = False

    def _handle_msgtype_06(self, _data : bytearray) -> None:
        """MsgType=06 - Time out. Timeout message from the PM, most likely we are/were in download mode."""
        log.debug("[handle_msgtype06] Timeout Received")
        self.TimeoutReceived = True

    def _handle_msgtype_07(self, data : bytearray) -> None:
        """MsgType=07 - No idea what this means."""
        log.debug(f"[handle_msgtype07] No idea what this message means, data = {toString(data)}")
        self._check_unknown("    and its different", "handle_msgtype07", toString(data))
        # Assume that we need to send an ack

    def _handle_msgtype_08(self, data : bytearray) -> None:
        """MsgType=08 - Access Denied."""
        log.debug(f"[handle_msgtype08] Access Denied  len {len(data)} data {toString(data)}")
        self.AccessDeniedReceived = True
        self.AccessDeniedMessage = self.pmLastSentMessage
        if len(data) > 0 and data[0] == Packet.POWERLINK_TERMINAL:
            log.debug("[handle_msgtype08]        Access Denied  from a Powerlink 0x43 command")

    def _handle_msgtype_0B(self, _data : bytearray) -> None:  # LOOPBACK TEST SUCCESS, STOP COMMAND (0x0B) IS THE FIRST COMMAND SENT TO THE PANEL WHEN THIS INTEGRATION STARTS
        """Handle LOOPBACK."""
        #log.debug(f"[handle_msgtype0B] Loopback test assumed {toString(data)}")
        self.loopbackTest = True
        self.loopbackCounter += 1
        log.warning(f"[handle_msgtype0B] LOOPBACK TEST SUCCESS, Counter is {self.loopbackCounter}")

    def _handle_msgtype_0F(self, data : bytearray) -> None:  # EXIT
        """Handle EXIT from the panel."""
        log.debug(f"[handle_msgtype0F] Exit    data is {toString(data)}")
        # This is sent by the panel during download to tell us to stop the download
        self.ExitReceived = True

    def _handle_msgtype_25(self, data : bytearray) -> None:  # Download retry
        """MsgType=25 - Download retry. Unit is not ready to enter download mode."""
        # Format: <MsgType> <?> <?> <delay in sec>
        int_delay = data[2]
        log.debug(f"[handle_msgtype25] Download Retry, have to wait {int_delay} seconds     data is {toString(data)}")
        self.DownloadRetryReceived = True

    def _handle_msgtype_33(self, data : bytearray) -> None:
        """MsgType=33 - Settings. Message sent after a MSG_START. We will store the information in an internal array/collection."""

        if len(data) != 10:
            log.debug(f"[handle_msgtype33] ERROR: MSGTYPE=0x33 Expected len=14, Received={len(data)}")
            log.debug(f"[handle_msgtype33]                            {toString(data)}")
            return

        # Data Format is: <index> <page> <8 data bytes>
        # Extract Page and Index information
        index = data[0]
        page = data[1]

        # log.debug(f"[handle_msgtype33] Getting Data {toString(data)}   page {hexify(iPage)}   index {hexify(iIndex)}")
        # Write to memory map structure, but remove the first 2 bytes from the data
        self.epromManager.saveEPROMSettings(page, index, data[2:])

    def _handle_msgtype_3C(self, data : bytearray) -> None:  # Panel Info Messsage when start the download
        """The panel information is in 4 & 5. 5=PanelType e.g. PowerMax, PowerMaster.  4=Sub model type of the panel - just informational, not used."""
        if not self.pmGotPanelDetails:
            self.ModelType = data[4]
            if not self._set_data_from_panel_type(data[5], self.pmForceDownloadByEPROM):
                log.debug(f"[handle_msgtype3C] Panel Type {data[5]} Unknown")

            log.debug(f"[handle_msgtype3C] PanelType={self.PanelType} : {self.PanelModel} , Model={self.ModelType}   Powermaster {self.PowerMaster}")

            self.pmGotPanelDetails = True
        else:
            log.debug("[handle_msgtype3C] Not Processed as already got Panel Details")

    def _handle_msgtype_3F(self, data : bytearray) -> None:
        """MsgType=3F - Download information. Multiple 3F can follow each other, maximum block size seems to be 0xB0 bytes."""

        def format_download_list(dl: list[bytearray]):
            for lh in dl:
                log.warning(f"[3F Message handler]    {toString(lh)}")

        if self.PanelMode != AlPanelMode.DOWNLOAD:
            log.debug("[handle_msgtype3F] Received data but in Standard Mode so ignoring data")
            return

        # data format is normally: <index> <page> <length> <data ...>
        # If the <index> <page> = FF, then it is an additional PowerMaster MemoryMap
        index = data[0]
        page = data[1]
        length = data[2]

        # PowerMaster 10 (Model 7) and PowerMaster 33 (Model 10) has a very specific problem with downloading the Panel EPROM and doesn't respond with the correct number of bytes
        #if self.PanelType is not None and self.ModelType is not None and ((self.PanelType == 7 and self.ModelType == 68) or (self.PanelType == 10 and self.ModelType == 71)):
        #    if iLength != len(data) - 3:
        #        log.debug(f"[handle_msgtype3F] Not checking data length as it could be incorrect.  We requested {iLength} and received {len(data) - 3}")
        #        log.debug(f"[handle_msgtype3F]                            {toString(data)}")
        #    # Write to memory map structure, but remove the first 3 bytes (index/page/length) from the data
        #    self.epromManager.saveEPROMSettings(iPage, iIndex, data[3:])

        blocklen = self.epromManager.findLength(self.is_power_master(), page, index)

        #log.warning(f"[3F Message handler] got data for  {iPage=}  {iIndex=}   {iLength=}")
        #format_download_list(self.myDownloadList)

        if length == len(data) - 3 and blocklen is not None and blocklen == length:
            # Write to memory map structure, but remove the first 3 bytes (index/page/length) from the data
            #log.warning("[3F Message handler]     Success")
            self.epromManager.saveEPROMSettings(page, index, data[3:])
            # Are we finished yet?
            if len(self.myDownloadList) > 0:
                self.pmDownloadInProgress = True
                self.add_message_to_send_queue(Send.DL, options=[ [1, self.myDownloadList.pop(0)] ])  # Read the next block of EPROM data
            else:
                self.myDownloadList = self.epromManager.populatEPROMDownload(self.is_power_master())
                if len(self.myDownloadList) == 0:
                    # This is the message to tell us that the panel has finished download mode, so we too should stop download mode
                    log.debug("[handle_msgtype3F] Download Complete")
                    self.pmDownloadInProgress = False
                    self.pmDownloadMode = False
                    self.pmDownloadComplete = True
                else:
                    log.debug("[handle_msgtype3F] Download seemed to be complete but not got all EPROM data yet")
                    self.pmDownloadInProgress = True
                    self.add_message_to_send_queue(Send.DL, options=[ [1, self.myDownloadList.pop(0)] ])  # Read the next block of EPROM data
        elif self.pmDownloadRetryCount <= DOWNLOAD_PDU_RETRY_COUNT:
            log.warning(f"[handle_msgtype3F] Invalid EPROM data block length (received: {len(data)-3}, Expected: {length},  blocklen: {blocklen}). Adding page {page} Index {index} to the end of the list to redownload")
            log.warning(f"[handle_msgtype3F]                            {toString(data)}")
            #tmp = self.epromManager.findLength(self.is_power_master, iPage, iIndex) # used to debug only
            # Add it back on to the end to re-download it
            if blocklen is not None:
                self.myDownloadList.append(bytearray([index, page, blocklen, 0]))
            # Increment counter
            self.pmDownloadRetryCount += 1
        else:
            log.warning(f"[handle_msgtype3F] Invalid EPROM data block length (received: {len(data)-3}, Expected: {length},  blocklen: {blocklen}). Giving up on page {page} Index {index}")
            self.myDownloadList = []
            log.debug("[handle_msgtype3F] Download InComplete")
            self.pmDownloadInProgress = False
            self.pmDownloadMode = False
            self.pmDownloadComplete = False

    def _handle_msgtype_A0(self, data : bytearray) -> None:
        """MsgType=A0 - Event Log."""
        # From my Powermaster30  [handle_MsgTypeA0] Packet = 5f 02 01 64 58 5c 58 d3 41 51

        # My PowerMax
        #    To Ct Pt ---- time ---- Zo Ev    Time does not have the seconds value
        #    fb 01 00 00 00 00 00 00 03 00
        #    fb 02 01 1c 15 06 0a 18 1f 55    6/10/24 at 21:28:01    Disarmed   FOB-01    why are all the seconds 0 or 1
        #    fb 03 01 09 12 06 0a 18 1f 52    6/10/24 at 18:09:01    Armed Away FOB-01

        # From a PM10:
        #    To Ct Pt -- time ---  Y Zo Ev    Don't know what Y and data[7] is. It could be the panel state e.g. 0x52 is Armed Away
        #    fb 02 00 3f 71 02 67 04 01 5c
        #    fb 03 00 69 3a 01 67 53 00 1c
        #    fb 04 01 69 3a 01 67 52 61 1b

        event_number = data[1]
        # Check for the first entry, it only contains the number of events
        if event_number == 0x01:
            log.debug("[handle_msgtypeA0]    Eventlog received")
            self.eventCount = data[0] - 1  ## the number of messages (including this one) minus 1
        elif self.onPanelLogHandler is not None:
            # There's no point in doing all of this if there's no handler to send it to!

            if self.is_power_master(): # PowerMaster models
                # extract the time as "epoch time" and convert to normal time
                hs = b2i(data[3:7])
                pmtime = datetime.fromtimestamp(hs)
                #log.debug(f"[handle_msgtypeA0]   Powermaster time {hs} as hex {hex(hs)} from epoch is {pmtime}")
                event_zone = data[8]
            else:
                # Assume that seconds is 0 for PowerMax panels
                #        datetime(year, month, day, hour, minute, second, microsecond)
                pmtime = datetime(int(data[7]) + 2000, data[6], data[5], data[4], data[3], 0, 0)
                event_zone = int(data[8] & 0x7F) # PowerMax limits the event zones, 0 to 127

            # Send the event log in to HA
            #     Do not use timezone times as it was the log created on that day at that time
            #log.debug(f"[handle_msgtypeA0]                       Log Entry {pl}")
            self.onPanelLogHandler(total = self.eventCount, current = event_number - 1, partition = data[2], dateandtime = pmtime, zone = event_zone, event = data[9])

    def _handle_msgtype_A3(self, data : bytearray) -> None:
        """MsgType=A3 - Zone Names."""
        log.debug(f"[handle_MsgTypeA3] Packet = {toString(data)}")
        msg_cnt = int(data[0])
        offset = 8 * (int(data[1]) - 1)
        log.debug(f"            Message Count is {msg_cnt}   offset={offset}     self.PanelMode = {self.PanelMode}")

        if len(self.PanelSettings[PanelSetting.ZoneNames]) < offset+8:
            self.PanelSettings[PanelSetting.ZoneNames].extend(bytearray(offset+8-len(self.PanelSettings[PanelSetting.ZoneNames])))
        for i in range(8):
            # Save the Zone Name
            self.PanelSettings[PanelSetting.ZoneNames][offset+i] = data[2+i] & 0x1F
            if self.PanelMode not in (AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED) and (offset+i) in self.SensorList:
                self._update_sensor(sensor_identifier = offset+i)

    def _handle_msgtype_A5(self, data : bytearray) -> None:  # Status Message
        """MsgType=A5 - Zone Data Update."""

        # msgTot = data[0]
        command_type = data[1]

        #log.debug(f"[handle_msgtypeA5] Parsing A5 packet {toString(data)}")

        match command_type:
            case 1 if len(self.SensorList) > 0:
                log.debug("[handle_msgtypeA5] Zone Alarm Status: Ztrip and ZTamper")
                self._do_sensor_update(data[2:6],  ZoneFunctions.DO_ZTRIP,   "[handle_msgtypeA5]      Zone Trip Alarm 32-01")
                self._do_sensor_update(data[6:10], ZoneFunctions.DO_ZTAMPER, "[handle_msgtypeA5]      Zone Tamper Alarm 32-01")

            case 2 if len(self.SensorList) > 0:
                # if in standard mode then use this A5 status message to reset the watchdog timer
                if self.PanelMode != AlPanelMode.POWERLINK:
                    log.debug("[handle_msgtypeA5] Got A5 02 message, resetting watchdog")
                    self._reset_watchdog_timeout()

                log.debug("[handle_msgtypeA5] Zone Status: Status and Battery")
                self._do_sensor_update(data[2:6],  ZoneFunctions.DO_STATUS,  "[handle_msgtypeA5]      Open Door/Window Status Zones 32-01")
                self._do_sensor_update(data[6:10], ZoneFunctions.DO_BATTERY, "[handle_msgtypeA5]      Battery Low Zones 32-01")

            case 3 if len(self.SensorList) > 0:
                # This status is different from the status in the 0x02 part above i.e they are different values.
                #    This one is wrong (I had a door open and this status had 0, the one above had 1)
                #       According to domotica forum, this represents "active" but what does that actually mean?
                log.debug("[handle_msgtypeA5] Zone Status: Inactive and Tamper")
                if self.is_power_master():
                    # For PowerMaster only use the B0 message data
                    val = b2i(data[2:6])
                    log.debug(f"[handle_msgtypeA5]      Trigger (Inactive) Status Zones 32-01: {val:032b} Not Used")
                else:
                    # Use this information for PowerMax panels
                    self._do_sensor_update(data[2:6],  ZoneFunctions.DO_INACTIVE, "[handle_msgtypeA5]      Zone Inactive 32-01")
                self._do_sensor_update(data[6:10], ZoneFunctions.DO_TAMPER, "[handle_msgtypeA5]      Tamper Zones 32-01")

            case 4:
                # 00 04 01 15 00 00 02 02 00 00
                # Assume that every zone event causes the need to push a change to the sensors etc
                if self.PanelMode != AlPanelMode.POWERLINK:
                    #log.debug("[handle_msgtypeA5] Got A5 04 message, resetting watchdog")
                    self._reset_watchdog_timeout()

                sys_status = data[2]
                sys_flags = data[3]
                event_device = data[4]
                event_type = data[5]
                # dont know what 6 and 7 are
                dummy1 = data[6]
                dummy2 = data[7]
                log.info(f"[handle_msgtypeA5]      sys_status=0x{hexify(sys_status)}    sys_flags=0x{hexify(sys_flags)}    event_device=0x{hexify(event_device)}    event_type=0x{hexify(event_type)}    unknowns are 0x{hexify(dummy1)} 0x{hexify(dummy2)}")
                self._check_unknown("    A5 4 data[6] is different to last time", "handle_msgtypeA5_4_6", data[6])
                self._check_unknown("    A5 4 data[7] is different to last time", "handle_msgtypeA5_4_7", data[7])

                if event_device > 31:
                    log.info("[handle_msgtypeA5]     ************ event zone not a zone maybe *******************")

                if sys_status > 0x1F:  # Mark-Mills with a PowerMax Complete Part, sometimes this has the 0x20 bit set and I'm not sure why
                    log.debug(f"[handle_msgtypeA5]           {notknown} -->  sys_status is a large number, what does bit 6 mean?")

                if self.get_partitions_in_use() is None:
                    sys_status = sys_status & 0x1F     # Mark-Mills with a PowerMax Complete Part, sometimes this has the 0x20 bit set and I'm not sure why
                    #last10seconds = sys_flags & 0x10

                    # Process sys_status and sys_flags only if there are no partitions
                    #     The panel sends A5 messages for all partitions but we don't know the partition number. So how do we know what to decode?
                    old_panel_state = self.PartitionState[0].PanelStateData
                    s = self.PartitionState[0].UpdatePartition(sysStatus=sys_status, sysFlags=sys_flags, PanelMode=self.PanelMode)   # does not set partition in return value
                    if s is not None:
                        self.add_panel_event_data(s)
                    new_panel_state = self.PartitionState[0].PanelStateData
                    if new_panel_state == AlPanelStatus.DISARMED and new_panel_state != old_panel_state:
                        # Panel state is Disarmed and it has just changed, get the bypass state of the sensors as the panel may have changed them
                        self.add_message_to_send_queue(Send.BYPASSTAT)

                if sys_flags & 0x20 != 0:  # Zone Event
                    if event_type > 0 and event_device != 0xff: # I think that 0xFF refers to the panel itself as a zone. Currently not processed
                        self._process_zone_event(event_device=event_device, event_type=event_type)

                switch_stat1 = data[8]
                switch_stat2 = data[9]
                self._process_switch_state_update(switch_status=switch_stat1 + (switch_stat2 * 0x100))

    #        elif event_type == 0x05:
    #            # 0d a5 10 05 00 00 00 00 00 00 12 34 43 bc 0a
    #            # 0d a5 0d 05 00 00 00 07 00 00 12 34 43 b7 0a
    #            #     Might be a coincidence but the "1st Account No" is set to 001234
    #            pass

            case 6:
                log.debug("[handle_msgtypeA5] Zone Status: Enrolled and Bypass")
                val = b2i(data[2:6])
                if val != self.enrolled_old:
                    log.debug(f"[handle_msgtypeA5]      Enrolled Zones 32-01: {val:032b}")
                    self.enrolled_old = val

                    self._update_panel_setting(key = PanelSetting.ZoneEnrolled, length = 4, datasize = RAW.BITS.value, data = data[2:6], display = True, msg = "A5 Zone Enrolled Data")
                    self._update_all_sensors()

                self._do_sensor_update(data[6:10], ZoneFunctions.DO_BYPASS, "[handle_msgtypeA5]      Bypassed Zones 32-01")

            case _:
                # easiest way to check if its full of zeros
                vala = b2i(data[2:6])
                valb = b2i(data[6:10])
                if vala != 0 or valb != 0:
                    log.info(f"[handle_msgtypeA5]      Unknown A5 Message: {toString(data)}")
                    # [handle_msgtypeA5]      Unknown A5 Message: 10 05 00 00 00 00 00 00 43 21 43        # 4321 is the 1st account number
                self._check_unknown("[handle_msgtypeA5]              This A5 Message is different to last time", f"handle_msgtypeA5_{command_type}", toString(data))
        self.send_panel_update(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend

    def _handle_msgtype_A6(self, data : bytearray) -> None:
        """MsgType=A6 - Zone Types."""
        log.debug(f"[handle_MsgTypeA6] Packet = {toString(data)}")
        msg_cnt = int(data[0])
        offset = 8 * (int(data[1]) - 1)
        log.debug(f"            Message Count is {msg_cnt}   offset={offset}     self.PanelMode={self.PanelMode}")
        if len(self.PanelSettings[PanelSetting.ZoneTypes]) < offset+8:
            self.PanelSettings[PanelSetting.ZoneTypes].extend(bytearray(offset+8-len(self.PanelSettings[PanelSetting.ZoneTypes])))
        for i in range(8):
            # Save the Zone Type
            self.PanelSettings[PanelSetting.ZoneTypes][offset+i] = ((int(data[2+i])) - 0x1E) & 0x0F
            log.debug(f"                        Zone type for sensor {offset+i+1} is {hexify((int(data[2+i])) - 0x1E)} : {pmZoneTypeKey[self.PanelSettings[PanelSetting.ZoneTypes][offset+i]]}")
            if self.PanelMode not in (AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED) and (offset+i) in self.SensorList:
                self._update_sensor(sensor_identifier = offset+i)

    def _handle_msgtype_A7(self, data : bytearray) -> None:
        """MsgType=A7 - Panel Status Change."""
        #log.debug(f"[handle_msgtypeA7] Panel Status Change {toString(data)}")
        # 01 00 27 51 02 ff 00 02 00 00
        # ff 5d 00 2d 00 00 11 0c 00 00

        def getType(event) -> EventType:
            return EventType(event) if event in EventType else EventType.NOT_DEFINED

        def getTypeStr(event) -> str:
            return self.logEventList[event] if 0 <= event <= 151 and len(self.logEventList[event]) > 0 else "Unknown"

        def displayEvent(m, zone, event):
            et : EventType = getType(event)
            event_str = getTypeStr(event)
            if self.is_power_master():
                log.debug(f"[handle_msgtypeA7]           {m}  {zone}/{event}   {et.name}     {event_str=}")
            else:
                log.debug(f"[handle_msgtypeA7]           {m}  {zone}/{event}   {et.name}     {event_str=}")

        def processEvent(partition, zone, event) -> EventType:

            et : EventType = getType(event)
            self.add_panel_event_data(AlPanelEventData(name=zone, action=int(event))) # assume partition -1 means a panel event not tied to a partition

            part: PartitionStateClass = self.PartitionState[partition]
            if zone-1 in self.SensorList:                                                 # only used if it decides that siren is sounding, then that is the trigger sensor
                part.UpdatePanelState(et, self.SensorList[zone-1])
            else:
                part.UpdatePanelState(et)

            if et == EventType.FORCE_ARM or (self.pmForceArmSetInPanel and et == EventType.DISARM): # Force Arm OR (ForceArm has been set and Disarm)
                self.pmForceArmSetInPanel = et == EventType.FORCE_ARM                                # When the panel uses ForceArm then sensors may be automatically armed and bypassed by the panel
                log.debug("[handle_msgtypeA7]              Panel has been Armed using Force Arm, sensors may have been bypassed by the panel, asking panel for an update on bypassed sensors")
                if self.is_power_master():
                    self.B0_Wanted.add(B0SubType.ZONE_BYPASS)
                else:
                    self.add_message_to_send_queue(Send.BYPASSTAT)

            return et

        def device_battery_check(device_reference: int, event_type: EventType, index_name: IndexName, dt: str, good: EventType, bad: EventType, dev_type: GenericDeviceType):
            if self.PowerMaster:
                start = powermaster_devices.get(dt)
            else:
                start = powermax_devices.get(dt)
            finish = start + self.PanelCapabilities.get(index_name, -100000)
            if finish > 0 and device_reference >= start and device_reference <= finish:
                index = device_reference - start
                device_id = AlGenericDeviceHelper.make_key(dev_type, index)
                if device_id in self.DeviceList:
                    if event_type == bad:
                        self.DeviceList[device_id].low_battery = True
                        self.DeviceList[device_id].notify()
                    if event_type == good:
                        self.DeviceList[device_id].low_battery = False
                        self.DeviceList[device_id].notify()

        msg_cnt = int(data[0])
        # If message count is FF then it looks like the first message is valid so decode it (this is experimental)
        #if msg_cnt == 0xFF:
        #    msg_cnt = 1

        if msg_cnt == 255 and (_piu := self.get_partitions_in_use()) is not None:
            log.debug(f"[handle_msgtypeA7]      A7 FF message (partitions), cannot rely on anything in this message for a powermaster with partitions, data={toString(data)}")
            ## I have tried for many hours to make sense of this message data for my PowerMaster 30 panel with 2 partitons set up for testing.
            ## It looked like I had it with the code below and then it gave me ARMED_AWAY and DISARMED messages when they were not commanded, not even within the same hour.
            ## It looks like other messages that come in slow time e.g. low battery, maybe system reset, may be able to be processed.  Need to think about this more!
            ##     i.e. Although when i exit installer on the panel it sends a system reset, how do i know that the panel isn't going to send a random system reset message at any time?
            # Looks like it's parsed differently
            # I cannot process this data until I know what it means:
            #    byte 1 seems to be a counter
            #    byte 2 is Zone --> as per usual, but it doesn't match the B0 data or the panel itself
            #    byte 3 is Type --> as per usual, but it doesn't match the B0 data or the panel itself
            #    byte 4 could be partition
            #    byte 5 -->  00 or 01 or FF.  I'm using FF as validity
            #    byte 6 is this the reason for the Type in byte 3
            #    byte 7 is 03 or 06 or 0C            0011    0110    1100
            #    byte 8 is 00 or 36
            #    byte 9 always 00

            # 23:34:58.595 [handle_msgtypeA7]      A7 FF message (partitions) NOT CURRENTLY PROCESSED IN THIS INTEGRATION - contains data=ff dc 06 01 02 ff 40 03 05 00 43     Zone 6 Partition 2 ALARM_INTERIOR     byte 8 is 1 less than byte 2 i.e. zone
            # 23:34:58.746 [handle_msgtypeA7]      A7 FF message (partitions) NOT CURRENTLY PROCESSED IN THIS INTEGRATION - contains data=ff d8 37 01 01 ff 40 03 36 00 43     Zone 56 Partition 1 ALARM_INTERIOR    byte 8 is 1 less than byte 2 i.e. zone

            #event_device = int(data[2])          # Looks like event zone
            #event_type = int(data[3])          # Looks like an event_type but the timing of when it arrives is all wrong
            #partition = int(data[4])          # Looks like the partition, 0 = all
            #valid = int(data[5]) == 0xFF      # data[5] is 0xFF so process the data, it looks like the rest of the data is valid to process
            #event_reason = int(data[6])

            #log.debug(f"[handle_msgtypeA7]      A7 FF message (partitions) contains data={toString(data)}")
            #self._check_unknown("[handle_msgtypeA7]              A7 Message unknown byte is different to last time", f"handle_msgtypeA7_{msg_cnt}", data[1])
            #if valid:
            #    et = getType(event_reason)
            #    es = getTypeStr(event_reason)
            #    displayEvent("Assuming that it is", event_device, event_type)
            #    log.debug(f"[handle_msgtypeA7]                 May be linked with byte 6: {et.name}        String={es}")
            #    log.debug(f"[handle_msgtypeA7]                 Partition: {partition}")
            #    log.debug(f"[handle_msgtypeA7]                 Counter: {data[1]}")
            #    if partition == 0: # assume all partitions
            #        for p in list(piu):
            #            processEvent(p-1, event_device, event_type)
            #    else:
            #        #processEvent(partition-1, event_device, event_type)
            #        partitionCnt = self._get_panel_capability(IndexName.PARTITIONS)
            #        if partition is not None and partitionCnt > 1:
            #            for j in range(0, partitionCnt):  # max partitions of all panels
            #                if (partition & (1 << j)) != 0:
            #                    processEvent(j, event_device, event_type)
            #else:
            #    displayEvent("Not sure what this represents, is it historical, or perhaps 'live'", event_device, event_type)

        elif msg_cnt == 255: # no partitions
            # Not from any of my panels:
            #  10:40:41.179 [handle_msgtypeA7]      A7 FF message (no partitions) contains,   unknown byte is 0x51  : data=ff 51 61 51 01 00 11 06 00 00 43
            #  10:40:41.480 [handle_msgtypeA7]      A7 FF message (no partitions) contains,   unknown byte is 0x51  : data=ff 51 61 51 01 ff 10 06 00 00 43
            #  10:40:42.722 [handle_msgtypeA7]      A7 FF message (no partitions) contains,   unknown byte is 0x52  : data=ff 52 61 55 01 ff 10 06 00 00 43
            #  10:41:36.070 [handle_msgtypeA7]      A7 FF message (no partitions) contains,   unknown byte is 0x51  : data=ff 51 61 51 01 00 11 06 00 00 43
            #  10:42:31.053 [handle_msgtypeA7]      A7 FF message (no partitions) contains,   unknown byte is 0x51  : data=ff 51 61 51 01 00 11 06 00 00 43
            #  10:43:26.033 [handle_msgtypeA7]      A7 FF message (no partitions) contains,   unknown byte is 0x51  : data=ff 51 61 51 01 00 11 06 00 00 43
            #  10:45:04.772 [handle_msgtypeA7]      A7 FF message (no partitions) contains,   unknown byte is 0x53  : data=ff 53 00 61 00 ff 00 0c 00 00 43

            log.debug(f"[handle_msgtypeA7]      A7 FF message (no partitions) contains,   unknown byte is {hex(int(data[1]))}  : data={toString(data)}")
            self._check_unknown("[handle_msgtypeA7]              A7 Message unknown byte is different to last time", f"handle_msgtypeA7_{msg_cnt}", data[1])

            # The first entry always looks valid, so for now, process it
            event_device = int(data[2])
            event_type = int(data[3])                     # Looks like an event_type but the timing of when it arrives is all wrong

            processEvent(0, event_device, event_type)        # Assume all panel state goes through partition 1

            partition = int(data[4])
            event_reason = int(data[6])
            et = getType(event_reason)
            es = getTypeStr(event_reason)
            log.debug(f"[handle_msgtypeA7]                 May be linked with byte 6: {et.name}        String={es}")
            log.debug(f"[handle_msgtypeA7]                 Partition may be: {partition}")
            log.debug(f"[handle_msgtypeA7]                 Counter: {data[1]}")

            #for i in range(1,4):
            #    event_device = int(data[2 + (2 * i)])
            #    event_type = int(data[3 + (2 * i)])
            #    displayEvent(f"Entry {i} Could be", event_device, event_type)

        elif msg_cnt > 4:
            log.warning(f"[handle_msgtypeA7]      A7 message contains too many messages to process : {msg_cnt}   data={toString(data)}")

        elif self.get_partitions_in_use() is None:   # message count 0 to 4 and we have no partitions so process message data
            # 0d a7 01 00 1f 52 01 ff 00 01 00 00 43 a0 0a
            #             03 00 01 03 08 0e 01 13
            #             03 00 2f 55 2f 1b 00 1c
            self._check_unknown("[handle_msgtypeA7]              A7 Message, count is different to last time", "handle_msgtypeA7_msgCnt", msg_cnt)
            self._check_unknown("[handle_msgtypeA7]              A7 Message unknown byte is different to last time", "handle_msgtypeA7_norm", data[1])
            log.debug(f"[handle_msgtypeA7]      A7 message (no partitions) contains {msg_cnt} messages,   unknown byte is {hex(int(data[1]))}    data={toString(data)}")
            for i in range(msg_cnt):
                device_reference = int(data[2 + (2 * i)])
                event = int(data[3 + (2 * i)])
                displayEvent("Event", device_reference, event)
                event_type = processEvent(0, device_reference, event)        # Assume all panel state goes through partition 0
                device_battery_check(device_reference, event_type, IndexName.KEYFOBS, "Fob", EventType.KEYFOB_LOW_BATTERY_RESTORE, EventType.KEYFOB_LOW_BATTERY, GenericDeviceType.KEYFOB)
                device_battery_check(device_reference, event_type, IndexName.KEYPADS_ONE_WAY, "1Pad", EventType.KEYPAD_LOW_BATTERY_RESTORE, EventType.KEYPAD_LOW_BATTERY, GenericDeviceType.KEYPAD1)
                device_battery_check(device_reference, event_type, IndexName.KEYPADS_TWO_WAY, "2Pad", EventType.KEYPAD_LOW_BATTERY_RESTORE, EventType.KEYPAD_LOW_BATTERY, GenericDeviceType.KEYPAD2)

        else:
            log.warning(f"[handle_msgtypeA7]      DATA NOT PROCESSED AND NEVER SEEN THIS BEFORE. Partitions in use = {self.get_partitions_in_use()} and received a msg_cnt in range 0 to 4, data={toString(data)}")

    def _handle_msgtype_AB(self, data : bytearray) -> bool:  # PowerLink Message
        """MsgType=AB - Panel Powerlink Messages."""
        log.debug(f"[handle_msgtypeAB]  data {toString(data)}")

        # Restart the timer
        self._reset_watchdog_timeout()

        sub_type = data[0]
        if sub_type == 1 and self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.STANDARD_PLUS]:
            # Panel Time
            log.debug("[handle_msgtypeAB] ***************************** Got Panel Time ****************************")

            pt = datetime(2000 + data[7], data[6], data[5], data[4], data[3], data[2]).astimezone()
            log.debug(f"[handle_msgtypeAB]    Panel time is {pt}")
            self._set_time_in_panel(pt)
            self._check_unknown("[handle_msgtypeAB]              AB Message 1 unknown byte is different to last time", f"handle_msgtypeAB_{sub_type}", data[1])

        elif sub_type == 3 and self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.STANDARD_PLUS]:  # keepalive message
            # Example 0D AB 03 00 1E 00 31 2E 31 35 00 00 43 2A 0A
            #               03 00 1e 00 33 33 31 34 00 00 43        From a Powermax+     PanelType=1, Model=33
            self._check_unknown("[handle_msgtypeAB]              AB Message 3 message is different to last time", f"handle_msgtypeAB_{sub_type}", toString(data))

            log.debug("[handle_msgtypeAB] ***************************** Got PowerLink Keep-Alive ****************************")
            # It is possible to receive this between enrolling (when the panel accepts the enrol successfully) and the EPROM download
            #     I suggest we simply ignore it

            self._reset_powerlink_counter() # reset when received keep-alive from the panel

            if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.STANDARD_PLUS]:
                self._reset_keep_alive_messages()
                # A panel connected via the bridge should not be sending AB keep alive messages, but process it just in case!
                self.add_message_to_send_queue(Send.ALIVE if self.ABMessageSupported else Send.PM_KEEPALIVE) # and not self.PowerLinkBridgeConnected
                #self.add_message_to_send_queue (Send.ALIVE)       # The Powerlink module sends this when it gets an i'm alive from the panel.

            if self.PanelMode == AlPanelMode.STANDARD_PLUS:
                log.debug("[handle_msgtypeAB]         Got alive message while Powerlink mode pending, going to full powerlink and calling Restore")
                self.PanelMode = AlPanelMode.POWERLINK  # it is truly in powerlink now we are receiving powerlink alive messages from the panel
                self._trigger_restore_status()        # Clear message buffers and send a Restore (if in Powerlink or standard plus) or Status (not in Powerlink) to the Panel
                #self._dumpAllDevicesToLogFile()

        elif sub_type == 3:  # keepalive message
            log.debug("[handle_msgtypeAB] ***************************** Got PowerLink Keep-Alive ****************************")
            log.debug("[handle_msgtypeAB] ********************* Panel Mode not Powerlink / Standard Plus **********************")
            self.UnexpectedPanelKeepAlive = True
            self._check_unknown("[handle_msgtypeAB]              AB Message 3 message is different to last time", f"handle_msgtypeAB_{sub_type}", toString(data))

        elif sub_type == 5 and self.PanelMode == AlPanelMode.POWERLINK:  # -- phone message
            self._check_unknown("[handle_msgtypeAB]              AB Message 5 message is different to last time", f"handle_msgtypeAB_{sub_type}", toString(data))
            action = data[2]
            if action == 1:
                log.debug("[handle_msgtypeAB] PowerLink Phone: Calling User")
                # pmMessage("Calling user " + pmUserCalling + " (" + pmPhoneNr_t[pmUserCalling] +  ").", 2)
                # pmUserCalling += 1
                # if (pmUserCalling > pmPhoneNr_t) then
                #    pmUserCalling = 1
            elif action == 2:
                log.debug("[handle_msgtypeAB] PowerLink Phone: User Acknowledged")
                # pmMessage("User " .. pmUserCalling .. " acknowledged by phone.", 2)
                # pmUserCalling = 1
            else:
                log.debug(f"[handle_msgtypeAB] PowerLink Phone: Unknown Action {hex(data[1]).upper()}")

        elif sub_type == 10 and data[2] == 0 and self.PanelMode == AlPanelMode.POWERLINK:
            log.debug(f"[handle_msgtypeAB] PowerLink telling us what the code {hex(data[3]).upper()} {hex(data[4]).upper()} is for downloads, currently not used as I'm not certain of this, and never seen it")
            self._check_unknown("[handle_msgtypeAB]              AB Message 5 message is different to last time", f"handle_msgtypeAB_{sub_type}_A", toString(data))

        elif sub_type == 10 and data[2] == 1:
            self._check_unknown("[handle_msgtypeAB]              AB Message 5 message is different to last time", f"handle_msgtypeAB_{sub_type}_B", toString(data))
            if self.PanelMode == AlPanelMode.POWERLINK:
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enrol but not acted on (already in powerlink) **************************")
            elif not self.ForceStandardMode:
                self.PanelWantsToEnrol = True
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enrol **************************")

        return True

    def _handle_msgtype_AC(self, data : bytearray) -> None:  # PowerLink Message
        """MsgType=AC - ???"""
        log.debug(f"[handle_msgtypeAC]  data {toString(data)}")
        self._check_unknown("[handle_msgtypeAC]              AC Message is different to last time", "handle_msgtypeAC", toString(data))

    def _handle_msgtype_AD(self, data : bytearray) -> None:  # PowerLink Message
        """MsgType=AD - Panel Powerlink Messages."""
        log.debug(f"[handle_msgtypeAD]  data {toString(data)}")
        #if data[2] == 0x00: # the request was accepted by the panel
        #    if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
        #        log.debug(f"[handle_msgtypeAD]      adding Image FB to send list")
        #        self.add_message_to_send_queue(Send.IMAGE_FB)

    # Only Powermasters send this message
    def _handle_msgtype_B0(self, data : bytearray) -> None:  # PowerMaster Message
        """MsgType=B0 - Panel PowerMaster Message."""
        # Only Powermasters send this message
        # Format: <Type> <SubType> <Length of Data and Counter> <Data> <Counter> <0x43>

        def chunkme(data: bytearray) -> list[Chunky]:
            data_len = len(data)
            msg_type = data[0]
            sub_type = data[1]
            # Check validity of data chunk (it could be valid and have no chunks)
            current = 3
            sequence: int | None = None
            overall_length = data[2]
            retval = []
            mv = memoryview(data)

            while current < data_len - 3 and (data[current] == 0xFF or msg_type == 2):
                sequence = data[current]
                datasize: int = data[current+1]
                index: int = data[current+2]
                length = data[current+3]
                payload_start: int = current + 4
                payload_end: int = payload_start + length
                if payload_end > data_len:
                    break
                payload = mv[payload_start : payload_end]
                retval.append(
                    Chunky(
                        type = msg_type,
                        subtype = sub_type,
                        sequence = sequence,
                        datasize = datasize,
                        index = index,
                        length = length,
                        data = payload,
                    )
                )

                current += length + 4

            if current-2 == overall_length:
                return retval
            ctrl = hexify(sequence) if sequence is not None else "N/A"
            log.debug(f"[handle_msgtypeB0] *******************"
                    f"Message not fully processed for {msg_type}   "
                    f"{overall_length - (current-2)} bytes not processed     "
                    f"control byte = {ctrl}    "
                    f"data is {toString(data[current:])} "
                    f"********************************************************")
            return []

        def isitchunky(data: bytearray) -> bool:
            """Return True if data contains one or more chunks."""
            return bool(chunkme(data))

        # A powermaster mainly interacts with B0 messages so reset watchdog on receipt
        self._reset_watchdog_timeout()

        # Include B0 messages to reset the im alive counter. PowerMax panels seem to be OK, but PowerMaster fail to get the i'm alive message in time
        self._reset_powerlink_counter() # reset when received keep-alive from the panel

        msg_type = data[0]
        sub_type = data[1]
        msg_length = data[2]
        #seq_type = SEQUENCE(msg_type) if msg_type in SEQUENCE else SEQUENCE.UNDEFINED
        #
        #if seq_type == SEQUENCE.SUB:
        #    log.debug(f"[handle_msgtypeB0] Queue it")
        #    return

        # The data <Length> value is 4 bytes less then the length of the data block (as the <MessageCounter> is part of the data count)
        if len(data) != msg_length + 4:
            log.debug(f"[handle_msgtypeB0]              Invalid Length, {notknown} not processing")
            # Do not process this B0 message as it seems to be incorrect
            return

        if sub_type in self.B0_Waiting:
            self.B0_Waiting.remove(sub_type)

        if OBFUS:
            log.debug(f"[handle_msgtypeB0] Received {self.PanelModel or "UNKNOWN_PANEL_MODEL"} message {hexify(msg_type):>02}/{hexify(sub_type):>02} (len = {msg_length})    data = <OBFUSCATED>")
        else:
            log.debug(f"[handle_msgtypeB0] Received {self.PanelModel or "UNKNOWN_PANEL_MODEL"} message {hexify(msg_type):>02}/{hexify(sub_type):>02} (len = {msg_length})    data = {toString(data)}")

        msg_info = pmSendMsgB0_reverseLookup.get(sub_type)

        log.debug(f"[handle_msgtypeB0]    msg_info: {'unknown' if msg_info is None else msg_info}")

        if msg_info is None:
            # Message unknown
            log.debug(f"[handle_msgtypeB0]             Message {notknown} {msg_type=} {sub_type=} not known about, chunky={isitchunky(data[:-2])}.   data = {toString(data)}")

        elif msg_info.chunky:
            # Process the messages that we know about and we believe are chunked
            chunks = chunkme(data[:-2]) # exclude b0 counter and Packet.POWERLINK_TERMINAL at the end
            if len(chunks) == 0:
                log.debug(f"[handle_msgtypeB0] ******************************************************** Message not chunky (we thought it was) and not processed further ************************************************* data = {toString(data)}")
            else:
                for chunk in chunks:
                    log.debug(f"[handle_msgtypeB0]       {toString(data[:2])}     Decode Chunk: {chunk}")
                    # Check the PanelSettings to see if there's one that refers to this message chunk
                    for key, value in pmPanelSettingCodes.items():
                        if value.PMasterB0Mess is not None and value.PMasterB0Mess in pmSendMsgB0 and pmSendMsgB0[value.PMasterB0Mess].data == sub_type and value.PMasterB0Index == chunk.index:
                            self._update_panel_setting(key = key, length = chunk.length, datasize = chunk.datasize, data = chunk.data, display = True, msg = f"{sub_type=}")
                            break
                    self.process_chunk(chunk)

        elif sub_type == pmSendMsgB0[B0SubType.INVALID_COMMAND].data: # msg_info.data == "INVALID_COMMAND":
            log.debug(f"[handle_msgtypeB0]             The Panel Indicates a B0 INVALID_COMMAND sent to the panel:   data={toString(data)}")
            if msg_length % 2 == 0: # msg_length is an even number
                for i in range(0, msg_length, 2):
                    command = data[3+i]
                    message = data[4+i]
                    log.debug(f"[handle_msgtypeB0]                     The Panel Indicates {hexify(command):0>2} {hexify(message):0>2}")
                    if command == 0x0D:                             # I think this is "retry later" instruction from the panel (and if it isn't then we can still ask for the message again)
                        if message in pmSendMsgB0_reverseLookup:    # Make sure that were asking for a message that we know about
                            self.B0_Wanted.add(message)
                        else:
                            log.debug(f"[handle_msgtypeB0]                            Unknown Message type for 'retry later' {hexify(message):0>2} so not asking for it")
                    elif command == 0x02:
                        self.gotBeeZeroInvalidCommand = True

        elif sub_type == pmSendMsgB0[B0SubType.PANEL_STATE_2].data and msg_length == 15: #  I've only seen a message length of 15 with all 3 partitions populated
            # Panel State (without zone data and not chunky)
            # 03 0f 0f 07 08 0f 00 00 00 43 03 00 87 00 87 00 07 24 43
            log.debug(f"[handle_msgtypeB0]             Panel State short (15) has been provided data={toString(data)}")
            # Check to make sure its not chunky
            #isitchunky(data[:-2])
            # process the data
            if not isitchunky(data[:-2]):                      # Check to make sure its not chunky
                for i in range(data[10]):                      # data[10] has the total supported partitions and not just the ones in use
                    offset = i * 2
                    # Repeat 2 bytes (11 to 12) for more than 1 partition.  Message length is 15 so we do not need to check the length.
                    self._updatePartitionStatus(i, data[offset + 11], data[offset + 12], 0, 0)
            else:
                log.debug(f"[handle_msgtypeB0]             The message is chunky so I don't know how to process it:  data={toString(data)}")

        elif sub_type == pmSendMsgB0[B0SubType.PANEL_STATE_2].data and msg_length == 11: #  This is a test, I've only seen a message length of 15 with all 3 partitions populated
            # Panel State (without zone data and not chunky)
            log.debug(f"[handle_msgtypeB0]             Panel State short (11) has been provided data={toString(data)}")
            # Check to make sure its not chunky
            #isitchunky(data[:-2])
            if not isitchunky(data[:-2]):                      # Check to make sure its not chunky
                # process the data, assume 1 partition
                self._updatePartitionStatus(0, data[11], data[12], 0, 0)
            else:
                log.debug(f"[handle_msgtypeB0]             The message is chunky so I don't know how to process it:  data={toString(data)}")

        else:
            # Process the messages that we know about and are not chunked
            log.debug(f"[handle_msgtypeB0]             Message {msg_info.data} known about but not chunky and not currently processed data={toString(data)}")
            if msg_info.data in self.B0_temp and self.B0_temp[msg_info.data] != data:
                log.debug("[handle_msgtypeB0]                 and its different to last time")
            self.B0_temp[msg_info.data] = data
        #log.debug(f"[handle_msgtypeB0] ******************************************************** Leaving *************************************************")

    def _handle_msgtype_C0(self, _data : bytearray) -> None:  # Redirected Powerlink Data
        log.debug("[handle_msgtypeC0] ******************************************************** Should not be here *************************************************")

    def _handle_msgtype_E0(self, data : bytearray) -> None:  # Visonic Proxy
        # 0d e0 <no of alarm clients connected> <no of visonic clients connected> <no of monitor clients connected> <if in proxy mode> <if in stealth mode> 43 <checksum> 0a
        log.warning('[handle_msgtypeE0]  Visonic Proxy Status   '
                  f'Alarm: {"Connected" if data[0] == 1 else "Disconnected"}    '
                  f'Visonic: {"Connected" if data[1] == 1 else "Disconnected"}    '
                  f'HA: {"Connected" if data[2] == 1 else "Disconnected"}    '
                  f'Proxy: {"Yes" if data[3] == 1 else "No"}    '
                  f'Stealth: {"Yes" if data[4] == 1 else "No"}    '
                  f'Download: {"Yes" if data[5] == 1 else "No"}' )
        if self.ForceStandardMode:
            log.debug("[handle_msgtypeE0]  Visonic Proxy Not Being Used as Currently forced in Standard Mode")
        else:
            self.PowerLinkBridgeConnected = True
            self.PowerLinkBridgeAlarm = data[0] != 0
            self.PowerLinkBridgeProxy = data[3] != 0
            self.PowerLinkBridgeStealth = data[4] != 0

    def _handle_msgtype_E1(self, _data : bytearray) -> None:  # Visonic Proxy Command Ringback
        """MsgType=E1 - Visonic Proxy Command Ringback."""
        log.info("Integration has received a proxy command ringback, this indicates that Rx and Tx are incorrectly connected. Are you testing Ringback?")

    def _handle_msgtype_F4(self, data : bytearray) -> bool:  # Static JPG Image
        """MsgType=F4 - Static JPG Image."""

        def send_f4_07(zone: int, unique_id: int, image_id: int, status: int):
            # The f4 07 messages need to be sent to the panel to inform it that we have received the image OK or not.
            #      status=0 for success, status=1 for failure, asking the panel to resent the image
            _body = f'f4 07 00 01 04 {zone:>02} {hexify(unique_id):>02} {hexify(image_id):>02} {status:>02}'
            _c1, _c2 = self.f4_checksum(convert_bytearray(_body))
            self.add_message_to_send_queue(convert_bytearray(f'0d {_body} {_c1:02x} {_c2:02x} 0a'))

        def send_f4_10(zone: int, unique_id: int, image_id: int):
            # The f4 10 messages tell the panel what to do next, send the next image or stop sending image data
            # Assume that we are managing the interaction/protocol with the panel
            _body = f'f4 10 00 01 04 00 {zone:>02} {hexify(unique_id):>02} {hexify(image_id):>02}'
            _c1, _c2 = self.f4_checksum(convert_bytearray(_body))
            self.add_message_to_send_queue(convert_bytearray(f'0d {_body} {_c1:02x} {_c2:02x} 0a'))


        #log.debug(f"[handle_msgtypeF4]  data {toString(data)}")

        #      0 - message type  ==>  3=start, 5=data
        #      1 - always 0
        #      2 - sequence
        #      3 - data length
        msgtype = data[0]
        sequence = data[2]
        datalen = data[3]

        pushchange = False

        if self.PanelMode not in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
            log.debug(f"[handle_msgtypeF4] PanelMode is {self.PanelMode} so not processing F4 data")
            if not self.ignoreF4DataMessages:
                _izc, ir = self.image_manager.getCurrentImageRecord()
                if ir is not None:
                    zone = ir.zone
                elif msgtype == 0x03:
                    zone = (10 * int(data[5] // 16)) + (data[5] % 16)
                else:
                    zone = 0
                self.send_panel_update(AlCondition.IMAGE_UPDATE, {"finished": True, "state": ABORTED, "zone": zone, "message": "invalid panel mode"})
            self.image_manager.stop()
            self.ignoreF4DataMessages = True

        elif msgtype == 0x03:     # JPG Header
            log.debug(f"[handle_msgtypeF4]  data {toString(data)}")
            pushchange = True
            zone = (10 * int(data[5] // 16)) + (data[5] % 16)         # the // does integer floor division so always rounds down
            unique_id = data[6]
            image_id = data[7]
            lastimage = data[11] == 1
            size = (data[13] * 256) + data[12]
            totalimages = data[14]                    # 0xFF in the first header of a capture, the real total after that
            crc = (data[15], data[16])                # CRC-16 of the finished image, low byte first

            if self.image_manager.isImageDataInProgress():
                # A new header arrived while the previous image was still part built, so that one
                # is lost. Drop just that image and carry on with this header: binning the whole
                # capture and locking the zone out until a lastimage happens to arrive costs far
                # more than the single frame actually lost.
                log.warning(f"[handle_msgtypeF4]        Previous image incomplete, dropping it and continuing with image {image_id} for zone {zone}")
                izc, _ = self.image_manager.getCurrentImageRecord()
                izc.degraded = True
                self.image_manager.reset_current()
                self.send_panel_update(AlCondition.IMAGE_UPDATE, {"finished": False, "state": DEGRADED, "zone": zone, "message": "previous image incomplete, ignoring it and continuing"})

            if zone in self.image_ignore:
                log.debug(f"[handle_msgtypeF4]        Ignoring Image Header, so not processing F4 data.      zone = {zone}    size = {size}    unique_id = {hex(unique_id)}    image_id = {image_id}     lastimage = {lastimage}    totalimages = {totalimages}")
                if lastimage:
                    self.image_ignore.remove(zone)

            elif zone - 1 in self.SensorList:
                log.debug("[handle_msgtypeF4]        Processing Image Header data")
                # Initialise the receipt of an image in the ImageManager
                success = self.image_manager.setCurrent(zone = zone, unique_id = unique_id, image_id = image_id, size = size, sequence = sequence, lastimage = lastimage, totalimages = totalimages, crc = crc)
                # Assume that we are managing the interaction/protocol with the panel
                self.ignoreF4DataMessages = not success

            else:
                log.debug(f"[handle_msgtypeF4]        Panel sending image for Zone {zone} but it does not exist or is not a CAMERA")

        elif msgtype == 0x05:   # JPG Data
            if self.ignoreF4DataMessages:
                log.debug("[handle_msgtypeF4]        Not processing F4 0x05 data")

            elif self.image_manager.hasStartedSequence():
                # Image receipt has been initialised by self.image_manager.setCurrent
                datastart = 4
                is_in_sequence = self.image_manager.addData(data[datastart:datastart+datalen], sequence)
                if is_in_sequence:
                    if self.image_manager.isImageComplete():
                        izc, ir = self.image_manager.getLastImageRecord()
                        log.debug(f"[handle_msgtypeF4]        Image Complete       Current Data     zone={ir.zone}    unique_id={hex(izc.unique_id)}    image_id={ir.image_id}    total_images={izc.totalimages}    lastimage={ir.lastimage}")
                        pushchange = True

                        # The F4-03 header carries a CRC-16 of the finished image, so a damaged one
                        # can be spotted and asked for again. Give up after MAX_IMAGE_ATTEMPTS and
                        # let the capture carry on: nine good frames beat hanging on a bad fifth.
                        attempt = self.image_manager.note_attempt(ir.zone, ir.image_id)
                        if not ir.isChecksumValid():
                            if self.image_manager.attempts_left(ir.zone, ir.image_id):
                                log.debug(f"[handle_msgtypeF4]        Image checksum wrong for zone {ir.zone} image {ir.image_id} (attempt {attempt}), asking for it again")
                                self.send_panel_update(AlCondition.IMAGE_UPDATE, {"finished": False, "state": DELAYED, "zone": ir.zone, "message": f"image checksum wrong for image {ir.image_id} (attempt {attempt}), asking for it again"})
                                self.image_manager.discard_last()
                                send_f4_07(ir.zone, izc.unique_id, ir.image_id, IMAGE_BAD)
                                send_f4_10(ir.zone, izc.unique_id, ir.image_id)
                                return pushchange
                            # Out of attempts. The audio is kept even when it is bad, because it is
                            # also the end-of-capture marker: dropping it means the clip is never
                            # rendered and the user gets loose stills and no video. A glitch in a
                            # few hundred ms of sound is the lesser problem. A bad JPEG has no such
                            # second job, so that one is dropped.
                            if not _is_capture_audio(ir):
                                log.warning(f"[handle_msgtypeF4]        Image checksum still wrong for zone {ir.zone} image {ir.image_id} after {attempt} attempts, skipping it")
                                self.image_manager.discard_last()
                                send_f4_07(ir.zone, izc.unique_id, ir.image_id, IMAGE_GOOD)   # accept it so the panel moves on
                                send_f4_10(ir.zone, izc.unique_id, ir.image_id)
                                if ir.lastimage:
                                    self.image_manager.stop()
                                    self.send_panel_update(AlCondition.IMAGE_UPDATE, {"finished": True, "state": FAILED, "zone": ir.zone, "message": "image checksum wrong, stopping image retrieval"})
                                else:
                                    izc.degraded = True
                                    self.send_panel_update(AlCondition.IMAGE_UPDATE, {"finished": False, "state": DEGRADED, "zone": ir.zone, "message": f"image checksum wrong for image {ir.image_id} after {attempt} attempts, skipping it"})
                                return pushchange
                            log.warning(f"[handle_msgtypeF4]        Audio checksum still wrong for zone {ir.zone} after {attempt} attempts, keeping it so the capture still renders")
                            self.send_panel_update(AlCondition.IMAGE_UPDATE, {"finished": False, "state": DEGRADED, "zone": ir.zone, "message": f"audio checksum still wrong after {attempt} attempts, keeping it so the capture still renders"})
                            izc.degraded = True

                        #self.add_message_to_send_queue(Send.IMAGE_FB)

                        # get time now to store image
                        t = get_local_time()

                        # The panel sends 11 "images" per capture: 1 to 10 are JPEG frames, and the 11th
                        # (marked as image 0) is not an image at all, it is the capture's audio - a RIFF/WAVE
                        # clip, IMA ADPCM mono 8kHz. It used to be logged as a corrupt image because it was
                        # handed to PIL. Identify it up front instead so it can be kept.
                        is_audio = _is_capture_audio(ir)
                        # Assume a corrupt image
                        width = 100000
                        height = 100000
                        if is_audio:
                            log.debug(f"[handle_msgtypeF4]           Got Audio clip for sensor {ir.zone}, {len(ir.buffer)} bytes (RIFF/WAVE)")
                        elif ir.buffer is not None:
                            # Get the width and height of the image. I assume that if PIL can't load the image then it is corrupt.
                            try:
                                img = Image.open(io.BytesIO(ir.buffer))
                                width, height = img.size
                            except Exception as ex:
                                tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                                log.debug("[handle_msgtypeF4] Image Processing, caused an exception\n%s", tb_str)
                                self.send_panel_update(AlCondition.IMAGE_UPDATE, {"finished": False, "state": DEGRADED, "zone": ir.zone, "message": "image processing, caused an exception but continuing"})
                                izc.degraded = True

                            log.debug(f"[handle_msgtypeF4]           Got Image width {width}    height {height}")

                        # Got all the data so write it out to a jpg file
                        #fn = f"camera_image_z{ir.zone:0>2}_{t.day:0>2}{t.month:0>2}{t.year - 2000:0>2}_{t.hour:0>2}{t.minute:0>2}{t.second:0>2}.jpg"
                        #with open(fn, 'wb') as f1:
                        #    f1.write(buffer)
                        #    f1.close()

                        if ir.zone - 1 in self.SensorList and (is_audio or (width <= 1024 and height <= 768)):
                            log.debug(f"[handle_msgtypeF4]           Saving {'Audio' if is_audio else 'Image'} for sensor {ir.zone}")
                            self.SensorList[ir.zone - 1].jpg_data = ir.buffer
                            self.SensorList[ir.zone - 1].jpg_is_audio = is_audio
                            self.SensorList[ir.zone - 1].jpg_timestamp = t
                            self.SensorList[ir.zone - 1].has_jpg = True
                            self.SensorList[ir.zone - 1].notify(AlSensorCondition.CAMERA)

                        # An external bridge (e.g. an ESP32 stream-server built with F4-ack support) can
                        # answer the panel's image acks directly over the serial link, ~3ms after the last
                        # data chunk. When that is in use, HA must NOT also ack: two uncoordinated writers on
                        # the same UART TX collide. Drop a file named 'visonic_no_ha_f4_ack' in the HA config
                        # dir to offload F4 acking to the bridge. (Note: the wifi round-trip vs a local UART
                        # ack was measured to make no difference to the panel's residual resends -- both drive
                        # the panel through the sequence equally; resends are panel-side link/state behaviour.)
                        _offload_f4_ack = os.path.exists("/config/visonic_no_ha_f4_ack")
                        if not _offload_f4_ack:
                            send_f4_07(ir.zone, izc.unique_id, ir.image_id, IMAGE_GOOD)
                            send_f4_10(ir.zone, izc.unique_id, ir.image_id)

                        if ir.lastimage:
                            # Tell the panel we received that one OK, we're ready for the next
                            log.debug("[handle_msgtypeF4]         Finished everything so stopping as we've just received the last image")
                            if izc.degraded:
                                self.send_panel_update(AlCondition.IMAGE_UPDATE, {"finished": True, "state": DEGRADED, "zone": ir.zone, "message": "transfer complete but degraded"})
                            else:
                                self.send_panel_update(AlCondition.IMAGE_UPDATE, {"finished": True, "state": SUCCESS, "zone": ir.zone, "message": "transfer complete"})
                            self.image_manager.stop()

                else:
                    # Received an F4-05 data message out of sequence, get the current image data
                    izc, ir = self.image_manager.getCurrentImageRecord()
                    if izc is not None:
                        # Ask for same image again
                        log.debug(f"[handle_msgtypeF4]         Message out of sequence, requesting resend of zone {ir.zone}, image {ir.image_id}")
                        send_f4_07(ir.zone, izc.unique_id, ir.image_id, IMAGE_BAD)
                        send_f4_10(ir.zone, izc.unique_id, ir.image_id)
                        self.image_manager.reset_current()
                    else:
                        log.debug("[handle_msgtypeF4]         Message out of sequence, dumping all data")
                        self.send_panel_update(AlCondition.IMAGE_UPDATE, {"finished": True, "state": FAILED, "zone": ir.zone, "message": "image processing out of sequence, stopping image retrieval"})
                        self.image_manager.stop()

        elif msgtype == 0x01:
            log.debug(f"[handle_msgtypeF4]  data {toString(data)}")
            log.debug("[handle_msgtypeF4]           Message Type not processed")
            pushchange = True

        else:
            log.debug(f"[handle_msgtypeF4]  not seen data {toString(data)}")
            log.debug("[handle_msgtypeF4]           Message Type not processed")

        return pushchange
