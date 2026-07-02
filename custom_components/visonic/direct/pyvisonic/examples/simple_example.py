"""Create a commandline connection to a Visonic PowerMax or PowerMaster Alarm System."""

# Make sure Ruff ignores f-strings
# ruff: noqa: T201, BLE001

# set the parent directory on the import path
import argparse
import asyncio
from datetime import timedelta
from enum import Enum
from functools import partial
import logging
import socket
import sys
import time

from serialx import create_serial_connection

from ..py_abstract_classes import AlTransport  # noqa: TID252
from ..py_visonic import VisonicProtocol  # noqa: TID252

parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
parser.add_argument("-usb", help="visonic alarm usb device", default="")
parser.add_argument("-address", help="visonic alarm ip address", default="")
parser.add_argument("-port", help="visonic alarm ip port", type=int)
parser.add_argument("-panel", help="visonic panel number", default="0")
parser.add_argument("-connect", help="connection mode: powerlink, standard, dataonly", default="powerlink")
parser.add_argument("-logfile", help="log file name to output to", default="")
parser.add_argument("-print", help="print mode: error, warning, info, debug", default="error")
args = parser.parse_args()
conn_type = "ethernet" if len(args.address) > 0 else "usb"

# config parameters for myconfig, just to make the defaults easier
CONF_DOWNLOAD_CODE = "download_code"
CONF_EMULATION_MODE = "emulation_mode"

class ConnectionMode(Enum):
    """Emulation Mode."""
    POWERLINK = 1
    STANDARD = 2
    DATAONLY = 3

myconfig = {
    CONF_DOWNLOAD_CODE: "",
    CONF_EMULATION_MODE: ConnectionMode.POWERLINK
}

def toBool(val) -> bool:
    """To bool."""
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val != 0
    if isinstance(val, str):
        v = val.lower()
        return v not in {"no", "false", "0"}
    print(f"Visonic unable to decode boolean value {val}    type is {type(val)}")
    return False

def setConnectionMode(connect_mode):
    """Set connection mode."""
    global connection_mode

    if connect_mode[0] == "p":
        myconfig[CONF_EMULATION_MODE] = ConnectionMode.POWERLINK
        connection_mode = "Powerlink (full capability)"
    elif connect_mode[0] == "s":
        myconfig[CONF_EMULATION_MODE] = ConnectionMode.STANDARD
        connection_mode = "Standard (not in powerlink but includes ability to set alarm state)"
    elif connect_mode[0] == "d":
        myconfig[CONF_EMULATION_MODE] = ConnectionMode.DATAONLY
        connection_mode = "Data Only (exchange of simple data with alarm panel, no ability to set alarm state)"

string_type="string"
int_type = "int"
bool_type = "bool"
list_type = "list"
myconfigtypes = [string_type, string_type, int_type, string_type, int_type, string_type, bool_type, bool_type, bool_type, string_type, bool_type, list_type, bool_type, int_type, string_type, bool_type, bool_type, list_type, bool_type, int_type, int_type]


def setupLocalLoggerBasic():
    """Local logging handler."""
    import logging  # noqa: PLC0415
    return logging.getLogger()

def setupLocalLogger(level: str = "WARNING", empty = False):
    """Set up local logger."""
    global logger_level

    root_logger = logging.getLogger()

    class ElapsedFormatter:
        def __init__(self):
            self.start_time = time.time()

        def format(self, record):
            #print(f"record {record}")
            elapsed_seconds = record.created - self.start_time
            # using timedelta here for convenient default formatting
            elapsed = str(timedelta(seconds=elapsed_seconds))
            return f"{elapsed: <15} <{record.filename: <15}:{record.lineno: >5}> {record.levelname: >8}   {record.getMessage()}"

    # remove existing handlers
    while root_logger.hasHandlers():
        root_logger.removeHandler(root_logger.handlers[0])

    # add custom formatter to root logger
    formatter = ElapsedFormatter()
    shandler = logging.StreamHandler(stream=sys.stdout)
    shandler.setFormatter(formatter)
    if args.logfile is not None and len(args.logfile) > 0:
        fhandler = logging.FileHandler(args.logfile, mode=("w" if empty else "a"))
        fhandler.setFormatter(formatter)
        root_logger.addHandler(fhandler)

    #root_logger.propagate = False
    root_logger.addHandler(shandler)

    # level = logging.getLevelName('INFO')
    logger_level = level
    level = logging.getLevelName(level)  # INFO, DEBUG
    root_logger.setLevel(level)

