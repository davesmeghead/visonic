"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""
#! /usr/bin/python3

# Make sure Ruff ignores f-strings
# ruff: noqa: T201, BLE001

# python3 complete_example.py -usb /dev/ttyUSB0 -baud 38400 -print debug

from __future__ import annotations  # noqa: TID251

import argparse
import asyncio
from datetime import timedelta
from enum import Enum, IntEnum
import logging
from pathlib import Path
import sys
import time
import traceback

from requests import ConnectTimeout, HTTPError
from textual.app import App, ComposeResult
from textual.message import Message
from textual.widgets import Footer, Input, RichLog, Static

package_dir = Path(__file__).resolve().parent.parent
project_dir = package_dir.parent
sys.path.insert(0, str(project_dir))

from example_common import BasicConnection, ClientVisonicProtocol  # noqa: E402
from pyvisonic.py_abstract_classes import (  # noqa: E402
    AlCommandStatus,
    AlPanelCommand,
    AlPanelInterface,
    AlPanelMode,
    AlPanelStatus,
    AlSensorCondition,
    AlSensorDevice,
    AlSwitchDevice,
)
from pyvisonic.py_enum import AlCondition  # noqa: E402
from pyvisonic.py_visonic import VisonicProtocol  # noqa: E402  # noqa: E402

terminating_clean = "terminating_clean"

class PrintMode(Enum):
    """Print mode."""
    NONE = 0
    ERROR = 1
    WARNING = 2
    INFO = 3
    DEBUG = 4

# Setup the command line parser
parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
parser.add_argument("-panel", help="visonic panel number", default="0")
parser.add_argument("-usb", help="visonic alarm usb device", default="")
parser.add_argument("-baud", help="visonic alarm baud", type=int, default="9600")
parser.add_argument("-address", help="visonic alarm ip address", default="")
parser.add_argument("-port", help="visonic alarm ip port", type=int)
parser.add_argument("-logfile", help="log file name to output to", default="")
parser.add_argument("-connect", help="connection mode: powerlink, standard, dataonly", default="powerlink")
parser.add_argument("-print", help="print mode: error, warning, info, debug", default="error")
args = parser.parse_args()

conn_type = "ethernet" if len(args.address) > 0 else "usb"
connection_mode = None
logger_level = None

# Create new protocol
connect = args.connect
if len(args.connect) == 0:
    connect = "powerlink"
force_standard_mode = connect.lower() == "standard"
disable_all_panel_commands = connect.lower() == "dataonly"
if disable_all_panel_commands:
    force_standard_mode = True

if not force_standard_mode:
    connection_mode = "Powerlink"
elif disable_all_panel_commands:
    connection_mode = "Dataonly"
else:
    connection_mode = "Standard"

_visonic_handlers: list[logging.Handler] = []

def setupLocalLoggerBasic():
    """Local logging handler."""
    import logging  # noqa: PLC0415
    return logging.getLogger()

def setupLocalLogger(level: str = "WARNING", empty: bool = False) -> None:
    """Set up logging.

    Logging is deliberately NOT sent to stdout because Textual owns
    the terminal while the application is running.
    """
    global logger_level  # noqa: PLW0603
    global _visonic_handlers  # noqa: PLW0602

    root_logger = logging.getLogger()

    class ElapsedFormatter(logging.Formatter):
        """Format log messages with elapsed time."""

        def __init__(self):
            super().__init__()
            self.start_time = time.time()

        def format(self, record):
            elapsed_seconds = record.created - self.start_time
            elapsed = str(timedelta(seconds=elapsed_seconds))

            return (
                f"{elapsed: <15} "
                f"<{record.filename: <15}:{record.lineno: >5}> "
                f"{record.levelname: >8}   "
                f"{record.getMessage()}"
            )

    formatter = ElapsedFormatter()

    # Remove only handlers belonging to this application.
    #
    # Do NOT remove handlers belonging to Textual or any other
    # library/application.
    # Remove handlers belonging to this application.
    for handler in _visonic_handlers:
        root_logger.removeHandler(handler)
        handler.close()
    _visonic_handlers.clear()

    # ---------------------------------------------------------------
    # File logging
    # ---------------------------------------------------------------
    if args.logfile:
        fhandler = logging.FileHandler(
            args.logfile,
            mode="w" if empty else "a",
        )
        fhandler.setFormatter(formatter)
        root_logger.addHandler(fhandler)
        _visonic_handlers.append(fhandler)

    logger_level = level

    # Only change the level here.
    #
    # There is deliberately NO StreamHandler(sys.stdout).
    root_logger.setLevel(logging.getLevelName(level))


