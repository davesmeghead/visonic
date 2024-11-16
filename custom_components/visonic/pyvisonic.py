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
#    PanelType=0 : PowerMax , Model=21   Powermaster False  <<== THIS DOES NOT WORK (NO POWERLINK SUPPORT and only supports EEPROM download i.e no sensor data) ==>>
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
from enum import IntEnum
from string import punctuation
from textwrap import wrap


try:
    from .pyconst import (AlTransport, AlPanelDataStream, NO_DELAY_SET, PanelConfig, AlConfiguration, AlPanelMode, AlPanelCommand, AlTroubleType, AlPanelEventData, 
                          AlAlarmType, AlPanelStatus, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlLogPanelEvent, AlSensorType, AlTerminationType, PE_PARTITION)
    from .pyhelper import (toString, MyChecksumCalc, AlImageManager, ImageRecord, titlecase, AlPanelInterfaceHelper, 
                           AlSensorDeviceHelper, AlSwitchDeviceHelper)
except:
    from pyconst import (AlTransport, AlPanelDataStream, NO_DELAY_SET, PanelConfig, AlConfiguration, AlPanelMode, AlPanelCommand, AlTroubleType, AlPanelEventData,
                         AlAlarmType, AlPanelStatus, AlSensorCondition, AlCommandStatus, AlX10Command, AlCondition, AlLogPanelEvent, AlSensorType, AlTerminationType, PE_PARTITION)
    from pyhelper import (toString, MyChecksumCalc, AlImageManager, ImageRecord, titlecase, AlPanelInterfaceHelper, 
                          AlSensorDeviceHelper, AlSwitchDeviceHelper)

PLUGIN_VERSION = "1.7.1.0"

# Obfuscate sensitive data, regardless of the other Debug settings.
#     Setting this to True limits the logging of messages sent to the panel to CMD or NONE
OBFUS  = True  # Obfuscate sensitive data

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
DEFAULT_DL_CODE = "56 50"

# Number of seconds delay between trying to achieve EEPROM download
DOWNLOAD_RETRY_DELAY = 60

# Number of times to retry the download, this is a total
DOWNLOAD_RETRY_COUNT = 10

# Number of times to retry the retrieval of a block to download, this is a total across all blocks to download and not each block
DOWNLOAD_PDU_RETRY_COUNT = 30

# Number of seconds delay between trying to achieve powerlink (must have achieved download first)
POWERLINK_RETRY_DELAY = 180

# Number of seconds delay between not getting I'm alive messages from the panel in Powerlink Mode
POWERLINK_IMALIVE_RETRY_DELAY = 100

# Maximum number of seconds between the panel sending I'm alive messages
MAX_TIME_BETWEEN_POWERLINK_ALIVE = 60

# Number of seconds between trying to achieve powerlink (must have achieved download first) and giving up. Better to be half way between retry delays
POWERLINK_TIMEOUT = 4.5 * POWERLINK_RETRY_DELAY

# This is the minimum time interval (in milli seconds) between sending subsequent messages to the panel so the panel has time to process them. 
#    This value is based on the slowest supported panel
MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMAX = 150
MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMASTER = 130

# The number of seconds that if we have not received any data packets from the panel at all (from the start) then suspend this plugin and report to HA
#    This is only used when no data at all has been received from the panel ... ever
NO_RECEIVE_DATA_TIMEOUT = 30

# The number of seconds between receiving data from the panel and then no communication (the panel has stopped sending data for this period of time) then suspend this plugin and report to HA
#    This is used when this integration has received data and then stopped receiving data
LAST_RECEIVE_DATA_TIMEOUT = 240  # 4 minutes

# Whether to download all the EEPROM from the panel or to just download the parts that we gete usable data from
EEPROM_DOWNLOAD_ALL = False

# Interval (in seconds) to get the time and for most panels to try and set it if it's out by more than TIME_INTERVAL_ERROR seconds
#     PowerMaster uses time interval for checking motion triggers so more critical to keep it updated
POWERMASTER_CHECK_TIME_INTERVAL =   300  # 5 minutes  (this uses B0 messages and not DOWNLOAD panel state)
POWERMAX_CHECK_TIME_INTERVAL    = 14400  # 4 hours    (this uses the DOWNLOAD panel state
TIME_INTERVAL_ERROR = 3

# Message/Packet Constants to make the code easier to read
PACKET_HEADER = 0x0D
PACKET_FOOTER = 0x0A
PACKET_MAX_SIZE = 0xF0
ACK_MESSAGE = 0x02
REDIRECT = 0xC0
VISPROX  = 0xE0

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


class PriorityQueueWithPeek(asyncio.PriorityQueue):
    
    def peek_nowait(self):
        #log.debug(f"[peek_nowait]  entry")
        t = self._queue[0]     # PriorityQueue is an ordered list so look at the head of the list
        #log.debug(f"[peek_nowait]  The head of the queue is {t}")
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
   16 : "PowerMaster 360R"
}

# Config for each panel type (0-16).  
#     Assume 360R is Panel 16 for this release as it was released after the PM33, also I've an old log file from a user that indicates this
#               So make column 16 the same as column 13
#     Don't know what 9, 11, 12 or 14 are so just copy other settings. I know that there are Commercial/Industry Panel versions so it might be them
#     This data defines each panel type's maximum capability
#     I know that panel types 4 and 5 support 3 partitions but I can't figure out how they are represented in A5 and A7 messages, so partitions only supported for PowerMaster and B0 messages
pmPanelConfig = {    #      0       1       2       3       4       5       6       7       8       9      10      11      12      13      14      15      16      See pmPanelType above
   "CFG_SUPPORTED"     : (  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True ), # Supported Panels i.e. not a PowerMax
   "CFG_KEEPALIVE"     : (      0,     25,     25,     25,     25,     25,     25,     25,     25,     25,     25,     25,     25,     15,     25,     25,     15 ), # Keep Alive message interval if no other messages sent
   "CFG_DLCODE_1"      : (     "", "5650", "5650", "5650", "5650", "5650", "5650", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA" ), # Default download codes (for reset panels or panels that have not been changed)
   "CFG_DLCODE_2"      : (     "", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "5650", "5650", "5650", "5650", "5650", "5650", "5650", "5650", "5650", "5650" ), # Alternative 1 (Master) known default download codes
   "CFG_DLCODE_3"      : (     "", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB" ), # Alternative 2 (Master) known default download codes
   "CFG_PARTITIONS"    : (      0,      1,      1,      1,      1,      1,      1,      3,      3,      3,      3,      3,      3,      3,      3,      3,      3 ), 
   "CFG_EVENTS"        : (      0,    250,    250,    250,    250,    250,    250,    250,   1000,   1000,   1000,   1000,   1000,   1000,   1000,   1000,   1000 ),
   "CFG_KEYFOBS"       : (      0,      8,      8,      8,      8,      8,      8,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32 ),
   "CFG_1WKEYPADS"     : (      0,      8,      8,      8,      8,      8,      8,      0,      0,      0,      0,      0,      0,      0,      0,      0,      0 ),
   "CFG_2WKEYPADS"     : (      0,      2,      2,      2,      2,      2,      2,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32 ),
   "CFG_SIRENS"        : (      0,      2,      2,      2,      2,      2,      2,      4,      8,      8,      8,      8,      8,      8,      8,      8,      8 ),
   "CFG_USERCODES"     : (      0,      8,      8,      8,      8,      8,      8,      8,     48,     48,     48,     48,     48,     48,     48,     48,     48 ),
   "CFG_REPEATERS"     : (      0,      0,      0,      0,      0,      0,      0,      4,      4,      4,      4,      4,      4,      4,      4,      4,      4 ),
   "CFG_PROXTAGS"      : (      0,      0,      8,      0,      8,      8,      0,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32 ),
   "CFG_ZONECUSTOM"    : (      0,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5 ),
   "CFG_WIRELESS"      : (      0,     28,     28,     28,     28,     28,     29,     29,     62,     62,     62,     62,     62,     64,     62,     62,     64 ), # Wireless + Wired total 30 or 64
   "CFG_WIRED"         : (      0,      2,      2,      2,      2,      2,      1,      1,      2,      2,      2,      2,      2,      0,      2,      2,      0 ),
   "CFG_X10"           : (      0,     15,     15,     15,     15,     15,     15,      0,      0,      0,      0,      0,      0,      0,      0,      0,      0 ), # Supported X10 devices
   "CFG_AUTO_ENROLL"   : (  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False,   True,   True,  False ), # 360 and 360R cannot autoenroll to Powerlink
   "CFG_AUTO_SYNCTIME" : (  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True ), # Assume 360 and 360R can auto sync time
   "CFG_POWERMASTER"   : (  False,  False,  False,  False,  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True ), # Panels that use and respond to the additional PowerMaster Messages
   "CFG_INIT_SUPPORT"  : (  False,  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False,   True,   True,  False )  # Panels that support the INIT command
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
pmSendMsg = {
   # Quick command codes to start and stop download/powerlink are a single value                                      
   "MSG_BUMP"         : VisonicCommand(convertByteArray('09')                                          , [0x3C] , False, False,      SendDebugM, 0.5, "Bump Panel Data From Panel" ),  # Bump to try to get the panel to send a 3C
   "MSG_START"        : VisonicCommand(convertByteArray('0A')                                          , [0x0B] , False, False,      SendDebugM, 0.0, "Start" ),                          # waiting for STOP from panel for download complete
   "MSG_STOP"         : VisonicCommand(convertByteArray('0B')                                          , None   , False, False,      SendDebugM, 1.5, "Stop" ),     #
   "MSG_EXIT"         : VisonicCommand(convertByteArray('0F')                                          , None   , False, False,      SendDebugM, 1.5, "Exit" ),
                                                                                              
   # Command codes do not have the 0x43 on the end and are only 11 values                                          
   "MSG_DOWNLOAD_DL"  : VisonicCommand(convertByteArray('24 00 00 99 99 00 00 00 00 00 00')            , None   , False,  True,      SendDebugD, 0.0, "Start Download Mode" ),            # This gets either an acknowledge OR an Access Denied response
   "MSG_DOWNLOAD_TIME": VisonicCommand(convertByteArray('24 00 00 99 99 00 00 00 00 00 00')            , None   , False, False,      SendDebugD, 0.5, "Trigger Panel To Set Time" ),      # Use this instead of BUMP as can be used by all panels. To set time.
   "MSG_DOWNLOAD_3C"  : VisonicCommand(convertByteArray('24 00 00 99 99 00 00 00 00 00 00')            , [0x3C] , False, False,      SendDebugD, 0.5, "Trigger Panel Data From Panel" ),  # Use this instead of BUMP as can be used by all panels
   "MSG_WRITE"        : VisonicCommand(convertByteArray('3D 00 00 00 00 00 00 00 00 00 00')            , None   , False, False,      SendDebugD, 0.0, "Write Data Set" ),
   "MSG_DL"           : VisonicCommand(convertByteArray('3E 00 00 00 00 B0 00 00 00 00 00')            , [0x3F] ,  True, False,      SendDebugD, 0.0, "Download Data Set" ),
   "MSG_SETTIME"      : VisonicCommand(convertByteArray('46 F8 00 01 02 03 04 05 06 FF FF')            , None   , False, False,      SendDebugM, 1.0, "Setting Time" ),                   # may not need an ack so I don't wait for 1 and just get on with it
   "MSG_SER_TYPE"     : VisonicCommand(convertByteArray('5A 30 04 01 00 00 00 00 00 00 00')            , [0x33] , False, False,      SendDebugM, 0.0, "Get Serial Type" ),
                                                                                              
   "MSG_EVENTLOG"     : VisonicCommand(convertByteArray('A0 00 00 00 99 99 00 00 00 00 00 43')         , [0xA0] , False, False,      SendDebugC, 0.0, "Retrieving Event Log" ),
   "MSG_ARM"          : VisonicCommand(convertByteArray('A1 00 00 99 99 99 07 00 00 00 00 43')         , None   ,  True, False,      SendDebugC, 0.0, "(Dis)Arming System" ),             # Including 07 to arm all 3 partitions
   "MSG_MUTE_SIREN"   : VisonicCommand(convertByteArray('A1 00 00 0B 99 99 00 00 00 00 00 43')         , None   ,  True, False,      SendDebugC, 0.0, "Mute Siren" ),                     #
   "MSG_STATUS"       : VisonicCommand(convertByteArray('A2 00 00 3F 00 00 00 00 00 00 00 43')         , [0xA5] ,  True, False,      SendDebugM, 0.0, "Getting Status" ),                 # Ask for A5 messages, the 0x3F asks for 01 02 03 04 05 06 messages
   "MSG_STATUS_SEN"   : VisonicCommand(convertByteArray('A2 00 00 08 00 00 00 00 00 00 00 43')         , [0xA5] ,  True, False,      SendDebugM, 0.0, "Getting A5 04 Status" ),           # Ask for A5 messages, the 0x08 asks for 04 message only
   "MSG_BYPASSTAT"    : VisonicCommand(convertByteArray('A2 00 00 20 00 00 00 00 00 00 00 43')         , [0xA5] , False, False,      SendDebugC, 0.0, "Get Bypass and Enrolled Status" ), # Ask for A5 06 message (Enrolled and Bypass Status)
   "MSG_ZONENAME"     : VisonicCommand(convertByteArray('A3 00 00 00 00 00 00 00 00 00 00 43')         , [0xA3] ,  True, False,      SendDebugM, 0.0, "Requesting Zone Names" ),          # We expect 4 or 8 (64 zones) A3 messages back but at least get 1
   "MSG_X10PGM"       : VisonicCommand(convertByteArray('A4 00 00 00 00 00 99 99 99 00 00 43')         , None   , False, False,      SendDebugM, 0.0, "X10 Data" ),                       # Retrieve X10 data
   "MSG_ZONETYPE"     : VisonicCommand(convertByteArray('A6 00 00 00 00 00 00 00 00 00 00 43')         , [0xA6] ,  True, False,      SendDebugM, 0.0, "Requesting Zone Types" ),          # We expect 4 or 8 (64 zones) A6 messages back but at least get 1
                                                                                              
   "MSG_BYPASSEN"     : VisonicCommand(convertByteArray('AA 99 99 12 34 56 78 00 00 00 00 43')         , None   , False, False,      SendDebugM, 0.0, "BYPASS Enable" ),                  # Bypass sensors
   "MSG_BYPASSDI"     : VisonicCommand(convertByteArray('AA 99 99 00 00 00 00 12 34 56 78 43')         , None   , False, False,      SendDebugM, 0.0, "BYPASS Disable" ),                 # Arm Sensors (cancel bypass)
                                                                                              
   "MSG_GETTIME"      : VisonicCommand(convertByteArray('AB 01 00 00 00 00 00 00 00 00 00 43')         , [0xAB] ,  True, False,      SendDebugM, 0.0, "Get Panel Time" ),                 # Returns with an AB 01 message back
   "MSG_ALIVE"        : VisonicCommand(convertByteArray('AB 03 00 00 00 00 00 00 00 00 00 43')         , None   ,  True, False,      SendDebugM, 0.0, "I'm Alive Message To Panel" ),
   "MSG_RESTORE"      : VisonicCommand(convertByteArray('AB 06 00 00 00 00 00 00 00 00 00 43')         , None   ,  True, False,      SendDebugM, 0.0, "Restore Connection" ),             # It can take multiple of these to put the panel back in to powerlink
   "MSG_ENROLL"       : VisonicCommand(convertByteArray('AB 0A 00 00 99 99 00 00 00 00 00 43')         , None   ,  True, False,      SendDebugM, 2.5, "Auto-Enroll PowerMax/Master" ),    # should get a reply of [0xAB] but its not guaranteed
   "MSG_NO_IDEA"      : VisonicCommand(convertByteArray('AB 0E 00 17 1E 00 00 03 01 05 00 43')         , None   ,  True, False,      SendDebugM, 0.0, "PowerMaster after jpg feedback" ), # 
   "MSG_INIT"         : VisonicCommand(convertByteArray('AB 0A 00 01 00 00 00 00 00 00 00 43')         , None   ,  True, False,      SendDebugM, 3.0, "Init PowerLink Connection" ),
                                                                                              
   "MSG_X10NAMES"     : VisonicCommand(convertByteArray('AC 00 00 00 00 00 00 00 00 00 00 43')         , [0xAC] , False, False,      SendDebugM, 0.0, "Requesting X10 Names" ),
   "MSG_GET_IMAGE"    : VisonicCommand(convertByteArray('AD 99 99 0A FF FF 00 00 00 00 00 43')         , [0xAD] ,  True, False,      SendDebugI, 0.0, "Requesting JPG Image" ),           # The first 99 might be the number of images. Request a jpg image, second 99 is the zone.  
                                                                                              
   # Acknowledges                                                                             
   "MSG_ACK"          : VisonicCommand(convertByteArray('02')                                          , None   , False, False, DebugLevel.NONE, 0.0, "Ack" ),
   "MSG_ACK_PLINK"    : VisonicCommand(convertByteArray('02 43')                                       , None   , False, False, DebugLevel.NONE, 0.0, "Ack Powerlink" ),
                                                                                              
   # PowerMaster specific                                                                     
   "MSG_PM_REQUEST"   : VisonicCommand(convertByteArray('B0 01 99 01 05 43')                           , [0xB0] ,  True, False,      SendDebugM, 0.0, "Powermaster Request Type 1" ),       # Request a message type from the panel, change 99 with the message type
   "MSG_PM_REQUEST54" : VisonicCommand(convertByteArray('B0 01 54 00 43')                              , [0xB0] ,  True, False,      SendDebugM, 0.0, "Powermaster Request a 54" ),         # Request a 54 message type from the panel
   "MSG_PM_REQUEST58" : VisonicCommand(convertByteArray('B0 01 58 00 43')                              , [0xB0] ,  True, False,      SendDebugM, 0.0, "Powermaster Request a 58" ),         # Request a 58 message type from the panel

   "MSG_PM_SIREN_MODE": VisonicCommand(convertByteArray('B0 00 47 09 99 99 00 FF 08 0C 02 99 07 43')   , None   ,  True, False,      SendDebugM, 0.0, "Powermaster Trigger Siren Mode" ),   # Trigger Siren, the 99 99 needs to be the usercode, other 99 is Siren Type
   "MSG_PM_SIREN"     : VisonicCommand(convertByteArray('B0 00 3E 0A 99 99 05 FF 08 02 03 00 00 01 43'), None   ,  True, False,      SendDebugM, 0.0, "Powermaster Trigger Siren" ),        # Trigger Siren, the 99 99 needs to be the usercode
   "MSG_PL_BRIDGE"    : VisonicCommand(convertByteArray('E1 99 99 43')                                 , None   , False, False,      SendDebugM, 0.0, "Powerlink Bridge" ),                 # Command to the Bridge

#   "MSG_PM_SETBAUD"   : VisonicCommand(convertByteArray('B0 00 41 0D AA AA 01 FF 28 0C 05 01 00 BB BB 00 05 43'), None   ,  True, False,   CMD, 2.5, "Powermaster Set Serial Baud Rate" ),
                                                                                              
   # Not sure what these do to the panel. Panel replies with powerlink ack 0x43               
#   "MSG4"             : VisonicCommand(convertByteArray('04 43')                                       , None   , False, False,      SendDebugM, 0.0, "Message 04 43. Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
#   "MSGC"             : VisonicCommand(convertByteArray('0C 43')                                       , None   , False, False,      SendDebugM, 0.0, "Message 0C 43. Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
#   "MSG_UNKNOWN_0E"   : VisonicCommand(convertByteArray('0E')                                          , None   , False, False,      SendDebugM, 0.0, "Message 0E.    Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
#   "MSGE"             : VisonicCommand(convertByteArray('0E 43')                                       , None   , False, False,      SendDebugM, 0.0, "Message 0E 43. Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
}


# B0 Messages subset that we can send to a Powermaster, embed within MSG_POWERMASTER to use
B0_SendMessageTuple = collections.namedtuple('B0_SendMessageTuple', 'data chunky paged')
pmSendMsgB0 = {
   "WIRELESS_DEVICES_00"   : B0_SendMessageTuple(0x00,  True, False),      # 
   "WIRELESS_DEVICES_01"   : B0_SendMessageTuple(0x01,  True, False),      # 
   "WIRELESS_DEVICES_UPD"  : B0_SendMessageTuple(0x02,  True, False),      # 
   "WIRELESS_DEVICES_CH"   : B0_SendMessageTuple(0x04,  True, False),      # 
   "WIRELESS_DEVICES_05"   : B0_SendMessageTuple(0x05,  True, False),      # 
   "WIRELESS_DEVICES_08"   : B0_SendMessageTuple(0x08,  True, False),      # 
   "WIRELESS_DEVICES_09"   : B0_SendMessageTuple(0x09,  True, False),      # 
   "WIRELESS_DEVICES_0C"   : B0_SendMessageTuple(0x0C,  True, False),      # 
   "WIRELESS_DEVICES_0D"   : B0_SendMessageTuple(0x0D,  True, False),      # 
   "WIRELESS_DEVICES_0E"   : B0_SendMessageTuple(0x0E,  True, False),      # 
   "INVALID_COMMAND"       : B0_SendMessageTuple(0x06, False, False),      # This isn't chunked  INVALID_COMMAND
   "ZONE_STAT07"           : B0_SendMessageTuple(0x07,  True, False),
   "TAMPER_ACTIVITY"       : B0_SendMessageTuple(0x0A,  True, False),      # Mark: Tamper Activities
   "TAMPER_ALERT"          : B0_SendMessageTuple(0x0B,  True, False),      # Mark: Tamper Alert
   "ZONE_STAT0F"           : B0_SendMessageTuple(0x0F, False, False),
   
   "ZONE_STAT10"           : B0_SendMessageTuple(0x10,  True, False),
   "ZONE_STAT11"           : B0_SendMessageTuple(0x11,  True, False),
   "ZONE_STAT12"           : B0_SendMessageTuple(0x12,  True, False),
   "TRIGGERED_ZONE"        : B0_SendMessageTuple(0x13,  True, False),
   "ZONE_STAT14"           : B0_SendMessageTuple(0x14,  True, False),
   "ZONE_STAT15"           : B0_SendMessageTuple(0x15,  True, False),
   "ZONE_STAT16"           : B0_SendMessageTuple(0x16,  True, False),
   "ZONE_STAT17"           : B0_SendMessageTuple(0x17,  True, False),
   "ZONE_OPENCLOSE"        : B0_SendMessageTuple(0x18,  True, False),      # Sensor Open/Close State
   "ZONE_BYPASS"           : B0_SendMessageTuple(0x19,  True, False),      # Sensor Bypass
   "ZONE_STAT1A"           : B0_SendMessageTuple(0x1A,  True, False),
   "ZONE_STAT1B"           : B0_SendMessageTuple(0x1B,  True, False),
   "ZONE_STAT1C"           : B0_SendMessageTuple(0x1C,  True, False),
   "SENSOR_ENROL"          : B0_SendMessageTuple(0x1D,  True, False),      # Sensors Enrolment
   "ZONE_STAT1E"           : B0_SendMessageTuple(0x1E,  True, False),
   "DEVICE_TYPES"          : B0_SendMessageTuple(0x1F,  True, False),      # Sensors

   "ASSIGNED_PARTITION"    : B0_SendMessageTuple(0x20,  True,  True),
   "ZONE_NAMES"            : B0_SendMessageTuple(0x21,  True, False),      # Zone Names
   "SYSTEM_CAPABILITIES"   : B0_SendMessageTuple(0x22,  True, False),      # System
   "ZONE_STAT23"           : B0_SendMessageTuple(0x23,  True, False),
   "PANEL_STATE"           : B0_SendMessageTuple(0x24,  True, False),      # Panel State
   "ZONE_STAT25"           : B0_SendMessageTuple(0x25,  True, False),
   "ZONE_STAT26"           : B0_SendMessageTuple(0x26,  True, False),      # INVALID
   "WIRED_STATUS"          : B0_SendMessageTuple(0x27,  True, False),
   "WIRED_STATUS_2"        : B0_SendMessageTuple(0x28,  True, False),
   "ZONE_STAT29"           : B0_SendMessageTuple(0x29,  True, False),
   "EVENT_LOG"             : B0_SendMessageTuple(0x2A,  True,  True),      # Event Log
   "ZONE_STAT2B"           : B0_SendMessageTuple(0x2B,  True, False),
   "ZONE_STAT2C"           : B0_SendMessageTuple(0x2C,  True, False),      # INVALID
   "ZONE_TYPES"            : B0_SendMessageTuple(0x2D,  True, False),      # Zone Types
   "ZONE_STAT2E"           : B0_SendMessageTuple(0x2E,  True, False),
   "ZONE_STAT2F"           : B0_SendMessageTuple(0x2F,  True, False),

   "ZONE_STAT30"           : B0_SendMessageTuple(0x30,  True, False),
   "ZONE_STAT31"           : B0_SendMessageTuple(0x31,  True, False),
   "ZONE_STAT32"           : B0_SendMessageTuple(0x32,  True, False),
   "ZONE_STAT33"           : B0_SendMessageTuple(0x33,  True, False),
   "ZONE_STAT34"           : B0_SendMessageTuple(0x34,  True, False),
   "PANEL_SETTINGS_35"     : B0_SendMessageTuple(0x35,  True, False),
   "LEGACY_EVENT_LOG"      : B0_SendMessageTuple(0x36,  True, False),
   "ZONE_STAT37"           : B0_SendMessageTuple(0x37,  True, False),
   "ZONE_STAT38"           : B0_SendMessageTuple(0x38,  True, False),
   "ASK_ME_1"              : B0_SendMessageTuple(0x39,  True, False),      # Panel sending a list of message types that may have updated info
   "ZONE_STAT3A"           : B0_SendMessageTuple(0x3A,  True, False),
   "ZONE_STAT3B"           : B0_SendMessageTuple(0x3B,  True, False),      # INVALID
   "ZONE_STAT3C"           : B0_SendMessageTuple(0x3C,  True, False),      # INVALID
   "ZONE_TEMPS"            : B0_SendMessageTuple(0x3D,  True, False),      # Zone Temperatures
   "ZONE_STAT3E"           : B0_SendMessageTuple(0x3E,  True, False),
   "ZONE_STAT3F"           : B0_SendMessageTuple(0x3F,  True, False),

   "WIRELESS_DEVICES_40"   : B0_SendMessageTuple(0x40,  True, False),
   "ZONE_STAT41"           : B0_SendMessageTuple(0x41,  True, False),      # 
   "PANEL_SETTINGS_42"     : B0_SendMessageTuple(0x42,  True, False),
   "ZONE_STAT43"           : B0_SendMessageTuple(0x43,  True, False),      # 
   "ZONE_STAT44"           : B0_SendMessageTuple(0x44,  True, False),      # INVALID
   "ZONE_STAT45"           : B0_SendMessageTuple(0x45,  True, False),      # INVALID
   "ZONE_STAT46"           : B0_SendMessageTuple(0x46,  True, False),      # INVALID
   "ZONE_STAT47"           : B0_SendMessageTuple(0x47,  True, False),      # INVALID
   "ZONE_STAT48"           : B0_SendMessageTuple(0x48,  True, False),      # 
   "ZONE_STAT49"           : B0_SendMessageTuple(0x49,  True, False),      # 
   "ZONE_STAT4A"           : B0_SendMessageTuple(0x4A,  True, False),      # 

   "ZONE_LAST_EVENT"       : B0_SendMessageTuple(0x4B,  True,  True),      # Zone Last Event. Paged for more than 30 sensors.
   "ZONE_STAT4E"           : B0_SendMessageTuple(0x4E,  True, False),
   "ZONE_STAT4F"           : B0_SendMessageTuple(0x4F,  True, False),
   
   "ZONE_STAT50"           : B0_SendMessageTuple(0x50,  True, False),
   "ASK_ME_2"              : B0_SendMessageTuple(0x51,  True, False),      # Panel sending a list of message types that may have updated info
   "ZONE_STAT52"           : B0_SendMessageTuple(0x52,  True, False),      # Mark: Device Counts
   "ZONE_STAT54"           : B0_SendMessageTuple(0x54,  True, False),      # Mark: Sensor Troubles
   "ZONE_STAT56"           : B0_SendMessageTuple(0x56,  True, False),
   "ZONE_STAT58"           : B0_SendMessageTuple(0x58,  True, False),
   "ZONE_LUX"              : B0_SendMessageTuple(0x77,  True, False),      # Zone Luminance / lux.  Tried asking for this and didn't get it on my PM10.
}

pmSendMsgB0_reverseLookup = { v.data : B0_SendMessageTuple(k, v.chunky, v.paged) for k,v in pmSendMsgB0.items() }

SUB = 2
MAIN = 3
BITS = 1
BYTES = 8
WORDS = 16

class IndexName(IntEnum):
    """Index name.

    This came from b0 35 51 01 on Powermater-10
    """
    REPEATERS = 0
    PANIC_BUTTONS = 1
    SIRENS = 2
    ZONES = 3
    KEYPADS = 4
    KEYFOBS = 5
    USERS = 6
    X10_DEVICES = 7
    GSM_MODULES = 8
    POWERLINK = 9
    TAGS = 10
    PGM = 11
    PANEL = 12
    GUARDS = 13
    EVENTS = 14
    PARTITIONS = 15
    UNK16 = 16
    EXPANDER_33 = 17
    IOV = 18
    UNK19 = 19
    UNK20 = 20
    MIXED = 255

pmDataIndexes = {
            0: "Repeaters",
            1: "X10",        # Could be Panic Buttons
            2: "Sirens",
            3: "Zones",
            4: "Keypads",
            5: "Keyfobs",
            6: "Usercodes",
           10: "Proxtags",
           12: "Panel",
           13: "Powerlink",
           14: "Partitions", # or maybe 15
           17: "Events"      # or maybe 14
}

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
#    When length is 0 then we stop processing the message on the first PACKET_FOOTER. This is only used for the short messages (4 or 5 bytes long) like ack, stop, denied and timeout
PanelCallBack = collections.namedtuple("PanelCallBack", 'length ackneeded isvariablelength varlenbytepos flexiblelength ignorechecksum debugprint msg' )
pmReceiveMsg = {
   0x00     : PanelCallBack(  0,  True, False, -1, 0, False, DebugLevel.NONE,                          "Dummy Message" ),       # Dummy message used in the algorithm when the message type is unknown. The -1 is used to indicate an unknown message in the algorithm
   0x02     : PanelCallBack(  0, False, False,  0, 0, False, DebugLevel.NONE,                          "Acknowledge" ),         # Ack
   0x06     : PanelCallBack(  0,  True, False,  0, 0, False,      RecvDebugC,                          "Timeout" ),             # Timeout. See the receiver function for ACK handling
   0x07     : PanelCallBack(  0,  True, False,  0, 0, False,      RecvDebugC,                          "Unknowm 07" ),          # No idea what this means but decode it anyway
   0x08     : PanelCallBack(  0,  True, False,  0, 0, False,      RecvDebugC,                          "Access Denied" ),       # Access Denied
   0x0B     : PanelCallBack(  0, False, False,  0, 0, False, DebugLevel.FULL,                          "Loopback Test" ),       # THE PANEL DOES NOT SEND THIS. THIS IS USED FOR A LOOP BACK TEST
   0x0F     : PanelCallBack(  0,  True, False,  0, 0, False,      RecvDebugC,                          "Exit Download" ),       # The panel may send this during download to tell us to exit download 
   0x22     : PanelCallBack( 14,  True, False,  0, 0, False, DebugLevel.FULL,                          "Not Used" ),            # 14 Panel Info (older visonic powermax panels so not used by this integration)
   0x25     : PanelCallBack( 14,  True, False,  0, 0, False, DebugLevel.CMD  if OBFUS else RecvDebugD, "Download Retry" ),      # 14 Download Retry
   0x33     : PanelCallBack( 14,  True, False,  0, 0, False, DebugLevel.NONE if OBFUS else RecvDebugD, "Download Settings" ),   # 14 Download Settings
   0x3C     : PanelCallBack( 14,  True, False,  0, 0, False, DebugLevel.FULL,                          "Panel Info" ),          # 14 Panel Info
   0x3F     : PanelCallBack(  7,  True,  True,  4, 5, False, DebugLevel.CMD  if OBFUS else RecvDebugD, "Download Block" ),      # Download Info in varying lengths  (For variable length, the length is the fixed number of bytes). This contains panel data so don't log it.
   0xA0     : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugM,                          "Event Log (A0)" ),      # 15 Event Log
   0xA3     : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugM,                          "Zone Names (A3)" ),     # 15 Zone Names
   0xA5     : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugM,                          "Status Update (A5)" ),  # 15 Status Update       Length was 15 but panel seems to send different lengths
   0xA6     : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugM,                          "Zone types (A6)" ),     # 15 Zone Types
   0xA7     : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugM,                          "Panel Status (A7)" ),   # 15 Panel Status Change
   0xAB     : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugC,                          "Powerlink (AB)" ),      # 15 Enroll Request 0x0A  OR Ping 0x03      Length was 15 but panel seems to send different lengths
   0xAC     : PanelCallBack( 15,  True, False,  0, 0, False,      RecvDebugC,                          "X10 Names" ),           # 15 X10 Names
   0xAD     : PanelCallBack( 15,  True, False,  0, 0, False, DebugLevel.CMD  if OBFUS else RecvDebugI, "JPG Mgmt" ),            # 15 Panel responds with this when we ask for JPG images
   0xB0     : PanelCallBack(  8,  True,  True,  4, 2, False, DebugLevel.CMD  if OBFUS else RecvDebugM, "PowerMaster (B0)" ),    # The B0 message comes in varying lengths, sometimes it is shorter than what it states and the CRC is sometimes wrong
   REDIRECT : PanelCallBack(  5, False,  True,  2, 0, False, DebugLevel.FULL,                          "Redirect" ),            # TESTING: These are redirected Powerlink messages. 0D C0 len <data> cs 0A   so 5 plus the original data length
   VISPROX  : PanelCallBack( 11,  True, False,  0, 0, False, DebugLevel.FULL,                          "Proxy" ),               # VISPROX : Interaction with Visonic Proxy
   # The F1 message needs to be ignored, I have no idea what it is but the crc is always wrong and only Powermax+ panels seem to send it. Assume a minimum length of 9, a variable length and ignore the checksum calculation.
   0xF1     : PanelCallBack(  9,  True,  True,  0, 0,  True,      RecvDebugC,                          "Unknown F1" ),          # Ignore checksum on all F1 messages
   # The F4 message comes in varying lengths. It is the image data from a PIR camera. Ignore checksum on all F4 messages
   0xF4 : { 0x01 : PanelCallBack(  9, False, False,  0, 0,  True, RecvDebugI,                          "Image Footer" ),        # 
            0x03 : PanelCallBack(  9, False,  True,  5, 0,  True, RecvDebugI,                          "Image Header" ),        # Image Header
            0x05 : PanelCallBack(  9, False,  True,  5, 0,  True, RecvDebugI,                          "Image Data" ),          # Image Data Sequence
            0x15 : PanelCallBack( 13, False, False,  0, 0,  True, RecvDebugI,                          "Image Unknown" ) 
          }
}

