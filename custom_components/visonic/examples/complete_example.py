"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""
#! /usr/bin/python3

# set the parent directory on the import path
import os,sys,inspect
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(1000000,parentdir) 

import asyncio
from collections import defaultdict
from time import sleep
import pyvisonic 
import argparse
from pconst import PyConfiguration, PyPanelMode, PyPanelCommand, PyPanelStatus, PyCommandStatus, PyX10Command, PyCondition, PySensorDevice, PyLogPanelEvent, PySensorType, PySwitchDevice

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
CONF_DEVICE_TYPE = "type"
CONF_DEVICE_BAUD = "baud"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PATH = "path"
CONF_DOWNLOAD_CODE = "download_code"
CONF_FORCE_AUTOENROLL = "force_autoenroll"
CONF_AUTO_SYNC_TIME = "sync_time"
CONF_LANGUAGE = "language"
CONF_FORCE_STANDARD = "force_standard"

CONF_MOTION_OFF_DELAY = "motion_off"
CONF_SIREN_SOUNDING = "siren_sounding"

# Temporary B0 Config Items
CONF_B0_ENABLE_MOTION_PROCESSING = "b0_enable_motion_processing"
CONF_B0_MIN_TIME_BETWEEN_TRIGGERS = "b0_min_time_between_triggers"
CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT = "b0_max_time_for_trigger_event"


parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
parser.add_argument("-usb", help="visonic alarm usb device", default="")
parser.add_argument("-address", help="visonic alarm ip address", default="")
parser.add_argument("-port", help="visonic alarm ip port", type=int)
parser.add_argument("-baud", help="visonic alarm baud", type=int, default="9600")
args = parser.parse_args()

conn_type = "ethernet" if len(args.address) > 0 else "usb"

myconfig = { 
    CONF_DEVICE_TYPE: conn_type,    # then path and baud are used (as this is for a direct RS232 as well).
    CONF_HOST: args.address,
    CONF_PORT: str(args.port),
    CONF_PATH: args.usb,
    CONF_DEVICE_BAUD: args.baud,
    CONF_DOWNLOAD_CODE: "",
    CONF_FORCE_STANDARD: False,
    CONF_FORCE_AUTOENROLL: True,
    CONF_AUTO_SYNC_TIME : True,
    CONF_LANGUAGE: "EN",
    CONF_MOTION_OFF_DELAY: 50,
    CONF_SIREN_SOUNDING: ["Intruder"],
    CONF_B0_ENABLE_MOTION_PROCESSING: False,
    CONF_B0_MIN_TIME_BETWEEN_TRIGGERS: 5,
    CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT: 30
}

