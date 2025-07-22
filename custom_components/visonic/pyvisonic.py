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

# The defaults are set for use in Home Assistant.
#    If using MicroPython / CircuitPython then set these values in the environment
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

    mylog = logging.getLogger(__name__)
    mylog.setLevel(logging.DEBUG)

else:
    import inspect as ipt  
    import logging
    import datetime as dt
    from datetime import datetime, timedelta, timezone
    from typing import Callable, List
    import copy
    from abc import abstractmethod

    def convertByteArray(s) -> bytearray:
        return bytearray.fromhex(s)

    mylog = logging.getLogger(__name__)

import asyncio
import sys
import collections
import time
import math
import io
import socket
import random
import traceback

from collections import namedtuple
from time import sleep
from enum import StrEnum, IntEnum, Enum, auto, unique
from string import punctuation
from textwrap import wrap

try:
    from .pyenum import (CFG, RAW, SEQUENCE, PanelSetting, MessagePriority, DataType, IndexName, Packet, B0SubType, EPROM, Send, Receive, PanelTypeEnum, EVENT_TYPE, PANEL_STATUS)
    from .pyconst import (AlTransport, AlPanelDataStream, NO_DELAY_SET, PanelConfig, AlConfiguration, AlPanelMode, AlPanelCommand, AlTroubleType, AlPanelEventData, EPROM_DOWNLOAD_ALL,
                          AlAlarmType, AlPanelStatus, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlLogPanelEvent, AlSensorType, AlDeviceType, AlTerminationType, PE_PARTITION, NOBYPASSSTR, DISABLE_TEXT,
                          TEXT_PANEL_MODEL, TEXT_WATCHDOG_TIMEOUT_TOTAL, TEXT_WATCHDOG_TIMEOUT_DAY, TEXT_DOWNLOAD_TIMEOUT, TEXT_DL_MESSAGE_RETRIES, TEXT_PROTOCOL_VERSION, TEXT_POWER_MASTER )
    from .pyhelper import (toString, MyChecksumCalc, AlImageManager, ImageRecord, titlecase, AlPanelInterfaceHelper, 
                           AlSensorDeviceHelper, AlSwitchDeviceHelper)
    from .pyeprom import EPROMManager
except:
    from pyenum import (CFG, RAW, SEQUENCE, PanelSetting, MessagePriority, DataType, IndexName, Packet, B0SubType, EPROM, Send, Receive, PanelTypeEnum, EVENT_TYPE, PANEL_STATUS)
    from pyconst import (AlTransport, AlPanelDataStream, NO_DELAY_SET, PanelConfig, AlConfiguration, AlPanelMode, AlPanelCommand, AlTroubleType, AlPanelEventData, EPROM_DOWNLOAD_ALL,
                          AlAlarmType, AlPanelStatus, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlLogPanelEvent, AlSensorType, AlDeviceType, AlTerminationType, PE_PARTITION, NOBYPASSSTR, DISABLE_TEXT,
                          TEXT_PANEL_MODEL, TEXT_WATCHDOG_TIMEOUT_TOTAL, TEXT_WATCHDOG_TIMEOUT_DAY, TEXT_DOWNLOAD_TIMEOUT, TEXT_DL_MESSAGE_RETRIES, TEXT_PROTOCOL_VERSION, TEXT_POWER_MASTER )
    from pyhelper import (toString, MyChecksumCalc, AlImageManager, ImageRecord, titlecase, AlPanelInterfaceHelper, 
                          AlSensorDeviceHelper, AlSwitchDeviceHelper)
    from pyeprom import EPROMManager

PLUGIN_VERSION = "1.9.2.2"

#############################################################################################################################################################################
######################### Global variables used to determine what is included in the log file ###############################################################################
#############################################################################################################################################################################

# Obfuscate sensitive data, regardless of the other Debug settings.
#     Setting this to True limits the logging of messages sent to the panel to CMD or NONE
#                     It also limits logging of received data
OBFUS = True

# Whether to include B0 35 and B0 42 panel data decode in the log file.  Note that this is also combined with OBFUS.
B0_35_PANEL_DATA_LOG = True  # True or False
B0_42_PANEL_DATA_LOG = B0_35_PANEL_DATA_LOG

class DebugLevel(IntEnum):
    NONE = 0   # 0 = do not log this message
    CMD  = 1   # 1 = Show only the msg string in the log file, not the message content
    FULL = 2   # 2 = Show the full data in the log file, including the message content

# Debug Settings (what information to put in the log files) - Sending Messages to the Panel
SendDebugC = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug sending control messages
SendDebugM = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug sending message data
SendDebugD = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug sending EPROM message data
SendDebugI = DebugLevel.NONE if OBFUS else DebugLevel.FULL  # Debug sending image data

# Debug Settings (what information to put in the log files) - Receiving Messages from the Panel
RecvDebugC = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug incoming control messages
RecvDebugM = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug incoming message data
RecvDebugD = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug incoming EPROM message data
RecvDebugI = DebugLevel.NONE if OBFUS else DebugLevel.FULL  # Debug incoming image data

#############################################################################################################################################################################
######################### Global variables used to configure specific timeouts and maximum settings.  These also help readability of the code. ##############################
#############################################################################################################################################################################

# Maximum number of CRC errors on receiving data from the alarm panel before performing a restart
#    This means a maximum of 5 CRC errors in 10 minutes before resetting the connection
MAX_CRC_ERROR = 5
CRC_ERROR_PERIOD = 600  # seconds, 10 minutes

# Maximum number of received messages that are exactly the same from the alarm panel before performing a restart
SAME_PACKET_ERROR = 10000

# If we are waiting on a message back from the panel or we are explicitly waiting for an acknowledge,
#    then wait this time before resending the message.
#  Note that not all messages will get a resend, only ones waiting for a specific response and/or are blocking on an ack
RESEND_MESSAGE_TIMEOUT = timedelta(seconds=30000) # Not currently used 

# We must get specific messages from the panel, if we do not in this time period (seconds) then trigger a restore/status request
WATCHDOG_TIMEOUT = 120

# If there has been a watchdog timeout this many times per 24 hours then go to standard (plus) mode
WATCHDOG_MAXIMUM_EVENTS = 10

# Response timeout, when we send a PDU this is the time we wait for a response (defined in replytype in VisonicCommand)
RESPONSE_TIMEOUT = timedelta(seconds=10) # 

# If a message has not been sent to the panel in this time (seconds) then send an I'm alive message
KEEP_ALIVE_PERIOD = 25  # Seconds

# When we send a download command wait for DownloadMode to become false.
#   If this timesout then I'm not sure what to do, maybe we really need to just start again
#   In Vera, if we timeout we just assume we're in Standard mode by default
DOWNLOAD_TIMEOUT = 90
DOWNLOAD_TIMEOUT_GIVE_UP = 280    # 

# Default Download Code
DEFAULT_DL_CODE = "5650"

# Number of seconds delay between trying to achieve EPROM download
DOWNLOAD_RETRY_DELAY = 60

# Number of times to retry the download, this is a total
DOWNLOAD_RETRY_COUNT = 10

# Whether to download the EPROM or to use default to get the panel data, this is or'd with CFG.EPROM_DOWNLOAD in pmPanelConfig and used for debug
FORCE_DOWNLOAD_TO_USE_EPROM = True

# Number of times to retry the retrieval of a block to download, this is a total across all blocks to download and not each block
DOWNLOAD_PDU_RETRY_COUNT = 30

# Number of seconds delay between trying to achieve powerlink (must have achieved download first)
POWERLINK_RETRY_DELAY = 180

# Number of seconds delay between not getting I'm alive messages from the panel in Powerlink Mode
POWERLINK_IMALIVE_RETRY_DELAY = 100

STANDARD_STATUS_RETRY_DELAY = 90

# Maximum number of seconds between the panel sending I'm alive messages
MAX_TIME_BETWEEN_POWERLINK_ALIVE = 60

# Number of seconds between trying to achieve powerlink (must have achieved download first) and giving up. Better to be half way between retry delays
POWERLINK_TIMEOUT = 4.5 * POWERLINK_RETRY_DELAY

# This is the minimum time interval (in milli seconds) between sending subsequent messages to the panel so the panel has time to process them. 
#    This value is based on the slowest supported panel
MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMAX = 190
MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMASTER = 150

# The number of seconds that if we have not received any data packets from the panel at all (from the start) then suspend this plugin and report to HA
#    This is only used when no data at all has been received from the panel ... ever
NO_RECEIVE_DATA_TIMEOUT = 30

# The number of seconds between receiving data from the panel and then no communication (the panel has stopped sending data for this period of time) then suspend this plugin and report to HA
#    This is used when this integration has received data and then stopped receiving data
LAST_RECEIVE_DATA_TIMEOUT = 240  # 4 minutes

# Interval (in seconds) to get the time and for most panels to try and set it if it's out by more than TIME_INTERVAL_ERROR seconds
#     PowerMaster uses time interval for checking motion triggers so more critical to keep it updated
POWERMASTER_CHECK_TIME_INTERVAL =   300  # 5 minutes  (this uses B0 messages and not DOWNLOAD panel state)
POWERMAX_CHECK_TIME_INTERVAL    = 14400  # 4 hours    (this uses the DOWNLOAD panel state)
TIME_INTERVAL_ERROR = 3

# Message/Packet Constants to make the code easier to read

PACKET_MAX_SIZE = 0xF0
#ACK_MESSAGE = 0x02

log = mylog

#from .pyhelper import vloggerclass
#log = vloggerclass(mylog, 0, False)

# Turn off auto code formatting when using black
# fmt: off

# Then we will create tree_class function  
def tree_class(cls, ind = 0):  

    # Then we will print the name of the class  
    print ('-' * ind, cls.__name__)  

    # now, we will iterate through the subclasses  
    for K in cls.__subclasses__():  
        tree_class(K, ind + 3)  

# Use PriorityQueue but add a peek function to see the head of the list
class PriorityQueueWithPeek(asyncio.PriorityQueue):

    def peek_nowait(self):
        t = self._queue[0]     # PriorityQueue is an ordered list so look at the head of the list
        return t

##############################################################################################################################################################################################################################################
##########################  Panel Type Information  ##########################################################################################################################################################################################
##############################################################################################################################################################################################################################################

# Panel Names for each panel type (0-16).
#     0 : "PowerMax" is not a supported panel type  
#     Assume 360R is Panel 16 for this release as it was released after the PM33, also I've an old log file from a user that indicates this
pmPanelType = {
   0 : "PowerMax", 
   1 : "PowerMax+", 
   2 : "PowerMax Pro", 
   3 : "PowerMax Complete", 
   4 : "PowerMax Pro Part",
   5 : "PowerMax Complete Part", 
   6 : "PowerMax Express", 
   7 : "PowerMaster 10",   
   8 : "PowerMaster 30",
   10 : "PowerMaster 33", 
   13 : "PowerMaster 360", 
   15 : "PowerMaster 33", 
   16 : "PowerMaster 360R",
   17 : "Default"                     # This is the default panel settings i.e. the most basic panel
}

# Config for each panel type (0-16).  
#     Assume 360R is Panel 16 for this release as it was released after the PM33, also I've an old log file from a user that indicates this
#               So make column 16 the same as column 13
#     Don't know what 9, 11, 12 or 14 are so just copy other settings. I know that there are Commercial/Industry Panel versions so it might be them
#     This data defines each panel type's maximum capability
#     I know that panel types 4 and 5 support 3 partitions but I can't figure out how they are represented in A5 and A7 messages, so partitions only supported for PowerMaster and B0 messages

pmPanelConfig = {       #     0       1       2       3       4       5       6       7       8       9      10      11      12      13      14      15      16      17    See pmPanelType above
   CFG.SUPPORTED      : ( False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True ), # Supported Panels i.e. not a PowerMax
   CFG.KEEPALIVE      : (     0,     25,     25,     25,     25,     25,     25,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15 ), # Keep Alive message interval
   CFG.DLCODE_1       : (    "", "5650", "5650", "5650", "5650", "5650", "5650", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "5650" ), # Default download codes (for reset panels or panels that have not been changed)
   CFG.DLCODE_2       : (    "", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "5650", "5650", "5650", "5650", "5650", "5650", "5650", "5650", "5650", "5650", "AAAA" ), # Alternative 1 (Master) known default download codes
   CFG.DLCODE_3       : (    "", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB" ), # Alternative 2 (Master) known default download codes
   CFG.PARTITIONS     : (     0,      1,      1,      1,      1,      1,      1,      3,      3,      3,      3,      3,      3,      3,      3,      3,      3,      1 ), # Force all PowerMax Panels to only have 1 partition
   CFG.EVENTS         : (     0,    250,    250,    250,    250,    250,    250,    250,   1000,   1000,   1000,   1000,   1000,   1000,   1000,   1000,   1000,    250 ),
   CFG.KEYFOBS        : (     0,      8,      8,      8,      8,      8,      8,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32,      8 ),
   CFG.ONE_WKEYPADS   : (     0,      8,      8,      8,      8,      8,      8,      0,      0,      0,      0,      0,      0,      0,      0,      0,      0,      8 ),
   CFG.TWO_WKEYPADS   : (     0,      2,      2,      2,      2,      2,      2,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32,      2 ),
   CFG.SIRENS         : (     0,      2,      2,      2,      2,      2,      2,      4,      8,      8,      8,      8,      8,      8,      8,      8,      8,      2 ),
   CFG.USERCODES      : (     0,      8,      8,      8,      8,      8,      8,      8,     48,     48,     48,     48,     48,     48,     48,     48,     48,      8 ),
   CFG.REPEATERS      : (     0,      0,      0,      0,      0,      0,      0,      4,      8,      4,      4,      4,      4,      4,      4,      4,      4,      0 ),
   CFG.PROXTAGS       : (     0,      0,      8,      0,      8,      8,      0,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32,      0 ),
   CFG.ZONECUSTOM     : (     0,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5 ),
   CFG.DEV_ZONE_TYPES : (     0,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30 ),
   CFG.WIRELESS       : (     0,     28,     28,     28,     28,     28,     29,     29,     62,     62,     62,     62,     62,     64,     62,     62,     64,     28 ), # Wireless + Wired total 30 or 64
   CFG.WIRED          : (     0,      2,      2,      2,      2,      2,      1,      1,      2,      2,      2,      2,      2,      0,      2,      2,      0,      2 ),
   CFG.X10            : (     0,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15 ), # Supported X10 devices
   CFG.PGM            : (     0,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1 ), # PGM
   CFG.AUTO_ENROL     : (  None,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False,   True,   True,  False,  False ), # 360 and 360R cannot autoenrol to Powerlink
   CFG.AUTO_SYNCTIME  : (  None,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False ), # Assume 360 and 360R can auto sync time
   CFG.POWERMASTER    : (  None,  False,  False,  False,  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False ), # Panels that use and respond to the additional PowerMaster Messages
   CFG.EPROM_DOWNLOAD : (  None,   True,   True,   True,   True,   True,   True,   True,  False,  False,  False,  False,  False,  False,  False,  False,  False,   True ), # Panel does EPROM Download (True) or can do B0 Message Download (False)
   CFG.INIT_SUPPORT   : (  None,  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False,   True,   True,  False,  False )  # Panels that support the INIT command
}

##############################################################################################################################################################################################################################################
##########################  Messages that we can send to the panel  ##########################################################################################################################################################################
##############################################################################################################################################################################################################################################

# A gregorian year, on average, contains 365.2425 days
#    Thus, expressed as seconds per average year, we get 365.2425 × 24 × 60 × 60 = 31,556,952 seconds/year
# use a named tuple for data and acknowledge
#    replytype   is a message type from the Panel that we should get in response
#    waitforack, if True means that we should wait for the acknowledge from the Panel before progressing
#    debugprint  If False then do not log the full raw data as it may contain the user code
#    waittime    a number of seconds after sending the command to wait before sending the next command
VisonicCommand = collections.namedtuple('VisonicCommand', 'data replytype waitforack download debugprint waittime msg')
pmSendMsg = {  #                        data                                                                 replytype            waitforack download   debugprint waittime   msg
   # Quick command codes to start and stop download/powerlink are a single value                                      
   Send.BUMP         : VisonicCommand(convertByteArray('09')                                          , [Receive.PANEL_INFO]        , False, False,      SendDebugM, 0.5, "Bump Panel Data From Panel" ),  # Bump to try to get the panel to send a 3C
   Send.START        : VisonicCommand(convertByteArray('0A')                                          , [Receive.LOOPBACK_TEST]     , False, False,      SendDebugM, 0.0, "Start" ),                          # waiting for STOP from panel for download complete
   Send.STOP         : VisonicCommand(convertByteArray('0B')                                          , None                        , False, False,      SendDebugM, 1.5, "Stop" ),     #
   Send.EXIT         : VisonicCommand(convertByteArray('0F')                                          , None                        , False, False,      SendDebugM, 1.5, "Exit" ),

   # Command codes do not have the Packet.POWERLINK_TERMINAL (0x43) on the end and are only 11 values                                                               
   Send.DOWNLOAD_DL  : VisonicCommand(convertByteArray('24 00 00 99 99 00 00 00 00 00 00')            , None                        , False,  True,      SendDebugD, 0.0, "Start Download Mode" ),            # This gets either an acknowledge OR an Access Denied response
   Send.DOWNLOAD_TIME: VisonicCommand(convertByteArray('24 00 00 99 99 00 00 00 00 00 00')            , None                        , False, False,      SendDebugD, 0.5, "Trigger Panel To Set Time" ),      # Use this instead of BUMP as can be used by all panels. To set time.
   Send.PANEL_DETAILS: VisonicCommand(convertByteArray('24 00 00 99 99 00 00 00 00 00 00')            , [Receive.PANEL_INFO]        , False, False,      SendDebugD, 0.5, "Trigger Panel Data From Panel" ),  # Use this instead of BUMP as can be used by all panels
   Send.WRITE        : VisonicCommand(convertByteArray('3D 00 00 00 00 00 00 00 00 00 00')            , None                        , False, False,      SendDebugD, 0.0, "Write Data Set" ),
   Send.DL           : VisonicCommand(convertByteArray('3E 00 00 00 00 B0 00 00 00 00 00')            , [Receive.DOWNLOAD_BLOCK]    ,  True, False,      SendDebugD, 0.0, "Download Data Set" ),
   Send.SETTIME      : VisonicCommand(convertByteArray('46 F8 00 01 02 03 04 05 06 FF FF')            , None                        , False, False,      SendDebugM, 1.0, "Setting Time" ),                   # may not need an ack so I don't wait for 1 and just get on with it
   Send.SER_TYPE     : VisonicCommand(convertByteArray('5A 30 04 01 00 00 00 00 00 00 00')            , [Receive.DOWNLOAD_SETTINGS] , False, False,      SendDebugM, 0.0, "Get Serial Type" ),

   Send.EVENTLOG     : VisonicCommand(convertByteArray('A0 00 00 00 99 99 00 00 00 00 00 43')         , [Receive.EVENT_LOG]         , False, False,      SendDebugC, 0.0, "Retrieving Event Log" ),
   Send.ARM          : VisonicCommand(convertByteArray('A1 00 00 99 99 99 07 00 00 00 00 43')         , None                        ,  True, False,      SendDebugC, 0.0, "(Dis)Arming System" ),             # Including 07 to arm all 3 partitions
   Send.MUTE_SIREN   : VisonicCommand(convertByteArray('A1 00 00 0B 99 99 00 00 00 00 00 43')         , None                        ,  True, False,      SendDebugC, 0.0, "Mute Siren" ),                     #
   Send.STATUS       : VisonicCommand(convertByteArray('A2 00 00 3F 00 00 00 00 00 00 00 43')         , [Receive.STATUS_UPDATE]     ,  True, False,      SendDebugM, 0.0, "Getting Status" ),                 # Ask for A5 messages, the 0x3F asks for 01 02 03 04 05 06 messages
   Send.STATUS_SEN   : VisonicCommand(convertByteArray('A2 00 00 08 00 00 00 00 00 00 00 43')         , [Receive.STATUS_UPDATE]     ,  True, False,      SendDebugM, 0.0, "Getting A5 04 Status" ),           # Ask for A5 messages, the 0x08 asks for 04 message only
   Send.BYPASSTAT    : VisonicCommand(convertByteArray('A2 00 00 20 00 00 00 00 00 00 00 43')         , [Receive.STATUS_UPDATE]     , False, False,      SendDebugC, 0.0, "Get Bypass and Enrolled Status" ), # Ask for A5 06 message (Enrolled and Bypass Status)
   Send.ZONENAME     : VisonicCommand(convertByteArray('A3 00 00 00 00 00 00 00 00 00 00 43')         , [Receive.ZONE_NAMES]        ,  True, False,      SendDebugM, 0.0, "Requesting Zone Names" ),          # We expect 4 or 8 (64 zones) A3 messages back but at least get 1
   Send.X10PGM       : VisonicCommand(convertByteArray('A4 00 00 00 00 00 99 99 99 00 00 43')         , None                        , False, False,      SendDebugM, 0.0, "X10 Data" ),                       # Retrieve X10 data
   Send.ZONETYPE     : VisonicCommand(convertByteArray('A6 00 00 00 00 00 00 00 00 00 00 43')         , [Receive.ZONE_TYPES]        ,  True, False,      SendDebugM, 0.0, "Requesting Zone Types" ),          # We expect 4 or 8 (64 zones) A6 messages back but at least get 1

   Send.BYPASSEN     : VisonicCommand(convertByteArray('AA 99 99 12 34 56 78 00 00 00 00 43')         , None                        , False, False,      SendDebugM, 0.0, "BYPASS Enable" ),                  # Bypass sensors
   Send.BYPASSDI     : VisonicCommand(convertByteArray('AA 99 99 00 00 00 00 12 34 56 78 43')         , None                        , False, False,      SendDebugM, 0.0, "BYPASS Disable" ),                 # Arm Sensors (cancel bypass)

   Send.GETTIME      : VisonicCommand(convertByteArray('AB 01 00 00 00 00 00 00 00 00 00 43')         , [Receive.POWERLINK]         ,  True, False,      SendDebugM, 0.0, "Get Panel Time" ),                 # Returns with an AB 01 message back
   Send.ALIVE        : VisonicCommand(convertByteArray('AB 03 00 00 00 00 00 00 00 00 00 43')         , None                        ,  True, False,      SendDebugM, 0.0, "I'm Alive Message To Panel" ),
   Send.RESTORE      : VisonicCommand(convertByteArray('AB 06 00 00 00 00 00 00 00 00 00 43')         , None                        ,  True, False,      SendDebugM, 0.0, "Restore Connection" ),             # It can take multiple of these to put the panel back in to powerlink
   Send.ENROL        : VisonicCommand(convertByteArray('AB 0A 00 00 99 99 00 00 00 00 00 43')         , None                        ,  True, False,      SendDebugM, 2.5, "Auto-Enrol PowerMax/Master" ),     # should get a reply of [0xAB] but its not guaranteed
   Send.INIT         : VisonicCommand(convertByteArray('AB 0A 00 01 00 00 00 00 00 00 00 43')         , None                        ,  True, False,      SendDebugM, 3.0, "Init PowerLink Connection" ),
   Send.IMAGE_FB     : VisonicCommand(convertByteArray('AB 0E 00 17 1E 00 00 03 01 05 00 43')         , None                        ,  True, False,      SendDebugM, 0.0, "PowerMaster after jpg feedback" ), # 

   Send.X10NAMES     : VisonicCommand(convertByteArray('AC 00 00 00 00 00 00 00 00 00 00 43')         , [Receive.X10_NAMES]         , False, False,      SendDebugM, 0.0, "Requesting X10 Names" ),
   Send.GET_IMAGE    : VisonicCommand(convertByteArray('AD 99 99 0A FF FF 00 00 00 00 00 43')         , [Receive.IMAGE_MGMT]        ,  True, False,      SendDebugI, 0.0, "Requesting JPG Image" ),           # The first 99 might be the number of images. Request a jpg image, second 99 is the zone.  
   # DISCONNECT_MESSAGE = "0d ad 0a 00 00 00 00 00 00 00 00 00 43 05 0a"
   
   # Acknowledges                                                                                             
   Send.ACK          : VisonicCommand(convertByteArray('02')                                          , None                        , False, False, DebugLevel.NONE, 0.0, "Ack" ),
   Send.ACK_PLINK    : VisonicCommand(convertByteArray('02 43')                                       , None                        , False, False, DebugLevel.NONE, 0.0, "Ack Powerlink" ),

   # PowerMaster specific 
   Send.PM_REQUEST   : VisonicCommand(convertByteArray('B0 01 99 01 05 43')                           , [Receive.POWERMASTER]       ,  True, False,      SendDebugM, 0.0, "Powermaster Request Type 1" ),       # Request a message type from the panel, change 99 with the message type
   Send.PM_REQUEST54 : VisonicCommand(convertByteArray('B0 01 54 00 43')                              , [Receive.POWERMASTER]       ,  True, False,      SendDebugM, 0.0, "Powermaster Request a 54" ),         # Request a 54 message type from the panel
   Send.PM_REQUEST58 : VisonicCommand(convertByteArray('B0 01 58 00 43')                              , [Receive.POWERMASTER]       ,  True, False,      SendDebugM, 0.0, "Powermaster Request a 58" ),         # Request a 58 message type from the panel
   Send.PM_KEEPALIVE : VisonicCommand(convertByteArray('B0 01 6A 00 43')                              , [Receive.POWERMASTER]       ,  True, False,      SendDebugM, 0.0, "Powermaster Keep Alive Request" ),   # Request a Keep Alive from the panel

   Send.PM_SIREN_MODE: VisonicCommand(convertByteArray('B0 00 47 09 99 99 00 FF 08 0C 02 99 07 43')   , None                        ,  True, False,      SendDebugM, 0.0, "Powermaster Trigger Siren Mode" ),   # Trigger Siren, the 99 99 needs to be the usercode, other 99 is Siren Type
   Send.PM_SIREN     : VisonicCommand(convertByteArray('B0 00 3E 0A 99 99 05 FF 08 02 03 00 00 01 43'), None                        ,  True, False,      SendDebugM, 0.0, "Powermaster Trigger Siren" ),        # Trigger Siren, the 99 99 needs to be the usercode
   Send.PL_BRIDGE    : VisonicCommand(convertByteArray('E1 99 99 43')                                 , None                        , False, False,      SendDebugM, 0.0, "Powerlink Bridge" ),                 # Command to the Bridge

#   Send.PM_SETBAUD   : VisonicCommand(convertByteArray('B0 00 41 0D AA AA 01 FF 28 0C 05 01 00 BB BB 00 05 43'), None   ,  True, False,   CMD, 2.5, "Powermaster Set Serial Baud Rate" ),

# Not sure what these do to the panel. Panel replies with powerlink ack Packet.POWERLINK_TERMINAL 0x43               
#   Send.MSG4             : VisonicCommand(convertByteArray('04 43')                                       , None   , False, False,      SendDebugM, 0.0, "Message 04 43. Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
#   Send.MSGC             : VisonicCommand(convertByteArray('0C 43')                                       , None   , False, False,      SendDebugM, 0.0, "Message 0C 43. Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
#   Send.UNKNOWN_0E       : VisonicCommand(convertByteArray('0E')                                          , None   , False, False,      SendDebugM, 0.0, "Message 0E.    Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
#   Send.MSGE             : VisonicCommand(convertByteArray('0E 43')                                       , None   , False, False,      SendDebugM, 0.0, "Message 0E 43. Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
}

# B0 Messages subset that we can send to a Powermaster, embed within MSG_POWERMASTER to use
B0_SendMessageTupleTmp = collections.namedtuple('B0_SendMessageTupleTmp', 'data chunky paged')

# Subclass the namedtuple to add a custom __str__ method
class B0_SendMessageTuple(B0_SendMessageTupleTmp):
    # Define a custom string for logging purposes
    def __str__(self):
        if isinstance(self.data, int):
            return (f"Chunky={self.chunky} Paged={self.paged} Subtype={self.data}")
        elif isinstance(self.data, B0SubType):
            return (f"Chunky={self.chunky} Paged={self.paged} Subtype={self.data.name}")
        return ("B0_SendMessageTuple unknown type")

pmSendMsgB0 = {   #                                      data  chunky paged
    B0SubType.WIRELESS_DEV_INACTIVE : B0_SendMessageTuple(0x02,  True, False),      # 
    B0SubType.WIRELESS_DEV_CHANNEL  : B0_SendMessageTuple(0x04,  True, False),      # 
    B0SubType.INVALID_COMMAND       : B0_SendMessageTuple(0x06, False, False),      # This isn't chunked  INVALID_COMMAND
    B0SubType.ZONE_STAT07           : B0_SendMessageTuple(0x07,  True, False),
    B0SubType.WIRELESS_DEV_MISSING  : B0_SendMessageTuple(0x09,  True, False),      # 
    B0SubType.TAMPER_ACTIVITY       : B0_SendMessageTuple(0x0A,  True, False),      # Mark: Tamper Activities
    B0SubType.TAMPER_ALERT          : B0_SendMessageTuple(0x0B,  True, False),      # Mark: Tamper Alert
    B0SubType.WIRELESS_DEV_ONEWAY   : B0_SendMessageTuple(0x0E,  True, False),      # 
    B0SubType.PANEL_STATE_2         : B0_SendMessageTuple(0x0F, False, False),      # Panel State 2
    B0SubType.TRIGGERED_ZONE        : B0_SendMessageTuple(0x13,  True, False),      # Triggered Zone ... maybe????????????????  0d b0 03 13 0d ff 01 03 08 00 00 00 00 00 00 00 00 da 43 02 0a  Decoded Chunk type 3   subtype 19   sequence 255  datasize 1    length 8    index ZONES            data 00 00 00 00 00 00 00 00
    B0SubType.ZONE_OPENCLOSE        : B0_SendMessageTuple(0x18,  True, False),      # Sensor Open/Close State
    B0SubType.ZONE_BYPASS           : B0_SendMessageTuple(0x19,  True, False),      # Sensor Bypass
    B0SubType.SENSOR_UNKNOWN_1C     : B0_SendMessageTuple(0x1C,  True, False),      # Sensors UNKNOWN ...  ???????????????????  0d b0 03 1c 0d ff 01 03 08 00 00 00 00 00 00 00 00 db 43 f7 0a  Decoded Chunk type 3   subtype 28   sequence 255  datasize 1    length 8    index ZONES            data 00 00 00 00 00 00 00 00
    B0SubType.SENSOR_ENROL          : B0_SendMessageTuple(0x1D,  True, False),      # Sensors Enrolment
    B0SubType.DEVICE_TYPES          : B0_SendMessageTuple(0x1F,  True, False),      # Sensors
    B0SubType.ASSIGNED_PARTITION    : B0_SendMessageTuple(0x20,  True,  True),
    B0SubType.ZONE_NAMES            : B0_SendMessageTuple(0x21,  True, False),      # Zone Names
    B0SubType.SYSTEM_CAP            : B0_SendMessageTuple(0x22,  True, False),      # System
    B0SubType.PANEL_STATE           : B0_SendMessageTuple(0x24,  True, False),      # Panel State
    B0SubType.WIRED_STATUS_1        : B0_SendMessageTuple(0x27,  True, False),
    B0SubType.WIRED_STATUS_2        : B0_SendMessageTuple(0x28,  True, False),
    B0SubType.EVENT_LOG             : B0_SendMessageTuple(0x2A,  True,  True),      # Event Log
    B0SubType.ZONE_TYPES            : B0_SendMessageTuple(0x2D,  True, False),      # Zone Types
    B0SubType.SENSOR_UNKNOWN_30     : B0_SendMessageTuple(0x30,  True, False),      # Sensors UNKNOWN ...  ???????????????????   0d b0 03 30 0d ff 01 03 08 00 00 00 00 00 00 00 00 dd 43 e1 0a  Decoded Chunk type 3   subtype 48   sequence 255  datasize 1    length 8    index ZONES            data 00 00 00 00 00 00 00 00
    B0SubType.SENSOR_UNKNOWN_32     : B0_SendMessageTuple(0x32,  True, False),      # Sensors UNKNOWN ...  ???????????????????   0d b0 03 32 0d ff 01 03 08 00 00 00 00 00 00 00 00 de 43 de 0a  Decoded Chunk type 3   subtype 50   sequence 255  datasize 1    length 8    index ZONES            data 00 00 00 00 00 00 00 00
    B0SubType.SENSOR_UNKNOWN_34     : B0_SendMessageTuple(0x34,  True, False),      # Sensors UNKNOWN ...  ???????????????????   0d b0 03 34 0d ff 01 03 08 00 00 00 00 00 00 00 00 df 43 db 0a  Decoded Chunk type 3   subtype 52   sequence 255  datasize 1    length 8    index ZONES            data 00 00 00 00 00 00 00 00
    B0SubType.PANEL_SETTINGS_35     : B0_SendMessageTuple(0x35,  True, False),
    B0SubType.LEGACY_EVENT_LOG      : B0_SendMessageTuple(0x36,  True, False),
    B0SubType.ASK_ME_1              : B0_SendMessageTuple(0x39,  True, False),      # Panel sending a list of message types that may have updated info
    B0SubType.ZONE_TEMPERATURE      : B0_SendMessageTuple(0x3D,  True, False),      # Zone Temperatures
#    "WIRELESS_DEVICES_40"       : B0_SendMessageTuple(0x40,  True, False),
    B0SubType.PANEL_SETTINGS_42     : B0_SendMessageTuple(0x42,  True, False),
    B0SubType.ZONE_LAST_EVENT       : B0_SendMessageTuple(0x4B,  True,  True),      # Zone Last Event. Paged for more than 30 sensors.
    B0SubType.ASK_ME_2              : B0_SendMessageTuple(0x51,  True, False),      # Panel sending a list of message types that may have updated info

    # Not currently used, experimentation only
    B0SubType.DEVICE_COUNTS         : B0_SendMessageTuple(0x52,  True, False),
    B0SubType.WIRED_DEVICES_53      : B0_SendMessageTuple(0x53,  True, False),      # X10_DEVICES and PGM
    B0SubType.TROUBLES              : B0_SendMessageTuple(0x54,  True, False),
    B0SubType.REPEATERS_55          : B0_SendMessageTuple(0x55,  True, False),
    B0SubType.DEVICE_INFO           : B0_SendMessageTuple(0x58,  True, False),
    B0SubType.GSM_STATUS            : B0_SendMessageTuple(0x59,  True, False),
    B0SubType.KEYPADS               : B0_SendMessageTuple(0x5b,  True, False),
    B0SubType.DEVICES_5D            : B0_SendMessageTuple(0x5d,  True, False),      # PM10 gave invalid
    B0SubType.SOFTWARE_VERSION      : B0_SendMessageTuple(0x64,  True, False),
    B0SubType.SIRENS                : B0_SendMessageTuple(0x66,  True, False),
    B0SubType.EPROM_AND_SW_VERSION  : B0_SendMessageTuple(0x69,  True, False),      # PM10 gave invalid
    B0SubType.KEEP_ALIVE            : B0_SendMessageTuple(0x6a,  True, False),
    B0SubType.SOME_LOG_75           : B0_SendMessageTuple(0x75,  True, False),
    B0SubType.IOVS                  : B0_SendMessageTuple(0x76,  True, False),
    B0SubType.TIMED_PGM_COMMAND     : B0_SendMessageTuple(0x7a,  True, False),      # for sending PGM on for timed period (secs) - 0d b0 00 7a 0b 31 80 01 ff 20 0b 04 00 01 3c 00 43 67 0a

    # 0x59 Message msgType=3 subType=89 not known about,  its chunky.   data = 03 59 0a ff 28 ff 05 01 01 16 00 00 72 43
    #             Decoded Chunk type 3   subtype 89   sequence 255  datasize 40   length 5    index MIXED            data 01 01 16 00 00
    # 0x66 Message msgType=3 subType=102 not known about, its chunky.   data = 03 66 0b ff 08 02 06 00 00 00 00 00 00 63 43
    #             Decoded Chunk type 3   subtype 102  sequence 255  datasize 8    length 6    index SIRENS           data 00 00 00 00 00 00
   
    B0SubType.ZONE_LUX              : B0_SendMessageTuple(0x77,  True, False),      # Zone Luminance / lux.  Tried asking for this and didn't get it on my PM10.
}

# Create a reverse lookup e.g. given the 0x0A then get the enumeration B0SubType.TAMPER_ACTIVITY
pmSendMsgB0_reverseLookup = { v.data : B0_SendMessageTuple(k, v.chunky, v.paged) for k,v in pmSendMsgB0.items() }

# Data to embed in the MSG_ARM message
#  All values in HEX
#     1/2/3/7/8/9/A/11/12/13/17/18/19/1A/1B/21/22/23  Access Denied
#     6/16      User Test
#     B         Mute Siren
#     20        Probably disarm but not tested
#     0/10      Disarm (not sure whether 10 is Disarm Instant)
#     4/C/E/24  Arm Home
#     5/D/F/25  Arm Away
#     14/1C/1E  Arm Home Instant
#     15/1D/1F  Arm Away Instant
pmArmMode = {
   AlPanelCommand.DISARM : 0x00, AlPanelCommand.ARM_HOME : 0x04, AlPanelCommand.ARM_AWAY : 0x05, AlPanelCommand.ARM_HOME_INSTANT : 0x14, AlPanelCommand.ARM_AWAY_INSTANT : 0x15    # "usertest" : 0x06,
}

# Data to embed in the MSG_PM_SIREN_MODE message
# PowerMaster to command the siren mode
pmSirenMode = {
   AlPanelCommand.EMERGENCY : 0x23, AlPanelCommand.FIRE : 0x20, AlPanelCommand.PANIC : 0x0C
}

# Data to embed in the MSG_X10PGM message
pmX10State = {
   AlX10Command.OFF : 0x00, AlX10Command.ON : 0x01, AlX10Command.DIMMER : 0x0A, AlX10Command.BRIGHTEN : 0x0B
}

##############################################################################################################################################################################################################################################
##########################  Messages that we can receive from the panel  #####################################################################################################################################################################
##############################################################################################################################################################################################################################################

# Message types we can receive with their length and whether they need an ACK.
#    When isvariablelength is True:
#             the length is the fixed number of bytes in the message.  Add this to the flexiblelength when it is received to get the total packet length.
#             varlenbytepos is the byte position of the variable length of the message.
#    flexiblelength provides support for messages that have a variable length
#    ignorechecksum is for messages that do not have a checksum.  These are F1 and F4 messages (so far)
#    When length is 0 then we stop processing the message on the first Packet.FOOTER. This is only used for the short messages (4 or 5 bytes long) like ack, stop, denied and timeout
PanelCallBack = collections.namedtuple("PanelCallBack", 'length ackneeded isvariablelength varlenbytepos flexiblelength ignorechecksum debugprint msg' )
pmReceiveMsg = {
   Receive.DUMMY_MESSAGE      : PanelCallBack(  0,  True, False, -1, 0, False, DebugLevel.NONE,                          "Dummy Message" ),       # Dummy message used in the algorithm when the message type is unknown. The -1 is used to indicate an unknown message in the algorithm
   Receive.ACKNOWLEDGE        : PanelCallBack(  0, False, False,  0, 0, False, DebugLevel.NONE,                          "Acknowledge" ),         # Ack
   Receive.TIMEOUT            : PanelCallBack(  0,  True, False,  0, 0, False,      RecvDebugC,                          "Timeout" ),             # Timeout. See the receiver function for ACK handling
   Receive.UNKNOWN_07         : PanelCallBack(  0,  True, False,  0, 0, False,      RecvDebugC,                          "Unknowm 07" ),          # No idea what this means but decode it anyway
   Receive.ACCESS_DENIED      : PanelCallBack(  0,  True, False,  0, 0, False,      RecvDebugC,                          "Access Denied" ),       # Access Denied
   Receive.LOOPBACK_TEST      : PanelCallBack(  0, False, False,  0, 0, False, DebugLevel.FULL,                          "Loopback Test" ),       # THE PANEL DOES NOT SEND THIS. THIS IS USED FOR A LOOP BACK TEST
   Receive.EXIT_DOWNLOAD      : PanelCallBack(  0,  True, False,  0, 0, False,      RecvDebugC,                          "Exit Download" ),       # The panel may send this during download to tell us to exit download 
   Receive.NOT_USED           : PanelCallBack( 14,  True, False,  0, 0, False, DebugLevel.FULL,                          "Not Used" ),            # 14 Panel Info (older visonic powermax panels so not used by this integration)
   Receive.DOWNLOAD_RETRY     : PanelCallBack( 14,  True, False,  0, 0, False, DebugLevel.CMD  if OBFUS else RecvDebugD, "Download Retry" ),      # 14 Download Retry
   Receive.DOWNLOAD_SETTINGS  : PanelCallBack( 14,  True, False,  0, 0, False, DebugLevel.NONE if OBFUS else RecvDebugD, "Download Settings" ),   # 14 Download Settings
   Receive.PANEL_INFO         : PanelCallBack( 14,  True, False,  0, 0, False, DebugLevel.FULL,                          "Panel Info" ),          # 14 Panel Info
   Receive.DOWNLOAD_BLOCK     : PanelCallBack(  7,  True,  True,  4, 5, False, DebugLevel.CMD  if OBFUS else RecvDebugD, "Download Block" ),      # Download Info in varying lengths  (For variable length, the length is the fixed number of bytes). This contains panel data so don't log it.
   Receive.EVENT_LOG          : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugM,                          "Event Log (A0)" ),      # 15 Event Log
   Receive.ZONE_NAMES         : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugM,                          "Zone Names (A3)" ),     # 15 Zone Names
   Receive.STATUS_UPDATE      : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugM,                          "Status Update (A5)" ),  # 15 Status Update       Length was 15 but panel seems to send different lengths
   Receive.ZONE_TYPES         : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugM,                          "Zone types (A6)" ),     # 15 Zone Types
   Receive.PANEL_STATUS       : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugM,                          "Panel Status (A7)" ),   # 15 Panel Status Change
   Receive.POWERLINK          : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugC,                          "Powerlink (AB)" ),      # 15 Enrol Request 0x0A  OR Ping 0x03      Length was 15 but panel seems to send different lengths
   Receive.X10_NAMES          : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugC,                          "X10 Names" ),           # 15 X10 Names
   Receive.IMAGE_MGMT         : PanelCallBack( 15,  True, False,  0, 0, False, DebugLevel.CMD  if OBFUS else RecvDebugI, "JPG Mgmt" ),            # 15 Panel responds with this when we ask for JPG images
   Receive.POWERMASTER        : PanelCallBack(  8,  True,  True,  4, 2, False, DebugLevel.CMD  if OBFUS else RecvDebugM, "PowerMaster (B0)" ),    # The B0 message comes in varying lengths, sometimes it is shorter than what it states and the CRC is sometimes wrong
   Receive.REDIRECT           : PanelCallBack(  5, False,  True,  2, 0, False, DebugLevel.FULL,                          "Redirect" ),            # TESTING: These are redirected Powerlink messages. 0D C0 len <data> cs 0A   so 5 plus the original data length
   Receive.PROXY              : PanelCallBack( 11,  True, False,  0, 0, False, DebugLevel.FULL,                          "Proxy" ),               # VISPROX : Interaction with Visonic Proxy
   # The F1 message needs to be ignored, I have no idea what it is but the crc is always wrong and only Powermax+ panels seem to send it. Assume a minimum length of 9, a variable length and ignore the checksum calculation.
   Receive.UNKNOWN_F1         : PanelCallBack(  9,  True,  True,  0, 0,  True,      RecvDebugC,                          "Unknown F1" ),          # Ignore checksum on all F1 messages
   # The F4 message comes in varying lengths. It is the image data from a PIR camera. Ignore checksum on all F4 messages
   Receive.IMAGE_DATA : {0x01 : PanelCallBack(  9, False, False,  0, 0,  True, RecvDebugI,                               "Image Footer" ),        # 
                         0x03 : PanelCallBack(  9, False,  True,  5, 0,  True, RecvDebugI,                               "Image Header" ),        # Image Header
                         0x05 : PanelCallBack(  9, False,  True,  5, 0,  True, RecvDebugI,                               "Image Data" ),          # Image Data Sequence
                         0x15 : PanelCallBack( 13, False, False,  0, 0,  True, RecvDebugI,                               "Image Unknown" ) 
          }
}

##############################################################################################################################################################################################################################################
##########################  B0 Data to Retrieve ##############################################################################################################################################################################################
##############################################################################################################################################################################################################################################
PanelSettingsCollection = collections.namedtuple('PanelSettingsCollection', 'sequence length processinstandard display datatype datacount msg') # overall length in bytes, datatype in bits
pmPanelSettingsB0_35 = {
   0x0000 : PanelSettingsCollection(      None,   6,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         6, "Central Station Account Number 1"),  # size of each entry is 6 nibbles
   0x0001 : PanelSettingsCollection(      None,   6,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         6, "Central Station Account Number 2"),  # size of each entry is 6 nibbles
   0x0002 : PanelSettingsCollection(      None,   0,  True, B0_35_PANEL_DATA_LOG,                DataType.FF_PADDED_STRING,          0, "Panel Serial Number"),
   0x0003 : PanelSettingsCollection(      None,   9,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,        12, "Central Station IP 1"),              # 12 nibbles e.g. 192.168.010.001
   0x0004 : PanelSettingsCollection(      None,   6,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         0, "Central Station Port 1"),            # 
   0x0005 : PanelSettingsCollection(      None,   9,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,        12, "Central Station IP 2"),              # 12 nibbles
   0x0006 : PanelSettingsCollection(      None,   6,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         0, "Central Station Port 2"),            # 
   0x0007 : PanelSettingsCollection(      None,  39,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                   0, "Capabilities unknown"),              # 
   0x0008 : PanelSettingsCollection(      None,  99, False, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         2, "User Code"),                         # size of each entry is 4 nibbles
   0x000D : PanelSettingsCollection( [1,2,255],   0,  True, B0_35_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING_LIST, 32, "Zone Names"),                        # 32 nibbles i.e. each string name is 16 bytes long. The 0x35 message has 3 sequenced messages.
   0x000F : PanelSettingsCollection(      None,   5,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         4, "Download Code"),                     # size of each entry is 4 nibbles
   0x0010 : PanelSettingsCollection(      None,   4,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                   0, "Panel EPROM Version 1"),
 # 0x0011 SMS_MMS_BY_SERVER_TEL1 
 # 0x0012 SMS_MMS_BY_SERVER_TEL2 
 # 0x0013 SMS_MMS_BY_SERVER_TEL3 
 # 0x0014 SMS_MMS_BY_SERVER_TEL4 
 # 0x0015 EMAIL_BY_SERVER_EMAIL1 
 # 0x0016 EMAIL_BY_SERVER_EMAIL2 
 # 0x0017 EMAIL_BY_SERVER_EMAIL3 
 # 0x0018 EMAIL_BY_SERVER_EMAIL4 
 # 0x0019 SOME_SETTINGS25
   0x0024 : PanelSettingsCollection(      None,   5,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                   0, "Panel EPROM Version 2"),
 # 0x0027 TYPE_OFFSETS - no idea what this means!
 # 0x0028 CAPABILITIES 
   0x0029 : PanelSettingsCollection(      None,   4,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.INTEGER,                   0, "Unknown B"),
 # 0x002B UNKNOWN_SOFTWARE_VERSION
   0x002C : PanelSettingsCollection(      None,  19,  True, B0_35_PANEL_DATA_LOG,                DataType.STRING,                    0, "Panel Default Version"),
   0x002D : PanelSettingsCollection(      None,  19,  True, B0_35_PANEL_DATA_LOG,                DataType.STRING,                    0, "Panel Software Version"),
   0x0030 : PanelSettingsCollection(      None,   4,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                   0, "Partition Enabled"),
 # 0x0031 ASSIGNED_ZONE_TYPES
 # 0x0032 ASSIGNED_ZONE_NAMES
   0x0033 : PanelSettingsCollection(      None,  67,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                  64, "Zone Chime Data"),
 # 0x0034 MAP_VALUE
 # 0x0035 MAP_VALUE_2
   0x0036 : PanelSettingsCollection(      None,  67,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                  64, "Partition Data"),
 # 0x0037 TAG_PARTITION_ASSIGNMENT
 # 0x0038 KEYPAD_PARTITION_ASSIGNMENT
 # 0x0039 SIREN_PARTITION_ASSIGNMENT
   0x003C : PanelSettingsCollection(      None,   0,  True, B0_35_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING,       0, "Panel Hardware Version"),
   0x003D : PanelSettingsCollection(      None,  19,  True, B0_35_PANEL_DATA_LOG,                DataType.STRING,                    0, "Panel RSU Version"),
   0x003E : PanelSettingsCollection(      None,  19,  True, B0_35_PANEL_DATA_LOG,                DataType.STRING,                    0, "Panel Boot Version"),
   0x0042 : PanelSettingsCollection( [1,2,255],   0,  True, B0_35_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING_LIST, 32, "Custom Zone Names"),
 # 0x0045 ZONE_NAMES2
 # 0x0046 CUSTOM_ZONE_NAMES2
 # 0x0047 H24_TIME_FORMAT
 # 0x0048 US_DATE_FORMAT
 # 0x004D PRIVATE_REPORTING_TELNOS
 # 0x004E MAX_PARTITIONS - NEEDS CHECKING SHOWS 03
 # 0x0051 SMS_REPORT_NUMBERS
   0x0054 : PanelSettingsCollection(      None,   5,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         4, "Installer Code"),                    # size of each entry is 4 nibbles
   0x0055 : PanelSettingsCollection(      None,   5,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         4, "Master Code"),                       # size of each entry is 4 nibbles
 # 0x0056 GUARD_CODE
 # 0x0057 EN50131_EXIT_DELAYS
   0x0058 : PanelSettingsCollection(      None,   4,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.INTEGER,                   2, "Unknown D"),                         # 0x0058 EXIT_DELAY maybe
   0x0106 : PanelSettingsCollection(      None,   4,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.INTEGER,                   1, "Unknown A"),                         # size of each entry is 4 nibbles
}                                                                         

pmPanelSettingsB0_42 = {                           
   0x0000 : PanelSettingsCollection(      None,  20,  True, B0_42_PANEL_DATA_LOG,                DataType.DIRECT_MAP_STRING,         3, "Central Station Account Number 1"),  # size of each entry is 6 nibbles
   0x0002 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.FF_PADDED_STRING,          0, "Panel Serial Number"),
   0x0008 : PanelSettingsCollection(      None,   0, False, B0_42_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         2, "User Code"),                         # size of each entry is 4 nibbles
   0x000D : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING_LIST, 30, "Zone Names"),                        # 32 nibbles i.e. each string name is 16 bytes long. The 0x35 message has 3 sequenced messages.
   0x0030 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.INTEGER,                   1, "Partition Enabled"),
   0x0033 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.INTEGER,                   1, "Zone Chime Data"),
   0x0036 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.INTEGER,                   1, "Partition Data"),
   0x003C : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING,       0, "Panel Hardware Version"),
   0x0042 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING_LIST, 32, "Custom Zone Names"),                 # variable length with the sequence of messages
#   0x00a4 : PanelSettingsCollection(     None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.INTEGER,                   2, "XXXXXXXXX"),
   0x0106 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG and not OBFUS,  DataType.INTEGER,                   1, "Unknown A"),                         # size of each entry is 4 nibbles
}                                                                       


##############################################################################################################################################################################################################################################
##########################  Panel Data to Retrieve using a combination of EPROM and (for PowerMaster Panels) B0 message data #################################################################################################################
##############################################################################################################################################################################################################################################
# pmPanelSettingCodes represents the ways that we can get data to populate the PanelSettings
#   A PowerMax Panel only has 1 way and that is to download the EPROM = PMaxEPROM
#   A PowerMaster Panel has 3 ways:
#        1. Download the EPROM = PMasterEPROM
#        2. Ask the panel for a B0 panel settings message 0x51 e.g. 0x0800 sends the user codes  = PMasterB035Panel
#        3. Ask the panel for a B0 data message = PMasterB0Mess PMasterB0Index

# Zone names are translated using the language translation file. These need to match the keys in the translations.
pmZoneName = [
   "attic", "back_door", "basement", "bathroom", "bedroom", "child_room", "conservatory", "play_room", "dining_room", "downstairs",
   "emergency", "fire", "front_door", "garage", "garage_door", "guest_room", "hall", "kitchen", "laundry_room", "living_room",
   "master_bathroom", "master_bedroom", "office", "upstairs", "utility_room", "yard", "custom_1", "custom_2", "custom_3",
   "custom_4", "custom_5", "not_installed"
]

# These are conversion to string functions
def psc_lba(p):   # p = a list of bytearrays
    s = ""
    for ba in p:
        s = s + toString(ba, "") + " "
    return s[:-1] if len(s) > 0 else s

def psc_dummy(p):
    return p

PanelSettingCodesType = collections.namedtuple('PanelSettingCodesType', 'item mandatory PMaxEPROM PMasterEPROM PMasterB035Panel PMasterB042Panel PMasterB0Mess PMasterB0Index tostring default')
# For PMasterB0Mess there is an assumption that the message type is 0x03, and this is the subtype
#       PMasterB0Index index 3 is Sensor data, I should have an enum for this
#       mandatory : When True, this setting means that the data is mandatory before creating sensors when trying for Powerlink emulation mode
# These are used to create the self.PanelSettings dictionary to create a common set of settings across the different ways of obtaining them
pmPanelSettingCodes = { #                                  item mandatory PMaxEPROM            PMasterEPROM        PMasterB035Panel PMasterB042Panel PMasterB0Mess          PMasterB0Index   tostring       default
    PanelSetting.UserCodes        : PanelSettingCodesType( None,  True, EPROM.USERCODE_MAX,   EPROM.USERCODE_MAS,   None  ,         0x0008,         None,                   None,             toString ,     bytearray([0,0]) ),
    PanelSetting.PartitionData    : PanelSettingCodesType( None,  True, EPROM.PART_ZONE_DATA, EPROM.PART_ZONE_DATA, None  ,         0x0036,         None,                   None,             toString ,     bytearray()),
    PanelSetting.ZoneNames        : PanelSettingCodesType( None,  True, EPROM.ZONENAME_MAX,   EPROM.ZONENAME_MAS,   None  ,         None  ,         B0SubType.ZONE_NAMES,   IndexName.ZONES,  toString ,     bytearray()),
    PanelSetting.ZoneNameString   : PanelSettingCodesType( None, False, EPROM.ZONE_STR_NAM,   EPROM.ZONE_STR_NAM,   None  ,         0x000D,         None,                   None,             psc_dummy,     [] ), # pmZoneName[0:21] ),       # The string names themselves
    PanelSetting.ZoneCustNameStr  : PanelSettingCodesType( None, False, EPROM.ZONE_STR_EXT,   EPROM.ZONE_STR_EXT,   None  ,         0x0042,         None,                   None,             psc_dummy,     [] ), # pmZoneName[21:31] ),      # The string names themselves
    PanelSetting.ZoneTypes        : PanelSettingCodesType( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.ZONE_TYPES,   IndexName.ZONES,  toString ,     bytearray()),           
    PanelSetting.ZoneExt          : PanelSettingCodesType( None,  True, None,                 EPROM.ZONEEXT_MAS,    None  ,         None  ,         None,                   None,             toString ,     bytearray()),
    PanelSetting.DeviceTypesZones : PanelSettingCodesType( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.DEVICE_TYPES, IndexName.ZONES,  toString ,     bytearray()),
    PanelSetting.DeviceTypesSirens: PanelSettingCodesType( None, False, None,                 None,                 None  ,         None  ,         B0SubType.DEVICE_TYPES, IndexName.SIRENS, toString ,     bytearray()),
    PanelSetting.HasPGM           : PanelSettingCodesType( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.SYSTEM_CAP,   IndexName.PGM,    psc_dummy,     []),
    PanelSetting.ZoneDelay        : PanelSettingCodesType( None,  True, None,                 EPROM.ZONE_DEL_MAS,   None  ,         None  ,         None,                   None,             toString ,     bytearray(64)),     # Initialise to 0s so it passes the I've got it from the panel test until I know how to get this using B0 data
    PanelSetting.ZoneData         : PanelSettingCodesType( None,  True, EPROM.ZONEDATA_MAX,   EPROM.ZONEDATA_MAS,   None  ,         None  ,         None,                   None,             toString ,     bytearray()),
    PanelSetting.ZoneEnrolled     : PanelSettingCodesType( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.SENSOR_ENROL, IndexName.ZONES,  psc_dummy,     [] ),               # Powermax relies on EPROM data or A5 message to provide sensor enrol         
    PanelSetting.PanelBypass      : PanelSettingCodesType( 0,     True, EPROM.PANEL_BYPASS,   EPROM.PANEL_BYPASS,   None  ,         None  ,         None,                   None,             psc_dummy,     [NOBYPASSSTR]),
    PanelSetting.PanelDownload    : PanelSettingCodesType( None, False, EPROM.INSTALDLCODE,   EPROM.INSTALDLCODE,   None  ,         0x000f,         None,                   None,             psc_dummy,     bytearray()),
    PanelSetting.PanelSerial      : PanelSettingCodesType( None, False, EPROM.PANEL_SERIAL,   EPROM.PANEL_SERIAL,   None  ,         0x0002,         None,                   None,             toString,      bytearray() ),
    PanelSetting.PanelName        : PanelSettingCodesType( None, False, None,                 None,                 None  ,         0x003C,         None,                   None,             toString ,     bytearray() ),
    PanelSetting.PartitionEnabled : PanelSettingCodesType( None,  True, EPROM.PART_ENABLED,   EPROM.PART_ENABLED,   None  ,         0x0030,         None,                   None,             toString ,     bytearray() ),
    PanelSetting.ZoneChime        : PanelSettingCodesType( None,  True, None,                 None,                 None  ,         0x0033,         None,                   None,             toString ,     bytearray() )
}

#   PanelSetting.TestTest         : PanelSettingCodesType( None, None,               None,                       None  ,         0x0031,         None,           None,            toString ,     bytearray() ),
#   PanelSetting.Keypad_1Way      : PanelSettingCodesType( None, EPROM.KEYPAD_1_MAX, None,                       None  ,         None  ,         None,           None,            toString ,     bytearray()),      # PowerMaster Panels do not have 1 way keypads
#   PanelSetting.Keypad_2Way      : PanelSettingCodesType( None, EPROM.KEYPAD_2_MAX, EPROM.KEYPAD_MAS,           None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.KeyFob           : PanelSettingCodesType( None, "KeyFobsPMax",      "",                         None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.Sirens           : PanelSettingCodesType( None, EPROM.SIRENS_MAX,   EPROM.SIRENS_MAS,           None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.AlarmLED         : PanelSettingCodesType( None, None,               "AlarmLED",                 None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.ZoneSignal       : PanelSettingCodesType( None, "ZoneSignalPMax",   "",                         None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.PanicAlarm       : PanelSettingCodesType( 0,    "panicAlarm",       "panicAlarm",               None  ,         None  ,         None,           None,            psc_dummy,     [False]),
#   PanelSetting.PanelModel       : PanelSettingCodesType( 0,    EPROM.PANEL_MODEL_CODE, EPROM.PANEL_MODEL_CODE, None  ,         None  ,         None,           None,            psc_lba  ,     [bytearray([0,0,0,0])]),


##############################################################################################################################################################################################################################################
##########################  Known Sensor Types ###############################################################################################################################################################################################
##############################################################################################################################################################################################################################################

# Default Sensor Zone Types
pmZoneTypeKey = ( "non-alarm", "emergency", "flood", "gas", "delay_1", "delay_2", "interior_follow", "perimeter", "perimeter_follow",
                "24_hours_silent", "24_hours_audible", "fire", "interior", "home_delay", "temperature", "outdoor", "undefined" )

# Default Sensor Chime
pmZoneChimeKey = ("chime_off", "melody_chime", "zone_name_chime")

# Default Sensor Types if not found in the dictionaries below
pmZoneMaxGeneric = {
   0x0 : AlSensorType.VIBRATION, 0x2 : AlSensorType.SHOCK, 0x3 : AlSensorType.MOTION, 0x4 : AlSensorType.MOTION, 0x5 : AlSensorType.MAGNET,
   0x6 : AlSensorType.MAGNET, 0x7 : AlSensorType.MAGNET, 0x8 : AlSensorType.MAGNET, 0x9 : AlSensorType.MAGNET, 
   0xA : AlSensorType.SMOKE, 0xB : AlSensorType.GAS, 0xC : AlSensorType.MOTION, 0xF : AlSensorType.WIRED
} # unknown to date: Push Button, Flood, Universal

#0x75 : ZoneSensorType("Next+ K9-85 MCW", AlSensorType.MOTION ), # Jan
#0x86 : ZoneSensorType("MCT-426", AlSensorType.SMOKE ), # Jan
ZoneSensorType = collections.namedtuple("ZoneSensorType", 'name func' )
pmZoneMax = {
   0x6D : ZoneSensorType("MCX-601 Wireless Repeater", AlSensorType.IGNORED ),       # Joao-Sousa   ********************* Wireless Repeater so exclude it **************
   0x08 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # Fabio72
   0x09 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # Fabio72
   0x1A : ZoneSensorType("MCW-K980", AlSensorType.MOTION ),        # Botap
   0x6A : ZoneSensorType("MCT-550", AlSensorType.FLOOD ),          # Joao-Sousa
   0x74 : ZoneSensorType("Next+ K9-85", AlSensorType.MOTION ),     # christopheVia
#   0x75 : ZoneSensorType("Next K9-85", AlSensorType.MOTION ),      # thermostat (Visonic part number 0-3592-B, NEXT K9-85 DDMCW)
   0x75 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # 15/9/23 rogerthn2019 (Powermax Pro) and others have this sensor so removed the previous setting, also ending in 5 should be a magnet
   0x76 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # open1999
   0x7A : ZoneSensorType("MCT-550", AlSensorType.FLOOD ),          # fguerzoni, Joao-Sousa
   0x86 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # Joao-Sousa
   0x87 : ZoneSensorType("MCT-302", AlSensorType.MAGNET ),         # Jan/eijlers
   0x8A : ZoneSensorType("MCT-550", AlSensorType.FLOOD ),          # Joao-Sousa
   0x93 : ZoneSensorType("Next MCW", AlSensorType.MOTION ),        # Tomas-Corral
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
pmZoneMaster = {
   0x01 : ZoneSensorType("Next PG2", AlSensorType.MOTION ),
   0x03 : ZoneSensorType("Clip PG2", AlSensorType.MOTION ),
   0x04 : ZoneSensorType("Next CAM PG2", AlSensorType.CAMERA ),
   0x05 : ZoneSensorType("GB-502 PG2", AlSensorType.SOUND ),
   0x06 : ZoneSensorType("TOWER-32AM PG2", AlSensorType.MOTION ),
   0x07 : ZoneSensorType("TOWER-32AMK9", AlSensorType.MOTION ),
   0x08 : ZoneSensorType("TOWER-20AM PG2", AlSensorType.MOTION ),
   0x0A : ZoneSensorType("TOWER CAM PG2", AlSensorType.CAMERA ),
   0x0B : ZoneSensorType("GB-502 PG2", AlSensorType.GLASS_BREAK),
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
   0xFA : ZoneSensorType("MC-302E PG2", AlSensorType.MAGNET ),          # iaxexo
   0xFE : ZoneSensorType("Wired", AlSensorType.WIRED )
}

ZoneDeviceType = collections.namedtuple("ZoneDeviceType", 'name func' )
pmSirenMaster = {
   0x01 : ZoneDeviceType("SR-730 PG2 Outdoor Siren", AlDeviceType.EXTERNAL ),
   0x02 : ZoneDeviceType("SR-720 PG2 Indoor Siren", AlDeviceType.INTERNAL )
}

pmKeyfobMaster = {
   0x01 : ZoneDeviceType("Keyfob", AlDeviceType.EXTERNAL ),
   0x02 : ZoneDeviceType("KF-235 PG2", AlDeviceType.EXTERNAL )
}

pmKeypadMaster = {
   0x05: ZoneDeviceType("KP-160 PG2", AlDeviceType.EXTERNAL)
}

##############################################################################################################################################################################################################################################
##########################  Data Driven Message Decode #######################################################################################################################################################################################
##############################################################################################################################################################################################################################################

# These functions must exist inside the Sensor Class, they must only have a single parameter. NO_ACTION will fail and not make the call.
class ZoneFunctions(StrEnum):          
    NO_ACTION   = ""
    PUSH_CHANGE = "pushChange"
    DO_TAMPER   = "do_tamper"
    DO_STATUS   = "do_status"
    DO_BATTERY  = "do_battery"
    DO_TRIGGER  = "do_trigger"
    DO_ZTRIP    = "do_ztrip"
    DO_ZTAMPER  = "do_ztamper"
    DO_BYPASS   = "do_bypass"
    DO_INACTIVE = "do_inactive"
    DO_MISSING  = "do_missing"
    DO_ONEWAY   = "do_oneway"

# These problem values are in the language json file file for zone_trouble
TEXT_NONE       = "none"
TEXT_TAMPER     = "tamper"
TEXT_JAMMING    = "jamming"
TEXT_COMM_FAIL  = "comm_failure"
TEXT_LINE_FAIL  = "line_failure"
TEXT_FUSE       = "fuse"
TEXT_NOT_ACTIVE = "not_active"
TEXT_AC_FAIL    = "ac_failure"

# The func values are looked up in the Sensor Class for a function call
# The problem values are in the language json file file for zone_trouble
# The parameter values are sent in with the function call (as the only parameter)
ZoneEventActionCollection = collections.namedtuple('ZoneEventActionCollection', 'func problem parameter')
pmZoneEventAction = {
      0 : ZoneEventActionCollection(ZoneFunctions.NO_ACTION,   TEXT_NONE,        None ),                        # "None",
      1 : ZoneEventActionCollection(ZoneFunctions.DO_TAMPER,   TEXT_TAMPER,      True ),                        # "Tamper Alarm",          
      2 : ZoneEventActionCollection(ZoneFunctions.DO_TAMPER,   TEXT_NONE,        False ),                       # "Tamper Restore",        
      3 : ZoneEventActionCollection(ZoneFunctions.DO_STATUS,   TEXT_NONE,        True ),                        # "Zone Open",             
      4 : ZoneEventActionCollection(ZoneFunctions.DO_STATUS,   TEXT_NONE,        False ),                       # "Zone Closed",           
      5 : ZoneEventActionCollection(ZoneFunctions.DO_TRIGGER,  TEXT_NONE,        True ),                        # "Zone Violated (Motion)",
      6 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_NONE,        AlSensorCondition.PANIC ),     # "Panic Alarm",           
      7 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_JAMMING,     AlSensorCondition.PROBLEM ),   # "RF Jamming",            
      8 : ZoneEventActionCollection(ZoneFunctions.DO_TAMPER,   TEXT_TAMPER,      True ),                        # "Tamper Open",           
      9 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_COMM_FAIL,   AlSensorCondition.PROBLEM ),   # "Communication Failure", 
     10 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_LINE_FAIL,   AlSensorCondition.PROBLEM ),   # "Line Failure",          
     11 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_FUSE,        AlSensorCondition.PROBLEM ),   # "Fuse",                  
     12 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_NOT_ACTIVE , AlSensorCondition.PROBLEM ),   # "Not Active" ,           
     13 : ZoneEventActionCollection(ZoneFunctions.DO_BATTERY,  TEXT_NONE,        True ),                        # "Low Battery",           
     14 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_AC_FAIL,     AlSensorCondition.PROBLEM ),   # "AC Failure",            
     15 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_NONE,        AlSensorCondition.FIRE ),      # "Fire Alarm",            
     16 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_NONE,        AlSensorCondition.EMERGENCY ), # "Emergency",             
     17 : ZoneEventActionCollection(ZoneFunctions.DO_TAMPER,   TEXT_TAMPER,      True ),                        # "Siren Tamper",          
     18 : ZoneEventActionCollection(ZoneFunctions.DO_TAMPER,   TEXT_NONE,        False ),                       # "Siren Tamper Restore",  
     19 : ZoneEventActionCollection(ZoneFunctions.DO_BATTERY,  TEXT_NONE,        True ),                        # "Siren Low Battery",     
     20 : ZoneEventActionCollection(ZoneFunctions.PUSH_CHANGE, TEXT_AC_FAIL,     AlSensorCondition.PROBLEM ),   # "Siren AC Fail",         
}

##############################################################################################################################################################################################################################################
##########################  Code Start  ######################################################################################################################################################################################################
##############################################################################################################################################################################################################################################

# get the current date and time
def getTimeFunction() -> datetime:
    return datetime.now(timezone.utc).astimezone()

def b2i(byte: bytes, big_endian: bool = False) -> int:
    """Convert hex to byte."""
    if big_endian:
        return int.from_bytes(byte, "big")
    return int.from_bytes(byte, "little")

class chunky:
    type     : int
    subtype  : int
    sequence : int  # sequence number
    datasize : int  # Bits -->  8 is 1 Byte, 1 is Bits, 4 is Nibbles, greater than 8 is total bits e.g. 40 is 5 Bytes
    index    : int  # 3 is Zones, 
    length   : int
    data     : bytearray

    def __init__(self, type = 0, subtype = 0, sequence = 0, datasize = 0, index = 0, length = 0, data = bytearray()):
        self.type = type
        self.subtype = subtype
        self.sequence = sequence
        self.datasize = datasize
        self.index = index
        self.length = length
        if data is None:
            self.data = bytearray()
        else:
            self.data = data

    def __str__(self):
        # Assume logging of all chunky data is ok unless disabled by the 0x42 or 0x35 setting
        #     Normal data does not include user codes etc, just panel and sensor status
        ok = True    
        if self.subtype == 0x42 and len(self.data) > 2:
            dataContent = b2i(self.data[0:2], big_endian=False)
            if dataContent in pmPanelSettingsB0_42:
                ok = pmPanelSettingsB0_42[dataContent].display
        elif self.subtype == 0x35 and len(self.data) > 2:
            dataContent = b2i(self.data[0:2], big_endian=False)
            if dataContent in pmPanelSettingsB0_35:
                ok = pmPanelSettingsB0_35[dataContent].display
        if not ok:
            return f"type {self.type:<2}  subtype {self.subtype:<3}  sequence {self.sequence:<3}  datasize {self.datasize:<3}  length {self.length:<3}  index {IndexName(self.index).name:<14}   obfus datalen = {len(self.data)}"
        return f"type {self.type:<2}  subtype {self.subtype:<3}  sequence {self.sequence:<3}  datasize {self.datasize:<3}  length {self.length:<3}  index {IndexName(self.index).name:<14}   data {toString(self.data)}"

    def GetItAll(self):
        # Get it all, ignore display setting and obfus
        return f"type {self.type:<2}  subtype {self.subtype:<3}  sequence {self.sequence:<3}  datasize {self.datasize:<3}  length {self.length:<3}  index {IndexName(self.index).name:<14}   data {toString(self.data)}"


# Entry in a queue of commands (and PDUs) to send to the panel
class VisonicListEntry:
    def __init__(self, command = None, raw = None, options = None, response = None):
        self.command = command # kwargs.get("command", None)
        self.options = options # kwargs.get("options", None)
        self.raw = raw
        self.response = [] if response is None else response
        if command is not None:
            if self.command.replytype is not None:
                self.response = self.command.replytype.copy()  # list of message reply needed
            # are we waiting for an acknowledge from the panel (do not send a message until we get it)
            if self.command.waitforack:
                self.response.append(Receive.ACKNOWLEDGE)  # add an acknowledge to the list
        self.triedResendingMessage = False
        self.created = getTimeFunction()

    def __str__(self):
        if self.command is not None:
            return f"Command:{self.command.msg}    Options:{self.options}"
        elif self.raw is not None:
            return f"Raw: {toString(self.raw)}"
        return "Command:None"

    def __lt__(self, other: object) -> bool:             # Implement < based on the creation time
        if not isinstance(other, VisonicListEntry):
            raise NotImplementedError
        return self.created < other.created

    def insertOptions(self, data : bytearray) -> bytearray:
        # push in the options in to the appropriate places in the message. Examples are the pin or the specific command
        if self.options != None:
            # the length of instruction.options has to be an even number
            # it is a list of couples:  bitoffset , bytearray to insert
            #op = int(len(instruction.options) / 2)
            # log.debug(f"[sendPdu] Options {instruction.options} {op}")
            for o in range(0, len(self.options)):
                s = self.options[o][0] # [o * 2]      # bit offset as an integer
                a = self.options[o][1] # [o * 2 + 1]  # the bytearray to insert
                if isinstance(a, int):
                    data[s] = a
                else:
                    for i in range(0, len(a)):
                        data[s + i] = a[i]
        return data

class SensorDevice(AlSensorDeviceHelper):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

class X10Device(AlSwitchDeviceHelper):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

def hexify(v : int) -> str:
    return format(v, '02x')  # f"{hex(v)[2:]}"

# This class handles the detailed low level interface to the panel.
#    It sends the messages
#    It builds and received messages from the raw byte stream and coordinates the acknowledges back to the panel
#    It checks CRC for received messages and creates CRC for sent messages
#    It coordinates the downloading of the EPROM (but doesn't decode the data here)
#    It manages the communication connection
class ProtocolBase(AlPanelInterfaceHelper, AlPanelDataStream, MyChecksumCalc):
    """Manage low level Visonic protocol."""

    log.debug(f"Initialising Protocol - Protocol Version {PLUGIN_VERSION}")

    def __init__(self, loop=None, panelConfig : PanelConfig = None, panel_id : int = None, packet_callback: Callable = None, logger = None) -> None:
        super().__init__(panel_id = panel_id, logger = logger)
        """Initialize class."""
        
        ####################################
        # Variables that do not get reset  #
        ####################################

        #if logger is not None:
        #    log = logger

        if loop:
            self.loop = loop
            log.debug("Establishing Protocol - Using Home Assistant Loop")
        else:
            self.loop = asyncio.get_event_loop()
            log.debug("Establishing Protocol - Using Asyncio Event Loop")

        # install the packet callback handler
        self.packet_callback = packet_callback

        self.transport = None  # type: asyncio.Transport

        self.WatchdogTimeoutCounter = 0
        self.WatchdogTimeoutPastDay = 0

        # Loopback capability added. Connect Rx and Tx together without connecting to the panel
        self.loopbackTest = False
        self.loopbackCounter = 0

        # Configured from the client INTERFACE
        #   These are the default values
        self.ForceStandardMode = False        # INTERFACE : Get user variable from HA to force standard mode or try for PowerLink
        self.DisableAllCommands = False       # INTERFACE : Get user variable from HA to allow or disable all commands to the panel 
        self.DownloadCodeUserSet = False
        
        self.pmForceDownloadByEPROM = False   # For PowerMaster panels, try the B0 messages first and if they dont work in 20 seconds then force EPROM download

        self.sequencerTask = None
        self.despatcherTask = None
        self.despatcherException = False

        # Mark's Powerlink Bridge
        self.PowerLinkBridgeConnected = False   # This is set true on first receipt of an E0.  It means that there is a server running to communicate with
        self.PowerLinkBridgeAlarm = False       # The server has an Alarm Panel connection
        self.PowerLinkBridgeStealth = False     # The server is in stealth mode (giving this integration sole access to the panel)
        self.PowerLinkBridgeProxy = False       # The server is acting in proxy mode i.e. it supports a Visonic Go connection to an external site)

        self.resetGlobals()

        # Now that the defaults have been set, update them from the panel config dictionary (that may not have all settings in)
        self.updateSettings(panelConfig)

    # This is used for debugging from command line
    def setLogger(self, loggy):
        super().setLogger(loggy)
        log = loggy

    def resetVariablesForNewConnection(self):
        self.lastRecvTimeOfPanelData = self._getUTCTimeFunction()    # Do not set to None
        self.pmCrcErrorCount = 0              # The CRC Error Count for Received Messages
        #self.PanelMode = AlPanelMode.STARTING
        # keep alive counter for the timer
        self.keep_alive_counter = 0  # only used in _sequencer
        # This is the time stamp of the last Send
        self.pmLastTransactionTime = self._getUTCTimeFunction() - timedelta(seconds=1)  # take off 1 second so the first command goes through immediately
        # This is the time stamp of the CRC error
        self.pmFirstCRCErrorTime = self._getUTCTimeFunction() - timedelta(seconds=1)    # take off 1 second so the first command goes through immediately
        self.resetMessageData()
        # The last sent message
        self._clearReceiveResponseList()

    def resetGlobals(self):
        ########################################################################
        # Global Variables that define the overall panel status
        ########################################################################
        self.PanelMode = AlPanelMode.STARTING

        # Set when the panel details have been received i.e. a 3C message
        self.pmGotPanelDetails = False
        # Can the panel support the INIT Command
        self.ModelType = None
        self.PanelType = 17                  # We do not yet know the paneltype so set default settings
        self.PanelModel = "UNKNOWN"
        self.PowerMaster = None              # Set to None to represent unknown until we know True or False
        self.AutoEnrol = True
        self.AutoSyncTime = False
        self.pmDownloadByEPROM = False
        self.KeepAlivePeriod = KEEP_ALIVE_PERIOD
        self.pmInitSupportedByPanel = False

        self.firstCmdSent = False
        self.lastPacket = None

        # A queue of messages to send (i.e. VisonicListEntry)
        self.SendQueue = PriorityQueueWithPeek()

        # partition related data
        self.partitionsEnabled = False
        self.PartitionsInUse = set()  # this is a set so no repetitions allowed
        self.PartitionState[0].Reset()
        self.PartitionState[1].Reset()
        self.PartitionState[2].Reset()
        self.PanelStatus = {}                # This is the set of EPROM settings shown

        self.PanelCapabilities = {}

        self.PanelSettings = {}              # This is the record of settings for the integration to work
        for key in pmPanelSettingCodes:
            self.PanelSettings[key] = pmPanelSettingCodes[key].default.copy()     # populate each setting with the default

        self.B0_Wanted = set()
        self.B0_Waiting = set()
        self.B0_LastPanelStateTime = self._getUTCTimeFunction()

        # this is the watchdog counter (in seconds)
        self.watchdog_counter = 0

        # Time difference between Panel and Integration 
        self.Panel_Integration_Time_Difference = None

        # The last sent message
        self._clearReceiveResponseList()

        # Timestamp of the last received data from the panel. If this remains set to none then we have a comms problem
        self.lastRecvTimeOfPanelData = None

        # This is the time stamp of the last Send
        self.pmLastTransactionTime = self._getUTCTimeFunction() - timedelta(seconds=1)  # take off 1 second so the first command goes through immediately

        # This is the time stamp of the CRC error
        self.pmFirstCRCErrorTime = self._getUTCTimeFunction() - timedelta(seconds=1)    # take off 1 second so the first command goes through immediately

        # When to stop trying to download the EPROM
        self.StopTryingDownload = False

        # When we are downloading the EPROM settings and finished parsing them and setting up the system.
        #   There should be no user (from Home Assistant for example) interaction when self.pmDownloadMode is True
        self.pmDownloadInProgress = False
        self.pmDownloadMode = False
        self.triggeredDownload = False
        self.myDownloadList = []
        self.DownloadCounter = 0
        if not self.DownloadCodeUserSet:
            self.DownloadCode = DEFAULT_DL_CODE   # INTERFACE : Set the Download Code
        # Set when we receive a STOP from the panel, indicating that the EPROM data has finished downloading
        self.pmDownloadComplete = False
        # Download block retry count (this is for individual 3F download failures)
        self.pmDownloadRetryCount = 0

        self.PanelResetEvent = False
        self.PanelWantsToEnrol = False
        self.UnexpectedPanelKeepAlive = False
        self.TimeoutReceived = False
        self.ExitReceived = False
        self.DownloadRetryReceived = False
        self.AccessDeniedReceived = False
        self.AccessDeniedMessage = None
        self.gotBeeZeroInvalidCommand = False
        self.EnableB0ReceiveProcessing = False

        # When trying to connect in powerlink from the timer loop, this allows the receipt of a powerlink ack to trigger a MSG_RESTORE
        self.allowAckToTriggerRestore = False
        self.receivedPowerlinkAcknowledge = False

        ########################################################################
        # Variables that are only used in handle_received_message function
        ########################################################################
        self.pmIncomingPduLen = 0             # The length of the incoming message
        self.pmCrcErrorCount = 0              # The CRC Error Count for Received Messages
        self.pmCurrentPDU = pmReceiveMsg[0]   # The current receiving message type
        self.pmFlexibleLength = 0             # How many bytes less then the proper message size do we start checking for Packet.FOOTER and a valid CRC
        # The receive byte array for receiving a message
        self.ReceiveData = bytearray(b"")

        # keep alive counter for the timer
        self.keep_alive_counter = 0  # only used in _sequencer

        self.epromManager = EPROMManager()

        # Current F4 jpg image 
        self.ImageManager = AlImageManager()
        self.ignoreF4DataMessages = False
        self.image_ignore = set()

    def shutdownOperation(self):
        if not self.suspendAllOperations:
            super().shutdownOperation()
            #self._initVars()
            # empty the panel settings data when stopped
            self.PanelCapabilities = {}
            self.PanelSettings = {}
            # empty the EPROM data when stopped
            self.epromManager.reset()

            self.lastRecvTimeOfPanelData = None

            # Stop the despatcher and sequencer and all panel interaction
            self.stopDespatcher()
            self.stopSequencer()

            self._emptySendQueue(pri_level = -1)
            self.setTransportConnection(None)

            self.suspendAllOperations = True
            self.PanelMode = AlPanelMode.STOPPED
            self.PartitionState[0].Reset()
            self.PartitionState[1].Reset()
            self.PartitionState[2].Reset()
            self.PanelStatus = {}
            log.debug("[Controller] ********************************************************************************")
            log.debug("[Controller] ********************************************************************************")
            log.debug("[Controller] ****************************** Operations Suspended ****************************")
            log.debug("[Controller] ********************************************************************************")
            log.debug("[Controller] ********************************************************************************")

    def updateSettings(self, newdata: PanelConfig):
        if newdata is not None:
            # log.debug(f"[updateSettings] Settings refreshed - Using panel config {newdata}")
            self.ForceStandardMode = newdata.get(AlConfiguration.ForceStandard, self.ForceStandardMode)
            self.DisableAllCommands = newdata.get(AlConfiguration.DisableAllCommands, self.DisableAllCommands)
            if AlConfiguration.DownloadCode in newdata:
                tmpDLCode = newdata[AlConfiguration.DownloadCode]  # INTERFACE : Get the download code
                if len(tmpDLCode) == 4 and type(tmpDLCode) is str:
                    self.DownloadCode = tmpDLCode
                    if not OBFUS:
                        log.debug(f"[Settings] Download Code set by user to {self.DownloadCode}")
                    self.DownloadCodeUserSet = True
        if self.DisableAllCommands:
            self.ForceStandardMode = True
        # By the time we get here there are 3 combinations of self.DisableAllCommands and self.ForceStandardMode
        #     Both are False --> Try to get to Powerlink 
        #     self.ForceStandardMode is True --> Force Standard Mode, the panel can still be armed and disarmed
        #     self.ForceStandardMode and self.DisableAllCommands are True --> The integration interacts with the panel but commands such as arm/disarm/log/bypass are not allowed
        # The if statement above ensure these are the only supported combinations.
        log.debug(f"[Settings] ForceStandard = {self.ForceStandardMode}     DisableAllCommands = {self.DisableAllCommands}")

    def getPanelSetting(self, p : PanelSetting, offset : int) -> str | int | bool | None: 
        # Do not use for usercodes
        if p is not None and offset is not None and p in self.PanelSettings and offset < len(self.PanelSettings[p]):
            return self.PanelSettings[p][offset]   # could be a list or a bytearray
        return None

    def getPanelCapability(self, i : IndexName) -> int:
        if i is not None and i in self.PanelCapabilities:
            return self.PanelCapabilities[i]    # always an integer
#        if self.Panel
        return 0

    def gotValidUserCode(self) -> bool:
        return not self.ForceStandardMode and len(self.PanelSettings[PanelSetting.UserCodes]) >= 2 and self.PanelSettings[PanelSetting.UserCodes][0] != 0 and self.PanelSettings[PanelSetting.UserCodes][1] != 0

    def getUserCode(self):
        if self.gotValidUserCode():
            #log.debug(f"[getUserCode] {self.PanelSettings[PanelSetting.UserCodes]}")
            #log.debug(f"[getUserCode] {self.PanelSettings[PanelSetting.UserCodes][0]}  {self.PanelSettings[PanelSetting.UserCodes][1]}")
            return bytearray([self.PanelSettings[PanelSetting.UserCodes][0], self.PanelSettings[PanelSetting.UserCodes][1]])
        return bytearray([0,0])

    def isPowerMaster(self) -> bool:
        return self.PowerMaster is not None and self.PowerMaster # PowerMaster models

    # This is called from the loop handler when the connection to the transport is made
    #   This also starts the sequencer
    def setTransportConnection(self, transport : AlTransport):
        """Set the transport connection to the Panel."""
        self.transport = transport
        if transport is not None:
            log.debug("[Connection] Connected to local Protocol handler and Transport Layer")
        if self.transport is not None and self.sequencerTask is None:
            # Start sequencer the first time the transport is set, after that don't
            self.sequencerTask = self.loop.create_task(self._sequencer())

    def stopSequencer(self):
        if self.sequencerTask is not None:
            try:
                log.debug("[stopSequencer] Cancelling _sequencer")
                self.sequencerTask.cancel()
            except Exception as ex:
                log.debug("[stopSequencer]     Caused an exception")
                log.debug(f"             {ex}")
            self.sequencerTask = None

    def startDespatcher(self):
        '''(re)start the PDU despatcher, the task that sends messages to the panel'''
        self.stopDespatcher()
        self._reset_watchdog_timeout()
        self._reset_keep_alive_messages()
        self._clearReceiveResponseList()
        self._emptySendQueue(pri_level = -1)  # empty the list
        log.debug("[_sequencer] Starting _despatcher")
        self.despatcherTask = self.loop.create_task(self._despatcher())

    def stopDespatcher(self):
        if self.despatcherTask is not None:
            try:
                log.debug("[stopDespatcher] Cancelling _despatcher")
                self.despatcherTask.cancel()
            except Exception as ex:
                # This could happen in normal operation if the despatcher thread is blocked in the "get" queue function
                #     This is the exception for that
                #         ERROR (MainThread) [homeassistant] Error doing job: Task was destroyed but it is pending! (<Task pending name='Task-202' coro=<ProtocolBase._despatcher() 
                #             running at /config/custom_components/visonic/pyvisonic.py:1754> wait_for=<Future pending cb=[Task.task_wakeup()]>>)
                log.debug("[stopDespatcher]     Caused an exception")
                log.debug(f"             {ex}")
            self.despatcherTask = None

    # when the connection has problems then call the onProblem when available
    def _reportProblem(self, termination : AlTerminationType):
        """Log when connection is closed, if needed call callback."""

        if self.suspendAllOperations:
            log.debug("[_reportProblem] Operations Already Suspended. Sorry but all operations have been suspended, please recreate connection")
            return

        log.debug(f"[_reportProblem] Problem due to {termination}")
        # Set mode to Stopped just in case the handler uses it, leave all other variables as they are
        self.PanelMode = AlPanelMode.STOPPED
        if self.onProblemHandler:
            #log.debug("[_reportProblem]                         Calling Exception handler.")
            self.onProblemHandler(termination)
        else:
            log.debug("[_reportProblem]                         No Exception handler to call......")
        #self.shutdownOperation()

    def isSendQueueEmpty(self) -> bool:
        return self.SendQueue.empty()

    # Clear the send queue and reset the associated parameters
    def _emptySendQueue(self, pri_level : int = 1):
        """ Clear the List by priority level, preventing any retry causing issue. """
        #log.debug(f"[_emptySendQueue]    enter {self.SendQueue.qsize()}")
        other = PriorityQueueWithPeek()
        # move it to other
        while not self.SendQueue.empty():
            other.put_nowait(self.SendQueue.get_nowait())

        # move back the higher priority items
        while not other.empty():
            v = other.get_nowait() # return a tuple (priority, VisonicListEntry)
            if v[0] <= pri_level:
                self.SendQueue.put_nowait(v)

        #log.debug(f"[_emptySendQueue]    exit {self.SendQueue.qsize()}")

    def _clearReceiveResponseList(self):
        self.pmLastSentMessage = None
        self.pmExpectedResponse = set()

    # This function performs a "soft" reset to the send comms, it resets the queues, clears expected responses,
    #     resets watchdog timers and asks the panel for a status
    #     if in powerlink it tries a RESTORE to re-establish powerlink comms protocols
    def _triggerRestoreStatus(self):
        # restart the watchdog and keep-alive counters
        self._reset_watchdog_timeout()
        self._reset_keep_alive_messages()
        if self.PowerLinkBridgeConnected:
            self.B0_Wanted.add(B0SubType.PANEL_STATE)        # 24
        elif self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK]:
            # Send RESTORE to the panel
            self._addMessageToSendList(Send.RESTORE)  # also gives status.  This is an AB message which we can't send to POWERLINK_BRIDGED
        else:
            self._addMessageToSendList(Send.STATUS)

    def _reset_keep_alive_messages(self):
        self.keep_alive_counter = 0

    # This function needs to be called within the timeout to reset the timer period
    def _reset_watchdog_timeout(self):
        self.watchdog_counter = 0

    # This function needs to be called within the timeout to reset the timer period
    def _reset_powerlink_counter(self):
        self.powerlink_counter = 0

    def setTimeInPanel(self, paneltime = None):
        # To set the time in the panel we need to be in DOWNLOAD Mode
        t = self._getTimeFunction()
        settime = self.AutoSyncTime  # should we sync time between the HA and the Alarm Panel
        if paneltime is not None and t.year > 2000:
            # Regardless of whether we autosync time, calculate the time difference
            self.Panel_Integration_Time_Difference = t - paneltime
            d = self.Panel_Integration_Time_Difference.total_seconds()
            log.debug(f"[setTimeInPanel]      Local time is {t}      time difference {d} seconds")   # 
            if abs(d) < TIME_INTERVAL_ERROR:
                log.debug(f"[setTimeInPanel]      Not Correcting Time in Panel as less than {TIME_INTERVAL_ERROR} seconds difference.")
                settime = False
            #else:
            #    log.debug("[setTimeInPanel]      Correcting Time in Panel.")
        if settime:
            log.debug(f"[setTimeInPanel]      Setting time in panel {t}")
            timePdu = bytearray([t.second + 1, t.minute, t.hour, t.day, t.month, t.year - 2000])   # add about 1 seconds on as it takes over 1 to get to the panel to set it
            # Set these as urgent to get them to the panel asap (so the time is set asap to synchronise panel and local time)
            self._addMessageToSendList(Send.DOWNLOAD_TIME, priority = MessagePriority.URGENT, options=[ [3, convertByteArray(self.DownloadCode)] ])  
            self._addMessageToSendList(Send.SETTIME, priority = MessagePriority.URGENT, options=[ [3, timePdu] ])
            self._addMessageToSendList(Send.EXIT, priority = MessagePriority.URGENT)

    def _create_B0_35_Data_Request(self, taglist : list = None, strlist : str = None) -> bytearray:
        if taglist is None and strlist is None:
            return bytearray()
        elif taglist is not None:
            PM_Request_Data = bytearray(taglist)
        elif strlist is not None:
            log.debug(f"[_create_B0_35_Data_request] {strlist}")
            PM_Request_Data = convertByteArray(strlist)
        else:
            log.debug(f"[_create_B0_35_Data_request] Error not sending anything as both params set")
            return

        PM_Request_Start = convertByteArray('b0 01 35 99 02 ff 08 ff 99')  # The 2 means that each data parameter is 2 bytes
        PM_Request_End   = bytearray([Packet.POWERLINK_TERMINAL])          # create from a list

        PM_Data = PM_Request_Start + PM_Request_Data + PM_Request_End

        PM_Data[3] = len(PM_Request_Data) + 5
        PM_Data[8] = len(PM_Request_Data)

        CS = self._calculateCRC(PM_Data)   # returns a bytearray with a single byte
        To_Send = bytearray([Packet.HEADER]) + PM_Data + CS + bytearray([Packet.FOOTER])

        log.debug(f"[_create_B0_35_Data_request] Returning {toString(To_Send)}")
        return To_Send

    def _create_B0_42_Data_Request(self, taglist : list = None, strlist : str = None) -> bytearray:
        if taglist is None and strlist is None:
            return bytearray()
        elif taglist is not None:
            PM_Request_Data = bytearray(taglist)
        elif strlist is not None:
            #log.debug(f"[_create_B0_42_Data_request] {strlist}")
            PM_Request_Data = convertByteArray(strlist)
        else:
            log.debug(f"[_create_B0_42_Data_request] Error not sending anything as both params set incorrectly")
            return bytearray()

        if len(PM_Request_Data) % 2 == 1:  # its an odd number of bytes
            log.debug(f"[_create_B0_42_Data_request] Error not sending anything as its an odd number of bytes {toString(PM_Request_Data)}")
            return bytearray()
        elif len(PM_Request_Data) == 2:
            PM_Request_Data.extend(convertByteArray("00 00 ff ff"))
            special = 2
        elif len(PM_Request_Data) == 4:
            PM_Request_Data.extend(convertByteArray("00 00"))
            special = 6
        else:
            special = 6

        PM_Request_Start = convertByteArray(f'b0 01 42 99 0{special} ff 08 0c 99')
        PM_Request_End   = bytearray([Packet.POWERLINK_TERMINAL])

        PM_Data = PM_Request_Start + PM_Request_Data + PM_Request_End

        PM_Data[3] = len(PM_Request_Data) + 5
        PM_Data[8] = len(PM_Request_Data)

        CS = self._calculateCRC(PM_Data)   # returns a bytearray with a single byte
        To_Send = bytearray([Packet.HEADER]) + PM_Data + CS + bytearray([Packet.FOOTER])

        #log.debug(f"[_create_B0_42_Data_request] Returning {toString(To_Send)}")
        return To_Send

    def _create_B0_Data_Request(self, taglist : set = None) -> bytearray:

        if taglist is None or len(taglist) == 0:
            taglist = {pmSendMsgB0[B0SubType.PANEL_STATE].data}
            log.debug(f"[_create_B0_Data_Request] Taglist is empty so asking for PANEL_STATE")

        PM_Request_Data = bytearray(set(taglist))                          # just to make sure there are no duplicates
        PM_Request_Start = convertByteArray('b0 01 17 99 01 ff 08 ff 99')
        PM_Request_End   = bytearray([Packet.POWERLINK_TERMINAL])

        PM_Data = PM_Request_Start + PM_Request_Data + PM_Request_End

        PM_Data[3] = len(PM_Request_Data) + 5   # Was 6 but removed counter at the end!!!!!!
        PM_Data[8] = len(PM_Request_Data)

        CS = self._calculateCRC(PM_Data)   # returns a bytearray with a single byte
        To_Send = bytearray([Packet.HEADER]) + PM_Data + CS + bytearray([Packet.FOOTER])

        log.debug(f"[_create_B0_Data_Request] Returning {toString(To_Send)}")
        return To_Send

    def fetchPanelStatus(self):
        if self.isPowerMaster():
            s = self._create_B0_Data_Request(taglist = {pmSendMsgB0[B0SubType.PANEL_STATE].data} )
            self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)
        else:
            self._addMessageToSendList(Send.STATUS_SEN, priority = MessagePriority.IMMEDIATE)


    # There are 2 Tasks that manage the panel (despatcher and sequencer):
    #    This is the despatcher, it manages the sending of messages to the panel from a PriorityQueue
    #        The SendQueue is set up as a PriorityQueue and needs a < function implementing in VisonicListEntry based on time, oldest < newest
    #        By doing this it's like having two queues in one, a high priority queue, date ordered oldest first, and a low priority queue date ordered oldest first
    async def _despatcher(self):

        async def waitForTransport(s : int):
            s = s * 10
            while s >= 0 and self.transport is None:
                await asyncio.sleep(0.1)
                s = s - 1
            if self.transport is None:
                log.debug("[_despatcher] **************************************************************************************")
                log.debug("[_despatcher] ****************************** Transport Mechanism Invalid ***************************")
                log.debug("[_despatcher] **************************************************************************************")

        def checkQueuePriorityLevel():
            if not self.SendQueue.empty():
                #log.debug(f"[_despatcher]  Checking The head of the queue")
                priority, item = self.SendQueue.peek_nowait()
                #log.debug(f"[_despatcher]  The head of the queue is priority {priority}")
                return priority
            return 10 # big number, above the priority levels used

        def sleepytime(interval) -> float:
            # If needed, create a minimum time delay between sending the panel messages as the panel can't cope (not enough CPU power and bandwidth on the serial link)
            # A PowerMaster is faster than a PowerMax so it can have a smaller minimum gap between sequential messages
            gap = MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMASTER if self.isPowerMaster() else MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMAX
            s = timedelta(milliseconds=gap) - interval
            if s > timedelta(milliseconds=0):
                return s.total_seconds()
            return -1.0

        # Function to send all PDU messages to the panel
        def sendPdu(instruction: VisonicListEntry) -> float:        # return the delay before sending the next PDU
            """Encode and put packet string onto write buffer."""

            if self.suspendAllOperations:
                log.debug("[sendPdu] Suspended all operations, not sending PDU")
                return -1.0

            if instruction is None:
                log.error("[sendPdu] Attempt to send a command that is empty")
                return -1.0

            if instruction.command is None and instruction.raw is None:
                log.error("[sendPdu] Attempt to send a sub command that is empty")
                return -1.0

            sData = None
            command = None

            if instruction.raw is not None:
                sData = instruction.insertOptions(instruction.raw)

            elif instruction.command is not None:
                # Send a command to the panel
                command = instruction.command
                data = instruction.insertOptions(command.data)

                # log.debug(f"[sendPdu] input data: {toString(packet)}")
                # First add header (Packet.HEADER), then the packet, then crc and footer (Packet.FOOTER)
                sData = bytearray([Packet.HEADER])
                sData += data
                if self.isPowerMaster() and (data[0] == 0xB0 or data[0] == 0xAB):
                    sData += self._calculateCRCAlt(data)
                else:
                    sData += self._calculateCRC(data)
                sData += bytearray([Packet.FOOTER])
            else:
                log.warning("[sendPdu]      Invalid message data, not sending anything to the panel")
                return -1.0

            # no need to send i'm alive message for a while as we're about to send a command anyway
            self._reset_keep_alive_messages()
            # Log some useful information in debug mode
            if self.transport is not None:
                self.transport.write(sData)
                self.firstCmdSent = True
                self.pmLastTransactionTime = self._getUTCTimeFunction()
                if sData[1] != Receive.ACKNOWLEDGE:  # the message is not an acknowledge back to the panel, then save it
                    self.pmLastSentMessage = instruction

                if command is not None and command.download:
                    self.pmDownloadMode = True
                    self.triggeredDownload = False
                    log.debug("[sendPdu] Setting Download Mode to true")

                if command is not None and command.debugprint == DebugLevel.FULL:
                    log.debug(f"[sendPdu] Sent Command ({command.msg})    raw data {toString(sData)}   waiting for message response {[hex(no).upper() for no in self.pmExpectedResponse]}")
                elif command is not None and command.debugprint == DebugLevel.CMD:
                    log.debug(f"[sendPdu] Sent Command ({command.msg})    waiting for message response {[hex(no).upper() for no in self.pmExpectedResponse]}")
                elif instruction.raw is not None:
                    # Assume raw data to send is not obfuscated for now
                    log.debug(f"[sendPdu] Sent Raw Command      raw data {toString(sData[:4] if OBFUS else sData)}   waiting for message response {[hex(no).upper() for no in self.pmExpectedResponse]}")            

            else:
                log.debug("[sendPdu]      Comms transport has been set to none, must be in process of terminating comms")
                return -1.0           

            if command is not None and command.waittime > 0.0:
                return command.waittime
            return -1.0           

        log.debug(f"[_despatcher]  Starting")
        self.despatcherException = False
        await waitForTransport(20) # Wait up to 20 seconds for the transport to be setup, if it isn't then other functions set self.suspendAllOperations to True
        while not self.suspendAllOperations:
            try:
                post_delay = 0.01
                # calc the time interval between sending the last message and now
                interval = self._getUTCTimeFunction() - self.pmLastTransactionTime

                if len(self.pmExpectedResponse) == 0 or (not self.SendQueue.empty() and checkQueuePriorityLevel() < 2):
                    # Here when either:
                    #     The expected response list is empty so we're not waiting for a specific message to be received before sending the next
                    #             in this case the get function will block waiting
                    #     The send queue is not empty and there is either an immediate pdu to send or an ack pdu
                    #             immediate pdu's are commanded by the user e.g. arm, disarm etc
                    # ensure that there is a minimum delay between sending messages to the panel
                    #log.debug(f"[_despatcher]  Loopy")
                    if (s := sleepytime(interval)) > 0.0:
                        # If needed, create a minimum time delay between sending the panel messages as the panel can't cope (not enough CPU power and bandwidth on the serial link)
                        #log.debug(f"[_despatcher]  {s}")
                        await asyncio.sleep(s)
                    # since we might have been asleep, check it again :)
                    if not self.suspendAllOperations:
                        #log.debug(f"[_despatcher] Start Get      queue size {self.SendQueue.qsize()}")
                        # pop the highest priority and oldest item from the list, this could be the only item.
                        d = await self.SendQueue.get()  # this blocks waiting for something to be added to the queue, nothing else is relevant as pmExpectedResponse is empty and can only be added to by calling sendPdu
                        #log.debug(f"[_despatcher] Get worked and got something priority={d[0]}          queue size {self.SendQueue.qsize()}")

                        # since we might have been waiting for something to send, check it again :)
                        if not self.suspendAllOperations:
                            instruction = d[1]   # PriorityQueue is put as a tuple (priority, viscommand), so get the viscommand
                            if len(instruction.response) > 0:
                                # update the expected response list straight away (without having to wait for it to be actually sent) to make sure protocol is followed
                                self.pmExpectedResponse.update(instruction.response)
                            self.SendQueue.task_done()
                            #log.debug(f"[_despatcher] _despatcher sending it to sendPdu, instruction={instruction}          queue size {self.SendQueue.qsize()}")
                            post_delay = sendPdu(instruction)
                            #log.debug(f"[_despatcher] Nothing to do      queue size {self.SendQueue.qsize()}")
                else: #elif not self.pmDownloadMode:
                    # We're waiting for a message back from the panel before continuing
                    # self.pmExpectedResponse will prevent us sending another message to the panel
                    if interval > RESPONSE_TIMEOUT:
                        # If the panel is lazy or we've got the timing wrong........
                        # Expected response timeouts are only a problem when in Powerlink Mode as we expect a response
                        #   But in all modes, give the panel a self._triggerRestoreStatus
                        if len(self.pmExpectedResponse) == 1 and Receive.ACKNOWLEDGE in self.pmExpectedResponse:
                            self.pmExpectedResponse = set()  # If it's only for an acknowledge response then ignore it
                        else:
                            st = '[{}]'.format(', '.join(hex(x) for x in self.pmExpectedResponse))
                            log.debug(f"[_despatcher] ****************************** Response Timer Expired ********************************")
                            log.debug(f"[_despatcher]                While Waiting for: {st}")
                            # Reset Send state (clear queue and reset flags)
                            self._clearReceiveResponseList()
                            self._triggerRestoreStatus()     # Clear message buffers and send a Restore (if in Powerlink) or Status (not in Powerlink) to the Panel
                    elif self.pmLastSentMessage is not None and interval > RESEND_MESSAGE_TIMEOUT:
                        #   If there's a timeout then resend the previous message. If that doesn't work then dump the message and continue, but log the error
                        if not self.pmLastSentMessage.triedResendingMessage:
                            # resend the last message
                            log.debug(f"[_despatcher] ****************************** Resend Timer Expired ********************************")
                            log.debug(f"[_despatcher]                Re-Sending last message  {self.pmLastSentMessage.command.msg}")
                            self.pmLastSentMessage.triedResendingMessage = True
                            post_delay = sendPdu(self.pmLastSentMessage)
                        else:
                            # tried resending once, no point in trying again so reset settings, start from scratch
                            log.debug(f"[_despatcher] ****************************** Resend Timer Expired ********************************")
                            log.debug(f"[_despatcher]                Tried Re-Sending last message but didn't work. Message is dumped")
                            # Reset Send state (clear queue and reset flags)
                            self._clearReceiveResponseList()
                            self._emptySendQueue(pri_level = 1)
                        # restart the watchdog and keep-alive counters
                        self._reset_watchdog_timeout()
                        self._reset_keep_alive_messages()

                # implement any post delay for the message
                if post_delay >= 0:  # Check send queue
                    #log.debug(f"[_despatcher]  Command has a post delay of {post_delay}")
                    await asyncio.sleep(post_delay)

            except Exception as ex:
                log.error("[_despatcher] Visonic Executor loop has caused an exception")
                log.error(f"             {ex}")
                self.despatcherException = True

    # There are 2 Tasks that manage the panel (despatcher and sequencer):
    #  This is the sequencer, it manages the state of the connection with the panel
    # Function to send I'm Alive and status request messages to the panel
    # This is also a timeout function for a watchdog. If we are in powerlink, we should get a AB 03 message every 20 to 30 seconds
    #    If we haven't got one in the timeout period then reset the send queues and state and then call a MSG_RESTORE
    # In standard mode, this command asks the panel for a status
    async def _sequencer(self):

        class SequencerType(IntEnum):
            Invalid                 = -1
            Reset                   = 1
            LookForPowerlinkBridge  = 2
            InitialisePanel         = 3
            WaitingForPanelDetails  = 4
            AimingForStandard       = 5
            DoingStandard           = 6
            #AimingForStandardPlus   = 6
            EPROMInitialiseDownload = 7
            EPROMTriggerDownload    = 8
            EPROMStartedDownload    = 9
            EPROMDoingDownload      = 10
            EPROMDownloadComplete   = 11
            EPROMExitDownload       = 12
            EnrollingPowerlink      = 13
            DoingStandardPlus       = 14
            WaitingForEnrolSuccess  = 15
            DoingPowerlink          = 16
            DoingPowerlinkBridge    = 17
            GettingB0SensorMessages = 18
            CreateSensors           = 19

            def __str__(self):
                return str(self.name)

        class PanelErrorStates(IntEnum):
            AllGood               = 0
            AccessDeniedDownload  = 1
            AccessDeniedPin       = 2
            AccessDeniedStop      = 3
            AccessDeniedCommand   = 4
            Exit                  = 5
            TimeoutReceived       = 6
            DownloadRetryReceived = 7
            DespatcherException   = 8
            BeeZeroInvalidCommand = 9

        checkAllPanelData = True

        _sequencerState = SequencerType.LookForPowerlinkBridge
        _sequencerStatePrev = SequencerType.Invalid

        self._reset_watchdog_timeout()
        self._reset_keep_alive_messages()

        # declare a list and fill it with zeroes
        watchdog_list = [0] * WATCHDOG_MAXIMUM_EVENTS
        # The starting point doesn't really matter
        watchdog_pos = WATCHDOG_MAXIMUM_EVENTS - 1
        self.powerlink_counter = 0

        counter = 0                     # create a generic counter that gets reset every state change, so it can be used in a single state
        no_data_received_counter = 0
        no_packet_received_counter = 0
        _my_panel_state_trigger_count = 5
        _sendStartUp = False
        image_delay_counter = 0
        log_sensor_state_counter = 0
        _last_B0_wanted_request_time = self._getTimeFunction()
        lastrecv = None
        delay_loops = 0
        a_day = 24 * 60 * 60  # seconds in a day

        async def waitForTransport(s : int):
            while s >= 0 and self.transport is None:
                await asyncio.sleep(0.1)
                s = s - 1

        def _resetPanelInterface():
            """ This should re-initialise the panel """
            log.debug(f"[_resetPanelInterface]   ************************************* Reset Panel Interface **************************************  {self.PanelMode=}")

            # Clear the send list and empty the expected response list
            self._clearReceiveResponseList()
            self._emptySendQueue(pri_level = -1) # empty the list

            # Send Exit and Stop to the panel. This should quit download mode.
            self._addMessageToSendList(Send.EXIT)
            self._addMessageToSendList(Send.STOP)

            if not self.PowerLinkBridgeConnected and self.pmInitSupportedByPanel:
                self._addMessageToSendList(Send.INIT)

        def _requestMissingPanelConfig(missing):
            m = []
            for a in missing:
                if a in pmPanelSettingCodes and pmPanelSettingCodes[a].PMasterB035Panel is not None:
                    m.append(pmPanelSettingCodes[a].PMasterB035Panel) # 35 message types to ask for
            if len(m) > 0:
                log.debug(f"[_requestMissingPanelConfig]      Type 35 Wanting {m}")
                tmp = bytearray()
                for a in m:
                    y1, y2 = (a & 0xFFFF).to_bytes(2, "little")
                    tmp = tmp + bytearray([y1, y2])
                s = self._create_B0_35_Data_Request(strlist = toString(tmp))
                self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)
            else:
                m = []
                for a in missing:
                    if a in pmPanelSettingCodes and pmPanelSettingCodes[a].PMasterB042Panel is not None:
                        m.append(pmPanelSettingCodes[a].PMasterB042Panel) # 42 message types to ask for
                if len(m) > 0:
                    log.debug(f"[_requestMissingPanelConfig]      Type 42 Wanting {m}")
                    tmp = bytearray()
                    for a in m:
                        y1, y2 = (a & 0xFFFF).to_bytes(2, "little")
                        tmp = tmp + bytearray([y1, y2])
                    s = self._create_B0_42_Data_Request(strlist = toString(tmp))
                    self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)
                else:
                    m = []
                    for a in missing:
                        if a in pmPanelSettingCodes and pmPanelSettingCodes[a].PMasterB0Mess is not None:
                            m.append(pmPanelSettingCodes[a].PMasterB0Mess)
                    if len(m) > 0:
                        log.debug(f"[_requestMissingPanelConfig]      Wanting {m}")
                        tmp = [pmSendMsgB0[i].data if i in pmSendMsgB0 else i for i in m]      # m can contain State enumerations or the integer of the message subtype
                        s = self._create_B0_Data_Request(taglist = set(tmp))
                        self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)

        def _gotoStandardModeStopDownload():
            if not self.PowerLinkBridgeConnected:  # Should not be in this function when this is True but use it anyway
                if self.DisableAllCommands:
                    log.debug("[Standard Mode] Entering MINIMAL ONLY Mode")
                    self.PanelMode = AlPanelMode.MINIMAL_ONLY
                elif self.pmDownloadComplete and not self.ForceStandardMode and self.gotValidUserCode():
                    log.debug("[Standard Mode] Entering Standard Plus Mode as we got the pin codes from the EPROM (You can still manually Enrol your Panel)")
                    self.PanelMode = AlPanelMode.STANDARD_PLUS
                else:
                    log.debug("[Standard Mode] Entering Standard Mode")
                    self.PanelMode = AlPanelMode.STANDARD
                    self.ForceStandardMode = True
            # Stop download mode
            self.pmDownloadComplete = False
            self.pmDownloadMode = False
            self.triggeredDownload = False
            self.StopTryingDownload = True
            self.sendPanelUpdate(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
            if not self.PowerLinkBridgeConnected and self.DisableAllCommands:
                # Clear the send list and empty the expected response list
                self._clearReceiveResponseList()
                self._emptySendQueue(pri_level = 1)
            else:
                _resetPanelInterface()
            self._addMessageToSendList(Send.STATUS_SEN)

        def _clearPanelErrorMessages():
            self.AccessDeniedReceived = False
            self.AccessDeniedMessage = None
            self.ExitReceived = False
            self.DownloadRetryReceived = False
            self.TimeoutReceived = False
            self.gotBeeZeroInvalidCommand = False

        # Process the panel error messages, in order: Access Denied, Exit, DownloadRetry and Timeout
        def processPanelErrorMessages() -> PanelErrorStates:

            if self.despatcherException:
                self.despatcherException = False
                return PanelErrorStates.DespatcherException

            # Make sure that the Access Denied is processed first
            if self.AccessDeniedReceived:
                log.debug("[_sequencer] Access Denied")
                self.AccessDeniedReceived = False
                if self.AccessDeniedMessage is not None and self.AccessDeniedMessage.command is not None:
                    lastCommandData = self.AccessDeniedMessage.command.data
                    self.AccessDeniedMessage = None
                    if lastCommandData is not None:
                        log.debug(f"[_sequencer]     AccessDenied last command {toString(lastCommandData[:3] if OBFUS else lastCommandData)}")
                        # Check download first, then pin, then stop
                        if lastCommandData[0] == 0x24 or lastCommandData[0] == 0x09:
                            log.debug("[_sequencer]           Got an Access Denied and we have sent a Bump or a Download command to the Panel")
                            return PanelErrorStates.AccessDeniedDownload
                        elif lastCommandData[0] != 0xAB and lastCommandData[0] & 0xA0 == 0xA0:  # this will match A0, A1, A2, A3 etc but not Receive.POWERLINK
                            log.debug("[_sequencer]           Attempt to send a command message to the panel that has been denied, wrong pin code used")
                            # INTERFACE : tell user that wrong pin has been used
                            return PanelErrorStates.AccessDeniedPin
                        elif lastCommandData[0] == 0x0B:  # Stop
                            log.debug("[_sequencer]           Received a stop command from the panel")
                            return PanelErrorStates.AccessDeniedStop
                    return PanelErrorStates.AccessDeniedCommand
                log.debug(f"[_sequencer]           AccessDenied, either no last command or not processed  {self.AccessDeniedMessage}")
                self.AccessDeniedMessage = None
                return PanelErrorStates.AccessDeniedCommand

            if self.ExitReceived:
                log.debug("[_sequencer] Exit received")
                self.ExitReceived = False
                return PanelErrorStates.Exit

            if self.DownloadRetryReceived:
                self.DownloadRetryReceived = False
                if not self.PowerLinkBridgeConnected:
                    log.debug("[_sequencer] DownloadRetryReceived")
                    return PanelErrorStates.DownloadRetryReceived

            if self.TimeoutReceived:
                self.TimeoutReceived = False
                if not self.PowerLinkBridgeConnected:
                    log.debug("[_sequencer] TimeoutReceived")
                    return PanelErrorStates.TimeoutReceived
            
            if self.gotBeeZeroInvalidCommand:
                self.gotBeeZeroInvalidCommand = False
                return PanelErrorStates.BeeZeroInvalidCommand
            
            return PanelErrorStates.AllGood

        def toStringList(ll) -> []:
            return {pmSendMsgB0_reverseLookup[i].data if isinstance(i, int) and i in pmSendMsgB0_reverseLookup else i for i in ll}

        def reset_vars():
            self.resetGlobals()

            checkAllPanelData = True

            _sequencerState = SequencerType.InitialisePanel
            _sequencerStatePrev = SequencerType.Invalid

            _last_B0_wanted_request_time = self._getTimeFunction()
            _my_panel_state_trigger_count = 5
            _sendStartUp = False
            # declare a list and fill it with zeroes
            watchdog_list = [0] * WATCHDOG_MAXIMUM_EVENTS
            # The starting point doesn't really matter
            watchdog_pos = WATCHDOG_MAXIMUM_EVENTS - 1

            self.startDespatcher()

            counter = 0                     # create a generic counter that gets reset every state change, so it can be used in a single state
            no_data_received_counter = 0
            no_packet_received_counter = 0
            image_delay_counter = 0
            log_sensor_state_counter = 0
            lastrecv = None
            delay_loops = 0

        def updateSensorNamesAndTypes(force = False) -> bool:
            """ Retrieve Zone Names and Zone Types if needed """
            # This function checks to determine if the Zone Names and Zone Types have been retrieved and if not it gets them
            retval = None
            if self.PanelType is not None and 0 <= self.PanelType <= 16:
                retval = False
                #zoneCnt = self.getPanelCapability(IndexName.ZONES)
                zoneCnt = self.getPanelCapability(IndexName.ZONES)
                if self.isPowerMaster():
                    if force or len(self.PanelSettings[PanelSetting.ZoneNames]) < zoneCnt:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone names, zone count = {zoneCnt}  I've only got {len(self.PanelSettings[PanelSetting.ZoneNames])} zone names")
                        self.B0_Wanted.add(B0SubType.ZONE_NAMES)
                    if force or len(self.PanelSettings[PanelSetting.ZoneTypes]) < zoneCnt:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone types, zone count = {zoneCnt}  I've only got {len(self.PanelSettings[PanelSetting.ZoneTypes])} zone types")
                        self.B0_Wanted.add(B0SubType.ZONE_TYPES)
                    #if force or len(self.SensorList) == 0:
                    #    retval = True
                    #    log.debug("[updateSensorNamesAndTypes] Trying to get the sensor status")
                    #    self.B0_Wanted.add(B0SubType.DEVICE_TYPES)
                else:
                    if force or len(self.PanelSettings[PanelSetting.ZoneNames]) < zoneCnt:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone names again zone count = {zoneCnt}  I've only got {len(self.PanelSettings[PanelSetting.ZoneNames])} zone names")
                        self._addMessageToSendList(Send.ZONENAME)
                    if force or len(self.PanelSettings[PanelSetting.ZoneTypes]) < zoneCnt:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone types again zone count = {zoneCnt}  I've only got {len(self.PanelSettings[PanelSetting.ZoneTypes])} zone types")
                        self._addMessageToSendList(Send.ZONETYPE)
            else:
                log.debug(f"[updateSensorNamesAndTypes] Warning: Panel Type error {self.PanelType=}")
            return retval

        def setNextDownloadCode(paneltype) -> str:
            if not self.DownloadCodeUserSet:
                if self.DownloadCode == pmPanelConfig[CFG.DLCODE_1][paneltype]:
                    self.DownloadCode = pmPanelConfig[CFG.DLCODE_2][paneltype]
                elif self.DownloadCode == pmPanelConfig[CFG.DLCODE_2][paneltype]:
                    self.DownloadCode = pmPanelConfig[CFG.DLCODE_3][paneltype]
                else:
                    ra = random.randint(10, 240)
                    rb = random.randint(10, 240)
                    self.DownloadCode = f"{hexify(ra):>02}{hexify(rb):>02}"
            self.PanelSettings[PanelSetting.PanelDownload] = self.DownloadCode
            return self.DownloadCode

        # We can only use this function when the panel has sent a "installing powerlink" message i.e. AB 0A 00 01
        #   We need to clear the send queue and reset the send parameters to immediately send an MSG_ENROL
        def sendMsgENROL(force = False):
            """ Auto enrol the PowerMax/Master unit """
            # Only attempt to auto enrol powerlink for newer panels but not the 360 or 360R.
            #       Older panels need the user to manually enrol
            #       360 and 360R can get to Standard Plus but not Powerlink as (I assume that) they already have this hardware and panel will not support 2 powerlink connections
            if force or (self.PanelMode == AlPanelMode.STANDARD_PLUS):
                if force or (self.PanelType is not None and self.AutoEnrol):
                    # Only attempt to auto enrol powerlink for newer panels. Older panels need the user to manually enrol, we should be in Standard Plus by now.
                    log.debug("[sendMsgENROL] Trigger Powerlink Attempt, sending ENROL request to the panel")
                    # Allow the receipt of a powerlink ack to then send a MSG_RESTORE to the panel,
                    #      this should kick it in to powerlink after we just enrolled
                    self.allowAckToTriggerRestore = True
                    # Send enrol to the panel to try powerlink
                    self._addMessageToSendList(Send.ENROL, priority = MessagePriority.IMMEDIATE, options=[ [4, convertByteArray(self.DownloadCode)] ])
                elif self.PanelType is not None and self.PanelType >= 1:
                    # Powermax+ or Powermax Pro, attempt to just send a MSG_RESTORE to prompt the panel in to taking action if it is able to
                    log.debug("[sendMsgENROL] Trigger Powerlink Prompt attempt to a Powermax+ or Powermax Pro panel")
                    # Prevent the receipt of a powerlink ack to then send a MSG_RESTORE to the panel,
                    self.allowAckToTriggerRestore = False
                    # Send a MSG_RESTORE, if it sends back a powerlink acknowledge then another MSG_RESTORE will be sent,
                    #      hopefully this will be enough to kick the panel in to sending Receive.POWERLINK Keep-Alive
                    self._addMessageToSendList(Send.RESTORE)


        myspecialcounter = 0

        await waitForTransport(200)
        reset_vars()
        while not self.suspendAllOperations:
            try:
                changedState = _sequencerState != _sequencerStatePrev
                if changedState:       
                    # create a generic counter that gets reset every state change, so it can be used in a single state
                    log.debug(f"[_sequencer] Changed state from {_sequencerStatePrev} to {_sequencerState}, I was in state {_sequencerStatePrev} for approx {counter} seconds")
                    counter = 0
                    if _sequencerState in [SequencerType.DoingStandard, SequencerType.DoingStandardPlus, SequencerType.DoingPowerlink, SequencerType.DoingPowerlinkBridge]:
                        # if we're at the point of "doing" then give the client a chance to set everything up with all the async calls
                        await asyncio.sleep(1.0)
                    # If the state has changed then do it straight away, don't do the 1 second loop
                else:
                    # If the state has stayed the same then delay 1 second, if the state has changed then get on with it
                    await asyncio.sleep(1.0)
                    # increment the counter every loop
                    counter = counter + 1 if counter < a_day - 1 else 0  # reset the counter 24 hours (approx), has to be < so 4 hour delays are OK

                _sequencerStatePrev = _sequencerState

                if not self.suspendAllOperations:  ## To make sure as it could have changed in the 1 second sleep

                    #############################################################################################################################################################
                    ####### Check the global connection state of the panel, have we received data ###############################################################################
                    #######       These 3 tests take drastic action, they stop the integration    ###############################################################################
                    #############################################################################################################################################################
                    if self.lastRecvTimeOfPanelData is None:  # has any data been received from the panel yet, even just a single byte?
                        no_data_received_counter = no_data_received_counter + 1
                        # log.debug(f"[_sequencer] no_data_received_counter {no_data_received_counter}")
                        if no_data_received_counter >= NO_RECEIVE_DATA_TIMEOUT:  ## lets assume approx 30 seconds
                            log.error("[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. no data has been received from the panel)" )
                            self._reportProblem(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
                            no_data_received_counter = 0
                            continue   # just do the while loop, which will exit as self.suspendAllOperations will be True
                    elif self.lastPacket is None: # have we been able to construct at least one full and crc checked message 
                        no_packet_received_counter = no_packet_received_counter + 1
                        #log.debug(f"[_sequencer] no_packet_received_counter {no_packet_received_counter}")
                        if no_packet_received_counter >= NO_RECEIVE_DATA_TIMEOUT:  ## lets assume approx 30 seconds
                            log.error("[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. no valid packet has been received from the panel)" )
                            self._reportProblem(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
                            no_packet_received_counter = 0
                            continue   # just do the while loop, which will exit as self.suspendAllOperations will be True
                    else:  # Data has been received from the panel but check when it was last received
                        # calc time difference between now and when data was last received
                        no_packet_received_counter = 0
                        no_data_received_counter = 0
                        # calculate the time interval back to the last receipt of any data
                        interval = self._getUTCTimeFunction() - self.lastRecvTimeOfPanelData
                        if interval >= timedelta(seconds=LAST_RECEIVE_DATA_TIMEOUT):
                            log.error(f"[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. data has not been received from the panel in {interval})" )
                            self._reportProblem(AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED)
                            continue   # just do the while loop, which will exit as self.suspendAllOperations will be True

                    #############################################################################################################################################################
                    ####### Sequencer activities ################################################################################################################################
                    #############################################################################################################################################################

                    if (
                        _sequencerState not in [SequencerType.DoingStandard, SequencerType.DoingStandardPlus, SequencerType.DoingPowerlink, SequencerType.DoingPowerlinkBridge]
                        or changedState
                        or counter % 180 == 0
                    ):
                        # When we reach 1 of the 4 final states then stop logging it, but then output every 3 minutes
                        ps = [p.PanelState.name for p in self.PartitionState]
                        log.debug(f"[_sequencer] SeqState={str(_sequencerState)}     Counter={counter}      PanelMode={self.PanelMode}     PanelState={ps}     SendQueue={self.SendQueue.qsize()}")

                    if self.loopbackTest:
                        # This supports the loopback test
                        #await asyncio.sleep(2.0)
                        self._clearReceiveResponseList()
                        self._emptySendQueue(pri_level = -1) # empty the list
                        self._addMessageToSendList(Send.STOP)
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.LookForPowerlinkBridge:   ################################################################ LookForPowerlinkBridge  ###################################################
                        if not self.ForceStandardMode:
                            for i in range(0,2):
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self._addMessageToSendList(Send.PL_BRIDGE, priority = MessagePriority.IMMEDIATE, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                            # Make a 1 off request for the panel to send the Download Code and the panel name e.g. PowerMaster-10
                            self.EnableB0ReceiveProcessing = True
                            #s = self._create_B0_35_Data_Request(strlist = "3c 00 0f 00")
                            #s = self._create_B0_42_Data_Request(strlist = "3c 00 0f 00")
                            s = self._create_B0_42_Data_Request(strlist = "0f 00")
                            self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)

                            await asyncio.sleep(1.0)
                        _sequencerState = SequencerType.Reset
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.Reset:                    ################################################################ Reset                   ###################################################
                        # log.debug(f"[_sequencer] In Reset state {self.PowerLinkBridgeConnected} and {self.PowerLinkBridgeAlarm}")
                        if self.PowerLinkBridgeConnected and not self.PowerLinkBridgeAlarm:  # if bridge but the alarm panel is not connected then go no further
                            # This sequencer loop is once per second.  That is enough time between LookForPowerlinkBridge and here to make the connection and get a reply to set the variables
                            _sequencerState = SequencerType.LookForPowerlinkBridge
                            log.debug(f"[_sequencer] Waiting for Alarm Panel to connect to the Bridge")
                            await asyncio.sleep(1.0)
                        else:
                            reset_vars()
                            _sequencerState = SequencerType.InitialisePanel
                        continue   # just do the while loop

                    elif delay_loops > 0:                                           ################################################################ Delay Loop              ###################################################
                        no_data_received_counter = 0
                        no_packet_received_counter = 0
                        delay_loops = delay_loops - 1
                        _clearPanelErrorMessages() # Clear all panel reported errors for the duration of the delay
                        continue   # do all the basic connection checks above and then just do the while loop

                    elif (                                                          ################################################################ PanelWantsToEnrol       ###################################################
                        not self.pmDownloadMode
                        and not self.ForceStandardMode
                        and not self.allowAckToTriggerRestore
                        and self.PanelWantsToEnrol
                    ):     
                        log.debug("[_sequencer] Panel wants to enrol and not downloading so sending Enrol")
                        self.PanelWantsToEnrol = False
                        sendMsgENROL(True)
                        delay_loops = 3
                        continue   # just do the while loop

                    elif self.UnexpectedPanelKeepAlive:                             ################################################################ PanelKeepAlive          ###################################################
                        self.UnexpectedPanelKeepAlive = False
                        if (
                           not self.pmDownloadMode                                    
                           and not self.ForceStandardMode
                           and not self.allowAckToTriggerRestore
                           and self.PanelMode in [AlPanelMode.STOPPED]  # 
                        ):
                            log.debug("[_sequencer] Unexpected Panel Powerlink Keep Alive, setting sequencer to LookForPowerlinkBridge")
                            _sequencerState = SequencerType.LookForPowerlinkBridge
                            delay_loops = 2
                        else:
                            log.debug("[_sequencer] Unexpected Panel Powerlink Keep Alive, ignoring it")
                            
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.InitialisePanel:          ################################################################ Initialising            ###################################################
                        await asyncio.sleep(1.0)
                        _resetPanelInterface()
                        _clearPanelErrorMessages()
                        if not self.pmGotPanelDetails:
                            self._addMessageToSendList(Send.PANEL_DETAILS, options=[ [3, convertByteArray(self.DownloadCode)] ])  # 
                        _sequencerState = SequencerType.WaitingForPanelDetails
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.WaitingForPanelDetails:   ################################################################ WaitingForPanelDetails  ###################################################

                        # Take care of the first part of initialisation
                        if self.pmGotPanelDetails:          # Got 3C panel data message
                            log.debug(f"[_sequencer] Got panel details, I am a {self.PanelModel}")
                            # ignore all possible errors etc, call the function and ignore the return value
                            _clearPanelErrorMessages()
                            if self.ForceStandardMode:
                                if self.PanelType is not None and (self.PowerLinkBridgeConnected or self.isPowerMaster()):     #
                                    _sequencerState = SequencerType.GettingB0SensorMessages   #
                                else:
                                    self._addMessageToSendList(Send.EXIT)  # when we receive a 3C we know that the panel is in download mode, so exit download mode
                                    _sequencerState = SequencerType.AimingForStandard
                            else:
                                self.firstSendOfDownloadEprom = self._getUTCTimeFunction()
                                if self.pmDownloadByEPROM:
                                    _sequencerState = SequencerType.EPROMInitialiseDownload  # This is the same as default for PowerMax so should be OK
                                elif self.PanelType is not None and (self.PowerLinkBridgeConnected or self.isPowerMaster()):     #
                                    _sequencerState = SequencerType.GettingB0SensorMessages   #
                                else:
                                    log.warning("[_sequencer] Abnormal: Should not get here!  Got panel details and downloading by EPROM, tell the author of this integration by reporting an issue on Github")
                                    _sequencerState = SequencerType.EPROMInitialiseDownload

                        elif (s := processPanelErrorMessages()) != PanelErrorStates.AllGood:
                            self._clearReceiveResponseList()
                            _clearPanelErrorMessages()
                            delay_loops = 4
                            if s == PanelErrorStates.DespatcherException:
                                # start again, restart the despatcher task
                                _sequencerState = SequencerType.LookForPowerlinkBridge
                            elif s in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.AccessDeniedStop]:
                                _sequencerState = SequencerType.InitialisePanel
                                setNextDownloadCode(self.PanelType if self.PanelType is not None else 1)
                                log.debug("[_sequencer]    Abnormal: Moved on to next download code and going to init")
                            elif s == PanelErrorStates.Exit:
                                _sequencerState = SequencerType.InitialisePanel
                            elif s == PanelErrorStates.TimeoutReceived:
                                _sequencerState = SequencerType.InitialisePanel
                                log.debug("[_sequencer]    Abnormal: TimeoutReceived")
                            elif s == PanelErrorStates.DownloadRetryReceived:
                                delay_loops = 10
                                _sequencerState = SequencerType.InitialisePanel
                                log.debug(f"[_sequencer]    Abnormal: DownloadRetryReceived loop = {delay_loops}")
                            # Ignore other errors 
                        elif counter >= 7:     # up to 7 seconds to get panel data message (worst case to also allow for Bridge traffic)
                            log.debug("[_sequencer]    Abnormal: Taken too long, going to init")
                            _sequencerState = SequencerType.InitialisePanel

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.GettingB0SensorMessages:  ################################################################ GettingB0SensorMessages ###################################################

                        if not self.pmGotPanelDetails or self.PanelType is None: # This should never happen but just in case :)
                            _sequencerState = SequencerType.LookForPowerlinkBridge
                            continue

                        self.EnableB0ReceiveProcessing = True

                        (mandatory, optional) = self.checkPanelDataPresent(checkAllPanelData)
                        missing = mandatory | optional
                        
                        log.debug(f"[_sequencer]   checkPanelDataPresent {checkAllPanelData=}    missing items {mandatory=}  {optional=}")
                        checkAllPanelData = False

                        #zoneCnt = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
                        zoneCnt = self.getPanelCapability(IndexName.ZONES)
                        if len(mandatory) == 0 and len(self.PanelSettings[PanelSetting.ZoneEnrolled]) >= zoneCnt: # Include a check to make certain we have the sensor enrolled data
                            self.B0_Wanted = set()
                            # We can create the sensors with just the mandatory data and progress the sequencer
                            _clearPanelErrorMessages()
                            _sequencerState = SequencerType.CreateSensors
                        elif counter >= 20: # timeout. My PM panels both take about 7 to 8 seconds so if we get to 20 seconds then EPROMInitialiseDownload
                            self.pmForceDownloadByEPROM = True
                            self.pmDownloadByEPROM = True
                            _sequencerState = SequencerType.InitialisePanel
                            delay_loops = 2
                        elif counter != 3 and counter % 3 == 0: # every 3 seconds (or so). This is a compromise delay, not too often so the panel starts sending back "wait" messages.
                            _clearPanelErrorMessages()
                            _requestMissingPanelConfig(missing)
                        elif counter > 2 and (s := processPanelErrorMessages()) != PanelErrorStates.AllGood:
                            _clearPanelErrorMessages()
                            if s in [PanelErrorStates.BeeZeroInvalidCommand]:
                                # We've tried to get B0 messages to get Panel Data but it's replied with an InvalidCommand
                                self.pmForceDownloadByEPROM = True
                                self.pmDownloadByEPROM = True
                                _sequencerState = SequencerType.InitialisePanel
                                delay_loops = 2
                        elif self.PartitionState[0].PanelState == AlPanelStatus.DOWNLOADING:
                            self.triggeredDownload = False
                            self.pmDownloadInProgress = False
                            self.pmDownloadMode = False
                            self.pmDownloadComplete = True
                            if counter == 0:                             # First time just get the panel status
                                log.debug(f"[_sequencer]   Panel status is DOWNLOADING so updating panel status")
                                self.fetchPanelStatus()                  # This should update .PanelState
                            elif counter % 5 == 0:
                                log.debug(f"[_sequencer]   Panel status is DOWNLOADING so trying to kick it out")
                                self._clearReceiveResponseList()
                                self._emptySendQueue(pri_level = 1)
                                self._addMessageToSendList(Send.EXIT)    # Kick the panel out of downloading, and wait for 1.5 seconds
                                self._addMessageToSendList(Send.STOP)    # Kick the panel out of downloading, and wait for 1.5 seconds
                                self.fetchPanelStatus()                  # This should update .PanelState

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.EPROMInitialiseDownload:   ################################################################ EPROMInitialiseDownload  ###################################################

                        self.EnableB0ReceiveProcessing = False

                        interval = self._getUTCTimeFunction() - self.firstSendOfDownloadEprom

                        if self.DownloadCounter >= DOWNLOAD_RETRY_COUNT or (not EPROM_DOWNLOAD_ALL and interval > timedelta(seconds=DOWNLOAD_TIMEOUT)): 
                            # Give it DOWNLOAD_RETRY_COUNT attempts start the download
                            # Give it DOWNLOAD_TIMEOUT seconds to complete the download
                            log.warning("[Controller] Abnormal: ********************** Download Timer has Expired, Download has taken too long *********************")
                            self.sendPanelUpdate(AlCondition.DOWNLOAD_TIMEOUT)                 # download timer expired

                            if self.PowerLinkBridgeConnected:
                                log.debug("[Controller] ***************************** Bridge connected so start again ***************************************")
                                # Reset download counter to check for the number of attempts
                                self.DownloadCounter = 0
                                # Delete all existing EPROM data
                                self.epromManager.reset()
                                _sequencerState = SequencerType.Reset
                            else:
                                log.debug("[Controller] ************************************* Going to standard mode ***************************************")
                                _sequencerState = SequencerType.AimingForStandard
                        else:
                            # Populate the full list of EPROM blocks
                            self.myDownloadList = self.epromManager.populatEPROMDownload(self.isPowerMaster())
                            # Send the first EPROM block to the panel to retrieve
                            if len(self.myDownloadList) == 0:
                                # This is the message to tell us that the panel has finished download mode, so we too should stop download mode
                                log.debug("[_readPanelSettings] Download Complete")
                                self.triggeredDownload = False
                                self.pmDownloadInProgress = False
                                self.pmDownloadMode = False
                                self.pmDownloadComplete = True
                                _sequencerState = SequencerType.EPROMDownloadComplete
                            else:
                                if self.PowerLinkBridgeConnected:
                                    if self.PowerLinkBridgeStealth:
                                        _sequencerState = SequencerType.EPROMTriggerDownload
                                        log.debug("[_sequencer] Bridge already in Stealth, continuing to EPROMTriggerDownload")
                                    else:
                                        log.debug("[_sequencer] Sending command to Bridge - Please Turn Stealth ON")
                                        command = 2   # Stealth command
                                        param = 1     # Enter it
                                        self._addMessageToSendList(Send.PL_BRIDGE, priority = MessagePriority.IMMEDIATE, options=[ [1, command], [2, param] ])  # Tell the Bridge to go in to exclusive mode
                                        command = 1   # Get Status command
                                        param = 0     # Irrelevant
                                        self._addMessageToSendList(Send.PL_BRIDGE, priority = MessagePriority.IMMEDIATE, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                                        # Continue in this SequencerType until the bridge is in stealth
                                else:
                                    _sequencerState = SequencerType.EPROMTriggerDownload

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.EPROMTriggerDownload:     ################################################################ EPROMTriggerDownload    ###################################################

                        self._clearReceiveResponseList()
                        self._emptySendQueue(pri_level = 1)
                        self.DownloadCounter = self.DownloadCounter + 1
                        log.debug("[_sequencer] Asking for panel EPROM")

                        self._addMessageToSendList(Send.DOWNLOAD_DL, options=[ [3, convertByteArray(self.DownloadCode)] ])  #
                        # We got a first response, now we can Download the panel EPROM settings
                        self.lastSendOfDownloadEprom = self._getUTCTimeFunction()
                        # Kick off the download sequence and set associated variables
                        self.pmExpectedResponse = set()
                        self.PanelMode = AlPanelMode.DOWNLOAD
                        self.PartitionState[0].PanelState = AlPanelStatus.DOWNLOADING  # Downloading
                        self.sendPanelUpdate(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
                        log.debug("[_readPanelSettings] Download Ongoing")
                        self.triggeredDownload = True
                        self.pmDownloadInProgress = True
                        self._addMessageToSendList(Send.DL, options=[ [1, self.myDownloadList.pop(0)] ])  # Read the next block of EPROM data
                        lastrecv = self.lastRecvTimeOfPanelData
                        _sequencerState = SequencerType.EPROMStartedDownload

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.EPROMStartedDownload:     ################################################################ EPROMStartedDownload    ###################################################

                        # We got a first response, now we can Download the panel EPROM settings
                        if (s := processPanelErrorMessages()) != PanelErrorStates.AllGood:
                            if s in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.DownloadRetryReceived, PanelErrorStates.TimeoutReceived]:
                                self.pmExpectedResponse = set()
                                _sequencerState = SequencerType.EPROMInitialiseDownload
                            elif s == PanelErrorStates.DespatcherException:
                                # start again, restart the despatcher task
                                _sequencerState = SequencerType.Reset
                            else:
                                _sequencerState = SequencerType.InitialisePanel
                        else:
                            interval = self._getUTCTimeFunction() - self.lastSendOfDownloadEprom
                            log.debug(f"[_sequencer] interval={interval}  td={DOWNLOAD_RETRY_DELAY}   self.lastSendOfDownloadEprom(UTC)={self.lastSendOfDownloadEprom}    timenow(UTC)={self._getUTCTimeFunction()}")

                            if interval > timedelta(seconds=DOWNLOAD_RETRY_DELAY):            # Give it this number of seconds to start the downloading
                                _sequencerState = SequencerType.EPROMInitialiseDownload
                            elif lastrecv != self.lastRecvTimeOfPanelData and (self.pmDownloadInProgress or self.pmDownloadComplete):
                                _sequencerState = SequencerType.EPROMDoingDownload

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.EPROMDoingDownload:       ################################################################ EPROMDoingDownload      ###################################################

                        if (s := processPanelErrorMessages()) != PanelErrorStates.AllGood:
                            if s in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.DownloadRetryReceived, PanelErrorStates.TimeoutReceived]:
                                self.pmExpectedResponse = set()
                                _sequencerState = SequencerType.EPROMInitialiseDownload
                            elif s == PanelErrorStates.DespatcherException:
                                # start again, restart the despatcher task
                                _sequencerState = SequencerType.Reset
                            else:
                                _sequencerState = SequencerType.InitialisePanel
                        elif self.pmDownloadComplete:
                            _sequencerState = SequencerType.EPROMDownloadComplete
                        else:
                            intervalStart = self._getUTCTimeFunction() - self.lastSendOfDownloadEprom
                            intervalLastReceive = self._getUTCTimeFunction() - self.lastRecvTimeOfPanelData
                            #log.debug(f"[_sequencer] timenow={self._getUTCTimeFunction()}   intervalStart={intervalStart}  self.lastSendOfDownloadEprom={self.lastSendOfDownloadEprom}")
                            #log.debug(f"[_sequencer]                                        intervalLastReceive={intervalLastReceive}  self.lastRecvTimeOfPanelData={self.lastRecvTimeOfPanelData}")
                            if intervalStart > timedelta(seconds=60):                         # Download hasn't finished in this timeout
                                _sequencerState = SequencerType.InitialisePanel
                            elif intervalLastReceive > timedelta(seconds=3):                  # 3 seconds since we last received a byte of data from the panel
                                _sequencerState = SequencerType.InitialisePanel

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.EPROMDownloadComplete:    ################################################################ EPROMDownloadComplete   ###################################################

                        # Check the panel type from EPROM against the panel type from the 3C message to give a basic test of the EPROM download
                        pmPanelTypeNr = self.epromManager.lookupEpromSingle(EPROM.PANEL_TYPE_CODE)    
                        if pmPanelTypeNr is None or (pmPanelTypeNr is not None and pmPanelTypeNr == 0xFF):
                            log.error(f"[_sequencer] Lookup of panel type string and model from the EPROM failed, assuming EPROM download failed {pmPanelTypeNr=}, going to Standard Mode")
                            _sequencerState = SequencerType.AimingForStandard
                        elif self.PanelType is not None and self.PanelType != pmPanelTypeNr:
                            log.error(f"[_sequencer] Panel Type not set from EPROM, assuming EPROM download failed {pmPanelTypeNr=}, going to Standard Mode")
                            _sequencerState = SequencerType.AimingForStandard
                        else:
                            # Process the EPROM data
                            try:
                                log.debug("[Process Settings] Process Settings from EPROM")
                                self._processEPROMSettings()
                                self.PanelStatus[PANEL_STATUS.DEVICES] = self._processKeypadsAndSirensFromEPROM()
                                self._updateAllSirens()
                                self._processX10Settings()
                                log.debug("[Process Settings] EPROM Processing Complete")
                            except Exception as ex:
                                log.warning("[Process Settings] EPROM Processing failed by exception:")
                                log.warning(f"[Process Settings]             {ex}")
                                _sequencerState = SequencerType.Reset
                            else:
                                if self.isPowerMaster(): # PowerMaster so get any remaining B0 data
                                    _sequencerState = SequencerType.GettingB0SensorMessages
                                    checkAllPanelData = False                                    # We've downloaded the EPROM so no need to get all B0 panel data
                                    self.EnableB0ReceiveProcessing = True
                                else:
                                    _sequencerState = SequencerType.CreateSensors

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.CreateSensors:            ################################################################ CreateSensors and PGM   ###################################################
                        self._updateAllSensors()
                        self._dumpSensorsToLogFile(True)
                        self._createPGMSwitch()

                        if not self.ForceStandardMode and self.gotValidUserCode():
                            if self.PowerLinkBridgeConnected:
                                log.debug("[_sequencer] Sending command to Bridge - Stealth OFF")
                                command = 2   # Stealth command
                                param = 0     # Exit it
                                self._addMessageToSendList(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to exit exclusive mode
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self._addMessageToSendList(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                                self.PanelMode = AlPanelMode.POWERLINK_BRIDGED
                                _sequencerState = SequencerType.DoingPowerlinkBridge
                            else:
                                self.PanelMode = AlPanelMode.STANDARD_PLUS
                                _sequencerState = SequencerType.EPROMExitDownload
                            self.sendPanelUpdate(AlCondition.DOWNLOAD_SUCCESS)   # download completed successfully, panel type matches and got usercode (so assume all sensors etc loaded)
                        else:
                            _sequencerState = SequencerType.AimingForStandard
                            self.sendPanelUpdate(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.EPROMExitDownload:        ################################################################ EPROMExitDownload       ###################################################

                        if self.PartitionState[0].PanelState != AlPanelStatus.DOWNLOADING:
                            self.triggeredDownload = False
                            self.pmDownloadInProgress = False
                            self.pmDownloadMode = False
                            self.pmDownloadComplete = True
                            _sequencerState = SequencerType.EnrollingPowerlink
                        elif counter % 3 == 0: # and self.PartitionState[0].PanelState == AlPanelStatus.DOWNLOADING:
                            self._clearReceiveResponseList()
                            self._emptySendQueue(pri_level = 1)
                            self._addMessageToSendList(Send.EXIT)
                            self.fetchPanelStatus()                  # This should update self.PartitionState[0].PanelState
                        #elif (counter+2) % 4 == 0: # and self.PartitionState[0].PanelState == AlPanelStatus.DOWNLOADING:
                        #    self._clearReceiveResponseList()
                        #    self._emptySendQueue(pri_level = 1)
                        #    self._addMessageToSendList(Send.STOP)
                        #    self.fetchPanelStatus()                  # This should update self.PartitionState[0].PanelState

                        continue   # just do the while loop
                        
                    elif _sequencerState == SequencerType.EnrollingPowerlink:       ################################################################ EnrollingPowerlink      ###################################################

                        if self.PanelMode in [AlPanelMode.POWERLINK]:
                            _sequencerState = SequencerType.DoingPowerlink         # Very unlikely but possible
                        elif counter == 10:
                            self.PanelMode = AlPanelMode.STANDARD_PLUS                    # After 10 attempts to enrol, stay in StandardPlus Emulation Mode
                            _sequencerState = SequencerType.DoingStandardPlus
                        else:
                            log.debug(f"[_sequencer] Try to enrol (panel {self.PanelModel})")
                            if self.PartitionState[0].PanelState == AlPanelStatus.DOWNLOADING:
                                log.debug(f"[_sequencer]       Partition 0 still thinks we're Downloading (panel {self.PanelModel})")
                                # ??????????????????????????? SHOULD WE GO BACK TO EPROMExitDownload ????????????????????
                            sendMsgENROL()  #  Try to enrol with the Download Code that worked for Downloading the EPROM
                            _sequencerState = SequencerType.WaitingForEnrolSuccess
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.WaitingForEnrolSuccess:   ################################################################ WaitingForEnrolSuccess  ###################################################

                        self.keep_alive_counter = self.keep_alive_counter + 1
                        log.debug(f"[_sequencer]     WaitingForEnrolSuccess {self.SendQueue.empty()=} {self.pmDownloadMode=} {self.keep_alive_counter=}  threshold is 15")
                        
                        if self.PanelType is not None and not self.AutoEnrol:
                            self.PanelMode = AlPanelMode.STANDARD_PLUS                    # Cannot AutoEnrol this panel so go straight to Std+ operation
                            _sequencerState = SequencerType.DoingStandardPlus
                            log.debug(f"[_sequencer]     WaitingForEnrolSuccess        Panel does not support Auto Enrol, going to Standard Plus and waiting for manual enrol")
                        elif (s := processPanelErrorMessages()) == PanelErrorStates.DespatcherException:
                            # start again, restart the despatcher task
                            _sequencerState = SequencerType.Reset
                        elif s != PanelErrorStates.AllGood:
                            _clearPanelErrorMessages()
                            self.pmExpectedResponse = set()
                            self.PanelMode = AlPanelMode.STANDARD_PLUS
                            _sequencerState = SequencerType.EPROMExitDownload
                        elif self.PanelMode in [AlPanelMode.POWERLINK]:
                            _sequencerState = SequencerType.DoingPowerlink
                            self._reset_keep_alive_messages()
                        elif counter == (MAX_TIME_BETWEEN_POWERLINK_ALIVE if self.receivedPowerlinkAcknowledge else 3):   
                            # once we receive a powerlink acknowledge then we wait for the I'm alive message (usually every 30 seconds from the panel)
                            self.PanelMode = AlPanelMode.STANDARD_PLUS
                            _sequencerState = SequencerType.EPROMExitDownload
                        elif self.SendQueue.empty() and not self.pmDownloadMode and self.keep_alive_counter >= 15:  #
                            self._reset_keep_alive_messages()
                            self._addMessageToSendList (Send.EXIT)
                            self._addMessageToSendList (Send.ALIVE)

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.AimingForStandard:        ################################################################ AimingForStandard       ###################################################

                        self.PanelMode = AlPanelMode.STANDARD
                        # only if we meet the criteria do we move on to the next step.  Until then just do it
                        _gotoStandardModeStopDownload()
                        _sequencerState = SequencerType.DoingStandard
                        if self.isPowerMaster(): # PowerMaster so get B0 data
                            # Powerlink panel so ask the panel for B0 data to get panel details, as these can be asked for and received within download mode we can do it straight away
                            #log.debug("[_sequencer] Adding lots of B0 requests to wanted list")
                            #self.B0_Wanted.update([0x20, 0x21, 0x2d, 0x1f, 0x07, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x11, 0x13, 0x14, 0x15, 0x18, 0x1a, 0x19, 0x1b, 0x1d, 0x2f, 0x31, 0x33, 0x1e, 0x24, 0x02, 0x23, 0x3a, 0x4b])

                            log.debug(f"[_sequencer] Aiming for standard - Adding B0 wanted data to list")
                            # Request Sensor Information and State
                            self.B0_Wanted.add(B0SubType.ZONE_NAMES)         # 21
                            self.B0_Wanted.add(B0SubType.ZONE_TYPES)         # 2D
                            self.B0_Wanted.add(B0SubType.DEVICE_TYPES)       # 1F
                            self.B0_Wanted.add(B0SubType.PANEL_STATE)        # 24
                            self.B0_Wanted.add(B0SubType.ZONE_LAST_EVENT)    # 4B
                            self.B0_Wanted.add(B0SubType.ZONE_OPENCLOSE)     # 18
                            self.B0_Wanted.add(B0SubType.ZONE_TEMPERATURE)   # 3D  
                            self.B0_Wanted.add(B0SubType.SENSOR_ENROL)       # 1D
                            self.B0_Wanted.add(B0SubType.TAMPER_ALERT)       #
                            self.B0_Wanted.add(B0SubType.TAMPER_ACTIVITY)    #
                            self.B0_Wanted.add(B0SubType.SYSTEM_CAP)    #

                        else:    # PowerMax get ZONE_NAMES, ZONE_TYPES etc
                            self._addMessageToSendList(Send.ZONENAME)
                            self._addMessageToSendList(Send.ZONETYPE)
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.DoingStandard:            ################################################################ DoingStandard           ###################################################
                        # Put all the special standard mode things here
                        # Keep alive functionality
                        self.keep_alive_counter = self.keep_alive_counter + 1
                        if self.SendQueue.empty() and not self.pmDownloadMode and self.keep_alive_counter >= self.KeepAlivePeriod:  #
                            self._reset_keep_alive_messages()
                            self._addMessageToSendList (Send.STATUS_SEN)

                        # Do most of this for ALL Panel Types
                        # Only check these every 180 seconds
                        if (counter % 180) == 0:
                            if self.PartitionState[0].PanelState == AlPanelStatus.UNKNOWN:
                                log.debug("[_sequencer] ****************************** Getting Panel Status ********************************")
                                self._addMessageToSendList(Send.STATUS_SEN)
                            elif self.PartitionState[0].PanelState == AlPanelStatus.DOWNLOADING:
                                log.debug("[_sequencer] ****************************** Exit Download Kicker ********************************")
                                self._addMessageToSendList(Send.EXIT, priority = MessagePriority.URGENT)
                            elif not self.pmGotPanelDetails:
                                log.debug("[_sequencer] ****************************** Asking For Panel Details ****************************")
                                _sequencerState = SequencerType.InitialisePanel
                            else:
                                # The first time this may create sensors (for PowerMaster, especially those in the range Z33 to Z64 as the A5 message will not have created them)
                                # Subsequent calls make sure we have all zone names, zone types and the sensor list
                                updateSensorNamesAndTypes()

                    elif _sequencerState == SequencerType.DoingStandardPlus:        ################################################################ DoingStandardPlus       ###################################################

                        # Put all the special standard plus mode things here
                        # Keep alive functionality
                        self.keep_alive_counter = self.keep_alive_counter + 1
                        if self.SendQueue.empty() and not self.pmDownloadMode and self.keep_alive_counter >= self.KeepAlivePeriod:  #
                            self._reset_keep_alive_messages()
                            self._addMessageToSendList (Send.ALIVE)
                            #if self.PanelType is not None and not self.AutoEnrol:
                            #    self.PanelMode = AlPanelMode.STANDARD_PLUS             # should already be but just to make sure
                            #    _sequencerState = SequencerType.EPROMExitDownload

                        if self.PanelMode in [AlPanelMode.POWERLINK]:
                            _sequencerState = SequencerType.DoingPowerlink
                        elif self.PanelMode in [AlPanelMode.POWERLINK_BRIDGED]:  # This is only possible from EPROM Download so it's unlikely to happen, but just in case ....
                            _sequencerState = SequencerType.DoingPowerlinkBridge

                    elif _sequencerState == SequencerType.DoingPowerlink:           ################################################################ DoingPowerlink          ###################################################
                        # Put all the special powerlink mode things here
                        self.PanelMode = AlPanelMode.POWERLINK

                        # by here we should have all mandatory panel settings but maybe not all optional
                        (mandatory, optional) = self.checkPanelDataPresent()
                        missing = mandatory | optional
                        
                        if len(mandatory) > 0:
                            log.debug(f"[_sequencer]   Mandatory should all be obtained by now but it isnt {mandatory=}")

                        if self.isPowerMaster() and len(missing) > 0 and counter != 3 and counter % 3 == 0: # every 3 seconds (or so). This is a compromise delay, not too often so the panel starts sending back "wait" messages.
                            log.debug(f"[_sequencer]   requesting missing panel data, missing items {mandatory=}  {optional=}")
                            # mandatory should be empty but add it just in case
                            _requestMissingPanelConfig(missing)

                        # Keep alive functionality
                        self.keep_alive_counter = self.keep_alive_counter + 1    # This is for me sending to the panel
                        self.powerlink_counter = self.powerlink_counter + 1      # This gets reset to 0 when I receive I'm Alive from the panel

                        if self.powerlink_counter > POWERLINK_IMALIVE_RETRY_DELAY:
                            # Go back to Std+ and re-enrol
                            log.debug(f"[_sequencer] ****************************** Not Received I'm Alive From Panel for {POWERLINK_IMALIVE_RETRY_DELAY} Seconds, going to Std+ **************")
                            self.receivedPowerlinkAcknowledge = False
                            self.PanelMode = AlPanelMode.STANDARD_PLUS
                            _sequencerState = SequencerType.EnrollingPowerlink
                            self._reportProblem(AlTerminationType.NO_POWERLINK_FOR_PERIOD)
                            continue   # just do the while loop

                        if self.SendQueue.empty() and not self.pmDownloadMode and self.keep_alive_counter >= self.KeepAlivePeriod:  #
                            # Every self.KeepAlivePeriod seconds, unless watchdog has been reset
                            self._reset_keep_alive_messages()
                            # Send I'm Alive to the panel so it knows we're still here
                            self._addMessageToSendList (Send.ALIVE)

                    elif _sequencerState == SequencerType.DoingPowerlinkBridge:     ################################################################ DoingPowerlinkBridge    ###################################################

                        if self.PowerLinkBridgeConnected:
                            if self.PowerLinkBridgeStealth:
                                log.debug("[_sequencer] Sending commands to Bridge to exit stealth and get status")
                                command = 2   # Stealth command
                                param = 0     # Exit it
                                self._addMessageToSendList(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to exit exclusive mode
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self._addMessageToSendList(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                                self.PowerLinkBridgeStealth = False # To make certain it's disabled

                            elif counter % 30 == 0:  # approx every 30 seconds
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self._addMessageToSendList(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status

                            interval = self._getUTCTimeFunction() - self.B0_LastPanelStateTime # make sure that we get the panel state at most every 45 seconds. If we get it for other reasons then OK
                            if interval >= timedelta(seconds=25):                              # every 25 seconds get the panel state
                                log.debug("[_sequencer] Adding Panel State request to B0 wanted due to timer")
                                self.B0_LastPanelStateTime = self._getUTCTimeFunction()        # to stop it retriggering (although its a set so it should not matter)
                                self.B0_Wanted.add(B0SubType.PANEL_STATE)                  # Remember that it's a set so if it's already there then it will only be in once

                    #############################################################################################################################################################
                    ####### Drop through to here to do generic code for DoingStandard, DoingStandardPlus, DoingPowerlinkBridge and DoingPowerlink ###############################
                    #############################################################################################################################################################

                    #if self.isPowerMaster() and counter % 4 == 0:
                        # Dump normal B0 data to the log file
                        #m = []
                        #m.append(myspecialcounter % 256)

                        #log.debug(f"[Process Settings]      myspecialcounter {myspecialcounter}   m={m}")
                        #tmp = [pmSendMsgB0[i].data if i in pmSendMsgB0 for i in m] # Theres only 1 thing in m but do it like this so it can do more than 1
                        #s = self._create_B0_Data_Request(taglist = tmp)
                        #self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)

                        # Dump 0x35 data to the log file
                        #high = myspecialcounter // 256
                        #low  = myspecialcounter % 256
                        #st = f"{low:0>2x} {high:0>2x}"
                        #s = self._create_B0_35_Data_Request(strlist = st)
                        #log.debug(f"[Process Settings]      myspecialcounter {myspecialcounter}   st={st}")
                        #myspecialcounter = (myspecialcounter + 1) % 256
                        #self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)

                        #myspecialcounter = myspecialcounter + 1


                    if self.PanelMode == AlPanelMode.POWERLINK_BRIDGED and self.PartitionState[0].PanelState == AlPanelStatus.DOWNLOADING:
                        _my_panel_state_trigger_count = _my_panel_state_trigger_count - 1
                        log.debug(f"[_sequencer] By here we should be in normal operation, we are in {self.PanelMode.name} panel mode"
                                  f" and status is {self.PartitionState[0].PanelState}    {_my_panel_state_trigger_count=}")
                        if _my_panel_state_trigger_count < 0:
                            _my_panel_state_trigger_count = 10
                            self._reset_keep_alive_messages()
                            self._reset_watchdog_timeout()
                            _resetPanelInterface()
                            _clearPanelErrorMessages()
                        continue   # just do the while loop
                    elif not (self.PowerLinkBridgeConnected and self.PowerLinkBridgeProxy) and \
                        (self.PartitionState[0].PanelState == AlPanelStatus.DOWNLOADING or self.PanelMode == AlPanelMode.DOWNLOAD):
                        # We may still be in the downloading state or the panel is in the downloading state
                        _my_panel_state_trigger_count = _my_panel_state_trigger_count - 1
                        log.debug(f"[_sequencer] By here we should be in normal operation, we are in {self.PanelMode.name} panel mode"
                                  f" and status is {self.PartitionState[0].PanelState}    {_my_panel_state_trigger_count=}")
                        if _my_panel_state_trigger_count < 0:
                            if self.PanelMode in [AlPanelMode.POWERLINK_BRIDGED]:
                                # Restart the sequence from the beginning
                                _sequencerState = SequencerType.Reset
                            else:
                                _my_panel_state_trigger_count = 10
                                self._reset_keep_alive_messages()
                                self._reset_watchdog_timeout()
                                _resetPanelInterface()
                                _clearPanelErrorMessages()
                                if self.pmDownloadComplete or self.ForceStandardMode:
                                    self._triggerRestoreStatus() # Clear message buffers and send a Restore (if in Powerlink) or Status (not in Powerlink) to the Panel
                                # Do not come back here for 5 seconds at least
                                delay_loops = 5
                        continue   # just do the while loop

                    if self.PanelMode not in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.MINIMAL_ONLY]:
                        # By here the panel connection should be in one of the proper modes (and we've already tested for DOWNLOAD) but it isn't so go back to the beginning
                        #    Allow it for 5 seconds (_my_panel_state_trigger_count is set to 5 by default) but then restart the sequence
                        _my_panel_state_trigger_count = _my_panel_state_trigger_count - 1
                        log.debug(f"[_sequencer] By here we should be in normal operation but we are still in {self.PanelMode.name} panel mode     {_my_panel_state_trigger_count=}")
                        if _my_panel_state_trigger_count < 0:
                            _my_panel_state_trigger_count = 10
                            self._reset_keep_alive_messages()
                            self._reset_watchdog_timeout()
                            # Restart the sequence from the beginning
                            _sequencerState = SequencerType.Reset
                        continue   # just do the while loop

                    _my_panel_state_trigger_count = 5

                    if self.PanelResetEvent:
                        # If the user has been in to the installer settings there may have been changes that are relevant to this integration.
                        self.PanelResetEvent = False
                        log.debug(f"[_sequencer] Performing a System Reset so reloading Panel Data")
                        self.sendPanelUpdate ( AlCondition.PANEL_RESET )   # push changes through to the host, the panel itself has been reset. Let user decide what action to take.
                        # Restart the sequence from Reset.  
                        reset_vars()
                        _sequencerState = SequencerType.Reset
                        continue   # just do the while loop

                    if not _sendStartUp:
                        _sendStartUp = True
                        self.sendPanelUpdate(AlCondition.STARTUP_SUCCESS)   # startup completed successfully (in whatever mode)

                    self.EnableB0ReceiveProcessing = True

                    # If Std+ or PL then periodically check and then maybe update the time in the panel
                    if self.AutoSyncTime and self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:

                        panel_check_time = POWERMASTER_CHECK_TIME_INTERVAL if self.isPowerMaster() else POWERMAX_CHECK_TIME_INTERVAL
                        update_check_interval = (counter % panel_check_time == 0
                                                or  (counter % 10 == 0
                                                    and (self.Panel_Integration_Time_Difference is None 
                                                        or (self.Panel_Integration_Time_Difference is not None
                                                            and abs(self.Panel_Integration_Time_Difference.total_seconds()) > 5)
                                                        )
                                                    )
                                                )
                        
                        if self.isPowerMaster():
                            # PowerMaster Panels
                            if update_check_interval:
                                # Request Sensor Information and State
                                #      remember that self.B0_Wanted is a set so can only be added once
                                log.debug("[_sequencer] Adding Panel and Sensor State requests - Set A")
                                self.B0_Wanted.update({B0SubType.SENSOR_ENROL, B0SubType.ZONE_NAMES, B0SubType.ZONE_TYPES, B0SubType.DEVICE_TYPES, B0SubType.PANEL_STATE, B0SubType.ZONE_OPENCLOSE, B0SubType.WIRED_STATUS_1, 0x53})
                            
                            elif (counter + (POWERMASTER_CHECK_TIME_INTERVAL / 2)) % POWERMASTER_CHECK_TIME_INTERVAL == 0:
                                # Request Sensor Information and State
                                #      remember that self.B0_Wanted is a set so can only be added once
                                log.debug("[_sequencer] Adding Panel and Sensor State requests - Set B")
                                self.B0_Wanted.update({B0SubType.ZONE_TEMPERATURE, B0SubType.TAMPER_ALERT, B0SubType.TAMPER_ACTIVITY, B0SubType.WIRELESS_DEV_MISSING, B0SubType.WIRELESS_DEV_INACTIVE, B0SubType.WIRELESS_DEV_ONEWAY, B0SubType.WIRED_STATUS_2}) # WIRELESS_DEV_CHANNEL
                        elif update_check_interval: # counter % POWERMAX_CHECK_TIME_INTERVAL 
                            # PowerMax Panels
                            # We set the time and then check it periodically, and then set it again if different by more than 5 seconds
                            #     every 4 hours (approx) or if not set yet or a big difference (set from B0 data)
                            # Get the time from the panel (this will compare to local time and set the panel time if different)
                            self._addMessageToSendList(Send.GETTIME, priority = MessagePriority.URGENT)

                    elif self.PanelMode in [AlPanelMode.STANDARD]:
                        if self.isPowerMaster():
                            # PowerMaster Panels
                            if (counter % STANDARD_STATUS_RETRY_DELAY) == 0:
                                # Request Sensor Information and State
                                #      remember that self.B0_Wanted is a set so can only be added once
                                log.debug("[_sequencer] Adding Panel and Sensor State requests - Set A")
                                self.B0_Wanted.update({B0SubType.SENSOR_ENROL, B0SubType.ZONE_NAMES, B0SubType.ZONE_TYPES, B0SubType.DEVICE_TYPES, B0SubType.PANEL_STATE, B0SubType.ZONE_OPENCLOSE})
                            
                            elif (counter + (STANDARD_STATUS_RETRY_DELAY / 2)) % STANDARD_STATUS_RETRY_DELAY == 0:
                                # Request Sensor Information and State
                                #      remember that self.B0_Wanted is a set so can only be added once
                                log.debug("[_sequencer] Adding Panel and Sensor State requests - Set B")
                                self.B0_Wanted.update({B0SubType.ZONE_TEMPERATURE, B0SubType.TAMPER_ALERT, B0SubType.TAMPER_ACTIVITY, B0SubType.WIRELESS_DEV_MISSING, B0SubType.WIRELESS_DEV_INACTIVE, B0SubType.WIRELESS_DEV_ONEWAY}) # WIRELESS_DEV_CHANNEL

                        elif (counter % STANDARD_STATUS_RETRY_DELAY) == 0:
                            # PowerMax Panels
                            log.debug("[_sequencer] Adding Panel for sensor status")
                            self._addMessageToSendList(Send.STATUS_SEN)

                    # Check all error conditions sent from the panel
                    dotrigger = False
                    while (s := processPanelErrorMessages()) != PanelErrorStates.AllGood: # An error state from the panel so process it
                        match s:
                            case PanelErrorStates.AccessDeniedDownload | PanelErrorStates.AccessDeniedStop:
                                log.debug("[_sequencer] Attempt to download from the panel that has been rejected, assumed to be from get/set time")
                                # reset the download params just in case it's not a get/set time
                                self.pmDownloadInProgress = False
                                self.pmDownloadMode = False
                                dotrigger = True
                            case PanelErrorStates.AccessDeniedPin:
                                log.debug("[_sequencer] Attempt to send a command message to the panel that has been denied, wrong pin code used")
                                # INTERFACE : tell user that wrong pin has been used
                                self._reset_watchdog_timeout()
                                self.sendPanelUpdate(AlCondition.PIN_REJECTED)  # push changes through to the host, the pin has been rejected
                            case PanelErrorStates.AccessDeniedCommand:
                                log.debug("[_sequencer] Attempt to send a command message to the panel that has been rejected")
                                self._reset_watchdog_timeout()
                                self.sendPanelUpdate(AlCondition.COMMAND_REJECTED)  # push changes through to the host, something has been rejected (other than the pin)
                            case PanelErrorStates.Exit:
                                log.debug(f"[_sequencer] Received a Exit state, we assume that DOWNLOAD was called and rejected by the panel")
                                if Receive.PANEL_INFO in self.pmExpectedResponse:    # We sent DOWNLOAD to the panel (probably to set the time) and it has responded with EXIT
                                    self.pmExpectedResponse.remove(Receive.PANEL_INFO)  #
                            case PanelErrorStates.TimeoutReceived:
                                log.debug(f"[_sequencer] Received a Panel state Timeout")
                                # Reset Send state (clear queue and reset flags)
                                self._clearReceiveResponseList()
                                #self._emptySendQueue(pri_level = 1)
                                dotrigger = True
                            case PanelErrorStates.DownloadRetryReceived:
                                log.debug(f"[_sequencer] Received a Download Retry and dont know why {str(s)}")
                                dotrigger = True
                            case PanelErrorStates.DespatcherException:
                                # restart the despatcher task
                                self.startDespatcher()
                            case PanelErrorStates.BeeZeroInvalidCommand:
                                log.debug(f"[_sequencer] Received a BeeZeroInvalidCommand {str(s)}")
                            case _:
                                log.debug(f"[_sequencer] Received an unexpected panel error state and dont know why {str(s)}")
                                dotrigger = True

                    # Do the Watchdog functionality
                    self.watchdog_counter = self.watchdog_counter + 1
                    # every iteration, decrement all WATCHDOG_MAXIMUM_EVENTS watchdog counters (loop time is 1 second approx, doesn't have to be accurate)
                    watchdog_list = [x - 1 if x > 0 else 0 for x in watchdog_list]

                    if self.watchdog_counter >= WATCHDOG_TIMEOUT:  #  the loop runs at 1 second
                        # Check to see if the watchdog timer has expired
                        # watchdog timeout
                        log.debug("[_sequencer] ****************************** WatchDog Timer Expired ********************************")
                        self._reset_watchdog_timeout()
                        self._reset_keep_alive_messages()

                        # Total Watchdog timeouts
                        self.WatchdogTimeoutCounter = self.WatchdogTimeoutCounter + 1
                        # Total Watchdog timeouts in last 24 hours. Total up the entries > 0
                        self.WatchdogTimeoutPastDay = 1 + sum(1 if x > 0 else 0 for x in watchdog_list)    # in range 1 to 11

                        # move to the next position which is the oldest entry in the list
                        watchdog_pos = (watchdog_pos + 1) % WATCHDOG_MAXIMUM_EVENTS

                        # When watchdog_list[watchdog_pos] > 0 then the 24 hour period from the timeout WATCHDOG_MAXIMUM_EVENTS times ago hasn't decremented to 0.
                        #    So it's been less than 1 day for the previous WATCHDOG_MAXIMUM_EVENTS timeouts
                        self._clearReceiveResponseList()
                        if not self.StopTryingDownload and watchdog_list[watchdog_pos] > 0:
                            if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
                                log.debug("[_sequencer]               **************** Too many Timeouts in 24 hours and we're in Powerlink mode, going to re-establish panel connection *******************")
                                _sequencerState = SequencerType.InitialisePanel
                                self.sendPanelUpdate(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired
                                continue 
                            elif self.PanelMode in [AlPanelMode.STANDARD_PLUS]:
                                log.debug("[_sequencer]               **************** Too many Timeouts in 24 hours, but we're in Std+ so just Trigger Restore Status *******************")
                                self.sendPanelUpdate(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired
                                # Reset Send state (clear queue and reset flags)
                                self._emptySendQueue(pri_level = 1)
                                dotrigger = True 
                            else:
                                log.debug("[_sequencer]               **************** Too many Timeouts in 24 hours, giving up and going to Standard Mode *******************")
                                self._gotoStandardModeStopDownload()
                                self.sendPanelUpdate(AlCondition.WATCHDOG_TIMEOUT_GIVINGUP)   # watchdog timer expired, going to standard (plus) mode
                        else:
                            log.debug("[_sequencer]               **************** Trigger Restore Status *******************")
                            self.sendPanelUpdate(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired, going to try again
                            # Reset Send state (clear queue and reset flags)
                            self._emptySendQueue(pri_level = 1)
                            dotrigger = True 

                        # Overwrite the oldest entry and set it to 1 day in seconds. Keep the stats going in all modes for the statistics
                        #    Note that the asyncio 1 second sleep does not create an accurate time and this may be slightly more (but probably not less) than 24 hours.
                        watchdog_list[watchdog_pos] = 60 * 60 * 24  # seconds in 1 day
                        log.debug(f"[_sequencer]               Watchdog counter array, current={watchdog_pos}")
                        log.debug(f"[_sequencer]                       {watchdog_list}")

                    if dotrigger:
                        self._triggerRestoreStatus() # Clear message buffers and send a Restore (if in Powerlink) or Status (not in Powerlink) to the Panel

                    #if self.ImageManager.isImageDataInProgress():
                    #    # Manage the download of the F4 messages for Camera PIRs
                    #    # As this does not use acknowledges or checksums then prevent the expected response timer from kicking in
                    #    self.ImageManager.terminateIfExceededTimeout(40)

                    # log.debug(f"[_sequencer] is {self.watchdog_counter}")

                    # We create a B0 message to request other B0 messages from a PowerMaster panel.
                    #    Wait 1 second per B0 request between sending again to give the panel a chance to send them
                    if self.isPowerMaster() and self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]: # not AlPanelMode.MINIMAL_ONLY
                        tnow = self._getTimeFunction()
                        diff = (tnow - _last_B0_wanted_request_time).total_seconds()
                        if diff >= 5: # every 5 seconds (or more) do an update
                            if len(self.B0_Waiting) > 0:  # have we received the data that we last asked for
                                log.debug(f"[_sequencer] ****************************** Waiting For B0_Waiting **************************** {toStringList(self.B0_Waiting)}")
                                self.B0_Wanted.update(self.B0_Waiting) # ask again for them
                            if len(self.B0_Wanted) > 0:
                                log.debug(f"[_sequencer] ****************************** Asking For B0_Wanted **************************** {toStringList(self.B0_Wanted)}     timediff={diff}")
                                tmp = [pmSendMsgB0[i].data if i in pmSendMsgB0 else i for i in self.B0_Wanted]  # self.B0_Wanted can contain State enumerations or the integer of the message subtype
                                self.B0_Wanted = set()
                                s = self._create_B0_Data_Request(taglist = set(tmp))
                                self._addMessageToSendList(s)
                                self.B0_Waiting.update(tmp)
                                _last_B0_wanted_request_time = tnow

                    # Dump all sensors to the file every 60 seconds (1 minute)
                    log_sensor_state_counter = log_sensor_state_counter + 1
                    if log_sensor_state_counter >= 60:
                        log_sensor_state_counter = 0
                        self._dumpSensorsToLogFile()

            except Exception as ex:
                log.error("[_sequencer] Visonic Executor loop has caused an exception")
                log.error(f"             {ex}")
                # we will build a tree hierarchy   
                #ipt.getclasstree(ipt.getmro(ex))  
                #tree_class(ex)
                reset_vars()

    # Process any received bytes (in data as a bytearray)
    def data_received(self, data):
        """Add incoming data to ReceiveData."""
        if self.suspendAllOperations:
            return
        if not self.firstCmdSent:
            log.debug(f"[data receiver] Ignoring garbage data: {toString(data)}")
            return
        #log.debug(f"[data receiver] received data: {toString(data)}")
        self.lastRecvTimeOfPanelData = self._getUTCTimeFunction()
        try:
            for databyte in data:
                # process a single byte at a time
                self._handle_received_byte(databyte)
        except Exception as ex:
            #log.warning(f"[Data Received] Exception {ex}")
            log.exception(ex)

    def resetMessageData(self):
        # clear our buffer again so we can receive a new packet.
        self.ReceiveData = bytearray(b"")  # messages should never be longer than PACKET_MAX_SIZE
        # Reset control variables ready for next time
        self.pmCurrentPDU = pmReceiveMsg[0]
        self.pmIncomingPduLen = 0
        self.pmFlexibleLength = 0

    # Process one received byte at a time to build up the received PDU (Protocol Description Unit)
    #       self.pmIncomingPduLen is only used in this function
    #       self.pmCrcErrorCount is only used in this function
    #       self.pmCurrentPDU is only used in this function
    def _handle_received_byte(self, data):
        """Process a single byte as incoming data."""

        def processCRCFailure():
            msgType = self.ReceiveData[1]
            if msgType != Receive.UNKNOWN_F1:  # ignore CRC errors on F1 message
                self.pmCrcErrorCount = self.pmCrcErrorCount + 1
                if self.pmCrcErrorCount >= MAX_CRC_ERROR:
                    self.pmCrcErrorCount = 0
                    interval = self._getUTCTimeFunction() - self.pmFirstCRCErrorTime
                    if interval <= timedelta(seconds=CRC_ERROR_PERIOD):
                        self._reportProblem(AlTerminationType.CRC_ERROR)
                    self.pmFirstCRCErrorTime = self._getUTCTimeFunction()

        def processReceivedMessage(ackneeded, debugp, data, msg):
            # Unknown Message has been received
            msgType = data[1]
            # log.debug(f"[data receiver] *** Received validated message {hexify(msgType)}   data {toString(data)}")
            # Send an ACK if needed
            if ackneeded:
                # log.debug(f"[data receiver] Sending an ack as needed by last panel status message {hexify(msgType)}")
                sendAck(data=data)

            # Check response
            #tmplength = len(self.pmExpectedResponse)
            if len(self.pmExpectedResponse) > 0:  # and msgType != 2:   # 2 is a simple acknowledge from the panel so ignore those
                # We've sent something and are waiting for a reponse - this is it
                if msgType in self.pmExpectedResponse:
                    # while msgType in self.pmExpectedResponse:
                    self.pmExpectedResponse.remove(msgType)

            if data is not None and debugp == DebugLevel.FULL:
                log.debug(f"[processReceivedMessage] Received {msg}   raw data {toString(data)}          response list {[hex(no).upper() for no in self.pmExpectedResponse]}")
            elif data is not None and debugp == DebugLevel.CMD:
                log.debug(f"[processReceivedMessage] Received {msg}   raw data {toString(data[1:4])}          response list {[hex(no).upper() for no in self.pmExpectedResponse]}")

            # Handle the message
            if self.packet_callback is not None:
                self.packet_callback(data)

        # Send an achnowledge back to the panel
        def sendAck(data=bytearray(b"")):
            """ Send ACK if packet is valid """

            iscommand = data is not None and len(data) > 2 and data[1] >= 0x40   # command message types
            panel_state_enrolled = not self.pmDownloadMode and self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]

            # There are 2 types of acknowledge that we can send to the panel
            #    Normal    : For a normal message
            #    Powerlink : For when we are in powerlink mode
            #if not isbase and panel_state_enrolled and ispm:
            if iscommand and panel_state_enrolled:             # When in Std+, PL Mode and message type is at or above 0x40
                message = pmSendMsg[Send.ACK_PLINK]
            else:
                message = pmSendMsg[Send.ACK]   # MSG_ACK
            assert message is not None
            e = VisonicListEntry(command=message)
            self._addMessageToSendList(message = e, priority = MessagePriority.ACK)


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
                #log.debug(f"[data receiver] Variable length Message Being Received  Message Type {hex(self.ReceiveData[1]).upper()}     pmIncomingPduLen {self.pmIncomingPduLen}   data var {int(data)}")

        # If we were expecting a message of a particular length (i.e. self.pmIncomingPduLen > 0) and what we have is already greater then that length then dump the message and resynchronise.
        if 0 < self.pmIncomingPduLen <= pdu_len:                             # waiting for pmIncomingPduLen bytes but got more and haven't been able to validate a PDU
            log.info(f"[data receiver] PDU Too Large: Dumping current buffer {toString(self.ReceiveData)}    The next byte is {hex(data).upper()}")
            pdu_len = 0                                                      # Reset the incoming data to 0 length
            self.resetMessageData()

        # If this is the start of a new message, 
        #      then check to ensure it is a PACKET_HEADER (message preamble)
        if pdu_len == 0:
            self.resetMessageData()
            if data == Packet.HEADER:  # preamble
                self.ReceiveData.append(data)
                #log.debug(f"[data receiver] Starting PDU {toString(self.ReceiveData)}")
            # else we're trying to resync and walking through the bytes waiting for a Packet.HEADER preamble byte

        elif pdu_len == 1:
            #log.debug(f"[data receiver] Received message Type {data}")
            if data != Receive.DUMMY_MESSAGE and data in pmReceiveMsg:       # Is it a message type that we know about
                self.pmCurrentPDU = pmReceiveMsg[data]                       # set to current message type parameter settings for length, does it need an ack etc
                self.ReceiveData.append(data)                                # Add on the message type to the buffer
                if not isinstance(self.pmCurrentPDU, dict):
                    self.pmIncomingPduLen = self.pmCurrentPDU.length         # for variable length messages this is the fixed length and will work with this algorithm until updated.
                #log.debug(f"[data receiver] Building PDU: It's a message {hex(data).upper()}; pmIncomingPduLen = {self.pmIncomingPduLen}   variable = {self.pmCurrentPDU.isvariablelength}")
            elif data == Receive.DUMMY_MESSAGE or data == 0xFD:              # Special case for pocket and PowerMaster 10
                log.info(f"[data receiver] Received message type {hexify(data)} so not processing it")
                self.resetMessageData()
            else:
                # build an unknown PDU. As the length is not known, leave self.pmIncomingPduLen set to 0 so we just look for Packet.FOOTER as the end of the PDU
                self.pmCurrentPDU = pmReceiveMsg[0]                          # Set to unknown message structure to get settings, varlenbytepos is -1
                self.pmIncomingPduLen = 0                                    # self.pmIncomingPduLen should already be set to 0 but just to make sure !!!
                log.warning(f"[data receiver] Warning : Construction of incoming packet unknown - Message Type {hex(data).upper()}")
                self.ReceiveData.append(data)                                # Add on the message type to the buffer

        elif pdu_len == 2 and isinstance(self.pmCurrentPDU, dict):
            #log.debug(f"[data receiver] Building PDU: It's a variable message {hex(self.ReceiveData[0]).upper()} {hex(data).upper()}")
            if data in self.pmCurrentPDU:
                self.pmCurrentPDU = self.pmCurrentPDU[data]
                #log.debug("[data receiver] Building PDU:   doing it properly")
            else:
                self.pmCurrentPDU = self.pmCurrentPDU[0]                     # All should have a 0 entry so use as default when unknown
                log.debug(f"[data receiver] Building PDU: It's a variable message {hex(self.ReceiveData[0]).upper()} {hex(data).upper()} BUT it is unknown")
            self.pmIncomingPduLen = self.pmCurrentPDU.length                 # for variable length messages this is the fixed length and will work with this algorithm until updated.
            self.ReceiveData.append(data)                                    # Add on the message type to the buffer

        elif self.pmFlexibleLength > 0 and data == Packet.FOOTER and pdu_len + 1 < self.pmIncomingPduLen and (self.pmIncomingPduLen - pdu_len) < self.pmFlexibleLength:
            # Only do this when:
            #       Looking for "flexible" messages
            #              At the time of writing this, only the 0x3F EPROM Download PDU does this with some PowerMaster panels
            #       Have got the Packet.FOOTER message terminator
            #       We have not yet received all bytes we expect to get
            #       We are within 5 bytes of the expected message length, self.pmIncomingPduLen - pdu_len is the old length as we already have another byte in data
            #              At the time of writing this, the 0x3F was always only up to 3 bytes short of the expected length and it would pass the CRC checks
            # Do not do this when (pdu_len + 1 == self.pmIncomingPduLen) i.e. the correct length
            # There is possibly a fault with some panels as they sometimes do not send the full EPROM data.
            #    - Rather than making it panel specific I decided to make this a generic capability
            self.ReceiveData.append(data)  # add byte to the message buffer
            if self.pmCurrentPDU.ignorechecksum or self._validatePDU(self.ReceiveData):  # if the message passes CRC checks then process it
                # We've got a validated message
                log.debug(f"[data receiver] Validated PDU: Got Validated PDU type {hexify(int(self.ReceiveData[1]))}   data {toString(self.ReceiveData)}")
                processReceivedMessage(ackneeded=self.pmCurrentPDU.ackneeded, debugp=self.pmCurrentPDU.debugprint, msg=self.pmCurrentPDU.msg, data=self.ReceiveData)
                self.resetMessageData()

        elif (self.pmIncomingPduLen == 0 and data == Packet.FOOTER) or (pdu_len + 1 == self.pmIncomingPduLen): # postamble (the +1 is to include the current data byte)
            # (waiting for Packet.FOOTER and got it) OR (actual length == calculated expected length)
            self.ReceiveData.append(data)  # add byte to the message buffer
            #log.debug(f"[data receiver] Building PDU: Checking it {toString(self.ReceiveData)}")
            msgType = self.ReceiveData[1]
            if self.pmCurrentPDU.ignorechecksum or self._validatePDU(self.ReceiveData):
                # We've got a validated message
                #log.debug(f"[data receiver] Building PDU: Got Validated PDU type {hexify(int(msgType))}   data {toString(self.ReceiveData)}")
                if self.pmCurrentPDU.varlenbytepos < 0:  # is it an unknown message i.e. varlenbytepos is -1
                    log.warning(f"[data receiver] Received Valid but Unknown PDU {hex(msgType)}")
                    sendAck()  # assume we need to send an ack for an unknown message
                else:  # Process the received known message
                    processReceivedMessage(ackneeded=self.pmCurrentPDU.ackneeded, debugp=self.pmCurrentPDU.debugprint, msg=self.pmCurrentPDU.msg, data=self.ReceiveData)
                self.resetMessageData()
            else:
                # CRC check failed
                a = self._calculateCRC(self.ReceiveData[1:-2])[0]  # this is just used to output to the log file
                if len(self.ReceiveData) > PACKET_MAX_SIZE:
                    # If the length exceeds the max PDU size from the panel then stop and resync
                    log.warning(f"[data receiver] PDU with CRC error Message = {toString(self.ReceiveData)}   checksum calcs {hex(a).upper()}")
                    processCRCFailure()
                    self.resetMessageData()
                elif self.pmIncomingPduLen == 0:
                    if msgType in pmReceiveMsg:
                        # A known message with zero length and an incorrect checksum. Reset the message data and resync
                        log.warning(f"[data receiver] Warning : Construction of zero length incoming packet validation failed - Message = {toString(self.ReceiveData)}  checksum calcs {hex(a).upper()}")

                        # Send an ack even though the its an invalid packet to prevent the panel getting confused
                        if self.pmCurrentPDU.ackneeded:
                            # log.debug(f"[data receiver] Sending an ack as needed by last panel status message {hexify(msgType)}")
                            sendAck(data=self.ReceiveData)

                        # Dump the message and carry on
                        processCRCFailure()
                        self.resetMessageData()
                    else:  # if msgType != Receive.UNKNOWN_F1:        # ignore CRC errors on F1 message
                        # When self.pmIncomingPduLen == 0 then the message is unknown, the length is not known and we're waiting for a Packet.FOOTER where the checksum is correct, so carry on
                        log.debug(f"[data receiver] Building PDU: Length is {len(self.ReceiveData)} bytes (apparently PDU not complete)  {toString(self.ReceiveData)}  checksum calcs {hex(a).upper()}")
                else:
                    # When here then the message is a known message type of the correct length but has failed it's validation
                    log.warning(f"[data receiver] Warning : Construction of incoming packet validation failed - Message = {toString(self.ReceiveData)}   checksum calcs {hex(a).upper()}")

                    # Send an ack even though the its an invalid packet to prevent the panel getting confused
                    if self.pmCurrentPDU.ackneeded:
                        # log.debug(f"[data receiver] Sending an ack as needed by last panel status message {hexify(msgType)}")
                        sendAck(data=self.ReceiveData)

                    # Dump the message and carry on
                    processCRCFailure()
                    self.resetMessageData()

        elif pdu_len <= PACKET_MAX_SIZE:
            # log.debug(f"[data receiver] Current PDU {toString(self.ReceiveData)}   adding {hexify(data)}")
            self.ReceiveData.append(data)
        else:
            log.debug(f"[data receiver] Dumping Current PDU {toString(self.ReceiveData)}")
            self.resetMessageData()
        # log.debug(f"[data receiver] Building PDU {toString(self.ReceiveData)}")

    def _addMessageToSendList(self, message : Send | bytearray | VisonicListEntry, priority : MessagePriority = MessagePriority.NORMAL, options : list = [], response : list = None):
        if message is not None:
            if isinstance(message, Send):
                m = pmSendMsg[message]
                assert m is not None
                e = VisonicListEntry(command = m, options = options)
            elif isinstance(message, bytearray):
                e = VisonicListEntry(raw = message, response = response, options = options)
            elif isinstance(message, VisonicListEntry):
                e = message
            else:
                log.error(f"[_addMessageToSendList] Message not added as not a string and not a bytearray, it is of type {type(message)}")
                return
            # The SendQueue is set up as a PriorityQueue and needs a < function implementing in VisonicListEntry based on time, oldest < newest
            # By doing this it's like having three queues in one, an immediate queue, a high priority queue, and a low priority queue, each one date ordered oldest first
            # 0 < 1 so 0 is the high priority queue
            # So when get is called it looks at the high priority queue first and if nothing then looks at the low priority queue
            # So urgent tagged messages get sent to the panel asap, like arm, disarm etc
            self.SendQueue.put_nowait(item=(int(priority), e))


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
        self.enrolled_old = 0  # means nothing enrolled

        self.pmForceArmSetInPanel = False          # If the Panel is using "Force Arm" then sensors may be automatically armed and bypassed by the panel when it is armed and disarmed

        self.PostponeEventCounter = 0

        self.lastPacketCounter = 0

        self.B0_PANEL_LOG_Counter = 0

        self.B0_temp = {}

        self.beezero_024B_sensorcount = None

        self.builderMessage = {}  # Temporary variable
        self.builderData = {}     # Temporary variable

    def mySensorChangeHandler(self, sensor : SensorDevice, s : AlSensorCondition):
        log.debug("=============================================================== Sensor Change ===========================================================================")
        log.debug(f"     {self.PanelMode.name:<18}   {str(s):<11}   Sensor {sensor}")
        #log.debug("=========================================================================================================================================================")
        #self._dumpSensorsToLogFile()

    def mySwitchChangeHandler(self, switch : X10Device):
        log.debug("=============================================================== Switch Change ===========================================================================")
        log.debug(f"     {self.PanelMode.name:<18}   X10    {switch}")
        #log.debug("=========================================================================================================================================================")
        #self._dumpSensorsToLogFile(True)

    def _updateSensor(self, sensor) -> bool:
        
        def getPanelStringName(zonename):
            if len(self.PanelSettings[PanelSetting.ZoneNameString]) == 21 and len(self.PanelSettings[PanelSetting.ZoneCustNameStr]) == 10 and 0 <= zonename <= 30:
                return self.getPanelSetting(PanelSetting.ZoneNameString, zonename) if zonename <= 20 else self.getPanelSetting(PanelSetting.ZoneCustNameStr, zonename - 21)
            log.debug(f"[_updateSensor]    getPanelStringName  NOT OK, got missing data       {zonename=}    {len(self.PanelSettings[PanelSetting.ZoneNameString])=}    {len(self.PanelSettings[PanelSetting.ZoneCustNameStr])=}")
            log.debug(f"[_updateSensor]               {self.PanelSettings[PanelSetting.ZoneNameString]}")
            log.debug(f"[_updateSensor]               {self.PanelSettings[PanelSetting.ZoneCustNameStr]}")
            return None

        (mandatory, optional) = self.checkPanelDataPresent()
        #m = mandatory | optional
        if not self.ForceStandardMode and len(mandatory) > 0:
            log.debug(f"[_updateSensor]       Not Forcing Standard and not got all mandatory panel settings so not updating sensor {mandatory=}")
            return False

        enrolled        = self.getPanelSetting(PanelSetting.ZoneEnrolled,     sensor)
        zoneType        = self.getPanelSetting(PanelSetting.ZoneTypes,        sensor)
        zoneChime       = self.getPanelSetting(PanelSetting.ZoneChime,        sensor)
        device_type     = self.getPanelSetting(PanelSetting.DeviceTypesZones, sensor)
        motiondelaytime = self.getPanelSetting(PanelSetting.ZoneDelay,        sensor)
        zn              = self.getPanelSetting(PanelSetting.ZoneNames,        sensor)
        partitionData   = self.getPanelSetting(PanelSetting.PartitionData,    sensor)
        zonePanelName   = None if zn is None else getPanelStringName(zn & 0x1F)

        #log.debug(f"[_updateSensor]     partitiondata set as {self.PanelSettings[PanelSetting.PartitionData] if PanelSetting.PartitionData in self.PanelSettings else "Undefined"}")

        if enrolled is None or not enrolled:
            if sensor in self.SensorList:
                log.info(f"[_updateSensor]       Removing sensor Z{(sensor+1):0>2} as it is not enrolled in Panel EPROM Data")
                if self.onNewSensorHandler is not None:
                    self.onNewSensorHandler(False, self.SensorList[sensor])
                del self.SensorList[sensor]
                return True
            return False

        log.debug(f"[_updateSensor]  Zone Z{(sensor+1):>02} : {enrolled=} {zoneType=} {zoneChime=} {device_type=} {motiondelaytime=} {zn=} {partitionData=}")

        part = set()
        if self.getPartitionsInUse() is not None:
            #partitionCnt = pmPanelConfig[CFG.PARTITIONS][self.PanelType]
            partitionCnt = self.getPanelCapability(IndexName.PARTITIONS)
            if partitionData is not None and partitionCnt > 1:
                for j in range(0, partitionCnt):  # max partitions of all panels
                    if (partitionData & (1 << j)) != 0:
                        log.debug(f"[_updateSensor]     Adding to partition list - ref {sensor}  Z{(sensor+1):0>2}     Partition {(j+1)}")
                        part.add(j + 1)                  # partitions for this sensor
                        self.PartitionsInUse.add(j + 1)  # overall used partitions, this is a set so no repetitions allowed
            else:
                part.add(1)

        updated = False
        created_new_sensor = False

        if sensor not in self.SensorList:
            self.SensorList[sensor] = SensorDevice( id = sensor + 1 )
            created_new_sensor = True

        zoneName = "not_installed"
        if sensor < len(self.PanelSettings[PanelSetting.ZoneNames]):     # 
            zoneName = pmZoneName[zn & 0x1F]

        if zn is not None and self.SensorList[sensor].zname != zoneName:
            updated = True
            self.SensorList[sensor].zname = zoneName

        if zonePanelName is not None and self.SensorList[sensor].zpanelname != zonePanelName:
            updated = True
            self.SensorList[sensor].zpanelname = zonePanelName

        if device_type is not None:
            sensorType = AlSensorType.UNKNOWN
            sensorModel = "Unknown"

            if self.isPowerMaster(): # PowerMaster models
                if device_type in pmZoneMaster:
                    sensorType = pmZoneMaster[device_type].func
                    sensorModel = pmZoneMaster[device_type].name
                    if motiondelaytime is not None and motiondelaytime == 0xFFFF and (sensorType == AlSensorType.MOTION or sensorType == AlSensorType.CAMERA):
                        log.debug(f"[_updateSensor] PowerMaster Zone Z{sensor+1:0>2} has no motion delay set (Sensor will only be useful when the panel is armed)")
                else:
                    log.debug(f"[_updateSensor] Found unknown sensor type {hexify(device_type)}")
            else:  #  PowerMax models
                tmpid = device_type & 0x0F
                #sensorType = "UNKNOWN " + str(tmpid)

                # User cybfox77 found that PIR sensors were returning the sensor type 'device_type' as 0xe5 and 0xd5, these would be decoded as Magnet sensors
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

                if device_type in pmZoneMax:
                    sensorType = pmZoneMax[device_type].func
                    sensorModel = pmZoneMax[device_type].name
                elif tmpid in pmZoneMaxGeneric:
                    # if tmpid in pmZoneMaxGeneric:
                    sensorType = pmZoneMaxGeneric[tmpid]
                else:
                    log.debug(f"[_updateSensor] Found unknown sensor type {device_type}")

            if self.SensorList[sensor].sid != device_type:
                updated = True
                self.SensorList[sensor].sid = device_type
                self.SensorList[sensor].stype = sensorType
                self.SensorList[sensor].model = sensorModel

        if zoneChime is not None and 0 <= zoneChime <= 2:
            #log.debug(f"[_updateSensor]   Setting Zone Chime {zoneChime}  {pmZoneChimeKey[zoneChime]}")
            self.PanelSettings[PanelSetting.ZoneChime][sensor] = zoneChime
            if self.SensorList[sensor].zchime != pmZoneChimeKey[zoneChime]:
                updated = True
                self.SensorList[sensor].zchimeref = zoneChime
                if zoneChime < len(pmZoneChimeKey):
                    self.SensorList[sensor].zchime = pmZoneChimeKey[zoneChime]
                else:
                    self.SensorList[sensor].zchime = "undefined " + str(zoneChime)

        if zoneType is not None:
            self.PanelSettings[PanelSetting.ZoneTypes][sensor] = zoneType
        elif sensor < len(self.PanelSettings[PanelSetting.ZoneTypes]):     # 
            zoneType = self.PanelSettings[PanelSetting.ZoneTypes][sensor]
        else:
            zoneType = None

        if zoneType is not None and self.SensorList[sensor].ztype != zoneType:
            updated = True
            self.SensorList[sensor].ztype = zoneType
            if zoneType < len(pmZoneTypeKey):
                self.SensorList[sensor].ztypeName = pmZoneTypeKey[zoneType]
            else:
                self.SensorList[sensor].ztypeName = "undefined " + str(zoneType)   # undefined

        if motiondelaytime is not None and motiondelaytime != 0xFFFF:
            if self.SensorList[sensor].motiondelaytime != motiondelaytime:
                updated = True
                self.SensorList[sensor].motiondelaytime = motiondelaytime

        if self.getPartitionsInUse() is not None and self.SensorList[sensor].partition != part:
            updated = True
            log.debug(f"[_updateSensor]     Change to partition list - sensor {sensor}   {part=}")
            # If we get EPROM data, assume it is all correct and override any existing settings (as some were assumptions)
            self.SensorList[sensor].partition = part.copy()

        # if the new value is True and the old Value is False then push change enrolled
        enrolled_push_change = (enrolled and not self.SensorList[sensor].enrolled) if self.SensorList[sensor].enrolled is not None and enrolled is not None else False
        if enrolled is not None:
            self.SensorList[sensor].enrolled = enrolled

        if created_new_sensor:
            self.SensorList[sensor].onChange(self.mySensorChangeHandler)
            if self.onNewSensorHandler is not None:
                self.onNewSensorHandler(True, self.SensorList[sensor])

        # Enrolled is only sent on enrol and not on change to not enrolled
        if enrolled_push_change:
            self.SensorList[sensor].pushChange(AlSensorCondition.ENROLLED)
        elif updated:
            self.SensorList[sensor].pushChange(AlSensorCondition.STATE)
        #else:
        #    self.SensorList[sensor].pushChange(AlSensorCondition.RESET)

        # Has something changed?
        return enrolled_push_change or updated

    def _processKeypadsAndSirensFromEPROM(self) -> str:

        def logSetting(msg, setting):
            log.debug(f"[_processKeypadsAndSirensFromEPROM] EPROM Decode for {msg}")
            for i in range(len(setting)):
                o = ""
                for j in range(len(setting[i])):
                    v = setting[i][j]
                    o = o + " " + hexify(v)
                log.debug(f"[_processKeypadsAndSirensFromEPROM]        {msg} {i}   in hex: {o}")

        #sirenCnt = pmPanelConfig[CFG.SIRENS][self.PanelType]
        sirenCnt = self.getPanelCapability(IndexName.SIRENS)
        keypad1wCnt = pmPanelConfig[CFG.ONE_WKEYPADS][self.PanelType]
        #keypad2wCnt = pmPanelConfig[CFG.TWO_WKEYPADS][self.PanelType]
        keypad2wCnt = self.getPanelCapability(IndexName.KEYPADS)

        setting = self.epromManager.lookupEprom("KeyFobsPMax")
        logSetting("keyfob", setting)

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process Devices (Sirens and Keypads)
        # ------------------------------------------------------------------------------------------------------------------------------------------------
        deviceStr = ""
        if self.isPowerMaster(): # PowerMaster models
            # Process keypad settings
            setting = self.epromManager.lookupEprom(EPROM.KEYPAD_MAS)
            logSetting("keypad2", setting)
            for i in range(0, min(len(setting), keypad2wCnt)):
                if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0 or setting[i][3] != 0 or setting[i][4] != 0:
                    log.debug(f"[_processKeypadsAndSirensFromEPROM] Found an Enrolled PowerMaster keypad {i}")
                    deviceStr = f"{deviceStr},K2{i:0>2}"

            # Process siren settings
            setting = self.epromManager.lookupEprom(EPROM.SIRENS_MAS)
            self.PanelSettings[PanelSetting.SirenEnrolled] = [setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0 or setting[i][3] != 0 or setting[i][4] != 0 for i in range(0, min(len(setting), sirenCnt))]
            logSetting("siren", setting)
            for i in range(0, min(len(setting), sirenCnt)):
                #self.PanelSettings[PanelSetting.SirenEnrolled].append(v)
                if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0 or setting[i][3] != 0 or setting[i][4] != 0:
                    log.debug(f"[_processKeypadsAndSirensFromEPROM] Found an Enrolled PowerMaster siren {i}")
                    deviceStr = f"{deviceStr},S{i:0>2}"
        else:
            # Process keypad settings
            setting = self.epromManager.lookupEprom(EPROM.KEYPAD_1_MAX)
            logSetting("keypad1", setting)
            for i in range(0, min(len(setting), keypad1wCnt)):
                if setting[i][0] != 0 or setting[i][1] != 0:
                    log.debug(f"[_processKeypadsAndSirensFromEPROM] Found an Enrolled PowerMax 1-way keypad {i}")
                    deviceStr = f"{deviceStr},K1{i:0>2}"

            setting = self.epromManager.lookupEprom(EPROM.KEYPAD_2_MAX)
            logSetting("keypad2", setting)
            for i in range(0, min(len(setting), keypad2wCnt)):
                if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0:
                    log.debug(f"[_processKeypadsAndSirensFromEPROM] Found an Enrolled PowerMax 2-way keypad {i}")
                    deviceStr = f"{deviceStr},K2{i:0>2}"

            # Process siren settings
            setting = self.epromManager.lookupEprom(EPROM.SIRENS_MAX)
            logSetting("siren", setting)
            self.PanelSettings[PanelSetting.SirenEnrolled] = [setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0 for i in range(0, min(len(setting), sirenCnt))]
            for i in range(0, min(len(setting), sirenCnt)):
                if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0:
                    log.debug(f"[_processKeypadsAndSirensFromEPROM] Found a PowerMax siren {i}")
                    deviceStr = f"{deviceStr},S{i:0>2}"

        return deviceStr[1:]

    def _setDataFromPanelType(self, p) -> bool:
        if p in pmPanelType:
            self.PanelType = p

            if self.DownloadCodeUserSet:
                log.debug(f"[_setDataFromPanelType] Using the user defined Download Code {self.DownloadCode if not OBFUS else "OBFUSCATED"}")
            elif self.DownloadCode == DEFAULT_DL_CODE:
                # If the panel still has its startup default Download Code, or if it hasn't been set by the user to something different
                self.DownloadCode = pmPanelConfig[CFG.DLCODE_1][self.PanelType]
                self.PanelSettings[PanelSetting.PanelDownload] = self.DownloadCode
                log.debug(f"[_setDataFromPanelType] Setting Download Code from the Default value {DEFAULT_DL_CODE} to the default Panel Value {self.DownloadCode}")
            else:
                log.debug(f"[_setDataFromPanelType] Using Download Code {self.DownloadCode}")

            if 0 <= self.PanelType <= len(pmPanelConfig[CFG.SUPPORTED]) - 1:
                isSupported = pmPanelConfig[CFG.SUPPORTED][self.PanelType]
                if isSupported:
                    self.PanelModel = pmPanelType[self.PanelType] if self.PanelType in pmPanelType else "UNKNOWN"   # INTERFACE : PanelType set to model
                    self.PowerMaster = pmPanelConfig[CFG.POWERMASTER][self.PanelType]
                    self.AutoEnrol = pmPanelConfig[CFG.AUTO_ENROL][self.PanelType]
                    self.AutoSyncTime = pmPanelConfig[CFG.AUTO_SYNCTIME][self.PanelType]
                    self.KeepAlivePeriod = pmPanelConfig[CFG.KEEPALIVE][self.PanelType]
                    self.pmInitSupportedByPanel = pmPanelConfig[CFG.INIT_SUPPORT][self.PanelType]
                    self.pmDownloadByEPROM = FORCE_DOWNLOAD_TO_USE_EPROM or self.pmForceDownloadByEPROM or pmPanelConfig[CFG.EPROM_DOWNLOAD][self.PanelType]
                    
                    self.PanelCapabilities[IndexName.REPEATERS] = pmPanelConfig[CFG.REPEATERS][self.PanelType]
                    self.PanelCapabilities[IndexName.PANIC_BUTTONS] = 1
                    self.PanelCapabilities[IndexName.SIRENS] = pmPanelConfig[CFG.SIRENS][self.PanelType]
                    self.PanelCapabilities[IndexName.ZONES] = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
                    self.PanelCapabilities[IndexName.KEYPADS] = pmPanelConfig[CFG.TWO_WKEYPADS][self.PanelType]
                    self.PanelCapabilities[IndexName.KEYFOBS] = pmPanelConfig[CFG.KEYFOBS][self.PanelType]
                    self.PanelCapabilities[IndexName.USERS] = pmPanelConfig[CFG.USERCODES][self.PanelType]
                    self.PanelCapabilities[IndexName.X10_DEVICES] = pmPanelConfig[CFG.X10][self.PanelType]
                    self.PanelCapabilities[IndexName.GSM_MODULES] = 1
                    self.PanelCapabilities[IndexName.POWERLINK] = 1
                    self.PanelCapabilities[IndexName.PROXTAGS] = pmPanelConfig[CFG.PROXTAGS][self.PanelType]
                    self.PanelCapabilities[IndexName.PGM] = pmPanelConfig[CFG.PGM][self.PanelType]
                    self.PanelCapabilities[IndexName.PANEL] = 1
                    self.PanelCapabilities[IndexName.GUARDS] = 1
                    self.PanelCapabilities[IndexName.PARTITIONS] = pmPanelConfig[CFG.PARTITIONS][self.PanelType]
                    self.PanelCapabilities[IndexName.UNK15] = 1
                    self.PanelCapabilities[IndexName.UNK16] = 0
                    self.PanelCapabilities[IndexName.EXPANDER_33] = 0
                    self.PanelCapabilities[IndexName.IOV] = 0
                    self.PanelCapabilities[IndexName.UNK19] = 0
                    self.PanelCapabilities[IndexName.UNK20] = 0
                    
                    return True
                # Panel 0 i.e original PowerMax
                log.error(f"Lookup of Visonic Panel type reveals that this seems to be a PowerMax Panel and supports EPROM Download only with no capability, this Panel cannot be used with this Integration")
                return False
        # Then it is an unknown panel type
        log.error(f"Lookup of Visonic Panel type {p} reveals that this is a new Panel Type that is unknown to this Software. Please contact the Author of this software")
        return False

    def checkPanelDataPresent(self, forceall = False) -> (set, set):
        #zoneCnt = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
        zoneCnt = self.getPanelCapability(IndexName.ZONES)
        if self.isPowerMaster():
            need_these = {PanelSetting.ZoneNames       : zoneCnt,
                          PanelSetting.ZoneNameString  : 21,            # All panels have 31 zone names (21 fixed and 10 user defined) e.g. Living Roon, Kitchen etc
                          PanelSetting.ZoneCustNameStr : 10,            # 10 user defined zones
                          PanelSetting.ZoneTypes       : pmPanelConfig[CFG.DEV_ZONE_TYPES][self.PanelType],
                          PanelSetting.DeviceTypesZones: pmPanelConfig[CFG.DEV_ZONE_TYPES][self.PanelType],
                          PanelSetting.ZoneEnrolled    : zoneCnt,
                          PanelSetting.PanelBypass     : 1,             # This is a string so ensure a min length of 1 character
                          PanelSetting.ZoneChime       : zoneCnt, 
                          PanelSetting.ZoneDelay       : zoneCnt,
                          PanelSetting.HasPGM          : 1,
                          #PanelSetting.PanelSerial     : 1,
                          #PanelSetting.PanelDownload   : 1,
                          PanelSetting.PartitionData   : self.getPanelCapability(IndexName.PARTITIONS),
                          #PanelSetting.PanelName       : 1,
                          PanelSetting.PartitionEnabled: 1,
                          }
        else:
            need_these = {PanelSetting.ZoneNames       : zoneCnt,
                          PanelSetting.ZoneTypes       : pmPanelConfig[CFG.DEV_ZONE_TYPES][self.PanelType],
                          PanelSetting.DeviceTypesZones: pmPanelConfig[CFG.DEV_ZONE_TYPES][self.PanelType],
                          PanelSetting.ZoneEnrolled    : zoneCnt,
                          PanelSetting.PanelBypass     : 1,             # This is a string so ensure a min length of 1 character
                          PanelSetting.ZoneChime       : zoneCnt
                          }

        if not self.ForceStandardMode:
            need_these[PanelSetting.UserCodes] = 2 * self.getPanelCapability(IndexName.USERS)

        if forceall:
            log.debug(f"[checkPanelDataPresent]  forceall is True")
        optional = set()
        mandatory = set()
        for s,v in need_these.items():
            m = pmPanelSettingCodes[s].mandatory
            if not OBFUS:
                if s in self.PanelSettings:
                    if forceall or v > len(self.PanelSettings[s]):
                        log.debug(f"[checkPanelDataPresent]     {s.name:<15}   want {v}  got {len(self.PanelSettings[s])}    {'mandatory' if m else 'optional'}")
                else:
                    log.debug(f"[checkPanelDataPresent]     {s.name:<15}   want {v}  s not in panelsettings    {'mandatory' if m else 'optional'}")
            if forceall or not (s in self.PanelSettings and len(self.PanelSettings[s]) >= v):
                if m:
                    mandatory.add(s)
                else:
                    optional.add(s)
        return (mandatory, optional)

    # _processEPROMSettings
    #    Decode the EPROM and the various settings to determine
    #       The general state of the panel
    #       The zones and the sensors
    #       The X10 devices
    #       The phone numbers
    #       The user pin codes

    def _processEPROMSettings(self) -> bool:
        """Process Settings from the downloaded EPROM data from the panel"""

        if self.pmDownloadComplete:
            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Panel type and serial number
            #     This checks whether the EPROM settings have been downloaded OK

            #pmDisplayName = self.epromManager.lookupEpromSingle(EPROM.DISPLAY_NAME)    

            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Need the panel type to be valid so we can decode some of the remaining downloaded data correctly
            # when we get here then self.PanelType is set and it's a known panel type i.e. if self.PanelType is not None and self.PanelType in pmPanelType is TRUE
            # ------------------------------------------------------------------------------------------------------------------------------------------------

            # self._dumpEPROMSettings()

            #log.debug(f"[Process Settings] Panel Type Number {str(self.PanelType)}   serial string {toString(panelSerialType)}")
            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Process Panel Status to display in the user interface
            self.PanelStatus.update(self.epromManager.processEPROMData())

            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Process Panel Settings to use as a common panel settings regardless of how they were obtained.  This way gets them from EPROM.
            if self.isPowerMaster(): # PowerMaster models
                for key in pmPanelSettingCodes:
                    if pmPanelSettingCodes[key].PMasterEPROM in EPROM:
                        if pmPanelSettingCodes[key].item is not None:
                            self.PanelSettings[key] = self.epromManager.lookupEprom(pmPanelSettingCodes[key].PMasterEPROM)[pmPanelSettingCodes[key].item]
                        else:
                            self.PanelSettings[key] = self.epromManager.lookupEprom(pmPanelSettingCodes[key].PMasterEPROM)
            else:
                for key in pmPanelSettingCodes:
                    if pmPanelSettingCodes[key].PMaxEPROM in EPROM:
                        if pmPanelSettingCodes[key].item is not None:
                            self.PanelSettings[key] = self.epromManager.lookupEprom(pmPanelSettingCodes[key].PMaxEPROM)[pmPanelSettingCodes[key].item] # [pmPanelSettingCodes[key].item]
                        else:
                            self.PanelSettings[key] = self.epromManager.lookupEprom(pmPanelSettingCodes[key].PMaxEPROM)

            log.debug(f"[Process Settings]     UpdatePanelSettings")

            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Process panel type and serial
            #pmPanelTypeCodeStr = self.PanelSettings[PanelSetting.PanelModel]      # self.epromManager.lookupEpromSingle(EPROM.PANEL_MODEL_CODE)
            #idx = f"{hex(self.PanelType).upper()[2:]:0>2}{hex(int(pmPanelTypeCodeStr)).upper()[2:]:0>2}"
            #pmPanelName = pmPanelName_t[idx] if idx in pmPanelName_t else "Unknown_" + idx

            #log.debug(f"[Process Settings]   Processing settings - panel code index {idx}")

            #  INTERFACE : Add this param to the status panel first
            #self.PanelStatus[PANEL_STATUS.PANEL_NAME] = pmPanelName

            #log.warning(f"[Process Settings]    Installer Code {toString(self.epromManager.lookupEpromSingle(EPROM.INSTALLERCODE))}")
            #log.warning(f"[Process Settings]    Master DL Code {toString(self.epromManager.lookupEpromSingle(EPROM.MASTERDLCODE))}")
            #if self.isPowerMaster():
            #    log.debug(f"[Process Settings]    Master Code {toString(self.epromManager.lookupEpromSingle(EPROM.MASTERCODE))}")
            #    log.debug(f"[Process Settings]    Installer DL Code {toString(self.epromManager.lookupEpromSingle(EPROM.INSTALDLCODE))}")

            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Process zone settings

            #zonesignalstrength = self.PanelSettings[PanelSetting.ZoneSignal]

            # For zone_data these 2 get the same data block but they are structured differently
            # PowerMax
            #    It is 30 zones, each is 4 bytes
            #        2 = Sensor Type
            #        3 = Zone Type
            #      e.g. cd ce e4 0c
            # PowerMaster
            #    It is 64 zones, each is 1 byte, represents Zone Type
            zone_data = self.PanelSettings[PanelSetting.ZoneData]

            # This is 640 bytes, PowerMaster only.
            # It is 64 zones, each is 10 bytes
            #    5 = Sensor Type
            pmaster_zone_ext_data = self.PanelSettings[PanelSetting.ZoneExt] # self.epromManager.lookupEprom(EPROM.ZONEEXT_MAS)

            #for index , value in enumerate(pmaster_zone_ext_data):
            #    log.debug(f"[Process Settings]   Raw pmaster_zone_ext_data {index:<3} = {toString(value)}")

            #zoneCnt = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
            zoneCnt = self.getPanelCapability(IndexName.ZONES)
            
            log.debug(f"[Process Settings]     Zones Data Buffer      zoneCnt {zoneCnt}    len settings {len(zone_data)}     len ZoneNames {len(self.PanelSettings[PanelSetting.ZoneNames])}")
            log.debug(f"[Process Settings]         Zones Names Buffer :  {toString(self.PanelSettings[PanelSetting.ZoneNames])}")
            #log.debug(f"[Process Settings]     Zones Data Buffer  :  {zone_data}")

            if len(zone_data) > 0:
                self.PanelSettings[PanelSetting.ZoneTypes] = bytearray(zoneCnt)
                self.PanelSettings[PanelSetting.DeviceTypesZones] = bytearray(zoneCnt)
                self.PanelSettings[PanelSetting.ZoneChime] = bytearray(zoneCnt)
                self.PanelSettings[PanelSetting.ZoneEnrolled] = [False] * zoneCnt  # bytearray(zoneCnt)
                motiondel = [0 for i in range(zoneCnt)]

                for i in range(zoneCnt):
                    self.PanelSettings[PanelSetting.ZoneTypes][i] = (int(zone_data[i]) if self.isPowerMaster() else int(zone_data[i][3])) & 0x0F
                    self.PanelSettings[PanelSetting.ZoneChime][i] = ((int(zone_data[i]) if self.isPowerMaster() else int(zone_data[i][3])) >> 4 ) &0x03
                    self.PanelSettings[PanelSetting.DeviceTypesZones][i] = int(pmaster_zone_ext_data[i][5]) if self.isPowerMaster() else int(zone_data[i][2])
                    motiondel[i] = self.PanelSettings[PanelSetting.ZoneDelay][i][0] + (256 * self.PanelSettings[PanelSetting.ZoneDelay][i][1]) if self.isPowerMaster() else 0
                    if self.isPowerMaster():  # PowerMaster models
                        self.PanelSettings[PanelSetting.ZoneEnrolled][i] = pmaster_zone_ext_data[i][4:9] != bytearray.fromhex("00 00 00 00 00") and pmaster_zone_ext_data[i][4:6] != bytearray.fromhex("FF FF")
                    else:
                        self.PanelSettings[PanelSetting.ZoneEnrolled][i] = zone_data[i][0:3] != bytearray.fromhex("00 00 00")
                self.PanelSettings[PanelSetting.ZoneDelay] = motiondel

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Store partition info & check if partitions are on
                self.partitionsEnabled = False

                partitionCnt = self.getPanelCapability(IndexName.PARTITIONS)
                if partitionCnt > 1:  # Could the panel have more than 1 partition?
                    partition = self.PanelSettings[PanelSetting.PartitionData]
                    partEnabled = False
                    if partition is not None and len(partition) >= zoneCnt: 
                        partEnabled = self.PanelSettings[PanelSetting.PartitionEnabled] != 255 and self.PanelSettings[PanelSetting.PartitionEnabled] != 0  # i think that == 1 enables partitions
                        if partEnabled:
                            result = all(x == partition[0] for x in partition[:zoneCnt])
                            if result:
                                log.debug(f"[Process Settings]     The partition data all represent the same partition value, so partitions disabled")
                            partEnabled = not result
                        else:
                            log.debug(f"[Process Settings]     self.PanelSettings[PanelSetting.PartitionEnabled] indicates partitions disabled")
                    # If that panel type can have more than 1 partition, then check to see if the panel has defined more than 1
                    log.debug(f"[Process Settings]     partitionCnt = {partitionCnt}    partEnabled = {partEnabled}    Partition Data {toString(partition[:zoneCnt]) if partition is not None else "Invalid"}")
                    self.partitionsEnabled = partEnabled
                else:
                    log.debug(f"[Process Settings]     partitionCnt = {partitionCnt}    coded settings define a single partition")

            return True
        else:
            log.warning("[Process Settings]     WARNING: Cannot process panel EPROM settings, download has not completed")
            return False

    def _updateAllSirens(self) -> bool:
        count = self.getPanelCapability(IndexName.SIRENS)
        se = self.PanelSettings.get(PanelSetting.SirenEnrolled, [])
        dt = self.PanelSettings.get(PanelSetting.DeviceTypesSirens, bytearray())
        log.debug(f"[Process Settings]     Updating sirens {se=}  {toString(dt)=}")
        for i in range(min(count, len(se), len(dt))):
            if se[i]:
                log.debug(f"[Process Settings]       Siren {i} enrolled, device type {dt[i]}    {pmSirenMaster[dt[i]].name if dt[i] in pmSirenMaster else "Unknown Device"}")
            else:
                log.debug(f"[Process Settings]       Siren {i} not enrolled")
        return False

    def _updateAllSensors(self) -> bool:

        if self.PanelType is None:
            return False

        (mandatory, optional) = self.checkPanelDataPresent()

        retval = False
        # Do not create or update sensors until all mandatory data has been obtained
        if self.ForceStandardMode or len(mandatory) == 0:
            # Only when we have all EPROM or B0 Zone Data, or we're in Standard Emulation Mode
            
            # this gets updated in self._updateSensor
            #self.PartitionsInUse = set()

            # List of door/window sensors
            doorZoneStr = ""
            # List of motion sensors
            motionZoneStr = ""
            # List of smoke sensors
            smokeZoneStr = ""
            # List of other sensors
            otherZoneStr = ""

            log.debug("[Process Settings]   Processing Zone devices")

            #zoneCnt = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
            zoneCnt = self.getPanelCapability(IndexName.ZONES)
            for i in range(zoneCnt):

                tmp = self._updateSensor( sensor = i )
                retval = retval or tmp

                if i in self.SensorList:
                    sensorType = self.SensorList[i].stype
                    if sensorType == AlSensorType.MAGNET or sensorType == AlSensorType.WIRED:
                        doorZoneStr = f"{doorZoneStr},Z{i+1:0>2}"
                    elif sensorType == AlSensorType.MOTION or sensorType == AlSensorType.CAMERA:
                        motionZoneStr = f"{motionZoneStr},Z{i+1:0>2}"
                    elif sensorType == AlSensorType.SMOKE or sensorType == AlSensorType.GAS:
                        smokeZoneStr = f"{smokeZoneStr},Z{i+1:0>2}"
                    else:
                        otherZoneStr = f"{otherZoneStr},Z{i+1:0>2}"

            #log.debug(f"[Process Settings]          self.PartitionsInUse = {self.PartitionsInUse}")
            if (piu := self.getPartitionsInUse()) is not None:
                log.debug(f"[Process Settings]                I see that you have {piu} partition(s) set in the panel")
            else:
                log.debug(f"[Process Settings]                I see that you have no partitions")

            self.PanelStatus[PANEL_STATUS.DOOR_ZONES] = doorZoneStr[1:]
            self.PanelStatus[PANEL_STATUS.MOTION_ZONES] = motionZoneStr[1:]
            self.PanelStatus[PANEL_STATUS.SMOKE_ZONES] = smokeZoneStr[1:]
            self.PanelStatus[PANEL_STATUS.OTHER_ZONES] = otherZoneStr[1:]

        else:
            log.debug(f"[_updateAllSensors]   checkPanelDataPresent missing mandatory items {mandatory=}")

        return retval # return True if any of the sensor data has been changed because of this function

    def _createPGMSwitch(self):
        # Process PGM settings
        #has_pgm = pmPanelConfig[CFG.PGM][self.PanelType]
        if self.getPanelCapability(IndexName.PGM) > 0:
            x10Location = "PGM"
            x10Type = "onoff"             # Assume PGM is onoff switch, all other devices are dimmer Switches
            if 0 in self.SwitchList:
                self.SwitchList[0].type = x10Type
                self.SwitchList[0].location = x10Location
                self.SwitchList[0].state = False
            else:
                self.SwitchList[0] = X10Device(type=x10Type, location=x10Location, id=0, enabled=True)
                self.SwitchList[0].onChange(self.mySwitchChangeHandler)
                log.debug(f"[Process Settings]             Creating PGM Switch")
                if self.onNewSwitchHandler is not None:
                    self.onNewSwitchHandler(True, self.SwitchList[0])  

    def _processX10Settings(self):
        # Process X10 settings

        x10_device_max = self.getPanelCapability(IndexName.X10_DEVICES)

        if x10_device_max > 0:

            log.debug(f"[Process Settings]     Processing X10 devices     Panel Type supports up to {x10_device_max} X10 devices plus a PGM")
        
            data = [EPROM.X10_BYARMAWAY, EPROM.X10_BYARMHOME, EPROM.X10_BYDISARM, EPROM.X10_BYDELAY, EPROM.X10_BYMEMORY, EPROM.X10_BYKEYFOB, EPROM.X10_ACTZONEA, EPROM.X10_ACTZONEB, EPROM.X10_ACTZONEC ]
            s = []
            # Each of these has to be 16 bytes long, this is defined in the EPROM data array
            for i in range(len(data)):
                e = self.epromManager.lookupEprom( data[i], x10_device_max + 1 )
                log.debug(f"[Process Settings]             Processing X10 devices e={e}")
                if len(e) == x10_device_max + 1:      # X10 devices + PGM
                    s.append(e)

            log.debug(f"[Process Settings]             Processing X10 devices s={s}")

            x10Names = self.epromManager.lookupEprom(EPROM.X10_ZONENAMES, x10_device_max + 1)  # 0 = PGM, 1 = X01
            
            if len(data) != len(s) or len(x10Names) != x10_device_max + 1:
                log.debug(f"[Process Settings]              There has been a problem loading EPROM X10 data {len(data)} != {len(s)}  or  {len(x10Names)} != {x10_device_max + 1}")
            else:
                log.debug(f"[Process Settings]            X10 device EPROM Name Data {toString(x10Names)}")

                # Start at 1 to exclude the PGM, we always create the PGM
                for i in range(1, len(s[0])):
                    x10Enabled = False
                    for j in range(len(s)):
                        x10Enabled = x10Enabled or s[j][i] != DISABLE_TEXT    # look for this X10 device in all 9 EPROM settings to see if it's used in any way

                    x10Name = x10Names[i] & 0x1F   # PGM needs to be set by x10Enabled.  x10Names[0] i.e. PGM this is 0xFF

                    if x10Enabled or (self.PanelType >= 3 and x10Name != 0x1F) or (self.PanelType < 3 and x10Name != 0x00):   # For some reason the PowerMax+ sets the x10Names array to 0 by default
                        x10Location = pmZoneName[x10Name]
                        x10Type = "dimmer"            # Assume PGM is onoff switch, all other devices are dimmer Switches
                        if i in self.SwitchList:
                            self.SwitchList[i].type = x10Type
                            self.SwitchList[i].location = x10Location
                            self.SwitchList[i].state = False
                        else:
                            self.SwitchList[i] = X10Device(type=x10Type, location=x10Location, id=i, enabled=True)
                            self.SwitchList[i].onChange(self.mySwitchChangeHandler)
                            if self.onNewSwitchHandler is not None:
                                self.onNewSwitchHandler(True, self.SwitchList[i])                                    

                log.debug(f"[Process Settings]     Processed X10 devices, you have {len(self.SwitchList)} X10 devices")
        else:
            log.debug(f"[Process Settings]     Panel Type does not support X10 devices")


    def _makeInt(self, data) -> int:
        val = data[0]
        for i in range(1, len(data)):
            val = val + ( pow(256, i) * data[i] )
        return val

    def ProcessZoneEvent(self, eventZone, eventType):
        log.debug(f"[ProcessZoneEvent]      Zone Event      Zone: {eventZone}    Type: {eventType}")
        key = eventZone - 1  # get the key from the zone - 1

        if self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.MINIMAL_ONLY, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK] and key not in self.SensorList and eventType > 0:
            log.debug("[ProcessZoneEvent]          Got a Zone Sensor that I did not know about so creating it")
            self._updateSensor(sensor = key)

        if key in self.SensorList and eventType in pmZoneEventAction:
            sf = getattr(self.SensorList[key], pmZoneEventAction[eventType].func if eventType in pmZoneEventAction else "")
            if sf is not None:
                log.debug(f"[ProcessZoneEvent]               Processing event {eventType}  calling {pmZoneEventAction[eventType].func}({str(pmZoneEventAction[eventType].parameter)})")
                sf(pmZoneEventAction[eventType].parameter)
            self.SensorList[key].setProblem(pmZoneEventAction[eventType].problem)
        else:
            log.debug(f"[ProcessZoneEvent]               Not processing zone {eventZone}   event {eventType}")

    def ProcessX10StateUpdate(self, x10status, total = 16):
        # Examine X10 status
        for i in range(total):
            status = x10status & (1 << i)
            if i in self.SwitchList:
                # INTERFACE : use this to set X10 status
                oldstate = self.SwitchList[i].state
                self.SwitchList[i].state = bool(status)
                # Check to see if the state has changed
                if (oldstate and not self.SwitchList[i].state) or (not oldstate and self.SwitchList[i].state):
                    log.debug(f"[ProcessX10StateUpdate]      X10 device {i} changed to {self.SwitchList[i].state} ({status})")
                    self.SwitchList[i].pushChange()

    def do_sensor_update(self, data : bytearray, func : str, msg : str, startzone : int = 0, endzone : int = 32):
        endzone_min = min(endzone, self.getPanelCapability(IndexName.ZONES))
        no_of_bytes = (1 + ((endzone_min - startzone - 1) // 8)) if endzone_min > startzone else None
        if no_of_bytes is not None and len(data) >= no_of_bytes:
            val = self._makeInt(data)
            log.debug(f"{msg} : {val:032b}       startzone={startzone}    {f'corrected endzone={endzone_min-1}' if endzone_min != endzone else f'endzone={endzone_min-1}'}      {no_of_bytes=}")
            for i in range(startzone, endzone_min):
                if i in self.SensorList:
                    sf = getattr(self.SensorList[i], func)
                    if sf is not None:
                        sf(bool(val & (1 << (i-startzone)) != 0))
        else:
            log.debug(f"{msg} : len(data)={len(data)}  data={toString(data)} not processed    {startzone=}    {endzone=}   {endzone_min=}   {no_of_bytes=}")

    # This function handles a received message packet and processes it
    def _processReceivedPacket(self, packet):
        """Handle one raw incoming packet."""

        def statelist():
            A = self.PartitionState[0].statelist()
            B = self.PartitionState[1].statelist()
            C = self.PartitionState[2].statelist()
            return [self.PanelMode, A, B, C]

        if self.suspendAllOperations:
            # log.debug('[Disconnection] Suspended. Sorry but all operations have been suspended, please recreate connection')
            return

        # Check the current packet against the last packet to determine if they are the same
        if self.lastPacket is not None:
            if self.lastPacket == packet and packet[1] == Receive.STATUS_UPDATE:  # only consider A5 Receive.STATUS_UPDATE packets for consecutive error
                self.lastPacketCounter = self.lastPacketCounter + 1
            else:
                self.lastPacketCounter = 0
        self.lastPacket = packet

        if self.lastPacketCounter == SAME_PACKET_ERROR:
            log.debug(f"[_processReceivedPacket] Had the same packet for {SAME_PACKET_ERROR} times in a row : {toString(packet)}")
            self._reportProblem(AlTerminationType.SAME_PACKET_ERROR)
            return
        #else:
        #    log.debug(f"[_processReceivedPacket] Parsing complete valid packet: {toString(packet)}")

        # Record all main variables to see if the message content changes any
        oldState = statelist() # make it a function so if it's changed it remains consistent
        oldPowerMaster = self.PowerMaster

        if self.PanelMode == AlPanelMode.PROBLEM and not self.PowerLinkBridgeConnected:
            # A PROBLEM indicates that there has been a response timeout (either normal or trying to get to powerlink)
            # However, we have clearly received a packet so put the panel mode back to MINIMAL_ONLY, Standard or StandardPlus and wait for a powerlink response from the panel
            if self.DisableAllCommands:
                log.debug("[Standard Mode] Entering MINIMAL_ONLY Mode")
                self.PanelMode = AlPanelMode.MINIMAL_ONLY
            elif self.pmDownloadComplete and not self.ForceStandardMode and self.gotValidUserCode():
                log.debug("[_processReceivedPacket] Had a response timeout PROBLEM but received a data packet so entering Standard Plus Mode")
                self.PanelMode = AlPanelMode.STANDARD_PLUS
            else:
                log.debug("[_processReceivedPacket] Had a response timeout PROBLEM but received a data packet and entering Standard Mode")
                self.PanelMode = AlPanelMode.STANDARD

        #processAB         = not self.pmDownloadMode and self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK]
        processAB         = not self.pmDownloadMode and not self.ForceStandardMode and self.PanelMode not in [AlPanelMode.POWERLINK_BRIDGED]
        processNormalData = not self.pmDownloadMode and self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.MINIMAL_ONLY, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]
        processB0         = self.EnableB0ReceiveProcessing or processNormalData

        pushchange = self._handle_msgtype(packet, processAB, processNormalData, processB0, self.pmDownloadMode)

        if self.sendPanelEventData(): # sent at least 1 event so no need to send PUSH_CHANGE
            pushchange = False

        if self.PostponeEventCounter == 0 and oldState != statelist():   # make statelist a function so if it's changed it remains consistent
            self.sendPanelUpdate(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
        elif oldPowerMaster != self.PowerMaster or pushchange:
            self.sendPanelUpdate(AlCondition.PUSH_CHANGE)

    def _handle_msgtype(self, packet, processAB, processNormalData, processB0, processDownload) -> bool:

        pushchange = False
        log.debug(f"[_processReceivedPacket] {processAB=} {processB0=} {processNormalData=} {processDownload=}")

        # Leave this here as it needs to be created dynamically to create the condition and message columns
        DecodeMessage = collections.namedtuple('DecodeMessage', 'condition, func, pushchange, message')
        _decodeMessageFunction = {
            Receive.ACKNOWLEDGE       : DecodeMessage(                True , self.handle_msgtype02, False, None ),  # ACK
            Receive.TIMEOUT           : DecodeMessage(                True , self.handle_msgtype06, False, None ),  # Timeout
            Receive.UNKNOWN_07        : DecodeMessage(                True , self.handle_msgtype07, False, None ),  # No idea what this means
            Receive.ACCESS_DENIED     : DecodeMessage(                True , self.handle_msgtype08, False, None ),  # Access Denied
            Receive.LOOPBACK_TEST     : DecodeMessage(                True , self.handle_msgtype0B, False, None ),  # # LOOPBACK TEST, STOP (0x0B) IS THE FIRST COMMAND SENT TO THE PANEL WHEN THIS INTEGRATION STARTS
            Receive.EXIT_DOWNLOAD     : DecodeMessage(                True , self.handle_msgtype0F, False, None ),  # Exit
            Receive.NOT_USED          : DecodeMessage(               False , None                 , False, "WARNING: Message 0x22 is not decoded, are you using an old Powermax Panel as this is not supported?" ),
            Receive.DOWNLOAD_RETRY    : DecodeMessage(                True , self.handle_msgtype25, False, None ),  # Download retry
            Receive.DOWNLOAD_SETTINGS : DecodeMessage(     processDownload , self.handle_msgtype33, False, f"Received 33 Message, we are in {self.PanelMode.name} mode (so I'm ignoring the message), data: {toString(packet)}"),  # Settings send after a MSGV_START
            Receive.PANEL_INFO        : DecodeMessage(                True , self.handle_msgtype3C, False, None ),  # Message when start the download
            Receive.DOWNLOAD_BLOCK    : DecodeMessage(     processDownload , self.handle_msgtype3F, False, f"Received 3F Message, we are in {self.PanelMode.name} mode (so I'm ignoring the message), data: {toString(packet)}"),  # Download information
            Receive.EVENT_LOG         : DecodeMessage(   processNormalData , self.handle_msgtypeA0, False, None ),  # Event log
            Receive.ZONE_NAMES        : DecodeMessage(   processNormalData , self.handle_msgtypeA3,  True, None ),  # Zone Names
            Receive.STATUS_UPDATE     : DecodeMessage(   processNormalData , self.handle_msgtypeA5,  True, None ),  # Zone Information/Update
            Receive.ZONE_TYPES        : DecodeMessage(   processNormalData , self.handle_msgtypeA6,  True, None ),  # Zone Types
            Receive.PANEL_STATUS      : DecodeMessage(   processNormalData , self.handle_msgtypeA7,  True, None ),  # Panel Information/Update
            Receive.POWERLINK         : DecodeMessage(           processAB , self.handle_msgtypeAB,  True, f"Received AB Message, we are in {self.PanelMode.name} mode and Download is set to {processDownload} (so I'm ignoring the message), data: {toString(packet)}"),  # 
            Receive.X10_NAMES         : DecodeMessage(   processNormalData , self.handle_msgtypeAC,  True, None ),  # X10 Names
            Receive.IMAGE_MGMT        : DecodeMessage(   processNormalData , self.handle_msgtypeAD,  True, None ),  # No idea what this means, it might ...  send it just before transferring F4 video data ?????
            Receive.POWERMASTER       : DecodeMessage(           processB0 , self.handle_msgtypeB0,  True, None ),  # 
            Receive.IMAGE_DATA        : DecodeMessage(   processNormalData , self.handle_msgtypeF4,  None, None ),  # F4 Message from a Powermaster, can't decode it yet but this will accept it and ignore it
            Receive.REDIRECT          : DecodeMessage(                True , self.handle_msgtypeC0, False, None ),
            Receive.PROXY             : DecodeMessage(                True , self.handle_msgtypeE0, False, None )
        }

        if len(packet) < 4:  # there must at least be a header, command, checksum and footer
            log.warning(f"[_processReceivedPacket] Received invalid packet structure, not processing it {toString(packet)}")
        elif packet[1] in _decodeMessageFunction:
            dm = _decodeMessageFunction[packet[1]]
            if dm.condition:
                pushchange = dm.func(packet[2:-2])    # Use the return value if the function returns
                if pushchange is None:
                    pushchange = dm.pushchange        # If the function does not return a value then use the dm value
            elif dm.message is not None:
                log.debug(f"[_processReceivedPacket]     {dm.message}")
            else:
                log.debug(f"[_processReceivedPacket]     Received data not processed, data bytes are {toString(packet)}")
        elif processNormalData or processAB:
            log.debug(f"[_processReceivedPacket] Unknown/Unhandled packet type {toString(packet)}")
        return pushchange

    def handle_msgtype_testing(self, packet) -> bool:
        return self._handle_msgtype(packet, True, True, True, True)   # process any of the messages for testing

    def handle_msgtype02(self, data):  # ACK
        """ Handle Acknowledges from the panel """
        # Normal acknowledges have msgtype 0x02 but no data, when in powerlink the panel also sends data byte 0x43
        #    I have not found this on the internet, this is my hypothesis
        #log.debug(f"[handle_msgtype02] Ack Received  data = {toString(data)}")

        processAB = not self.pmDownloadMode and self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK]
        if processAB and len(data) > 0 and data[0] == Packet.POWERLINK_TERMINAL:
            self.receivedPowerlinkAcknowledge = True
            if self.allowAckToTriggerRestore:
                log.debug("[handle_msgtype02]        Received a powerlink acknowledge, I am in STANDARD_PLUS mode and sending MSG_RESTORE")
                self._addMessageToSendList(Send.RESTORE)
                self.allowAckToTriggerRestore = False

    def handle_msgtype06(self, data):
        """ MsgType=06 - Time out
            Timeout message from the PM, most likely we are/were in download mode"""
        log.debug("[handle_msgtype06] Timeout Received")
        self.TimeoutReceived = True

    def handle_msgtype07(self, data):
        """MsgType=07 - No idea what this means"""
        log.debug(f"[handle_msgtype07] No idea what this message means, data = {toString(data)}")
        # Assume that we need to send an ack

    def handle_msgtype08(self, data):
        log.debug(f"[handle_msgtype08] Access Denied  len {len(data)} data {toString(data)}")
        self.AccessDeniedReceived = True
        self.AccessDeniedMessage = self.pmLastSentMessage

    def handle_msgtype0B(self, data):  # LOOPBACK TEST SUCCESS, STOP COMMAND (0x0B) IS THE FIRST COMMAND SENT TO THE PANEL WHEN THIS INTEGRATION STARTS
        """ Handle LOOPBACK """
        #log.debug(f"[handle_msgtype0B] Loopback test assumed {toString(data)}")
        self.loopbackTest = True
        self.loopbackCounter = self.loopbackCounter + 1
        log.warning(f"[handle_msgtype0B] LOOPBACK TEST SUCCESS, Counter is {self.loopbackCounter}")

    def handle_msgtype0F(self, data):  # EXIT
        """ Handle EXIT from the panel """
        log.debug(f"[handle_msgtype0F] Exit    data is {toString(data)}")
        # This is sent by the panel during download to tell us to stop the download
        self.ExitReceived = True

    def handle_msgtype25(self, data):  # Download retry
        """ MsgType=25 - Download retry. Unit is not ready to enter download mode """
        # Format: <MsgType> <?> <?> <delay in sec>
        iDelay = data[2]
        log.debug(f"[handle_msgtype25] Download Retry, have to wait {iDelay} seconds     data is {toString(data)}")
        self.DownloadRetryReceived = True

    def handle_msgtype33(self, data):
        """MsgType=33 - Settings
        Message sent after a MSG_START. We will store the information in an internal array/collection"""

        if len(data) != 10:
            log.debug(f"[handle_msgtype33] ERROR: MSGTYPE=0x33 Expected len=14, Received={len(data)}")
            log.debug(f"[handle_msgtype33]                            {toString(data)}")
            return

        # Data Format is: <index> <page> <8 data bytes>
        # Extract Page and Index information
        iIndex = data[0]
        iPage = data[1]

        # log.debug(f"[handle_msgtype33] Getting Data {toString(data)}   page {hexify(iPage)}   index {hexify(iIndex)}")
        # Write to memory map structure, but remove the first 2 bytes from the data
        self.epromManager.saveEPROMSettings(iPage, iIndex, data[2:])

    def handle_msgtype3C(self, data):  # Panel Info Messsage when start the download
        """ The panel information is in 4 & 5
            5=PanelType e.g. PowerMax, PowerMaster
            4=Sub model type of the panel - just informational, not used
        """
        if not self.pmGotPanelDetails:
            self.ModelType = data[4]
            if not self._setDataFromPanelType(data[5]):
                log.debug(f"[handle_msgtype3C] Panel Type {data[5]} Unknown")

            log.debug(f"[handle_msgtype3C] PanelType={self.PanelType} : {self.PanelModel} , Model={self.ModelType}   Powermaster {self.PowerMaster}")

            self.pmGotPanelDetails = True
        else:
            log.debug("[handle_msgtype3C] Not Processed as already got Panel Details")

    def handle_msgtype3F(self, data):
        """MsgType=3F - Download information
           Multiple 3F can follow each other, maximum block size seems to be 0xB0 bytes"""

        if self.PanelMode != AlPanelMode.DOWNLOAD:
            log.debug("[handle_msgtype3F] Received data but in Standard Mode so ignoring data")
            return

        # data format is normally: <index> <page> <length> <data ...>
        # If the <index> <page> = FF, then it is an additional PowerMaster MemoryMap
        iIndex = data[0]
        iPage = data[1]
        iLength = data[2]

        # PowerMaster 10 (Model 7) and PowerMaster 33 (Model 10) has a very specific problem with downloading the Panel EPROM and doesn't respond with the correct number of bytes
        #if self.PanelType is not None and self.ModelType is not None and ((self.PanelType == 7 and self.ModelType == 68) or (self.PanelType == 10 and self.ModelType == 71)):
        #    if iLength != len(data) - 3:
        #        log.debug(f"[handle_msgtype3F] Not checking data length as it could be incorrect.  We requested {iLength} and received {len(data) - 3}")
        #        log.debug(f"[handle_msgtype3F]                            {toString(data)}")
        #    # Write to memory map structure, but remove the first 3 bytes (index/page/length) from the data
        #    self.epromManager.saveEPROMSettings(iPage, iIndex, data[3:])

        blocklen = self.epromManager.findLength(iPage, iIndex)

        if iLength == len(data) - 3 and blocklen is not None and blocklen == iLength:
            # Write to memory map structure, but remove the first 3 bytes (index/page/length) from the data
            self.epromManager.saveEPROMSettings(iPage, iIndex, data[3:])
            # Are we finished yet?
            if len(self.myDownloadList) > 0:
                self.pmDownloadInProgress = True
                self._addMessageToSendList(Send.DL, options=[ [1, self.myDownloadList.pop(0)] ])  # Read the next block of EPROM data
            else:
                self.myDownloadList = self.epromManager.populatEPROMDownload(self.isPowerMaster())
                if len(self.myDownloadList) == 0:
                    # This is the message to tell us that the panel has finished download mode, so we too should stop download mode
                    log.debug("[handle_msgtype3F] Download Complete")
                    self.pmDownloadInProgress = False
                    self.pmDownloadMode = False
                    self.pmDownloadComplete = True
                else:
                    log.debug("[handle_msgtype3F] Download seemed to be complete but not got all EPROM data yet")
                    self.pmDownloadInProgress = True
                    self._addMessageToSendList(Send.DL, options=[ [1, self.myDownloadList.pop(0)] ])  # Read the next block of EPROM data
        elif self.pmDownloadRetryCount <= DOWNLOAD_PDU_RETRY_COUNT:
            log.warning(f"[handle_msgtype3F] Invalid EPROM data block length (received: {len(data)-3}, Expected: {iLength},  blocklen: {blocklen}). Adding page {iPage} Index {iIndex} to the end of the list to redownload")
            log.warning(f"[handle_msgtype3F]                            {toString(data)}")
            # Add it back on to the end to re-download it
            self.myDownloadList.append(bytearray([iIndex, iPage, blocklen, 0]))
            # Increment counter
            self.pmDownloadRetryCount = self.pmDownloadRetryCount + 1
        else:
            log.warning(f"[handle_msgtype3F] Invalid EPROM data block length (received: {len(data)-3}, Expected: {iLength},  blocklen: {blocklen}). Giving up on page {iPage} Index {iIndex}")
            self.myDownloadList = []
            log.debug("[handle_msgtype3F] Download InComplete")
            self.pmDownloadInProgress = False
            self.pmDownloadMode = False
            self.pmDownloadComplete = False

    def handle_msgtypeA0(self, data):
        """ MsgType=A0 - Event Log """
        # From my Powermaster30  [handle_MsgTypeA0] Packet = 5f 02 01 64 58 5c 58 d3 41 51

        # My PowerMax
        #    To Ct Pt ---- time ---- Zo Ev    Time does not have the seconds value
        #    fb 01 00 00 00 00 00 00 03 00
        #    fb 02 01 1c 15 06 0a 18 1f 55    6/10/24 at 21:28:01    Disarmed   FOB-01    why are all the seconds 0 or 1
        #    fb 03 01 09 12 06 0a 18 1f 52    6/10/24 at 18:09:01    Armed Away FOB-01

        # From a PM10:
        #    To Ct Pt -- time ---  Y Zo Ev    Don't know what Y and data[7] is. It could be the panel state e.g. 0x52 is Armed Away
        #    fb 02 00 3f 71 02 67 04 01 5c    
        #    fb 03 00 69 3a 01 67 53 00 1c    
        #    fb 04 01 69 3a 01 67 52 61 1b    

        eventNum = data[1]
        # Check for the first entry, it only contains the number of events
        if eventNum == 0x01:
            log.debug("[handle_msgtypeA0]    Eventlog received")
            self.eventCount = data[0] - 1  ## the number of messages (including this one) minus 1
        elif self.onPanelLogHandler is not None:
            # There's no point in doing all of this if there's no handler to send it to!

            if self.isPowerMaster(): # PowerMaster models
                # extract the time as "epoch time" and convert to normal time
                hs = self._makeInt(data[3:7])
                pmtime = datetime.fromtimestamp(hs)
                #log.debug(f"[handle_msgtypeA0]   Powermaster time {hs} as hex {hex(hs)} from epoch is {pmtime}")
                iEventZone = data[8]
            else:
                # Assume that seconds is 0 for PowerMax panels
                #        datetime(year, month, day, hour, minute, second, microsecond)
                pmtime = datetime(int(data[7]) + 2000, data[6], data[5], data[4], data[3], 0, 0)
                iEventZone = int(data[8] & 0x7F) # PowerMax limits the event zones, 0 to 127

            # Send the event log in to HA
            #     Do not use timezone times as it was the log created on that day at that time
            l = AlLogPanelEvent(total = self.eventCount, current = eventNum - 1, partition = data[2], dateandtime = pmtime, zone = iEventZone, event = data[9])
            #log.debug(f"[handle_msgtypeA0]                       Log Entry {l}")
            self.onPanelLogHandler(l)

    def handle_msgtypeA3(self, data):
        """ MsgType=A3 - Zone Names """
        log.debug(f"[handle_MsgTypeA3] Packet = {toString(data)}")
        msgCnt = int(data[0])
        offset = 8 * (int(data[1]) - 1)
        log.debug(f"            Message Count is {msgCnt}   offset={offset}     self.PanelMode = {str(self.PanelMode)}")

        if len(self.PanelSettings[PanelSetting.ZoneNames]) < offset+8:
            self.PanelSettings[PanelSetting.ZoneNames].extend(bytearray(offset+8-len(self.PanelSettings[PanelSetting.ZoneNames])))
        for i in range(0, 8):
            # Save the Zone Name
            self.PanelSettings[PanelSetting.ZoneNames][offset+i] = data[2+i] & 0x1F
            if self.PanelMode != AlPanelMode.POWERLINK and self.PanelMode != AlPanelMode.POWERLINK_BRIDGED and (offset+i) in self.SensorList:
                self._updateSensor(sensor = offset+i)

    def handle_msgtypeA5(self, data):  # Status Message
        """ MsgType=A5 - Zone Data Update """

        # msgTot = data[0]
        eventType = data[1]

        #log.debug(f"[handle_msgtypeA5] Parsing A5 packet {toString(data)}")

        match eventType:
            case 1 if len(self.SensorList) > 0:
                log.debug("[handle_msgtypeA5] Zone Alarm Status: Ztrip and ZTamper")
                self.do_sensor_update(data[2:6],  ZoneFunctions.DO_ZTRIP,   "[handle_msgtypeA5]      Zone Trip Alarm 32-01")
                self.do_sensor_update(data[6:10], ZoneFunctions.DO_ZTAMPER, "[handle_msgtypeA5]      Zone Tamper Alarm 32-01")

            case 2 if len(self.SensorList) > 0:
                # if in standard mode then use this A5 status message to reset the watchdog timer
                if self.PanelMode != AlPanelMode.POWERLINK:
                    log.debug("[handle_msgtypeA5] Got A5 02 message, resetting watchdog")
                    self._reset_watchdog_timeout()

                log.debug("[handle_msgtypeA5] Zone Status: Status and Battery")
                self.do_sensor_update(data[2:6],  ZoneFunctions.DO_STATUS,  "[handle_msgtypeA5]      Open Door/Window Status Zones 32-01")
                self.do_sensor_update(data[6:10], ZoneFunctions.DO_BATTERY, "[handle_msgtypeA5]      Battery Low Zones 32-01")

            case 3 if len(self.SensorList) > 0:
                # This status is different from the status in the 0x02 part above i.e they are different values.
                #    This one is wrong (I had a door open and this status had 0, the one above had 1)
                #       According to domotica forum, this represents "active" but what does that actually mean?
                log.debug("[handle_msgtypeA5] Zone Status: Inactive and Tamper")
                val = self._makeInt(data[2:6])
                log.debug(f"[handle_msgtypeA5]      Trigger (Inactive) Status Zones 32-01: {val:032b} Not Used")
                self.do_sensor_update(data[6:10], ZoneFunctions.DO_TAMPER, "[handle_msgtypeA5]      Tamper Zones 32-01")

            case 4:
                # 00 04 01 15 00 00 02 02 00 00
                # Assume that every zone event causes the need to push a change to the sensors etc
                if self.PanelMode != AlPanelMode.POWERLINK:
                    #log.debug("[handle_msgtypeA5] Got A5 04 message, resetting watchdog")
                    self._reset_watchdog_timeout()

                sysStatus = data[2]  # Mark-Mills with a PowerMax Complete Part, sometimes this has 0x20 bit set and I'm not sure why
                sysFlags = data[3]
                eventZone = data[4]
                eventType = data[5]
                # dont know what 6 and 7 are
                dummy1 = data[6]
                dummy2 = data[7]
                log.debug(f"[handle_msgtypeA5]      sysStatus=0x{hexify(sysStatus)}    sysFlags=0x{hexify(sysFlags)}    eventZone=0x{hexify(eventZone)}    eventType=0x{hexify(eventType)}    unknowns are 0x{hexify(dummy1)} 0x{hexify(dummy2)}")

                #last10seconds = sysFlags & 0x10

                if self.getPartitionsInUse() is None:   
                    # Process sysStatus and sysFlags only if there are no partitions
                    #     The panel sends A5 messages for all partitions but we don't know the partition number. So how do we know what to decode?
                    oldPS = self.PartitionState[0].PanelState
                    s = self.PartitionState[0].ProcessPanelStateUpdate(sysStatus=sysStatus, sysFlags=sysFlags, PanelMode=self.PanelMode)   # does not set partition in return value
                    if s is not None:
                        self.addPanelEventData(s)
                    newPS = self.PartitionState[0].PanelState
                    if newPS == AlPanelStatus.DISARMED and newPS != oldPS:
                        # Panel state is Disarmed and it has just changed, get the bypass state of the sensors as the panel may have changed them
                        self._addMessageToSendList(Send.BYPASSTAT)

                if sysFlags & 0x20 != 0:  # Zone Event
                    if eventType > 0 and eventZone != 0xff: # I think that 0xFF refers to the panel itself as a zone. Currently not processed
                        self.ProcessZoneEvent(eventZone=eventZone, eventType=eventType)

                x10stat1 = data[8]
                x10stat2 = data[9]
                self.ProcessX10StateUpdate(x10status=x10stat1 + (x10stat2 * 0x100))

    #        elif eventType == 0x05:  # 
    #            # 0d a5 10 05 00 00 00 00 00 00 12 34 43 bc 0a
    #            # 0d a5 0d 05 00 00 00 07 00 00 12 34 43 b7 0a
    #            #     Might be a coincidence but the "1st Account No" is set to 001234
    #            pass

            case 6:
                log.debug("[handle_msgtypeA5] Zone Status: Enrolled and Bypass")
                val = self._makeInt(data[2:6])
                if val != self.enrolled_old:
                    log.debug(f"[handle_msgtypeA5]      Enrolled Zones 32-01: {val:032b}")
                    send_zone_type_request = False
                    self.enrolled_old = val

                    self.updatePanelSetting(key = PanelSetting.ZoneEnrolled, length = 4, datasize = RAW.BITS.value, data = data[2:6], display = True, msg = f"A5 Zone Enrolled Data")
                    self._updateAllSensors()

                self.do_sensor_update(data[6:10], ZoneFunctions.DO_BYPASS, "[handle_msgtypeA5]      Bypassed Zones 32-01")

            case _:
                # easiest way to check if its full of zeros
                vala = self._makeInt(data[2:6])
                valb = self._makeInt(data[6:10])
                if vala != 0 or valb != 0:
                    log.debug(f"[handle_msgtypeA5]      Unknown A5 Message: {toString(data)}")
                    # [handle_msgtypeA5]      Unknown A5 Message: 10 05 00 00 00 00 00 00 43 21 43        # 4321 is the 1st account number

        self.sendPanelUpdate(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend

    def handle_msgtypeA6(self, data):
        """ MsgType=A6 - Zone Types """
        log.debug(f"[handle_MsgTypeA6] Packet = {toString(data)}")
        msgCnt = int(data[0])
        offset = 8 * (int(data[1]) - 1)
        log.debug(f"            Message Count is {msgCnt}   offset={offset}     self.PanelMode={str(self.PanelMode)}")
        if len(self.PanelSettings[PanelSetting.ZoneTypes]) < offset+8:
            self.PanelSettings[PanelSetting.ZoneTypes].extend(bytearray(offset+8-len(self.PanelSettings[PanelSetting.ZoneTypes])))
        for i in range(0, 8):
            # Save the Zone Type
            self.PanelSettings[PanelSetting.ZoneTypes][offset+i] = ((int(data[2+i])) - 0x1E) & 0x0F
            log.debug(f"                        Zone type for sensor {offset+i+1} is {hexify((int(data[2+i])) - 0x1E)} : {pmZoneTypeKey[self.PanelSettings[PanelSetting.ZoneTypes][offset+i]]}")
            if self.PanelMode != AlPanelMode.POWERLINK and self.PanelMode != AlPanelMode.POWERLINK_BRIDGED and (offset+i) in self.SensorList:
                self._updateSensor(sensor = offset+i)

    def handle_msgtypeA7(self, data):
        # This is a complete cheat as this library should not access this, just for debug
        from . import pmLogEvent_t, pmLogPowerMaxUser_t, pmLogPowerMasterUser_t
        """ MsgType=A7 - Panel Status Change """
        #log.debug(f"[handle_msgtypeA7] Panel Status Change {toString(data)}")
        # 01 00 27 51 02 ff 00 02 00 00
        # ff 5d 00 2d 00 00 11 0c 00 00

        def displayit(m, eventZone, eventType):
            et : EVENT_TYPE = EVENT_TYPE(eventType) if eventType in EVENT_TYPE else EVENT_TYPE.NOT_DEFINED
            eventStr = "Unknown"
            if 0 <= eventType <= 151:
                if len(pmLogEvent_t[eventType]) > 0:
                    eventStr = pmLogEvent_t[eventType]
                #else:
                #    self.logstate_debug(f"[process_panel_event_log] Found unknown log event {entry.event}")
            if self.isPowerMaster():
                s = pmLogPowerMasterUser_t[eventZone] or "Unknown"
                log.debug(f"[handle_msgtypeA7]           {m}  {eventZone}/{eventType}   {s}  {et.name}     {eventStr=}")
            else:
                s = pmLogPowerMaxUser_t[int(eventZone & 0x7F)] or "Unknown"
                log.debug(f"[handle_msgtypeA7]           {m}  {eventZone}/{eventType}   {s}  {et.name}     {eventStr=}")
        
        def processSpecialEntries(eventZone, eventType) -> bool:
            retval = False
            SYSTEM = 0
            if eventZone == SYSTEM and eventType == EVENT_TYPE.SYSTEM_RESET:  # panel reset
                log.info(f"[handle_msgtypeA7]               A7 FF message : data={toString(data)}.  Panel has been reset.")
                self.PanelResetEvent = True
                retval = True
            elif eventZone == SYSTEM and eventType == EVENT_TYPE.PANEL_LOW_BATTERY:             # 0/45   System  PANEL_LOW_BATTERY
                log.debug(f"[handle_msgtypeA7]               A7 FF message : data={toString(data)}.  Panel Low Battery.")
                # if multiple partitions then only needed for partition 1
                self.PartitionState[0].PanelBattery = False
            elif eventZone == SYSTEM and eventType == EVENT_TYPE.PANEL_LOW_BATTERY_RESTORE:     # 0/46   System  PANEL_LOW_BATTERY_RESTORE
                log.debug(f"[handle_msgtypeA7]               A7 FF message : data={toString(data)}.  Panel Low Battery Restore.")
                # if multiple partitions then only needed for partition 1
                self.PartitionState[0].PanelBattery = True
            return retval

        msgCnt = int(data[0])

        # If message count is FF then it looks like the first message is valid so decode it (this is experimental)
        #if msgCnt == 0xFF:
        #    msgCnt = 1

        if msgCnt == 255 and self.getPartitionsInUse() is not None:
            # Looks like it's parsed differently
                # From my PM30, the 01 and 00 alternate several times back and forth, but never the same sequentially.  Could be partition as there are 2.
                #     data=ff 09 06 27 00 01 41 03 05 00 43
                #     data=ff 09 06 27 00 00 41 03 05 00 43

            log.debug(f"[handle_msgtypeA7]      A7 FF message (partitions) contains,   unknown byte is {hex(int(data[1]))}  : data={toString(data)}")
            # The first entry always looks valid, so for now, process it for reset and panel battery state            
            eventZone = int(data[2])   # 61h or 0 : For a PowerMaster panel 0x61 is decimal 97, this is "User 1" in pmLogPowerMasterUser_t, also 0 is System in pmLogPowerMasterUser_t
            eventType = int(data[3])   # Looks like an eventType but the timing of when it arrives is all wrong

            #if not processSpecialEntries(eventZone, eventType):
            #    log.debug(f"[handle_msgtypeA7]      A7 FF message (partitions) contains,   unknown byte is {hex(int(data[1]))}  : data={toString(data)}")

            for i in range(4):
                eventZone = int(data[2 + (2 * i)])
                eventType = int(data[3 + (2 * i)])
                displayit("Could be", eventZone, eventType)

        elif msgCnt == 255:
            log.debug(f"[handle_msgtypeA7]      A7 FF message (no partitions) contains,   unknown byte is {hex(int(data[1]))}  : data={toString(data)}")

            # The first entry always looks valid, so for now, process it for reset and panel battery state            
            eventZone = int(data[2])     # 61h or 0 : For a PowerMaster panel 0x61 is decimal 97, this is "User 1" in pmLogPowerMasterUser_t, also 0 is System in pmLogPowerMasterUser_t
            eventType = int(data[3])     # Looks like an eventType but the timing of when it arrives is all wrong
            #if not processSpecialEntries(eventZone, eventType):
            #    log.debug(f"[handle_msgtypeA7]      A7 FF message (partitions) contains,   unknown byte is {hex(int(data[1]))}  : data={toString(data)}")

            for i in range(4):
                eventZone = int(data[2 + (2 * i)])
                eventType = int(data[3 + (2 * i)])
                displayit("Could be", eventZone, eventType)

            # These are from a PowerMaster panel
            # data= ff 00 61 51 01 ff 00 0b 00 00 43
            # data= ff 00 61 55 01 ff 00 0b 00 00 43
            # data= ff 00 61 51 01 ff 00 0b 00 00 43
            # data= ff 00 61 55 01 ff 00 00 00 00 43
            # data= ff 00 61 51 01 ff 00 0b 00 00 43
            # data= ff 00 61 55 01 ff 00 00 00 00 43
            # Different powermaster panel
            # data= ff 44 00 60 00 ff 00 0c 00 00 43
            # data[3] could be an event type
            #        ARMED_HOME = 0x51
            #        ARMED_AWAY = 0x52
            #        QUICK_ARMED_HOME = 0x53
            #        QUICK_ARMED_AWAY = 0x54
            #        DISARM = 0x55
            #        SYSTEM_RESET = 0x60
            # data[2] could be from pmLogPowerMasterUser_t
            #   For a PowerMaster panel 0x61 is decimal 97, this is "User 1" in pmLogPowerMasterUser_t

        elif msgCnt > 4:
            log.warning(f"[handle_msgtypeA7]      A7 message contains too many messages to process : {msgCnt}   data={toString(data)}")

        elif self.getPartitionsInUse() is None:   # message count 0 to 4 and we have no partitions so process message data
            # 0d a7 01 00 1f 52 01 ff 00 01 00 00 43 a0 0a
            #             03 00 01 03 08 0e 01 13
            #             03 00 2f 55 2f 1b 00 1c
            log.debug(f"[handle_msgtypeA7]      A7 message (no partitions) contains {msgCnt} messages,   unknown byte is {hex(int(data[1]))}    data={toString(data)}")
            for i in range(msgCnt):
                eventZone = int(data[2 + (2 * i)])
                eventType = int(data[3 + (2 * i)])
                displayit("Event", eventZone, eventType)

                et : EVENT_TYPE = EVENT_TYPE(eventType) if eventType in EVENT_TYPE else EVENT_TYPE.NOT_DEFINED

                if not processSpecialEntries(eventZone, eventType):
                    self.addPanelEventData(AlPanelEventData(name = eventZone, action = int(eventType))) # assume partition -1 means a panel event not tied to a partition

                    if eventZone-1 in self.SensorList:                                              # only used if it decides that siren is sounding, then that is the trigger sensor
                        self.PartitionState[0].UpdatePanelState(et, self.SensorList[eventZone-1])   # Assume all panel state goes through partition 1

                    if et == EVENT_TYPE.FORCE_ARM or (self.pmForceArmSetInPanel and et == EVENT_TYPE.DISARM): # Force Arm OR (ForceArm has been set and Disarm)
                        self.pmForceArmSetInPanel = (et == EVENT_TYPE.FORCE_ARM)                                 # When the panel uses ForceArm then sensors may be automatically armed and bypassed by the panel
                        log.debug("[handle_msgtypeA7]              Panel has been Armed using Force Arm, sensors may have been bypassed by the panel, asking panel for an update on bypassed sensors")
                        if self.isPowerMaster():
                            self.B0_Wanted.add(B0SubType.ZONE_BYPASS)
                        else:
                            self._addMessageToSendList(Send.BYPASSTAT)
        else:
            log.debug(f"[handle_msgtypeA7]      Partitions in use = {self.getPartitionsInUse()}  data={toString(data)}")
            

    def handle_msgtypeAB(self, data) -> bool:  # PowerLink Message
        """ MsgType=AB - Panel Powerlink Messages """
        log.debug(f"[handle_msgtypeAB]  data {toString(data)}")

        # Restart the timer
        self._reset_watchdog_timeout()

        subType = data[0]
        if subType == 1 and self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.STANDARD_PLUS]:
            # Panel Time
            log.debug("[handle_msgtypeAB] ***************************** Got Panel Time ****************************")

            pt = datetime(2000 + data[7], data[6], data[5], data[4], data[3], data[2]).astimezone()            
            log.debug(f"[handle_msgtypeAB]    Panel time is {pt}")
            self.setTimeInPanel(pt)

        elif subType == 3 and self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.STANDARD_PLUS]:  # keepalive message
            # Example 0D AB 03 00 1E 00 31 2E 31 35 00 00 43 2A 0A
            #               03 00 1e 00 33 33 31 34 00 00 43        From a Powermax+     PanelType=1, Model=33

            log.debug("[handle_msgtypeAB] ***************************** Got PowerLink Keep-Alive ****************************")
            # It is possible to receive this between enrolling (when the panel accepts the enrol successfully) and the EPROM download
            #     I suggest we simply ignore it

            self._reset_powerlink_counter() # reset when received keep-alive from the panel

            if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.STANDARD_PLUS]:
                self._reset_keep_alive_messages()
                self._addMessageToSendList(Send.ALIVE)       # The Powerlink module sends this when it gets an i'm alive from the panel.

            if self.PanelMode == AlPanelMode.STANDARD_PLUS:
                log.debug("[handle_msgtypeAB]         Got alive message while Powerlink mode pending, going to full powerlink and calling Restore")
                self.PanelMode = AlPanelMode.POWERLINK  # it is truly in powerlink now we are receiving powerlink alive messages from the panel
                self._triggerRestoreStatus()
                #self._dumpSensorsToLogFile()

        elif subType == 3:  # keepalive message
            log.debug("[handle_msgtypeAB] ***************************** Got PowerLink Keep-Alive ****************************")
            log.debug("[handle_msgtypeAB] ********************* Panel Mode not Powerlink / Standard Plus **********************")
            self.UnexpectedPanelKeepAlive = True    

        elif subType == 5 and self.PanelMode == AlPanelMode.POWERLINK:  # -- phone message
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
                log.debug(f"[handle_msgtypeAB] PowerLink Phone: Unknown Action {hex(data[1]).upper()}")

        elif subType == 10 and data[2] == 0 and self.PanelMode == AlPanelMode.POWERLINK:
            log.debug(f"[handle_msgtypeAB] PowerLink telling us what the code {data[3]} {data[4]} is for downloads, currently not used as I'm not certain of this, and never seen it")

        elif subType == 10 and data[2] == 1:
            if self.PanelMode == AlPanelMode.POWERLINK:
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enrol but not acted on (already in powerlink) **************************")
            elif not self.ForceStandardMode:
                self.PanelWantsToEnrol = True
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enrol **************************")

        return True

    # X10 Names (0xAC) I think
    def handle_msgtypeAC(self, data):  # PowerLink Message
        """ MsgType=AC - ??? """
        log.debug(f"[handle_msgtypeAC]  data {toString(data)}")

    def handle_msgtypeAD(self, data):  # PowerLink Message
        """ MsgType=AD - Panel Powerlink Messages """
        log.debug(f"[handle_msgtypeAD]  data {toString(data)}")
        if data[2] == 0x00: # the request was accepted by the panel
            if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
                log.debug(f"[handle_msgtypeAD]      adding Image FB to send list")
                self._addMessageToSendList(Send.IMAGE_FB)

    def _checkallsame(self, val, b : bytearray) -> []:
        retval = []
        for i in range(0,len(b)):
            if int(b[i]) != val:
                retval.append(i)
        return retval

    def processB0LogEntry(self, total, current, data):
        # PM10
        #    -- time ---          Ev Pt               Pt = Partition I think.  Partition 0 is System or the Panel itself.
        #    3f 71 02 67 03 00 00 5c 00 04            data[4] seems to always be 03, 06 or 0C.  device type - 0c - panel, 09 - plink, 03 - zones
        #    69 3a 01 67 0c 00 00 1c 00 53            data[5] if device type is zones, this is the zero based zone id
        #    69 3a 01 67 06 00 00 1b 01 52 

        if self.onPanelLogHandler is not None:
            # There's no point in doing all of this if there's no handler to send it to!
            # extract the time as "epoch time" and convert to normal time
            hs = self._makeInt(data[0:4])
            pmtime = datetime.fromtimestamp(hs)
            #log.debug(f"[handle_msgtypeA0]   Powermaster time {hs} as hex {hex(hs)} from epoch is {pmtime}")
            device_type = data[4]
            iEventZone = 0
            if device_type == 3:          # device type =>  0c - panel, 09 - plink, 03 - zone
                iEventZone = data[5] + 1  # if device type is zone, zero based zone id

            partition = data[8]
            # Create an event log array
            l = AlLogPanelEvent(total = total, current = current, partition = partition, dateandtime = pmtime, zone = iEventZone, event = data[7])
            log.debug(f"[processB0LogEntry]                       Log Entry {l}")
            # Send the event log in to HA
            #     Do not use timezone times as it was the log created on that day at that time
            self.onPanelLogHandler(l)

    def _decode_4B(self, sensor, data):
        # Get local time
        t = self._getTimeFunction()
        # create an integer from the B0 data, this is the number of seconds since the epoch (00:00 on 1st Jan 1970)
        hs = self._makeInt(data[0:4])
        # Make a datetime from it using the same timezone but subtract off the difference between local time and UTC
        trigger = datetime.fromtimestamp(hs, tz=t.tzinfo) - t.utcoffset()
        code = int(data[4])
        # 00 - Not a zone
        # 01 - Open (need to check timestamp)
        # 02 - Closed (need to check timestamp)
        # 03 - Motion (need to check timestamp)
        # 04 - CheckedIn?  As in device checked in.     
        if sensor in self.SensorList and (code == 0 or code == 4):
            self.SensorList[sensor].statuslog = trigger
        elif sensor in self.SensorList and code >= 1:
            triggered = False
            if self.Panel_Integration_Time_Difference is not None:  # Can only be True if AB messages are processed, therefore Std+ or Powerlink
                tolerance = 4 # seconds
                panelTime = t + self.Panel_Integration_Time_Difference
                diff = abs((trigger - panelTime).total_seconds())
                log.debug(f"[_decode_4B]           Sensor Updated = {sensor:>2}  timenow = {t}  self.Panel_Integration_Time_Difference {self.Panel_Integration_Time_Difference.total_seconds()}    diff {diff}     panelTime {panelTime}     trigger {trigger}")
                triggered = diff <= tolerance

            else:
                log.debug(f"[_decode_4B]           Sensor Updated = {sensor:>2}  trigger {trigger}")
                triggered = self.SensorList[sensor].statuslog is None or (trigger - self.SensorList[sensor].statuslog) >= timedelta(milliseconds=500)

            if triggered:
                log.debug(f"[_decode_4B]           Sensor Updated = {sensor:>2}  code {code}     trigger {trigger}")
                if code == 1:
                    self.SensorList[sensor].do_status(True)
                elif code == 2:
                    self.SensorList[sensor].do_status(False)
                elif code == 3:
                    self.SensorList[sensor].do_trigger(True)
                else:
                    log.debug("[_decode_4B]          ***************************** Sensor Updated with an unused code *****************************")
                log.debug(f"[_decode_4B]                  my time {self.SensorList[sensor].triggertime}    panels time {trigger}")

                self.SensorList[sensor].statuslog = trigger
            else:
                log.debug(f"[_decode_4B]           Sensor {sensor:>2} Not Updated as Timestamp the same =  code {code}     sensor time {trigger}     {self.SensorList[sensor].statuslog}")

    def settings_data_type_formatter( self, data_type: int, data: bytes, data_item_size: int = 16, byte_size: int = 1, no_of_entries: int = 1 ) -> int | str | bytearray:
        """Format data for 35 and 42 data."""

        match data_type:
            case DataType.ZERO_PADDED_STRING:
                return data.decode("ascii", errors="ignore").rstrip("\x00")     # \x00 padded string

            case DataType.DIRECT_MAP_STRING:
                datalen = int(len(data) / no_of_entries)
                return data.hex() if no_of_entries == 1 else [ data[i:i+datalen].hex() for i in range(0, no_of_entries, datalen) ]

            case DataType.FF_PADDED_STRING:
                return data.hex().replace("ff", "")

            case DataType.DOUBLE_LE_INT:  # 2 byte int
                return [b2i(data[i : i + 2], False) for i in range(0, len(data), 2)] if len(data) > 2 else b2i(data[0:2], False)

            case DataType.INTEGER:  # 1 byte int?
                # Assume 1 byte int list
                return b2i(data) if len(data) == byte_size else [ b2i(data[i : i + byte_size]) for i in range(0, len(data), byte_size) ]

            case DataType.STRING:
                return data.decode("ascii", errors="ignore")

            case DataType.SPACE_PADDED_STRING: # Space padded string
                return data.decode("ascii", errors="ignore").rstrip(" ")

            case DataType.SPACE_PADDED_STRING_LIST: # Space paddeded string list - seems all 16 chars
                # Cmd 35 0d 00 can include a \x00 instead of \x20 (space)
                # Remove any \x00 also when decoding.
                names = wrap(data.decode("ascii", errors="ignore"), data_item_size)
                return [ name.replace("\x00", "").rstrip(" ") for name in names if name.replace("\x00", "").rstrip(" ") != "" ]
        
        return data.hex(" ")

    def updatePanelSetting(self, key, length, datasize, data, display : bool = False, msg : str = "") -> bool:

        s = pmPanelSettingCodes[key].tostring(self.PanelSettings[key])              # Save the data before the update

        if pmPanelSettingCodes[key].item is not None:
            if len(data) > pmPanelSettingCodes[key].item:
                self.PanelSettings[key] = data[pmPanelSettingCodes[key].item]
        elif datasize == RAW.BITS.value:
            if len(self.PanelSettings[key]) < length * 8 :
                # replace as current length less than the new data
                #log.debug(f"[updatePanelSetting]              {key=}  replace")
                self.PanelSettings[key] = []
                for i in range(0, length):
                    for j in range(0,8):  # 8 bits in a byte
                        self.PanelSettings[key].append((data[i] & (1 << j)) != 0)
            else:
                # overwrite as current length is same as or more than new data
                #log.debug(f"[updatePanelSetting]              {key=}  overwrite")
                for i in range(0, length):
                    for j in range(0,8):  # 8 bits in a byte
                        self.PanelSettings[key][(i*8)+j] = (data[i] & (1 << j)) != 0
        else:
            self.PanelSettings[key] = data

        v = pmPanelSettingCodes[key].tostring(self.PanelSettings[key])
        if display:
            if len(s) > 100 or len(v) > 100:
                if s != v:
                    log.debug(f"[updatePanelSetting]              changed=True      {key=}   ({msg})")
                    log.debug(f"[updatePanelSetting]                        replacing {s}")
                    log.debug(f"[updatePanelSetting]                        with      {v}")
                else:
                    log.debug(f"[updatePanelSetting]              changed=False     {key=}   ({msg})    data is {v}")
            else:
                if s != v:
                    log.debug(f"[updatePanelSetting]              changed=True      {key=}   ({msg})    replacing {s}  with {v}")
                else:
                    log.debug(f"[updatePanelSetting]              changed=False     {key=}   ({msg})    data is {v}")
        else:
            log.debug(f"[updatePanelSetting]              changed={s != v}     {key=}   ({msg})")
        return s != v

    def _extract_35_data(self, ch):
        #03 35 0b ff 08 ff 06 00 00 01 00 00 00 02 43
        #dataContent = b2i(data[0:2], big_endian=False)
        dataContentA = ch.data[0]
        dataContentB = ch.data[1]
        dataContent = (dataContentB << 8) | dataContentA
        datatype = ch.data[2]        # 6 is a String
        datalen = ch.length - 3
        data = ch.data[3:]
        log.debug("[_extract_35_data]     ***************************** Panel Settings ********************************")
        dat = self.settings_data_type_formatter(datatype, data)

        if not OBFUS:
            log.debug(f"[_extract_35_data]           dataContent={hex(dataContent)} panel setting   { DataType(datatype) }  {datalen=}    data={toString(data)}")
            log.debug(f"[_extract_35_data]               dat type = {type(dat)}   dat = {dat}")

        processed_data = False

        building = False
        if dat is not None and (d := pmPanelSettingsB0_35.get(dataContent)) is not None:
            if d.processinstandard or not self.ForceStandardMode: #  self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
                if (d.length == 0 or ch.length == d.length) and datatype == d.datatype:
                    # Check the PanelSettings to see if there's one that refers to this dataContent
                    for key in pmPanelSettingCodes:
                        if pmPanelSettingCodes[key].PMasterB035Panel == dataContent:
                            log.debug(f"[_extract_35_data]          Matched it {key=}")
                            processed_data = True
                            if d.sequence is not None and isinstance(d.sequence, list): # We have a list of sequence identifiers e.g. [1,2,255]
                                # We should really check to make sure that we get all messages in the list but not yet --> TODO
                                # I'm assuming that 0x35 messages (and 0x42 maybe) are the only messages with sequences
                                if ch.datasize == RAW.BYTES.value and datatype == DataType.SPACE_PADDED_STRING_LIST:
                                    if key not in self.builderData:
                                        self.builderData[key] = []                    # empty the data list to concatenate the sequenced message data
                                        self.builderMessage[key] = d.sequence         # get the list of sequences there needs to be to complete the data
                                    if ch.sequence in self.builderMessage[key]:
                                        self.builderData[key].extend(dat)    # Add actual data to the end of the list, we assume that the panel sends the data in order and we don't need to check the order
                                        self.builderMessage[key].remove(ch.sequence)  # Got this sequence data so remove from the list
                                    else:
                                        log.debug(f"[_extract_35_data]                        building {key}   Unexpected data sequence {ch.sequence} or sequence sent more than once")
                                    if ch.sequence == 255:
                                        if len(self.builderMessage[key]) > 0:
                                            # Received the sequence end but we are missing some of the sequence
                                            log.debug(f"[_extract_35_data]                        building {key}   We have the sequence terminator message but we still have missing sequenced messages {self.builderMessage[key]}.  Dumping all message data and not using it")
                                        else:
                                            # copy across to use it
                                            self.PanelSettings[key] = self.builderData[key]
                                    if d.display:
                                        log.debug(f"[_extract_35_data]                        building {key}   {self.builderData[key]}")
                                    building = ch.sequence != 255
                            else:
                                self.updatePanelSetting(key = key, length = ch.length, datasize = ch.datasize, data = data, display = d.display, msg = d.msg)
                            break
                else:
                    log.debug(f"[_extract_35_data]               {d.msg} data lengths differ: {ch.length=} {d.length=}   type: {datatype=} {d.datatype=}")
            else:
                log.debug(f"[_extract_35_data]               {d.msg} not processed as this specifically prevented in standard mode")
        else:
            log.debug(f"[_extract_35_data]               dataContent={hex(dataContent)} panel setting unknown      {datatype=}  {datalen=}    data={toString(ch.data[3:])}")

        if not building:
            # remove it from the dictionary
            self.builderMessage = {}  # 
            self.builderData = {}     # 

        if dataContent == 0x000F and datalen == 2 and isinstance(dat,str):
            processed_data = True
            if not self.DownloadCodeUserSet and len(dat) == 4:
                self.DownloadCode = dat
                self.DownloadCodeUserSet = True    # Set to True as the download code has been obtained directly from the panel so it mist be correct
                self.PanelSettings[PanelSetting.PanelDownload] = self.DownloadCode
                log.debug(f"[_extract_35_data]               Setting Download Code : {self.DownloadCode}")

        elif dataContent == 0x003C and datalen == 15 and isinstance(dat,str): # 8 is a string
            processed_data = True
            if not self.pmGotPanelDetails:
                name = dat.replace("-"," ")
                log.debug(f"[_extract_35_data] Panel Name {name}.  Not got panel details so trying to reconcile:")
                for p,v in pmPanelType.items():
                    log.debug(f"[_extract_35_data]     Checking: {p} {v}")
                    if name == v:
                        log.debug(f"[_extract_35_data] Fount it: {v}")
                        self.ModelType = 0xDA7E  # No idea what model type it is so just set it to a valid number, DAVE
                        if not self._setDataFromPanelType(p):
                            log.debug(f"[_extract_35_data] Panel Type {data[5]} Unknown")                            
                        else:
                            log.debug(f"[_extract_35_data] PanelType={self.PanelType} : {self.PanelModel} , Model={hexify(self.ModelType)}   Powermaster {self.PowerMaster}")
                            self.pmGotPanelDetails = True
                        break
            else:
                log.debug("[_extract_35_data] Not Processed as already got Panel Details")

        elif dataContent == 0x0030 and datalen == 1 and isinstance(dat,int): #
            processed_data = True
            if dat == 0:
                log.debug(f"[_extract_35_data] Seems to indicate that partitions are disabled in the panel")
            else:
                log.debug(f"[_extract_35_data] Seems to indicate that partitions are enabled in the panel, but nothing done with this data, dat={dat}")
            self.PanelSettings[PanelSetting.PartitionEnabled] = [dat]   # this just stops it being mandatory again, it is not used
            self.partitionsEnabled = dat != 0 and dat != 255

        if not processed_data and dat is not None:
#            if not OBFUS:
            log.debug(f"[_extract_35_data]               NOT PROCESSED dat = {dat}")
        log.debug("[_extract_35_data]     ***************************** Panel Settings Exit ***************************")

    def _extract_42_data(self, ch) -> tuple[int, str | int | list[str | int]]:
        """Format a command 42 message.

        This has many parameter options to retrieve EPROM settings.
        bytes 0 & 1 are the parameter
        bytes 2 & 3 is the max number of data items
        bytes 4 & 5 is the size of each data item (in bits)
        bytes 6 & 7 don't know
        bytes 8     is the data type
        bytes 9     byte_size 
        bytes 10 & 11 is the start index of data item
        bytes 12 & 13 is the number of data items
        bytes 14 to end is data

        """

        def chunk_bytearray(data: bytearray, size: int) -> list[bytes]:
            """Split bytearray into sized chunks."""
            if data:
                return [data[i : i + size] for i in range(0, len(data), size)]
            return None

        dataContent = b2i(ch.data[0:2], big_endian=False)
        max_data_items = b2i(ch.data[2:4], big_endian=False)
        data_item_size = max(1, int(b2i(ch.data[4:6], big_endian=False) / 8))
        not_known = b2i(ch.data[6:8], big_endian=False)
        datatype = ch.data[8]  # This is actually 2 bytes, what is second byte??
        byte_size = 2 if ch.data[9] == 0 else 1
        start_entry = b2i(ch.data[10:12], big_endian=False)
        no_of_entries = b2i(ch.data[12:14], big_endian=False)

        log.debug(f"[_extract_42_data]               {dataContent=}   {max_data_items=}   {data_item_size=}   {start_entry=}   { DataType(datatype) if datatype in DataType else "DataType is UNDEFINED" }   {byte_size=}   {no_of_entries=}")

        #####################################################################################################################################################
        dat = self.settings_data_type_formatter(datatype, ch.data[14:], data_item_size=data_item_size, byte_size=byte_size, no_of_entries=no_of_entries)
        if dat is None:
            log.debug(f"[_extract_42_data]               dat is NONE")
        elif OBFUS:
            log.debug(f"[_extract_42_data]               dat type = {type(dat)}   dat = OBFUSCATED")
        else:
            log.debug(f"[_extract_42_data]               dat type = {type(dat)}   dat = {dat}")
        #####################################################################################################################################################

        processed_data = False

        if dat is not None and (d := pmPanelSettingsB0_42.get(dataContent)) is not None:
            #log.debug(f"[_extract_42_data]                  DataContent {d=}")
            if d.processinstandard or not self.ForceStandardMode: #  self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
                if (d.length == 0 or ch.length == d.length) and datatype == d.datatype:
                    # Check the PanelSettings to see if there's one that refers to this dataContent
                    for key in pmPanelSettingCodes:
                        if pmPanelSettingCodes[key].PMasterB042Panel == dataContent:
                            log.debug(f"[_extract_42_data]          Matched it {key=}")
                            processed_data = True
                            if ch.datasize == RAW.BYTES.value and datatype == DataType.SPACE_PADDED_STRING_LIST:
                                s = f"{self.PanelSettings[key]}"              # Save the data before the update
                                log.debug(f"[_extract_42_data]               dat {dat}")
                                if len(self.PanelSettings[key]) <= start_entry:
                                    aa = [f"Undefined{i}" for i in range(len(self.PanelSettings[key]), start_entry+1)]
                                    self.PanelSettings[key].extend(aa)
                                self.PanelSettings[key][start_entry:start_entry+no_of_entries] = dat[0:no_of_entries]
                                log.debug(f"[_extract_42_data]               before {s}")
                                log.debug(f"[_extract_42_data]               after  {self.PanelSettings[key]}")
                            else:                        
                                self.updatePanelSetting(key = key, length = ch.length, datasize = ch.datasize, data = ch.data[14:], display = d.display, msg = d.msg)
                else:
                    log.debug(f"[_extract_42_data]               {d.msg} data lengths differ: {ch.length=} {d.length=}   type: {datatype=} {d.datatype=}")
            else:
                log.debug(f"[_extract_42_data]               {d.msg} not processed as this specifically prevented in standard mode")
        else:
            log.debug(f"[_extract_42_data]               dataContent={hex(dataContent)} panel setting unknown      {datatype=}  data={toString(ch.data)}")

        if dataContent == 0x000F and data_item_size == 2 and isinstance(dat,str) and len(dat) == 4: #
            processed_data = True
            if not self.DownloadCodeUserSet and len(dat) == 4:
                self.DownloadCode = dat
                self.DownloadCodeUserSet = True    # Set to True as the download code has been obtained directly from the panel so it mist be correct
                self.PanelSettings[PanelSetting.PanelDownload] = self.DownloadCode
                log.debug(f"[_extract_42_data]               Setting Download Code : {self.DownloadCode}")

        elif dataContent == 0x003C and data_item_size == 15 and max_data_items == 1 and no_of_entries == 1 and isinstance(dat,str): #
            processed_data = True
            name = dat.replace("-"," ")
            self.PanelSettings[PanelSetting.PanelName] = name
            if not self.pmGotPanelDetails:
                log.debug(f"[_extract_42_data] Panel Name {name}.  Not got panel details so trying to reconcile:")
                for p,v in pmPanelType.items():
                    log.debug(f"[_extract_42_data]     Checking: {p} {v}")
                    if name.lower() == v.lower():
                        log.debug(f"[_extract_42_data] Fount it: {v}")
                        self.ModelType = 0xDA7E  # No idea what model type it is so just set it to a valid number, DAVE
                        if self._setDataFromPanelType(p):
                            log.debug(f"[_extract_42_data] PanelType={self.PanelType} : {self.PanelModel} , Model={hexify(self.ModelType)}   Powermaster {self.PowerMaster}")
                            self.pmGotPanelDetails = True
                        else:
                            log.debug(f"[_extract_42_data] Panel Type {ch.data[5]} Unknown")                            
                        break
            else:
                log.debug("[_extract_42_data] Not Processed as already got Panel Details")

        elif dataContent == 0x0030 and data_item_size == 1 and max_data_items == 1 and isinstance(dat,int): #
            log.debug(f"[_extract_42_data]     partitiondata set as {self.PanelSettings[PanelSetting.PartitionData] if PanelSetting.PartitionData in self.PanelSettings else "Undefined"}")
            processed_data = True
            log.debug(f"[_extract_42_data]         processing 0x0030     {dat}")
            if dat == 0:
                log.debug("[_extract_42_data]          Seems to indicate that partitions are disabled in the panel")
            else:
                log.debug(f"[_extract_42_data]          Seems to indicate that partitions are enabled in the panel {dat}")
            self.PanelSettings[PanelSetting.PartitionEnabled] = [dat]   # this just stops it being mandatory again, it is not used
            self.partitionsEnabled = dat != 0 and dat != 255

        if not processed_data and dat is not None:
#            if not OBFUS:
            log.debug(f"[_extract_42_data]               NOT PROCESSED dat = {dat}")

    def _updateSystemStatus(self, partition, sysFlags, sysStatus, sysStatus2, unknown4):
        piu = self.getPartitionsInUse()
        if sysFlags & 0x80 != 0:  # This seems to be a "partition enabled" indication
            #sysStatus = data[offset + 17]
            #sysStatus2 = data[offset + 19]  # Bit 0 represents the "last 10 seconds" bit, not sure about the rest.
            #unknown4 = data[offset + 20]

            log.debug(f"[_updateSystemStatus]        Partition={partition+1} with data sysStatus=0x{hexify(sysStatus)}  sysFlags=0x{hexify(sysFlags)}  X=0x{hexify(sysStatus2)}  Y=0x{hexify(unknown4)}")
            # I believe that bit 0 of sysStatus2 represents the "Instant" indication for armed home and armed away (and maybe disarm etc) i.e. all the PanelState values above 0x0F
            sysStatus = (sysStatus & 0xF) | (( sysStatus2 << 4 ) & 0x10 )

            oldPS = self.PartitionState[partition].PanelState
            # Mask off the top bit as seems to be used to indicate overall validity
            s = self.PartitionState[partition].ProcessPanelStateUpdate(sysStatus=sysStatus, sysFlags=sysFlags & 0x7F, PanelMode=self.PanelMode)  # does not set partition in return value
            if s is not None:
                if self.getPartitionsInUse() is not None:   # we have partitions so add it in as an attribute
                    s.setPartition(partition+1)
                self.addPanelEventData(s)

            newPS = self.PartitionState[partition].PanelState
            if newPS == AlPanelStatus.DISARMED and newPS != oldPS:
                # Panel state is Disarmed and it has just changed
                self.B0_Wanted.add(B0SubType.ZONE_BYPASS)
                #self._addMessageToSendList(Send.BYPASSTAT)

            if sysFlags & 0x20 != 0:  # Zone Event
                log.debug(f"[_updateSystemStatus]                 It also claims to have a zone event with data (hex) {hex(sysStatus2)} possibly with this data {hex(unknown4)}")
                #self.ProcessZoneEvent(eventZone=eventZone, eventType=eventType)
        elif piu is not None and partition+1 in piu:
            log.debug(f"[_updateSystemStatus]        Partition={partition+1}  Not Enabled but it is in the current Partition set {piu}, that's a problem")
        else:
            log.debug(f"[_updateSystemStatus]        Partition={partition+1}  Not Enabled")
        

    def processChunk(self, ch : chunky):

        def collateFromRawBits(d, s, m) -> str:
            deviceStr = ""
            for i in range(0, s):
                v = (d >> i) & 0x01
                if v == 1:
                    log.debug(f"[processChunk] Found an Enrolled PowerMaster {m} {i}")
                    deviceStr = f"{deviceStr},{i:0>2}"
            return deviceStr[1:]   # miss the first comma
        
        def _decode_24(partitionCount : int, dateData: bytearray, unknownData : bytearray, partitionData : bytearray):
            
            iSec = dateData[0]
            iMin = dateData[1]
            iHour = dateData[2]
            iDay = dateData[3]
            iMonth = dateData[4]
            iYear = dateData[5]

            # Attempt to check and correct time
            pt = datetime(2000 + iYear, iMonth, iDay, iHour, iMin, iSec).astimezone()
            self.setTimeInPanel(pt)
            messagedate = f"{iDay:0>2}/{iMonth:0>2}/{iYear}   {iHour:0>2}:{iMin:0>2}:{iSec:0>2}"

            unknown1 = unknownData[0]
            unknown2 = unknownData[1]

            log.debug(f"[_decode_24]    Panel time is {pt}  date={messagedate}    data (hex) 14={hex(unknown1)}  15={hex(unknown2)}  PartitionCount={partitionCount}")

            for i in range(partitionCount):
                offset = i * 4
                # Repeat 4 bytes (17 to 20) for more than 1 partition, assume 19 and 20 are zone data
                self._updateSystemStatus(i, partitionData[offset + 1], partitionData[offset], partitionData[offset + 2], partitionData[offset + 3])

        # Whether to process the experimental code (and associated B0 message data) or not
        experimental = True
        beezerodebug = True
        beezerodebug2 = True
        beezerodebug4 = True
        beezerodebug7 = True

        st = pmSendMsgB0_reverseLookup[ch.subtype].data if ch.subtype in pmSendMsgB0_reverseLookup else None
        
        if self.beezero_024B_sensorcount is not None and st != B0SubType.ZONE_LAST_EVENT:
            self.beezero_024B_sensorcount = None   # If theres a next time so they are coordinated
            log.debug(f"[handle_msgtypeB0]        Resetting beezero_024B_sensorcount st=<{st}>")

        if st is None:
            log.debug(f"[handle_msgtypeB0]     Unknown chunk={ch.GetItAll()}")
            return

        ind = IndexName(ch.index) if ch.index in IndexName else IndexName.UNDEFINED
        datasize = RAW(ch.datasize) if ch.datasize in RAW else RAW.UNDEFINED
        seq_type = SEQUENCE(ch.type) if ch.type in SEQUENCE else SEQUENCE.UNDEFINED

        #log.debug(f"[handle_msgtypeB0]     st = {st}      chunky = {ch}      self.beezero_024B_sensorcount = {self.beezero_024B_sensorcount}") # [processChunk]                 chunky = sequence 255  datasize 40  index 3   length 140
        if datasize == RAW.UNDEFINED:
            log.debug(f"[handle_msgtypeB0]     datasize is undefined, chunk={ch.GetItAll()}")

        match (st, datasize, ind, ch.length):

            case (B0SubType.PANEL_SETTINGS_35, _    , _    ,  _ ):
                #log.debug(f"[handle_msgtypeB0]          Got PANEL_SETTINGS_35 {ch}")
                # I'm 100% sure this is correct
                self._extract_35_data(ch)
                self._updateAllSensors()

            case (B0SubType.PANEL_SETTINGS_42, _    , _    ,  _ ):
                #log.debug(f"[handle_msgtypeB0]          Got PANEL_SETTINGS_42 {ch}")
                if experimental:    
                    self._extract_42_data(ch)
                    self._updateAllSensors()

            case (B0SubType.PANEL_STATE,    RAW.BYTES, IndexName.MIXED,  20):
                # Panel state change, added just in case the panel abbreviates this message 
                # 06 00 00 00 02 00 00 00 29 0b 10 08 0b 18 14 06 00 85 00 00
                _decode_24(ch.data, 8, 14, 1, 17)
                self.B0_LastPanelStateTime = self._getUTCTimeFunction()

            case (B0SubType.PANEL_STATE,    RAW.BYTES, IndexName.MIXED,  21):
                # Panel state change, no idea what bytes 0 to 7 mean.
                # e.g. 06 00 00 00 02 00 00 00 29 0b 10 08 0b 18 14 06 01 00 85 00 00
                if ch.data[16] == 1:   # partition count set to 1
                    # We already know that the length of the ch.data is 21 so no need to check it
                    _decode_24(1, ch.data[8:14], ch.data[14:16], ch.data[17:21])
                    self.B0_LastPanelStateTime = self._getUTCTimeFunction()

            case (B0SubType.PANEL_STATE,    RAW.BYTES, IndexName.MIXED, 28):
                # Panel state change, no idea what bytes 0 to 7 mean. - the user that has the panel that sends this uses all 3 partitions
                # e.g. 0b 00 00 00 00 00 00 00 22 32 14 03 05 19 14 07 00 85 00 00 00 85 00 00 00 85 00 00
                if self.getPartitionsInUse() is not None:              # we have a 24 message that has extended data (for the partitions) and the panel has reported it has partitions in use
                    # We already know that the length of the ch.data is 28 so no need to check it
                    _decode_24(3, ch.data[8:14], ch.data[14:16], ch.data[16:28])
                    self.B0_LastPanelStateTime = self._getUTCTimeFunction()

            case (B0SubType.PANEL_STATE,    RAW.BYTES, IndexName.MIXED,  29):
                # Panel state change, no idea what bytes 0 to 7 mean.
                # e.g. 07 00 00 00 02 00 00 00 10 1d 0a 0a 0b 18 14 01 03 00 87 00 00 00 87 00 00 00 07 00 00
                if self.getPartitionsInUse() is not None:              # we have a 24 message that has extended data (for the partitions) and the panel has reported it has partitions in use
                    # We already know that the length of the ch.data is 29 so no need to check it
                    _decode_24(ch.data[16], ch.data[8:14], ch.data[14:16], ch.data[17:29])
                    self.B0_LastPanelStateTime = self._getUTCTimeFunction()

            case (B0SubType.SYSTEM_CAP, RAW.WORDS, IndexName.MIXED, _ ):
                # System capabilities
                ds = 2 # each entry is 2 words
                b = ch.length // ds
                # Set / Update panel capabilities
                self.PanelCapabilities = {}
                for i in range(0, b):
                    d = ch.data[(i*ds)+1] * 256 + ch.data[i*ds]
                    t = IndexName(i).name if i in IndexName else f'Type {i}'
                    log.debug(f"[handle_msgtypeB0]              Got {st.name:<20}   {t:<14}   {toString(ch.data[i*ds:(i+1)*ds])}    decimal {d:>4}")
                    if i in IndexName:
                        self.PanelCapabilities[IndexName(i)] = d
                        # make sure any mandatory capabilities are recorded as complete
                        if IndexName(i) == IndexName.PGM:
                            self.PanelSettings[PanelSetting.HasPGM] = [ d >= 1 ]
                log.debug(f"[handle_msgtypeB0]             Panel Capabilities = {self.PanelCapabilities}")

            case (B0SubType.ZONE_OPENCLOSE, RAW.BITS,  IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                zoneLen = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, open/close information, zone length = {zoneLen}")
                self.do_sensor_update(ch.data[0:4], ZoneFunctions.DO_STATUS, "[handle_msgtypeB0]             Zone Status 32-01")
                if zoneLen >= 33:
                    self.do_sensor_update(ch.data[4:8], ZoneFunctions.DO_STATUS, f"[handle_msgtypeB0]             Zone Status {zoneLen}-33", 32, zoneLen)

            case (B0SubType.ZONE_BYPASS,    RAW.BITS,  IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                zoneLen = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, bypass information, zone length = {zoneLen}")
                self.do_sensor_update(ch.data[0:4], ZoneFunctions.DO_BYPASS, "[handle_msgtypeB0]             Zone Bypass 32-01")
                if zoneLen >= 33:
                    self.do_sensor_update(ch.data[4:8], ZoneFunctions.DO_BYPASS, f"[handle_msgtypeB0]             Zone Bypass {zoneLen}-33", 32, zoneLen)

            case (B0SubType.TAMPER_ALERT,   RAW.BITS,  IndexName.ZONES,  _ ):
                # I'm 50% sure this is correct
                zoneLen = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, tamper alert, zone length = {zoneLen}   --> Not yet processed as not 100% sure")
                #self.do_sensor_update(ch.data[0:4], ZoneFunctions.DO_TAMPER, "[handle_msgtypeB0]             Zone Tamper 32-01")
                #if zoneLen >= 33:
                #    self.do_sensor_update(ch.data[4:8], ZoneFunctions.DO_TAMPER, f"[handle_msgtypeB0]             Zone Tamper {zoneLen}-33", 32, zoneLen)

            case (B0SubType.TAMPER_ACTIVITY,   RAW.BITS,  IndexName.ZONES,  _ ):
                # I'm 50% sure this is correct
                zoneLen = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, tamper activity, zone length = {zoneLen}   --> Not yet processed as not 100% sure")
                #self.do_sensor_update(ch.data[0:4], ZoneFunctions.DO_TAMPER, "[handle_msgtypeB0]             Zone Tamper 32-01")
                #if zoneLen >= 33:
                #    self.do_sensor_update(ch.data[4:8], ZoneFunctions.DO_TAMPER, f"[handle_msgtypeB0]             Zone Tamper {zoneLen}-33", 32, zoneLen)

            case (B0SubType.ASSIGNED_PARTITION, RAW.BYTES, _    ,  _ ):   # paged
                if ch.index in IndexName:
                    log.debug(f"[handle_msgtypeB0]          Got Assigned Partition, {IndexName(ch.index).name:<14}  chunk = {ch}")
                else:
                    log.debug(f"[handle_msgtypeB0]          Got Assigned Partition, Index unknown    chunk = {ch}")

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                self._updateAllSensors()

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.SIRENS, _ ):
                count = pmPanelConfig[CFG.SIRENS][self.PanelType]
                self.PanelSettings[PanelSetting.SirenEnrolled] = [(ch.data[0] >> i) & 0x01 == 1 for i in range(min(ch.length * 8, count))]
                self.PanelStatus[PANEL_STATUS.SIRENS] = collateFromRawBits(ch.data[0], min(ch.length * 8, count), "siren")
                self._updateAllSirens()

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.REPEATERS, _ ):
                count = pmPanelConfig[CFG.REPEATERS][self.PanelType]
                self.PanelStatus[PANEL_STATUS.PANIC_BUTTONS] = collateFromRawBits(ch.data[0], min(ch.length * 8, count), "repeater")

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.PANIC_BUTTONS, _ ):
                #count = pmPanelConfig[CFG.PANIC_BUTTONS][self.PanelType]
                self.PanelStatus[PANEL_STATUS.PANIC_BUTTONS] = collateFromRawBits(self._makeInt(ch.data), ch.length * 8, "panic-button")

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.KEYPADS, _ ):
                count = pmPanelConfig[CFG.TWO_WKEYPADS][self.PanelType]
                self.PanelStatus[PANEL_STATUS.KEYPADS] = collateFromRawBits(self._makeInt(ch.data), min(ch.length * 8, count), "keypad")

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.KEYFOBS, _ ):
                count = pmPanelConfig[CFG.KEYFOBS][self.PanelType]
                self.PanelStatus[PANEL_STATUS.KEYFOBS] = collateFromRawBits(self._makeInt(ch.data), min(ch.length * 8, count), "keyfob")

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.PROXTAGS, _ ):
                count = pmPanelConfig[CFG.PROXTAGS][self.PanelType]
                self.PanelStatus[PANEL_STATUS.PROXTAGS] = collateFromRawBits(self._makeInt(ch.data), min(ch.length * 8, count), "proxtag")

            case (B0SubType.DEVICE_TYPES,   RAW.BYTES, IndexName.SIRENS,  _ ):
                # I'm 1% sure this is correct ie. it might be wrong
                self._updateAllSirens()

            case (B0SubType.DEVICE_TYPES,   RAW.BYTES, IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                self._updateAllSensors()

            case (B0SubType.ZONE_NAMES,     RAW.BYTES, IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                self._updateAllSensors()

            case (B0SubType.ZONE_TYPES,     RAW.BYTES, IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                self._updateAllSensors()

            case (B0SubType.ZONE_TEMPERATURE, RAW.BYTES, IndexName.ZONES,  _ ):
                #log.debug(f"[handle_msgtypeB0]          Got Zone Temperatures Chunk {ch}")
                #zoneCnt = self.getPanelCapability(IndexName.ZONES)
                zoneCnt = self.getPanelCapability(IndexName.ZONES)
                if ch.length >= zoneCnt:
                    for i in range(zoneCnt):
                        if i in self.SensorList and ch.data[i] != 255:
                            temp = -40.5 + (ch.data[i] / 2)
                            log.debug(f"[handle_msgtypeB0]            Zone {i+1} has temperature raw value {ch.data[i]}     temp={temp}")
                            self.SensorList[i].updateTemperature(temp)

            case (B0SubType.ZONE_LUX  ,     RAW.BYTES, IndexName.ZONES,  _ ):
                #log.debug(f"[handle_msgtypeB0]          Got Zone Luminance Chunk {ch}")
                #zoneCnt = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
                zoneCnt = self.getPanelCapability(IndexName.ZONES)
                if ch.length >= zoneCnt:
                    for i in range(zoneCnt):
                        if i in self.SensorList and ch.data[i] != 255:
                            log.debug(f"[handle_msgtypeB0]               Zone {i+1} has luminance value {ch.data[i]} --> not sure what the value means")
                            self.SensorList[i].updateLux(ch.data[i])

            case (B0SubType.ASK_ME_1,       RAW.BYTES, IndexName.MIXED,  _ ):
                log.debug(f"[handle_msgtypeB0]          Received ASK_ME_1 pop message   {ch}")
                if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD_PLUS, AlPanelMode.STANDARD]:
                    if ch.length > 0:
                        s = self._create_B0_Data_Request(taglist = set(ch.data))
                        self._addMessageToSendList(s, priority = MessagePriority.URGENT)
                    else:
                        log.debug(f"[handle_msgtypeB0]                   Empty ASK_ME_1 chunk={ch.GetItAll()}")

            case (B0SubType.ASK_ME_2,       RAW.BYTES, IndexName.MIXED,  _ ):
                log.debug(f"[handle_msgtypeB0]          Received ASK_ME_2 pop message   {ch}")
                if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD_PLUS, AlPanelMode.STANDARD]:
                    if ch.length > 0:
                        s = self._create_B0_Data_Request(taglist = set(ch.data))
                        self._addMessageToSendList(s, priority = MessagePriority.URGENT)
                    else:
                        #log.debug(f"[handle_msgtypeB0]                   Empty ASK_ME_2 chunk={ch.GetItAll()}   so asking for PANEL_STATE and ZONE_LAST_EVENT")
                        #s = self._create_B0_Data_Request(taglist = {pmSendMsgB0[B0SubType.PANEL_STATE].data, pmSendMsgB0[B0SubType.ZONE_LAST_EVENT].data} )
                        log.debug(f"[handle_msgtypeB0]                   Empty ASK_ME_2 chunk={ch.GetItAll()}   so asking for PANEL_STATE")
                        s = self._create_B0_Data_Request(taglist = {pmSendMsgB0[B0SubType.PANEL_STATE].data} )
                        self._addMessageToSendList(s, priority = MessagePriority.URGENT)
            
            case (B0SubType.ZONE_LAST_EVENT, RAW.FIVE_BYTE, IndexName.ZONES,  _ ):  # Each entry is ch.datasize=40 bits (or 5 bytes)
                if seq_type == SEQUENCE.SUB:    
                    # Zone Last Event
                    # PM10: I assume this does not get sent by the panel.
                    # PM30: This represents sensors Z01 to Z36.  Each sensor is 5 bytes.
                    #       For the PM30 with 64 sensors this comes out as 180 / 5 = 36
                    #log.debug(f"[handle_msgtypeB0] ZONE_LAST_EVENT sub   self.beezero_024B_sensorcount = {self.beezero_024B_sensorcount}")
                    if self.beezero_024B_sensorcount is None and ch.length % 5 == 0:             # Divisible by 5, each sensors data is 5 bytes
                        self.beezero_024B_sensorcount = int(ch.length / 5)
                        for i in range(0, self.beezero_024B_sensorcount):
                            o = i * 5
                            self._decode_4B(i, ch.data[o:o+5])
                elif seq_type == SEQUENCE.MAIN:    
                    # Zone Last Event
                    # PM10: This represents sensors Z01 to Z30.
                    #       For the PM10 with 30 sensors this comes out as 150 / 5 = 30
                    # PM30: This represents sensors Z37 to Z64.  Each sensor is 5 bytes.   
                    #       For the PM30 with 64 sensors this comes out as 140 / 5 = 28     (64-36=28)
                    #log.debug(f"[handle_msgtypeB0] ZONE_LAST_EVENT main   self.beezero_024B_sensorcount = {self.beezero_024B_sensorcount}")
                    if ch.length % 5 == 0:         # Divisible by 5, each sensors data is 5 bytes
                        if self.beezero_024B_sensorcount is not None: 
                            sensorcount = int(ch.length / 5)
                            for i in range(0, sensorcount):
                                o = i * 5
                                self._decode_4B(i + self.beezero_024B_sensorcount, ch.data[o:o+5])
                        else: # Assume PM10
                            # Assume that when the PowerMaster panel has less than 32 sensors then it just sends this and not msgType == 0x02, subType == pmSendMsgB0[B0SubType.ZONE_LAST_EVENT]
                            sensorcount = int(ch.length / 5)
                            for i in range(0, sensorcount):
                                o = i * 5
                                self._decode_4B(i, ch.data[o:o+5])
                    self.beezero_024B_sensorcount = None   # If theres a next time so they are coordinated

            case (B0SubType.LEGACY_EVENT_LOG, RAW.TEN_BYTE, IndexName.MIXED,  _ ):
                log.debug(f"[handle_msgtypeB0]       Got Legacy Event Log Chunk {ch}")
                self.processB0LogEntry(1, 1, ch.data)

            case (B0SubType.EVENT_LOG,        RAW.TEN_BYTE, IndexName.MIXED,  _ ):
                if seq_type == SEQUENCE.SUB:    
                    log.debug(f"[handle_msgtypeB0]          Got Sub Event Log Chunk {ch}")
                    eventTotal = pmPanelConfig[CFG.EVENTS][self.PanelType]
                    # Got Event Log Chunk sequence 6  datasize 80  index 255   length 170    data 92 73 00 67 0c 00 00 1c 00 63 92 73 00 67 06 00 00 1b 01 62 92 73 00 67 06 00 00 55 01 61 83 73 00 67 03 00 00 01 01 6a 7c 73 00 67 06 00 00 52 01 60 64 72 00 67 0c 00 00 1c 00 5f 64 72 00 67 06 00 00 1b 01 5e 56 72 00 67 0c 00 00 20 00 5d 2d 70 00 67 0c 00 00 1c 00 5c 26 70 00 67 0c 00 00 23 00 5b 0f 6e 00 67 0c 00 00 1c 00 5a 0f 6e 00 67 06 00 00 1b 01 59 fd 6d 00 67 0c 00 00 0c 00 58 a6 69 00 67 0c 00 00 1c 00 57 a6 69 00 67 06 00 00 1b 01 56 8e 69 00 67 0c 00 00 0c 00 55 24 69 00 67 0c 00 00 1c 00 54
                    datalength = 10 # We know this as we check datasize to be 80 above       ch.datasize // 8 # 8 bits in a byte
                    entries = ch.length // datalength
                    offset = (ch.sequence-1) * entries       # This assumes that all previous messages in the sequence had the same number of entries
                    if ch.length % datalength == 0:  # is the length divisible by datalength exactly
                        for i in range(0, ch.length, datalength):
                            logentry = offset + (i // datalength)
                            self.B0_PANEL_LOG_Counter = max(self.B0_PANEL_LOG_Counter, logentry)
                            log.debug(f"[handle_msgtypeB0]            Processing log entry {logentry}     data = {toString(ch.data[i:i+datalength])}")
                            self.processB0LogEntry(eventTotal, logentry + 1, ch.data[i:i+datalength])
                elif seq_type == SEQUENCE.MAIN:
                    log.debug(f"[handle_msgtypeB0]          Got Main Event Log Chunk {ch}")
                    eventTotal = pmPanelConfig[CFG.EVENTS][self.PanelType]
                    datalength = 10 # We know this as we check datasize to be 80 above       ch.datasize // 8 # 8 bits in a byte
                    offset = self.B0_PANEL_LOG_Counter + 1  # self.B0_PANEL_LOG_Counter is the maximum value from the 0x02 sequence so start from here + 1
                    if ch.length % datalength == 0:  # is the length divisible by datalength exactly
                        for i in range(0, ch.length, datalength):
                            logentry = offset + (i // datalength)
                            log.debug(f"[handle_msgtypeB0]               Processing log entry {logentry}     data = {toString(ch.data[i:i+datalength])}")
                            self.processB0LogEntry(eventTotal, logentry + 1, ch.data[i:i+datalength])

            case (B0SubType.WIRELESS_DEV_MISSING,    RAW.BITS, IndexName.ZONES,  _ ):
                # I'm 80% sure of this but all it does is set some attributes of the sensor
                log.debug(f"[handle_msgtypeB0]          Received message, 03 02 information (WIRELESS_DEV_MISSING), zone length = {ch.length}")
                zoneLen = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, zone missing or wireless issues, zone length = {zoneLen}")
                self.do_sensor_update(ch.data[0:4], ZoneFunctions.DO_MISSING, "[handle_msgtypeB0]             Zone Missing 32-01")
                if zoneLen >= 33:
                    self.do_sensor_update(ch.data[4:8], ZoneFunctions.DO_MISSING, f"[handle_msgtypeB0]             Zone Missing {zoneLen}-33", 32, zoneLen)

            case (B0SubType.WIRELESS_DEV_INACTIVE,   RAW.BITS, IndexName.ZONES,  _ ):
                # I'm 80% sure of this but all it does is set some attributes of the sensor
                log.debug(f"[handle_msgtypeB0]          Received message, 03 09 information (WIRELESS_DEV_INACTIVE), zone length = {ch.length}")
                zoneLen = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, zone inactive or wireless issues, zone length = {zoneLen}")
                self.do_sensor_update(ch.data[0:4], ZoneFunctions.DO_INACTIVE, "[handle_msgtypeB0]             Zone Inactive 32-01")
                if zoneLen >= 33:
                    self.do_sensor_update(ch.data[4:8], ZoneFunctions.DO_INACTIVE, f"[handle_msgtypeB0]             Zone Inactive {zoneLen}-33", 32, zoneLen)

            case (B0SubType.WIRELESS_DEV_ONEWAY,   RAW.BITS, IndexName.ZONES,  _ ):
                # I'm 80% sure of this but all it does is set some attributes of the sensor
                log.debug(f"[handle_msgtypeB0]          Received message, 03 0E information (WIRELESS_DEV_ONEWAY), zone length = {ch.length}")
                zoneLen = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, zone one way or wireless issues, zone length = {zoneLen}")
                self.do_sensor_update(ch.data[0:4], ZoneFunctions.DO_ONEWAY, "[handle_msgtypeB0]             Zone One Way 32-01")
                if zoneLen >= 33:
                    self.do_sensor_update(ch.data[4:8], ZoneFunctions.DO_ONEWAY, f"[handle_msgtypeB0]             Zone One Way {zoneLen}-33", 32, zoneLen)

            case (B0SubType.WIRELESS_DEV_CHANNEL,    RAW.BYTES, IndexName.ZONES,  _ ):
                # Something about Zone information (probably) but I'm not sure
                # The values after the ch.length represents something about the zone but I'm not sure what, the values change but I can't work out the pattern/sequence
                #   Received PowerMaster10 message 3/4 (len = 35)    data = 03 04 23 ff 08 03 1e 26 00 00 01 00 00 <24 * 00> 0c 43
                #   Received PowerMaster30 message 3/4 (len = 69)    data = 03 04 45 ff 08 03 40 11 08 08 04 08 08 <58 * 00> 89 43
                #   Received PowerMaster33 message 3/4 (len = 69)    data = 03 04 45 ff 08 03 40 11 11 15 15 11 15 15 11 <56 * 00> b9 43  # user has 8 sensors, Z01 to Z08
                #   Received PowerMaster33 message 3/4 (len = 69)    data = 03 04 45 ff 08 03 40 11 11 15 15 11 15 15 11 <56 * 00> bb 43
                #   Received PowerMaster33 message 3/4 (len = 69)    data = 03 04 45 ff 08 03 40 15 04 11 08 04 08 08 08 <56 * 00> c9 43
                #   Received PowerMaster33 message 3/4 (len = 69)    data = 03 04 45 ff 08 03 40 15 04 11 08 04 08 08 08 <56 * 00> cd 43

                log.debug(f"[handle_msgtypeB0]          Received message, 03 04 information, zone length = {ch.length}")
                if beezerodebug4:
                    for z in range(0, ch.length):
                        if z in self.SensorList:
                            s = int(ch.data[z])
                            log.debug(f"[handle_msgtypeB0]             Zone {z}  State(hex) {hex(s)}")

            case (B0SubType.ZONE_STAT07,    RAW.BYTES, IndexName.ZONES,  _ ):
                #  Received PowerMaster10 message 3/7 (len = 35)    data = 03 07 23 ff 08 03 1e 03 00 00 03 00 00 <24 * 00> 0d 43
                #  Received PowerMaster30 message 3/7 (len = 69)    data = 03 07 45 ff 08 03 40 03 03 03 03 03 03 <58 * 00> 92 43
                #  My PM30:  data = 03 07 45 ff 08 03 40 00 00 00 00 00 03 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 03 00 00 03 00 1d 43
                # Unknown information
                log.debug(f"[handle_msgtypeB0]          Received message, 03 07 information, zone length = {ch.length}")
                if beezerodebug7:
                    for z in range(0, ch.length):
                        #if z in self.SensorList:
                        if ch.data[z] != 0:
                            s = int(ch.data[z])
                            log.debug(f"[handle_msgtypeB0]             Zone {z}  State {s}")

            case _:
                if beezerodebug:
                    #log.debug(f"@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@")
                    #log.debug(f"[handle_msgtypeB0]        Received message chunk for  {st}, dont know what this is, chunk = {str(ch)}")

                    if ch.index == IndexName.MIXED: # Some kind of panel settings
                        b = -1
                        if ch.datasize > 8 and ch.datasize % 8 == 0:  # if it's exactly divisible by 8 then
                            ds = ch.datasize // 8
                            if ch.length % ds == 0:  # If it's exactly divisible
                                b = ch.length // ds
                                for i in range(0, b):
                                    log.debug(f"[handle_msgtypeB0]                     Got Unprocessed {st:<20}   MIXED     Block {i:<3}   {toString(ch.data[i*ds:(i+1)*ds])}")
                        if b < 0:      
                            log.debug(f"[handle_msgtypeB0]                     Got Unprocessed {st:<20}  MIXED   data = {toString(ch.data)}")
                    else:
                        t = IndexName(ch.index).name if ch.index in IndexName else f"Unknown Index {ch.index}"
                        log.debug(f"[handle_msgtypeB0]                     Got Unprocessed {st:<20} {t:<18}  data = {toString(ch.data)}")
                    #log.debug(f"@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@")

    # Only Powermasters send this message
    def handle_msgtypeB0(self, data):  # PowerMaster Message
        """ MsgType=B0 - Panel PowerMaster Message """
        # Only Powermasters send this message
        # Format: <Type> <SubType> <Length of Data and Counter> <Data> <Counter> <0x43>

        def chunkme(data) -> list:
            msgType = data[0]
            subType = data[1]
            if data[3] == 0xFF or (data[3] != 0xFF and msgType == 2):               # Check validity of data chunk (it could be valid and have no chunks)
                overall_length = data[2]
                retval = []
                current = 3
                #while current < len(data) and (data[current] == 0xFF or (data[current] != 0xFF and current == 3 and msgType == 2)):
                while current < len(data) - 3 and (data[current] == 0xFF or (data[current] != 0xFF and msgType == 2)):
                    length = data[current+3]
                    if length >= 0:
                        d = data[current + 4 : current + length + 4]
                        c = chunky(type = msgType, subtype = subType, sequence = data[current], datasize = data[current+1], index = data[current+2], length = length, data = d)
                        retval.append(c)
                    current = current + length + 4
                if current-2 != overall_length:
                    log.debug(f"[handle_msgtypeB0] ******************************************************** Message not fully processed for {msgType}   {overall_length - (current-2)} bytes not processed     control byte = {hexify(data[current])}    data is {toString(data[current:])} ********************************************************")
                if current-2 == overall_length:
                    return retval
            #else:
            #    log.debug(f"[handle_msgtypeB0] ******************************************************** Message not chunky for {msgType}  data is {toString(data)} ********************************************************")
            return []

        def isitchunky(chunks) -> bool:
            if len(chunks) == 0:
                log.debug(f"[handle_msgtypeB0]                        ++++++++++++++++++++++++++++++++ Message not chunky +++++++++++++++++++++++++++++++++++++++++++++++++")                
            else:
                for chunk in chunks:
                    log.debug(f"[handle_msgtypeB0]                    Decoded Chunk {chunk}")
            return len(chunks) > 0

        # A powermaster mainly interacts with B0 messages so reset watchdog on receipt
        self._reset_watchdog_timeout()

        # Include B0 messages to reset the im alive counter. PowerMax panels seem to be OK, but PowerMaster fail to get the i'm alive message in time
        self._reset_powerlink_counter() # reset when received keep-alive from the panel

        msgType = data[0]
        subType = data[1]
        msgLen  = data[2]
        #seq_type = SEQUENCE(msgType) if msgType in SEQUENCE else SEQUENCE.UNDEFINED
        #
        #if seq_type == SEQUENCE.SUB:
        #    log.debug(f"[handle_msgtypeB0] Queue it")
        #    return

        # The data <Length> value is 4 bytes less then the length of the data block (as the <MessageCounter> is part of the data count)
        if len(data) != msgLen + 4:
            log.debug("[handle_msgtypeB0]              Invalid Length, not processing")
            # Do not process this B0 message as it seems to be incorrect
            return

        if subType in self.B0_Waiting:
            self.B0_Waiting.remove(subType)

        if OBFUS:
            log.debug(f"[handle_msgtypeB0] Received {self.PanelModel or "UNKNOWN_PANEL_MODEL"} message {hexify(msgType):>02}/{hexify(subType):>02} (len = {msgLen})    data = <OBFUSCATED>")
        else:
            log.debug(f"[handle_msgtypeB0] Received {self.PanelModel or "UNKNOWN_PANEL_MODEL"} message {hexify(msgType):>02}/{hexify(subType):>02} (len = {msgLen})    data = {toString(data)}")

        msgInfo = pmSendMsgB0_reverseLookup[subType] if subType in pmSendMsgB0_reverseLookup else None

        log.debug(f"[handle_msgtypeB0]    msgInfo: {"unknown" if msgInfo is None else msgInfo}")

        if msgInfo is None:
            # Message unknown
            log.debug(f"[handle_msgtypeB0]             Message {msgType=} {subType=} not known about, lets see if its chunky.   data = {toString(data)}")
            isitchunky(chunkme(data[:-2]))

        elif msgInfo.chunky:
            # Process the messages that we know about and we believe are chunked
            chunks = chunkme(data[:-2]) # exclude b0 counter and Packet.POWERLINK_TERMINAL at the end
            if len(chunks) == 0:
                log.debug(f"[handle_msgtypeB0] ******************************************************** Message not chunky (we thought it was) and not processed further ************************************************* data = {toString(data)}")
            else:
                for chunk in chunks:
                    log.debug(f"[handle_msgtypeB0]       {toString(data[:2])}     Decode Chunk: {chunk}")
                    # Check the PanelSettings to see if there's one that refers to this message chunk
                    for key, value in pmPanelSettingCodes.items():
                        if value.PMasterB0Mess is not None and value.PMasterB0Mess in pmSendMsgB0 and pmSendMsgB0[value.PMasterB0Mess].data == subType and pmPanelSettingCodes[key].PMasterB0Index == chunk.index:
                            self.updatePanelSetting(key = key, length = chunk.length, datasize = chunk.datasize, data = chunk.data, display = True, msg = f"{subType=}")
                            break
                    self.processChunk(chunk)

        elif subType == pmSendMsgB0[B0SubType.INVALID_COMMAND].data: # msgInfo.data == "INVALID_COMMAND":  # 
            log.debug(f"[handle_msgtypeB0]             The Panel Indicates a B0 INVALID_COMMAND sent to the panel:   data={toString(data)}")
            if msgLen % 2 == 0: # msgLen is an even number
                for i in range(0, msgLen, 2):
                    command = data[3+i]
                    message = data[4+i]
                    log.debug(f"[handle_msgtypeB0]                     The Panel Indicates {hexify(command):0>2} {hexify(message):0>2}")
                    if command == 0x0D:                             # I think this is "retry later" instruction from the panel (and if it isn't then we can still ask for the message again)
                        if message in pmSendMsgB0_reverseLookup:    # Make sure that were asking for a message that we know about
                            self.B0_Wanted.add(message)
                        else:
                            log.debug(f"[handle_msgtypeB0]                            Unknown Message type for 'retry later' {hexify(message):0>2} so not asking for it")
                    elif command == 0x02:                     # 
                        self.gotBeeZeroInvalidCommand = True
        
        elif subType == pmSendMsgB0[B0SubType.PANEL_STATE_2].data and msgLen == 15: #  I've only seen a message length of 15 with all 3 partitions populated
            # Panel State (without zone data and not chunky)
            # 03 0f 0f 07 08 0f 00 00 00 43 03 00 87 00 87 00 07 24 43
            log.debug(f"[handle_msgtypeB0]             Panel State short (15) has been provided data={toString(data)}")
            # Check to make sure its not chunky
            #isitchunky(chunkme(data[:-2]))
            # process the data
            if len(chunkme(data[:-2])) == 0:                   # Check to make sure its not chunky
                for i in range(data[10]):                      # data[10] has the total supported partitions and not just the ones in use
                    offset = i * 2
                    # Repeat 2 bytes (11 to 12) for more than 1 partition.  Message length is 15 so we do not need to check the length.
                    self._updateSystemStatus(i, data[offset + 12], data[offset + 11], 0, 0)
            else:
                log.debug(f"[handle_msgtypeB0]             The message is chunky so I don't know how to process it:  data={toString(data)}")
                
        elif subType == pmSendMsgB0[B0SubType.PANEL_STATE_2].data and msgLen == 11: #  This is a test, I've only seen a message length of 15 with all 3 partitions populated
            # Panel State (without zone data and not chunky)
            log.debug(f"[handle_msgtypeB0]             Panel State short (11) has been provided data={toString(data)}")
            # Check to make sure its not chunky
            #isitchunky(chunkme(data[:-2]))
            if len(chunkme(data[:-2])) == 0:                   # Check to make sure its not chunky
                # process the data, assume 1 partition 
                self._updateSystemStatus(0, data[12], data[11], 0, 0)
            else:
                log.debug(f"[handle_msgtypeB0]             The message is chunky so I don't know how to process it:  data={toString(data)}")

        else:
            # Process the messages that we know about and are not chunked
            log.debug(f"[handle_msgtypeB0]             Message {msgInfo.data} known about but not chunky and not currently processed data={toString(data)}")
            if msgInfo.data in self.B0_temp and self.B0_temp[msgInfo.data] != data:
                log.debug(f"[handle_msgtypeB0]                 and its different to last time")
            self.B0_temp[msgInfo.data] = data
        #log.debug(f"[handle_msgtypeB0] ******************************************************** Leaving *************************************************")    

    def handle_msgtypeC0(self, data):  # Redirected Powerlink Data
        log.debug(f"[handle_msgtypeC0] ******************************************************** Should not be here *************************************************")

    def handle_msgtypeE0(self, data):  # Visonic Proxy
        # 0d e0 <no of alarm clients connected> <no of visonic clients connected> <no of monitor clients connected> <if in proxy mode> <if in stealth mode> 43 <checksum> 0a
        log.debug(f'[handle_msgtypeE0]  Visonic Proxy Status   '
                  f'Alarm: {"Connected" if data[0] == 1 else "Disconnected"}    '
                  f'Visonic: {"Connected" if data[1] == 1 else "Disconnected"}    '
                  f'HA: {"Connected" if data[2] == 1 else "Disconnected"}    ' 
                  f'Proxy: {"Yes" if data[3] == 1 else "No"}    '
                  f'Stealth: {"Yes" if data[4] == 1 else "No"}    '
                  f'Download: {"Yes" if data[5] == 1 else "No"}' )
        if self.ForceStandardMode:
            log.debug(f"[handle_msgtypeE0]  Visonic Proxy Not Being Used as Currently in Standard Mode")
        else:
            self.PowerLinkBridgeConnected = True
            self.PowerLinkBridgeAlarm = data[0] != 0
            self.PowerLinkBridgeProxy = data[3] != 0
            self.PowerLinkBridgeStealth = data[4] != 0

    def handle_msgtypeF4(self, data) -> bool:  # Static JPG Image
        """ MsgType=F4 - Static JPG Image """
        from PIL import Image, UnidentifiedImageError                        

        #log.debug(f"[handle_msgtypeF4]  data {toString(data)}")

        #      0 - message type  ==>  3=start, 5=data
        #      1 - always 0
        #      2 - sequence
        #      3 - data length
        msgtype = data[0]
        sequence = data[2]
        datalen = data[3]

        pushchange = False

        if msgtype == 0x03:     # JPG Header 
            log.debug(f"[handle_msgtypeF4]  data {toString(data)}")
            pushchange = True
            zone = (10 * int(data[5] // 16)) + (data[5] % 16)         # the // does integer floor division so always rounds down
            unique_id = data[6]
            image_id = data[7]
            lastimage = (data[11] == 1)
            size = (data[13] * 256) + data[12]
            totalimages = data[14]

            if zone in self.image_ignore:
                log.debug(f"[handle_msgtypeF4]        Ignoring Image Header, so not processing F4 data {lastimage=}")
                if lastimage:
                    self.image_ignore.remove(zone)
            elif self.ImageManager.isImageDataInProgress():
                # We have received an unexpected F4 message header when the previous image transfer is still in progress
                log.debug(f"[handle_msgtypeF4]        Previous Image transfer incomplete, so not processing F4 data and terminating image creation for zone {zone}")
                self.image_ignore.add(zone)        # Prevent the user being able to ask for this zone again until we've cleared all the current data
                self.ignoreF4DataMessages = True   # Ignore 0x05 data packets
                self.ImageManager.terminateImage()

            elif self.PanelMode == AlPanelMode.UNKNOWN or self.PanelMode == AlPanelMode.PROBLEM or self.PanelMode == AlPanelMode.STARTING or self.PanelMode == AlPanelMode.DOWNLOAD or self.PanelMode == AlPanelMode.STOPPED:
                # 
                log.debug(f"[handle_msgtypeF4]        PanelMode is {self.PanelMode} so not processing F4 data")
                self.image_ignore.add(zone)        # Prevent the user being able to ask for this zone again until we've cleared all the current data
                self.ignoreF4DataMessages = True   # Ignore 0x05 data packets
                self.ImageManager.terminateImage()

            elif zone - 1 in self.SensorList and self.SensorList[zone-1].getSensorType() == AlSensorType.CAMERA:
                log.debug(f"[handle_msgtypeF4]        Processing")
                # Here when PanelMode is MINIMAL_ONLY, STANDARD, STANDARD_PLUS, POWERLINK

                if self.PanelMode == AlPanelMode.MINIMAL_ONLY:
                    # Support externally requested images, from a real PowerLink Hardware device for example
                    if not self.ImageManager.isValidZone(zone):
                        self.ImageManager.create(zone, 11)   # This makes sure that there isn't an ongoing image retrieval for this sensor

                # Initialise the receipt of an image in the ImageManager
                self.ImageManager.setCurrent(zone = zone, unique_id = unique_id, image_id = image_id, size = size, sequence = sequence, lastimage = lastimage, totalimages = totalimages)

                if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD_PLUS, AlPanelMode.STANDARD]:
                    # Assume that we are managing the interaction/protocol with the panel
                    self.ignoreF4DataMessages = False

                    self._addMessageToSendList(Send.IMAGE_FB)
                    #self._addMessageToSendList(convertByteArray('0d ab 0e 00 17 1e 00 00 03 01 05 00 43 c5 0a')) # 43 should be bytearray([Packet.POWERLINK_TERMINAL])

                    # 0d f4 10 00 01 04 00 55 1e 01 f7 fc 0a
                    fnoseA = 0xF7
                    fnoseB = 0xFC

                    # Tell the panel we received that one OK, we're ready for the next 
                    #     --> *************************** THIS DOES NOT WORK ***************************
                    #         I assume because of fnoseA and fnoseB but I don't know what to set them to
                    if image_id == 0:   #   
                        id = data[14] - 1
                        self._addMessageToSendList(convertByteArray(f'0d f4 10 00 01 04 00 {zone:>02} {hexify(unique_id):>02} {hexify(id):>02} {hexify(fnoseA):>02} {hexify(fnoseB):>02} 0a'))
                    elif image_id >= 2:   #   image_id of 2 is the recorded sequence, I need to try this at 1
                        self._addMessageToSendList(convertByteArray(f'0d f4 10 00 01 04 00 {zone:>02} {hexify(unique_id):>02} {hexify(image_id - 1):>02} {hexify(fnoseA):>02} {hexify(fnoseB):>02} 0a'))

            else:
                log.debug(f"[handle_msgtypeF4]        Panel sending image for Zone {zone} but it does not exist or is not a CAMERA")

        elif msgtype == 0x05:   # JPG Data
            if self.ignoreF4DataMessages:
                log.debug(f"[handle_msgtypeF4]        Not processing F4 0x05 data")
            elif self.ImageManager.hasStartedSequence():
                # Image receipt has been initialised by self.ImageManager.setCurrent
                #     Therefore we only get here when PanelMode is MINIMAL_ONLY, STANDARD, STANDARD_PLUS, POWERLINK
                datastart = 4
                inSequence = self.ImageManager.addData(data[datastart:datastart+datalen], sequence)
                if inSequence:
                    if self.ImageManager.isImageComplete():
                        zone, unique_id, image_id, total_images, buffer, lastimage = self.ImageManager.getLastImageRecord()
                        log.debug(f"[handle_msgtypeF4]        Image Complete       Current Data     zone={zone}    unique_id={hex(unique_id)}    image_id={image_id}    total_images={total_images}    lastimage={lastimage}")
                        pushchange = True

                        #self._addMessageToSendList(Send.IMAGE_FB)

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
                        #fn = f"camera_image_z{zone:0>2}_{t.day:0>2}{t.month:0>2}{t.year - 2000:0>2}_{t.hour:0>2}{t.minute:0>2}{t.second:0>2}.jpg"
                        #with open(fn, 'wb') as f1:
                        #    f1.write(buffer)
                        #    f1.close()

                        if zone - 1 in self.SensorList and width <= 1024 and height <= 768:
                            log.debug(f"[handle_msgtypeF4]           Saving Image sensor {zone}   width {width}    height {height}")
                            self.SensorList[zone - 1].jpg_data = buffer
                            self.SensorList[zone - 1].jpg_time = t
                            self.SensorList[zone - 1].hasJPG = True
                            self.SensorList[zone - 1].pushChange(AlSensorCondition.CAMERA)

                        if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD]:
                            # Assume that we are managing the interaction/protocol with the panel
                            if total_images != 0xFF:
                                fnoseA = 0xF7
                                fnoseB = 0xFC
                                # Tell the panel we received that one OK, we're ready for the next
                                #                                         0d f4 07 00 01 04 55 1e 01 00 15 21 0a  
                                self._addMessageToSendList(convertByteArray(f'0d f4 07 00 01 04 {zone:>02} {hexify(unique_id):>02} {hexify(image_id):>02} 00 {hexify(fnoseA):>02} {hexify(fnoseB):>02} 0a'))

                            if lastimage:
                                fnoseA = 0xF7
                                fnoseB = 0xFC
                                # Tell the panel we received that one OK, we're ready for the next
                                self._addMessageToSendList(convertByteArray(f'0d f4 10 00 01 04 00 {zone:>02} {hexify(unique_id):>02} 00 {hexify(fnoseA):>02} {hexify(fnoseB):>02} 0a'))

                        if lastimage:
                            # Tell the panel we received that one OK, we're ready for the next
                            log.debug(f"[handle_msgtypeF4]         Finished everything so stopping as we've just received the last image")
                            self.ImageManager.terminateImage()

                else:
                    log.debug(f"[handle_msgtypeF4]         Message out of sequence, dumping all data")
                    self.ImageManager.terminateImage()

        elif msgtype == 0x01:
            log.debug(f"[handle_msgtypeF4]  data {toString(data)}")
            log.debug(f"[handle_msgtypeF4]           Message Type not processed")
            pushchange = True

        else:
            log.debug(f"[handle_msgtypeF4]  not seen data {toString(data)}")
            log.debug(f"[handle_msgtypeF4]           Message Type not processed")

        return pushchange

    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================
    # ================== Functions below this are utility functions to support the interface ================
    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================

    def _createPin(self, pin : str):
        # Pin is None when either we can perform the action without a code OR we're in Powerlink/StandardPlus and have the pin code to use
        # Other cases, the pin must be set
        if pin is None:
            bpin = self.getUserCode() # defaults to 0000
        elif len(pin) == 4:
            bpin = convertByteArray(pin[0:2] + " " + pin[2:4])
        else:
            # default to setting it to "0000" and see what happens when its sent to the panel
            bpin = bytearray([0,0])
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

    def getEventData(self, partition : int | None) -> dict:
        if partition is not None:
            if 1 <= partition <= 3:
                return self.PartitionState[partition-1].getEventData(False)
            return {}
        return self.PartitionState[0].getEventData(True)

    # A dictionary that is used to add to the attribute list of the Alarm Control Panel
    #     If this is overridden then please include the items in the dictionary defined here by using super()
    def getPanelStatusDict(self, partition : int | None = None, include_extended_status : bool = None) -> dict:
        """ Get a dictionary representing the panel status. """
        a = self.getEventData(partition)

        if partition is None or partition == 0:
            b = { TEXT_PROTOCOL_VERSION : PLUGIN_VERSION,
                  "emulationmode"    : str(self.PanelMode.name.lower()) }
            self.merge(a,b)

            f = self.getPanelFixedDict()
            self.merge(a,f)

            if self.ForceStandardMode:
                c = {
                    TEXT_WATCHDOG_TIMEOUT_TOTAL: self.WatchdogTimeoutCounter,
                    TEXT_WATCHDOG_TIMEOUT_DAY: self.WatchdogTimeoutPastDay
                }
            else:
                c = {
                    TEXT_WATCHDOG_TIMEOUT_TOTAL: self.WatchdogTimeoutCounter,
                    TEXT_WATCHDOG_TIMEOUT_DAY: self.WatchdogTimeoutPastDay,
                    TEXT_DOWNLOAD_TIMEOUT: self.DownloadCounter - 1 if self.DownloadCounter > 0 else 0,            # This is the number of download attempts and it would normally be 1 so subtract 1 off => the number of retries
                    TEXT_DL_MESSAGE_RETRIES: self.pmDownloadRetryCount                                       # This is for individual 3F download failures
                }
            self.merge(a,c)
            if include_extended_status and len(self.PanelStatus) > 0:
                # r = {**d, **self.PanelStatus}
                self.merge(a, self.PanelStatus)
            #if partition == 0:
            #    self.merge(a, { PE_PARTITION : partition } )

        elif self.getPartitionsInUse() is not None and len(self.getPartitionsInUse()) > 1:
            self.merge(a, { PE_PARTITION : partition } )

        #log.debug(f"[getPanelStatusDict]  returning {a}")
        return a

    def requestSensorBypassStateUpdate(self):
        if self.isPowerMaster():
            # Request the bypass status from the panel to update the sensors
            #     Instead of delaying the request, do it immediate
            #self.B0_Wanted.add(B0SubType.ZONE_BYPASS)
            s = self._create_B0_Data_Request(taglist = set([pmSendMsgB0[B0SubType.ZONE_BYPASS].data]))
            self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)
        else:
            # PowerMax
            # Request the bypass status from the panel to update the sensors
            self._addMessageToSendList(Send.BYPASSTAT, priority = MessagePriority.IMMEDIATE)
        
    # requestPanelCommand
    #       state is PanelCommand
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    def requestPanelCommand(self, state : AlPanelCommand, code : str = "", partitions : set = {1,2,3} ) -> AlCommandStatus:
        """ Send a request to the panel to Arm/Disarm """

        if not self.pmDownloadMode:
            if self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
                bpin = self._createPin(code)
                # Ensure that the state is valid
                if state in pmArmMode:
                    armCode = bytearray()
                    # Retrieve the code to send to the panel
                    armCode.append(pmArmMode[state])
                    if partitions is None:
                        partitions = {1,2,3} 
                    partition = 0
                    for i in partitions:
                        partition = partition | (1 << i-1)
                    if partition == 0:
                        partition = 1
                    self._addMessageToSendList(Send.ARM, priority = MessagePriority.IMMEDIATE, options=[ [3, armCode], [4, bpin], [6, partition] ])  #
                    self.fetchPanelStatus()
                    return AlCommandStatus.SUCCESS

                elif self.isPowerMaster():

                    if state == AlPanelCommand.MUTE:
                        self._addMessageToSendList(Send.MUTE_SIREN, priority = MessagePriority.IMMEDIATE, options=[ [4, bpin] ])  #
                        self.fetchPanelStatus()
                        return AlCommandStatus.SUCCESS

                    elif state == AlPanelCommand.TRIGGER:
                        self._addMessageToSendList(Send.PM_SIREN, priority = MessagePriority.IMMEDIATE, options=[ [4, bpin] ])  #
                        self.fetchPanelStatus()
                        return AlCommandStatus.SUCCESS

                    elif state in pmSirenMode:
                        sirenCode = bytearray()
                        # Retrieve the code to send to the panel
                        sirenCode.append(pmSirenMode[state])
                        self._addMessageToSendList(Send.PM_SIREN_MODE, priority = MessagePriority.IMMEDIATE, options=[ [4, bpin], [11, sirenCode] ])  #
                        self.fetchPanelStatus()
                        return AlCommandStatus.SUCCESS

            return AlCommandStatus.FAIL_INVALID_STATE
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    def setX10(self, device : int, state : AlX10Command) -> AlCommandStatus:
        # Send.X10PGM      : VisonicCommand(convertByteArray('A4 00 00 00 00 00 99 99 99 00 00 43'), None  , False, "X10 Data" ),
        #log.debug(f"[SendX10Command] Processing {device} {type(device)}")
        if not self.pmDownloadMode:
            if self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
                if device >= 0 and device <= 15:
                    log.debug(f"[SendX10Command]  Send X10 Command : id = {device}  state = {state}")
                    calc = 1 << device
                    byteA = calc & 0xFF
                    byteB = (calc >> 8) & 0xFF
                    if state in pmX10State:
                        what = pmX10State[state]
                        self._addMessageToSendList(Send.X10PGM, priority = MessagePriority.IMMEDIATE, options=[ [6, what], [7, byteA], [8, byteB] ])
                        self._addMessageToSendList(Send.STATUS_SEN, priority = MessagePriority.IMMEDIATE)
                        if self.isPowerMaster():
                            self.B0_Wanted.add(B0SubType.PANEL_STATE)        # 24
                        return AlCommandStatus.SUCCESS
                    return AlCommandStatus.FAIL_INVALID_STATE
                return AlCommandStatus.FAIL_ENTITY_INCORRECT
            return AlCommandStatus.FAIL_X10_PROBLEM
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    def getJPG(self, device : int, count : int) -> AlCommandStatus:
        if not self.pmDownloadMode:
            if self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
                if device - 1 in self.SensorList and self.SensorList[device-1].getSensorType() == AlSensorType.CAMERA:
                    if device not in self.image_ignore:
                        if self.ImageManager.create(device, count):   # This makes sure that there isn't an ongoing image retrieval for this sensor
                            self._addMessageToSendList(Send.GET_IMAGE, options=[ [1, count], [2, device] ])  #  
                            return AlCommandStatus.SUCCESS
                    return AlCommandStatus.FAIL_INVALID_STATE
                return AlCommandStatus.FAIL_ENTITY_INCORRECT
            return AlCommandStatus.FAIL_INVALID_STATE
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    def _createBypassB0Message(self, bypass : bool, zone_data : bytearray, pin : bytearray) -> bytearray:

        PM_Request_Data = convertByteArray("00 ff 01 03 08") + zone_data
        ll = len(PM_Request_Data) + 2

        PM_Request_Start = convertByteArray(f'b0 {"00" if bypass else "04"} 19') + bytearray([ll]) + pin
        PM_Request_End   = bytearray([Packet.POWERLINK_TERMINAL])

        PM_Data = PM_Request_Start + PM_Request_Data + PM_Request_End

        CS = self._calculateCRC(PM_Data)   # returns a bytearray with a single byte
        To_Send = bytearray([Packet.HEADER]) + PM_Data + CS + bytearray([Packet.FOOTER])

        log.debug(f"[_createBypassB0Message] Returning {toString(To_Send)}")
        return To_Send

    # Individually or as a set, arm/disarm the sensors
    #   This sets/clears the bypass for each sensor
    #       sensor is the zone number 1 to 31 or 1 to 64
    #       bypassValue is a boolean ( True then Bypass, False then Arm )
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink  (only used for PowerMax)
    #   Return : success or not
    #
    def setSensorBypassState(self, sensor : int | set, bypassValue : bool, pin : str = "") -> AlCommandStatus:
        """ Set or Clear Sensor Bypass """
        if not self.pmDownloadMode:
            if self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
                if self.PanelSettings[PanelSetting.PanelBypass] is not None and self.PanelSettings[PanelSetting.PanelBypass] != NOBYPASSSTR:

                    bypassint = 0
                    if isinstance(sensor, int) and (sensor - 1) in self.SensorList:
                        bypassint = 1 << (sensor - 1)
                    elif isinstance(sensor, set):
                        for s in sensor:
                            if (s - 1) in self.SensorList:
                                bypassint = bypassint | (1 << (s - 1))

                    if bypassint != 0: 
                        # There is something to do
                        if self.isPowerMaster():
                            #log.debug(f"[SensorArmState]  setSensorBypassState {hexify(bypassint)}")
                            y1, y2, y3, y4, y5, y6, y7, y8 = (bypassint & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")
                            bypass_data = bytearray([y1, y2, y3, y4, y5, y6, y7, y8])
                            log.debug(f"[SensorArmState]  setSensorBypassState data = {toString(bypass_data)}")

                            if len(bypass_data) == 8:
                                s = self._createBypassB0Message(bypassValue, bypass_data, convertByteArray(self.DownloadCode))
                                self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)
                                # Request the bypass status from the panel to update the sensors
                                #     Instead of delaying the request, do it immediate
                                #self.B0_Wanted.add(B0SubType.ZONE_BYPASS)
                                s = self._create_B0_Data_Request(taglist = set([pmSendMsgB0[B0SubType.ZONE_BYPASS].data]))
                                self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)
                                return AlCommandStatus.SUCCESS

                        else:
                            # PowerMax
                            # The MSG_BYPASSEN and MSG_BYPASSDI commands are the same i.e. command is A1
                            #      byte 0 is the command A1
                            #      bytes 1 and 2 are the pin
                            #      bytes 3 to 6 are the Enable bits for the 32 zones
                            #      bytes 7 to 10 are the Disable bits for the 32 zones
                            #      byte 11 is Packet.POWERLINK_TERMINAL
                            bpin = self._createPin(pin)
                            #log.debug(f"[SensorArmState]  setSensorBypassState {hexify(bypassint)}")
                            y1, y2, y3, y4 = (bypassint & 0xFFFFFFFF).to_bytes(4, "little")
                            bypass_data = bytearray([y1, y2, y3, y4])
                            log.debug(f"[SensorArmState]  setSensorBypassState data = {toString(bypass_data)}")

                            if len(bypass_data) == 4:
                                if bypassValue:
                                    self._addMessageToSendList(Send.BYPASSEN, priority = MessagePriority.IMMEDIATE, options=[ [1, bpin], [3, bypass_data] ])
                                else:
                                    self._addMessageToSendList(Send.BYPASSDI, priority = MessagePriority.IMMEDIATE, options=[ [1, bpin], [7, bypass_data] ])
                                # Request the bypass status from the panel to update the sensors
                                self._addMessageToSendList(Send.BYPASSTAT, priority = MessagePriority.IMMEDIATE)
                                return AlCommandStatus.SUCCESS
                        return AlCommandStatus.FAIL_INVALID_STATE

                    return AlCommandStatus.FAIL_ENTITY_INCORRECT

                return AlCommandStatus.FAIL_INVALID_STATE
            return AlCommandStatus.FAIL_PANEL_CONFIG_PREVENTED
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    # Get the Event Log
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    def getEventLog(self, pin : str = "") -> AlCommandStatus:
        """ Get Panel Event Log """
        if not self.pmDownloadMode:
            if self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
                log.debug("getEventLog")
                self.eventCount = 0
                #if self.isPowerMaster():
                #    self.B0_Wanted.add(B0SubType.EVENT_LOG)
                #else:
                bpin = self._createPin(pin)
                self._addMessageToSendList(Send.EVENTLOG, priority = MessagePriority.URGENT, options=[ [4, bpin] ])
                return AlCommandStatus.SUCCESS
            return AlCommandStatus.FAIL_INVALID_STATE
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

# Turn on auto code formatting when using black
# fmt: on