##############################################################################################################################################################################################################################################
##########################  A7 Message Alarm / Trouble Status ################################################################################################################################################################################
##############################################################################################################################################################################################################################################

# A single value is in the A7 message that denotes the alarm / trouble status.  There could be up to 4 messages in A7.
# Event Type Constants
EVENT_TYPE_SYSTEM_RESET = 0x60
EVENT_TYPE_FORCE_ARM = 0x59
EVENT_TYPE_DISARM = 0x55

# Zone names are taken from the panel, so no langauage support needed, these are updated when EEPROM is downloaded or the B0 message is received
#    TODO : Ensure that the EPROM and downloaded strings match the lower case with underscores to match the language files where possible
pmZoneName = [
   "attic", "back_door", "basement", "bathroom", "bedroom", "child_room", "conservatory", "play_room", "dining_room", "downstairs",
   "emergency", "fire", "front_door", "garage", "garage_door", "guest_room", "hall", "kitchen", "laundry_room", "living_room",
   "master_bathroom", "master_bedroom", "office", "upstairs", "utility_room", "yard", "custom_1", "custom_2", "custom_3",
   "custom_4", "custom_5", "not_installed"
]
# ['Loft', 'Back door', 'Cellar', 'Bathroom', 'Bedroom', 'Child room', 'Conservatory', 'Play room', 'Dining room', 'Downstairs']
# ['Emergency', 'Fire', 'Front door', 'Garage', 'Garage door', 'Office', 'Hall', 'Kitchen', 'Laundry room', 'Living room']
# ['Master bath', 'Master Bdrm', 'Study', 'Upstairs', 'Utility room', 'Garden']






##############################################################################################################################################################################################################################################
##########################  EEPROM Decode  ###################################################################################################################################################################################################
##############################################################################################################################################################################################################################################

# Set 1 of the following but not both, depending on the panel type
XDumpy = False # True     # Used to dump PowerMax Data to the log file
SDumpy = False # False    # Used to dump PowerMaster Data to the log file
Dumpy = XDumpy or SDumpy

NOBYPASSSTR = "No Bypass"

# PMAX EEPROM CONFIGURATION version 1_2
SettingsCommand = collections.namedtuple('SettingsCommand', 'show count type poff psize pstep pbitoff name values')
pmDecodePanelSettings = {
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
    "panelbypass"    : SettingsCommand(   True,  1, "BYTE",    284,   2,   0,     6,  "Panel Global Bypass",                { '2':"Manual Bypass", '0':NOBYPASSSTR, '1':"Force Arm"} ),
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
    "displayName"    : SettingsCommand(  Dumpy,  1,"STRING",   428, 128,   0,    -1,  "Displayed String Panel Name",        {} ),   # This is shown on the display as it is centred in the string.  360 shows "SECURITY SYSTEM" for example
    "gsmAntenna"     : SettingsCommand(   True,  1, "BYTE",    447,   8,   0,    -1,  "GSM Select Antenna",                 { '0':"Internal antenna", '1':"External antenna", '2':"Auto detect"} ),

    "userCodeMax"    : SettingsCommand( XDumpy, 16, "BYTE",    506,   8,   1,    -1,  "PowerMax User Codes",                {} ),
    "userCodeMaster" : SettingsCommand( SDumpy, 96, "BYTE",   2712,   8,   1,    -1,  "PowerMaster User Codes",             {} ),

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

    "x10Unknown"     : SettingsCommand(  Dumpy,  2, "BYTE",    745,   8,   1,    -1,  "X10 Unknown",                        {} ),

    "x10Trouble"     : SettingsCommand(  Dumpy,  1, "BYTE",    747,   8,   0,    -1,  "X10 Trouble Indication",             { '1':"Enable", '0':"Disable"} ),
    "x10Phase"       : SettingsCommand(  Dumpy,  1, "BYTE",    748,   8,   0,    -1,  "X10 Phase and frequency",            { '0':"Disable", '1':"50 Hz", '2':"60 Hz"} ),
    "x10ReportCs1"   : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     0,  "X10 Report on Fail to Central 1",    { '1':"Enable", '0':"Disable"} ),
    "x10ReportCs2"   : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     1,  "X10 Report on Fail to Central 2",    { '1':"Enable", '0':"Disable"} ),
    "x10ReportPagr"  : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     2,  "X10 Report on Fail to Pager",        { '1':"Enable", '0':"Disable"} ),
    "x10ReportPriv"  : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     3,  "X10 Report on Fail to Private",      { '1':"Enable", '0':"Disable"} ),
    "x10ReportSMS"   : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     4,  "X10 Report on Fail to SMS",          { '1':"Enable", '0':"Disable"} ),
    "usrVoice"       : SettingsCommand(  Dumpy,  1, "BYTE",    763,   8,   0,    -1,  "Set Voice Option",                   { '0':"Disable Voice", '1':"Enable Voice"} ),
    "usrSquawk"      : SettingsCommand(  Dumpy,  1, "BYTE",    764,   8,   0,    -1,  "Squawk Option",                      { '0':"Disable", '1':"Low Level", '2':"Medium Level", '3':"High Level"}),
    "usrArmTime"     : SettingsCommand(  Dumpy,  1, "TIME",    765,  16,   0,    -1,  "Auto Arm Time",                      {} ),
    "PartitionData"  : SettingsCommand(  Dumpy,255, "BYTE",    768,   8,   1,    -1,  "Partition Data",                     {} ),   # I'm not sure how many bytes this is or what they mean, i get all 255 bytes to the next entry so they can be displayed
    "panelEprom"     : SettingsCommand(   True,  1,"STRING",  1024, 128,   0,    -1,  "Panel Eprom",                        {} ),
    "panelSoftware"  : SettingsCommand(   True,  1,"STRING",  1040, 144,   0,    -1,  "Panel Software",                     {} ),
    "panelSerial"    : SettingsCommand(   True,  1, "CODE",   1072,  48,   0,    -1,  "Panel Serial",                       {} ),   # page 4 offset 48
    "panelModelCode" : SettingsCommand(  Dumpy,  1, "BYTE",   1078,   8,   0,    -1,  "Panel Model Code",                   {} ),   # page 4 offset 54 and 55 ->> Panel model code
    "panelTypeCode"  : SettingsCommand(  Dumpy,  1, "BYTE",   1079,   8,   0,    -1,  "Panel Type Code",                    {} ),   # page 4 offset 55

    #"MaybeEventLog"  : SettingsCommand(  Dumpy,256, "BYTE",   1247,   8,   1,    -1,  "Maybe the event log",                {} ),   # Structure not known   was length 808 but cut to 256 to see what data we get
    "x10ZoneNames"   : SettingsCommand(  Dumpy, 16, "BYTE",   2864,   8,   1,    -1,  "X10 Location Name references",       {} ),   # 
    #"MaybeScreenSaver":SettingsCommand(  Dumpy, 75, "BYTE",   5888,   8,   1,    -1,  "Maybe the screen saver",             {} ),   # Structure not known 

#    "ZoneStringNames": SettingsCommand(  Dumpy, 32,"STRING",  6400, 128,  16,    -1,  "Zone String Names",                  {} ),   # Zone String Names e.g "Attic", "Back door", "Basement", "Bathroom" etc 32 strings of 16 characters each, replace pmZoneName


#PowerMax Only
    "ZoneDataPMax"   : SettingsCommand( XDumpy, 30, "BYTE",   2304,  32,   4,    -1,  "Zone Data, PowerMax",                {} ),   # 4 bytes each, 30 zones --> 120 bytes
    "KeyFobsPMax"    : SettingsCommand( XDumpy, 16, "BYTE",   2424,  32,   4,    -1,  "Maybe KeyFob Data PowerMax",         {} ),   # Structure not known

    "ZoneSignalPMax" : SettingsCommand( XDumpy, 28, "BYTE",   2522,   8,   1,    -1,  "Zone Signal Strength, PowerMax",     {} ),   # 28 wireless zones
    "Keypad2PMax"    : SettingsCommand( XDumpy,  2, "BYTE",   2560,  32,   4,    -1,  "Keypad2 Data, PowerMax",             {} ),   # 4 bytes each, 2 keypads 
    "Keypad1PMax"    : SettingsCommand( XDumpy,  8, "BYTE",   2592,  32,   4,    -1,  "Keypad1 Data, PowerMax",             {} ),   # 4 bytes each, 8 keypads        THIS TOTALS 32 BYTES BUT IN OTHER SYSTEMS IVE SEEN 64 BYTES
    "SirensPMax"     : SettingsCommand( XDumpy,  2, "BYTE",   2656,  32,   4,    -1,  "Siren Data, PowerMax",               {} ),   # 4 bytes each, 2 sirens 

    "ZoneNamePMax"   : SettingsCommand( XDumpy, 30, "BYTE",   2880,   8,   1,    -1,  "Zone Names, PowerMax",               {} ),

    #"ZoneStrType1X"  : SettingsCommand( XDumpy, 16,"STRING", 22568, 120,  16,    -1,  "PowerMax Zone Type String",          {} ),   # Zone String Types e.g 
#    "ZoneStrType1X"  : SettingsCommand( XDumpy, 16,"STRING", 22571,  96,  16,    -1,  "PowerMax Zone Type String",          {} ),   # Zone String Types e.g This starts 3 bytes later as it misses the "1. " and the strings are only 12 characters
#    "ZoneStrType2X"  : SettingsCommand( XDumpy, 16,  "BYTE", 22583,   8,  16,    -1,  "PowerMax Zone Type Reference",       {} ),   # Zone String Types e.g 

#    "ZoneChimeType1X": SettingsCommand( XDumpy,  3,"STRING",0x64D8, 120,  16,    -1,  "PowerMax Zone Chime Type String",    {} ),   # Zone String Types e.g 
#    "ZoneChimeType2X": SettingsCommand( XDumpy,  3,  "BYTE",0x64E7,   8,  16,    -1,  "PowerMax Zone Chime Type Ref",       {} ),   # Zone String Types e.g 

    #"Test2"          : SettingsCommand(  Dumpy,128, "BYTE",   2816,   8,   1,    -1,  "Test 2 String, PowerMax",            {} ),   # 0xB00
    #"Test1"          : SettingsCommand(  Dumpy,128, "BYTE",   2944,   8,   1,    -1,  "Test 1 String, PowerMax",            {} ),   # 0xB80

#PowerMaster only
    "ZoneDataPMaster": SettingsCommand( SDumpy, 64, "BYTE",   2304,   8,   1,    -1,  "Zone Data, PowerMaster",             {} ),   # 1 bytes each, 64 zones --> 64 bytes
    "ZoneNamePMaster": SettingsCommand( SDumpy, 64, "BYTE",   2400,   8,   1,    -1,  "Zone Names, PowerMaster",            {} ),   # 

    "SirensPMaster"  : SettingsCommand( SDumpy,  8, "BYTE",  46818,  80,  10,    -1,  "Siren Data, PowerMaster",            {} ),   # 10 bytes each, 8 sirens
    "KeypadPMaster"  : SettingsCommand( SDumpy, 32, "BYTE",  46898,  80,  10,    -1,  "Keypad Data, PowerMaster",           {} ),   # 10 bytes each, 32 keypads 
    "ZoneExtPMaster" : SettingsCommand( SDumpy, 64, "BYTE",  47218,  80,  10,    -1,  "Zone Extended Data, PowerMaster",    {} ),   # 10 bytes each, 64 zones 

    #"ZoneStrType1S"  : SettingsCommand( SDumpy, 16,"STRING", 33024, 120,  16,    -1,  "PowerMaster Zone Type String",       {} ),   # Zone String Types e.g 
#    "ZoneStrType1S"  : SettingsCommand( SDumpy, 16,"STRING", 33027,  96,  16,    -1,  "PowerMaster Zone Type String",       {} ),   # Zone String Types e.g  This starts 3 bytes later as it misses the "1. " and the strings are only 12 characters
#    "ZoneStrType2S"  : SettingsCommand( SDumpy, 16,  "BYTE", 33039,   8,  16,    -1,  "PowerMaster Zone Type Reference",    {} ),   # Zone String Types e.g 

#    "ZoneChimeType1S": SettingsCommand( SDumpy,  3,"STRING",0x8EB0, 120,  16,    -1,  "PowerMaster Zone Chime Type String", {} ),   # Zone String Types e.g 
#    "ZoneChimeType2S": SettingsCommand( SDumpy,  3,  "BYTE",0x8EBF,   8,  16,    -1,  "PowerMaster Zone Chime Type Ref",    {} ),   # Zone String Types e.g 

#    "LogEventStr"    : SettingsCommand( SDumpy,160,"STRING",0xED00, 128,  16,    -1,  "Log Event Strings",                  {} ),   # Zone String Types e.g 

    "AlarmLED"       : SettingsCommand( SDumpy, 64, "BYTE",  49250,   8,   1,    -1,  "Alarm LED, PowerMaster",             {} ),   # This is the Alarm LED On/OFF settings for Motion Sensors -> Dev Settings --> Alarm LED
    "ZoneDelay"      : SettingsCommand( SDumpy, 64, "BYTE",  49542,  16,   2,    -1,  "Zone Delay, PowerMaster",            {} )    # This is the Zone Delay settings for Motion Sensors -> Dev Settings --> Disarm Activity  
}
# 'show count type poff psize pstep pbitoff name values'

# These are the panel settings to keep a track of, most come from pmPanelSettingCodes and the EPROM/B0 
class PanelSetting(IntEnum):
    UserCodes       = 1
#    PanelSerial     = 2
#    Keypad_1Way     = 3
#    Keypad_2Way     = 4
#    KeyFob          = 5
#    Sirens          = 6
#    AlarmLED        = 7
    PartitionData   = 8
    ZoneChime       = 9
    ZoneNames       = 10
    ZoneTypes       = 11
    ZoneExt         = 12
    ZoneDelay       = 13
#    ZoneSignal      = 14
    ZoneData        = 15
    ZoneEnrolled    = 16
#    PanicAlarm      = 17
    PanelBypass     = 18
#    PanelModel      = 19
#    PanelDownload   = 20
    DeviceTypes     = 21
#    ZoneNameString  = 22
    PartitionEnabled = 23
#    TestTest        = 200


B0All = True
PanelSettingsCollection = collections.namedtuple('PanelSettingsCollection', 'length display datatype datacount msg') # overall length in bytes, datatype in bits
pmPanelSettingsB0 = {
   0x0000 : PanelSettingsCollection(  6, B0All,  1,  6, "Central Station Account Number 1"),  # size of each entry is 6 nibbles
   0x0001 : PanelSettingsCollection(  6, B0All,  1,  6, "Central Station Account Number 2"),  # size of each entry is 6 nibbles
   0x0002 : PanelSettingsCollection(  7, B0All,  2,  0, "Panel Serial Number"),
   0x0003 : PanelSettingsCollection(  9, B0All,  1, 12, "Central Station IP 1"),              # 12 nibbles e.g. 192.168.010.001
   0x0004 : PanelSettingsCollection(  6, B0All,  1,  0, "Central Station Port 1"),            # 
   0x0005 : PanelSettingsCollection(  9, B0All,  1, 12, "Central Station IP 2"),              # 12 nibbles
   0x0006 : PanelSettingsCollection(  6, B0All,  1,  0, "Central Station Port 2"),            # 
   0x0007 : PanelSettingsCollection( 39, B0All,  4,  0, "Capabilities unknown"),              # 
   0x0008 : PanelSettingsCollection( 99, False,  1,  4, "User Code"),                         # size of each entry is 4 nibbles
   0x000D : PanelSettingsCollection(  0, B0All, 10, 32, "Zone Names"),                        # 32 nibbles i.e. each string name is 16 bytes long
   0x000F : PanelSettingsCollection(  5, False,  1,  4, "Download Code"),                     # size of each entry is 4 nibbles
   0x0010 : PanelSettingsCollection(  4, B0All,  4,  0, "Panel EPROM Version 1"),
   0x0024 : PanelSettingsCollection(  5, B0All,  4,  0, "Panel EPROM Version 2"),
   0x0029 : PanelSettingsCollection(  4, B0All,  4,  0, "Unknown B"),
   0x0030 : PanelSettingsCollection(  4, B0All,  4,  0, "Unknown C"),
   0x002C : PanelSettingsCollection( 19, B0All,  6,  0, "Panel Default Version"),
   0x002D : PanelSettingsCollection( 19, B0All,  6,  0, "Panel Software Version"),
   0x0033 : PanelSettingsCollection( 67, B0All,  4, 64, "Zone Chime Data"),
   0x0036 : PanelSettingsCollection( 67, B0All,  4, 64, "Partition Data"),
   0x003C : PanelSettingsCollection( 18, B0All,  8,  0, "Panel Hardware Version"),
   0x003D : PanelSettingsCollection( 19, B0All,  6,  0, "Panel RSU Version"),
   0x003E : PanelSettingsCollection( 19, B0All,  6,  0, "Panel Boot Version"),
   0x0054 : PanelSettingsCollection(  5, False,  1,  4, "Installer Code"),                    # size of each entry is 4 nibbles
   0x0055 : PanelSettingsCollection(  5, False,  1,  4, "Master Code"),                       # size of each entry is 4 nibbles
   0x0058 : PanelSettingsCollection(  4, B0All,  4,  2, "Unknown D"),
   0x0106 : PanelSettingsCollection(  4, B0All,  4,  1, "Unknown A"),                         # size of each entry is 4 nibbles
}                                                                       

# pmPanelSettingCodes represents the ways that we can get data to populate the PanelSettings
#   A PowerMax Panel only has 1 way and that is to download the EPROM = PMaxEPROM
#   A PowerMaster Panel has 3 ways:
#        1. Download the EPROM = PMasterEPROM
#        2. Ask the panel for a B0 panel settings message 0x51 e.g. 0x0800 sends the user codes  = PMasterB0Panel
#        3. Ask the panel for a B0 data message = PMasterB0Mess PMasterB0Index

# These are conversion to string functions

def psc_lba(p):   # p = a list of bytearrays
    s = ""
    for ba in p:
        s = s + toString(ba, "") + " "
    return s[:-1] if len(s) > 0 else s

def psc_dummy(p):
    return p

PanelSettingCodesType = collections.namedtuple('PanelSettingCodesType', 'item PMaxEPROM PMasterEPROM PMasterB0Panel PMasterB0Mess PMasterB0Index tostring default')
#   For PMasterB0Mess there is an assumption that the message type is 0x03, and this is the subtype
#       PMasterB0Index index 3 is Sensor data, I should have an enum for this
pmPanelSettingCodes = { # These are used to create the self.PanelSettings dictionary to create a common set of settings across the different ways of obtaining them
#                                                          item   PMaxEPROM         PMasterEPROM      PMasterB0Panel  PMasterB0Mess   PMasterB0Index   tostring       default
    PanelSetting.UserCodes        : PanelSettingCodesType( None, "userCodeMax",    "userCodeMaster",  0x0008,         None,           None,            toString ,     bytearray([0,0]) ),
    PanelSetting.PartitionData    : PanelSettingCodesType( None, "PartitionData",  "PartitionData",   0x0036,         None,           None,            toString ,     bytearray()),
    PanelSetting.ZoneNames        : PanelSettingCodesType( None, "ZoneNamePMax",   "ZoneNamePMaster", None  ,         "ZONE_NAMES",   IndexName.ZONES, toString ,     bytearray()),
#    PanelSetting.ZoneNameString   : PanelSettingCodesType( None, None,             None,              0x000D,         None,           None,            toString ,     bytearray()),          # The string names themselves
    PanelSetting.ZoneTypes        : PanelSettingCodesType( None, None,             None,              None  ,         "ZONE_TYPES",   IndexName.ZONES, toString ,     bytearray()),          # 
    PanelSetting.ZoneExt          : PanelSettingCodesType( None, None,             "ZoneExtPMaster",  None  ,         None,           None,            toString ,     bytearray()),
    PanelSetting.DeviceTypes      : PanelSettingCodesType( None, None,             None            ,  None  ,         "DEVICE_TYPES", IndexName.ZONES, toString ,     bytearray()),          
    PanelSetting.ZoneDelay        : PanelSettingCodesType( None, None,             "ZoneDelay",       None  ,         None,           None,            toString ,     bytearray(64)),        # Initialise to 0s so it passes the I've got it from the panel test until I know how to get this using B0 data
    PanelSetting.ZoneData         : PanelSettingCodesType( None, "ZoneDataPMax",   "ZoneDataPMaster", None  ,         None,           None,            toString ,     bytearray()),
    PanelSetting.ZoneEnrolled     : PanelSettingCodesType( None, None,             None,              None  ,         "SENSOR_ENROL", IndexName.ZONES, psc_dummy,     [] ),           
    PanelSetting.PanelBypass      : PanelSettingCodesType( 0,    "panelbypass",    "panelbypass",     None  ,         None,           None,            psc_dummy,     [NOBYPASSSTR]),
    PanelSetting.PartitionEnabled : PanelSettingCodesType( None, None,             None,              0x0030,         None,           None,            toString ,     bytearray() ),
#    PanelSetting.TestTest         : PanelSettingCodesType( None, None,             None,              0x0031,         None,           None,            toString ,     bytearray() ),
    PanelSetting.ZoneChime        : PanelSettingCodesType( None, None,             None,              0x0033,         None,           None,            toString ,     bytearray() )
}                                                                                                                                   

#   PanelSetting.PanelDownload    : PanelSettingCodesType( None, "masterDlCode",   "masterDlCode",    0x000f,         None,           None,            psc_dummy ,     bytearray()),
#   PanelSetting.PanelSerial      : PanelSettingCodesType( 0,    "panelSerial",    "panelSerial",     0x0002,         None,           None,            psc_dummy,     ["Undefined"] ),
#   PanelSetting.Keypad_1Way      : PanelSettingCodesType( None, "Keypad1PMax",    None,              None  ,         None,           None,            toString ,     bytearray()),            # PowerMaster Panels do not have 1 way keypads
#   PanelSetting.Keypad_2Way      : PanelSettingCodesType( None, "Keypad2PMax",    "KeypadPMaster",   None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.KeyFob           : PanelSettingCodesType( None, "KeyFobsPMax",    "",                None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.Sirens           : PanelSettingCodesType( None, "SirensPMax",     "SirensPMaster",   None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.AlarmLED         : PanelSettingCodesType( None, None,             "AlarmLED",        None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.ZoneSignal       : PanelSettingCodesType( None, "ZoneSignalPMax", "",                None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.PanicAlarm       : PanelSettingCodesType( 0,    "panicAlarm",     "panicAlarm",      None  ,         None,           None,            psc_dummy,     [False]),
#   PanelSetting.PanelModel       : PanelSettingCodesType( 0,    "panelModelCode", "panelModelCode",  None  ,         None,           None,            psc_lba  ,     [bytearray([0,0,0,0])]),

# These blocks are not value specific, they are used to download blocks of EEPROM data that we need without reference to what the data means
#    They are used when EEPROM_DOWNLOAD_ALL is False
#    We have to do it like this as the max message size is 176 (0xB0) bytes.

pmBlockDownload_Short = {
    "PowerMax" : ( 
              ( 0x0100, 0x0500 ),
              ( 0x0900, 0x0C00 )
#              ( 0x1900, 0x1B00 ),      # ZoneStringNames 0x1900 = 6400 Decimal
#              ( 0x5800, 0x5A00 ),      # pmZoneType_t starts at 0x5828
#              ( 0x64D8, 0x6510 )       # pmZoneChime starts at 0x64D8
    ),
    "PowerMaster" : (
              ( 0x0100, 0x0500 ),
              ( 0x0900, 0x0C00 ),
#              ( 0x1900, 0x1B00 ),      # ZoneStringNames 0x1900 = 6400 Decimal
#              ( 0x8100, 0x8200 ),
#              ( 0x8EB0, 0x8EE8 ),      # Chime
              ( 0xB600, 0xBB00 ),
              ( 0xC000, 0xC280 )
#              ( 0xED00, 0xF700 )       # The pmLogEvent_t
    )
}

MAX_DOWNLOAD_BLOCK_SIZE = 0xB0
pmBlockDownload = {}
for blk in pmBlockDownload_Short:
    l = []
    for d in pmBlockDownload_Short[blk]:
        s = d[0]
        e = d[1]
        while s < e:
            l.append(bytearray([s & 0xFF, (s >> 8) & 0xFF, MAX_DOWNLOAD_BLOCK_SIZE if e - s >= MAX_DOWNLOAD_BLOCK_SIZE else e - s, 0]))
            s = s + MAX_DOWNLOAD_BLOCK_SIZE
    pmBlockDownload[blk] = l
#for t in pmBlockDownload:
#    print(t)
#    for d in pmBlockDownload[t]:
#        print(toString(d))


pmZoneTypeKey = ( "non-alarm", "emergency", "flood", "gas", "delay_1", "delay_2", "interior_follow", "perimeter", "perimeter_follow",
                "24_hours_silent", "24_hours_audible", "fire", "interior", "home_delay", "temperature", "outdoor", "undefined" )

pmZoneChimeKey = ("chime_off", "melody_chime", "zone_name_chime")

# Note: names need to match to VAR_xxx
pmZoneSensorMaxGeneric_t = {
   0x0 : AlSensorType.VIBRATION, 0x2 : AlSensorType.SHOCK, 0x3 : AlSensorType.MOTION, 0x4 : AlSensorType.MOTION, 0x5 : AlSensorType.MAGNET,
   0x6 : AlSensorType.MAGNET, 0x7 : AlSensorType.MAGNET, 0x8 : AlSensorType.MAGNET, 0x9 : AlSensorType.MAGNET, 
   0xA : AlSensorType.SMOKE, 0xB : AlSensorType.GAS, 0xC : AlSensorType.MOTION, 0xF : AlSensorType.WIRED
} # unknown to date: Push Button, Flood, Universal

