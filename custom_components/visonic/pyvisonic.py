"""Asyncio protocol implementation of Visonic PowerMaster/PowerMax.
  Based on the DomotiGa and Vera implementation:

  Credits:
    Initial setup by Wouter Wolkers and Alexander Kuiper.
    Thanks to everyone who helped decode the data.

  Originally converted to Python module by Wouter Wolkers and David Field

  The Component now follows the new HA file structure and uses asyncio
"""

#################################################################
# PowerMax/Master send and receive messages
#################################################################

#################################################################
######### Known Panel Types to work (or not) ####################
#    PanelType=0 : PowerMax , Model=21   Powermaster False  <<== THIS DOES NOT WORK (NO POWERLINK SUPPORT and only supports EPROM download i.e no sensor data) ==>>
#    PanelType=1 : PowerMax+ , Model=33   Powermaster False
#    PanelType=1 : PowerMax+ , Model=47   Powermaster False
#    PanelType=2 : PowerMax Pro , Model=22   Powermaster False
#    PanelType=4 : PowerMax Pro Part , Model=17   Powermaster False
#    PanelType=4 : PowerMax Pro Part , Model=62   Powermaster False
#    PanelType=4 : PowerMax Pro Part , Model=71   Powermaster False
#    PanelType=4 : PowerMax Pro Part , Model=86   Powermaster False
#    PanelType=5 : PowerMax Complete Part , Model=18   Powermaster False
#    PanelType=5 : PowerMax Complete Part , Model=79   Powermaster False
#    PanelType=7 : PowerMaster10 , Model=32   Powermaster True
#    PanelType=7 : PowerMaster10 , Model=68   Powermaster True   #  Under investigation. Problem with 0x3F Message data (EPROM) being less than requested
#    PanelType=7 : PowerMaster10 , Model=153   Powermaster True
#    PanelType=8 : PowerMaster30 , Model=6   Powermaster True
#    PanelType=8 : PowerMaster30 , Model=53   Powermaster True
#    PanelType=8 : PowerMaster30 , Model=63   Powermaster True   #  This is my test panel, all 0x3F  Message data is formatted correctly
#    PanelType=10: PowerMaster33 , Model=71   Powermaster True   #  Under investigation. Problem with 0x3F Message data (EPROM) being less than requested
#    PanelType=15: PowerMaster33 , Model=146   Powermaster True  #  Under investigation.
#################################################################

import os
# import requests

# The defaults are set for use in Home Assistant.
#    If using MocroPython / CircuitPython then set these values in the environment
MicroPython = os.getenv("MICRO_PYTHON")

if MicroPython is not None:
    import adafruit_logging as logging
    import binascii
    from adafruit_datetime import datetime as datetime
    from adafruit_datetime import timedelta as timedelta
    IntEnum = object
    List = object
    ABC = object
    Callable = object

    class ABC:
        pass

    def abstractmethod(f):
        return f

    def convertByteArray(s) -> bytearray:
        b = binascii.unhexlify(s.replace(' ', ''))
        return bytearray(b)

    log = logging.getLogger(__name__)
    log.setLevel(logging.DEBUG)

else:
    import logging
    from datetime import datetime, timedelta
    from typing import Callable, List
    import copy
    from abc import abstractmethod

    def convertByteArray(s) -> bytearray:
        return bytearray.fromhex(s)

    log = logging.getLogger(__name__)

import asyncio
import sys
import collections
import time
import math
import io
import socket

from collections import namedtuple
from time import sleep

try:
    from .pyconst import AlTransport, AlPanelDataStream, NO_DELAY_SET, PanelConfig, AlConfiguration, AlPanelMode, AlPanelCommand, AlTroubleType, AlAlarmType, AlPanelStatus, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlLogPanelEvent, AlSensorType
    from .pyhelper import MyChecksumCalc, AlImageManager, ImageRecord, titlecase, pmPanelTroubleType_t, pmPanelAlarmType_t, AlPanelInterfaceHelper, AlSensorDeviceHelper, AlSwitchDeviceHelper
except:
    from pyconst import AlTransport, AlPanelDataStream, NO_DELAY_SET, PanelConfig, AlConfiguration, AlPanelMode, AlPanelCommand, AlTroubleType, AlAlarmType, AlPanelStatus, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlLogPanelEvent, AlSensorType
    from pyhelper import MyChecksumCalc, AlImageManager, ImageRecord, titlecase, pmPanelTroubleType_t, pmPanelAlarmType_t, AlPanelInterfaceHelper, AlSensorDeviceHelper, AlSwitchDeviceHelper

PLUGIN_VERSION = "1.3.2.2"

# Some constants to help readability of the code

# Maximum number of CRC errors on receiving data from the alarm panel before performing a restart
#    This means a maximum of 5 CRC errors in 10 minutes before resetting the connection
MAX_CRC_ERROR = 5
CRC_ERROR_PERIOD = 600  # seconds, 10 minutes

# Maximum number of received messages that are exactly the same from the alarm panel before performing a restart
SAME_PACKET_ERROR = 10000

# If we are waiting on a message back from the panel or we are explicitly waiting for an acknowledge,
#    then wait this time before resending the message.
#  Note that not all messages will get a resend, only ones waiting for a specific response and/or are blocking on an ack
RESEND_MESSAGE_TIMEOUT = timedelta(seconds=100)

# We must get specific messages from the panel, if we do not in this time period (seconds) then trigger a restore/status request
WATCHDOG_TIMEOUT = 120

# If there has been a watchdog timeout this many times per 24 hours then go to standard (plus) mode
WATCHDOG_MAXIMUM_EVENTS = 10

# Response timeout, when we send a PDU this is the time we wait for a response (defined in replytype in VisonicCommand)
RESPONSE_TIMEOUT = 10

# If a message has not been sent to the panel in this time (seconds) then send an I'm alive message
KEEP_ALIVE_PERIOD = 25  # Seconds

# When we send a download command wait for DownloadMode to become false.
#   If this timesout then I'm not sure what to do, maybe we really need to just start again
#   In Vera, if we timeout we just assume we're in Standard mode by default
DOWNLOAD_TIMEOUT = 90
DOWNLOAD_TIMEOUT_GIVE_UP = 280    # 

# Number of seconds delay between trying to achieve EPROM download
DOWNLOAD_RETRY_DELAY = 60

# Number of times to retry the retrieval of a block to download, this is a total across all blocks to download and not each block
DOWNLOAD_RETRY_COUNT = 30

# Number of seconds delay between trying to achieve powerlink (must have achieved download first)
POWERLINK_RETRY_DELAY = 180

# Number of seconds between trying to achieve powerlink (must have achieved download first) and giving up. Better to be half way between retry delays
POWERLINK_TIMEOUT = 4.5 * POWERLINK_RETRY_DELAY

# The number of seconds that if we have not received any data packets from the panel at all (from the start) then suspend this plugin and report to HA
#    This is only used when no data at all has been received from the panel ... ever
NO_RECEIVE_DATA_TIMEOUT = 30

# The number of seconds between receiving data from the panel and then no communication (the panel has stopped sending data for this period of time) then suspend this plugin and report to HA
#    This is used when this integration has received data and then stopped receiving data
LAST_RECEIVE_DATA_TIMEOUT = 240  # 4 minutes

# Whether to download all the EEPROM from the panel or to just download the parts that we gete usable data from
EEPROM_DOWNLOAD_ALL = False

# Message/Packet Constants to make the code easier to read
PACKET_HEADER = 0x0D
PACKET_FOOTER = 0x0A
PACKET_MAX_SIZE = 0xF0
ACK_MESSAGE = 0x02
REDIRECT_POWERLINK_DATA = 0xC0

# Messages that we can send to the panel
#
# A gregorian year, on average, contains 365.2425 days
#    Thus, expressed as seconds per average year, we get 365.2425 × 24 × 60 × 60 = 31,556,952 seconds/year
# use a named tuple for data and acknowledge
#    replytype   is a message type from the Panel that we should get in response
#    waitforack, if True means that we should wait for the acknowledge from the Panel before progressing
#    debugprint  If False then do not log the full raw data as it may contain the user code
#    waittime    a number of seconds after sending the command to wait before sending the next command

# fmt: off
VisonicCommand = collections.namedtuple('VisonicCommand', 'data replytype waitforack download debugprint waittime msg')
pmSendMsg = {
   "MSG_EVENTLOG"     : VisonicCommand(convertByteArray('A0 00 00 00 99 99 00 00 00 00 00 43'), [0xA0] , False, False, False, 0.0, "Retrieving Event Log" ),
   "MSG_ARM"          : VisonicCommand(convertByteArray('A1 00 00 99 99 99 07 00 00 00 00 43'), None   ,  True, False, False, 0.0, "(Dis)Arming System" ),             # Including 07
   "MSG_MUTE_SIREN"   : VisonicCommand(convertByteArray('A1 00 00 0B 99 99 00 00 00 00 00 43'), None   ,  True, False, False, 0.0, "Mute Siren" ),                     #
   "MSG_STATUS"       : VisonicCommand(convertByteArray('A2 00 00 3F 00 00 00 00 00 00 00 43'), [0xA5] ,  True, False,  True, 0.0, "Getting Status" ),                 # Ask for A5 messages, the 0x3F asks for 01 02 03 04 05 06 messages
   "MSG_STATUS_SEN"   : VisonicCommand(convertByteArray('A2 00 00 08 00 00 00 00 00 00 00 43'), [0xA5] ,  True, False,  True, 0.0, "Getting A5 04 Status" ),           # Ask for A5 messages, the 0x08 asks for 04 message only
   "MSG_BYPASSTAT"    : VisonicCommand(convertByteArray('A2 00 00 20 00 00 00 00 00 00 00 43'), [0xA5] , False, False,  True, 0.0, "Get Bypass and Enrolled Status" ), # Ask for A5 06 message (Enrolled and Bypass Status)
   "MSG_ZONENAME"     : VisonicCommand(convertByteArray('A3 00 00 00 00 00 00 00 00 00 00 43'), [0xA3] ,  True, False,  True, 0.0, "Requesting Zone Names" ),          # We expect 4 or 8 (64 zones) A3 messages back but at least get 1
   "MSG_X10PGM"       : VisonicCommand(convertByteArray('A4 00 00 00 00 00 99 99 99 00 00 43'), None   , False, False,  True, 0.0, "X10 Data" ),                       # Retrieve X10 data
   "MSG_ZONETYPE"     : VisonicCommand(convertByteArray('A6 00 00 00 00 00 00 00 00 00 00 43'), [0xA6] ,  True, False,  True, 0.0, "Requesting Zone Types" ),          # We expect 4 or 8 (64 zones) A6 messages back but at least get 1
   "MSG_BYPASSEN"     : VisonicCommand(convertByteArray('AA 99 99 12 34 56 78 00 00 00 00 43'), None   , False, False, False, 0.0, "BYPASS Enable" ),                  # Bypass sensors
   "MSG_BYPASSDI"     : VisonicCommand(convertByteArray('AA 99 99 00 00 00 00 12 34 56 78 43'), None   , False, False, False, 0.0, "BYPASS Disable" ),                 # Arm Sensors (cancel bypass)
   "MSG_GETTIME"      : VisonicCommand(convertByteArray('AB 01 00 00 00 00 00 00 00 00 00 43'), [0xAB] ,  True, False,  True, 0.0, "Get Panel Time" ),                 # Returns with an AB 01 message back
   "MSG_ALIVE"        : VisonicCommand(convertByteArray('AB 03 00 00 00 00 00 00 00 00 00 43'), None   ,  True, False,  True, 0.0, "I'm Alive Message To Panel" ),
   "MSG_RESTORE"      : VisonicCommand(convertByteArray('AB 06 00 00 00 00 00 00 00 00 00 43'), [0xA5] ,  True, False,  True, 0.0, "Restore Connection" ),             # It can take multiple of these to put the panel back in to powerlink
   "MSG_ENROLL"       : VisonicCommand(convertByteArray('AB 0A 00 00 99 99 00 00 00 00 00 43'), None   ,  True, False,  True, 0.0, "Auto-Enroll PowerMax/Master" ),    # should get a reply of [0xAB] but its not guaranteed

   "MSG_NO_IDEA"      : VisonicCommand(convertByteArray('AB 0E 00 17 1E 00 00 03 01 05 00 43'), None   ,  True, False,  True, 0.0, "PowerMaster after jpg feedback" ), #

   "MSG_INIT"         : VisonicCommand(convertByteArray('AB 0A 00 01 00 00 00 00 00 00 00 43'), None   ,  True, False,  True, 8.0, "Init PowerLink Connection" ),
   "MSG_X10NAMES"     : VisonicCommand(convertByteArray('AC 00 00 00 00 00 00 00 00 00 00 43'), [0xAC] , False, False,  True, 0.0, "Requesting X10 Names" ),
   "MSG_GET_IMAGE"    : VisonicCommand(convertByteArray('AD 99 99 0A FF FF 00 00 00 00 00 43'), [0xAD] ,  True, False,  True, 0.0, "Requesting JPG Image" ),           # The first 99 might be the number of images. Request a jpg image, second 99 is the zone.  
   # Command codes (powerlink) do not have the 0x43 on the end and are only 11 values
   "MSG_DOWNLOAD"     : VisonicCommand(convertByteArray('24 00 00 99 99 00 00 00 00 00 00')   , [0x3C] , False,  True, False, 0.0, "Start Download Mode" ),            # This gets either an acknowledge OR an Access Denied response
   "MSG_WRITE"        : VisonicCommand(convertByteArray('3D 00 00 00 00 00 00 00 00 00 00')   , None   , False, False, False, 0.0, "Write Data Set" ),
   "MSG_DL"           : VisonicCommand(convertByteArray('3E 00 00 00 00 B0 00 00 00 00 00')   , [0x3F] ,  True, False,  True, 0.0, "Download Data Set" ),
   "MSG_SETTIME"      : VisonicCommand(convertByteArray('46 F8 00 01 02 03 04 05 06 FF FF')   , None   , False, False,  True, 0.0, "Setting Time" ),                   # may not need an ack
   "MSG_SER_TYPE"     : VisonicCommand(convertByteArray('5A 30 04 01 00 00 00 00 00 00 00')   , [0x33] , False, False,  True, 0.0, "Get Serial Type" ),
   
   # Quick command codes to start and stop download/powerlink are a single value
   "MSG_UNKNOWN_07"   : VisonicCommand(convertByteArray('07')                                 , None   , False, False,  True, 0.0, "Not sure what this does to the panel." ),     #
   "MSG_BUMP"         : VisonicCommand(convertByteArray('09')                                 , [0x3C] , False, False,  True, 0.2, "Bump" ),                           # Bump to try to get the panel to send a 3C
   "MSG_START"        : VisonicCommand(convertByteArray('0A')                                 , [0x0B] , False, False,  True, 0.0, "Start" ),                          # waiting for STOP from panel for download complete
   "MSG_STOP"         : VisonicCommand(convertByteArray('0B')                                 , None   , False, False,  True, 1.5, "Stop" ),     #
   "MSG_UNKNOWN_0E"   : VisonicCommand(convertByteArray('0E')                                 , None   , False, False,  True, 0.0, "Not sure what this does to the panel. Panel adds the powerlink 0x43." ),
   "MSG_EXIT"         : VisonicCommand(convertByteArray('0F')                                 , None   , False, False,  True, 1.5, "Exit" ),
   # Acknowledges
   "MSG_ACK"          : VisonicCommand(convertByteArray('02')                                 , None   , False, False, False, 0.0, "Ack" ),
   "MSG_ACK_PLINK"    : VisonicCommand(convertByteArray('02 43')                              , None   , False, False, False, 0.0, "Ack Powerlink" ),
   # PowerMaster specific

   "MSG_PM_SPECIAL1"  : VisonicCommand(convertByteArray('B0 00 42 12 AA AA 01 FF 00 0C 0A 54 01 00 00 19 21 68 00 00 20 7C 43'), None   ,  True, False,  True, 0.5, "Powermaster Special 1" ),
   "MSG_PM_SPECIAL2"  : VisonicCommand(convertByteArray('B0 00 42 12 AA AA 01 FF 00 0C 0A 54 01 01 00 25 52 55 25 50 00 7D 43'), None   ,  True, False,  True, 0.5, "Powermaster Special 2" ),
   "MSG_PM_SPECIAL3"  : VisonicCommand(convertByteArray('B0 00 42 12 AA AA 01 FF 00 0C 0A 54 01 02 00 19 21 68 00 00 01 7E 43'), None   ,  True, False,  True, 0.5, "Powermaster Special 3" ),
   "MSG_PM_SPECIAL4"  : VisonicCommand(convertByteArray('B0 00 42 12 AA AA 01 FF 00 0C 0A 54 01 02 00 19 21 68 00 00 01 7F 43'), None   ,  True, False,  True, 0.5, "Powermaster Special 4" ),

   "MSG_PM_SIREN_MODE": VisonicCommand(convertByteArray('B0 00 47 09 99 99 00 FF 08 0C 02 99 07 43')   , None   ,  True, False,  True, 0.0, "Powermaster Trigger Siren Mode" ),   # Trigger Siren, the 99 99 needs to be the usercode, other 99 is Siren Type
   "MSG_PM_SIREN"     : VisonicCommand(convertByteArray('B0 00 3E 0A 99 99 05 FF 08 02 03 00 00 01 43'), None   ,  True, False,  True, 0.0, "Powermaster Trigger Siren" ),        # Trigger Siren, the 99 99 needs to be the usercode
   "MSG_PM_SENSORS"   : VisonicCommand(convertByteArray('B0 01 17 08 01 FF 08 FF 02 18 4B 00 43')      , [0xB0] ,  True, False,  True, 0.0, "Powermaster Sensor Status" ),        # This should return 3 B0 messages "02 4B", "03 4B" and "03 18"
   "MSG_POWERMASTER"  : VisonicCommand(convertByteArray('B0 01 00 00 00 00 00 00 00 00 43')            , [0xB0] ,  True, False,  True, 0.5, "Powermaster Command Original" )
}

# B0 Messages subset that we can send to a Powermaster, embed within MSG_POWERMASTER to use
pmSendMsgB0_t = {
   "ZONE_STAT04" : convertByteArray('04 06 02 FF 08 03 00'),
   "ZONE_STAT07" : convertByteArray('07 06 02 FF 08 03 00'),
   "ZONE_STAT18" : convertByteArray('18 06 02 FF 08 03 00'),      # Sensor Open/Close State
   "ZONE_STAT1F" : convertByteArray('1F 06 02 FF 08 03 00'),      # Sensors
   "ZONE_STAT21" : convertByteArray('21 06 02 FF 08 03 00'),      # Zone Names
   "ZONE_STAT2D" : convertByteArray('2D 06 02 FF 08 03 00'),      # Zone Types
   "ZONE_STAT30" : convertByteArray('30 06 02 FF 08 03 00'),
   "ZONE_STAT31" : convertByteArray('31 06 02 FF 08 03 00'),
   "ZONE_STAT32" : convertByteArray('32 06 02 FF 08 03 00'),
   "ZONE_STAT33" : convertByteArray('33 06 02 FF 08 03 00'),
   "ZONE_STAT34" : convertByteArray('34 06 02 FF 08 03 00'),
   "ZONE_STAT35" : convertByteArray('35 06 02 FF 08 03 00'),
   "ZONE_STAT36" : convertByteArray('36 06 02 FF 08 03 00'),
   "ZONE_STAT37" : convertByteArray('37 06 02 FF 08 03 00'),
   "ZONE_STAT38" : convertByteArray('38 06 02 FF 08 03 00'),
   "ZONE_STAT39" : convertByteArray('39 06 02 FF 08 03 00'),
   "ZONE_STAT3A" : convertByteArray('3A 06 02 FF 08 03 00'),
   "ZONE_STAT3B" : convertByteArray('3B 06 02 FF 08 03 00'),
   "ZONE_STAT3C" : convertByteArray('3C 06 02 FF 08 03 00'),
   "ZONE_STAT3D" : convertByteArray('3D 06 02 FF 08 03 00'),
   "ZONE_STAT3E" : convertByteArray('3E 06 02 FF 08 03 00'),
   "ZONE_STAT3F" : convertByteArray('3F 06 02 FF 08 03 00'),
   "ZONE_STAT24" : convertByteArray('24 06 02 FF 08 03 00')
}

# Data to embed in the MSG_ARM message
pmArmMode_t = {
   AlPanelCommand.DISARM : 0x00, AlPanelCommand.ARM_HOME : 0x04, AlPanelCommand.ARM_AWAY : 0x05, AlPanelCommand.ARM_HOME_INSTANT : 0x14, AlPanelCommand.ARM_AWAY_INSTANT : 0x15    # "usertest" : 0x06,
}

# Data to embed in the MSG_PM_SIREN_MODE message
# PowerMaster to command the siren mode
pmSirenMode_t = {
   AlPanelCommand.EMERGENCY : 0x23, AlPanelCommand.FIRE : 0x20, AlPanelCommand.PANIC : 0x0C
}

# Data to embed in the MSG_X10PGM message
pmX10State_t = {
   AlX10Command.OFF : 0x00, AlX10Command.ON : 0x01, AlX10Command.DIM : 0x0A, AlX10Command.BRIGHTEN : 0x0B
}


# Message types we can receive with their length and whether they need an ACK.
#    When isvariablelength is True:
#             the length is the fixed number of bytes in the message.  Add this to the flexiblelength when it is received to get the total packet length.
#             varlenbytepos is the byte position of the variable length of the message.
#    flexiblelength provides support for messages that have a variable length
#    ignorechecksum is for messages that do not have a checksum.  These are F1 and F4 messages (so far)
#    When length is 0 then we stop processing the message on the first PACKET_FOOTER. This is only used for the short messages (4 or 5 bytes long) like ack, stop, denied and timeout
PanelCallBack = collections.namedtuple("PanelCallBack", 'length ackneeded isvariablelength varlenbytepos flexiblelength ignorechecksum' )
pmReceiveMsg_t = {
   0x00 : PanelCallBack(  0,  True, False, -1, 0, False ),   # Dummy message used in the algorithm when the message type is unknown. The -1 is used to indicate an unknown message in the algorithm
   0x02 : PanelCallBack(  0, False, False,  0, 0, False ),   # Ack
   0x06 : PanelCallBack(  0, False, False,  0, 0, False ),   # Timeout. See the receiver function for ACK handling
   0x07 : PanelCallBack(  0, False, False,  0, 0, False ),   # No idea what this means but decode it anyway
   0x08 : PanelCallBack(  0, False, False,  0, 0, False ),   # Access Denied
   0x0B : PanelCallBack(  0,  True, False,  0, 0, False ),   # Stop --> Download Complete
   0x0F : PanelCallBack(  0, False, False,  0, 0, False ),   # THE PANEL DOES NOT SEND THIS. THIS IS USED FOR A LOOP BACK TEST
   0x22 : PanelCallBack( 14,  True, False,  0, 0, False ),   # 14 Panel Info (older visonic powermax panels)
   0x25 : PanelCallBack( 14,  True, False,  0, 0, False ),   # 14 Download Retry
   0x33 : PanelCallBack( 14,  True, False,  0, 0, False ),   # 14 Download Settings
   0x3C : PanelCallBack( 14,  True, False,  0, 0, False ),   # 14 Panel Info
   0x3F : PanelCallBack(  7,  True,  True,  4, 5, False ),   # Download Info in varying lengths  (For variable length, the length is the fixed number of bytes).
   0xA0 : PanelCallBack( 15,  True, False,  0, 0, False ),   # 15 Event Log
   0xA3 : PanelCallBack( 15,  True, False,  0, 0, False ),   # 15 Zone Names
   0xA5 : PanelCallBack( 15,  True, False,  0, 0, False ),   # 15 Status Update       Length was 15 but panel seems to send different lengths
   0xA6 : PanelCallBack( 15,  True, False,  0, 0, False ),   # 15 Zone Types I think!!!!
   0xA7 : PanelCallBack( 15,  True, False,  0, 0, False ),   # 15 Panel Status Change
   0xAB : PanelCallBack( 15,  True, False,  0, 0, False ),   # 15 Enroll Request 0x0A  OR Ping 0x03      Length was 15 but panel seems to send different lengths
   0xAC : PanelCallBack( 15,  True, False,  0, 0, False ),   # 15 X10 Names ???
   0xAD : PanelCallBack( 15,  True, False,  0, 0, False ),   # 15 Panel responds with this when we ask for JPG images
   0xB0 : PanelCallBack(  8,  True,  True,  4, 2, False ),   # The B0 message comes in varying lengths, sometimes it is shorter than what it states and the CRC is sometimes wrong
   REDIRECT_POWERLINK_DATA : PanelCallBack(  5, False,  True,  2, 0, False ),   # TESTING: These are redirected Powerlink messages. 0D C0 len <data> cs 0A   so 5 plus the original data length
   # The F1 message needs to be ignored, I have no idea what it is but the crc is always wrong and only Powermax+ panels seem to send it. Assume a minimum length of 9, a variable length and ignore the checksum calculation.
   0xF1 : PanelCallBack(  9,  True,  True,  0, 0,  True ),   # Ignore checksum on all F1 messages
   # The F4 message comes in varying lengths. It is the image data from a PIR camera. Ignore checksum on all F4 messages
   0xF4 : { 0x01 : PanelCallBack(  9, False, False,  0, 0,  True ),     # 
            0x03 : PanelCallBack(  9, False,  True,  5, 0,  True ),     #
            0x05 : PanelCallBack(  9, False,  True,  5, 0,  True ),     # 
            0x15 : PanelCallBack( 13, False, False,  0, 0,  True ) }    # 
}

# Log Events
pmLogEvent_t = {
   "EN" : (
           "None",
           # 1
           "Interior Alarm", "Perimeter Alarm", "Delay Alarm", "24h Silent Alarm", "24h Audible Alarm",
           "Tamper", "Control Panel Tamper", "Tamper Alarm", "Tamper Alarm", "Communication Loss",
           "Panic From Keyfob", "Panic From Control Panel", "Duress", "Confirm Alarm", "General Trouble",
           "General Trouble Restore", "Interior Restore", "Perimeter Restore", "Delay Restore", "24h Silent Restore",
           # 21
           "24h Audible Restore", "Tamper Restore", "Control Panel Tamper Restore", "Tamper Restore", "Tamper Restore",
           "Communication Restore", "Cancel Alarm", "General Restore", "Trouble Restore", "Not used",
           "Recent Close", "Fire", "Fire Restore", "Not Active", "Emergency",
           "Remove User", "Disarm Latchkey", "Confirm Alarm Emergency", "Supervision (Inactive)", "Supervision Restore (Active)",
           "Low Battery", "Low Battery Restore", "AC Fail", "AC Restore", "Control Panel Low Battery",
           "Control Panel Low Battery Restore", "RF Jamming", "RF Jamming Restore", "Communications Failure", "Communications Restore",
           # 51
           "Telephone Line Failure", "Telephone Line Restore", "Auto Test", "Fuse Failure", "Fuse Restore",
           "Keyfob Low Battery", "Keyfob Low Battery Restore", "Engineer Reset", "Battery Disconnect", "1-Way Keypad Low Battery",
           "1-Way Keypad Low Battery Restore", "1-Way Keypad Inactive", "1-Way Keypad Restore Active", "Low Battery Ack", "Clean Me",
           "Fire Trouble", "Low Battery", "Battery Restore", "AC Fail", "AC Restore",
           "Supervision (Inactive)", "Supervision Restore (Active)", "Gas Alert", "Gas Alert Restore", "Gas Trouble",
           "Gas Trouble Restore", "Flood Alert", "Flood Alert Restore", "X-10 Trouble", "X-10 Trouble Restore",
           # 81
           "Arm Home", "Arm Away", "Quick Arm Home", "Quick Arm Away", "Disarm",
           "Fail To Auto-Arm", "Enter To Test Mode", "Exit From Test Mode", "Force Arm", "Auto Arm",
           "Instant Arm", "Bypass", "Fail To Arm", "Door Open", "Communication Established By Control Panel",
           "System Reset", "Installer Programming", "Wrong Password", "Not Sys Event", "Not Sys Event",
           # 101
           "Extreme Hot Alert", "Extreme Hot Alert Restore", "Freeze Alert", "Freeze Alert Restore", "Human Cold Alert",
           "Human Cold Alert Restore", "Human Hot Alert", "Human Hot Alert Restore", "Temperature Sensor Trouble", "Temperature Sensor Trouble Restore",

           # New values for PowerMaster and models with partitions
           "PIR Mask", "PIR Mask Restore", "Repeater low battery", "Repeater low battery restore", "Repeater inactive",
           "Repeater inactive restore", "Repeater tamper", "Repeater tamper restore", "Siren test end", "Devices test end",
           # 121
           "One way comm. trouble", "One way comm. trouble restore", "Sensor outdoor alarm", "Sensor outdoor restore", "Guard sensor alarmed",
           "Guard sensor alarmed restore", "Date time change", "System shutdown", "System power up", "Missed Reminder",
           "Pendant test fail", "Basic KP inactive", "Basic KP inactive restore", "Basic KP tamper", "Basic KP tamper Restore",
           "Heat", "Heat restore", "LE Heat Trouble", "CO alarm", "CO alarm restore",
           # 141
           "CO trouble", "CO trouble restore", "Exit Installer", "Enter Installer", "Self test trouble",
           "Self test restore", "Confirm panic event", "n/a", "Soak test fail", "Fire Soak test fail",
           "Gas Soak test fail", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a"),
   "NL" : (
           "Geen",
           # 1
           "In alarm", "In alarm", "In alarm", "In alarm", "In alarm",
           "Sabotage alarm", "Systeem sabotage", "Sabotage alarm", "Add user", "Communicate fout",
           "Paniekalarm", "Code bedieningspaneel paniek", "Dwang", "Bevestig alarm", "Successful U/L",
           "Probleem herstel", "Herstel", "Herstel", "Herstel", "Herstel",
           "Herstel", "Sabotage herstel", "Systeem sabotage herstel", "Sabotage herstel", "Sabotage herstel",
           "Communicatie herstel", "Stop alarm", "Algemeen herstel", "Brand probleem herstel", "Systeem inactief",
           "Recent close", "Brand", "Brand herstel", "Niet actief", "Noodoproep",
           "Remove user", "Controleer code", "Bevestig alarm", "Supervisie", "Supervisie herstel",
           "Batterij laag", "Batterij OK", "230VAC uitval", "230VAC herstel", "Controlepaneel batterij laag",
           "Controlepaneel batterij OK", "Radio jamming", "Radio herstel", "Communicatie mislukt", "Communicatie hersteld",
           # 51
           "Telefoonlijn fout", "Telefoonlijn herstel", "Automatische test", "Zekeringsfout", "Zekering herstel",
           "Batterij laag", "Batterij OK", "Monteur reset", "Accu vermist", "Batterij laag",
           "Batterij OK", "Supervisie", "Supervisie herstel", "Lage batterij bevestiging", "Reinigen",
           "Probleem", "Batterij laag", "Batterij OK", "230VAC uitval", "230VAC herstel",
           "Supervisie", "Supervisie herstel", "Gas alarm", "Gas herstel", "Gas probleem",
           "Gas probleem herstel", "Lekkage alarm", "Lekkage herstel", "X-10 Probleem", "X-10 Probleem herstel",
           # 81
           "Deelschakeling", "Ingeschakeld", "Snel deelschakeling", "Snel ingeschakeld", "Uitgezet",
           "Inschakelfout (auto)", "Test gestart", "Test gestopt", "Force aan", "Geheel in (auto)",
           "Onmiddelijk", "Overbruggen", "Inschakelfout", "Door Open", "Communication Established By Control Panel",
           "Systeem reset", "Installateur programmeert", "Foutieve code", "Not Sys Event", "Not Sys Event",
           # 101
           "Extreme Hot Alert", "Extreme Hot Alert Restore", "Freeze Alert", "Freeze Alert Restore", "Human Cold Alert",
           "Human Cold Alert Restore", "Human Hot Alert", "Human Hot Alert Restore", "Temperature Sensor Trouble", "Temperature Sensor Trouble Restore",

           # New values for PowerMaster and models with partitions
           "PIR Mask", "PIR Mask Restore", "Repeater low battery", "Repeater low battery restore", "Repeater inactive",
           "Repeater inactive restore", "Repeater tamper", "Repeater tamper restore", "Siren test end", "Devices test end",
           # 121
           "One way comm. trouble", "One way comm. trouble restore", "Sensor outdoor alarm", "Sensor outdoor restore", "Guard sensor alarmed",
           "Guard sensor alarmed restore", "Date time change", "System shutdown", "System power up", "Missed Reminder",
           "Pendant test fail", "Basic KP inactive", "Basic KP inactive restore", "Basic KP tamper", "Basic KP tamper Restore",
           "Heat", "Heat restore", "LE Heat Trouble", "CO alarm", "CO alarm restore",
           # 141
           "CO trouble", "CO trouble restore", "Exit Installer", "Enter Installer", "Self test trouble",
           "Self test restore", "Confirm panic event", "n/a", "Soak test fail", "Fire Soak test fail",
           "Gas Soak test fail", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a"),
   "FR" : (
           "Aucun",
           # 1
           "Alarme Intérieure", "Alarme Périphérie", "Alarme Différée", "Alarme Silencieuse 24H", "Alarme Audible 24H",
           "Autoprotection", "Alarme Autoprotection Centrale", "Alarme Autoprotection", "Alarme Autoprotection", "Défaut de communication",
           "Alarme Panique Depuis Memclé", "Alarme Panique depuis Centrale", "Contrainte", "Confirmer l'alarme", "Perte de Communication",
           "Rétablissement Défaut Supervision", "Rétablissement Alarme Intérieure", "Rétablissement Alarme Périphérie", "Rétablissement Alarme Différée", "Rétablissement Alarme Silencieuse 24H",
           "Rétablissement Alarme Audible 24H", "Rétablissement Autoprotection", "Rétablissement Alarme Autoprotection Centrale", "Rétablissement Alarme Autoprotection", "Rétablissement Alarme Autoprotection",
           "Rétablissement des Communications", "Annuler Alarme", "Rétablissement Général", "Rétablissement Défaut", "Pas utilisé",
           "Evenement Fermeture Récente", "Alarme Incendie", "Rétablissement Alarme Incendie", "Non Actif", "Urgence",
           "Pas utilisé", "Désarmement Latchkey", "Rétablissement Alarme Panique", "Défaut Supervision (Inactive)", "Rétablissement Supervision (Active)",
           "Batterie Faible", "Rétablissement Batterie Faible", "Coupure Secteur", "Rétablissement Secteur", "Batterie Centrale Faible",
           "Rétablissement Batterie Centrale Faible", "Détection Brouillage Radio", "Rétablissement Détection Brouillage Radio", "Défaut Communication", "Rétablissement Communications",
           # 51
           "Défaut Ligne Téléphonique", "Rétablissement Ligne Téléphonique", "Auto Test", "Coupure Secteur/Fusible", "Rétablissement Secteur/Fusible",
           "Memclé Batterie Faible", "Rétablissement Memclé Batterie Faible", "Réinitialisation Technicien", "Batterie Déconnectée ", "Clavier/Télécommande Batterie Faible",
           "Rétablissement Clavier/Télécommande Batterie Faible", "Clavier/Télécommande Inactif", "Rétablissement Clavier/Télécommande Actif", "Batterie Faible", "Nettoyage Détecteur Incendie",
           "Alarme incendie", "Batterie Faible", "Rétablissement Batterie", "Coupure Secteur", "Rétablissement Secteur",
           "Défaut Supervision (Inactive)", "Rétablissement Supervision (Active)", "Alarme Gaz", "Rétablissement Alarme Gaz", "Défaut Gaz",
           "Rétablissement Défaut Gaz", "Alarme Inondation", "Rétablissement Alarme Inondation", "Défaut X-10", "Rétablissement Défaut X-10",
           # 81
           "Armement Partiel", "Armement Total", "Armement Partiel Instantané", "Armement Total Instantané", "Désarmement",
           "Echec d'armement", "Entrer dans Mode Test", "Sortir du Mode Test", "Fermeture Forcée", "Armement Automatique",
           "Armement Instantané", "Bypass", "Echec d'Armement", "Porte Ouverte", "Communication établie par le panneau de control",
           "Réinitialisation du Système", "Installer Programming", "Mauvais code PIN", "Not Sys Event", "Not Sys Event",
           # 101
           "Alerte Chaleure Extrême", "Rétablissement Alerte Chaleure Extrême", "Alerte Gel", "Rétablissement Alerte Gel", "Alerte Froid",
           "Rétablissement Alerte Froid", "Alerte Chaud", "Rétablissement Alerte Chaud", "Défaut Capteur de Température", "Rétablissement Défaut Capteur de Température",

           # New values for PowerMaster and models with partitions
           "PIR Masqué", "Rétablissement PIR Masqué", "Repeater low battery", "Repeater low battery restore", "Repeater inactive",
           "Repeater inactive restore", "Repeater tamper", "Repeater tamper restore", "Siren test end", "Devices test end",
           # 121
           "One way comm. trouble", "One way comm. trouble restore", "Sensor outdoor alarm", "Sensor outdoor restore", "Guard sensor alarmed",
           "Guard sensor alarmed restore", "Date time change", "System shutdown", "System power up", "Missed Reminder",
           "Pendant test fail", "Basic KP inactive", "Basic KP inactive restore", "Basic KP tamper", "Basic KP tamper Restore",
           "Heat", "Heat restore", "LE Heat Trouble", "CO alarm", "CO alarm restore",
           # 141
           "CO trouble", "CO trouble restore", "Sortir Mode Installeur", "Entrer Mode Installeur", "Self test trouble",
           "Self test restore", "Confirm panic event", "n/a", "Soak test fail", "Fire Soak test fail",
           "Gas Soak test fail", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a", "n/a", "n/a", "n/a", "n/a",
           "n/a")
}


pmLogPowerMaxUser_t = {
  "EN" : [ "System", "Zone 01", "Zone 02", "Zone 03", "Zone 04", "Zone 05", "Zone 06", "Zone 07", "Zone 08",
           "Zone 09", "Zone 10", "Zone 11", "Zone 12", "Zone 13", "Zone 14", "Zone 15", "Zone 16", "Zone 17", "Zone 18",
           "Zone 19", "Zone 20", "Zone 21", "Zone 22", "Zone 23", "Zone 24", "Zone 25", "Zone 26", "Zone 27", "Zone 28",
           "Zone 29", "Zone 30", "Fob  01", "Fob  02", "Fob  03", "Fob  04", "Fob  05", "Fob  06", "Fob  07", "Fob  08",
           "User 01", "User 02", "User 03", "User 04", "User 05", "User 06", "User 07", "User 08", "Pad  01", "Pad  02",
           "Pad  03", "Pad  04", "Pad  05", "Pad  06", "Pad  07", "Pad  08", "Sir  01", "Sir  02", "2Pad 01", "2Pad 02",
           "2Pad 03", "2Pad 04", "X10  01", "X10  02", "X10  03", "X10  04", "X10  05", "X10  06", "X10  07", "X10  08",
           "X10  09", "X10  10", "X10  11", "X10  12", "X10  13", "X10  14", "X10  15", "PGM    ", "GSM    ", "P-LINK ",
           "PTag 01", "PTag 02", "PTag 03", "PTag 04", "PTag 05", "PTag 06", "PTag 07", "PTag 08", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown"],
  "NL" : [ "Systeem", "Zone 01", "Zone 02", "Zone 03", "Zone 04", "Zone 05", "Zone 06", "Zone 07", "Zone 08",
           "Zone 09", "Zone 10", "Zone 11", "Zone 12", "Zone 13", "Zone 14", "Zone 15", "Zone 16", "Zone 17", "Zone 18",
           "Zone 19", "Zone 20", "Zone 21", "Zone 22", "Zone 23", "Zone 24", "Zone 25", "Zone 26", "Zone 27", "Zone 28",
           "Zone 29", "Zone 30", "Fob  01", "Fob  02", "Fob  03", "Fob  04", "Fob  05", "Fob  06", "Fob  07", "Fob  08",
           "Gebruiker 01", "Gebruiker 02", "Gebruiker 03", "Gebruiker 04", "Gebruiker 05", "Gebruiker 06", "Gebruiker 07",
           "Gebruiker 08", "Pad  01", "Pad  02",
           "Pad  03", "Pad  04", "Pad  05", "Pad  06", "Pad  07", "Pad  08", "Sir  01", "Sir  02", "2Pad 01", "2Pad 02",
           "2Pad 03", "2Pad 04", "X10  01", "X10  02", "X10  03", "X10  04", "X10  05", "X10  06", "X10  07", "X10  08",
           "X10  09", "X10  10", "X10  11", "X10  12", "X10  13", "X10  14", "X10  15", "PGM    ", "GSM    ", "P-LINK ",
           "PTag 01", "PTag 02", "PTag 03", "PTag 04", "PTag 05", "PTag 06", "PTag 07", "PTag 08", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown"],
  "FR" : [ "Système", "Zone 01", "Zone 02", "Zone 03", "Zone 04", "Zone 05", "Zone 06", "Zone 07", "Zone 08",
           "Zone 09", "Zone 10", "Zone 11", "Zone 12", "Zone 13", "Zone 14", "Zone 15", "Zone 16", "Zone 17", "Zone 18",
           "Zone 19", "Zone 20", "Zone 21", "Zone 22", "Zone 23", "Zone 24", "Zone 25", "Zone 26", "Zone 27", "Zone 28",
           "Zone 29", "Zone 30", "Memclé  01", "Memclé  02", "Memclé  03", "Memclé  04", "Memclé  05", "Memclé  06", "Memclé  07", "Memclé  08",
           "User 01", "User 02", "User 03", "User 04", "User 05", "User 06", "User 07", "User 08", "Pad  01", "Pad  02",
           "Pad  03", "Pad  04", "Pad  05", "Pad  06", "Pad  07", "Pad  08", "Sir  01", "Sir  02", "2Pad 01", "2Pad 02",
           "2Pad 03", "2Pad 04", "X10  01", "X10  02", "X10  03", "X10  04", "X10  05", "X10  06", "X10  07", "X10  08",
           "X10  09", "X10  10", "X10  11", "X10  12", "X10  13", "X10  14", "X10  15", "PGM    ", "GSM    ", "P-LINK ",
           "PTag 01", "PTag 02", "PTag 03", "PTag 04", "PTag 05", "PTag 06", "PTag 07", "PTag 08", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown"]
}

pmLogPowerMasterUser_t = {
  "EN" : [ "System", "Zone 01", "Zone 02", "Zone 03", "Zone 04", "Zone 05", "Zone 06", "Zone 07", "Zone 08",
           "Zone 09", "Zone 10", "Zone 11", "Zone 12", "Zone 13", "Zone 14", "Zone 15", "Zone 16", "Zone 17", "Zone 18",
           "Zone 19", "Zone 20", "Zone 21", "Zone 22", "Zone 23", "Zone 24", "Zone 25", "Zone 26", "Zone 27", "Zone 28",
           "Zone 29", "Zone 30", "Zone 31", "Zone 32", "Zone 33", "Zone 34", "Zone 35", "Zone 36", "Zone 37", "Zone 38",
           "Zone 39", "Zone 40", "Zone 41", "Zone 42", "Zone 43", "Zone 44", "Zone 45", "Zone 46", "Zone 47", "Zone 48",
           "Zone 49", "Zone 50", "Zone 51", "Zone 52", "Zone 53", "Zone 54", "Zone 55", "Zone 56", "Zone 57", "Zone 58",
           "Zone 59", "Zone 60", "Zone 61", "Zone 62", "Zone 63", "Zone 64",
           "Fob  01", "Fob  02", "Fob  03", "Fob  04", "Fob  05", "Fob  06", "Fob  07", "Fob  08", "Fob  09", "Fob  10",
           "Fob  11", "Fob  12", "Fob  13", "Fob  14", "Fob  15", "Fob  16", "Fob  17", "Fob  18", "Fob  19", "Fob  20",
           "Fob  21", "Fob  22", "Fob  23", "Fob  24", "Fob  25", "Fob  26", "Fob  27", "Fob  28", "Fob  29", "Fob  30",
           "Fob  31", "Fob  32",
           "User 01", "User 02", "User 03", "User 04", "User 05", "User 06", "User 07", "User 08", "User 09", "User 10",
           "User 11", "User 12", "User 13", "User 14", "User 15", "User 16", "User 17", "User 18", "User 19", "User 20",
           "User 21", "User 22", "User 23", "User 24", "User 25", "User 26", "User 27", "User 28", "User 29", "User 30",
           "User 31", "User 32", "User 33", "User 34", "User 35", "User 36", "User 37", "User 38", "User 39", "User 40",
           "User 41", "User 42", "User 43", "User 44", "User 45", "User 46", "User 47", "User 48",
           "Pad  01", "Pad  02", "Pad  03", "Pad  04", "Pad  05", "Pad  06", "Pad  07", "Pad  08", "Pad  09", "Pad  10",
           "Pad  11", "Pad  12", "Pad  13", "Pad  14", "Pad  15", "Pad  16", "Pad  17", "Pad  18", "Pad  19", "Pad  20",
           "Pad  21", "Pad  22", "Pad  23", "Pad  24", "Pad  25", "Pad  26", "Pad  27", "Pad  28", "Pad  29", "Pad  30",
           "Pad  31", "Pad  32",
           "Sir  01", "Sir  02", "Sir  03", "Sir  04", "Sir  05", "Sir  06", "Sir  07", "Sir  08",
           "2Pad 01", "2Pad 02", "2Pad 03", "2Pad 04",
           "X10  01", "X10  02", "X10  03", "X10  04", "X10  05", "X10  06", "X10  07", "X10  08",
           "X10  09", "X10  10", "X10  11", "X10  12", "X10  13", "X10  14", "X10  15", "PGM    ", "P-LINK ",
           "PTag 01", "PTag 02", "PTag 03", "PTag 04", "PTag 05", "PTag 06", "PTag 07", "PTag 08", "PTag 09", "PTag 10",
           "PTag 11", "PTag 12", "PTag 13", "PTag 14", "PTag 15", "PTag 16", "PTag 17", "PTag 18", "PTag 19", "PTag 20",
           "PTag 21", "PTag 22", "PTag 23", "PTag 24", "PTag 25", "PTag 26", "PTag 27", "PTag 28", "PTag 29", "PTag 30",
           "PTag 31", "PTag 32",
           "Rptr 01", "Rptr 02", "Rptr 03", "Rptr 04", "Rptr 05", "Rptr 06", "Rptr 07", "Rptr 08",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown"],
  "NL" : [ "Systeem", "Zone 01", "Zone 02", "Zone 03", "Zone 04", "Zone 05", "Zone 06", "Zone 07", "Zone 08",
           "Zone 09", "Zone 10", "Zone 11", "Zone 12", "Zone 13", "Zone 14", "Zone 15", "Zone 16", "Zone 17", "Zone 18",
           "Zone 19", "Zone 20", "Zone 21", "Zone 22", "Zone 23", "Zone 24", "Zone 25", "Zone 26", "Zone 27", "Zone 28",
           "Zone 29", "Zone 30", "Zone 31", "Zone 32", "Zone 33", "Zone 34", "Zone 35", "Zone 36", "Zone 37", "Zone 38",
           "Zone 39", "Zone 40", "Zone 41", "Zone 42", "Zone 43", "Zone 44", "Zone 45", "Zone 46", "Zone 47", "Zone 48",
           "Zone 49", "Zone 50", "Zone 51", "Zone 52", "Zone 53", "Zone 54", "Zone 55", "Zone 56", "Zone 57", "Zone 58",
           "Zone 59", "Zone 60", "Zone 61", "Zone 62", "Zone 63", "Zone 64",
           "Fob  01", "Fob  02", "Fob  03", "Fob  04", "Fob  05", "Fob  06", "Fob  07", "Fob  08", "Fob  09", "Fob  10",
           "Fob  11", "Fob  12", "Fob  13", "Fob  14", "Fob  15", "Fob  16", "Fob  17", "Fob  18", "Fob  19", "Fob  20",
           "Fob  21", "Fob  22", "Fob  23", "Fob  24", "Fob  25", "Fob  26", "Fob  27", "Fob  28", "Fob  29", "Fob  30",
           "Fob  31", "Fob  32",
           "Gebruiker 01", "Gebruiker 02", "Gebruiker 03", "Gebruiker 04", "Gebruiker 05", "Gebruiker 06", "Gebruiker 07", "Gebruiker 08", "Gebruiker 09", "Gebruiker 10",
           "Gebruiker 11", "Gebruiker 12", "Gebruiker 13", "Gebruiker 14", "Gebruiker 15", "Gebruiker 16", "Gebruiker 17", "Gebruiker 18", "Gebruiker 19", "Gebruiker 20",
           "Gebruiker 21", "Gebruiker 22", "Gebruiker 23", "Gebruiker 24", "Gebruiker 25", "Gebruiker 26", "Gebruiker 27", "Gebruiker 28", "Gebruiker 29", "Gebruiker 30",
           "Gebruiker 31", "Gebruiker 32", "Gebruiker 33", "Gebruiker 34", "Gebruiker 35", "Gebruiker 36", "Gebruiker 37", "Gebruiker 38", "Gebruiker 39", "Gebruiker 40",
           "Gebruiker 41", "Gebruiker 42", "Gebruiker 43", "Gebruiker 44", "Gebruiker 45", "Gebruiker 46", "Gebruiker 47", "Gebruiker 48",
           "Pad  01", "Pad  02", "Pad  03", "Pad  04", "Pad  05", "Pad  06", "Pad  07", "Pad  08", "Pad  09", "Pad  10",
           "Pad  11", "Pad  12", "Pad  13", "Pad  14", "Pad  15", "Pad  16", "Pad  17", "Pad  18", "Pad  19", "Pad  20",
           "Pad  21", "Pad  22", "Pad  23", "Pad  24", "Pad  25", "Pad  26", "Pad  27", "Pad  28", "Pad  29", "Pad  30",
           "Pad  31", "Pad  32",
           "Sir  01", "Sir  02", "Sir  03", "Sir  04", "Sir  05", "Sir  06", "Sir  07", "Sir  08",
           "2Pad 01", "2Pad 02", "2Pad 03", "2Pad 04",
           "X10  01", "X10  02", "X10  03", "X10  04", "X10  05", "X10  06", "X10  07", "X10  08",
           "X10  09", "X10  10", "X10  11", "X10  12", "X10  13", "X10  14", "X10  15", "PGM    ", "P-LINK ",
           "PTag 01", "PTag 02", "PTag 03", "PTag 04", "PTag 05", "PTag 06", "PTag 07", "PTag 08", "PTag 09", "PTag 10",
           "PTag 11", "PTag 12", "PTag 13", "PTag 14", "PTag 15", "PTag 16", "PTag 17", "PTag 18", "PTag 19", "PTag 20",
           "PTag 21", "PTag 22", "PTag 23", "PTag 24", "PTag 25", "PTag 26", "PTag 27", "PTag 28", "PTag 29", "PTag 30",
           "PTag 31", "PTag 32",
           "Rptr 01", "Rptr 02", "Rptr 03", "Rptr 04", "Rptr 05", "Rptr 06", "Rptr 07", "Rptr 08"],
  "FR" : [ "Système", "Zone 01", "Zone 02", "Zone 03", "Zone 04", "Zone 05", "Zone 06", "Zone 07", "Zone 08",
           "Zone 09", "Zone 10", "Zone 11", "Zone 12", "Zone 13", "Zone 14", "Zone 15", "Zone 16", "Zone 17", "Zone 18",
           "Zone 19", "Zone 20", "Zone 21", "Zone 22", "Zone 23", "Zone 24", "Zone 25", "Zone 26", "Zone 27", "Zone 28",
           "Zone 29", "Zone 30", "Zone 31", "Zone 32", "Zone 33", "Zone 34", "Zone 35", "Zone 36", "Zone 37", "Zone 38",
           "Zone 39", "Zone 40", "Zone 41", "Zone 42", "Zone 43", "Zone 44", "Zone 45", "Zone 46", "Zone 47", "Zone 48",
           "Zone 49", "Zone 50", "Zone 51", "Zone 52", "Zone 53", "Zone 54", "Zone 55", "Zone 56", "Zone 57", "Zone 58",
           "Zone 59", "Zone 60", "Zone 61", "Zone 62", "Zone 63", "Zone 64",
           "Memclé  01", "Memclé  02", "Memclé  03", "Memclé  04", "Memclé  05", "Memclé  06", "Memclé  07", "Memclé  08", "Memclé  09", "Memclé  10",
           "Memclé  11", "Memclé  12", "Memclé  13", "Memclé  14", "Memclé  15", "Memclé  16", "Memclé  17", "Memclé  18", "Memclé  19", "Memclé  20",
           "Memclé  21", "Memclé  22", "Memclé  23", "Memclé  24", "Memclé  25", "Memclé  26", "Memclé  27", "Memclé  28", "Memclé  29", "Memclé  30",
           "Memclé  31", "Memclé  32",
           "User 01", "User 02", "User 03", "User 04", "User 05", "User 06", "User 07", "User 08", "User 09", "User 10",
           "User 11", "User 12", "User 13", "User 14", "User 15", "User 16", "User 17", "User 18", "User 19", "User 20",
           "User 21", "User 22", "User 23", "User 24", "User 25", "User 26", "User 27", "User 28", "User 29", "User 30",
           "User 31", "User 32", "User 33", "User 34", "User 35", "User 36", "User 37", "User 38", "User 39", "User 40",
           "User 41", "User 42", "User 43", "User 44", "User 45", "User 46", "User 47", "User 48",
           "Pad  01", "Pad  02", "Pad  03", "Pad  04", "Pad  05", "Pad  06", "Pad  07", "Pad  08", "Pad  09", "Pad  10",
           "Pad  11", "Pad  12", "Pad  13", "Pad  14", "Pad  15", "Pad  16", "Pad  17", "Pad  18", "Pad  19", "Pad  20",
           "Pad  21", "Pad  22", "Pad  23", "Pad  24", "Pad  25", "Pad  26", "Pad  27", "Pad  28", "Pad  29", "Pad  30",
           "Pad  31", "Pad  32",
           "Sir  01", "Sir  02", "Sir  03", "Sir  04", "Sir  05", "Sir  06", "Sir  07", "Sir  08",
           "2Pad 01", "2Pad 02", "2Pad 03", "2Pad 04",
           "X10  01", "X10  02", "X10  03", "X10  04", "X10  05", "X10  06", "X10  07", "X10  08",
           "X10  09", "X10  10", "X10  11", "X10  12", "X10  13", "X10  14", "X10  15", "PGM    ", "P-LINK ",
           "PTag 01", "PTag 02", "PTag 03", "PTag 04", "PTag 05", "PTag 06", "PTag 07", "PTag 08", "PTag 09", "PTag 10",
           "PTag 11", "PTag 12", "PTag 13", "PTag 14", "PTag 15", "PTag 16", "PTag 17", "PTag 18", "PTag 19", "PTag 20",
           "PTag 21", "PTag 22", "PTag 23", "PTag 24", "PTag 25", "PTag 26", "PTag 27", "PTag 28", "PTag 29", "PTag 30",
           "PTag 31", "PTag 32",
           "Rptr 01", "Rptr 02", "Rptr 03", "Rptr 04", "Rptr 05", "Rptr 06", "Rptr 07", "Rptr 08",
           "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown"]
}

pmSysStatus_t = {
   "EN" : (
           "Disarmed", "Home Exit Delay", "Away Exit Delay", "Entry Delay", "Armed Home", "Armed Away", "User Test",
           "Downloading", "Programming", "Installer", "Home Bypass", "Away Bypass", "Ready", "Not Ready", "??", "??",
           "Disarmed Instant", "Home Instant Exit Delay", "Away Instant Exit Delay", "Entry Delay Instant", "Armed Home Instant",
           "Armed Away Instant", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??" ),
   "NL" : (
           "Uitgeschakeld", "Deel uitloopvertraging", "Totaal uitloopvertraging", "Inloopvertraging", "Deel ingeschakeld",
           "Totaal ingeschakeld", "Gebruiker test", "Downloaden", "Programmeren", "Monteurmode", "Deel met overbrugging",
           "Totaal met overbrugging", "Klaar", "Niet klaar", "??", "??", "Direct uitschakelen", "Direct Deel uitloopvertraging",
           "Direct Totaal uitloopvertraging", "Direct inloopvertraging", "Direct Deel", "Direct Totaal",
           "??", "??", "??", "??", "??", "??", "??", "??", "??", "??" ),
	"FR" : (
           "Désarmé", "Délai Armement Partiel", "Délai Armement Total", "Délai d'Entrée", "Armé Partiel", "Armé Total", "Test Utilisateur",
           "Téléchargement", "Programmation", "Mode Installateur", "Isolation Partielle", "Isolation Total", "Prêt", "Non Prêt", "??", "??",
           "Désarmé Instantané", "Temporisation Armement Partiel Instantané", "Temporisation Armement Total Instantané", "Temporisation d'Entrée Instantané", "Armé Total Instantané",
           "Armé Total Instantané", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??" )
}

pmSysStatusFlags_t = {
   "EN" : ( "Ready", "Alert in memory", "Trouble", "Bypass on", "Last 10 seconds", "Zone event", "Status changed", "Alarm event" ),
   "NL" : ( "Klaar", "Alarm in geheugen", "Probleem", "Overbruggen aan", "Laatste 10 seconden", "Zone verstoord", "Status gewijzigd", "Alarm actief"),
   "FR" : ( "Prêt", "Alerte en mémoire", "Défaut/Panne", "Isolation sur", "10 dernières secondes", "Evenement Zone", "Etat modifié", "Evenment Alarme" )
}

pmDetailedArmMode_t = (
   "Disarmed", "ExitDelay_ArmHome", "ExitDelay_ArmAway", "EntryDelay", "Stay", "Armed", "UserTest", "Downloading", "Programming", "Installer",
   "Home Bypass", "Away Bypass", "Ready", "NotReady", "??", "??", "Disarm", "ExitDelay", "ExitDelay", "EntryDelay", "StayInstant", "ArmedInstant",
   "??", "??", "??", "??", "??", "??", "??", "??", "??", "??"
)

pmEventType_t = {
   "EN" : (
           "None", "Tamper Alarm", "Tamper Restore", "Open", "Closed", "Violated (Motion)", "Panic Alarm", "RF Jamming",
           "Tamper Open", "Communication Failure", "Line Failure", "Fuse", "Not Active", "Low Battery", "AC Failure",
           "Fire Alarm", "Emergency", "Siren Tamper", "Siren Tamper Restore", "Siren Low Battery", "Siren AC Fail" ),
   "NL" : (
           "Geen", "Sabotage alarm", "Sabotage herstel", "Open", "Gesloten", "Verstoord (beweging)", "Paniek alarm", "RF verstoring",
           "Sabotage open", "Communicatie probleem", "Lijnfout", "Zekering", "Niet actief", "Lage batterij", "AC probleem",
           "Brandalarm", "Noodoproep", "Sirene sabotage", "Sirene sabotage herstel", "Sirene lage batterij", "Sirene AC probleem" ),
   "FR" : (
           "Aucun", "Alarme Autoprotection", "Rétablissement Alarme Autoprotection", "Ouvert", "Fermé", "Violation (Mouvement)", "Alarme Panique", "Détection Brouillage Radio",
           "Ouverture Autoprotection", "Echec de Communication", "Echec Ligne Téléphonique", "Fusible", "Pas Active", "Batterie Faible", "Echec Alimentation Secteur",
           "Alarme Incendie", "Urgence", "Autoprotection Sirène", "Rétablissement Autoprotection Sirène", "Batterie Faible Sirène", "Coupure Secteur Sirène" )
}

# A single value is in the A7 message that denotes the alarm / trouble status.  There could be up to 4 messages in A7.
# Event Type Constants
EVENT_TYPE_SYSTEM_RESET = 0x60
EVENT_TYPE_FORCE_ARM = 0x59
EVENT_TYPE_DISARM = 0x55
EVENT_TYPE_SENSOR_TAMPER = 0x06
EVENT_TYPE_PANEL_TAMPER = 0x07
EVENT_TYPE_TAMPER_ALARM_A = 0x08
EVENT_TYPE_TAMPER_ALARM_B = 0x09
EVENT_TYPE_ALARM_CANCEL = 0x1B
EVENT_TYPE_FIRE_RESTORE = 0x21
EVENT_TYPE_FLOOD_ALERT_RESTORE = 0x4A
EVENT_TYPE_GAS_TROUBLE_RESTORE = 0x4E
EVENT_TYPE_INTERIOR_RESTORE = 0x11
EVENT_TYPE_PERIMETER_RESTORE = 0x12
EVENT_TYPE_DELAY_RESTORE = 0x13
EVENT_TYPE_CONFIRM_ALARM = 0x0E

pmPanelType_t = {
   0 : "PowerMax", 1 : "PowerMax+", 2 : "PowerMax Pro", 3 : "PowerMax Complete", 4 : "PowerMax Pro Part",
   5  : "PowerMax Complete Part", 6 : "PowerMax Express", 7 : "PowerMaster 10",   8 : "PowerMaster 30",
   10 : "PowerMaster 33", 13 : "PowerMaster 360", 15 : "PowerMaster 33", 16 : "PowerMaster 360R"
}

# Config for each panel type (0-16).  8 is a PowerMaster 30, 10 is a PowerMaster 33, 15 is a PowerMaster 33 later model.  Don't know what 9, 11, 12 or 14 is.
pmPanelConfig_t = {
   "CFG_PARTITIONS"  : (   1,   1,   1,   1,   3,   3,   1,   3,   3,   3,   3,   3,   3,   3,   3,   3,   3 ),
#   "CFG_EVENTS"      : ( 250, 250, 250, 250, 250, 250, 250, 250,1000,1000,1000,1000,1000,1000,1000,1000,1000 ),
#   "CFG_KEYFOBS"     : (   8,   8,   8,   8,   8,   8,   8,   8,  32,  32,  32,  32,  32,  32,  32,  32,  32 ),
   "CFG_1WKEYPADS"   : (   8,   8,   8,   8,   8,   8,   8,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0 ),
   "CFG_2WKEYPADS"   : (   2,   2,   2,   2,   2,   2,   2,   8,  32,  32,  32,  32,  32,  32,  32,  32,  32 ),
   "CFG_SIRENS"      : (   2,   2,   2,   2,   2,   2,   2,   4,   8,   8,   8,   8,   8,   8,   8,   8,   8 ),
   "CFG_USERCODES"   : (   8,   8,   8,   8,   8,   8,   8,   8,  48,  48,  48,  48,  48,  48,  48,  48,  48 ),
#   "CFG_PROXTAGS"    : (   0,   0,   8,   0,   8,   8,   0,   8,  32,  32,  32,  32,  32,  32,  32,  32,  32 ),
#   "CFG_ZONECUSTOM"  : (   0,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5 ),
   "CFG_WIRELESS"    : (  28,  28,  28,  28,  28,  28,  29,  29,  62,  62,  62,  62,  62,  64,  62,  62,  64 ), # Wireless + Wired total 30 or 64
   "CFG_WIRED"       : (   2,   2,   2,   2,   2,   2,   1,   1,   2,   2,   2,   2,   2,   0,   2,   2,   0 )
}

XDumpy = False      # Used to dump PowerMax Data to the log file
SDumpy = False      # Used to dump PowerMaster Data to the log file
Dumpy = XDumpy or SDumpy

# PMAX EEPROM CONFIGURATION version 1_2
SettingsCommand = collections.namedtuple('SettingsCommand', 'show count type poff psize pstep pbitoff name values')
DecodePanelSettings = {
    "ChangedTime"    : SettingsCommand(   True,  1, "DATE",    248,  48,   0,    -1,  "EPROM Change Time I Think",          { } ),
    "jamDetect"      : SettingsCommand(   True,  1, "BYTE",    256,   8,   0,    -1,  "Jamming Detection",                  { '1':"UL 20/20", '2':"EN 30/60", '3':"Class 6", '4':"Other", '0':"Disable"} ),
    "entryDelays"    : SettingsCommand(   True,  2, "BYTE",    257,   8,   1,     2,  ["Entry Delay 1","Entry Delay 2"],    { '0':"None", '15':"15 Seconds", '30':"30 Seconds", '45':"45 Seconds", '60':"1 Minute", '180':"3 Minutes", '240':"4 Minutes"}),  # 257, 258
    "exitDelay"      : SettingsCommand(   True,  1, "BYTE",    259,   8,   0,    -1,  "Exit Delay",                         { '30':"30 Seconds", '60':"60 Seconds", '90':"90 Seconds", '120':"2 Minutes", '180':"3 Minutes", '240':"4 Minutes"}),
    "bellTime"       : SettingsCommand(   True,  1, "BYTE",    260,   8,   0,    -1,  "Bell Time",                          { '1':"1 Minute", '3':"3 Minutes", '4':"4 Minutes", '8':"8 Minutes", '10':"10 Minutes", '15':"15 Minutes", '20':"20 Minutes"}),
    "piezoBeeps"     : SettingsCommand(   True,  1, "BYTE",    261,   8,   0,    -1,  "Piezo Beeps",                        { '3':"Enable (off when home)", '2':"Enable", '1':"Off when Home", '0':"Disable"} ),
    "swingerStop"    : SettingsCommand(   True,  1, "BYTE",    262,   8,   0,    -1,  "Swinger Stop",                       { '1':"After 1 Time", '2':"After 2 Times", '3':"After 3 Times", '0':"No Shutdown"} ),
    "fobAux"         : SettingsCommand(   True,  2, "BYTE",    263,   8,  14,    -1,  ["Aux Key 1","Aux Key 2"],            { '1':"System Status", '2':"Instant Arm", '3':"Cancel Exit Delay", '4':"PGM/X-10"} ), # 263, 277
    "supervision"    : SettingsCommand(   True,  1, "BYTE",    264,   8,   0,    -1,  "Supervision Interval",               { '1':"1 Hour", '2':"2 Hours", '4':"4 Hours", '8':"8 Hours", '12':"12 Hours", '0':"Disable"} ),
    "noActivity"     : SettingsCommand(   True,  1, "BYTE",    265,   8,   0,    -1,  "No Activity Time",                   { '3':"3 Hours", '6':"6 Hours",'12':"12 Hours", '24':"24 Hours", '48':"48 Hours", '72':"72 Hours", '0':"Disable"} ),
    "cancelTime"     : SettingsCommand(   True,  1, "BYTE",    266,   8,   0,    -1,  "Alarm Cancel Time",                  { '0':"Inactive", '1':"1 Minute", '5':"5 Minutes", '15':"15 Minutes", '60':"60 Minutes", '240':"4 Hours"}),
    "abortTime"      : SettingsCommand(   True,  1, "BYTE",    267,   8,   0,    -1,  "Abort Time",                         { '0':"None", '15':"15 Seconds", '30':"30 Seconds", '45':"45 Seconds", '60':"1 Minute", '120':"2 Minutes", '180':"3 Minutes", '240':"4 Minutes"} ),
    "confirmAlarm"   : SettingsCommand(   True,  1, "BYTE",    268,   8,   0,    -1,  "Confirm Alarm Timer",                { '0':"None", '30':"30 Minutes", '45':"45 Minutes", '60':"60 Minutes", '90':"90 Minutes"} ),
    "screenSaver"    : SettingsCommand(   True,  1, "BYTE",    269,   8,   0,    -1,  "Screen Saver",                       { '2':"Reset By Key", '1':"Reset By Code", '0':"Off"} ),
    "resetOption"    : SettingsCommand(   True,  1, "BYTE",    270,   8,   0,    -1,  "Reset Option",                       { '1':"Engineer Reset", '0':"User Reset"}  ),
    "duress"         : SettingsCommand(   True,  1, "CODE",    273,  16,   0,    -1,  "Duress",                             {  } ),
    "acFailure"      : SettingsCommand(   True,  1, "BYTE",    275,   8,   0,    -1,  "AC Failure Report",                  { '0':"None", '5':"5 Minutes", '30':"30 Minutes", '60':"60 Minutes", '180':"180 Minutes"} ),
    "userPermit"     : SettingsCommand(   True,  1, "BYTE",    276,   8,   0,    -1,  "User Permit",                        { '1':"Enable", '0':"Disable"} ),
    "zoneRestore"    : SettingsCommand(   True,  1, "BYTE",    280,   1,   0,     0,  "Zone Restore",                       { '0':"Report Restore", '1':"Don't Report"} ),
    "tamperOption"   : SettingsCommand(   True,  1, "BYTE",    280,   1,   0,     1,  "Tamper Option",                      { '1':"On", '0':"Off"} ),
    "pgmByLineFail"  : SettingsCommand(   True,  1, "BYTE",    280,   1,   0,     2,  "PGM By Line Fail",                   { '1':"Yes", '0':"No"} ),
    "usrArmOption"   : SettingsCommand(   True,  1, "BYTE",    280,   1,   0,     5,  "Auto Arm Option",                    { '1':"Enable", '0':"Disable"} ),
    "send2wv"        : SettingsCommand(   True,  1, "BYTE",    280,   1,   0,     6,  "Send 2wv Code",                      { '1':"Send", '0':"Don't Send"} ),
    "memoryPrompt"   : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     0,  "Memory Prompt",                      { '1':"Enable", '0':"Disable" } ),
    "usrTimeFormat"  : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     1,  "Time Format",                        { '0':"USA - 12H", '1':"Europe - 24H"}),
    "usrDateFormat"  : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     2,  "Date Format",                        { '0':"USA MM/DD/YYYY", '1':"Europe DD/MM/YYYY"}),
    "lowBattery"     : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     3,  "Low Battery Acknowledge",            { '1':"On", '0':"Off"} ),
    "notReady"       : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     4,  "Not Ready",                          { '0':"Normal", '1':"In Supervision"}  ),
    "x10Flash"       : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     5,  "X10 Flash On Alarm",                 { '0':"No Flash", '1':"All Lights Flash" } ),
    "disarmOption"   : SettingsCommand(   True,  1, "BYTE",    281,   2,   0,     6,  "Disarm Option",                      { '0':"Any Time", '1':"On Entry All", '2':"On Entry Wireless", '3':"Entry + Away KP"} ),
    "sirenOnLine"    : SettingsCommand(   True,  1, "BYTE",    282,   1,   0,     1,  "Siren On Line",                      { '0':"Disable on Fail", '1':"Enable on Fail" }  ),
    "uploadOption"   : SettingsCommand(   True,  1, "BYTE",    282,   1,   0,     2,  "Upload Option",                      { '0':"When System Off", '1':"Any Time"} ),
    "panicAlarm"     : SettingsCommand(   True,  1, "BYTE",    282,   2,   0,     4,  "Panic Alarm",                        { '1':"Silent Panic", '2':"Audible Panic", '0':"Disable Panic"}  ),
    "exitMode"       : SettingsCommand(   True,  1, "BYTE",    282,   2,   0,     6,  "Exit Mode",                          { '1':"Restart Exit", '2':"Off by Door", '0':"Normal"} ),
    "bellReport"     : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     0,  "Bell Report Option",                 { '1':"EN Standard", '0':"Others"}  ),
    "intStrobe"      : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     1,  "Internal/Strobe Siren",              { '0':"Internal Siren", '1':"Strobe"} ),
    "quickArm"       : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     3,  "Quick Arm",                          { '1':"On", '0':"Off"} ),
    "backLight"      : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     5,  "Back Light Time",                    { '1':"Allways On", '0':"Off After 10 Seconds"} ),
    "voice2Private"  : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     6,  "Two-Way Voice - Private",            { '0':"Disable", '1':"Enable"} ),
    "latchKey"       : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     7,  "Latchkey Arming",                    { '1':"On", '0':"Off"} ),
    "bypass"         : SettingsCommand(   True,  1, "BYTE",    284,   2,   0,     6,  "Bypass",                             { '2':"Manual Bypass", '0':"No Bypass", '1':"Force Arm"} ),
    "troubleBeeps"   : SettingsCommand(   True,  1, "BYTE",    284,   2,   0,     1,  "Trouble Beeps",                      { '3':"Enable", '1':"Off at Night", '0':"Disable"} ),
    "crossZoning"    : SettingsCommand(   True,  1, "BYTE",    284,   1,   0,     0,  "Cross Zoning",                       { '1':"On", '0':"Off"} ),
    "recentClose"    : SettingsCommand(   True,  1, "BYTE",    284,   1,   0,     3,  "Recent Close Report",                { '1':"On", '0':"Off"} ),
    "piezoSiren"     : SettingsCommand(   True,  1, "BYTE",    284,   1,   0,     5,  "Piezo Siren",                        { '1':"On", '0':"Off"} ),
    "dialMethod"     : SettingsCommand(   True,  1, "BYTE",    285,   1,   0,     0,  "Dialing Method",                     { '0':"Tone (DTMF)", '1':"Pulse"} ),
    "privateAck"     : SettingsCommand(  Dumpy,  1, "BYTE",    285,   1,   0,     1,  "Private Telephone Acknowledge",      { '0':"Single Acknowledge", '1':"All Acknowledge"} ),
    "remoteAccess"   : SettingsCommand(   True,  1, "BYTE",    285,   1,   0,     2,  "Remote Access",                      { '1':"On", '0':"Off"}),
    "reportConfirm"  : SettingsCommand(   True,  1, "BYTE",    285,   2,   0,     6,  "Report Confirmed Alarm",             { '0':"Disable Report", '1':"Enable Report", '2':"Enable + Bypass"} ),
    "centralStation" : SettingsCommand(   True,  2, "PHONE",   288,  64,  11,    -1,  ["1st Central Tel", "2nd Central Tel"], {} ), # 288, 299
    "accountNo"      : SettingsCommand(   True,  2, "ACCOUNT", 296,  24,  11,    -1,  ["1st Account No","2nd Account No"],  {} ), # 296, 307
    "usePhoneNrs"    : SettingsCommand(  Dumpy,  4, "PHONE",   310,  64,   8,    -1,  ["1st Private Tel","2nd Private Tel","3rd Private Tel","4th Private Tel"],  {} ),  # 310, 318, 326, 334
    "pagerNr"        : SettingsCommand(   True,  1, "PHONE",   342,  64,   0,    -1,  "Pager Tel Number",                   {} ),
    "pagerPIN"       : SettingsCommand(   True,  1, "PHONE",   350,  64,   0,    -1,  "Pager PIN #",                        {} ),
    "ringbackTime"   : SettingsCommand(   True,  1, "BYTE",    358,   8,   0,    -1,  "Ringback Time",                      { '1':"1 Minute", '3':"3 Minutes", '5':"5 Minutes", '10':"10 Minutes"} ),
    "reportCentral"  : SettingsCommand(   True,  1, "BYTE",    359,   8,   0,    -1,  "Report to Central Station",          { '15':"All * Backup", '7':"All but Open/Close * Backup", '255':"All * All", '119':"All but Open/Close * All but Open/Close", '135':"All but Alert * Alert", '45':"Alarms * All but Alarms", '0':"Disable"} ),
    "pagerReport"    : SettingsCommand(   True,  1, "BYTE",    360,   8,   0,    -1,  "Report To Pager",                    { '15':"All", '3':"All + Alerts", '7':"All but Open/Close", '12':"Troubles+Open/Close", '4':"Troubles", '8':"Open/Close", '0':"Disable Report"}  ),
    "privateReport"  : SettingsCommand(   True,  1, "BYTE",    361,   8,   0,    -1,  "Reporting To Private Tel",           { '15':"All", '7':"All but Open/Close", '13':"All but Alerts", '1':"Alarms", '2':"Alerts", '8':"Open/Close", '0':"Disable Report"} ),
    "csDialAttempt"  : SettingsCommand(   True,  1, "BYTE",    362,   8,   0,    -1,  "Central Station Dialing Attempts",   { '2':"2", '4':"4", '8':"8", '12':"12", '16':"16"} ),
    "reportFormat"   : SettingsCommand(   True,  1, "BYTE",    363,   8,   0,    -1,  "Report Format",                      { '0':"Contact ID", '1':"SIA", '2':"4/2 1900/1400", '3':"4/2 1800/2300", '4':"Scancom"}  ),
    "pulseRate"      : SettingsCommand(   True,  1, "BYTE",    364,   8,   0,    -1,  "4/2 Pulse Rate",                     { '0':"10 pps", '1':"20 pps", '2':"33 pps", '3':"40 pps"} ),
    "privateAttempt" : SettingsCommand(  Dumpy,  1, "BYTE",    365,   8,   0,    -1,  "Private Telephone Dialing Attempts", { '1':"1 Attempt", '2':"2 Attempts", '3':"3 Attempts", '4':"4 Attempts"} ),
    "voice2Central"  : SettingsCommand(   True,  1, "BYTE",    366,   8,   0,    -1,  "Two-Way Voice To Central Stations",  { '10':"Time-out 10 Seconds", '45':"Time-out 45 Seconds", '60':"Time-out 60 Seconds", '90':"Time-out 90 Seconds", '120':"Time-out 2 Minutes", '1':"Ring Back", '0':"Disable"} ),
    "autotestTime"   : SettingsCommand(   True,  1, "TIME",    367,  16,   0,    -1,  "Autotest Time",                      {} ),
    "autotestCycle"  : SettingsCommand(   True,  1, "BYTE",    369,   8,   0,    -1,  "Autotest Cycle",                     { '1':"1 Day", '4':"5 Days", '2':"7 Days", '3':"30 Days", '0':"Disable"}  ),
    "areaCode"       : SettingsCommand(  Dumpy,  1, "CODE",    371,  24,   0,    -1,  "Area Code",                          {} ),
    "outAccessNr"    : SettingsCommand(  Dumpy,  1, "CODE",    374,   8,   0,    -1,  "Out Access Number",                  {} ),
    "lineFailure"    : SettingsCommand(   True,  1, "BYTE",    375,   8,   0,    -1,  "Line Failure Report",                { '0':"Don't Report", '1':"Immediately", '5':"5 Minutes", '30':"30 Minutes", '60':"60 Minutes", '180':"180 Minutes"} ),
    "remoteProgNr"   : SettingsCommand(   True,  1, "PHONE",   376,  64,   0,    -1,  "Remote Programmer Tel. No.",         {} ),
    "inactiveReport" : SettingsCommand(   True,  1, "BYTE",    384,   8,   0,    -1,  "System Inactive Report",             { '0':"Disable", '180':"7 Days", '14':"14 Days", '30':"30 Days", '90':"90 Days"} ),
    "ambientLevel"   : SettingsCommand(   True,  1, "BYTE",    388,   8,   0,    -1,  "Ambient Level",                      { '0':"High Level", '1':"Low Level"} ),
    "plFailure"      : SettingsCommand(   True,  1, "BYTE",    391,   8,   0,    -1,  "PowerLink Failure",                  { '1':"Report", '0':"Disable Report"} ),
    "gsmPurpose"     : SettingsCommand(   True,  1, "BYTE",    392,   8,   0,    -1,  "GSM Line Purpose",                   { '1':"GSM is Backup", '2':"GSM is Primary", '3':"GSM Only", '0':"SMS Only" } ),
    "gsmSmsReport"   : SettingsCommand(   True,  1, "BYTE",    393,   8,   0,    -1,  "GSM Report to SMS",                  { '15':"All", '7':"All but Open/Close", '13':"All but Alerts", '1':"Alarms", '2':"Alerts", '8':"Open/Close", '0':"Disable Report"} ),
    "gsmFailure"     : SettingsCommand(   True,  1, "BYTE",    394,   8,   0,    -1,  "GSM Line Failure",                   { '0':"Don't Report", '2':"2 Minutes", '5':"5 Minutes", '15':"15 Minutes", '30':"30 Minutes"} ),
    "gsmInstall"     : SettingsCommand(  Dumpy,  1, "BYTE",    395,   8,   0,    -1,  "GSM Install",                        { '1':"Installed", '0':"Not Installed"} ),
    "gsmSmsNrs"      : SettingsCommand(  Dumpy,  4, "PHONE",   396,  64,   8,    -1,  ["1st SMS Tel","2nd SMS Tel","3rd SMS Tel","4th SMS Tel"], {} ),  #  396,404,412,420
    "gsmAntenna"     : SettingsCommand(   True,  1, "BYTE",    447,   8,   0,    -1,  "GSM Select Antenna",                 { '0':"Internal antenna", '1':"External antenna", '2':"Auto detect"} ),

    "userCodeMax"    : SettingsCommand( XDumpy,  8, "BYTE",    506,  16,   2,    -1,  "PowerMax User Codes",                {} ),
    "userCodeMaster" : SettingsCommand( SDumpy, 48, "BYTE",   2712,  16,   2,    -1,  "PowerMaster User Codes",             {} ),

    "masterCode"     : SettingsCommand( SDumpy,  1, "BYTE",    522,  16,   0,    -1,  "Master Code",                        {} ),
    "installerCode"  : SettingsCommand(  Dumpy,  1, "BYTE",    524,  16,   0,    -1,  "Installer Code",                     {} ),
    "masterDlCode"   : SettingsCommand(  Dumpy,  1, "BYTE",    526,  16,   0,    -1,  "Master Download Code",               {} ),
    "instalDlCode"   : SettingsCommand( SDumpy,  1, "BYTE",    528,  16,   0,    -1,  "Installer Download Code",            {} ),

    "x10Lockout"     : SettingsCommand(  Dumpy,  1, "TIME",    532,  16,   0,    -1,  "X10 Lockout Time (start HH:MM)",     {} ),
    "x10HouseCode"   : SettingsCommand(  Dumpy,  1, "BYTE",    536,   8,   0,    -1,  "X10 House Code",                     { '0':"A", '1':"B", '2':"C", '3':"D", '4':"E", '5':"F", '6':"G", '7':"H", '8':"I", '9':"J", '10':"K", '11':"L", '12':"M", '13':"N", '14':"O", '15':"P"}  ),
    "x10ByArmAway"   : SettingsCommand(  Dumpy, 16, "BYTE",    537,   8,   1,    -1,  "X10 By Arm Away",                    { '255':"Disable", '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    "x10ByArmHome"   : SettingsCommand(  Dumpy, 16, "BYTE",    553,   8,   1,    -1,  "X10 By Arm Home",                    { '255':"Disable", '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    "x10ByDisarm"    : SettingsCommand(  Dumpy, 16, "BYTE",    569,   8,   1,    -1,  "X10 By Disarm",                      { '255':"Disable", '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    "x10ByDelay"     : SettingsCommand(  Dumpy, 16, "BYTE",    585,   8,   1,    -1,  "X10 By Delay",                       { '255':"Disable", '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    "x10ByMemory"    : SettingsCommand(  Dumpy, 16, "BYTE",    601,   8,   1,    -1,  "X10 By Memory",                      { '255':"Disable", '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    "x10ByKeyfob"    : SettingsCommand(  Dumpy, 16, "BYTE",    617,   8,   1,    -1,  "X10 By Keyfob",                      { '255':"Disable", '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    "x10ActZoneA"    : SettingsCommand(  Dumpy, 16, "BYTE",    633,   8,   1,    -1,  "X10 Act Zone A",                     { '255':"Disable", '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    "x10ActZoneB"    : SettingsCommand(  Dumpy, 16, "BYTE",    649,   8,   1,    -1,  "X10 Act Zone B",                     { '255':"Disable", '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    "x10ActZoneC"    : SettingsCommand(  Dumpy, 16, "BYTE",    665,   8,   1,    -1,  "X10 Act Zone C",                     { '255':"Disable", '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    "x10PulseTime"   : SettingsCommand(  Dumpy, 16, "BYTE",    681,   8,   1,    -1,  "X10 Pulse Time",                     { '255':"Disable", '0':"Unknown", '2':"2 Seconds", '30':"30 Seconds", '120':"2 Minutes", '240':"4 Minutes"} ),
    "x10Zone"        : SettingsCommand(  Dumpy, 16, "BYTE",    697,  24,   3,    -1,  "X10 Zone Data",                      {} ),

    "x10Unknown1"    : SettingsCommand(  Dumpy,  1, "BYTE",    745,   8,   0,    -1,  "X10 Unknown 1",                      {} ),
    "x10Unknown2"    : SettingsCommand(  Dumpy,  1, "BYTE",    746,   8,   0,    -1,  "X10 Unknown 2",                      {} ),

    "x10Trouble"     : SettingsCommand(  Dumpy,  1, "BYTE",    747,   8,   0,    -1,  "X10 Trouble Indication",             { '1':"Enable", '0':"Disable"} ),
    "x10Phase"       : SettingsCommand(  Dumpy,  1, "BYTE",    748,   8,   0,    -1,  "X10 Phase and frequency",            { '0':"Disable", '1':"50 Hz", '2':"60 Hz"} ),
    "x10ReportCs1"   : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     0,  "X10 Report on Fail to Central Station 1", { '1':"Enable", '0':"Disable"} ),
    "x10ReportCs2"   : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     1,  "X10 Report on Fail to Central Station 2", { '1':"Enable", '0':"Disable"} ),
    "x10ReportPagr"  : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     2,  "X10 Report on Fail to Pager",        { '1':"Enable", '0':"Disable"} ),
    "x10ReportPriv"  : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     3,  "X10 Report on Fail to Private",      { '1':"Enable", '0':"Disable"} ),
    "x10ReportSMS"   : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     4,  "X10 Report on Fail to SMS",          { '1':"Enable", '0':"Disable"} ),
    "usrVoice"       : SettingsCommand(  Dumpy,  1, "BYTE",    763,   8,   0,    -1,  "Set Voice Option",                   { '0':"Disable Voice", '1':"Enable Voice"} ),
    "usrSquawk"      : SettingsCommand(  Dumpy,  1, "BYTE",    764,   8,   0,    -1,  "Squawk Option",                      { '0':"Disable", '1':"Low Level", '2':"Medium Level", '3':"High Level"}),
    "usrArmTime"     : SettingsCommand(  Dumpy,  1, "TIME",    765,  16,   0,    -1,  "Auto Arm Time",                      {} ),
    "PartitionData"  : SettingsCommand(  Dumpy,255, "BYTE",    768,   8,   1,    -1,  "Partition Data. Not sure what it is",{} ),   # I'm not sure how many bytes this is or what they mean, i get all 255 bytes to the next entry so they can be displayed
    "panelEprom"     : SettingsCommand(   True,  1, "STRING", 1024, 128,   0,    -1,  "Panel Eprom",                        {} ),
    "panelSoftware"  : SettingsCommand(   True,  1, "STRING", 1040, 144,   0,    -1,  "Panel Software",                     {} ),
    "panelSerial"    : SettingsCommand(   True,  1, "CODE",   1072,  48,   0,    -1,  "Panel Serial",                       {} ),   # page 4 offset 48
    "panelTypeCode"  : SettingsCommand(  Dumpy,  1, "BYTE",   1078,   8,   0,    -1,  "Panel Code Type",                    {} ),   # page 4 offset 54 and 55 ->> Panel type code
    "panelSerialCode": SettingsCommand(  Dumpy,  1, "BYTE",   1079,   8,   0,    -1,  "Panel Serial Code",                  {} ),   # page 4 offset 55

    "MaybeEventLog"  : SettingsCommand(  Dumpy,256, "BYTE",   1247,   8,   1,    -1,  "Maybe the event log",                {} ),   # Structure not known   was length 808 but cut to 256 to see what data we get
    "x10ZoneNames"   : SettingsCommand(  Dumpy, 32, "BYTE",   2862,   8,   1,    -1,  "X10 Location Name references",       {} ),     # originally 16 and 2864 
    "MaybeScreenSaver":SettingsCommand(  Dumpy, 75, "BYTE",   5888,   8,   1,    -1,  "Maybe the screen saver",             {} ),   # Structure not known 

#PowerMax Only
    "ZoneDataPMax"   : SettingsCommand( XDumpy, 30, "BYTE",   2304,  32,   4,    -1,  "Zone Data, PowerMax",                {} ),   # 4 bytes each, 30 zones --> 120 bytes
    "KeyFobsPMax"    : SettingsCommand( XDumpy, 16, "BYTE",   2424,  32,   4,    -1,  "Maybe KeyFob Data PowerMax",         {} ),   # Structure not known

    "ZoneSignalPMax" : SettingsCommand( XDumpy, 28, "BYTE",   2522,   8,   1,    -1,  "Zone Signal Strength, PowerMax",     {} ),   # 28 wireless zones
    "Keypad2PMax"    : SettingsCommand( XDumpy,  2, "BYTE",   2560,  32,   4,    -1,  "Keypad2 Data, PowerMax",             {} ),   # 4 bytes each, 2 keypads 
    "Keypad1PMax"    : SettingsCommand( XDumpy,  8, "BYTE",   2592,  32,   4,    -1,  "Keypad1 Data, PowerMax",             {} ),   # 4 bytes each, 8 keypads        THIS TOTALS 32 BYTES BUT IN OTHER SYSTEMS IVE SEEN 64 BYTES
    "SirensPMax"     : SettingsCommand( XDumpy,  2, "BYTE",   2656,  32,   4,    -1,  "Siren Data, PowerMax",               {} ),   # 4 bytes each, 2 sirens 

    "ZoneNamePMax"   : SettingsCommand( XDumpy, 30, "BYTE",   2880,   8,   1,    -1,  "Zone Names, PowerMax",               {} ),

    "Test2"          : SettingsCommand(  Dumpy,128, "BYTE",   2816,   8,   1,    -1,  "Test 2 String, PowerMax",            {} ),   # 0xB00
    "Test1"          : SettingsCommand(  Dumpy,128, "BYTE",   2944,   8,   1,    -1,  "Test 1 String, PowerMax",            {} ),   # 0xB80

    "ZoneStrPMax"    : SettingsCommand(  Dumpy, 32,"STRING",  6400, 128,  16,    -1,  "Zone String, PowerMax",              {} ),   # Not Sure what this is   originally 512 bytes, 32 strings of 16 characters each
    #"ZoneCustomPMax" : SettingsCommand(  Dumpy, 80, "BYTE",   6816,   8,   1,    -1,  "Zone Custom, PowerMax",              {} ),   # Not Sure what this is   originally 80 bytes -->  THIS OVERLAPS WITH PREVIOUS ANYWAY

#PowerMaster only
    "ZoneDataPMaster": SettingsCommand( SDumpy, 64, "BYTE",   2304,   8,   1,    -1,  "Zone Data, PowerMaster",             {} ),   # 1 bytes each, 64 zones --> 64 bytes
    "ZoneNamePMaster": SettingsCommand( SDumpy, 64, "BYTE",   2400,   8,   1,    -1,  "Zone Names, PowerMaster",            {} ),   # This will be downloaded by a PowerMax but will be meaningless

    "SirensPMaster"  : SettingsCommand( SDumpy,  8, "BYTE",  46818,  80,  10,    -1,  "Siren Data, PowerMaster",            {} ),   # 10 bytes each, 8 sirens
    "KeypadPMaster"  : SettingsCommand( SDumpy, 32, "BYTE",  46898,  80,  10,    -1,  "Keypad Data, PowerMaster",           {} ),   # 10 bytes each, 32 keypads 
    "ZoneExtPMaster" : SettingsCommand( SDumpy, 64, "BYTE",  47218,  80,  10,    -1,  "Zone Extended Data, PowerMaster",    {} ),   # 10 bytes each, 64 zones 

    "AlarmLED"       : SettingsCommand( SDumpy, 64, "BYTE",  49250,   8,   1,    -1,  "Alarm LED, PowerMaster",             {} ),   # This is the Alarm LED On/OFF settings for Motion Sensors -> Dev Settings --> Alarm LED
    "ZoneDelay"      : SettingsCommand( SDumpy, 64, "BYTE",  49542,  16,   2,    -1,  "Zone Delay, PowerMaster",            {} )    # This is the Zone Delay settings for Motion Sensors -> Dev Settings --> Disarm Activity  
}

# These blocks are not value specific, they are used to download blocks of EPROM data that we need without reference to what the data means
#    They are used when EEPROM_DOWNLOAD_ALL is False
#    Each block is 128 bytes long. Each EPROM page is 256 bytes so 2 downloads are needed per EPROM page
#    We have to do it like this as the max message size is 176 bytes. I decided this was messy so I download 128 bytes at a time instead
pmBlockDownload = {
    "PowerMax" : (
            convertByteArray('00 00 80 00'),
            convertByteArray('00 00 80 00'),
            convertByteArray('80 00 80 00'),
            convertByteArray('00 01 80 00'),
            convertByteArray('80 01 80 00'),
            convertByteArray('00 02 80 00'),
            convertByteArray('80 02 80 00'),
            convertByteArray('00 03 80 00'),
            convertByteArray('80 03 80 00'),
            convertByteArray('00 04 80 00'),
            convertByteArray('80 04 80 00'),
            convertByteArray('00 05 80 00'),   # added
            convertByteArray('80 05 80 00'),   # added
            convertByteArray('00 09 80 00'),
            convertByteArray('80 09 80 00'),
            convertByteArray('00 0A 80 00'),
            convertByteArray('80 0A 80 00'),
            convertByteArray('00 0B 80 00'),   # Test2
            convertByteArray('80 0B 80 00'),   # Test1
            convertByteArray('00 17 80 00'),   # added to test MaybeScreenSaver  0x1700 = 5888
            convertByteArray('00 19 80 00'),   # ZoneStrPMax
            convertByteArray('80 19 80 00'),   # ZoneStrPMax
            convertByteArray('00 1A 80 00'),   # ZoneStrPMax
            convertByteArray('80 1A 80 00')    # ZoneStrPMax
    ),
    "PowerMaster" : (
            convertByteArray('00 B6 80 00'),
            convertByteArray('80 B6 80 00'),
            convertByteArray('00 B7 80 00'),
            convertByteArray('80 B7 80 00'),
            convertByteArray('00 B8 80 00'),
            convertByteArray('80 B8 80 00'),
            convertByteArray('00 B9 80 00'),
            convertByteArray('80 B9 80 00'),
            convertByteArray('00 BA 80 00'),
            convertByteArray('80 BA 80 00'),
            convertByteArray('00 C0 80 00'),
            convertByteArray('80 C0 80 00'),
            convertByteArray('00 C1 80 00'),
            convertByteArray('80 C1 80 00'),
            convertByteArray('00 C2 80 00')
    )
}

pmPanelName_t = {
   "0000" : "PowerMax", "0001" : "PowerMax LT", "0004" : "PowerMax A", "0005" : "PowerMax", "0006" : "PowerMax LT",
   "0009" : "PowerMax B", "000a" : "PowerMax A", "000b" : "PowerMax", "000c" : "PowerMax LT", "000f" : "PowerMax B",
   "0014" : "PowerMax A", "0015" : "PowerMax", "0016" : "PowerMax", "0017" : "PowerArt", "0018" : "PowerMax SC",
   "0019" : "PowerMax SK", "001a" : "PowerMax SV", "001b" : "PowerMax T", "001e" : "PowerMax WSS", "001f" : "PowerMax Smith",
   "0100" : "PowerMax+", "0103" : "PowerMax+ UK (3)", "0104" : "PowerMax+ JP", "0106" : "PowerMax+ CTA", "0108" : "PowerMax+",
   "010a" : "PowerMax+ SH", "010b" : "PowerMax+ CF", "0112" : "PowerMax+ WSS", "0113" : "PowerMax+ 2INST",
   "0114" : "PowerMax+ HL", "0115" : "PowerMax+ UK", "0116" : "PowerMax+ 2INST3", "0118" : "PowerMax+ CF",
   "0119" : "PowerMax+ 2INST", "011a" : "PowerMax+", "011c" : "PowerMax+ WSS", "011d" : "PowerMax+ UK",
   "0120" : "PowerMax+ 2INST33", "0121" : "PowerMax+", "0122" : "PowerMax+ CF", "0124" : "PowerMax+ UK",
   "0127" : "PowerMax+ 2INST_MONITOR", "0128" : "PowerMax+ KeyOnOff", "0129" : "PowerMax+ 2INST_MONITOR",
   "012a" : "PowerMax+ 2INST_MONITOR42", "012b" : "PowerMax+ 2INST33", "012c" : "PowerMax+ One Inst_1_44_0",
   "012d" : "PowerMax+ CF_1_45_0", "012e" : "PowerMax+ SA_1_46", "012f" : "PowerMax+ UK_1_47", "0130" : "PowerMax+ SA UK_1_48",
   "0132" : "PowerMax+ KeyOnOff 1_50", "0201" : "PowerMax Pro", "0202" : "PowerMax Pro-Nuon ",
   "0204" : "PowerMax Pro-PortugalTelecom", "020a" : "PowerMax Pro-PortugalTelecom2", "020c" : "PowerMax HW-V9 Pro",
   "020d" : "PowerMax ProSms", "0214" : "PowerMax Pro-PortugalTelecom_4_5_02", "0216" : "PowerMax HW-V9_4_5_02 Pro",
   "0217" : "PowerMax ProSms_4_5_02", "0218" : "PowerMax UK_DD243_4_5_02 Pro M", "021b" : "PowerMax Pro-Part2__2_27",
   "0223" : "PowerMax Pro Bell-Canada", "0301" : "PowerMax Complete", "0302" : "PowerMax Complete_NV",
   "0303" : "PowerMax Complete-PortugalTelecom", "0307" : "PowerMax Complete_1_0_07", "0308" : "PowerMax Complete_NV_1_0_07",
   "030a" : "PowerMax Complete_UK_DD243_1_1_03", "030b" : "PowerMax Complete_COUNTERFORCE_1_0_06", "0401" : "PowerMax Pro-Part",
   "0402" : "PowerMax Pro-Part CellAdaptor", "0405" : "PowerMax Pro-Part_5_0_08", "0406" : "PowerMax Pro-Part CellAdaptor_5_2_04",
   "0407" : "PowerMax Pro-Part KeyOnOff_5_0_08", "0408" : "PowerMax UK Pro-Part_5_0_08",
   "0409" : "PowerMax SectorUK Pro-Part_5_0_08", "040a" : "PowerMax Pro-Part CP1 4_10", "040c" : "PowerMax Pro-Part_Cell_key_4_12",
   "040d" : "PowerMax Pro-Part UK 4_13", "040e" : "PowerMax SectorUK Pro-Part_4_14", "040f" : "PowerMax Pro-Part UK 4_15",
   "0410" : "PowerMax Pro-Part CP1 4_16", "0411" : "PowerMax NUON key 4_17", "0433" : "PowerMax Pro-Part2__4_51",
   "0434" : "PowerMax UK Pro-Part2__4_52", "0436" : "PowerMax Pro-Part2__4_54", "0437" : "PowerMax Pro-Part2__4_55 (CP_01)",
   "0438" : "PowerMax Pro-Part2__4_56", "0439" : "PowerMax Pro-Part2__4_57 (NUON)", "043a" : "PowerMax Pro 4_58",
   "043c" : "PowerMax Pro 4_60", "043e" : "PowerMax Pro-Part2__4_62", "0440" : "PowerMax Pro-Part2__4_64",
   "0442" : "PowerMax 4_66", "0443" : "PowerMax Pro 4_67", "0444" : "PowerMax Pro 4_68", "0445" : "PowerMax Pro 4_69",
   "0446" : "PowerMax Pro-Part2__4_70", "0447" : "PowerMax 4_71", "0449" : "PowerMax 4_73", "044b" : "PowerMax Pro-Part2__4_75",
   "0451" : "PowerMax Pro 4_81", "0452" : "PowerMax Pro 4_82", "0454" : "PowerMax 4_84", "0455" : "PowerMax 4_85",
   "0456" : "PowerMax 4_86", "0503" : "PowerMax UK Complete partition 1_5_00", "050a" : "PowerMax Complete partition GPRS",
   "050b" : "PowerMax Complete partition NV GPRS", "050c" : "PowerMax Complete partition GPRS NO-BBA",
   "050d" : "PowerMax Complete partition NV GPRS NO-BBA", "050e" : "PowerMax Complete part. GPRS NO-BBA UK_5_14",
   "0511" : "PowerMax Pro-Part CP1 GPRS 5_17", "0512" : "PowerMax Complete part. BBA UK_5_18",
   "0533" : "PowerMax Complete part2  5_51", "0534" : "PowerMax Complete part2 5_52 (UK)",
   "0536" : "PowerMax Complete 5_54 (GR)", "0537" : "PowerMax Complete  5_55", "053a" : "PowerMax Complete 5_58 (PT)",
   "053b" : "PowerMax Complete part2 5_59 (NV)", "053c" : "PowerMax Complete  5_60", "053e" : "PowerMax Complete 5_62",
   "053f" : "PowerMax Complete part2  5_63", "0540" : "PowerMax Complete  5_64", "0541" : "PowerMax Complete  5_65",
   "0543" : "PowerMax Complete  5_67", "0544" : "PowerMax Complete  5_68", "0545" : "PowerMax Complete  5_69",
   "0546" : "PowerMax Complete  5_70", "0547" : "PowerMax Complete  5_71", "0549" : "PowerMax Complete  5_73",
   "054b" : "PowerMax Complete  5_75", "054f" : "PowerMax Complete  5_79", "0601" : "PowerMax Express",
   "0603" : "PowerMax Express CP 01", "0605" : "PowerMax Express OEM 6_5", "0607" : "PowerMax Express BBA 6_7",
   "0608" : "PowerMax Express CP 01 BBA 6_8", "0609" : "PowerMax Express OEM1 BBA 6_9", "060b" : "PowerMax Express BBA 6_11",
   "0633" : "PowerMax Express 6_51", "063b" : "PowerMax Express 6_59", "063d" : "PowerMax Express 6_61",
   "063e" : "PowerMax Express 6_62 (UK)", "0645" : "PowerMax Express 6_69", "0647" : "PowerMax Express 6_71",
   "0648" : "PowerMax Express 6_72", "0649" : "PowerMax Express 6_73", "064a" : "PowerMax Activa 6_74",
   "064c" : "PowerMax Express 6_76", "064d" : "PowerMax Express 6_77", "064e" : "PowerMax Express 6_78",
   "064f" : "PowerMax Secure 6_79", "0650" : "PowerMax Express 6_80", # "0650" : "PowerMax Express part2 M 6_80",
   "0651" : "PowerMax Express 6_81", "0652" : "PowerMax Express 6_82", "0653" : "PowerMax Express 6_83",
   "0654" : "PowerMax 6_84", "0655" : "PowerMax 6_85", "0658" : "PowerMax 6_88", "0659" : "PowerMax 6_89",
   "065a" : "PowerMax 6_90", "065b" : "PowerMax 6_91", "0701" : "PowerMax PowerCode-G 7_1", "0702" : "PowerMax PowerCode-G 7_2",
   "0704" : "PowerMaster10 7_4", "0705" : "PowerMaster10 7_05", "0707" : "PowerMaster10 7_07", "070c" : "PowerMaster10 7_12",
   "070f" : "PowerMaster10 7_15", "0710" : "PowerMaster10 7_16", "0711" : "PowerMaster10 7_17", "0712" : "PowerMaster10 7_18",
   "0713" : "PowerMaster10 7_19", "0802" : "PowerMax Complete PowerCode-G 8_2", "0803" : "PowerMaster30 8_3",
   "080f" : "PowerMaster30 8_15", "0810" : "PowerMaster30 8_16", "0812" : "PowerMaster30 8_18", "0813" : "PowerMaster30 8_19",
   "0815" : "PowerMaster30 8_21",
   "0A47" : "PowerMaster33 10_71",
   "0F92" : "PowerMaster33 15_146"
}

pmZoneType_t = {
   "EN" : (
           "Non-Alarm", "Emergency", "Flood", "Gas", "Delay 1", "Delay 2", "Interior-Follow", "Perimeter", "Perimeter-Follow",
           "24 Hours Silent", "24 Hours Audible", "Fire", "Interior", "Home Delay", "Temperature", "Outdoor", "16" ),
   "NL" : (
           "Geen alarm", "Noodtoestand", "Water", "Gas", "Vertraagd 1", "Vertraagd 2", "Interieur volg", "Omtrek", "Omtrek volg",
           "24 uurs stil", "24 uurs luid", "Brand", "Interieur", "Thuis vertraagd", "Temperatuur", "Buiten", "16" ),
   "FR" : (
           "Non Alarme", "Urgence", "Inondation", "Gaz", "Temporisée 1", "Temporisée 2", "Interior-Follow", "Voie Périphérique", "Voie Périphérique d'Entrée",
           "24 Heures Silencieuse", "24 Heures Audible", "Incendie", "Intérieure", "Home Delay", "Température", "Extérieure", "16" )
} # "Arming Key", "Guard" ??

# Zone names are taken from the panel, so no langauage support needed
pmZoneName_t = [
   "Attic", "Back door", "Basement", "Bathroom", "Bedroom", "Child room", "Conservatory", "Play room", "Dining room", "Downstairs",
   "Emergency", "Fire", "Front door", "Garage", "Garage door", "Guest room", "Hall", "Kitchen", "Laundry room", "Living room",
   "Master bathroom", "Master bedroom", "Office", "Upstairs", "Utility room", "Yard", "Custom 1", "Custom 2", "Custom 3",
   "Custom 4", "Custom 5", "Not Installed"
]

pmZoneChime_t = {
   "EN" : ("Off", "Melody", "Zone", "Invalid"),
   "NL" : ("Uit", "Muziek", "Zone", "Invalid"),
   "FR" : ("Eteint", "Melodie", "Zone", "Invalide")
}

# Note: names need to match to VAR_xxx
pmZoneSensorMaxGeneric_t = {
   0x0 : AlSensorType.VIBRATION, 0x2 : AlSensorType.SHOCK, 0x3 : AlSensorType.MOTION, 0x4 : AlSensorType.MOTION, 0x5 : AlSensorType.MAGNET,
   0x6 : AlSensorType.MAGNET, 0x7 : AlSensorType.MAGNET, 0xA : AlSensorType.SMOKE, 0xB : AlSensorType.GAS, 0xC : AlSensorType.MOTION,
   0xF : AlSensorType.WIRED
} # unknown to date: Push Button, Flood, Universal

ZoneSensorType = collections.namedtuple("ZoneSensorType", 'name func' )
pmZoneSensorMax_t = {
   0x6D : ZoneSensorType("MCX-601 Wireless Repeater", AlSensorType.IGNORED ),       # Joao-Sousa   ********************* Wireless Repeater so exclude it **************
   0x08 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # Fabio72
   0x09 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # Fabio72
   0x1A : ZoneSensorType("MCW-K980", AlSensorType.MOTION ),        # Botap
   0x6A : ZoneSensorType("MCT-550", AlSensorType.FLOOD ),          # Joao-Sousa
   0x74 : ZoneSensorType("Next+ K9-85", AlSensorType.MOTION ),     # christopheVia
   0x75 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # 15/9/23 rogerthn2019 (Powermax Pro) and others have this sensor so removed the previous setting
#   0x75 : ZoneSensorType("Next K9-85", AlSensorType.MOTION ),      # thermostat (Visonic part number 0-3592-B, NEXT K985 DDMCW)
   0x76 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # open1999
   0x7A : ZoneSensorType("MCT-550", AlSensorType.FLOOD ),          # fguerzoni, Joao-Sousa
   0x86 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # Joao-Sousa
   0x8A : ZoneSensorType("MCT-550", AlSensorType.FLOOD ),          # Joao-Sousa
   0x95 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # me, fguerzoni
   0x96 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # me, g4seb, rogerthn2019
   0x97 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # christopheVia
   0x9A : ZoneSensorType("MCT-425", AlSensorType.SMOKE ),          # Joao-Sousa, rogerthn2019
   0xA3 : ZoneSensorType("Disc MCW", AlSensorType.MOTION ),        # Joao-Sousa
   0xB3 : ZoneSensorType("Clip MCW", AlSensorType.MOTION ),        # Joao-Sousa
   0xC0 : ZoneSensorType("Next K9-85", AlSensorType.MOTION ),      # g4seb
   0xC3 : ZoneSensorType("Clip MCW", AlSensorType.MOTION ),        # Joao-Sousa
   0xC4 : ZoneSensorType("Clip MCW", AlSensorType.MOTION ),        # Joao-Sousa
   0xD3 : ZoneSensorType("Next MCW", AlSensorType.MOTION ),        # me, Joao-Sousa
   0xD4 : ZoneSensorType("Next K9-85", AlSensorType.MOTION ),      # rogerthn2019
   0xD5 : ZoneSensorType("Next K9", AlSensorType.MOTION ),         # fguerzoni
   0xE4 : ZoneSensorType("Next MCW", AlSensorType.MOTION ),        # me
   0xE5 : ZoneSensorType("Next K9-85", AlSensorType.MOTION ),      # g4seb, fguerzoni
   0xF3 : ZoneSensorType("MCW-K980", AlSensorType.MOTION ),        # Botap, Joao-Sousa
   0xF5 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # open1999
   0xF9 : ZoneSensorType("MCT-100", AlSensorType.MAGNET ),         # Fabio72
   0xFA : ZoneSensorType("MCT-427", AlSensorType.SMOKE ),          # Joao-Sousa
   0xFF : ZoneSensorType("Wired", AlSensorType.WIRED )
}

# SMD-426 PG2 (photoelectric smoke detector)
# SMD-427 PG2 (heat and photoelectric smoke detector)
# SMD-429 PG2 (Smoke and Heat Detector)
pmZoneSensorMaster_t = {
   0x01 : ZoneSensorType("Next PG2", AlSensorType.MOTION ),
   0x03 : ZoneSensorType("Clip PG2", AlSensorType.MOTION ),
   0x04 : ZoneSensorType("Next CAM PG2", AlSensorType.CAMERA ),
   0x05 : ZoneSensorType("GB-502 PG2", AlSensorType.SOUND ),
   0x06 : ZoneSensorType("TOWER-32AM PG2", AlSensorType.MOTION ),
   0x07 : ZoneSensorType("TOWER-32AMK9", AlSensorType.MOTION ),
   0x0A : ZoneSensorType("TOWER CAM PG2", AlSensorType.CAMERA ),
   0x0C : ZoneSensorType("MP-802 PG2", AlSensorType.MOTION ),
   0x0F : ZoneSensorType("MP-902 PG2", AlSensorType.MOTION ),
   0x15 : ZoneSensorType("SMD-426 PG2", AlSensorType.SMOKE ),
   0x16 : ZoneSensorType("SMD-429 PG2", AlSensorType.SMOKE ),
   0x18 : ZoneSensorType("GSD-442 PG2", AlSensorType.SMOKE ),
   0x19 : ZoneSensorType("FLD-550 PG2", AlSensorType.FLOOD ),
   0x1A : ZoneSensorType("TMD-560 PG2", AlSensorType.TEMPERATURE ),
   0x1E : ZoneSensorType("SMD-429 PG2", AlSensorType.SMOKE ),
   0x29 : ZoneSensorType("MC-302V PG2", AlSensorType.MAGNET),
   0x2A : ZoneSensorType("MC-302 PG2", AlSensorType.MAGNET),
   0x2C : ZoneSensorType("MC-303V PG2", AlSensorType.MAGNET),
   0x2D : ZoneSensorType("MC-302V PG2", AlSensorType.MAGNET),
   0x35 : ZoneSensorType("SD-304 PG2", AlSensorType.SHOCK),
   0xFE : ZoneSensorType("Wired", AlSensorType.WIRED )
}

# Entry in a queue of commands (and PDUs) to send to the panel
class VisonicListEntry:
    def __init__(self, command = None, options = None, raw = None):
        self.command = command # kwargs.get("command", None)
        self.options = options # kwargs.get("options", None)
        self.raw = raw
        self.response = []
        if command is not None:
            if self.command.replytype is not None:
                self.response = self.command.replytype.copy()  # list of message reply needed
            # are we waiting for an acknowledge from the panel (do not send a message until we get it)
            if self.command.waitforack:
                self.response.append(ACK_MESSAGE)  # add an acknowledge to the list
            self.triedResendingMessage = False

    def __str__(self):
        if self.command is not None:
            return ("Command:{0}    Options:{1}".format(self.command.msg, self.options))
        elif self.raw is not None:
            return ("Raw: {0}".format(self.raw))
        return ("Command:None")

class SensorDevice(AlSensorDeviceHelper):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

class X10Device(AlSwitchDeviceHelper):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def hexify(v : int) -> str:
    return f"{hex(v)[2:]}"

# This class handles the detailed low level interface to the panel.
#    It sends the messages
#    It builds and received messages from the raw byte stream and coordinates the acknowledges back to the panel
#    It checks CRC for received messages and creates CRC for sent messages
#    It coordinates the downloading of the EEPROM (but doesn't decode the data here)
#    It manages the communication connection
class ProtocolBase(AlPanelInterfaceHelper, AlPanelDataStream, MyChecksumCalc):
    """Manage low level Visonic protocol."""

    log.debug("Initialising Protocol - Protocol Version {0}".format(PLUGIN_VERSION))

    def __init__(self, loop=None, panelConfig : PanelConfig = None, pl_sock : socket = None, packet_callback: Callable = None) -> None:
        super().__init__()
        """Initialize class."""
        if loop:
            self.loop = loop
            log.debug("Establishing Protocol - Using Home Assistant Loop")
        else:
            self.loop = asyncio.get_event_loop()
            log.debug("Establishing Protocol - Using Asyncio Event Loop")

        # install the packet callback handler
        self.packet_callback = packet_callback

        self.transport = None  # type: asyncio.Transport

        ########################################################################
        # Global Variables that define the overall panel status
        ########################################################################
        self.PanelMode = AlPanelMode.STARTING
        self.PanelProblemCount = 0
        self.LastPanelProblemTime = None
        self.WatchdogTimeout = 0
        self.WatchdogTimeoutPastDay = 0
        self.DownloadTimeout = 0

        # A5 related data
        self.PanelStatusText = "Unknown"
        self.PanelState = AlPanelStatus.UNKNOWN
        self.PanelStatus = {}

        self.PowerMaster = None              # Set to None to represent unknown until we know True or False
        self.ModelType = None
        self.PanelStatus = {}

        # Loopback capability added. Connect Rx and Tx together without connecting to the panel
        self.loopbackTest = False
        self.loopbackCounter = 0

        # determine when MSG_ENROLL is sent to the panel
        self.doneAutoEnroll = False

        # Configured from the client INTERFACE
        #   These are the default values
        self.ForceStandardMode = False        # INTERFACE : Get user variable from HA to force standard mode or try for PowerLink
        self.DisableAllCommands = False       # INTERFACE : Get user variable from HA to allow or disable all commands to the panel 
        self.CompleteReadOnly = False         # INTERFACE : Get user variable from HA to represent complete readonly
        self.AutoEnroll = True                # INTERFACE : Auto Enroll when don't know panel type. Set to true as default as most panels can do this
        self.AutoSyncTime = True              # INTERFACE : sync time with the panel
        self.DownloadCode = '56 50'           # INTERFACE : Set the Download Code
        self.pmLang = 'EN'                    # INTERFACE : Get the plugin language from HA, either "EN", "FR" or "NL"
        self.MotionOffDelay = 120             # INTERFACE : Get the motion sensor off delay time (between subsequent triggers)
        self.SirenTriggerList = ["intruder"]  # INTERFACE : This is the trigger list that we can assume is making the siren sound
        self.IncludeEEPROMAttributes = False  # INTERFACE : Whether to include the EEPROM attributes in the alarm panel HA attributes list

        # Now that the defaults have been set, update them from the panel config dictionary (that may not have all settings in)
        self.updateSettings(panelConfig)

        ########################################################################
        # Variables that are only used in handle_received_message function
        ########################################################################
        self.pmIncomingPduLen = 0             # The length of the incoming message
        self.pmCrcErrorCount = 0              # The CRC Error Count for Received Messages
        self.pmCurrentPDU = pmReceiveMsg_t[0] # The current receiving message type
        self.pmFlexibleLength = 0             # How many bytes less then the proper message size do we start checking for PACKET_FOOTER and a valid CRC

        ########################################################################
        # Variables that are only used in this class and not subclasses
        ########################################################################

        # a list of message types we are expecting from the panel
        self.pmExpectedResponse = []

        # The last sent message
        self.pmLastSentMessage = None

        # Timestamp of the last received data from the panel. If this remains set to none then we have a comms problem
        self.lastRecvOfPanelData = None

        # keep alive counter for the timer
        self.keep_alive_counter = 0  # only used in _keep_alive_and_watchdog_timer

        # this is the watchdog counter (in seconds)
        self.watchdog_counter = 0

        # sendlock is used in _sendPdu to provide a mutex on sending to the panel, this ensures that a message is sent before another message
        self.sendlock = None

        # commandlock is used in _sendCommand to provide a mutex on sending to the panel, this ensures that a message is sent before another message
        self.commandlock = None

        # The receive byte array for receiving a message
        self.ReceiveData = bytearray(b"")

        # A queue of messages to send (i.e. VisonicListEntry)
        self.SendList = []

        self.myDownloadList = []

        # This is the time stamp of the last Send
        self.pmLastTransactionTime = self._getUTCTimeFunction() - timedelta(
            seconds=1
        )  # take off 1 second so the first command goes through immediately

        self.pmFirstCRCErrorTime = self._getUTCTimeFunction() - timedelta(
            seconds=1
        )  # take off 1 second so the first command goes through immediately

        # When to stop trying to download the EEPROM
        self.GiveupTryingDownload = False

        self.expectedResponseTimeout = 0

        self.lastPacket = None

        self.firstCmdSent = False
        ###################################################################
        # Variables that are used and modified throughout derived classes
        ###################################################################

        ############## Variables that are set and read in this class and in derived classes ############

        # whether we are in powerlink state
        #    Set True when
        #         pmPowerlinkModePending must be True (see its dependencies)
        #         We receive a PowerLink Keep-Alive message from the panel
        self.pmPowerlinkMode = False

        # we have finished downloading the EEPROM and are trying to get in to powerlink state
        #    Set to True when
        #         we complete eprom download and
        #         receive a STOP from the panel (self.pmDownloadComplete is also set to True) and
        #             the last command from us was MSG_START
        #    Set to False when we achieve powerlink i.e. self.pmPowerlinkMode is set to True
        self.pmPowerlinkModePending = False

        # When we are downloading the EEPROM settings and finished parsing them and setting up the system.
        #   There should be no user (from Home Assistant for example) interaction when self.pmDownloadMode is True
        self.pmDownloadMode = False
        self.triggeredDownload = False

        # Set when the panel details have been received i.e. a 3C message
        self.pmGotPanelDetails = False

        # Can the panel support the INIT Command
        self.pmInitSupportedByPanel = False

        # Set when we receive a STOP from the panel, indicating that the EEPROM data has finished downloading
        self.pmDownloadComplete = False

        # Download block retry count
        self.pmDownloadRetryCount = 0

        self.ZoneNames = { }
        self.ZoneTypes = { }
        self.BeeZeroSensorList = False

        # Set when the EEPROM has been downloaded and we have extracted a user pin code
        self.pmGotUserCode = False

        # When trying to connect in powerlink from the timer loop, this allows the receipt of a powerlink ack to trigger a MSG_RESTORE
        self.allowAckToTriggerRestore = False

        #self.pmPhoneNr_t = {}
        self.pmEventLogDictionary = {}

        # We do not put these pin codes in to the panel status
        self.pmPincode_t = []  # allow maximum of 48 user pin codes

        # Save the EEPROM data when downloaded
        self.pmRawSettings = {}

        # Save the sirens
        #self.pmSirenDev_t = {}
        
        # Current F4 jpg image 
        self.ImageManager = AlImageManager()

        ##############################################################################################################################################################
        ##############################################################################################################################################################
        ##############################################################################################################################################################
        ##############################################################################################################################################################
        ##############################################################################################################################################################
        self.saved_A = None
        self.saved_B = None
        self.saved_C = None
        self.saved_D = None
        self.saved_E = None
        self.saved_F = None
        self.saved_G = None
        self.saved_H = None
        self.saved_I = None
        self.saved_J = None
        self.saved_K = None
        self.saved_L = None
        self.saved_M = None
        self.saved_N = None
        ##############################################################################################################################################################
        ##############################################################################################################################################################
        ##############################################################################################################################################################
        ##############################################################################################################################################################
        ##############################################################################################################################################################

    def updateSettings(self, newdata: PanelConfig):
        if newdata is not None:
            # log.debug("[updateSettings] Settings refreshed - Using panel config {0}".format(newdata))
            if AlConfiguration.ForceStandard in newdata:
                # Get user variable from HA to force standard mode or try for PowerLink
                self.ForceStandardMode = newdata[AlConfiguration.ForceStandard]
                log.debug("[Settings] Force Standard set to {0}".format(self.ForceStandardMode))
            if AlConfiguration.DisableAllCommands in newdata:
                # Get user variable from HA to Disable All Commands
                self.DisableAllCommands = newdata[AlConfiguration.DisableAllCommands]
                log.debug("[Settings] Disable All Commands set to {0}".format(self.DisableAllCommands))
            if AlConfiguration.CompleteReadOnly in newdata:
                # Get user variable from HA to make the integration fully readonly (no data is sent to the panel)
                self.CompleteReadOnly = newdata[AlConfiguration.CompleteReadOnly]
                log.debug("[Settings] Complete ReadOnly set to {0}".format(self.CompleteReadOnly))
            if AlConfiguration.AutoEnroll in newdata:
                # Force Auto Enroll when don't know panel type. Only set to true
                self.AutoEnroll = newdata[AlConfiguration.AutoEnroll]
                log.debug("[Settings] Force Auto Enroll set to {0}".format(self.AutoEnroll))
            if AlConfiguration.AutoSyncTime in newdata:
                self.AutoSyncTime = newdata[AlConfiguration.AutoSyncTime]  # INTERFACE : sync time with the panel
                log.debug("[Settings] Force Auto Sync Time set to {0}".format(self.AutoSyncTime))
            if AlConfiguration.DownloadCode in newdata:
                tmpDLCode = newdata[AlConfiguration.DownloadCode]  # INTERFACE : Get the download code
                if len(tmpDLCode) == 4 and type(tmpDLCode) is str:
                    self.DownloadCode = tmpDLCode[0:2] + " " + tmpDLCode[2:4]
                    log.debug("[Settings] Download Code set to {0}".format(self.DownloadCode))
            if AlConfiguration.PluginLanguage in newdata:
                # Get the plugin language from HA, either "EN", "FR" or "NL"
                self.pmLang = newdata[AlConfiguration.PluginLanguage]
                log.debug("[Settings] Language set to {0}".format(self.pmLang))
            if AlConfiguration.MotionOffDelay in newdata:
                # Get the motion sensor off delay time (between subsequent triggers)
                self.MotionOffDelay = newdata[AlConfiguration.MotionOffDelay]
                log.debug("[Settings] Motion Off Delay set to {0}".format(self.MotionOffDelay))
            if AlConfiguration.SirenTriggerList in newdata:
                tmpList = newdata[AlConfiguration.SirenTriggerList]
                self.SirenTriggerList = [x.lower() for x in tmpList]
                log.debug("[Settings] Siren Trigger List set to {0}".format(self.SirenTriggerList))
            if AlConfiguration.EEPROMAttributes in newdata:
                self.IncludeEEPROMAttributes = newdata[AlConfiguration.EEPROMAttributes]
                log.debug("[Settings] Include EEPROM Attributes set to {0}".format(self.IncludeEEPROMAttributes))

        if self.CompleteReadOnly:
            self.DisableAllCommands = True
        if self.DisableAllCommands:
            self.ForceStandardMode = True
        # By the time we get here there are 4 combinations of self.CompleteReadOnly, self.DisableAllCommands and self.ForceStandardMode
        #     All 3 are False --> Try to get to Powerlink 
        #     self.ForceStandardMode is True --> Force Standard Mode, the panel can still be armed and disarmed
        #     self.ForceStandardMode and self.DisableAllCommands are True --> The integration interacts with the panel but commands such as arm/disarm are not allowed
        #     All 3 are True  --> Full readonly, no data sent to the panel
        # The 2 if statements above ensure these are the only supported combinations.
        log.debug(f"[Settings] ForceStandard to {self.ForceStandardMode}     DisableAllCommands to {self.DisableAllCommands}     CompleteReadOnly to {self.CompleteReadOnly}")
         
    def isPowerMaster(self):
        if self.PowerMaster is not None and self.PowerMaster: # PowerMaster models
            return True
        return False

    # This is called from the loop handler when the connection to the transport is made
    def vp_connection_made(self, transport : AlTransport):
        """Make the protocol connection to the Panel."""
        self.transport = transport
        log.debug("[Connection] Connected to local Protocol handler and Transport Layer")

        # get the value for Force standard mode (i.e. do not attempt to go to powerlink)
        self._initialiseConnection()

    # This is called by the asyncio parent when the connection is lost
    def vp_connection_lost(self, exc):
        """Log when connection is closed, if needed call callback."""
        if not self.suspendAllOperations:
            log.error(f"ERROR Connection Lost : disconnected because the Ethernet/USB connection was externally terminated.  {exc}")
        self._performDisconnect(reason="termination", exc=exc)

    # when the connection has problems then call the onDisconnect when available,
    #     otherwise try to reinitialise the connection from here
    def _performDisconnect(self, reason : str, exc=None):
        """Log when connection is closed, if needed call callback."""
        if self.suspendAllOperations:
            # log.debug('[Disconnection] Suspended. Sorry but all operations have been suspended, please recreate connection')
            return

        self.shutdownOperation()

        if exc is not None:
            # log.exception("ERROR Connection Lost : disconnected due to exception  <{0}>".format(exc))
            log.error("ERROR Connection Lost : disconnected due to exception {0}".format(exc))
        else:
            log.error("ERROR Connection Lost : disconnected because of close/abort.")

        #self.pmPhoneNr_t = {}
        self.pmEventLogDictionary = {}

        # We do not put these pin codes in to the panel status
        self.pmPincode_t = []  # allow maximum of 48 user pin codes

        # Empty the sensor details
        self.SensorList = {}

        # Empty the X10 details
        self.SwitchList = {}

        # Save the EEPROM data when downloaded
        self.pmRawSettings = {}

        self.lastRecvOfPanelData = None

        # Save the sirens
        #self.pmSirenDev_t = {}

        self.transport = None

        #sleep(5.0)  # a bit of time for the watchdog timers and keep alive loops to self terminate
        if self.onDisconnectHandler:
            log.error("                        Calling Exception handler.")
            self.onDisconnectHandler(reason, exc)
        else:
            log.error("                        No Exception handler to call, terminating Component......")

    # _initialiseConnection the parameters for the comms and set everything ready
    #    This is called when the connection is first made and then after any comms exceptions
    def _initialiseConnection(self):
        if self.suspendAllOperations:
            log.warning("[Connection] Suspended. Sorry but all operations have been suspended, please recreate connection")
            return
        # Define powerlink seconds timer and start it for PowerLink communication
        self._reset_watchdog_timeout()
        self._reset_keep_alive_messages()
        if not self.ForceStandardMode:
            self.triggeredDownload = False
            self._sendInterfaceResetCommand()
            self._startDownload()
        else:
            self._gotoStandardMode()
        asyncio.create_task(self._keep_alive_and_watchdog_timer())

    def _sendInterfaceResetCommand(self):
        # This should re-initialise the panel, most of the time it works!
        # Clear the send list and empty the expected response list
        self._clearList()
        # Send Exit and Stop to the panel. This should quit download mode.
        self._sendCommand("MSG_STOP")
        self._sendCommand("MSG_EXIT")
        
        if self.pmInitSupportedByPanel:
            log.debug("[_sendInterfaceResetCommand]   ************************************* Sending an INIT Command ************************************")
            self._sendCommand("MSG_INIT")
        # Wait for the Exit and Stop (and Init) to be sent. Make sure nothing is left to send.
        while not self.suspendAllOperations and len(self.SendList) > 0:
            log.debug("[_sendInterfaceResetCommand]       Waiting to empty send command queue")
            self._sendCommand(None)  # Check send queue
        if self.ForceStandardMode and not self.pmGotPanelDetails:
            self._sendCommand("MSG_BUMP")

    def _gotoStandardMode(self):
        if self.CompleteReadOnly:
            log.debug("[Standard Mode] Entering Complete Readonly Mode")
            self.PanelMode = AlPanelMode.COMPLETE_READONLY
        elif self.DisableAllCommands:
            log.debug("[Standard Mode] Entering Monitor Mode")
            self.PanelMode = AlPanelMode.MONITOR_ONLY
        elif self.pmDownloadComplete and not self.ForceStandardMode and self.pmGotUserCode:
            log.debug("[Standard Mode] Entering Standard Plus Mode as we got the pin codes from the EEPROM")
            self.PanelMode = AlPanelMode.STANDARD_PLUS
        else:
            log.debug("[Standard Mode] Entering Standard Mode")
            self.PanelMode = AlPanelMode.STANDARD
            self.ForceStandardMode = True
        self.GiveupTryingDownload = True
        self.pmPowerlinkModePending = False
        self.pmPowerlinkMode = False
        self.PanelLastEventData = self.setLastEventData()
        self.sendPanelUpdate(AlCondition.PANEL_UPDATE)  # push through a panel update to the HA Frontend
        if self.DisableAllCommands:
            # Clear the send list and empty the expected response list
            # Do not sent STOP, EXIT or INIT commands to the panel (as it already has Powerlink Hardware connected)
            self._clearList()
        else:
            self._sendInterfaceResetCommand()
        self._sendCommand("MSG_BYPASSTAT")

    def updateSensorNamesAndTypes(self, force = False) -> bool:
        """ Retrieve Zone Names and Zone Types if needed """
        # This function checks to determine if the Zone Names and Zone Types have been retrieved and if not it gets them
        #    For PowerMaster it also asks the panel for the sensor list
        retval = None
        if self.PanelType is not None and 0 <= self.PanelType <= 15:
            retval = False
            zoneCnt = pmPanelConfig_t["CFG_WIRELESS"][self.PanelType] + pmPanelConfig_t["CFG_WIRED"][self.PanelType]
            if self.PowerMaster is not None and self.PowerMaster:
                if force or len(self.ZoneNames) < zoneCnt:
                    retval = True
                    log.debug("[updateSensorNamesAndTypes] Trying to get the zone names again zone count = " + str(zoneCnt) + "  I've only got " + str(len(self.ZoneNames)) + " zone names")
                    self._sendCommand("MSG_POWERMASTER", options=[ [2, pmSendMsgB0_t["ZONE_STAT21"]] ])  # This asks the panel to send 03 21 messages (zone names)
                if force or len(self.ZoneTypes) < zoneCnt:
                    retval = True
                    log.debug("[updateSensorNamesAndTypes] Trying to get the zone types again zone count = " + str(zoneCnt) + "  I've only got " + str(len(self.ZoneTypes)) + " zone types")
                    self._sendCommand("MSG_POWERMASTER", options=[ [2, pmSendMsgB0_t["ZONE_STAT2D"]] ])  # This asks the panel to send 03 2D messages (zone types)
                if retval or not self.BeeZeroSensorList:
                    retval = True
                    self._sendCommand("MSG_POWERMASTER", options=[ [2, pmSendMsgB0_t["ZONE_STAT1F"]] ])  # This asks the panel to send 03 1F messages (sensor list)
            else:
                if force or len(self.ZoneNames) < zoneCnt:
                    retval = True
                    log.debug("[updateSensorNamesAndTypes] Trying to get the zone names again zone count = " + str(zoneCnt) + "  I've only got " + str(len(self.ZoneNames)) + " zone names")
                    self._sendCommand("MSG_ZONENAME")
                if force or len(self.ZoneTypes) < zoneCnt:
                    retval = True
                    log.debug("[updateSensorNamesAndTypes] Trying to get the zone types again zone count = " + str(zoneCnt) + "  I've only got " + str(len(self.ZoneTypes)) + " zone types")
                    self._sendCommand("MSG_ZONETYPE")
        else:
            log.debug(f"[updateSensorNamesAndTypes] Warning: Panel Type error {self.PanelType}")
        return retval

    # This function performs a "soft" reset to the send comms, it resets the queues, clears expected responses,
    #     resets watchdog timers and asks the panel for a status
    #     if in powerlink it tries a RESTORE to re-establish powerlink comms protocols
    def _triggerRestoreStatus(self):
        # Reset Send state (clear queue and reset flags)
        self._clearList()
        self.expectedResponseTimeout = 0
        # restart the watchdog and keep-alive counters
        self._reset_watchdog_timeout()
        self._reset_keep_alive_messages()
        if self.pmPowerlinkMode:
            # Send RESTORE to the panel
            self._sendCommand("MSG_RESTORE")  # also gives status
        else:
            self._sendCommand("MSG_STATUS")
            self.updateSensorNamesAndTypes()

    # We can only use this function when the panel has sent a "installing powerlink" message i.e. AB 0A 00 01
    #   We need to clear the send queue and reset the send parameters to immediately send an MSG_ENROLL
    def _sendMsgENROLL(self, triggerdownload):
        """ Auto enroll the PowerMax/Master unit """
        altDownloadCode = "BB BB"
        if not self.doneAutoEnroll:
            self.doneAutoEnroll = True
            self._sendCommand("MSG_ENROLL", options=[ [4, convertByteArray(self.DownloadCode)] ])
            if triggerdownload:
                self._startDownload()
        elif self.DownloadCode == altDownloadCode:
            log.warning("[_sendMsgENROLL] Warning: Trying to re enroll, already tried BBBB and still not successful")
        else:
            log.debug("[_sendMsgENROLL] Warning: Trying to re enroll but not triggering download")
            self.DownloadCode = altDownloadCode  # Force the Download code to be something different and try again ?????
            self._sendCommand("MSG_ENROLL", options=[ [4, convertByteArray(self.DownloadCode)] ])

    def _triggerEnroll(self, force):
        if force or (self.PanelType is not None and self.PanelType >= 3):
            # Only attempt to auto enroll powerlink for newer panels. Older panels need the user to manually enroll, we should be in Standard Plus by now.
            log.debug("[_triggerEnroll] Trigger Powerlink Attempt")
            # Allow the receipt of a powerlink ack to then send a MSG_RESTORE to the panel,
            #      this should kick it in to powerlink after we just enrolled
            self.allowAckToTriggerRestore = True
            # Send enroll to the panel to try powerlink
            # self._sendCommand("MSG_ENROLL", options=[ [4, convertByteArray(self.DownloadCode)] ])
            self._sendMsgENROLL(False)  # Auto enroll, do not request download
        elif self.PanelType is not None and self.PanelType >= 1:
            # Powermax+ or Powermax Pro, attempt to just send a MSG_RESTORE to prompt the panel in to taking action if it is able to
            log.debug("[_triggerEnroll] Trigger Powerlink Prompt attempt to a Powermax+ or Powermax Pro panel")
            # Prevent the receipt of a powerlink ack to then send a MSG_RESTORE to the panel,
            self.allowAckToTriggerRestore = False
            # Send a MSG_RESTORE, if it sends back a powerlink acknowledge then another MSG_RESTORE will be sent,
            #      hopefully this will be enough to kick the panel in to sending 0xAB Keep-Alive
            self._sendCommand("MSG_RESTORE")

    def _reset_keep_alive_messages(self):
        self.keep_alive_counter = 0

    # This function needs to be called within the timeout to reset the timer period
    def _reset_watchdog_timeout(self):
        self.watchdog_counter = 0

    # This function needs to be called within the timeout to reset the timer period
    def _reset_powerlink_counter(self):
        self.powerlink_counter = 1

    # Function to send I'm Alive and status request messages to the panel
    # This is also a timeout function for a watchdog. If we are in powerlink, we should get a AB 03 message every 20 to 30 seconds
    #    If we haven't got one in the timeout period then reset the send queues and state and then call a MSG_RESTORE
    # In standard mode, this command asks the panel for a status
    async def _keep_alive_and_watchdog_timer(self):
        self._reset_watchdog_timeout()
        self._reset_keep_alive_messages()
        status_counter = 0  # don't trigger first time!

        # declare a list and fill it with zeroes
        watchdog_list = [0] * WATCHDOG_MAXIMUM_EVENTS
        # The starting point doesn't really matter
        watchdog_pos = WATCHDOG_MAXIMUM_EVENTS - 1

        self.powerlink_counter = POWERLINK_RETRY_DELAY - 10  # set so first time it does it after 10 seconds
        download_counter = 0
        downloadDuration = 0
        no_data_received_counter = 0
        no_packet_received_counter = 0
        settime_counter = 0
        mode_counter = 0
        prevent_status_updates = False
        image_delay_counter = 0

        while not self.suspendAllOperations and not self.CompleteReadOnly:
            try:
                if self.loopbackTest:
                    # This supports the loopback test
                    await asyncio.sleep(5.0)
                    self._clearList()
                    self._sendCommand("MSG_STOP")

                else:
                    #  We set the time at the end of download and then check it periodically
                    if self.pmPowerlinkMode and self.AutoSyncTime:
                        settime_counter = settime_counter + 1
                        if settime_counter == 14400:      # every 4 hours (approx)
                            settime_counter = 0
                            # Get the time from the panel (this will compare to local time and set the panel time if different)
                            self._sendCommand("MSG_GETTIME")

                    # Watchdog functionality
                    self.watchdog_counter = self.watchdog_counter + 1
                    # every iteration, decrement all WATCHDOG_MAXIMUM_EVENTS watchdog counters (loop time is 1 second approx, doesn't have to be accurate)
                    watchdog_list = [x - 1 if x > 0 else 0 for x in watchdog_list]

                    # Keep alive functionality
                    self.keep_alive_counter = self.keep_alive_counter + 1

                    # The Expected Response Timer
                    self.expectedResponseTimeout = self.expectedResponseTimeout + 1

                    if not self.pmDownloadMode:
                        downloadDuration = 0

                    # log.debug("[Controller] is {0}".format(self.watchdog_counter))

                    if not self.GiveupTryingDownload and self.pmDownloadMode and not self.ForceStandardMode:
                        # First, during actual download keep resetting the watchdogs and timers to prevent any other comms to the panel, check for download timeout
                        # Disable watchdog and keep-alive during download (and keep flushing send queue)
                        self._reset_watchdog_timeout()
                        self._reset_keep_alive_messages()
                        # count in seconds that we've been in download mode
                        downloadDuration = downloadDuration + 1

                        if downloadDuration == 5 and not self.pmGotPanelDetails:
                            # Download has already been triggered
                            # This is a check at 5 seconds in to see if the panel details have been retrieved.  If not then kick off the download request again.
                            log.debug("[Controller] Trigger Panel Download Attempt - Not yet received the panel details")
                            self.pmDownloadComplete = False
                            self.pmDownloadMode = False
                            self.triggeredDownload = False
                            self._startDownload()

                        elif not EEPROM_DOWNLOAD_ALL and downloadDuration > DOWNLOAD_TIMEOUT:
                            log.warning("[Controller] ********************** Download Timer has Expired, Download has taken too long *********************")
                            log.warning("[Controller] ************************************* Going to standard mode ***************************************")
                            # Stop download mode
                            self.pmDownloadMode = False
                            # goto standard mode
                            self._gotoStandardMode()
                            self.DownloadTimeout = self.DownloadTimeout + 1
                            self.sendPanelUpdate(AlCondition.DOWNLOAD_TIMEOUT)  # download timer expired

                    elif not self.GiveupTryingDownload and not self.pmDownloadComplete and not self.ForceStandardMode and not self.pmDownloadMode:
                        # Second, if not actually doing download but download is incomplete then try every DOWNLOAD_RETRY_DELAY seconds
                        self._reset_watchdog_timeout()
                        download_counter = download_counter + 1
                        log.debug("[Controller] download_counter is {0}".format(download_counter))
                        if download_counter >= DOWNLOAD_TIMEOUT_GIVE_UP:
                            download_counter = 0
                            log.warning("[Controller] ********************** Download Timer has Expired, Download has taken far too long *********************")
                            log.warning("[Controller] ************************************* Going to standard mode *******************************************")
                            # Stop download mode
                            self.pmDownloadMode = False
                            # goto standard mode
                            self._gotoStandardMode()
                            self.DownloadTimeout = self.DownloadTimeout + 1
                            self.sendPanelUpdate(AlCondition.DOWNLOAD_TIMEOUT)  # download timer expired
                        
                        elif download_counter % DOWNLOAD_RETRY_DELAY == 0:  #
                            # trigger a download
                            log.debug("[Controller] Trigger Panel Download Attempt")
                            self.triggeredDownload = False
                            self._startDownload()

                    elif (
                        not self.GiveupTryingDownload
                        and self.pmPowerlinkModePending
                        and not self.ForceStandardMode
                        and self.pmDownloadComplete
                        and not self.pmPowerlinkMode
                    ):
                        # Third, when download has completed successfully, and not ForceStandard from the user, then attempt to connect in powerlink
                        if self.PanelType is not None:  # By the time EPROM download is complete, this should be set but just check to make sure
                            # Attempt to enter powerlink mode
                            self._reset_watchdog_timeout()
                            self.powerlink_counter = self.powerlink_counter + 1
                            #log.debug("[Controller] Powerlink Counter {0}".format(self.powerlink_counter))
                            if (self.powerlink_counter % POWERLINK_RETRY_DELAY) == 0:  # when the remainder is zero
                                self._triggerEnroll(False)
                            elif len(self.pmExpectedResponse) > 0 and self.expectedResponseTimeout >= RESPONSE_TIMEOUT:
                                log.debug("[Controller] ****************************** During Powerlink Attempts - Response Timer Expired ********************************")
                                if self.PanelMode != AlPanelMode.PROBLEM:
                                    # If it does come here multiple times then only count once
                                    self.PanelProblemCount = self.PanelProblemCount + 1
                                    self.LastPanelProblemTime = self._getTimeFunction() # local time as its for the user
                                    self.PanelMode = AlPanelMode.PROBLEM
                                    # Remember that PowerlinkMode is False here anyway
                                self.PanelReady = False
                                self.PanelLastEventData = self.setLastEventData()
                                self.sendPanelUpdate(AlCondition.PANEL_UPDATE)  # push through a panel update to the HA Frontend
                                self.pmExpectedResponse = []
                                self.expectedResponseTimeout = 0
                            elif self.powerlink_counter >= POWERLINK_TIMEOUT:
                                # give up on trying to get to powerlink and goto standard mode (could be either Standard Plus or Standard)
                                log.debug("[Controller] Giving up on Powerlink Attempts, going to one of the standard modes")
                                self._gotoStandardMode()

                    elif self.watchdog_counter >= WATCHDOG_TIMEOUT:  #  the loop runs at 1 second
                        # Fourth, check to see if the watchdog timer has expired
                        # watchdog timeout
                        log.debug("[Controller] ****************************** WatchDog Timer Expired ********************************")
                        status_counter = 0  # delay status requests
                        self._reset_watchdog_timeout()
                        self._reset_keep_alive_messages()

                        # Total Watchdog timeouts
                        self.WatchdogTimeout = self.WatchdogTimeout + 1
                        # Total Watchdog timeouts in last 24 hours. Total up the entries > 0
                        self.WatchdogTimeoutPastDay = 1 + sum(1 if x > 0 else 0 for x in watchdog_list)    # in range 1 to 11

                        # move to the next position which is the oldest entry in the list
                        watchdog_pos = (watchdog_pos + 1) % WATCHDOG_MAXIMUM_EVENTS

                        # When watchdog_list[watchdog_pos] > 0 then the 24 hour period from the timeout WATCHDOG_MAXIMUM_EVENTS times ago hasn't decremented to 0.
                        #    So it's been less than 1 day for the previous WATCHDOG_MAXIMUM_EVENTS timeouts
                        if not self.GiveupTryingDownload and watchdog_list[watchdog_pos] > 0:
                            log.debug("[Controller]               **************** Going to Standard (Plus) Mode and re-establish panel connection ***************")
                            self.sendPanelUpdate(AlCondition.WATCHDOG_TIMEOUT_GIVINGUP)   # watchdog timer expired, going to standard (plus) mode
                            self._gotoStandardMode()     # This sets self.GiveupTryingDownload to True
                        else:
                            log.debug("[Controller]               ******************* Trigger Restore Status *******************")
                            self.sendPanelUpdate(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired, going to try again
                            self._triggerRestoreStatus() # Clear message buffers and send a Restore (if in Powerlink) or Status (not in Powerlink) to the Panel

                        # Overwrite the oldest entry and set it to 1 day in seconds. Keep the stats going in all modes for the statistics
                        #    Note that the asyncio 1 second sleep does not create an accurate time and this may be slightly more than 24 hours.
                        watchdog_list[watchdog_pos] = 60 * 60 * 24  # seconds in 1 day
                        log.debug("[Controller]               Watchdog counter array, current=" + str(watchdog_pos))
                        log.debug("[Controller]                       " + str(watchdog_list))

                    elif self.ImageManager.isImageDataInProgress():
                        # Manage the download of the F4 messages for Camera PIRs
                        # As this does not use acknowledges or checksums then prevent the expected response timer from kicking in
                        self.ImageManager.terminateIfExceededTimeout(40)
                        
                    elif len(self.pmExpectedResponse) > 0 and self.expectedResponseTimeout >= RESPONSE_TIMEOUT:
                        # Expected response timeouts are only a problem when in Powerlink Mode as we expect a response
                        #   But in all modes, give the panel a _triggerRestoreStatus
                        if not self.ForceStandardMode and self.PanelMode != AlPanelMode.PROBLEM:
                            st = '[{}]'.format(', '.join(hex(x) for x in self.pmExpectedResponse))
                            log.debug("[Controller] ****************************** Response Timer Expired ********************************")
                            log.debug("[Controller]                                While Waiting for: {0}".format(st))
                            # If it does come here multiple times then only count once
                            self.PanelProblemCount = self.PanelProblemCount + 1
                            self.LastPanelProblemTime = self._getTimeFunction()   # local time as its for the user
                            self.PanelMode = AlPanelMode.PROBLEM
                            # Drop out of Powerlink mode if there are problems with the panel connection (it is no longer reliable)
                            self.pmPowerlinkMode = False
                            self._reset_powerlink_counter()
                            self.sendPanelUpdate(AlCondition.PANEL_UPDATE)  # push through a panel update to the HA Frontend
                        self.PanelReady = False
                        self.PanelLastEventData = self.setLastEventData()
                        self._triggerRestoreStatus()     # Clear message buffers and send a Restore (if in Powerlink) or Status (not in Powerlink) to the Panel

                    
                    # TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        
                    # TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        
                    # TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        
                    # TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        TESTING FROM HERE        

                    if not self.pmDownloadMode and not prevent_status_updates:
                        # We can't do any of this if the panel is in the downloading state or receiving a jpg image
                        mode_counter = mode_counter + 1
                        if (mode_counter % 86400) == 0:
                            # Every 24 hours
                            mode_counter = 0
                            # Force an update to see if sensors have been added / removed, or zone names and types changed
                            self.updateSensorNamesAndTypes(True)
                        elif self.PowerMaster is not None and self.PowerMaster and self.pmPowerlinkMode:
                            # Check these every 120 seconds
                            if (mode_counter % 120) == 0:
                                log.debug("[Controller] ****************************** Asking For Panel Sensor State ****************************")
                                # This is probably too much for the panel and also too often but for testing this will be OK
                                self._sendCommand("MSG_PM_SENSORS")  # This asks the panel to send panel sensors                
                        elif self.ForceStandardMode:
                            # Do most of this for ALL Panel Types
                            # Only check these every 180 seconds
                            if (mode_counter % 180) == 0:
                                if self.PanelState == AlPanelStatus.UNKNOWN:
                                    log.debug("[Controller] ****************************** Getting Panel Status ********************************")
                                    self._sendCommand("MSG_STATUS_SEN")
                                elif self.PanelState == AlPanelStatus.DOWNLOADING:
                                    log.debug("[Controller] ****************************** Exit Download Kicker ********************************")
                                    self._sendInterfaceResetCommand()
                                elif not self.pmGotPanelDetails:
                                    log.debug("[Controller] ****************************** Asking For Panel Details ****************************")
                                    self._sendCommand("MSG_BUMP")
                                else:
                                    # The first time this may create sensors (for PowerMaster, especially those in the range Z33 to Z64 as the A5 message will not have created them)
                                    # Subsequent calls make sure we have all zone names, zone types and the sensor list
                                    self.updateSensorNamesAndTypes()
                                    if self.PowerMaster is not None and self.PowerMaster:
                                        log.debug("[Controller] ****************************** Asking For 0x03 0x39 Details ****************************")
                                        # This is probably to much too often but for testing this will be OK
                                        self._sendCommand("MSG_POWERMASTER", options=[ [2, pmSendMsgB0_t["ZONE_STAT39"]] ])  # This asks the panel to send 03 39 messages
                    
                    # TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      
                    # TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      
                    # TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      TESTING TO HERE      

                    # Is it time to send an I'm Alive message to the panel
                    if not prevent_status_updates and len(self.SendList) == 0 and not self.pmDownloadMode and self.keep_alive_counter >= KEEP_ALIVE_PERIOD:  #
                        # Every KEEP_ALIVE_PERIOD seconds, unless watchdog has been reset
                        self._reset_keep_alive_messages()

                        status_counter = status_counter + 1
                        if status_counter >= 3:  # around the loop i.e every KEEP_ALIVE_PERIOD * 3 seconds
                            status_counter = 0
                            if not self.pmPowerlinkMode:
                                # When is standard mode, sending this asks the panel to send us the status so we know that the panel is ok.
                                self._sendCommand("MSG_STATUS")  # Asks the panel to send us the A5 message set
                            elif self.PowerMaster is not None and self.PowerMaster:
                                # When in powerlink mode and the panel is PowerMaster get the status to make sure the sensor states get updated
                                #   (if powerlink and powermax panel then no need to keep doing this)
                                # self._sendCommand("MSG_RESTORE")  # Commented out on 3/12/2020 as user with PM10, the panel keeps ignoring the MSG_RESTORE
                                self._sendCommand("MSG_STATUS")  #
                            else:
                                # When in powerlink mode and the panel is PowerMax, get the bypass status to make sure the sensor states get updated
                                # This is to make sure that if the user changes the setting on the panel itself, this updates the sensor state here
                                self._sendCommand("MSG_BYPASSTAT")
                        else:
                            # Send I'm Alive to the panel so it knows we're still here
                            self._sendCommand("MSG_ALIVE")
                    else:
                        # Every 1.0 seconds, try to flush the send queue
                        self._sendCommand(None)  # check send queue

                    # sleep, doesn't need to be highly accurate so just count each second
                    await asyncio.sleep(1.0)

                    if not self.suspendAllOperations:  ## To make sure as it could have changed in the 1 second sleep
                        if self.lastPacket is None:
                            no_packet_received_counter = no_packet_received_counter + 1
                            #log.debug(f"[Controller] no_packet_received_counter {no_packet_received_counter}")
                            if no_packet_received_counter >= NO_RECEIVE_DATA_TIMEOUT:  ## lets assume approx 30 seconds
                                log.error(
                                    "[Controller] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. no valid packet has been received from the panel)"
                                )
                                self.StopAndSuspend("neverconnected")

                        if not self.suspendAllOperations and self.lastRecvOfPanelData is None:  ## has any data been received from the panel yet?
                            no_data_received_counter = no_data_received_counter + 1
                            # log.debug(f"[Controller] no_data_received_counter {no_data_received_counter}")
                            if no_data_received_counter >= NO_RECEIVE_DATA_TIMEOUT:  ## lets assume approx 30 seconds
                                log.error(
                                    "[Controller] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. no data has been received from the panel)"
                                )
                                self.StopAndSuspend("neverconnected")
                        else:  # Data has been received from the panel but check when it was last received
                            # calc time difference between now and when data was last received
                            interval = self._getUTCTimeFunction() - self.lastRecvOfPanelData
                            # log.debug("Checking last receive time {0}".format(interval))
                            if interval >= timedelta(seconds=LAST_RECEIVE_DATA_TIMEOUT):
                                log.error(
                                    "[Controller] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. data has not been received from the panel in " + str(interval) + " seconds)"
                                )
                                self.StopAndSuspend("disconnected")

            except Exception as ex:
                log.error("[Controller] Visonic Executor loop has caused an exception, reinitialising variables and restarting loop")
                log.error(f"             {ex}")
                self._reset_watchdog_timeout()
                self._reset_keep_alive_messages()
                status_counter = 0  # don't trigger first time!

                # declare a list and fill it with zeroes
                watchdog_list = [0] * WATCHDOG_MAXIMUM_EVENTS
                # The starting point doesn't really matter
                watchdog_pos = WATCHDOG_MAXIMUM_EVENTS - 1

                self.powerlink_counter = POWERLINK_RETRY_DELAY - 10  # set so first time it does it after 10 seconds
                download_counter = 0
                downloadDuration = 0
                no_data_received_counter = 0
                settime_counter = 0
                mode_counter = 0
                prevent_status_updates = False
                
    def StopAndSuspend(self, t : str):
        self.shutdownOperation()
        self.sendPanelUpdate(AlCondition.NO_DATA_FROM_PANEL)  # Plugin suspended itself
        if self.transport is not None:
            self.transport.close()
        self.transport = None

    # Process any received bytes (in data as a bytearray)
    def vp_data_received(self, data):
        """Add incoming data to ReceiveData."""
        if self.suspendAllOperations:
            return
        if not self.firstCmdSent:
            log.debug("[data receiver] Ignoring garbage data: " + self._toString(data))
            return
        # log.debug('[data receiver] received data: %s', self._toString(data))
        self.lastRecvOfPanelData = self._getUTCTimeFunction()
        for databyte in data:
            # process a single byte at a time
            self._handle_received_byte(databyte)

    # Process one received byte at a time to build up the received PDU (Protocol Description Unit)
    #       self.pmIncomingPduLen is only used in this function
    #       self.pmCrcErrorCount is only used in this function
    #       self.pmCurrentPDU is only used in this function
    #       self._resetMessageData is only used in this function
    #       self._processCRCFailure is only used in this function
    def _handle_received_byte(self, data):
        """Process a single byte as incoming data."""
        if self.suspendAllOperations:
            return

        pdu_len = len(self.ReceiveData)                                      # Length of the received data so far
        
        # If we're receiving a variable length message and we're at the position in the message where we get the variable part
        if not isinstance(self.pmCurrentPDU, dict):
            # log.debug(f"[data receiver] {self.pmCurrentPDU.isvariablelength} {pdu_len == self.pmCurrentPDU.varlenbytepos}")
            if self.pmCurrentPDU.isvariablelength and pdu_len == self.pmCurrentPDU.varlenbytepos:
                # Determine total length of the message by getting the variable part int(data) and adding it to the fixed length part
                self.pmIncomingPduLen = self.pmCurrentPDU.length + int(data)
                self.pmFlexibleLength = self.pmCurrentPDU.flexiblelength
                #log.debug("[data receiver] Variable length Message Being Received  Message Type {0}     pmIncomingPduLen {1}   data var {2}".format(hex(self.ReceiveData[1]).upper(), self.pmIncomingPduLen, int(data)))

        # If we were expecting a message of a particular length (i.e. self.pmIncomingPduLen > 0) and what we have is already greater then that length then dump the message and resynchronise.
        if 0 < self.pmIncomingPduLen <= pdu_len:                             # waiting for pmIncomingPduLen bytes but got more and haven't been able to validate a PDU
            log.info("[data receiver] PDU Too Large: Dumping current buffer {0}    The next byte is {1}".format(self._toString(self.ReceiveData), hex(data).upper()))
            pdu_len = 0                                                      # Reset the incoming data to 0 length
            self._resetMessageData()

        # If this is the start of a new message, 
        #      then check to ensure it is a PACKET_HEADER (message preamble)
        if pdu_len == 0:
            self._resetMessageData()
            if data == PACKET_HEADER:  # preamble
                self.ReceiveData.append(data)
                #log.debug("[data receiver] Starting PDU " + self._toString(self.ReceiveData))
            # else we're trying to resync and walking through the bytes waiting for a PACKET_HEADER preamble byte

        elif pdu_len == 1:
            #log.debug("[data receiver] Received message Type %d", data)
            if data != 0x00 and data in pmReceiveMsg_t:                      # Is it a message type that we know about
                self.pmCurrentPDU = pmReceiveMsg_t[data]                     # set to current message type parameter settings for length, does it need an ack etc
                self.ReceiveData.append(data)                                # Add on the message type to the buffer
                if not isinstance(self.pmCurrentPDU, dict):
                    self.pmIncomingPduLen = self.pmCurrentPDU.length         # for variable length messages this is the fixed length and will work with this algorithm until updated.
                #log.debug("[data receiver] Building PDU: It's a message {0}; pmIncomingPduLen = {1}   variable = {2}".format(hex(data).upper(), self.pmIncomingPduLen, self.pmCurrentPDU.isvariablelength))
            elif data == 0x00 or data == 0xFD:                               # Special case for pocket and PowerMaster 10
                log.info("[data receiver] Received message type {0} so not processing it".format(hex(data).upper()))
                self._resetMessageData()
            else:
                # build an unknown PDU. As the length is not known, leave self.pmIncomingPduLen set to 0 so we just look for PACKET_FOOTER as the end of the PDU
                self.pmCurrentPDU = pmReceiveMsg_t[0]                        # Set to unknown message structure to get settings, varlenbytepos is -1
                self.pmIncomingPduLen = 0                                    # self.pmIncomingPduLen should already be set to 0 but just to make sure !!!
                log.warning("[data receiver] Warning : Construction of incoming packet unknown - Message Type {0}".format(hex(data).upper()))
                self.ReceiveData.append(data)                                # Add on the message type to the buffer

        elif pdu_len == 2 and isinstance(self.pmCurrentPDU, dict):
            #log.debug("[data receiver] Building PDU: It's a variable message {0} {1}".format(hex(self.ReceiveData[0]).upper(), hex(data).upper()))
            if data in self.pmCurrentPDU:
                self.pmCurrentPDU = self.pmCurrentPDU[data]
                #log.debug("[data receiver] Building PDU:   doing it properly")
            else:
                self.pmCurrentPDU = self.pmCurrentPDU[0]                     # All should have a 0 entry so use as default when unknown
                log.debug("[data receiver] Building PDU: It's a variable message {0} {1} BUT it is unknown".format(hex(self.ReceiveData[0]).upper(), hex(data).upper()))
            self.pmIncomingPduLen = self.pmCurrentPDU.length                 # for variable length messages this is the fixed length and will work with this algorithm until updated.
            self.ReceiveData.append(data)                                    # Add on the message type to the buffer

        elif self.pmFlexibleLength > 0 and data == PACKET_FOOTER and pdu_len + 1 < self.pmIncomingPduLen and (self.pmIncomingPduLen - pdu_len) < self.pmFlexibleLength:
            # Only do this when:
            #       Looking for "flexible" messages
            #              At the time of writing this, only the 0x3F EPROM Download PDU does this with some PowerMaster panels
            #       Have got the PACKET_FOOTER message terminator
            #       We have not yet received all bytes we expect to get
            #       We are within 5 bytes of the expected message length, self.pmIncomingPduLen - pdu_len is the old length as we already have another byte in data
            #              At the time of writing this, the 0x3F was always only up to 3 bytes short of the expected length and it would pass the CRC checks
            # Do not do this when (pdu_len + 1 == self.pmIncomingPduLen) i.e. the correct length
            # There is possibly a fault with some panels as they sometimes do not send the full EPROM data.
            #    - Rather than making it panel specific I decided to make this a generic capability
            self.ReceiveData.append(data)  # add byte to the message buffer
            if self.pmCurrentPDU.ignorechecksum or self._validatePDU(self.ReceiveData):  # if the message passes CRC checks then process it
                # We've got a validated message
                log.debug("[data receiver] Validated PDU: Got Validated PDU type 0x%02x   data %s", int(self.ReceiveData[1]), self._toString(self.ReceiveData))
                self._processReceivedMessage(ackneeded=self.pmCurrentPDU.ackneeded, data=self.ReceiveData)
                self._resetMessageData()

        elif (self.pmIncomingPduLen == 0 and data == PACKET_FOOTER) or (pdu_len + 1 == self.pmIncomingPduLen): # postamble (the +1 is to include the current data byte)
            # (waiting for PACKET_FOOTER and got it) OR (actual length == calculated expected length)
            self.ReceiveData.append(data)  # add byte to the message buffer
            # log.debug("[data receiver] Building PDU: Checking it " + self._toString(self.ReceiveData))
            msgType = self.ReceiveData[1]
            if self.pmCurrentPDU.ignorechecksum or self._validatePDU(self.ReceiveData):
                # We've got a validated message
                # log.debug("[data receiver] Building PDU: Got Validated PDU type 0x%02x   data %s", int(msgType), self._toString(self.ReceiveData))
                if self.pmCurrentPDU.varlenbytepos < 0:  # is it an unknown message i.e. varlenbytepos is -1
                    log.warning("[data receiver] Received Valid but Unknown PDU {0}".format(hex(msgType)))
                    self._sendAck()  # assume we need to send an ack for an unknown message
                else:  # Process the received known message
                    self._processReceivedMessage(ackneeded=self.pmCurrentPDU.ackneeded, data=self.ReceiveData)
                self._resetMessageData()
            else:
                # CRC check failed
                a = self._calculateCRC(self.ReceiveData[1:-2])[0]  # this is just used to output to the log file
                if len(self.ReceiveData) > PACKET_MAX_SIZE:
                    # If the length exceeds the max PDU size from the panel then stop and resync
                    log.warning("[data receiver] PDU with CRC error Message = {0}   checksum calcs {1}".format(self._toString(self.ReceiveData), hex(a).upper()))
                    self._processCRCFailure()
                    self._resetMessageData()
                elif self.pmIncomingPduLen == 0:
                    if msgType in pmReceiveMsg_t:
                        # A known message with zero length and an incorrect checksum. Reset the message data and resync
                        log.warning("[data receiver] Warning : Construction of zero length incoming packet validation failed - Message = {0}  checksum calcs {1}".format(self._toString(self.ReceiveData), hex(a).upper()))

                        # Send an ack even though the its an invalid packet to prevent the panel getting confused
                        if self.pmCurrentPDU.ackneeded:
                            # log.debug("[data receiver] Sending an ack as needed by last panel status message " + hex(msgType).upper())
                            self._sendAck(data=self.ReceiveData)

                        # Dump the message and carry on
                        self._processCRCFailure()
                        self._resetMessageData()
                    else:  # if msgType != 0xF1:        # ignore CRC errors on F1 message
                        # When self.pmIncomingPduLen == 0 then the message is unknown, the length is not known and we're waiting for a PACKET_FOOTER where the checksum is correct, so carry on
                        log.debug("[data receiver] Building PDU: Length is {0} bytes (apparently PDU not complete)  {1}  checksum calcs {2}".format(len(self.ReceiveData), self._toString(self.ReceiveData), hex(a).upper()) )
                else:
                    # When here then the message is a known message type of the correct length but has failed it's validation
                    log.warning("[data receiver] Warning : Construction of incoming packet validation failed - Message = {0}   checksum calcs {1}".format(self._toString(self.ReceiveData), hex(a).upper()))

                    # Send an ack even though the its an invalid packet to prevent the panel getting confused
                    if self.pmCurrentPDU.ackneeded:
                        # log.debug("[data receiver] Sending an ack as needed by last panel status message " + hex(msgType).upper())
                        self._sendAck(data=self.ReceiveData)

                    # Dump the message and carry on
                    self._processCRCFailure()
                    self._resetMessageData()

        elif pdu_len <= PACKET_MAX_SIZE:
            # log.debug("[data receiver] Current PDU " + self._toString(self.ReceiveData) + "    adding " + str(hex(data).upper()))
            self.ReceiveData.append(data)
        else:
            log.debug("[data receiver] Dumping Current PDU " + self._toString(self.ReceiveData))
            self._resetMessageData()
        # log.debug("[data receiver] Building PDU " + self._toString(self.ReceiveData))

    def _resetMessageData(self):
        # clear our buffer again so we can receive a new packet.
        self.ReceiveData = bytearray(b"")  # messages should never be longer than PACKET_MAX_SIZE
        # Reset control variables ready for next time
        self.pmCurrentPDU = pmReceiveMsg_t[0]
        self.pmIncomingPduLen = 0
        self.pmFlexibleLength = 0

    def _processCRCFailure(self):
        msgType = self.ReceiveData[1]
        if msgType != 0xF1:  # ignore CRC errors on F1 message
            self.pmCrcErrorCount = self.pmCrcErrorCount + 1
            if self.pmCrcErrorCount >= MAX_CRC_ERROR:
                self.pmCrcErrorCount = 0
                interval = self._getUTCTimeFunction() - self.pmFirstCRCErrorTime
                if interval <= timedelta(seconds=CRC_ERROR_PERIOD):
                    self._performDisconnect(reason="crcerror", exc="CRC errors")
                self.pmFirstCRCErrorTime = self._getUTCTimeFunction()

    def _processReceivedMessage(self, ackneeded, data):
        # Unknown Message has been received
        msgType = data[1]
        # log.debug("[data receiver] *** Received validated message " + hex(msgType).upper() + "   data " + self._toString(data))
        # Send an ACK if needed
        if ackneeded:
            # log.debug("[data receiver] Sending an ack as needed by last panel status message " + hex(msgType).upper())
            self._sendAck(data=data)

        # Check response
        tmplength = len(self.pmExpectedResponse)
        if len(self.pmExpectedResponse) > 0:  # and msgType != 2:   # 2 is a simple acknowledge from the panel so ignore those
            # We've sent something and are waiting for a reponse - this is it
            # log.debug("[data receiver] msgType {0}  expected one of {1}".format(hex(msgType).upper(), [hex(no).upper() for no in self.pmExpectedResponse]))
            if msgType in self.pmExpectedResponse:
                # while msgType in self.pmExpectedResponse:
                self.pmExpectedResponse.remove(msgType)
                #log.debug("[data receiver] msgType {0} got it so removed from list, list is now {1}".format(hex(msgType).upper(), [hex(no).upper() for no in self.pmExpectedResponse]))
            #else:
            #    log.debug("[data receiver] msgType not in self.pmExpectedResponse   Waiting for next PDU :  expected {0}   got {1}".format([hex(no).upper() for no in self.pmExpectedResponse], hex(msgType).upper()))

        if len(self.pmExpectedResponse) == 0:
            #if tmplength > 0:
            #    log.debug("[data receiver] msgType {0} resetting expected response counter, it got up to {1}".format(hex(msgType).upper(), self.expectedResponseTimeout))
            self.expectedResponseTimeout = 0

        # Handle the message
        if self.packet_callback is not None:
            self.packet_callback(data)

    # Send an achnowledge back to the panel
    def _sendAck(self, data=bytearray(b"")):
        """ Send ACK if packet is valid """

        #ispm = len(data) > 3 and (data[1] >= 0xA5 or (data[1] < 0x10 and data[-2] == 0x43))
        ispm = len(data) > 3 and (data[1] == 0xAB or (data[1] < 0x10 and data[-2] == 0x43))

        # There are 2 types of acknowledge that we can send to the panel
        #    Normal    : For a normal message
        #    Powerlink : For when we are in powerlink mode
        if not self.ForceStandardMode and ispm:
            message = pmSendMsg["MSG_ACK_PLINK"]
        else:
            message = pmSendMsg["MSG_ACK"]
        assert message is not None
        e = VisonicListEntry(command=message)
        t = self.loop.create_task(self._sendPdu(e)) # , name="Send Acknowledge") #loop=self.loop)
        asyncio.gather(t)

    # Function to send all PDU messages to the panel, using a mutex lock to combine acknowledges and other message sends
    async def _sendPdu(self, instruction: VisonicListEntry):
        """Encode and put packet string onto write buffer."""

        if self.CompleteReadOnly:
            self._clearList()
            self.firstCmdSent = True
            return

        if self.suspendAllOperations:
            log.debug("[sendPdu] Suspended all operations, not sending PDU")
            return

        if instruction is None:
            log.error("[sendPdu] Attempt to send a command that is empty")
            return

        if instruction.command is None and instruction.raw is None:
            log.error("[sendPdu] Attempt to send a sub command that is empty")
            return

        if self.sendlock is None:
            self.sendlock = asyncio.Lock()

        async with self.sendlock:
            sData = None
            command = None

            if instruction.raw is not None:
                sData = instruction.raw
            
            elif instruction.command is not None:
                # Send a command to the panel
                command = instruction.command
                data = command.data
                
                # push in the options in to the appropriate places in the message. Examples are the pin or the specific command
                if instruction.options != None:
                    # the length of instruction.options has to be an even number
                    # it is a list of couples:  bitoffset , bytearray to insert
                    #op = int(len(instruction.options) / 2)
                    # log.debug("[sendPdu] Options {0} {1}".format(instruction.options, op))
                    for o in range(0, len(instruction.options)):
                        s = instruction.options[o][0] #   [o * 2]  # bit offset as an integer
                        a = instruction.options[o][1] # [o * 2 + 1]  # the bytearray to insert
                        if isinstance(a, int):
                            # log.debug("[sendPdu] Options {0} {1} {2} {3}".format(type(s), type(a), s, a))
                            data[s] = a
                        else:
                            # log.debug("[sendPdu] Options {0} {1} {2} {3} {4}".format(type(s), type(a), s, a, len(a)))
                            for i in range(0, len(a)):
                                data[s + i] = a[i]

                # log.debug('[sendPdu] input data: %s', self._toString(packet))
                # First add header (PACKET_HEADER), then the packet, then crc and footer (PACKET_FOOTER)
                sData = b"\x0D"
                sData += data
                #if self.PowerMaster is not None and self.PowerMaster:
                #    sData += self._calculateCRCAlt(data)
                #else:
                sData += self._calculateCRC(data)
                sData += b"\x0A"

            interval = self._getUTCTimeFunction() - self.pmLastTransactionTime
            sleepytime = timedelta(milliseconds=150) - interval
            if sleepytime > timedelta(milliseconds=0):
                # log.debug("[sendPdu] Speepytime {0}".format(sleepytime.total_seconds()))
                await asyncio.sleep(sleepytime.total_seconds())

            # no need to send i'm alive message for a while as we're about to send a command anyway
            self._reset_keep_alive_messages()
            self.firstCmdSent = True
            # Log some useful information in debug mode
            if self.transport is not None:
                self.transport.write(sData)
            else:
                log.debug("[sendPdu]      Comms transport has been set to none, must be in process of terminating comms")

            # log.debug("[sendPdu]      waiting for message response {}".format([hex(no).upper() for no in self.pmExpectedResponse]))

            if command is not None and command.download:
                self._clearList()
                self.pmDownloadMode = True
                self.triggeredDownload = False
                log.debug("[sendPdu] Setting Download Mode to true")

            if sData[1] != ACK_MESSAGE:  # the message is not an acknowledge back to the panel, then save it
                self.pmLastSentMessage = instruction

            self.pmLastTransactionTime = self._getUTCTimeFunction()

            if command is not None and command.debugprint:
                log.debug("[sendPdu] Sending Command ({0})    raw data {1}   waiting for message response {2}".format(command.msg, self._toString(sData), [hex(no).upper() for no in self.pmExpectedResponse]))
            elif instruction.raw is not None:
                # Assume raw data to send is not obfuscated for now
                log.debug("[sendPdu] Sending Raw Command      raw data {0}   waiting for message response {1}".format(self._toString(sData), [hex(no).upper() for no in self.pmExpectedResponse]))            
            #elif command is not None:
            #    # Do not log the full raw data as it may contain the user code
            #    log.debug("[sendPdu] Sending Command ({0})    <Obfuscated>   waiting for message response {1}".format(command.msg, [hex(no).upper() for no in self.pmExpectedResponse]))

            if command is not None and command.waittime > 0.0:
                log.debug("[sendPdu]          Command has a wait time after transmission {0}".format(command.waittime))
                await asyncio.sleep(command.waittime)

    def _addMessageToSendList(self, message, options=[]):
        if message is not None:
            m = pmSendMsg[message]
            assert m is not None
            e = VisonicListEntry(command=m, options=options)
            self.SendList.append(e)

    def _addPDUToSendList(self, m):
        if m is not None:
            e = VisonicListEntry(raw=m)
            self.SendList.append(e)


    # This is called to queue a command.
    def _sendCommand(self, message_type, options=[] ):
        """ Queue a command to send to the panel """
        # Up until HA version 2021-6-6 this function worked with the 3 lines in the "try" part all the time
        # With version 2021-7-1 it began to fail with exceptions.  There are also the same exceptions in other components, including the old built in zwave component.
        # I haven't checked but this could be to do with the version of python or changes that the core team have made to the code, I don't know
        #    It only fails when it is called from a loop other than the MainLoop from HA

        # This is a "fix" for HA 2021-7-1 onwards
        # When this function works is is because it has the current asyncio Main loop
        # So the external calls to arm/disarm, bypass/arm and get the event log are now handled by _addMessageToSendList as they may be from a different loop
        try:
            t = self.loop.create_task(self._sendCommandAsync(message_type, options)) #, name="Send Command to Panel") #, loop=self.loop)
            asyncio.gather(t)
        except RuntimeError as ex:
            log.debug("[_sendCommand] Exception {0}".format(ex))
            #if "there is no current event loop in thread" in str(ex).lower() or "operation invoked on an event loop other than the current one" in str(ex).lower():
            #    # External command from worker loop, this is not the main loop
            #    #      Simply add it to the list of messages to be sent and it will be sent in the next 1 second
            #    self._addMessageToSendList(message_type, kwargs)
            #    # We could terminate the sleep function early in _keep_alive_and_watchdog_timer but I don't know how (and it would screw up the timing in that function anyway)
            #    #   As it is, it will take up to 1 second to send the message (also remember that it is queued with other messages as well)


    # This is called to queue a command.
    # If it is possible, then also send the message
    async def _sendCommandAsync(self, message_type, options=[]):
        """Add a command to the send List
        The List is needed to prevent sending messages too quickly normally it requires 500msec between messages"""

        if self.suspendAllOperations:
            log.debug("[_sendCommand] Suspended all operations, not sending PDU")
            return

        if self.commandlock is None:
            self.commandlock = asyncio.Lock()

        # log.debug("[_sendCommand] options  {0}  {1}".format(type(options), options))

        async with self.commandlock:
            interval = self._getUTCTimeFunction() - self.pmLastTransactionTime
            timeout = interval > RESEND_MESSAGE_TIMEOUT
 
            # command may be set to None on entry
            # Always add the command to the list
            if message_type is not None:
                self._addMessageToSendList(message_type, options)

            # self.pmExpectedResponse will prevent us sending another message to the panel
            #   If the panel is lazy or we've got the timing wrong........
            #   If there's a timeout then resend the previous message. If that doesn't work then do a reset using _triggerRestoreStatus function
            #  Do not resend during startup or download as the timing is critical anyway
            if not self.pmDownloadMode and self.pmLastSentMessage is not None and timeout and len(self.pmExpectedResponse) > 0:
                if not self.pmLastSentMessage.triedResendingMessage:
                    # resend the last message
                    log.debug("[_sendCommand] Re-Sending last message  {0}".format(self.pmLastSentMessage.command.msg))
                    self.pmLastSentMessage.triedResendingMessage = True
                    await self._sendPdu(self.pmLastSentMessage)
                else:
                    # tried resending once, no point in trying again so reset settings, start from scratch
                    log.debug("[_sendCommand] Tried Re-Sending last message but didn't work. Assume a powerlink timeout state and resync")
                    self._triggerRestoreStatus()
            elif len(self.SendList) > 0 and len(self.pmExpectedResponse) == 0:  # we are ready to send
                # pop the oldest item from the list, this could be the only item.
                instruction = self.SendList.pop(0)

                if len(instruction.response) > 0:
                    #log.debug("[sendPdu] Resetting expected response counter, it got to {0}   Response list is now {1}".format(self.expectedResponseTimeout, len(instruction.response)))
                    self.expectedResponseTimeout = 0
                    # update the expected response list straight away (without having to wait for it to be actually sent) to make sure protocol is followed
                    self.pmExpectedResponse.extend(instruction.response)

                await self._sendPdu(instruction)

    # Clear the send queue and reset the associated parameters
    def _clearList(self):
        """ Clear the List, preventing any retry causing issue. """
        # Clear the List
        log.debug("[_clearList] Setting queue empty")
        self.SendList = []
        self.pmLastSentMessage = None
        self.pmExpectedResponse = []

    def _getLastSentMessage(self):
        return self.pmLastSentMessage

    # This puts the panel in to download mode. It is the start of determining powerlink access
    def _startDownload(self):
        """ Start download mode """
        if not self.pmDownloadComplete and not self.pmDownloadMode and not self.triggeredDownload:
            # self.pmWaitingForAckFromPanel = False
            self.pmExpectedResponse = []
            log.debug("[StartDownload] Starting download mode")
            self._sendCommand("MSG_DOWNLOAD", options=[ [3, convertByteArray(self.DownloadCode)] ])  #
            self.PanelMode = AlPanelMode.DOWNLOAD
            self.PanelState = AlPanelStatus.DOWNLOADING  # Downloading
            self.triggeredDownload = True
            self.PanelLastEventData = self.setLastEventData()
            self.sendPanelUpdate(AlCondition.PANEL_UPDATE)  # push through a panel update to the HA Frontend
        elif self.pmDownloadComplete:
            log.debug("[StartDownload] Download has already completed (so not doing anything)")
        else:
            log.debug("[StartDownload] Already in Download Mode (so not doing anything)")


    def _populateEPROMDownload(self, isPowerMaster):
        """ Populate the EEPROM Download List """

        # Empty list and start at the beginning
        self.myDownloadList = []

        if EEPROM_DOWNLOAD_ALL:
            for page in range(0, 256):
                mystr = '00 ' + format(page, '02x').upper() + ' 80 00'
                self.myDownloadList.append(convertByteArray(mystr))
                mystr = '80 ' + format(page, '02x').upper() + ' 80 00'
                self.myDownloadList.append(convertByteArray(mystr))

        else:
            lenMax = len(pmBlockDownload["PowerMax"])
            lenMaster = len(pmBlockDownload["PowerMaster"])

            # log.debug("lenMax = " + str(lenMax) + "    lenMaster = " + str(lenMaster))

            for dl in range(0, lenMax):
                self.myDownloadList.append(pmBlockDownload["PowerMax"][dl])

            if isPowerMaster:
                for dl in range(0, lenMaster):
                    self.myDownloadList.append(pmBlockDownload["PowerMaster"][dl])


    # Attempt to enroll with the panel in the same was as a powerlink module would inside the panel
    def _readPanelSettings(self, isPowerMaster):
        """ Attempt to Enroll as a Powerlink """
        log.debug("[Panel Settings] Uploading panel settings")

        # Populate the full list of EEPROM blocks
        self._populateEPROMDownload(isPowerMaster)

        # Send the first EPROM block to the panel to retrieve
        self._sendCommand("MSG_DL", options=[ [1, self.myDownloadList.pop(0)] ])  # Read the names of the zones


# This class performs transactions based on messages (ProtocolBase is the raw data)
class PacketHandling(ProtocolBase):
    """Handle decoding of Visonic packets."""

    def __init__(self, *args, **kwargs) -> None:
        """ Perform transactions based on messages (and not bytes) """
        super().__init__(packet_callback=self._processReceivedPacket, *args, **kwargs)
        self.eventCount = 0

        secdelay = DOWNLOAD_RETRY_DELAY + 100
        self.lastSendOfDownloadEprom = self._getUTCTimeFunction() - timedelta(seconds=secdelay)  # take off X seconds so the first command goes through immediately

        # Variables to manage the PowerMaster B0 message and the triggering of Motion
        self.lastRecvOfMasterMotionData = self._getUTCTimeFunction() - timedelta(seconds=secdelay)  # take off X seconds so the first command goes through immediately
        self.firstRecvOfMasterMotionData = self._getUTCTimeFunction() - timedelta(seconds=secdelay)  # take off X seconds so the first command goes through immediately
        self.zoneNumberMasterMotion = 0
        self.zoneDataMasterMotion = bytearray(b"")

        # These are used in the A5 message to reduce processing but mainly to reduce the amount of callbacks in to HA when nothing changes
        self.lowbatt_old = -1
        self.tamper_old = -1
        self.enrolled_old = 0  # means nothing enrolled
        self.status_old = -1
        self.bypass_old = -1
        self.zonealarm_old = -1
        self.zonetamper_old = -1

        self.pmBypassOff = False         # Do we allow the user to bypass the sensors, this is read from the EEPROM data
        self.pmPanicAlarmSilent = False  # Is Panic Alarm set to silent panic set in the panel. This is read from the EEPROM data

        self.pmForceArmSetInPanel = False          # If the Panel is using "Force Arm" then sensors may be automatically armed and bypassed by the panel when it is armed and disarmed

        self.lastPacketCounter = 0
        self.sensorsCreated = False  # Have the sensors benn created. Either from an A5 message or the EEPROM data

        # EXPERIMENTAL
        # The PowerMaster "B0" Messages
        self.save0306 = None
        self.beezero_024B_sensorcount = None

        self.PostponeEventTimer = 0
        
        # Task to cancel the trigger status of sensors after the timer expires
        asyncio.create_task(self._resetTriggeredStateTimer())

    def checkPostponeTimer(self):
        if self.PostponeEventTimer > 0:
            self.PostponeEventTimer = self.PostponeEventTimer - 1
            if self.PostponeEventTimer == 0:
                self.sendPanelUpdate(AlCondition.PANEL_UPDATE)  # push through a panel update to the HA Frontend

    # For the sensors that have been triggered, turn them off after self.MotionOffDelay seconds
    async def _resetTriggeredStateTimer(self):
        """ reset triggered state"""
        counter = 0
        while not self.suspendAllOperations:
            counter = counter + 1
            # cycle through the sensors and set the triggered value back to False after the timeout duration
            #    get it here so if it gets updated then we use the new value
            td = timedelta(seconds=self.MotionOffDelay)
            for key in self.SensorList:
                if self.SensorList[key].triggered:
                    interval = self._getUTCTimeFunction() - self.SensorList[key].utctriggertime
                    # at least self.MotionOffDelay seconds as it also depends on the frequency the panel sends messages
                    if interval > td:
                        log.debug("[_resetTriggeredStateTimer]   Sensor {0}   triggered to False".format(key))
                        self.SensorList[key].triggered = False
                        self.SensorList[key].pushChange(AlSensorCondition.RESET)
            # check every 0.5 seconds
            self.checkPostponeTimer()
            await asyncio.sleep(0.25)  # must be less than 5 seconds for self.suspendAllOperations:
            self.checkPostponeTimer()
            await asyncio.sleep(0.25)  # must be less than 5 seconds for self.suspendAllOperations:

    # _writeEPROMSettings: add a certain setting to the settings table
    #      When we send a MSG_DL and insert the 4 bytes from pmDownloadItem_t, what we're doing is setting the page, index and len
    # This function stores the downloaded status and EPROM data
    def _writeEPROMSettings(self, page, index, setting):
        settings_len = len(setting)
        wrap = index + settings_len - 0x100
        sett = [bytearray(b""), bytearray(b"")]

        #log.debug("[Write Settings]   Entering Function  page {0}   index {1}    length {2}".format(page, index, settings_len))
        if settings_len > 0xB1:
            log.debug("[Write Settings] ********************* Write Settings too long ********************")
            return

        if wrap > 0:
            # log.debug("[Write Settings] The write settings data is Split across 2 pages")
            sett[0] = setting[: settings_len - wrap]  # bug fix in 0.0.6, removed the -1
            sett[1] = setting[settings_len - wrap :]
            # log.debug("[Write Settings]         Wrapping  original len {0}   left len {1}   right len {2}".format(len(setting), len(sett[0]), len(sett[1])))
            wrap = 1
        else:
            sett[0] = setting
            wrap = 0

        for i in range(0, wrap + 1):
            if (page + i) not in self.pmRawSettings:
                self.pmRawSettings[page + i] = bytearray()
                for dummy in range(0, 256):
                    self.pmRawSettings[page + i].append(255)
                if len(self.pmRawSettings[page + i]) != 256:
                    log.debug("[Write Settings] the EEPROM settings is incorrect for page {0}".format(page + i))
                # else:
                #    log.debug("[Write Settings] WHOOOPEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE")

            settings_len = len(sett[i])
            if i == 1:
                index = 0
            #log.debug("[Write Settings]         Writing settings page {0}  index {1}    length {2}".format(page+i, index, settings_len))
            self.pmRawSettings[page + i] = self.pmRawSettings[page + i][0:index] + sett[i] + self.pmRawSettings[page + i][index + settings_len :]
            #if len(self.pmRawSettings[page + i]) != 256:
            #    log.debug("[Write Settings] OOOOOOOOOOOOOOOOOOOO len = {0}".format(len(self.pmRawSettings[page + i])))
            # else:
            #    log.debug("[Write Settings] Page {0} is now {1}".format(page+i, self._toString(self.pmRawSettings[page + i])))

    # _readEPROMSettingsPageIndex
    # This function retrieves the downloaded status and EPROM data
    def _readEPROMSettingsPageIndex(self, page, index, settings_len):
        retlen = settings_len
        retval = bytearray()
        if self.pmDownloadComplete:
            #log.debug("[_readEPROMSettingsPageIndex]    Entering Function  page {0}   index {1}    length {2}".format(page, index, settings_len))
            while page in self.pmRawSettings and retlen > 0:
                rawset = self.pmRawSettings[page][index : index + retlen]
                retval = retval + rawset
                page = page + 1
                retlen = retlen - len(rawset)
                index = 0
            if settings_len == len(retval):
                #log.debug("[_readEPROMSettingsPageIndex]       Length " + str(settings_len) + " returning (just the 1st value) " + self._toString(retval[:1]))
                return retval
        log.debug("[_readEPROMSettingsPageIndex]     Sorry but you havent downloaded that part of the EEPROM data     page={0} index={1} length={2}".format(hex(page), hex(index), settings_len))
        if not self.pmDownloadMode:
            self.pmDownloadComplete = False
            # prevent any more retrieval of the EEPROM settings and put us back to Standard Mode
            self._delayDownload()
            # try to download panel EPROM again
            self._startDownload()
        # return a bytearray filled with 0xFF values
        retval = bytearray()
        for dummy in range(0, settings_len):
            retval.append(255)
        return retval

    # this can be called from an entry in pmDownloadItem_t such as
    #      page index lenhigh lenlow
    def _readEPROMSettings(self, item):
        return self._readEPROMSettingsPageIndex(item[0], item[1], item[3] + (0x100 * item[2]))

    # This function was going to save the settings (including EPROM) to a file
    def _dumpEPROMSettings(self):
        log.debug("Dumping EPROM Settings")
        for p in range(0, 0x100):  ## assume page can go from 0 to 255
            if p in self.pmRawSettings:
                for j in range(0, 0x100, 0x10):  ## assume that each page can be 256 bytes long, step by 16 bytes
                    # do not display the rows with pin numbers
                    # if not (( p == 1 and j == 240 ) or (p == 2 and j == 0) or (p == 10 and j >= 140)):
                    if EEPROM_DOWNLOAD_ALL or ((p != 1 or j != 240) and (p != 2 or j != 0) and (p != 10 or j <= 140)):
                        if j <= len(self.pmRawSettings[p]):
                            s = self._toString(self.pmRawSettings[p][j : j + 0x10])
                            log.debug("{0:3}:{1:3}  {2}".format(p, j, s))

    def _calcBoolFromIntMask(self, val, mask):
        return True if val & mask != 0 else False

    # SettingsCommand = collections.namedtuple('SettingsCommand', 'show count type poff psize pstep pbitoff name values')
    def _lookupEprom(self, val: SettingsCommand):
        retval = []

        if val is None:
            retval.append("Not Found")
            retval.append("Not Found As Well")
            return retval

        for ctr in range(0, val.count):
            addr = val.poff + (ctr * val.pstep)
            page = math.floor(addr / 0x100)
            pos = addr % 0x100

            myvalue = ""

            size = 1 + ((val.psize - 1) // 8)

            if val.type == "BYTE":
                #log.debug(f"[_lookupEprom] A {val}")
                v = self._readEPROMSettingsPageIndex(page, pos, size)
                #log.debug(f"[_lookupEprom] B {v}")
                if val.psize > 8:
                    myvalue = v
                elif val.psize == 8:
                    myvalue = v[0]
                else:
                    mask = (1 << val.psize) - 1
                    offset = val.pbitoff | 0
                    myvalue = str((v[0] >> offset) & mask)

            elif val.type == "PHONE":
                for j in range(0, size):
                    nr = self._readEPROMSettingsPageIndex(page, pos + j, 1)
                    if nr[0] != 0xFF:
                        myvalue = myvalue + "".join("%02x" % b for b in nr)
            elif val.type == "TIME":
                t = self._readEPROMSettingsPageIndex(page, pos, size)
                myvalue = "".join("%02d:" % b for b in t)[:-1]  # miss the last character off, which will be a colon :
            elif val.type == "CODE" or val.type == "ACCOUNT":
                nr = self._readEPROMSettingsPageIndex(page, pos, size)
                myvalue = "".join("%02x" % b for b in nr).upper()
                myvalue = myvalue.replace("FF", ".")
            elif val.type == "STRING" or val.type == "DATE":
                for j in range(0, size):
                    nr = self._readEPROMSettingsPageIndex(page, pos + j, 1)
                    if nr[0] != 0xFF:
                        myvalue = myvalue + chr(nr[0])
            else:
                myvalue = "Not Set"

            if len(val.values) > 0:
                if isinstance(myvalue, int) and str(myvalue) in val.values:
                    retval.append(val.values[str(myvalue)])
                elif isinstance(myvalue, str) and myvalue in val.values:
                    retval.append(val.values[myvalue])
                elif isinstance(myvalue, list):
                    for v in myvalue:
                        if v in val.values:
                            retval.append(val.values[v])
            else:
                retval.append(myvalue)

        return retval

    def _lookupEpromSingle(self, key):
        v = self._lookupEprom(DecodePanelSettings[key])
        if len(v) >= 1:
            return v[0]
        return None

    def createSensor(self, i, zoneInfo, sensor_type, motiondelaytime = None, part = [1]) -> AlSensorType:
        zoneName = "Unknown"
        if i in self.ZoneNames:
            zoneName = pmZoneName_t[self.ZoneNames[i]]
        sensorType = AlSensorType.UNKNOWN
        sensorModel = "Model Unknown"

        if self.PowerMaster is not None and self.PowerMaster: # PowerMaster models
            if sensor_type in pmZoneSensorMaster_t:
                sensorType = pmZoneSensorMaster_t[sensor_type].func
                sensorModel = pmZoneSensorMaster_t[sensor_type].name
                if motiondelaytime == 0xFFFF and (sensorType == AlSensorType.MOTION or sensorType == AlSensorType.CAMERA):
                    log.debug("[Process Settings] PowerMaster Sensor " + str(i) + " has no motion delay set (Sensor will only be useful when the panel is armed)")
            else:
                log.debug("[Process Settings] Found unknown sensor type " + hex(sensor_type))
        else:  #  PowerMax models
            tmpid = sensor_type & 0x0F
            #sensorType = "UNKNOWN " + str(tmpid)

            # User cybfox77 found that PIR sensors were returning the sensor type 'sensor_type' as 0xe5 and 0xd5, these would be decoded as Magnet sensors
            # This is a very specific workaround for that particular panel type and model number and we'll wait and see if other users have issues
            #          These issues could be either way, users with or without that panel/model getting wrong results
            #          [handle_msgtype3C] PanelType=4 : PowerMax Pro Part , Model=62   Powermaster False
            # User fguerzoni also has a similar problem
            #          User has multiple PIRs and the only difference is the date stamp
            #               PIRs model "NEXT MCW/K9 MCW" (8-3591-A20 v.01) are coming through as magnet as 0xd5
            #                     Date stamp 01/11 on the sensor seen as 'magnet'
            #                     Date stamp 05/10 on the sensor seen as 'motion'
            #          [handle_msgtype3C] PanelType=1 : PowerMax+ , Model=32 Powermaster False
            # Yet another user G4seb has an issue with sensor types being wrong
            #          [handle_msgtype3C] PanelType=4 : PowerMax Pro Part , Model=81   Powermaster False
            #                Sensor Types 0x96  0xC0 and 0xE5     I hope that E5 is a Motion as that is what it has been previously

            if sensor_type in pmZoneSensorMax_t:
                sensorType = pmZoneSensorMax_t[sensor_type].func
                sensorModel = pmZoneSensorMax_t[sensor_type].name
            elif tmpid in pmZoneSensorMaxGeneric_t:
                # if tmpid in pmZoneSensorMaxGeneric_t:
                sensorType = pmZoneSensorMaxGeneric_t[tmpid]
            else:
                log.debug("[Process Settings] Found unknown sensor type " + str(sensor_type))

        zoneType = zoneInfo & 0x0F
        zoneChime = (zoneInfo >> 4) & 0x03

        log.debug("[Process Settings]      Z{0:0>2} :  sensor_type={1:0>2}   zoneInfo={2:0>2}   ZTypeName={3}   Chime={4}   SensorType={5}   zoneName={6}".format(
               i+1, hex(sensor_type).upper(), hex(zoneInfo).upper(), pmZoneType_t["EN"][zoneType], pmZoneChime_t["EN"][zoneChime], sensorType, zoneName))

        if i in self.SensorList:
            # If we get EPROM data, assume it is all correct and override any existing settings (as they were assumptions)
            self.SensorList[i].stype = sensorType
            self.SensorList[i].sid = sensor_type
            self.SensorList[i].model = sensorModel
            self.SensorList[i].ztype = zoneType
            self.SensorList[i].ztypeName = pmZoneType_t[self.pmLang][zoneType]
            self.SensorList[i].zname = zoneName
            self.SensorList[i].zchime = pmZoneChime_t[self.pmLang][zoneChime]
            self.SensorList[i].partition = part
            self.SensorList[i].id = i + 1
            self.SensorList[i].motiondelaytime = motiondelaytime

        else:
            self.SensorList[i] = SensorDevice(stype = sensorType, sid = sensor_type, model = sensorModel, ztype = zoneType,
                         ztypeName = pmZoneType_t[self.pmLang][zoneType], zname = zoneName, zchime = pmZoneChime_t[self.pmLang][zoneChime],
                         partition = part, id=i+1, enrolled = True, motiondelaytime = motiondelaytime)
            if self.onNewSensorHandler is not None:
                self.onNewSensorHandler(self.SensorList[i])

        self.SensorList[i].enrolled = True
        self.SensorList[i].pushChange(AlSensorCondition.ENROLLED)
    
    def _processKeypadsAndSirens(self, pmPanelTypeNr) -> str:
        sirenCnt = pmPanelConfig_t["CFG_SIRENS"][pmPanelTypeNr]
        keypad1wCnt = pmPanelConfig_t["CFG_1WKEYPADS"][pmPanelTypeNr]
        keypad2wCnt = pmPanelConfig_t["CFG_2WKEYPADS"][pmPanelTypeNr]
        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process Devices (Sirens and Keypads)

        deviceStr = ""
        if self.PowerMaster is not None and self.PowerMaster: # PowerMaster models
            # Process keypad settings
            setting = self._lookupEprom(DecodePanelSettings["KeypadPMaster"])
            #setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_KEYPADS"])
            if len(setting) == keypad2wCnt:
                for i in range(0, keypad2wCnt):
                    if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0 or setting[i][3] != 0 or setting[i][4] != 0:
                        log.debug("[Process Settings] Found an enrolled PowerMaster keypad {0}".format(i))
                        deviceStr = "{0},K2{1:0>2}".format(deviceStr, i)
            else:
                log.debug(f"[Process Settings] Mismatch PowerMaster Keypad {len(setting)}   {keypad2wCnt}")

            # Process siren settings
            setting = self._lookupEprom(DecodePanelSettings["KeypadPMaster"])
            # setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_SIRENS"])
            if len(setting) == sirenCnt:
                for i in range(0, sirenCnt):
                    if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0 or setting[i][3] != 0 or setting[i][4] != 0:
                        log.debug("[Process Settings] Found an enrolled PowerMaster siren {0}".format(i))
                        deviceStr = "{0},S{1:0>2}".format(deviceStr, i)
            else:
                log.debug(f"[Process Settings] Mismatch PowerMaster Sirens {len(setting)}   {sirenCnt}")
        else:
            # Process keypad settings
            setting = self._lookupEprom(DecodePanelSettings["Keypad1PMax"])
            #setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_1WKEYPAD"])
            if len(setting) == keypad1wCnt:
                for i in range(0, keypad1wCnt):
                    if setting[i][0] != 0 or setting[i][1] != 0:
                        log.debug("[Process Settings] Found an enrolled PowerMax 1-way keypad {0}".format(i))
                        deviceStr = "{0},K1{1:0>2}".format(deviceStr, i)
            else:
                log.debug(f"[Process Settings] Mismatch PowerMax 1-way keypad {len(setting)}   {keypad1wCnt}")

            setting = self._lookupEprom(DecodePanelSettings["Keypad2PMax"])
            #setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_2WKEYPAD"])
            if len(setting) == keypad2wCnt:
                for i in range(0, keypad2wCnt):
                    if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0:
                        log.debug("[Process Settings] Found an enrolled PowerMax 2-way keypad {0}".format(i))
                        deviceStr = "{0},K2{1:0>2}".format(deviceStr, i)
            else:
                log.debug(f"[Process Settings] Mismatch PowerMax 2-way keypad {len(setting)}   {keypad2wCnt}")

            # Process siren settings
            setting = self._lookupEprom(DecodePanelSettings["SirensPMax"])
            # setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_SIRENS"])
            if len(setting) == sirenCnt:
                for i in range(0, sirenCnt):
                    if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0:
                        log.debug("[Process Settings] Found a PowerMax siren {0}".format(i))
                        deviceStr = "{0},S{1:0>2}".format(deviceStr, i)
            else:
                log.debug(f"[Process Settings] Mismatch PowerMax Sirens {len(setting)}   {sirenCnt}")
        
        return deviceStr[1:]
    
    def logEEPROMData(self, addToLog):
        # If val.show is True but addToLog is False then:
        #      Add the "True" values to the self.Panelstatus
        # If val.show is True and addToLog is True then:
        #      Add all (either PowerMax / PowerMaster) values to the self.Panelstatus and the log file
        for key in DecodePanelSettings:
            val = DecodePanelSettings[key]
            if val.show:
                result = self._lookupEprom(val)
                if result is not None:
                    if type(DecodePanelSettings[key].name) is str and len(result) == 1:
                        if isinstance(result[0], (bytes, bytearray)):
                            tmpdata = self._toString(result[0])
                            if addToLog:
                                log.debug( "[Process Settings]      {0:<18}  {1:<40}  {2}".format(key, DecodePanelSettings[key].name, tmpdata))
                            self.PanelStatus[DecodePanelSettings[key].name] = tmpdata
                        else:
                            if addToLog:
                                log.debug( "[Process Settings]      {0:<18}  {1:<40}  {2}".format(key, DecodePanelSettings[key].name, result[0]))
                            self.PanelStatus[DecodePanelSettings[key].name] = result[0]
                    
                    elif type(DecodePanelSettings[key].name) is list and len(result) == len(DecodePanelSettings[key].name):
                        for i in range(0, len(result)):
                            if isinstance(result[0], (bytes, bytearray)):
                                tmpdata = self._toString(result[i])
                                if addToLog:
                                    log.debug( "[Process Settings]      {0:<18}  {1:<40}  {2}".format(key, DecodePanelSettings[key].name[i], tmpdata))
                                self.PanelStatus[DecodePanelSettings[key].name[i]] = tmpdata
                            else:
                                if addToLog:
                                    log.debug( "[Process Settings]      {0:<18}  {1:<40}  {2}".format(key, DecodePanelSettings[key].name[i], result[i]))
                                self.PanelStatus[DecodePanelSettings[key].name[i]] = result[i]
                    
                    elif len(result) > 1 and type(DecodePanelSettings[key].name) is str:
                        tmpdata = ""
                        for i in range(0, len(result)):
                            if isinstance(result[0], (bytes, bytearray)):
                                tmpdata = tmpdata + self._toString(result[i]) + ", "
                            else:
                                tmpdata = tmpdata + str(result[i]) + ", "
                        # there's at least 2 so this will not exception
                        tmpdata = tmpdata[:-2]
                        if addToLog:
                            log.debug( "[Process Settings]      {0:<18}  {1:<40}  {2}".format(key, DecodePanelSettings[key].name, tmpdata))
                        self.PanelStatus[DecodePanelSettings[key].name] = tmpdata
                    
                    else:
                        log.debug( "[Process Settings]   ************************** NOTHING DONE ************************     {0:<18}  {1}  {2}".format(key, DecodePanelSettings[key].name, result))

    # _processEPROMSettings
    #    Decode the EEPROM and the various settings to determine
    #       The general state of the panel
    #       The zones and the sensors
    #       The X10 devices
    #       The phone numbers
    #       The user pin codes
    def _processEPROMSettings(self):
        """Process Settings from the downloaded EPROM data from the panel"""
        log.debug("[Process Settings] Process Settings from EPROM")

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Panel type and serial number
        #     This checks whether the EEPROM settings have been downloaded OK
        pmPanelTypeNr = self._lookupEpromSingle("panelSerialCode")    
        if pmPanelTypeNr is not None and 1 <= pmPanelTypeNr <= 16:
            log.debug("[Process Settings] old pmPanelTypeNr {0} ({1})    model {2}".format(pmPanelTypeNr, self.PanelType, self.PanelModel))
            self.PanelModel = pmPanelType_t[pmPanelTypeNr] if pmPanelTypeNr in pmPanelType_t else "UNKNOWN"   # INTERFACE : PanelType set to model
            self.PanelType = pmPanelTypeNr
            self.PowerMaster = self.PanelType >= 7
            log.debug("[Process Settings] new pmPanelTypeNr {0} ({1})    model {2}".format(pmPanelTypeNr, self.PanelType, self.PanelModel))
        elif pmPanelTypeNr is not None and pmPanelTypeNr == 0:
            # Then its a Basic PowerMax wih Download Capabilities only, assume the latter
            log.error(f"[Process Settings] Lookup of panel type reveals that this seems to be a PowerMax Panel and supports EEPROM Download only with no capability, going to Standard Mode")
            self._gotoStandardMode()
            return
        else:
            # Somekind of error or the EEPROM hasn't downloaded
            log.error(f"[Process Settings] Lookup of panel type string and model from the EEPROM failed, assuming EEPROM download failed {pmPanelTypeNr=}, going to Standard Mode")
            self._gotoStandardMode()
            return

        # self._dumpEPROMSettings()

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Need the panel type to be valid so we can decode some of the remaining downloaded data correctly
        if self.PanelType is not None and self.PanelType in pmPanelType_t:

            if self.pmDownloadComplete:
                log.debug("[Process Settings] Processing settings information")

                # List of door/window sensors
                doorZoneStr = ""
                # List of motion sensors
                motionZoneStr = ""
                # List of smoke sensors
                smokeZoneStr = ""
                # List of other sensors
                otherZoneStr = ""

                # log.debug("[Process Settings] Panel Type Number " + str(pmPanelTypeNr) + "    serial string " + self._toString(panelSerialType))
                zoneCnt = pmPanelConfig_t["CFG_WIRELESS"][pmPanelTypeNr] + pmPanelConfig_t["CFG_WIRED"][pmPanelTypeNr]
                #dummy_customCnt = pmPanelConfig_t["CFG_ZONECUSTOM"][pmPanelTypeNr]
                userCnt = pmPanelConfig_t["CFG_USERCODES"][pmPanelTypeNr]
                partitionCnt = pmPanelConfig_t["CFG_PARTITIONS"][pmPanelTypeNr]

                self.pmPincode_t = [convertByteArray("00 00")] * userCnt  # allow maximum of userCnt user pin codes

                devices = ""

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process panel type and serial

                pmPanelTypeCodeStr = self._lookupEpromSingle("panelTypeCode")
                idx = "{0:0>2}{1:0>2}".format(hex(pmPanelTypeNr).upper()[2:], hex(int(pmPanelTypeCodeStr)).upper()[2:])
                pmPanelName = pmPanelName_t[idx] if idx in pmPanelName_t else "Unknown"

                log.debug("[Process Settings] Processing settings - panel code index {0}".format(idx))

                #  INTERFACE : Add this param to the status panel first
                self.PanelStatus["Panel Name"] = pmPanelName

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process Panel Settings to display in the user interface
                self.logEEPROMData(Dumpy)

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process alarm settings
                #log.debug("panic {0}   bypass {1}".format(self._lookupEpromSingle("panicAlarm"), self._lookupEpromSingle("bypass") ))
                self.pmPanicAlarmSilent = self._lookupEpromSingle("panicAlarm") == "Silent Panic"    # special
                self.pmBypassOff = self._lookupEpromSingle("bypass") == "No Bypass"                  # special   '2':"Manual Bypass", '0':"No Bypass", '1':"Force Arm"}

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process user pin codes
                if self.PowerMaster is not None and self.PowerMaster:
                    #setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_PINCODES"])
                    uc = self._lookupEprom(DecodePanelSettings["userCodeMaster"])
                else:
                    #setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_PINCODES"])
                    uc = self._lookupEprom(DecodePanelSettings["userCodeMax"])

                # DON'T SAVE THE USER CODES TO THE LOG
                #log.debug("[Process Settings] User Codes:")
                if len(uc) == userCnt:
                    self.pmPincode_t = uc
                    self.pmGotUserCode = True
                    #for i in range(0, userCnt):
                    #    log.debug(f"[Process Settings]      User {i} has code {self._toString(uc[i])}")
                else:
                    log.debug(f"[Process Settings]  User code count is different {userCnt} != {len(uc)}")

                #log.debug(f"[Process Settings]    Installer Code {self._toString(self._lookupEpromSingle('installerCode'))}")
                #log.debug(f"[Process Settings]    Master DL Code {self._toString(self._lookupEpromSingle('masterDlCode'))}")
                #if self.PowerMaster is not None and self.PowerMaster:
                #    log.debug(f"[Process Settings]    Master Code {self._toString(self._lookupEpromSingle('masterCode'))}")
                #    log.debug(f"[Process Settings]    Installer DL Code {self._toString(self._lookupEpromSingle('instalDlCode'))}")

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Store partition info & check if partitions are on
                partition = None
                if partitionCnt > 1:  # Could the panel have more than 1 partition?
                    # If that panel type can have more than 1 partition, then check to see if the panel has defined more than 1
                    partition = self._lookupEprom(DecodePanelSettings["PartitionData"])
                    #partition = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_PARTITIONS"])
                    if partition is None or partition[0] == 255:
                        partitionCnt = 1
                    #else:
                    #    log.debug("[Process Settings] Partition settings " + self._toString(partition))

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process zone settings
                zoneNames = bytearray()
                #settingMr = bytearray()
                pmaster_zone_ext_data = [ ]

                if self.PowerMaster is not None and self.PowerMaster:
                    #zoneNames = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_ZONENAMES"])
                    zoneNames = self._lookupEprom(DecodePanelSettings["ZoneNamePMaster"])
                    #settingMr = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_ZONES"])
                    
                    # This is 640 bytes.
                    # It is 64 zones, each is 10 bytes
                    #    5 = Sensor Type
                    pmaster_zone_ext_data = self._lookupEprom(DecodePanelSettings["ZoneExtPMaster"])

                    #for i in range(0, 640):
                    #    if settingMr[i] != pmaster_zone_ext_data[i//10][i%10]:
                    #        log.debug("[Process Settings] Zone Extended Data is different BOOOOOOOOOOOOOOO {0} {1}".format(settingMr[i], pmaster_zone_ext_data[i//10][i%10]))
                    
                    # log.debug("[Process Settings] MSG_DL_MR_ZONES Buffer " + self._toString(settingMr))
                    # motiondelayarray = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_ZONEDELAY"])
                    motiondelayarray = self._lookupEprom(DecodePanelSettings["ZoneDelay"])
                    # alarmledarray = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_ALARMLED"])
                    # log.debug("[Process Settings] alarmled = " + self._toString(alarmledarray))
                else:
                    zonestrings = self._lookupEprom(DecodePanelSettings["ZoneStrPMax"])
                    for i in range(0, len(zonestrings)):
                        pmZoneName_t[i] = zonestrings[i].strip()
                
                    # zoneNames = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_ZONENAMES"])
                    zoneNames = self._lookupEprom(DecodePanelSettings["ZoneNamePMax"])
                    zonesignalstrength = self._lookupEprom(DecodePanelSettings["ZoneSignalPMax"])
                    log.debug("[Process Settings] ZoneSignal " + self._toString(zonesignalstrength))
                    
                # This is 120 bytes. These 2 get the same data block but they are structured differently
                # It is 30 zones, each is 4 bytes
                #    2 = Sensor Type
                #    3 = Zone Info
                pmax_zone_data   = self._lookupEprom(DecodePanelSettings["ZoneDataPMax"])     
                # This is 64 bytes. These 2 get the same data block but they are structured differently
                # It is 64 zones, each is 1 byte, represents Zone Info
                pmaster_zone_data = self._lookupEprom(DecodePanelSettings["ZoneDataPMaster"])  # This is 64 bytes
                
                #setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_ZONES"])
                
                ################# TEST #######################
                #for i in range(0, 120):
                #    if setting[i] != pmax_zone_data[i//4][i%4]:
                #        log.debug("[Process Settings] Zone pmax_zone_data   Data is different BOOOOOOOOOOOOOOO {0} {1}".format(setting[i], pmax_zone_data[i//4][i%4]))

                #for i in range(0, 64):
                #    if setting[i] != pmaster_zone_data[i]:
                #        log.debug("[Process Settings] Zone pmaster_zone_data Data is different BOOOOOOOOOOOOOOO {0} {1}".format(setting[i], pmaster_zone_data[i]))

                ################# TEST #######################

                log.debug("[Process Settings] Zones Names Buffer :  {0}".format(self._toString(zoneNames)))

                if len(pmaster_zone_data) > 0 and len(zoneNames) > 0:
                    log.debug("[Process Settings] Zones:    len settings {0}     len zoneNames {1}    zoneCnt {2}".format(len(pmax_zone_data), len(zoneNames), zoneCnt))
                    for i in range(0, zoneCnt):

                        self.ZoneNames[i] = zoneNames[i] & 0x1F
                        self.ZoneTypes[i] = int(pmaster_zone_data[i]) if self.PowerMaster is not None and self.PowerMaster else int(pmax_zone_data[i][3])
                        zoneEnrolled = int(pmaster_zone_ext_data[i][5]) != 0 if self.PowerMaster is not None and self.PowerMaster else int(pmax_zone_data[i][2]) != 0

                        if zoneEnrolled:
                            sensor_type = int(pmaster_zone_ext_data[i][5]) if self.PowerMaster is not None and self.PowerMaster else int(pmax_zone_data[i][2])
                            motiondel = motiondelayarray[i][0] + (256 * motiondelayarray[i][1]) if self.PowerMaster is not None and self.PowerMaster else None

                            #part = []
                            #if partitionCnt > 1 and partition is not None:
                            #    for j in range(0, partitionCnt):
                            #        if (partition[0x11 + i] & (1 << j)) > 0:
                            #            # log.debug("[Process Settings]     Adding to partition list - ref {0}  Z{1:0>2}   Partition {2}".format(i, i+1, j+1))
                            #            part.append(j + 1)
                            #else:
                            #    part = [1]
                            part = [1]
                            
                            sensorType = self.createSensor( i, zoneInfo = self.ZoneTypes[i], sensor_type = sensor_type, motiondelaytime = motiondel, part = part )

                            if i in self.SensorList:
                                if sensorType == AlSensorType.MAGNET or sensorType == AlSensorType.WIRED:
                                    doorZoneStr = "{0},Z{1:0>2}".format(doorZoneStr, i + 1)
                                elif sensorType == AlSensorType.MOTION or sensorType == AlSensorType.CAMERA:
                                    motionZoneStr = "{0},Z{1:0>2}".format(motionZoneStr, i + 1)
                                elif sensorType == AlSensorType.SMOKE or sensorType == AlSensorType.GAS:
                                    smokeZoneStr = "{0},Z{1:0>2}".format(smokeZoneStr, i + 1)
                                else:
                                    otherZoneStr = "{0},Z{1:0>2}".format(otherZoneStr, i + 1)

                        else:
                            if i in self.SensorList:
                                log.debug("[Process Settings]       Removing sensor {0} as it is not enrolled".format(i+1))
                                del self.SensorList[i]
                                # self.SensorList[i] = None # remove zone if needed

                self.sensorsCreated = True

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process PGM/X10 settings

                s = []
                s.append(self._lookupEprom(DecodePanelSettings["x10ByArmAway"]))
                s.append(self._lookupEprom(DecodePanelSettings["x10ByArmHome"]))
                s.append(self._lookupEprom(DecodePanelSettings["x10ByDisarm"]))
                s.append(self._lookupEprom(DecodePanelSettings["x10ByDelay"]))
                s.append(self._lookupEprom(DecodePanelSettings["x10ByMemory"]))
                s.append(self._lookupEprom(DecodePanelSettings["x10ByKeyfob"]))
                s.append(self._lookupEprom(DecodePanelSettings["x10ActZoneA"]))
                s.append(self._lookupEprom(DecodePanelSettings["x10ActZoneB"]))
                s.append(self._lookupEprom(DecodePanelSettings["x10ActZoneC"]))

                x10Names = self._lookupEprom(DecodePanelSettings["x10ZoneNames"])

                # setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_PGMX10"])
                # x10Names = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_X10NAMES"])
                
                for i in range(0, 16):
                    if i == 0 or x10Names[i - 1] != 0x1F:   # python should process left to right, so i == 0 gets evaluated first.
                        for j in range(0, 9):
                            if i not in self.SwitchList and s[j][i] != 'Disable':
                                x10Location = "PGM" if i == 0 else pmZoneName_t[x10Names[i - 1] & 0x1F]
                                x10Type = "OnOff Switch" if i == 0 else "Dimmer Switch"
                                self.SwitchList[i] = X10Device(type=x10Type, location=x10Location, id=i, enabled=True)
                                if self.onNewSwitchHandler is not None:
                                    self.onNewSwitchHandler(self.SwitchList[i])
                                break # break the j loop and continue to the next i, we shouldn't need this (i not in self.SwitchList) in the if test but kept just in case!

                log.debug("[Process Settings] Adding zone devices")

                self.PanelStatus["Door Zones"] = doorZoneStr[1:]
                self.PanelStatus["Motion Zones"] = motionZoneStr[1:]
                self.PanelStatus["Smoke Zones"] = smokeZoneStr[1:]
                self.PanelStatus["Other Zones"] = otherZoneStr[1:]
                self.PanelStatus["Devices"] = self._processKeypadsAndSirens(pmPanelTypeNr)

                # INTERFACE : Create Partitions in the interface
                # for i in range(1, partitionCnt+1): # TODO

                log.debug("[Process Settings] Ready for use")

            else:
                log.warning("[Process Settings] WARNING: Cannot process panel EPROM settings, download has not completed")

        elif pmPanelTypeNr is None or pmPanelTypeNr == 0xFF:
            log.warning("[Process Settings] WARNING: Cannot process panel EPROM settings, we're probably connected to the panel in standard mode")
        else:
            log.warning("[Process Settings] WARNING: Cannot process panel EPROM settings, the panel is too new new new {0}".format(self.PanelType))

        self._dumpSensorsToLogFile()

#        if self.PanelType == 13 or self.PanelType == 16:
#            log.debug("[Process Settings]         PowerMaster 360 (R), going to full powerlink and calling Restore")
#            self.pmPowerlinkMode = True
#            self.pmPowerlinkModePending = False
#            self.PanelMode = AlPanelMode.POWERLINK  # it is truly in powerlink now we are receiving powerlink alive messages from the panel
#            self._triggerRestoreStatus()
#            self._dumpSensorsToLogFile()
#        elif self.pmPowerlinkMode:
        if self.pmPowerlinkMode:
            self.PanelMode = AlPanelMode.POWERLINK
        elif self.pmDownloadComplete and self.pmGotUserCode:
            log.debug("[Process Settings] Entering Standard Plus Mode as we got the pin codes from the EEPROM")
            self.PanelMode = AlPanelMode.STANDARD_PLUS
        else:
            self.PanelMode = AlPanelMode.STANDARD
        self.PanelLastEventData = self.setLastEventData()
        self.sendPanelUpdate(AlCondition.PANEL_UPDATE)  # push through a panel update to the HA Frontend


    # This function handles a received message packet and processes it
    def _processReceivedPacket(self, packet):
        """Handle one raw incoming packet."""

        if self.suspendAllOperations:
            # log.debug('[Disconnection] Suspended. Sorry but all operations have been suspended, please recreate connection')
            return

        # log.debug("[_processReceivedPacket] Received Packet %s", self._toString(packet))
        
        # Check the current packet against the last packet to determine if they are the same
        if self.lastPacket is not None:
            if self.lastPacket == packet and packet[1] == 0xA5:  # only consider A5 packets for consecutive error
                self.lastPacketCounter = self.lastPacketCounter + 1
            else:
                self.lastPacketCounter = 0
        self.lastPacket = packet

        if self.lastPacketCounter == SAME_PACKET_ERROR:
            log.debug("[_processReceivedPacket] Had the same packet for " + str(SAME_PACKET_ERROR) + " times in a row : %s", self._toString(packet))
            # _performDisconnect
            self._performDisconnect(reason="samepacketerror", exc="Same Packet for {0} times in a row".format(SAME_PACKET_ERROR))
        # else:
        #    log.debug("[_processReceivedPacket] Parsing complete valid packet: %s", self._toString(packet))

        # Record all main variables to see if the message content changes any
        oldSirenActive = self.SirenActive
        oldPanelState = self.PanelState
        oldPanelMode = self.PanelMode
        oldPowerMaster = self.PowerMaster
        oldPanelReady = self.PanelReady
        oldPanelTrouble = self.PanelTroubleStatus
        oldPanelAlarm = self.PanelAlarmStatus
        oldPanelBypass = self.PanelBypass
        pushchange = False
        
        if self.PanelMode == AlPanelMode.PROBLEM:
            # A PROBLEM indicates that there has been a response timeout (either normal or trying to get to powerlink)
            # However, we have clearly received a packet so put the panel mode back to MONITOR_ONLY, Standard or StandardPlus and wait for a powerlink response from the panel
            if self.DisableAllCommands:
                log.debug("[Standard Mode] Entering MONITOR_ONLY Mode")
                self.PanelMode = AlPanelMode.MONITOR_ONLY
            elif self.pmDownloadComplete and not self.ForceStandardMode and self.pmGotUserCode:
                log.debug("[_processReceivedPacket] Had a response timeout PROBLEM but received a data packet so entering Standard Plus Mode")
                self.PanelMode = AlPanelMode.STANDARD_PLUS
                # We are back in to STANDARD_PLUS so set this and wait for a powerlink alive message from the panel
                #    This is the only way to recover powerlink as there should be no need to re-enroll
                self.pmPowerlinkModePending = True
            else:
                log.debug("[_processReceivedPacket] Had a response timeout PROBLEM but received a data packet and entering Standard Mode")
                self.PanelMode = AlPanelMode.STANDARD

        if len(packet) < 4:  # there must at least be a header, command, checksum and footer
            log.warning("[_processReceivedPacket] Received invalid packet structure, not processing it " + self._toString(packet))
        elif packet[1] == ACK_MESSAGE:  # ACK
            # remove the header and command bytes as the start. remove the footer and the checksum at the end
            self.handle_msgtype02(packet[2:-2])
        elif packet[1] == 0x06:  # Timeout
            self.handle_msgtype06(packet[2:-2])
        elif packet[1] == 0x07:  # No idea what this means
            self.handle_msgtype07(packet[2:-2])
        elif packet[1] == 0x08:  # Access Denied
            self.handle_msgtype08(packet[2:-2])
        elif packet[1] == 0x0B:  # Stopped   # LOOPBACK TEST, EXIT (0x0F) IS THE FIRST COMMAND SENT TO THE PANEL WHEN THIS INTEGRATION STARTS
            self.handle_msgtype0B(packet[2:-2])
        elif packet[1] == 0x0F:  # Exit
            self.handle_msgtype0F(packet[2:-2])
        elif packet[1] == 0x22:
            # Message from Powermax Panel when starting the download. Seems to be similar to a 3C message.
            log.warning("[_processReceivedPacket] WARNING: Message 0x22 is not decoded, are you using an old Powermax Panel?")
        elif packet[1] == 0x25:  # Download retry
            self.handle_msgtype25(packet[2:-2])
        elif packet[1] == 0x33:  # Settings send after a MSGV_START
            self.handle_msgtype33(packet[2:-2])
        elif packet[1] == 0x3C:  # Message when start the download
            self.handle_msgtype3C(packet[2:-2])
        elif packet[1] == 0x3F:  # Download information
            self.handle_msgtype3F(packet[2:-2])
        elif packet[1] == 0xA0:  # Event log
            self.handle_msgtypeA0(packet[2:-2])
            log.debug("[handle_msgtypeA0] Finished")
        elif packet[1] == 0xA3:  # Zone Names
            self.handle_msgtypeA3(packet[2:-2])
            pushchange = True
        elif packet[1] == 0xA5:  # General Event
            self.handle_msgtypeA5(packet[2:-2])
            disarmlist = [AlPanelStatus.DISARMED, AlPanelStatus.ARMING_HOME, AlPanelStatus.ARMING_AWAY]
            alarmlist = [AlPanelStatus.ENTRY_DELAY, AlPanelStatus.ARMED_HOME, AlPanelStatus.ARMED_AWAY]
            if not self.pmPowerlinkMode and oldSirenActive != self.SirenActive and self.SirenActive:
                # The siren has just been activated and is sounding.  We are not in Powerlink Mode.
                pass
            elif (oldPanelState in alarmlist and self.PanelState in disarmlist) or (oldPanelState in disarmlist and self.PanelState in alarmlist):
                # postpone sending the event to HA for up to 0.5 seconds to wait for an A7 message with detailed information about the change
                #   push through the change to the frontend, just do not create the event as well
                #   to do that reset all old variables so it doesnt trigger a AlCondition.PANEL_UPDATE
                #   do not reset the oldSirenActive just in case self.SirenActive is set to True
                oldPanelState = self.PanelState
                oldPanelMode = self.PanelMode
                oldPowerMaster = self.PowerMaster
                oldPanelReady = self.PanelReady
                oldPanelTrouble = self.PanelTroubleStatus
                oldPanelAlarm = self.PanelAlarmStatus
                oldPanelBypass = self.PanelBypass
                log.debug("[_processReceivedPacket] Diff on PanelState but not pushing through yet, setting timer to 2")
                self.PostponeEventTimer = 2
                # need to let it fallthrough just in case the siren is sounding
            #pushchange = True
        elif packet[1] == 0xA6:  # General Event
            self.handle_msgtypeA6(packet[2:-2])
            pushchange = True
        elif packet[1] == 0xA7:  # General Event
            self.handle_msgtypeA7(packet[2:-2])
            if self.PostponeEventTimer > 0:
                # stop the time sending it as we've received an A7 before the timer triggered
                self.PostponeEventTimer = 0
                log.debug("[_processReceivedPacket] processed A7 and timer set so setting timer to 0")
                # just set 2 of the variables to unknown so it triggers the sending of an AlCondition.PANEL_UPDATE further down
                oldPanelMode = AlPanelMode.UNKNOWN
                oldPanelState = AlPanelStatus.UNKNOWN
                # need to let it fallthrough just in case the siren is sounding
            #pushchange = True
        elif packet[1] == 0xAB and not self.ForceStandardMode:
            # PowerLink Event. Only process AB if not forced standard
            self.handle_msgtypeAB(packet[2:-2])
            #pushchange = True
        elif packet[1] == 0xAB:  # PowerLink Event. Only process AB if not forced standard
            log.debug(f"[_processReceivedPacket] Received AB Message but we are in {self.PanelMode.name} mode (so I'm ignoring the message), data: {self._toString(packet)}")
        elif packet[1] == 0xAC:  # X10 Names
            self.handle_msgtypeAC(packet[2:-2])
            pushchange = True
        elif packet[1] == 0xAD:  # No idea what this means, it might ...  send it just before transferring F4 video data ?????
            self.handle_msgtypeAD(packet[2:-2])
            pushchange = True
        elif packet[1] == 0xB0:  # PowerMaster Event
            if not self.pmDownloadMode:  # only process when not downloading EPROM
                self.handle_msgtypeB0(packet[2:-2])
                #pushchange = True
        elif packet[1] == REDIRECT_POWERLINK_DATA:  # Redirected Powerlink Data
            self.handle_msgtypeC0(packet[2:-2])
        elif packet[1] == 0xF4:  # F4 Message from a Powermaster, can't decode it yet but this will accept it and ignore it
            pushchange = self.handle_msgtypeF4(packet[2:-2])
        else:
            log.debug("[_processReceivedPacket] Unknown/Unhandled packet type " + self._toString(packet))

        self.PanelLastEventData = self.setLastEventData()

        if oldSirenActive != self.SirenActive and self.SirenActive:
            self.sendPanelUpdate(AlCondition.PANEL_UPDATE_ALARM_ACTIVE)  # push changes through to the host to get it to update, alarm is active!!!!!!!!!
        elif oldSirenActive != self.SirenActive or \
                oldPanelState != self.PanelState or \
                oldPanelMode != self.PanelMode or \
                oldPanelReady != self.PanelReady or \
                oldPanelTrouble != self.PanelTroubleStatus or \
                oldPanelAlarm != self.PanelAlarmStatus or \
                oldPanelBypass != self.PanelBypass:

            if oldSirenActive != self.SirenActive:
                log.debug("[_processReceivedPacket] Diff on SirenActive")
            if oldPanelAlarm != self.PanelAlarmStatus:
                log.debug("[_processReceivedPacket] Diff on PanelAlarmStatus")
            if oldPanelState != self.PanelState:
                log.debug("[_processReceivedPacket] Diff on PanelState")
            if oldPanelMode != self.PanelMode:
                log.debug("[_processReceivedPacket] Diff on PanelMode")
            if oldPanelReady != self.PanelReady:
                log.debug("[_processReceivedPacket] Diff on PanelReady")
            if oldPanelTrouble != self.PanelTroubleStatus:
                log.debug("[_processReceivedPacket] Diff on PanelTroubleStatus")
            if oldPanelBypass != self.PanelBypass:
                log.debug("[_processReceivedPacket] Diff on PanelBypass")

            self.sendPanelUpdate(AlCondition.PANEL_UPDATE)  # push through a panel update to the HA Frontend
        elif oldPowerMaster != self.PowerMaster or pushchange:
            self.sendPanelUpdate(AlCondition.PUSH_CHANGE)

    def setZoneName(self, key) -> bool:
        if key in self.SensorList and key in self.ZoneNames and self.SensorList[key].zname is None:     # if not already set
            log.debug("[setZoneName]                  Setting Zone Name {0}".format(self.ZoneNames[key]))
            self.SensorList[key].zname = pmZoneName_t[self.ZoneNames[key]]
            return True
        return False

    def setZoneType(self, key) -> bool:
        if key in self.SensorList and key in self.ZoneTypes and self.SensorList[key].ztypeName is None:     # if not already set
            log.debug("[setZoneType]                  Setting Zone Type {0}".format(self.ZoneTypes[key]))
            self.SensorList[key].ztype = self.ZoneTypes[key]
            self.SensorList[key].ztypeName = pmZoneType_t[self.pmLang][self.ZoneTypes[key]]
            return True
        return False

    def dynamicallyAddSensor(self, key, sid : int = 0, func : AlSensorType = AlSensorType.UNKNOWN, name : str = ""):
        new_one = False
        changed = False
        enrolled = False
        if key not in self.SensorList:
            new_one = True
            log.debug("[dynamicallyAddSensor]        Added new sensor, Key {0}".format(key))
            self.SensorList[key] = SensorDevice(id = key + 1)

        if not self.SensorList[key].enrolled:
            enrolled = True
            self.SensorList[key].enrolled = True
    
        if self.SensorList[key].sid == 0 and sid > 0:      # sid > 0 also means use name and func
            changed = True
            self.SensorList[key].sid = sid
            self.SensorList[key].stype = func
            self.SensorList[key].model = name

        if self.setZoneName(key):
            changed = True
            
        if self.setZoneType(key):
            changed = True

        if new_one:
            if self.onNewSensorHandler is not None:
                self.onNewSensorHandler(self.SensorList[key])
        elif enrolled:
            self.SensorList[key].pushChange(AlSensorCondition.ENROLLED)
        elif changed:
            self.SensorList[key].pushChange(AlSensorCondition.STATE)


    def handle_msgtype02(self, data):  # ACK
        """ Handle Acknowledges from the panel """
        # Normal acknowledges have msgtype 0x02 but no data, when in powerlink the panel also sends data byte 0x43
        #    I have not found this on the internet, this is my hypothesis
        #log.debug("[handle_msgtype02] Ack Received  data = {0}".format(self._toString(data)))
        if not self.pmPowerlinkMode and len(data) > 0:
            if data[0] == 0x43:  # Received a powerlink acknowledge
                #log.debug("[handle_msgtype02]    Received a powerlink acknowledge, I am in {0} mode".format(self.PanelMode.name))
                if self.allowAckToTriggerRestore:
                    log.debug("[handle_msgtype02]        and sending MSG_RESTORE")
                    self._sendCommand("MSG_RESTORE")
                    self.allowAckToTriggerRestore = False

    def _delayDownload(self):
        self.pmDownloadMode = False
        self.pmDownloadComplete = False
        if self.ForceStandardMode:
            self.GiveupTryingDownload = True
        elif self.DisableAllCommands:
            self.PanelMode = AlPanelMode.MONITOR_ONLY
            self.GiveupTryingDownload = True
        else:
            self.GiveupTryingDownload = False
            # exit download mode and try again in DOWNLOAD_RETRY_DELAY seconds
            self._sendCommand("MSG_STOP")
            self._sendCommand("MSG_EXIT")
            self._triggerRestoreStatus()
            # Assume that we are not in Powerlink as we haven't completed download yet.
            self.PanelMode = AlPanelMode.STANDARD

    def handle_msgtype06(self, data):
        """MsgType=06 - Time out
        Timeout message from the PM, most likely we are/were in download mode"""
        log.debug("[handle_msgtype06] Timeout Received  data {0}".format(self._toString(data)))

        # Clear the expected response to ensure that pending messages are sent
        self.pmExpectedResponse = []

        if self.pmDownloadMode:
            self._delayDownload()
            log.debug("[handle_msgtype06] Timeout Received - Going to Standard Mode and going to try download again soon")
        else:
            self._sendAck()

    def handle_msgtype07(self, data):
        """MsgType=07 - No idea what this means"""
        log.debug("[handle_msgtype07] No idea what this message means, data = {0}".format(self._toString(data)))
        # Clear the expected response to ensure that pending messages are sent
        self.pmExpectedResponse = []
        # Assume that we need to send an ack
        if not self.pmDownloadMode:
            self._sendAck()

    def handle_msgtype08(self, data):
        log.debug("[handle_msgtype08] Access Denied  len {0} data {1}".format(len(data), self._toString(data)))

        if self._getLastSentMessage() is not None:
            lastCommandData = self._getLastSentMessage().command.data
            log.debug("[handle_msgtype08]                last command {0}".format(self._toString(lastCommandData)))
            self._reset_watchdog_timeout()
            if lastCommandData is not None:
                self.pmExpectedResponse = []  ## really we should look at the response from the last command and only remove the appropriate responses from this list
                if lastCommandData[0] != 0xAB and lastCommandData[0] & 0xA0 == 0xA0:  # this will match A0, A1, A2, A3 etc but not 0xAB
                    log.debug("[handle_msgtype08] Attempt to send a command message to the panel that has been denied, wrong pin code used")
                    # INTERFACE : tell user that wrong pin has been used
                    self.sendPanelUpdate(AlCondition.PIN_REJECTED)  # push changes through to the host, the pin has been rejected
                elif lastCommandData[0] == 0x24:
                    log.debug("[handle_msgtype08] Got an Access Denied and we have sent a Download command to the Panel")
                    self.pmDownloadMode = False
                    self.doneAutoEnroll = False
                    if self.PanelType is not None:  # By the time EPROM download is complete, this should be set but just check to make sure
                        if self.PanelType >= 3:     # Only attempt to auto enroll powerlink for newer panels. Older panels need the user to manually enroll, we should be in Standard Plus by now.
                            log.debug("[handle_msgtype08]                   Try to auto enroll (panel type {0})".format(self.PanelType))
                            self._sendMsgENROLL(True)  # Auto enroll, trigger download.  The panel type indicates that it can auto enroll
                    elif self.AutoEnroll:
                        log.debug("[handle_msgtype08]                   Auto enroll (panel type unknown but user settings say to autoenroll)")
                        self._sendMsgENROLL(True)  #  Auto enroll, retrigger download
                elif lastCommandData[0] != 0xAB and lastCommandData[0] != 0x0B:  # Powerlink command and the Stop Command
                    log.debug("[handle_msgtype08] Attempt to send a command message to the panel that has been rejected")
                    self.sendPanelUpdate(AlCondition.COMMAND_REJECTED)  # push changes through to the host, something has been rejected (other than the pin)


    def handle_msgtype0B(self, data):  # LOOPBACK TEST SUCCESS, STOP COMMAND (0x0B) IS THE FIRST COMMAND SENT TO THE PANEL WHEN THIS INTEGRATION STARTS
        """ Handle STOP from the panel """
        log.debug("[handle_msgtype0B] Stop    data is {0}".format(self._toString(data)))
        self.loopbackTest = True
        self.loopbackCounter = self.loopbackCounter + 1
        log.warning("[handle_msgtype0B] LOOPBACK TEST SUCCESS, Counter is {0}".format(self.loopbackCounter))

    def handle_msgtype0F(self, data):  # EXIT
        """ Handle STOP from the panel """
        log.debug("[handle_msgtype0F] Exit    data is {0}".format(self._toString(data)))
        # This is sent by the panel during download to tell us to stop the download
        if self.pmDownloadMode:
            self._delayDownload()
            log.debug("[handle_msgtype0F] Exit Received During Download - Going to Standard Mode and going to try download again soon")
        else:
            self._sendAck()

    def handle_msgtype25(self, data):  # Download retry
        """ MsgType=25 - Download retry. Unit is not ready to enter download mode """
        # Format: <MsgType> <?> <?> <delay in sec>
        iDelay = data[2]
        log.debug("[handle_msgtype25] Download Retry, have to wait {0} seconds     data is {1}".format(iDelay, self._toString(data)))
        self._delayDownload()

    def handle_msgtype33(self, data):
        """MsgType=33 - Settings
        Message sent after a MSG_START. We will store the information in an internal array/collection"""

        if len(data) != 10:
            log.debug("[handle_msgtype33] ERROR: MSGTYPE=0x33 Expected len=14, Received={0}".format(len(data)))
            log.debug("[handle_msgtype33]                            " + self._toString(data))
            return

        # Data Format is: <index> <page> <8 data bytes>
        # Extract Page and Index information
        iIndex = data[0]
        iPage = data[1]

        # log.debug("[handle_msgtype33] Getting Data " + self._toString(data) + "   page " + hex(iPage) + "    index " + hex(iIndex))

        # Write to memory map structure, but remove the first 2 bytes from the data
        self._writeEPROMSettings(iPage, iIndex, data[2:])

    def handle_msgtype3C(self, data):  # Panel Info Messsage when start the download
        """The panel information is in 4 & 5
        5=PanelType e.g. PowerMax, PowerMaster
        4=Sub model type of the panel - just informational, not used
        """
        log.debug("[handle_msgtype3C] Packet = {0}".format(self._toString(data)))
        
        firsttime = self.PowerMaster is None
        
        self.ModelType = data[4]
        self.PanelType = data[5]

        self.PowerMaster = (self.PanelType >= 7)
        self.PanelModel = pmPanelType_t[self.PanelType] if self.PanelType in pmPanelType_t else "UNKNOWN"   # INTERFACE : PanelType set to model
        self.pmInitSupportedByPanel = (self.PanelType >= 4)

        log.debug("[handle_msgtype3C] PanelType={0} : {2} , Model={1}   Powermaster {3}".format(self.PanelType, self.ModelType, self.PanelModel, self.PowerMaster))

        if self.PowerMaster and self.ForceStandardMode:
            # log.debug("[handle_msgtype3C] Queueing Powermaster Zone Names, Types and try to get all sensors")
            self.updateSensorNamesAndTypes(firsttime)
        self.pmGotPanelDetails = True

        if not self.ForceStandardMode:
            # We got a first response, now we can Download the panel EPROM settings
            interval = self._getUTCTimeFunction() - self.lastSendOfDownloadEprom
            td = timedelta(seconds=DOWNLOAD_RETRY_DELAY)  # prevent multiple requests for the EEPROM panel settings, at least DOWNLOAD_RETRY_DELAY seconds
            log.debug("[handle_msgtype3C] interval={0}  td={1}   self.lastSendOfDownloadEprom(UTC)={2}    timenow(UTC)={3}".format(interval, td, self.lastSendOfDownloadEprom, self._getUTCTimeFunction()))
            if interval > td:
                self.lastSendOfDownloadEprom = self._getUTCTimeFunction()
                self._readPanelSettings(self.PowerMaster)
            elif self.triggeredDownload and not self.pmDownloadComplete and len(self.myDownloadList) > 0:
                # Download has already started and we've already started asking for the EEPROM Data
                # So lets ask for it all again, from the beginning
                #    On a particular panel we received a 3C and send the first download block request.
                #       We received nothing for 10 seconds and then another 3C from the panel.
                #       Originally we ignored this and then the panel sent a timeout 20 seconds later, so lets try resending the request
                log.debug("[handle_msgtype3C]          Asking for panel EPROM again")
                self.lastSendOfDownloadEprom = self._getUTCTimeFunction()
                self._readPanelSettings(self.PowerMaster)

    def handle_msgtype3F(self, data):
        """MsgType=3F - Download information
        Multiple 3F can follow each other, if we request more then &HFF bytes"""

        if self.ForceStandardMode:
            log.debug("[handle_msgtype3F] Received data but in Standard Mode so ignoring data")
            return

        # data format is normally: <index> <page> <length> <data ...>
        # If the <index> <page> = FF, then it is an additional PowerMaster MemoryMap
        iIndex = data[0]
        iPage = data[1]
        iLength = data[2]

        log.debug("[handle_msgtype3F] actual data block length=" + str(len(data)-3) + "   data content length=" + str(iLength))

        # PowerMaster 10 (Model 7) and PowerMaster 33 (Model 10) has a very specific problem with downloading the Panel EPROM and doesn't respond with the correct number of bytes
        # if self.PanelType is not None and self.ModelType is not None and ((self.PanelType == 7 and self.ModelType == 68) or (self.PanelType == 10 and self.ModelType == 71)):
        #    if iLength != len(data) - 3:
        #        log.debug("[handle_msgtype3F] Not checking data length as it could be incorrect.  We requested {0} and received {1}".format(iLength, len(data) - 3))
        #        log.debug("[handle_msgtype3F]                            " + self._toString(data))
        #    # Write to memory map structure, but remove the first 3 bytes (index/page/length) from the data
        #    self._writeEPROMSettings(iPage, iIndex, data[3:])

        if self.pmDownloadRetryCount < DOWNLOAD_RETRY_COUNT and iLength != len(data) - 3:  # 3 because -->   index & page & length
            log.warning("[handle_msgtype3F] Invalid data block length, Received: {0}, Expected: {1}    Adding page {2} Index {3} to the end of the list".format(len(data)-3, iLength, iPage, iIndex))
            log.warning("[handle_msgtype3F]                            " + self._toString(data))
            # Add it back on to the end to re-download it
            repeatDownloadCommand = bytearray(4)
            repeatDownloadCommand[0] = iIndex
            repeatDownloadCommand[1] = iPage
            repeatDownloadCommand[2] = iLength
            repeatDownloadCommand[3] = 0
            self.myDownloadList.append(repeatDownloadCommand)
            # Increment counter
            self.pmDownloadRetryCount = self.pmDownloadRetryCount + 1

        # Write to memory map structure, but remove the first 3 bytes (index/page/length) from the data
        self._writeEPROMSettings(iPage, iIndex, data[3:])

        if len(self.myDownloadList) > 0:
            self._sendCommand("MSG_DL", options=[ [1, self.myDownloadList.pop(0)] ])  # Read the next block of EPROM data
        else:
            # This is the message to tell us that the panel has finished download mode, so we too should stop download mode
            self.pmDownloadMode = False
            self.pmDownloadComplete = True
            if self._getLastSentMessage() is not None:
                lastCommandData = self._getLastSentMessage().command.data
                log.debug("[handle_msgtype3F]                last command {0}".format(self._toString(lastCommandData)))
                if lastCommandData is not None:
                    if lastCommandData[0] == 0x3E:  # Download Data
                        log.debug("[handle_msgtype3F] We're almost in powerlink mode *****************************************")
                        self.pmPowerlinkModePending = True
            else:
                log.debug("[handle_msgtype3F]                no last command")

            if self.AutoSyncTime:  # should we sync time between the HA and the Alarm Panel
                t = self._getTimeFunction()
                if t.year > 2020:
                    log.debug("[handle_msgtype3F]    Local time is {0}".format(t))
                    year = t.year - 2000
                    values = [t.second, t.minute, t.hour, t.day, t.month, year]
                    timePdu = bytearray(values)
                    log.debug("[handle_msgtype3F]        Setting Time " + self._toString(timePdu))
                    self._sendCommand("MSG_SETTIME", options=[ [3, timePdu] ])
                else:
                    log.debug("[handle_msgtype3F] Please correct your local time.")

            self._sendCommand("MSG_EXIT")  # Exit download mode
            # We received a download exit message, restart timer
            self._reset_watchdog_timeout()
            self._processEPROMSettings()
            self.sendPanelUpdate(AlCondition.DOWNLOAD_SUCCESS)   # download completed successfully

    def _makeInt(self, data) -> int:
        if len(data) == 4:
            val = data[0]
            val = val + (0x100 * data[1])
            val = val + (0x10000 * data[2])
            val = val + (0x1000000 * data[3])
            return int(val)
        return 0

    def handle_msgtypeA0(self, data):
        """ MsgType=A0 - Event Log """
        log.debug("[handle_MsgTypeA0] Packet = {0}".format(self._toString(data)))
        # From my Powermaster30  [handle_MsgTypeA0] Packet = 5f 02 01 64 58 5c 58 d3 41 51 43

        eventNum = data[1]
        # Check for the first entry, it only contains the number of events
        if eventNum == 0x01:
            log.debug("[handle_msgtypeA0] Eventlog received")
            self.eventCount = data[0] - 1  ## the number of messages (including this one) minus 1
        else:
            iSec = data[2]
            iMin = data[3]
            iHour = data[4]
            iDay = data[5]
            iMonth = data[6]
            iYear = int(data[7]) + 2000

            iEventZone = data[8]
            iLogEvent = data[9]

            zoneStr = ""

            if self.PowerMaster is not None and self.PowerMaster: # PowerMaster models
                zoneStr = pmLogPowerMasterUser_t[self.pmLang][iEventZone] or "UNKNOWN"
                # extract the time as "epoch time" and convert to normal time
                hs = self._makeInt(data[3:7])
                # hs = data[3] + (data[4] * 256) + (data[5] * 65536) + (data[6] * 16777216)
                pmtime = datetime.fromtimestamp(hs)
                #log.debug("[handle_msgtypeA0]   Powermaster time {0} as hex {1} from epoch is {2}".format(hs, hex(hs), pmtime))
                iSec = pmtime.second
                iMin = pmtime.minute
                iHour = pmtime.hour
                iDay = pmtime.day
                iMonth = pmtime.month
                iYear = pmtime.year
            else:
                iEventZone = int(iEventZone & 0x7F)
                zoneStr = pmLogPowerMaxUser_t[self.pmLang][iEventZone] or "UNKNOWN"

            eventStr = pmLogEvent_t[self.pmLang][iLogEvent] or "UNKNOWN"

            idx = eventNum - 1

            # Create an event log array
            self.pmEventLogDictionary[idx] = AlLogPanelEvent()
            if pmPanelConfig_t["CFG_PARTITIONS"][self.PanelType] > 1:
                part = 0
                for i in range(1, 4):
                    part = (iSec % (2 * i) >= i) and i or part
                self.pmEventLogDictionary[idx].partition = (part == 0) and "Panel" or part
                self.pmEventLogDictionary[idx].time = "{0:0>2}:{1:0>2}:{2:0>2}".format(iHour, iMin, iSec)
            else:
                # This alarm panel only has a single partition so it must either be panel or partition 1
                self.pmEventLogDictionary[idx].partition = (iEventZone == 0) and "Panel" or "1"
                self.pmEventLogDictionary[idx].time = "{0:0>2}:{1:0>2}:{2:0>2}".format(iHour, iMin, iSec)

            self.pmEventLogDictionary[idx].date = "{0:0>2}/{1:0>2}/{2}".format(iDay, iMonth, iYear)
            self.pmEventLogDictionary[idx].zone = zoneStr
            self.pmEventLogDictionary[idx].event = eventStr
            self.pmEventLogDictionary[idx].total = self.eventCount
            self.pmEventLogDictionary[idx].current = idx
            # log.debug("[handle_msgtypeA0] Log Event {0}".format(self.pmEventLogDictionary[idx]))

            # Send the event log in to HA
            if self.onPanelLogHandler is not None:
                self.onPanelLogHandler(self.pmEventLogDictionary[idx])

            log.debug("[handle_msgtypeA0] Finished processing Log Event {0}".format(self.pmEventLogDictionary[idx]))

    def handle_msgtypeA3(self, data):
        """ MsgType=A3 - Zone Names """
        log.debug("[handle_MsgTypeA3] Packet = {0}".format(self._toString(data)))
        msgCnt = int(data[0])
        offset = 8 * (int(data[1]) - 1)
        log.debug("            Message Count is {0}   offset={1}     self.pmPowerlinkMode={2}".format( msgCnt, offset, self.pmPowerlinkMode ))
        for i in range(0, 8):
            # Save the Zone Name
            self.ZoneNames[offset+i] = int(data[2+i]) & 0x1F
            log.debug("                        Zone name for sensor {0} is {1} : {2}".format( offset+i+1, int(data[2+i]), self.ZoneNames[offset+i] ))
            if not self.pmPowerlinkMode and offset+i in self.SensorList:
                self.setZoneName(offset+i)
                self.SensorList[offset+i].pushChange(AlSensorCondition.RESET)

    def UpdateContactSensor(self, sensor, status = None, trigger = None):
        if sensor in self.SensorList:
            #log.debug("[UpdateContactSensor]   Sensor {0}   before".format(sensor))
            #self._dumpSensorsToLogFile()
            if trigger is not None and trigger:
                # If trigger is set then the caller is confident that it is a motion or camera sensor
                log.debug("[UpdateContactSensor]   Sensor {0}   triggered to True".format(sensor))
                self.SensorList[sensor].triggered = True
                self.SensorList[sensor].triggertime = self._getTimeFunction()
                self.SensorList[sensor].utctriggertime = self._getUTCTimeFunction()
                self.SensorList[sensor].pushChange(AlSensorCondition.STATE)
            elif self.SensorList[sensor].status != status:
                # The current setting is different
                if status:
                    log.debug("[UpdateContactSensor]   Sensor {0}   triggered to True".format(sensor))
                    self.SensorList[sensor].triggered = True
                    self.SensorList[sensor].triggertime = self._getTimeFunction()
                    self.SensorList[sensor].utctriggertime = self._getUTCTimeFunction()
                if self.SensorList[sensor].getSensorType() != AlSensorType.MOTION and self.SensorList[sensor].getSensorType() != AlSensorType.CAMERA:
                    # Not a motion or camera to set status
                    log.debug("[UpdateContactSensor]   Sensor {0}   status from {1} to {2}".format(sensor, self.SensorList[sensor].status, status))
                    self.SensorList[sensor].status = status
                    if not status:
                        self.SensorList[sensor].pushChange(AlSensorCondition.RESET)
                if status:
                    self.SensorList[sensor].pushChange(AlSensorCondition.STATE)
                    
            #log.debug("[UpdateContactSensor]   Sensor {0}   after".format(sensor))
            #self._dumpSensorsToLogFile()
        else:
            log.warning(f"[UpdateContactSensor]      Update for sensor {sensor} that does not exist")

    def ProcessPanelStateUpdate(self, sysStatus, sysFlags):
        sysStatus = sysStatus & 0x1F     # Mark-Mills with a PowerMax Complete Part, sometimes this has the 0x20 bit set and I'm not sure why
        slog = pmDetailedArmMode_t[sysStatus]
        sarm_detail = "Unknown"
        if 0 <= sysStatus < len(pmSysStatus_t[self.pmLang]):
            sarm_detail = pmSysStatus_t[self.pmLang][sysStatus]

        if sysStatus == 7 and self.pmDownloadComplete:  # sysStatus == 7 means panel "downloading"
            log.debug("[ProcessPanelStateUpdate]      Sending a STOP and EXIT as we seem to be still in the downloading state and it should have finished")
            self._sendCommand("MSG_STOP")
            self._sendCommand("MSG_EXIT")

        # -1  Not yet defined
        # 0   Disarmed (Also includes 0x0A "Home Bypass", 0x0B "Away Bypass", 0x0C "Ready", 0x0D "Not Ready" and 0x10 "Disarmed Instant")
        # 1   Home Exit Delay  or  Home Instant Exit Delay
        # 2   Away Exit Delay  or  Away Instant Exit Delay
        # 3   Entry Delay
        # 4   Armed Home  or  Home Bypass  or  Entry Delay Instant  or  Armed Home Instant
        # 5   Armed Away  or  Away Bypass  or  Armed Away Instant
        # 6   User Test   or  Programming  or  Installer
        # 7   Downloading

        if sysStatus in [0x03]:
            sarm = "Armed"
            self.PanelState = AlPanelStatus.ENTRY_DELAY  # Entry Delay
        elif sysStatus in [0x04, 0x0A, 0x13, 0x14]:
            sarm = "Armed"
            self.PanelState = AlPanelStatus.ARMED_HOME  # Armed Home
        elif sysStatus in [0x05, 0x0B, 0x15]:
            sarm = "Armed"
            self.PanelState = AlPanelStatus.ARMED_AWAY  # Armed Away
        elif sysStatus == 0x01:
            sarm = "Arming"
            self.PanelState = AlPanelStatus.ARMING_HOME  # Arming Home
        elif sysStatus == 0x11:
            sarm = "Arming"
            self.PanelState = AlPanelStatus.ARMING_HOME  # Arming Home Last 10 Seconds
        elif sysStatus == 0x02:
            sarm = "Arming"
            self.PanelState = AlPanelStatus.ARMING_AWAY  # Arming Away
        elif sysStatus == 0x12:
            sarm = "Arming"
            self.PanelState = AlPanelStatus.ARMING_AWAY  # Arming Away Last 10 Seconds
        elif sysStatus in [0x07]:
            sarm = "Disarmed"
            self.PanelState = AlPanelStatus.DOWNLOADING  # Downloading
        elif sysStatus in [0x06, 0x08, 0x09]:
            sarm = "Disarmed"
            self.PanelState = AlPanelStatus.SPECIAL  # Special ("User Test", "Programming", "Installer")
        elif sysStatus > 0x15:
            log.debug("[ProcessPanelStateUpdate]      Unknown state {0}, assuming Disarmed".format(sysStatus))
            sarm = "Disarmed"
            self.PanelState = AlPanelStatus.DISARMED  # Disarmed
        else:
            sarm = "Disarmed"
            self.PanelState = AlPanelStatus.DISARMED  # Disarmed

        if self.PanelMode == AlPanelMode.DOWNLOAD:
            self.PanelState = AlPanelStatus.DOWNLOADING  # Downloading

        #log.debug("[ProcessPanelStateUpdate]      log: {0}, arm: {1}".format(slog + "(" + sarm_detail + ")", sarm))

        self.PanelStatusText = sarm_detail
        self.PanelReady = sysFlags & 0x01 != 0
        self.PanelAlertInMemory = sysFlags & 0x02 != 0

        if (sysFlags & 0x04 != 0):                   # Trouble
            self.PanelTroubleStatus = AlTroubleType.GENERAL
        else:
            self.PanelTroubleStatus = AlTroubleType.NONE

        self.PanelBypass = sysFlags & 0x08 != 0
        #if sysFlags & 0x10 != 0:  # last 10 seconds of entry/exit
        #    self.PanelArmed = sarm == "Arming"
        #else:
        #     self.PanelArmed = sarm == "Armed"
        PanelAlarmEvent = sysFlags & 0x80 != 0

        if not self.pmPowerlinkMode:
            # if the system status has the panel armed and there has been an alarm event, assume that the alarm is sounding
            #        and that the sensor that triggered it isn't an entry delay
            #   Normally this would only be directly available in Powerlink mode with A7 messages, but an assumption is made here
            if sarm == "Armed" and PanelAlarmEvent and self.PanelState != AlPanelStatus.ENTRY_DELAY:
                log.debug("[ProcessPanelStateUpdate]      Alarm Event Assumed while in Standard Mode")
                # Alarm Event
                self.SirenActive = True
                #self.PanelLastEventData = self.setLastEventData(siren=True)
                #self.sendPanelUpdate(AlCondition.PANEL_UPDATE_ALARM_ACTIVE)  # Alarm Event
                # As we have just pushed a change through there's no need to do it again

        # Clear any alarm event if the panel alarm has been triggered before (while armed) but now that the panel is disarmed (in all modes)
        if self.SirenActive and sarm == "Disarmed":
            log.debug("[ProcessPanelStateUpdate] ******************** Alarm Not Sounding (Disarmed) ****************")
            self.SirenActive = False

    def ProcessZoneEvent(self, eventZone, eventType):
        sEventLog = pmEventType_t[self.pmLang][eventType]
        log.debug("[ProcessZoneEvent]      Zone Event      Zone: {0}    Type: {1}, {2}".format(eventZone, eventType, sEventLog))
        key = eventZone - 1  # get the key from the zone - 1
        
        if self.ForceStandardMode and key not in self.SensorList and eventType > 0:
            log.debug("[ProcessZoneEvent]          In Standard Mode, got a Zone Sensor that I did not know about so creating it")
            self.dynamicallyAddSensor(key)

        if key in self.SensorList:
            if eventType == 1:  # Tamper Alarm
                if not self.SensorList[key].tamper:
                    self.SensorList[key].tamper = True
                    self.SensorList[key].pushChange(AlSensorCondition.TAMPER)
            elif eventType == 2:  # Tamper Restore
                if self.SensorList[key].tamper:
                    self.SensorList[key].tamper = False
                    self.SensorList[key].pushChange(AlSensorCondition.RESET)
            elif eventType == 3:  # Zone Open
                self.UpdateContactSensor(sensor = key, status = True)
            elif eventType == 4:  # Zone Closed
                self.UpdateContactSensor(sensor = key, status = False)
            elif eventType == 5:  # Zone Violated
                self.UpdateContactSensor(sensor = key, trigger = True)
            elif eventType == 6: # Panic Alarm
                self.SensorList[key].pushChange(AlSensorCondition.PANIC)
            elif eventType == 7: # RF Jamming
                self.SensorList[key].pushChange(AlSensorCondition.PROBLEM)
            elif eventType == 8: # Tamper Open
                if not self.SensorList[key].tamper:
                    self.SensorList[key].tamper = True
                    self.SensorList[key].pushChange(AlSensorCondition.TAMPER)
            elif eventType == 9: # Comms Failure
                self.SensorList[key].pushChange(AlSensorCondition.PROBLEM)
            elif eventType == 10: # Line Failure
                self.SensorList[key].pushChange(AlSensorCondition.PROBLEM)
            elif eventType == 11: # Fuse
                self.SensorList[key].pushChange(AlSensorCondition.PROBLEM)
            # elif eventType == 12: # Not Active
            #    self.SensorList[key].triggered = False
            #    self.SensorList[key].status = False
            elif eventType == 13:  # Low Battery
                if not self.SensorList[key].lowbatt:
                    self.SensorList[key].lowbatt = True
                    self.SensorList[key].pushChange(AlSensorCondition.BATTERY)
            elif eventType == 14: # AC Failure
                self.SensorList[key].pushChange(AlSensorCondition.PROBLEM)
            elif eventType == 15: # Fire Alarm
                self.SensorList[key].pushChange(AlSensorCondition.FIRE)
            elif eventType == 16: # Emergency
                self.SensorList[key].pushChange(AlSensorCondition.EMERGENCY)
            elif eventType == 17: # Siren Tamper
                if not self.SensorList[key].tamper:
                    self.SensorList[key].tamper = True
                    self.SensorList[key].pushChange(AlSensorCondition.TAMPER)
            elif eventType == 18: # Siren Tamper Restore
                if self.SensorList[key].tamper:
                    self.SensorList[key].tamper = False
                    self.SensorList[key].pushChange(AlSensorCondition.RESET)
            elif eventType == 19: # Siren Low Battery
                if not self.SensorList[key].lowbatt:
                    self.SensorList[key].lowbatt = True
                    self.SensorList[key].pushChange(AlSensorCondition.BATTERY)
            elif eventType == 20: # Siren AC Fail
                self.SensorList[key].pushChange(AlSensorCondition.PROBLEM)
            #self.SensorList[key].pushChange()
        #else:
            #log.debug("[ProcessZoneEvent]            Zone not in sensor list")
    

    def ProcessX10StateUpdate(self, x10status, total = 16):
        # Examine X10 status
        for i in range(0, total):
            status = x10status & (1 << i)
            if i in self.SwitchList:
                # INTERFACE : use this to set X10 status
                oldstate = self.SwitchList[i].state
                self.SwitchList[i].state = bool(status)
                # Check to see if the state has changed
                if (oldstate and not self.SwitchList[i].state) or (not oldstate and self.SwitchList[i].state):
                    log.debug("[handle_msgtypeA5]      X10 device {0} changed to {2} ({1})".format(i, status, self.SwitchList[i].state))
                    self.SwitchList[i].pushChange()


    def handle_msgtypeA5(self, data):  # Status Message
        """ MsgType=A3 - Zone Data Update """

        # msgTot = data[0]
        eventType = data[1]

        #log.debug("[handle_msgtypeA5] Parsing A5 packet " + self._toString(data))

        if self.sensorsCreated and eventType == 0x01:  # Zone alarm status
            log.debug("[handle_msgtypeA5] Zone Alarm Status")
            val = self._makeInt(data[2:6])
            if val != self.zonealarm_old: # one of the sensors has changed
                self.zonealarm_old = val
                log.debug("[handle_msgtypeA5]      Zone Trip Alarm 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.SensorList:
                        if self.SensorList[i].ztrip != (val & (1 << i) != 0):
                            self.SensorList[i].ztrip = val & (1 << i) != 0
                            if self.SensorList[i].ztrip: # I can't remember seeing this from the panel
                                self.SensorList[i].pushChange(AlSensorCondition.STATE)
                            else:
                                self.SensorList[i].pushChange(AlSensorCondition.RESET)

            val = self._makeInt(data[6:10])
            if val != self.zonetamper_old: # one of the sensors has changed
                self.zonetamper_old = val
                log.debug("[handle_msgtypeA5]      Zone Tamper Alarm 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.SensorList:
                        if self.SensorList[i].ztamper != (val & (1 << i) != 0):
                            self.SensorList[i].ztamper = val & (1 << i) != 0
                            if self.SensorList[i].ztamper:
                                self.SensorList[i].pushChange(AlSensorCondition.TAMPER)
                            else:
                                self.SensorList[i].pushChange(AlSensorCondition.RESET)

        elif self.sensorsCreated and eventType == 0x02:  # Status message - Zone Open/Close and Battery Low
            # if in standard mode then use this A5 status message to reset the watchdog timer
            if not self.pmPowerlinkMode:
                log.debug("[handle_msgtypeA5]      Got A5 02 message, resetting watchdog")
                self._reset_watchdog_timeout()

            val = self._makeInt(data[2:6])
            if val != self.status_old:
                self.status_old = val
                log.debug("[handle_msgtypeA5]      Open Door/Window Status Zones 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.SensorList:
                        self.UpdateContactSensor(sensor = i, status = (val & (1 << i) != 0))

            val = self._makeInt(data[6:10])
            if val != self.lowbatt_old:
                self.lowbatt_old = val
                log.debug("[handle_msgtypeA5]      Battery Low Zones 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.SensorList:
                        if self.SensorList[i].lowbatt != (val & (1 << i) != 0):
                            self.SensorList[i].lowbatt = val & (1 << i) != 0
                            if self.SensorList[i].lowbatt:
                                self.SensorList[i].pushChange(AlSensorCondition.BATTERY)
                            else:
                                self.SensorList[i].pushChange(AlSensorCondition.RESET)

            self._dumpSensorsToLogFile()

        elif self.sensorsCreated and eventType == 0x03:  # Tamper Event
            val = self._makeInt(data[2:6])
            log.debug("[handle_msgtypeA5]      Trigger (Inactive) Status Zones 32-01: {:032b}".format(val))
            # This status is different from the status in the 0x02 part above i.e they are different values.
            #    This one is wrong (I had a door open and this status had 0, the one above had 1)
            #       According to domotica forum, this represents "active" but what does that actually mean?
            # for i in range(0, 32):
            #    if i in self.SensorList:
            #        self.SensorList[i].status = (val & (1 << i) != 0)

            val = self._makeInt(data[6:10])
            if val != self.tamper_old:
                self.tamper_old = val
                log.debug("[handle_msgtypeA5]      Tamper Zones 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.SensorList:
                        if self.SensorList[i].tamper != (val & (1 << i) != 0):
                            self.SensorList[i].tamper = val & (1 << i) != 0
                            if self.SensorList[i].tamper:
                                self.SensorList[i].pushChange(AlSensorCondition.TAMPER)
                            else:
                                self.SensorList[i].pushChange(AlSensorCondition.RESET)

        elif eventType == 0x04:  # Zone event
            # Assume that every zone event causes the need to push a change to the sensors etc

            if not self.pmPowerlinkMode:
                #log.debug("[handle_msgtypeA5]      Got A5 04 message, resetting watchdog")
                self._reset_watchdog_timeout()

            sysStatus = data[2]  # Mark-Mills with a PowerMax Complete Part, sometimes this has 0x20 bit set and I'm not sure why
            sysFlags = data[3]
            eventZone = data[4]
            eventType = data[5]
            # dont know what 6 and 7 are
            dummy1 = data[6]
            dummy2 = data[7]

            self.ProcessPanelStateUpdate(sysStatus=sysStatus, sysFlags=sysFlags)
            if sysFlags & 0x20 != 0:  # Zone Event
                if eventType > 0 and eventZone != 0xff: # I think that 0xFF refers to the panel itself as a zone. Currently not processed
                    self.ProcessZoneEvent(eventZone=eventZone, eventType=eventType)

            x10stat1 = data[8]
            x10stat2 = data[9]
            self.ProcessX10StateUpdate(x10status=x10stat1 + (x10stat2 * 0x100))

        elif eventType == 0x06:  # Status message enrolled/bypassed
            val = self._makeInt(data[2:6])

            if val != self.enrolled_old:
                log.debug("[handle_msgtypeA5]      Enrolled Zones 32-01: {:032b}".format(val))
                send_zone_type_request = False
                self.enrolled_old = val
                for i in range(0, 32):
                    # if the sensor is enrolled
                    if val & (1 << i) != 0:
                        self.dynamicallyAddSensor(i)

                        if not send_zone_type_request:
                            # We didn't find out about it by getting the EEPROM so we maybe didn't get it or its just been added to the panel
                            #   Queue up the commands to get the panel to send the sensor name and type
                            #for zn in range(10, 1, -1): 
                            #    self._sendCommand("MSG_ZONENAME", options=[ [zn, 6] ])
                            self.updateSensorNamesAndTypes()
                            send_zone_type_request = True

                    elif i in self.SensorList:
                        # it is not enrolled and we already know about it from the EEPROM, set enrolled to False
                        # self.SensorList[i].enrolled = False
                        log.debug("[handle_msgtypeA5]      Keeping Zone " + str(i+1) + " Enrolled but panel thinks it is not anymore" )

                self.sensorsCreated = True

            val = self._makeInt(data[6:10])
            if self.sensorsCreated and val != self.bypass_old:
                log.debug("[handle_msgtypeA5]      Bypassed Zones 32-01: {:032b}".format(val))
                self.bypass_old = val
                for i in range(0, 32):
                    if i in self.SensorList:
                        if self.SensorList[i].bypass != (val & (1 << i) != 0):
                            self.SensorList[i].bypass = val & (1 << i) != 0
                            if self.SensorList[i].bypass:
                                self.SensorList[i].pushChange(AlSensorCondition.BYPASS)
                            else:
                                self.SensorList[i].pushChange(AlSensorCondition.RESET)

            self._dumpSensorsToLogFile()
        # else:
        #    log.debug("[handle_msgtypeA5]      Unknown A5 Message: " + self._toString(data))

    def handle_msgtypeA6(self, data):
        """ MsgType=A6 - Zone Types I think """
        log.debug("[handle_MsgTypeA6] Packet = {0}".format(self._toString(data)))
        msgCnt = int(data[0])
        offset = 8 * (int(data[1]) - 1)
        log.debug("            Message Count is {0}   offset={1}     self.pmPowerlinkMode={2}".format( msgCnt, offset, self.pmPowerlinkMode ))
        for i in range(0, 8):
            # Save the Zone Type
            self.ZoneTypes[offset+i] = ((int(data[2+i])) - 0x1E) & 0x0F
            log.debug("                        Zone type for sensor {0} is {1} : {2}".format( offset+i+1, (int(data[2+i])) - 0x1E, pmZoneType_t["EN"][self.ZoneTypes[offset+i]] ))
            if not self.pmPowerlinkMode and (offset+i) in self.SensorList:
                self.setZoneType(offset+i)
                self.SensorList[offset+i].pushChange(AlSensorCondition.RESET)

    # This function may change global variables:
    #     self.SirenActive
    #     self.PanelAlarmStatus
    #     self.PanelTroubleStatus
    #     self.pmForceArmSetInPanel
    def handle_msgtypeA7(self, data):
        """ MsgType=A7 - Panel Status Change """
        log.debug("[handle_msgtypeA7] Panel Status Change " + self._toString(data))
        #
        #   In a log file I reveiced from pocket,    there was this A7 message 0d a7 ff fc 00 60 00 ff 00 0c 00 00 43 45 0a
        #   In a log file I reveiced from UffeNisse, there was this A7 message 0d a7 ff 64 00 60 00 ff 00 0c 00 00 43 45 0a     msgCnt is 0xFF and temp is 0x64 ????
        #
        msgCnt = int(data[0])

        # don't know what this is (It is 0x00 in test messages so could be the higher 8 bits for msgCnt)
        dummy = int(data[1])

        # If message count is FF then it looks like the first message is valid so decode it (this is experimental)
        if msgCnt == 0xFF:
            msgCnt = 1

        if msgCnt > 4:
            log.warning("[handle_msgtypeA7]      A7 message contains too many messages to process : {0}   data={1}".format(msgCnt, self._toString(data)))
        if msgCnt <= 0:
            log.warning("[handle_msgtypeA7]      A7 message contains no messages to process : {0}   data={1}".format(msgCnt, self._toString(data)))
        else:  ## message count 1 to 4
            log.debug("[handle_msgtypeA7]      A7 message contains {0} messages".format(msgCnt))

            PanelTamper = False
            zoneCnt = 0  # this means it wont work in the case we're in standard mode and the panel type is not set
            if self.PanelType is not None:
                zoneCnt = pmPanelConfig_t["CFG_WIRELESS"][self.PanelType] + pmPanelConfig_t["CFG_WIRED"][self.PanelType]

            # If there are multiple messages in the same A7 message then alarmStatus represents the last "not None" valid message i.e. in pmPanelAlarmType_t
            oldTroubleStatus = self.PanelTroubleStatus
            oldAlarmStatus = self.PanelAlarmStatus

            dictType = []
            dictMode = []
            dictEvent = []
            dictName = []

            # 03 00 01 03 08 0e 01 13
            # 03 00 2f 55 2f 1b 00 1c
            for i in range(0, msgCnt):
                eventZone = int(data[2 + (2 * i)])
                eventType = int(data[3 + (2 * i)])

                zoneStr = "Unknown"
                if self.PowerMaster is not None:
                    if self.PowerMaster:
                        zoneStr = pmLogPowerMasterUser_t[self.pmLang][eventZone] or "Unknown"
                    else:
                        eventZone = int(eventZone & 0x7F)
                        zoneStr = pmLogPowerMaxUser_t[self.pmLang][eventZone] or "Unknown"
                else:
                    log.debug("[handle_msgtypeA7]         Got an A7 message and the self.PowerMaster variable is not set")

                modeStr = pmLogEvent_t[self.pmLang][eventType] or "Unknown"

                dictType.insert(0, eventType)
                dictMode.insert(0, modeStr)
                dictEvent.insert(0, eventZone)
                dictName.insert(0, zoneStr)

                # set the sensor data
                #if eventZone >= 1 and eventZone <= zoneCnt:
                #    if eventZone-1 in self.SensorList:
                #        dasdfasdfasdfda

                if eventType == EVENT_TYPE_SYSTEM_RESET: # system restart
                    log.info("[handle_msgtypeA7]          Panel has been reset.")
                    self.sendPanelUpdate ( AlCondition.PANEL_RESET )   # push changes through to the host, the panel itself has been reset. Let user decide what action to take.
                    # Force an update in the Zone Names and Types
                    self.updateSensorNamesAndTypes(True)
                else:
                    # ---------------------------------------------------------------------------------------
                    log.debug("[handle_msgtypeA7]         self.SirenTriggerList = {0}".format(self.SirenTriggerList))

                    # Siren state
                    siren = False
                    if eventType in pmPanelAlarmType_t:
                        self.PanelAlarmStatus = pmPanelAlarmType_t[eventType]
                        alarmStatus = self.PanelAlarmStatus
                        log.debug("[handle_msgtypeA7]         Checking if {0} is in the siren trigger list {1}".format(str(alarmStatus).lower(), self.SirenTriggerList))
                        if str(alarmStatus).lower() in self.SirenTriggerList:
                            # If any of the A7 messages are in the SirenTriggerList then assume the Siren is triggered
                            log.debug("[handle_msgtypeA7]             And it is, setting siren to True")
                            siren = True

                    # Trouble state
                    if eventType in pmPanelTroubleType_t:
                        self.PanelTroubleStatus = pmPanelTroubleType_t[eventType]

                    log.debug("[handle_msgtypeA7]         System message " + modeStr + " / " + zoneStr + "  alarmStatus " + str(self.PanelAlarmStatus) + "   troubleStatus " + str(self.PanelTroubleStatus))

                    #---------------------------------------------------------------------------------------
                    # Update tamper and siren status
                    # The reasons to indicate a tamper
                    tamper = eventType == EVENT_TYPE_SENSOR_TAMPER or \
                             eventType == EVENT_TYPE_PANEL_TAMPER or \
                             eventType == EVENT_TYPE_TAMPER_ALARM_A or \
                             eventType == EVENT_TYPE_TAMPER_ALARM_B

                    # The reasons to cancel the siren
                    cancel = eventType == EVENT_TYPE_DISARM or \
                             eventType == EVENT_TYPE_ALARM_CANCEL or \
                             eventType == EVENT_TYPE_FIRE_RESTORE or \
                             eventType == EVENT_TYPE_FLOOD_ALERT_RESTORE or \
                             eventType == EVENT_TYPE_GAS_TROUBLE_RESTORE

                    # The reasons to ignore (not cancel) an alarm
                    ignore = eventType == EVENT_TYPE_DELAY_RESTORE or \
                             eventType == EVENT_TYPE_CONFIRM_ALARM or \
                             eventType == EVENT_TYPE_INTERIOR_RESTORE or \
                             eventType == EVENT_TYPE_PERIMETER_RESTORE

                    if tamper:
                        PanelTamper = True

                    # no clauses as if siren gets true again then keep updating self.SirenActive with the time
                    if siren: # and not self.pmPanicAlarmSilent:
                        self.SirenActive = True
                        log.debug("[handle_msgtypeA7] ******************** Alarm Active *******************")

                    # cancel alarm and the alarm has been triggered
                    if cancel and self.SirenActive:  # Cancel Alarm
                        self.SirenActive = False
                        log.debug("[handle_msgtypeA7] ******************** Alarm Cancelled ****************")

                    # Siren has been active but it is no longer active (probably timed out and has then been disarmed)
                    if not ignore and not siren and self.SirenActive:  # Alarm Timed Out ????
                        self.SirenActive = False
                        log.debug("[handle_msgtypeA7] ******************** Alarm Not Sounding ****************")

                    # INTERFACE Indicate whether siren active
                    log.debug("[handle_msgtypeA7]           self.SirenActive={0}   siren={1}   eventType={2}   self.pmPanicAlarmSilent={3}   tamper={4}".format(self.SirenActive, siren, hex(eventType), self.pmPanicAlarmSilent, tamper) )

                    #---------------------------------------------------------------------------------------
                    if eventType == EVENT_TYPE_FORCE_ARM or (self.pmForceArmSetInPanel and eventType == EVENT_TYPE_DISARM): # Force Arm OR (ForceArm has been set and Disarm)
                        self.pmForceArmSetInPanel = True                                 # When the panel uses ForceArm then sensors may be automatically armed and bypassed by the panel
                        log.debug("[handle_msgtypeA7]          Panel has been Armed using Force Arm, sensors may have been bypassed by the panel, asking panel for an update")
                        self._sendCommand("MSG_BYPASSTAT")

            #if oldAlarmStatus != self.PanelAlarmStatus or oldTroubleStatus != self.PanelTroubleStatus:
            #    # send update
            #    pass

            if len(dictType) > 0:
                log.debug(f"[handle_msgtypeA7] setLastPanelEventData {len(dictType)}")
                # count=0, type=[], event=[], mode=[], name=[]
                self.setLastPanelEventData(count=len(dictType), type=dictType, zonemode=dictMode, event=dictEvent, name=dictName)

            # reset=False
            self.PanelLastEventData = self.setLastEventData()

            if PanelTamper:
                log.debug("[handle_msgtypeA7] ******************** Tamper Triggered *******************")
                self.sendPanelUpdate(AlCondition.PANEL_TAMPER_ALARM)  # push changes through to the host to get it to update, tamper is active!


    # pmHandlePowerlink (0xAB)
    def handle_msgtypeAB(self, data) -> bool:  # PowerLink Message
        """ MsgType=AB - Panel Powerlink Messages """
        log.debug("[handle_msgtypeAB]  data {0}".format(self._toString(data)))

        # Restart the timer
        self._reset_watchdog_timeout()

        subType = data[0]
        if subType == 1:
            # Panel Time
            log.debug("[handle_msgtypeAB] ***************************** Got Panel Time ****************************")

            dt = datetime(2000 + data[7], data[6], data[5], data[4], data[3], data[2])
            log.debug("[handle_msgtypeAB]    Panel time is {0}".format(dt))

            # There is no point in setting the time here as we need to be in DOWNLOAD mode with the panel
            # So compare the times and log a difference
            t = self._getTimeFunction()
            if t.year > 2020:
                duration = dt - t                              # Get Time Difference, timedelta
                duration_in_s = abs(duration.total_seconds())  # Convert to seconds (and make it a positive value)
                log.debug("[handle_msgtypeAB]    Local time is {0}      time difference {1} seconds".format(t, duration_in_s))
            else:
                log.debug("[handle_msgtypeAB]    Please correct your local time.")

        elif subType == 3:  # keepalive message
            # Example 0D AB 03 00 1E 00 31 2E 31 35 00 00 43 2A 0A
            log.debug("[handle_msgtypeAB] ***************************** Got PowerLink Keep-Alive ****************************")
            # It is possible to receive this between enrolling (when the panel accepts the enroll successfully) and the EEPROM download
            #     I suggest we simply ignore it

            self._sendCommand("MSG_ALIVE")       # EXPERIMENTAL 29/8/2022.  The Powerlink module sends this when it gets an i'm alive from the panel.

            if self.pmPowerlinkModePending:
                log.debug("[handle_msgtypeAB]         Got alive message while Powerlink mode pending, going to full powerlink and calling Restore")
                self.pmPowerlinkMode = True
                self.pmPowerlinkModePending = False
                self.PanelMode = AlPanelMode.POWERLINK  # it is truly in powerlink now we are receiving powerlink alive messages from the panel
                self._triggerRestoreStatus()
                self._dumpSensorsToLogFile()

                # There is no point in setting the time here as we need to be in DOWNLOAD mode with the panel
                #  We set the time at the end of download
                # Get the time from the panel
                if self.AutoSyncTime:
                    self._sendCommand("MSG_GETTIME")

            elif not self.pmPowerlinkMode and not self.ForceStandardMode:
                if self.pmDownloadMode:
                    log.debug("[handle_msgtypeAB]         Got alive message while not in Powerlink mode but we're in Download mode")
                else:
                    log.debug("[handle_msgtypeAB]         Got alive message while not in Powerlink mode and not in Download mode")
            else:
                self._dumpSensorsToLogFile()
        elif subType == 5:  # -- phone message
            action = data[2]
            if action == 1:
                log.debug("[handle_msgtypeAB] PowerLink Phone: Calling User")
                # pmMessage("Calling user " + pmUserCalling + " (" + pmPhoneNr_t[pmUserCalling] +  ").", 2)
                # pmUserCalling = pmUserCalling + 1
                # if (pmUserCalling > pmPhoneNr_t) then
                #    pmUserCalling = 1
            elif action == 2:
                log.debug("[handle_msgtypeAB] PowerLink Phone: User Acknowledged")
                # pmMessage("User " .. pmUserCalling .. " acknowledged by phone.", 2)
                # pmUserCalling = 1
            else:
                log.debug("[handle_msgtypeAB] PowerLink Phone: Unknown Action {0}".format(hex(data[1]).upper()))
        elif subType == 10 and data[2] == 0:
            log.debug("[handle_msgtypeAB] PowerLink telling us what the code {0} {1} is for downloads, currently commented out as I'm not certain of this".format(data[3], data[4]))
            # data[3] data[4]
        elif subType == 10 and data[2] == 1:
            if self.pmPowerlinkMode:
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enroll but not acted on (already in powerlink) **************************")
            elif self.ForceStandardMode:
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enroll but not acted on (user has set force standard) **************************")
            elif not self.pmDownloadComplete:
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enroll but not acted on (download is not complete) **************************")
            elif not self.doneAutoEnroll:
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enroll, first time ************************** ")
                self.pmPowerlinkModePending = True   ## just to make sure it is True !
                self._triggerEnroll(True)             # The panel is asking to auto enroll so set force to True regardless of the panel type
            elif self.pmPowerlinkModePending:
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enroll, lets give it another try **************************")
                self._triggerEnroll(True)             # The panel is asking to auto enroll so set force to True regardless of the panel type
            else:
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enroll but not acted on (not sure why) **************************")

            self.doneAutoEnroll = True
        return True

    # X10 Names (0xAC)
    def handle_msgtypeAC(self, data):  # PowerLink Message
        """ MsgType=AC - ??? """
        log.debug("[handle_msgtypeAC]  data {0}".format(self._toString(data)))

    def handle_msgtypeAD(self, data):  # PowerLink Message
        """ MsgType=AD - Panel Powerlink Messages """
        log.debug("[handle_msgtypeAD]  data {0}".format(self._toString(data)))
        #if data[2] == 0x00: # the request was accepted by the panel
        #    self._sendCommand("MSG_NO_IDEA")
        

    def handle_msgtypeF4(self, data) -> bool:  # Static JPG Image
        """ MsgType=F4 - Static JPG Image """
        from PIL import Image, UnidentifiedImageError                        

        #log.debug("[handle_msgtypeF4]  data {0}".format(self._toString(data)))

        #      0 - message type  3=start, 5=data
        #      1 - always 0
        #      2 - sequence
        #      3 - data length
        msgtype = data[0]
        sequence = data[2]
        datalen = data[3]
        datastart = 4
        
        pushchange = False

        if msgtype == 0x03:     # JPG Header 
            log.debug("[handle_msgtypeF4]  data {0}".format(self._toString(data)))
            pushchange = True
            tmp = data[5]
            zone = (10 * int(tmp // 16)) + (tmp % 16)         # the // does integer floor division so always rounds down
            unique_id = data[6]
            image_id = data[7]
         
            if self.ImageManager.isImageDataInProgress():
                # We have received an unexpected F4 message header when the previous image transfer is still in progress
                log.debug(f"[handle_msgtypeF4]        Previous Image transfer incomplete, so not processing F4 data")

            elif self.PanelMode == AlPanelMode.UNKNOWN or self.PanelMode == AlPanelMode.PROBLEM or self.PanelMode == AlPanelMode.STARTING or self.PanelMode == AlPanelMode.DOWNLOAD or self.PanelMode == AlPanelMode.STOPPED:
                # We have received an unexpected F4 message so ignore it and try to prevent the panel from sending more
                log.debug(f"[handle_msgtypeF4]        PanelMode is {self.PanelMode} so not processing F4 data")

            elif zone - 1 in self.SensorList and self.SensorList[zone-1].getSensorType() == AlSensorType.CAMERA:
                log.debug(f"[handle_msgtypeF4]        Processing")
                # Here when PanelMode is COMPLETE_READONLY, MONITOR_ONLY, STANDARD, STANDARD_PLUS, POWERLINK

                if self.PanelMode == AlPanelMode.MONITOR_ONLY or self.PanelMode == AlPanelMode.COMPLETE_READONLY:
                    # Support externally requested images, from a real PowerLink Hardware device for example
                    if not self.ImageManager.isValidZone(zone):
                        self.ImageManager.create(zone, 11)   # This makes sure that there isn't an ongoing image retrieval for this sensor

                # Initialise the receipt of an image in the ImageManager
                self.ImageManager.setCurrent(zone = zone, unique_id = unique_id, image_id = image_id, size = (data[13] * 256) + data[12], sequence = sequence, lastimage = (data[11] == 1), totalimages = data[14])
 
                if self.PanelMode == AlPanelMode.POWERLINK or self.PanelMode == AlPanelMode.STANDARD_PLUS or self.PanelMode == AlPanelMode.STANDARD:
                    # Assume that we are managing the interaction/protocol with the panel
                    
                    self._addPDUToSendList(convertByteArray('0d ab 0e 00 17 1e 00 00 03 01 05 00 43 c5 0a'))

                    # 0d f4 10 00 01 04 00 55 1e 01 f7 fc 0a
                    fnoseA = 0xF7
                    fnoseB = 0xFC
                    
                    # Tell the panel we received that one OK, we're ready for the next 
                    #     --> *************************** THIS DOES NOT WORK ***************************
                    #         I assume because of fnoseA and fnoseB but I don't know what to set them to
                    if image_id == 0:   #   
                        id = data[14] - 1
                        self._addPDUToSendList(convertByteArray(f'0d f4 10 00 01 04 00 {zone:>02} {hexify(unique_id):>02} {hexify(id):>02} {hexify(fnoseA):>02} {hexify(fnoseB):>02} 0a'))
                    elif image_id >= 2:   #   image_id of 2 is the recorded sequence, I need to try this at 1
                        self._addPDUToSendList(convertByteArray(f'0d f4 10 00 01 04 00 {zone:>02} {hexify(unique_id):>02} {hexify(image_id - 1):>02} {hexify(fnoseA):>02} {hexify(fnoseB):>02} 0a'))

            else:
                log.debug(f"[handle_msgtypeF4]        Panel sending image for Zone {zone} but it does not exist or is not a CAMERA")

        elif msgtype == 0x05:   # JPG Data
            if not self.ImageManager.hasStartedSequence():
                log.debug(f"[handle_msgtypeF4]        Not processing F4 data") #, attempting to stop F4 data")
            else:
                # Image receipt has been initialised by self.ImageManager.setCurrent
                #     Therefore we only get here when PanelMode is COMPLETE_READONLY, MONITOR_ONLY, STANDARD, STANDARD_PLUS, POWERLINK
                inSequence = self.ImageManager.addData(data[datastart:datastart+datalen], sequence)
                if inSequence:
                    if self.ImageManager.isImageComplete():
                        zone, unique_id, image_id, total_images, buffer, lastimage = self.ImageManager.getLastImageRecord()
                        log.debug(f"[handle_msgtypeF4]        Image Complete       Current Data     zone={zone}    unique_id={hex(unique_id)}    image_id={image_id}    total_images={total_images}    lastimage={lastimage}")
                        pushchange = True
                    
                        #self._sendCommand("MSG_NO_IDEA")

                        # get time now to store image
                        t = self._getTimeFunction()
                        
                        # Assume a corrupt image
                        width = 100000
                        height = 100000
                        # Get the width and height of the image. I assume that if PIL can't load the image then it is corrupt.
                        #   The panel always sends 11 images:
                        #           images 1 to 10 are sent first in order and are always good, 
                        #           image 11 (marked as image 0) is always corrupt and has lots more bytes than the other 10
                        #                I wonder if its a different image/video format --> But the PIL library doesn't recognise it
                        try:
                            img = Image.open(io.BytesIO(buffer))
                            width, height = img.size
                        except Exception as ex:
                            log.debug(f"Image Exception {ex}")
                            
                        total = 0
                        for b in buffer:
                            total = total + b

                        log.debug(f"[handle_msgtypeF4]           Got Image width {width}    height {height}      total = {total} = {hex(total)}")

                        # Got all the data so write it out to a jpg file
                        #fn = "camera_image_z{0:0>2}_{1:0>2}{2:0>2}{3:0>2}_{4:0>2}{5:0>2}{6:0>2}.jpg".format(zone, t.day, t.month, t.year - 2000, t.hour, t.minute, t.second, )                    
                        #with open(fn, 'wb') as f1:
                        #    f1.write(buffer)
                        #    f1.close()

                        if zone - 1 in self.SensorList and width <= 1024 and height <= 768:
                            log.debug(f"[handle_msgtypeF4]           Saving Image sensor {zone}   width {width}    height {height}")
                            self.SensorList[zone - 1].jpg_data = buffer
                            self.SensorList[zone - 1].jpg_time = t
                            self.SensorList[zone - 1].hasJPG = True
                            self.SensorList[zone - 1].pushChange(AlSensorCondition.CAMERA)

                        if self.PanelMode == AlPanelMode.POWERLINK or self.PanelMode == AlPanelMode.STANDARD_PLUS or self.PanelMode == AlPanelMode.STANDARD:
                            # Assume that we are managing the interaction/protocol with the panel
                            if total_images != 0xFF:
                                fnoseA = 0xF7
                                fnoseB = 0xFC
                                # Tell the panel we received that one OK, we're ready for the next
                                #                                         0d f4 07 00 01 04 55 1e 01 00 15 21 0a  
                                self._addPDUToSendList(convertByteArray(f'0d f4 07 00 01 04 {zone:>02} {hexify(unique_id):>02} {hexify(image_id):>02} 00 {hexify(fnoseA):>02} {hexify(fnoseB):>02} 0a'))

                            if lastimage:
                                fnoseA = 0xF7
                                fnoseB = 0xFC
                                # Tell the panel we received that one OK, we're ready for the next
                                self._addPDUToSendList(convertByteArray(f'0d f4 10 00 01 04 00 {zone:>02} {hexify(unique_id):>02} 00 {hexify(fnoseA):>02} {hexify(fnoseB):>02} 0a'))

                        if lastimage:
                            # Tell the panel we received that one OK, we're ready for the next
                            log.debug(f"[handle_msgtypeF4]         Finished everything so stopping as we've just received the last image")
                            self.ImageManager.terminateImage()

                else:
                    log.debug(f"[handle_msgtypeF4]         Message out of sequence, dumping all data")
                    self.ImageManager.terminateImage()

        elif msgtype == 0x01:
            log.debug("[handle_msgtypeF4]  data {0}".format(self._toString(data)))
            #log.debug("[handle_msgtypeF4]  data {0}".format(self._toString(data)))
            log.debug(f"[handle_msgtypeF4]           Message Type not processed")
            pushchange = True

        else:
            log.debug("[handle_msgtypeF4]  not seen data {0}".format(self._toString(data)))
            log.debug(f"[handle_msgtypeF4]           Message Type not processed")

        return pushchange


    def handle_msgtypeC0(self, data):  # Redirected Powerlink Data
        log.debug(f"[handle_msgtypeC0] ******************************************************** Should not be here *************************************************")
         
    def checkallsame(self, val, b : bytearray) -> []:
        retval = []
        for i in range(0,len(b)):
            if int(b[i]) != val:
                retval.append(i)
        return retval

    # Only Powermasters send this message
    def handle_msgtypeB0(self, data):  # PowerMaster Message
        """ MsgType=B0 - Panel PowerMaster Message """
        # Only Powermasters send this message
        # Format: <Type> <SubType> <Length of Data and Counter> <Data> <Counter> <0x43>

        msgType = data[0]
        subType = data[1]
        msgLen  = data[2]
        
        # Whether to process the experimental code (and associated B0 message data) or not
        # If the "if" statement below includes this variable then I'm still trying to work out what the message data means
        experimental = True
        beezerodebug = True
        
        dontprint = [0x0306, 0x0335]
        command = (msgType << 8) | subType
        if command not in dontprint:
            log.debug("[handle_msgtypeB0] Received {0} message {1}/{2} (len = {3})    data = {4}".format(self.PanelModel or "UNKNOWN", msgType, subType, msgLen, self._toString(data)))

        # A powermaster mainly interacts with B0 messages so reset watchdog on receipt
        self._reset_watchdog_timeout()
        
        # The data <Length> value is 4 bytes less then the length of the data block (as the <MessageCounter> is part of the data count)
        if len(data) != msgLen + 4:
            log.debug("[handle_msgtypeB0]              Invalid Length, not processing")
            # Do not process this B0 message as it seems to be incorrect
            return

        if experimental and msgType == 0x03 and subType == 0x04:
            # Something about Zone information (probably) but I'm not sure
            #   Received PowerMaster10 message 3/4 (len = 35)    data = 03 04 23 ff 08 03 1e 26 00 00 01 00 00 <24 * 00> 0c 43
            #   Received PowerMaster30 message 3/4 (len = 69)    data = 03 04 45 ff 08 03 40 11 08 08 04 08 08 <58 * 00> 89 43
            #   Received PowerMaster33 message 3/4 (len = 69)    data = 03 04 45 ff 08 03 40 11 11 15 15 11 15 15 11 <56 * 00> b9 43  # user has 8 sensors, Z01 to Z08
            #   Received PowerMaster33 message 3/4 (len = 69)    data = 03 04 45 ff 08 03 40 11 11 15 15 11 15 15 11 <56 * 00> bb 43
            #   Received PowerMaster33 message 3/4 (len = 69)    data = 03 04 45 ff 08 03 40 15 04 11 08 04 08 08 08 <56 * 00> c9 43
            #   Received PowerMaster33 message 3/4 (len = 69)    data = 03 04 45 ff 08 03 40 15 04 11 08 04 08 08 08 <56 * 00> cd 43
            zoneLen = data[6] # The length of the zone data (64 for PM30, 30 for PM10)
            log.debug("[handle_msgtypeB0]       Received message, 03 04 information, zone length = {0}".format(zoneLen))
            if beezerodebug:
                for z in range(0, zoneLen):
                    if z in self.SensorList:
                        s = int(data[7 + z])
                        log.debug("                            Zone {0}  State(hex) {1}".format(z, hex(s)))
                        #self.SensorList[z].timelog.append([self._getUTCTimeFunction(), s])
                        #for r in self.SensorList[z].timelog:
                        #    log.debug("                                History {0}  State(hex) {1}".format(r[0], hex(r[1])))

        elif experimental and msgType == 0x03 and subType == 0x06:
            if msgLen == 2:
                if beezerodebug:
                    if self.save0306 is None:
                        self.save0306 = data[3]
                        log.debug("[handle_msgtypeB0]         Received Initial 0x0306 Byte as Data {0}   counter {1}".format(data[3], data[4]))
                    elif self.save0306 != data[3]:
                        self.save0306 = data[3]
                        log.debug("[handle_msgtypeB0]         Received Updated 0x0306 Byte as Data {0}   counter {1}".format(data[3], data[4]))
            else:
                log.debug("[handle_msgtypeB0] Received {0} message {1}/{2} (len = {3})    data = {4}".format(self.PanelModel or "UNKNOWN", msgType, subType, msgLen, self._toString(data)))
                
        elif experimental and msgType == 0x03 and subType == 0x07:
            #  Received PowerMaster10 message 3/7 (len = 35)    data = 03 07 23 ff 08 03 1e 03 00 00 03 00 00 <24 * 00> 0d 43
            #  Received PowerMaster30 message 3/7 (len = 69)    data = 03 07 45 ff 08 03 40 03 03 03 03 03 03 <58 * 00> 92 43
            #  My PM30 03 07 45 ff 08 03 40 00 00 00 00 00 03 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 03 00 00 03 00 1d 43
            # Unknown information
            zoneLen = data[6] # The length of the zone data (64 for PM30, 30 for PM10)
            log.debug("[handle_msgtypeB0]       Received message, 03 07 information, zone length = {0}".format(zoneLen))
            if beezerodebug:
                for z in range(0, zoneLen):
                    #if z in self.SensorList:
                    if data[7 + z] != 0:
                        s = data[7 + z]
                        log.debug("                            Zone {0}  State {1}".format(z, s))

        elif msgType == 0x03 and subType == 0x18:
            # Open/Close information

            # The length of the zone data (64 for PM30, 30 for PM10)
            #     set to 8 on my panel (29/8/2022)
            zoneLen = data[6] * 8     # 8 bits in a byte
            log.debug("[handle_msgtypeB0]       Received message, open/close information, zone length = {0}".format(zoneLen))

            val = self._makeInt(data[7:11])  # bytes 7,8,9,10
            for i in range(0, 32):
                if i in self.SensorList:
                    status = val & (1 << i) != 0
                    log.debug("                            Zone {0}  State {1}".format(i, status))
                    self.UpdateContactSensor(sensor = i, status = status)

            if zoneLen >= 32:
                val = self._makeInt(data[11:15])  # bytes 11,12,13,14
                for i in range(32, 64):
                    if i in self.SensorList:
                        status = val & (1 << (i-32)) != 0
                        log.debug("                            Zone {0}  State {1}".format(i, status))
                        self.UpdateContactSensor(sensor = i, status = status)

        elif experimental and msgType == 0x02 and subType == 0x20:
            # There are between 1 to 3 arrays and they are all filled with ones (1s)
            #     e.g. 02 20 b4 ff 08 00 04 01 01 01 01 ff 08 01 0f 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 ff 08 02 08 01 01 01 01 01 01 01 01 ff 08 03 40 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 ff 08 04 20 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 ff 08 05 20 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 69 43
            if msgLen == 69:
                start = 5
                zoneLen = data[start]
                log.debug("[handle_msgtypeB0]       Received message, dont know what this is at all, length = {0}".format(zoneLen))
            elif msgLen == 0xb4:
                if beezerodebug:
                    start = 45
                    zoneLen = data[start]
                    diff = self.checkallsame(1, data[start+1:start+1+zoneLen])
                    log.debug("[handle_msgtypeB0]       Received message, dont know what this is at all, length = {0}  It's all ones apart from {1}".format(zoneLen, diff))
                    
                    start = 113
                    zoneLen = data[start]
                    diff = self.checkallsame(1, data[start+1:start+1+zoneLen])
                    log.debug("[handle_msgtypeB0]       Received message, dont know what this is at all, length = {0}  It's all ones apart from {1}".format(zoneLen, diff))

                    start = 149
                    zoneLen = data[start]
                    diff = self.checkallsame(1, data[start+1:start+1+zoneLen])
                    log.debug("[handle_msgtypeB0]       Received message, dont know what this is at all, length = {0}  It's all ones apart from {1}".format(zoneLen, diff))

            else:
                log.debug("[handle_msgtypeB0]       Received message, length = {0}".format(zoneLen))

        elif msgType == 0x03 and subType == 0x1F:
            # Sensor List
            #     e.g. 03 1f b4 ff 08 00 04 00 00 00 00 ff 08 01 0f 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ff 08 02 08 00 00 00 00 00 00 00 00 ff 08 03 40 00 fe 00 00 00 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 29 00 00 04 00 ff 08 04 20 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ff 08 05 20 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 68 43
            #     e.g. 03 1f 45 ff 08 03 40 00 fe 00 00 00 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 29 00 00 04 00 8f 43
            #          03 1f 23 ff 08 03 1e 35 35 35 35 35 35 35 35 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 87 43  # powermaster 10

            self.BeeZeroSensorList = True
            start = 0
            if msgLen == 180:   # PowerMaster 30
                start = 45
            elif msgLen == 69:  # PowerMaster 30
                start = 6
            elif msgLen == 35:  # PowerMaster 10
                start = 6
            else:
                log.debug("[handle_msgtypeB0]       Received message, this should be sensor list but its the wrong length, length = {0}".format(msgLen))
                return
            
            zoneLen = data[start]
            log.debug("[handle_msgtypeB0]       Received message, sensor list, length = {0}".format(zoneLen))
            for i in range(0, zoneLen):
                v = int(data[start+1+i])
                if v > 0:   # Is it a sensor?
                    log.debug("[handle_msgtypeB0]          sensor type for sensor {0} is {1}".format( i+1, v ))
                    # Create the sensor
                    if v in pmZoneSensorMaster_t:         # PowerMaster models, we assume that only PowerMaster models send B0 PDUs
                        self.dynamicallyAddSensor(i, sid = v, func = pmZoneSensorMaster_t[v].func, name = pmZoneSensorMaster_t[v].name)
                    else:
                        self.dynamicallyAddSensor(i)
                        log.debug("[handle_msgtypeB0]                 Found unknown sensor type " + hex(v))

        elif msgType == 0x03 and subType == 0x21:
            # Zone Names
            #     e.g. 03 21 5e ff 08 03 40 0c 10 13 01 05 16 08 08 11 13 13 04 04 0f 15 15 12 14 02 18 18 0a 0a 02 16 00 07 19 10 18 16 16 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 06 00 ff 08 07 10 1f 1f 1f 1f 1f 1f 1f 1f 1f 1f 1f 1f 1f 1f 1f 1f ff 08 0b 01 ff 88 43
            #     e.g. 03 21 45 ff 08 03 40 0c 10 13 01 05 16 08 08 11 13 13 04 04 0f 15 15 12 14 02 18 18 0a 0a 02 16 00 07 19 10 18 16 16 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 06 00 90 43
            start = 45
            if msgLen == 69:     # PowerMaster 30
                start = 6
            elif msgLen == 94:   # PowerMaster 30
                start = 6
            zoneLen = data[start]
            log.debug("[handle_msgtypeB0]       Received message, zone names, length = {0}".format(zoneLen))
            for i in range(0, zoneLen):
                zoneName = int(data[start+1+i]) & 0x1F
                if i in self.ZoneNames:
                    if self.ZoneNames[i] != zoneName:
                        log.debug("                        Zone name for sensor {0} is {1} : {2}".format( i+1, zoneName, pmZoneName_t[zoneName] ))
                        log.debug("                             And its different to the EEPROM downloaded value {0} {1}".format(self.ZoneNames[i], zoneName))
                # Save the Zone Name
                self.ZoneNames[i] = zoneName
                if not self.pmPowerlinkMode and i in self.SensorList:
                    self.setZoneName(i)
                    self.SensorList[i].pushChange(AlSensorCondition.RESET)

        elif msgType == 0x03 and subType == 0x24:
            # Panel state change
            iSec = data[15]
            iMin = data[16]
            iHour = data[17]
            iDay = data[18]
            iMonth = data[19]
            iYear = data[20]

            messagedate = "{0:0>2}/{1:0>2}/{2}   {3:0>2}:{4:0>2}:{5:0>2}".format(iDay, iMonth, iYear, iHour, iMin, iSec)
            log.debug("[handle_msgtypeB0]       Received message, 03 24 information  date={0}".format(messagedate))
            log.debug("[handle_msgtypeB0]                    data (hex) 21={0}  22={1}  23={2}  status={3}  flags={4}  26={5}  27={6}".format(hex(data[21]).upper(), hex(data[22]).upper(), hex(data[23]).upper(), hex(data[24]).upper(), hex(data[25]).upper(), hex(data[26]).upper(), hex(data[27]).upper()))
 
            #######################################################################################################################################
            #######################################################################################################################################
            #######################################################################################################################################
            #######################################################################################################################################
            #######################################################################################################################################
            if beezerodebug:
                d = data[2:15]
                if self.saved_A is not None:
                    if self.saved_A != d:
                        log.debug(f"[handle_msgtypeB0]             First bit different to last time {self._toString(self.saved_A)}")
                        log.debug(f"                                                           now  {self._toString(d)}")
                self.saved_A = d
                    
                d = data[21:24]
                if self.saved_B is not None:
                    if self.saved_B != d:
                        log.debug(f"[handle_msgtypeB0]             Middle bit different to last time {self._toString(self.saved_B)}")
                        log.debug(f"                                                            now  {self._toString(d)}")
                self.saved_B = d
                    
                d = data[26:28]
                if self.saved_C is not None:
                    if self.saved_C != d:
                        log.debug(f"[handle_msgtypeB0]             Last bit different to last time {self._toString(self.saved_C)}")
                        log.debug(f"                                                          now  {self._toString(d)}")
                self.saved_C = d
            #######################################################################################################################################
            #######################################################################################################################################
            #######################################################################################################################################
            #######################################################################################################################################
            #######################################################################################################################################
                
            self.ProcessPanelStateUpdate(sysStatus=data[24], sysFlags=data[25])
            if data[24] > 0:
                log.debug(f"[handle_msgtypeB0]             Zone Event **************************************************************")
            
            #if sysFlags & 0x20 != 0:  # Zone Event
            #    self.ProcessZoneEvent(eventZone=eventZone, eventType=eventType)

            # Nothing in the 03 24 messsage tells us which sensor might have been triggered (it may not be any and the panel is just sending an update)
            if self.PanelMode == AlPanelMode.POWERLINK or \
               self.PanelMode == AlPanelMode.MONITOR_ONLY or \
               self.PanelMode == AlPanelMode.STANDARD or \
               self.PanelMode == AlPanelMode.STANDARD_PLUS: 
                log.debug("[handle_msgtypeB0]       Requesting sensor messages from the panel")
                self._sendCommand("MSG_PM_SENSORS")
            
        elif msgType == 0x03 and subType == 0x2d:
            # Zone Types
            #     e.g. 03 2d 45 ff 08 03 40 04 0c 07 07 07 0c 06 07 07 07 06 06 07 07 06 07 07 07 07 0a 0a 07 07 09 09 0a 0a 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 05 07 8b 43
            #     e.g. 03 2d 45 ff 08 03 40 04 0c 07 07 07 0c 06 07 07 07 06 06 07 07 06 07 07 07 07 0a 0a 07 07 09 09 0a 0a 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 07 05 07 91 43
            #          03 2d 23 ff 08 03 1e 0c 0c 0c 07 07 0c 07 07 07 07 06 06 07 07 06 07 07 07 07 0a 0a 07 07 09 09 0a 0a 07 07 07 ab 43 

            start = 0
            if msgLen == 69:
                start = 6
            elif msgLen == 94:
                start = 6
            elif msgLen == 35:
                start = 6
            else:
                log.debug("[handle_msgtypeB0]            ************** Received message, zone types, but unknown length so not processing it ***************")
                return
            zoneLen = data[start]
            log.debug("[handle_msgtypeB0]       Received message, zone types, length = {0}".format(zoneLen))
            for i in range(0, zoneLen):
                zoneType = int(data[start+1+i]) & 0x0F
                # Save the Zone Type
                if i in self.ZoneTypes:
                    if self.ZoneTypes[i] != zoneType:
                        log.debug("                        Zone type for sensor {0} is {1} : {2}".format( i+1, zoneType, pmZoneType_t["EN"][zoneType] ))
                        log.debug("                             And its different to the EEPROM downloaded value {0} {1}".format(self.ZoneTypes[i], zoneType))
                self.ZoneTypes[i] = zoneType
                if not self.pmPowerlinkMode and i in self.SensorList:
                    self.setZoneType(i)
                    self.SensorList[i].pushChange(AlSensorCondition.RESET)

        elif experimental and msgType == 0x03 and subType == 0x35:   # process B0 configuration data
            dataLen = data[6]
            dataTypeA = data[7]
            dataTypeB = data[8]
            dataType = (dataTypeB << 8) | dataTypeA
            if beezerodebug:
                if dataType == 0x0054 and dataLen == 5:
                    # Installer Panel Code
                    code = (data[10] << 8) | data[11]
                    #log.debug("[handle_msgtypeB0]           Installer Panel Code {0}".format(hex(code)))
                elif dataType == 0x0055 and dataLen == 5:
                    # Master Panel Code
                    code = (data[10] << 8) | data[11]
                    #log.debug("[handle_msgtypeB0]           Master Panel Code {0}".format(hex(code)))
                elif dataType == 0x000F and dataLen == 5:
                    # Download Panel Code
                    code = (data[10] << 8) | data[11]
                    #log.debug("[handle_msgtypeB0]           Download Panel Code {0}".format(hex(code)))
                elif dataType == 0x0008:
                    # First User Panel Code
                    code = (data[10] << 8) | data[11]
                    #log.debug("[handle_msgtypeB0]           First User Code {0}".format(hex(code)))                
                #elif dataType == 0x002C and dataLen == 19:
                #    s = data[10:26]
                #    log.debug("[handle_msgtypeB0]           Data 2C {0}".format(s.decode()))                
                #elif dataType == 0x002D and dataLen == 19:
                #    s = data[10:26]
                #    log.debug("[handle_msgtypeB0]           Data 2D {0}".format(s.decode()))                
                #elif dataType == 0x003D and dataLen == 19:
                #    s = data[10:26]
                #    log.debug("[handle_msgtypeB0]           Data 3D {0}".format(s.decode()))                
                #elif dataType == 0x003E and dataLen == 19:
                #    s = data[10:26]
                #    log.debug("[handle_msgtypeB0]           Data 3E {0}".format(s.decode()))                
                else:
                    log.debug("[handle_msgtypeB0] Received {0} message {1}/{2} (len = {3})    data = {4}".format(self.PanelModel or "UNKNOWN", msgType, subType, msgLen, self._toString(data)))

        elif msgType == 0x03 and subType == 0x39:
            # 03 39 06 ff 08 ff 01 02 11 43
            # 03 39 07 ff 08 ff 02 0b 24 29 43
            # 03 39 08 ff 08 ff 03 18 24 4b 3e 43
            # 03 39 09 ff 08 ff 04 09 18 24 4b 1f 43
            # 03 39 0c ff 08 ff 07 0b 13 1c 24 30 32 34 3d 43
            msglen = data[2]
            if beezerodebug:
                if msglen == 6:
                    d = data[3:8]
                    if self.saved_H is not None:
                        if self.saved_H != d:
                            log.debug(f"[handle_msgtypeB0]             03 39 06 bit different to last time {self._toString(self.saved_H)}")
                            log.debug(f"                                                              now  {self._toString(d)}")
                    self.saved_H = d
                elif msglen == 7:
                    d = data[3:9]
                    if self.saved_K is not None:
                        if self.saved_K != d:
                            log.debug(f"[handle_msgtypeB0]             03 39 07 bit different to last time {self._toString(self.saved_K)}")
                            log.debug(f"                                                              now  {self._toString(d)}")
                    self.saved_K = d
                elif msglen == 8:
                    d = data[3:10]
                    if self.saved_G is not None:
                        if self.saved_G != d:
                            log.debug(f"[handle_msgtypeB0]             03 39 08 bit different to last time {self._toString(self.saved_G)}")
                            log.debug(f"                                                              now  {self._toString(d)}")
                    self.saved_G = d
                elif msglen == 9:
                    d = data[3:11]
                    if self.saved_J is not None:
                        if self.saved_J != d:
                            log.debug(f"[handle_msgtypeB0]             03 39 09 bit different to last time {self._toString(self.saved_J)}")
                            log.debug(f"                                                              now  {self._toString(d)}")
                    self.saved_J = d
                elif msglen == 12:
                    d = data[3:14]
                    if self.saved_L is not None:
                        if self.saved_L != d:
                            log.debug(f"[handle_msgtypeB0]             03 39 0C bit different to last time {self._toString(self.saved_L)}")
                            log.debug(f"                                                              now  {self._toString(d)}")
                    self.saved_L = d
                else:
                    log.debug("[handle_msgtypeB0]         Not seen this size before")
               
        
            # Panel state change ??????
#            if self.PanelMode == AlPanelMode.POWERLINK or \
#               self.PanelMode == AlPanelMode.MONITOR_ONLY or \
#               self.PanelMode == AlPanelMode.STANDARD or \
#               self.PanelMode == AlPanelMode.STANDARD_PLUS: 
#                log.debug("[handle_msgtypeB0]       Requesting sensor messages from the panel")
#                self._sendCommand("MSG_PM_SENSORS")

        elif msgType == 0x02 and subType == 0x4B:
            # I think that this represents sensors Z01 to Z36.  Each sensor is 5 bytes.
            # 0d b0 02 4b b9 01 28 03 b4 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 d0 28 6e 38 03 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 2a 43 62 0a
            #     For the PM30 with 64 sensors this comes out as 180 / 5 = 36
            #log.debug(f"[handle_msgtypeB0]             Here here                {self.beezero_024B_sensorcount}")
            sensortotalbytes = int(data[6])
            if sensortotalbytes % 5 == 0:             # Divisible by 5, each sensors data is 5 bytes
                #log.debug(f"[handle_msgtypeB0]       {sensortotalbytes}    {self.beezero_024B_sensorcount}")
                self.beezero_024B_sensorcount = int(sensortotalbytes / 5)
                #log.debug(f"[handle_msgtypeB0]       {sensortotalbytes}    {self.beezero_024B_sensorcount}")
                for i in range(0, self.beezero_024B_sensorcount):
                    if i in self.SensorList:
                        o = 7 + (i * 5)
                        log.debug("[handle_msgtypeB0]           Sensor = {0:>2}  data (hex) = {1} {2} {3} {4} {5}".format(i, hex(data[o]).upper(), hex(data[o+1]).upper(), hex(data[o+2]).upper(), hex(data[o+3]).upper(), hex(data[o+4]).upper()))
                        if self.SensorList[i].statuslog is not None:
                            if self.SensorList[i].statuslog != data[o:o+5]:
                                self.UpdateContactSensor(sensor = i, trigger = True)
                                # I'm pretty sure that this is the open / close state of contact sensors but motion sensors return 0x03 all the time, I'm not sure
                                #open = data[o+4] == 0x01
                                #closed = data[o+4] == 0x02
                                #log.debug(f"[handle_msgtypeB0]                  Sensor Triggered open={open}    closed={closed}")
                        self.SensorList[i].statuslog = data[o:o+5]

        elif msgType == 0x03 and subType == 0x4B:
            # I think that this represents sensors Z37 to Z64.  Each sensor is 5 bytes.   With a PM10 I'm not sure that we'll get this message.
            # 0d b0 03 4b 91 ff 28 03 8c 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 c4 18 6e 38 02 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 d3 f9 6d 38 03 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 41 ec 6d 38 00 2b 43 ca 0a
            #     For the PM30 with 64 sensors this comes out as 140 / 5 = 28
            if self.beezero_024B_sensorcount is not None:
                sensortotalbytes = int(data[6])
                if sensortotalbytes % 5 == 0:          # Divisible by 5, each sensors data is 5 bytes
                    sensorcount = int(sensortotalbytes / 5)
                    for i in range(0, sensorcount):
                        sensor = i + self.beezero_024B_sensorcount # The 36 comes from the above message
                        if sensor in self.SensorList:
                            o = 7 + (i * 5)
                            log.debug("[handle_msgtypeB0]           Sensor = {0:>2}  data (hex) = {1} {2} {3} {4} {5}".format(sensor, hex(data[o]).upper(), hex(data[o+1]).upper(), hex(data[o+2]).upper(), hex(data[o+3]).upper(), hex(data[o+4]).upper()))
                            if self.SensorList[sensor].statuslog is not None:
                                if self.SensorList[sensor].statuslog != data[o:o+5]:
                                    self.UpdateContactSensor(sensor = sensor, trigger = True)
                                    #open = data[o+4] == 0x01
                                    #closed = data[o+4] == 0x02
                                    #log.debug(f"[handle_msgtypeB0]                  Sensor Triggered open={open}    closed={closed}")
                            self.SensorList[sensor].statuslog = data[o:o+5]
                self.beezero_024B_sensorcount = None   # If theres a next time so they are coordinated

        elif experimental and msgType == 0x03 and subType == 0x42:
            log.debug("[handle_msgtypeB0]       Received 03 42 message")
            
            if data[2] == 0x15 and data[6] == 0x10:
                # Just create local variables, dont alter the global self. variables
                ModelType = data[21]
                PanelType = data[22]

                PowerMaster = (PanelType >= 7)
                PanelModel = pmPanelType_t[PanelType] if PanelType in pmPanelType_t else "UNKNOWN"   # INTERFACE : PanelType set to model

                log.debug("[handle_msgtypeB0] PanelType={0} : {2} , Model={1}   Powermaster {3}".format(PanelType, ModelType, PanelModel, PowerMaster))

        elif experimental and msgType == 0x03 and subType == 0x51:
            # 03 51 08 ff 08 ff 03 18 24 4b 9c 43
            # 03 51 08 ff 08 ff 03 18 24 4b 9f 43
            # 03 51 08 ff 08 ff 03 18 24 4b a2 43
            # 03 51 0c ff 08 ff 07 02 09 0b 13 18 24 4b 37 43
            # 03 51 06 ff 08 ff 01 24 a8 43
            # 03 51 09 ff 08 ff 04 02 0b 13 24 45 43
            # 03 51 0b ff 08 ff 06 02 0b 13 18 24 4b 55 43
            #log.debug("[handle_msgtypeB0]       Received message")
            
            msglen = data[2]
            if beezerodebug:
                if msglen == 8:
                    d = data[3:10]
                    if self.saved_D is not None:
                        if self.saved_D != d:
                            log.debug(f"[handle_msgtypeB0]             0x08 bit different to last time {self._toString(self.saved_D)}")
                            log.debug(f"                                                          now  {self._toString(d)}")
                    self.saved_D = d
                elif msglen == 12:
                    d = data[3:14]
                    if self.saved_E is not None:
                        if self.saved_E != d:
                            log.debug(f"[handle_msgtypeB0]             0x0C bit different to last time {self._toString(self.saved_E)}")
                            log.debug(f"                                                          now  {self._toString(d)}")
                    self.saved_E = d
                elif msglen == 6:
                    d = data[3:8]
                    if self.saved_F is not None:
                        if self.saved_F != d:
                            log.debug(f"[handle_msgtypeB0]             0x06 bit different to last time {self._toString(self.saved_F)}")
                            log.debug(f"                                                          now  {self._toString(d)}")
                    self.saved_F = d
                elif msglen == 9:
                    d = data[3:11]
                    if self.saved_M is not None:
                        if self.saved_M != d:
                            log.debug(f"[handle_msgtypeB0]             0x09 bit different to last time {self._toString(self.saved_M)}")
                            log.debug(f"                                                          now  {self._toString(d)}")
                    self.saved_M = d
                elif msglen == 11:
                    d = data[3:13]
                    if self.saved_I is not None:
                        if self.saved_I != d:
                            log.debug(f"[handle_msgtypeB0]             0x0B bit different to last time {self._toString(self.saved_I)}")
                            log.debug(f"                                                          now  {self._toString(d)}")
                    self.saved_I = d
                else:
                    log.debug("[handle_msgtypeB0]         Not seen this size before")
            
        elif experimental and msgType == 0x03 and subType == 0x54:
            pass
            #log.debug("[handle_msgtypeB0]       Received message")


    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================
    # ================== Functions below this are utility functions to support the interface ================
    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================

    def _dumpSensorsToLogFile(self):
        log.debug("================================================== Display Status ==================================================")
        for key, sensor in self.SensorList.items():
            log.debug("     key {0:<2} Sensor {1}".format(key, sensor))
        log.debug("   Model {: <18}     PowerMaster {: <18}     LastEvent {: <18}     Ready   {: <13}".format(self.PanelModel,
                                        'Yes' if self.PowerMaster else 'No', self.getPanelLastEvent(), 'Yes' if self.PanelReady else 'No'))
        pm = titlecase(self.PanelMode.name.replace("_"," ")) # str(AlPanelMode()[self.PanelMode]).replace("_"," ")
        ts = titlecase(self.PanelTroubleStatus.name.replace("_"," ")) # str(AlTroubleType()[self.PanelTroubleStatus]).replace("_"," ")
        al = titlecase(self.PanelAlarmStatus.name.replace("_"," ")) # str(AlAlarmType()[self.PanelAlarmStatus]).replace("_"," ")

        log.debug("   Mode  {: <18}     Status      {: <18}     Trouble {: <13}     AlarmStatus {: <12}".format(pm, self.PanelStatusText, ts, al))
        log.debug("====================================================================================================================")

    def _createPin(self, pin : str):
        # Pin is None when either we can perform the action without a code OR we're in Powerlink/StandardPlus and have the pin code to use
        # Other cases, the pin must be set
        if pin is None:
            if self.pmGotUserCode:
                bpin = self.pmPincode_t[0]   # if self.pmGotUserCode, then we downloaded the pin codes. Use the first one
            else:
                bpin = convertByteArray("00 00")
        elif len(pin) == 4:
            bpin = convertByteArray(pin[0:2] + " " + pin[2:4])
        else:
            # default to setting it to "0000" and see what happens when its sent to the panel
            bpin = convertByteArray("00 00")
        return bpin

# Event handling and externally callable client functions (plus updatestatus)
class VisonicProtocol(PacketHandling):
    """ Event Handling """

    def __init__(self, *args, **kwargs) -> None:
        """Add VisonicProtocol specific initialization."""
        super().__init__(*args, **kwargs)

    ############################################################################################################
    ############################################################################################################
    ############################################################################################################
    ######################## The following functions are called from the client ################################
    ############################################################################################################
    ############################################################################################################
    ############################################################################################################

    def shutdownOperation(self):
        super().shutdownOperation()
        self.sendPanelUpdate(AlCondition.PANEL_UPDATE)  # push through a panel update to the HA Frontend

    # A dictionary that is used to add to the attribute list of the Alarm Control Panel
    #     If this is overridden then please include the items in the dictionary defined here by using super()
    def getPanelStatusDict(self) -> dict:
        """ Get a dictionary representing the panel status. """
        a = self.setLastEventData()
        b = { "Protocol Version" : PLUGIN_VERSION }
        self.merge(a,b)
        #log.debug("[getPanelStatusDict]  getPanelStatusDict a = {0}".format(a))

        f = self.getPanelFixedDict()
        self.merge(a,f)

        c = {
            "Watchdog Timeout (Total)": self.WatchdogTimeout,
            "Watchdog Timeout (Past 24 Hours)": self.WatchdogTimeoutPastDay,
            "Download Timeout": self.DownloadTimeout,
            "Download Retries": self.pmDownloadRetryCount,
            "Panel Problem Count": self.PanelProblemCount,
            "Panel Problem Time": self.LastPanelProblemTime if self.LastPanelProblemTime else ""
        }
        #log.debug("[getPanelStatusDict A] type a={0} type c={1}".format(type(a), type(c)))
        self.merge(a,c)
        if self.IncludeEEPROMAttributes and len(self.PanelStatus) > 0:
            # r = {**d, **self.PanelStatus}
            self.merge(a,self.PanelStatus)
        #log.debug("[getPanelStatusDict]  getPanelStatusDict d = {0} {1}".format(type(d),d))
        return a

    # requestPanelCommand
    #       state is PanelCommand
    #       optional pin, if not provided then try to use the EEPROM downloaded pin if in powerlink
    def requestPanelCommand(self, state : AlPanelCommand, code : str = "") -> AlCommandStatus:
        """ Send a request to the panel to Arm/Disarm """
        if not self.pmDownloadMode:
            if state == AlPanelCommand.CHANGE_BAUD:
                if self.PanelMode == AlPanelMode.POWERLINK and self.pmGotUserCode:
                    log.debug("[requestPanelCommand] Changing Baud Rate, *************** always use 38400 and ignore code parameter ************")
                    bpin = self._createPin(None)
                    self._clearList()
                    # code = convert code to a baud rate, insert at pos 14
                    self._addMessageToSendList("MSG_PM_SETBAUD", options=[ [4, bpin] ])  #
                    #self._addMessageToSendList("MSG_PM_SETBAUD", options=[ [4, bpin] ])  #
                    return AlCommandStatus.SUCCESS
                else:
                    return AlCommandStatus.FAIL_INVALID_STATE
            else:
                bpin = self._createPin(code)
                # Ensure that the state is valid
                if state in pmArmMode_t:
                    armCode = bytearray()
                    # Retrieve the code to send to the panel
                    armCode.append(pmArmMode_t[state])
                    self._addMessageToSendList("MSG_ARM", options=[ [3, armCode], [4, bpin] ])  #
                    self._addMessageToSendList("MSG_BYPASSTAT")
                    return AlCommandStatus.SUCCESS
                elif state == AlPanelCommand.MUTE:
                    self._addMessageToSendList("MSG_MUTE_SIREN", options=[ [4, bpin] ])  #
                    return AlCommandStatus.SUCCESS
                elif state == AlPanelCommand.TRIGGER:
                    self._addMessageToSendList("MSG_PM_SIREN", options=[ [4, bpin] ])  #
                    return AlCommandStatus.SUCCESS
                elif state in pmSirenMode_t:
                    sirenCode = bytearray()
                    # Retrieve the code to send to the panel
                    sirenCode.append(pmSirenMode_t[state])
                    self._addMessageToSendList("MSG_PM_SIREN_MODE", options=[ [4, bpin], [11, sirenCode] ])  #
                    return AlCommandStatus.SUCCESS
                else:
                    return AlCommandStatus.FAIL_INVALID_STATE
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    def setX10(self, device : int, state : AlX10Command) -> AlCommandStatus:
        # This is untested
        # "MSG_X10PGM"      : VisonicCommand(convertByteArray('A4 00 00 00 00 00 99 99 00 00 00 43'), None  , False, "X10 Data" ),
        #log.debug("[SendX10Command] Processing {0} {1}".format(device, type(device)))
        if not self.pmDownloadMode:
            if device >= 0 and device <= 15:
                log.debug("[SendX10Command]  Send X10 Command : id = " + str(device) + "   state = " + str(state))
                calc = 1 << device
                byteA = calc & 0xFF
                byteB = (calc >> 8) & 0xFF
                if state in pmX10State_t:
                    what = pmX10State_t[state]
                    self._addMessageToSendList("MSG_X10PGM", options=[ [6, what], [7, byteA], [8, byteB] ])
                    self._addMessageToSendList("MSG_BYPASSTAT")
                    return AlCommandStatus.SUCCESS
                else:
                    return AlCommandStatus.FAIL_INVALID_STATE
            else:
                return AlCommandStatus.FAIL_X10_PROBLEM
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    def getJPG(self, device : int, count : int) -> AlCommandStatus:
        if self.PanelMode == AlPanelMode.STANDARD or self.PanelMode == AlPanelMode.STANDARD_PLUS or self.PanelMode == AlPanelMode.POWERLINK:
            if not self.pmDownloadMode:
                #if device >= 1 and device <= 64:
                #    if device - 1 in self.SensorList and self.SensorList[device-1].getSensorType() == AlSensorType.CAMERA:
                #        if self.ImageManager.create(device, count):   # This makes sure that there isn't an ongoing image retrieval for this sensor
                #            self._addMessageToSendList("MSG_GET_IMAGE", options=[ [1, count], [2, device] ])  #  
                #            return AlCommandStatus.SUCCESS
                return AlCommandStatus.FAIL_INVALID_STATE
            else:
                return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS
        else:
            return AlCommandStatus.FAIL_INVALID_STATE

    # Individually arm/disarm the sensors
    #   This sets/clears the bypass for each sensor
    #       zone is the zone number 1 to 31 or 1 to 64
    #       bypassValue is a boolean ( True then Bypass, False then Arm )
    #       optional pin, if not provided then try to use the EEPROM downloaded pin if in powerlink
    #   Return : success or not
    #
    #   the MSG_BYPASSEN and MSG_BYPASSDI commands are the same i.e. command is A1
    #      byte 0 is the command A1
    #      bytes 1 and 2 are the pin
    #      bytes 3 to 6 are the Enable bits for the 32 zones
    #      bytes 7 to 10 are the Disable bits for the 32 zones
    #      byte 11 is 0x43
    def setSensorBypassState(self, sensor : int, bypassValue : bool, pin : str = "") -> AlCommandStatus:
        """ Set or Clear Sensor Bypass """
        if not self.pmDownloadMode:
            if not self.pmBypassOff:
                bpin = self._createPin(pin)
                bypassint = 1 << (sensor - 1)
                #log.debug("[SensorArmState]  setSensorBypassState A " + hex(bypassint))
                y1, y2, y3, y4 = (bypassint & 0xFFFFFFFF).to_bytes(4, "little")
                bypass = bytearray([y1, y2, y3, y4])
                log.debug("[SensorArmState]  setSensorBypassState bypass = " + self._toString(bypass))
                if len(bypass) == 4:
                    if bypassValue:
                        self._addMessageToSendList("MSG_BYPASSEN", options=[ [1, bpin], [3, bypass] ])
                    else:
                        self._addMessageToSendList("MSG_BYPASSDI", options=[ [1, bpin], [7, bypass] ])
                    # request status to check success and update sensor variable
                    self._addMessageToSendList("MSG_BYPASSTAT")
                    return AlCommandStatus.SUCCESS
                else:
                    return AlCommandStatus.FAIL_INVALID_STATE
            else:
                return AlCommandStatus.FAIL_PANEL_CONFIG_PREVENTED
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    # Get the Event Log
    #       optional pin, if not provided then try to use the EEPROM downloaded pin if in powerlink
    def getEventLog(self, pin : str = "") -> AlCommandStatus:
        """ Get Panel Event Log """
        log.debug("getEventLog")
        self.eventCount = 0
        self.pmEventLogDictionary = {}
        if not self.pmDownloadMode:
            bpin = self._createPin(pin)
            self._addMessageToSendList("MSG_EVENTLOG", options=[ [4, bpin] ])
            return AlCommandStatus.SUCCESS
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

# Turn on auto code formatting when using black
# fmt: on
