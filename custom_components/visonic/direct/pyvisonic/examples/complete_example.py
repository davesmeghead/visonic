"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""
#! /usr/bin/python3

# Make sure Ruff ignores f-strings
# ruff: noqa: T201, BLE001

import argparse
import asyncio
from datetime import timedelta
from enum import Enum, IntEnum
from functools import partial
import inspect
from inspect import currentframe
import logging
import socket
import sys
import time
from time import sleep
import traceback

from requests import ConnectTimeout, HTTPError
from serialx import create_serial_connection

from ..py_abstract_classes import (  # noqa: TID252
    AlCommandStatus,
    AlPanelCommand,
    AlPanelInterface,
    AlPanelMode,
    AlPanelStatus,
    AlSensorCondition,
    AlSensorDevice,
    AlSwitchDevice,
    AlTransport,
)
from ..py_enum import AlCondition  # noqa: TID252
from ..py_visonic import VisonicProtocol  # noqa: TID252

# Try to import aconsole, if it fails then print an error message
try:
    import aconsole
except:
    print("You need to install the aconsole library like this:  pip install -r requirements.txt")
    print("This will install the necessary python libraries for you")
    sys.exit(0)

terminating_clean = "terminating_clean"

# config parameters for myconfig, just to make the defaults easier
CONF_PANEL_NUMBER = "panel_number"
CONF_TYPE = "type"
CONF_DEVICE_BAUD = "baud"
CONF_EXCLUDE_SENSOR = "exclude_sensor"
CONF_EXCLUDE_X10 = "exclude_x10"
CONF_DOWNLOAD_CODE = "download_code"
CONF_EMULATION_MODE = "emulation_mode"
CONF_COMMAND = "command"
CONF_X10_COMMAND = "x10command"

class ConnectionMode(Enum):
    """Emulation Mode."""
    POWERLINK = 1
    STANDARD = 2
    DATAONLY = 3

class PrintMode(Enum):
    """Print mode."""
    NONE = 0
    ERROR = 1
    WARNING = 2
    INFO = 3
    DEBUG = 4

myconfig = { 
    CONF_DOWNLOAD_CODE: "",
    CONF_EMULATION_MODE: ConnectionMode.POWERLINK,
}

string_type="string"
int_type = "int"
bool_type = "bool"
list_type = "list"
myconfigtypes = [string_type, string_type, bool_type, bool_type, string_type, int_type, list_type, bool_type, bool_type, int_type, int_type] #, list_type, bool_type, int_type, string_type, bool_type, bool_type, list_type, bool_type, int_type, int_type]

# Setup the command line parser
parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
parser.add_argument("-panel", help="visonic panel number", default="0")
parser.add_argument("-usb", help="visonic alarm usb device", default="")
parser.add_argument("-address", help="visonic alarm ip address", default="")
parser.add_argument("-port", help="visonic alarm ip port", type=int)
parser.add_argument("-baud", help="visonic alarm baud", type=int, default="9600")
parser.add_argument("-logfile", help="log file name to output to", default="")
parser.add_argument("-connect", help="connection mode: powerlink, standard, dataonly", default="powerlink")
parser.add_argument("-print", help="print mode: error, warning, info, debug", default="error")
args = parser.parse_args()

conn_type = "ethernet" if len(args.address) > 0 else "usb"
connection_mode = None
logger_level = None

def setConnectionMode(connect_mode):
    """Set the connection mode."""
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
    """Configure logger settings."""
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