#0x75 : ZoneSensorType("Next+ K9-85 MCW", AlSensorType.MOTION ), # Jan
#0x86 : ZoneSensorType("MCT-426", AlSensorType.SMOKE ), # Jan
ZoneSensorType = collections.namedtuple("ZoneSensorType", 'name func' )
pmZoneSensorMax = {
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
pmZoneSensorMaster = {
   0x01 : ZoneSensorType("Next PG2", AlSensorType.MOTION ),
   0x03 : ZoneSensorType("Clip PG2", AlSensorType.MOTION ),
   0x04 : ZoneSensorType("Next CAM PG2", AlSensorType.CAMERA ),
   0x05 : ZoneSensorType("GB-502 PG2", AlSensorType.SOUND ),
   0x06 : ZoneSensorType("TOWER-32AM PG2", AlSensorType.MOTION ),
   0x07 : ZoneSensorType("TOWER-32AMK9", AlSensorType.MOTION ),
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
   0xFE : ZoneSensorType("Wired", AlSensorType.WIRED )
}

##############################################################################################################################################################################################################################################
##########################  Data Driven Message Decode #######################################################################################################################################################################################
##############################################################################################################################################################################################################################################

ZoneEventActionCollection = collections.namedtuple('ZoneEventActionCollection', 'func problem parameter')
pmZoneEventAction = {
      0 : ZoneEventActionCollection("",           "none",          None ),                        # "None",
      1 : ZoneEventActionCollection("do_tamper",  "tamper",        True ),                        # "Tamper Alarm",          
      2 : ZoneEventActionCollection("do_tamper",  "none",          False ),                       # "Tamper Restore",        
      3 : ZoneEventActionCollection("do_status",  "none",          True ),                        # "Zone Open",             
      4 : ZoneEventActionCollection("do_status",  "none",          False ),                       # "Zone Closed",           
      5 : ZoneEventActionCollection("do_trigger", "none",          True ),                        # "Zone Violated (Motion)",
      6 : ZoneEventActionCollection("pushChange", "none",          AlSensorCondition.PANIC ),     # "Panic Alarm",           
      7 : ZoneEventActionCollection("pushChange", "jamming",       AlSensorCondition.PROBLEM ),   # "RF Jamming",            
      8 : ZoneEventActionCollection("do_tamper",  "tamper",        True ),                        # "Tamper Open",           
      9 : ZoneEventActionCollection("pushChange", "comm_failure",  AlSensorCondition.PROBLEM ),   # "Communication Failure", 
     10 : ZoneEventActionCollection("pushChange", "line_failure",  AlSensorCondition.PROBLEM ),   # "Line Failure",          
     11 : ZoneEventActionCollection("pushChange", "fuse",          AlSensorCondition.PROBLEM ),   # "Fuse",                  
     12 : ZoneEventActionCollection("pushChange", "not_active" ,   AlSensorCondition.PROBLEM ),   # "Not Active" ,           
     13 : ZoneEventActionCollection("do_battery", "none",          True ),                        # "Low Battery",           
     14 : ZoneEventActionCollection("pushChange", "ac_failure",    AlSensorCondition.PROBLEM ),   # "AC Failure",            
     15 : ZoneEventActionCollection("pushChange", "none",          AlSensorCondition.FIRE ),      # "Fire Alarm",            
     16 : ZoneEventActionCollection("pushChange", "none",          AlSensorCondition.EMERGENCY ), # "Emergency",             
     17 : ZoneEventActionCollection("do_tamper",  "tamper",        True ),                        # "Siren Tamper",          
     18 : ZoneEventActionCollection("do_tamper",  "none",          False ),                       # "Siren Tamper Restore",  
     19 : ZoneEventActionCollection("do_battery", "none",          True ),                        # "Siren Low Battery",     
     20 : ZoneEventActionCollection("pushChange", "ac_failure",    AlSensorCondition.PROBLEM ),   # "Siren AC Fail",         
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

class DataType(IntEnum):
    """Command 35 Message data data types."""

    ZERO_PADDED_STRING = 0
    DIRECT_MAP_STRING = 1
    FF_PADDED_STRING = 2
    DOUBLE_LE_INT = 3
    INTEGER = 4
    STRING = 6
    SPACE_PADDED_STRING = 8
    SPACE_PADDED_STRING_LIST = 10

class MessagePriority(IntEnum):
    IMMEDIATE = 0
    ACK       = 1
    URGENT    = 2
    NORMAL    = 3

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
        self.data = data
    
    def __str__(self):
#        if OBFUS:
#            return f"type {self.type:<2}  subtype {self.subtype:<3}  sequence {self.sequence:<3}  datasize {self.datasize:<3}  index {self.index:<3}   length {self.length:<3}    data <OBFUSCATED>"
        return f"type {self.type:<2}  subtype {self.subtype:<3}  sequence {self.sequence:<3}  datasize {self.datasize:<3}  index {self.index:<3}   length {self.length:<3}    data {toString(self.data)}"

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
                self.response.append(ACK_MESSAGE)  # add an acknowledge to the list
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
    return f"{hex(v)[2:]}"

# This class handles the detailed low level interface to the panel.
#    It sends the messages
#    It builds and received messages from the raw byte stream and coordinates the acknowledges back to the panel
#    It checks CRC for received messages and creates CRC for sent messages
#    It coordinates the downloading of the EEPROM (but doesn't decode the data here)
#    It manages the communication connection
class ProtocolBase(AlPanelInterfaceHelper, AlPanelDataStream, MyChecksumCalc):
    """Manage low level Visonic protocol."""

    log.debug(f"Initialising Protocol - Protocol Version {PLUGIN_VERSION}")

    def __init__(self, loop=None, panelConfig : PanelConfig = None, panel_id : int = None, packet_callback: Callable = None, logger = None) -> None:
        super().__init__(panel_id = panel_id, logger = logger)
        """Initialize class."""

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
        
        self.PanelProblemCount = 0
        self.LastPanelProblemTime = None
        self.WatchdogTimeoutCounter = 0
        self.WatchdogTimeoutPastDay = 0
        self.DownloadCounter = 0

        # Loopback capability added. Connect Rx and Tx together without connecting to the panel
        self.loopbackTest = False
        self.loopbackCounter = 0

        # Configured from the client INTERFACE
        #   These are the default values
        self.ForceStandardMode = False        # INTERFACE : Get user variable from HA to force standard mode or try for PowerLink
        self.DisableAllCommands = False       # INTERFACE : Get user variable from HA to allow or disable all commands to the panel 
        self.DownloadCode = DEFAULT_DL_CODE   # INTERFACE : Set the Download Code
        # Now that the defaults have been set, update them from the panel config dictionary (that may not have all settings in)
        self.updateSettings(panelConfig)

        ########################################################################
        # Variables that are only used in handle_received_message function
        ########################################################################
        self.pmIncomingPduLen = 0             # The length of the incoming message
        self.pmCrcErrorCount = 0              # The CRC Error Count for Received Messages
        self.pmCurrentPDU = pmReceiveMsg[0] # The current receiving message type
        self.pmFlexibleLength = 0             # How many bytes less then the proper message size do we start checking for PACKET_FOOTER and a valid CRC
        # The receive byte array for receiving a message
        self.ReceiveData = bytearray(b"")

        # keep alive counter for the timer
        self.keep_alive_counter = 0  # only used in _sequencer

        # this is the watchdog counter (in seconds)
        self.watchdog_counter = 0

        # Save the EEPROM data when downloaded
        self.pmRawSettings = {}

        # Set when the panel details have been received i.e. a 3C message
        self.pmGotPanelDetails = False
        # Can the panel support the INIT Command
        self.ModelType = None
        self.PanelType = None                # We do not yet know the paneltype
        self.PanelModel = "UNKNOWN"
        self.PowerMaster = None              # Set to None to represent unknown until we know True or False
        self.AutoEnroll = True
        self.AutoSyncTime = False
        self.KeepAlivePeriod = KEEP_ALIVE_PERIOD
        self.pmInitSupportedByPanel = False

        self.firstCmdSent = False
        self.lastPacket = None
        
        # Mark's Powerlink Bridge
        self.PowerLinkBridgeConnected = False   # This is set true on first receipt of an E0.  It means that there is a server running to communicate with
        self.PowerLinkBridgeAlarm = False       # The server has an Alarm Panel connection
        self.PowerLinkBridgeStealth = False     # The server is in stealth mode (giving this integration sole access to the panel)
        self.PowerLinkBridgeProxy = False       # The server is acting in proxy mode i.e. it supports a Visonic Go connection to an external site)
        
        self.despatcherTask = None
        self.despatcherException = False
        # A queue of messages to send (i.e. VisonicListEntry)
        self.SendQueue = PriorityQueueWithPeek()

        self.resetGlobals()

        self.loop.create_task(self._sequencer())

    def resetGlobals(self):
        ########################################################################
        # Global Variables that define the overall panel status
        ########################################################################
        self.PanelMode = AlPanelMode.STARTING

        # partition related data
        self.partitionsEnabled = False
        self.PartitionState[0].Reset()
        self.PartitionState[1].Reset()
        self.PartitionState[2].Reset()
        self.PanelStatus = {}                # This is the set of EPROM settings shown
        
        self.PanelSettings = {}              # This is the record of settings for the integration to work
        for key in PanelSetting:
            self.PanelSettings[key] = pmPanelSettingCodes[key].default     # populate each setting with the default

        self.B0_Message_Count = 0
        self.B0_Message_Wanted = set()
        self.B0_Message_Waiting = set()
        self.B0_LastPanelStateTime = self._getUTCTimeFunction()
        
        # determine when MSG_ENROLL is sent to the panel
        self.nextDownloadCode = None
        
        self.PanelResetEvent = False
        
        ########################################################################
        # Variables that are only used in this class and not subclasses
        ########################################################################

        # a list of message types we are expecting from the panel
        self.pmExpectedResponse = set()

        # The last sent message
        self.pmLastSentMessage = None

        # Timestamp of the last received data from the panel. If this remains set to none then we have a comms problem
        self.lastRecvTimeOfPanelData = None

        self.myDownloadList = []

        # This is the time stamp of the last Send
        self.pmLastTransactionTime = self._getUTCTimeFunction() - timedelta(
            seconds=1
        )  # take off 1 second so the first command goes through immediately

        self.pmFirstCRCErrorTime = self._getUTCTimeFunction() - timedelta(
            seconds=1
        )  # take off 1 second so the first command goes through immediately

        # When to stop trying to download the EEPROM
        self.StopTryingDownload = False

        ###################################################################
        # Variables that are used and modified throughout derived classes
        ###################################################################

        ############## Variables that are set and read in this class and in derived classes ############

        # When we are downloading the EEPROM settings and finished parsing them and setting up the system.
        #   There should be no user (from Home Assistant for example) interaction when self.pmDownloadMode is True
        self.pmDownloadInProgress = False
        self.pmDownloadMode = False
        self.triggeredDownload = False
        
        self.PanelWantsToEnrol = False
        self.PanelKeepAlive = False
        self.TimeoutReceived = False
        self.ExitReceived = False
        self.DownloadRetryReceived = False
        self.AccessDeniedReceived = False
        self.AccessDeniedMessage = None
        self.EnableB0ReceiveProcessing = False

        # Set when we receive a STOP from the panel, indicating that the EEPROM data has finished downloading
        self.pmDownloadComplete = False

        # Download block retry count (this is for individual 3F download failures)
        self.pmDownloadRetryCount = 0

        # When trying to connect in powerlink from the timer loop, this allows the receipt of a powerlink ack to trigger a MSG_RESTORE
        self.allowAckToTriggerRestore = False
        self.receivedPowerlinkAcknowledge = False

        #self.pmPhoneNr_t = {}
        # Save the sirens
        #self.pmSirenDev_t = {}
        
        # Current F4 jpg image 
        self.ImageManager = AlImageManager()
        self.ignoreF4DataMessages = False
        self.image_ignore = set()
        
    def updateSettings(self, newdata: PanelConfig):
        if newdata is not None:
            # log.debug(f"[updateSettings] Settings refreshed - Using panel config {newdata}")
            if AlConfiguration.ForceStandard in newdata:
                # Get user variable from HA to force standard mode or try for PowerLink
                self.ForceStandardMode = newdata[AlConfiguration.ForceStandard]
                log.debug(f"[Settings] Force Standard set to {self.ForceStandardMode}")
            if AlConfiguration.DisableAllCommands in newdata:
                # Get user variable from HA to Disable All Commands
                self.DisableAllCommands = newdata[AlConfiguration.DisableAllCommands]
                log.debug(f"[Settings] Disable All Commands set to {self.DisableAllCommands}")
            if AlConfiguration.DownloadCode in newdata:
                tmpDLCode = newdata[AlConfiguration.DownloadCode]  # INTERFACE : Get the download code
                if len(tmpDLCode) == 4 and type(tmpDLCode) is str:
                    self.DownloadCode = tmpDLCode[0:2] + " " + tmpDLCode[2:4]
                    log.debug(f"[Settings] Download Code set to {self.DownloadCode}")

        if self.DisableAllCommands:
            self.ForceStandardMode = True
        # By the time we get here there are 3 combinations of self.DisableAllCommands and self.ForceStandardMode
        #     Both are False --> Try to get to Powerlink 
        #     self.ForceStandardMode is True --> Force Standard Mode, the panel can still be armed and disarmed
        #     self.ForceStandardMode and self.DisableAllCommands are True --> The integration interacts with the panel but commands such as arm/disarm/log/bypass are not allowed
        # The if statement above ensure these are the only supported combinations.
        log.debug(f"[Settings] ForceStandard = {self.ForceStandardMode}     DisableAllCommands = {self.DisableAllCommands}")

        if self.ForceStandardMode:
            log.error(f"[Settings] Force Standard is not supported in this development release.  Please either change your configuration or go back to the main release from HACS.")
        self.ForceStandardMode = False        # INTERFACE : Get user variable from HA to force standard mode or try for PowerLink
        self.DisableAllCommands = False       # INTERFACE : Get user variable from HA to allow or disable all commands to the panel 

    
    def getPanelZoneSetting(self, p : PanelSetting, zone : int) -> int | bool | None: 
        # Do not use for usercodes
        if p in self.PanelSettings:
            if zone < len(self.PanelSettings[p]):
                return self.PanelSettings[p][zone]
        return None
    
    def gotValidUserCode(self) -> bool:
        return len(self.PanelSettings[PanelSetting.UserCodes]) >= 2 and self.PanelSettings[PanelSetting.UserCodes][0] != 0 and self.PanelSettings[PanelSetting.UserCodes][1] != 0

    def getUserCode(self):
        if self.gotValidUserCode():
            #log.debug(f"[getUserCode] {self.PanelSettings[PanelSetting.UserCodes]}")
            #log.debug(f"[getUserCode] {self.PanelSettings[PanelSetting.UserCodes][0]}  {self.PanelSettings[PanelSetting.UserCodes][1]}")
            return bytearray([self.PanelSettings[PanelSetting.UserCodes][0], self.PanelSettings[PanelSetting.UserCodes][1]])
        return bytearray([0,0])

    def isPowerMaster(self) -> bool:
        return self.PowerMaster is not None and self.PowerMaster # PowerMaster models

    # This is called from the loop handler when the connection to the transport is made
    def vp_connection_made(self, transport : AlTransport):
        """Make the protocol connection to the Panel."""
        self.transport = transport
        log.debug("[Connection] Connected to local Protocol handler and Transport Layer")


    # This is called by the asyncio parent when the connection is lost
    # The problem is that it is also called when there isn't an exception and we just close the connection
    def vp_connection_lost(self, exc):
        """Log when connection is closed, if needed call callback."""
        if not self.suspendAllOperations:
            log.error(f"ERROR Connection Lost : disconnected because the Ethernet/USB connection was externally terminated.  {exc}")
        
        if exc is not None:
            log.error(f"ERROR Connection Lost : disconnected due to external error, exception data = {exc}")
            self._performDisconnect(AlTerminationType.EXTERNAL_TERMINATION)
        else:
            log.debug(f"Connection Closed, assume that the connection is closing gracefully")

    # when the connection has problems then call the onDisconnect when available,
    #     otherwise try to reinitialise the connection from here
    def _performDisconnect(self, termination : AlTerminationType):
        """Log when connection is closed, if needed call callback."""
        
        if self.suspendAllOperations:
            log.debug("[_performDisconnect] Operations Already Suspended. Sorry but all operations have been suspended, please recreate connection")
            return

        log.debug(f"Connection Lost : disconnected due to {termination}")

        # empty the panel settings data when stopped
        self.PanelSettings = {}

        # Empty the sensor details
        self.SensorList = {}

        # Empty the X10 details
        self.SwitchList = {}

        # empty the EEPROM data when stopped
        self.pmRawSettings = {}

        self.lastRecvTimeOfPanelData = None

        # Save the sirens
        #self.pmSirenDev_t = {}

        if self.transport is not None:
            self.transport.close()   # This will make the underlying code call vp_connection_lost with no exception
        self.transport = None

        #sleep(5.0)  # a bit of time for the watchdog timers and keep alive loops to self terminate
        if self.onDisconnectHandler:
            #log.debug("                        Calling Exception handler.")
            self.onDisconnectHandler(termination)
        else:
            log.error("                        No Exception handler to call, terminating Component......")

        self.shutdownOperation()

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
            self.B0_Message_Wanted.add("PANEL_STATE")        # 24
        elif self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK]:
            # Send RESTORE to the panel
            self._addMessageToSendList("MSG_RESTORE")  # also gives status.  This is an AB message which we can't send to POWERLINK_BRIDGED
        else:
            self._addMessageToSendList("MSG_STATUS")

    def setNextDownloadCode(self, paneltype) -> str:
        # The first time its called it leaves DownloadCode alone
        if self.nextDownloadCode is None:
            tmp = self.DownloadCode[:2] + self.DownloadCode[2:]
            if tmp == pmPanelConfig["CFG_DLCODE_1"][paneltype]: # The base setting is the same as DLCODE 1 so set the next one to be DLCODE 2
                self.nextDownloadCode = pmPanelConfig["CFG_DLCODE_2"][paneltype]
            else:
                self.nextDownloadCode = pmPanelConfig["CFG_DLCODE_1"][paneltype]
        elif self.nextDownloadCode == pmPanelConfig["CFG_DLCODE_1"][paneltype]:
            self.DownloadCode = pmPanelConfig["CFG_DLCODE_1"][paneltype][:2] + " " + pmPanelConfig["CFG_DLCODE_1"][paneltype][2:]
            self.nextDownloadCode = pmPanelConfig["CFG_DLCODE_2"][paneltype]
        elif self.nextDownloadCode == pmPanelConfig["CFG_DLCODE_2"][paneltype]:
            self.DownloadCode = pmPanelConfig["CFG_DLCODE_2"][paneltype][:2] + " " + pmPanelConfig["CFG_DLCODE_2"][paneltype][2:]
            self.nextDownloadCode = pmPanelConfig["CFG_DLCODE_3"][paneltype]
        elif self.nextDownloadCode == pmPanelConfig["CFG_DLCODE_3"][paneltype]:
            self.DownloadCode = pmPanelConfig["CFG_DLCODE_3"][paneltype][:2] + " " + pmPanelConfig["CFG_DLCODE_3"][paneltype][2:]
            self.nextDownloadCode = "" # not None and invalid, so it goes to else next time
        else:
            ra = random.randint(10, 240)
            rb = random.randint(10, 240)
            self.DownloadCode = f"{hexify(ra):>02} {hexify(rb):>02}"
        return self.DownloadCode

    # We can only use this function when the panel has sent a "installing powerlink" message i.e. AB 0A 00 01
    #   We need to clear the send queue and reset the send parameters to immediately send an MSG_ENROLL
    def _sendMsgENROLL(self, force = False):
        """ Auto enroll the PowerMax/Master unit """
        # Only attempt to auto enroll powerlink for newer panels but not the 360 or 360R.
        #       Older panels need the user to manually enroll
        #       360 and 360R can get to Standard Plus but not Powerlink as (I assume that) they already have this hardware and panel will not support 2 powerlink connections
        if force or (self.PanelMode == AlPanelMode.STANDARD_PLUS):
            if force or (self.PanelType is not None and self.AutoEnroll):
                # Only attempt to auto enroll powerlink for newer panels. Older panels need the user to manually enroll, we should be in Standard Plus by now.
                log.debug("[_sendMsgENROLL] Trigger Powerlink Attempt")
                # Allow the receipt of a powerlink ack to then send a MSG_RESTORE to the panel,
                #      this should kick it in to powerlink after we just enrolled
                self.allowAckToTriggerRestore = True
                # Send enroll to the panel to try powerlink
                self._addMessageToSendList("MSG_ENROLL", priority = MessagePriority.IMMEDIATE, options=[ [4, convertByteArray(self.DownloadCode)] ])
                #self._addMessageToSendList("MSG_ENROLL", options=[ [4, convertByteArray(self.DownloadCode)] ])
            elif self.PanelType is not None and self.PanelType >= 1:
                # Powermax+ or Powermax Pro, attempt to just send a MSG_RESTORE to prompt the panel in to taking action if it is able to
                log.debug("[_sendMsgENROLL] Trigger Powerlink Prompt attempt to a Powermax+ or Powermax Pro panel")
                # Prevent the receipt of a powerlink ack to then send a MSG_RESTORE to the panel,
                self.allowAckToTriggerRestore = False
                # Send a MSG_RESTORE, if it sends back a powerlink acknowledge then another MSG_RESTORE will be sent,
                #      hopefully this will be enough to kick the panel in to sending 0xAB Keep-Alive
                self._addMessageToSendList("MSG_RESTORE")


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
            self._addMessageToSendList("MSG_DOWNLOAD_TIME", priority = MessagePriority.URGENT, options=[ [3, convertByteArray(self.DownloadCode)] ])  
            self._addMessageToSendList("MSG_SETTIME", priority = MessagePriority.URGENT, options=[ [3, timePdu] ])
            self._addMessageToSendList("MSG_EXIT", priority = MessagePriority.URGENT)

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
        PM_Request_End   = convertByteArray('43')

        PM_Data = PM_Request_Start + PM_Request_Data + PM_Request_End

        PM_Data[3] = len(PM_Request_Data) + 5
        PM_Data[8] = len(PM_Request_Data)

        CS = self._calculateCRC(PM_Data)   # returns a bytearray with a single byte
        To_Send = bytearray([0x0d]) + PM_Data + CS + bytearray([0x0a])

        log.debug(f"[_create_B0_35_Data_request] Returning {toString(To_Send)}")
        return To_Send

    def _create_B0_Data_Request(self, taglist : list = None, strlist : str = None) -> bytearray:

        if taglist is None and strlist is None:
            PM_Request_Data = convertByteArray('00 00')
        elif taglist is not None:
            PM_Request_Data = bytearray(taglist)
        elif strlist is not None:
            PM_Request_Data = convertByteArray(strlist)
        else:
            log.debug(f"[_create_B0_Data_Request] Error not sending anything as both params set")
            return
        
        PM_Request_Start = convertByteArray('b0 01 17 99 01 ff 08 ff 99')
        PM_Request_End   = convertByteArray('43')

        PM_Data = PM_Request_Start + PM_Request_Data + PM_Request_End

        PM_Data[3] = len(PM_Request_Data) + 5   # Was 6 but removed counter at the end!!!!!!
        PM_Data[8] = len(PM_Request_Data)

        CS = self._calculateCRC(PM_Data)   # returns a bytearray with a single byte
        To_Send = bytearray([0x0d]) + PM_Data + CS + bytearray([0x0a])

        log.debug(f"[_create_B0_Data_Request] Returning {toString(To_Send)}")
        return To_Send

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
                        d = await self.SendQueue.get()  # this blocks waiting for something to be added to the queue, nothing else is relevant as pmExpectedResponse is empty and can only be added to by calling _sendPdu
                        #log.debug(f"[_despatcher] Get worked and got something priority={d[0]}          queue size {self.SendQueue.qsize()}")

                        # since we might have been waiting for something to send, check it again :)
                        if not self.suspendAllOperations:
                            instruction = d[1]   # PriorityQueue is put as a tuple (priority, viscommand), so get the viscommand
                            if len(instruction.response) > 0:
                                # update the expected response list straight away (without having to wait for it to be actually sent) to make sure protocol is followed
                                self.pmExpectedResponse.update(instruction.response)
                            self.SendQueue.task_done()
                            #log.debug(f"[_despatcher] _despatcher sending it to sendPdu, instruction={instruction}          queue size {self.SendQueue.qsize()}")
                            post_delay = self._sendPdu(instruction)
                            #log.debug(f"[_despatcher] Nothing to do      queue size {self.SendQueue.qsize()}")
                else: #elif not self.pmDownloadMode:
                    # We're waiting for a message back from the panel before continuing (and we're not downloading EPROM)
                    # Do not do the timeouts when getting EPROM, the sequencer sorts it all out
                    # self.pmExpectedResponse will prevent us sending another message to the panel
                    if interval > RESPONSE_TIMEOUT:
                        # If the panel is lazy or we've got the timing wrong........
                        # Expected response timeouts are only a problem when in Powerlink Mode as we expect a response
                        #   But in all modes, give the panel a self._triggerRestoreStatus
                        if len(self.pmExpectedResponse) == 1 and ACK_MESSAGE in self.pmExpectedResponse:
                            self.pmExpectedResponse = set()  # If it's only for an acknowledge response then ignore it
                        else:
                            st = '[{}]'.format(', '.join(hex(x) for x in self.pmExpectedResponse))
                            log.debug(f"[_despatcher] ****************************** Response Timer Expired ********************************")
                            log.debug(f"[_despatcher]                While Waiting for: {st}")
                            # Reset Send state (clear queue and reset flags)
                            self._clearReceiveResponseList()
                            #self._emptySendQueue(pri_level = 1)
                            self._triggerRestoreStatus()     # Clear message buffers and send a Restore (if in Powerlink) or Status (not in Powerlink) to the Panel
                    elif interval > RESEND_MESSAGE_TIMEOUT:
                        #   If there's a timeout then resend the previous message. If that doesn't work then dump the message and continue, but log the error
                        if not self.pmLastSentMessage.triedResendingMessage:
                            # resend the last message
                            log.debug(f"[_despatcher] ****************************** Resend Timer Expired ********************************")
                            log.debug(f"[_despatcher]                Re-Sending last message  {self.pmLastSentMessage.command.msg}")
                            self.pmLastSentMessage.triedResendingMessage = True
                            post_delay = self._sendPdu(self.pmLastSentMessage)
                        else:
                            # tried resending once, no point in trying again so reset settings, start from scratch
                            log.debug("[_despatcher] ****************************** Resend Timer Expired ********************************")
                            log.debug("[_despatcher]                Tried Re-Sending last message but didn't work. Message is dumped")
                            # Reset Send state (clear queue and reset flags)
                            self._clearReceiveResponseList()
                            self._emptySendQueue(pri_level = 1)
                            # restart the watchdog and keep-alive counters
                            #self._triggerRestoreStatus()
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
            InitialiseEPROMDownload = 7
            TriggerEPROMDownload    = 8
            StartedEPROMDownload    = 9
            DoingEPROMDownload      = 10
            EPROMDownloadComplete   = 11
            EnrollingPowerlink      = 12
            DoingStandardPlus       = 13
            WaitingForEnrolSuccess  = 14
            DoingPowerlink          = 15
            DoingPowerlinkBridge    = 16
            GettingB0SensorMessages = 17
            CreateSensors           = 18

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
            self._addMessageToSendList("MSG_EXIT")
            self._addMessageToSendList("MSG_STOP")

            if not self.PowerLinkBridgeConnected and self.pmInitSupportedByPanel:
                self._addMessageToSendList("MSG_INIT")

        def _gotoStandardModeStopDownload():
            if not self.PowerLinkBridgeConnected:  # Should not be in this function when this is True but use it anyway
                if self.DisableAllCommands:
                    log.debug("[Standard Mode] Entering MINIMAL ONLY Mode")
                    self.PanelMode = AlPanelMode.MINIMAL_ONLY
                elif self.pmDownloadComplete and not self.ForceStandardMode and self.gotValidUserCode():
                    log.debug("[Standard Mode] Entering Standard Plus Mode as we got the pin codes from the EEPROM (You can still manually Enroll your Panel)")
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
            self._addMessageToSendList("MSG_STATUS_SEN")

        def _clearPanelErrorMessages():
            self.AccessDeniedReceived = False
            self.AccessDeniedMessage = None
            self.ExitReceived = False
            self.DownloadRetryReceived = False
            self.TimeoutReceived = False
            
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
                    if lastCommandData is not None:
                        log.debug(f"[_sequencer]     AccessDenied last command {toString(lastCommandData[:3] if OBFUS else lastCommandData)}")
                        # Check download first, then pin, then stop
                        if lastCommandData[0] == 0x24:
                            log.debug("[_sequencer]           Got an Access Denied and we have sent a Bump or a Download command to the Panel")
                            return PanelErrorStates.AccessDeniedDownload
                        elif lastCommandData[0] != 0xAB and lastCommandData[0] & 0xA0 == 0xA0:  # this will match A0, A1, A2, A3 etc but not 0xAB
                            log.debug("[_sequencer]           Attempt to send a command message to the panel that has been denied, wrong pin code used")
                            # INTERFACE : tell user that wrong pin has been used
                            return PanelErrorStates.AccessDeniedPin
                        elif lastCommandData[0] == 0x0B:  # Stop
                            log.debug("[_sequencer]           Received a stop command from the panel")
                            return PanelErrorStates.AccessDeniedStop
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
            return PanelErrorStates.AllGood

        def startDespatcher():
            # (re)start the PDU despatcher, the task that sends messages to the panel
            if self.despatcherTask is not None:
                log.debug("[_sequencer] Cancelling _despatcher")
                self.despatcherTask.cancel()
            self._reset_watchdog_timeout()
            self._reset_keep_alive_messages()
            self._clearReceiveResponseList()
            self._emptySendQueue(pri_level = -1)  # empty the list
            log.debug("[_sequencer] Starting _despatcher")
            self.despatcherTask = self.loop.create_task(self._despatcher())

        def reset_vars():
            self.resetGlobals()

            _sequencerState = SequencerType.InitialisePanel
            _sequencerStatePrev = SequencerType.Invalid

            _last_B0_wanted_request_time = self._getTimeFunction()
            _my_panel_state_trigger_count = 5
            _sendStartUp = False
            # declare a list and fill it with zeroes
            watchdog_list = [0] * WATCHDOG_MAXIMUM_EVENTS
            # The starting point doesn't really matter
            watchdog_pos = WATCHDOG_MAXIMUM_EVENTS - 1

            startDespatcher()

            counter = 0                     # create a generic counter that gets reset every state change, so it can be used in a single state
            no_data_received_counter = 0
            no_packet_received_counter = 0
            image_delay_counter = 0
            log_sensor_state_counter = 0
            lastrecv = None
            delay_loops = 0

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
                            log.error( "[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. no data has been received from the panel)" )
                            self._performDisconnect(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
                            continue   # just do the while loop, which will exit as self.suspendAllOperations will be True
                    elif self.lastPacket is None: # have we been able to construct at least one full and crc checked message 
                        no_packet_received_counter = no_packet_received_counter + 1
                        #log.debug(f"[_sequencer] no_packet_received_counter {no_packet_received_counter}")
                        if no_packet_received_counter >= NO_RECEIVE_DATA_TIMEOUT:  ## lets assume approx 30 seconds
                            log.error( "[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. no valid packet has been received from the panel)" )
                            self._performDisconnect(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
                            continue   # just do the while loop, which will exit as self.suspendAllOperations will be True
                    else:  # Data has been received from the panel but check when it was last received
                        # calc time difference between now and when data was last received
                        no_packet_received_counter = 0
                        no_data_received_counter = 0
                        # calculate the time interval back to the last receipt of any data
                        interval = self._getUTCTimeFunction() - self.lastRecvTimeOfPanelData
                        if interval >= timedelta(seconds=LAST_RECEIVE_DATA_TIMEOUT):
                            log.error( "[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. data has not been received from the panel in " + str(interval) + ")" )
                            self._performDisconnect(AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED)
                            continue   # just do the while loop, which will exit as self.suspendAllOperations will be True

                    #############################################################################################################################################################
                    ####### Sequencer activities ################################################################################################################################
                    #############################################################################################################################################################

                    if _sequencerState not in [SequencerType.DoingStandard, SequencerType.DoingStandardPlus, SequencerType.DoingPowerlink, SequencerType.DoingPowerlinkBridge] or changedState or counter % 120 == 0:
                        # When we reach 1 of the 3 final states then stop logging it, but then output every 2 minutes
                        ps = [p.PanelState.name for p in self.PartitionState]
                        log.debug(f"[_sequencer] SeqState={str(_sequencerState)}     Counter={counter}      PanelMode={self.PanelMode}     PanelState={ps}     SendQueue={self.SendQueue.qsize()}")

                    if self.loopbackTest:
                        # This supports the loopback test
                        #await asyncio.sleep(2.0)
                        self._clearReceiveResponseList()
                        self._emptySendQueue(pri_level = -1) # empty the list
                        self._addMessageToSendList("MSG_STOP")
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.LookForPowerlinkBridge:   ################################################################ LookForPowerlinkBridge ####################################################
                        if not self.ForceStandardMode:
                            for i in range(0,2):
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self._addMessageToSendList("MSG_PL_BRIDGE", priority = MessagePriority.IMMEDIATE, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                                # Make a 1 off request for the panel to send the Download Code and the panel name e.g. PowerMaster-10
                            self.EnableB0ReceiveProcessing = True
                            s = self._create_B0_35_Data_Request(strlist = "3c 00 0f 00")
                            self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)
                                
                            await asyncio.sleep(1.0)
                        _sequencerState = SequencerType.Reset
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.Reset:                    ################################################################ Reset ####################################################
                        # log.debug(f"[_sequencer] In Reset state {self.PowerLinkBridgeConnected} and {self.PowerLinkBridgeAlarm}")
                        if self.PowerLinkBridgeConnected and not self.PowerLinkBridgeAlarm:  # if bridge but the alarm panel is not connected then go no further
                            # This sequencer loop is once per second.  That is enough time between LookForPowerlinkBridge and here to make the connection and get a reply to set the variables
                            _sequencerState = SequencerType.LookForPowerlinkBridge
                            log.debug(f"[_sequencer] Waiting for Alarm Panel to connect to the Bridge")
                        else:
                            reset_vars()
                            _sequencerState = SequencerType.InitialisePanel
                        continue   # just do the while loop

                    elif delay_loops > 0:
                        delay_loops = delay_loops - 1
                        _clearPanelErrorMessages() # Clear all panel reported errors for the duration of the delay
                        continue   # do all the basic connection checks above and then just do the while loop

                    elif not self.pmDownloadMode and not self.ForceStandardMode and self.PanelWantsToEnrol:     #################################### PanelWantsToEnrol ####################################################
                        log.debug("[_sequencer] Panel wants to auto enroll and not downloading so sending Auto Enroll")
                        self.PanelWantsToEnrol = False
                        self._sendMsgENROLL(True)
                        delay_loops = 2
                        continue   # just do the while loop

                    elif not self.pmDownloadMode and not self.ForceStandardMode and self.PanelKeepAlive and not self.allowAckToTriggerRestore and _sequencerState in [SequencerType.InitialisePanel, SequencerType.WaitingForPanelDetails]:  ###### PanelKeepAlive ################################################
                        log.debug("[_sequencer] Panel Powerlink Keep Alive so assume that panel wants to auto enroll and we're not downloading so sending Auto Enroll")
                        self.PanelKeepAlive = False
                        self._sendMsgENROLL(True)
                        delay_loops = 2
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.InitialisePanel:          ################################################################ Initialising ####################################################
                        await asyncio.sleep(1.0)
                        _resetPanelInterface()
                        _clearPanelErrorMessages()
                        if not self.pmGotPanelDetails:
                            self._addMessageToSendList("MSG_DOWNLOAD_3C", options=[ [3, convertByteArray(self.DownloadCode)] ])  # 
                        _sequencerState = SequencerType.WaitingForPanelDetails
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.WaitingForPanelDetails:   ################################################################ WaitingForPanelDetails ####################################################

                        # Take care of the first part of initialisation
                        if self.pmGotPanelDetails:          # Got 3C panel data message
                            log.debug("[_sequencer] Got panel details")
                            # ignore all possible errors etc, call the function and ignore the return value
                            _clearPanelErrorMessages()
                            if self.ForceStandardMode:
                                self._addMessageToSendList("MSG_EXIT")  # when we receive a 3C we know that the panel is in download mode, so exit download mode
                                _sequencerState = SequencerType.AimingForStandard
                            else:
                                self.firstSendOfDownloadEprom = self._getUTCTimeFunction()
                                if self.PanelType is not None and (self.PowerLinkBridgeConnected or self.isPowerMaster()):     #
                                    _sequencerState = SequencerType.GettingB0SensorMessages   #
                                    self.EnableB0ReceiveProcessing = True
                                else:
                                    _sequencerState = SequencerType.InitialiseEPROMDownload
                                
                        elif (s := processPanelErrorMessages()) != PanelErrorStates.AllGood:
                            self._clearReceiveResponseList()
                            _clearPanelErrorMessages()
                            delay_loops = 4
                            if s == PanelErrorStates.DespatcherException:
                                # start again, restart the despatcher task
                                _sequencerState = SequencerType.Reset
                            elif s in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.AccessDeniedStop]:
                                _sequencerState = SequencerType.InitialisePanel
                                self.setNextDownloadCode(self.PanelType if self.PanelType is not None else 1)
                                log.debug("[_sequencer]    Moved on to next download code and going to init")
                            elif s == PanelErrorStates.Exit:
                                _sequencerState = SequencerType.InitialisePanel
                            elif s == PanelErrorStates.TimeoutReceived:
                                _sequencerState = SequencerType.InitialisePanel
                                log.debug("[_sequencer]    TimeoutReceived")
                            elif s == PanelErrorStates.DownloadRetryReceived:
                                delay_loops = 10
                                _sequencerState = SequencerType.InitialisePanel
                                log.debug(f"[_sequencer]    DownloadRetryReceived loop = {delay_loops}")
                            # Ignore other errors 
                        elif counter >= 7:     # up to 7 seconds to get panel data message (worst case to also allow for Bridge traffic)
                            _sequencerState = SequencerType.InitialisePanel

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.GettingB0SensorMessages:  ################################################################ GettingB0SensorMessages ##############################################

                        if not self.pmGotPanelDetails or self.PanelType is None: # This should never happen but just in case :)
                            _sequencerState = SequencerType.Reset
                            continue
                            
                        missing = self.checkPanelDataPresent()
                        log.debug(f"[Process Settings]   checkPanelDataPresent missing items {missing}")
                       
                        zoneCnt = pmPanelConfig["CFG_WIRELESS"][self.PanelType] + pmPanelConfig["CFG_WIRED"][self.PanelType]
                        if len(missing) == 0 and len(self.PanelSettings[PanelSetting.ZoneEnrolled]) >= zoneCnt:
                            _sequencerState = SequencerType.CreateSensors

                        elif counter >= 15: # timeout
                            _sequencerState = SequencerType.Reset
                        
                        elif counter % 2 == 0: # every 2 seconds (or so)

                            m = []
                            for a in missing:
                                if a in pmPanelSettingCodes and pmPanelSettingCodes[a].PMasterB0Panel is not None:
                                    m.append(pmPanelSettingCodes[a].PMasterB0Panel) # 35 message types to ask for
                            if len(m) > 0:
                                log.debug(f"[Process Settings]      Type 35 Wanting {m}")
                                tmp = bytearray()
                                for a in m:
                                    y1, y2 = (a & 0xFFFF).to_bytes(2, "little")
                                    tmp = tmp + bytearray([y1, y2])
                                s = self._create_B0_35_Data_Request(strlist = toString(tmp))
                                self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)
                            else:

                                m = []
                                for a in missing:
                                    if a in pmPanelSettingCodes and pmPanelSettingCodes[a].PMasterB0Mess is not None:
                                        m.append(pmPanelSettingCodes[a].PMasterB0Mess)
                                if len(m) > 0:
                                    log.debug(f"[Process Settings]      Wanting {m}")
                                    tmp = [pmSendMsgB0[i].data if isinstance(i, str) and i in pmSendMsgB0 else i for i in m]
                                    s = self._create_B0_Data_Request(taglist = tmp)
                                    self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)

                            #_last_B0_wanted_request_time = tnow

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.InitialiseEPROMDownload:  ################################################################ InitialiseEPROMDownload ##############################################
                        
                        self.EnableB0ReceiveProcessing = False
                        
                        interval = self._getUTCTimeFunction() - self.firstSendOfDownloadEprom

                        if self.DownloadCounter >= DOWNLOAD_RETRY_COUNT or (not EEPROM_DOWNLOAD_ALL and interval > timedelta(seconds=DOWNLOAD_TIMEOUT)): 
                            # Give it DOWNLOAD_RETRY_COUNT attempts start the download
                            # Give it DOWNLOAD_TIMEOUT seconds to complete the download
                            log.warning("[Controller] ********************** Download Timer has Expired, Download has taken too long *********************")
                            self.sendPanelUpdate(AlCondition.DOWNLOAD_TIMEOUT)                 # download timer expired

                            if self.PowerLinkBridgeConnected:
                                log.debug("[Controller] ***************************** Bridge connected so start again ***************************************")
                                # Reset download counter to check for the number of attempts
                                self.DownloadCounter = 0
                                # Delete all existing EPROM data
                                self.pmRawSettings = {}
                                _sequencerState = SequencerType.Reset
                            else:
                                log.debug("[Controller] ************************************* Going to standard mode ***************************************")
                                _sequencerState = SequencerType.AimingForStandard
                        else:
                            # Populate the full list of EEPROM blocks
                            self._populateEPROMDownload()
                            # Send the first EEPROM block to the panel to retrieve
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
                                        _sequencerState = SequencerType.TriggerEPROMDownload
                                        log.debug("[_sequencer] Bridge already in Stealth, continuing to TriggerEPROMDownload")
                                    else:
                                        log.debug("[_sequencer] Sending command to Bridge - Please Turn Stealth ON")
                                        command = 2   # Stealth command
                                        param = 1     # Enter it
                                        self._addMessageToSendList("MSG_PL_BRIDGE", priority = MessagePriority.IMMEDIATE, options=[ [1, command], [2, param] ])  # Tell the Bridge to go in to exclusive mode
                                        command = 1   # Get Status command
                                        param = 0     # Irrelevant
                                        self._addMessageToSendList("MSG_PL_BRIDGE", priority = MessagePriority.IMMEDIATE, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                                        # Continue in this SequencerType until the bridge is in stealth
                                else:
                                    _sequencerState = SequencerType.TriggerEPROMDownload
                            
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.TriggerEPROMDownload:     ################################################################ TriggerEPROMDownload ##############################################

                        self._clearReceiveResponseList()
                        self._emptySendQueue(pri_level = 1)
                        self.DownloadCounter = self.DownloadCounter + 1
                        log.debug("[_sequencer] Asking for panel EEPROM")
                        #self.setNextDownloadCode(self.PanelType if self.PanelType is not None else 1) # Set the next self.DownloadCode to try
                        self._addMessageToSendList("MSG_DOWNLOAD_DL", options=[ [3, convertByteArray(self.DownloadCode)] ])  #
                        # We got a first response, now we can Download the panel EEPROM settings
                        self.lastSendOfDownloadEprom = self._getUTCTimeFunction()
                        # Kick off the download sequence and set associated variables
                        self.pmExpectedResponse = set()
                        self.PanelMode = AlPanelMode.DOWNLOAD
                        self.PartitionState[0].PanelState = AlPanelStatus.DOWNLOADING  # Downloading
                        self.sendPanelUpdate(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
                        log.debug("[_readPanelSettings] Download Ongoing")
                        self.triggeredDownload = True
                        self.pmDownloadInProgress = True
                        self._addMessageToSendList("MSG_DL", options=[ [1, self.myDownloadList.pop(0)] ])  # Read the next block of EEPROM data
                        lastrecv = self.lastRecvTimeOfPanelData
                        _sequencerState = SequencerType.StartedEPROMDownload

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.StartedEPROMDownload:     ################################################################ StartedEPROMDownload ##############################################
                        
                        # We got a first response, now we can Download the panel EEPROM settings
                        if (s := processPanelErrorMessages()) != PanelErrorStates.AllGood:
                            if s in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.DownloadRetryReceived, PanelErrorStates.TimeoutReceived]:
                                self.pmExpectedResponse = set()
                                _sequencerState = SequencerType.InitialiseEPROMDownload
                            elif s == PanelErrorStates.DespatcherException:
                                # start again, restart the despatcher task
                                _sequencerState = SequencerType.Reset
                            else:
                                _sequencerState = SequencerType.InitialisePanel
                        else:
                            interval = self._getUTCTimeFunction() - self.lastSendOfDownloadEprom
                            log.debug(f"[_sequencer] interval={interval}  td={DOWNLOAD_RETRY_DELAY}   self.lastSendOfDownloadEprom(UTC)={self.lastSendOfDownloadEprom}    timenow(UTC)={self._getUTCTimeFunction()}")
                            
                            if interval > timedelta(seconds=DOWNLOAD_RETRY_DELAY):            # Give it this number of seconds to start the downloading
                                _sequencerState = SequencerType.InitialiseEPROMDownload
                            elif lastrecv != self.lastRecvTimeOfPanelData and (self.pmDownloadInProgress or self.pmDownloadComplete):
                                _sequencerState = SequencerType.DoingEPROMDownload
                                
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.DoingEPROMDownload:       ################################################################ DoingEPROMDownload ##############################################
                        
                        if (s := processPanelErrorMessages()) != PanelErrorStates.AllGood:
                            if s in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.DownloadRetryReceived, PanelErrorStates.TimeoutReceived]:
                                self.pmExpectedResponse = set()
                                _sequencerState = SequencerType.InitialiseEPROMDownload
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

                    elif _sequencerState == SequencerType.EPROMDownloadComplete:    ################################################################ EPROMDownloadComplete ##############################################
                        
                        # Check the panel type from EPROM against the panel type from the 3C message to give a basic test of the EPROM download
                        pmPanelTypeNr = self._lookupEpromSingle("panelTypeCode")    
                        if pmPanelTypeNr is None or (pmPanelTypeNr is not None and pmPanelTypeNr == 0xFF):
                            log.error(f"[_sequencer] Lookup of panel type string and model from the EEPROM failed, assuming EEPROM download failed {pmPanelTypeNr=}, going to Standard Mode")
                            _sequencerState = SequencerType.AimingForStandard
                        elif self.PanelType is not None and self.PanelType != pmPanelTypeNr:
                            log.error(f"[_sequencer] Panel Type not set from EEPROM, assuming EEPROM download failed {pmPanelTypeNr=}, going to Standard Mode")
                            _sequencerState = SequencerType.AimingForStandard
                        else:
                            # Process the EPROM data
                            self._processEPROMSettings()
                            self.PanelStatus["Devices"] = self._processKeypadsAndSirens(self.PanelType)
                            self._processX10Settings()
                            _sequencerState = SequencerType.CreateSensors
                            
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.CreateSensors:           ################################################################ CreateSensors ##############################################
                        self._updateAllSensors()
                        self._dumpSensorsToLogFile(True)

                        if self.gotValidUserCode():
                            if self.PowerLinkBridgeConnected:
                                log.debug("[_sequencer] Sending command to Bridge - Stealth OFF")
                                command = 2   # Stealth command
                                param = 0     # Exit it
                                self._addMessageToSendList("MSG_PL_BRIDGE", priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to exit exclusive mode
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self._addMessageToSendList("MSG_PL_BRIDGE", priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                                self.PanelMode = AlPanelMode.POWERLINK_BRIDGED
                                _sequencerState = SequencerType.DoingPowerlinkBridge
                            else:
                                self.PanelMode = AlPanelMode.STANDARD_PLUS
                                _sequencerState = SequencerType.EnrollingPowerlink
                            self.sendPanelUpdate(AlCondition.DOWNLOAD_SUCCESS)   # download completed successfully, panel type matches and got usercode (so assume all sensors etc loaded)
                        else:
                            _sequencerState = SequencerType.AimingForStandard
                            self.sendPanelUpdate(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.EnrollingPowerlink:       ################################################################ EnrollingPowerlink ##############################################
                        
                        if self.PanelMode in [AlPanelMode.POWERLINK]:
                            _sequencerState = SequencerType.DoingPowerlink         # Very unlikely but possible
                        elif counter == 10:
                            self.PanelMode = AlPanelMode.STANDARD_PLUS                    # After 10 attempts to enrol, stay in StandardPlus Emulation Mode
                            _sequencerState = SequencerType.DoingStandardPlus
                        else:
                            self._clearReceiveResponseList()
                            self._emptySendQueue(pri_level = 1)
                            self._addMessageToSendList("MSG_EXIT")
                            log.debug(f"[_sequencer] Try to auto enroll (panel {self.PanelModel})  attempt {counter}")
                            #self._addMessageToSendList("MSG_EXIT")  # Exit download mode
                            self._sendMsgENROLL()  #  Try to enroll with the Download Code that worked for Downloading the EPROM
                            _sequencerState = SequencerType.WaitingForEnrolSuccess
                        
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.WaitingForEnrolSuccess:   ################################################################ WaitingForEnrolSuccess ##############################################

                        if (s := processPanelErrorMessages()) == PanelErrorStates.DespatcherException:
                            # start again, restart the despatcher task
                            _sequencerState = SequencerType.Reset
                        elif s != PanelErrorStates.AllGood:
                            _clearPanelErrorMessages()
                            self.pmExpectedResponse = set()
                            self.PanelMode = AlPanelMode.STANDARD_PLUS
                            _sequencerState = SequencerType.EnrollingPowerlink
                        elif self.PanelMode in [AlPanelMode.POWERLINK]:
                            _sequencerState = SequencerType.DoingPowerlink
                        elif counter == (MAX_TIME_BETWEEN_POWERLINK_ALIVE if self.receivedPowerlinkAcknowledge else 3):   
                            # once we receive a powerlink acknowledge then we wait for the I'm alive message (usually every 30 seconds from the panel)
                            self.PanelMode = AlPanelMode.STANDARD_PLUS
                            #self.setNextDownloadCode(self.PanelType if self.PanelType is not None else 1) # We're going back to Enrol so set the next self.DownloadCode to try
                            _sequencerState = SequencerType.EnrollingPowerlink

                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.AimingForStandard:        ################################################################ AimingForStandard ##############################################
                        
                        self.PanelMode = AlPanelMode.STANDARD
                        if self.isPowerMaster(): # PowerMaster so get B0 data
                            # Powerlink panel so ask the panel for B0 data to get panel details, as these can be asked for and received within download mode we can do it straight away
                            log.debug("[_sequencer] Adding lots of B0 requests to wanted list")
                            #self.B0_Message_Wanted.update([0x20, 0x21, 0x2d, 0x1f, 0x07, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x11, 0x13, 0x14, 0x15, 0x18, 0x1a, 0x19, 0x1b, 0x1d, 0x2f, 0x31, 0x33, 0x1e, 0x24, 0x02, 0x23, 0x3a, 0x4b])

                            # Request User Codes                
                            #PM_Data = convertByteArray("b0 01 35 07 02 ff 08 ff 02 08 00 43")   # Ask for panel settings with 08 00 Usercodes, 00 as the counter
                            #CS = self._calculateCRC(PM_Data)   # returns a bytearray with a single byte
                            #To_Send = bytearray([0x0d]) + PM_Data + CS + bytearray([0x0a])   # Add all the bytearrays together to create the raw PDU
                            #self._addMessageToSendList(To_Send) # , res = [0xB0])   # Wait for B0

                            # Request Sensor Information and State
                            self.B0_Message_Wanted.add("ZONE_NAMES")         # 21
                            self.B0_Message_Wanted.add("ZONE_TYPES")         # 2D
                            self.B0_Message_Wanted.add("DEVICE_TYPES")       # 1F
                            self.B0_Message_Wanted.add("PANEL_STATE")        # 24
                            self.B0_Message_Wanted.add("ZONE_LAST_EVENT")    # 4B
                            self.B0_Message_Wanted.add("ZONE_OPENCLOSE")     # 18
                            self.B0_Message_Wanted.add("ZONE_TEMPS")         # 3D  
                            self.B0_Message_Wanted.add("SENSOR_ENROL")       # 1D
                        else:    # PowerMax get ZONE_NAMES, ZONE_TYPES etc
                            self._addMessageToSendList("MSG_ZONENAME")
                            self._addMessageToSendList("MSG_ZONETYPE")
                        # only if we meet the criteria do we move on to the next step.  Until then just do it
                        _gotoStandardModeStopDownload()
                        _sequencerState = SequencerType.DoingStandard
                        continue   # just do the while loop

                    elif _sequencerState == SequencerType.DoingStandard:            ################################################################ DoingStandard ##############################################
                        # Put all the special standard mode things here
                        # Keep alive functionality
                        self.keep_alive_counter = self.keep_alive_counter + 1
                        if self.SendQueue.empty() and not self.pmDownloadMode and self.keep_alive_counter >= self.KeepAlivePeriod:  #
                            self._reset_keep_alive_messages()
                            self._addMessageToSendList ("MSG_STATUS_SEN")
                        
                        # Do most of this for ALL Panel Types
                        # Only check these every 180 seconds
                        if (counter % 180) == 0:
                            if self.PartitionState[0].PanelState == AlPanelStatus.UNKNOWN:
                                log.debug("[_sequencer] ****************************** Getting Panel Status ********************************")
                                self._addMessageToSendList("MSG_STATUS_SEN")
                            elif self.PartitionState[0].PanelState == AlPanelStatus.DOWNLOADING:
                                log.debug("[_sequencer] ****************************** Exit Download Kicker ********************************")
                                self._addMessageToSendList("MSG_EXIT", priority = MessagePriority.URGENT)
                            elif not self.pmGotPanelDetails:
                                log.debug("[_sequencer] ****************************** Asking For Panel Details ****************************")
                                _sequencerState = SequencerType.InitialisePanel
                            else:
                                # The first time this may create sensors (for PowerMaster, especially those in the range Z33 to Z64 as the A5 message will not have created them)
                                # Subsequent calls make sure we have all zone names, zone types and the sensor list
                                self._updateSensorNamesAndTypes()

                    elif _sequencerState == SequencerType.DoingStandardPlus:        ################################################################ DoingStandardPlus ##############################################
                        
                        # Put all the special standard plus mode things here
                        # Keep alive functionality
                        self.keep_alive_counter = self.keep_alive_counter + 1
                        if self.SendQueue.empty() and not self.pmDownloadMode and self.keep_alive_counter >= self.KeepAlivePeriod:  #
                            self._reset_keep_alive_messages()
                            self._addMessageToSendList ("MSG_STATUS_SEN")

                        if self.PanelMode in [AlPanelMode.POWERLINK]:
                            _sequencerState = SequencerType.DoingPowerlink
                        elif self.PanelMode in [AlPanelMode.POWERLINK_BRIDGED]:  # This is only possible from EPROM Download so it's unlikely to happen, but just in case ....
                            _sequencerState = SequencerType.DoingPowerlinkBridge

                    elif _sequencerState == SequencerType.DoingPowerlink:           ################################################################ DoingPowerlink ##############################################
                        self.PanelMode = AlPanelMode.POWERLINK
                        # Put all the special powerlink mode things here

                        # Keep alive functionality
                        self.keep_alive_counter = self.keep_alive_counter + 1    # This is for me sending to the panel
                        self.powerlink_counter = self.powerlink_counter + 1      # This gets reset to 0 when I receive I'm Alive from the panel

                        if self.powerlink_counter > POWERLINK_IMALIVE_RETRY_DELAY:
                            # Go back to Std+ and re-enroll
                            log.debug(f"[_sequencer] ****************************** Not Received I'm Alive From Panel for {POWERLINK_IMALIVE_RETRY_DELAY} Seconds, going to Std+ **************")
                            self.receivedPowerlinkAcknowledge = False
                            self.PanelMode = AlPanelMode.STANDARD_PLUS
                            _sequencerState = SequencerType.EnrollingPowerlink
                            continue   # just do the while loop

                        if self.SendQueue.empty() and not self.pmDownloadMode and self.keep_alive_counter >= self.KeepAlivePeriod:  #
                            # Every self.KeepAlivePeriod seconds, unless watchdog has been reset
                            self._reset_keep_alive_messages()
                            # Send I'm Alive to the panel so it knows we're still here
                            self._addMessageToSendList ("MSG_ALIVE")

                    elif _sequencerState == SequencerType.DoingPowerlinkBridge:     ################################################################ DoingPowerlinkBridge ##############################################

                        if self.PowerLinkBridgeConnected:
                            if self.PowerLinkBridgeStealth:
                                log.debug("[_sequencer] Sending commands to Bridge to exit stealth and get status")
                                command = 2   # Stealth command
                                param = 0     # Exit it
                                self._addMessageToSendList("MSG_PL_BRIDGE", priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to exit exclusive mode
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self._addMessageToSendList("MSG_PL_BRIDGE", priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                                self.PowerLinkBridgeStealth = False # To make certain it's disabled

                            elif counter % 30 == 0:  # approx every 30 seconds
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self._addMessageToSendList("MSG_PL_BRIDGE", priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                                
                            interval = self._getUTCTimeFunction() - self.B0_LastPanelStateTime # make sure that we get the panel state at most every 45 seconds. If we get it for other reasons then OK
                            if interval >= timedelta(seconds=25):                              # every 25 seconds get the panel state
                                log.debug("[_sequencer] Adding Panel State request to B0 wanted due to timer")
                                self.B0_LastPanelStateTime = self._getUTCTimeFunction()        # to stop it retriggering (although its a set so it should not matter)
                                self.B0_Message_Wanted.add("PANEL_STATE")                      # add 24 panel state. Remember that it's a set so if it's already there then it will only be in once

                    #############################################################################################################################################################
                    ####### Drop through to here to do generic code for DoingStandard, DoingStandardPlus, DoingPowerlinkBridge and DoingPowerlink ###############################
                    #############################################################################################################################################################

                    #if self.isPowerMaster() and counter % 4 == 0:
                        # Dump normal B0 data to the log file
                        #m = []
                        #m.append(myspecialcounter % 256)
                        
                        #log.debug(f"[Process Settings]      myspecialcounter {myspecialcounter}   m={m}")
                        #tmp = [pmSendMsgB0[i].data if isinstance(i, str) and i in pmSendMsgB0 else i for i in m] # Theres only 1 thing in m but do it like this so it can do more than 1
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

                    if not (self.PowerLinkBridgeConnected and self.PowerLinkBridgeProxy) and \
                        (self.PartitionState[0].PanelState == AlPanelStatus.DOWNLOADING or self.PanelMode == AlPanelMode.DOWNLOAD):
                        # We may still be in the downloading state or the panel is in the downloading state
                        _my_panel_state_trigger_count = _my_panel_state_trigger_count - 1
                        log.debug(f"[_sequencer] By here we should be in normal operation, we are in {self.PanelMode.name} panel mode and status is {self.PartitionState[0].PanelState}    {_my_panel_state_trigger_count=}")
                        if _my_panel_state_trigger_count < 0:
                            if self.PanelMode in [AlPanelMode.POWERLINK_BRIDGED]:
                                # Restart the sequence from the beginning
                                _sequencerState = SequencerType.Reset
                            else:
                                _my_panel_state_trigger_count = 10
                                self._reset_keep_alive_messages()
                                self.watchdog_counter = 0
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
                            self.watchdog_counter = 0
                            # Restart the sequence from the beginning
                            _sequencerState = SequencerType.Reset
                        continue   # just do the while loop

                    _my_panel_state_trigger_count = 5
                    
                    if self.PanelResetEvent:
                        # If the user has been in to the installer settings there may have been changes that are relevant to this integration.
                        self.PanelResetEvent = False
                        log.debug(f"[_sequencer] Performing a System Reset so reloading EPROM")
                        self.sendPanelUpdate ( AlCondition.PANEL_RESET )   # push changes through to the host, the panel itself has been reset. Let user decide what action to take.
                        # Restart the sequence from WaitingForPanelDetails.  
                        #    As we have already got panel details this will set up the EPROM download
                        _sequencerState = SequencerType.WaitingForPanelDetails
                        # Reset download counter to check for the number of attempts
                        self.DownloadCounter = 0
                        # Delete all existing EPROM data
                        self.pmRawSettings = {}
                        continue   # just do the while loop

                    if not _sendStartUp:
                        _sendStartUp = True
                        self.sendPanelUpdate(AlCondition.STARTUP_SUCCESS)   # startup completed successfully (in whatever mode)

                    # If Std+ or PL then periodically check and then maybe update the time in the panel
                    if self.AutoSyncTime:
                        if self.isPowerMaster() and self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
                            # PowerMaster Panels
                            if counter % POWERMASTER_CHECK_TIME_INTERVAL == 0 or (counter % 10 == 0 and (self.Panel_Integration_Time_Difference is None or (self.Panel_Integration_Time_Difference is not None and abs(self.Panel_Integration_Time_Difference.total_seconds()) > 5))):
                                # Request Sensor Information and State
                                #      remember that self.B0_Message_Wanted is a set so can only be added once
                                log.debug("[_sequencer] Adding Panel and Sensor State requests")
                                self.B0_Message_Wanted.add("SENSOR_ENROL")       # 1D
                                self.B0_Message_Wanted.add("ZONE_NAMES")         # 21
                                self.B0_Message_Wanted.add("ZONE_TYPES")         # 2D
                                self.B0_Message_Wanted.add("DEVICE_TYPES")       # 1F
                                self.B0_Message_Wanted.add("DEVICE_TYPES")       # 1F  Get the device types to determine whether there have been changes
                                self.B0_Message_Wanted.add("PANEL_STATE")        # 24  To make sure we have the state updated 
                                #self.B0_Message_Wanted.add("ZONE_LAST_EVENT")    # 4B
                                self.B0_Message_Wanted.add("ZONE_OPENCLOSE")     # 18  To make sure we have latest open/close data
                                self.B0_Message_Wanted.add("ZONE_TEMPS")         # 3D  Update the Temps periodically
                                #self.B0_Message_Wanted.add("SENSOR_ENROL")       # 1D
                                #self.B0_Message_Wanted.add(0x19)
                                #self.B0_Message_Wanted.add(0x3A)
                                #self.B0_Message_Wanted.add(0x27)
                        elif self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK]:
                            # PowerMax Panels
                            # We set the time and then check it periodically, and then set it again if different by more than 5 seconds
                            #     every 4 hours (approx) or if not set yet or a big difference (set from B0 data)
                            if counter % POWERMAX_CHECK_TIME_INTERVAL == 0 or (counter % 10 == 0 and (self.Panel_Integration_Time_Difference is None or (self.Panel_Integration_Time_Difference is not None and abs(self.Panel_Integration_Time_Difference.total_seconds()) > 5))):
                                # Get the time from the panel (this will compare to local time and set the panel time if different)
                                self._addMessageToSendList("MSG_GETTIME", priority = MessagePriority.URGENT)

                    # Check all error conditions sent from the panel
                    dotrigger = False
                    while (s := processPanelErrorMessages()) != PanelErrorStates.AllGood: # An error state from the panel so process it
                        match s:
                            case PanelErrorStates.AccessDeniedPin:
                                log.debug("[_sequencer] Attempt to send a command message to the panel that has been denied, wrong pin code used")
                                # INTERFACE : tell user that wrong pin has been used
                                self._reset_watchdog_timeout()
                                self.sendPanelUpdate(AlCondition.PIN_REJECTED)  # push changes through to the host, the pin has been rejected
                            case PanelErrorStates.DespatcherException:
                                # restart the despatcher task
                                startDespatcher()
                            case PanelErrorStates.AccessDeniedCommand:
                                log.debug("[_sequencer] Attempt to send a command message to the panel that has been rejected")
                                self._reset_watchdog_timeout()
                                self.sendPanelUpdate(AlCondition.COMMAND_REJECTED)  # push changes through to the host, something has been rejected (other than the pin)
                            case PanelErrorStates.AccessDeniedDownload | PanelErrorStates.AccessDeniedStop:
                                log.debug("[_sequencer] Attempt to download from the panel that has been rejected, assumed to be from get/set time")
                                # reset the download params just in case it's not a get/set time
                                self.pmDownloadInProgress = False
                                self.pmDownloadMode = False
                                dotrigger = True
                            case PanelErrorStates.Exit:
                                log.debug(f"[_sequencer] Received a Exit state, we assume that DOWNLOAD was called and rejected by the panel")
                                if 0x3C in self.pmExpectedResponse:    # We sent DOWNLOAD to the panel (probably to set the time) and it has responded with EXIT
                                    self.pmExpectedResponse.remove(0x3C)  #
                            case PanelErrorStates.DownloadRetryReceived:
                                log.debug(f"[_sequencer] Received a Download Retry and dont know why {str(s)}")
                                dotrigger = True
                            case PanelErrorStates.TimeoutReceived:
                                log.debug(f"[_sequencer] Received a Panel state Timeout")
                                # Reset Send state (clear queue and reset flags)
                                self._clearReceiveResponseList()
                                #self._emptySendQueue(pri_level = 1)
                                dotrigger = True
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
                        if not self.StopTryingDownload and watchdog_list[watchdog_pos] > 0:
                            self.pmExpectedResponse = set()
                            if self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
                                log.debug("[_sequencer]               **************** Going to re-establish panel connection ***************")
                                #self.PanelMode = AlPanelMode.STANDARD_PLUS
                                #self.firstSendOfDownloadEprom = self._getUTCTimeFunction()
                                _sequencerState = SequencerType.InitialisePanel
                                self.sendPanelUpdate(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired, going to standard (plus) mode
                                continue 
                            else:
                                log.debug("[_sequencer]               **************** Giving up and going to Standard Mode, too many watchdog timeouts with the connection ***************")
                                self._gotoStandardModeStopDownload()
                                self.sendPanelUpdate(AlCondition.WATCHDOG_TIMEOUT_GIVINGUP)   # watchdog timer expired, going to standard (plus) mode
                        else:
                            log.debug("[_sequencer]               ******************* Trigger Restore Status *******************")
                            self.sendPanelUpdate(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired, going to try again
                            # Reset Send state (clear queue and reset flags)
                            self._clearReceiveResponseList()
                            self._emptySendQueue(pri_level = 1)
                            dotrigger = True 

                        # Overwrite the oldest entry and set it to 1 day in seconds. Keep the stats going in all modes for the statistics
                        #    Note that the asyncio 1 second sleep does not create an accurate time and this may be slightly more than 24 hours.
                        watchdog_list[watchdog_pos] = 60 * 60 * 24  # seconds in 1 day
                        log.debug("[_sequencer]               Watchdog counter array, current=" + str(watchdog_pos))
                        log.debug("[_sequencer]                       " + str(watchdog_list))

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
                            if len(self.B0_Message_Waiting) > 0:  # have we received the data that we last asked for
                                log.debug(f"[_sequencer] ****************************** Waiting For B0_Message_Waiting **************************** {self.B0_Message_Waiting}")
                                self.B0_Message_Wanted.update(self.B0_Message_Waiting) # ask again for them
                            if len(self.B0_Message_Wanted) > 0:
                                log.debug(f"[_sequencer] ****************************** Asking For B0_Message_Wanted **************************** {self.B0_Message_Wanted}     timediff={diff}")
                                tmp = {pmSendMsgB0[i].data if isinstance(i, str) and i in pmSendMsgB0 else i for i in self.B0_Message_Wanted}
                                self.B0_Message_Wanted = set()
                                s = self._create_B0_Data_Request(taglist = list(tmp))
                                self._addMessageToSendList(s)
                                self.B0_Message_Waiting.update(tmp)
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
    def vp_data_received(self, data):
        """Add incoming data to ReceiveData."""
        if self.suspendAllOperations:
            return
        if not self.firstCmdSent:
            log.debug("[data receiver] Ignoring garbage data: " + toString(data))
            return
        #log.debug('[data receiver] received data: %s', toString(data))
        self.lastRecvTimeOfPanelData = self._getUTCTimeFunction()
        try:
            for databyte in data:
                # process a single byte at a time
                self._handle_received_byte(databyte)
        except Exception as ex:
            #log.warning(f"[Data Received] Exception {ex}")
            log.exception(ex)

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
                #log.debug(f"[data receiver] Variable length Message Being Received  Message Type {hex(self.ReceiveData[1]).upper()}     pmIncomingPduLen {self.pmIncomingPduLen}   data var {int(data)}")

        # If we were expecting a message of a particular length (i.e. self.pmIncomingPduLen > 0) and what we have is already greater then that length then dump the message and resynchronise.
        if 0 < self.pmIncomingPduLen <= pdu_len:                             # waiting for pmIncomingPduLen bytes but got more and haven't been able to validate a PDU
            log.info(f"[data receiver] PDU Too Large: Dumping current buffer {toString(self.ReceiveData)}    The next byte is {hex(data).upper()}")
            pdu_len = 0                                                      # Reset the incoming data to 0 length
            self._resetMessageData()

        # If this is the start of a new message, 
        #      then check to ensure it is a PACKET_HEADER (message preamble)
        if pdu_len == 0:
            self._resetMessageData()
            if data == PACKET_HEADER:  # preamble
                self.ReceiveData.append(data)
                #log.debug("[data receiver] Starting PDU " + toString(self.ReceiveData))
            # else we're trying to resync and walking through the bytes waiting for a PACKET_HEADER preamble byte

        elif pdu_len == 1:
            #log.debug("[data receiver] Received message Type %d", data)
            if data != 0x00 and data in pmReceiveMsg:                      # Is it a message type that we know about
                self.pmCurrentPDU = pmReceiveMsg[data]                     # set to current message type parameter settings for length, does it need an ack etc
                self.ReceiveData.append(data)                                # Add on the message type to the buffer
                if not isinstance(self.pmCurrentPDU, dict):
                    self.pmIncomingPduLen = self.pmCurrentPDU.length         # for variable length messages this is the fixed length and will work with this algorithm until updated.
                #log.debug(f"[data receiver] Building PDU: It's a message {hex(data).upper()}; pmIncomingPduLen = {self.pmIncomingPduLen}   variable = {self.pmCurrentPDU.isvariablelength}")
            elif data == 0x00 or data == 0xFD:                               # Special case for pocket and PowerMaster 10
                #log.info(f"[data receiver] Received message type {hex(data).upper()} so not processing it")
                self._resetMessageData()
            else:
                # build an unknown PDU. As the length is not known, leave self.pmIncomingPduLen set to 0 so we just look for PACKET_FOOTER as the end of the PDU
                self.pmCurrentPDU = pmReceiveMsg[0]                        # Set to unknown message structure to get settings, varlenbytepos is -1
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

        elif self.pmFlexibleLength > 0 and data == PACKET_FOOTER and pdu_len + 1 < self.pmIncomingPduLen and (self.pmIncomingPduLen - pdu_len) < self.pmFlexibleLength:
            # Only do this when:
            #       Looking for "flexible" messages
            #              At the time of writing this, only the 0x3F EEPROM Download PDU does this with some PowerMaster panels
            #       Have got the PACKET_FOOTER message terminator
            #       We have not yet received all bytes we expect to get
            #       We are within 5 bytes of the expected message length, self.pmIncomingPduLen - pdu_len is the old length as we already have another byte in data
            #              At the time of writing this, the 0x3F was always only up to 3 bytes short of the expected length and it would pass the CRC checks
            # Do not do this when (pdu_len + 1 == self.pmIncomingPduLen) i.e. the correct length
            # There is possibly a fault with some panels as they sometimes do not send the full EEPROM data.
            #    - Rather than making it panel specific I decided to make this a generic capability
            self.ReceiveData.append(data)  # add byte to the message buffer
            if self.pmCurrentPDU.ignorechecksum or self._validatePDU(self.ReceiveData):  # if the message passes CRC checks then process it
                # We've got a validated message
                log.debug("[data receiver] Validated PDU: Got Validated PDU type 0x%02x   data %s", int(self.ReceiveData[1]), toString(self.ReceiveData))
                self._processReceivedMessage(ackneeded=self.pmCurrentPDU.ackneeded, debugp=self.pmCurrentPDU.debugprint, msg=self.pmCurrentPDU.msg, data=self.ReceiveData)
                self._resetMessageData()

        elif (self.pmIncomingPduLen == 0 and data == PACKET_FOOTER) or (pdu_len + 1 == self.pmIncomingPduLen): # postamble (the +1 is to include the current data byte)
            # (waiting for PACKET_FOOTER and got it) OR (actual length == calculated expected length)
            self.ReceiveData.append(data)  # add byte to the message buffer
            #log.debug("[data receiver] Building PDU: Checking it " + toString(self.ReceiveData))
            msgType = self.ReceiveData[1]
            if self.pmCurrentPDU.ignorechecksum or self._validatePDU(self.ReceiveData):
                # We've got a validated message
                #log.debug("[data receiver] Building PDU: Got Validated PDU type 0x%02x   data %s", int(msgType), toString(self.ReceiveData))
                if self.pmCurrentPDU.varlenbytepos < 0:  # is it an unknown message i.e. varlenbytepos is -1
                    log.warning(f"[data receiver] Received Valid but Unknown PDU {hex(msgType)}")
                    self._sendAck()  # assume we need to send an ack for an unknown message
                else:  # Process the received known message
                    self._processReceivedMessage(ackneeded=self.pmCurrentPDU.ackneeded, debugp=self.pmCurrentPDU.debugprint, msg=self.pmCurrentPDU.msg, data=self.ReceiveData)
                self._resetMessageData()
            else:
                # CRC check failed
                a = self._calculateCRC(self.ReceiveData[1:-2])[0]  # this is just used to output to the log file
                if len(self.ReceiveData) > PACKET_MAX_SIZE:
                    # If the length exceeds the max PDU size from the panel then stop and resync
                    log.warning(f"[data receiver] PDU with CRC error Message = {toString(self.ReceiveData)}   checksum calcs {hex(a).upper()}")
                    self._processCRCFailure()
                    self._resetMessageData()
                elif self.pmIncomingPduLen == 0:
                    if msgType in pmReceiveMsg:
                        # A known message with zero length and an incorrect checksum. Reset the message data and resync
                        log.warning(f"[data receiver] Warning : Construction of zero length incoming packet validation failed - Message = {toString(self.ReceiveData)}  checksum calcs {hex(a).upper()}")

                        # Send an ack even though the its an invalid packet to prevent the panel getting confused
                        if self.pmCurrentPDU.ackneeded:
                            # log.debug("[data receiver] Sending an ack as needed by last panel status message " + hex(msgType).upper())
                            self._sendAck(data=self.ReceiveData)

                        # Dump the message and carry on
                        self._processCRCFailure()
                        self._resetMessageData()
                    else:  # if msgType != 0xF1:        # ignore CRC errors on F1 message
                        # When self.pmIncomingPduLen == 0 then the message is unknown, the length is not known and we're waiting for a PACKET_FOOTER where the checksum is correct, so carry on
                        log.debug(f"[data receiver] Building PDU: Length is {len(self.ReceiveData)} bytes (apparently PDU not complete)  {toString(self.ReceiveData)}  checksum calcs {hex(a).upper()}")
                else:
                    # When here then the message is a known message type of the correct length but has failed it's validation
                    log.warning(f"[data receiver] Warning : Construction of incoming packet validation failed - Message = {toString(self.ReceiveData)}   checksum calcs {hex(a).upper()}")

                    # Send an ack even though the its an invalid packet to prevent the panel getting confused
                    if self.pmCurrentPDU.ackneeded:
                        # log.debug("[data receiver] Sending an ack as needed by last panel status message " + hex(msgType).upper())
                        self._sendAck(data=self.ReceiveData)

                    # Dump the message and carry on
                    self._processCRCFailure()
                    self._resetMessageData()

        elif pdu_len <= PACKET_MAX_SIZE:
            # log.debug("[data receiver] Current PDU " + toString(self.ReceiveData) + "    adding " + str(hex(data).upper()))
            self.ReceiveData.append(data)
        else:
            log.debug("[data receiver] Dumping Current PDU " + toString(self.ReceiveData))
            self._resetMessageData()
        # log.debug("[data receiver] Building PDU " + toString(self.ReceiveData))

    def _resetMessageData(self):
        # clear our buffer again so we can receive a new packet.
        self.ReceiveData = bytearray(b"")  # messages should never be longer than PACKET_MAX_SIZE
        # Reset control variables ready for next time
        self.pmCurrentPDU = pmReceiveMsg[0]
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
                    self._performDisconnect(AlTerminationType.CRC_ERROR)
                self.pmFirstCRCErrorTime = self._getUTCTimeFunction()

    def _processReceivedMessage(self, ackneeded, debugp, data, msg):
        # Unknown Message has been received
        msgType = data[1]
        # log.debug("[data receiver] *** Received validated message " + hex(msgType).upper() + "   data " + toString(data))
        # Send an ACK if needed
        if ackneeded:
            # log.debug("[data receiver] Sending an ack as needed by last panel status message " + hex(msgType).upper())
            self._sendAck(data=data)

        # Check response
        #tmplength = len(self.pmExpectedResponse)
        if len(self.pmExpectedResponse) > 0:  # and msgType != 2:   # 2 is a simple acknowledge from the panel so ignore those
            # We've sent something and are waiting for a reponse - this is it
            if msgType in self.pmExpectedResponse:
                # while msgType in self.pmExpectedResponse:
                self.pmExpectedResponse.remove(msgType)

        if data is not None and debugp == DebugLevel.FULL:
            log.debug(f"[_processReceivedMessage] Received {msg}   raw data {toString(data)}          response list {[hex(no).upper() for no in self.pmExpectedResponse]}")
        elif data is not None and debugp == DebugLevel.CMD:
            log.debug(f"[_processReceivedMessage] Received {msg}   raw data {toString(data[1:4])}          response list {[hex(no).upper() for no in self.pmExpectedResponse]}")

        # Handle the message
        if self.packet_callback is not None:
            self.packet_callback(data)

    # Send an achnowledge back to the panel
    def _sendAck(self, data=bytearray(b"")):
        """ Send ACK if packet is valid """

        iscommand = data is not None and len(data) > 2 and data[1] >= 0x40   # command message types
        panel_state_enrolled = not self.pmDownloadMode and self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]

        # There are 2 types of acknowledge that we can send to the panel
        #    Normal    : For a normal message
        #    Powerlink : For when we are in powerlink mode
        #if not isbase and panel_state_enrolled and ispm:
        if iscommand and panel_state_enrolled:             # When in Std+, PL Mode and message type is at or above 0x40
            message = pmSendMsg["MSG_ACK_PLINK"]
        else:
            message = pmSendMsg["MSG_ACK"]   # MSG_ACK
        assert message is not None
        e = VisonicListEntry(command=message)
        self._addMessageToSendList(message = e, priority = MessagePriority.ACK)

    # Function to send all PDU messages to the panel, using a mutex lock to combine acknowledges and other message sends
    def _sendPdu(self, instruction: VisonicListEntry) -> float:        # return the delay before sending the next PDU
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
            
            # log.debug('[sendPdu] input data: %s', toString(packet))
            # First add header (PACKET_HEADER), then the packet, then crc and footer (PACKET_FOOTER)
            sData = b"\x0D"
            sData += data
            if self.isPowerMaster() and (data[0] == 0xB0 or data[0] == 0xAB):
                sData += self._calculateCRCAlt(data)
            else:
                sData += self._calculateCRC(data)
            sData += b"\x0A"
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
            if sData[1] != ACK_MESSAGE:  # the message is not an acknowledge back to the panel, then save it
                self.pmLastSentMessage = instruction
        else:
            log.debug("[sendPdu]      Comms transport has been set to none, must be in process of terminating comms")

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
        #elif command is not None:
        #    # Do not log the full raw data as it may contain the user code
        #    log.debug(f"[sendPdu] Sent Command ({command.msg})    <Obfuscated>   waiting for message response {[hex(no).upper() for no in self.pmExpectedResponse]}")

        if command is not None and command.waittime > 0.0:
            return command.waittime
        return -1.0           

    def _addMessageToSendList(self, message : str | bytearray | VisonicListEntry, priority : MessagePriority = MessagePriority.NORMAL, options : list = [], response : list = None):
        if message is not None:
            if isinstance(message, str):
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

    def _getLastSentMessage(self):
        return self.pmLastSentMessage

    def _updateSensorNamesAndTypes(self, force = False) -> bool:
        """ Retrieve Zone Names and Zone Types if needed """
        # This function checks to determine if the Zone Names and Zone Types have been retrieved and if not it gets them
        retval = None
        if self.PanelType is not None and 0 <= self.PanelType <= 16:
            retval = False
            zoneCnt = pmPanelConfig["CFG_WIRELESS"][self.PanelType] + pmPanelConfig["CFG_WIRED"][self.PanelType]
            if self.isPowerMaster():
                if force or len(self.PanelSettings[PanelSetting.ZoneNames]) < zoneCnt:
                    retval = True
                    log.debug("[updateSensorNamesAndTypes] Trying to get the zone names, zone count = " + str(zoneCnt) + "  I've only got " + str(len(self.PanelSettings[PanelSetting.ZoneNames])) + " zone names")
                    self.B0_Message_Wanted.add("ZONE_NAMES")
                if force or len(self.PanelSettings[PanelSetting.ZoneTypes]) < zoneCnt:
                    retval = True
                    log.debug("[updateSensorNamesAndTypes] Trying to get the zone types, zone count = " + str(zoneCnt) + "  I've only got " + str(len(self.PanelSettings[PanelSetting.ZoneTypes])) + " zone types")
                    self.B0_Message_Wanted.add("ZONE_TYPES")
                #if force or len(self.SensorList) == 0:
                #    retval = True
                #    log.debug("[updateSensorNamesAndTypes] Trying to get the sensor status")
                #    self.B0_Message_Wanted.add("DEVICE_TYPES")
            else:
                if force or len(self.PanelSettings[PanelSetting.ZoneNames]) < zoneCnt:
                    retval = True
                    log.debug("[updateSensorNamesAndTypes] Trying to get the zone names again zone count = " + str(zoneCnt) + "  I've only got " + str(len(self.PanelSettings[PanelSetting.ZoneNames])) + " zone names")
                    self._addMessageToSendList("MSG_ZONENAME")
                if force or len(self.PanelSettings[PanelSetting.ZoneTypes]) < zoneCnt:
                    retval = True
                    log.debug("[updateSensorNamesAndTypes] Trying to get the zone types again zone count = " + str(zoneCnt) + "  I've only got " + str(len(self.PanelSettings[PanelSetting.ZoneTypes])) + " zone types")
                    self._addMessageToSendList("MSG_ZONETYPE")
        else:
            log.debug(f"[updateSensorNamesAndTypes] Warning: Panel Type error {self.PanelType=}")
        return retval


    def _validateEPROMSettingsBlock(self, block) -> bool:
        page = block[1]
        index = block[0]
        settings_len = block[2]
        
        retlen = settings_len
        retval = bytearray()
        while page in self.pmRawSettings and retlen > 0:
            rawset = self.pmRawSettings[page][index : index + retlen]
            retval = retval + rawset
            page = page + 1
            retlen = retlen - len(rawset)
            index = 0
        log.debug(f"[_validateEPROMSettingsBlock]    page {block[1]:>3}   index {block[0]:>3}   length {block[2]:>3}     {'Already Got It' if settings_len == len(retval) else 'Not Got It'}")
        return settings_len == len(retval)

    def _populateEPROMDownload(self):
        """ Populate the EEPROM Download List """

        # Empty list and start at the beginning
        self.myDownloadList = []

        if EEPROM_DOWNLOAD_ALL:
            for page in range(0, 256):
                mystr = '00 ' + format(page, '02x').upper() + ' 80 00'
                if not self._validateEPROMSettingsBlock(convertByteArray(mystr)):
                    self.myDownloadList.append(convertByteArray(mystr))
                mystr = '80 ' + format(page, '02x').upper() + ' 80 00'
                if not self._validateEPROMSettingsBlock(convertByteArray(mystr)):
                    self.myDownloadList.append(convertByteArray(mystr))
        else:
            lenMax = len(pmBlockDownload["PowerMax"])
            lenMaster = len(pmBlockDownload["PowerMaster"])

            # log.debug("lenMax = " + str(lenMax) + "    lenMaster = " + str(lenMaster))

            for block in pmBlockDownload["PowerMax"]:
                if not self._validateEPROMSettingsBlock(block):
                    self.myDownloadList.append(block)

            if self.isPowerMaster():
                for block in pmBlockDownload["PowerMaster"]:
                    if not self._validateEPROMSettingsBlock(block):
                        self.myDownloadList.append(block)


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
        
        self.B0_logCounter = 0
        
        self.B0_temp = {}

        # Time difference between Panel and Integration 
        self.Panel_Integration_Time_Difference = None
        
        self.beezero_024B_sensorcount = None

    # _saveEPROMSettings: add a certain setting to the settings table
    #      When we send a MSG_DL and insert the 4 bytes from pmDownloadItem_t, what we're doing is setting the page, index and len
    # This function stores the downloaded status and EEPROM data
    def _saveEPROMSettings(self, page, index, setting):
        settings_len = len(setting)
        wrappoint = index + settings_len - 0x100
        sett = [bytearray(b""), bytearray(b"")]

        #log.debug(f"[Write Settings]   Entering Function  page {page}   index {index}    length {settings_len}")
        if settings_len > 0xB1:
            log.debug("[Write Settings] ********************* Write Settings too long ********************")
            return

        if wrappoint > 0:
            # log.debug("[Write Settings] The write settings data is Split across 2 pages")
            sett[0] = setting[: settings_len - wrappoint]  # bug fix in 0.0.6, removed the -1
            sett[1] = setting[settings_len - wrappoint :]
            # log.debug(f"[Write Settings]         Wrapping  original len {len(setting)}   left len {len(sett[0])}   right len {len(sett[1])}")
            wrappoint = 1
        else:
            sett[0] = setting
            wrappoint = 0

        for i in range(0, wrappoint + 1):
            if (page + i) not in self.pmRawSettings:
                self.pmRawSettings[page + i] = bytearray()
                for dummy in range(0, 256):
                    self.pmRawSettings[page + i].append(255)
                if len(self.pmRawSettings[page + i]) != 256:
                    log.debug(f"[Write Settings] the EEPROM settings is incorrect for page {page + i}")
                # else:
                #    log.debug("[Write Settings] WHOOOPEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE")

            settings_len = len(sett[i])
            if i == 1:
                index = 0
            #log.debug(f"[Write Settings]         Writing settings page {page+i}  index {index}    length {settings_len}")
            self.pmRawSettings[page + i] = self.pmRawSettings[page + i][0:index] + sett[i] + self.pmRawSettings[page + i][index + settings_len :]
            #if len(self.pmRawSettings[page + i]) != 256:
            #    log.debug(f"[Write Settings] OOOOOOOOOOOOOOOOOOOO len = {len(self.pmRawSettings[page + i])}")
            # else:
            #    log.debug(f"[Write Settings] Page {page+i} is now {toString(self.pmRawSettings[page + i])}")

    # _readEPROMSettingsPageIndex
    # This function retrieves the downloaded status and EEPROM data
    def _readEPROMSettingsPageIndex(self, page, index, settings_len):
        retlen = settings_len
        retval = bytearray()
        while index > 255:
            page = page + 1
            index = index - 256
        
        if self.pmDownloadComplete:
            #log.debug(f"[_readEPROMSettingsPageIndex]    Entering Function  page {page}   index {index}    length {settings_len}")
            while page in self.pmRawSettings and retlen > 0:
                rawset = self.pmRawSettings[page][index : index + retlen]
                retval = retval + rawset
                page = page + 1
                retlen = retlen - len(rawset)
                index = 0
            if settings_len == len(retval):
                return retval
        log.debug(f"[_readEPROMSettingsPageIndex]     Sorry but you havent downloaded that part of the EEPROM data     page={hex(page)} index={hex(index)} length={settings_len}")
        
        # return a bytearray filled with 0xFF values
        retval = bytearray()
        for dummy in range(0, settings_len):
            retval.append(255)
        return retval

    # this can be called from an entry in pmDownloadItem_t such as
    #      page index lenhigh lenlow
    def _readEPROMSettings(self, item):
        return self._readEPROMSettingsPageIndex(item[0], item[1], item[3] + (0x100 * item[2]))

    # This function was going to save the settings (including EEPROM) to a file
    def _dumpEPROMSettings(self):
        log.debug("Dumping EEPROM Settings")
        for p in range(0, 0x100):  ## assume page can go from 0 to 255
            if p in self.pmRawSettings:
                for j in range(0, 0x100, 0x10):  ## assume that each page can be 256 bytes long, step by 16 bytes
                    # do not display the rows with pin numbers
                    # if not (( p == 1 and j == 240 ) or (p == 2 and j == 0) or (p == 10 and j >= 140)):
                    if EEPROM_DOWNLOAD_ALL or ((p != 1 or j != 240) and (p != 2 or j != 0) and (p != 10 or j <= 140)):
                        if j <= len(self.pmRawSettings[p]):
                            s = toString(self.pmRawSettings[p][j : j + 0x10])
                            log.debug(f"{p:3}:{j:3}  {s}")

    def _calcBoolFromIntMask(self, val, mask) -> bool:
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
                    #log.debug(f"[_lookupEprom] {page} {pos+j}  character {nr}   {chr(nr[0])}")
                    if nr[0] != 0xFF:
                        myvalue = myvalue + chr(nr[0])
                #log.debug(f"[_lookupEprom] myvalue  <{myvalue}>")
                myvalue = myvalue.strip()
                #log.debug(f"[_lookupEprom] myvalue stripped <{myvalue}>")
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
        v = self._lookupEprom(pmDecodePanelSettings[key])
        if len(v) >= 1:
            return v[0]
        return None

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

    def _updateSensor(self, zone) -> bool:

        if not self.ForceStandardMode:
            m = self.checkPanelDataPresent()
            if m is None:
                return
            elif len(m) > 0:
                log.debug(f"[Process Settings]       Not Forcing Standard but not got all panel settings so not updating sensor {m}")
                return

        enrolled        = self.getPanelZoneSetting(PanelSetting.ZoneEnrolled,  zone)
        zoneType        = self.getPanelZoneSetting(PanelSetting.ZoneTypes,     zone)
        zoneChime       = self.getPanelZoneSetting(PanelSetting.ZoneChime,     zone)
        device_type     = self.getPanelZoneSetting(PanelSetting.DeviceTypes,   zone)
        motiondelaytime = self.getPanelZoneSetting(PanelSetting.ZoneDelay,     zone)
        zoneNames       = self.getPanelZoneSetting(PanelSetting.ZoneNames,     zone)
        partitionData   = self.getPanelZoneSetting(PanelSetting.PartitionData, zone)

        part = set()
        if self.partitionsEnabled and partitionData is not None:
            partitionCnt = pmPanelConfig["CFG_PARTITIONS"][self.PanelType]
            for j in range(0, partitionCnt):  # max partitions of all panels
                if (partitionData & (1 << j)) != 0:
                    #log.debug(f"[Process Settings]     Adding to partition list - ref {zone}  Z{(zone+1):0>2}     Partition {(j+1)}")
                    part.add(j + 1)
                    if partitionCnt > j:
                        self.PartitionsInUse.add(j+1)  # overall used partitions, this is a set so no repetitions allowed
        else:
            part.add(1)

        if enrolled is not None and enrolled:
            log.debug(f"[Process Settings] _updateSensor Zone {zone} : {enrolled=} {zoneType=} {zoneChime=} {device_type=} {motiondelaytime=} {zoneNames=} {partitionData=}")

        if enrolled is None or not enrolled:
            if zone in self.SensorList:
                log.info(f"[Process Settings]       Removing sensor Z{zone+1:0>2} as it is not enrolled in Panel EEPROM Data")
                del self.SensorList[zone]
            return
                    
        updated = False
        created_new_sensor = False
        
        if zone not in self.SensorList:
            self.SensorList[zone] = SensorDevice( id = zone + 1 )
            created_new_sensor = True

        zoneName = "unknown"
        if zone < len(self.PanelSettings[PanelSetting.ZoneNames]):     # 
            zoneName = pmZoneName[zoneNames & 0x1F]
        
        if zoneNames is not None and self.SensorList[zone].zname != zoneName:
            updated = True
            self.SensorList[zone].zname = zoneName

        if device_type is not None:
            sensorType = AlSensorType.UNKNOWN
            sensorModel = "Model Unknown"

            if self.isPowerMaster(): # PowerMaster models
                if device_type in pmZoneSensorMaster:
                    sensorType = pmZoneSensorMaster[device_type].func
                    sensorModel = pmZoneSensorMaster[device_type].name
                    if motiondelaytime is not None and motiondelaytime == 0xFFFF and (sensorType == AlSensorType.MOTION or sensorType == AlSensorType.CAMERA):
                        log.debug(f"[_updateSensor] PowerMaster Sensor Z{zone+1:0>2} has no motion delay set (Sensor will only be useful when the panel is armed)")
                else:
                    log.debug("[_updateSensor] Found unknown sensor type " + hex(device_type))
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

                if device_type in pmZoneSensorMax:
                    sensorType = pmZoneSensorMax[device_type].func
                    sensorModel = pmZoneSensorMax[device_type].name
                elif tmpid in pmZoneSensorMaxGeneric_t:
                    # if tmpid in pmZoneSensorMaxGeneric_t:
                    sensorType = pmZoneSensorMaxGeneric_t[tmpid]
                else:
                    log.debug("[_updateSensor] Found unknown sensor type " + str(device_type))

            if self.SensorList[zone].sid != device_type:
                updated = True
                self.SensorList[zone].sid = device_type
                self.SensorList[zone].stype = sensorType
                self.SensorList[zone].model = sensorModel

        if zoneChime is not None and 0 <= zoneChime <= 2:
            #log.debug(f"Setting Zone Chime {zoneChime}  {pmZoneChimeKey[zoneChime]}")
            self.PanelSettings[PanelSetting.ZoneChime][zone] = zoneChime
            if self.SensorList[zone].zchime != pmZoneChimeKey[zoneChime]:
                updated = True
                self.SensorList[zone].zchimeref = zoneChime
                if zoneChime < len(pmZoneChimeKey):
                    self.SensorList[zone].zchime = pmZoneChimeKey[zoneChime]
                else:
                    self.SensorList[zone].zchime = "undefined " + str(zoneChime)
                    
        if zoneType is not None:
            self.PanelSettings[PanelSetting.ZoneTypes][zone] = zoneType
        elif zone < len(self.PanelSettings[PanelSetting.ZoneTypes]):     # 
            zoneType = self.PanelSettings[PanelSetting.ZoneTypes][zone]
        else:
            zoneType = None
        
        if zoneType is not None and self.SensorList[zone].ztype != zoneType:
            updated = True
            self.SensorList[zone].ztype = zoneType
            if zoneType < len(pmZoneTypeKey):
                self.SensorList[zone].ztypeName = pmZoneTypeKey[zoneType]
            else:
                self.SensorList[zone].ztypeName = "undefined " + str(zoneType)   # undefined

        if motiondelaytime is not None and motiondelaytime != 0xFFFF:
            if self.SensorList[zone].motiondelaytime != motiondelaytime:
                updated = True
                self.SensorList[zone].motiondelaytime = motiondelaytime

        if self.SensorList[zone].partition != part:
            updated = True
            log.debug(f"[Process Settings]     Change to partition list - sensor {zone}")
            # If we get EEPROM data, assume it is all correct and override any existing settings (as some were assumptions)
            self.SensorList[zone].partition = part

        # if the new value is True and the old Value is False then push change Enrolled
        enrolled_push_change = (enrolled and not self.SensorList[zone].enrolled) if self.SensorList[zone].enrolled is not None and enrolled is not None else False
        if enrolled is not None:
            self.SensorList[zone].enrolled = enrolled

        if created_new_sensor:
            self.SensorList[zone].onChange(self.mySensorChangeHandler)
            if self.onNewSensorHandler is not None:
                self.onNewSensorHandler(self.SensorList[zone])
            
        # Enrolled is only sent on enrol and not on change to not enrolled
        if enrolled_push_change:
            self.SensorList[zone].pushChange(AlSensorCondition.ENROLLED)
        elif updated:
            self.SensorList[zone].pushChange(AlSensorCondition.STATE)
        else:
            self.SensorList[zone].pushChange(AlSensorCondition.RESET)
        
        # Has something changed?
        return enrolled_push_change or updated
        
    
    def _processKeypadsAndSirens(self, pmPanelTypeNr) -> str:
        sirenCnt = pmPanelConfig["CFG_SIRENS"][pmPanelTypeNr]
        keypad1wCnt = pmPanelConfig["CFG_1WKEYPADS"][pmPanelTypeNr]
        keypad2wCnt = pmPanelConfig["CFG_2WKEYPADS"][pmPanelTypeNr]

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process Devices (Sirens and Keypads)

        deviceStr = ""
        if self.isPowerMaster(): # PowerMaster models
            # Process keypad settings
            setting = self._lookupEprom(pmDecodePanelSettings["KeypadPMaster"])
            for i in range(0, min(len(setting), keypad2wCnt)):
                if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0 or setting[i][3] != 0 or setting[i][4] != 0:
                    log.debug(f"[_processKeypadsAndSirens] Found an enrolled PowerMaster keypad {i}")
                    deviceStr = f"{deviceStr},K2{i:0>2}"

            # Process siren settings
            setting = self._lookupEprom(pmDecodePanelSettings["SirensPMaster"])
            for i in range(0, min(len(setting), sirenCnt)):
                if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0 or setting[i][3] != 0 or setting[i][4] != 0:
                    log.debug(f"[_processKeypadsAndSirens] Found an enrolled PowerMaster siren {i}")
                    deviceStr = f"{deviceStr},S{i:0>2}"
        else:
            # Process keypad settings
            setting = self._lookupEprom(pmDecodePanelSettings["Keypad1PMax"])
            for i in range(0, min(len(setting), keypad1wCnt)):
                if setting[i][0] != 0 or setting[i][1] != 0:
                    log.debug(f"[_processKeypadsAndSirens] Found an enrolled PowerMax 1-way keypad {i}")
                    deviceStr = f"{deviceStr},K1{i:0>2}"

            setting = self._lookupEprom(pmDecodePanelSettings["Keypad2PMax"])
            for i in range(0, min(len(setting), keypad2wCnt)):
                if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0:
                    log.debug(f"[_processKeypadsAndSirens] Found an enrolled PowerMax 2-way keypad {i}")
                    deviceStr = f"{deviceStr},K2{i:0>2}"

            # Process siren settings
            setting = self._lookupEprom(pmDecodePanelSettings["SirensPMax"])
            for i in range(0, min(len(setting), sirenCnt)):
                if setting[i][0] != 0 or setting[i][1] != 0 or setting[i][2] != 0:
                    log.debug(f"[_processKeypadsAndSirens] Found a PowerMax siren {i}")
                    deviceStr = f"{deviceStr},S{i:0>2}"

        return deviceStr[1:]

    def processEEPROMData(self, addToLog):
        # If val.show is True but addToLog is False then:
        #      Add the "True" values to the self.Panelstatus
        # If val.show is True and addToLog is True then:
        #      Add all (either PowerMax / PowerMaster) values to the self.Panelstatus and the log file
        for key in pmDecodePanelSettings:
            val = pmDecodePanelSettings[key]
            if val.show:
                result = self._lookupEprom(val)
                if result is not None:
                    if type(val.name) is str and len(result) == 1:
                        if isinstance(result[0], (bytes, bytearray)):
                            tmpdata = toString(result[0])
                            if addToLog:
                                log.debug( f"[Process Settings]      {key:<18}  {val.name:<40}  {tmpdata}")
                            self.PanelStatus[val.name] = tmpdata
                        else:
                            if addToLog:
                                log.debug( f"[Process Settings]      {key:<18}  {val.name:<40}  {result[0]}")
                            self.PanelStatus[val.name] = result[0]
                    
                    elif type(val.name) is list and len(result) == len(val.name):
                        for i in range(0, len(result)):
                            if isinstance(result[0], (bytes, bytearray)):
                                tmpdata = toString(result[i])
                                if addToLog:
                                    log.debug( f"[Process Settings]      {key:<18}  {val.name[i]:<40}  {tmpdata}")
                                self.PanelStatus[val.name[i]] = tmpdata
                            else:
                                if addToLog:
                                    log.debug( f"[Process Settings]      {key:<18}  {val.name[i]:<40}  {result[i]}")
                                self.PanelStatus[val.name[i]] = result[i]
                    
                    elif len(result) > 1 and type(val.name) is str:
                        tmpdata = ""
                        for i in range(0, len(result)):
                            if isinstance(result[0], (bytes, bytearray)):
                                tmpdata = tmpdata + toString(result[i]) + ", "
                            else:
                                tmpdata = tmpdata + str(result[i]) + ", "
                        # there's at least 2 so this will not exception
                        tmpdata = tmpdata[:-2]
                        if addToLog:
                            log.debug( f"[Process Settings]      {key:<18}  {val.name:<40}  {tmpdata}")
                        self.PanelStatus[val.name] = tmpdata
                    
                    else:
                        log.debug( f"[Process Settings]   ************************** NOTHING DONE ************************     {key:<18}  {val.name}  {result}")

    def _setDataFromPanelType(self, p) -> bool:
        if p in pmPanelType:
            self.PanelType = p

            if self.DownloadCode == DEFAULT_DL_CODE:
                # If the panel still has its startup default Download Code, or if it hasn't been set by the user to something different
                self.DownloadCode = pmPanelConfig["CFG_DLCODE_1"][self.PanelType][:2] + " " + pmPanelConfig["CFG_DLCODE_1"][self.PanelType][2:]
                log.debug(f"[_setDataFromPanelType] Setting Download Code from the Default value {DEFAULT_DL_CODE} to the default Panel Value {self.DownloadCode}")
            else:
                log.debug("[_setDataFromPanelType] Using the user defined Download Code")
            
            if 0 <= self.PanelType <= len(pmPanelConfig["CFG_SUPPORTED"]) - 1:
                isSupported = pmPanelConfig["CFG_SUPPORTED"][self.PanelType]
                if isSupported:
                    self.PanelModel = pmPanelType[self.PanelType] if self.PanelType in pmPanelType else "UNKNOWN"   # INTERFACE : PanelType set to model
                    self.PowerMaster = pmPanelConfig["CFG_POWERMASTER"][self.PanelType]
                    self.AutoEnroll = pmPanelConfig["CFG_AUTO_ENROLL"][self.PanelType]
                    self.AutoSyncTime = pmPanelConfig["CFG_AUTO_SYNCTIME"][self.PanelType]
                    self.KeepAlivePeriod = pmPanelConfig["CFG_KEEPALIVE"][self.PanelType]
                    self.pmInitSupportedByPanel = pmPanelConfig["CFG_INIT_SUPPORT"][self.PanelType]
                    return True
                # Panel 0 i.e original PowerMax
                log.error(f"Lookup of Visonic Panel type reveals that this seems to be a PowerMax Panel and supports EEPROM Download only with no capability, this Panel cannot be used with this Integration")
                return False
        # Then it is an unknown panel type
        log.error(f"Lookup of Visonic Panel type {p} reveals that this is a new Panel Type that is unknown to this Software. Please contact the Author of this software")
        return False

    def checkPanelDataPresent(self) -> list:
        zoneCnt = pmPanelConfig["CFG_WIRELESS"][self.PanelType] + pmPanelConfig["CFG_WIRED"][self.PanelType]
        if self.isPowerMaster():
            need_these = {PanelSetting.UserCodes : pmPanelConfig["CFG_USERCODES"][self.PanelType],
                          PanelSetting.ZoneNames : zoneCnt,
                          PanelSetting.ZoneTypes : zoneCnt,
                          PanelSetting.DeviceTypes : zoneCnt,
                          PanelSetting.ZoneEnrolled : zoneCnt,
#                          PanelSetting.TestTest : zoneCnt,
                          PanelSetting.PartitionEnabled : 1,
                          PanelSetting.PanelBypass : 1, 
                          PanelSetting.ZoneChime : zoneCnt, 
                          PanelSetting.ZoneDelay : zoneCnt,
                          PanelSetting.PartitionData : pmPanelConfig["CFG_PARTITIONS"][self.PanelType] }
        else:
            need_these = {PanelSetting.UserCodes : pmPanelConfig["CFG_USERCODES"][self.PanelType],
                          PanelSetting.ZoneNames : zoneCnt,
                          PanelSetting.ZoneTypes : zoneCnt,
                          PanelSetting.DeviceTypes : zoneCnt,
                          PanelSetting.ZoneEnrolled : zoneCnt,
                          PanelSetting.PanelBypass : 1, 
                          PanelSetting.ZoneChime : zoneCnt }
        retval = []
        for s,v in need_these.items():
            if not (s in self.PanelSettings and len(self.PanelSettings[s]) >= v):
                retval.append(s)
        return retval


    # _processEPROMSettings
    #    Decode the EEPROM and the various settings to determine
    #       The general state of the panel
    #       The zones and the sensors
    #       The X10 devices
    #       The phone numbers
    #       The user pin codes

    def _processEPROMSettings(self) -> bool:
        """Process Settings from the downloaded EEPROM data from the panel"""
        log.debug("[Process Settings] Process Settings from EEPROM")

        if self.pmDownloadComplete:
            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Panel type and serial number
            #     This checks whether the EEPROM settings have been downloaded OK
            
            #pmDisplayName = self._lookupEpromSingle("displayName")    

            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Need the panel type to be valid so we can decode some of the remaining downloaded data correctly
            # when we get here then self.PanelType is set and it's a known panel type i.e. if self.PanelType is not None and self.PanelType in pmPanelType is TRUE
            # ------------------------------------------------------------------------------------------------------------------------------------------------

            # self._dumpEPROMSettings()

            #log.debug(f"[Process Settings] Panel Type Number {str(self.PanelType)}   serial string {toString(panelSerialType)}")
            zoneCnt = pmPanelConfig["CFG_WIRELESS"][self.PanelType] + pmPanelConfig["CFG_WIRED"][self.PanelType]

            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Process Panel Status to display in the user interface
            self.processEEPROMData(Dumpy)

            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Process Panel Settings to use as a common panel settings regardless of how they were obtained.  This way gets them from EPROM.
            if self.isPowerMaster(): # PowerMaster models
                for key in PanelSetting:
                    if key in pmPanelSettingCodes and pmPanelSettingCodes[key].PMasterEPROM is not None and len(pmPanelSettingCodes[key].PMasterEPROM) > 0:
                        if pmPanelSettingCodes[key].item is not None:
                            self.PanelSettings[key] = self._lookupEprom(pmDecodePanelSettings[pmPanelSettingCodes[key].PMasterEPROM])[pmPanelSettingCodes[key].item]
                        else:
                            self.PanelSettings[key] = self._lookupEprom(pmDecodePanelSettings[pmPanelSettingCodes[key].PMasterEPROM])
            else:
                for key in PanelSetting:
                    if key in pmPanelSettingCodes and pmPanelSettingCodes[key].PMaxEPROM is not None and len(pmPanelSettingCodes[key].PMaxEPROM) > 0:
                        if pmPanelSettingCodes[key].item is not None:
                            self.PanelSettings[key] = self._lookupEprom(pmDecodePanelSettings[pmPanelSettingCodes[key].PMaxEPROM])[pmPanelSettingCodes[key].item] # [pmPanelSettingCodes[key].item]
                        else:
                            self.PanelSettings[key] = self._lookupEprom(pmDecodePanelSettings[pmPanelSettingCodes[key].PMaxEPROM])
            
            log.debug(f"[Process Settings]    UpdatePanelSettings")

            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Process panel type and serial
            #pmPanelTypeCodeStr = self.PanelSettings[PanelSetting.PanelModel]      # self._lookupEpromSingle("panelModelCode")
            #idx = f"{hex(self.PanelType).upper()[2:]:0>2}{hex(int(pmPanelTypeCodeStr)).upper()[2:]:0>2}"
            #pmPanelName = pmPanelName_t[idx] if idx in pmPanelName_t else "Unknown_" + idx

            #log.debug(f"[Process Settings]   Processing settings - panel code index {idx}")

            #  INTERFACE : Add this param to the status panel first
            #self.PanelStatus["Panel Name"] = pmPanelName

            #log.debug(f"[Process Settings]    Installer Code {toString(self._lookupEpromSingle('installerCode'))}")
            #log.debug(f"[Process Settings]    Master DL Code {toString(self._lookupEpromSingle('masterDlCode'))}")
            #if self.isPowerMaster():
            #    log.debug(f"[Process Settings]    Master Code {toString(self._lookupEpromSingle('masterCode'))}")
            #    log.debug(f"[Process Settings]    Installer DL Code {toString(self._lookupEpromSingle('instalDlCode'))}")

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
            pmaster_zone_ext_data = self.PanelSettings[PanelSetting.ZoneExt] # self._lookupEprom(pmDecodePanelSettings["ZoneExtPMaster"])
            
            #for index , value in enumerate(pmaster_zone_ext_data):
            #    log.debug(f"[Process Settings]   Raw pmaster_zone_ext_data {index:<3} = {toString(value)}")

            log.debug(f"[Process Settings]   Zones Data Buffer  len settings {len(zone_data)}     len zoneNames {len(self.PanelSettings[PanelSetting.ZoneNames])}    zoneCnt {zoneCnt}")
            log.debug(f"[Process Settings]   Zones Names Buffer :  {toString(self.PanelSettings[PanelSetting.ZoneNames])}")
            #log.debug(f"[Process Settings]   Zones Data Buffer  :  {zone_data}")

            if len(zone_data) > 0:
                self.PanelSettings[PanelSetting.ZoneTypes] = bytearray(zoneCnt)
                self.PanelSettings[PanelSetting.DeviceTypes] = bytearray(zoneCnt)
                self.PanelSettings[PanelSetting.ZoneChime] = bytearray(zoneCnt)
                self.PanelSettings[PanelSetting.ZoneEnrolled] = bytearray(zoneCnt)
                motiondel = [0 for i in range(zoneCnt)]
                
                for i in range(0, zoneCnt):
                    self.PanelSettings[PanelSetting.ZoneTypes][i] = (int(zone_data[i]) if self.isPowerMaster() else int(zone_data[i][3])) & 0x0F
                    self.PanelSettings[PanelSetting.ZoneChime][i] = ((int(zone_data[i]) if self.isPowerMaster() else int(zone_data[i][3])) >> 4 ) &0x03
                    self.PanelSettings[PanelSetting.DeviceTypes][i] = int(pmaster_zone_ext_data[i][5]) if self.isPowerMaster() else int(zone_data[i][2])
                    motiondel[i] = self.PanelSettings[PanelSetting.ZoneDelay][i][0] + (256 * self.PanelSettings[PanelSetting.ZoneDelay][i][1]) if self.isPowerMaster() else 0
                    if self.isPowerMaster():  # PowerMaster models
                        self.PanelSettings[PanelSetting.ZoneEnrolled][i] = pmaster_zone_ext_data[i][4:9] != bytearray.fromhex("00 00 00 00 00") and pmaster_zone_ext_data[i][4:6] != bytearray.fromhex("FF FF")
                    else:
                        self.PanelSettings[PanelSetting.ZoneEnrolled][i] = zone_data[i][0:3] != bytearray.fromhex("00 00 00")
                self.PanelSettings[PanelSetting.ZoneDelay] = motiondel

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Store partition info & check if partitions are on
                self.partitionsEnabled = False
                partitionZoneOffset = 17  # Not sure what positions 1 to 16 are
                partitionCnt = pmPanelConfig["CFG_PARTITIONS"][self.PanelType]
                partition = self.PanelSettings[PanelSetting.PartitionData]
                if partitionCnt > 1:  # Could the panel have more than 1 partition?
                    # If that panel type can have more than 1 partition, then check to see if the panel has defined more than 1
                    if partition is not None and len(partition) >= partitionZoneOffset + zoneCnt and partition[0] != 255 and partition[0] != 0: # i think that partition[0] == 1 enables partitions
                        log.debug(f"[Process Settings] partitionCnt = {partitionCnt}    Partitions enabled, settings {toString(partition)}")
                        self.partitionsEnabled = True
                        self.PanelSettings[PanelSetting.PartitionData] = self.PanelSettings[PanelSetting.PartitionData][partitionZoneOffset:]
                    else:
                        partitionCnt = 1
                        log.debug(f"[Process Settings] partitionCnt = {partitionCnt}    Partitioning not set {toString(partition) if partition is not None else "and Invalid"}")
                else:
                    log.debug(f"[Process Settings] partitionCnt = {partitionCnt}    Panel {pmPanelType[self.PanelType]} settings define a single partition")
            return True
        else:
            log.warning("[Process Settings] WARNING: Cannot process panel EEPROM settings, download has not completed")
            return False

    def _updateAllSensors(self) -> bool:

        if self.ForceStandardMode or self.PanelType is None:
            return

        b = self.checkPanelDataPresent()
        log.debug(f"[Process Settings]   checkPanelDataPresent missing items {b}")
        
        retval = False
        if len(b) == 0:
            # Only when we have all EPROM or B0 Zone Data
            self.PartitionsInUse = set()

            # List of door/window sensors
            doorZoneStr = ""
            # List of motion sensors
            motionZoneStr = ""
            # List of smoke sensors
            smokeZoneStr = ""
            # List of other sensors
            otherZoneStr = ""

            log.debug("[Process Settings]   Processing Zone devices")
            
            zoneCnt = pmPanelConfig["CFG_WIRELESS"][self.PanelType] + pmPanelConfig["CFG_WIRED"][self.PanelType]
            for i in range(0, zoneCnt):
                
                tmp = self._updateSensor( zone = i )
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
            if len(self.PartitionsInUse) > 1:
                log.debug(f"[Process Settings]                I see that you have more than 1 partition")
            elif len(self.PartitionsInUse) == 0:
                log.debug(f"[Process Settings]                I see that you have no partitions")

            self.PanelStatus["Door Zones"] = doorZoneStr[1:]
            self.PanelStatus["Motion Zones"] = motionZoneStr[1:]
            self.PanelStatus["Smoke Zones"] = smokeZoneStr[1:]
            self.PanelStatus["Other Zones"] = otherZoneStr[1:]
        
        return retval # return True if any of the sensor data has been changed because of this function

    def _processX10Settings(self):
        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process PGM/X10 settings

        log.debug("[Process Settings] Processing X10 devices")

        s = []
        s.append(self._lookupEprom(pmDecodePanelSettings["x10ByArmAway"]))  # 0 = pgm, 1 = X01
        s.append(self._lookupEprom(pmDecodePanelSettings["x10ByArmHome"]))
        s.append(self._lookupEprom(pmDecodePanelSettings["x10ByDisarm"]))
        s.append(self._lookupEprom(pmDecodePanelSettings["x10ByDelay"]))
        s.append(self._lookupEprom(pmDecodePanelSettings["x10ByMemory"]))
        s.append(self._lookupEprom(pmDecodePanelSettings["x10ByKeyfob"]))
        s.append(self._lookupEprom(pmDecodePanelSettings["x10ActZoneA"]))
        s.append(self._lookupEprom(pmDecodePanelSettings["x10ActZoneB"]))
        s.append(self._lookupEprom(pmDecodePanelSettings["x10ActZoneC"]))

        x10Names = self._lookupEprom(pmDecodePanelSettings["x10ZoneNames"])  # 0 = X01
        log.debug(f"[Process Settings]            X10 device EPROM Name Data {toString(x10Names)}")

        for i in range(0, 16):
            x10Enabled = False
            for j in range(0, 9):
                x10Enabled = x10Enabled or s[j][i] != 'Disable'

            x10Name = (x10Names[i - 1] & 0x1F) if i > 0 else 0x1F     # PGM needs to be set by x10Enabled

            if x10Enabled or x10Name != 0x1F:
                x10Location = pmZoneName[x10Name] if i > 0 else "PGM"
                x10Type = "onoff" if i == 0 else "dimmer"       # Assume PGM is onoff switch, all other devices are dimmer Switches
                if i in self.SwitchList:
                    self.SwitchList[i].type = x10Type
                    self.SwitchList[i].location = x10Location
                    self.SwitchList[i].state = False
                else:
                    self.SwitchList[i] = X10Device(type=x10Type, location=x10Location, id=i, enabled=True)
                    self.SwitchList[i].onChange(self.mySwitchChangeHandler)
                    if self.onNewSwitchHandler is not None:
                        self.onNewSwitchHandler(self.SwitchList[i])                                    

        # INTERFACE : Create Partitions in the interface
        # for i in range(1, partitionCnt+1): # TODO

        log.debug("[Process Settings] Ready for use")



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
            self._updateSensor(zone = key)

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
        for i in range(0, total):
            status = x10status & (1 << i)
            if i in self.SwitchList:
                # INTERFACE : use this to set X10 status
                oldstate = self.SwitchList[i].state
                self.SwitchList[i].state = bool(status)
                # Check to see if the state has changed
                if (oldstate and not self.SwitchList[i].state) or (not oldstate and self.SwitchList[i].state):
                    log.debug(f"[ProcessX10StateUpdate]      X10 device {i} changed to {self.SwitchList[i].state} ({status})")
                    self.SwitchList[i].pushChange()


    def do_sensor_update(self, data : bytearray, func : str, msg : str, startzone : int = 0):
        val = self._makeInt(data)
        log.debug(f"{msg} : {val:032b}")
        for i in range(startzone, startzone+32):
            if i in self.SensorList:
                sf = getattr(self.SensorList[i], func)
                if sf is not None:
                    sf(bool(val & (1 << (i-startzone)) != 0))

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
            if self.lastPacket == packet and packet[1] == 0xA5:  # only consider A5 packets for consecutive error
                self.lastPacketCounter = self.lastPacketCounter + 1
            else:
                self.lastPacketCounter = 0
        self.lastPacket = packet

        if self.lastPacketCounter == SAME_PACKET_ERROR:
            log.debug("[_processReceivedPacket] Had the same packet for " + str(SAME_PACKET_ERROR) + " times in a row : %s", toString(packet))
            self._performDisconnect(AlTerminationType.SAME_PACKET_ERROR)
            return
        #else:
        #    log.debug("[_processReceivedPacket] Parsing complete valid packet: %s", toString(packet))

        # Record all main variables to see if the message content changes any
        oldState = statelist() # make it a function so if it's changed it remains consistent
        oldPowerMaster = self.PowerMaster
        pushchange = False
        
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
 
        #log.debug(f"[_processReceivedPacket] {processAB=} {processNormalData=}    {self.pmDownloadMode=}")
        
        # Leave this here as it needs to be created dynamically to create the condition and message columns
        DecodeMessage = collections.namedtuple('DecodeMessage', 'condition, func, pushchange, message')
        _decodeMessageFunction = {
            ACK_MESSAGE : DecodeMessage(                True , self.handle_msgtype02, False, None ),  # ACK
            0x06        : DecodeMessage(                True , self.handle_msgtype06, False, None ),  # Timeout
            0x07        : DecodeMessage(                True , self.handle_msgtype07, False, None ),  # No idea what this means
            0x08        : DecodeMessage(                True , self.handle_msgtype08, False, None ),  # Access Denied
            0x0B        : DecodeMessage(                True , self.handle_msgtype0B, False, None ),  # # LOOPBACK TEST, STOP (0x0B) IS THE FIRST COMMAND SENT TO THE PANEL WHEN THIS INTEGRATION STARTS
            0x0F        : DecodeMessage(                True , self.handle_msgtype0F, False, None ),  # Exit
            0x22        : DecodeMessage(               False , None                 , False, "WARNING: Message 0x22 is not decoded, are you using an old Powermax Panel as this is not supported?" ),
            0x25        : DecodeMessage(                True , self.handle_msgtype25, False, None ),  # Download retry
            0x33        : DecodeMessage( self.pmDownloadMode , self.handle_msgtype33, False, f"Received 33 Message, we are in {self.PanelMode.name} mode (so I'm ignoring the message), data: {toString(packet)}"),  # Settings send after a MSGV_START
            0x3C        : DecodeMessage(                True , self.handle_msgtype3C, False, None ),  # Message when start the download
            0x3F        : DecodeMessage( self.pmDownloadMode , self.handle_msgtype3F, False, f"Received 3F Message, we are in {self.PanelMode.name} mode (so I'm ignoring the message), data: {toString(packet)}"),  # Download information
            0xA0        : DecodeMessage(   processNormalData , self.handle_msgtypeA0, False, None ),  # Event log
            0xA3        : DecodeMessage(   processNormalData , self.handle_msgtypeA3,  True, None ),  # Zone Names
            0xA5        : DecodeMessage(   processNormalData , self.handle_msgtypeA5,  True, None ),  # Zone Information/Update
            0xA6        : DecodeMessage(   processNormalData , self.handle_msgtypeA6,  True, None ),  # Zone Types
            0xA7        : DecodeMessage(   processNormalData , self.handle_msgtypeA7,  True, None ),  # Panel Information/Update
            0xAB        : DecodeMessage(           processAB , self.handle_msgtypeAB,  True, f"Received AB Message, we are in {self.PanelMode.name} mode and Download is set to {self.pmDownloadMode} (so I'm ignoring the message), data: {toString(packet)}"),  # 
            0xAC        : DecodeMessage(   processNormalData , self.handle_msgtypeAC,  True, None ),  # X10 Names
            0xAD        : DecodeMessage(   processNormalData , self.handle_msgtypeAD,  True, None ),  # No idea what this means, it might ...  send it just before transferring F4 video data ?????
            0xB0        : DecodeMessage(           processB0 , self.handle_msgtypeB0,  True, None ),  # 
            0xF4        : DecodeMessage(   processNormalData , self.handle_msgtypeF4,  None, None ),  # F4 Message from a Powermaster, can't decode it yet but this will accept it and ignore it
            REDIRECT    : DecodeMessage(                True , self.handle_msgtypeC0, False, None ),
            VISPROX     : DecodeMessage(                True , self.handle_msgtypeE0, False, None )
        }

        if len(packet) < 4:  # there must at least be a header, command, checksum and footer
            log.warning("[_processReceivedPacket] Received invalid packet structure, not processing it " + toString(packet))
        elif packet[1] in _decodeMessageFunction:
            dm = _decodeMessageFunction[packet[1]]
            if dm.condition:
                pushchange = dm.func(packet[2:-2])    # Use the return value if the function returns
                if pushchange is None:
                    pushchange = dm.pushchange        # If the function does not return a value then use the dm value
            elif dm.message is not None:
                log.info(f"[_processReceivedPacket] {dm.message}")
            if self.sendPanelEventData(): # sent at least 1 event so no need to send PUSH_CHANGE
                pushchange = False
        elif processNormalData or processAB:
            log.debug("[_processReceivedPacket] Unknown/Unhandled packet type " + toString(packet))
        self.sendPanelEventData()
        if self.PostponeEventCounter == 0 and oldState != statelist():   # make statelist a function so if it's changed it remains consistent
            self.sendPanelUpdate(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
        elif oldPowerMaster != self.PowerMaster or pushchange:
            self.sendPanelUpdate(AlCondition.PUSH_CHANGE)


    def handle_msgtype02(self, data):  # ACK
        """ Handle Acknowledges from the panel """
        # Normal acknowledges have msgtype 0x02 but no data, when in powerlink the panel also sends data byte 0x43
        #    I have not found this on the internet, this is my hypothesis
        #log.debug(f"[handle_msgtype02] Ack Received  data = {toString(data)}")

        processAB = not self.pmDownloadMode and self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK]
        if processAB and len(data) > 0 and data[0] == 0x43:
            self.receivedPowerlinkAcknowledge = True
            if self.allowAckToTriggerRestore:
                log.debug("[handle_msgtype02]        Received a powerlink acknowledge, I am in STANDARD_PLUS mode and sending MSG_RESTORE")
                self._addMessageToSendList("MSG_RESTORE")
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
        self.AccessDeniedMessage = self._getLastSentMessage()

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

        # log.debug("[handle_msgtype33] Getting Data " + toString(data) + "   page " + hex(iPage) + "    index " + hex(iIndex))
        # Write to memory map structure, but remove the first 2 bytes from the data
        self._saveEPROMSettings(iPage, iIndex, data[2:])

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

        def findLength(page, index) -> int | None:
            for b in pmBlockDownload["PowerMax"]:
                if b[0] == index and b[1] == page:
                    return b[2]
            for b in pmBlockDownload["PowerMaster"]:
                if b[0] == index and b[1] == page:
                    return b[2]
            return None
 
        if self.PanelMode != AlPanelMode.DOWNLOAD:
            log.debug("[handle_msgtype3F] Received data but in Standard Mode so ignoring data")
            return

        # data format is normally: <index> <page> <length> <data ...>
        # If the <index> <page> = FF, then it is an additional PowerMaster MemoryMap
        iIndex = data[0]
        iPage = data[1]
        iLength = data[2]
        
        #pr = bytes((x for x in data[3:] if x >= 0x20 and x < 127))
        #log.debug("[handle_msgtype3F] actual data block length=" + str(len(data)-3) + "   data content length=" + str(iLength))

        # PowerMaster 10 (Model 7) and PowerMaster 33 (Model 10) has a very specific problem with downloading the Panel EEPROM and doesn't respond with the correct number of bytes
        #if self.PanelType is not None and self.ModelType is not None and ((self.PanelType == 7 and self.ModelType == 68) or (self.PanelType == 10 and self.ModelType == 71)):
        #    if iLength != len(data) - 3:
        #        log.debug(f"[handle_msgtype3F] Not checking data length as it could be incorrect.  We requested {iLength} and received {len(data) - 3}")
        #        log.debug(f"[handle_msgtype3F]                            {toString(data)}")
        #    # Write to memory map structure, but remove the first 3 bytes (index/page/length) from the data
        #    self._saveEPROMSettings(iPage, iIndex, data[3:])
           
        blocklen = findLength(iPage, iIndex)
        
        if iLength == len(data) - 3 and blocklen is not None and blocklen == iLength:
            # Write to memory map structure, but remove the first 3 bytes (index/page/length) from the data
            self._saveEPROMSettings(iPage, iIndex, data[3:])
            # Are we finished yet?
            if len(self.myDownloadList) > 0:
                self.pmDownloadInProgress = True
                self._addMessageToSendList("MSG_DL", options=[ [1, self.myDownloadList.pop(0)] ])  # Read the next block of EEPROM data
            else:
                self._populateEPROMDownload()
                if len(self.myDownloadList) == 0:
                    # This is the message to tell us that the panel has finished download mode, so we too should stop download mode
                    log.debug("[handle_msgtype3F] Download Complete")
                    self.pmDownloadInProgress = False
                    self.pmDownloadMode = False
                    self.pmDownloadComplete = True
                else:
                    log.debug("[handle_msgtype3F] Download seemed to be complete but not got all EPROM data yet")
                    self.pmDownloadInProgress = True
                    self._addMessageToSendList("MSG_DL", options=[ [1, self.myDownloadList.pop(0)] ])  # Read the next block of EEPROM data
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
                self._updateSensor(zone = offset+i)


    def handle_msgtypeA5(self, data):  # Status Message
        """ MsgType=A5 - Zone Data Update """

        # msgTot = data[0]
        eventType = data[1]

        #log.debug("[handle_msgtypeA5] Parsing A5 packet " + toString(data))

        match eventType:
            case 1 if len(self.SensorList) > 0:
                log.debug("[handle_msgtypeA5] Zone Alarm Status: Ztrip and ZTamper")
                self.do_sensor_update(data[2:6],  "do_ztrip",   "[handle_msgtypeA5]      Zone Trip Alarm 32-01")
                self.do_sensor_update(data[6:10], "do_ztamper", "[handle_msgtypeA5]      Zone Tamper Alarm 32-01")

            case 2 if len(self.SensorList) > 0:
                # if in standard mode then use this A5 status message to reset the watchdog timer
                if self.PanelMode != AlPanelMode.POWERLINK:
                    log.debug("[handle_msgtypeA5] Got A5 02 message, resetting watchdog")
                    self._reset_watchdog_timeout()

                log.debug("[handle_msgtypeA5] Zone Status: Status and Battery")
                self.do_sensor_update(data[2:6],  "do_status",  "[handle_msgtypeA5]      Open Door/Window Status Zones 32-01")
                self.do_sensor_update(data[6:10], "do_battery", "[handle_msgtypeA5]      Battery Low Zones 32-01")

            case 3 if len(self.SensorList) > 0:
                # This status is different from the status in the 0x02 part above i.e they are different values.
                #    This one is wrong (I had a door open and this status had 0, the one above had 1)
                #       According to domotica forum, this represents "active" but what does that actually mean?
                log.debug("[handle_msgtypeA5] Zone Status: Inactive and Tamper")
                val = self._makeInt(data[2:6])
                log.debug(f"[handle_msgtypeA5]      Trigger (Inactive) Status Zones 32-01: {val:032b} Not Used")
                self.do_sensor_update(data[6:10], "do_tamper", "[handle_msgtypeA5]      Tamper Zones 32-01")

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
                log.debug(f"[handle_msgtypeA5]      sysStatus={hexify(sysStatus)}    sysFlags={hexify(sysFlags)}    eventZone={hexify(eventZone)}    eventType={hexify(eventType)}    unknowns are {hexify(dummy1)} {hexify(dummy2)}")

                #last10seconds = sysFlags & 0x10

                if self.getPartitionsInUse() is None:   
                    # Process sysStatus and sysFlags only if there are no partitions
                    #     The panel sends A5 messages for all partitions but we don't know the partition number. So how do we know wha to decode?
                    oldPS = self.PartitionState[0].PanelState
                    s = self.PartitionState[0].ProcessPanelStateUpdate(sysStatus=sysStatus, sysFlags=sysFlags, PanelMode=self.PanelMode)   # does not set partition in return value
                    if s is not None:
                        #s.setPartition(1)
                        self.addPanelEventData(s)
                    newPS = self.PartitionState[0].PanelState
                    if newPS == AlPanelStatus.DISARMED and newPS != oldPS:
                        # Panel state is Disarmed and it has just changed
                        #if self.isPowerMaster():
                        #    # Could replace this with a Command B0 data to get Bypass info
                        #    self._addMessageToSendList("MSG_BYPASSTAT")
                        #else:
                        self._addMessageToSendList("MSG_BYPASSTAT")

                if sysFlags & 0x20 != 0:  # Zone Event
                    if eventType > 0 and eventZone != 0xff: # I think that 0xFF refers to the panel itself as a zone. Currently not processed
                        self.ProcessZoneEvent(eventZone=eventZone, eventType=eventType)

                x10stat1 = data[8]
                x10stat2 = data[9]
                self.ProcessX10StateUpdate(x10status=x10stat1 + (x10stat2 * 0x100))

    #        elif eventType == 0x05:  # 
    #            # 0d a5 10 05 00 00 00 00 00 00 12 34 43 bc 0a
    #            #     Might be a coincidence but the "1st Account No" is set to 001234
    #            pass

            case 6:
                log.debug("[handle_msgtypeA5] Zone Status: Enrolled and Bypass")
                val = self._makeInt(data[2:6])
                if val != self.enrolled_old:
                    log.debug(f"[handle_msgtypeA5]      Enrolled Zones 32-01: {val:032b}")
                    send_zone_type_request = False
                    self.enrolled_old = val

                    # Build a B0 chunk out of the data to process it in a common way
                    ch = chunky(datasize = BITS, length = 4, data = data[2:6])
                    self.updatePanelSetting(key = PanelSetting.ZoneEnrolled, ch = ch, display = True, msg = f"A5 Zone Enrolled Data")
                    self._updateAllSensors()

                self.do_sensor_update(data[6:10], "do_bypass", "[handle_msgtypeA5]      Bypassed Zones 32-01")

            case _:
                # easiest way to check if its full of zeros
                vala = self._makeInt(data[2:6])
                valb = self._makeInt(data[6:10])
                if vala != 0 or valb != 0:
                    log.debug("[handle_msgtypeA5]      Unknown A5 Message: " + toString(data))
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
            log.debug(f"                        Zone type for sensor {offset+i+1} is {(int(data[2+i])) - 0x1E} : {pmZoneTypeKey[self.PanelSettings[PanelSetting.ZoneTypes][offset+i]]}")
            if self.PanelMode != AlPanelMode.POWERLINK and self.PanelMode != AlPanelMode.POWERLINK_BRIDGED and (offset+i) in self.SensorList:
                self._updateSensor(zone = offset+i)

    def handle_msgtypeA7(self, data):
        """ MsgType=A7 - Panel Status Change """
        #log.debug("[handle_msgtypeA7] Panel Status Change " + toString(data))
        # 01 00 27 51 02 ff 00 02 00 00
        # ff 5d 00 2d 00 00 11 0c 00 00

        msgCnt = int(data[0])

        # If message count is FF then it looks like the first message is valid so decode it (this is experimental)
        #if msgCnt == 0xFF:
        #    msgCnt = 1

        if msgCnt == 255 and self.getPartitionsInUse() is not None:
            # Looks like it's parsed differently, and this seems to be only sent when the panel is set to partitions enabled
            dummy1 = int(data[1])     # some kind of sequence counter maybe
            dummy2 = int(data[2])     # 61h or 0
            dummy3 = int(data[3])     # Looks like an eventType but the timing of when it arrives is all wrong
            dummy4 = int(data[4])     # Looks like partition number but timing of arrival is all wrong
            dummy5 = int(data[5])     # FF or 0 or 1.   0 and 1 alternated for a while.
            dummy6 = int(data[6])     # 0 or 40h or 41h
            dummy7 = int(data[7])     # 6 or Ch
            dummy8 = int(data[8])     # All 0
            dummy9 = int(data[9])     # All 0
            # 0d a7 ff 6c 00 60 00 ff 00 0c 00 00 43 3d 0a
            if dummy2 == 0 and dummy3 == EVENT_TYPE_SYSTEM_RESET:  # panel reset
                log.info("[handle_msgtypeA7]          Panel has been reset.    msgCnt is 255")
                self.PanelResetEvent = True
            else:
                log.debug(f"[handle_msgtypeA7]      A7 FF message contains : data={toString(data)}")
                
        elif msgCnt > 4:
            log.warning(f"[handle_msgtypeA7]      A7 message contains too many messages to process : {msgCnt}   data={toString(data)}")

        elif self.getPartitionsInUse() is None:   # message count 0 to 4 and we have no partitions so process message data

            # don't know what this is (It is 0x00 in test messages so could be the higher 8 bits for msgCnt)
            dummy = int(data[1])
            log.debug(f"[handle_msgtypeA7]      A7 message contains {msgCnt} messages,   unknown byte is {hex(dummy)}")

            #zoneCnt = 0  # this means it wont work in the case we're in standard mode and the panel type is not set
            #if self.PanelType is not None:
            #    zoneCnt = pmPanelConfig["CFG_WIRELESS"][self.PanelType] + pmPanelConfig["CFG_WIRED"][self.PanelType]
            # 03 00 01 03 08 0e 01 13
            # 03 00 2f 55 2f 1b 00 1c
            for i in range(0, msgCnt):
                eventZone = int(data[2 + (2 * i)])
                eventType = int(data[3 + (2 * i)])
                #partition = int(data[4 + (4 * i)]) if doingPartitions else 1
                #thingy    = int(data[5 + (4 * i)])
                
                if eventType == EVENT_TYPE_SYSTEM_RESET: # system restart
                    log.info("[handle_msgtypeA7]          Panel has been reset.")
                    self.PanelResetEvent = True
                else:
                    self.addPanelEventData(AlPanelEventData(name = eventZone, action = eventType)) # assume partition -11 means a panel event not tied to a partition

                    log.debug(f"[handle_msgtypeA7] {hexify(eventType)=}      {hexify(eventZone)=}")
                    
                    s = self.SensorList[eventZone-1] if eventZone-1 in self.SensorList else None  # only used if it decides that siren is sounding, then that is the trigger sensor
                    self.PartitionState[0].UpdatePanelState(eventType, s)                         # Assume all panel state goes through partition 1

                    if eventType == EVENT_TYPE_FORCE_ARM or (self.pmForceArmSetInPanel and eventType == EVENT_TYPE_DISARM): # Force Arm OR (ForceArm has been set and Disarm)
                        self.pmForceArmSetInPanel = eventType == EVENT_TYPE_FORCE_ARM                                 # When the panel uses ForceArm then sensors may be automatically armed and bypassed by the panel
                        log.debug("[handle_msgtypeA7]              Panel has been Armed using Force Arm, sensors may have been bypassed by the panel, asking panel for an update on bypassed sensors")
                        if self.isPowerMaster():
                            self.B0_Message_Wanted.add("ZONE_BYPASS")
                        else:
                            self._addMessageToSendList("MSG_BYPASSTAT")

    def handle_msgtypeAB(self, data) -> bool:  # PowerLink Message
        """ MsgType=AB - Panel Powerlink Messages """
        log.debug(f"[handle_msgtypeAB]  data {toString(data)}")

        # Restart the timer
        self._reset_watchdog_timeout()

        subType = data[0]
        if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.STANDARD_PLUS] and subType == 1:
            # Panel Time
            log.debug("[handle_msgtypeAB] ***************************** Got Panel Time ****************************")

            pt = datetime(2000 + data[7], data[6], data[5], data[4], data[3], data[2]).astimezone()            
            log.debug(f"[handle_msgtypeAB]    Panel time is {pt}")
            self.setTimeInPanel(pt)

        elif subType == 3 and self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.STANDARD_PLUS]:  # keepalive message
            # Example 0D AB 03 00 1E 00 31 2E 31 35 00 00 43 2A 0A
            #               03 00 1e 00 33 33 31 34 00 00 43        From a Powermax+     PanelType=1, Model=33
            log.debug("[handle_msgtypeAB] ***************************** Got PowerLink Keep-Alive ****************************")
            # It is possible to receive this between enrolling (when the panel accepts the enroll successfully) and the EEPROM download
            #     I suggest we simply ignore it

            self._reset_powerlink_counter() # reset when received keep-alive from the panel

            if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.STANDARD_PLUS]:
                self._addMessageToSendList("MSG_ALIVE")       # The Powerlink module sends this when it gets an i'm alive from the panel.

            if self.PanelMode == AlPanelMode.STANDARD_PLUS:
                log.debug("[handle_msgtypeAB]         Got alive message while Powerlink mode pending, going to full powerlink and calling Restore")
                self.PanelMode = AlPanelMode.POWERLINK  # it is truly in powerlink now we are receiving powerlink alive messages from the panel
                self._triggerRestoreStatus()
                #self._dumpSensorsToLogFile()

        elif subType == 3:  # keepalive message
            log.debug("[handle_msgtypeAB] ***************************** Got PowerLink Keep-Alive ****************************")
            log.debug("[handle_msgtypeAB] ********************* Panel Mode not Powerlink / Standard Plus **********************")
            self.PanelKeepAlive = True    

        elif self.PanelMode == AlPanelMode.POWERLINK and subType == 5:  # -- phone message
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

        elif self.PanelMode == AlPanelMode.POWERLINK and subType == 10 and data[2] == 0:
            log.debug(f"[handle_msgtypeAB] PowerLink telling us what the code {data[3]} {data[4]} is for downloads, currently commented out as I'm not certain of this")

        elif subType == 10 and data[2] == 1:
            if self.PanelMode == AlPanelMode.POWERLINK:
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enroll but not acted on (already in powerlink) **************************")
            elif not self.ForceStandardMode:
                self.PanelWantsToEnrol = True
                log.debug("[handle_msgtypeAB] ************************** PowerLink, Panel wants to auto-enroll **************************")

        return True

    # X10 Names (0xAC) I think
    def handle_msgtypeAC(self, data):  # PowerLink Message
        """ MsgType=AC - ??? """
        log.debug(f"[handle_msgtypeAC]  data {toString(data)}")

    def handle_msgtypeAD(self, data):  # PowerLink Message
        """ MsgType=AD - Panel Powerlink Messages """
        log.debug(f"[handle_msgtypeAD]  data {toString(data)}")
        if data[2] == 0x00: # the request was accepted by the panel
            if self.PanelMode == AlPanelMode.POWERLINK:
                self._addMessageToSendList("MSG_NO_IDEA")
        
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

    
    def _decode_24(self, data, partitions):
        iSec = data[8]
        iMin = data[9]
        iHour = data[10]
        iDay = data[11]
        iMonth = data[12]
        iYear = data[13]
        
        unknown1 = data[14]
        unknown2 = data[15]
        
        partitionCount = data[16] if partitions else 1

        # Attempt to check and correct time
        pt = datetime(2000 + data[13], data[12], data[11], data[10], data[9], data[8]).astimezone()
        self.setTimeInPanel(pt)
        messagedate = f"{iDay:0>2}/{iMonth:0>2}/{iYear}   {iHour:0>2}:{iMin:0>2}:{iSec:0>2}"
        log.debug(f"[_decode_24]    Panel time is {pt}  date={messagedate}    data (hex) 14={hex(unknown1)}  15={hex(unknown2)}  PartitionCount={partitionCount}")

        for i in range(0, partitionCount):
            offset = i * 4
            # Repeat 4 bytes (17 to 20) for more than 1 partition
            sysFlags = data[offset + 18]    # Bit 7 is a validity flag
            if sysFlags & 0x80 != 0:  # This seems to be a "partition enabled" indication
                sysStatus = data[offset + 17]
                sysStatus2 = data[offset + 19]  # Bit 0 represents the "last 10 seconds" bit, not sure about the rest.
                unknown4 = data[offset + 20]
     
                log.debug(f"[_decode_24]        Partition={i+1} with data (hex) Status={hex(sysStatus)}  System={hex(sysFlags)}  X={hex(sysStatus2)}  Y={hex(unknown4)}")
                # I believe that bit 0 of sysStatus2 represents the "Instant" indication for armed home and armed away (and maybe disarm etc) i.e. all the PanelState values above 0x0F
                sysStatus = (sysStatus & 0xF) | (( sysStatus2 << 4 ) & 0x10 )
     
                oldPS = self.PartitionState[i].PanelState
                # Mask off the top bit as seems to be used to indicate overall validity
                s = self.PartitionState[i].ProcessPanelStateUpdate(sysStatus=sysStatus, sysFlags=sysFlags & 0x7F, PanelMode=self.PanelMode)  # does not set partition in return value
                if s is not None:
                    if self.getPartitionsInUse() is not None:   # we have partitions so add it in as an attribute
                        s.setPartition(i+1)
                    self.addPanelEventData(s)

                newPS = self.PartitionState[i].PanelState
                if newPS == AlPanelStatus.DISARMED and newPS != oldPS:
                    # Panel state is Disarmed and it has just changed
                    self.B0_Message_Wanted.add("ZONE_BYPASS")
                    #self._addMessageToSendList("MSG_BYPASSTAT")

                if sysFlags & 0x20 != 0:  # Zone Event
                    log.debug(f"[_decode_24]                 It also claims to have a zone event with data (hex) {hex(sysStatus2)} possibly with this data {hex(unknown4)}")
                    #self.ProcessZoneEvent(eventZone=eventZone, eventType=eventType)
            else:
                log.debug(f"[_decode_24]        Partition={i+1}  Not Enabled")
    

    def settings_data_type_formatter( self, data_type: int, data: bytes, string_size: int = 16, byte_size: int = 1 ) -> int | str | bytearray:
        """Format data for 35 and 42 data."""
        if data_type == DataType.ZERO_PADDED_STRING:  # \x00 padded string
            return data.decode("ascii", errors="ignore").rstrip("\x00")
        if data_type == DataType.DIRECT_MAP_STRING:  # Direct map to string
            return data.hex()
        if data_type == DataType.FF_PADDED_STRING:
            return data.hex().replace("ff", "")
        if data_type == DataType.DOUBLE_LE_INT:  # 2 byte int
            return (
                [b2i(data[i : i + 2], False) for i in range(0, len(data), 2)]
                if len(data) > 2
                else b2i(data[0:2], False)
            )
        if data_type == DataType.INTEGER:  # 1 byte int?
            if len(data) == byte_size:
                return b2i(data)
            # Assume 1 byte int list
            return [
                b2i(data[i : i + byte_size]) for i in range(0, len(data), byte_size)
            ]
        if data_type == DataType.STRING:
            return data.decode("ascii", errors="ignore")
        if data_type == DataType.SPACE_PADDED_STRING:  # Space padded string
            return data.decode("ascii", errors="ignore").rstrip(" ")
        if (
            data_type == DataType.SPACE_PADDED_STRING_LIST
        ):  # Space paddeded string list - seems all 16 chars
            # Cmd 35 0d 00 can include a \x00 instead of \x20 (space)
            # Remove any \x00 also when decoding.
            names = wrap(data.decode("ascii", errors="ignore"), string_size)
            if names and len(names) == 1:
                return names[0].replace("\x00", "").rstrip(" ")
            return [
                name.replace("\x00", "").rstrip(" ") for name in names if name != ""
            ]
        return data.hex(" ")

    
    def _extract_35_data(self, ch):
        #03 35 0b ff 08 ff 06 00 00 01 00 00 00 02 43
        dataContentA = ch.data[0]
        dataContentB = ch.data[1]
        datatype = ch.data[2]        # 6 is a String
        dataContent = (dataContentB << 8) | dataContentA
        datalen = ch.length - 3
        data = ch.data[3:]
        log.debug("[_extract_35_data]     ***************************** Panel Settings ********************************")
        data_setting = self.settings_data_type_formatter(datatype, data)
        if not OBFUS:
            log.debug(f"[_extract_35_data]           data_setting = {data_setting}   type is {type(data_setting)}")
            log.debug(f"[_extract_35_data]               dataContent={hex(dataContent)} panel setting   {datatype=}  {datalen=}    data={toString(data)}")
        if dataContent in pmPanelSettingsB0:
            d = pmPanelSettingsB0[dataContent]
            if (d.length == 0 or ch.length == d.length) and datatype == d.datatype:
                # Check the PanelSettings to see if there's one that refers to this dataContent
                for key in PanelSetting:
                    if key in pmPanelSettingCodes and pmPanelSettingCodes[key].PMasterB0Panel == dataContent:
                        ch.data = data
                        self.updatePanelSetting(key, ch, d.display, d.msg)
                        break
            else:
                log.debug(f"[_extract_35_data]               {d.msg} data lengths differ: {ch.length=} {d.length=}   type: {datatype=} {d.datatype=}")
        else:
            log.debug(f"[_extract_35_data]               dataContent={hex(dataContent)} panel setting unknown      {datatype=}  {datalen=}    data={toString(ch.data[3:])}")
        
        if dataContent == 0x000F and datalen == 2 and isinstance(data_setting,str):
            if not OBFUS:
                log.debug(f"[_extract_35_data]               Download code : {data_setting}")
            self.DownloadCode = data_setting[:2] + " " + data_setting[2:]

        elif dataContent == 0x003C and datalen == 15 and isinstance(data_setting,str): # 8 is a string
            if not self.pmGotPanelDetails:
                name = data_setting.replace("-"," ")
                log.debug(f"[_extract_35_data] Panel Name {name}.  Not got panel details so trying to reconcile:")
                for p,v in pmPanelType.items():
                    log.debug(f"[_extract_35_data]     Checking: {p} {v}")
                    if name == v:
                        log.debug(f"[_extract_35_data] Fount it: {v}")
                        self.ModelType = 0xDA7E  # No idea what model type it is so just set it to a valid number, DAVE
                        if not self._setDataFromPanelType(p):
                            log.debug(f"[_extract_35_data] Panel Type {data[5]} Unknown")                            
                        else:
                            log.debug(f"[_extract_35_data] PanelType={self.PanelType} : {self.PanelModel} , Model={self.ModelType}   Powermaster {self.PowerMaster}")
                            self.pmGotPanelDetails = True
                        break
            else:
                log.debug("[_extract_35_data] Not Processed as already got Panel Details")
            
        elif dataContent == 0x0030 and datalen == 1 and isinstance(data_setting,int): #
            if data_setting == 0:
                log.debug(f"[_extract_35_data] Seems to indicate that partitions are disabled in the panel")
            else:
                log.debug(f"[_extract_35_data] Seems to indicate that partitions are enabled in the panel {data_setting}")
            self.partitionsEnabled = data_setting != 0
            
        log.debug("[_extract_35_data]     ***************************** Panel Settings Exit ***************************")

    def updatePanelSetting(self, key, ch, display : bool = False, msg : str = ""):
        
        s = pmPanelSettingCodes[key].tostring(self.PanelSettings[key])

        if pmPanelSettingCodes[key].item is not None:
            if len(ch.data) > pmPanelSettingCodes[key].item:
                self.PanelSettings[key] = ch.data[pmPanelSettingCodes[key].item]
        elif ch.datasize == BITS:
            if len(self.PanelSettings[key]) < ch.length * 8 :
                # replace as current length less than the new data
                #log.debug(f"[updatePanelSetting]              {key=}  replace")
                self.PanelSettings[key] = []
                for i in range(0, ch.length):
                    for j in range(0,8):  # 8 bits in a byte
                        self.PanelSettings[key].append((ch.data[i] & (1 << j)) != 0)
            else:
                # overwrite as current length is same as or more than new data
                #log.debug(f"[updatePanelSetting]              {key=}  overwrite")
                for i in range(0, ch.length):
                    for j in range(0,8):  # 8 bits in a byte
                        self.PanelSettings[key][(i*8)+j] = (ch.data[i] & (1 << j)) != 0
        else:
            self.PanelSettings[key] = ch.data

        if display:
            v = pmPanelSettingCodes[key].tostring(self.PanelSettings[key])
            if len(s) > 100 or len(v) > 100:
                log.debug(f"[updatePanelSetting]              {key=}   ({msg})")
                log.debug(f"[updatePanelSetting]                        replacing {s}")
                log.debug(f"[updatePanelSetting]                        with      {v}")
            else:
                log.debug(f"[updatePanelSetting]              {key=}   ({msg})    replacing {s}  with {v}")
        else:
            log.debug(f"[updatePanelSetting]              {key=}   ({msg})")


    def _extract_42_data(self, data: bytes) -> tuple[int, str | int | list[str | int]]:
        """Format a command 42 message.
        
        This has many parameter options to retrieve EPROM settings.
        bytes 0 & 1 are the parameter
        bytes 2 & 3 is the max number of data items
        bytes 4 & 5 is the size of each data item
        bytes 6 & 7 - don't know
        bytes 8 & 9 is the data type
        bytes 10 & 11 is the start index of data item
        bytes 12 & 13 is the number of data items
        bytes 14 to end is data
        """

        def chunk_bytearray(data: bytearray, size: int) -> list[bytes]:
            """Split bytearray into sized chunks."""
            if data:
                return [data[i : i + size] for i in range(0, len(data), size)]
        
        def settings_42_data_type_formatter(
            data_type: int, data: bytes, byte_size: int = 1
        ) -> int | str | bytearray:
            """Preformatter for settings 42."""
            if data_type == DataType.SPACE_PADDED_STRING_LIST:
                # Remove any \x00 also when decoding.
                # On 42 46 00 strings are \n terminated
                name = data.decode("ascii", errors="ignore")
                return name.replace("\x00", "").rstrip("\n").rstrip(" ")
            return self.settings_data_type_formatter(data_type, data, byte_size=byte_size)

        setting = b2i(data[0:2], big_endian=False)
        # max_data_items = b2i(data[2:4], big_endian=False)
        data_item_size = max(1, int(b2i(data[4:6], big_endian=False) / 8))
        data_type = data[8]  # This is actually 2 bytes, what is second byte??
        byte_size = 2 if data[9] == 0 else 1
        # start_entry = b2i(data[10:12], big_endian=False)
        # entries = b2i(data[12:14], big_endian=False)

        # Split into entries
        data_items = chunk_bytearray(data[14:], data_item_size)
        decoded_format = []
        log.debug(f"[_extract_42_data]        42 {setting=}   {data_item_size=}   {data_type=}   {byte_size=}")
        if data_items:
            for data_item in data_items:
                #log.debug(f"[_extract_42_data]             42 data_item = {toString(data_item)}")
                # Convert to correct data type
                data_setting = settings_42_data_type_formatter(data_type, data_item, byte_size=byte_size)
                decoded_format.append(data_setting)
#                if not OBFUS:
                log.debug(f"[_extract_42_data]                formatted = {data_setting}")

        if len(decoded_format) == 1:
            return setting, decoded_format[0]
        return setting, decoded_format


    def processChunk(self, ch : chunky):
        # Whether to process the experimental code (and associated B0 message data) or not
        experimental = True
        beezerodebug = False
        beezerodebug4 = True
        beezerodebug7 = True
        
        st = pmSendMsgB0_reverseLookup[ch.subtype].data if ch.subtype in pmSendMsgB0_reverseLookup else ""
        
        #log.debug(f"[handle_msgtypeB0]     st = {st}      chunky = {ch}      self.beezero_024B_sensorcount = {self.beezero_024B_sensorcount}") # [processChunk]                 chunky = sequence 255  datasize 40  index 3   length 140

        if self.beezero_024B_sensorcount is not None and st != "ZONE_LAST_EVENT":
            self.beezero_024B_sensorcount = None   # If theres a next time so they are coordinated
            log.debug(f"[handle_msgtypeB0]        Resetting beezero_024B_sensorcount st=<{st}>")
        
        match (st, ch.datasize, IndexName(ch.index), ch.length):
            
            case ("PANEL_STATE",    BYTES, IndexName.MIXED,  21 | 29):
                # Panel state change
                self._decode_24(data = ch.data, partitions = ch.length == 29)
                self.B0_LastPanelStateTime = self._getUTCTimeFunction()
    
            case ("SYSTEM_CAPABILITIES", WORDS, IndexName.MIXED, _ ):
                # System capabilities
                ds = 2 # 16 // 8
                b = ch.length // ds
                for i in range(0, b):
                    d = ch.data[(i*ds)+1] * 256 + ch.data[i*ds]
                    t = pmDataIndexes[i] if i in pmDataIndexes else f'Type {i}'
                    log.debug(f"[handle_msgtypeB0]          Got {st:<20}   {t:<10}   {toString(ch.data[i*ds:(i+1)*ds])}    decimal {d:>4}")
    
            case ("ZONE_OPENCLOSE", BITS,  IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                zoneLen = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]       Received message, open/close information, zone length = {zoneLen}")
                self.do_sensor_update(ch.data[0:4], "do_status", "[handle_msgtypeB0]          Zone Status 32-01")
                if zoneLen >= 32:
                    self.do_sensor_update(ch.data[4:8], "do_status", "[handle_msgtypeB0]          Zone Status 64-33", 32)

            case ("ZONE_BYPASS",    BITS,  IndexName.ZONES,  _ ):
                # I'm 50% sure this is correct
                # 0d b0 03 19 0d ff 01 03 08 01 00 00 00 00 00 00 00 6f 43 66 0a        Z01 (sensor 0) has been bypassed
                zoneLen = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]       Received message, bypass information, zone length = {zoneLen}")
                self.do_sensor_update(ch.data[0:4], "do_bypass", "[handle_msgtypeB0]          Zone Bypass 32-01")
                if zoneLen >= 32:
                    self.do_sensor_update(ch.data[4:8], "do_bypass", "[handle_msgtypeB0]          Zone Bypass 64-33", 32)
            
            case ("TAMPER_ALERT",   BITS,  IndexName.ZONES,  _ ):
                # I'm 50% sure this is correct
                zoneLen = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]       Received message, tamper alert, zone length = {zoneLen}")
                self.do_sensor_update(ch.data[0:4], "do_tamper", "[handle_msgtypeB0]          Zone Tamper 32-01")
                if zoneLen >= 32:
                    self.do_sensor_update(ch.data[4:8], "do_tamper", "[handle_msgtypeB0]          Zone Tamper 64-33", 32)
            
            case ("TAMPER_ACTIVITY",   BITS,  IndexName.ZONES,  _ ):
                # I'm 50% sure this is correct
                zoneLen = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]       Received message, tamper activity, zone length = {zoneLen}")
                self.do_sensor_update(ch.data[0:4], "do_tamper", "[handle_msgtypeB0]          Zone Tamper 32-01")
                if zoneLen >= 32:
                    self.do_sensor_update(ch.data[4:8], "do_tamper", "[handle_msgtypeB0]          Zone Tamper 64-33", 32)
            
            case ("SENSOR_ENROL",   BITS,  IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                self._updateAllSensors()
        
            case ("DEVICE_TYPES",   BYTES, _    ,  _ ):
                if ch.index in pmDataIndexes:
                    log.debug(f"[handle_msgtypeB0]          Got Device Type, {pmDataIndexes[ch.index]:<10} {ch}")
                else:
                    log.debug(f"[handle_msgtypeB0]          Got Device Type {ch}")

                if ch.index == 3: # Sensors
                    # I'm 100% sure this is correct
                    self._updateAllSensors()
        
            case ("ASSIGNED_PARTITION", BYTES, _    ,  _ ):   # paged
                if ch.index in pmDataIndexes:
                    log.debug(f"[handle_msgtypeB0]          Got Assigned Partition, {pmDataIndexes[ch.index]:<10} {ch}")
                else:
                    log.debug(f"[handle_msgtypeB0]          Got Assigned Partition {ch}")
        
            case ("ZONE_NAMES",     BYTES, IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                self._updateAllSensors()
        
            case ("ZONE_TYPES",     BYTES, IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                self._updateAllSensors()
        
            case ("ZONE_TEMPS",     BYTES, IndexName.ZONES,  _ ):
                log.debug(f"[handle_msgtypeB0]          Got Zone Temperatures Chunk {ch}")
                zoneCnt = pmPanelConfig["CFG_WIRELESS"][self.PanelType] + pmPanelConfig["CFG_WIRED"][self.PanelType]
                if ch.length == zoneCnt:
                    for i in range(0, zoneCnt):
                        if i in self.SensorList and ch.data[i] != 255:
                            temp = -40.5 + (ch.data[i] / 2)
                            log.debug(f"[handle_msgtypeB0]            Zone {i+1} has temperature raw value {ch.data[i]}     temp={temp}")
                            self.SensorList[i].updateTemperature(temp)
                            
            case ("ZONE_LUX"  ,     BYTES, IndexName.ZONES,  _ ):
                log.debug(f"[handle_msgtypeB0]          Got Zone Luminance Chunk {ch}")
                zoneCnt = pmPanelConfig["CFG_WIRELESS"][self.PanelType] + pmPanelConfig["CFG_WIRED"][self.PanelType]
                if ch.length == zoneCnt:
                    for i in range(0, zoneCnt):
                        if i in self.SensorList and ch.data[i] != 255:
                            log.debug(f"[handle_msgtypeB0]               Zone {i+1} has luminance value {ch.data[i]} --> not sure what the value means")
                            self.SensorList[i].updateLux(ch.data[i])

            case ("PANEL_SETTINGS_35", _    , _    ,  _ ):
                # I'm 100% sure this is correct
                self._extract_35_data(ch)
                self._updateAllSensors()
       
            case ("PANEL_SETTINGS_42", _    , _    ,  _ ):
                if experimental:    
                    setting, decoded_format = self._extract_42_data(ch.data)
                    self._updateAllSensors()
        
            case ("ASK_ME_1",       BYTES, IndexName.MIXED,  _ ):
                log.debug(f"[handle_msgtypeB0]          Received ASK_ME_1 pop message   {ch}")
                if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD_PLUS, AlPanelMode.STANDARD]:
                    if ch.length > 0:
                        s = self._create_B0_Data_Request(taglist = ch.data)
                        self._addMessageToSendList(s, priority = MessagePriority.URGENT)
        
            case ("ASK_ME_2",       BYTES, IndexName.MIXED,  _ ):
                log.debug(f"[handle_msgtypeB0]          Received ASK_ME_2 pop message   {ch}")
                if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD_PLUS, AlPanelMode.STANDARD]:
                    if ch.length > 0:
                        s = self._create_B0_Data_Request(taglist = ch.data)
                        self._addMessageToSendList(s, priority = MessagePriority.URGENT)
            case ("ZONE_LAST_EVENT",   40, IndexName.ZONES,  _ ):  # Each entry is ch.datasize=40 bits (or 5 bytes)
                if ch.type == SUB:    
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
                elif ch.type == MAIN:    
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
                            # Assume that when the PowerMaster panel has less than 32 sensors then it just sends this and not msgType == 0x02, subType == pmSendMsgB0["ZONE_LAST_EVENT"]
                            sensorcount = int(ch.length / 5)
                            for i in range(0, sensorcount):
                                o = i * 5
                                self._decode_4B(i, ch.data[o:o+5])
                    self.beezero_024B_sensorcount = None   # If theres a next time so they are coordinated

            case ("LEGACY_EVENT_LOG",  80, IndexName.MIXED,  _ ):
                log.debug(f"[handle_msgtypeB0]       Got Legacy Event Log Chunk {ch}")
                self.processB0LogEntry(1, 1, ch.data)

            case ("EVENT_LOG",         80, IndexName.MIXED,  _ ):
                if ch.type == SUB:    
                    log.debug(f"[handle_msgtypeB0]          Got Sub Event Log Chunk {ch}")
                    eventTotal = pmPanelConfig["CFG_EVENTS"][self.PanelType]
                    # Got Event Log Chunk sequence 6  datasize 80  index 255   length 170    data 92 73 00 67 0c 00 00 1c 00 63 92 73 00 67 06 00 00 1b 01 62 92 73 00 67 06 00 00 55 01 61 83 73 00 67 03 00 00 01 01 6a 7c 73 00 67 06 00 00 52 01 60 64 72 00 67 0c 00 00 1c 00 5f 64 72 00 67 06 00 00 1b 01 5e 56 72 00 67 0c 00 00 20 00 5d 2d 70 00 67 0c 00 00 1c 00 5c 26 70 00 67 0c 00 00 23 00 5b 0f 6e 00 67 0c 00 00 1c 00 5a 0f 6e 00 67 06 00 00 1b 01 59 fd 6d 00 67 0c 00 00 0c 00 58 a6 69 00 67 0c 00 00 1c 00 57 a6 69 00 67 06 00 00 1b 01 56 8e 69 00 67 0c 00 00 0c 00 55 24 69 00 67 0c 00 00 1c 00 54
                    datalength = 10 # We know this as we check datasize to be 80 above       ch.datasize // 8 # 8 bits in a byte
                    entries = ch.length // datalength
                    offset = (ch.sequence-1) * entries       # This assumes that all previous messages in the sequence had the same number of entries
                    if ch.length % datalength == 0:  # is the length divisible by datalength exactly
                        for i in range(0, ch.length, datalength):
                            logentry = offset + (i // datalength)
                            self.B0_logCounter = max(self.B0_logCounter, logentry)
                            log.debug(f"[handle_msgtypeB0]            Processing log entry {logentry}     data = {toString(ch.data[i:i+datalength])}")
                            self.processB0LogEntry(eventTotal, logentry + 1, ch.data[i:i+datalength])
                elif ch.type == MAIN:
                    log.debug(f"[handle_msgtypeB0]          Got Main Event Log Chunk {ch}")
                    eventTotal = pmPanelConfig["CFG_EVENTS"][self.PanelType]
                    datalength = 10 # We know this as we check datasize to be 80 above       ch.datasize // 8 # 8 bits in a byte
                    offset = self.B0_logCounter + 1  # self.B0_logCounter is the maximum value from the 0x02 sequence so start from here + 1
                    if ch.length % datalength == 0:  # is the length divisible by datalength exactly
                        for i in range(0, ch.length, datalength):
                            logentry = offset + (i // datalength)
                            log.debug(f"[handle_msgtypeB0]               Processing log entry {logentry}     data = {toString(ch.data[i:i+datalength])}")
                            self.processB0LogEntry(eventTotal, logentry + 1, ch.data[i:i+datalength])

            case ("ZONE_STAT04",    BYTES, IndexName.ZONES,  _ ):
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
                            log.debug(f"                            Zone {z}  State(hex) {hex(s)}")

            case ("ZONE_STAT07",    BYTES, IndexName.ZONES,  _ ):
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
                            log.debug(f"                            Zone {z}  State {s}")

            case _:
                if beezerodebug:
                    #log.debug(f"@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@")
                    #log.debug(f"[handle_msgtypeB0]        Received message chunk for  {st}, dont know what this is, chunk = {str(ch)}")

                    if ch.index in pmDataIndexes:
                        log.debug(f"[handle_msgtypeB0]                     Got Unknown {st:<20} {pmDataIndexes[ch.index]:<10} {toString(ch.data)}")
                    elif ch.index == 255: # Some kind of panel settings
                        b = -1
                        if ch.datasize > 8 and ch.datasize % 8 == 0:  # if it's exactly divisible by 8 then
                            ds = ch.datasize // 8
                            if ch.length % ds == 0:  # If it's exactly divisible
                                b = ch.length // ds
                                for i in range(0, b):
                                    log.debug(f"[handle_msgtypeB0]                     Got Unknown {st:<20}  Block {i:<3}   {toString(ch.data[i*ds:(i+1)*ds])}")
                        if b < 0:      
                            log.debug(f"[handle_msgtypeB0]                     Got Unknown {st:<20} {toString(ch.data)}")
                    else:
                        log.debug(f"[handle_msgtypeB0]                     Got Unknown {st:<20} {toString(ch.data)}")
                    #log.debug(f"@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@")


    # Only Powermasters send this message
    def handle_msgtypeB0(self, data):  # PowerMaster Message
        """ MsgType=B0 - Panel PowerMaster Message """
        # Only Powermasters send this message
        # Format: <Type> <SubType> <Length of Data and Counter> <Data> <Counter> <0x43>

        def chunkme(t, s, data) -> list:
            message_type = data[0]
            if data[3] == 0xFF or (data[3] != 0xFF and message_type == 2):               # Check validity of data chunk (it could be valid and have no chunks)
                overall_length = data[2]
                retval = []
                current = 3
                while current < len(data) and (data[current] == 0xFF or (data[current] != 0xFF and current == 3 and message_type == 2)):
                    d = data[current + 4 : current + data[current+3] + 4]
                    c = chunky(type = t, subtype = s, sequence = data[current], datasize = data[current+1], index = data[current+2], length = data[current+3], data = d)
                    current = current + c.length + 4
                    retval.append(c)
                if current-2 == overall_length:
                    return retval
            #else:
            #    log.debug(f"[handle_msgtypeB0] ******************************************************** Message not chunky for {message_type}  data is {toString(data)} ********************************************************")
            return []

        # A powermaster mainly interacts with B0 messages so reset watchdog on receipt
        self._reset_watchdog_timeout()

        msgType = data[0]
        subType = data[1]
        msgLen  = data[2]
        
        # The data <Length> value is 4 bytes less then the length of the data block (as the <MessageCounter> is part of the data count)
        if len(data) != msgLen + 4:
            log.debug("[handle_msgtypeB0]              Invalid Length, not processing")
            # Do not process this B0 message as it seems to be incorrect
            return
        
        if subType in self.B0_Message_Waiting:
            self.B0_Message_Waiting.remove(subType)
        
        msgInfo = pmSendMsgB0_reverseLookup[subType] if subType in pmSendMsgB0_reverseLookup else None
        
        if not OBFUS:
            log.debug(f"[handle_msgtypeB0] Received {self.PanelModel or "UNKNOWN_PANEL_MODEL"} message {hexify(msgType):>02}/{hexify(subType):>02} (len = {msgLen})    data = {toString(data)}")
        else:
            log.debug(f"[handle_msgtypeB0] Received {self.PanelModel or "UNKNOWN_PANEL_MODEL"} message {hexify(msgType):>02}/{hexify(subType):>02} (len = {msgLen})    data = <OBFUSCATED>")

        log.debug(f"[handle_msgtypeB0]    msgInfo = {"unknown" if msgInfo is None else msgInfo}")

        if msgInfo is None:
            # Message unknown
            log.debug(f"[handle_msgtypeB0]             Message {msgType=} {subType=} not known about, lets see if its chunky")
            chunks = chunkme(msgType, subType, data[:-2]) # exclude b0 counter and 0x43 at the end
            if len(chunks) == 0:
                log.debug(f"[handle_msgtypeB0]                        ++++++++++++++++++++++++++++++++ Message not chunky and not processed further +++++++++++++++++++++++++++++++++++++++++++++++++")
            else:
                for chunk in chunks:
                    log.debug(f"[handle_msgtypeB0]                    Decoded Chunk {chunk}")
                    
        elif msgInfo.chunky:
            # Process the messages that we believe are chunked
            chunks = chunkme(msgType, subType, data[:-2]) # exclude b0 counter and 0x43 at the end
            if len(chunks) == 0:
                log.debug(f"[handle_msgtypeB0] ******************************************************** Message not chunky (we thought it was) and not processed further *************************************************")
            else:
                for chunk in chunks:
                    log.debug(f"[handle_msgtypeB0]               {toString(data[:2])}     Decode Chunk         {chunk}")
                    # Check the PanelSettings to see if there's one that refers to this message chunk
                    for key in PanelSetting:
                        if key in pmPanelSettingCodes:
                            k = pmPanelSettingCodes[key].PMasterB0Mess
                            if k is not None and k in pmSendMsgB0 and pmSendMsgB0[k].data == subType and pmPanelSettingCodes[key].PMasterB0Index == chunk.index:
                                #log.debug(f"              {subType=}  {key=}   replacing {self.PanelSettings[key]}  with {toString(chunk.data)}")
                                self.updatePanelSetting(key, chunk, True, f"{subType=}")
                                break
                    self.processChunk(chunk)
        elif msgInfo.data == "INVALID_COMMAND":  # 
            log.debug(f"[handle_msgtypeB0]             INVALID_COMMAND B0 sent to the panel:")
            if msgLen % 2 == 0: # msgLen is an even number
                for i in range(0, msgLen, 2):
                    log.debug(f"[handle_msgtypeB0]                     The Invalid Command is {hexify(data[3+i]):0>2} {hexify(data[4+i]):0>2}")
        else:
            # Process the messages that we know about and are not chunked
            log.debug(f"[handle_msgtypeB0]             Message {msgInfo.data} known about but not chunky and not currently processed")
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
            log.debug(f'[handle_msgtypeE0]  Visonic Proxy Not Being Used as Currently in Standard Mode')
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
                    
                    self._addMessageToSendList(convertByteArray('0d ab 0e 00 17 1e 00 00 03 01 05 00 43 c5 0a'))

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
                    
                        #self._addMessageToSendList("MSG_NO_IDEA")

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
            bpin = self.getUserCode() # PanelSettings[PanelSetting.UserCodes][0]    # defaults to 0000
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

    def getEventData(self, partition : int | None):
        if partition is not None and 1 <= partition <= 3:
            return self.PartitionState[partition-1].getEventData()
        return self.PartitionState[0].getEventData()

    # A dictionary that is used to add to the attribute list of the Alarm Control Panel
    #     If this is overridden then please include the items in the dictionary defined here by using super()
    def getPanelStatusDict(self, partition : int | None = None, include_extended_status : bool = None) -> dict:
        """ Get a dictionary representing the panel status. """
        a = self.getEventData(partition)
        
        #log.debug(f"[getPanelStatusDict]  getPanelStatusDict a = {a}")
        
        if partition is None or partition == 1:
            b = { "Protocol Version" : PLUGIN_VERSION,
                  "mode" : self.PanelMode.name.lower() }
            self.merge(a,b)

            f = self.getPanelFixedDict()
            self.merge(a,f)

            if self.ForceStandardMode:
                c = {
                    "Watchdog Timeout (Total)": self.WatchdogTimeoutCounter,
                    "Watchdog Timeout (Past 24 Hours)": self.WatchdogTimeoutPastDay,
                    "Panel Problem Count": self.PanelProblemCount,
                    "Last Panel Problem Time": self.LastPanelProblemTime if self.LastPanelProblemTime else ""
                }
            else:
                c = {
                    "Watchdog Timeout (Total)": self.WatchdogTimeoutCounter,
                    "Watchdog Timeout (Past 24 Hours)": self.WatchdogTimeoutPastDay,
                    "Download Timeout": self.DownloadCounter - 1 if self.DownloadCounter > 0 else 0,            # This is the number of download attempts and it would normally be 1 so subtract 1 off => the number of retries
                    "Download Message Retries": self.pmDownloadRetryCount,                                              # This is for individual 3F download failures
                    "Panel Problem Count": self.PanelProblemCount,
                    "Last Panel Problem Time": self.LastPanelProblemTime if self.LastPanelProblemTime else ""
                }
            self.merge(a,c)
            if include_extended_status and len(self.PanelStatus) > 0:
                # r = {**d, **self.PanelStatus}
                self.merge(a, self.PanelStatus)
            if partition == 1:
                self.merge(a, { PE_PARTITION : partition } )
                
        elif self.getPartitionsInUse() is not None and len(self.getPartitionsInUse()) > 1:
            self.merge(a, { PE_PARTITION : partition } )
            
        return a

    # requestPanelCommand
    #       state is PanelCommand
    #       optional pin, if not provided then try to use the EEPROM downloaded pin if in powerlink
    def requestPanelCommand(self, state : AlPanelCommand, code : str = "", partitions : set = {1,2,3} ) -> AlCommandStatus:
        """ Send a request to the panel to Arm/Disarm """
        
        def getPanelStatus():
            if self.isPowerMaster():
                s = self._create_B0_Data_Request(taglist = [ pmSendMsgB0["PANEL_STATE"].data ])
                self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)
            else:
                self._addMessageToSendList("MSG_STATUS_SEN", priority = MessagePriority.IMMEDIATE)
        
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
                    self._addMessageToSendList("MSG_ARM", priority = MessagePriority.IMMEDIATE, options=[ [3, armCode], [4, bpin], [6, partition] ])  #
                    getPanelStatus()
                    return AlCommandStatus.SUCCESS

                elif self.isPowerMaster():
                        
                    if state == AlPanelCommand.MUTE:
                        self._addMessageToSendList("MSG_MUTE_SIREN", priority = MessagePriority.IMMEDIATE, options=[ [4, bpin] ])  #
                        getPanelStatus()
                        return AlCommandStatus.SUCCESS

                    elif state == AlPanelCommand.TRIGGER:
                        self._addMessageToSendList("MSG_PM_SIREN", priority = MessagePriority.IMMEDIATE, options=[ [4, bpin] ])  #
                        getPanelStatus()
                        return AlCommandStatus.SUCCESS

                    elif state in pmSirenMode:
                        sirenCode = bytearray()
                        # Retrieve the code to send to the panel
                        sirenCode.append(pmSirenMode[state])
                        self._addMessageToSendList("MSG_PM_SIREN_MODE", priority = MessagePriority.IMMEDIATE, options=[ [4, bpin], [11, sirenCode] ])  #
                        getPanelStatus()
                        return AlCommandStatus.SUCCESS

            return AlCommandStatus.FAIL_INVALID_STATE
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    def setX10(self, device : int, state : AlX10Command) -> AlCommandStatus:
        # This is untested
        # "MSG_X10PGM"      : VisonicCommand(convertByteArray('A4 00 00 00 00 00 99 99 99 00 00 43'), None  , False, "X10 Data" ),
        #log.debug(f"[SendX10Command] Processing {device} {type(device)}")
        if not self.pmDownloadMode:
            if self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
                if device >= 0 and device <= 15:
                    log.debug("[SendX10Command]  Send X10 Command : id = " + str(device) + "   state = " + str(state))
                    calc = 1 << device
                    byteA = calc & 0xFF
                    byteB = (calc >> 8) & 0xFF
                    if state in pmX10State:
                        what = pmX10State[state]
                        self._addMessageToSendList("MSG_X10PGM", priority = MessagePriority.IMMEDIATE, options=[ [6, what], [7, byteA], [8, byteB] ])
                        self._addMessageToSendList("MSG_STATUS_SEN", priority = MessagePriority.IMMEDIATE)
                        if self.isPowerMaster():
                            self.B0_Message_Wanted.add("PANEL_STATE")        # 24
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
                            self._addMessageToSendList("MSG_GET_IMAGE", options=[ [1, count], [2, device] ])  #  
                            return AlCommandStatus.SUCCESS
                    return AlCommandStatus.FAIL_INVALID_STATE
                return AlCommandStatus.FAIL_ENTITY_INCORRECT
            return AlCommandStatus.FAIL_INVALID_STATE
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS


    def _createBypassB0Message(self, bypass : bool, zone_data : bytearray, pin : bytearray) -> bytearray:
    
        PM_Request_Data = convertByteArray("00 ff 01 03 08") + zone_data
        ll = len(PM_Request_Data) + 2

        PM_Request_Start = convertByteArray(f'b0 {"00" if bypass else "04"} 19') + bytearray([ll]) + pin
        PM_Request_End   = convertByteArray('43')

        PM_Data = PM_Request_Start + PM_Request_Data + PM_Request_End

        CS = self._calculateCRC(PM_Data)   # returns a bytearray with a single byte
        To_Send = bytearray([0x0d]) + PM_Data + CS + bytearray([0x0a])

        log.debug(f"[_createBypassB0Message] Returning {toString(To_Send)}")
        return To_Send


    # Individually or as a set, arm/disarm the sensors
    #   This sets/clears the bypass for each sensor
    #       sensor is the zone number 1 to 31 or 1 to 64
    #       bypassValue is a boolean ( True then Bypass, False then Arm )
    #       optional pin, if not provided then try to use the EEPROM downloaded pin if in powerlink  (only used for PowerMax)
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
                            #log.debug("[SensorArmState]  setSensorBypassState " + hex(bypassint))
                            y1, y2, y3, y4, y5, y6, y7, y8 = (bypassint & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")
                            bypass_data = bytearray([y1, y2, y3, y4, y5, y6, y7, y8])
                            log.debug("[SensorArmState]  setSensorBypassState data = " + toString(bypass_data))

                            if len(bypass_data) == 8:
                                s = self._createBypassB0Message(bypassValue, bypass_data, convertByteArray(self.DownloadCode))
                                self._addMessageToSendList(s, priority = MessagePriority.IMMEDIATE)
                                self.B0_Message_Wanted.add("ZONE_BYPASS")
                                return AlCommandStatus.SUCCESS
                            
                        else:
                            # PowerMax
                            # The MSG_BYPASSEN and MSG_BYPASSDI commands are the same i.e. command is A1
                            #      byte 0 is the command A1
                            #      bytes 1 and 2 are the pin
                            #      bytes 3 to 6 are the Enable bits for the 32 zones
                            #      bytes 7 to 10 are the Disable bits for the 32 zones
                            #      byte 11 is 0x43
                            bpin = self._createPin(pin)
                            #log.debug("[SensorArmState]  setSensorBypassState " + hex(bypassint))
                            y1, y2, y3, y4 = (bypassint & 0xFFFFFFFF).to_bytes(4, "little")
                            bypass_data = bytearray([y1, y2, y3, y4])
                            log.debug("[SensorArmState]  setSensorBypassState data = " + toString(bypass_data))
                            
                            if len(bypass_data) == 4:
                                if bypassValue:
                                    self._addMessageToSendList("MSG_BYPASSEN", priority = MessagePriority.IMMEDIATE, options=[ [1, bpin], [3, bypass_data] ])
                                else:
                                    self._addMessageToSendList("MSG_BYPASSDI", priority = MessagePriority.IMMEDIATE, options=[ [1, bpin], [7, bypass_data] ])
                                # request status to check success and update sensor variable
                                self._addMessageToSendList("MSG_BYPASSTAT", priority = MessagePriority.URGENT)
                                return AlCommandStatus.SUCCESS
                        return AlCommandStatus.FAIL_INVALID_STATE

                    return AlCommandStatus.FAIL_ENTITY_INCORRECT

                return AlCommandStatus.FAIL_INVALID_STATE
            return AlCommandStatus.FAIL_PANEL_CONFIG_PREVENTED
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    # Get the Event Log
    #       optional pin, if not provided then try to use the EEPROM downloaded pin if in powerlink
    def getEventLog(self, pin : str = "") -> AlCommandStatus:
        """ Get Panel Event Log """
        if not self.pmDownloadMode:
            if self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
                log.debug("getEventLog")
                self.eventCount = 0
                #if self.isPowerMaster():
                #    self.B0_Message_Wanted.add("EVENT_LOG")
                #else:
                bpin = self._createPin(pin)
                self._addMessageToSendList("MSG_EVENTLOG", priority = MessagePriority.URGENT, options=[ [4, bpin] ])
                return AlCommandStatus.SUCCESS
            return AlCommandStatus.FAIL_INVALID_STATE
        return AlCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

# Turn on auto code formatting when using black
# fmt: on
