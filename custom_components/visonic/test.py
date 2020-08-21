""" Create a commandline connection to a Visonic PowerMax or PowerMaster Alarm System """
import asyncio
import logging
import pyvisonic
import time
import collections
import argparse
from time import sleep
from collections import defaultdict

logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger(__name__)


def add_visonic_device(visonic_devices, dict={}):

    if visonic_devices == None:
        _LOGGER.debug("Visonic attempt to add device when sensor is undefined")
        return
    if type(visonic_devices) == defaultdict:
        _LOGGER.debug("Visonic got new sensors {0}".format(visonic_devices))
    elif type(visonic_devices) == pyvisonic.SensorDevice:
        # This is an update of an existing device
        _LOGGER.debug("Visonic got a sensor update {0}".format(visonic_devices))

    # elif type(visonic_devices) == visonicApi.SwitchDevice:   # doesnt exist yet

    else:
        _LOGGER.debug("Visonic attempt to add device with type {0}  device is {1}".format(type(visonic_devices), visonic_devices))


pyvisonic.setupLocalLogger()

conn = None

parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
parser.add_argument("-usb", help="visonic alarm usb device", default="")
parser.add_argument("-address", help="visonic alarm ip address", default="")
parser.add_argument("-port", help="visonic alarm ip port", type=int)
args = parser.parse_args()

testloop = asyncio.get_event_loop()

if len(args.address) > 0:
    conn = pyvisonic.create_tcp_visonic_connection(address=args.address, port=args.port, loop=testloop, event_callback=add_visonic_device,)
elif len(args.usb) > 0:
    conn = pyvisonic.create_usb_visonic_connection(port="//./" + args.usb, loop=testloop, event_callback=add_visonic_device)

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