def ConfigureLogger(mode, console = None):
    """Config logger."""
    if mode[0] == 'd':
        setupLocalLogger("DEBUG")   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
        if console is not None:
            console.print("Setting output mode to DEBUG")
    elif mode[0] == 'i':
        setupLocalLogger("INFO")   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
        if console is not None:
            console.print("Setting output mode to INFO")
    elif mode[0] == 'w':
        setupLocalLogger("WARNING")   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
        if console is not None:
            console.print("Setting output mode to WARNING")
    elif mode[0] == 'e':
        setupLocalLogger("ERROR")   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
        if console is not None:
            console.print("Setting output mode to ERROR")
    elif console is not None:
        console.print(f"Not Setting output mode, unknown mode {mode}")


class TransportWrapper(AlTransport):
    """Transport wrapper."""

    def __init__(self, t):
        """Initialise."""
        self.transport = t

    def write(self, b : bytearray) -> bool:
        """Write bytes."""
        self.transport.write(b)

    def close(self):
        """Close the transport."""
        self.transport.close()

# This class joins the Protocol data stream to the visonic protocol handler.
#    transport needs to have 2 functions:   write(bytearray)  and  close()
class ClientVisonicProtocol(asyncio.Protocol):
    """Protocol handler."""

    def __init__(self, vp : VisonicProtocol, client):
        """Initialise."""
        #super().__init__(*args, **kwargs)
        #print(f"CVP Init")
        self._transport = None
        self.vp = vp
        self.client = client
        if client is not None:
            client.tellemaboutme(self)

    def data_received(self, data):
        """Receive data and pass on to the visonic protocol."""
        #print(f"Received Data {data}")
        self.vp.data_received(data)

    def connection_made(self, transport):
        """Connection has been made callback."""
        print("connection_made Whooooo")
        self._transport = TransportWrapper(transport)
        self._transport.update_transport(self._transport)

    def _stop(self):
        """General function to stop everything."""
        print("stop called")
        self.client = None
        self.vp = None
        if self._transport is not None:
            print("stop called on protocol => closed")
            self._transport.close()
        self._transport = None
        print("stop finished")

    def connection_lost(self, exc):
        """Connection lost callback."""
        print("connection_lost Booooo")
        #if self.client is not None:
        #    print(f"connection_lost    setup to reconnect")
        if self._transport is not None:
            self._stop()
        print("connection_lost finished")

    def close(self):
        """Close the connection."""
        #print(f"Connection Closed")
        print("close called on protocol")
        if self._transport is not None:
            self._stop()
            self._transport = None
        print("close finished")

    # This is needed so we can create the class instance before giving it to the protocol handlers
    def __call__(self):
        """Caller."""
        return self