# Convert byte array to a string of hex values
def toString(array_alpha: bytearray, gap = " "):
    """Convert bytearray for display."""
    return ("".join(("%02x"+gap) % b for b in array_alpha))[:-len(gap)] if len(gap) > 0 else ("".join(f"{b:02x}" for b in array_alpha))


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
    """Set up for Visonic devices."""

    def __init__(self, loop, config, logger):
        """Initialize the Visonic Client."""
        # Get the user defined config
        self.config = config
        self.loop = loop
        self.log = logger

        self.panel_exception_counter = 0
        self.visonicTask = None
        self.SystemStarted = False

        self.process_event = None
        self.process_log = None
        self.process_sensor = None
        self.process_x10 = None

        self.visonicProtocol = None
        self.cvp = None
        self.visonicCommsTask = None
        self.visonicProtocol : AlPanelInterface = None
        self.SystemStarted = False
        self._createdAlarmPanel = False
        self.doingReconnect = None

    def _initialise(self):
        pass

    def create_ha_notification(self, message: str):
        """Create a message in the log file and a notification on the HA Frontend."""
        print(f"Notification: {message}")

    def onSensorChange(self, sensor : AlSensorDevice, s : AlSensorCondition):
        """On sensor change callback."""
        if self.process_sensor is not None:
            self.process_sensor(sensor)
#        print(f"onSensorChange {s.name} {sensor}")

    def onSwitchChange(self, switch : AlSwitchDevice):
        """On switch change callback."""
        if self.process_x10 is not None:
            self.process_x10(switch)
#        print(f"onSwitchChange {switch}")

    def on_new_switch(self, create : bool, switch: AlSwitchDevice):
        """Process a new x10."""
        # Check to ensure variables are set correctly
        #print("on_new_switch")
        if switch is None:
            print("Visonic attempt to add X10 switch when sensor is undefined")
            return
        #print("VS: X10 Switch list ", switch)
        if switch.isEnabled():
            if self.process_x10 is not None:
                self.process_x10(switch)
                switch.onChange(self.onSwitchChange)

    def on_new_sensor(self, create : bool, sensor: AlSensorDevice):
        """Process a new sensor."""
        if sensor is None:
            print("Visonic attempt to add sensor when sensor is undefined")
            return
        if sensor.getDeviceID() is None:
            print("     Sensor ID is None")
        elif self.process_sensor is not None:
            self.process_sensor(sensor)
            sensor.onChange(self.onSensorChange)

    def onPanelChangeHandler(self, e: AlCondition, data : dict):
        """This is a callback function, called from the visonic library."""
        if isinstance(e, IntEnum):
            if self.process_event is not None:
                datadict = self.visonicProtocol.getEventData(None)
                self.process_event(e, datadict)
        else:
            print(f"Visonic attempt to call onPanelChangeHandler type {type(e)}  device is {e}")

