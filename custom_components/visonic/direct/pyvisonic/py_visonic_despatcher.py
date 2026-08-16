"""Visonic Despatcher - send dat to the panel."""

# ruff: noqa: G004, C901, BLE001

import asyncio
from datetime import timedelta
from enum import Enum, auto
import logging
import traceback

from .py_const import (
    MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMASTER,
    MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMAX,
    OBFUS,
    RESEND_MESSAGE_TIMEOUT,
    RESPONSE_TIMEOUT,
    DebugLevel,
)
from .py_enum import (
    AlPanelMode,
    AlTerminationType,
    B0SubType,
    MessagePriority,
    Packet,
    Receive,
    Send,
)
from .py_types_sending import VisonicListEntry
from .py_utils import get_utc_time, toString
from .py_visonic_devices import ManageDevices

log = logging.getLogger(__name__)

class DespatchError(Enum):
    """Return state from sendPDU."""
    SUCCESS = auto()
    SUSPENDED = auto()
    NO_INSTRUCTION = auto()
    NO_COMMAND = auto()
    NO_TRANSPORT = auto()

class Despatcher(ManageDevices):
    """Despatcher. Send data to the panel from a priority queue."""

    # There are 2 Tasks that manage the panel (despatcher and sequencer):
    #    This is the despatcher, it manages the sending of messages to the panel from a PriorityQueue
    #        The SendQueue is set up as a PriorityQueue and needs a < function implementing in VisonicListEntry based on time, oldest < newest
    #        By doing this it's like having two queues in one, a high priority queue, date ordered oldest first, and a low priority queue date ordered oldest first

    def __init__(self, force_standard_mode : bool, disable_all_commands : bool, download_code : str, user_code_slot: int, loop = None, logger = None) -> None:
        """Perform transactions based on messages (and not bytes)."""
        super().__init__(loop=loop, force_standard_mode=force_standard_mode, disable_all_commands=disable_all_commands, download_code=download_code, user_code_slot=user_code_slot, logger=logger)
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # start unpaused

    def _reset_full(self):
        """Reset all non-permanent variables."""
        super()._reset_full()
        self._despatcher_task = None
        self._despatcher_exception : bool = False
        self._transport : asyncio.Transport
        self._transport_valid : bool = False
        # Mark's Powerlink Bridge
        self.PowerLinkBridgeConnected : bool = False   # This is set true on first receipt of an E0.  It means that there is a server running to communicate with
        self.PowerLinkBridgeAlarm : bool = False       # The server has an Alarm Panel connection
        self.PowerLinkBridgeStealth : bool = False     # The server is in stealth mode (giving this integration sole access to the panel)
        self.PowerLinkBridgeProxy : bool = False       # The server is acting in proxy mode i.e. it supports a Visonic Go connection to an external site)

        self.B0_Wanted: set[int | B0SubType] = set()
        self.B0_Waiting: set[int | B0SubType] = set()
        self.B0_LastPanelStateTime = get_utc_time()
        self._triggered_download = False

    def _reset_connection(self):
        """Reset the variables needed to make a new connection."""
        super()._reset_connection()
        # This is the time stamp of the last Send
        self._last_transaction_time = get_utc_time() - timedelta(seconds=1)  # take off 1 second so the first command goes through immediately
        # keep alive counter for the timer
        self._reset_keep_alive_messages()  # only used in _sequencer
        # The last sent message
        self._clear_receive_response_list()

    def _shutdown(self):
        """Shutdown the connection to the panel."""
        super()._shutdown()
        # Set that the transport connection to the panel is invalid.
        self._transport_valid = False
        self._stop_despatcher()
        self._empty_send_queue(priority = MessagePriority.DELETE_ALL)

    async def waitForTransport(self, s : int):
        """Wait for the transport to be valid."""
        while s >= 0 and not self._transport_valid:
            await asyncio.sleep(0.1)
            s = s - 1

    # This sets the transport (to write to the panel) and starts the sequencer
    def set_transport(self, transport : asyncio.Transport):
        """Set the transport connection to the Panel."""
        self._transport = transport
        self._transport_valid = True
        log.debug("[Connection] Connected to local Protocol handler and Transport Layer")

    def _start_despatcher(self):
        """Re-start the PDU despatcher, the task that sends messages to the panel."""
        self._stop_despatcher()
        self._clear_despatcher()
        log.debug("[_start_despatcher] Starting _despatcher")
        self._despatcher_task = self.loop.create_task(self._despatcher(), name="pyvisonic_despatcher")

    def _clear_despatcher(self):
        """Re-start the PDU despatcher, the task that sends messages to the panel."""
        self._reset_watchdog_timeout()
        self._reset_keep_alive_messages()
        self._clear_receive_response_list()
        self._empty_send_queue(priority = MessagePriority.DELETE_ALL)  # empty the list

    def _stop_despatcher(self):
        """Stop the despatcher."""
        if self._despatcher_task is not None:
            try:
                log.debug("[_stop_despatcher] Cancelling _despatcher")
                self._despatcher_task.cancel()
            except Exception as ex:
                # This could happen in normal operation if the despatcher thread is blocked in the "get" queue function
                #     This is the exception for that
                #         ERROR (MainThread) [homeassistant] Error doing job: Task was destroyed but it is pending! (<Task pending name='Task-202' coro=<ProtocolBase._despatcher()
                #             running at /config/custom_components/visonic/pyvisonic.py:1754> wait_for=<Future pending cb=[Task.task_wakeup()]>>)
                tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                log.error("[_stop_despatcher] Visonic Executor loop has caused an exception\n%s", tb_str)
            self._despatcher_task = None

    # This function needs to be called within the timeout to reset the timer period
    def _reset_keep_alive_messages(self):
        """Reset the keep alive counter."""
        self.keep_alive_counter = 0

    # This function asks the panel for its status
    #     resets watchdog timers and asks the panel for a status
    #     it tries a RESTORE to re-establish powerlink comms protocols
    def _trigger_restore_status(self):
        # restart the watchdog and keep-alive counters
        self._reset_watchdog_timeout()
        self._reset_keep_alive_messages()
        if self.PowerLinkBridgeConnected:
            if self.is_power_master():
                self.B0_Wanted.add(B0SubType.PANEL_STATE_1)        # 24
            else:
                self.add_message_to_send_queue(Send.STATUS)
        elif self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK]:
            # Send RESTORE to the panel
            self.add_message_to_send_queue(Send.RESTORE)  # also gives status.  This is an AB message which we can't send to POWERLINK_BRIDGED
        else:
            self.add_message_to_send_queue(Send.STATUS)

    async def _despatcher(self):

        async def waitForTransport(s : int):
            s = s * 10
            while s >= 0 and not self._transport_valid:
                await asyncio.sleep(0.1)
                s = s - 1
            if not self._transport_valid:
                log.debug("[_despatcher] **************************************************************************************")
                log.debug("[_despatcher] ****************************** Transport Mechanism Invalid ***************************")
                log.debug("[_despatcher] **************************************************************************************")

        def checkQueuePriorityLevel():
            if not self._is_send_queue_empty():
                #log.debug(f"[_despatcher]  Checking The head of the queue")
                priority, _item = self._send_queue.peek_nowait()
                #log.debug(f"[_despatcher]  The head of the queue is priority {priority}")
                return priority
            return 10 # big number, above the priority levels used

        def sleepytime(interval) -> float:
            # If needed, create a minimum time delay between sending the panel messages as the panel can't cope (not enough CPU power and bandwidth on the serial link)
            # A PowerMaster is faster than a PowerMax so it can have a smaller minimum gap between sequential messages
            gap = MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMASTER if self.is_power_master() else MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMAX
            s: timedelta = timedelta(milliseconds=gap) - interval
            if s > timedelta(milliseconds=0):
                return s.total_seconds()
            return -1.0

        # Function to send all PDU messages to the panel
        def sendPdu(instruction: VisonicListEntry) -> tuple[DespatchError, float]:        # return the delay before sending the next PDU
            """Encode and put packet string onto write buffer."""

            if self.suspendAllOperations:
                log.debug("[sendPdu] Suspended all operations, not sending PDU")
                return DespatchError.SUSPENDED, -1.0

            if instruction is None:
                log.error("[sendPdu] Attempt to send a command that is empty")
                return DespatchError.NO_INSTRUCTION, -1.0

            if instruction.command is None and instruction.raw is None:
                log.error("[sendPdu] Attempt to send a sub command that is empty")
                return DespatchError.NO_COMMAND, -1.0

            if not self._transport_valid or self._transport.is_closing():
                log.debug("[sendPdu] Comms transport has been set to none, must be in process of terminating comms")
                return DespatchError.NO_TRANSPORT, -1.0

            data_out = None
            command = None

            if instruction.raw is not None:
                data_out = instruction.insertOptions(instruction.raw)

            elif instruction.command is not None:
                # Send a command to the panel
                command = instruction.command
                data = instruction.insertOptions(command.data)

                # log.debug(f"[sendPdu] input data: {toString(packet)}")
                # First add header (Packet.HEADER), then the packet, then crc and footer (Packet.FOOTER)
                data_out = bytearray([Packet.HEADER])
                data_out += data
                if self.AB_CRC_Type_Alternate and (data[0] == 0xAB):
                    data_out += self._calculateCRCAlt(data)
                else:
                    data_out += self._calculateCRC(data)
                data_out += bytearray([Packet.FOOTER])
            else:
                log.warning("[sendPdu]      Invalid message data, not sending anything to the panel")
                return DespatchError.NO_COMMAND, -1.0

            # no need to send i'm alive message for a while as we're about to send a command anyway
            self._reset_keep_alive_messages()

            # Write the data to the transport
            self._first_cmd_sent = True
            self._transport.write(data_out)
            self._last_transaction_time = get_utc_time()

            if data_out[1] != Receive.ACKNOWLEDGE:
                # The message is not an acknowledge back to the panel, then save it
                self.pmLastSentMessage = instruction

            if command is not None and command.download:
                self.pmDownloadMode = True
                self._triggered_download = False
                log.debug("[sendPdu] Setting Download Mode to true")

            # Log some useful information in debug mode
            if command is not None and command.debugprint == DebugLevel.FULL:
                log.debug(f"[sendPdu] Sent Command ({command.msg})    raw data {toString(data_out)}   waiting for message response {[hex(no).upper() for no in self.pmExpectedResponse]}")
            elif command is not None and command.debugprint == DebugLevel.CMD:
                log.debug(f"[sendPdu] Sent Command ({command.msg})    waiting for message response {[hex(no).upper() for no in self.pmExpectedResponse]}")
            elif instruction.raw is not None:
                # Assume raw data to send is not obfuscated for now
                log.debug(f"[sendPdu] Sent Raw Command      raw data {toString(data_out[:4] if OBFUS else data_out)}   waiting for message response {[hex(no).upper() for no in self.pmExpectedResponse]}")

            if command is not None and command.waittime > 0.0:
                return DespatchError.SUCCESS, command.waittime
            return DespatchError.SUCCESS, -1.0

        log.debug("[_despatcher]  Starting")
        self._despatcher_exception = False
        await waitForTransport(20) # Wait up to 20 seconds for the transport to be setup, if it isn't then other functions set self.suspendAllOperations to True
        while not self.suspendAllOperations:
            try:
                await self._pause_event.wait()  # pauses here if cleared
                post_delay: float = 0.01
                # calc the time interval between sending the last message and now
                interval = get_utc_time() - self._last_transaction_time
                write_status = DespatchError.SUCCESS  # combined with post_delay creates a small delay in the loop if nothing to process

                if len(self.pmExpectedResponse) == 0 or (not self._is_send_queue_empty() and checkQueuePriorityLevel() < 2):
                    # Here when either:
                    #     The expected response list is empty so we're not waiting for a specific message to be received before sending the next
                    #             in this case the get function will block waiting
                    #     The send queue is not empty and there is either an immediate pdu to send or an ack pdu
                    #             immediate pdu's are commanded by the user e.g. arm, disarm etc
                    # ensure that there is a minimum delay between sending messages to the panel
                    #log.debug(f"[_despatcher]  Loopy")
                    if (s := sleepytime(interval)) > 0.0:
                        # If needed, create a minimum time delay between sending the panel messages as the panel can't cope (not enough CPU power and bandwidth on the serial link)
                        #log.debug(f"[_despatcher]  sleeping for {s} seconds")
                        await asyncio.sleep(s)
                    # since we might have been asleep, check it again :)
                    if not self.suspendAllOperations:
                        #log.debug(f"[_despatcher] Start Get      queue size {self._send_queue.qsize()}")
                        # pop the highest priority and oldest item from the list, this could be the only item.
                        d = await self._send_queue.get()  # this blocks waiting for something to be added to the queue, nothing else is relevant as pmExpectedResponse is empty and can only be added to by calling sendPdu
                        #log.debug(f"[_despatcher] Get worked and got something priority={d[0]}          queue size {self._send_queue.qsize()}")

                        # since we might have been waiting for something to send, check it again :)
                        if not self.suspendAllOperations:
                            instruction: VisonicListEntry = d[1]   # PriorityQueue is put as a tuple (priority, viscommand), so get the viscommand
                            if len(instruction.response) > 0:
                                # update the expected response list straight away (without having to wait for it to be actually sent) to make sure protocol is followed
                                self.pmExpectedResponse.update(instruction.response)
                            self._send_queue.task_done()
                            #log.debug(f"[_despatcher] _despatcher sending it to sendPdu, instruction={instruction}          queue size {self._send_queue.qsize()}")
                            write_status, post_delay = sendPdu(instruction)
                            #log.debug(f"[_despatcher] Nothing to do      queue size {self._send_queue.qsize()}")
                elif interval > RESPONSE_TIMEOUT:
                    # If the panel is lazy or we've got the timing wrong........
                    # Expected response timeouts are only a problem when in Powerlink Mode as we expect a response
                    #   But in all modes, give the panel a self._trigger_restore_status
                    if len(self.pmExpectedResponse) == 1 and Receive.ACKNOWLEDGE in self.pmExpectedResponse:
                        self.pmExpectedResponse = set()  # If it's only for an acknowledge response then ignore it
                    else:
                        st = '[{}]'.format(', '.join(hex(x) for x in self.pmExpectedResponse))
                        log.debug("[_despatcher] ****************************** Response Timer Expired ********************************")
                        log.debug(f"[_despatcher]                While Waiting for: {st}")
                        # Reset Send state (clear queue and reset flags)
                        self._clear_receive_response_list()
                        self._trigger_restore_status()                                                # Clear message buffers and send a Restore (if in Powerlink or standard plus) or Status (not in Powerlink) to the Panel
                elif self.pmLastSentMessage is not None and interval > RESEND_MESSAGE_TIMEOUT:
                    #   If there's a timeout then resend the previous message. If that doesn't work then dump the message and continue, but log the error
                    if not self.pmLastSentMessage.triedResendingMessage:
                        # resend the last message
                        log.debug("[_despatcher] ****************************** Resend Timer Expired ********************************")
                        log.debug(f"[_despatcher]                Re-Sending last message  {self.pmLastSentMessage.command.msg}")
                        self.pmLastSentMessage.triedResendingMessage = True
                        write_status, post_delay = sendPdu(self.pmLastSentMessage)
                    else:
                        # tried resending once, no point in trying again so reset settings, start from scratch
                        log.debug("[_despatcher] ****************************** Resend Timer Expired ********************************")
                        log.debug("[_despatcher]                Tried Re-Sending last message but didn't work. Message is dumped")
                        # Reset Send state (clear queue and reset flags)
                        self._clear_receive_response_list()
                        self._empty_send_queue(priority = MessagePriority.ACK)
                    # restart the watchdog and keep-alive counters
                    self._reset_watchdog_timeout()
                    self._reset_keep_alive_messages()

                    if write_status == DespatchError.NO_TRANSPORT:
                        self._report_problem(AlTerminationType.EXTERNAL_TERMINATION)

                # implement any post delay for the message
                if not self.suspendAllOperations and write_status == DespatchError.SUCCESS and post_delay >= 0.0:  # Check send queue
                    #log.debug(f"[_despatcher]  Command has a post delay of {post_delay}")
                    await asyncio.sleep(post_delay)

            except Exception as ex:
                tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                log.error("[_despatcher] Visonic Executor loop has caused an exception\n%s", tb_str)
                self._despatcher_exception = True
