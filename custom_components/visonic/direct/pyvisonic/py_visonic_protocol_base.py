"""Visonic Protocol Base."""

# ruff: noqa: G004, C901, BLE001, FURB171

from abc import abstractmethod
import asyncio
from collections.abc import Callable
import copy
from datetime import datetime, timedelta
import inspect
import logging
import traceback
from typing import Any

from .py_abstract_classes import AlPanelInterface
from .py_checksum import MyChecksumCalc
from .py_const import (
    CRC_ERROR_PERIOD,
    DEFAULT_DL_CODE,
    LIBRARY_VERSION,
    MAX_CRC_ERROR,
    MAX_PARTITIONS,
    OBFUS,
    PACKET_MAX_SIZE,
    SAME_PACKET_ERROR,
    DebugLevel,
)
from .py_enum import (
    AlCondition,
    AlPanelMode,
    AlTerminationType,
    MessagePriority,
    Packet,
    Receive,
    Send,
)
from .py_generic_device import AlGenericDeviceHelper
from .py_partition_state import PartitionStateClass
from .py_sensor import AlSensorDeviceHelper
from .py_switch import AlSwitchDeviceHelper
from .py_types import AlPanelEventData
from .py_types_receiving import ChecksumType, PanelCallBack, pmReceiveMsg
from .py_types_sending import PriorityQueueWithPeek, VisonicListEntry, pmSendMsg
from .py_utils import get_local_time, get_utc_time, hexify, toString

log = logging.getLogger(__name__)


class vloggerclass:
    """Virtual Logger Class that adds file name, line number and function name to log messages."""
    def __init__(self, loggy, panel_id: int = -1, detail: bool = False) -> None:
        """Initialize the vloggerclass."""
        self.detail = detail
        self.loggy = loggy
        if panel_id is not None and panel_id >= 0:
            self.panel_id_str = f"P{panel_id} "
        else:
            self.panel_id_str = ""

    def _createPrefix(self) -> str:
        cf = inspect.currentframe()
        if cf is None:
            return ""
        previous_frame = cf.f_back.f_back if cf.f_back is not None else cf.f_back
        if previous_frame is None:
            return ""
        (
            _filepath,
            line_number,
            function,
            _lines,
            _index,
        ) = inspect.getframeinfo(previous_frame)
        #filename = filepath[filepath.rfind('/')+1:]
        return f"{line_number:<5} " + (f"{function:<30} " if self.detail else "")

    def debug(self, msg, *args, **kwargs):
        """Debug log message with file name, line number and function name."""
        try:
            prefix = self.panel_id_str + self._createPrefix()
            self.loggy.debug("%s%s", prefix, msg, *args, **kwargs)
        except TypeError as ex:
            self.loggy.error("[vloggerclass] Formatting TypeError: %s", ex)

    def info(self, msg, *args, **kwargs):
        """Info log message with file name, line number and function name."""
        try:
            prefix = self.panel_id_str + self._createPrefix()
            self.loggy.info("%s%s", prefix, msg, *args, **kwargs)
        except TypeError as ex:
            self.loggy.error("[vloggerclass] Formatting TypeError: %s", ex)

    def warning(self, msg, *args, **kwargs):
        """Warning log message with file name, line number and function name."""
        try:
            prefix = self.panel_id_str + self._createPrefix()
            self.loggy.warning("%s%s", prefix, msg, *args, **kwargs)
        except TypeError as ex:
            self.loggy.error("[vloggerclass] Formatting TypeError: %s", ex)

    def error(self, msg, *args, **kwargs):
        """Error log message with file name, line number and function name."""
        try:
            prefix = self.panel_id_str + self._createPrefix()
            self.loggy.error("%s%s", prefix, msg, *args, **kwargs)
        except TypeError as ex:
            self.loggy.error("[vloggerclass] Formatting TypeError: %s", ex)

#log = vloggerclass(mylog, 0, False)

