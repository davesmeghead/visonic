""" Create a connection to a Visonic PowerMax or PowerMaster Alarm System """
########################################################
# PowerMax/Master Transfer for Visonic PC App
########################################################

#  python bridge.py -address 192.168.X.X -port YYYYY -usb COM1 *>> outty2.txt
#
# Get History from panel:     0d 3e 06 80 d2 07 b0 00 00 01 01 0a a4 0a
# Get Event Log from panel:   0d 3e df 04 28 03 b0 0b 1c 05 13 00 c2 0a
# Panel Definitions           0d 3e 00 01 10 00 b0 1e 08 01 02 04 d2 0a
# Zone Definitions            0d 3e 11 03 1e 00 b0 01 01 01 01 01 d9 0a
# Pin Codes                   0d 3e fa 01 10 00 b0 00 00 00 00 00 05 0a   and
#                             0d 3e 51 03 08 00 b0 01 01 01 01 01 af 0a
# Site Information            0d 3e 0a 02 08 00 b0 14 56 50 00 00 42 0a   and
#                             0d 3e 00 03 01 00 b0 01 01 01 01 01 08 0a
# Screen Saver                0d 3e 00 17 4b 00 b0 00 00 00 00 00 ae 0a
# RF Diagnostics              0d 3e da 09 1c 00 b0 00 00 00 00 00 11 0a

import struct
import re
import asyncio
import concurrent
import logging
import sys
import pkg_resources
import threading
import collections
import time
import copy
import math
import argparse
import serial_asyncio
import datetime

from collections import defaultdict
from datetime import datetime
from time import sleep
from datetime import timedelta
from functools import partial
from typing import Callable, List
from collections import namedtuple

logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger()

class ProtocolBase(asyncio.Protocol):
    """Manage low level Visonic protocol."""

    transport = None  # type: asyncio.Transport

    _LOGGER.debug("Initialising Protocol")

    def __init__(self, loop=None, receiver=None, sender=None, name="", deb=False) -> None:
        """Initialize class."""
        _LOGGER.debug("Initialising Connection : %s", name)
        if loop:
            self.loop = loop
        else:
            self.loop = asyncio.get_event_loop()
        self.receiver = receiver
        self.sender = sender
        self.name = name
        self.deb = deb
        self.ReceiveData = bytearray(b"")
        asyncio.ensure_future(self.transportwriter(), loop=self.loop)

    def _toString(self, array_alpha: bytearray):
        return "".join("%02x " % b for b in array_alpha)

    def _toHex(self, d):
        return "%02x" % d

    def handle_msgtype3F(self, data):
        """MsgType=3F - Download information
        Multiple 3F can follow eachother, if we request more then &HFF bytes"""

        _LOGGER.debug("[handle_msgtype3F]")
        # data format is normally: <index> <page> <length> <data ...>
        # If the <index> <page> = FF, then it is an additional PowerMaster MemoryMap
        iIndex = data[0]
        iPage = data[1]
        iLength = data[2]

        if iLength != len(data) - 3:  # 3 because -->   index & page & length
            _LOGGER.warning("[handle_msgtype3F]        ERROR: Type=3F has an invalid length, Received: {0}, Expected: {1}".format(len(data)-3, iLength))
            _LOGGER.warning("[handle_msgtype3F]                            " + self._toString(data))
            return

        for x in range(iLength):
            val = (iPage * 256) + iIndex
            _LOGGER.debug(self._toHex(iPage) + self._toHex(iIndex) + "  (" + str(val) + ")       " + self._toHex(data[x+3]))
            
            if iIndex == 255:
                iIndex = 0
                iPage = iPage + 1
            else:
                iIndex = iIndex + 1
            

    async def transportwriter(self):
        while True:
            item = await self.receiver.get()
            if item is None:
                # the producer emits None to indicate that it is done
                break
            if self.deb:
                _LOGGER.debug(
                    "[sending to %s at %s] : %s", self.name, str(datetime.now()), self._toString(item),
                )
            self.transport.write(item)

    # This is called from the loop handler when the connection to the transport is made
    def connection_made(self, transport):
        """Make the protocol connection to the Panel."""
        self.transport = transport
        _LOGGER.debug("[Connection] Connected made : %s", self.name)

    # check the checksum of received messages
    def _validatePDU(self, packet: bytearray) -> bool:
        """Verify if packet is valid.
        >>> Packets start with a preamble (\x0D) and end with postamble (\x0A)
        """
        # Validate a received message
        # Does it start with a header
        if packet[:1] != b"\x0D":
            return False
        # Does it end with a footer
        if packet[-1:] != b"\x0A":
            return False

        if packet[-2:-1][0] == self._calculateCRC(packet[1:-2])[0] + 1:
            _LOGGER.debug("[_validatePDU] Validated a Packet with a checksum that is 1 more than the actual checksum!!!! {0} and {1}".format(packet[-2:-1][0], self._calculateCRC(packet[1:-2])[0]))
            return True

        if packet[-2:-1][0] == self._calculateCRC(packet[1:-2])[0] - 1:
            _LOGGER.debug("[_validatePDU] Validated a Packet with a checksum that is 1 less than the actual checksum!!!! {0} and {1}".format(packet[-2:-1][0], self._calculateCRC(packet[1:-2])[0]))
            return True

        # Check the CRC
        if packet[-2:-1] == self._calculateCRC(packet[1:-2]):
            # _LOGGER.debug("[_validatePDU] VALID PACKET!")
            return True

        _LOGGER.debug("[_validatePDU] Not valid packet, CRC failed, may be ongoing and not final 0A")
        return False

    # calculate the checksum for sending and receiving messages
    def _calculateCRC(self, msg: bytearray):
        """ Calculate CRC Checksum """
        # _LOGGER.debug("[_calculateCRC] Calculating for: %s", self._toString(msg))
        # Calculate the checksum
        checksum = 0
        for char in msg[0 : len(msg)]:
            checksum += char
        checksum = 0xFF - (checksum % 0xFF)
        if checksum == 0xFF:
            checksum = 0x00
        #            _LOGGER.debug("[_calculateCRC] Checksum was 0xFF, forsing to 0x00")
        # _LOGGER.debug("[_calculateCRC] Calculating for: %s     calculated CRC is: %s", self._toString(msg), self._toString(bytearray([checksum])))
        return bytearray([checksum])
    def _resetMessageData(self):
        # clear our buffer again so we can receive a new packet.
        self.ReceiveData = bytearray(b"")  # messages should never be longer than 0xC0

    def _processReceivedMessage(self, data):
        if data[1] == 0x3F:  # Download information
            self.handle_msgtype3F(data[2:-2])

    def processByte(self, data):
        pdu_len = len(self.ReceiveData)                                # Length of the received data so far

        if pdu_len == 0:
            self._resetMessageData()
            if data == 0x0D:  # preamble
                self.ReceiveData.append(data)
                #_LOGGER.debug("[data receiver] Starting PDU " + self._toString(self.ReceiveData))
            # else we're trying to resync and walking through the bytes waiting for an 0x0D preamble byte
        elif data == 0x0A:
            # (waiting for 0x0A and got it) OR (actual length == calculated length)
            self.ReceiveData.append(data)  # add byte to the message buffer
            #_LOGGER.debug("[data receiver] Building PDU: Checking it " + self._toString(self.ReceiveData))
            msgType = self.ReceiveData[1]
            if self._validatePDU(self.ReceiveData):
                self._processReceivedMessage(data=self.ReceiveData)
                self._resetMessageData()
        elif pdu_len <= 0xC0:
            #_LOGGER.debug("[data receiver] Current PDU " + self._toString(self.ReceiveData) + "    adding " + str(hex(data).upper()))
            self.ReceiveData.append(data)
        else:
            _LOGGER.debug("[data receiver] Dumping Current PDU " + self._toString(self.ReceiveData))
            self._resetMessageData()
    
    # Process any received bytes (in data as a bytearray)
    def data_received(self, data):
        """Add incoming data to ReceiveData."""
        if self.deb:
            _LOGGER.debug(
                "[received from %s at %s] : %s", self.name, str(datetime.now()), self._toString(data),
            )
            for x in range(len(data)):
                self.processByte(data[x])
                    
        asyncio.ensure_future(self.sender.put(data))

    def connection_lost(self, exc):
        """Close the protocol connection to the Panel."""
        _LOGGER.debug("[Connection] Connected closed")


