""" Create a commandline connection to a Visonic PowerMax or PowerMaster Alarm System """
# set the parent directory on the import path
import os,sys,inspect,traceback
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(1000000,parentdir) 

import asyncio
import logging
import time
import collections
import argparse
from time import sleep
from collections import defaultdict
import socket

from pyconst import AlIntEnum, AlTransport, PanelConfig, AlConfiguration, AlPanelMode, AlPanelCommand, AlPanelStatus, AlTroubleType, AlAlarmType, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlSensorDevice, AlLogPanelEvent, AlSensorType, AlSwitchDevice
from pyvisonic import VisonicProtocol

CONF_DEVICE_TYPE = "type"
CONF_DEVICE_BAUD = "baud"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PATH = "path"

# config parameters for myconfig, just to make the defaults easier
CONF_DOWNLOAD_CODE = "download_code"
CONF_LANGUAGE = "language"
CONF_EMULATION_MODE = "emulation_mode"
CONF_SIREN_SOUNDING = "siren_sounding"

class ConnectionMode(Enum):
    POWERLINK = 1
    STANDARD = 2
    DATAONLY = 3

myconfig = { 
    CONF_DOWNLOAD_CODE: "",
    CONF_EMULATION_MODE: ConnectionMode.POWERLINK,
    CONF_LANGUAGE: "EN",
    CONF_SIREN_SOUNDING: ["Intruder"]
}

def toBool(val) -> bool:
    if type(val) == bool:
        return val
    elif type(val) == int:
        return val != 0
    elif type(val) == str:
        v = val.lower()
        return not (v == "no" or v == "false" or v == "0")
    print(f"Visonic unable to decode boolean value {val}    type is {type(val)}")
    return False


string_type="string"
int_type = "int"
bool_type = "bool"
list_type = "list"
myconfigtypes = [string_type, string_type, int_type, string_type, int_type, string_type, bool_type, bool_type, bool_type, string_type, bool_type, list_type, bool_type, int_type, string_type, bool_type, bool_type, list_type, bool_type, int_type, int_type]

class MyTransport(AlTransport):
 
    def __init__(self, t):
        self.transport = t
    
    def write(self, b : bytearray):
        self.transport.write(b)

    def close(self):
        self.transport.close()

class ClientVisonicProtocol(asyncio.Protocol, VisonicProtocol):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def data_received(self, data):
        super().vp_data_received(data)

    def connection_made(self, transport):
        self.trans = MyTransport(t=transport)
        super().vp_connection_made(self.trans)

    def connection_lost(self, exc):
        super().vp_connection_lost(exc)

    # This is needed so we can create the class instance before giving it to the protocol handlers
    def __call__(self):
        return self

def getConfigData() -> PanelConfig:
    """ Create a dictionary full of the configuration data. """
    v = self.config.get(CONF_EMULATION_MODE, ConnectionMode.POWERLINK)        
    self.ForceStandardMode = v == ConnectionMode.STANDARD
    self.DisableAllCommands = v == ConnectionMode.DATAONLY

    if DisableAllCommands:
        ForceStandardMode = True
    # By the time we get here there are 3 combinations of self.DisableAllCommands and self.ForceStandardMode
    #     Both are False --> Try to get to Powerlink 
    #     self.ForceStandardMode is True --> Force Standard Mode, the panel can still be armed and disarmed
    #     self.ForceStandardMode and self.DisableAllCommands are True --> The integration interacts with the panel but commands such as arm/disarm/log/bypass are not allowed
    # The if statement above ensure these are the only supported combinations.

    print(f"Emulation Mode {myconfig.get(CONF_EMULATION_MODE)}   so setting    ForceStandard to {ForceStandardMode}     DisableAllCommands to {DisableAllCommands}")

    return {
        AlConfiguration.DownloadCode: myconfig.get(CONF_DOWNLOAD_CODE, ""),
        AlConfiguration.ForceStandard: ForceStandardMode,
        AlConfiguration.DisableAllCommands: DisableAllCommands,
        AlConfiguration.PluginLanguage: myconfig.get(CONF_LANGUAGE, "EN"),
        AlConfiguration.SirenTriggerList: myconfig.get(CONF_SIREN_SOUNDING, ["Intruder"])
    }

