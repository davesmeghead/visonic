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
#    PanelType=4 : PowerMax Pro Part , Model=71   Powermaster False
#    PanelType=4 : PowerMax Pro Part , Model=86   Powermaster False
#    PanelType=5 : PowerMax Complete Part , Model=18   Powermaster False
#    PanelType=5 : PowerMax Complete Part , Model=79   Powermaster False
#    PanelType=7 : PowerMaster10 , Model=32   Powermaster True
#    PanelType=7 : PowerMaster10 , Model=68   Powermaster True   #  Under investigation. Problem with 0x3F Message data (EPROM) being less than requested
#    PanelType=7 : PowerMaster10 , Model=153   Powermaster True
#    PanelType=8 : PowerMaster30 , Model=6   Powermaster True
#    PanelType=8 : PowerMaster30 , Model=53   Powermaster True
#    PanelType=10: PowerMaster33 , Model=71   Powermaster True   #  Under investigation. Problem with 0x3F Message data (EPROM) being less than requested
#    PanelType=15: PowerMaster33 , Model=146   Powermaster True  #  Under investigation.
#################################################################

import asyncio
import logging
import sys
import collections
import time
import copy
import math
import socket

from abc import ABC, abstractmethod
from enum import Enum
from collections import defaultdict
from datetime import datetime, timedelta
from time import sleep

from functools import partial
from typing import Callable, List
from collections import namedtuple

try:
    from .pconst import PyConfiguration, PyPanelMode, PyPanelCommand, PyPanelStatus, PyCommandStatus, PyX10Command, PyCondition, PyPanelInterface, PySensorDevice, PyLogPanelEvent, PySensorType, PySwitchDevice
except:
    from pconst import PyConfiguration, PyPanelMode, PyPanelCommand, PyPanelStatus, PyCommandStatus, PyX10Command, PyCondition, PyPanelInterface, PySensorDevice, PyLogPanelEvent, PySensorType, PySwitchDevice

PLUGIN_VERSION = "1.0.13.1"

# Some constants to help readability of the code
ACK_MESSAGE = 0x02

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

RESPONSE_TIMEOUT = 10

# If a message has not been sent to the panel in this time (seconds) then send an I'm alive message
KEEP_ALIVE_PERIOD = 25  # Seconds

# When we send a download command wait for DownloadMode to become false.
#   If this timesout then I'm not sure what to do, maybe we really need to just start again
#   In Vera, if we timeout we just assume we're in Standard mode by default
DOWNLOAD_TIMEOUT = 180

# Number of seconds delay between trying to achieve EPROM download
DOWNLOAD_RETRY_DELAY = 90

# Number of times to retry the retrieval of a block to download, this is a total across all blocks to download and not each block
DOWNLOAD_RETRY_COUNT = 30

# Number of seconds delay between trying to achieve powerlink (must have achieved download first)
POWERLINK_RETRY_DELAY = 180

# Number of seconds between trying to achieve powerlink (must have achieved download first) and giving up. Better to be half way between retry delays
POWERLINK_TIMEOUT = 4.5 * POWERLINK_RETRY_DELAY

# The number of seconds that if we have not received any data packets from the panel at all then suspend this plugin and report to HA
NO_RECEIVE_DATA_TIMEOUT = 30

# The number of seconds between receiving data from the panel and then no communication (the panel has stopped sending data for this period of time) then suspend this plugin and report to HA
LAST_RECEIVE_DATA_TIMEOUT = 600  # 10 minutes

# Messages left to work out
#      Panel sent 0d 22 fd 0a 01 16 15 00 0d 00 00 00 9c 0a    No idea what this means
#                Powermax+ panel 0d f1 07 43 00 00 8b 56 0a    checksum calcs 0X38

# A gregorian year, on average, contains 365.2425 days
# Thus, expressed as seconds per average year, we get 365.2425 × 24 × 60 × 60 = 31,556,952 seconds/year

# use a named tuple for data and acknowledge
#    replytype   is a message type from the Panel that we should get in response
#    waitforack, if True means that we should wait for the acknowledge from the Panel before progressing
#    debugprint  If False then do not log the full raw data as it may contain the user code
#    waittime    a number of seconds after sending the command to wait before sending the next command
# fmt: off
VisonicCommand = collections.namedtuple('VisonicCommand', 'data replytype waitforack download debugprint waittime msg')
pmSendMsg = {
   "MSG_EVENTLOG"    : VisonicCommand(bytearray.fromhex('A0 00 00 00 99 99 00 00 00 00 00 43'), [0xA0]                  , False, False, False, 0.0, "Retrieving Event Log" ),
   "MSG_ARM"         : VisonicCommand(bytearray.fromhex('A1 00 00 00 99 99 00 00 00 00 00 43'), None                    ,  True, False, False, 0.0, "(Dis)Arming System" ),
   "MSG_STATUS"      : VisonicCommand(bytearray.fromhex('A2 00 00 00 00 00 00 00 00 00 00 43'), [0xA5]                  ,  True, False,  True, 0.0, "Getting Status" ),
   "MSG_BYPASSTAT"   : VisonicCommand(bytearray.fromhex('A2 00 00 20 00 00 00 00 00 00 00 43'), [0xA5]                  , False, False,  True, 0.0, "Get Bypass Status" ),
   "MSG_ZONENAME"    : VisonicCommand(bytearray.fromhex('A3 00 00 00 00 00 00 00 00 00 00 43'), [0xA3, 0xA3, 0xA3, 0xA3],  True, False,  True, 0.0, "Requesting Zone Names" ),
   "MSG_X10PGM"      : VisonicCommand(bytearray.fromhex('A4 00 00 00 00 00 99 99 99 00 00 43'), None                    , False, False,  True, 0.0, "X10 Data" ),
   "MSG_ZONETYPE"    : VisonicCommand(bytearray.fromhex('A6 00 00 00 00 00 00 00 00 00 00 43'), [0xA6, 0xA6, 0xA6, 0xA6],  True, False,  True, 0.0, "Requesting Zone Types" ),
   "MSG_BYPASSEN"    : VisonicCommand(bytearray.fromhex('AA 99 99 12 34 56 78 00 00 00 00 43'), None                    , False, False, False, 0.0, "BYPASS Enable" ),
   "MSG_BYPASSDI"    : VisonicCommand(bytearray.fromhex('AA 99 99 00 00 00 00 12 34 56 78 43'), None                    , False, False, False, 0.0, "BYPASS Disable" ),
   "MSG_GETTIME"     : VisonicCommand(bytearray.fromhex('AB 01 00 00 00 00 00 00 00 00 00 43'), [0xAB]                  ,  True, False,  True, 0.0, "Get Panel Time" ),   # Returns with an AB 01 message back
   "MSG_ALIVE"       : VisonicCommand(bytearray.fromhex('AB 03 00 00 00 00 00 00 00 00 00 43'), None                    ,  True, False,  True, 0.0, "I'm Alive Message To Panel" ),
   "MSG_RESTORE"     : VisonicCommand(bytearray.fromhex('AB 06 00 00 00 00 00 00 00 00 00 43'), [0xA5]                  ,  True, False,  True, 0.0, "Restore PowerMax/Master Connection" ),  # It can take multiple of these to put the panel back in to powerlink
   "MSG_ENROLL"      : VisonicCommand(bytearray.fromhex('AB 0A 00 00 99 99 00 00 00 00 00 43'), None                    ,  True, False,  True, 0.0, "Auto-Enroll of the PowerMax/Master" ),  # should get a reply of [0xAB] but its not guaranteed
   "MSG_INIT"        : VisonicCommand(bytearray.fromhex('AB 0A 00 01 00 00 00 00 00 00 00 43'), None                    ,  True, False,  True, 8.0, "Initializing PowerMax/Master PowerLink Connection" ),
   "MSG_X10NAMES"    : VisonicCommand(bytearray.fromhex('AC 00 00 00 00 00 00 00 00 00 00 43'), [0xAC]                  , False, False,  True, 0.0, "Requesting X10 Names" ),
   # Command codes (powerlink) do not have the 0x43 on the end and are only 11 values                                            
   "MSG_DOWNLOAD"    : VisonicCommand(bytearray.fromhex('24 00 00 99 99 00 00 00 00 00 00')   , [0x3C]                  , False,  True, False, 0.0, "Start Download Mode" ),  # This gets either an acknowledge OR an Access Denied response
   "MSG_WRITE"       : VisonicCommand(bytearray.fromhex('3D 00 00 00 00 00 00 00 00 00 00')   , None                    , False, False, False, 0.0, "Write Data Set" ),
   "MSG_DL"          : VisonicCommand(bytearray.fromhex('3E 00 00 00 00 B0 00 00 00 00 00')   , [0x3F]                  ,  True, False,  True, 0.0, "Download Data Set" ),
   "MSG_SETTIME"     : VisonicCommand(bytearray.fromhex('46 F8 00 01 02 03 04 05 06 FF FF')   , None                    , False, False,  True, 0.0, "Setting Time" ),   # may not need an ack
   "MSG_SER_TYPE"    : VisonicCommand(bytearray.fromhex('5A 30 04 01 00 00 00 00 00 00 00')   , [0x33]                  , False, False,  True, 0.0, "Get Serial Type" ),
   # quick command codes to start and stop download/powerlink are a single value                                                 
   "MSG_START"       : VisonicCommand(bytearray.fromhex('0A')                                 , [0x0B]                  , False, False,  True, 0.0, "Start" ),    # waiting for STOP from panel for download complete
   "MSG_STOP"        : VisonicCommand(bytearray.fromhex('0B')                                 , None                    , False, False,  True, 1.5, "Stop" ),     #
   "MSG_EXIT"        : VisonicCommand(bytearray.fromhex('0F')                                 , None                    , False, False,  True, 1.5, "Exit" ),
   # Acknowledges                                                                                                                
   "MSG_ACK"         : VisonicCommand(bytearray.fromhex('02')                                 , None                    , False, False,  True, 0.0, "Ack" ),
   "MSG_ACKLONG"     : VisonicCommand(bytearray.fromhex('02 43')                              , None                    , False, False,  True, 0.0, "Ack Long" ),
   # PowerMaster specific                                                                                                        
   "MSG_POWERMASTER" : VisonicCommand(bytearray.fromhex('B0 01 00 00 00 00 00 00 00 00 43')   , [0xB0]                  , False, False,  True, 0.0, "Powermaster Command" )
}

pmSendMsgB0_t = {
   "ZONE_STAT1" : bytearray.fromhex('04 06 02 FF 08 03 00 00'),
   "ZONE_STAT2" : bytearray.fromhex('07 06 02 FF 08 03 00 00'),
   "ZONE_STAT3" : bytearray.fromhex('18 06 02 FF 08 03 00 00')
   #"ZONE_NAME"  : bytearray.fromhex('21 02 05 00'),   # not used in Vera Lua Script
   #"ZONE_TYPE"  : bytearray.fromhex('2D 02 05 00')    # not used in Vera Lua Script
}

# To use the following, use  "MSG_DL" above and replace bytes 1 to 4 with the following
#    index page lenlow lenhigh
pmDownloadItem_t = {
   "MSG_DL_TIME"         : bytearray.fromhex('F8 00 06 00'),   # used
   "MSG_DL_COMMDEF"      : bytearray.fromhex('01 01 1E 00'),
   "MSG_DL_PHONENRS"     : bytearray.fromhex('36 01 20 00'),
   "MSG_DL_PINCODES"     : bytearray.fromhex('FA 01 10 00'),   # used
   "MSG_DL_INSTPIN"      : bytearray.fromhex('0C 02 02 00'),
   "MSG_DL_DOWNLOADPIN"  : bytearray.fromhex('0E 02 02 00'),
   "MSG_DL_PGMX10"       : bytearray.fromhex('14 02 D5 00'),   # used
   "MSG_DL_PARTITIONS"   : bytearray.fromhex('00 03 F0 00'),   # used
   "MSG_DL_PANELFW"      : bytearray.fromhex('00 04 20 00'),   # used
   "MSG_DL_SERIAL"       : bytearray.fromhex('30 04 08 00'),   # used
   "MSG_DL_EVENTLOG"     : bytearray.fromhex('DF 04 28 03'),
   "MSG_DL_ZONES"        : bytearray.fromhex('00 09 78 00'),   # used
   "MSG_DL_KEYFOBS"      : bytearray.fromhex('78 09 40 00'),
   "MSG_DL_ZONESIGNAL"   : bytearray.fromhex('DA 09 1C 00'),   # used    # zone signal strength - the 1C may be the zone count i.e. the 28 wireless zones
   "MSG_DL_2WKEYPAD"     : bytearray.fromhex('00 0A 08 00'),   # used
   "MSG_DL_1WKEYPAD"     : bytearray.fromhex('20 0A 40 00'),   # used
   "MSG_DL_SIRENS"       : bytearray.fromhex('60 0A 08 00'),   # used
   "MSG_DL_X10NAMES"     : bytearray.fromhex('30 0B 10 00'),   # used
   "MSG_DL_ZONENAMES"    : bytearray.fromhex('40 0B 1E 00'),   # used
   "MSG_DL_ZONESTR"      : bytearray.fromhex('00 19 00 02'),
   "MSL_DL_ZONECUSTOM"   : bytearray.fromhex('A0 1A 50 00'),
   
   "MSG_DL_MR_ZONENAMES" : bytearray.fromhex('60 09 40 00'),   # used
   "MSG_DL_MR_PINCODES"  : bytearray.fromhex('98 0A 60 00'),   # used
   "MSG_DL_MR_SIRENS"    : bytearray.fromhex('E2 B6 50 00'),   # used
   "MSG_DL_MR_KEYPADS"   : bytearray.fromhex('32 B7 40 01'),   # used
   "MSG_DL_MR_ZONES"     : bytearray.fromhex('72 B8 80 02'),   # used
   "MSG_DL_MR_ZONES1"    : bytearray.fromhex('72 B8 A0 00'),   # used
   "MSG_DL_MR_ZONES2"    : bytearray.fromhex('12 B9 A0 00'),   # used
   "MSG_DL_MR_ZONES3"    : bytearray.fromhex('B2 B9 A0 00'),   # used
   "MSG_DL_MR_ZONES4"    : bytearray.fromhex('52 BA A0 00'),   # used
   "MSG_DL_MR_SIRKEYZON" : bytearray.fromhex('E2 B6 10 04'),    # Combines Sirens keypads and sensors

   "MSG_DL_ALL"          : bytearray.fromhex('00 00 00 00')     #
}


# These blocks are meaningless, they are used to download blocks of EPROM data without reference to what the data means
#    Each block is 128 bytes long. Each EPROM page is 256 bytes so 2 downloads are needed per EPROM page
#    We have to do it like this as the max message size is 176 bytes. I decided this was messy so I download 128 bytes at a time instead
pmBlockDownload_t = {
   "MSG_DL_Block000"      : bytearray.fromhex('00 00 80 00'),   #
   "MSG_DL_Block001"      : bytearray.fromhex('80 00 80 00'),   #
   "MSG_DL_Block010"      : bytearray.fromhex('00 01 80 00'),   #
   "MSG_DL_Block011"      : bytearray.fromhex('80 01 80 00'),   #
   "MSG_DL_Block020"      : bytearray.fromhex('00 02 80 00'),   #
   "MSG_DL_Block021"      : bytearray.fromhex('80 02 80 00'),   #
   "MSG_DL_Block030"      : bytearray.fromhex('00 03 80 00'),   #
   "MSG_DL_Block031"      : bytearray.fromhex('80 03 80 00'),   #
   "MSG_DL_Block040"      : bytearray.fromhex('00 04 80 00'),   #
   "MSG_DL_Block041"      : bytearray.fromhex('80 04 80 00'),   #
   "MSG_DL_Block090"      : bytearray.fromhex('00 09 80 00'),   #
   "MSG_DL_Block091"      : bytearray.fromhex('80 09 80 00'),   #
   "MSG_DL_Block0A0"      : bytearray.fromhex('00 0A 80 00'),   #
   "MSG_DL_Block0A1"      : bytearray.fromhex('80 0A 80 00'),   #
   "MSG_DL_Block0B0"      : bytearray.fromhex('00 0B 80 00'),   #
   "MSG_DL_Block0B1"      : bytearray.fromhex('80 0B 80 00'),   #
   "MSG_DL_Block190"      : bytearray.fromhex('00 19 80 00'),   #
   "MSG_DL_Block191"      : bytearray.fromhex('80 19 80 00'),   #
   "MSG_DL_Block1A0"      : bytearray.fromhex('00 1A 80 00'),   #
   "MSG_DL_Block1A1"      : bytearray.fromhex('80 1A 80 00'),   #
   "MSG_DL_BlockB60"      : bytearray.fromhex('00 B6 80 00'),   #
   "MSG_DL_BlockB61"      : bytearray.fromhex('80 B6 80 00'),   #
   "MSG_DL_BlockB70"      : bytearray.fromhex('00 B7 80 00'),   #
   "MSG_DL_BlockB71"      : bytearray.fromhex('80 B7 80 00'),   #
   "MSG_DL_BlockB80"      : bytearray.fromhex('00 B8 80 00'),   #
   "MSG_DL_BlockB81"      : bytearray.fromhex('80 B8 80 00'),   #
   "MSG_DL_BlockB90"      : bytearray.fromhex('00 B9 80 00'),   #
   "MSG_DL_BlockB91"      : bytearray.fromhex('80 B9 80 00'),   #
   "MSG_DL_BlockBA0"      : bytearray.fromhex('00 BA 80 00'),   #
   "MSG_DL_BlockBA1"      : bytearray.fromhex('80 BA 80 00')    #
}

#Private VMSG_DL_MASTER10_EVENTLOG As Byte[] = [&H3E, &HFF, &HFF, &HD2, &H07, &HB0, &H05, &H48, &H01, &H00, &H00] '&H3F
#Private VMSG_DL_MASTER30_EVENTLOG As Byte[] = [&H3E, &HFF, &HFF, &H42, &H1F, &HB0, &H05, &H48, &H01, &H00, &H00] '&H3F

# Message types we can receive with their length and whether they need an ACK.
#    When isvariablelength is True:
#             the length is the fixed number of bytes in the message.  Add this to the variable part when it is received to get the total packet length.
#             varlenbytepos is the byte position of the variable length of the message.
#    When length is 0 then we stop processing the message on the first 0x0A. This is only used for the short messages (4 or 5 bytes long) like ack, stop, denied and timeout
PanelCallBack = collections.namedtuple("PanelCallBack", 'length ackneeded isvariablelength varlenbytepos flexiblelength' )
pmReceiveMsg_t = {
   0x00 : PanelCallBack(  0,  True, False, -1, 0 ),   # Dummy message used in the algorithm when the message type is unknown. The -1 is used to indicate an unknown message in the algorithm
   0x02 : PanelCallBack(  0, False, False,  0, 0 ),   # Ack
   0x06 : PanelCallBack(  0, False, False,  0, 0 ),   # Timeout. See the receiver function for ACK handling
   0x08 : PanelCallBack(  0, False, False,  0, 0 ),   # Access Denied
   0x0B : PanelCallBack(  0,  True, False,  0, 0 ),   # Stop --> Download Complete
   0x0F : PanelCallBack(  0, False, False,  0, 0 ),   # THE PANEL DOES NOT SEND THIS. THIS IS USED FOR A LOOP BACK TEST
   0x22 : PanelCallBack( 14,  True, False,  0, 0 ),   # 14 Panel Info (older visonic powermax panels)
   0x25 : PanelCallBack( 14,  True, False,  0, 0 ),   # 14 Download Retry
   0x33 : PanelCallBack( 14,  True, False,  0, 0 ),   # 14 Download Settings
   0x3C : PanelCallBack( 14,  True, False,  0, 0 ),   # 14 Panel Info
   0x3F : PanelCallBack(  7,  True,  True,  4, 5 ),   # Download Info in varying lengths  (For variable length, the length is the fixed number of bytes).  
   0xA0 : PanelCallBack( 15,  True, False,  0, 0 ),   # 15 Event Log
   0xA3 : PanelCallBack( 15,  True, False,  0, 0 ),   # 15 Zone Names
   0xA5 : PanelCallBack( 15,  True, False,  0, 0 ),   # 15 Status Update       Length was 15 but panel seems to send different lengths
   0xA6 : PanelCallBack( 15,  True, False,  0, 0 ),   # 15 Zone Types I think!!!!
   0xA7 : PanelCallBack( 15,  True, False,  0, 0 ),   # 15 Panel Status Change
   0xAB : PanelCallBack( 15,  True, False,  0, 0 ),   # 15 Enroll Request 0x0A  OR Ping 0x03      Length was 15 but panel seems to send different lengths
   0xAC : PanelCallBack( 15,  True, False,  0, 0 ),   # 15 X10 Names ???
   0xB0 : PanelCallBack(  8,  True,  True,  4, 2 ),   # The B0 message comes in varying lengths, sometimes it is shorter than what is states and the CRC is sometimes wrong
   0xF1 : PanelCallBack(  0,  True,  True,  0, 0 ),   # The F1 message needs to be ignored, I have no idea what it is but the crc is always wrong and only Powermax+ panels seem to send it
   0xF4 : PanelCallBack(  7,  True,  True,  4, 2 )    # The F4 message comes in varying lengths. Can't decode it yet but accept and ignore it. Not sure about the length of 7 for the fixed part.
}

pmReceiveMsgB0_t = {
   0x04 : "Zone status",
   0x18 : "Open/close status",
   0x39 : "Activity"
}

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

pmArmMode_t = {
   PyPanelCommand.DISARM : 0x00, PyPanelCommand.ARM_HOME : 0x04, PyPanelCommand.ARM_AWAY : 0x05, PyPanelCommand.ARM_HOME_INSTANT : 0x14, PyPanelCommand.ARM_AWAY_INSTANT : 0x15    # "usertest" : 0x06,
}