class VisonicClient:
    """Client."""

    # Create a connection using asyncio using an ip and port
    async def async_create_tcp_visonic_connection(self, vp : VisonicProtocol, address, port):
        """Create Visonic manager class, returns tcp transport coroutine."""

        def createSocketConnection(address, port):
            """Create the Socket Connection to the Device in the Panel."""
            try:
                print(f"Setting TCP socket Options {address} {port}")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                sock.setblocking(1)  # Set blocking to on, this is the default but just make sure
                sock.settimeout(1.0)  # set timeout to 1 second to flush the receive buffer
                sock.connect((address, port))

                # Flush the buffer, receive any data and dump it
                try:
                    _dummy = sock.recv(10000)  # try to receive 10000 bytes
                    print("Buffer Flushed and Received some data!")
                except TimeoutError:  # fail after 1 second of no activity
                    print("Buffer Flushed and Didn't receive data! [Timeout]")

                # set the timeout to infinite
                sock.settimeout(None)
                # return the socket
                return sock  # noqa: TRY300

            except OSError as err:
                # Do not cause a full Home Assistant Exception, keep it local here
                print(f"Setting TCP socket Options Exception {err}")
                if sock is not None:
                    sock.close()

            return None

        try:
            sock = createSocketConnection(address, int(port))
            if sock is not None:
                # Create the Protocol Handler for the Panel, also handle Powerlink connection inside this protocol handler
                cvp = ClientVisonicProtocol(vp=vp, client=self)
                # create the connection to the panel as an asyncio protocol handler and then set it up in a task
                coro = self.loop.create_connection(cvp, sock=sock)
                #print("The coro type is " + str(type(coro)) + "   with value " + str(coro))
                # Wrap the coroutine in a task to add it to the asyncio loop
                vTask = self.loop.create_task(coro)
                # Return the task and protocol
                return vTask, cvp

        except Exception:
            # Do not cause a full Home Assistant Exception, keep it local here
            pass

        return None, None

    def tellemaboutme(self, thisisme):
        """This function is here so that the coroutine can tell us the protocol handler."""
        self.tell_em = thisisme

    # Create a connection using asyncio through a linux port (usb or rs232)
    async def async_create_usb_visonic_connection(self, vp : VisonicProtocol, path, baud="9600"):
        """Create Visonic manager class, returns rs232 transport coroutine."""

        print("Setting USB Options")

        # use default protocol if not specified
        protocol = partial(
            ClientVisonicProtocol,
            vp=vp,
            client=self,
        )

        # setup serial connection
        baud = int(baud)
        try:
            self.tell_em = None
            # create the connection to the panel as an asyncio protocol handler and then set it up in a task
            conn = create_serial_connection(self.loop, protocol, path, baud)
            #print("The coro type is " + str(type(conn)) + "   with value " + str(conn))
            vTask = self.loop.create_task(conn)
            if vTask is not None:
                ctr = 0
                while self.tell_em is None and ctr < 40:     # 40 with a sleep of 0.05 is approx 2 seconds. Wait up to 2 seconds for this to start.
                    await asyncio.sleep(0.05)                # This should only happen once while the Protocol Handler starts up and calls tellemaboutme to set self.tell_em
                    ctr += 1
                if self.tell_em is not None:
                    # Return the task and protocol
                    return vTask, self.tell_em
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            print(f"Setting USB Options Exception {ex}")
        return None, None

    async def startitall(self, testloop):
        """Start it going."""

        print("Client Creating VP")
        visonicProtocol = VisonicProtocol(panelConfig=myconfig, panel_id=args.panel, loop=testloop)

        if visonicProtocol is not None:

            visonicProtocol.resetMessageData()

            # Get Visonic specific configuration.
            print(f"Reconnection Device Type is {conn_type}")
            if conn_type == "ethernet":
                host = args.address
                port = args.port
                (_visonicCommsTask, _cvp) = await self.async_create_tcp_visonic_connection(vp=visonicProtocol, address=host, port=port)
            elif conn_type == "usb":
                path = args.usb
                (_visonicCommsTask, _cvp) = await self.async_create_usb_visonic_connection(vp=visonicProtocol, path=path, baud=self.baud_rate)
            #return cvp is not None and visonicCommsTask is not None
        #return False

def handle_exception(loop, context):
    """Handle exception."""
    # context["message"] will always be there; but context["exception"] may not
    _msg = context.get("exception", context["message"])
    print(f"Caught exception: {_msg}")
    #print(f"                  {context}")

if __name__ == '__main__':
    #setupLocalLogger("DEBUG", False)   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
    #logging.basicConfig(level=logging.DEBUG)
    #_LOGGER = logging.getLogger(__name__)

    log = setupLocalLoggerBasic()
    setupLocalLogger("ERROR", empty = True)   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
    ConfigureLogger(str(args.print).lower(), None)
    setConnectionMode(str(args.connect).lower())

    testloop = asyncio.new_event_loop()
    asyncio.set_event_loop(testloop)
    #testloop.set_exception_handler(handle_exception)

    client = VisonicClient()

    _task = testloop.create_task(client.startitall(testloop))
    try:
        #print("Calling run_forever")
        testloop.run_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        pass
    finally:
        # cleanup connection
        print("Cleaning up")
        #testloop.close()
