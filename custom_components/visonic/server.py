"""TCP Server transport.

This is currently not used
"""
import asyncio
from collections.abc import Callable
from functools import partial
import logging
import re
import socket

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .direct.crc16 import Crc16Arc
from .direct.pyvisonic.py_abstract_classes import AlPanelInterface
from .exceptions import VisonicException
from .utils import kill_asyncio_task, to_string
from .visonic_data_types import create_key

_LOGGER = logging.getLogger(__name__)

# Local constants
TEXT_UNKNOWN = "UNKNOWN"
VIS_ACK = "VIS-ACK"
VIS_BBA = "VIS-BBA"
ADM_CID = "*ADM-CID"
ADM_ACK = "*ACK"
ACK = "ACK"
DUH = "DUH"
NAK = "NAK"
MESSAGE_POWERLINK = bytes([0x0d, 0xab])
MESSAGE_BRIDGE = bytes([0x0d, 0xe1])
MESSAGE_ACK = bytes([0x0d, 0x02])

class PowerLink31Translator:
    r"""Class to translate messages that are sent and received via the powerlink hardware module.

    format is
        0a CRC16 Length MSG_TYPE  MSG_ID  L  ACCT_NO  #  ALARM_SERIAL [  COMMAND               ]  0d
        0a 6BAF  001D   "VIS-BBA" 5564    L  001234   #  1ABCDE       [See standard or b0 below]  0d

        0a 36 42 41 46 30 30 31 44 22 56 49 53 2d 41 43 4b 22 35 35 36 34 4c 30 23 32 41 34 43 43 33 5b 0d 02 43 ba 0a 5d 0d
        b'\n6BAF001D"VIS-ACK"5564L0#2A4CC3[\r\x02C\xba\n]\r'

    Notes:
        The MSG_TYPE includes the quotes in the message
        CRC16 is CRC16ARC of message from first " to last ]
        Length is from first " to last ]
        "VIS-ACK"/"VIS-BBA" - ACK for msg acknowldge, BBA for command/response message - have seen some *ADM-CID and *ACK
        MSG_ID increases with each BBA message.  ACK should be same as BBA it is ACK'ing
        ACCT_NO - message to Alarm is 0, from alarm is account no.
    """

    def __init__(self, account_id: str, panel_id: str):
        """Initialise the powerlink hardware module translator."""
        if len(account_id) != 6 or len(panel_id) != 6:
            raise VisonicException("Powerlink Module account number and panel id both need to be 6 characters.", code=700)
        self.account_id = account_id
        self.panel_id = panel_id

    def get_powerlink_31_wrapper(self, message: bytes) -> bytes:
        """Get first part of message."""
        i = message.find(b"\x5b")
        return message[1:i]

    def build_powerlink31_message(self, msg_id: int, message: bytes, is_ack: bool = False) -> bytearray:
        """Build initial part of B0 message."""

        message = message.hex(" ")

        msg_initiator = "\n"
        msg_type = "VIS-ACK" if is_ack else "VIS-BBA"

        msg_start = (
            f'"{msg_type}"{msg_id:04}L{self.account_id}#{self.panel_id}['
        )
        msg_end = "]"
        msg_terminator = "\r"

        base_msg = bytearray()
        base_msg.extend(map(ord, msg_start))
        base_msg.extend(bytearray.fromhex(message))
        base_msg.extend(map(ord, msg_end))

        # generate message prefix
        # crc
        crc16 = Crc16Arc.calchex(base_msg)
        msg_length = len(base_msg).to_bytes(2, byteorder="big")

        msg = bytearray()
        msg.extend(map(ord, msg_initiator))
        msg.extend(map(ord, crc16.upper()))
        msg.extend(map(ord, msg_length.hex()))
        msg.extend(base_msg)
        msg.extend(map(ord, msg_terminator))

        return msg

    def decode_powerlink31_message(self, message: bytes) -> bytearray:
        """Decode powerlink 3.1 message wrapper."""

        msg_decode = self.get_powerlink_31_wrapper(message).decode("ascii", errors="replace")
        #l_index = msg_decode.find("L")
        #hash_index = msg_decode.find("#")
        msg_start = message.find(b"\x5b")
        quote_start = message.find(b"\x22")
        square_end = message.rfind(b"\x5d")

        crc16 = msg_decode[0:4]
        #length = msg_decode[4:8]
        #msg_type = re.findall('"([^"]*)"', msg_decode)[0]
        matches = re.findall('"([^"]*)"', msg_decode)
        if not matches:
            raise VisonicException("Invalid message format")
        msg_type = matches[0]

        crc_test = message[quote_start:square_end+1]
        crc16_result = Crc16Arc.calchex(crc_test) # should we compare with above from message to ensure correct crc
        if crc16_result.upper() != crc16.upper():
            _LOGGER.warning("CRC mismatch in PowerLink message %s  %s", crc16_result.upper(), crc16.upper())

        if msg_type in [ADM_CID, ADM_ACK, NAK]:
            # These are special messages with slightly different format
            # A NAK does not have any msgid, panel or account info
            # A *AMD-CID, *ACK have no closing ]
            # Data is empty and followed by a time/date
            # *ADM-CID: b'\n1ADC00FD"*ADM-CID"0278LXXXXXX#001234[3FDFE5EB....FF14FF56\r'
            # *ACK: b'\n65F20059"*ACK"0278L25594E#001234[349772....D1605B74\r'
            # NAK: b'\nE5630025"NAK"0000R0L0A0[]_10:10:18,07-30-2024\r'

            if msg_type in [ADM_CID, ADM_ACK]:
                #msg_id = msg_decode[l_index - 4 : l_index]
                #account_id = msg_decode[l_index + 1 : hash_index]
                #panel_id = msg_decode[hash_index + 1 : hash_index + 7]
                msg = message[msg_start + 1 : -1]
            else:
                # NAK message
                # Set message to be time/date
                msg = message[msg_start + 3 : -1]
                #msg_id = "0000"
                #account_id = "0"
                #panel_id = "0"
        else:

            # Otherwise a normal VIS-BBA, VIS-ACK message

            #msg_id = msg_decode[l_index - 4 : l_index]
            #account_id = msg_decode[l_index + 1 : hash_index]
            #panel_id = msg_decode[hash_index + 1 : hash_index + 7]
            msg = message[msg_start + 1 : -2]
            #message_class = msg[1:2].hex()

        return msg

