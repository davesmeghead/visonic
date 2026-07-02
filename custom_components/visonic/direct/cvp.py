"""Client Visonic Protocol."""

import asyncio
from collections.abc import Callable
from functools import partial
import logging
import socket
from socket import AddressFamily
from urllib.parse import urlparse

from serialx import (
    Parity,
    SerialException,
    StopBits,
    UnsupportedSetting,
    create_serial_connection as create_serial_connection_serialx,
)

from homeassistant.components.esphome.entry_data import RuntimeEntryData
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from ..exceptions import VisonicException  # noqa: TID252
from ..log_events import logEvents  # noqa: TID252
from ..visonic_types import CVP_Status  # noqa: TID252
from .pyvisonic.py_abstract_classes import AlPanelInterface

_LOGGER = logging.getLogger(__name__)

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

    def connection_made(self, transport: asyncio.Transport):
        """Connection made."""
        p = transport.get_protocol()
        if self._connection_status is None or p is not self:
            _LOGGER.debug("[ClientVisonicProtocol] connection_made for an orphaned protocol, closing transport")
            transport.close()
            return
        _LOGGER.debug("[ClientVisonicProtocol] connection_made Whooooo")
        self._transport = transport
        self.connections.add(self)
        self.vp.set_transport(self._transport)
        self.vp.start()
        #self.vp.setTransportConnection(self._transport)

    def connection_lost(self, exc):
        """Connection lost."""
        if self in self.connections and self._connection_status is not None:
            _LOGGER.debug("[ClientVisonicProtocol] connection_lost Booooo: shutdown vp and calling client handler")
            self.vp.shutdown()
            self.connections.discard(self)
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
            logger.logstate_debug(
                "Creating TCP Connection, Creating socket and setting socket options"
            )
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
            logger.logstate_debug(f"Creating TCP {family.name} Connection to {address}")
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

    entry : ConfigEntry = None
    part_protocol = partial(
        CVP_Direct,
        vp=vp,
        connection_status_callback=connection_status_callback,
    )

    def get_entry_id(url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme != "esphome-hass":
            return None
        return parsed.path.lstrip("/")

    async def _make_serial_connection(url) -> CVP_Direct | None:
        # Let's assume that serialx can handle all url/paths
        try:
            _, cvp = await create_serial_connection_serialx(
                loop=loop,
                protocol_factory=part_protocol,
                url=url,
                baudrate=baud,
                #byte_size=serialx.EIGHTBITS,
                parity=Parity.NONE,
                stopbits=StopBits.ONE,
            )
        except (SerialException, UnsupportedSetting) as ex:
            logger.logstate_warning(f"Integration not loaded {path}: {ex}")
        else:
            return cvp
        return None

    async def monitor_config_state_changes(entry : ConfigEntry):
         # Do not use NOT_LOADED as this function is never called with that state
        if entry.state == ConfigEntryState.LOADED:
            for _ in range(5):  # 5 seconds, try once per second
                try:
                    cvp = None
                    if is_esphome_url(path):
                        runtime: RuntimeEntryData = entry.runtime_data
                        if runtime and getattr(runtime, "available", False):
                            cvp = await _make_serial_connection(path)
                        else:
                            _unsub = runtime.async_subscribe_device_updated(_on_entry_state_change)
                            connection_status_callback(CVP_Status.CONNECTION_PENDING)
                            return
                    else:
                        cvp = await _make_serial_connection(path)
                    if cvp is not None:
                        connection_status_callback(CVP_Status.CONNECTED, cvp)
                        return
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(1.0)
            connection_status_callback(CVP_Status.NO_CONNECTION_MADE)
        elif entry.state == ConfigEntryState.UNLOAD_IN_PROGRESS:
            connection_status_callback(CVP_Status.DISCONNECTED)

    def _on_entry_state_change():
        hass.loop.create_task(monitor_config_state_changes(entry), name="serial_protocol_monitor")

    cvp = None
    logger.logstate_debug(f"Creating SERIAL Connection {path=} {baud=}")
    async with connection_lock:
        try:
            loop = asyncio.get_running_loop()
            # Determine if the path contains a ConfigEntry entry_id, if so then use it
            if isinstance(path, str):
                entry_id = get_entry_id(path)
                if entry_id is not None:
                    entry = hass.config_entries.async_get_entry(entry_id)
                    if entry is None:
                        # retry later OR wait for HA start
                        connection_status_callback(CVP_Status.NO_CONNECTION_MADE)
                        return
                    if entry.state == ConfigEntryState.LOADED:
                        # It's loaded so might as well give it an immediate tey
                        cvp = await _make_serial_connection(path)
                    # Start the change handler
                    _unsub = entry.async_on_state_change(_on_entry_state_change)
                else:
                    # Assume path does not contain entry_id
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

    #if cvp is not None:
    #    cvp.close()
    #else:
    #    connection_status_callback(CVP_Status.NO_CONNECTION_MADE)
