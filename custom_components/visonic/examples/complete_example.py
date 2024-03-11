"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""
#! /usr/bin/python3

# set the parent directory on the import path
import os,sys,inspect,traceback
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(1000000,parentdir) 
import time
import json
import asyncio
from collections import defaultdict
from time import sleep
from datetime import datetime
from pyconst import AlIntEnum, AlTransport, PanelConfig, AlConfiguration, AlPanelMode, AlPanelCommand, AlPanelStatus, AlTroubleType, AlAlarmType, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlSensorDevice, AlLogPanelEvent, AlSensorType, AlSwitchDevice
import pyvisonic 
import argparse
import re

from pyvisonic import VisonicProtocol
import socket

# Try to import aconsole, if it fails then print an error message
try:
    import aconsole
except:
    print("")
    print("You need to install the aconsole library like this:  pip install -r requirements.txt")
    print("")
    print("This will install the necessary python libraries for you")
    print("")
    sys.exit(0)

# config parameters for myconfig, just to make the defaults easier
CONF_DOWNLOAD_CODE = "download_code"
CONF_FORCE_AUTOENROLL = "force_autoenroll"
CONF_AUTO_SYNC_TIME = "sync_time"
CONF_LANGUAGE = "language"
CONF_FORCE_STANDARD = "force_standard"

CONF_MOTION_OFF_DELAY = "motion_off"
CONF_SIREN_SOUNDING = "siren_sounding"
CONF_EEPROM_ATTRIBUTES = "show_eeprom_attributes"

# Temporary B0 Config Items
CONF_B0_ENABLE_MOTION_PROCESSING = "b0_enable_motion_processing"
CONF_B0_MIN_TIME_BETWEEN_TRIGGERS = "b0_min_time_between_triggers"
CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT = "b0_max_time_for_trigger_event"

parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
parser.add_argument("-panel", help="visonic panel number", default="0")
parser.add_argument("-usb", help="visonic alarm usb device", default="")
parser.add_argument("-address", help="visonic alarm ip address", default="")
parser.add_argument("-port", help="visonic alarm ip port", type=int)
parser.add_argument("-baud", help="visonic alarm baud", type=int, default="9600")
args = parser.parse_args()

conn_type = "ethernet" if len(args.address) > 0 else "usb"

myconfig = { 
    CONF_DOWNLOAD_CODE: "",
    CONF_FORCE_STANDARD: False,
    CONF_FORCE_AUTOENROLL: True,
    CONF_AUTO_SYNC_TIME : True,
    CONF_LANGUAGE: "EN",
    CONF_MOTION_OFF_DELAY: 30,
    CONF_SIREN_SOUNDING: ["Intruder"],
    CONF_EEPROM_ATTRIBUTES: False,
    CONF_B0_ENABLE_MOTION_PROCESSING: False,
    CONF_B0_MIN_TIME_BETWEEN_TRIGGERS: 5,
    CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT: 30
}

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


class VisonicClient:
    """Set up for Visonic devices."""

    def __init__(self, loop, config):
        """Initialize the Visonic Client."""
        # Get the user defined config
        self.config = config
        self.loop = loop

        self.panel_exception_counter = 0
        self.visonicTask = None
        self.SystemStarted = False

        self.process_event = None
        self.process_log = None
        self.process_sensor = None
        self.process_x10 = None

        # variables for creating the event log for csv and xml
        self.visonicProtocol = None
        #print(f"init self.config = {PYVConst.DownloadCode}  {self.config}")

    def onSensorChange(self, sensor : AlSensorDevice, s : AlSensorCondition):
        if self.process_sensor is not None:
            self.process_sensor(sensor)
#        print("onSensorChange {0} {1}".format(s.name, sensor) )
#        self.sendSensor(sensor)
        
    def onSwitchChange(self, switch : AlSwitchDevice):
        if self.process_x10 is not None:
            self.process_x10(switch)
#        print("onSwitchChange {0}".format(switch))

    def onNewSwitch(self, switch: AlSwitchDevice): 
        """Process a new x10."""
        # Check to ensure variables are set correctly
        #print("onNewSwitch")
        if switch is None:
            print("Visonic attempt to add X10 switch when sensor is undefined")
            return
        #print("VS: X10 Switch list ", switch)
        if switch.isEnabled():
            if self.process_x10 is not None:
                self.process_x10(switch)
                switch.onChange(self.onSwitchChange)

    def onNewSensor(self, sensor: AlSensorDevice):
        """Process a new sensor."""
        if sensor is None:
            print("Visonic attempt to add sensor when sensor is undefined")
            return
        if sensor.getDeviceID() is None:
            print("     Sensor ID is None")
        else:
            #print("     Sensor ", str(sensor))