class TransportWrapperTranslator(asyncio.Transport):
    """Transport layer for serial data."""

    def __init__(self, t: asyncio.Transport, special_protocol : PowerLink31Translator = None) -> None:
        """Transport layer for serial data."""
        super().__init__(transport=t)
        self.special_protocol : PowerLink31Translator | None = special_protocol
        self.msg_id = 1
        self.stealth = False

    def _process_bridge_message(self, b: bytearray):
        """Process the bridge message from the low level library."""
        if b[2] == 1:
            _LOGGER.warning("Bridge command received  %s --> asking for status.", to_string(b))
            if self.vp is not None:
                #  f'Alarm: {"Connected" if data[0] == 1 else "Disconnected"}    '
                #  f'Visonic: {"Connected" if data[1] == 1 else "Disconnected"}    '
                #  f'HA: {"Connected" if data[2] == 1 else "Disconnected"}    '
                #  f'Proxy: {"Yes" if data[3] == 1 else "No"}    '
                #  f'Stealth: {"Yes" if data[4] == 1 else "No"}    '
                #  f'Download: {"Yes" if data[5] == 1 else "No"}' )
                status = f"0d e0 {'01' if self.connected else '00'} 00 01 00 {'01' if self.stealth else '00'} 00 00 1b 0a"
                self.vp.data_received(bytearray.fromhex(status))
        elif b[2] == 2:
            if b[3] == 1:
                _LOGGER.warning("Bridge command received  %s --> enter stealth.", to_string(b))
                self.stealth = True
            else:
                _LOGGER.warning("Bridge command received  %s --> exit stealth.", to_string(b))
                self.stealth = False
        else:
            _LOGGER.warning("Bridge command received  %s --> unknown.", to_string(b))

    def write(self, b: bytearray):
        """Transport layer for serial data."""
        # _LOGGER.debug(f"Data Sent {b}")
        if self.ok_to_write and self._transport is not None and self.special_protocol is not None:
            if b.startswith(MESSAGE_BRIDGE):
                # decode what the pyvisonic library is asking for
                self._process_bridge_message(b)
                return
            if b.startswith(MESSAGE_POWERLINK):
                _LOGGER.warning("Do not send powerlink messages using this connection type")
                return
            b = self.special_protocol.build_powerlink31_message(msg_id = self.msg_id, message = b, is_ack = b.startswith(MESSAGE_ACK))
            self.msg_id = (self.msg_id + 1) % 10000
            _LOGGER.info("Data Sent %s", to_string(b))
            super().write(b)

    def decode_to_message(self, data: bytearray) -> bytearray | None:
        """Decode message from a cloud server to raw byte stream."""
        if self.has_translator():
            return self.special_protocol.decode_powerlink31_message(data)
        return None

    def update_translator(self, t : PowerLink31Translator):
        """Update the translator."""
        self.special_protocol = t

    def has_translator(self) -> bool:
        """Is the translator set?"""
        return self.special_protocol is not None