# This class handles the detailed low level interface to the panel.
#    It sends the messages
#    It builds and received messages from the raw byte stream and coordinates the acknowledges back to the panel
#    It checks CRC for received messages and creates CRC for sent messages
#    It coordinates the downloading of the EPROM (but doesn't decode the data here)
#    It manages the communication connection
class ProtocolBase(AlPanelInterface, MyChecksumCalc):
    """Manage low level Visonic protocol."""

    log.debug(f"Initialising Protocol - Protocol Version {LIBRARY_VERSION}")

    def __init__(self, loop, force_standard_mode : bool, disable_all_commands : bool, download_code : str, user_code_slot: int, logger = None) -> None:
        """Initialize class."""
        super().__init__(logger = logger)

        ####################################
        # Variables that do not get reset  #
        ####################################

        if loop:
            self.loop = loop
            log.debug("Establishing Protocol - Using Home Assistant Loop")
        else:
            self.loop = asyncio.get_event_loop()
            log.debug("Establishing Protocol - Using Asyncio Event Loop")

        if logger is not None:
            self.log = logger

        #self.log = vloggerclass(panel_id=panel_id)
        self.suspendAllOperations = False

        # install the packet callback handler
        self._packet_callback = self._processReceivedPacket

        # Set these from the panel config dictionary (that may not have all settings in)
        self.ForceStandardMode : bool = force_standard_mode        # INTERFACE : Get user variable from HA to force standard mode or try for PowerLink
        self.DisableAllCommands : bool = disable_all_commands       # INTERFACE : Get user variable from HA to allow or disable all commands to the panel

        self.DownloadCodeUserSet: bool = False
        self.DownloadCode = DEFAULT_DL_CODE
        if isinstance(download_code, str) and len(download_code) == 4:
            self.DownloadCode = download_code
            if not OBFUS:
                log.debug(f"[Settings] Download Code set by user to {self.DownloadCode}")
            self.DownloadCodeUserSet = True

        self.user_code_slot: int = int(user_code_slot) if isinstance(user_code_slot, (int, float)) else 1

        # If disable all commands then force standard is set to True
        if self.DisableAllCommands:
            self.ForceStandardMode = True

        # By the time we get here there are 3 combinations of self.DisableAllCommands and self.ForceStandardMode
        #     Both are False --> Try to get to Powerlink
        #     self.ForceStandardMode is True --> Force Standard Mode, the panel can still be armed and disarmed
        #     self.ForceStandardMode and self.DisableAllCommands are True --> The integration interacts with the panel but commands such as arm/disarm/log/bypass are not allowed
        # The if statement above ensure these are the only supported combinations.
        log.debug(f"[Settings] ForceStandard = {self.ForceStandardMode}     DisableAllCommands = {self.DisableAllCommands}")

    @abstractmethod
    def _processReceivedPacket(self, packet : bytearray, processAB : bool, processNormalData : bool, processB0 : bool, processDownload : bool) -> bool:
        """Process a received packet."""
        # Forward reference the packet handler function as abstract, it must be implemented

    def _reset_full(self):
        """Reset all non-permanent variables."""
        # set the event callback handlers to None
        self.onPanelChangeHandler: Callable[[AlCondition, dict | None], None] | None = None
        self.onNewSensorHandler: Callable[[bool, AlSensorDeviceHelper], None] | None = None
        self.onNewSwitchHandler: Callable[[bool, AlSwitchDeviceHelper], None] | None = None
        self.onNewDeviceHandler: Callable[[bool, AlGenericDeviceHelper], None] | None = None
        self.onProblemHandler: Callable[[Exception | str | None], None] | None = None
        self.onPanelLogHandler: Callable[..., None] | None = None
        # Global Variables that define the overall panel status
        self.PanelMode: AlPanelMode = AlPanelMode.UNKNOWN
        # A queue of messages to send (i.e. VisonicListEntry)
        # The SendQueue is set up as a PriorityQueue and needs a < function implementing in VisonicListEntry based on time, oldest < newest
        # By doing this it's like having many queues in one, each one date ordered oldest first
        # 0 < 1 so 0 is the high priority queue
        # So when get is called it looks at the highest priority queue first and if nothing then looks at the next priority queue etc
        # So urgent tagged messages get sent to the panel asap, like arm, disarm etc
        self._send_queue = PriorityQueueWithPeek()
        # Timestamp of the last received data from the panel. If this remains set to none then we have a comms problem
        #   Override to None from what _reset_connection sets it to
        self._last_recv_time_panel_data : datetime | None = None
        self._first_cmd_sent : bool = False
        self._last_packet : bytearray | None = None
        self._last_packet_counter : int = 0
        self._reset_watchdog_timeout()
        self.pmDownloadMode = False

    def _reset_connection(self):
        """Reset the variables needed to make a new connection."""
        self.PanelMode = AlPanelMode.STARTING
        # Whether its a powermax or powermaster
        self.PowerMaster : bool | None = None                    # Set to None to represent unknown until we know True or False
        # Define model type to be unknown

        # partition related data
        self.PartitionState: list[PartitionStateClass] = [PartitionStateClass(self.loop) for _ in range(MAX_PARTITIONS)]
        self.partitionsEnabled = False
        self.PartitionsInUse :set[int] = {0}           # this is a set so no repetitions allowed

        self.EnableB0ReceiveProcessing : bool = False
        self.lastPanelEvent = None
        self.panelEventData : list[AlPanelEventData] = []
        self._last_recv_time_panel_data = get_utc_time()    # Do not set to None
        self._crc_error_count = 0              # The CRC Error Count for Received Messages
        # This is the time stamp of the CRC error
        self._first_crc_error_time = get_utc_time() - timedelta(seconds=1)    # take off 1 second so the first command goes through immediately
        # This is the time stamp of the last Send
        self._reset_message_data()

    def _shutdown(self):
        """Shutdown the connection to the panel."""
        # Set that the transport connection to the panel is invalid.
        for p in self.PartitionState:
            p.shutdownOperation()
        self.PanelMode = AlPanelMode.STOPPED

    # This is used for debugging from command line
    def setLogger(self, loggy):
        """Set the logger."""
        self.log = loggy

    def send_panel_event_data(self) -> bool:
        """Send panel event data."""
        retval = False
        for ped in self.panelEventData:
            retval = True
            a = ped.as_dict()
            #log.debug(f"[PanelUpdate] ped = {ped}  event data = {a}")
            self.send_panel_update(AlCondition.PANEL_UPDATE, a)
        self.panelEventData = [] # empty the list
        return retval

    def add_panel_event_data(self, ped: AlPanelEventData):
        """Add panel event data."""
        if self.lastPanelEvent is not None and self.lastPanelEvent == ped:
            # log.info(f"[add_panel_event_data] Not adding {ped} as matches last time {self.lastPanelEvent}")
            return
        #log.debug(f"[add_panel_event_data] {ped}")
        ped.time = get_local_time()
        self.panelEventData.append(ped)
        self.lastPanelEvent = copy.deepcopy(ped)

    def send_panel_update(self, ev: AlCondition, d: dict[str, Any] | None = None):
        """Send panel update."""
        if self.onPanelChangeHandler is not None:
            self.onPanelChangeHandler(ev, d)

    # Set the on_problem callback handlers
    def on_problem(self, fn: Callable[..., None]):             # on_problem ( exception or string or None )
        """On Problem Callback Setter."""
        self.onProblemHandler = fn

    # Set the on_new_sensor callback handlers
    def on_new_sensor(self, fn: Callable[..., None]):             # on_new_sensor ( device: AlSensorDevice )
        """On New Sensor Callback Setter."""
        self.onNewSensorHandler = fn

    # Set the on_new_switch callback handlers
    def on_new_switch(self, fn: Callable[..., None]):             # on_new_switch ( sensor: AlSwitchDevice )
        """On New Switch Callback Setter."""
        self.onNewSwitchHandler = fn

    # Set the on_new_sensor callback handlers
    def on_new_device(self, fn: Callable[..., None]):             # on_new_device ( device: AlGenericDevice )
        """On New Device Callback Setter."""
        self.onNewDeviceHandler = fn

    # Set the on_panel_event_log callback handlers
    def on_panel_event_log(self, fn: Callable[..., None]):
        """On Panel Log Callback Setter."""
        self.onPanelLogHandler = fn

    # Set the onPanelEvent callback handlers
    def on_panel_change(self, fn: Callable[..., None]):             # on_panel_change ( datadictionary: dict )
        """On Panel Change Callback Setter."""
        self.onPanelChangeHandler = fn

    def is_power_master(self) -> bool:
        """Are we connected to a PowerMaster panel?"""
        return self.PowerMaster is not None and self.PowerMaster # PowerMaster models

    # when the connection has problems then call the on_problem when available
    def _report_problem(self, termination : AlTerminationType):
        """Log when connection is closed, if needed call callback."""
        if self.suspendAllOperations:
            log.debug("[_report_problem] Operations Already Suspended. Please recreate connection")
            return
        log.debug(f"[_report_problem] Problem due to {termination.name}")
        # Set mode to Stopped just in case the handler uses it, leave all other variables as they are
        self.PanelMode = AlPanelMode.STOPPED
        if self.onProblemHandler:
            self.onProblemHandler(termination)

    def _is_send_queue_empty(self, priority : MessagePriority | None = None) -> bool:
        """Is the send message queue empty (at the given priority level)."""
        if priority is None or self._send_queue.empty():
            return self._send_queue.empty()
        # Here when the queue is not empty and priority is set to something
        item_priority, _ = self._send_queue.peek_nowait()
        return item_priority > priority

    def _on_main_loop(self) -> bool:
        """Are we on the main event loop or a sync loop."""
        try:
            return asyncio.get_running_loop() is self.loop
        except RuntimeError:
            return False

    def _send_queue_put_nowait(self, item : tuple[int, VisonicListEntry]):
        if self._on_main_loop():
            self._send_queue.put_nowait(item)
        else:
            self.loop.call_soon_threadsafe(self._send_queue.put_nowait, item)

    # Clear the send queue and reset the associated parameters
    def _empty_send_queue(self, priority : MessagePriority):
        """Clear the List by priority level, preventing any retry causing issue."""
        ############## This must be executed on the main loop ###############
        #log.debug(f"[_empty_send_queue]    enter {self._send_queue.qsize()}")
        other = PriorityQueueWithPeek()
        # move it to other
        while not self._send_queue.empty():
            other.put_nowait(self._send_queue.get_nowait())

        # move back the higher priority items
        while not other.empty():
            v = other.get_nowait() # return a tuple (priority, VisonicListEntry)
            if v[0] <= int(priority):
                self._send_queue_put_nowait(v)

        #log.debug(f"[_empty_send_queue]    exit {self._send_queue.qsize()}")

    def _clear_receive_response_list(self):
        self.pmLastSentMessage: VisonicListEntry | None = None
        self.pmExpectedResponse = set()

    # This function needs to be called within the timeout to reset the timer period
    def _reset_watchdog_timeout(self):
        """Reset the watchdog timeout."""
        self._watchdog_counter = 0

    def _reset_message_data(self):
        """Reset message data ready to receive the next message from the panel."""
        # clear our buffer again so we can receive a new packet.
        self.ReceiveData = bytearray(b"")  # messages should never be longer than PACKET_MAX_SIZE
        # Reset control variables ready for next time
        self.pmCurrentPDU = pmReceiveMsg[Receive(0)]
        self.pmIncomingPduLen = 0
        self.pmFlexibleLength = 0

    # Process any received bytes (in data as a bytearray)
    def data_received(self, data):
        """Add incoming data to ReceiveData."""
        if self.suspendAllOperations:
            return
        if not self._first_cmd_sent:
            log.warning(f"[data receiver] Ignoring received data as first command not sent.  Received data: {toString(data)}")
            return
        #log.debug(f"[data receiver] received data: {toString(data)}")

        timenow = get_utc_time()
        interval = timenow - self._last_recv_time_panel_data
        if interval >= timedelta(seconds=2) and len(self.ReceiveData) > 0:
            # If the last block of received data was 2 or more seconds ago
            #    and we're in the middle of a message. then assume a problem and reset
            log.warning(f"[data receiver] Warning : Construction of incoming data incomplete - Message = {toString(self.ReceiveData)}")
            self._reset_message_data()
        self._last_recv_time_panel_data = timenow
        try:
            #log.warning(f"[data receiver] data is {toString(data)}")
            for databyte in data:
                # process a single byte at a time
                self._handle_received_byte(databyte)
        except Exception as ex:
            tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
            log.error("[Data Received] Processing, caused an exception\n%s", tb_str)

    # Process one received byte at a time to build up the received PDU (Protocol Description Unit)
    #       self.pmIncomingPduLen is only used in this function
    #       self._crc_error_count is only used in this function
    #       self.pmCurrentPDU is only used in this function
    def _handle_received_byte(self, data : int):
        """Process a single byte as incoming data."""

        def processCRCFailure():
            msg_type = self.ReceiveData[1]
            if msg_type not in (Receive.UNKNOWN_F1, Receive.IMAGE_DATA):  # ignore CRC errors on F1/F4 message
                self._crc_error_count += 1
                if self._crc_error_count >= MAX_CRC_ERROR:
                    self._crc_error_count = 0
                    interval = get_utc_time() - self._first_crc_error_time
                    if interval <= timedelta(seconds=CRC_ERROR_PERIOD):
                        self._report_problem(AlTerminationType.CRC_ERROR)
                    self._first_crc_error_time = get_utc_time()

        # Send an achnowledge back to the panel
        def sendAck(packet=bytearray(b"")):
            """Send ACK if packet is valid."""

            iscommand = packet is not None and len(packet) > 2 and packet[1] >= 0x40   # command message types
            panel_state_enrolled = not self.pmDownloadMode and self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]

            # There are 2 types of acknowledge that we can send to the panel
            #    Normal    : For a normal message
            #    Powerlink : For when we are in powerlink mode
            #if not isbase and panel_state_enrolled and ispm:
            if iscommand and panel_state_enrolled:             # When in Std+, PL Mode and message type is at or above 0x40
                message = pmSendMsg[Send.ACK_PLINK]
            else:
                message = pmSendMsg[Send.ACK]   # MSG_ACK
            assert message is not None
            e = VisonicListEntry(command=message)
            self.add_message_to_send_queue(message = e, priority = MessagePriority.ACK)

        def processReceivedPacket(ackneeded : bool, debugp : DebugLevel, packet : bytearray, msg: str):
            """Decode the received packet and call the message handler."""

            def statelist():
                sl0 = self.PartitionState[0].statelist()
                sl1 = self.PartitionState[1].statelist()
                sl2 = self.PartitionState[2].statelist()
                return [self.PanelMode, sl0, sl1, sl2]

            # A validated packet has been received
            msg_type = packet[1]
            # log.debug(f"[data receiver] *** Received validated message {hexify(msg_type)}   packet {toString(packet)}")
            # Send an ACK if needed
            if ackneeded:
                # log.debug(f"[data receiver] Sending an ack as needed by last panel status message {hexify(msg_type)}")
                sendAck(packet=packet)

            # Check response
            #tmplength = len(self.pmExpectedResponse)
            if len(self.pmExpectedResponse) > 0:  # and msg_type != 2:   # 2 is a simple acknowledge from the panel so ignore those
                # We've sent something and are waiting for a reponse - this is it
                if msg_type in self.pmExpectedResponse:
                    # while msg_type in self.pmExpectedResponse:
                    self.pmExpectedResponse.remove(msg_type)

            if packet is not None and debugp == DebugLevel.FULL:
                log.debug(f"[processReceivedPacket] Received {msg}   raw packet {toString(packet)}          response list {[hex(no).upper() for no in self.pmExpectedResponse]}")
            elif packet is not None and debugp == DebugLevel.CMD:
                log.debug(f"[processReceivedPacket] Received {msg}   raw packet {toString(packet[1:4])}          response list {[hex(no).upper() for no in self.pmExpectedResponse]}")

            if self.suspendAllOperations:
                # log.debug('[Disconnection] Suspended. Sorry but all operations have been suspended, please recreate connection')
                return

            # Check the current packet against the last packet to determine if they are the same
            if self._last_packet is not None:
                if self._last_packet == packet and packet[1] == Receive.STATUS_UPDATE:  # only consider A5 Receive.STATUS_UPDATE packets for consecutive error
                    self._last_packet_counter += 1
                else:
                    self._last_packet_counter = 0
            self._last_packet = packet

            if self._last_packet_counter == SAME_PACKET_ERROR:
                log.debug(f"[processReceivedPacket] Had the same packet for {SAME_PACKET_ERROR} times in a row : {toString(packet)}")
                self._report_problem(AlTerminationType.SAME_PACKET_ERROR)
                return

            # Handle the message
            if self._packet_callback is not None:
                # Record all main variables to see if the message content changes any
                old_state = statelist() # make it a function so if it's changed it remains consistent
                old_power_master = self.PowerMaster

                #process_ab         = not self.pmDownloadMode and self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK]
                process_ab          = not self.pmDownloadMode and not self.ForceStandardMode and self.PanelMode not in [AlPanelMode.POWERLINK_BRIDGED]
                process_normal_data = not self.pmDownloadMode and self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.MINIMAL_ONLY, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]
                process_b0          = self.EnableB0ReceiveProcessing or process_normal_data

                pushchange = self._packet_callback(packet, process_ab, process_normal_data, process_b0, self.pmDownloadMode)

                if self.send_panel_event_data(): # sent at least 1 event so no need to send PUSH_CHANGE
                    pushchange = False

                if pushchange or old_power_master != self.PowerMaster or old_state != statelist():   # make statelist a function so if it's changed it remains consistent
                    self.send_panel_update(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend

        if self.suspendAllOperations:
            return

        pdu_len = len(self.ReceiveData)                                      # Length of the received data so far
        # If we're receiving a variable length message and we're at the position in the message where we get the variable part
        if not isinstance(self.pmCurrentPDU, dict):
            # log.debug(f"[data receiver] {self.pmCurrentPDU.isvariablelength} {pdu_len == self.pmCurrentPDU.varlenbytepos}")
            if self.pmCurrentPDU.isvariablelength and pdu_len == self.pmCurrentPDU.varlenbytepos:
                # Determine total length of the message by getting the variable part int(data) and adding it to the fixed length part
                self.pmIncomingPduLen = self.pmCurrentPDU.length + int(data)
                self.pmFlexibleLength = self.pmCurrentPDU.flexiblelength
                #log.debug(f"[data receiver] Variable length Message Being Received  Message Type {hex(self.ReceiveData[1]).upper()}     pmIncomingPduLen {self.pmIncomingPduLen}   data var {int(data)}")

        # If we were expecting a message of a particular length (i.e. self.pmIncomingPduLen > 0) and what we have is already greater then that length then dump the message and resynchronise.
        if 0 < self.pmIncomingPduLen <= pdu_len:                             # waiting for pmIncomingPduLen bytes but got more and haven't been able to validate a PDU
            log.info(f"[data receiver] PDU Too Large: Dumping current buffer {toString(self.ReceiveData)}    The next byte is {hex(data).upper()}")
            pdu_len = 0                                                      # Reset the incoming data to 0 length
            self._reset_message_data()

        # If this is the start of a new message,
        #      then check to ensure it is a PACKET_HEADER (message preamble)
        if pdu_len == 0:
            self._reset_message_data()
            if data == Packet.HEADER:  # preamble
                self.ReceiveData.append(data)
                #log.debug(f"[data receiver] Starting PDU {toString(self.ReceiveData)}")
            # else we're trying to resync and walking through the bytes waiting for a Packet.HEADER preamble byte

        elif pdu_len == 1:
            #log.debug(f"[data receiver] Received message Type {data}")
            if data != Receive.DUMMY_MESSAGE and data in pmReceiveMsg:       # Is it a message type that we know about
                self.pmCurrentPDU = pmReceiveMsg[Receive(data)]              # set to current message type parameter settings for length, does it need an ack etc
                self.ReceiveData.append(data)                                # Add on the message type to the buffer
                if not isinstance(self.pmCurrentPDU, dict):
                    self.pmIncomingPduLen = self.pmCurrentPDU.length         # for variable length messages this is the fixed length and will work with this algorithm until updated.
                #log.debug(f"[data receiver] Building PDU: It's a message {hex(data).upper()}; pmIncomingPduLen = {self.pmIncomingPduLen}   variable = {self.pmCurrentPDU.isvariablelength}")
            elif data in (Receive.DUMMY_MESSAGE, 0xFD):                      # Special case for pocket and PowerMaster 10
                log.info(f"[data receiver] Received message type {hexify(data)} so not processing it")
                self._reset_message_data()
            else:
                # build an unknown PDU. As the length is not known, leave self.pmIncomingPduLen set to 0 so we just look for Packet.FOOTER as the end of the PDU
                self.pmCurrentPDU = pmReceiveMsg[Receive(0)]                 # Set to unknown message structure to get settings, varlenbytepos is -1
                self.pmIncomingPduLen = 0                                    # self.pmIncomingPduLen should already be set to 0 but just to make sure !!!
                log.warning(f"[data receiver] Warning : Construction of incoming packet unknown - Message Type {hex(data).upper()}")
                self.ReceiveData.append(data)                                # Add on the message type to the buffer

        elif pdu_len == 2 and isinstance(self.pmCurrentPDU, dict):
            #log.debug(f"[data receiver] Building PDU: It's a variable message {hex(self.ReceiveData[0]).upper()} {hex(data).upper()}")
            if data in self.pmCurrentPDU:
                self.pmCurrentPDU = self.pmCurrentPDU[data]
                #log.debug("[data receiver] Building PDU:   doing it properly")
            else:
                self.pmCurrentPDU = self.pmCurrentPDU[0]                     # All should have a 0 entry so use as default when unknown
                #log.debug(f"[data receiver] Building PDU: It's a variable message {hex(self.ReceiveData[0]).upper()} {hex(data).upper()} BUT it is unknown")
            self.pmIncomingPduLen = self.pmCurrentPDU.length                 # for variable length messages this is the fixed length and will work with this algorithm until updated.
            self.ReceiveData.append(data)                                    # Add on the message type to the buffer

        elif self.pmFlexibleLength > 0 and data == Packet.FOOTER and pdu_len + 1 < self.pmIncomingPduLen and (self.pmIncomingPduLen - pdu_len) < self.pmFlexibleLength:
            # Only do this when:
            #       Looking for "flexible" messages
            #              At the time of writing this, only the 0x3F EPROM Download PDU does this with some PowerMaster panels
            #       Have got the Packet.FOOTER message terminator
            #       We have not yet received all bytes we expect to get
            #       We are within 5 bytes of the expected message length, self.pmIncomingPduLen - pdu_len is the old length as we already have another byte in data
            #              At the time of writing this, the 0x3F was always only up to 3 bytes short of the expected length and it would pass the CRC checks
            # Do not do this when (pdu_len + 1 == self.pmIncomingPduLen) i.e. the correct length
            # There is possibly a fault with some panels as they sometimes do not send the full EPROM data.
            #    - Rather than making it panel specific I decided to make this a generic capability
            self.ReceiveData.append(data)  # add byte to the message buffer
            if isinstance(self.pmCurrentPDU, PanelCallBack) and self._validatePDU(self.pmCurrentPDU.checksum, self.ReceiveData):  # if the message passes CRC checks then process it
                # We've got a validated message
                #log.debug(f"[data receiver] Validated PDU: Got Validated PDU type {hexify(int(self.ReceiveData[1]))}   data {toString(self.ReceiveData)}")
                processReceivedPacket(ackneeded=self.pmCurrentPDU.ackneeded, debugp=self.pmCurrentPDU.debugprint, msg=self.pmCurrentPDU.msg, packet=self.ReceiveData)
                self._reset_message_data()

        elif (self.pmIncomingPduLen == 0 and data == Packet.FOOTER) or (pdu_len + 1 == self.pmIncomingPduLen): # postamble (the +1 is to include the current data byte)
            # (waiting for Packet.FOOTER and got it) OR (actual length == calculated expected length)
            self.ReceiveData.append(data)  # add byte to the message buffer
            #log.debug(f"[data receiver] Building PDU: Checking it {toString(self.ReceiveData)}")
            msg_type = self.ReceiveData[1]
            if isinstance(self.pmCurrentPDU, PanelCallBack) and self._validatePDU(self.pmCurrentPDU.checksum, self.ReceiveData):
                # We've got a validated message
                #log.debug(f"[data receiver] Building PDU: Got Validated PDU type {hexify(int(msg_type))}   data {toString(self.ReceiveData)}")
                if self.pmCurrentPDU.varlenbytepos < 0:  # is it an unknown message i.e. varlenbytepos is -1
                    log.warning(f"[data receiver] Received Valid but Unknown PDU {hex(msg_type)}")
                    sendAck()  # assume we need to send an ack for an unknown message
                else:  # Process the received known message
                    processReceivedPacket(ackneeded=self.pmCurrentPDU.ackneeded, debugp=self.pmCurrentPDU.debugprint, msg=self.pmCurrentPDU.msg, packet=self.ReceiveData)
                self._reset_message_data()
            else:
                # CRC check failed, create a message for the log file and process it as a failure
                if isinstance(self.pmCurrentPDU, PanelCallBack):
                    match (self.pmCurrentPDU.checksum):
                        case ChecksumType.IGNORE:
                            mess = "Checksum ignored, header and footer must be wrong"
                        case ChecksumType.IMAGE_DATA:
                            a,b = self.f4_checksum(self.ReceiveData[1:-3])
                            mess = f"{hexify(a)}/{hexify(b)}"
                            pattern = bytearray([0x0d, 0xF4, 0x05])
                            index = self.ReceiveData[1:].find(pattern)
                            if index != -1:
                                mess = f"{mess}, with a contained F4 05 at offset {index}"
                        case _:
                            a = self._calculateCRC(self.ReceiveData[1:-2])[0]  # this is just used to output to the log file
                            mess = f"{hexify(a)}"
                else:
                    mess = "Unknown message type"

                if len(self.ReceiveData) > PACKET_MAX_SIZE:
                    # If the length exceeds the max PDU size from the panel then stop and resync
                    log.warning(f"[data receiver] PDU with CRC error Message = {toString(self.ReceiveData)}   checksum calcs: {mess}")
                    processCRCFailure()
                    self._reset_message_data()
                elif self.pmIncomingPduLen == 0:
                    if msg_type in pmReceiveMsg:
                        # A known message with zero length and an incorrect checksum. Reset the message data and resync
                        log.warning(f"[data receiver] Warning : Construction of zero length incoming packet validation failed - Message = {toString(self.ReceiveData)}  checksum calcs: {mess}")

                        # Send an ack even though the its an invalid packet to prevent the panel getting confused
                        if isinstance(self.pmCurrentPDU, PanelCallBack) and self.pmCurrentPDU.ackneeded:
                            # log.debug(f"[data receiver] Sending an ack as needed by last panel status message {hexify(msg_type)}")
                            sendAck(packet=self.ReceiveData)

                        # Dump the message and carry on
                        processCRCFailure()
                        self._reset_message_data()
                    else:  # if msg_type != Receive.UNKNOWN_F1:        # ignore CRC errors on F1 message
                        # When self.pmIncomingPduLen == 0 then the message is unknown, the length is not known and we're waiting for a Packet.FOOTER where the checksum is correct, so carry on
                        log.debug(f"[data receiver] Building PDU: Length is {len(self.ReceiveData)} bytes (apparently PDU not complete)  {toString(self.ReceiveData)}  checksum calcs: {mess}")
                else:
                    # When here then the message is a known message type of the correct length but has failed it's validation
                    log.warning(f"[data receiver] Warning : Construction of incoming packet validation failed - Message = {toString(self.ReceiveData)}   checksum calcs: {mess}")

                    # Send an ack even though the its an invalid packet to prevent the panel getting confused
                    if isinstance(self.pmCurrentPDU, PanelCallBack) and self.pmCurrentPDU.ackneeded:
                        # log.debug(f"[data receiver] Sending an ack as needed by last panel status message {hexify(msg_type)}")
                        sendAck(packet=self.ReceiveData)

                    # Dump the message and carry on
                    processCRCFailure()
                    self._reset_message_data()

        elif pdu_len <= PACKET_MAX_SIZE:
            # log.debug(f"[data receiver] Current PDU {toString(self.ReceiveData)}   adding {hexify(data)}")
            self.ReceiveData.append(data)
        else:
            log.debug(f"[data receiver] Dumping Current PDU {toString(self.ReceiveData)}")
            self._reset_message_data()
        # log.debug(f"[data receiver] Building PDU {toString(self.ReceiveData)}")

    def add_message_to_send_queue(self, message : Send | bytearray | VisonicListEntry, priority : MessagePriority = MessagePriority.NORMAL, options : list | None = None, response : list | None = None):
        """Add a message to the send queue, the despatcher manages the actual sending and the timing."""
        if message is not None:
            if isinstance(message, Send):
                m = pmSendMsg[message]
                assert m is not None
                e = VisonicListEntry(command = m, response = response, options = [] if options is None else options)
            elif isinstance(message, bytearray):
                e = VisonicListEntry(raw = message, response = response, options = [] if options is None else options)
            elif isinstance(message, VisonicListEntry):
                e = message
            else:
                log.error(f"[add_message_to_send_queue] Message not added as not a string and not a bytearray, it is of type {type(message)}")
                return

            if (f := self._send_queue.find(e)) is not None:
                if f[0] != MessagePriority.ACK:     # Multiple acknowledge messages are allowed
                    if priority == f[0]:
                        log.debug(f"[add_message_to_send_queue] Adding panel message at priority {priority.name} that is already in the queue {f[1]}")
                    else:
                        log.info(f"[add_message_to_send_queue] Adding panel message at priority {priority.name} that is already in the queue {f[0]} {f[1]}   (the priority is different)")

            self._send_queue_put_nowait((int(priority), e))
