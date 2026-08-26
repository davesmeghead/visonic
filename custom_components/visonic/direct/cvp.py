"""Client Visonic Protocol."""

import asyncio
from collections.abc import Callable
from enum import IntEnum
from functools import partial
import logging
import socket
from socket import AddressFamily

from serialx import (
    Parity,
    SerialException,
    StopBits,
    UnsupportedSetting,
    create_serial_connection as create_serial_connection_serialx,
)

from homeassistant.core import HomeAssistant

from ..exceptions import VisonicException  # noqa: TID252
from ..log_events import logEvents  # noqa: TID252
from .pyvisonic.py_abstract_classes import AlPanelInterface

_LOGGER = logging.getLogger(__name__)

class CVP_Status(IntEnum):
    """Status of the comms_visonic_protocol i.e. CVP connection."""
    # Used in _connection_status callback
    DISCONNECTED = 1
    CONNECTED = 2
    NO_CONNECTION_MADE = 3
    CONNECTION_PENDING = 4
    EXCEPTION = 5

class CVP_Direct(asyncio.Protocol):
    """Visonic Protocol Client."""

    connections = set()  # class-level set to track all active connections

    def __init__(
        self,
        vp: AlPanelInterface,
        connection_status_callback: Callable[[CVP_Status, CVP_Direct | None], None] | None
    ):
        """Visonic Protocol client."""
        #super().__init__(*args, **kwargs)
        #_LOGGER.debug(f"[ClientVisonicProtocol] Init")
        self._transport : asyncio.Transport | None = None
        self.vp: AlPanelInterface = vp
        self._connection_status = connection_status_callback
        self.paused = False

    def data_received(self, data):
        """Visonic Protocol client."""
        #_LOGGER.debug(f"Received Data {data}")
        if self.vp is not None and self._transport is not None and not self.paused:
            self.vp.data_received(bytearray(data))

    def connection_made(self, trans: asyncio.Transport):
        """Connection made."""
        p = trans.get_protocol()
        if self._connection_status is None or p is not self:
            _LOGGER.debug("[ClientVisonicProtocol] connection_made for an orphaned protocol, closing transport")
            trans.close()
            return
        _LOGGER.debug("[ClientVisonicProtocol] connection_made Whooooo")
        self._transport = trans
        self.connections.add(self)
        self.vp.set_transport(self._transport)
        self.vp.start()
        #self.vp.setTransportConnection(self._transport)

    def connection_lost(self, exc):
        """Connection lost."""
        if self in self.connections:
            _LOGGER.debug("[ClientVisonicProtocol] connection_lost Booooo: shutdown vp and calling client handler")
            self.vp.shutdown()
            self.connections.discard(self)
            if self._connection_status is not None:
                self._connection_status(CVP_Status.DISCONNECTED)
        self._connection_status = None
        self._transport = None

    def close(self):
        """Close the Connection from Client."""
        # set it to None here so that the connection_lost does not call the callback
        self._connection_status = None
        if self._transport is not None:
            _LOGGER.debug("[ClientVisonicProtocol] protocol closing down => closing transport")
            self._transport.close()
        self._transport = None
        self.connections.discard(self)

    @property
    def transport(self):
        """Getter for transport."""
        return self._transport

    def pause(self):
        """Pause vp and transport, remove from connections."""
        if self._transport is not None and not self.paused:
            self.vp.pause()
            self.connections.discard(self)
            self.paused = True

    def resume(self):
        """Resume vp, transport and add to connections."""
        if self._transport is not None and self.paused:
            self.connections.add(self)
            self.vp.resume()
            self.paused = False
        else:
            _LOGGER.warning("[ClientVisonicProtocol] cannot resume,   paused=%s   transport=%s",self.paused, self._transport)

###################################################################################
################### The 2 ways to make the hardware connection ####################
###################################################################################

# I did have these as class methods of the above but took them out as it seemed like the right thing to do.
#   I'm not sure either way as they are tied to the class in many ways

connection_lock = asyncio.Lock()