class ServerProtocol(asyncio.Protocol):
    """Server Protocol."""

    def __init__(
        self,
        on_connection: Callable[..., bool],
        on_disconnection: Callable[..., None],
        on_data: Callable[..., None],
    ):
        """Server Protocol."""
        self._transport: TransportWrapperTranslator | None = None
        self.account_id: str | None = None
        self.panel_id: str | None = None
        self.on_connection = on_connection
        self.on_disconnection = on_disconnection
        self.on_data = on_data
        self.vp = None
        self.new_connection = False

    def get_powerlink_31_wrapper(self, message: bytes) -> bytes:
        """Get first part of message."""
        i = message.find(b"\x5b")
        return message[1:i]

    @property
    def transport(self):
        """Getter for transport."""
        return self._transport

    def set_vp(self, vp: AlPanelInterface):
        """Set the visonic protocol."""
        self.vp = vp

    def extract_ids(self, data: bytes) -> tuple[None,None] | tuple[str,str]:
        """Extract account_id and panel_id from first message."""

        msg_decode = self.get_powerlink_31_wrapper(data).decode("ascii")
        l_index = msg_decode.find("L")
        hash_index = msg_decode.find("#")

        msg_type = re.findall('"([^"]*)"', msg_decode)[0]

        if msg_type in [ADM_CID, ADM_ACK, NAK]:
            if msg_type in [ADM_CID, ADM_ACK]:
                panel_id = msg_decode[l_index + 1 : hash_index]
                account_id = msg_decode[hash_index + 1 : hash_index + 7]
            else:
                # NAK message
                # Set message to be time/date
                return None, None
        else:
            account_id = msg_decode[l_index + 1 : hash_index]
            panel_id = msg_decode[hash_index + 1 : hash_index + 7]

        return account_id, panel_id

    def is_running(self):
        """Do we have a valid connection."""
        return self._transport is not None

    def connection_made(self, transport: asyncio.Transport):
        """Connection made."""
        self.new_connection = True
        if self.on_connection():
            if self._transport:
                self._transport.update_transport(transport)
            else:
                self._transport = TransportWrapperTranslator(transport, None)
            _LOGGER.info("New TCP client connected")
            # Can't do anything else until we receive the first data

    def data_received(self, data: bytes):
        """Data received."""
        try:
            if self.is_running():
                if self.account_id is None or self.panel_id is None:
                    self.account_id, self.panel_id = self.extract_ids(data)
                key = create_key(self.account_id, self.panel_id)
                if key is None:
                    # Received a data packet but cannot extract account and panel
                    #   so ignore it and wait for next data packet
                    # **************** Do we process the data anyway? ******************
                    self.account_id = None
                    self.panel_id = None
                    return

                if self._transport and not self._transport.has_translator():
                    self._transport.update_translator(PowerLink31Translator(self.account_id, self.panel_id))

                if self.new_connection or self.vp is None:
                    _LOGGER.warning("New panel connection *************************************")

                self.on_data(self.account_id, self.panel_id, self._transport, self, self.vp is not None)

                self.new_connection = False

                _LOGGER.info("Data received from %s/%s: %s", self.account_id, self.panel_id, to_string(bytearray(data)))
                # Process data
                if data.startswith(MESSAGE_POWERLINK):
                    _LOGGER.warning("Received a powerlink message from the panel *************************************")
                    return
                msg = self._transport.decode_to_message(data)
                _LOGGER.info("Received data %s", to_string(msg))
                if self.vp:
                    self.vp.data_received(msg)
                else:
                    _LOGGER.info("Received data but the vp is None so not processed")


        except (VisonicException, TimeoutError, OSError) as ex:
            _LOGGER.info("***************** Unable to decode account/panel info ******************")
            _LOGGER.info("***************** %s", ex)
        except (ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError) as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            _LOGGER.info("***************** Unable to decode account/panel info ******************")
            _LOGGER.info("***************** %s", ex)

    def connection_lost(self, exc):
        """Connection lost."""
        self.on_disconnection(self.account_id, self.panel_id)
        self.account_id = None
        self.panel_id = None
        self._transport = None

    def close(self):
        """Close the protocol."""
        _LOGGER.error("Close function called on a server created protocol")
        if self._transport:
            self._transport.close()
        self._transport = None

