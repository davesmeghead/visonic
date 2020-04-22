import asyncio
import logging
import pyvisonic
import time
import collections
import argparse
from time import sleep
from collections import defaultdict

def add_visonic_device(visonic_devices):
    
    if visonic_devices == None:
        log.info("Visonic attempt to add device when sensor is undefined")
        return
    if type(visonic_devices) == defaultdict:
        log.info("Visonic got new sensors {0}".format( visonic_devices ))
    elif type(visonic_devices) == pyvisonic.SensorDevice:
        # This is an update of an existing device
        log.info("Visonic got a sensor update {0}".format( visonic_devices ))
        
    #elif type(visonic_devices) == visonicApi.SwitchDevice:   # doesnt exist yet
    
    else:
        log.info("Visonic attempt to add device with type {0}  device is {1}".format(type(visonic_devices), visonic_devices ))

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

pyvisonic.setupLocalLogger()

conn = None

parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
parser.add_argument("-usb", help="visonic alarm usb device", default = '')
parser.add_argument("-address", help="visonic alarm ip address", default = '')
parser.add_argument("-port", help="visonic alarm ip port", type=int)
args = parser.parse_args()

testloop = asyncio.get_event_loop()

if len(args.address) > 0:
    conn = pyvisonic.create_tcp_visonic_connection(
                        address=args.address,
                        port=args.port,
                        loop=testloop,
                        event_callback=add_visonic_device)
elif len(args.usb) > 0:
    conn = pyvisonic.create_usb_visonic_connection(
                        port='//./' + args.usb,
                        loop=testloop,
                        event_callback=add_visonic_device)

if conn is not None:                        
    testloop.create_task(conn)

    try:
        testloop.run_forever()
    except KeyboardInterrupt:
        # cleanup connection
        conn.close()
        testloop.run_forever()
        testloop.close()
    finally:
        testloop.close()
