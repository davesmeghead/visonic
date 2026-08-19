"""Common Classes used in the 2 example."""

import asyncio
from collections.abc import Callable
from functools import partial
import logging
from pathlib import Path
import sys

from serial_asyncio import create_serial_connection

package_dir = Path(__file__).resolve().parent.parent
project_dir = package_dir.parent
sys.path.insert(0, str(project_dir))

from pyvisonic.py_abstract_classes import AlPanelInterface  # noqa: E402
from pyvisonic.py_visonic import VisonicProtocol  # noqa: E402  # noqa: E402

# This class joins the Protocol data stream to the visonic protocol handler.
#    transport needs to have 2 functions:   write(bytearray)  and  close()

_LOGGER = logging.getLogger(__name__)


class ClientVisonicProtocol(asyncio.Protocol):
    """Visonic Protocol Client."""

    connections = set()  # class-level set to track all active connections

    def __init__(
        self,
        vp: AlPanelInterface,
        connection_status_callback: Callable[[], None] | None
    ):
        """Visonic Protocol client."""
        #super().__init__(*args, **kwargs)
        _LOGGER.debug("[ClientVisonicProtocol] Init")
        self._transport : asyncio.Transport | None = None
        self.vp: AlPanelInterface = vp
        self._connection_status = connection_status_callback
        self.paused = False

    def data_received(self, data):
        """Visonic Protocol client."""
        #_LOGGER.debug(f"Received Data {data}")  # noqa: G004
        if self.vp is not None and self._transport is not None and not self.paused:
            self.vp.data_received(bytearray(data))

    def connection_made(self, transport: asyncio.Transport):
        """Connection made."""
        #p = transport.get_protocol()
        #if self._connection_status is None or p is not self:
        #    _LOGGER.debug("[ClientVisonicProtocol] connection_made for an orphaned protocol, closing transport")
        #    transport.close()
        #    return
        _LOGGER.debug("[ClientVisonicProtocol] connection_made Whooooo")
        self._transport = transport
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
                self._connection_status()
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

    # This is needed so we can create the class instance before giving it to the protocol handlers
    def __call__(self):
        """Caller."""
        return self

class BasicConnection:
    """Serial and TCP Connection Helpers."""

    async def async_create_tcp_visonic_connection(
        self, loop: asyncio.BaseEventLoop, vp: VisonicProtocol, connection_status_callback: Callable, address: str, port: int
    ):
        """Create a Visonic TCP connection."""

        def protocol_factory():
            return ClientVisonicProtocol(
                vp=vp,
                connection_status_callback=connection_status_callback,
            )

        try:
            return await loop.create_connection(
                protocol_factory,
                host=address,
                port=int(port),
            )

        except OSError as err:
            print(f"TCP connection failed: {err}")
            return None, None

    # Create a connection using asyncio through a linux port (usb or rs232)
    async def async_create_usb_visonic_connection(self, loop: asyncio.BaseEventLoop, vp : VisonicProtocol, connection_status_callback: Callable, path: str, baud="9600"):
        """Create Visonic manager class, returns rs232 transport coroutine."""
        print("Setting USB Options")
        # use default protocol if not specified
        protocol = partial(
            ClientVisonicProtocol,
            vp=vp,
            connection_status_callback=connection_status_callback,
        )
        # setup serial connection
        try:
            # create the connection to the panel as an asyncio protocol handler and then set it up in a task
            return await create_serial_connection(
                loop=loop, # put it on the main loop
                protocol_factory=protocol,
                url=path,
                baudrate=int(baud),
            )
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            print(f"Setting USB Options Exception {ex}")
        return None, None