async def async_create_tcp_client(
    logger: logEvents,
    hass: HomeAssistant,
    connection_status_callback: Callable[[CVP_Status, CVP_Direct | None], None] | None,  # Not used
    vp: AlPanelInterface,
    address: str,
    port: int,
) -> None:
    """Connect as a TCP client to a TCP Server (with TTL RS232 to the panel)."""

    def createSocketConnection(address: str, port: int):
        """Create the Socket Connection to the Device in the Panel."""
        sock = None
        try:
            # logger.logstate_debug(f"Setting TCP socket Options {address} {port}")
            family = AddressFamily.AF_INET6 if ":" in address else AddressFamily.AF_INET
            timeout = 5.0 if family == AddressFamily.AF_INET6 else 1.0
            # Create the socket using the detected family
            sock = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # Set blocking to on, this is the default but just make sure
            sock.setblocking(True)
            # Connection Logic
            sock.settimeout(timeout)
            # sock.connect works for both v4 and v6 tuples
            logger.logstate_debug(f"Creating TCP {family.name} Connection")
            sock.connect((address, port))

            sock.settimeout(0.01)  # Set to 0.01s timeout for flushing
            # Flush the buffer, receive any data and dump it
            try:
                sock.recv(10000)  # try to receive 10000 bytes
                logger.logstate_debug(
                    "Creating TCP Connection, Buffer Flushed and Received some data!"
                )
            except TimeoutError, BlockingIOError:  # fail after 1 second of no activity
                # logger.logstate_debug("Buffer Flushed and Didn't receive data! [Timeout]")
                pass

            # set the timeout to infinite
            sock.settimeout(None)
        except OSError as err:
            # Do not cause a full Home Assistant Exception, keep it local here
            logger.logstate_debug(f"Setting TCP socket Options Exception {err}")
            if sock is not None:
                sock.close()
                sock = None

        # return the socket
        return sock

    cvp = None
    async with connection_lock:
        try:
            logger.logstate_debug(
                "Creating TCP Connection, Creating socket and setting socket options"
            )
            sock = await hass.async_add_executor_job(createSocketConnection, address, port)
            if sock:
                loop = asyncio.get_running_loop()
                _, cvp = await loop.create_connection(
                    lambda: CVP_Direct(vp=vp, connection_status_callback=connection_status_callback),
                    sock=sock,
                )
                connection_status_callback(CVP_Status.CONNECTED, cvp)
                return
        except (VisonicException, TimeoutError, OSError) as ex:
            logger.logstate_warning(f"TCP client connection failed: {ex}")
    if cvp is not None:
        cvp.close()
    connection_status_callback(CVP_Status.NO_CONNECTION_MADE)

def is_esphome_url(url: str) -> bool:
    """Is the serialx connection an esphome device."""
    return url.startswith("esphome-hass://")

async def async_create_serial_client(
    logger: logEvents,
    hass: HomeAssistant,
    connection_status_callback: Callable[[CVP_Status, CVP_Direct | None], None],
    vp: AlPanelInterface,
    path: str,
    baud: int
) -> None:
    """Connect via USB/RS232/Serial."""

    async def _make_serial_connection(url: str) -> CVP_Direct | None:
        # Let's assume that serialx can handle all url/paths
        try:
            part_protocol = partial(
                CVP_Direct,
                vp=vp,
                connection_status_callback=connection_status_callback,
            )
            _, cvp = await create_serial_connection_serialx(
                loop=hass.loop, # put it on the main HA loop
                protocol_factory=part_protocol,
                url=url,
                baudrate=baud,
                parity=Parity.NONE,
                stopbits=StopBits.ONE,
            )
        except (SerialException, UnsupportedSetting) as ex:
            logger.logstate_warning(f"Integration not loaded {path}: {ex}")
        else:
            return cvp
        return None

    cvp = None
    logger.logstate_debug(f"Creating SERIAL Connection {path=} {baud=}")
    async with connection_lock:
        try:
            if isinstance(path, str):
                cvp = await _make_serial_connection(path)
            else:
                # Path isn't a string so I'm not sure what else it could be
                raise VisonicException("Unknown Serial Device")

            if cvp is not None:
                logger.logstate_debug("SERIAL Connection established")
                connection_status_callback(CVP_Status.CONNECTED, cvp)
            else:
                logger.logstate_debug("SERIAL Connection Not Made")
                connection_status_callback(CVP_Status.NO_CONNECTION_MADE)

        except (FileNotFoundError, OSError, SerialException, UnsupportedSetting) as ex:
            # SerialException comes from the serial library for things like "File Not Found" or "Permission Denied"
            logger.logstate_warning(
                f"Hardware error creating SERIAL Connection on {path}: {ex}"
            )
            connection_status_callback(CVP_Status.EXCEPTION)
        except (ValueError, TypeError) as ex:
            # Catch bad baud rates or invalid path types
            logger.logstate_error(f"Configuration error for SERIAL Connection: {ex}")
            connection_status_callback(CVP_Status.EXCEPTION)

        except asyncio.CancelledError:
            # Crucial: Allow Home Assistant to cancel this task during shutdown
            logger.logstate_debug("SERIAL Connection attempt cancelled by Home Assistant")
            connection_status_callback(CVP_Status.EXCEPTION)