class TCPServerConnection:
    """Prototype TCP server that tracks multiple panels by (account_id, panel_id)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, connection_made_callback):
        """Initialize server prototype."""
        self.hass = hass
        self.entry = entry
        self.connections: dict[str, TransportWrapperTranslator] = {}
        self.pending_removals: dict[str, asyncio.Task] = {}
        self.callback = connection_made_callback
        self.server_task: asyncio.Task | None = None
        self.server = None
        self.server_lock = asyncio.Lock()

    def is_running(self) -> bool:
        """Is the server running?"""
        return self.server_task is not None

    async def async_stop(self):
        """Stop the server."""
        # Kill the server task, set self.server_task to None to prevent new
        _LOGGER.info("***************** stopping server ******************")
        async with self.server_lock:
            _LOGGER.info("***************** stopping server got lock ******************")
            # Close the server
            if self.server:
                self.server.close()
                await self.server.wait_closed()
            self.server = None

            # Stop the server task
            tmp = self.server_task
            self.server_task = None
            await kill_asyncio_task(tmp)

            # Close all the transports
            for t in self.connections.values():
                t.close()
            self.connections: dict[str, TransportWrapperTranslator] = {}

            # Kill the timer tasks
            for t in self.pending_removals.values():
                await kill_asyncio_task(t)
            # Reset variables to make sure
            self.pending_removals: dict[str, asyncio.Task] = {}
            _LOGGER.info("***************** server stopped ******************")

    def connection_lost(self, account: str, panel: str):
        """A child calls this when it loses the connection."""
        if not self.is_running():
            return
        if key := create_key(account, panel):
            _LOGGER.info("Panel %s/%s disconnected, 60 second delay starting", account, panel)
            if key in self.pending_removals:
                _LOGGER.info("    cancelling existing timer task first")
                # cancel pending removal if it exists
                self.pending_removals[key].cancel()
                del self.pending_removals[key]
            # start delayed removal
            self.pending_removals[key] = self.entry.async_create_task(
                self.hass,
                self.delayed_remove(key, account, panel),
                name="Remove server client",
            )

    def data_received(self, account: str, panel: str, transport: TransportWrapperTranslator, protocol: ServerProtocol, vp_ok: bool):
        """A child calls this when data has been received."""
        key = create_key(account, panel)
        callback_called = False
        if key not in self.connections:
            _LOGGER.info("Panel %s/%s connected", account, panel)
            self.connections[key] = transport
            self.callback(account, panel, transport, protocol, vp_ok)
            callback_called = True
        elif key in self.pending_removals:
            _LOGGER.info("Panel %s/%s reconnected", account, panel)
            # cancel pending removal if it exists
            self.pending_removals[key].cancel()
            del self.pending_removals[key]
        if not vp_ok and not callback_called:
            _LOGGER.info("Panel %s/%s vp not OK", account, panel)
            self.callback(account, panel, transport, protocol, vp_ok)

    async def delayed_remove(self, key: str, account_id: str, panel_id: str):
        """Remove a panel after a timeout if it does not reconnect."""
        try:
            await asyncio.sleep(60)

            if key in self.connections:
                _LOGGER.info("Panel %s/%s timed out after disconnect", account_id, panel_id)
                del self.connections[key]
                self.callback(account_id, panel_id, None, None, False)

            self.pending_removals.pop(key, None)

        except asyncio.CancelledError:
            _LOGGER.info("Reconnect detected for %s/%s, cancelling removal", account_id, panel_id)

    async def async_start(self, hass: HomeAssistant, host: str = '0.0.0.0', port: int = 5001) -> bool:
        """Start a TCP server listening on all interfaces."""

        _LOGGER.info("***************** starting server ******************")
        async with self.server_lock:
            _LOGGER.info("***************** starting server got lock ******************")
            try:
                loop = asyncio.get_running_loop()
                self.server = await loop.create_server(
                    # TODO: This currently only supports 1 panel from discovery
                    partial(
                        ServerProtocol,
                        on_connection=self.is_running,
                        on_disconnection=self.connection_lost,
                        on_data=self.data_received,
                    ),
                    host=host,
                    port=port,
                    reuse_address=True,
                    reuse_port=True
                )

                for sock in self.server.sockets:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                self.server_task = self.entry.async_create_background_task(self.hass, self.server.serve_forever(), "Visonic TCP Server")
                _LOGGER.info("***************** server started on %s port %s ******************", host, port)
                return True  # noqa: TRY300
            except (VisonicException, TimeoutError, OSError) as ex:
                _LOGGER.info("***************** TransportWrapper, caused HA exception ******************")
                _LOGGER.info("***************** %s", ex)
            except (ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError) as ex:
                # Do not cause a full Home Assistant Exception, keep it local here
                _LOGGER.info("***************** TransportWrapper, caused connection exception ******************")
                _LOGGER.info("***************** %s", ex)
            return False