# Create a connection using asyncio using an ip and port
def create_tcp_visonic_connection(
    address, port, protocol=ProtocolBase, loop=None, receiver=None, sender=None, name="", debs=False,
):
    """Create Visonic manager class, returns tcp transport coroutine."""

    # use default protocol if not specified
    protocol = partial(protocol, receiver=receiver, sender=sender, name=name, deb=debs, loop=loop if loop else asyncio.get_event_loop(),)

    address = address
    port = port
    conn = loop.create_connection(protocol, address, port)

    return conn


# Create a connection using asyncio through a linux port (usb or rs232)
def create_usb_visonic_connection(
    port, baud=9600, protocol=ProtocolBase, loop=None, receiver=None, sender=None, name="", debs=False,
):
    """Create Visonic manager class, returns rs232 transport coroutine."""
    from serial_asyncio import create_serial_connection

    # use default protocol if not specified
    protocol = partial(protocol, receiver=receiver, sender=sender, name=name, deb=debs, loop=loop if loop else asyncio.get_event_loop(),)

    # setup serial connection
    port = port
    baud = baud
    conn = create_serial_connection(loop, protocol, port, baud)

    return conn


connalarm = None
connpc = None

parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
parser.add_argument("-usb", help="visonic alarm usb device", default="")
parser.add_argument("-address", help="visonic alarm ip address", default="")
parser.add_argument("-port", help="visonic alarm ip port", default="")
parser.add_argument("-coma", help="visonic COM port (left)", default="")
parser.add_argument("-comb", help="visonic COM port (right)", default="")
args = parser.parse_args()

testloop = asyncio.get_event_loop()

toalarm_queue = asyncio.Queue()
fromalarm_queue = asyncio.Queue()

if len(args.comb) > 0:
    connalarm = create_usb_visonic_connection(
        port="//./" + args.comb, loop=testloop, name="COM_B", debs=True, receiver=toalarm_queue, sender=fromalarm_queue,
    )

if len(args.coma) > 0:
    connpc = create_usb_visonic_connection(
        port="//./" + args.coma, loop=testloop, name="COM_A", debs=False, receiver=fromalarm_queue, sender=toalarm_queue,
    )

if len(args.address) > 0:
    connalarm = create_tcp_visonic_connection(
        address=args.address, port=args.port, loop=testloop, name="Alarm", debs=True, receiver=toalarm_queue, sender=fromalarm_queue,
    )

if len(args.usb) > 0:
    connpc = create_usb_visonic_connection(
        port="//./" + args.usb, loop=testloop, name="PC", debs=False, receiver=fromalarm_queue, sender=toalarm_queue,
    )

if connpc is not None and connalarm is not None:
    testloop.create_task(connpc)
    testloop.create_task(connalarm)

    try:
        testloop.run_forever()
    except KeyboardInterrupt:
        # cleanup connection
        connpc.close()
        connalarm.close()
        testloop.run_forever()
        testloop.close()
    finally:
        testloop.close()