def callback_handler(visonic_devices, dict={}):

    if visonic_devices == None:
        _LOGGER.debug("Visonic attempt to add device when sensor is undefined")
        return
    if type(visonic_devices) == defaultdict:
        _LOGGER.debug("Visonic got new sensors {0}".format(visonic_devices))
    elif type(visonic_devices) == pyvisonic.SensorDevice:
        # This is an update of an existing device
        _LOGGER.debug("Visonic got a sensor update {0}".format(visonic_devices))
    elif type(visonic_devices) == int:
        _LOGGER.debug("Visonic got an Event {0} {1}".format(visonic_devices,dict))
    else:
        _LOGGER.debug("Visonic attempt to add device with type {0}  device is {1}".format(type(visonic_devices), visonic_devices))

def onNewSwitch(dev: AlSwitchDevice): 
    """Process a new x10."""
    # Check to ensure variables are set correctly
    #print("onNewSwitch")
    if dev is None:
        print("Visonic attempt to add X10 switch when sensor is undefined")
        return
    #print("VS: X10 Switch list ", dev)
    if dev.isEnabled():
        if dev.getDeviceID() is None:
            print("X10 is None")
        else:
            print("X10 ", str(dev))

def onNewSensor(sensor: AlSensorDevice):
    """Process a new sensor."""
    #print("onNewSensor")
    if sensor is None:
        print("Visonic attempt to add sensor when sensor is undefined")
        return
    if sensor.getDeviceID() is None:
        print("Sensor ID is None")
    else:
        print("Sensor ", str(sensor))

def onPanelChangeHandler(e):
    """ This is a callback function, called from the visonic library. """
    print(f"onPanelChangeHandler {type(e)}  value {e}")

def onDisconnect(excep):
    """ Callback when the connection to the panel is disrupted """
    if excep is None:
        print("AlVisonic has caused an exception, no exception information is available")
    else:
        print("AlVisonic has caused an exception %s", str(excep))

def process_log(event_log_entry):
    print("process_log ", event_log_entry)


# Create a connection using asyncio using an ip and port
async def async_create_tcp_visonic_connection(address, port, panelConfig : PanelConfig = None, loop=None):
    """Create Visonic manager class, returns tcp transport coroutine."""
    
    if loop is None:
        print ("Loop is None and it shouldn't be")

    loop=loop if loop else asyncio.get_event_loop()
    
    #print("Setting address and port")
    address = address
    port = int(port)

    sock = None
    try:
        print(f"Setting TCP socket Options {address} {port}")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setblocking(1)  # Set blocking to on, this is the default but just make sure
        sock.settimeout(1.0)  # set timeout to 1 second to flush the receive buffer
        sock.connect((address, port))

        # Flush the buffer, receive any data and dump it
        try:
            dummy = sock.recv(10000)  # try to receive 100 bytes
            print("Buffer Flushed and Received some data!")
        except socket.timeout:  # fail after 1 second of no activity
            #print("Buffer Flushed and Didn't receive data! [Timeout]")
            pass

        # set the timeout to infinite
        sock.settimeout(None)

        vp = ClientVisonicProtocol(panelConfig=panelConfig, loop=loop)

        #print("The vp " + str(type(vp)) + "   with value " + str(vp))
        # create the connection to the panel as an asyncio protocol handler and then set it up in a task
        coro = loop.create_connection(vp, sock=sock)

        #print("The coro type is " + str(type(coro)) + "   with value " + str(coro))
        visonicTask = loop.create_task(coro)

        return visonicTask, vp

    except socket.error as _:
        err = _
        print("Setting TCP socket Options Exception {0}".format(err))
        if sock is not None:
            sock.close()
    except Exception as exc:
        print("Setting TCP Options Exception {0}".format(exc))
    return None, None