pmX10State_t = {
   PyX10Command.OFF : 0x00, PyX10Command.ON : 0x01, PyX10Command.DIM : 0x0A, PyX10Command.BRIGHTEN : 0x0B
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

pmPanelAlarmType_t = {
   0x01 : "Intruder",  0x02 : "Intruder", 0x03 : "Intruder", 0x04 : "Intruder", 0x05 : "Intruder", 0x06 : "Tamper",
   0x07 : "Tamper",    0x08 : "Tamper",   0x09 : "Tamper",   0x0B : "Panic",    0x0C : "Panic",    0x20 : "Fire",
   0x23 : "Emergency", 0x49 : "Gas",      0x4D : "Flood",    0x4F : "X10"
}

pmPanelTroubleType_t = {
   0x0A  : "Communication", 0x0F  : "General",   0x29  : "Battery", 0x2B: "Power",   0x2D : "Battery", 0x2F : "Jamming",
   0x31  : "Communication", 0x33  : "Telephone", 0x36  : "Power",   0x38 : "Battery", 0x3B : "Battery", 0x3C : "Battery",
   0x40  : "Battery",       0x43  : "Battery"
}

pmPanelType_t = {
   0 : "PowerMax", 1 : "PowerMax+", 2 : "PowerMax Pro", 3 : "PowerMax Complete", 4 : "PowerMax Pro Part",
   5  : "PowerMax Complete Part", 6 : "PowerMax Express", 7 : "PowerMaster10",   8 : "PowerMaster30",
   10 : "PowerMaster33", 15 : "PowerMaster33"
}

# Config for each panel type (0-16).  8 is a PowerMaster 30, 10 is a PowerMaster 33, 15 is a PowerMaster 33 later model.  Don't know what 9, 11, 12, 13 or 14 is.
pmPanelConfig_t = {
   "CFG_PARTITIONS"  : (   1,   1,   1,   1,   3,   3,   1,   3,   3,   3,   3,   3,   3,   3,   3,   3,   3 ),
   "CFG_EVENTS"      : ( 250, 250, 250, 250, 250, 250, 250, 250,1000,1000,1000,1000,1000,1000,1000,1000,1000 ),
   "CFG_KEYFOBS"     : (   8,   8,   8,   8,   8,   8,   8,   8,  32,  32,  32,  32,  32,  32,  32,  32,  32 ),
   "CFG_1WKEYPADS"   : (   8,   8,   8,   8,   8,   8,   8,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0 ),
   "CFG_2WKEYPADS"   : (   2,   2,   2,   2,   2,   2,   2,   8,  32,  32,  32,  32,  32,  32,  32,  32,  32 ),
   "CFG_SIRENS"      : (   2,   2,   2,   2,   2,   2,   2,   4,   8,   8,   8,   8,   8,   8,   8,   8,   8 ),
   "CFG_USERCODES"   : (   8,   8,   8,   8,   8,   8,   8,   8,  48,  48,  48,  48,  48,  48,  48,  48,  48 ),
   "CFG_PROXTAGS"    : (   0,   0,   8,   0,   8,   8,   0,   8,  32,  32,  32,  32,  32,  32,  32,  32,  32 ),
   "CFG_WIRELESS"    : (  28,  28,  28,  28,  28,  28,  28,  29,  62,  62,  62,  62,  62,  62,  62,  62,  62 ), # 30, 64
   "CFG_WIRED"       : (   2,   2,   2,   2,   2,   2,   1,   1,   2,   2,   2,   2,   2,   2,   2,   2,   2 ),
   "CFG_ZONECUSTOM"  : (   0,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5,   5 )
}

# PMAX EEPROM CONFIGURATION version 1_2
SettingsCommand = collections.namedtuple('SettingsCommand', 'show count type size poff psize pstep pbitoff name values')
DecodePanelSettings = {
    # USER SETTINGS                                      # size poff  psize pstep pbitoff
    "usePhoneNrs"    : SettingsCommand( False, 4, "PHONE",  64,  310,   64,   8,    -1,  ["1st Private Tel. No.","2nd Private Tel. No.","3rd Private Tel. No.","4th Private Tel. No."],  {} ),  # 310, 318, 326, 334
    "usrVoice"       : SettingsCommand(  True, 1, "BYTE",    8,  763,    8,   0,    -1,  "Set Voice Option", { '0':"Disable Voice", '1':"Enable Voice"} ),
    "usrArmOption"   : SettingsCommand(  True, 1, "BYTE",    8,  280,    1,   0,     5,  "Auto Arm Option",  { '1':"Enable", '0':"Disable"} ),
    "usrArmTime"     : SettingsCommand(  True, 1, "TIME",   16,  765,   16,   0,    -1,  "Auto Arm Time",    {  }),
    "usrSquawk"      : SettingsCommand(  True, 1, "BYTE",    8,  764,    8,   0,    -1,  "Squawk Option",    { '0':"Disable", '1':"Low Level", '2':"Medium Level", '3':"High Level"}),
    "usrTimeFormat"  : SettingsCommand(  True, 1, "BYTE",    8,  281,    1,   0,     1,  "Time Format",      { '0':"USA - 12H", '1':"Europe - 24H"}),
    "usrDateFormat"  : SettingsCommand(  True, 1, "BYTE",    8,  281,    1,   0,     2,  "Date Format",      { '0':"USA MM/DD/YYYY", '1':"Europe DD/MM/YYYY"}),

    # PANEL DEFINITION
    "entryDelays"    : SettingsCommand(  True, 2, "BYTE",    8,  257,    8,   1,     2,  ["Entry Delay 1","Entry Delay 2"],    {'0':"None", '15':"15 Seconds", '30':"30 Seconds", '45':"45 Seconds", '60':"1 Minute", '180':"3 Minutes", '240':"4 Minutes"}),  # 257, 258

    "exitDelay"      : SettingsCommand(  True, 1, "BYTE",    8,  259,    8,   0,    -1,  "Exit Delay",       { '30':"30 Seconds", '60':"60 Seconds", '90':"90 Seconds", '120':"2 Minutes", '180':"3 Minutes", '240':"4 Minutes"}),
    "bellTime"       : SettingsCommand(  True, 1, "BYTE",    8,  260,    8,   0,    -1,  "Bell Time",        { '1':"1 Minute", '3':"3 Minutes", '4':"4 Minutes", '8':"8 Minutes", '10':"10 Minutes", '15':"15 Minutes", '20':"20 Minutes"}),
    "abortTime"      : SettingsCommand(  True, 1, "BYTE",    8,  267,    8,   0,    -1,  "Abort Time",       { '0':"None", '15':"15 Seconds", '30':"30 Seconds", '45':"45 Seconds", '60':"1 Minute", '120':"2 Minutes", '180':"3 Minutes", '240':"4 Minutes"} ),
    "cancelTime"     : SettingsCommand(  True, 1, "BYTE",    8,  266,    8,   0,    -1,  "Alarm Cancel Time",   {  '1':"1 Minute", '5':"5 Minutes", '15':"15 Minutes", '60':"60 Minutes", '240':"4 Hours", '0':"Inactive"}),
    "quickArm"       : SettingsCommand(  True, 1, "BYTE",    8,  283,    1,   0,     3,  "Quick Arm",           { '1':"On", '0':"Off"} ),
    "bypass"         : SettingsCommand(  True, 1, "BYTE",    8,  284,    2,   0,     6,  "Bypass",              { '2':"Manual Bypass", '0':"No Bypass", '1':"Force Arm"} ),
    "exitMode"       : SettingsCommand(  True, 1, "BYTE",    8,  282,    2,   0,     6,  "Exit Mode",           { '1':"Restart Exit", '2':"Off by Door", '0':"Normal"} ),
    "piezoBeeps"     : SettingsCommand(  True, 1, "BYTE",    8,  261,    8,   0,    -1,  "Piezo Beeps",         { '2':"Enable", '1':"Off when Home", '0':"Disable"} ),
    "troubleBeeps"   : SettingsCommand(  True, 1, "BYTE",    8,  284,    2,   0,     1,  "Trouble Beeps",       { '3':"Enable", '1':"Off at Night", '0':"Disable"} ),
    "panicAlarm"     : SettingsCommand(  True, 1, "BYTE",    8,  282,    2,   0,     4,  "Panic Alarm",         { '1':"Silent Panic", '2':"Audible Panic", '0':"Disable Panic"}  ),
    "swingerStop"    : SettingsCommand(  True, 1, "BYTE",    8,  262,    8,   0,    -1,  "Swinger Stop",        { '1':"After 1 Time", '2':"After 2 Times", '3':"After 3 Times", '0':"No Shutdown"} ),
    "crossZoning"    : SettingsCommand(  True, 1, "BYTE",    8,  284,    1,   0,     0,  "Cross Zoning",        { '1':"On", '0':"Off"} ),
    "supervision"    : SettingsCommand(  True, 1, "BYTE",    8,  264,    8,   0,    -1,  "Supevision Interval", { '1':"1 Hour", '2':"2 Hours", '4':"4 Hours", '8':"8 Hours", '12':"12 Hours", '0':"Disable"} ),
    "notReady"       : SettingsCommand(  True, 1, "BYTE",    8,  281,    1,   0,     4,  "Not Ready",           { '0':"Normal", '1':"In Supervision"}  ),
    "fobAux"         : SettingsCommand(  True, 2, "BYTE",    8,  263,    8,  14,    -1,  ["Auxiliary Keyfob Button function 1","Auxiliary Keyfob Button function 2"], { '1':"System Status", '2':"Instant Arm", '3':"Cancel Exit Delay", '4':"PGM/X-10"} ), # 263, 277

    "jamDetect"      : SettingsCommand(  True, 1, "BYTE",    8,  256,    8,   0,    -1,  "Jamming Detection",       { '1':"UL 20/20", '2':"EN 30/60", '3':"Class 6", '4':"Other", '0':"Disable"} ),
    "latchKey"       : SettingsCommand(  True, 1, "BYTE",    8,  283,    1,   0,     7,  "Latchkey Arming",         { '1':"On", '0':"Off"} ),
    "noActivity"     : SettingsCommand(  True, 1, "BYTE",    8,  265,    8,   0,    -1,  "No Activity Time",        { '3':"3 Hours", '6':"6 Hours",'12':"12 Hours", '24':"24 Hours", '48':"48 Hours", '72':"72 Hours", '0':"Disable"} ),
    "backLight"      : SettingsCommand(  True, 1, "BYTE",    8,  283,    1,   0,     5,  "Back Light Time",         { '1':"Allways On", '0':"Off After 10 Seconds"} ),
    "duress"         : SettingsCommand(  True, 1, "CODE",   16,  273,   16,   0,    -1,  "Duress",                  {  } ),
    "piezoSiren"     : SettingsCommand(  True, 1, "BYTE",    8,  284,    1,   0,     5,  "Piezo Siren",             { '1':"On", '0':"Off"} ),
    "resetOption"    : SettingsCommand(  True, 1, "BYTE",    8,  270,    8,   0,    -1,  "Reset Option",            { '1':"Engineer Reset", '0':"User Reset"}  ),
    "tamperOption"   : SettingsCommand(  True, 1, "BYTE",    8,  280,    1,   0,     1,  "Tamper Option",           { '1':"On", '0':"Off"} ),
    "sirenOnLine"    : SettingsCommand(  True, 1, "BYTE",    8,  282,    1,   0,     1,  "Siren On Line",           { '1':"Enable on Fail", '0':"Disable on Fail"}  ),
    "memoryPrompt"   : SettingsCommand(  True, 1, "BYTE",    8,  281,    1,   0,     0,  "Memory Prompt",           { '1':"Enable", '0':"Disable" } ),
    "disarmOption"   : SettingsCommand(  True, 1, "BYTE",    8,  281,    2,   0,     6,  "Disarm Option",           { '0':"Any Time", '1':"On Entry All", '2':"On Entry Wireless", '3':"Entry + Away KP"} ),
    "bellReport"     : SettingsCommand(  True, 1, "BYTE",    8,  283,    1,   0,     0,  "Bell Report Option",      { '1':"EN Standard", '0':"Others"}  ),
    "lowBattery"     : SettingsCommand(  True, 1, "BYTE",    8,  281,    1,   0,     3,  "Low Battery Acknowledge", { '1':"On", '0':"Off"} ),
    "screenSaver"    : SettingsCommand(  True, 1, "BYTE",    8,  269,    8,   0,    -1,  "Screen Saver",            { '2':"Reset By Key", '1':"Reset By Code", '0':"Off"} ),
    "confirmAlarm"   : SettingsCommand(  True, 1, "BYTE",    8,  268,    8,   0,    -1,  "Confirm Alarm Timer",     { '0':"None", '30':"30 Minutes", '45':"45 Minutes", '60':"60 Minutes", '90':"90 Minutes"} ),
    "acFailure"      : SettingsCommand(  True, 1, "BYTE",    8,  275,    8,   0,    -1,  "AC Failure Report",       { '0':"None", '5':"5 Minutes", '30':"30 Minutes", '60':"60 Minutes", '180':"180 Minutes"} ),
    "userPermit"     : SettingsCommand(  True, 1, "BYTE",    8,  276,    8,   0,    -1,  "User Permit",             { '1':"Enable", '0':"Disable"} ),

    # COMMUNICATION SETTINGS
    "autotestTime"   : SettingsCommand(  True, 1, "TIME",   16,  367,   16,   0,    -1,  "Autotest Time", {} ),
    "autotestCycle"  : SettingsCommand(  True, 1, "BYTE",    8,  369,    8,   0,    -1,  "Autotest Cycle", { '1':"1 Day", '4':"5 Days", '2':"7 Days", '3':"30 Days", '0':"Disable"}  ),
    "areaCode"       : SettingsCommand( False, 1, "CODE",   24,  371,   24,   0,    -1,  "Area Code", {} ),
    "outAccessNr"    : SettingsCommand( False, 1, "CODE",    8,  374,    8,   0,    -1,  "Out Access Number", {} ),
    "centralStation" : SettingsCommand(  True, 2, "PHONE",  64,  288,   64,  11,    -1,  ["1st Central Station (CNTR) Tel. No.", "2nd Central Station (CNTR) Tel. No."], {} ), # 288, 299
    "accountNo"      : SettingsCommand(  True, 2, "ACCOUNT",24,  296,   24,  11,    -1,  ["1st Account No","2nd Account No"], {} ), # 296, 307
    "reportFormat"   : SettingsCommand(  True, 1, "BYTE",    8,  363,    8,   0,    -1, "Report Format",                     { '0':"Contact ID", '1':"SIA", '2':"4/2 1900/1400", '3':"4/2 1800/2300", '4':"Scancom"}  ),
    "pulseRate"      : SettingsCommand(  True, 1, "BYTE",    8,  364,    8,   0,    -1, "4/2 Pulse Rate",                    { '0':"10 pps", '1':"20 pps", '2':"33 pps", '3':"40 pps"} ),
    "reportCentral"  : SettingsCommand(  True, 1, "BYTE",    8,  359,    8,   0,    -1, "Report to Central Station",         { '15':"All * Backup", '7':"All but Open/Close * Backup", '255':"All * All", '119':"All but Open/Close * All but Open/Close", '135':"All but Alert * Alert", '45':"Alarms * All but Alarms", '0':"Disable"} ),
    "reportConfirm"  : SettingsCommand(  True, 1, "BYTE",    8,  285,    2,   0,     6, "Report Confirmed Alarm",            { '0':"Disable Report", '1':"Enable Report", '2':"Enable + Bypass"} ),
    "send2wv"        : SettingsCommand(  True, 1, "BYTE",    8,  280,    1,   0,     6, "Send 2wv Code",                     { '1':"Send", '0':"Don't Send"} ),
    "voice2Central"  : SettingsCommand(  True, 1, "BYTE",    8,  366,    8,   0,    -1, "Two-Way Voice To Central Stations", { '10':"Time-out 10 Seconds", '45':"Time-out 45 Seconds", '60':"Time-out 60 Seconds", '90':"Time-out 90 Seconds", '120':"Time-out 2 Minutes", '1':"Ring Back", '0':"Disable"} ),
    "ringbackTime"   : SettingsCommand(  True, 1, "BYTE",    8,  358,    8,   0,    -1, "Ringback Time",                     { '1':"1 Minute", '3':"3 Minutes", '5':"5 Minutes", '10':"10 Minutes"} ),
    "csDialAttempt"  : SettingsCommand(  True, 1, "BYTE",    8,  362,    8,   0,    -1, "Central Station Dialing Attempts",  { '2':"2", '4':"4", '8':"8", '12':"12", '16':"16"} ),
    "ringbackNrs"    : SettingsCommand(  True, 4, "PHONE",  64,  310,   64,   8,    -1, ["1st Ringback Tel No","2nd Ringback Tel No","3rd Ringback Tel No","4th Ringback Tel No"], {} ),  # 310, 318, 326, 334
    "voice2Private"  : SettingsCommand(  True, 1, "BYTE",    8,  283,    1,   0,     6, "Two-Way Voice - Private Phones",    { '0':"Disable", '1':"Enable"} ),

    "privateAttempt" : SettingsCommand( False, 1, "BYTE",    8,  365,    8,   0,    -1, "Private Telephone Dialing Attempts", { '1':"1 Attempt", '2':"2 Attempts", '3':"3 Attempts", '4':"4 Attempts"} ),
    "privateReport"  : SettingsCommand(  True, 1, "BYTE",    8,  361,    8,   0,    -1, "Reporting To Private Tel",           { '15':"All", '7':"All but Open/Close", '13':"All but Alerts", '1':"Alarms", '2':"Alerts", '8':"Open/Close", '0':"Disable Report"} ),

    "privateAck"     : SettingsCommand( False, 1, "BYTE",    8,  285,    1,   0,     1, "Private Telephone Acknowledge",      { '0':"Single Acknowledge", '1':"All Acknowledge"} ),
    "pagerNr"        : SettingsCommand(  True, 1, "PHONE",  64,  342,   64,   0,    -1, "Pager Tel Number", {} ),
    "pagerPIN"       : SettingsCommand(  True, 1, "PHONE",  64,  350,   64,   0,    -1, "Pager PIN #", {} ),
    "pagerReport"    : SettingsCommand(  True, 1, "BYTE",    8,  360,    8,   0,    -1, "Report To Pager", { '15':"All", '3':"All + Alerts", '7':"All but Open/Close", '12':"Troubles+Open/Close", '4':"Troubles", '8':"Open/Close", '0':"Disable Report"}  ),

    "recentClose"    : SettingsCommand(  True, 1, "BYTE",    8,0x11C,    1,   0,     3, "Recent Close Report", { '1':"On", '0':"Off"} ),
    "remoteAccess"   : SettingsCommand(  True, 1, "BYTE",    8,0x11D,    1,   0,     2, "Remote Access",       { '1':"On", '0':"Off"}),
    "masterCode"     : SettingsCommand( False, 1, "CODE",   16,0x20A,   16,   0,    -1, "Master Code",      {} ),
    "installerCode"  : SettingsCommand( False, 1, "CODE",   16,0x20C,   16,   0,    -1, "Installer Code",         {} ),
    "masterDlCode"   : SettingsCommand( False, 1, "CODE",   16,0x20E,   16,   0,    -1, "Master Download Code", {} ),
    "instalDlCode"   : SettingsCommand( False, 1, "CODE",   16,0x210,   16,   0,    -1, "Installer Download Code", {} ),

    "zoneRestore"    : SettingsCommand(  True, 1, "BYTE",    8,0x118,    1,   0,     0, "Zone Restore", { '0':"Report Restore", '1':"Don't Report"} ),
    "uploadOption"   : SettingsCommand(  True, 1, "BYTE",    8,0x11A,    1,   0,     2, "Upload Option", { '0':"When System Off", '1':"Any Time"} ),
    "dialMethod"     : SettingsCommand(  True, 1, "BYTE",    8,0x11D,    1,   0,     0, "Dialing Method", { '0':"Tone (DTMF)", '1':"Pulse"} ),
    "lineFailure"    : SettingsCommand(  True, 1, "BYTE",    8,  375,    8,   0,    -1, "Line Failure Report", { '0':"Don't Report", '1':"Immediately", '5':"5 Minutes", '30':"30 Minutes", '60':"60 Minutes", '180':"180 Minutes"} ),
    "remoteProgNr"   : SettingsCommand(  True, 1, "PHONE",  64,  376,   64,   0,    -1, "Remote Programmer Tel. No.", {} ),
    "inactiveReport" : SettingsCommand(  True, 1, "BYTE",    8,  384,    8,   0,    -1, "System Inactive Report", { '0':"Disable", '180':"7 Days", '14':"14 Days", '30':"30 Days", '90':"90 Days"} ),
    "ambientLevel"   : SettingsCommand(  True, 1, "BYTE",    8,  388,    8,   0,    -1, "Ambient Level", { '0':"High Level", '1':"Low Level"} ),

    # GSM DEFINITIONS
    "gsmInstall"     : SettingsCommand(  True, 1, "BYTE",    8,  395,    8,   0,    -1, "GSM Install", { '1':"Installed", '0':"Not Installed"} ),
    "gsmSmsNrs"      : SettingsCommand( False, 4, "PHONE",  64,  396,   64,   8,    -1, ["GSM 1st SMS Number","GSM 2nd SMS Number","GSM 3rd SMS Number","GSM 4th SMS Number"], {} ),  #  396,404,412,420
    "gsmSmsReport"   : SettingsCommand(  True, 1, "BYTE",    8,  393,    8,   0,    -1, "GSM Report to SMS", { '15':"All", '7':"All but Open/Close", '13':"All but Alerts", '1':"Alarms", '2':"Alerts", '8':"Open/Close", '0':"Disable Report"} ),
    "gsmFailure"     : SettingsCommand(  True, 1, "BYTE",    8,  394,    8,   0,    -1, "GSM Line Failure", { '0':"Don't Report", '2':"2 Minutes", '5':"5 Minutes", '15':"15 Minutes", '30':"30 Minutes"} ),
    "gsmPurpose"     : SettingsCommand(  True, 1, "BYTE",    8,  392,    8,   0,    -1, "GSM Line Purpose", { '1':"GSM is Backup", '2':"GSM is Primary", '3':"GSM Only", '0':"SMS Only" } ),
    "gsmAntenna"     : SettingsCommand(  True, 1, "BYTE",    8,  447,    8,   0,    -1, "GSM Select Antenna", { '0':"Internal antenna", '1':"External antenna", '2':"Auto detect"} ),

    # DEFINE POWERLINK
    "plFailure"      : SettingsCommand(  True, 1, "BYTE",    8,  391,    8,   0,    -1, "PowerLink Failure", { '1':"Report", '0':"Disable Report"} ),

    # PGM DEFINITION
    "pgmPulseTime"   : SettingsCommand(  True, 1, "BYTE",    8,  681,    8,   0,    -1, "PGM Pulse Time", { '2':"2 Seconds", '30':"30 Seconds", '120':"2 Minutes", '240':"4 Minutes"} ),
    "pgmByArmAway"   : SettingsCommand(  True, 1, "BYTE",    8,  537,    8,   0,    -1, "PGM By Arm Away", { '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active"} ),
    "pgmByArmHome"   : SettingsCommand(  True, 1, "BYTE",    8,  553,    8,   0,    -1, "PGM By Arm Home", { '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active"} ),
    "pgmByDisarm"    : SettingsCommand(  True, 1, "BYTE",    8,  569,    8,   0,    -1, "PGM By Disarm", { '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active"} ),
    "pgmByMemory"    : SettingsCommand(  True, 1, "BYTE",    8,  601,    8,   0,    -1, "PGM By Memory", { '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active"} ),
    "pgmByDelay"     : SettingsCommand(  True, 1, "BYTE",    8,  585,    8,   0,    -1, "PGM By Delay", { '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active"}  ),
    "pgmByKeyfob"    : SettingsCommand(  True, 1, "BYTE",    8,  617,    8,   0,    -1, "PGM By Keyfob", { '0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    "pgmByLineFail"  : SettingsCommand(  True, 1, "BYTE",    8,  280,    1,   0,     2, "PGM By Line Fail", { '1':"Yes", '0':"No"} ),
    "pgmZone"        : SettingsCommand( False, 3, "CODE",    8,  697,    8,   1,    -1, ["PGM Zone A","PGM Zone B","PGM Zone C"], {} ),   # 697, 698, 699
    "pgmActZone"     : SettingsCommand(  True, 3, "BYTE",    8,  633,    8,  16,    -1, ["PGM Act Zone A","PGM Act Zone B","PGM Act Zone C"], {'0':"Disable", '1':"Turn Off", '2':"Turn On", '3':"Pulse Active"} ), # 633, 649, 665

    # DEFINE INTERNAL
    "intStrobe"      : SettingsCommand(  True, 1, "BYTE",    8,  283,    1,   0,     1, "Internal/Strobe Siren", { '0':"Internal Siren", '1':"Strobe"} ),

    # X-10 GENERAL DEFINITION
    "x10HouseCode"   : SettingsCommand(  True, 1, "BYTE",    8,  536,    8,   0,    -1, "X10 House Code", { '0':"A", '1':"B", '2':"C", '3':"D", '4':"E", '5':"F", '6':"G", '7':"H", '8':"I", '9':"J", '10':"K", '11':"L", '12':"M", '13':"N", '14':"O", '15':"P"}  ),

    "x10Flash"       : SettingsCommand(  True, 1, "BYTE",    8,  281,    1,   0,     5, "X10 Flash On Alarm", { '1':"All Lights Flash", '0':"No Flash"} ),
    "x10Trouble"     : SettingsCommand(  True, 1, "BYTE",    8,  747,    8,   0,    -1, "X10 Trouble Indication", { '1':"Enable", '0':"Disable"} ),
    "x10ReportCs1"   : SettingsCommand(  True, 1, "BYTE",    8,  749,    1,   0,     0, "X10 Report on Fail to Central Station 1", {'1':"Enable", '0':"Disable"} ),
    "x10ReportCs2"   : SettingsCommand(  True, 1, "BYTE",    8,  749,    1,   0,     1, "X10 Report on Fail to Central Station 2", {'1':"Enable", '0':"Disable"} ),
    "x10ReportPagr"  : SettingsCommand(  True, 1, "BYTE",    8,  749,    1,   0,     2, "X10 Report on Fail to Pager", {'1':"Enable", '0':"Disable"} ),

    "x10ReportPriv"  : SettingsCommand(  True, 1, "BYTE",    8,  749,    1,   0,     3, "X10 Report on Fail to Private", {'1':"Enable", '0':"Disable"} ),
    "x10ReportSMS"   : SettingsCommand(  True, 1, "BYTE",    8,  749,    1,   0,     4, "X10 Report on Fail to SMS", {'1':"Enable", '0':"Disable"} ),
    "x10Lockout"     : SettingsCommand(  True, 1, "TIME",   16,  532,   16,   0,    -1, "X10 Lockout Time (start HH:MM)", {} ),
    "x10Phase"       : SettingsCommand(  True, 1, "BYTE",    8,  748,    8,   0,    -1, "X10 3 Phase and frequency", { '0':"Disable", '1':"50 Hz", '2':"60 Hz"} ),

    "panelSerialCode": SettingsCommand( False, 1, "BYTE",    8,0x437,    8,   0,    -1, "Panel Serial Code", {} ),  # page 4 offset 55
    "panelTypeCode"  : SettingsCommand( False, 1, "BYTE",    8,0x436,    8,   0,    -1, "Panel Code Type", {} ),  # page 4 offset 54 and 55 ->> Panel type code
    "panelSerial"    : SettingsCommand(  True, 1, "CODE",   48,0x430,   48,   0,    -1, "Panel Serial", {} ),  # page 4 offset 48

    # ZONES
    "zoneNameRaw"    : SettingsCommand( False,31, "STRING", 0x80, 0x1900,  0x80,   0x10,   -1, "Zone name <x>", {} ),
    "panelEprom"     : SettingsCommand(  True, 1, "STRING",  128,  0x400,   128,   0,   -1, "Panel Eprom", {} ),
    "panelSoftware"  : SettingsCommand(  True, 1, "STRING",  144,  0x410,   144,   0,   -1, "Panel Software", {} )
}
#	{ count:31, name:"", type:"STRING", size:0x80, poff:0x1900, psize:0x80, pstep:0x10 }


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
pmZoneName_t = (
   "Attic", "Back door", "Basement", "Bathroom", "Bedroom", "Child room", "Conservatory", "Play room", "Dining room", "Downstairs",
   "Emergency", "Fire", "Front door", "Garage", "Garage door", "Guest room", "Hall", "Kitchen", "Laundry room", "Living room",
   "Master bathroom", "Master bedroom", "Office", "Upstairs", "Utility room", "Yard", "Custom 1", "Custom 2", "Custom 3",
   "Custom 4", "Custom 5", "Not Installed"
)

pmZoneChime_t = {
   "EN" : ("Off", "Melody", "Zone", "Invalid"),
   "NL" : ("Uit", "Muziek", "Zone", "Invalid"),
   "FR" : ("Eteint", "Melodie", "Zone", "Invalide")
}

# Note: names need to match to VAR_xxx
pmZoneSensorMaxGeneric_t = {
   0x0 : PySensorType.VIBRATION, 0x2 : PySensorType.SHOCK, 0x3 : PySensorType.MOTION, 0x4 : PySensorType.MOTION, 0x5 : PySensorType.MAGNET, 
   0x6 : PySensorType.MAGNET, 0x7 : PySensorType.MAGNET, 0xA : PySensorType.SMOKE, 0xB : PySensorType.GAS, 0xC : PySensorType.MOTION, 
   0xF : PySensorType.WIRED
} # unknown to date: Push Button, Flood, Universal

ZoneSensorType = collections.namedtuple("ZoneSensorType", 'name func' )
pmZoneSensorMax_t = {
   0x1A : ZoneSensorType("MCW-K980", PySensorType.MOTION ),        # Botap
   0x75 : ZoneSensorType("Next K9-85", PySensorType.MOTION ),      # thermostat (Visonic part number 0-3592-B, NEXT K985 DDMCW)
   0x7A : ZoneSensorType("MCT-550", PySensorType.FLOOD ),          # fguerzoni
   0x95 : ZoneSensorType("MCT-302", PySensorType.MAGNET ),         # me, fguerzoni
   0x96 : ZoneSensorType("MCT-302", PySensorType.MAGNET ),         # me, g4seb
   0xC0 : ZoneSensorType("Next K9-85", PySensorType.MOTION ),      # g4seb
   0xD3 : ZoneSensorType("Next MCW", PySensorType.MOTION ),        # me
   0xD5 : ZoneSensorType("Next K9", PySensorType.MOTION ),         # fguerzoni
   0xE4 : ZoneSensorType("Next MCW", PySensorType.MOTION ),        # me
   0xE5 : ZoneSensorType("Next K9-85", PySensorType.MOTION ),      # g4seb, fguerzoni
   0xF3 : ZoneSensorType("MCW-K980", PySensorType.MOTION ),        # Botap
   0xFF : ZoneSensorType("Wired", PySensorType.WIRED )
}

# SMD-426 PG2 (photoelectric smoke detector)
# SMD-427 PG2 (heat and photoelectric smoke detector)
# SMD-429 PG2 (Smoke and Heat Detector)
pmZoneSensorMaster_t = {
   0x01 : ZoneSensorType("Next PG2", PySensorType.MOTION ),
   0x03 : ZoneSensorType("Clip PG2", PySensorType.MOTION ),
   0x04 : ZoneSensorType("Next CAM PG2", PySensorType.CAMERA ),
   0x05 : ZoneSensorType("GB-502 PG2", PySensorType.SOUND ),
   0x06 : ZoneSensorType("TOWER-32AM PG2", PySensorType.MOTION ),
   0x07 : ZoneSensorType("TOWER-32AMK9", PySensorType.MOTION ),
   0x0A : ZoneSensorType("TOWER CAM PG2", PySensorType.CAMERA ),
   0x0C : ZoneSensorType("MP-802 PG2", PySensorType.MOTION ),
   0x0F : ZoneSensorType("MP-902 PG2", PySensorType.MOTION ),
   0x15 : ZoneSensorType("SMD-426 PG2", PySensorType.SMOKE ),
   0x16 : ZoneSensorType("SMD-429 PG2", PySensorType.SMOKE ),
   0x18 : ZoneSensorType("GSD-442 PG2", PySensorType.SMOKE ),
   0x19 : ZoneSensorType("FLD-550 PG2", PySensorType.FLOOD ),
   0x1A : ZoneSensorType("TMD-560 PG2", PySensorType.TEMPERATURE ),
   0x29 : ZoneSensorType("MC-302V PG2", PySensorType.MAGNET),
   0x2A : ZoneSensorType("MC-302 PG2", PySensorType.MAGNET),
   0x2D : ZoneSensorType("MC-302V PG2", PySensorType.MAGNET),
   0x35 : ZoneSensorType("SD-304 PG2", PySensorType.SHOCK),
   0xFE : ZoneSensorType("Wired", PySensorType.WIRED )
}


log = logging.getLogger(__name__)

class VisonicListEntry:
    def __init__(self, **kwargs):
        self.command = kwargs.get("command", None)
        self.options = kwargs.get("options", None)
        self.response = []
        if self.command.replytype is not None:
            self.response = self.command.replytype.copy()  # list of message reply needed
        # are we waiting for an acknowledge from the panel (do not send a message until we get it)
        if self.command.waitforack:
            self.response.append(ACK_MESSAGE)  # add an acknowledge to the list
        self.triedResendingMessage = False

    def __str__(self):
        return "Command:{0}    Options:{1}".format(self.command.msg, self.options)

class SensorDevice(PySensorDevice):
    
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", None)  # int   device id
        self.dname = kwargs.get("dname", None)  # str   device name
        self.stype = kwargs.get("stype", PySensorType.UNKNOWN)  # PySensorType  sensor type
        self.sid = kwargs.get("sid", None)  # int   sensor id
        self.ztype = kwargs.get("ztype", None)  # int   zone type
        self.zname = kwargs.get("zname", None)  # str   zone name
        self.ztypeName = kwargs.get("ztypeName", None)  # str   Zone Type Name
        self.zchime = kwargs.get("zchime", None)  # str   zone chime
        self.partition = kwargs.get("partition", None)  # set   partition set (could be in more than one partition)
        self.bypass = kwargs.get("bypass", False)  # bool  if bypass is set on this sensor
        self.lowbatt = kwargs.get("lowbatt", False)  # bool  if this sensor has a low battery
        self.status = kwargs.get("status", False)  # bool  status, as returned by the A5 message
        self.tamper = kwargs.get("tamper", False)  # bool  tamper, as returned by the A5 message
        self.ztamper = kwargs.get("ztamper", False)  # bool  zone tamper, as returned by the A5 message
        self.ztrip = kwargs.get("ztrip", False)  # bool  zone trip, as returned by the A5 message
        self.enrolled = kwargs.get("enrolled", False)  # bool  enrolled, as returned by the A5 message
        self.triggered = kwargs.get("triggered", False)  # bool  triggered, as returned by the A5 message
        self.triggertime = None  # datetime  This is used to time out the triggered value and set it back to false
        self.model = kwargs.get("model", None)  # str   device model

    def __str__(self):
        stypestr = ""
        if self.stype != PySensorType.UNKNOWN:
            stypestr = str(self.stype)
        elif self.sid is not None:
            stypestr = "Unk " + str(self.sid)
        else:
            stypestr = "Unknown"
        strn = ""
        strn = strn + ("id=None" if self.id == None else "id={0:<2}".format(self.id))
        strn = strn + (" dname=None" if self.dname == None else " dname={0:<4}".format(self.dname[:4]))
        strn = strn + (" stype={0:<8}".format(stypestr))
        # temporarily miss it out to shorten the line in debug messages        strn = strn + (" model=None" if self.model == None else " model={0:<8}".format(self.model[:14]))
        # temporarily miss it out to shorten the line in debug messages        strn = strn + (" sid=None"       if self.sid == None else       " sid={0:<3}".format(self.sid, type(self.sid)))
        # temporarily miss it out to shorten the line in debug messages        strn = strn + (" ztype=None"     if self.ztype == None else     " ztype={0:<2}".format(self.ztype, type(self.ztype)))
        strn = strn + (" zname=None" if self.zname == None else " zname={0:<14}".format(self.zname[:14]))
        strn = strn + (" ztypeName=None" if self.ztypeName == None else " ztypeName={0:<10}".format(self.ztypeName[:10]))
        strn = strn + (" ztamper=None" if self.ztamper == None else " ztamper={0:<2}".format(self.ztamper))
        strn = strn + (" ztrip=None" if self.ztrip == None else " ztrip={0:<2}".format(self.ztrip))
        # temporarily miss it out to shorten the line in debug messages        strn = strn + (" zchime=None"    if self.zchime == None else    " zchime={0:<12}".format(self.zchime, type(self.zchime)))
        # temporarily miss it out to shorten the line in debug messages        strn = strn + (" partition=None" if self.partition == None else " partition={0}".format(self.partition, type(self.partition)))
        strn = strn + (" bypass=None" if self.bypass == None else " bypass={0:<2}".format(self.bypass))
        strn = strn + (" lowbatt=None" if self.lowbatt == None else " lowbatt={0:<2}".format(self.lowbatt))
        strn = strn + (" status=None" if self.status == None else " status={0:<2}".format(self.status))
        strn = strn + (" tamper=None" if self.tamper == None else " tamper={0:<2}".format(self.tamper))
        strn = strn + (" enrolled=None" if self.enrolled == None else " enrolled={0:<2}".format(self.enrolled))
        strn = strn + (" triggered=None" if self.triggered == None else " triggered={0:<2}".format(self.triggered))
        return strn

    def __eq__(self, other):
        if other is None:
            return False
        if self is None:
            return False
        return (
            self.id == other.id
            and self.dname == other.dname
            and self.stype == other.stype
            and self.sid == other.sid
            and self.model == other.model
            and self.ztype == other.ztype
            and self.zname == other.zname
            and self.zchime == other.zchime
            and self.partition == other.partition
            and self.bypass == other.bypass
            and self.lowbatt == other.lowbatt
            and self.status == other.status
            and self.tamper == other.tamper
            and self.ztypeName == other.ztypeName
            and self.ztamper == other.ztamper
            and self.ztrip == other.ztrip
            and self.enrolled == other.enrolled
            and self.triggered == other.triggered
            and self.triggertime == other.triggertime
        )

    def __ne__(self, other):
        return not self.__eq__(other)

    def getDeviceID(self):
        return self.id
        
    def isTriggered(self) -> bool:
        return self.triggered

    def isOpen(self) -> bool:
        return self.status

    def isEnrolled(self) -> bool:
        return self.enrolled

    def isBypass(self) -> bool:
        return self.bypass

    def isLowBattery(self) -> bool:
        return self.lowbatt

    def getDeviceName(self) -> str:
        return self.dname

    def getSensorModel(self) -> str:
        if self.model is not None:
            return self.model
        return "Unknown"    
    
    def getSensorType(self) -> PySensorType:
        return self.stype

    def getLastTriggerTime(self) -> datetime:
        return self.triggertime

    def getAttributes(self) -> dict:
        attr = {}

        attr["device name"] = self.dname

        if self.stype != PySensorType.UNKNOWN:
            attr["sensor type"] = str(self.stype)
        elif self.sid is not None:
            attr["sensor type"] = "Undefined " + str(self.sid)
        else:
            attr["sensor type"] = "Undefined"

        attr["zone type"] = self.ztype
        attr["zone name"] = self.zname
        attr["zone type name"] = self.ztypeName
        attr["zone chime"] = self.zchime
        attr["zone tripped"] = "Yes" if self.ztrip else "No"
        attr["zone tamper"] = "Yes" if self.ztamper else "No"
        #attr["device model"] = self.getSensorModel()
        attr["device tamper"] = "Yes" if self.tamper else "No"
        attr["zone open"] = "Yes" if self.status else "No"
        attr["visonic device"] = self.id

        # Not added
        #    self.partition = kwargs.get('partition', None)  # set   partition set (could be in more than one partition)
        return attr


class X10Device(PySwitchDevice):
    def __init__(self, **kwargs):
        self.enabled = kwargs.get("enabled", False)  # bool  enabled
        self.id = kwargs.get("id", None)  # int   device id
        self.name = kwargs.get("name", None)  # str   name
        self.type = kwargs.get("type", None)  # str   type
        self.location = kwargs.get("location", None)  # str   location
        self.state = False

    def __str__(self):
        strn = ""
        strn = strn + ("id=None" if self.id == None else "id={0:<2}".format(self.id))
        strn = strn + (" name=None" if self.name == None else " name={0:<4}".format(self.name))
        strn = strn + (" type=None" if self.type == None else " type={0:<8}".format(self.type))
        strn = strn + (" location=None" if self.location == None else " location={0:<14}".format(self.location))
        strn = strn + (" enabled=None" if self.enabled == None else " enabled={0:<2}".format(self.enabled))
        strn = strn + (" state=None" if self.state == None else " state={0:<2}".format(self.state))
        return strn

    def __eq__(self, other):
        if other is None:
            return False
        if self is None:
            return False
        return (
            self.id == other.id
            and self.enabled == other.enabled
            and self.name == other.name
            and self.type == other.type
            and self.location == other.location
        )

    def __ne__(self, other):
        return not self.__eq__(other)

    def getDeviceID(self):
        return self.id

    def isEnabled(self):
        return self.enabled
        
    def getName(self) -> str:
        return self.name
    
    def getType(self) -> str:
        return self.type

    def getLocation(self) -> str:
        return self.location

    def isOn(self) -> bool:
        return self.state
    

# This class handles the detailed low level interface to the panel.
#    It sends the messages
#    It builds and received messages from the raw byte stream and coordinates the acknowledges back to the panel
#    It checks CRC for received messages and creates CRC for sent messages
#    It coordinates the downloading of the EPROM (but doesn't decode the data here)
#    It manages the communication connection
class ProtocolBase(asyncio.Protocol):
    """Manage low level Visonic protocol."""

    log.debug("Initialising Protocol - Protocol Version {0}".format(PLUGIN_VERSION))

    def __init__(self, loop=None, panelConfig=None, packet_callback: Callable = None) -> None:
        """Initialize class."""
        if loop:
            self.loop = loop
            log.debug("Initialising Protocol - Using Home Assistant Loop")
        else:
            self.loop = asyncio.get_event_loop()
            log.debug("Initialising Protocol - Using Event Loop")

        # install the packet callback handler
        self.packet_callback = packet_callback

        # set the event callback handlers to None
        self.event_callback = None        
        self.new_sensor_callback = None
        self.new_switch_callback = None
        self.disconnect_callback = None
        self.panel_event_log_callback = None

        self.transport = None  # type: asyncio.Transport

        ########################################################################
        # Global Variables that define the overall panel status
        ########################################################################
        self.PanelMode = PyPanelMode.STARTING
        self.WatchdogTimeout = 0
        self.WatchdogTimeoutPastDay = 0
        self.DownloadTimeout = 0

        # A7 related data
        self.PanelLastEvent = "None"
        self.PanelLastEventData = {
            "Zone": 0,
            "Entity": None,
            "Tamper": False,
            "Siren": False,
            "Reset": False,
            "Time": "2020-01-01T00:00:00.0",
            "Count": 0,
            "Type": [],
            "Event": [],
            "Mode": [],
            "Name": [],
        }
        self.PanelAlarmStatus = "None"
        self.PanelTroubleStatus = "None"

        # A5 related data
        self.PanelStatusText = "Unknown"
        self.PanelStatusCode = PyPanelStatus.UNKNOWN
        self.PanelReady = False
        self.PanelAlertInMemory = False
        self.PanelTrouble = False
        self.PanelBypass = False
        self.PanelStatusChanged = False
        self.PanelAlarmEvent = False
        self.PanelArmed = False
        self.PanelStatus = {}

        # Define model type to be unknown
        self.PanelModel = "Unknown"
        self.ModelType = 0
        self.PanelStatus = {}
        self.PanelType = None

        # Loopback capability added. Connect Rx and Tx together without connecting to the panel
        self.loopbackTest = False
        self.loopbackCounter = 0

        # determine when MSG_ENROLL is sent to the panel
        self.doneAutoEnroll = False

        # Whether its a powermax or powermaster
        self.PowerMaster = None

        # Configured from the client INTERFACE
        #   These are the default values
        self.ForceStandardMode = False        # INTERFACE : Get user variable from HA to force standard mode or try for PowerLink
        self.ForceAutoEnroll = True           # INTERFACE : Force Auto Enroll when don't know panel type. Only set to true
        self.AutoSyncTime = True              # INTERFACE : sync time with the panel
        self.DownloadCode = '56 50'           # INTERFACE : Set the Download Code
        self.pmLang = 'EN'                    # INTERFACE : Get the plugin language from HA, either "EN", "FR" or "NL"
        self.MotionOffDelay = 120             # INTERFACE : Get the motion sensor off delay time (between subsequent triggers)
        self.SirenTriggerList = ["intruder"]  # INTERFACE : This is the trigger list that we can assume is making the siren sound
        self.BZero_Enable = False             # INTERFACE : B0 enable the processing of the experimental B0 message
        self.BZero_MinInterval = 30           # INTERFACE : B0 timing for the min interval between subsequent processed B0 messages
        self.BZero_MaxWaitTime = 5            # INTERFACE : B0 wait time to look for a change in the data in the B0 message

        # Now that the defaults have been set, update them from the panel config dictionary (that may not have all settings in)
        self.updateSettings(panelConfig)

        ########################################################################
        # Variables that are only used in handle_received_message function
        ########################################################################
        self.pmIncomingPduLen = 0             # The length of the incoming message
        self.pmCrcErrorCount = 0              # The CRC Error Count for Received Messages
        self.pmCurrentPDU = pmReceiveMsg_t[0] # The current receiving message type
        self.pmFlexibleLength = 0             # How many bytes less then the proper message size do we start checking for 0x0A and a valid CRC 

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

        # A queue of messages to send
        self.SendList = []

        self.myDownloadList = []

        # This is the time stamp of the last Send
        self.pmLastTransactionTime = self._getTimeFunction() - timedelta(
            seconds=1
        )  # take off 1 second so the first command goes through immediately

        self.pmFirstCRCErrorTime = self._getTimeFunction() - timedelta(
            seconds=1
        )  # take off 1 second so the first command goes through immediately

        self.giveupTrying = False

        self.expectedResponseTimeout = 0

        self.firstCmdSent = False
        ###################################################################
        # Variables that are used and modified throughout derived classes
        ###################################################################

        ############## Variables that are set in this class and only read in derived classes ############
        self.suspendAllOperations = False  # There has been a communication exception, when true do not send/receive(process) data

        ############## Variables that are set and read in this class and in derived classes ############

        # whether we are in powerlink state
        #    Set True when
        #         pmPowerlinkModePending must be True (see its dependencies)
        #         We receive a PowerLink Keep-Alive message from the panel
        self.pmPowerlinkMode = False

        # we have finished downloading the eprom and are trying to get in to powerlink state
        #    Set to True when
        #         we complete eprom download and
        #         receive a STOP from the panel (self.pmDownloadComplete is also set to True) and
        #             the last command from us was MSG_START
        #    Set to False when we achieve powerlink i.e. self.pmPowerlinkMode is set to True
        self.pmPowerlinkModePending = False

        # When we are downloading the EPROM settings and finished parsing them and setting up the system.
        #   There should be no user (from Home Assistant for example) interaction when self.pmDownloadMode is True
        self.pmDownloadMode = False
        self.triggeredDownload = False

        # Set when the panel details have been received i.e. a 3C message
        self.pmGotPanelDetails = False
        
        # Can the panel support the INIT Command
        self.pmInitSupportedByPanel = False

        # Set when we receive a STOP from the panel, indicating that the EPROM data has finished downloading
        self.pmDownloadComplete = False

        # Download block retry count        
        self.pmDownloadRetryCount = 0

        # Set when the EPROM has been downloaded and we have extracted a user pin code
        self.pmGotUserCode = False

        # When trying to connect in powerlink from the timer loop, this allows the receipt of a powerlink ack to trigger a MSG_RESTORE
        self.allowAckToTriggerRestore = False

        self.pmPhoneNr_t = {}
        self.pmEventLogDictionary = {}

        # We do not put these pin codes in to the panel status
        self.pmPincode_t = []  # allow maximum of 48 user pin codes

        # Store the sensor details
        self.pmSensorDev_t = {}

        # Store the X10 details
        self.pmX10Dev_t = {}

        # Save the EPROM data when downloaded
        self.pmRawSettings = {}

        # Save the sirens
        self.pmSirenDev_t = {}

    def updateSettings(self, newdata: dict):
        if newdata is not None:
            # log.debug("[updateSettings] Settings refreshed - Using panel config {0}".format(newdata))
            if PyConfiguration.ForceStandard in newdata:
                # Get user variable from HA to force standard mode or try for PowerLink
                self.ForceStandardMode = newdata[PyConfiguration.ForceStandard]
                log.debug("[Settings] Force Standard set to {0}".format(self.ForceStandardMode))
            if PyConfiguration.ForceAutoEnroll in newdata:
                # Force Auto Enroll when don't know panel type. Only set to true
                self.ForceAutoEnroll = newdata[PyConfiguration.ForceAutoEnroll]
                log.debug("[Settings] Force Auto Enroll set to {0}".format(self.ForceAutoEnroll))
            if PyConfiguration.AutoSyncTime in newdata:
                self.AutoSyncTime = newdata[PyConfiguration.AutoSyncTime]  # INTERFACE : sync time with the panel
                log.debug("[Settings] Force Auto Sync Time set to {0}".format(self.AutoSyncTime))
            if PyConfiguration.DownloadCode in newdata:
                tmpDLCode = newdata[PyConfiguration.DownloadCode]  # INTERFACE : Get the download code
                if len(tmpDLCode) == 4 and type(tmpDLCode) is str:
                    self.DownloadCode = tmpDLCode[0:2] + " " + tmpDLCode[2:4]
                    log.debug("[Settings] Download Code set to {0}".format(self.DownloadCode))
            if PyConfiguration.PluginLanguage in newdata:
                # Get the plugin language from HA, either "EN", "FR" or "NL"
                self.pmLang = newdata[PyConfiguration.PluginLanguage]
                log.debug("[Settings] Language set to {0}".format(self.pmLang))
            if PyConfiguration.MotionOffDelay in newdata:
                # Get the motion sensor off delay time (between subsequent triggers)
                self.MotionOffDelay = newdata[PyConfiguration.MotionOffDelay]
                log.debug("[Settings] Motion Off Delay set to {0}".format(self.MotionOffDelay))
            if PyConfiguration.SirenTriggerList in newdata:
                tmpList = newdata[PyConfiguration.SirenTriggerList]
                self.SirenTriggerList = [x.lower() for x in tmpList]
                log.debug("[Settings] Siren Trigger List set to {0}".format(self.SirenTriggerList))
            if PyConfiguration.B0_Enable in newdata:
                self.BZero_Enable = newdata[PyConfiguration.B0_Enable]
                log.debug("[Settings] B0 Enable set to {0}".format(self.BZero_Enable))
            if PyConfiguration.B0_Min_Interval_Time in newdata:
                self.BZero_MinInterval = newdata[PyConfiguration.B0_Min_Interval_Time]
                log.debug("[Settings] B0 Min Interval set to {0}".format(self.BZero_MinInterval))
            if PyConfiguration.B0_Max_Wait_Time in newdata:
                self.BZero_MaxWaitTime = newdata[PyConfiguration.B0_Max_Wait_Time]
                log.debug("[Settings] B0 Max Wait Time set to {0}".format(self.BZero_MaxWaitTime))

    # Convert byte array to a string of hex values
    def _toString(self, array_alpha: bytearray):
        return "".join("%02x " % b for b in array_alpha)

    # get the current date and time
    def _getTimeFunction(self) -> datetime:
        return datetime.now()

    # This is called from the loop handler when the connection to the transport is made
    def connection_made(self, transport):
        """Make the protocol connection to the Panel."""
        self.transport = transport
        log.debug("[Connection] Connected to local Protocol handler and Transport Layer")

        # get the value for Force standard mode (i.e. do not attempt to go to powerlink)
        self._initialiseConnection()

    # when the connection has problems then call the disconnect_callback when available,
    #     otherwise try to reinitialise the connection from here
    def _performDisconnect(self, exc=None):
        """Log when connection is closed, if needed call callback."""
        if self.suspendAllOperations:
            # log.debug('[Disconnection] Suspended. Sorry but all operations have been suspended, please recreate connection')
            return

        self.suspendAllOperations = True

        self.PanelMode = PyPanelMode.PROBLEM

        if exc is not None:
            # log.exception("ERROR Connection Lost : disconnected due to exception  <{0}>".format(exc))
            log.error("ERROR Connection Lost : disconnected due to exception {0}".format(exc))
        else:
            log.error("ERROR Connection Lost : disconnected because of close/abort.")

        self.pmPhoneNr_t = {}
        self.pmEventLogDictionary = {}

        # We do not put these pin codes in to the panel status
        self.pmPincode_t = []  # allow maximum of 48 user pin codes

        # Empty the sensor details
        self.pmSensorDev_t = {}

        # Empty the X10 details
        self.pmX10Dev_t = {}

        # Save the EPROM data when downloaded
        self.pmRawSettings = {}
        
        self.lastRecvOfPanelData = None

        # Save the sirens
        self.pmSirenDev_t = {}

        self.transport = None

        sleep(5.0)  # a bit of time for the watchdog timers and keep alive loops to self terminate
        if self.disconnect_callback:
            log.error("                        Calling Exception handler.")
            self.disconnect_callback(exc)
        else:
            log.error("                        No Exception handler to call, terminating Component......")

    # This is called by the asyncio parent when the connection is lost
    def connection_lost(self, exc):
        """Log when connection is closed, if needed call callback."""
        log.error("ERROR Connection Lost : disconnected because the Ethernet/USB connection was externally terminated.")
        self._performDisconnect(exc)

    def _sendResponseEvent(self, ev, dict={}):
        if self.event_callback is not None:
            self.event_callback(ev, dict)

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
            self._sendInitCommand()
            self._startDownload()
        else:
            self._gotoStandardMode()
        asyncio.create_task(self._keep_alive_and_watchdog_timer(), name="Watchdog Timer") #, loop=self.loop)

    def _sendInitCommand(self):  
        # This should re-initialise the panel, most of the time it works!
        # Clear the send list and empty the expected response list
        self._clearList()
        # Send Exit and Stop to the panel. This should quit download mode.
        self._sendCommand("MSG_EXIT")
        self._sendCommand("MSG_STOP")
        if self.pmInitSupportedByPanel:
            log.debug("[_sendInitCommand]   ************************************* Sending an INIT Command ************************************")
            self._sendCommand("MSG_INIT")
        # Wait for the Exit and Stop (and Init) to be sent. Make sure nothing is left to send.
        while not self.suspendAllOperations and len(self.SendList) > 0:
            log.debug("[ResetPanel]       Waiting")
            self._sendCommand(None)  # check send queue

    def _gotoStandardMode(self):
        if self.pmDownloadComplete and not self.ForceStandardMode and self.pmGotUserCode:
            log.debug("[Standard Mode] Entering Standard Plus Mode as we got the pin codes from the EPROM")
            self.PanelMode = PyPanelMode.STANDARD_PLUS
        else:
            log.debug("[Standard Mode] Entering Standard Mode")
            self.PanelMode = PyPanelMode.STANDARD
            self.ForceStandardMode = True
        self.giveupTrying = True
        self.pmPowerlinkModePending = False
        self.pmPowerlinkMode = False
        self._sendInitCommand()
        self._sendCommand("MSG_STATUS")

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

    # We can only use this function when the panel has sent a "installing powerlink" message i.e. AB 0A 00 01
    #   We need to clear the send queue and reset the send parameters to immediately send an MSG_ENROLL
    def _sendMsgENROLL(self, triggerdownload):
        """ Auto enroll the PowerMax/Master unit """
        if not self.doneAutoEnroll:
            self.doneAutoEnroll = True
            self._sendCommand("MSG_ENROLL", options=[4, bytearray.fromhex(self.DownloadCode)])
            if triggerdownload:
                self._startDownload()
        elif self.DownloadCode == "DF FD":
            log.warning("[_sendMsgENROLL] Warning: Trying to re enroll, already tried DFFD and still not successful")
        else:
            log.debug("[_sendMsgENROLL] Warning: Trying to re enroll but not triggering download")
            self.DownloadCode = "DF FD"  # Force the Download code to be something different and try again ?????
            self._sendCommand("MSG_ENROLL", options=[4, bytearray.fromhex(self.DownloadCode)])

    def _triggerEnroll(self, force):
        if force or (self.PanelType is not None and self.PanelType >= 3):  
            # Only attempt to auto enroll powerlink for newer panels. Older panels need the user to manually enroll, we should be in Standard Plus by now.
            log.debug("[Controller] Trigger Powerlink Attempt")
            # Allow the receipt of a powerlink ack to then send a MSG_RESTORE to the panel,
            #      this should kick it in to powerlink after we just enrolled
            self.allowAckToTriggerRestore = True
            # Send enroll to the panel to try powerlink
            # self._sendCommand("MSG_ENROLL", options=[4, bytearray.fromhex(self.DownloadCode)])
            self._sendMsgENROLL(False)  # Auto enroll, do not request download
        elif self.PanelType is not None and self.PanelType >= 1:
            # Powermax+ or Powermax Pro, attempt to just send a MSG_RESTORE to prompt the panel in to taking action if it is able to
            log.debug("[Controller] Trigger Powerlink Prompt attempt to a Powermax+ or Powermax Pro panel")
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
        
        download_counter = 0
        powerlink_counter = POWERLINK_RETRY_DELAY - 10  # set so first time it does it after 10 seconds
        downloadDuration = 0
        no_data_received_counter = 0
        settime_counter = 0
        
        while not self.suspendAllOperations:

            if self.loopbackTest:
                await asyncio.sleep(5.0)
                self._clearList()
                self._sendCommand("MSG_EXIT")
        
            else:
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

                if not self.giveupTrying and self.pmDownloadMode:
                    # First, during actual download keep resetting the watchdogs and timers to prevent any other comms to the panel, check for download timeout
                    # Disable watchdog and keep-alive during download (and keep flushing send queue)
                    self._reset_watchdog_timeout()
                    self._reset_keep_alive_messages()
                    # count in seconds that we've been in download mode
                    downloadDuration = downloadDuration + 1

                    if downloadDuration == 5 and not self.pmGotPanelDetails:
                        # Download has already been triggered so self.ForceStandardMode must be False and therefore no need to check it
                        # This is a check at 5 seconds in to see if the panel details have been retrieved.  If not then kick off the download request again.
                        log.debug("[Controller] Trigger Panel Download Attempt - Not yet received the panel details")
                        self.pmDownloadComplete = False
                        self.pmDownloadMode = False
                        self.triggeredDownload = False
                        self._startDownload()

                    elif downloadDuration > DOWNLOAD_TIMEOUT:
                        log.warning("[Controller] ********************** Download Timer has Expired, Download has taken too long *********************")
                        log.warning("[Controller] ************************************* Going to standard mode ***************************************")
                        # Stop download mode
                        self.pmDownloadMode = False
                        # goto standard mode
                        self._gotoStandardMode()
                        self.DownloadTimeout = self.DownloadTimeout + 1
                        self._sendResponseEvent(PyCondition.DOWNLOAD_TIMEOUT)  # download timer expired

                elif not self.giveupTrying and not self.pmDownloadComplete and not self.ForceStandardMode and not self.pmDownloadMode:
                    # Second, if not actually doing download but download is incomplete then try every DOWNLOAD_RETRY_DELAY seconds
                    self._reset_watchdog_timeout()
                    download_counter = download_counter + 1
                    log.debug("[Controller] download_counter is {0}".format(download_counter))
                    if download_counter >= DOWNLOAD_RETRY_DELAY:  #
                        download_counter = 0
                        # trigger a download
                        log.debug("[Controller] Trigger Panel Download Attempt")
                        self.triggeredDownload = False
                        self._startDownload()

                elif (
                    not self.giveupTrying
                    and self.pmPowerlinkModePending
                    and not self.ForceStandardMode
                    and self.pmDownloadComplete
                    and not self.pmPowerlinkMode
                ):
                    # Third, when download has completed successfully, and not ForceStandard from the user, then attempt to connect in powerlink
                    if self.PanelType is not None:  # By the time EPROM download is complete, this should be set but just check to make sure
                        # Attempt to enter powerlink mode
                        self._reset_watchdog_timeout()
                        powerlink_counter = powerlink_counter + 1
                        log.debug("[Controller] Powerlink Counter {0}".format(powerlink_counter))
                        if (powerlink_counter % POWERLINK_RETRY_DELAY) == 0:  # when the remainder is zero
                            self._triggerEnroll(False)
                        elif len(self.pmExpectedResponse) > 0 and self.expectedResponseTimeout >= RESPONSE_TIMEOUT:
                            log.debug("[Controller] ****************************** During Powerlink Attempts - Response Timer Expired ********************************")
                            self.pmExpectedResponse = []
                            self.expectedResponseTimeout = 0
                        elif powerlink_counter >= POWERLINK_TIMEOUT:
                            # give up on trying to get to powerlink and goto standard mode (could be either Standard Plus or Standard)
                            log.debug("[Controller] Giving up on Powerlink Attempts, going to one of the standard modes")
                            self._gotoStandardMode()

                elif self.watchdog_counter >= WATCHDOG_TIMEOUT:  #  the clock runs at 1 second
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
                    if not self.giveupTrying and watchdog_list[watchdog_pos] > 0:
                        log.debug("[Controller]               **************** Going to Standard (Plus) Mode and re-establish panel connection ***************")
                        self._sendResponseEvent(PyCondition.WATCHDOG_TIMEOUT_GIVINGUP)   # watchdog timer expired, going to standard (plus) mode
                        self._gotoStandardMode()     # This sets self.giveupTrying to True
                    else:
                        log.debug("[Controller]               ******************* Trigger Restore Status *******************")
                        self._sendResponseEvent(PyCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired, going to try again
                        self._triggerRestoreStatus() # Clear message buffers and send a Restore (if in Powerlink) or Status (not in Powerlink) to the Panel

                    # Overwrite the oldest entry and set it to 1 day in seconds. Keep the stats going in all modes for the statistics
                    #    Note that the asyncio 1 second sleep does not create an accurate time and this may be slightly more than 24 hours.
                    watchdog_list[watchdog_pos] = 60 * 60 * 24  # seconds in 1 day
                    log.debug("[Controller]               Watchdog counter array, current=" + str(watchdog_pos))
                    log.debug("[Controller]                       " + str(watchdog_list))

                elif len(self.pmExpectedResponse) > 0 and self.expectedResponseTimeout >= RESPONSE_TIMEOUT:
                    log.debug("[Controller] ****************************** Response Timer Expired ********************************")
                    self._triggerRestoreStatus()     # Clear message buffers and send a Restore (if in Powerlink) or Status (not in Powerlink) to the Panel

                # Is it time to send an I'm Alive message to the panel
                if len(self.SendList) == 0 and not self.pmDownloadMode and self.keep_alive_counter >= KEEP_ALIVE_PERIOD:  #
                    # Every KEEP_ALIVE_PERIOD seconds, unless watchdog has been reset
                    # log.debug("[Controller]   Send list is empty so sending I'm alive message or get status")
                    # reset counter
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
                            self._addMessageToSendList("MSG_BYPASSTAT")
                    elif not self.pmPowerlinkMode:
                        # When not in powerlink mode, send I'm Alive to the panel so it knows we're still here
                        self._sendCommand("MSG_ALIVE")
                else:
                    # Every 1.0 seconds, try to flush the send queue
                    self._sendCommand(None)  # check send queue

                # sleep, doesn't need to be highly accurate so just count each second
                await asyncio.sleep(1.0)

                if not self.suspendAllOperations:  ## To make sure as it could have changed in the 1 second sleep
                    if self.lastRecvOfPanelData is None:  ## has any data been received from the panel yet?
                        no_data_received_counter = no_data_received_counter + 1
                        if no_data_received_counter >= NO_RECEIVE_DATA_TIMEOUT:  ## lets assume approx 30 seconds
                            log.error(
                                "[Controller] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. no data has been received from the panel)"
                            )
                            self.suspendAllOperations = True
                            self.PanelMode = PyPanelMode.PROBLEM
                            self._sendResponseEvent(PyCondition.NO_DATA_FROM_PANEL)  # Plugin suspended itself
                    else:  # Data has been received from the panel but check when it was last received
                        # calc time difference between now and when data was last received
                        interval = self._getTimeFunction() - self.lastRecvOfPanelData
                        # log.debug("Checking last receive time {0}".format(interval))
                        if interval >= timedelta(seconds=LAST_RECEIVE_DATA_TIMEOUT):
                            log.error(
                                "[Controller] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. data has not been received from the panel in " + str(LAST_RECEIVE_DATA_TIMEOUT) + " seconds)"
                            )
                            self.suspendAllOperations = True
                            self.PanelMode = PyPanelMode.PROBLEM
                            self._sendResponseEvent(PyCondition.NO_DATA_FROM_PANEL)  # Plugin suspended itself

    # Process any received bytes (in data as a bytearray)
    def data_received(self, data):
        """Add incoming data to ReceiveData."""
        if self.suspendAllOperations:
            return
        if not self.firstCmdSent:
            log.debug("[data receiver] Ignoring garbage data: " + self._toString(data))
            return
        # log.debug('[data receiver] received data: %s', self._toString(data))
        self.lastRecvOfPanelData = self._getTimeFunction()
        for databyte in data:
            # process a single byte at a time
            self._handle_received_byte(databyte)

    # Process one received byte at a time to build up the received messages
    #       self.pmIncomingPduLen is only used in this function
    #       self.pmCrcErrorCount is only used in this function
    #       self.pmCurrentPDU is only used in this function
    #       self._resetMessageData is only used in this function
    #       self._processCRCFailure is only used in this function
    def _handle_received_byte(self, data):
        """Process a single byte as incoming data."""
        if self.suspendAllOperations:
            return
        # PDU = Protocol Description Unit

        pdu_len = len(self.ReceiveData)                                # Length of the received data so far

        # If we're receiving a variable length message and we're at the position in the message where we get the variable part
        if self.pmCurrentPDU.isvariablelength and pdu_len == self.pmCurrentPDU.varlenbytepos:
            # Determine total length of the message by getting the variable part int(data) and adding it to the fixed length part
            self.pmIncomingPduLen = self.pmCurrentPDU.length + int(data)
            self.pmFlexibleLength = self.pmCurrentPDU.flexiblelength
            log.debug("[data receiver] Variable length Message Being Received  Message Type {0}     pmIncomingPduLen {1}".format(hex(self.ReceiveData[1]).upper(), self.pmIncomingPduLen))

        # If we were expecting a message of a particular length (i.e. self.pmIncomingPduLen > 0) and what we have is already greater then that length then dump the message and resynchronise.
        if 0 < self.pmIncomingPduLen <= pdu_len:                       # waiting for pmIncomingPduLen bytes but got more and haven't been able to validate a PDU
            log.debug("[data receiver] PDU Too Large: Dumping current buffer {0}    The next byte is {1}".format(self._toString(self.ReceiveData), hex(data).upper()))
            pdu_len = 0                                                # Reset the incoming data to 0 length
            self._resetMessageData()

        # If this is the start of a new message, then check to ensure it is a 0x0D (message preamble)
        if pdu_len == 0:
            self._resetMessageData()
            if data == 0x0D:  # preamble
                self.ReceiveData.append(data)
                #log.debug("[data receiver] Starting PDU " + self._toString(self.ReceiveData))
            # else we're trying to resync and walking through the bytes waiting for an 0x0D preamble byte
        elif pdu_len == 1:
            #log.debug("[data receiver] Received message Type %d", data)
            if data != 0x00 and data in pmReceiveMsg_t:                # Is it a message type that we know about
                self.pmCurrentPDU = pmReceiveMsg_t[data]               # set to current message type parameter settings for length, does it need an ack etc
                self.pmIncomingPduLen = self.pmCurrentPDU.length       # for variable length messages this is the fixed length and will work with this algorithm until updated.
                self.ReceiveData.append(data)                          # Add on the message type to the buffer
                #log.debug("[data receiver] Building PDU: It's a message {0}; pmIncomingPduLen = {1}   variable = {2}".format(hex(data).upper(), self.pmIncomingPduLen, self.pmCurrentPDU.isvariablelength))
            elif data == 0x00 or data == 0xFD:                         # Special case for pocket and PowerMaster 10
                log.debug("[data receiver] Received message type {0} so not processing it".format(hex(data).upper()))
                self._resetMessageData()
            else:
                # build an unknown PDU. As the length is not known, leave self.pmIncomingPduLen set to 0 so we just look for 0x0A as the end of the PDU
                self.pmCurrentPDU = pmReceiveMsg_t[0]                  # Set to unknown message structure to get settings, varlenbytepos is -1
                self.pmIncomingPduLen = 0                              # self.pmIncomingPduLen should already be set to 0 but just to make sure !!!
                log.warning("[data receiver] Warning : Construction of incoming packet unknown - Message Type {0}".format(hex(data).upper()))
                self.ReceiveData.append(data)                          # Add on the message type to the buffer

        elif self.pmFlexibleLength > 0 and data == 0x0A and pdu_len + 1 < self.pmIncomingPduLen and (self.pmIncomingPduLen - pdu_len) < self.pmFlexibleLength:
            # Only do this when:
            #       Looking for "flexible" messages 
            #              At the time of writing this, only the 0x3F EPROM Download PDU does this with some PowerMaster panels
            #       Have got the 0x0A message terminator
            #       We have not yet received all bytes we expect to get
            #       We are within 5 bytes of the expected message length, self.pmIncomingPduLen - pdu_len is the old length as we already have another byte in data
            #              At the time of writing this, the 0x3F was always only up to 3 bytes short of the expected length and it would pass the CRC checks
            # Do not do this when (pdu_len + 1 == self.pmIncomingPduLen) i.e. the correct length
            # There is possibly a fault with some panels as they sometimes do not send the full EPROM data.
            #    - Rather than making it panel specific I decided to make this a generic capability
            self.ReceiveData.append(data)  # add byte to the message buffer
            if self._validatePDU(self.ReceiveData):  # if the message passes CRC checks then process it
                # We've got a validated message
                log.debug("[data receiver] Validated PDU: Got Validated PDU type 0x%02x   full data %s", int(self.ReceiveData[1]), self._toString(self.ReceiveData))
                self._processReceivedMessage(ackneeded=self.pmCurrentPDU.ackneeded, data=self.ReceiveData)
                self._resetMessageData()

        elif (self.pmIncomingPduLen == 0 and data == 0x0A) or (pdu_len + 1 == self.pmIncomingPduLen): # postamble (the +1 is to include the current data byte)
            # (waiting for 0x0A and got it) OR (actual length == calculated expected length)
            self.ReceiveData.append(data)  # add byte to the message buffer
            # log.debug("[data receiver] Building PDU: Checking it " + self._toString(self.ReceiveData))
            msgType = self.ReceiveData[1]
            if self._validatePDU(self.ReceiveData):
                # We've got a validated message
                # log.debug("[data receiver] Building PDU: Got Validated PDU type 0x%02x   full data %s", int(msgType), self._toString(self.ReceiveData))
                if self.pmCurrentPDU.varlenbytepos < 0:  # is it an unknown message i.e. varlenbytepos is -1
                    log.warning("[data receiver] Received Unknown PDU {0}".format(hex(msgType)))
                    self._sendAck()  # assume we need to send an ack for an unknown message
                else:  # Process the received known message
                    self._processReceivedMessage(ackneeded=self.pmCurrentPDU.ackneeded, data=self.ReceiveData)
                self._resetMessageData()
            else:
                # CRC check failed
                a = self._calculateCRC(self.ReceiveData[1:-2])[0]  # this is just used to output to the log file
                if len(self.ReceiveData) > 0xB0:
                    # If the length exceeds the max PDU size from the panel then stop and resync
                    log.warning("[data receiver] PDU with CRC error Message = {0}   checksum calcs {1}".format(self._toString(self.ReceiveData), hex(a).upper()))
                    self._processCRCFailure()
                    self._resetMessageData()
                elif self.pmIncomingPduLen == 0:
                    if msgType in pmReceiveMsg_t:
                        # A known message with zero length and an incorrect checksum. Reset the message data and resync
                        log.warning("[data receiver] Warning : Construction of incoming packet validation failed - Message = {0}  checksum calcs {1}".format(self._toString(self.ReceiveData), hex(a).upper()))
                        
                        # Send an ack even though the its an invalid packet to prevent the panel getting confused
                        if self.pmCurrentPDU.ackneeded:
                            # log.debug("[data receiver] Sending an ack as needed by last panel status message " + hex(msgType).upper())
                            self._sendAck(data=self.ReceiveData)
                        
                        # Dump the message and carry on
                        self._processCRCFailure()
                        self._resetMessageData()
                    else:  # if msgType != 0xF1:        # ignore CRC errors on F1 message
                        # When self.pmIncomingPduLen == 0 then the message is unknown, the length is not known and we're waiting for an 0x0A where the checksum is correct, so carry on
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

        elif pdu_len <= 0xC0:
            # log.debug("[data receiver] Current PDU " + self._toString(self.ReceiveData) + "    adding " + str(hex(data).upper()))
            self.ReceiveData.append(data)
        else:
            log.debug("[data receiver] Dumping Current PDU " + self._toString(self.ReceiveData))
            self._resetMessageData()
        # log.debug("[data receiver] Building PDU " + self._toString(self.ReceiveData))

    def _resetMessageData(self):
        # clear our buffer again so we can receive a new packet.
        self.ReceiveData = bytearray(b"")  # messages should never be longer than 0xC0
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
                interval = self._getTimeFunction() - self.pmFirstCRCErrorTime
                if interval <= timedelta(seconds=CRC_ERROR_PERIOD):
                    self._performDisconnect("CRC errors")
                self.pmFirstCRCErrorTime = self._getTimeFunction()

    def _processReceivedMessage(self, ackneeded, data):
        # Unknown Message has been received
        msgType = data[1]
        # log.debug("[data receiver] *** Received validated message " + hex(msgType).upper() + "   data " + self._toString(data))
        # log.debug("[data receiver] *** Received validated message " + hex(msgType).upper() + " ***")
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
                log.debug("[data receiver] msgType {0} got it so removed from list, list is now {1}".format(hex(msgType).upper(), [hex(no).upper() for no in self.pmExpectedResponse]))
            #else:
            #    log.debug("[data receiver] msgType not in self.pmExpectedResponse   Waiting for next PDU :  expected {0}   got {1}".format([hex(no).upper() for no in self.pmExpectedResponse], hex(msgType).upper()))

        if len(self.pmExpectedResponse) == 0:
            if tmplength > 0:
                log.debug("[data receiver] msgType {0} resetting expected response counter, it got up to {1}".format(hex(msgType).upper(), self.expectedResponseTimeout))
            self.expectedResponseTimeout = 0

        # Handle the message
        if self.packet_callback is not None:
            self.packet_callback(data)

    # Send an achnowledge back to the panel
    def _sendAck(self, data=bytearray(b"")):
        """ Send ACK if packet is valid """

        #ispm = len(data) > 3 and (data[1] >= 0xA5 or (data[1] < 0x10 and data[-2] == 0x43))
        ispm = len(data) > 3 and (data[1] == 0xAB or (data[1] < 0x10 and data[-2] == 0x43))

        # ---------------------- debug only start ----------------
        #lastType = 0
        #if len(data) > 2:
        #    lastType = data[1]
        #log.debug("[Sending ack] PowerlinkMode={0}    Is PM Ack Reqd={1}    This is an Ack for message={2}".format(self.pmPowerlinkMode, ispm, hex(lastType).upper()))
        #---------------------- debug only end ------------------

        # There are 2 types of acknowledge that we can send to the panel
        #    Normal    : For a normal message
        #    Powerlink : For when we are in powerlink mode
        if not self.ForceStandardMode and ispm:
            message = pmSendMsg["MSG_ACKLONG"]
        else:
            message = pmSendMsg["MSG_ACK"]
        assert message is not None
        e = VisonicListEntry(command=message, options=None)
        t = self.loop.create_task(self._sendPdu(e), name="Send Acknowledge") #loop=self.loop)
        asyncio.gather(t)

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
            log.debug("[_validatePDU] Validated a Packet with a checksum that is 1 more than the actual checksum!!!! {0} and {1}".format(packet[-2:-1][0], self._calculateCRC(packet[1:-2])[0]))
            return True

        if packet[-2:-1][0] == self._calculateCRC(packet[1:-2])[0] - 1:
            log.debug("[_validatePDU] Validated a Packet with a checksum that is 1 less than the actual checksum!!!! {0} and {1}".format(packet[-2:-1][0], self._calculateCRC(packet[1:-2])[0]))
            return True

        # Check the CRC
        if packet[-2:-1] == self._calculateCRC(packet[1:-2]):
            # log.debug("[_validatePDU] VALID PACKET!")
            return True

        log.debug("[_validatePDU] Not valid packet, CRC failed, may be ongoing and not final 0A")
        return False

    # calculate the checksum for sending and receiving messages
    def _calculateCRC(self, msg: bytearray):
        """ Calculate CRC Checksum """
        # log.debug("[_calculateCRC] Calculating for: %s", self._toString(msg))
        # Calculate the checksum
        checksum = 0
        for char in msg[0 : len(msg)]:
            checksum += char
        checksum = 0xFF - (checksum % 0xFF)
        if checksum == 0xFF:
            checksum = 0x00
        #            log.debug("[_calculateCRC] Checksum was 0xFF, forsing to 0x00")
        # log.debug("[_calculateCRC] Calculating for: %s     calculated CRC is: %s", self._toString(msg), self._toString(bytearray([checksum])))
        return bytearray([checksum])

    # Function to send all PDU messages to the panel, using a mutex lock to combine acknowledges and other message sends
    async def _sendPdu(self, instruction: VisonicListEntry):
        """Encode and put packet string onto write buffer."""

        if self.suspendAllOperations:
            log.debug("[sendPdu] Suspended all operations, not sending PDU")
            return

        if instruction is None:
            log.error("[sendPdu] Attempt to send a command that is empty")
            return

        if self.sendlock is None:
            self.sendlock = asyncio.Lock()

        async with self.sendlock:
            # Send a command to the panel
            command = instruction.command
            data = command.data
            # push in the options in to the appropriate places in the message. Examples are the pin or the specific command
            if instruction.options != None:
                # the length of instruction.options has to be an even number
                # it is a list of couples:  bitoffset , bytearray to insert
                op = int(len(instruction.options) / 2)
                # log.debug("[sendPdu] Options {0} {1}".format(instruction.options, op))
                for o in range(0, op):
                    s = instruction.options[o * 2]  # bit offset as an integer
                    a = instruction.options[o * 2 + 1]  # the bytearray to insert
                    if isinstance(a, int):
                        # log.debug("[sendPdu] Options {0} {1} {2} {3}".format(type(s), type(a), s, a))
                        data[s] = a
                    else:
                        # log.debug("[sendPdu] Options {0} {1} {2} {3} {4}".format(type(s), type(a), s, a, len(a)))
                        for i in range(0, len(a)):
                            data[s + i] = a[i]
                            # log.debug("[sendPdu]        Inserting at {0}".format(s+i))

            # log.debug('[sendPdu] input data: %s', self._toString(packet))
            # First add header (0x0D), then the packet, then crc and footer (0x0A)
            sData = b"\x0D"
            sData += data
            sData += self._calculateCRC(data)
            sData += b"\x0A"

            interval = self._getTimeFunction() - self.pmLastTransactionTime
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

            if command.download:
                self.pmDownloadMode = True
                self.triggeredDownload = False
                log.debug("[sendPdu] Setting Download Mode to true")

            if sData[1] != ACK_MESSAGE:  # the message is not an acknowledge back to the panel
                self.pmLastSentMessage = instruction

            self.pmLastTransactionTime = self._getTimeFunction()
            
            if command.debugprint:
                log.debug("[sendPdu] Sending Command ({0})    raw data {1}   waiting for message response {2}".format(command.msg, self._toString(sData), [hex(no).upper() for no in self.pmExpectedResponse]))
            else:
                # Do not log the full raw data as it may contain the user code
                log.debug("[sendPdu] Sending Command ({0})    waiting for message response {1}".format(command.msg, [hex(no).upper() for no in self.pmExpectedResponse]))
            
            if command.waittime > 0.0:
                log.debug("[sendPdu]          Command has a wait time after transmission {0}".format(command.waittime))
                await asyncio.sleep(command.waittime)


    def _addMessageToSendList(self, message_type, options=[]):
        if message_type is not None:
            message = pmSendMsg[message_type]
            assert message is not None
            #options = kwargs.get("options", None)
            e = VisonicListEntry(command=message, options=options)
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
            t = self.loop.create_task(self._sendCommandAsync(message_type, options), name="Send Command to Panel") #, loop=self.loop)
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
            interval = self._getTimeFunction() - self.pmLastTransactionTime
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
                    # self.SendList = []
                    # self.pmExpectedResponse = []
                    self.pmLastSentMessage.triedResendingMessage = True
                    await self._sendPdu(self.pmLastSentMessage)
                else:
                    # tried resending once, no point in trying again so reset settings, start from scratch
                    log.debug("[_sendCommand] Tried Re-Sending last message but didn't work. Assume a powerlink timeout state and resync")
                    # self._clearList()
                    # self.pmExpectedResponse = []
                    self._triggerRestoreStatus()
            elif len(self.SendList) > 0 and len(self.pmExpectedResponse) == 0:  # we are ready to send
                # pop the oldest item from the list, this could be the only item.
                instruction = self.SendList.pop(0)

                if len(instruction.response) > 0:
                    log.debug("[sendPdu] Resetting expected response counter, it got to {0}   Response list length before {1}  after {2}".format(self.expectedResponseTimeout, len(self.pmExpectedResponse), len(self.pmExpectedResponse) + len(instruction.response)))
                    self.expectedResponseTimeout = 0
                    # update the expected response list straight away (without having to wait for it to be actually sent) to make sure protocol is followed
                    self.pmExpectedResponse.extend(instruction.response)  # if an ack is needed it will already be in this list

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
            self._sendCommand("MSG_DOWNLOAD", options=[3, bytearray.fromhex(self.DownloadCode)])  #
            self.PanelMode = PyPanelMode.DOWNLOAD
            self.triggeredDownload = True
        elif self.pmDownloadComplete:
            log.debug("[StartDownload] Download has already completed (so not doing anything)")
        else:
            log.debug("[StartDownload] Already in Download Mode (so not doing anything)")


    def _populateEPROMDownload(self, isPowerMaster):
        """ Populate the EPROM Download List """

        # Empty list and start at the beginning
        self.myDownloadList = []
        
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block000"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block001"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block010"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block011"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block020"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block021"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block030"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block031"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block040"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block041"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block090"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block091"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block0A0"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block0A1"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block0B0"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block0B1"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block190"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block191"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block1A0"])
        self.myDownloadList.append(pmBlockDownload_t["MSG_DL_Block1A1"])

        if isPowerMaster:
            self.myDownloadList.append(pmBlockDownload_t["MSG_DL_BlockB60"])
            self.myDownloadList.append(pmBlockDownload_t["MSG_DL_BlockB61"])
            self.myDownloadList.append(pmBlockDownload_t["MSG_DL_BlockB70"])
            self.myDownloadList.append(pmBlockDownload_t["MSG_DL_BlockB71"])
            self.myDownloadList.append(pmBlockDownload_t["MSG_DL_BlockB80"])
            self.myDownloadList.append(pmBlockDownload_t["MSG_DL_BlockB81"])
            self.myDownloadList.append(pmBlockDownload_t["MSG_DL_BlockB90"])
            self.myDownloadList.append(pmBlockDownload_t["MSG_DL_BlockB91"])
            self.myDownloadList.append(pmBlockDownload_t["MSG_DL_BlockBA0"])
            self.myDownloadList.append(pmBlockDownload_t["MSG_DL_BlockBA1"])


    # Attempt to enroll with the panel in the same was as a powerlink module would inside the panel
    def _readPanelSettings(self, isPowerMaster):
        """ Attempt to Enroll as a Powerlink """
        log.debug("[Panel Settings] Uploading panel settings")
        
        # Populate the full list of EPROM blocks
        self._populateEPROMDownload(isPowerMaster)

        # Send the first EPROM block to the panel to retrieve
        self._sendCommand("MSG_DL", options=[1, self.myDownloadList.pop(0)])  # Read the names of the zones


# This class performs transactions based on messages (ProtocolBase is the raw data)
class PacketHandling(ProtocolBase):
    """Handle decoding of Visonic packets."""

    def __init__(self, *args, DataDict={}, **kwargs) -> None:
        """ Perform transactions based on messages (and not bytes) """
        super().__init__(*args, packet_callback=self._processReceivedPacket, **kwargs)

        self.eventCount = 0

        secdelay = DOWNLOAD_RETRY_DELAY + 100
        self.lastSendOfDownloadEprom = self._getTimeFunction() - timedelta(seconds=secdelay)  # take off X seconds so the first command goes through immediately

        # Variables to manage the PowerMAster B0 message and the triggering of Motion
        self.lastRecvOfMasterMotionData = self._getTimeFunction() - timedelta(seconds=secdelay)  # take off X seconds so the first command goes through immediately
        self.firstRecvOfMasterMotionData = self._getTimeFunction() - timedelta(seconds=secdelay)  # take off X seconds so the first command goes through immediately
        self.zoneNumberMasterMotion = 0
        self.zoneDataMasterMotion = bytearray(b"")

        self.pmSirenActive = None

        # These are used in the A5 message to reduce processing but mainly to reduce the amount of callbacks in to HA when nothing changes
        self.lowbatt_old = -1
        self.tamper_old = -1
        self.enrolled_old = 0  # means nothing enrolled
        self.status_old = -1
        self.bypass_old = -1
        self.zonealarm_old = -1
        self.zonetamper_old = -1

        self.pmBypassOff = False         # Do we allow the user to bypass the sensors, this is read from the EPROM data
        self.pmPanicAlarmSilent = False  # Is Panic Alarm set to silent panic set in the panel. This is read from the EPROM data

        self.lastPacket = None
        self.lastPacketCounter = 0
        self.sensorsCreated = False  # Have the sensors benn created. Either from an A5 message or the EPROM data

        asyncio.create_task(self._resetTriggeredStateTimer(), name="Turn Sensor Off After Timeout") #, loop=self.loop)

    # For the sensors that have been triggered, turn them off after self.MotionOffDelay seconds
    async def _resetTriggeredStateTimer(self):
        """ reset triggered state"""
        while not self.suspendAllOperations:
            # cycle through the sensors and set the triggered value back to False after the timeout duration
            td = timedelta(seconds=self.MotionOffDelay)
            pushChange = False
            for key in self.pmSensorDev_t:
                if self.pmSensorDev_t[key].triggered:
                    interval = self._getTimeFunction() - self.pmSensorDev_t[key].triggertime
                    # at least self.MotionOffDelay seconds as it also depends on the frequency the panel sends messages
                    if interval > td:
                        self.pmSensorDev_t[key].triggered = False
                        pushChange = True
                        #self.pmSensorDev_t[key].pushChange()
            if pushChange:      
                self._sendResponseEvent(PyCondition.PUSH_CHANGE)  # 0 means push through an HA Frontend change, do not create an HA Event
            # check every 1 second
            await asyncio.sleep(1.0)  # must be less than 5 seconds for self.suspendAllOperations:

    # _writeEPROMSettings: add a certain setting to the settings table
    #  So, to explain
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
                    log.debug("[Write Settings] The EPROM settings is incorrect for page {0}".format(page + i))
                # else:
                #    log.debug("[Write Settings] WHOOOPEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE")

            settings_len = len(sett[i])
            if i == 1:
                index = 0
            #log.debug("[Write Settings]         Writing settings page {0}  index {1}    length {2}".format(page+i, index, settings_len))
            self.pmRawSettings[page + i] = self.pmRawSettings[page + i][0:index] + sett[i] + self.pmRawSettings[page + i][index + settings_len :]
            if len(self.pmRawSettings[page + i]) != 256:
                log.debug("[Write Settings] OOOOOOOOOOOOOOOOOOOO len = {0}".format(len(self.pmRawSettings[page + i])))
            # else:
            #    log.debug("[Write Settings] Page {0} is now {1}".format(page+i, self._toString(self.pmRawSettings[page + i])))

    # _readEPROMSettingsPageIndex
    # This function retrieves the downloaded status and EPROM data
    def _readEPROMSettingsPageIndex(self, page, index, settings_len):
        retlen = settings_len
        retval = bytearray()
        if self.pmDownloadComplete:
            # log.debug("[Read Settings]    Entering Function  page {0}   index {1}    length {2}".format(page, index, settings_len))
            while page in self.pmRawSettings and retlen > 0:
                rawset = self.pmRawSettings[page][index : index + retlen]
                retval = retval + rawset
                page = page + 1
                retlen = retlen - len(rawset)
                index = 0
            if settings_len == len(retval):
                # log.debug("[Read Settings]       Length " + str(settings_len) + " returning (just the 1st value) " + self._toString(retval[:1]))
                return retval
        log.debug("[Read Settings]     Sorry but you havent downloaded that part of the EPROM data     page={0} index={1} length={2}".format(hex(page), hex(index), settings_len))
        if not self.pmDownloadMode:
            self.pmDownloadComplete = False
            # prevent any more retrieval of the EPROM settings and put us back to Standard Mode
            self._delayDownload()
            # try to download panel EPROM again
            self._startDownload()
        # return a bytearray filled with 0xFF values
        retval = bytearray()
        for dummy in range(0, settings_len):
            retval.append(255)
        return retval

    # this can be called from an entry in pmDownloadItem_t such as
    #       for example "MSG_DL_PANELFW"      : bytearray.fromhex('00 04 20 00'),
    #            this defines the index, page and 2 bytes for length
    #            in this example page 4   index 0   length 32
    def _readEPROMSettings(self, item):
        return self._readEPROMSettingsPageIndex(item[1], item[0], item[2] + (0x100 * item[3]))

    # This function was going to save the settings (including EPROM) to a file
    def _dumpEPROMSettings(self):
        log.debug("Dumping EPROM Settings")
        for p in range(0, 0x100):  ## assume page can go from 0 to 255
            if p in self.pmRawSettings:
                for j in range(0, 0x100, 0x10):  ## assume that each page can be 256 bytes long, step by 16 bytes
                    # do not display the rows with pin numbers
                    # if not (( p == 1 and j == 240 ) or (p == 2 and j == 0) or (p == 10 and j >= 140)):
                    if (p != 1 or j != 240) and (p != 2 or j != 0) and (p != 10 or j <= 140):
                        if j <= len(self.pmRawSettings[p]):
                            s = self._toString(self.pmRawSettings[p][j : j + 0x10])
                            log.debug("{0:3}:{1:3}  {2}".format(p, j, s))

    def _calcBoolFromIntMask(self, val, mask):
        return True if val & mask != 0 else False

    # SettingsCommand = collections.namedtuple('SettingsCommand', 'count type size poff psize pstep pbitoff name values')
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

            if val.type == "BYTE":
                v = self._readEPROMSettingsPageIndex(page, pos, val.count)
                if val.psize == 8:
                    myvalue = str(v[0])
                else:
                    mask = (1 << val.psize) - 1
                    offset = val.pbitoff | 0
                    myvalue = str((v[0] >> offset) & mask)
            elif val.type == "PHONE":
                for j in range(0, math.floor(val.psize / 8)):
                    nr = self._readEPROMSettingsPageIndex(page, pos + j, 1)
                    if nr[0] != 0xFF:
                        myvalue = myvalue + "".join("%02x" % b for b in nr)
            elif val.type == "TIME":
                t = self._readEPROMSettingsPageIndex(page, pos, 2)
                myvalue = "".join("%02d:" % b for b in t)[:-1]  # miss the last character off, which will be a colon :
            elif val.type == "CODE" or val.type == "ACCOUNT":
                nr = self._readEPROMSettingsPageIndex(page, pos, math.floor(val.psize / 8))
                myvalue = "".join("%02x" % b for b in nr).upper()
                myvalue = myvalue.replace("FF", ".")
                # if val.type == "CODE" and val.size == 16:
                #    myvalue = pmEncryptPIN(val);
                #    myvalue = [ this.rawSettings[page][pos], this.rawSettings[page][pos + 1] ];
            elif val.type == "STRING":
                for j in range(0, math.floor(val.psize / 8)):
                    nr = self._readEPROMSettingsPageIndex(page, pos + j, 1)
                    if nr[0] != 0xFF:
                        myvalue = myvalue + chr(nr[0])
            else:
                myvalue = "Not Set"

            if len(val.values) > 0 and myvalue in val.values:
                retval.append(val.values[str(myvalue)])
            else:
                retval.append(myvalue)

        return retval

    def _lookupEpromSingle(self, key):
        v = self._lookupEprom(DecodePanelSettings[key])
        if len(v) >= 1:
            return v[0]
        return ""

    # _processEPROMSettings
    #    Decode the EPROM and the various settings to determine
    #       The general state of the panel
    #       The zones and the sensors
    #       The X10 devices
    #       The phone numbers
    #       The user pin codes
    def _processEPROMSettings(self):
        """Process Settings from the downloaded EPROM data from the panel"""
        log.debug("[Process Settings] Process Settings from EPROM")

        # Process settings

        # List of door/window sensors
        doorZoneStr = ""
        # List of motion sensors
        motionZoneStr = ""
        # List of smoke sensors
        smokeZoneStr = ""
        # List of other sensors
        otherZoneStr = ""
        deviceStr = ""
        pmPanelTypeNr = None

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Panel type and serial number
        #     This kind of checks whether the EPROM settings have been downloaded OK
        pmPanelTypeNrStr = self._lookupEpromSingle("panelSerialCode")
        if pmPanelTypeNrStr is not None and len(pmPanelTypeNrStr) > 0:
            pmPanelTypeNr = int(pmPanelTypeNrStr)
            self.PanelModel = pmPanelType_t[pmPanelTypeNr] if pmPanelTypeNr in pmPanelType_t else "UNKNOWN"   # INTERFACE : PanelType set to model
            #log.debug("[Process Settings] EPROM Data")
            #self._dumpEPROMSettings()
            log.debug("[Process Settings] pmPanelTypeNr {0} ({1})    model {2}".format(pmPanelTypeNr, self.PanelType, self.PanelModel))
            if self.PanelType is None:
                self.PanelType = pmPanelTypeNr
                self.PowerMaster = self.PanelType >= 7
        else:
            log.error("[Process Settings] Lookup of panel type string and model from the EPROM failed, assuming EPROM download failed")
            # self._dumpEPROMSettings()

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Need the panel type to be valid so we can decode some of the remaining downloaded data correctly
        if self.PanelType is not None and self.PanelType in pmPanelType_t:

            if self.pmDownloadComplete:
                log.debug("[Process Settings] Processing settings information")

                # log.debug("[Process Settings] Panel Type Number " + str(pmPanelTypeNr) + "    serial string " + self._toString(panelSerialType))
                zoneCnt = pmPanelConfig_t["CFG_WIRELESS"][pmPanelTypeNr] + pmPanelConfig_t["CFG_WIRED"][pmPanelTypeNr]
                dummy_customCnt = pmPanelConfig_t["CFG_ZONECUSTOM"][pmPanelTypeNr]
                userCnt = pmPanelConfig_t["CFG_USERCODES"][pmPanelTypeNr]
                partitionCnt = pmPanelConfig_t["CFG_PARTITIONS"][pmPanelTypeNr]
                sirenCnt = pmPanelConfig_t["CFG_SIRENS"][pmPanelTypeNr]
                keypad1wCnt = pmPanelConfig_t["CFG_1WKEYPADS"][pmPanelTypeNr]
                keypad2wCnt = pmPanelConfig_t["CFG_2WKEYPADS"][pmPanelTypeNr]
                
                self.pmPincode_t = [bytearray.fromhex("00 00")] * userCnt  # allow maximum of userCnt user pin codes

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
                for key in DecodePanelSettings:
                    val = DecodePanelSettings[key]
                    if val.show:
                        result = self._lookupEprom(val)
                        if result is not None:
                            if type(DecodePanelSettings[key].name) is str:
                                # log.debug( "[Process Settings]      {0:<18}  {1:<40}  {2}".format(key, DecodePanelSettings[key].name, result[0]))
                                if len(result[0]) > 0:
                                    self.PanelStatus[DecodePanelSettings[key].name] = result[0]
                            else:
                                # log.debug( "[Process Settings]      {0:<18}  {1}  {2}".format(key, DecodePanelSettings[key].name, result))
                                for i in range(0, len(DecodePanelSettings[key].name)):
                                    if len(result[i]) > 0:
                                        self.PanelStatus[DecodePanelSettings[key].name[i]] = result[i]

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process alarm settings
                #log.debug("panic {0}   bypass {1}".format(self._lookupEpromSingle("panicAlarm"), self._lookupEpromSingle("bypass") ))
                self.pmPanicAlarmSilent = self._lookupEpromSingle("panicAlarm") == "Silent Panic"    # special
                self.pmBypassOff = self._lookupEpromSingle("bypass") == "No Bypass"                  # special   '2':"Manual Bypass", '0':"No Bypass", '1':"Force Arm"}

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process user pin codes
                if self.PowerMaster:
                    setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_PINCODES"])
                else:
                    setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_PINCODES"])
                # DON'T SAVE THE USER CODES TO THE LOG
                # log.debug("[Process Settings] User Codes:")
                for i in range(0, userCnt):
                    code = setting[2 * i : 2 * i + 2]
                    self.pmPincode_t[i] = code
                    if i == 0:
                        self.pmGotUserCode = True
                    # log.debug("[Process Settings]      User {0} has code {1}".format(i, self._toString(code)))

                # if not self.PowerMaster:
                #    setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_INSTPIN"])
                #    log.debug("[Process Settings]      Installer Code {0}".format(self._toString(setting)))
                #    setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_DOWNLOADPIN"])
                #    log.debug("[Process Settings]      Download  Code {0}".format(self._toString(setting)))

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Store partition info & check if partitions are on
                partition = None
                if partitionCnt > 1:  # Could the panel have more than 1 partition?
                    # If that panel type can have more than 1 partition, then check to see if the panel has defined more than 1
                    partition = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_PARTITIONS"])
                    if partition is None or partition[0] == 255:
                        partitionCnt = 1
                    # else:
                    #    log.debug("[Process Settings] Partition settings " + self._toString(partition))

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process zone settings
                zoneNames = bytearray()
                settingMr = bytearray()
                if not self.PowerMaster:
                    zoneNames = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_ZONENAMES"])
                else:  # PowerMaster models
                    zoneNames = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_ZONENAMES"])
                    settingMr = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_ZONES"])
                    # log.debug("[Process Settings] MSG_DL_MR_ZONES Buffer " + self._toString(settingMr))

                setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_ZONES"])

                # zonesignalstrength = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_ZONESIGNAL"])
                # log.debug("[Process Settings] ZoneSignal " + self._toString(zonesignalstrength))
                # log.debug("[Process Settings] DL Zone settings " + self._toString(setting))
                # log.debug("[Process Settings] Zones Names Buffer :  {0}".format(self._toString(zoneNames)))

                if len(setting) > 0 and len(zoneNames) > 0:
                    # log.debug("[Process Settings] Zones:    len settings {0}     len zoneNames {1}    zoneCnt {2}".format(len(setting), len(zoneNames), zoneCnt))
                    for i in range(0, zoneCnt):
                        # data in the setting bytearray is in blocks of 4
                        zoneName = pmZoneName_t[zoneNames[i] & 0x1F]
                        zoneEnrolled = False
                        if not self.PowerMaster:  # PowerMax models
                            zoneEnrolled = setting[i * 4 : i * 4 + 3] != bytearray.fromhex("00 00 00")
                            # log.debug("[Process Settings]       Zone Slice is " + self._toString(setting[i * 4 : i * 4 + 4]))
                        else:  # PowerMaster models (check only 5 of 10 bytes)
                            zoneEnrolled = settingMr[i * 10 + 4 : i * 10 + 9] != bytearray.fromhex("00 00 00 00 00")

                        if zoneEnrolled:
                            zoneInfo = 0
                            visonicSensorRef = 0
                            sensorType = PySensorType.UNKNOWN
                            sensorModel = "Model Unknown"

                            if not self.PowerMaster:  #  PowerMax models
                                zoneInfo = int(setting[i * 4 + 3])  # extract the zoneType and zoneChime settings
                                visonicSensorRef = int(setting[i * 4 + 2])  # extract the sensorType
                                tmpid = visonicSensorRef & 0x0F
                                #sensorType = "UNKNOWN " + str(tmpid)

                                # User cybfox77 found that PIR sensors were returning the sensor type 'visonicSensorRef' as 0xe5 and 0xd5, these would be decoded as Magnet sensors
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
                                
                                if visonicSensorRef in pmZoneSensorMax_t:
                                    sensorType = pmZoneSensorMax_t[visonicSensorRef].func
                                    sensorModel = pmZoneSensorMax_t[visonicSensorRef].name
                                elif tmpid in pmZoneSensorMaxGeneric_t:
                                    # if tmpid in pmZoneSensorMaxGeneric_t:
                                    sensorType = pmZoneSensorMaxGeneric_t[tmpid]
                                else:
                                    log.debug("[Process Settings] Found unknown sensor type " + str(visonicSensorRef))

                            else:  # PowerMaster models
                                zoneInfo = int(setting[i])
                                visonicSensorRef = int(settingMr[i * 10 + 5])
                                #sensorType = "UNKNOWN " + str(visonicSensorRef)
                                if visonicSensorRef in pmZoneSensorMaster_t:
                                    sensorType = pmZoneSensorMaster_t[visonicSensorRef].func
                                    sensorModel = pmZoneSensorMaster_t[visonicSensorRef].name
                                else:
                                    log.debug("[Process Settings] Found unknown sensor type " + str(visonicSensorRef))

                            zoneType = zoneInfo & 0x0F
                            zoneChime = (zoneInfo >> 4) & 0x03

                            part = []
                            if partitionCnt > 1 and partition is not None:
                                for j in range(0, partitionCnt):
                                    if (partition[0x11 + i] & (1 << j)) > 0:
                                        # log.debug("[Process Settings]     Adding to partition list - ref {0}  Z{1:0>2}   Partition {2}".format(i, i+1, j+1))
                                        part.append(j + 1)
                            else:
                                part = [1]

                            log.debug("[Process Settings]      i={0} :    VisonicSensorRef={1}   zoneInfo={2}   ZTypeName={3}   Chime={4}   SensorType={5}   zoneName={6}".format(
                                   i, hex(visonicSensorRef), hex(zoneInfo), pmZoneType_t["EN"][zoneType], pmZoneChime_t["EN"][zoneChime], sensorType, zoneName))

                            if i in self.pmSensorDev_t:
                                # If we get EPROM data, assume it is all correct and override any existing settings (as they were assumptions)
                                self.pmSensorDev_t[i].stype = sensorType
                                self.pmSensorDev_t[i].sid = visonicSensorRef
                                self.pmSensorDev_t[i].model = sensorModel
                                self.pmSensorDev_t[i].ztype = zoneType
                                self.pmSensorDev_t[i].ztypeName = pmZoneType_t[self.pmLang][zoneType]
                                self.pmSensorDev_t[i].zname = zoneName
                                self.pmSensorDev_t[i].zchime = pmZoneChime_t[self.pmLang][zoneChime]
                                self.pmSensorDev_t[i].dname = "Z{0:0>2}".format(i + 1)
                                self.pmSensorDev_t[i].partition = part
                                self.pmSensorDev_t[i].id = i + 1
                                self.pmSensorDev_t[i].enrolled = True
                            else:
                                self.pmSensorDev_t[i] = SensorDevice(stype = sensorType, sid = visonicSensorRef, model = sensorModel, ztype = zoneType,
                                             ztypeName = pmZoneType_t[self.pmLang][zoneType], zname = zoneName, zchime = pmZoneChime_t[self.pmLang][zoneChime],
                                             dname="Z{0:0>2}".format(i+1), partition = part, id=i+1, enrolled = True)
                                #visonic_devices['sensor'].append(self.pmSensorDev_t[i])
                                if self.new_sensor_callback is not None:
                                    self.new_sensor_callback(self.pmSensorDev_t[i])

                            if i in self.pmSensorDev_t:
                                if sensorType == PySensorType.MAGNET or sensorType == PySensorType.WIRED:
                                    doorZoneStr = "{0},Z{1:0>2}".format(doorZoneStr, i + 1)
                                elif sensorType == PySensorType.MOTION or sensorType == PySensorType.CAMERA:
                                    motionZoneStr = "{0},Z{1:0>2}".format(motionZoneStr, i + 1)
                                elif sensorType == PySensorType.SMOKE or sensorType == PySensorType.GAS:
                                    smokeZoneStr = "{0},Z{1:0>2}".format(smokeZoneStr, i + 1)
                                else:
                                    otherZoneStr = "{0},Z{1:0>2}".format(otherZoneStr, i + 1)
                        else:
                            # log.debug("[Process Settings]       Removing sensor {0} as it is not enrolled".format(i+1))
                            if i in self.pmSensorDev_t:
                                del self.pmSensorDev_t[i]
                                # self.pmSensorDev_t[i] = None # remove zone if needed

                self.sensorsCreated = True

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process PGM/X10 settings
                setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_PGMX10"])
                x10Names = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_X10NAMES"])
                for i in range(0, 16):
                    x10Enabled = False
                    x10Name = 0x1F
                    for j in range(0, 8):
                        x10Enabled = x10Enabled or setting[5 + i + (j * 0x10)] != 0

                    if i > 0:
                        x10Name = x10Names[i - 1]

                    x10Enabled = x10Enabled or x10Name != 0x1F

                    if i == 0:
                        x10Location = "PGM"
                        x10DeviceName = "PGM"
                        x10Type = "OnOff Switch"
                    else:
                        x10Location = pmZoneName_t[x10Name & 0x1F]
                        x10DeviceName = "X{0:0>2}".format(i)
                        x10Type = "Dimmer Switch"

                    if x10Enabled:
                        deviceStr = "{0},{1}".format(deviceStr, x10DeviceName)

                    if i in self.pmX10Dev_t:
                        self.pmX10Dev_t[i].name = x10DeviceName
                        self.pmX10Dev_t[i].enabled = x10Enabled
                        self.pmX10Dev_t[i].type = x10Type
                        self.pmX10Dev_t[i].location = x10Location
                        self.pmX10Dev_t[i].id = i
                    else:
                        self.pmX10Dev_t[i] = X10Device(name=x10DeviceName, type=x10Type, location=x10Location, id=i, enabled=x10Enabled)
                        if self.new_switch_callback is not None:
                            self.new_switch_callback(self.pmX10Dev_t[i])
                        #visonic_devices["switch"].append(self.pmX10Dev_t[i])

                    # log.debug("[Process Settings] X10 device {0} {1}".format(i, deviceStr))

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                if not self.PowerMaster:
                    # Process keypad settings
                    setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_1WKEYPAD"])
                    for i in range(0, keypad1wCnt):
                        keypadEnrolled = setting[i * 4 : i * 4 + 2] != bytearray.fromhex("00 00")
                        log.debug("[Process Settings] Found a 1keypad {0} enrolled {1}".format(i, keypadEnrolled))
                        if keypadEnrolled:
                            deviceStr = "{0},K1{1:0>2}".format(deviceStr, i)
                    setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_2WKEYPAD"])
                    for i in range(0, keypad2wCnt):
                        keypadEnrolled = setting[i * 4 : i * 4 + 3] != bytearray.fromhex("00 00 00")
                        log.debug("[Process Settings] Found a 2keypad {0} enrolled {1}".format(i, keypadEnrolled))
                        if keypadEnrolled:
                            deviceStr = "{0},K2{1:0>2}".format(deviceStr, i)

                    # Process siren settings
                    setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_SIRENS"])
                    for i in range(0, sirenCnt):
                        sirenEnrolled = setting[i * 4 : i * 4 + 3] != bytearray.fromhex("00 00 00")
                        log.debug("[Process Settings] Found a siren {0} enrolled {1}".format(i, sirenEnrolled))
                        if sirenEnrolled:
                            deviceStr = "{0},S{1:0>2}".format(deviceStr, i)
                else:  # PowerMaster
                    # Process keypad settings
                    setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_KEYPADS"])
                    for i in range(0, keypad2wCnt):
                        keypadEnrolled = setting[i * 10 : i * 10 + 5] != bytearray.fromhex("00 00 00 00 00")
                        log.debug("[Process Settings] Found a PMaster keypad {0} enrolled {1}".format(i, keypadEnrolled))
                        if keypadEnrolled:
                            deviceStr = "{0},K2{1:0>2}".format(deviceStr, i)
                    # Process siren settings
                    setting = self._readEPROMSettings(pmDownloadItem_t["MSG_DL_MR_SIRENS"])
                    for i in range(0, sirenCnt):
                        sirenEnrolled = setting[i * 10 : i * 10 + 5] != bytearray.fromhex("00 00 00 00 00")
                        log.debug("[Process Settings] Found a siren {0} enrolled {1}".format(i, sirenEnrolled))
                        if sirenEnrolled:
                            deviceStr = "{0},S{1:0>2}".format(deviceStr, i)

                doorZones = doorZoneStr[1:]
                motionZones = motionZoneStr[1:]
                smokeZones = smokeZoneStr[1:]
                devices = deviceStr[1:]
                otherZones = otherZoneStr[1:]

                log.debug("[Process Settings] Adding zone devices")

                self.PanelStatus["Door Zones"] = doorZones
                self.PanelStatus["Motion Zones"] = motionZones
                self.PanelStatus["Smoke Zones"] = smokeZones
                self.PanelStatus["Other Zones"] = otherZones
                self.PanelStatus["Devices"] = devices

                # INTERFACE : Create Partitions in the interface
                # for i in range(1, partitionCnt+1): # TODO

                log.debug("[Process Settings] Ready for use")

            else:
                log.warning("[Process Settings] WARNING: Cannot process panel EPROM settings, download has not completed")

        elif pmPanelTypeNr is None or pmPanelTypeNr == 0xFF:
            log.warning("[Process Settings] WARNING: Cannot process panel EPROM settings, we're probably connected to the panel in standard mode")
        else:
            log.warning("[Process Settings] WARNING: Cannot process panel EPROM settings, the panel is too new {0}".format(self.PanelType))

        self._dumpSensorsToLogFile()

        if self.pmPowerlinkMode:
            self.PanelMode = PyPanelMode.POWERLINK
        elif self.pmDownloadComplete and self.pmGotUserCode:
            log.debug("[Process Settings] Entering Standard Plus Mode as we got the pin codes from the EPROM")
            self.PanelMode = PyPanelMode.STANDARD_PLUS
        else:
            self.PanelMode = PyPanelMode.STANDARD


    # This function handles a received message packet and processes it
    def _processReceivedPacket(self, packet):
        """Handle one raw incoming packet."""

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
            self._performDisconnect("Same Packet for {0} times in a row".format(SAME_PACKET_ERROR))
        # else:
        #    log.debug("[_processReceivedPacket] Parsing complete valid packet: %s", self._toString(packet))

        pushChange = False

        if len(packet) < 4:  # there must at least be a header, command, checksum and footer
            log.warning("[_processReceivedPacket] Received invalid packet structure, not processing it " + self._toString(packet))
        elif packet[1] == ACK_MESSAGE:  # ACK
            # remove the header and command bytes as the start. remove the footer and the checksum at the end
            self.handle_msgtype02(packet[2:-2])  
        elif packet[1] == 0x06:  # Timeout
            self.handle_msgtype06(packet[2:-2])
        elif packet[1] == 0x08:  # Access Denied
            self.handle_msgtype08(packet[2:-2])
        elif packet[1] == 0x0B:  # Stopped
            self.handle_msgtype0B(packet[2:-2])
        elif packet[1] == 0x0F:  # LOOPBACK TEST, EXIT (0x0F) IS THE FIRST COMMAND SENT TO THE PANEL WHEN THIS INTEGRATION STARTS
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
            pushChange = self.handle_msgtypeA3(packet[2:-2])
        elif packet[1] == 0xA5:  # General Event
            pushChange = self.handle_msgtypeA5(packet[2:-2])
        elif packet[1] == 0xA6:  # General Event
            pushChange = self.handle_msgtypeA6(packet[2:-2])
        elif packet[1] == 0xA7:  # General Event
            pushChange = self.handle_msgtypeA7(packet[2:-2])
        elif packet[1] == 0xAB and not self.ForceStandardMode:  
            # PowerLink Event. Only process AB if not forced standard
            pushChange = self.handle_msgtypeAB(packet[2:-2])
        elif packet[1] == 0xAB:  # PowerLink Event. Only process AB if not forced standard
            log.debug("[_processReceivedPacket] Received AB Message but we are in Standard Mode (ignoring message) " + self._toString(packet))
        elif packet[1] == 0xAC:  # X10 Names
            self.handle_msgtypeAC(packet[2:-2])
        elif packet[1] == 0xB0:  # PowerMaster Event
            if not self.pmDownloadMode:  # only process when not downloading EPROM
                pushChange = self.handle_msgtypeB0(packet[2:-2])
        elif packet[1] == 0xF4:  # F4 Message from a Powermaster, can't decode it yet but this will accept it and ignore it
            log.debug("[_processReceivedPacket] Powermaster F4 message " + self._toString(packet))
        else:
            log.debug("[_processReceivedPacket] Unknown/Unhandled packet type " + self._toString(packet))

        if pushChange:      
            self._sendResponseEvent(PyCondition.PUSH_CHANGE)  # 0 means push through an HA Frontend change, do not create an HA Event


#    def _displayZoneBin(self, bits):
#        """Display Zones in reverse binary format
#        Zones are from e.g. 1-8, but it is stored in 87654321 order in binary format"""
#        return bin(bits)


    def handle_msgtype02(self, data):  # ACK
        """ Handle Acknowledges from the panel """
        # Normal acknowledges have msgtype 0x02 but no data, when in powerlink the panel also sends data byte 0x43
        #    I have not found this on the internet, this is my hypothesis
        log.debug("[handle_msgtype02] Ack Received  data = {0}".format(self._toString(data)))
        if not self.pmPowerlinkMode and len(data) > 0:
            if data[0] == 0x43:  # Received a powerlink acknowledge
                log.debug("[handle_msgtype02]    Received a powerlink acknowledge, I am in {0} mode".format(self.PanelMode.name))
                if self.allowAckToTriggerRestore:
                    log.debug("[handle_msgtype02]        and sending MSG_RESTORE")
                    self._sendCommand("MSG_RESTORE")
                    self.allowAckToTriggerRestore = False

    def _delayDownload(self):
        self.pmDownloadMode = False
        self.giveupTrying = False
        self.pmDownloadComplete = False
        # exit download mode and try again in DOWNLOAD_RETRY_DELAY seconds
        self._sendCommand("MSG_STOP")
        self._sendCommand("MSG_EXIT")
        self._triggerRestoreStatus()
        # Assume that we are not in Powerlink as we haven't completed download yet.
        self.PanelMode = PyPanelMode.STANDARD

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
                    self._sendResponseEvent(PyCondition.PIN_REJECTED)  # push changes through to the host, the pin has been rejected
                elif lastCommandData[0] == 0x24:
                    log.debug("[handle_msgtype08] Got an Access Denied and we have sent a Download command to the Panel")
                    self.pmDownloadMode = False
                    self.doneAutoEnroll = False
                    if self.PanelType is not None:  # By the time EPROM download is complete, this should be set but just check to make sure
                        if self.PanelType >= 3:     # Only attempt to auto enroll powerlink for newer panels. Older panels need the user to manually enroll, we should be in Standard Plus by now.
                            log.debug("[handle_msgtype08]                   Try to auto enroll (panel type {0})".format(self.PanelType))
                            self._sendMsgENROLL(True)  # Auto enroll, retrigger download
                    elif self.ForceAutoEnroll:
                        log.debug("[handle_msgtype08]                   Try to force auto enroll (panel type unknown)")
                        self._sendMsgENROLL(True)  #  Auto enroll, retrigger download
                elif lastCommandData[0] != 0xAB and lastCommandData[0] != 0x0B:  # Powerlink command and the Stop Command
                    log.debug("[handle_msgtype08] Attempt to send a command message to the panel that has been rejected")
                    self._sendResponseEvent(PyCondition.COMMAND_REJECTED)  # push changes through to the host, something has been rejected (other than the pin)
                

    def handle_msgtype0B(self, data):  # STOP
        """ Handle STOP from the panel """
        log.debug("[handle_msgtype0B] Stop    data is {0}".format(self._toString(data)))

    def handle_msgtype0F(self, data):  # LOOPBACK TEST SUCCESS, EXIT COMMAND (0x0F) IS THE FIRST COMMAND SENT TO THE PANEL WHEN THIS INTEGRATION STARTS
        """ Handle STOP from the panel """
        self.loopbackTest = True
        self.loopbackCounter = self.loopbackCounter + 1
        log.warning("[handle_msgtype0F] LOOPBACK TEST SUCCESS, Counter is {0}".format(self.loopbackCounter))

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
        self.ModelType = data[4]
        self.PanelType = data[5]

        self.PowerMaster = (self.PanelType >= 7)
        self.PanelModel = pmPanelType_t[self.PanelType] if self.PanelType in pmPanelType_t else "UNKNOWN"   # INTERFACE : PanelType set to model

        self.pmGotPanelDetails = True
        self.pmInitSupportedByPanel = (self.PanelType >= 4)

        log.debug("[handle_msgtype3C] PanelType={0} : {2} , Model={1}   Powermaster {3}".format(self.PanelType, self.ModelType, self.PanelModel, self.PowerMaster))

        # We got a first response, now we can Download the panel EPROM settings
        interval = self._getTimeFunction() - self.lastSendOfDownloadEprom
        td = timedelta(seconds=DOWNLOAD_RETRY_DELAY)  # prevent multiple requests for the EPROM panel settings, at least DOWNLOAD_RETRY_DELAY seconds
        log.debug("[handle_msgtype3C] interval={0}  td={1}   self.lastSendOfDownloadEprom={2}    timenow={3}".format(interval, td, self.lastSendOfDownloadEprom, self._getTimeFunction()))
        if interval > td:
            self.lastSendOfDownloadEprom = self._getTimeFunction()
            self._readPanelSettings(self.PowerMaster)
        elif self.triggeredDownload and not self.pmDownloadComplete and len(self.myDownloadList) > 0:
            # Download has already started and we've already started asking for the EPROM Data
            # So lets ask for it all again, from the beginning
            #    On a particular panel we received a 3C and send the first download block request.
            #       We received nothing for 10 seconds and then another 3C from the panel.
            #       Originally we ignored this and then the panel sent a timeout 20 seconds later, so lets try resending the request
            log.debug("[handle_msgtype3C]          Asking for panel EPROM again")
            self.lastSendOfDownloadEprom = self._getTimeFunction()
            self._readPanelSettings(self.PowerMaster)

    def handle_msgtype3F(self, data):
        """MsgType=3F - Download information
        Multiple 3F can follow each other, if we request more then &HFF bytes"""

        # data format is normally: <index> <page> <length> <data ...>
        # If the <index> <page> = FF, then it is an additional PowerMaster MemoryMap
        iIndex = data[0]
        iPage = data[1]
        iLength = data[2]

        log.debug("[handle_msgtype3F] actual data block length=" + str(len(data)-3) + "   data content length=" + str(iLength))

        # PowerMaster 10 (Model 7) and PowerMaster 33 (Model 10) has a very specific problem with downloading the Panel EPROM and doesn't respond with the correct number of bytes
        #if self.PanelType is not None and self.ModelType is not None and ((self.PanelType == 7 and self.ModelType == 68) or (self.PanelType == 10 and self.ModelType == 71)):
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
            self._sendCommand("MSG_DL", options=[1, self.myDownloadList.pop(0)])  # Read the next block of EPROM data
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
            self._sendCommand("MSG_EXIT")  # Exit download mode
            # We received a download exit message, restart timer
            self._reset_watchdog_timeout()
            self._processEPROMSettings()

    def handle_msgtypeA0(self, data):
        """ MsgType=A0 - Event Log """
        log.debug("[handle_MsgTypeA0] Packet = {0}".format(self._toString(data)))

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
            if self.PowerMaster is not None:
                if self.PowerMaster:
                    zoneStr = pmLogPowerMasterUser_t[self.pmLang][iEventZone] or "UNKNOWN"
                else:
                    iEventZone = int(iEventZone & 0x7F)
                    zoneStr = pmLogPowerMaxUser_t[self.pmLang][iEventZone] or "UNKNOWN"

            eventStr = pmLogEvent_t[self.pmLang][iLogEvent] or "UNKNOWN"

            idx = eventNum - 1

            # Create an event log array
            self.pmEventLogDictionary[idx] = PyLogPanelEvent()
            if pmPanelConfig_t["CFG_PARTITIONS"][self.PanelType] > 1:
                part = 0
                for i in range(1, 4):
                    part = (iSec % (2 * i) >= i) and i or part
                self.pmEventLogDictionary[idx].partition = (part == 0) and "Panel" or part
                self.pmEventLogDictionary[idx].time = "{0:0>2}:{1:0>2}".format(iHour, iMin)
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
            if self.panel_event_log_callback is not None:
                self.panel_event_log_callback(self.pmEventLogDictionary[idx])

            log.debug("[handle_msgtypeA0] Finished processing Log Event {0}".format(self.pmEventLogDictionary[idx]))

    def handle_msgtypeA3(self, data) -> bool:
        """ MsgType=A3 - Zone Names """
        log.debug("[handle_MsgTypeA3] Packet = {0}".format(self._toString(data)))
        pushChange = False
        msgCnt = int(data[0])
        offset = 8 * (int(data[1]) - 1)
        log.debug("            Message Count is {0}   offset={1}     self.pmPowerlinkMode={2}".format( msgCnt, offset, self.pmPowerlinkMode ))
        for i in range(0, 8):
            zoneName = pmZoneName_t[int(data[2+i]) & 0x1F]
            log.debug("                        Zone name for sensor {0} is {1} : {2}".format( offset+i+1, int(data[2+i]), zoneName ))
            if not self.pmPowerlinkMode and offset+i in self.pmSensorDev_t:
                if not self.pmSensorDev_t[offset+i].zname:     # if not already set
                    log.debug("                            Setting Zone Name")
                    self.pmSensorDev_t[offset+i].zname = zoneName
                    pushChange = True
    
        return pushChange

    #    def displaySensorBypass(self, sensor):
    #        armed = False
    #        if self.pmSensorShowBypass:
    #            armed = sensor.bypass
    #        else:
    #            zoneType = sensor.ztype
    #            mode = bitw.band(pmSysStatus, 0x0F) -- armed or not: 4=armed home; 5=armed away
    # 		 local alwaysOn = { [2] = "", [3] = "", [9] = "", [10] = "", [11] = "", [14] = "" }
    # Show as armed if
    #    a) the sensor type always triggers an alarm: (2)flood, (3)gas, (11)fire, (14)temp, (9/10)24h (silent/audible)
    #    b) the system is armed away (mode = 4)
    #    c) the system is armed home (mode = 5) and the zone is not interior(-follow) (6,12)
    #         armed = ((zoneType > 0) and (sensor['bypass'] ~= true) and ((alwaysOn[zoneType] ~= nil) or (mode == 0x5) or ((mode == 0x4) and (zoneType % 6 ~= 0)))) and "1" or "0"

    def _makeInt(self, data) -> int:
        if len(data) == 4:
            val = data[0]
            val = val + (0x100 * data[1])
            val = val + (0x10000 * data[2])
            val = val + (0x1000000 * data[3])
            return int(val)
        return 0

    # captured examples of A5 data
    #     0d a5 00 04 00 61 03 05 00 05 00 00 43 a4 0a
    def handle_msgtypeA5(self, data) -> bool:  # Status Message

        #zoneCnt = 0  # this means it wont work in case we're in standard mode and the panel type is not set
        #if self.PanelType is not None:
        #    zoneCnt = pmPanelConfig_t["CFG_WIRELESS"][self.PanelType] + pmPanelConfig_t["CFG_WIRED"][self.PanelType]

        # msgTot = data[0]
        eventType = data[1]

        log.debug("[handle_msgtypeA5] Parsing A5 packet " + self._toString(data))

        pushChange = False

        if self.sensorsCreated and eventType == 0x01:  # Zone alarm status
            log.debug("[handle_msgtypeA5] Zone Alarm Status")
            val = self._makeInt(data[2:6])
            if val != self.zonealarm_old:
                self.zonealarm_old = val
                log.debug("[handle_msgtypeA5]      Zone Trip Alarm 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        self.pmSensorDev_t[i].ztrip = val & (1 << i) != 0
                        pushChange = True
                        #self.pmSensorDev_t[i].pushChange()

            val = self._makeInt(data[6:10])
            if val != self.zonetamper_old:
                self.zonetamper_old = val
                log.debug("[handle_msgtypeA5]      Zone Tamper Alarm 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        self.pmSensorDev_t[i].ztamper = val & (1 << i) != 0
                        pushChange = True
                        #self.pmSensorDev_t[i].pushChange()

        elif self.sensorsCreated and eventType == 0x02:  # Status message - Zone Open Status
            # if in standard mode then use this A5 status message to reset the watchdog timer
            if not self.pmPowerlinkMode:
                log.debug("[handle_msgtypeA5]      Got A5 02 message, resetting watchdog")
                self._reset_watchdog_timeout()

            val = self._makeInt(data[2:6])
            if val != self.status_old:
                self.status_old = val
                log.debug("[handle_msgtypeA5]      Open Door/Window Status Zones 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        alreadyset = self.pmSensorDev_t[i].status
                        self.pmSensorDev_t[i].status = val & (1 << i) != 0
                        if not alreadyset and self.pmSensorDev_t[i].status:
                            self.pmSensorDev_t[i].triggered = True
                            self.pmSensorDev_t[i].triggertime = self._getTimeFunction()
                        pushChange = True
                        #self.pmSensorDev_t[i].pushChange()

            val = self._makeInt(data[6:10])
            if val != self.lowbatt_old:
                self.lowbatt_old = val
                log.debug("[handle_msgtypeA5]      Battery Low Zones 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        self.pmSensorDev_t[i].lowbatt = val & (1 << i) != 0
                        pushChange = True
                        #self.pmSensorDev_t[i].pushChange()

        elif self.sensorsCreated and eventType == 0x03:  # Tamper Event
            val = self._makeInt(data[2:6])
            log.debug("[handle_msgtypeA5]      Trigger (Inactive) Status Zones 32-01: {:032b}".format(val))
            # This status is different from the status in the 0x02 part above i.e they are different values.
            #    This one is wrong (I had a door open and this status had 0, the one above had 1)
            #       According to domotica forum, this represents "active" but what does that actually mean?
            # for i in range(0, 32):
            #    if i in self.pmSensorDev_t:
            #        self.pmSensorDev_t[i].status = (val & (1 << i) != 0)

            val = self._makeInt(data[6:10])
            if val != self.tamper_old:
                self.tamper_old = val
                log.debug("[handle_msgtypeA5]      Tamper Zones 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        self.pmSensorDev_t[i].tamper = val & (1 << i) != 0
                        pushChange = True
                        #self.pmSensorDev_t[i].pushChange()

        elif eventType == 0x04:  # Zone event
            if not self.pmPowerlinkMode:
                log.debug("[handle_msgtypeA5]      Got A5 04 message, resetting watchdog")
                self._reset_watchdog_timeout()

            sysStatus = data[2]  # Mark-Mills with a PowerMax Complete Part, sometimes this has 0x20 bit set and I'm not sure why
            sysFlags = data[3]
            eventZone = data[4]
            eventType = data[5]
            # dont know what 6 and 7 are
            x10stat1 = data[8]
            x10stat2 = data[9]
            x10status = x10stat1 + (x10stat2 * 0x100)

            sysStatus = sysStatus & 0x1F     # Mark-Mills with a PowerMax Complete Part, sometimes this has the 0x20 bit set and I'm not sure why

            # Examine X10 status
            for i in range(0, 16):
                status = x10status & (1 << i)
                if i in self.pmX10Dev_t:
                    # INTERFACE : use this to set X10 status
                    oldstate = self.pmX10Dev_t[i].state
                    self.pmX10Dev_t[i].state = bool(status)
                    # Check to see if the state has changed
                    if (oldstate and not self.pmX10Dev_t[i].state) or (not oldstate and self.pmX10Dev_t[i].state):
                        log.debug("[handle_msgtypeA5]      X10 device {0} changed to {1}".format(i, status))
                        #self.pmX10Dev_t[i].pushChange()

            slog = pmDetailedArmMode_t[sysStatus]
            sarm_detail = "Unknown"
            if 0 <= sysStatus < len(pmSysStatus_t[self.pmLang]):
                sarm_detail = pmSysStatus_t[self.pmLang][sysStatus]

            if sysStatus == 7 and self.pmDownloadComplete:  # sysStatus == 7 means panel "downloading"
                log.debug("[handle_msgtypeA5]      Sending a STOP and EXIT as we seem to be still in the downloading state and it should have finished")
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
                self.PanelStatusCode = PyPanelStatus.ENTRY_DELAY  # Entry Delay
            elif sysStatus in [0x04, 0x0A, 0x13, 0x14]:
                sarm = "Armed"
                self.PanelStatusCode = PyPanelStatus.ARMED_HOME  # Armed Home
            elif sysStatus in [0x05, 0x0B, 0x15]:
                sarm = "Armed"
                self.PanelStatusCode = PyPanelStatus.ARMED_AWAY  # Armed Away
            elif sysStatus in [0x01, 0x11]:
                sarm = "Arming"
                self.PanelStatusCode = PyPanelStatus.ARMING_HOME  # Arming Home
            elif sysStatus in [0x02, 0x12]:
                sarm = "Arming"
                self.PanelStatusCode = PyPanelStatus.ARMING_AWAY  # Arming Away
            elif sysStatus in [0x07]:
                sarm = "Disarmed"
                self.PanelStatusCode = PyPanelStatus.DOWNLOADING  # Downloading
            elif sysStatus in [0x06, 0x08, 0x09]:
                sarm = "Disarmed"
                self.PanelStatusCode = PyPanelStatus.SPECIAL  # Special ("User Test", "Programming", "Installer")
            elif sysStatus > 0x15:
                log.debug("[handle_msgtypeA5]      Unknown state {0}, assuming Disarmed".format(sysStatus))
                sarm = "Disarmed"
                self.PanelStatusCode = PyPanelStatus.DISARMED  # Disarmed
            else:
                sarm = "Disarmed"
                self.PanelStatusCode = PyPanelStatus.DISARMED  # Disarmed

            log.debug("[handle_msgtypeA5]      log: {0}, arm: {1}".format(slog + "(" + sarm_detail + ")", sarm))

            self.PanelStatusText = sarm_detail
            self.PanelReady = sysFlags & 0x01 != 0
            self.PanelAlertInMemory = sysFlags & 0x02 != 0
            self.PanelTrouble = sysFlags & 0x04 != 0
            self.PanelBypass = sysFlags & 0x08 != 0
            if sysFlags & 0x10 != 0:  # last 10 seconds of entry/exit
                self.PanelArmed = sarm == "Arming"
            else:
                self.PanelArmed = sarm == "Armed"
            self.PanelStatusChanged = sysFlags & 0x40 != 0
            self.PanelAlarmEvent = sysFlags & 0x80 != 0

            if not self.pmPowerlinkMode:
                # if the system status has the panel armed and there has been an alarm event, assume that the alarm is sounding
                #        and that the sensor that triggered it isn't an entry delay
                #   Normally this would only be directly available in Powerlink mode with A7 messages, but an assumption is made here
                if self.PanelArmed and self.PanelAlarmEvent and self.PanelStatusCode != PyPanelStatus.ENTRY_DELAY:
                    log.debug("[handle_msgtypeA5]      Alarm Event Assumed while in Standard Mode")
                    # Alarm Event
                    self.pmSirenActive = self._getTimeFunction()

                    datadict = {}
                    datadict["Zone"] = 0
                    datadict["Entity"] = None
                    datadict["Tamper"] = False
                    datadict["Siren"] = True
                    datadict["Reset"] = False
                    datadict["Time"] = self.pmSirenActive
                    datadict["Count"] = 0
                    datadict["Type"] = []
                    datadict["Event"] = []
                    datadict["Mode"] = []
                    datadict["Name"] = []
                    self._sendResponseEvent(PyCondition.PANEL_UPDATE_ALARM_ACTIVE, datadict)  # Alarm Event

            # Clear any alarm event if the panel alarm has been triggered before (while armed) but now that the panel is disarmed (in all modes)
            if self.pmSirenActive is not None and sarm == "Disarmed":
                log.debug("[handle_msgtypeA5] ******************** Alarm Not Sounding (Disarmed) ****************")
                self.pmSirenActive = None

            if sysFlags & 0x20 != 0:  # Zone Event
                sEventLog = pmEventType_t[self.pmLang][eventType]
                log.debug("[handle_msgtypeA5]      Zone Event")
                log.debug("[handle_msgtypeA5]            Zone: {0}    Type: {1}, {2}".format(eventZone, eventType, sEventLog))
                key = eventZone - 1  # get the key from the zone - 1
                #                for key, value in self.pmSensorDev_t.items():
                #                    if value.id == eventZone:      # look for the device name
                if key in self.pmSensorDev_t:
                    if eventType == 1:  # Tamper Alarm
                        self.pmSensorDev_t[key].tamper = True
                    elif eventType == 2:  # Tamper Restore
                        self.pmSensorDev_t[key].tamper = False
                    elif eventType == 3:  # Zone Open
                        self.pmSensorDev_t[key].triggered = True
                        self.pmSensorDev_t[key].status = True
                        self.pmSensorDev_t[key].triggertime = self._getTimeFunction()
                    elif eventType == 4:  # Zone Closed
                        self.pmSensorDev_t[key].triggered = False
                        self.pmSensorDev_t[key].status = False
                    elif eventType == 5:  # Zone Violated
                        if not self.pmSensorDev_t[key].triggered:
                            self.pmSensorDev_t[key].triggertime = self._getTimeFunction()
                            self.pmSensorDev_t[key].triggered = True
                    # elif eventType == 6: # Panic Alarm
                    # elif eventType == 7: # RF Jamming
                    elif eventType == 8: # Tamper Open
                        self.pmSensorDev_t[key].tamper = True
                    # elif eventType == 9: # Comms Failure
                    # elif eventType == 10: # Line Failure
                    # elif eventType == 11: # Fuse
                    # elif eventType == 12: # Not Active
                    #    self.pmSensorDev_t[key].triggered = False
                    #    self.pmSensorDev_t[key].status = False
                    elif eventType == 13:  # Low Battery
                        self.pmSensorDev_t[key].lowbatt = True
                        #self.pmSensorDev_t[key].pushChange()
                    # elif eventType == 14: # AC Failure
                    # elif eventType == 15: # Fire Alarm
                    # elif eventType == 16: # Emergency
                    elif eventType == 17: # Siren Tamper
                        self.pmSensorDev_t[key].tamper = True
                    elif eventType == 18: # Siren Tamper Restore
                        self.pmSensorDev_t[key].tamper = False
                    elif eventType == 19: # Siren Low Battery
                        self.pmSensorDev_t[key].lowbatt = True
                    # elif eventType == 20: # Siren AC Fail

                    datadict = {}
                    datadict["Zone"] = eventZone
                    datadict["Event"] = eventType
                    datadict["Description"] = sEventLog

                    self._sendResponseEvent(PyCondition.ZONE_UPDATE, datadict)  # push zone changes through to the host to get it to update

            pushChange = True
            
            #   0x03 : "", 0x04 : "", 0x05 : "", 0x0A : "", 0x0B : "", 0x13 : "", 0x14 : "", 0x15 : ""
            # armModeNum = 1 if pmArmed_t[sysStatus] != None else 0
            # armMode = "Armed" if armModeNum == 1 else "Disarmed"

        elif eventType == 0x06:  # Status message enrolled/bypassed
            # e.g. 00 06 7F 00 00 10 00 00 00 00 43
            val = self._makeInt(data[2:6])

            if val != self.enrolled_old:
                log.debug("[handle_msgtypeA5]      Enrolled Zones 32-01: {:032b}".format(val))
                send_zone_type_request = False
                #visonic_devices = defaultdict(list)
                self.enrolled_old = val
                for i in range(0, 32):
                    # if the sensor is enrolled
                    if val & (1 << i) != 0:
                        # do we already know about the sensor from the EPROM decode
                        if i in self.pmSensorDev_t:
                            self.pmSensorDev_t[i].enrolled = True
                        else:
                            # we dont know about it so create it and make it enrolled
                            pushChange = True
                            self.pmSensorDev_t[i] = SensorDevice(dname="Z{0:0>2}".format(i + 1), id=i + 1, stype=PySensorType.MAGNET, enrolled=True)
                            #visonic_devices["sensor"].append(self.pmSensorDev_t[i])
                            if self.new_sensor_callback is not None:
                                self.new_sensor_callback(self.pmSensorDev_t[i])
                            if not send_zone_type_request:
                                self._sendCommand("MSG_ZONENAME")
                                self._sendCommand("MSG_ZONETYPE")
                                send_zone_type_request = True

                    elif i in self.pmSensorDev_t:
                        # it is not enrolled and we already know about it from the EPROM, set enrolled to False
                        self.pmSensorDev_t[i].enrolled = False

                self.sensorsCreated = True               
                #self._sendResponseEvent(visonic_devices)

            val = self._makeInt(data[6:10])
            if self.sensorsCreated and val != self.bypass_old:
                log.debug("[handle_msgtypeA5]      Bypassed Zones 32-01: {:032b}".format(val))
                self.bypass_old = val
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        self.pmSensorDev_t[i].bypass = val & (1 << i) != 0
                        pushChange = True
                        #self.pmSensorDev_t[i].pushChange()

            self._dumpSensorsToLogFile()
        # else:
        #    log.debug("[handle_msgtypeA5]      Unknown A5 Message: " + self._toString(data))
        return pushChange

    def handle_msgtypeA6(self, data) -> bool:
        """ MsgType=A6 - Zone Types I think """
        log.debug("[handle_MsgTypeA6] Packet = {0}".format(self._toString(data)))
        pushChange = False
        msgCnt = int(data[0])
        offset = 8 * (int(data[1]) - 1)
        log.debug("            Message Count is {0}   offset={1}     self.pmPowerlinkMode={2}".format( msgCnt, offset, self.pmPowerlinkMode ))
        for i in range(0, 8):
            zoneType = ((int(data[2+i])) - 0x1E) & 0x0F
            log.debug("                        Zone type for sensor {0} is {1} : {2}".format( offset+i+1, (int(data[2+i])) - 0x1E, pmZoneType_t["EN"][zoneType] ))
            if not self.pmPowerlinkMode and (offset+i) in self.pmSensorDev_t:
                if not self.pmSensorDev_t[offset+i].ztypeName:     # if not already set
                    log.debug("                            Setting Zone Type")
                    self.pmSensorDev_t[offset+i].ztypeName = pmZoneType_t[self.pmLang][zoneType]
                    self.pmSensorDev_t[offset+i].ztype = zoneType
                    # This is based on an assumption that "Interior" and "Interior-Follow" zone types are motion sensors
                    if zoneType == 6 or zoneType == 12:
                        self.pmSensorDev_t[offset+i].stype = PySensorType.MOTION
                    pushChange = True
        return pushChange

    def handle_msgtypeA7(self, data) -> bool:
        """ MsgType=A7 - Panel Status Change """
        log.debug("[handle_msgtypeA7] Panel Status Change " + self._toString(data))
        #
        #   In a log file I reveiced from pocket,    there was this A7 message 0d a7 ff fc 00 60 00 ff 00 0c 00 00 43 45 0a
        #   In a log file I reveiced from UffeNisse, there was this A7 message 0d a7 ff 64 00 60 00 ff 00 0c 00 00 43 45 0a     msgCnt is 0xFF and temp is 0x64 ????
        #
        pmTamperActive = None
        msgCnt = int(data[0])

        # don't know what this is (It is 0x00 in test messages so could be the higher 8 bits for msgCnt)
        dummy = int(data[1])

        # If message count is FF then it looks like the first message is valid so decode it (this is experimental)
        if msgCnt == 0xFF:
            msgCnt = 1
        if msgCnt <= 4:
            datadict = {}
            datadict["Zone"] = 0
            datadict["Entity"] = None
            datadict["Tamper"] = False
            datadict["Siren"] = self.pmSirenActive is not None
            datadict["Reset"] = False
            datadict["Time"] = self._getTimeFunction()
            datadict["Count"] = msgCnt
            datadict["Type"] = []
            datadict["Event"] = []
            datadict["Mode"] = []
            datadict["Name"] = []

            log.debug("[handle_msgtypeA7]      A7 message contains {0} messages".format(msgCnt))

            zoneCnt = 0  # this means it wont work in case we're in standard mode and the panel type is not set
            if self.PanelType is not None:
                zoneCnt = pmPanelConfig_t["CFG_WIRELESS"][self.PanelType] + pmPanelConfig_t["CFG_WIRED"][self.PanelType]

            for i in range(0, msgCnt):
                eventZone = int(data[2 + (2 * i)])
                eventType = int(data[3 + (2 * i)])

                zoneStr = "Unknown"
                zoneData = 0
                if self.PowerMaster is not None:
                    if eventZone >= 1 and eventZone <= zoneCnt:
                        zoneData = eventZone
                    if self.PowerMaster:
                        zoneStr = pmLogPowerMasterUser_t[self.pmLang][eventZone] or "UNKNOWN"
                    else:
                        eventZone = int(eventZone & 0x7F)
                        zoneStr = pmLogPowerMaxUser_t[self.pmLang][eventZone] or "UNKNOWN"
                else:
                    log.debug("[handle_msgtypeA7]         Got an A7 message and the self.PowerMaster variable is not set")

                modeStr = pmLogEvent_t[self.pmLang][eventType] or "UNKNOWN"
                s = modeStr + " / " + zoneStr

                datadict["Type"].insert(0, eventType)
                datadict["Mode"].insert(0, modeStr)

                datadict["Event"].insert(0, eventZone)
                datadict["Name"].insert(0, zoneStr)

                # ---------------------------------------------------------------------------------------
                log.debug("[handle_msgtypeA7]         self.SirenTriggerList = {0}".format(self.SirenTriggerList))

                siren = False
                alarmStatus = None
                if eventType in pmPanelAlarmType_t:
                    alarmStatus = pmPanelAlarmType_t[eventType]
                    log.debug("[handle_msgtypeA7]         Checking if {0} is in the siren trigger list {1}".format(alarmStatus.lower(), self.SirenTriggerList))
                    if alarmStatus.lower() in self.SirenTriggerList:
                        log.debug("[handle_msgtypeA7]             And it is, setting siren to True")
                        siren = True

                # 0x01 is "Interior Alarm", 0x02 is "Perimeter Alarm", 0x03 is "Delay Alarm", 0x05 is "24h Audible Alarm"
                # 0x04 is "24h Silent Alarm", 0x0B is "Panic From Keyfob", 0x0C is "Panic From Control Panel", 0x20 is Fire
                # siren = eventType == 0x01 or eventType == 0x02 or eventType == 0x03 or eventType == 0x05
                # or eventType == 0x20 or eventType == 0x4D  ## Fire and Flood

                troubleStatus = None
                if eventType in pmPanelTroubleType_t:
                    troubleStatus = pmPanelTroubleType_t[eventType]

                # zoneData = 1  ## TESTING ONLY (so I dont need to set the siren sounding each time)
                # set the dictionary to send with the event
                if zoneData-1 in self.pmSensorDev_t:
                    if datadict['Zone'] != 0:
                        log.debug("[handle_msgtypeA7]          ************* Oops - multiple zone events in the same A7 Message **************")
                    datadict['Zone'] = zoneData
                    datadict['Entity'] = "binary_sensor.visonic_" + self.pmSensorDev_t[zoneData-1].dname.lower()

                self.PanelLastEvent      = s
                self.PanelAlarmStatus    = "None" if alarmStatus is None else alarmStatus
                self.PanelTroubleStatus  = "None" if troubleStatus is None else troubleStatus

                log.debug("[handle_msgtypeA7]         System message " + s + "  alarmStatus " + self.PanelAlarmStatus + "   troubleStatus " + self.PanelTroubleStatus)

                #---------------------------------------------------------------------------------------
                # Update tamper and siren status
                # 0x06 is "Tamper", 0x07 is "Control Panel Tamper", 0x08 is "Tamper Alarm", 0x09 is "Tamper Alarm"
                tamper = eventType == 0x06 or eventType == 0x07 or eventType == 0x08 or eventType == 0x09

                # 0x1B is "Cancel Alarm", 0x21 is "Fire Restore", 0x4E is "Flood Alert Restore", 0x4A is "Gas Trouble Restore"
                cancel = eventType == 0x1B or eventType == 0x21 or eventType == 0x4A or eventType == 0x4E

                # 0x13 is "Delay Restore", 0x0E is "Confirm Alarm"
                ignore = eventType == 0x13 or eventType == 0x0E

                if tamper:
                    pmTamperActive = self._getTimeFunction()
                    datadict["Tamper"] = True

                # no clauses as if siren gets true again then keep updating self.pmSirenActive with the time
                if siren: # and not self.pmPanicAlarmSilent:
                    self.pmSirenActive = self._getTimeFunction()
                    datadict["Siren"] = True
                    log.debug("[handle_msgtypeA7] ******************** Alarm Active *******************")

                # cancel alarm and the alarm has been triggered
                if cancel and self.pmSirenActive is not None:  # Cancel Alarm
                    self.pmSirenActive = None
                    datadict["Siren"] = False
                    log.debug("[handle_msgtypeA7] ******************** Alarm Cancelled ****************")

                # Siren has been active but it is no longer active (probably timed out and has then been disarmed)
                if not ignore and not siren and self.pmSirenActive is not None:  # Alarm Timed Out ????
                    self.pmSirenActive = None
                    datadict["Siren"] = False
                    log.debug("[handle_msgtypeA7] ******************** Alarm Not Sounding ****************")

                # INTERFACE Indicate whether siren active

                log.debug("[handle_msgtypeA7]           self.pmSirenActive={0}   siren={1}   eventType={2}   self.pmPanicAlarmSilent={3}   tamper={4}".format(self.pmSirenActive, siren, hex(eventType), self.pmPanicAlarmSilent, tamper) )

                #---------------------------------------------------------------------------------------
                if eventType == 0x60: # system restart
                    datadict['Reset'] = True
                    log.warning("[handle_msgtypeA7]          Panel has been reset. Don't do anything and the comms might reconnect and magically continue")
                    self._sendResponseEvent ( PyCondition.PANEL_RESET , datadict )   # push changes through to the host, the panel itself has been reset

            if pmTamperActive is not None:
                log.debug("[handle_msgtypeA7] ******************** Tamper Triggered *******************")
                self._sendResponseEvent(PyCondition.PANEL_TAMPER_ALARM)  # push changes through to the host to get it to update, tamper is active!

            if self.pmSirenActive is not None:
                self._sendResponseEvent(PyCondition.PANEL_UPDATE_ALARM_ACTIVE, datadict)  # push changes through to the host to get it to update, alarm is active!!!!!!!!!
            else:
                self._sendResponseEvent(PyCondition.PANEL_UPDATE, datadict)  # push changes through to the host to get it to update

            self.PanelLastEventData = datadict

        else:  ## message count is more than 4
            log.warning("[handle_msgtypeA7]      A7 message contains too many messages to process : {0}   data={1}".format(msgCnt, self._toString(data)))
        return True

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

            if self.AutoSyncTime:  # should we sync time between the HA and the Alarm Panel
                t = self._getTimeFunction()
                if t.year > 2020:
                    duration = dt - t                              # Get Time Difference, timedelta
                    duration_in_s = abs(duration.total_seconds())  # Convert to seconds (and make it a positive value)
                    log.debug("[handle_msgtypeAB]    Local time is {0}      time difference {1} seconds".format(t, duration_in_s))
                    
                    if duration_in_s > 20:                         # More than 20 seconds difference
                        year = t.year - 2000
                        values = [t.second, t.minute, t.hour, t.day, t.month, year]
                        timePdu = bytearray(values)
                        log.debug("[handle_msgtypeAB]        Setting Time " + self._toString(timePdu))
                        self._sendCommand("MSG_SETTIME", options=[3, timePdu])
                else:
                    log.debug("[handle_msgtypeAB] Please correct your local time.")

        elif subType == 3:  # keepalive message
            # Example 0D AB 03 00 1E 00 31 2E 31 35 00 00 43 2A 0A
            log.debug("[handle_msgtypeAB] ***************************** Got PowerLink Keep-Alive ****************************")
            # It is possible to receive this between enrolling (when the panel accepts the enroll successfully) and the EPROM download
            #     I suggest we simply ignore it
            if self.pmPowerlinkModePending:
                log.debug("[handle_msgtypeAB]         Got alive message while Powerlink mode pending, going to full powerlink and calling Restore")
                self.pmPowerlinkMode = True
                self.pmPowerlinkModePending = False
                self.PanelMode = PyPanelMode.POWERLINK  # it is truly in powerlink now we are receiving powerlink alive messages from the panel
                self._triggerRestoreStatus()
                self._dumpSensorsToLogFile()

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

    # Only Powermasters send this message
    def handle_msgtypeB0(self, data) -> bool:  # PowerMaster Message
        """ MsgType=B0 - Panel PowerMaster Message """
        #       Sources of B0 Code
        #                  https://github.com/nlrb/com.visonic.powermax/blob/master/node_modules/powermax-api/lib/handlers.js
        #        msgSubTypes = [0x00, 0x01, 0x02, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x11, 0x12, 0x13, 0x14, 0x15,
        #                       0x16, 0x18, 0x19, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x21, 0x24, 0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x32, 0x33, 0x34, 0x38, 0x39, 0x3A ]
        # Format: <Type> <SubType> <Length> <Data> <0x43>

        # From the same user
        #  Received PowerMaster30 message 3/36 (len = 26)    full data = 03 24 1a ff 08 ff 15 00 00 00 00 00 00 00 00 26 35 12 15 03 00 14 03 01 00 81 00 00 0a 43
        #  Received PowerMaster33 message 3/36 (len = 26)    full data = 03 24 1a ff 08 ff 15 0e 00 00 00 00 00 00 00 19 27 14 11 04 15 14 07 01 05 81 00 00 cb 43 
        #  Received PowerMaster33 message 3/36 (len = 26)    full data = 03 24 1a ff 08 ff 15 0e 00 00 00 00 00 00 00 22 27 14 11 04 15 14 07 01 00 81 00 00 d6 43
        #  Received PowerMaster33 message 3/36 (len = 26)    full data = 03 24 1a ff 08 ff 15 0e 00 00 00 00 00 00 00 2b 19 0d 01 01 00 14 07 01 00 81 00 00 29 43   siren is sounding according to user

        #  Received PowerMaster33 message 3/59 (len = 11)    full data = 03 3b 0b ff 28 ff 06 03 06 00 01 05 13 ca 43
        
        pushChange = False

        msgType = data[0]
        subType = data[1]
        msgLen  = data[2]
        log.debug("[handle_msgtypeB0] Received {0} message {1}/{2} (len = {3})    full data = {4}".format(self.PanelModel or "UNKNOWN", msgType, subType, msgLen, self._toString(data)))
        
        # The data block should contain <Type> <SubType> <Length> <Data> <0x43>
        # Therefore the data length should be 4 bytes less then the length of the data block
        if len(data) != msgLen + 4:
            log.debug("[handle_msgtypeB0]              Invalid Length, not processing")
            # Do not process this B0 message as it seems to be incorrect
            return False

        if self.BZero_Enable and msgType == 0x03 and subType == 0x39:
            # Movement detected (probably)
            #  Received PowerMaster10 message 3/57 (len = 6)    full data = 03 39 06 ff 08 ff 01 24 0b 43
            
            # From the same panel:
            #  Received PowerMaster33 message 3/57 (len = 6)    full data = 03 39 06 ff 08 ff 01 59 b8 43
            #  Received PowerMaster33 message 3/57 (len = 6)    full data = 03 39 06 ff 08 ff 01 59 ba 43  # PM33 this maybe after a siren is cancelled (disarmed)
            #  Received PowerMaster33 message 3/57 (len = 8)    full data = 03 39 08 ff 08 ff 03 18 24 4b cc 43
            #  Received PowerMaster33 message 3/57 (len = 6)    full data = 03 39 06 ff 08 ff 01 59 dd 43
            
            #  Received PowerMaster30 message 3/57 (len = 8)    full data = 03 39 08 ff 08 ff 03 18 24 4b 90 43
            log.debug("[handle_msgtypeB0]      Sending special {0} Commands to the panel".format(self.PanelModel or "UNKNOWN"))
            self._sendCommand("MSG_POWERMASTER", options=[2, pmSendMsgB0_t["ZONE_STAT1"]])  # This asks the panel to send 03 04 messages
            # self._sendCommand("MSG_POWERMASTER", options = [2, pmSendMsgB0_t["ZONE_STAT2"]])    # This asks the panel to send 03 07 messages
            # self._sendCommand("MSG_POWERMASTER", options = [2, pmSendMsgB0_t["ZONE_STAT3"]])    # This asks the panel to send 03 18 messages

        if self.BZero_Enable and msgType == 0x03 and subType == 0x04:
            log.debug("[handle_msgtypeB0]         Received {0} message, continue".format(self.PanelModel or "UNKNOWN"))
            # Zone information (probably)
            #  Received PowerMaster10 message 3/4 (len = 35)    full data = 03 04 23 ff 08 03 1e 26 00 00 01 00 00 <24 * 00> 0c 43
            #  Received PowerMaster30 message 3/4 (len = 69)    full data = 03 04 45 ff 08 03 40 11 08 08 04 08 08 <58 * 00> 89 43
            
            #  Received PowerMaster33 message 3/4 (len = 69)    full data = 03 04 45 ff 08 03 40 11 11 15 15 11 15 15 11 <56 * 00> b9 43  # user has 8 sensors, Z01 to Z08
            #  Received PowerMaster33 message 3/4 (len = 69)    full data = 03 04 45 ff 08 03 40 11 11 15 15 11 15 15 11 <56 * 00> bb 43
            #  Received PowerMaster33 message 3/4 (len = 69)    full data = 03 04 45 ff 08 03 40 15 04 11 08 04 08 08 08 <56 * 00> c9 43
            #  Received PowerMaster33 message 3/4 (len = 69)    full data = 03 04 45 ff 08 03 40 15 04 11 08 04 08 08 08 <56 * 00> cd 43
            
            interval = self._getTimeFunction() - self.lastRecvOfMasterMotionData
            self.lastRecvOfMasterMotionData = self._getTimeFunction()
            td = timedelta(seconds=self.BZero_MinInterval)  #
            if interval > td:
                # more than 30 seconds since the last B0 03 04 message so reset variables ready
                # also, it should enter here first time around as self.lastRecvOfMasterMotionData should be 100 seconds ago
                log.debug("[handle_msgtypeB0]         03 04 Data Reset")
                self.zoneNumberMasterMotion = False
                self.firstRecvOfMasterMotionData = self._getTimeFunction()
                self.zoneDataMasterMotion = data.copy()
            elif not self.zoneNumberMasterMotion:
                log.debug("[handle_msgtypeB0]         Checking if time delay is within {0} seconds".format(self.BZero_MaxWaitTime))
                interval = self._getTimeFunction() - self.firstRecvOfMasterMotionData
                td = timedelta(seconds=self.BZero_MaxWaitTime)  #
                if interval <= td and len(data) == len(self.zoneDataMasterMotion):
                    # less than or equal to 5 seconds since the last valid trigger message, and the data messages are the same length
                    zoneLen = data[6] # The length of the zone data (64 for PM30, 30 for PM10)
                    log.debug("[handle_msgtypeB0]         Received {0} message, zone length = {1}".format(self.PanelModel or "UNKNOWN", zoneLen))
                    for z in range(0, zoneLen):
                        # Check if the zone exists and it has to be a PIR
                        # do we already know about the sensor from the EPROM decode
                        if z in self.pmSensorDev_t:
                            # zone z
                            log.debug("[handle_msgtypeB0]           Checking Zone {0}".format(z))
                            if self.pmSensorDev_t[z].stype == PySensorType.MOTION:
                                # log.debug("[handle_msgtypeB0]             And its motion")
                                s1 = data[7 + z]
                                s2 = self.zoneDataMasterMotion[7 + z]
                                log.debug("[handle_msgtypeB0]             Zone {0}  Motion State Before {1}   After {2}".format(z, s2, s1))
                                if s1 != s2:
                                    log.debug("[handle_msgtypeB0]             Pre-Triggered Motion Detection to set B0 zone")
                                    self.zoneNumberMasterMotion = True   # this means we wait at least 'self.BZero_MinInterval' seconds for the next trigger
                                    if not self.pmSensorDev_t[z].triggered:
                                        log.debug("[handle_msgtypeB0]             Triggered Motion Detection")
                                        self.pmSensorDev_t[z].triggertime = self._getTimeFunction()
                                        self.pmSensorDev_t[z].triggered = True
                                        #self.pmSensorDev_t[z].pushChange()
                                        pushChange = True
                            # else:
                            #    s = data[7 + z]
                            #    log.debug("[handle_msgtypeB0]           Zone {0}  is not a motion stype   State = {1}".format(z, s))

        if msgType == 0x03 and subType == 0x18:
            # Open/Close information (probably)
            zoneLen = data[6] # The length of the zone data (64 for PM30, 30 for PM10)
            log.debug("[handle_msgtypeB0]       Received {0} message, open/close information (probably), zone length = {1}".format(self.PanelModel or "UNKNOWN", zoneLen))
            for z in range(0, zoneLen):
                if z in self.pmSensorDev_t:
                    s = data[7 + z]
                    log.debug("[handle_msgtypeB0]           Zone {0}  State {1}".format(z, s))

        if msgType == 0x03 and subType == 0x07:
            #  Received PowerMaster10 message 3/7 (len = 35)    full data = 03 07 23 ff 08 03 1e 03 00 00 03 00 00 <24 * 00> 0d 43
            #  Received PowerMaster30 message 3/7 (len = 69)    full data = 03 07 45 ff 08 03 40 03 03 03 03 03 03 <58 * 00> 92 43
            # Unknown information
            zoneLen = data[6] # The length of the zone data (64 for PM30, 30 for PM10)
            log.debug("[handle_msgtypeB0]       Received {0} message, 03 07 information, zone length = {1}".format(self.PanelModel or "UNKNOWN", zoneLen))
            for z in range(0, zoneLen):
                if z in self.pmSensorDev_t:
                    s = data[7 + z]
                    log.debug("[handle_msgtypeB0]           Zone {0}  State {1}".format(z, s))
        return pushChange

    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================
    # ================== Functions below this are utility functions to support the interface ================
    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================

    def _dumpSensorsToLogFile(self):
        log.debug("=============================================== Display Status ===============================================")
        for key, sensor in self.pmSensorDev_t.items():
            log.debug("     key {0:<2} Sensor {1}".format(key, sensor))
        log.debug("   Model {: <18}     PowerMaster {: <18}     LastEvent {: <18}     Ready   {: <13}".format(self.PanelModel,
                                        'Yes' if self.PowerMaster else 'No', self.PanelLastEvent, 'Yes' if self.PanelReady else 'No'))
        log.debug("   Mode  {: <18}     Status      {: <18}     Armed     {: <18}     Trouble {: <13}     AlarmStatus {: <12}".format(self.PanelMode.name.replace("_", " ").title(), self.PanelStatusText,
                                        'Yes' if self.PanelArmed else 'No', self.PanelTroubleStatus, self.PanelAlarmStatus))
        log.debug("==============================================================================================================")

    def _createPin(self, pin : str):
        # Pin is None when either we can perform the action without a code OR we're in Powerlink/StandardPlus and have the pin code to use
        # Other cases, the pin must be set
        if pin is None:
            if self.pmGotUserCode:
                bpin = self.pmPincode_t[0]   # if self.pmGotUserCode, then we downloaded the pin codes. Use the first one
            else:
                bpin = bytearray.fromhex("00 00")
        elif len(pin) == 4:
            bpin = bytearray.fromhex(pin[0:2] + " " + pin[2:4])
        else:
            # default to setting it to "0000" and see what happens when its sent to the panel
            bpin = bytearray.fromhex("00 00")
        return bpin

# Event handling and externally callable client functions (plus updatestatus)
class VisonicProtocol(PacketHandling, PyPanelInterface):
    """ Event Handling """

    def __init__(self, *args, client=None, **kwargs) -> None:
        """Add VisonicProtocol specific initialization."""
        super().__init__(*args, **kwargs)

        if client is not None:
            #log.debug("[VisonicProtocol]  client is not None, calling setPyVisonic " + str(type(client)))
            client.setPyVisonic(self)
        else:
            log.debug("[VisonicProtocol]  client is None")

    ############################################################################################################
    ############################################################################################################
    ############################################################################################################
    ######################## The following functions are called from the client ################################
    ############################################################################################################
    ############################################################################################################
    ############################################################################################################

    def shutdownOperation(self):
        if not self.suspendAllOperations:
            self.suspendAllOperations = True
            if self.transport is not None:
                self.transport.close()
        self.transport = None

    def isSirenActive(self) -> bool:
        return self.pmSirenActive is not None

    def getPanelStatusCode(self) -> PyPanelStatus:
        return self.PanelStatusCode

    def isPowerMaster(self) -> bool:
        return self.PowerMaster

    def getPanelMode(self) -> PyPanelMode:
        return self.PanelMode

    # Get a sensor by the key reference (integer)
    #   Return : The SensorDevice class of the provided refernce in s or None if not found
    #            I don't think it can be immutable in python but at least changes in either will not affect the other
    def getSensor(self, sensor) -> PySensorDevice:
        """ Return the sensor."""
        s = sensor - 1
        if s in self.pmSensorDev_t:
            return self.pmSensorDev_t[s]
        return None

    def populateDictionary(self) -> dict:
        datadict = {}
        datadict["PanelReady"] = self.PanelReady
        datadict["OpenZones"] = []
        datadict["Bypass"] = []
        datadict["Tamper"] = []
        datadict["ZoneTamper"] = []
        for key in self.pmSensorDev_t:
            entname = "binary_sensor.visonic_" + self.pmSensorDev_t[key].dname.lower()
            if self.pmSensorDev_t[key].status:
                datadict["OpenZones"].append(entname)
            if self.pmSensorDev_t[key].tamper:
                datadict["Tamper"].append(entname)
            if self.pmSensorDev_t[key].bypass:
                datadict["Bypass"].append(entname)
            if self.pmSensorDev_t[key].ztamper:
                datadict["ZoneTamper"].append(entname)
        return datadict

    def getPanelStatus(self) -> dict:
        # log.debug("In visonic getpanelstatus")
        d = {
            "Panel Mode": self.getPanelMode().name.replace("_", " ").title(),
            "Protocol Version": PLUGIN_VERSION,
            "Watchdog Timeout (Total)": self.WatchdogTimeout,
            "Watchdog Timeout (Past 24 Hours)": self.WatchdogTimeoutPastDay,
            "Download Timeout": self.DownloadTimeout,
            "Download Retries": self.pmDownloadRetryCount,
            "Panel Last Event": self.PanelLastEvent,
            "Panel Last Event Data": self.PanelLastEventData,
            "Panel Alarm Status": self.PanelAlarmStatus,
            "Panel Trouble Status": self.PanelTroubleStatus,
            "Panel Siren Active": self.isSirenActive(),
            "Panel Status": self.PanelStatusText,
            "Panel Status Code": self.getPanelStatusCode().name.replace("_", " ").title(),
            "Panel Ready": "Yes" if self.PanelReady else "No",
            "Panel Alert In Memory": "Yes" if self.PanelAlertInMemory else "No",
            "Panel Trouble": "Yes" if self.PanelTrouble else "No",
            "Panel Bypass": "Yes" if self.PanelBypass else "No",
            "Panel Status Changed": "Yes" if self.PanelStatusChanged else "No",
            "Panel Alarm Event": "Yes" if self.PanelAlarmEvent else "No",
            "Panel Armed": "Yes" if self.PanelArmed else "No",
            "Power Master": "Yes" if self.isPowerMaster() else "No",
            "Panel Model": self.PanelModel,
            "Panel Type": self.PanelType,
            "Model Type": self.ModelType,
        }

        if len(self.PanelStatus) > 0:
            return {**d, **self.PanelStatus}
        return d

    # requestArm
    #       state is PanelCommand
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    def requestArm(self, state : PyPanelCommand, pin : str = "") -> PyCommandStatus:
        """ Send a request to the panel to Arm/Disarm """
        if not self.pmDownloadMode:
            bpin = self._createPin(pin)
            # Ensure that the state is valid
            if state in pmArmMode_t:
                armCode = bytearray()
                # Retrieve the code to send to the panel
                armCode.append(pmArmMode_t[state])
                self._addMessageToSendList("MSG_ARM", options=[3, armCode, 4, bpin])  #
                self._addMessageToSendList("MSG_BYPASSTAT")
                return PyCommandStatus.SUCCESS
            else:
                return PyCommandStatus.FAIL_INVALID_STATE
        return PyCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    def setX10(self, device : int, state : PyX10Command) -> PyCommandStatus:
        # This is untested
        # "MSG_X10PGM"      : VisonicCommand(bytearray.fromhex('A4 00 00 00 00 00 99 99 00 00 00 43'), None  , False, "X10 Data" ),
        #log.debug("[SendX10Command] Processing {0} {1}".format(device, type(device)))

        if not self.pmDownloadMode:
            if device >= 0 and device <= 15:
                log.debug("[SendX10Command]  Send X10 Command : id = " + str(device) + "   state = " + str(state))
                calc = 1 << device
                byteA = calc & 0xFF
                byteB = (calc >> 8) & 0xFF
                if state in pmX10State_t:
                    what = pmX10State_t[state]
                    self._addMessageToSendList("MSG_X10PGM", options=[6, what, 7, byteA, 8, byteB])
                    self._addMessageToSendList("MSG_BYPASSTAT")
                    return PyCommandStatus.SUCCESS
                else:
                    return PyCommandStatus.FAIL_INVALID_STATE
            else:
                return PyCommandStatus.FAIL_X10_PROBLEM
        return PyCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    # Individually arm/disarm the sensors
    #   This sets/clears the bypass for each sensor
    #       zone is the zone number 1 to 31 or 1 to 64
    #       bypassValue is a boolean ( True then Bypass, False then Arm )
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    #   Return : success or not
    #
    #   the MSG_BYPASSEN and MSG_BYPASSDI commands are the same i.e. command is A1
    #      byte 0 is the command A1
    #      bytes 1 and 2 are the pin
    #      bytes 3 to 6 are the Enable bits for the 32 zones
    #      bytes 7 to 10 are the Disable bits for the 32 zones
    #      byte 11 is 0x43
    def setSensorBypassState(self, sensor : int, bypassValue : bool, pin : str = "") -> PyCommandStatus:
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
                        self._addMessageToSendList("MSG_BYPASSEN", options=[1, bpin, 3, bypass])
                    else:
                        self._addMessageToSendList("MSG_BYPASSDI", options=[1, bpin, 7, bypass])
                    # request status to check success and update sensor variable
                    self._addMessageToSendList("MSG_BYPASSTAT")
                    return PyCommandStatus.SUCCESS
                else:
                    return PyCommandStatus.FAIL_INVALID_STATE
            else:
                return PyCommandStatus.FAIL_PANEL_CONFIG_PREVENTED
        return PyCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    # Get the Event Log
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    def getEventLog(self, pin : str = "") -> PyCommandStatus:
        """ Get Panel Event Log """
        log.debug("getEventLog")
        self.eventCount = 0
        self.pmEventLogDictionary = {}
        if not self.pmDownloadMode:
            bpin = self._createPin(pin)
            self._addMessageToSendList("MSG_EVENTLOG", options=[4, bpin])
            return PyCommandStatus.SUCCESS
        return PyCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS

    def setCallbackHandlers(self, event_callback : Callable = None, disconnect_callback : Callable = None, new_sensor_callback : Callable = None, new_switch_callback : Callable = None, panel_event_log_callback : Callable = None):
        # set the disconnection callback handler
        self.disconnect_callback = disconnect_callback
        # set the event callback handler
        self.event_callback = event_callback
        self.new_sensor_callback = new_sensor_callback
        self.new_switch_callback = new_switch_callback
        self.panel_event_log_callback = panel_event_log_callback

class dummyclient:
    def __init__(self, *args, **kwargs) -> None:
        self.visprotocol = None

    def setPyVisonic(self, pyvis):
        """ Set the pyvisonic connection. This is called from the library. """
        self.visprotocol = pyvis

    def getPyVisonic(self) -> VisonicProtocol:
        return self.visprotocol

    # ===================================================================================================================================================
    # ===================================================================================================================================================
    # ===================================================================================================================================================
    # ========================= Functions below this are to be called from Home Assistant ===============================================================
    # ============================= These functions are to be used to configure and setup the connection to the panel ===================================
    # ===================================================================================================================================================
    # ===================================================================================================================================================

async def async_wait_for_connection(dc : dummyclient, loop) -> VisonicProtocol:
    # Wait for the Protocol Handler to start and get going. Do it once without sending anything to the log file.
    if dc.getPyVisonic() is None:
        await asyncio.sleep(1.0)

    count = 4
    while dc.getPyVisonic() is None and count > 0:
        log.debug("Waiting for Protocol Handler to Start")
        count = count - 1
        await asyncio.sleep(1.0)

    return dc.getPyVisonic()


# Create a connection using asyncio using an ip and port
async def async_create_tcp_visonic_connection(address, port, protocolvp=VisonicProtocol, panelConfig=None, loop=None):
    """Create Visonic manager class, returns tcp transport coroutine."""
    dc = dummyclient()
    loop = loop if loop else asyncio.get_event_loop()

    # use default protocol if not specified
    protocol = partial(
        protocolvp,
        client=dc,
        panelConfig=panelConfig,
        loop=loop,
    )

    address = address
    port = int(port)

    sock = None
    try:
        log.debug("Setting TCP socket Options")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setblocking(1)  # Set blocking to on, this is the default but just make sure
        sock.settimeout(1.0)  # set timeout to 1 second to flush the receive buffer
        sock.connect((address, port))

        # Flush the buffer, receive any data and dump it
        try:
            dummy = sock.recv(10000)  # try to receive 100 bytes
            log.debug("Buffer Flushed and Received some data!")
        except socket.timeout:  # fail after 1 second of no activity
            #log.debug("Buffer Flushed and Didn't receive data! [Timeout]")
            pass

        # set the timeout to infinite
        sock.settimeout(None)

        # create the connection to the panel as an asyncio protocol handler and then set it up in a task
        conn = loop.create_connection(protocol, sock=sock)
        visonicTask = loop.create_task(conn)

        return visonicTask, await async_wait_for_connection(dc, loop)

    except socket.error as _:
        err = _
        log.debug("Setting TCP socket Options Exception {0}".format(err))
        if sock is not None:
            sock.close()
    return None, None


# Create a connection using asyncio through a linux port (usb or rs232)
async def async_create_usb_visonic_connection(path, baud="9600", protocolvp=VisonicProtocol, panelConfig=None, loop=None):
    """Create Visonic manager class, returns rs232 transport coroutine."""
    from serial_asyncio import create_serial_connection

    dc = dummyclient()
    loop=loop if loop else asyncio.get_event_loop()

    log.debug("Setting USB Options")
    # use default protocol if not specified
    protocol = partial(
        protocolvp,
        client=dc,
        panelConfig=panelConfig,
        loop=loop,
    )

    # setup serial connection
    path = path
    baud = int(baud)
    try:
        # create the connection to the panel as an asyncio protocol handler and then set it up in a task
        conn = create_serial_connection(loop, protocol, path, baud)
        visonicTask = loop.create_task(conn)

        return visonicTask, await async_wait_for_connection(dc, loop)
    except:
        log.debug("Setting USB Options Exception")
    return None, None


def wait_for_connection(dc : dummyclient) -> VisonicProtocol:
    # Wait for the Protocol Handler to start and get going. Do it once without sending anything to the log file.
    if dc.getPyVisonic() is None:
        time.sleep(0.2)
    count = 4
    while dc.getPyVisonic() is None and count > 0:
        log.debug("Waiting for Protocol Handler to Start")
        time.sleep(0.5)
        count = count - 1
    return dc.getPyVisonic()

# Create a connection using asyncio using an ip and port
def create_tcp_visonic_connection(
    address, port, protocolvp=VisonicProtocol, panelConfig=None, loop=None):
    """Create Visonic manager class, returns tcp transport coroutine."""

    dc = dummyclient()

    # use default protocol if not specified
    protocol = partial(
        protocolvp,
        client=dc,
        panelConfig=panelConfig,
        loop=loop if loop else asyncio.get_event_loop(),
    )

    address = address
    port = int(port)

    sock = None
    try:
        log.debug("Setting TCP socket Options")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setblocking(1)  # Set blocking to on, this is the default but just make sure
        sock.settimeout(1.0)  # set timeout to 1 second to flush the receive buffer
        sock.connect((address, port))

        # Flush the buffer, receive any data and dump it
        try:
            dummy = sock.recv(10000)  # try to receive 100 bytes
            log.debug("Buffer Flushed and Received some data!")
        except socket.timeout:  # fail after 1 second of no activity
            #log.debug("Buffer Flushed and Didn't receive data! [Timeout]")
            pass

        # set the timeout to infinite
        sock.settimeout(None)

        # create the connection to the panel as an asyncio protocol handler and then set it up in a task
        conn = loop.create_connection(protocol, sock=sock)
        visonicTask = loop.create_task(conn)
        return visonicTask, wait_for_connection(dc)

    except socket.error as _:
        err = _
        log.debug("Setting TCP socket Options Exception {0}".format(err))
        if sock is not None:
            sock.close()
    return None, None


# Create a connection using asyncio through a linux port (usb or rs232)
def create_usb_visonic_connection(
    path, baud=9600, protocolvp=VisonicProtocol, panelConfig=None, loop=None
):
    """Create Visonic manager class, returns rs232 transport coroutine."""
    from serial_asyncio import create_serial_connection

    dc = dummyclient()

    log.debug("Setting USB Options")
    # use default protocol if not specified
    protocol = partial(
        protocolvp,
        client=dc,
        panelConfig=panelConfig,
        loop=loop if loop else asyncio.get_event_loop(),
    )

    # setup serial connection
    path = path
    baud = int(baud)
    try:
        # create the connection to the panel as an asyncio protocol handler and then set it up in a task
        conn = create_serial_connection(loop, protocol, path, baud)
        visonicTask = loop.create_task(conn)
        return visonicTask, wait_for_connection(dc)
    except:
        log.debug("Setting USB Options Exception")
    return None, None


class ElapsedFormatter:
    def __init__(self):
        self.start_time = time.time()

    def format(self, record):
        elapsed_seconds = record.created - self.start_time
        # using timedelta here for convenient default formatting
        elapsed = timedelta(seconds=elapsed_seconds)
        return "{} <{: >5}> {: >8}   {}".format(elapsed, record.lineno, record.levelname, record.getMessage())

def setupLocalLogger(level: str = "WARNING", logfile = False):
    # add custom formatter to root logger
    formatter = ElapsedFormatter()
    shandler = logging.StreamHandler(stream=sys.stdout)
    shandler.setFormatter(formatter)
    if logfile:
        fhandler = logging.FileHandler("log.txt", mode="w")
        fhandler.setFormatter(formatter)
        log.addHandler(fhandler)

    log.propagate = False

    log.addHandler(shandler)

    # level = logging.getLevelName('INFO')
    level = logging.getLevelName(level)  # INFO, DEBUG
    log.setLevel(level)

# Turn on auto code formatting when using black
# fmt: on