class TextualLogHandler(logging.Handler):
    """Send Python logging records to the Textual DEBUG LOG window."""

    def __init__(self, console: MyAsyncConsole):
        """Initialise the handler."""
        super().__init__()
        self.console = console

    def emit(self, record: logging.LogRecord) -> None:
        """Handle a logging record."""
        try:
            message = self.format(record)
            self.console.write_log(message)

        except Exception:
            # Don't allow logging failures to crash the application.
            self.handleError(record)

class MyAsyncConsole(App):
    """Textual based asynchronous console."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #debug_title {
        height: 1;
    }

    #debug_log {
        height: 15;
        border: solid $primary;
    }

    #output_title {
        height: 1;
    }

    #output {
        height: 1fr;
        border: solid $primary;
    }

    #prompt {
        height: 1;
        padding: 0 1;
    }

    #input {
        height: 3;
    }

    Footer {
        height: 1;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, **kwargs):
        """Initialise the console."""
        super().__init__(**kwargs)
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._input_widget: Input | None = None
        self._prompt_widget: Static | None = None
        self._output_widget: RichLog | None = None
        self._debug_widget: RichLog | None = None
        self._textual_log_handler: TextualLogHandler | None = None
        self._controller_task: asyncio.Task | None = None
        self.client = None

    def compose(self) -> ComposeResult:
        """Create the Textual UI."""
        yield Static("DEBUG LOG", id="debug_title")
        yield RichLog(
            id="debug_log",
            highlight=False,
            markup=False,
            wrap=False,
        )
        yield Static("OUTPUT", id="output_title")
        yield RichLog(
            id="output",
            highlight=True,
            markup=False,
            wrap=True,
        )
        yield Static("", id="prompt")
        yield Input(id="input")
        yield Footer()

    def on_mount(self) -> None:
        """Initialise widgets once mounted."""
        self._debug_widget = self.query_one("#debug_log", RichLog)
        self._output_widget = self.query_one("#output", RichLog)
        self._input_widget = self.query_one("#input", Input)
        self._prompt_widget = self.query_one("#prompt", Static)
        self._input_widget.focus()
        # Install the Textual logging handler only after the
        # Textual widgets actually exist.
        self._install_log_handler()
        self._controller_task = asyncio.create_task(
            controller(self.client, self)
        )

    def _install_log_handler(self) -> None:
        """Install the Textual logging handler."""
        if self._textual_log_handler is not None:
            return

        handler = TextualLogHandler(self)

        formatter = logging.Formatter(
            "%(asctime)s "
            "%(levelname)-8s "
            "%(name)s: "
            "%(message)s"
        )
        handler.setFormatter(formatter)
        self._textual_log_handler = handler
        logging.getLogger().addHandler(handler)
        _visonic_handlers.append(handler)

    def _remove_log_handler(self) -> None:
        """Remove the Textual logging handler."""
        if self._textual_log_handler is None:
            return
        root_logger = logging.getLogger()
        root_logger.removeHandler(
            self._textual_log_handler
        )
        self._textual_log_handler.close()
        self._textual_log_handler = None

    def write_log(self, message: str) -> None:
        """Write a message to the DEBUG LOG window."""
        #if self._debug_widget is not None:
        #    self._debug_widget.write(message)
        self.post_message(DebugMessage(message))

    def print(
        self,
        *args,
        sep: str = " ",
        end: str = "\n",
    ) -> None:
        """Print text into the OUTPUT window."""
        if self._output_widget is None:
            return

        text = sep.join(str(arg) for arg in args)

        if end != "\n":
            text += end

        #self._output_widget.write(text)
        self.post_message(OutputMessage(text))

    def clear_output(self) -> None:
        """Clear the OUTPUT window."""
        if self._output_widget is not None:
            self._output_widget.clear()

    def on_output_message(self, message: OutputMessage) -> None:
        """Write queued output to the OUTPUT window."""
        if self._output_widget is not None:
            self._output_widget.write(message.text)

    def on_debug_message(self, message: DebugMessage) -> None:
        """Write queued debug output to the DEBUG LOG window."""
        if self._debug_widget is not None:
            self._debug_widget.write(message.text)

    async def on_input_submitted(
        self,
        event: Input.Submitted,
    ) -> None:
        """Handle a command entered by the user."""
        await self._input_queue.put(event.value)
        # Clear input ready for the next command.
        event.input.value = ""

    async def input(self, prompt: str = "") -> str:
        """Wait asynchronously for the next command."""
        if self._prompt_widget is not None:
            self._prompt_widget.update(prompt)
        if self._input_widget is not None:
            self._input_widget.focus()
        result = await self._input_queue.get()
        if self._prompt_widget is not None:
            self._prompt_widget.update("")
        return result

    def quit(self) -> None:
        """Quit the application."""
        self.exit()

    async def on_unmount(self) -> None:
        """Clean up when Textual exits."""
        self._remove_log_handler()
        if self._controller_task is not None:
            self._controller_task.cancel()
            await asyncio.gather(
                self._controller_task,
                return_exceptions=True,
            )

class OutputMessage(Message):
    """Message containing text for the output window."""
    def __init__(self, text: str) -> None:
        """Init."""
        super().__init__()
        self.text = text

class DebugMessage(Message):
    """Message containing text for the debug window."""
    def __init__(self, text: str) -> None:
        """Init."""
        super().__init__()
        self.text = text

def ConfigureLogger(
    mode: str,
    console: MyAsyncConsole | None = None,
) -> None:
    """Configure logging level.

    This changes ONLY the logging level.

    It does NOT recreate logging handlers, which is important because
    Textual owns the terminal while it is running.
    """
    global logger_level  # noqa: PLW0603

    if not mode:
        return

    mode = mode.lower()

    levels = {
        "d": ("DEBUG", logging.DEBUG),
        "i": ("INFO", logging.INFO),
        "w": ("WARNING", logging.WARNING),
        "e": ("ERROR", logging.ERROR),
    }

    if mode[0] not in levels:
        if console is not None:
            console.print(
                f"Not Setting output mode, unknown mode {mode}"
            )
        return

    name, level = levels[mode[0]]
    logger_level = name
    logging.getLogger().setLevel(level)
    if console is not None:
        console.print(
            f"Setting output mode to {name}"
        )

# Convert byte array to a string of hex values
def toString(array_alpha: bytearray, gap = " "):
    """Convert bytearray for display."""
    return ("".join(("%02x"+gap) % b for b in array_alpha))[:-len(gap)] if len(gap) > 0 else ("".join(f"{b:02x}" for b in array_alpha))

class VisonicClient(BasicConnection):
    """Set up for Visonic devices."""

    def __init__(self, loop: asyncio.BaseEventLoop, logger):
        """Initialize the Visonic Client."""
        # Get the user defined config
        #self.config = config
        self.loop: asyncio.BaseEventLoop = loop
        self.log = logger

        self.panel_exception_counter = 0
        self.visonicTask = None
        self.SystemStarted = False

        self.process_event = None
        self.process_log = None
        self.process_sensor = None
        self.process_x10 = None

        self.cvp: ClientVisonicProtocol = None
        self.my_transport: asyncio.Transport = None
        self.visonic_protocol : AlPanelInterface = None
        self.SystemStarted = False
        self._createdAlarmPanel = False
        self.doingReconnect = None

    def _initialise(self):
        pass

    def create_ha_notification(self, message: str):
        """Create a message in the log file and a notification on the HA Frontend."""
        self.log.debug(f"Notification: {message}")  # noqa: G004

    def onSensorChange(self, sensor : AlSensorDevice, s : AlSensorCondition):
        """On sensor change callback."""
        if self.process_sensor is not None:
            self.process_sensor(sensor)
#        self.log.debug(f"onSensorChange {s.name} {sensor}")

    def onSwitchChange(self, switch : AlSwitchDevice):
        """On switch change callback."""
        if self.process_x10 is not None:
            self.process_x10(switch)
#        self.log.debug(f"onSwitchChange {switch}")

    def on_new_switch(self, create : bool, py_switch: AlSwitchDevice):
        """Process a new x10."""
        # Check to ensure variables are set correctly
        #self.log.debug("on_new_switch")
        if py_switch is None:
            self.log.debug("Visonic attempt to add X10 switch when sensor is undefined")
            return
        #self.log.debug("VS: X10 Switch list ", switch)
        if py_switch.enabled:
            if self.process_x10 is not None:
                self.process_x10(py_switch)
                py_switch.add_callback(self.onSwitchChange)

    def on_new_sensor(self, create : bool, py_sensor: AlSensorDevice):
        """Process a new sensor."""
        if py_sensor is None:
            self.log.debug("Visonic attempt to add sensor when sensor is undefined")
            return
        if py_sensor.id is None:
            self.log.debug("     Sensor ID is None")
        elif self.process_sensor is not None:
            self.process_sensor(py_sensor)
            py_sensor.add_callback(self.onSensorChange)

    def onPanelChangeHandler(self, e: AlCondition, data : dict):
        """This is a callback function, called from the visonic library."""
        if isinstance(e, IntEnum):
            if self.process_event is not None:
                datadict = self.visonic_protocol.get_panel_status_dict(None)
                self.process_event(e, datadict)
        else:
            self.log.debug(f"Visonic attempt to call onPanelChangeHandler type {type(e)}  device is {e}")  # noqa: G004

    def connection_status_callback(self):
        """Connection status callback."""

    async def _terminate_comms_task(self):
        if self.my_transport is not None:
            self.log.debug("........... Closing down Current Comms Task (to close the rs232/socket connection)")
            # Stop the comms task
            try:
                # Close the protocol handler
                if self.cvp is not None:
                    self.cvp.close()
                self.my_transport.close()
            except Exception as ex:
                # Do not cause a full Home Assistant Exception, keep it local here
                self.log.debug("...........      Caused an exception")
                self.log.debug(f"                    {ex}")  # noqa: G004
        # Indicate that both have been stopped
        self.my_transport = None
        self.cvp = None

    def setup_panel_connect_comms(self, force=False, event_id=None, datadictionary=None):
        """Setup panel comms."""
        if self.doingRestart is not None:
            self.log.debug("Not Setting up panel reconnection, already doing Restart")
        elif self.doingReconnect is None:
            self.log.debug("Setting up panel reconnection")
            self.doingReconnect = self.loop.create_task(self.async_panel_start(force))
        else:
            self.log.debug("Not Setting up panel reconnection, already in progress")

    async def async_service_panel_reconnect(self, call=None, force=False):
        """Service call to re-connect the comms connection."""
        # This is callable from frontend and checks user permission
        try:
            if self.SystemStarted:
                self.log.debug(f"Reconnecting Comms to Visonic Panel {self.getPanelID()}")  # noqa: G004
                self.setup_panel_connect_comms(force)
            else:
                self.log.debug(f"Sorry, a simple Reconnection is not possible to Visonic Panel {self.getPanelID()} as system has stopped and lost all context, so please Reload")  # noqa: G004
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            self.log.debug(f"........... async_service_panel_reconnect, caused exception {ex}")  # noqa: G004

    async def async_panel_stop(self, *args, **kwargs):
        """Service call to stop the connection."""
        try:
            if self.SystemStarted:
                # stop the usb/ethernet comms with the panel
                await self._terminate_comms_task()
                # Shutdown the protocol handler and any tasks it uses
                if self.visonic_protocol is not None:
                    self.visonic_protocol.shutdown()

            # Reset all variables, include setting self.SystemStarted to False
            self._initialise()
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            self.log.debug(f"........... async_panel_stop, caused exception {ex}")  # noqa: G004

    async def async_panel_start(self, force=False) -> bool:
        """Service call to start the connection."""

        async def connect_comms() -> bool:
            """Create the comms connection to the alarm panel."""
            await self._terminate_comms_task()
            # Connect in the way defined by the user in the config file, ethernet or usb
            if self.visonic_protocol is not None:
                #self.visonicProtocol.resetMessageData()
                # Get Visonic specific configuration.
                self.log.debug(f"Reconnection Device Type is {conn_type}")  # noqa: G004
                if conn_type == "ethernet":
                    host = args.address
                    port = args.port
                    (self.my_transport, self.cvp) = await self.async_create_tcp_visonic_connection(loop=self.loop, vp=self.visonic_protocol, connection_status_callback=self.connection_status_callback, address=host, port=port)
                elif conn_type == "usb":
                    path = args.usb
                    baud=args.baud
                    (self.my_transport, self.cvp) = await self.async_create_usb_visonic_connection(loop=self.loop, vp=self.visonic_protocol, connection_status_callback=self.connection_status_callback, path=path, baud=baud)
                return self.cvp is not None and self.my_transport is not None
            return False

        try:
            self.delayBetweenAttempts = 10
            self.totalAttempts = 1

            attempt_counter = 0
            while force or attempt_counter < self.totalAttempts:
                self.log.debug(f"........... connection attempt {attempt_counter + 1} of {self.totalAttempts}")  # noqa: G004

    #            if await self.connect_to_alarm():
                if await connect_comms():
                    # Connection to the panel has been initially successful
                    self.log.debug("........... connection made")
                    self.doingReconnect = None
                    return True
                # Failed so set up for next loop around
                self.log.debug("........... connection not made")
                attempt_counter += 1
                force = False
                if attempt_counter < self.totalAttempts:
                    self.log.debug(f"........... connection attempt delay {self.delayBetweenAttempts} seconds")  # noqa: G004
                    try:
                        await asyncio.sleep(self.delayBetweenAttempts)
                    except Exception:
                        self.log.debug("........... connection attempt delay exception")

            # Set all variables to their defaults, this means that no connection has been made
            self._initialise()

            self.create_ha_notification(f"Failed to connect into Visonic Alarm Panel {self.getPanelID()}. Check Your Network and the Configuration Settings.")
            self.log.debug("Giving up on trying to connect, sorry")
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            self.log.debug(f"........... async_panel_start, caused exception {ex}")  # noqa: G004

        self.doingReconnect = None
        return False

    def getPanelID(self):
        """Get the panel id."""
        return args.panel

    async def async_connect(self, force=True) -> bool:
        """Connect to the alarm panel using the pyvisonic library."""
        if self.SystemStarted:
            self.log.debug("Request to Start and the integraion is already running and connected")
        else:
            self.visonic_protocol = None
            try:
                self.log.debug(f"Client Creating VP {args.connect}")  # noqa: G004
                try:
                    if self.visonic_protocol is not None:
                        self.visonic_protocol.shutdown()
                    # Create new protocol
                    self.force_standard_mode = force_standard_mode
                    self.disable_all_panel_commands = disable_all_panel_commands
                    dlc = "AAAA"
                    #self.logger.logstate_debug("........... Creating Visonic Protocol")
                    self.visonic_protocol = VisonicProtocol(
                        force_standard_mode=self.force_standard_mode,
                        disable_all_commands=self.disable_all_panel_commands,
                        download_code=dlc,
                        user_code_slot=1,
                        loop=self.loop,
                    )
                except Exception as ex:
                    self.log.debug(ex)

                self.log.debug(f"Client connecting..... {connection_mode}")  # noqa: G004
                if await self.async_panel_start(force=force):
                    self.log.debug("Client connected .....")
                    self.visonic_protocol.on_panel_change(self.onPanelChangeHandler)
                    self.visonic_protocol.on_new_sensor(self.on_new_sensor)
                    self.visonic_protocol.on_new_switch(self.on_new_switch)
                    self.visonic_protocol.on_panel_event_log(self.process_log)
                    #self.visonic_protocol.set_log_events(self.language_decoder.getLogEventList())
                    #self.visonic_protocol.on_problem(self.on_panel_problem)
                    #self.visonic_protocol.on_new_device(self.on_new_device)
                    # Establish a callback to stop the component when the stop event occurs
                    #self.bus.async_listen_once(
                    #    EVENT_HOMEASSISTANT_STOP, self.async_panel_stop
                    #)
                    # Record that we have started the system
                    self.SystemStarted = True
                    # Assume that platforms have (or are being) loaded
                    self.unloadedPlatforms = False
                    return True

                self.visonic_protocol = None

            except (ConnectTimeout, HTTPError) as ex:
                self.create_ha_notification(f"Visonic Panel Connection Error: {ex}<br />You will need to restart hass after fixing.")

        if not self.SystemStarted and self.visonic_protocol is not None:
            self.log.debug("........... Shutting Down Protocol")
            self.visonic_protocol.shutdown()
            self.visonic_protocol = None
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
                self.log.debug("........... async_panel_restart, there is already an ongoing reconnection so stopping it as this restart takes precedence")
                try:
                    self.doingReconnect.cancel()
                except Exception as ex:
                    self.log.debug("...........             Caused an exception")
                    self.log.debug(f"                           {ex}")  # noqa: G004
                while not self.doingReconnect.done():
                    await asyncio.sleep(0.0)
                self.doingReconnect = None
                self.log.debug("........... async_panel_restart,                  ............... Ongoing Reconnection has been stopped")
            # Deschedule point to allow other threads to complete
            await asyncio.sleep(0.0)
            if self.SystemStarted:
                # If not already stopped, then stop the integrations connection to the panel
                self.log.debug("........... async_panel_restart, stopping panel interaction")
                await self.async_panel_stop()  # this should set self.SystemStarted to False
                self.log.debug("........... async_panel_restart, unloading platforms")
                #self.unloadedPlatforms = await self.hass.config_entries.async_unload_platforms(self.entry, PLATFORMS)

            self.log.debug("........... async_panel_restart, attempting reconnection")
            await self.async_connect(force=force)
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            self.log.debug(f"........... async_panel_restart, caused exception {ex}")  # noqa: G004

        self.doingReconnect = None
        self.doingRestart = None

    def is_siren_active(self, partition : int) -> tuple[bool, AlSensorDevice | None]:
        """Is the siren active."""
        if self.visonic_protocol is not None:
            return self.visonic_protocol.is_siren_active(partition)
        return (False, None)

    def is_panel_ready(self, partition : int) -> bool:
        """Is panel ready."""
        if self.visonic_protocol is not None:
            return self.visonic_protocol.is_panel_ready(partition)
        return False

    def get_partition_status(self, partition : int) -> AlPanelStatus:
        """Get the panel status code."""
        if self.visonic_protocol is not None:
            return self.visonic_protocol.get_partition_status(partition)
        return AlPanelStatus.UNKNOWN

    def get_panel_mode(self) -> AlPanelMode:
        """Get the panel mode."""
        if self.visonic_protocol is not None:
            return self.visonic_protocol.get_panel_mode()
        return AlPanelMode.UNKNOWN

    def get_event_log(self, code : str) -> AlCommandStatus:
        """Get the panel mode."""
        if self.visonic_protocol is not None:
            return self.visonic_protocol.get_event_log(code)
        return AlCommandStatus.FAIL_INVALID_STATE

    def isSystemStarted(self) -> bool:
        """Is system started?"""
        return self.SystemStarted

    def get_partitions_in_use(self) -> set:
        """Get partitions in use."""
        return self.visonic_protocol.get_partitions_in_use() if self.visonic_protocol is not None else {1}

    def sendCommand(self, command : AlPanelCommand, code : str, partitions : set | None = None) -> AlCommandStatus:
        """Send a command to the panel."""
        if partitions is None:
            partitions = {0, 1, 2}
        if self.visonic_protocol is not None:
            # def panel_command(self, state : AlPanelCommand, code : str = "")
            return self.visonic_protocol.panel_command(command, code, partitions)
        return AlCommandStatus.FAIL_INVALID_STATE

    def getJPG(self, device : int, count : int) -> AlCommandStatus:
        """Get the jpg image."""
        if self.visonic_protocol is not None:
            return self.visonic_protocol.get_sensor_image(device, count)
        return AlCommandStatus.FAIL_INVALID_STATE

    def sendBypass(self, devid, bypass, code) -> AlCommandStatus:
        """Send the bypass command to the panel."""
        if self.visonic_protocol is not None:
            return self.visonic_protocol.bypass_command(devid, bypass, code)
        return AlCommandStatus.FAIL_INVALID_STATE

    def send_switch(self, ident, state) -> AlCommandStatus:
        """Send an X10 command to the panel."""
        if self.visonic_protocol is not None:
            return self.visonic_protocol.send_switch(ident, state)
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
            self.log.debug("Unable to connect to Visonic Alarm Panel: " + str(ex))  # noqa: G003
        return False

async def controller(client : VisonicClient, console : MyAsyncConsole):  # noqa: C901
    """Overall controller."""

    def process_event(event_id : AlCondition, data : dict | None = None):
        # event means there's been a panel state change
        if event_id is not AlCondition.PUSH_CHANGE:
            console.print(f"Visonic update event condition {event_id} {data}")

    def process_log(total, current, partition, dateandtime, zone, event):
        """Process a sequence of panel log events."""
        data = {
            "current": current,  # only used for output and not logic,
            "total": total,
            "date": dateandtime,
            #"time": event_log_entry.time,
            "partition": partition,
            "zone": zone,
            "event": event,
        }
        console.print("Event log " + str(data))

    def process_sensor(dev: AlSensorDevice):
        """Process sensor."""
        if dev.id is None:
            console.print("Sensor ID is None")
        else:
            #console.print("process_sensor " + str(dev.id))
            if dev not in sensors:
                console.print("Adding Sensor " + str(dev))
                sensors.append(dev)
            if dev.triggered:
                console.print(f"Device {dev.id} Triggered")
            else:
                console.print(f"Device {dev.id} Settings have been updated, open = {dev.is_open}")

    def process_x10(dev: AlSwitchDevice):
        """Process switch."""
        if dev.enabled:
            if dev.id is None:
                console.print("X10 is None")
            elif dev not in devices:
                console.print("X10 ", str(dev))
                devices.append(dev)

    def getCode(ar, p):
        code = None
        if len(ar) > p:
            code = ar[p].strip()
        return code

    def command_help():
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
        console.print("Jpg <X> <C>          Download jpg images from zone X, optionally add an image count C")
        console.print("Quit                 Quit the programme")
        #console.print("Connect Mode         Connect to the panel (when not connected) Mode: Powerlink, Standard, DataOnly")
        console.print("Close                Close the connection to the panel (when connected)")
        console.print("Output Mode          Output mode: Debug, Info, Warning, Error")
        console.print("Print                Display the sensors and switches")
        #console.print("Variables            Display the configuration settings")
        console.print("Bypass <int> <code>  Bypass a sensor <the sensor number>")
        console.print("Rearm <int> <code>   Rearm a sensor <the sensor number>")
        #console.print("<int>=<setting>      Integer ref to variable and a setting (remember some are only used on connection)")
        console.print("Help                 This help information")
        console.print("")
        console.print("   <code> is optional in all cases")
        console.print("   You only need to type the first character of each command")
        console.print("   You can use cursor up/down for previous commands")
        console.print("")

    #print("Installing Handlers")
    client.installHandlers(process_event=process_event, process_log=process_log, process_sensor=process_sensor, process_x10=process_x10)

    console.clear_output()
    sensors = []
    devices = []

    prompt1 = '<help, quit, print, output, connect>: '
    prompt2 = '<help, quit, print, output, close, jpg, mode, trigger, arm, stay, disarm, log, bypass, rearm>: '
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
                processed_input = False
                #self.log.debug(f"Command Received {command}")
                if client.isSystemStarted():
                    # There must be a panel connection to do the following commands
                    if command == 'c':
                        ("Closing connection")
                        console.clear_output()
                        await client.async_panel_stop()
                        sensors = []
                        devices = []
                        prompt = prompt1
                        processed_input = True
                    elif command == 'm':
                        if (part := client.get_partitions_in_use()) is not None:
                            mode = client.get_panel_mode()
                            console.print(f"Panel Mode={mode.name}")
                            for p in part:
                                pstate = client.get_partition_status(p)
                                pready = client.is_panel_ready(p)
                                siren, _, _ = client.is_siren_active(p)
                                console.print(f"     Partition={p}    Ready={pready}   State={pstate}   Siren={siren}")
                        else:
                            pready = client.is_panel_ready(0)
                            pstate = client.get_partition_status(0)
                            siren, _, _ = client.is_siren_active(0)
                            mode = client.get_panel_mode()
                            console.print(f"Panel Mode={mode.name}    Ready={pready}   State={pstate}    Siren={siren}")
                        processed_input = True
                    elif command == 'd':
                        client.sendCommand(AlPanelCommand.DISARM, getCode(ar,1))
                        processed_input = True
                    elif command == 'a':
                        client.sendCommand(AlPanelCommand.ARM_AWAY, getCode(ar,1))
                        processed_input = True
                    elif command == 's':
                        client.sendCommand(AlPanelCommand.ARM_HOME, getCode(ar,1))
                        processed_input = True
                    elif command == 't':
                        client.sendCommand(AlPanelCommand.TRIGGER, getCode(ar,1))
                        processed_input = True
                    elif command == 'j':
                        if len(ar) > 1:
                            devid=int(ar[1].strip())
                            count = 3
                            if len(ar) > 2:
                                count = int(ar[2].strip())
                            client.getJPG(devid, count)
                        processed_input = True
                    elif command == 'l':
                        client.get_event_log(getCode(ar,1))
                        processed_input = True
                    elif command == 'b':
                        if len(ar) > 1:
                            devid=int(ar[1].strip())
                            client.sendBypass(devid, True, getCode(ar,2))
                        processed_input = True
                    elif command == 'r':
                        if len(ar) > 1:
                            devid=int(ar[1].strip())
                            client.sendBypass(devid, False, getCode(ar,2))
                        processed_input = True

                if not processed_input:
                    if command == 'h':
                        command_help()
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
                        #self.log.debug("Terminating program")
                        console.quit()
                        return
                    elif not client.isSystemStarted() and command == 'c':
                        if len(ar) > 1:
                            mode=str(ar[1].strip()).lower()
                        console.clear_output()
                        #print(f"Hello {connection_mode}")
                        console.print("Attempting connection, demanded mode is " + str(connection_mode))
                        console.print("")
                        await asyncio.sleep(0)
                        success = await client.connect()
                        if success:
                            prompt = prompt2
                    #elif command == 'v':
                    #    # list the config variables
                    #    c = 1
                    #    console.print("")
                    #    for key, value in myconfig.items():
                    #        s = str(key)
                    #        console.print(f"{c} :  {s} = {value}")
                    #        c += 1
                    #    console.print("")
                    #elif command.isnumeric():
                    #    x = result.split('=')
                    #    if len(x) == 2:
                    #        if len(x[0]) > 0 and len(x[1]) > 0:
                    #            updateVariable(int(x[0].strip()), x[1].strip())
                    #            client.updateConfig(conf = myconfig)
                    elif command == 'p':
                        for sensor in sensors:
                            console.print("Sensor " + str(sensor))
                        for device in devices:
                            console.print("Device " + str(device))
                    else:
                        console.print("ERROR: invalid command " + result)

        print("Here ZZZZZZZ")

    except Exception:
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

def handle_exception(loop, context):
    """Handle exceptions."""
    msg = context.get("exception", context["message"])
    if str(msg) != terminating_clean:
        print(f"Caught exception: {msg}")
        print(f"                  {context}")

async def main() -> None:
    """Run the Visonic example application."""

    setupLocalLogger(
        "DEBUG",
        empty=True,
    )

    client = VisonicClient(
        loop=asyncio.get_running_loop(),
        logger=logging.getLogger(),
    )

    console = MyAsyncConsole()
    console.title = "Visonic Alarm Panel Test"
    console.client = client

    try:
        await console.run_async()
    finally:
        if client.isSystemStarted():
            await client.async_panel_stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Keyboard Interrupt")
    except Exception as ex:
        print(f"General Exception {ex}")