#            self.sendSensor(sensor)
            if self.process_sensor is not None:
                self.process_sensor(sensor)
                sensor.onChange(self.onSensorChange)

    def onPanelChangeHandler(self, e):
        """ This is a callback function, called from the visonic library. """
        if type(e) == AlIntEnum:
            if self.process_event is not None:
                self.process_event(e, self.visonicProtocol.setLastEventData())
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
        if type(val) == bool:
            return val
        elif type(val) == int:
            return val != 0
        elif type(val) == str:
            v = val.lower()
            return not (v == "no" or v == "false" or v == "0")
        print(f"Visonic unable to decode boolean value {val}    type is {type(val)}")
        return False

    def __getConfigData(self) -> PanelConfig:
        """ Create a dictionary full of the configuration data. """
        return {
            AlConfiguration.DownloadCode: self.config.get(CONF_DOWNLOAD_CODE, ""),
            AlConfiguration.ForceStandard: self.toBool(
                self.config.get(CONF_FORCE_STANDARD, False)
            ),
            AlConfiguration.AutoEnroll: self.toBool(
                self.config.get(CONF_FORCE_AUTOENROLL, True)
            ),
            AlConfiguration.AutoSyncTime: self.toBool(
                self.config.get(CONF_AUTO_SYNC_TIME, True)
            ),
            AlConfiguration.PluginLanguage: self.config.get(CONF_LANGUAGE, "EN"),
            AlConfiguration.MotionOffDelay: self.config.get(CONF_MOTION_OFF_DELAY, 120),
            AlConfiguration.SirenTriggerList: self.config.get(
                CONF_SIREN_SOUNDING, ["Intruder"]
            ),
            AlConfiguration.EEPROMAttributes: self.toBool(
                self.config.get(CONF_EEPROM_ATTRIBUTES, False)
            ),
            AlConfiguration.B0_Enable: self.toBool(
                self.config.get(CONF_B0_ENABLE_MOTION_PROCESSING, False)
            ),
            AlConfiguration.B0_Min_Interval_Time: self.config.get(
                CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, 5
            ),
            AlConfiguration.B0_Max_Wait_Time: self.config.get(
                CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, 30
            ),
        }


    def onDisconnect(self, excep):
        """ Callback when the connection to the panel is disrupted """
        if excep is None:
            print("AlVisonic has caused an exception, no exception information is available")
        else:
            print("AlVisonic has caused an exception %s", str(excep))
        # General update trigger
        #    0 is a disconnect and (hopefully) reconnect from an exception (probably comms related)
        sleep(5.0)
        print(" ........... setting up reconnection")
        self.panel_exception_counter = self.panel_exception_counter + 1
        asyncio.create_task(self.disconnect_callback_async(excep))

    def getPanel(self):
        return self.panel


    # Create a connection using asyncio using an ip and port
    async def async_create_tcp_visonic_connection(self, address, port, panelConfig : PanelConfig = None, loop=None):
        """Create Visonic manager class, returns tcp transport coroutine."""
        loop = loop if loop else asyncio.get_event_loop()
        
        #print("Setting address and port")
        address = address
        port = int(port)

        sock = None
        try:
            print("Setting TCP socket Options")
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
    async def async_create_usb_visonic_connection(self, path, baud="9600", panelConfig : PanelConfig = None, loop=None):
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


    async def __connect_to_alarm(self) -> bool:
        """ Create the connection to the alarm panel """
        #import pyvisonic as visonicApi  # Connection to python Library

        # Is the system already running and connected
        if self.SystemStarted:
            return False

        #print("connect_to_alarm self.config = %s", self.config)

        conn_type = "ethernet" if len(args.address) > 0 else "usb"

        #print(f"Visonic Connection Device Type is {conn_type}") #, self.__getConfigData())

        # update config parameters (local in hass[DOMAIN] mainly)
        self.updateConfig()

        self.visonicTask = None
        self.visonicProtocol = None
        
        self.panel = args.panel
        
        # Connect in the way defined by the user in the config file, ethernet or usb
        if conn_type == "ethernet":
            self.visonicTask, self.visonicProtocol = await self.async_create_tcp_visonic_connection(
                address=args.address,
                port=str(args.port),
                panelConfig=self.__getConfigData()
                # loop=self.loop
            )

        elif conn_type == "usb":
            self.visonicTask, self.visonicProtocol = await self.async_create_usb_visonic_connection(
                path=args.usb,
                baud=args.baud,
                panelConfig=self.__getConfigData()
                # loop=self.loop
            )

        if self.visonicTask is not None and self.visonicProtocol is not None:
            # Connection to the panel has been initially successful
            #self.visonicProtocol.onPanelError(self.generate_ha_bus_error)
            self.visonicProtocol.onPanelChange(self.onPanelChangeHandler)
            #self.visonicProtocol.onPanelEvent(self.onPanelChangeHandler)
            self.visonicProtocol.onPanelLog(self.process_log)
            self.visonicProtocol.onDisconnect(self.onDisconnect)
            self.visonicProtocol.onNewSensor(self.onNewSensor)
            self.visonicProtocol.onNewSwitch(self.onNewSwitch)
            # Record that we have started the system
            self.SystemStarted = True
            return True

        self.visonicTask = None
        print("Failed to connect into Visonic Alarm. Check Settings.")
        return False

    async def service_comms_stop(self):
        """ Service call to close down the current serial connection, we need to reset the whole connection!!!! """
        if not self.SystemStarted:
            print("Request to Stop the Comms and it is already stopped")
            return

        # Try to get the asyncio Coroutine within the Task to shutdown the serial link connection properly
        if self.visonicProtocol is not None:
            self.visonicProtocol.shutdownOperation()
        await asyncio.sleep(0.5)
        # not a mistake, wait a bit longer to make sure it's closed as we get no feedback (we only get the fact that the queue is empty)

    async def service_panel_stop(self):
        """ Service call to stop the connection """
        if not self.SystemStarted:
            print("Request to Stop the HA alarm_control_panel and it is already stopped")
            return
        # cancel the task from within HA
        if self.visonicTask is not None:
            print("          ........... Closing down Current Task")
            self.visonicTask.cancel()
            await asyncio.sleep(2.0)
            if self.visonicTask.done():
                print("          ........... Current Task Done")
            else:
                print("          ........... Current Task Not Done")
        else:
            print("          ........... Current Task not set")
        self.SystemStarted = False

    async def service_panel_start(self):
        """ Service call to start the connection """
        if self.SystemStarted:
            print("Request to Start the HA alarm_control_panel and it is already running")
            return

        # re-initialise global variables, do not re-create the queue as we can't pass it to the alarm control panel. There's no need to create it again anyway
        self.visonicTask = None

        print("........... attempting connection")

        alarm_entity_exists = False

        if self.__connect_to_alarm():
            print("Success - connected to panel")
        else:
            print("Failure - not connected to panel")

    async def service_panel_reconnect(self, call):
        """ Service call to re-connect the connection """
        print("User has requested visonic panel reconnection")
        await self.service_comms_stop()
        await self.service_panel_stop()
        await self.service_panel_start()

    async def service_panel_disconnect(self):
        """ Service call to re-connect the connection """
        print("User has requested visonic panel disconnection")
        await self.service_comms_stop()
        await self.service_panel_stop()

    async def disconnect_callback_async(self, excep):
        """ Service call to disconnect """
        print(" ........... attempting reconnection")
        await self.service_panel_stop()
        await self.service_panel_start()

    async def service_panel_download(self, call):
        """ Service call to download the panels EPROM """
        if self.visonicProtocol is not None:
            await self.visonicProtocol.startDownloadAgain()

    def updateConfig(self, conf=None):
        """ Update the dictionary full of configuration data. """
        if conf is not None:
            self.config = conf
        if self.visonicProtocol is not None:
            self.visonicProtocol.updateSettings(self.__getConfigData())

    def getPanelLastEvent(self) -> str:
        """ Is the siren active. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelLastEvent()
        return False

    def getPanelTrouble(self) -> AlTroubleType:
        """ Get the panel trouble state """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelTrouble()
        return AlTroubleType.UNKNOWN

    def isPanelBypass(self) -> bool:
        """ Is the siren active. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isPanelBypass()
        return False

    def isSirenActive(self) -> bool:
        """ Is the siren active. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isSirenActive()
        return False

    def isPanelReady(self) -> bool:
        """ Is panel ready. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isPanelReady()
        return False

    def getPanelStatus(self) -> AlPanelStatus:
        """ Get the panel status code. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelStatus()
        return AlPanelStatus.UNKNOWN

    def getPanelMode(self) -> AlPanelMode:
        """ Get the panel mode. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelMode()
        return AlPanelMode.UNKNOWN

    def getEventLog(self, code : str) -> AlCommandStatus:
        """ Get the panel mode. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getEventLog(code)
        return AlCommandStatus.FAIL_INVALID_STATE

    def isSystemStarted(self) -> bool:
        return self.SystemStarted

    def sendCommand(self, command : AlPanelCommand, code : str) -> AlCommandStatus:
        """ Send a command to the panel """
        if self.visonicProtocol is not None:
            # def requestArm(self, state : AlPanelCommand, code : str = "")
            return self.visonicProtocol.requestArm(command, code)
        return AlCommandStatus.FAIL_INVALID_STATE

    def sendBypass(self, devid, bypass, code) -> AlCommandStatus:
        """ Send the bypass command to the panel """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.setSensorBypassState(devid, bypass, code)
        return AlCommandStatus.FAIL_INVALID_STATE

    def setX10(self, ident, state) -> AlCommandStatus:
        """ Send an X10 command to the panel """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.setX10(ident, state)
        return AlCommandStatus.FAIL_INVALID_STATE
    
    def installHandlers(self, process_event = None, process_log = None, process_sensor = None, process_x10 = None):
        self.process_event = process_event
        self.process_log = process_log
        self.process_sensor = process_sensor
        self.process_x10 = process_x10

    async def connect(self) -> bool:
        """ Main function to connect to the panel """
        try:
            success = await self.__connect_to_alarm()
            if success:
                return True

        except:
            ex = sys.exc_info()[0]
            print("Unable to connect to Visonic Alarm Panel: " + str(ex))
        return False

class MyAsyncConsole(aconsole.AsyncConsole):

    def __init__(self, **tkargs):
        # super init
        super().__init__(**tkargs)
        self.geometry("1600x600")
       
    def setOutputFontSize(self, s : int):
        if self.running:
            self._AsyncConsole__output_text.config(font=('Courier New', s))

    def setInputFontSize(self, s : int):
        if self.running:
            self._AsyncConsole__input_text.config(font=('Courier New', s))
            self._AsyncConsole__input_prompt.config(font=('Courier New', s))

async def controller(client : VisonicClient, console : MyAsyncConsole):

    panel = args.panel
       
    def process_event(event_id : AlCondition, data : dict = None):
        # event means there's been a panel state change
        if event_id is not AlCondition.PUSH_CHANGE:
            console.print("Visonic update event condition {0} {1}".format(str(event_id), data))
       
    def process_log(event_log_entry : AlLogPanelEvent):
        """ Process a sequence of panel log events """
        total = event_log_entry.total
        current = event_log_entry.current  # only used for output and not logic
        data = {
            "current": current,
            "total": total,
            "date": event_log_entry.date,
            "time": event_log_entry.time,
            "partition": event_log_entry.partition,
            "zone": event_log_entry.zone,
            "event": event_log_entry.event,
        }
        console.print("Event log " + str(data))
    
    def process_sensor(dev):
        if dev.getDeviceID() is None:
            console.print("Sensor ID is None")
        else:
            #console.print("process_sensor " + str(dev.getDeviceID()))
            if dev not in sensors:
                console.print("Adding Sensor " + str(dev))
                sensors.append(dev)
            #print("Sensor Update")
            #self.sendSensor(dev)
    
    def process_x10(dev):
        if dev.enabled:
            if dev.getDeviceID() is None:
                console.print("X10 is None")
            else:
                if dev not in devices:
                    console.print("X10 ", str(dev))
                    devices.append(dev)
                #self.sendSwitch(dev)
        
    def str2bool(v):
        return v.lower() in ("yes", "true", "t", "1")

    def updateVariable(i, v):
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
            c = c + 1

    def getCode(ar, p):
        code = None
        if len(ar) > p:
            code = ar[p].strip()
        return code

    def help():
        console.print("")
        console.print("===================   Help   ===================")
        console.print("")
        console.print("Mode                 Report a single line status")
        console.print("Arm <code>           Arm Away")
        console.print("Stay <code>          Arm Stay/Home")
        console.print("Disarm <code>        Disarm the panel")
        console.print("Log <code>           Retrieve the panels log file (this takes a few minutes)")
        console.print("Quit                 Quit the programme")
        console.print("Connect              Connect to the panel (when not connected)")
        console.print("Close                Close the connection to the panel (when connected)")
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
    
    prompt1 = '<help, quit, variables, print, connect>: '
    prompt2 = '<help, quit, variables, print, close, mode, arm, stay, disarm, log, bypass, rearm>: '
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
                #print("Command Received {0}".format(command))
                if client.isSystemStarted():
                    # There must be a panel connection to do the following commands
                    if command == 'c':
                        print("Closing connection")
                        console.clear_output()
                        await client.service_panel_disconnect()
                        sensors = []
                        devices = []
                        prompt = prompt1
                        processedInput = True
                    elif command == 'm':
                        pready = client.isPanelReady()
                        pstate = client.getPanelStatus()
                        siren = client.isSirenActive();
                        mode = client.getPanelMode();
                        console.print("Panel Mode=" + mode.name + "    Panel state=" + pstate.name + "    Panel Ready=" + str(pready) + "    Siren=" + str(siren) )
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
                    elif command == 'l':
                        client.getEventLog(getCode(ar,1))
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
                    elif command == 'q':
                        #  we are disconnected and so quit the program
                        #print("Terminating program")
                        raise Exception('terminating_clean')
                    elif not client.isSystemStarted() and command == 'c':
                        console.clear_output()
                        success = await client.connect()
                        if success:
                            prompt = prompt2
                    elif command == 'v':
                        # list the config variables
                        c = 1
                        console.print("")
                        for key, value in myconfig.items():
                            s = str(key)
                            console.print("{0} :  {1} = {2}".format(c, s, value))
                            c = c + 1
                        console.print("")
                    elif command.isnumeric() == True:
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
                        console.print("ERROR: There must be a panel connection to perform command " + result)
        
        print("Here ZZZZZZZ")
        
    except Exception as e:
        #print("Got an exception")
        #print(e.message)
        # Get current system exception
        ex_type, ex_value, ex_traceback = sys.exc_info()

        if str(ex_value) != "terminating_clean":
            #print("Exception {0} {1}".format(len("terminating_clean"),len(ex_value)))
            print("Exception: ")
            print(f"  type : {ex_type.__name__}")
            print(f"  message : {ex_value}")

            # Extract stack traces
            trace_back = traceback.extract_tb(ex_traceback)
            for trace in trace_back:
                print(f"File : {trace[0]} , Line : {trace[1]}, Func.Name : {trace[2]}, Message : {trace[3]}")

        if client is not None and client.isSystemStarted():
            print("Please wait .... disconnecting from panel")
            await client.service_panel_disconnect()
        raise e   

def handle_exception(loop, context):
    # context["message"] will always be there; but context["exception"] may not
    msg = context.get("exception", context["message"])
    #print(f"Caught exception: {msg}")
    asyncio.create_task(shutdown(loop))

async def shutdown(loop, signal=None):
    """Cleanup tasks tied to the service's shutdown."""
    if signal:
        print(f"Received exit signal {signal.name}...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

#def main():
#    loop = asyncio.get_event_loop()
    # May want to catch other signals too
    

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
    
if __name__ == '__main__':
    setupLocalLogger("DEBUG", False)   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"

# class MyIntEnum(int):
    # ThisShouldNotHappen = "ThisShouldNotHappen"
    
# #    def __new__(cls, *args, **kwargs):
# #        instance = super().__new__(cls)
# #        return instance
        
    # def __init__(self, d = 0):
        # self.myname = self.ThisShouldNotHappen

# class TestType:
    # UNKNOWN = MyIntEnum(0)

# A = TestType.UNKNOWN
# print(f"type(A) = {type(A)}     A={A}")
  
    testloop = asyncio.get_event_loop()
    testloop.set_exception_handler(handle_exception)

    client = VisonicClient(loop = testloop, config = myconfig)
    if client is not None:
        success = True #client.connect(wait_sleep=False, wait_loop=True)
        if success:
            try:
                console = MyAsyncConsole()
                #console.__init_ui = MethodType(my_init_ui, console)
                console.title('Visonic Alarm Panel Test')                
                testloop.create_task(console.mainloop())
                testloop.create_task(controller(client, console))
                testloop.run_forever()
            except KeyboardInterrupt:
                # cleanup connection
                print("Keyboard Interrupt")
                pass
            except:
                print("General Exception")
                pass
            finally:
                #print("Goodbye cruel world")
                testloop.close()