# Create a connection using asyncio through a linux port (usb or rs232)
async def async_create_usb_visonic_connection(path, baud="9600", panelConfig : PanelConfig = None, loop=None):
    """Create Visonic manager class, returns rs232 transport coroutine."""
    from serial_asyncio import create_serial_connection
    loop=loop if loop else asyncio.get_event_loop()
    # setup serial connection
    path = path
    baud = int(baud)
    try:
        vp = ClientVisonicProtocol(panelConfig=panelConfig, loop=loop)
        # create the connection to the panel as an asyncio protocol handler and then set it up in a task
        conn = create_serial_connection(loop, vp, path, baud)
        visonicTask = loop.create_task(conn)
        return visonicTask, vp
    except:
        print("Setting USB Options Exception")
    return None, None

async def startitall(testloop):
    visonicTask = None 
    visonicProtocol = None
    if len(args.address) > 0:
        print("Setting up TCP Connection")
        visonicTask, visonicProtocol = await async_create_tcp_visonic_connection(address=args.address, port=args.port, loop=testloop, panelConfig=getConfigData())
    elif len(args.usb) > 0:
        print("Setting up USB Connection")
        visonicTask, visonicProtocol = await async_create_usb_visonic_connection(path=args.usb, loop=testloop, panelConfig=getConfigData())
    else:
        print("No Valid Connection Configuration")
    
    if visonicTask is not None and visonicProtocol is not None:
        #visonicProtocol.onPanelError(onPanelChangeHandler)
        visonicProtocol.onPanelChange(onPanelChangeHandler)
        # visonicProtocol.onPanelEvent(onPanelChangeHandler)
        visonicProtocol.onPanelLog(process_log)
        visonicProtocol.onDisconnect(onDisconnect)
        visonicProtocol.onNewSensor(onNewSensor)
        visonicProtocol.onNewSwitch(onNewSwitch)
        
        while True:
            print("You can do stuff here with visonicProtocol, Mode=", visonicProtocol.getPanelMode())
            await asyncio.sleep(5.0)
    else:
        print("Please check your command line parameters")

def setupLocalLogger(level: str = "WARNING", logfile = False):
    from datetime import datetime, timedelta
    import logging
    root_logger = logging.getLogger()
    
    class ElapsedFormatter:
        def __init__(self):
            self.start_time = time.time()

        def format(self, record):
            #print(f"record {record}")
            elapsed_seconds = record.created - self.start_time
            # using timedelta here for convenient default formatting
            elapsed = str(timedelta(seconds=elapsed_seconds))
            return "{: <15} <{: <15}:{: >5}> {: >8}   {}".format(elapsed, record.filename, record.lineno, record.levelname, record.getMessage())

    # add custom formatter to root logger
    formatter = ElapsedFormatter()
    shandler = logging.StreamHandler(stream=sys.stdout)
    shandler.setFormatter(formatter)
    if logfile:
        fhandler = logging.FileHandler("log.txt", mode="w")
        fhandler.setFormatter(formatter)
        root_logger.addHandler(fhandler)

    #root_logger.propagate = False
    root_logger.addHandler(shandler)

    # level = logging.getLevelName('INFO')
    level = logging.getLevelName(level)  # INFO, DEBUG
    root_logger.setLevel(level)

def handle_exception(loop, context):
    # context["message"] will always be there; but context["exception"] may not
    msg = context.get("exception", context["message"])
    #print(f"Caught exception: {msg}")
    #print(f"                  {context}")
 
if __name__ == '__main__':
    setupLocalLogger("DEBUG", False)   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
    #logging.basicConfig(level=logging.DEBUG)
    #_LOGGER = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
    parser.add_argument("-usb", help="visonic alarm usb device", default="")
    parser.add_argument("-address", help="visonic alarm ip address", default="")
    parser.add_argument("-port", help="visonic alarm ip port", type=int)
    args = parser.parse_args()

    testloop = asyncio.get_event_loop()
    testloop.set_exception_handler(handle_exception)

    task = testloop.create_task(startitall(testloop))
    try:
        #print("Calling run_forever")
        testloop.run_forever()
    except KeyboardInterrupt:
        pass
    except:
        pass
    finally:
        # cleanup connection
        print("Cleaning up")
        #testloop.close()