string_type="string"
int_type = "int"
bool_type = "bool"
list_type = "list"
myconfigtypes = [string_type, string_type, int_type, string_type, int_type, string_type, bool_type, bool_type, bool_type, string_type, bool_type, list_type, bool_type, int_type, string_type, bool_type, bool_type, list_type, bool_type, int_type, int_type]

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
        #print("init self.config = %s  %s", PYVConst.DownloadCode, self.config)

    def new_switch_callback(self, dev: PySwitchDevice): 
        """Process a new x10."""
        # Check to ensure variables are set correctly
        #print("new_switch_callback")
        if dev is None:
            print("Visonic attempt to add X10 switch when sensor is undefined")
            return
        #print("VS: X10 Switch list ", dev)
        if dev.isEnabled():
            if self.process_x10 is not None:
                self.process_x10(dev)

    def new_sensor_callback(self, sensor: PySensorDevice):
        """Process a new sensor."""
        #print("new_sensor_callback")
        if sensor is None:
            print("Visonic attempt to add sensor when sensor is undefined")
            return
        if sensor.getDeviceID() is None:
            print("     Sensor ID is None")
        else:
            print("     Sensor ", str(sensor))
            if self.process_sensor is not None:
                self.process_sensor(sensor)

    def generate_ha_bus_event(self, visonic_devices, datadictionary):
        """ This is a callback function, called from the visonic library. """
        #print("generate_ha_bus_event ", type(visonic_devices))
        if type(visonic_devices) == PyCondition:
            if self.process_event is not None:
                self.process_event(visonic_devices, datadictionary)
        else:
            print("Visonic attempt to add device with type %s  device is %s", type(visonic_devices), visonic_devices)

    def toBool(self, val) -> bool:
        if type(val) == bool:
            return val
        elif type(val) == int:
            return val != 0
        elif type(val) == str:
            v = val.lower()
            return not (v == "no" or v == "false" or v == "0")
        print("Visonic unable to decode boolean value %s    type is %s", val, type(val))
        return False

    def __getConfigData(self) -> dict:
        """ Create a dictionary full of the configuration data. """
        return {
            PyConfiguration.DownloadCode: self.config.get(CONF_DOWNLOAD_CODE, ""),
            PyConfiguration.ForceStandard: self.toBool(
                self.config.get(CONF_FORCE_STANDARD, False)
            ),
            PyConfiguration.ForceAutoEnroll: self.toBool(
                self.config.get(CONF_FORCE_AUTOENROLL, True)
            ),
            PyConfiguration.AutoSyncTime: self.toBool(
                self.config.get(CONF_AUTO_SYNC_TIME, True)
            ),
            PyConfiguration.PluginLanguage: self.config.get(CONF_LANGUAGE, "EN"),
            PyConfiguration.MotionOffDelay: self.config.get(CONF_MOTION_OFF_DELAY, 120),
            PyConfiguration.SirenTriggerList: self.config.get(
                CONF_SIREN_SOUNDING, ["Intruder"]
            ),
            PyConfiguration.B0_Enable: self.toBool(
                self.config.get(CONF_B0_ENABLE_MOTION_PROCESSING, False)
            ),
            PyConfiguration.B0_Min_Interval_Time: self.config.get(
                CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, 5
            ),
            PyConfiguration.B0_Max_Wait_Time: self.config.get(
                CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, 30
            ),
        }


    def __disconnect_callback(self, excep):
        """ Callback when the connection to the panel is disrupted """
        if excep is None:
            print("PyVisonic has caused an exception, no exception information is available")
        else:
            print("PyVisonic has caused an exception %s", str(excep))
        # General update trigger
        #    0 is a disconnect and (hopefully) reconnect from an exception (probably comms related)
        sleep(5.0)
        print(" ........... setting up reconnection")
        self.panel_exception_counter = self.panel_exception_counter + 1
        asyncio.create_task(self.disconnect_callback_async(excep))

    async def __connect_to_alarm(self) -> bool:
        """ Create the connection to the alarm panel """
        #import pyvisonic as visonicApi  # Connection to python Library

        # Is the system already running and connected
        if self.SystemStarted:
            return False

        print("connect_to_alarm self.config = %s", self.config)

        # Get Visonic specific configuration.
        device_type = self.config.get(CONF_DEVICE_TYPE)

        print("Visonic Connection Device Type is ", device_type) #, self.__getConfigData())

        # update config parameters (local in hass[DOMAIN] mainly)
        self.updateConfig()

        self.visonicTask = None
        self.visonicProtocol = None
        
        # Connect in the way defined by the user in the config file, ethernet or usb
        if device_type == "ethernet":
            host = self.config.get(CONF_HOST)
            port = self.config.get(CONF_PORT)

            panelConfig=self.__getConfigData()
            
            self.visonicTask, self.visonicProtocol = await pyvisonic.async_create_tcp_visonic_connection(
                address=host,
                port=port,
                panelConfig=self.__getConfigData(),
                loop=self.loop
            )

        elif device_type == "usb":
            path = self.config.get(CONF_PATH)
            baud = self.config.get(CONF_DEVICE_BAUD)

            self.visonicTask, self.visonicProtocol = await pyvisonic.async_create_usb_visonic_connection(
                path=path,
                baud=baud,
                panelConfig=self.__getConfigData(),
                loop=self.loop
            )

        if self.visonicTask is not None and self.visonicProtocol is not None:
            # Connection to the panel has been initially successful
            # Record that we have started the system
            # def setCallbackHandlers(self, event_callback : Callable = None, disconnect_callback : Callable = None, new_sensor_callback : Callable = None, new_switch_callback : Callable = None, panel_event_log_callback : Callable = None):
            self.visonicProtocol.setCallbackHandlers(
                    event_callback=self.generate_ha_bus_event,
                    panel_event_log_callback=self.process_log,       
                    disconnect_callback=self.__disconnect_callback,  
                    new_sensor_callback = self.new_sensor_callback,
                    new_switch_callback = self.new_switch_callback)
            
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
            self.visonicProtocol.ShutdownOperation()
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

    def isSirenActive(self) -> bool:
        """ Is the siren active. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isSirenActive()
        return False

    def isPowerMaster(self) -> bool:
        """ Is it a PowerMaster panel. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.isPowerMaster()
        return False

    def getPanelStatusCode(self) -> PyPanelStatus:
        """ Get the panel status code. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelStatusCode()
        return PyPanelStatus.UNKNOWN

    def getPanelMode(self) -> PyPanelMode:
        """ Get the panel mode. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelMode()
        return PyPanelMode.UNKNOWN

    def getEventLog(self, code : str) -> PyCommandStatus:
        """ Get the panel mode. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getEventLog(code)
        return PyCommandStatus.FAIL_INVALID_STATE

    def getPanelStatus(self) -> dict:
        """ Get the panel status. """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.getPanelStatus(True)
        return {}
        
    def isSystemStarted(self) -> bool:
        return self.SystemStarted

    def sendCommand(self, command : PyPanelCommand, code : str) -> PyCommandStatus:
        """ Send a command to the panel """
        if self.visonicProtocol is not None:
            # def requestArm(self, state : PyPanelCommand, pin : str = "")
            return self.visonicProtocol.requestArm(command, code)
        return PyCommandStatus.FAIL_INVALID_STATE

    def sendBypass(self, devid, bypass, code) -> PyCommandStatus:
        """ Send the bypass command to the panel """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.setSensorBypassState(devid, bypass, code)
        return PyCommandStatus.FAIL_INVALID_STATE

    def setX10(self, ident, state) -> PyCommandStatus:
        """ Send an X10 command to the panel """
        if self.visonicProtocol is not None:
            return self.visonicProtocol.setX10(ident, state)
        return PyCommandStatus.FAIL_INVALID_STATE
    
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
    
    def process_event(event_id: PyCondition, datadictionary):
        # event 
        if event_id != PyCondition.PUSH_CHANGE:
            tmpdict = {}
            if datadictionary is not None:
                tmpdict = datadictionary.copy()
            console.print("Visonic update event condition ", str(event_id), str(tmpdict))

    def process_log(event_log_entry : PyLogPanelEvent):
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
            console.print("Sensor " + str(dev))
            sensors.append(dev)
    
    def process_x10(dev):
        if dev.enabled:
            if dev.getDeviceID() is None:
                console.print("X10 is None")
            else:
                console.print("X10 ", str(dev))
                devices.append(dev)
        
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
        console.print("Quit                 Disconnect from the panel")
        console.print("Connect              Connect to the panel")
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

    client.installHandlers(process_event=process_event, process_log=process_log, process_sensor=process_sensor, process_x10=process_x10)

    console.clear_output()
    console.setOutputFontSize(10)
    console.setInputFontSize(12)
    sensors = []
    devices = []
    
    prompt1 = '<help, quit, variables, print, connect>: '
    prompt2 = '<help, quit, variables, print, mode, arm, stay, disarm, log, bypass, rearm>: '
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
                if client.isSystemStarted():
                    # There must be a panel connection to do the following commands
                    if command == 'q':
                        print("Closing connection")
                        console.clear_output()
                        await client.service_panel_disconnect()
                        sensors = []
                        devices = []
                        prompt = prompt1
                        processedInput = True
                    elif command == 'm':
                        s = client.getPanelStatus(True)
                        pstate = s["Panel Status"]
                        pready = s["Panel Ready"]
                        parmed = s["Panel Armed"]
                        siren = client.isSirenActive();
                        powerm = client.isPowerMaster();
                        mode = client.getPanelMode();
                        code = client.getPanelStatusCode();
                        console.print("Mode=" + str(mode) + "    Panel state=" + str(pstate) + "    Panel ready=" + str(pready) + "    Panel Armed=" + str(parmed) + "    Siren=" + str(siren) + "    Panel state=" + str(code) + "    Powermaster=" + str(powerm) )
                        processedInput = True
                    elif command == 'd':
                        client.sendCommand(PyPanelCommand.DISARM, getCode(ar,1))
                        processedInput = True
                    elif command == 'a':
                        client.sendCommand(PyPanelCommand.ARM_AWAY, getCode(ar,1))
                        processedInput = True
                    elif command == 's':
                        client.sendCommand(PyPanelCommand.ARM_HOME, getCode(ar,1))
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
                        print("Terminating program")
                        raise Exception('terminating')
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
    except:
        if client is not None and client.isSystemStarted():
            print("Please wait .... disconnecting from panel")
            await client.service_panel_disconnect()
        raise    
         
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

def main():
    loop = asyncio.get_event_loop()
    # May want to catch other signals too
    
if __name__ == '__main__':
    pyvisonic.setupLocalLogger("DEBUG", True)   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
    print("Starting")

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
                print("Goodbye cruel world")
                testloop.close()
