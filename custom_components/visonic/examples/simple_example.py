""" Create a commandline connection to a Visonic PowerMax or PowerMaster Alarm System """
import os,sys,inspect
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0,parentdir) 

import asyncio
import logging
import pyvisonic
import time
import collections
import argparse
from time import sleep
from collections import defaultdict

from pyconst import AlConfiguration, AlPanelMode, AlPanelCommand, AlPanelStatus, AlCommandStatus, AlX10Command, AlCondition, AlSensorDevice, AlLogPanelEvent, AlSensorType, AlSwitchDevice

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

myconfig = { 
    CONF_DOWNLOAD_CODE: "",
    CONF_FORCE_STANDARD: False,
    CONF_FORCE_AUTOENROLL: True,
    CONF_AUTO_SYNC_TIME : True,
    CONF_LANGUAGE: "EN",
    CONF_MOTION_OFF_DELAY: 40,
    CONF_SIREN_SOUNDING: ["Intruder"],
    CONF_B0_ENABLE_MOTION_PROCESSING: False,
    CONF_B0_MIN_TIME_BETWEEN_TRIGGERS: 5,
    CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT: 30
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


def getConfigData() -> PanelConfig:
    """ Create a dictionary full of the configuration data. """
    return {
        AlConfiguration.DownloadCode: myconfig.get(CONF_DOWNLOAD_CODE, ""),
        AlConfiguration.ForceStandard: toBool(
            myconfig.get(CONF_FORCE_STANDARD, False)
        ),
        AlConfiguration.AutoEnroll: toBool(
            myconfig.get(CONF_FORCE_AUTOENROLL, True)
        ),
        AlConfiguration.AutoSyncTime: toBool(
            myconfig.get(CONF_AUTO_SYNC_TIME, True)
        ),
        AlConfiguration.PluginLanguage: myconfig.get(CONF_LANGUAGE, "EN"),
        AlConfiguration.MotionOffDelay: myconfig.get(CONF_MOTION_OFF_DELAY, 120),
        AlConfiguration.SirenTriggerList: myconfig.get(
            CONF_SIREN_SOUNDING, ["Intruder"]
        ),
#        AlConfiguration.B0_Enable: toBool(
#            myconfig.get(CONF_B0_ENABLE_MOTION_PROCESSING, False)
#        ),
#        AlConfiguration.B0_Min_Interval_Time: myconfig.get(
#            CONF_B0_MIN_TIME_BETWEEN_TRIGGERS, 5
#        ),
#        AlConfiguration.B0_Max_Wait_Time: myconfig.get(
#            CONF_B0_MAX_TIME_FOR_TRIGGER_EVENT, 30
#        ),
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

def onPanelChangeHandler(event_id):
    """ This is a callback function, called from the visonic library. """
    #print("onPanelChangeHandler ", type(visonic_devices))
    if type(event_id) == AlCondition:
        # event 
        if event_id != AlCondition.PUSH_CHANGE:
            console.print("Visonic update event condition ", str(event_id))
    else:
        print(f"Visonic attempt to add device with type {type(event_id)}  device is {visonic_devices}")

def onDisconnect(excep):
    """ Callback when the connection to the panel is disrupted """
    if excep is None:
        print("AlVisonic has caused an exception, no exception information is available")
    else:
        print("AlVisonic has caused an exception %s", str(excep))

def process_log(event_log_entry):
    print("process_log ", event_log_entry)

async def startitall(testloop):
    visonicTask = None 
    visonicProtocol = None
    print("Setting up Connection")
    if len(args.address) > 0:
        visonicTask, visonicProtocol = await pyvisonic.async_create_tcp_visonic_connection(address=args.address, port=args.port, loop=testloop, panelConfig=getConfigData())
    elif len(args.usb) > 0:
        visonicTask, visonicProtocol = await pyvisonic.async_create_usb_visonic_connection(path="//./" + args.usb, loop=testloop, panelConfig=getConfigData())
    if visonicTask is not None and visonicProtocol is not None:
        #visonicProtocol.onPanelError(self.onPanelChangeHandler)
        visonicProtocol.onPanelChange(self.onPanelChangeHandler)
        # visonicProtocol.onPanelEvent(self.onPanelChangeHandler)
        visonicProtocol.onPanelLog(self.process_log)
        visonicProtocol.onDisconnect(self.onDisconnect)
        visonicProtocol.onNewSensor(self.onNewSensor)
        visonicProtocol.onNewSwitch(self.onNewSwitch)
        
        while True:
            print("You can do stuff here with visonicProtocol, Mode=", visonicProtocol.getPanelMode())
            await asyncio.sleep(5.0)
    else:
        print("Please check you command line parameters")

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    _LOGGER = logging.getLogger(__name__)

    pyvisonic.setupLocalLogger("DEBUG")

    parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
    parser.add_argument("-usb", help="visonic alarm usb device", default="")
    parser.add_argument("-address", help="visonic alarm ip address", default="")
    parser.add_argument("-port", help="visonic alarm ip port", type=int)
    args = parser.parse_args()

    testloop = asyncio.get_event_loop()

    task = testloop.create_task(startitall(testloop))
    try:
        print("Calling run_forever")
        testloop.run_until_complete(task)
    except KeyboardInterrupt:
        pass
    except:
        pass
    finally:
        # cleanup connection
        print("Cleaning up")
        testloop.close()