#    def generate_ha_bus_error(self, e, datadictionary):
#        """ This is a callback function, called from the visonic library. """
#        if type(e) == AlError:
#            if self.process_event is not None:
#                self.process_event(e)
#        else:
#            print(f"Visonic attempt to call generate_ha_bus_error type {type(e)}  device is {e}")

    def toBool(self, val) -> bool:
        """Convert to bool."""
        if isinstance(val, bool):
            return val
        if isinstance(val, int):
            return val != 0
        if isinstance(val, str):
            v = val.lower()
            return v not in {"no", "false", "0"}
        print(f"Visonic unable to decode boolean value {val}    type is {type(val)}")
        return False

    def __getConfigData(self) -> PanelConfig:
        """Create a dictionary full of the configuration data."""
        v = self.config.get(CONF_EMULATION_MODE, ConnectionMode.POWERLINK)
        self.ForceStandardMode = v == ConnectionMode.STANDARD
        self.DisableAllCommands = v == ConnectionMode.DATAONLY

        if self.DisableAllCommands:
            self.ForceStandardMode = True
        # By the time we get here there are 3 combinations of self.DisableAllCommands and self.ForceStandardMode
        #     Both are False --> Try to get to Powerlink 
        #     self.ForceStandardMode is True --> Force Standard Mode, the panel can still be armed and disarmed
        #     self.ForceStandardMode and self.DisableAllCommands are True --> The integration interacts with the panel but commands such as arm/disarm/log/bypass are not allowed
        # The if statement above ensure these are the only supported combinations.

        print(f"Emulation Mode {v}   so setting    ForceStandard to {self.ForceStandardMode}     DisableAllCommands to {self.DisableAllCommands}")

        return {
            AlConfiguration.DownloadCode: self.config.get(CONF_DOWNLOAD_CODE, ""),
            AlConfiguration.ForceStandard: self.ForceStandardMode,
            AlConfiguration.DisableAllCommands: self.DisableAllCommands
        }

    def onDisconnect(self, excep, another_parameter):
        """Callback when the connection to the panel is disrupted."""
        if excep is None:
            print("AlVisonic has caused an exception, no exception information is available")
        else:
            print(f"AlVisonic has caused an exception {excep!s} {another_parameter!s}")
        # General update trigger
        #    0 is a disconnect and (hopefully) reconnect from an exception (probably comms related)
        sleep(5.0)
        print(" ........... setting up reconnection")
        self.panel_exception_counter += 1
        asyncio.create_task(self.disconnect_callback_async(excep))

    def getPanel(self):
        """Get the panel id."""
        return self.panel

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
                return sock

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
        path = path
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

    async def _terminate_comms_task(self):
        if self.visonicCommsTask is not None:
            print("........... Closing down Current Comms Task (to close the rs232/socket connection)")
            # Close the protocol handler 
            if self.cvp is not None:
                self.cvp.close()
            # Stop the comms task
            try:
                self.visonicCommsTask.cancel()
            except Exception as ex:
                # Do not cause a full Home Assistant Exception, keep it local here
                print("...........      Caused an exception")
                print(f"                    {ex}")   
            # Make sure its all stopped
            await asyncio.sleep(0.5)
            if self.visonicCommsTask is not None and self.visonicCommsTask.done():
                print("........... Current Comms Task Done")
            else:
                print("........... Current Comms Task Not Done")
        # Indicate that both have been stopped
        self.visonicCommsTask = None
        self.cvp = None

    def setup_panel_connect_comms(self, force=False, event_id=None, datadictionary=None):
        """Setup panel comms."""
        if self.doingRestart is not None:
            print("Not Setting up panel reconnection, already doing Restart")
        elif self.doingReconnect is None:
            print("Setting up panel reconnection")
            self.doingReconnect = self.loop.create_task(self.async_panel_start(force))
        else:
            print("Not Setting up panel reconnection, already in progress")

    async def async_service_panel_reconnect(self, call=None, force=False):
        """Service call to re-connect the comms connection."""
        # This is callable from frontend and checks user permission
        try:
            if self.SystemStarted:
                print(f"Reconnecting Comms to Visonic Panel {self.getPanelID()}")
                self.setup_panel_connect_comms(force)
            else:
                print(f"Sorry, a simple Reconnection is not possible to Visonic Panel {self.getPanelID()} as system has stopped and lost all context, so please Reload")
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            print(f"........... async_service_panel_reconnect, caused exception {ex}")

    async def async_panel_stop(self, *args, **kwargs):
        """Service call to stop the connection."""
        try:
            if self.SystemStarted:
                # stop the usb/ethernet comms with the panel
                await self._terminate_comms_task()
                # Shutdown the protocol handler and any tasks it uses
                if self.visonicProtocol is not None:
                    self.visonicProtocol.shutdown()

            # Reset all variables, include setting self.SystemStarted to False
            self._initialise()
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            print(f"........... async_panel_stop, caused exception {ex}")

    async def async_panel_start(self, force=False) -> bool:
        """Service call to start the connection."""

        async def connect_comms() -> bool:
            """Create the comms connection to the alarm panel."""
            await self._terminate_comms_task()
            # Connect in the way defined by the user in the config file, ethernet or usb
            if self.visonicProtocol is not None:
                self.visonicProtocol.resetMessageData()
                # Get Visonic specific configuration.
                print(f"Reconnection Device Type is {conn_type}")
                if conn_type == "ethernet":
                    host = args.address
                    port = args.port
                    (self.visonicCommsTask, self.cvp) = await self.async_create_tcp_visonic_connection(vp=self.visonicProtocol, address=host, port=port)
                elif conn_type == "usb":
                    path = args.usb
                    (self.visonicCommsTask, self.cvp) = await self.async_create_usb_visonic_connection(vp=self.visonicProtocol, path=path, baud=self.baud_rate)
                return self.cvp is not None and self.visonicCommsTask is not None
            return False

        try:
            if CONF_DEVICE_BAUD in self.config:
                self.baud_rate = self.config.get(CONF_DEVICE_BAUD, 9600)
            else:
                self.baud_rate = args.baud
            self.delayBetweenAttempts = 10
            self.totalAttempts = 1

            attemptCounter = 0
            while force or attemptCounter < self.totalAttempts:
                print(f"........... connection attempt {attemptCounter + 1} of {self.totalAttempts}")

    #            if await self.connect_to_alarm():
                if await connect_comms():
                    # Connection to the panel has been initially successful
                    print("........... connection made")
                    self.doingReconnect = None
                    return True
                # Failed so set up for next loop around
                print("........... connection not made")
                attemptCounter += 1
                force = False
                if attemptCounter < self.totalAttempts:
                    print(f"........... connection attempt delay {self.delayBetweenAttempts} seconds")
                    try:
                        await asyncio.sleep(self.delayBetweenAttempts)
                    except Exception:
                        print("........... connection attempt delay exception")

            # Set all variables to their defaults, this means that no connection has been made
            self._initialise()

            self.create_ha_notification(f"Failed to connect into Visonic Alarm Panel {self.getPanelID()}. Check Your Network and the Configuration Settings.")
            print("Giving up on trying to connect, sorry")
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            print(f"........... async_panel_start, caused exception {ex}")

        self.doingReconnect = None
        return False

    def getPanelID(self):
        """Get the panel id."""
        return args.panel

    async def async_connect(self, force=True) -> bool:
        """Connect to the alarm panel using the pyvisonic library."""
        if self.SystemStarted:
            print("Request to Start and the integraion is already running and connected")
        else:
            self.visonicProtocol = None
            try:
                print("Client Creating VP")
                try:
                    self.visonicProtocol = VisonicProtocol(panelConfig=self.config, panel_id=args.panel, loop=self.loop)
                except Exception as ex:
                    print(ex)

                print("Client connecting.....")
                if await self.async_panel_start(force=force):
                    print("Client connected .....")
                    self.visonicProtocol.on_panel_change(self.onPanelChangeHandler)
                    self.visonicProtocol.on_new_sensor(self.on_new_sensor)
                    self.visonicProtocol.on_new_switch(self.on_new_switch)
                    # Establish a callback to stop the component when the stop event occurs
                    #self.bus.async_listen_once(
                    #    EVENT_HOMEASSISTANT_STOP, self.async_panel_stop
                    #)
                    # Record that we have started the system
                    self.SystemStarted = True
                    # Assume that platforms have (or are being) loaded
                    self.unloadedPlatforms = False
                    return True

                self.visonicProtocol = None

            except (ConnectTimeout, HTTPError) as ex:
                self.create_ha_notification("Visonic Panel Connection Error: {ex}<br />You will need to restart hass after fixing.")

        if not self.SystemStarted and self.visonicProtocol is not None:
            print("........... Shutting Down Protocol")
            self.visonicProtocol.shutdown()
            self.visonicProtocol = None
        return False

    def hasUnloadedPlatforms(self):
        """Check is unloaded platforms."""
        return self.unloadedPlatforms

    async def async_panel_restart(self, force=False):
        """Restart."""
        try:
            # Deschedule point to allow other threads to complete
            await asyncio.sleep(0.0)
            # If already in the middle of a reconnection sequence then kill it
            if self.doingReconnect is not None:
                # kill it
                print("........... async_panel_restart, there is already an ongoing reconnection so stopping it as this restart takes precedence")
                try:
                    self.doingReconnect.cancel()
                except Exception as ex:
                    print("...........             Caused an exception")
                    print(f"                           {ex}")   
                while not self.doingReconnect.done():
                    await asyncio.sleep(0.0)
                self.doingReconnect = None
                print("........... async_panel_restart,                  ............... Ongoing Reconnection has been stopped")
            # Deschedule point to allow other threads to complete
            await asyncio.sleep(0.0)
            if self.SystemStarted:
                # If not already stopped, then stop the integrations connection to the panel
                print("........... async_panel_restart, stopping panel interaction")
                await self.async_panel_stop()  # this should set self.SystemStarted to False
                print("........... async_panel_restart, unloading platforms")
                #self.unloadedPlatforms = await self.hass.config_entries.async_unload_platforms(self.entry, PLATFORMS)

            print("........... async_panel_restart, attempting reconnection")
            await self.async_connect(force=force)
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            print(f"........... async_panel_restart, caused exception {ex}")

        self.doingReconnect = None
        self.doingRestart = None


    async def disconnect_callback_async(self, excep):
        """Service call to disconnect."""
        print(" ........... attempting reconnection")
        await self.service_panel_stop()
        await self.service_panel_start()

    async def service_panel_download(self, call):
        """Service call to download the panels EPROM."""
        if self.visonicProtocol is not None:
            await self.visonicProtocol.startDownloadAgain()

    def updateConfig(self, conf=None):
        """Update the dictionary full of configuration data."""
        #print("[updateConfig] entry")
        if conf is not None:
            self.config = conf
        if self.visonicProtocol is not None:
            self.visonicProtocol.updateSettings(self.__getConfigData())
        #print("[updateConfig] exit")

    def is_siren_active(self) -> (bool, AlSensorDevice | None):
        """Is the siren active."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.is_siren_active()
        return (False, None)

    def is_panel_ready(self, partition : int) -> bool:
        """Is panel ready."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.is_panel_ready(partition)
        return False

    def getPanelStatus(self, partition : int) -> AlPanelStatus:
        """Get the panel status code."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelStatus(partition)
        return AlPanelStatus.UNKNOWN

    def get_panel_mode(self) -> AlPanelMode:
        """Get the panel mode."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.get_panel_mode()
        return AlPanelMode.UNKNOWN

    def get_event_log(self, code : str) -> AlCommandStatus:
        """Get the panel mode."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.get_event_log(code)
        return AlCommandStatus.FAIL_INVALID_STATE

    def isSystemStarted(self) -> bool:
        """Is system started?"""
        return self.SystemStarted

    def get_partitions_in_use(self) -> set:
        """Get partitions in use."""
        return self.visonicProtocol.get_partitions_in_use() if self.visonicProtocol is not None else {1}

    def sendCommand(self, command : AlPanelCommand, code : str, partitions : set = {1,2,3}) -> AlCommandStatus:
        """Send a command to the panel."""
        if self.visonicProtocol is not None:
            # def panel_command(self, state : AlPanelCommand, code : str = "")
            return self.visonicProtocol.panel_command(command, code, partitions)
        return AlCommandStatus.FAIL_INVALID_STATE

    def getJPG(self, device : int, count : int) -> AlCommandStatus:
        """Get the jpg image."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getJPG(device, count)
        return AlCommandStatus.FAIL_INVALID_STATE

    def sendBypass(self, devid, bypass, code) -> AlCommandStatus:
        """Send the bypass command to the panel."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.bypass_command(devid, bypass, code)
        return AlCommandStatus.FAIL_INVALID_STATE

    def send_switch(self, ident, state) -> AlCommandStatus:
        """Send an X10 command to the panel."""
        if self.visonicProtocol is not None:
            return self.visonicProtocol.send_switch(ident, state)
        return AlCommandStatus.FAIL_INVALID_STATE

    def installHandlers(self, process_event = None, process_log = None, process_sensor = None, process_x10 = None):
        """Install the handlers."""
        self.process_event = process_event
        self.process_log = process_log
        self.process_sensor = process_sensor
        self.process_x10 = process_x10

    async def connect(self) -> bool:
        """Main function to connect to the panel."""
        try:
            success = await self.async_connect()
            if success:
                return True

        except Exception:
            ex = sys.exc_info()[0]
            print("Unable to connect to Visonic Alarm Panel: " + str(ex))
        return False

class MyAsyncConsole(aconsole.AsyncConsole):
    """Async console."""

    def __init__(self, **tkargs):
        """Initialise."""
        # super init
        super().__init__(**tkargs)
        self.geometry("1600x600")

    def setOutputFontSize(self, s : int):
        """Set the font size."""
        if self.running:
            self._AsyncConsole__output_text.config(font=('Courier New', s))

    def setInputFontSize(self, s : int):
        """Set the input font size."""
        if self.running:
            self._AsyncConsole__input_text.config(font=('Courier New', s))
            self._AsyncConsole__input_prompt.config(font=('Courier New', s))

async def controller(client : VisonicClient, console : MyAsyncConsole):
    """Overall controller."""

    def process_event(event_id : AlCondition, data : dict = None):
        # event means there's been a panel state change
        if event_id is not AlCondition.PUSH_CHANGE:
            console.print(f"Visonic update event condition {str(event_id)} {data}")

    def process_log(event_log_entry : AlLogPanelEvent):
        """Process a sequence of panel log events."""
        data = {
            "current": event_log_entry.current,  # only used for output and not logic,
            "total": event_log_entry.total,
            "date": event_log_entry.dateandtime,
            #"time": event_log_entry.time,
            "partition": event_log_entry.partition,
            "zone": event_log_entry.zone,
            "event": event_log_entry.event,
        }
        console.print("Event log " + str(data))

    def process_sensor(dev):
        """Process sensor."""
        if dev.getDeviceID() is None:
            console.print("Sensor ID is None")
        else:
            #console.print("process_sensor " + str(dev.getDeviceID()))
            if dev not in sensors:
                console.print("Adding Sensor " + str(dev))
                sensors.append(dev)
            if dev.isTriggered():
                console.print(f"Device {dev.getDeviceID()} Triggered")
            else:
                console.print(f"Device {dev.getDeviceID()} Settings have been updated, open = {dev.isOpen()}")

    def process_x10(dev):
        """Process switch."""
        if dev.enabled:
            if dev.getDeviceID() is None:
                console.print("X10 is None")
            elif dev not in devices:
                console.print("X10 ", str(dev))
                devices.append(dev)

    def str2bool(v):
        """String to bool."""
        return v.lower() in ("yes", "true", "t", "1")

    def updateVariable(i, v):
        """Update a variable."""
        c = 1
        for key, value in myconfig.items():
            if c == i:
                console.print("Setting " + str(key) + " to " + v)
                try:
                    if myconfigtypes[c-1] == string_type:
                        myconfig[key] = str(v)
                    elif myconfigtypes[c-1] == int_type:
                        myconfig[key] = int(v)
                    elif myconfigtypes[c-1] == bool_type:
                        console.print("Updating boolean " + str(str2bool(v)))
                        myconfig[key] = str2bool(v)
                    elif myconfigtypes[c-1] == list_type:
                        myconfig[key] = list(v.split(","))
                    else:
                        console.print("ERROR: Sorry but you must have the wrong type for that setting")
                except:
                    console.print("ERROR: Sorry but you must have the wrong type for that setting")
            c += 1

    def getCode(ar, p):
        code = None
        if len(ar) > p:
            code = ar[p].strip()
        return code

    def help():
        """Show help."""
        console.print("")
        console.print("===================   Help   ===================")
        console.print("")
        console.print("Mode                 Report a single line status")
        console.print("Arm <code>           Arm Away")
        console.print("Stay <code>          Arm Stay/Home")
        console.print("Trigger <code>       Trigger the Siren (PowerMaster panels only)")
        console.print("Disarm <code>        Disarm the panel")
        console.print("Log <code>           Retrieve the panels log file (this takes a few minutes)")
        console.print("Jpg <X> <C>          Download jpg images from zone X, optionally add an image count C but it doesn't work properly")
        console.print("Quit                 Quit the programme")
        console.print("Connect Mode         Connect to the panel (when not connected) Mode: Powerlink, Standard, DataOnly")
        console.print("Close                Close the connection to the panel (when connected)")
        console.print("Output Mode          Output mode: Debug, Info, Warning, Error")
        console.print("Print                Display the sensors and switches")
        console.print("Variables            Display the configuration settings")
        console.print("Bypass <int> <code>  Bypass a sensor <the sensor number>")
        console.print("Rearm <int> <code>   Rearm a sensor <the sensor number>")
        console.print("<int>=<setting>      Integer ref to variable and a setting (remember some are only used on connection)")
        console.print("Help                 This help information")
        console.print("")
        console.print("   <code> is optional in all cases")
        console.print("   You only need to type the first character of each command")
        console.print("   You can use cursor up/down for previous commands")
        console.print("")

    #print("Installing Handlers")
    client.installHandlers(process_event=process_event, process_log=process_log, process_sensor=process_sensor, process_x10=process_x10)

    console.clear_output()
    console.setOutputFontSize(10)
    console.setInputFontSize(12)
    sensors = []
    devices = []

    prompt1 = '<help, quit, variables, print, output, connect>: '
    prompt2 = '<help, quit, variables, print, output, close, jpg, mode, trigger, arm, stay, disarm, log, bypass, rearm>: '
    prompt = prompt1

    try:
        while True:
            result = await console.input(prompt)
            #console.print('echo:', result)
            if len(result) == 0:
                console.print("")
            else:
                command = result[0]
                ar = result.split(' ')
                processedInput = False
                #print(f"Command Received {command}")
                if client.isSystemStarted():
                    # There must be a panel connection to do the following commands
                    if command == 'c':
                        print("Closing connection")
                        console.clear_output()
                        await client.async_panel_stop()
                        sensors = []
                        devices = []
                        prompt = prompt1
                        processedInput = True
                    elif command == 'm':
                        if (part := client.get_partitions_in_use()) is not None:
                            for p in part:
                                pready = client.is_panel_ready(p)
                                pstate = client.getPanelStatus(p)
                                siren, _ = client.is_siren_active()
                                mode = client.get_panel_mode()
                                console.print(f"Panel Mode={mode.name}    Partition={p}     Partition state={pstate.name}    Partition Ready={str(pready)}   Siren={str(siren)}")
                        else:
                            pready = client.is_panel_ready(1)
                            pstate = client.getPanelStatus(1)
                            siren, _ = client.is_siren_active()
                            mode = client.get_panel_mode()
                            console.print(f"Panel Mode={mode.name}    Partition state={pstate.name}    Partition Ready={str(pready)}   Siren={str(siren)}")

                        processedInput = True
                    elif command == 'd':
                        client.sendCommand(AlPanelCommand.DISARM, getCode(ar,1))
                        processedInput = True
                    elif command == 'a':
                        client.sendCommand(AlPanelCommand.ARM_AWAY, getCode(ar,1))
                        processedInput = True
                    elif command == 's':
                        client.sendCommand(AlPanelCommand.ARM_HOME, getCode(ar,1))
                        processedInput = True
                    elif command == 't':
                        client.sendCommand(AlPanelCommand.TRIGGER, getCode(ar,1))
                        processedInput = True
                    elif command == 'j':
                        if len(ar) > 1:
                            devid=int(ar[1].strip())
                            count = 3
                            if len(ar) > 2:
                                count = int(ar[2].strip())
                            client.getJPG(devid, count)
                        processedInput = True
                    elif command == 'l':
                        client.get_event_log(getCode(ar,1))
                        processedInput = True
                    elif command == 'b':
                        if len(ar) > 1:
                            devid=int(ar[1].strip())
                            client.sendBypass(devid, True, getCode(ar,2))
                        processedInput = True
                    elif command == 'r':
                        if len(ar) > 1:
                            devid=int(ar[1].strip())
                            client.sendBypass(devid, False, getCode(ar,2))
                        processedInput = True

                if not processedInput:
                    if command == 'h':
                        help()
                    elif command == 'o':
                        #  output mode 
                        if len(ar) > 1:
                            mode=str(ar[1].strip()).lower()
                            #console.print(f"Setting output mode to {mode} :{mode[0]}:")
                            ConfigureLogger(mode, console)
                        else:
                            console.print("Current output level is " + str(logger_level))
                    elif command == 'q':
                        #  we are disconnected and so quit the program
                        #print("Terminating program")
                        raise Exception(terminating_clean)
                    elif not client.isSystemStarted() and command == 'c':
                        if len(ar) > 1:
                            mode=str(ar[1].strip()).lower()
                            setConnectionMode(mode)
                        console.clear_output()
                        console.print("Attempting connection, demanded mode is " + str(connection_mode))
                        console.print("")
                        success = await client.connect()
                        if success:
                            prompt = prompt2
                    elif command == 'v':
                        # list the config variables
                        c = 1
                        console.print("")
                        for key, value in myconfig.items():
                            s = str(key)
                            console.print(f"{c} :  {s} = {value}")
                            c += 1
                        console.print("")
                    elif command.isnumeric():
                        x = result.split('=')
                        if len(x) == 2:
                            if len(x[0]) > 0 and len(x[1]) > 0:
                                updateVariable(int(x[0].strip()), x[1].strip())
                                client.updateConfig(conf = myconfig)
                    elif command == 'p':
                        for sensor in sensors:
                            console.print("Sensor " + str(sensor))
                        for device in devices:
                            console.print("Device " + str(device))
                    else:
                        console.print("ERROR: invalid command " + result)

        print("Here ZZZZZZZ")

    except Exception as e:
        #print("Got an exception")
        #print(e.message)
        # Get current system exception
        ex_type, ex_value, ex_traceback = sys.exc_info()

        if str(ex_value) != terminating_clean:
            print(f"Exception {len(terminating_clean)} {len(ex_value)}")
            print("Exception: ")
            print(f"  type : {ex_type.__name__}")
            print(f"  message : {ex_value}")

            # Extract stack traces
            trace_back = traceback.extract_tb(ex_traceback)
            for trace in trace_back:
                print(f"File : {trace[0]} , Line : {trace[1]}, Func.Name : {trace[2]}, Message : {trace[3]}")

        if client is not None and client.isSystemStarted():
            print("Please wait .... disconnecting from panel")
            await client.async_panel_stop()
        raise e

def handle_exception(loop, context):
    """Handle exceptions."""

    def _createPrefix() -> str:
        previous_frame = currentframe().f_back
        (
            filepath,
            line_number,
            function,
            lines,
            index,
        ) = inspect.getframeinfo(previous_frame)
        filename = filepath[filepath.rfind('/')+1:]
        s = f"{filename} {line_number:<5} {function:<30} "
        previous_frame = currentframe()
        (
            filepath,
            line_number,
            function,
            lines,
            index,
        ) = inspect.getframeinfo(previous_frame)
        filename = filepath[filepath.rfind('/')+1:]

        return s + f"{filename} {line_number:<5} {function:<30} "

    # context["message"] will always be there; but context["exception"] may not
    msg = context.get("exception", context["message"])
    if str(msg) != terminating_clean:
        print(f"Caught exception: {msg}")
        print(f"                  {context}")
        #print(f"                  {_createPrefix()}")
    asyncio.create_task(shutdown(loop))

async def shutdown(loop, signal=None):
    """Cleanup tasks tied to the service's shutdown."""
    if signal:
        print(f"Received exit signal {signal.name}...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

if __name__ == '__main__':
    # Set up the asyncio first and then we don't get the debug data messages
    testloop = asyncio.new_event_loop()
    asyncio.set_event_loop(testloop)
    testloop.set_exception_handler(handle_exception)

    log = setupLocalLoggerBasic()
    setupLocalLogger("ERROR", empty = True)   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
    ConfigureLogger(str(args.print).lower(), None)
    setConnectionMode(str(args.connect).lower())

    client = VisonicClient(loop = testloop, config = myconfig, logger = log)

    if client is not None:
        success = True #client.connect(wait_sleep=False, wait_loop=True)
        if success:
            try:
                console = MyAsyncConsole()
                #console.__init_ui = MethodType(my_init_ui, console)
                console.title('Visonic Alarm Panel Test')
                ml = testloop.create_task(console.mainloop())
                c = testloop.create_task(controller(client, console))
                testloop.run_forever()
            except KeyboardInterrupt:
                # cleanup connection
                print("Keyboard Interrupt")
            except Exception:
                print("General Exception")
            finally:
                #print("Goodbye cruel world")
                testloop.close()
