"""Asyncio protocol implementation of Visonic PowerMaster/PowerMax.
  Based on the DomotiGa and Vera implementation:

  Credits:
    Initial setup by Wouter Wolkers and Alexander Kuiper.
    Thanks to everyone who helped decode the data.

  Converted to Python module by Wouter Wolkers and David Field
  
  The Component now follows the new HA file structure and uses asyncio  
"""

#################################################################
# PowerMax/Master send and receive messages
#################################################################

#################################################################
# Version 0.2.0 is not like any other visonic interface 
#   It downloads from the panel first and then tries powerlink
#################################################################

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

from collections import defaultdict
from datetime import datetime
from time import sleep
from datetime import timedelta
from dateutil.relativedelta import *
from functools import partial
from typing import Callable, List
from collections import namedtuple

PLUGIN_VERSION = "0.3.3"

# Maximum number of CRC errors on receiving data from the alarm panel before performing a restart
MAX_CRC_ERROR = 5

# Maximum number of received messages that are exactly the same from the alarm panel before performing a restart
SAME_PACKET_ERROR = 20

# If we are waiting on a message back from the panel or we are explicitly waiting for an acknowledge,
#    then wait this time before resending the message.
#  Note that not all messages will get a resend, only ones waiting for a specific response and/or are blocking on an ack
RESEND_MESSAGE_TIMEOUT = timedelta(seconds=100)

# We must get specific messages from the panel, if we do not in this time period (seconds) then trigger a restore/status request
WATCHDOG_TIMEOUT = 120

# If there has been a watchdog timeout this many times then go to standard mode
WATCHDOG_MAXIMUM_EVENTS = 10

RESPONSE_TIMEOUT = 10

# If a message has not been sent to the panel in this time (seconds) then send an I'm alive message
KEEP_ALIVE_PERIOD = 25  # Seconds

# When we send a download command wait for DownloadMode to become false.
#   If this timesout then I'm not sure what to do, maybe we really need to just start again
#   In Vera, if we timeout we just assume we're in Standard mode by default
DOWNLOAD_TIMEOUT = 180

# Number of seconds delay between trying to achieve EPROM download
DOWNLOAD_RETRY_DELAY = 240

# Number of seconds delay between trying to achieve powerlink (must have achieved download first)
POWERLINK_RETRY_DELAY = 240

PanelSettings = {
   "MotionOffDelay"       : 120,
   "OverrideCode"         : "",
   "DownloadCode"         : "",
   "PluginLanguage"       : "EN",
   "ArmWithoutCode"       : False,
   "PluginDebug"          : False,
   "ForceStandard"        : False,
   "ForceKeypad"          : False,
   "ResetCounter"         : 0,
   "AutoSyncTime"         : True,
   "EnableRemoteArm"      : False,
   "EnableRemoteDisArm"   : False,   #
   "EnableSensorBypass"   : False,   # Does user allow sensor bypass / arming

   "B0_Enable"            : False,
   "B0_Min_Interval_Time" : 30,
   "B0_Max_Wait_Time"     : 5
}

PanelStatus = {
   "PluginVersion"         : PLUGIN_VERSION,
   "Comm Exception Count"  : 0,
   "Mode"                  : "Unknown",
   "Watchdog Timeout"      : 0,
   "Download Timeout"      : 0,

# A7 message decode
   "Panel Last Event"      : "None",
   "Panel Alarm Status"    : "None",
   "Panel Trouble Status"  : "None",
   "Panel Siren Active"    : 'No',

# A5 message decode
   "Panel Status"          : "Unknown",
   "Panel Status Code"     : -1,
   "Panel Ready"           : 'No',
   "Panel Alert In Memory" : 'No',
   "Panel Trouble"         : 'No',
   "Panel Bypass"          : 'No',
   "Panel Status Changed"  : 'No',
   "Panel Alarm Event"     : 'No',
   "Panel Armed"           : 'No',

# 3C message decode
   "Power Master"          : 'No',
   
# Define model type to be unknown
   "Model"                 : "Unknown"
}

# Messages left to work out
#      Panel sent 0d 22 fd 0a 01 16 15 00 0d 00 00 00 9c 0a    No idea what this means

# use a named tuple for data and acknowledge
#    replytype   is a message type from the Panel that we should get in response
#    waitforack, if True means that we should wait for the acknowledge from the Panel before progressing
#    waittime    a number of seconds after sending the command to wait before sending the next command 
VisonicCommand = collections.namedtuple('VisonicCommand', 'data replytype waitforack waittime, msg')
pmSendMsg = {
   "MSG_EVENTLOG"    : VisonicCommand(bytearray.fromhex('A0 00 00 00 99 99 00 00 00 00 00 43'), [0xA0], False, 0.0, "Retrieving Event Log" ), 
   "MSG_ARM"         : VisonicCommand(bytearray.fromhex('A1 00 00 00 99 99 00 00 00 00 00 43'), None  ,  True, 0.0, "(Dis)Arming System" ),
   "MSG_STATUS"      : VisonicCommand(bytearray.fromhex('A2 00 00 00 00 00 00 00 00 00 00 43'), [0xA5],  True, 0.0, "Getting Status" ),
   "MSG_BYPASSTAT"   : VisonicCommand(bytearray.fromhex('A2 00 00 20 00 00 00 00 00 00 00 43'), [0xA5], False, 0.0, "Bypassing" ),
   "MSG_ZONENAME"    : VisonicCommand(bytearray.fromhex('A3 00 00 00 00 00 00 00 00 00 00 43'), [0xA3, 0xA3, 0xA3, 0xA3],  True, 0.0, "Requesting Zone Names" ),
   "MSG_X10PGM"      : VisonicCommand(bytearray.fromhex('A4 00 00 00 00 00 99 99 99 00 00 43'), None  , False, 0.0, "X10 Data" ),
   "MSG_ZONETYPE"    : VisonicCommand(bytearray.fromhex('A6 00 00 00 00 00 00 00 00 00 00 43'), [0xA6, 0xA6, 0xA6, 0xA6],  True, 0.0, "Requesting Zone Types" ),
   "MSG_U1"          : VisonicCommand(bytearray.fromhex('A7 04 00 00 00 00 00 00 00 00 00 43'), None  ,  True, 0.0, "Unknown 1" ),
   "MSG_U2"          : VisonicCommand(bytearray.fromhex('A8 04 00 00 00 00 00 00 00 00 00 43'), None  ,  True, 0.0, "Unknown 2" ),
   "MSG_U3"          : VisonicCommand(bytearray.fromhex('A9 04 00 00 00 00 00 00 00 00 00 43'), None  ,  True, 0.0, "Unknown 3" ),
   "MSG_BYPASSEN"    : VisonicCommand(bytearray.fromhex('AA 99 99 12 34 56 78 00 00 00 00 43'), None  , False, 0.0, "BYPASS Enable" ),
   "MSG_BYPASSDI"    : VisonicCommand(bytearray.fromhex('AA 99 99 00 00 00 00 12 34 56 78 43'), None  , False, 0.0, "BYPASS Disable" ),
   "MSG_ALIVE"       : VisonicCommand(bytearray.fromhex('AB 03 00 00 00 00 00 00 00 00 00 43'), None  ,  True, 0.0, "I'm Alive Message To Panel" ),
   "MSG_RESTORE"     : VisonicCommand(bytearray.fromhex('AB 06 00 00 00 00 00 00 00 00 00 43'), [0xA5],  True, 0.0, "Restore PowerMax/Master Connection" ), # It can take multiple of these to put the panel back in to powerlink
   "MSG_ENROLL"      : VisonicCommand(bytearray.fromhex('AB 0A 00 00 99 99 00 00 00 00 00 43'), None  ,  True, 0.0, "Auto-Enroll of the PowerMax/Master" ),   # should get a reply of [0xAB] but its not guaranteed
   "MSG_INIT"        : VisonicCommand(bytearray.fromhex('AB 0A 00 01 00 00 00 00 00 00 00 43'), None  ,  True, 8.0, "Initializing PowerMax/Master PowerLink Connection" ),
   "MSG_X10NAMES"    : VisonicCommand(bytearray.fromhex('AC 00 00 00 00 00 00 00 00 00 00 43'), [0xAC], False, 0.0, "Requesting X10 Names" ),
   # Command codes (powerlink) do not have the 0x43 on the end and are only 11 values
   "MSG_DOWNLOAD"    : VisonicCommand(bytearray.fromhex('24 00 00 99 99 00 00 00 00 00 00')   , [0x3C], False, 0.0, "Start Download Mode" ),  # This gets either an acknowledge OR an Access Denied response
   "MSG_WRITE"       : VisonicCommand(bytearray.fromhex('3D 00 00 00 00 00 00 00 00 00 00')   , None  , False, 0.0, "Write Data Set" ),
   "MSG_DL"          : VisonicCommand(bytearray.fromhex('3E 00 00 00 00 B0 00 00 00 00 00')   , [0x3F],  True, 0.0, "Download Data Set" ),
   "MSG_SETTIME"     : VisonicCommand(bytearray.fromhex('46 F8 00 01 02 03 04 05 06 FF FF')   , None  , False, 0.0, "Setting Time" ),   # may not need an ack
   "MSG_SER_TYPE"    : VisonicCommand(bytearray.fromhex('5A 30 04 01 00 00 00 00 00 00 00')   , [0x33], False, 0.0, "Get Serial Type" ),
   # quick command codes to start and stop download/powerlink are a single value
   "MSG_START"       : VisonicCommand(bytearray.fromhex('0A')                                 , [0x0B], False, 0.0, "Start" ),    # waiting for STOP from panel for download complete 
   "MSG_STOP"        : VisonicCommand(bytearray.fromhex('0B')                                 , None  , False, 1.5, "Stop" ),     #
   "MSG_EXIT"        : VisonicCommand(bytearray.fromhex('0F')                                 , None  , False, 1.5, "Exit" ),
   # Acknowledges
   "MSG_ACK"         : VisonicCommand(bytearray.fromhex('02')                                 , None  , False, 0.0, "Ack" ),
   "MSG_ACKLONG"     : VisonicCommand(bytearray.fromhex('02 43')                              , None  , False, 0.0, "Ack Long" ),
   # PowerMaster specific
   "MSG_POWERMASTER" : VisonicCommand(bytearray.fromhex('B0 01 00 00 00 00 00 00 00 00 43')   , [0xB0], False, 0.0, "Powermaster Command" )
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
   "MSG_DL_TIME"         : bytearray.fromhex('F8 00 06 00'),   # could be F8 00 20 00
   "MSG_DL_COMMDEF"      : bytearray.fromhex('01 01 1E 00'),
   "MSG_DL_PHONENRS"     : bytearray.fromhex('36 01 20 00'),
   "MSG_DL_PINCODES"     : bytearray.fromhex('FA 01 10 00'),   # used
   "MSG_DL_INSTPIN"      : bytearray.fromhex('0C 02 02 00'),
   "MSG_DL_DOWNLOADPIN"  : bytearray.fromhex('0E 02 02 00'),
   "MSG_DL_PGMX10"       : bytearray.fromhex('14 02 D5 00'),   # used
   "MSG_DL_PARTITIONS"   : bytearray.fromhex('00 03 F0 00'),   # used
   "MSG_DL_PANELFW"      : bytearray.fromhex('00 04 20 00'),   # used
   "MSG_DL_SERIAL"       : bytearray.fromhex('30 04 08 00'),   # used
   "MSG_DL_ZONES"        : bytearray.fromhex('00 09 78 00'),   # used
   "MSG_DL_KEYFOBS"      : bytearray.fromhex('78 09 40 00'),
   "MSG_DL_2WKEYPAD"     : bytearray.fromhex('00 0A 08 00'),   # used
   "MSG_DL_1WKEYPAD"     : bytearray.fromhex('20 0A 40 00'),   # used
   "MSG_DL_SIRENS"       : bytearray.fromhex('60 0A 08 00'),   # used
   "MSG_DL_X10NAMES"     : bytearray.fromhex('30 0B 10 00'),   # used
   "MSG_DL_ZONENAMES"    : bytearray.fromhex('40 0B 1E 00'),   # used
   "MSG_DL_EVENTLOG"     : bytearray.fromhex('DF 04 28 03'),
   "MSG_DL_ZONESTR"      : bytearray.fromhex('00 19 00 02'),
   "MSG_DL_ZONESIGNAL"   : bytearray.fromhex('DA 09 1C 00'),   # used    # zone signal strength
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

# Message types we can receive with their length (None=unknown) and whether they need an ACK
PanelCallBack = collections.namedtuple("PanelCallBack", 'length ackneeded variablelength' )
pmReceiveMsg_t = {
   0x02 : PanelCallBack( None, False, False ),   # Ack
   0x06 : PanelCallBack( None, False, False ),   # Timeout. See the receiver function for ACK handling
   0x08 : PanelCallBack( None, False, False ),   # Access Denied
   0x0B : PanelCallBack( None,  True, False ),   # Stop --> Download Complete
   0x25 : PanelCallBack(   14,  True, False ),   # 14 Download Retry
   0x33 : PanelCallBack(   14,  True, False ),   # 14 Download Settings
   0x3C : PanelCallBack(   14,  True, False ),   # 14 Panel Info
   0x3F : PanelCallBack( None,  True,  True ),   # Download Info
   0xA0 : PanelCallBack(   15,  True, False ),   # 15 Event Log
   0xA3 : PanelCallBack(   15,  True, False ),   # 15 Zone Names
   0xA5 : PanelCallBack(   15,  True, False ),   # 15 Status Update       Length was 15 but panel seems to send different lengths
   0xA6 : PanelCallBack(   15,  True, False ),   # 15 Zone Types I think!!!!
   0xA7 : PanelCallBack(   15,  True, False ),   # 15 Panel Status Change
   0xAB : PanelCallBack(   15,  True, False ),   # 15 Enroll Request 0x0A  OR Ping 0x03      Length was 15 but panel seems to send different lengths
   0xAC : PanelCallBack(   15,  True, False ),   # 15 X10 Names ???
   0xB0 : PanelCallBack( None,  True, False ),
   0xF1 : PanelCallBack(    9, False, False )    # 9
}

pmReceiveMsgB0_t = {
   0x04 : "Zone status",
   0x18 : "Open/close status",
   0x39 : "Activity"
}

pmLogEvent_t = {
   "EN" : (
           "None", "Interior Alarm", "Perimeter Alarm", "Delay Alarm", "24h Silent Alarm", "24h Audible Alarm",
           "Tamper", "Control Panel Tamper", "Tamper Alarm", "Tamper Alarm", "Communication Loss", "Panic From Keyfob",
           "Panic From Control Panel", "Duress", "Confirm Alarm", "General Trouble", "General Trouble Restore",
           "Interior Restore", "Perimeter Restore", "Delay Restore", "24h Silent Restore", "24h Audible Restore",
           "Tamper Restore", "Control Panel Tamper Restore", "Tamper Restore", "Tamper Restore", "Communication Restore",
           "Cancel Alarm", "General Restore", "Trouble Restore", "Not used", "Recent Close", "Fire", "Fire Restore",
           "No Active", "Emergency", "No used", "Disarm Latchkey", "Panic Restore", "Supervision (Inactive)",
           "Supervision Restore (Active)", "Low Battery", "Low Battery Restore", "AC Fail", "AC Restore",
           "Control Panel Low Battery", "Control Panel Low Battery Restore", "RF Jamming", "RF Jamming Restore",
           "Communications Failure", "Communications Restore", "Telephone Line Failure", "Telephone Line Restore",
           "Auto Test", "Fuse Failure", "Fuse Restore", "Keyfob Low Battery", "Keyfob Low Battery Restore", "Engineer Reset",
           "Battery Disconnect", "1-Way Keypad Low Battery", "1-Way Keypad Low Battery Restore", "1-Way Keypad Inactive",
           "1-Way Keypad Restore Active", "Low Battery", "Clean Me", "Fire Trouble", "Low Battery", "Battery Restore",
           "AC Fail", "AC Restore", "Supervision (Inactive)", "Supervision Restore (Active)", "Gas Alert", "Gas Alert Restore",
           "Gas Trouble", "Gas Trouble Restore", "Flood Alert", "Flood Alert Restore", "X-10 Trouble", "X-10 Trouble Restore",
           "Arm Home", "Arm Away", "Quick Arm Home", "Quick Arm Away", "Disarm", "Fail To Auto-Arm", "Enter To Test Mode",
           "Exit From Test Mode", "Force Arm", "Auto Arm", "Instant Arm", "Bypass", "Fail To Arm", "Door Open",
           "Communication Established By Control Panel", "System Reset", "Installer Programming", "Wrong Password",
           "Not Sys Event", "Not Sys Event", "Extreme Hot Alert", "Extreme Hot Alert Restore", "Freeze Alert",
           "Freeze Alert Restore", "Human Cold Alert", "Human Cold Alert Restore", "Human Hot Alert",
           "Human Hot Alert Restore", "Temperature Sensor Trouble", "Temperature Sensor Trouble Restore",
           # new values partition models
           "PIR Mask", "PIR Mask Restore", "", "", "", "", "", "", "", "", "", "",
           "Alarmed", "Restore", "Alarmed", "Restore", "", "", "", "", "", "", "", "", "", "",
           "", "", "", "", "", "Exit Installer", "Enter Installer", "", "", "", "", "" ),
   "NL" : (
           "Geen", "In alarm", "In alarm", "In alarm", "In alarm", "In alarm",
           "Sabotage alarm", "Systeem sabotage", "Sabotage alarm", "Add user", "Communicate fout", "Paniekalarm",
           "Code bedieningspaneel paniek", "Dwang", "Bevestig alarm", "Successful U/L", "Probleem herstel",
           "Herstel", "Herstel", "Herstel", "Herstel", "Herstel",
           "Sabotage herstel", "Systeem sabotage herstel", "Sabotage herstel", "Sabotage herstel", "Communicatie herstel",
           "Stop alarm", "Algemeen herstel", "Brand probleem herstel", "Systeem inactief", "Recent close", "Brand", "Brand herstel",
           "Niet actief", "Noodoproep", "Remove user", "Controleer code", "Bevestig alarm", "Supervisie",
           "Supervisie herstel", "Batterij laag", "Batterij OK", "230VAC uitval", "230VAC herstel",
           "Controlepaneel batterij laag", "Controlepaneel batterij OK", "Radio jamming", "Radio herstel",
           "Communicatie mislukt", "Communicatie hersteld", "Telefoonlijn fout", "Telefoonlijn herstel",
           "Automatische test", "Zekeringsfout", "Zekering herstel", "Batterij laag", "Batterij OK", "Monteur reset",
           "Accu vermist", "Batterij laag", "Batterij OK", "Supervisie",
           "Supervisie herstel", "Lage batterij bevestiging", "Reinigen", "Probleem", "Batterij laag", "Batterij OK",
           "230VAC uitval", "230VAC herstel", "Supervisie", "Supervisie herstel", "Gas alarm", "Gas herstel",
           "Gas probleem", "Gas probleem herstel", "Lekkage alarm", "Lekkage herstel", "Probleem", "Probleem herstel",
           "Deelschakeling", "Ingeschakeld", "Snel deelschakeling", "Snel ingeschakeld", "Uitgezet", "Inschakelfout (auto)", "Test gestart",
           "Test gestopt", "Force aan", "Geheel in (auto)", "Onmiddelijk", "Overbruggen", "Inschakelfout",
           "Log verzenden", "Systeem reset", "Installateur programmeert", "Foutieve code", "Overbruggen" ),
   "FR" : (
           "Aucun", "Alarme Intérieure", "Alarme Périphérie", "Alarme Différée", "Alarme Silencieuse 24H", "Alarme Audible 24H",
           "Autoprotection", "Alarme Autoprotection Centrale", "Alarme Autoprotection", "Alarme Autoprotection", "Défaut de communication", "Alarme Panique Depuis Memclé",
           "Alarme Panique depuis Centrale", "Contrainte", "Confirmer l'alarme", "Perte de Communication", "Rétablissement Défaut Supervision",
           "Rétablissement Alarme Intérieure", "Rétablissement Alarme Périphérie", "Rétablissement Alarme Différée", "Rétablissement Alarme Silencieuse 24H", "Rétablissement Alarme Audible 24H",
           "Rétablissement Autoprotection", "Rétablissement Alarme Autoprotection Centrale", "Rétablissement Alarme Autoprotection", "Rétablissement Alarme Autoprotection", "Rétablissement des Communications",
           "Annuler Alarme", "Rétablissement Général", "Rétablissement Défaut", "Pas utilisé", "Evenement Fermeture Récente", "Alarme Incendie", "Rétablissement Alarme Incendie",
           "Non Actif", "Urgence", "Pas utilisé", "Désarmement Latchkey", "Rétablissement Alarme Panique", "Défaut Supervision (Inactive)",
           "Rétablissement Supervision (Active)", "Batterie Faible", "Rétablissement Batterie Faible", "Coupure Secteur", "Rétablissement Secteur",
           "Batterie Centrale Faible", "Rétablissement Batterie Centrale Faible", "Détection Brouillage Radio", "Rétablissement Détection Brouillage Radio",
           "Défaut Communication", "Rétablissement Communications", "Défaut Ligne Téléphonique", "Rétablissement Ligne Téléphonique",
           "Auto Test", "Coupure Secteur/Fusible", "Rétablissement Secteur/Fusible", "Memclé Batterie Faible", "Rétablissement Memclé Batterie Faible", "Réinitialisation Technicien",
           "Batterie Déconnectée ", "Clavier/Télécommande Batterie Faible", "Rétablissement Clavier/Télécommande Batterie Faible", "Clavier/Télécommande Inactif",
           "Rétablissement Clavier/Télécommande Actif", "Batterie Faible", "Nettoyage Détecteur Incendie", "Alarme incendie", "Batterie Faible", "Rétablissement Batterie",
           "Coupure Secteur", "Rétablissement Secteur", "Défaut Supervision (Inactive)", "Rétablissement Supervision (Active)", "Alarme Gaz", "Rétablissement Alarme Gaz",
           "Défaut Gaz", "Rétablissement Défaut Gaz", "Alarme Inondation", "Rétablissement Alarme Inondation", "Défaut X-10", "Rétablissement Défaut X-10",
           "Armement Partiel", "Armement Total", "Armement Partiel Instantané", "Armement Total Instantané", "Désarmement", "Echec d'armement", "Entrer dans Mode Test",
           "Sortir du Mode Test", "Fermeture Forcée", "Armement Automatique", "Armement Instantané", "Bypass", "Echec d'Armement", "Porte Ouverte",
           "Communication établie par le panneau de control", "Réinitialisation du Système", "Installer Programming", "Mauvais code PIN",
           "Not Sys Event", "Not Sys Event", "Alerte Chaleure Extrême", "Rétablissement Alerte Chaleure Extrême", "Alerte Gel",
           "Rétablissement Alerte Gel", "Alerte Froid", "Rétablissement Alerte Froid", "Alerte Chaud",
           "Rétablissement Alerte Chaud", "Défaut Capteur de Température", "Rétablissement Défaut Capteur de Température",
           # new values partition models
           "PIR Masqué", "Rétablissement PIR Masqué", "", "", "", "", "", "", "", "", "", "",
           "Intrusion Vérifiée", "Rétablissement", "Intrusion Vérifiée", "Rétablissement", "", "", "", "", "", "", "", "", "", "",
           "", "", "", "", "", "Sortir Mode Installeur", "Entrer Mode Installeur", "", "", "", "", "" )
}

pmLogUser_t = {
  "EN" : [ "System ", "Zone 01", "Zone 02", "Zone 03", "Zone 04", "Zone 05", "Zone 06", "Zone 07", "Zone 08",
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

pmSysStatus_t = {
   "EN" : (
           "Disarmed", "Home Exit Delay", "Away Exit Delay", "Entry Delay", "Armed Home", "Armed Away", "User Test",
           "Downloading", "Programming", "Installer", "Home Bypass", "Away Bypass", "Ready", "Not Ready", "??", "??",
           "Disarmed Instant", "Home Instant Exit Delay", "Away Instant Exit Delay", "Entry Delay Instant", "Armed Home Instant",
           "Armed Away Instant" ),
   "NL" : (
           "Uitgeschakeld", "Deel uitloopvertraging", "Totaal uitloopvertraging", "Inloopvertraging", "Deel ingeschakeld",
           "Totaal ingeschakeld", "Gebruiker test", "Downloaden", "Programmeren", "Monteurmode", "Deel met overbrugging",
           "Totaal met overbrugging", "Klaar", "Niet klaar", "??", "??", "Direct uitschakelen", "Direct Deel uitloopvertraging",
           "Direct Totaal uitloopvertraging", "Direct inloopvertraging", "Direct Deel", "Direct Totaal" ),
	"FR" : (
           "Désarmé", "Délai Armement Partiel", "Délai Armement Total", "Délai d'Entrée", "Armé Partiel", "Armé Total", "Test Utilisateur",
           "Téléchargement", "Programmation", "Mode Installateur", "Isolation Partielle", "Isolation Total", "Prêt", "Non Prêt", "??", "??",
           "Désarmé Instantané", "Temporisation Armement Partiel Instantané", "Temporisation Armement Total Instantané", "Temporisation d'Entrée Instantané", "Armé Total Instantané",
           "Armé Total Instantané" )

}

pmSysStatusFlags_t = {
   "EN" : ( "Ready", "Alert in memory", "Trouble", "Bypass on", "Last 10 seconds", "Zone event", "Status changed", "Alarm event" ),
   "NL" : ( "Klaar", "Alarm in geheugen", "Probleem", "Overbruggen aan", "Laatste 10 seconden", "Zone verstoord", "Status gewijzigd", "Alarm actief"),
   "FR" : ( "Prêt", "Alerte en mémoire", "Défaut/Panne", "Isolation sur", "10 dernières secondes", "Evenement Zone", "Etat modifié", "Evenment Alarme" )

}

#pmArmed_t = {
#   0x03 : "", 0x04 : "", 0x05 : "", 0x0A : "", 0x0B : "", 0x13 : "", 0x14 : "", 0x15 : ""
#}

pmArmMode_t = {
# "Alarm" : 0x07, 
   "Disarmed" : 0x00, "Stay" : 0x04, "Armed" : 0x05, "StayInstant" : 0x14, "ArmedInstant" : 0x15, "Night" : 0x04, "NightInstant" : 0x14    # "UserTest" : 0x06, 
}

pmDetailedArmMode_t = (
   "Disarmed", "ExitDelay_ArmHome", "ExitDelay_ArmAway", "EntryDelay", "Stay", "Armed", "UserTest", "Downloading", "Programming", "Installer",
   "Home Bypass", "Away Bypass", "Ready", "NotReady", "??", "??", "Disarm", "ExitDelay", "ExitDelay", "EntryDelay", "StayInstant", "ArmedInstant"
) # Not used: Night, NightInstant, Vacation

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
   0x23 : "Emergency", 0x49 : "Gas",      0x4D : "Flood"
}

pmPanelTroubleType_t = {
   0x0A  : "Communication", 0x0F  : "General",   0x29  : "Battery", 0x2B: "Power",   0x2D : "Battery", 0x2F : "Jamming",
   0x31  : "Communication", 0x33  : "Telephone", 0x36  : "Power",   0x38 : "Battery", 0x3B : "Battery", 0x3C : "Battery",
   0x40  : "Battery",       0x43  : "Battery"
}

pmPanelType_t = {
   0 : "PowerMax", 1 : "PowerMax+", 2 : "PowerMax Pro", 3 : "PowerMax Complete", 4 : "PowerMax Pro Part",
   5  : "PowerMax Complete Part", 6 : "PowerMax Express", 7 : "PowerMaster10",   8 : "PowerMaster30"
}

pmX10State_t = {
   "off" : 0x00, "on" : 0x01, "dim" : 0x0A, "brighten" : 0x0B
}

# Config for each panel type (0-8)
pmPanelConfig_t = {
   "CFG_PARTITIONS"  : (   1,   1,   1,   1,   3,   3,   1,   3,   3 ),
   "CFG_EVENTS"      : ( 250, 250, 250, 250, 250, 250, 250, 250,1000 ),
   "CFG_KEYFOBS"     : (   8,   8,   8,   8,   8,   8,   8,   8,  32 ),
   "CFG_1WKEYPADS"   : (   8,   8,   8,   8,   8,   8,   8,   0,   0 ),
   "CFG_2WKEYPADS"   : (   2,   2,   2,   2,   2,   2,   2,   8,  32 ),
   "CFG_SIRENS"      : (   2,   2,   2,   2,   2,   2,   2,   4,   8 ),
   "CFG_USERCODES"   : (   8,   8,   8,   8,   8,   8,   8,   8,  48 ),
   "CFG_PROXTAGS"    : (   0,   0,   8,   0,   8,   8,   0,   8,  32 ),
   "CFG_WIRELESS"    : (  28,  28,  28,  28,  28,  28,  28,  29,  62 ), # 30, 64
   "CFG_WIRED"       : (   2,   2,   2,   2,   2,   2,   1,   1,   2 ),
   "CFG_ZONECUSTOM"  : (   0,   5,   5,   5,   5,   5,   5,   5,   5 )
}

# PMAX EEPROM CONFIGURATION version 1_2
SettingsCommand = collections.namedtuple('SettingsCommand', 'show count type size poff psize pstep pbitoff name values')
DecodePanelSettings = {
    # USER SETTINGS
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
    "installerCode"  : SettingsCommand( False, 1, "CODE",   16,0x20A,   16,   0,    -1, "Installer Code",      {} ),
    "masterCode"     : SettingsCommand( False, 1, "CODE",   16,0x20C,   16,   0,    -1, "Master Code",         {} ),
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
   "064f" : "PowerMax Secure 6_79", "0650" : "PowerMax Express 6_80", "0650" : "PowerMax Express part2 M 6_80",
   "0651" : "PowerMax Express 6_81", "0652" : "PowerMax Express 6_82", "0653" : "PowerMax Express 6_83",
   "0654" : "PowerMax 6_84", "0655" : "PowerMax 6_85", "0658" : "PowerMax 6_88", "0659" : "PowerMax 6_89",
   "065a" : "PowerMax 6_90", "065b" : "PowerMax 6_91", "0701" : "PowerMax PowerCode-G 7_1", "0702" : "PowerMax PowerCode-G 7_2",
   "0704" : "PowerMaster10 7_4", "0705" : "PowerMaster10 7_05", "0707" : "PowerMaster10 7_07", "070c" : "PowerMaster10 7_12",
   "070f" : "PowerMaster10 7_15", "0710" : "PowerMaster10 7_16", "0711" : "PowerMaster10 7_17", "0712" : "PowerMaster10 7_18",
   "0713" : "PowerMaster10 7_19", "0802" : "PowerMax Complete PowerCode-G 8_2", "0803" : "PowerMaster30 8_3",
   "080f" : "PowerMaster30 8_15", "0810" : "PowerMaster30 8_16", "0812" : "PowerMaster30 8_18", "0813" : "PowerMaster30 8_19",
   "0815" : "PowerMaster30 8_21"
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
   "Custom4", "Custom 5", "Not Installed"
)

pmZoneChime_t = {
   "EN" : ("Off", "Melody", "Zone", "Invalid"),
   "NL" : ("Uit", "Muziek", "Zone", "Invalid"),
   "FR" : ("Eteint", "Melodie", "Zone", "Invalide")

}

# Note: names need to match to VAR_xxx
pmZoneSensor_t = {
   0x0 : "Vibration", 0x2 : "Shock", 0x3 : "Motion", 0x4 : "Motion", 0x5 : "Magnet", 0x6 : "Magnet", 0x7 : "Magnet", 0xA : "Smoke", 0xB : "Gas", 0xC : "Motion", 0xF : "Wired"
} # unknown to date: Push Button, Flood, Universal

ZoneSensorMaster = collections.namedtuple("ZoneSensorMaster", 'name func' )
pmZoneSensorMaster_t = {
   0x01 : ZoneSensorMaster("Next PG2", "Motion" ),
   0x04 : ZoneSensorMaster("Next CAM PG2", "Camera" ),
   0x16 : ZoneSensorMaster("SMD-426 PG2", "Smoke" ),
   0x1A : ZoneSensorMaster("TMD-560 PG2", "Temperature" ),
   0x29 : ZoneSensorMaster("MC-302V PG2", "Magnet"),
   0x2A : ZoneSensorMaster("MC-302 PG2", "Magnet"),
   0x2D : ZoneSensorMaster("MC-302 PG2 (ID 104-5624)", "Magnet"),
   0xFE : ZoneSensorMaster("Wired", "Wired" )
}

class ElapsedFormatter():

    def __init__(self):
        self.start_time = time.time()

    def format(self, record):
        elapsed_seconds = record.created - self.start_time
        #using timedelta here for convenient default formatting
        elapsed = timedelta(seconds = elapsed_seconds)
        return "{} <{: >5}> {: >8}   {}".format(elapsed, record.lineno, record.levelname, record.getMessage())


log = logging.getLogger(__name__)

class LogPanelEvent:
    def __init__(self):
        self.partition = None
        self.time = None
        self.date = None
        self.zone = None
        self.event = None
    def __str__(self):
        strn = ""
        strn = strn + ("part=None" if self.partition == None else "part={0:<2}".format(self.partition))
        strn = strn + ("    time=None" if self.time == None else "    time={0:<2}".format(self.time))
        strn = strn + ("    date=None" if self.date == None else "    date={0:<2}".format(self.date))
        strn = strn + ("    zone=None" if self.zone == None else "    zone={0:<2}".format(self.zone))
        strn = strn + ("    event=None" if self.event == None else "    event={0:<2}".format(self.event))
        return strn

class SensorDevice:
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', None)                # int   device id
        self.dname = kwargs.get('dname', None)          # str   device name
        self.stype = kwargs.get('stype', None)          # str   sensor type
        self.sid = kwargs.get('sid', None)              # int   sensor id
        self.ztype = kwargs.get('ztype', None)          # int   zone type
        self.zname = kwargs.get('zname', None)          # str   zone name
        self.ztypeName = kwargs.get('ztypeName', None)  # str   Zone Type Name
        self.zchime = kwargs.get('zchime', None)        # str   zone chime
        self.partition = kwargs.get('partition', None)  # set   partition set (could be in more than one partition)
        self.bypass = kwargs.get('bypass', False)       # bool  if bypass is set on this sensor
        self.lowbatt = kwargs.get('lowbatt', False)     # bool  if this sensor has a low battery
        self.status = kwargs.get('status', False)       # bool  status, as returned by the A5 message
        self.tamper = kwargs.get('tamper', False)       # bool  tamper, as returned by the A5 message
        self.ztamper = kwargs.get('ztamper', False)     # bool  zone tamper, as returned by the A5 message
        self.ztrip = kwargs.get('ztrip', False)         # bool  zone trip, as returned by the A5 message
        self.enrolled = kwargs.get('enrolled', False)   # bool  enrolled, as returned by the A5 message
        self.triggered = kwargs.get('triggered', False) # bool  triggered, as returned by the A5 message
        self.triggertime = None                         # datetime  This is used to time out the triggered value and set it back to false
        self._change_handler = None

    def __str__(self):
        strn = ""
        strn = strn + ("id=None" if self.id == None else "id={0:<2}".format(self.id, type(self.id)))
        strn = strn + (" dname=None"     if self.dname == None else     " dname={0:<4}".format(self.dname, type(self.dname)))
        strn = strn + (" stype=None"     if self.stype == None else     " stype={0:<8}".format(self.stype, type(self.stype)))
# temporarily miss it out to shorten the line in debug messages        strn = strn + (" sid=None"       if self.sid == None else       " sid={0:<3}".format(self.sid, type(self.sid)))
# temporarily miss it out to shorten the line in debug messages        strn = strn + (" ztype=None"     if self.ztype == None else     " ztype={0:<2}".format(self.ztype, type(self.ztype)))
        strn = strn + (" zname=None"     if self.zname == None else     " zname={0:<14}".format(self.zname, type(self.zname)))
        strn = strn + (" ztypeName=None" if self.ztypeName == None else " ztypeName={0:<10}".format(self.ztypeName, type(self.ztypeName)))
        strn = strn + (" ztamper=None"   if self.ztamper == None else   " ztamper={0:<2}".format(self.ztamper, type(self.ztamper)))
        strn = strn + (" ztrip=None"     if self.ztrip == None else     " ztrip={0:<2}".format(self.ztrip, type(self.ztrip)))
# temporarily miss it out to shorten the line in debug messages        strn = strn + (" zchime=None"    if self.zchime == None else    " zchime={0:<12}".format(self.zchime, type(self.zchime)))
# temporarily miss it out to shorten the line in debug messages        strn = strn + (" partition=None" if self.partition == None else " partition={0}".format(self.partition, type(self.partition)))
        strn = strn + (" bypass=None"    if self.bypass == None else    " bypass={0:<2}".format(self.bypass, type(self.bypass)))
        strn = strn + (" lowbatt=None"   if self.lowbatt == None else   " lowbatt={0:<2}".format(self.lowbatt, type(self.lowbatt)))
        strn = strn + (" status=None"    if self.status == None else    " status={0:<2}".format(self.status, type(self.status)))
        strn = strn + (" tamper=None"    if self.tamper == None else    " tamper={0:<2}".format(self.tamper, type(self.tamper)))
        strn = strn + (" enrolled=None"  if self.enrolled == None else  " enrolled={0:<2}".format(self.enrolled, type(self.enrolled)))
        strn = strn + (" triggered=None" if self.triggered == None else " triggered={0:<2}".format(self.triggered, type(self.triggered)))
        return strn

    def __eq__(self, other):
        if other is None:
            return False
        if self is None:
            return False
        return (self.id == other.id and self.dname == other.dname and self.stype == other.stype and self.sid == other.sid and self.ztype == other.ztype and
            self.zname == other.zname and self.zchime == other.zchime and self.partition == other.partition and self.bypass == other.bypass and
            self.lowbatt == other.lowbatt and self.status == other.status and self.tamper == other.tamper and self.ztypeName == other.ztypeName and
            self.ztamper == other.ztamper and self.ztrip == other.ztrip and
            self.enrolled == other.enrolled and self.triggered == other.triggered and self.triggertime == other.triggertime)

    def __ne__(self, other):
        return not self.__eq__(other)

    def getDeviceID(self):
        return self.id
        
    def install_change_handler(self, ch):
        log.info("[SensorDevice] Installing update handler for device {}".format(self.id))
        self._change_handler = ch

    def pushChange(self):
        if self._change_handler is not None:
            #log.info("[SensorDevice] Calling update handler for device")
            self._change_handler()

class X10Device:
    def __init__(self, **kwargs):
        self.enabled = kwargs.get('enabled', False) # bool  enabled
        self.id = kwargs.get('id', None)            # int   device id
        self.name = kwargs.get('name', None)        # str   name
        self.type = kwargs.get('type', None)        # str   type
        self.location = kwargs.get('location', None)          # str   location
        self.state = False
        self._change_handler = None

    def __str__(self):
        strn = ""
        strn = strn + ("id=None" if self.id == None else "id={0:<2}".format(self.id, type(self.id)))
        strn = strn + (" name=None"     if self.name == None else     " name={0:<4}".format(self.name, type(self.name)))
        strn = strn + (" type=None"     if self.type == None else     " type={0:<8}".format(self.type, type(self.type)))
        strn = strn + (" location=None" if self.location == None else " location={0:<14}".format(self.location, type(self.location)))
        strn = strn + (" enabled=None"  if self.enabled == None else  " enabled={0:<2}".format(self.enabled, type(self.enabled)))
        strn = strn + (" state=None"    if self.state == None else    " state={0:<2}".format(self.state, type(self.state)))
        return strn

    def __eq__(self, other):
        if other is None:
            return False
        if self is None:
            return False
        return (self.id == other.id and self.enabled == other.enabled and self.name == other.name and self.type == other.type and self.location == other.location)

    def __ne__(self, other):
        return not self.__eq__(other)

    def getDeviceID(self):
        return self.id
        
    def install_change_handler(self, ch):
        log.info("[X10Device] Installing update handler for device {}".format(self.id))
        self._change_handler = ch

    def pushChange(self):
        if self._change_handler is not None:
            log.info("[X10Device] Calling update handler for X10 device")
            self._change_handler()


class VisonicListEntry:
    def __init__(self, **kwargs):
        self.command = kwargs.get('command', None)
        self.options = kwargs.get('options', None)
        self.response = []
        if self.command.replytype is not None:
            self.response = self.command.replytype.copy()  # list of message reply needed
        # are we waiting for an acknowledge from the panel (do not send a message until we get it)
        if self.command.waitforack:
            self.response.append(0x02)              # add an acknowledge to the list
        self.triedResendingMessage = False

    def __str__(self):
        return "Command:{0}    Options:{1}".format(self.command.msg, self.options)


# This class handles the detailed low level interface to the panel.
#    It sends the messages
#    It builds and received messages from the raw byte stream and coordinates the acknowledges back to the panel
#    It checks CRC for received messages and creates CRC for sent messages
#    It coordinates the downloading of the EPROM (but doesn't decode the data here)
#    It manages the communication connection
class ProtocolBase(asyncio.Protocol):
    """Manage low level Visonic protocol."""

    PanelStatus["Comm Exception Count"] = PanelSettings["ResetCounter"]
    
    log.info("Initialising Protocol - Component Version {0}".format(PLUGIN_VERSION))

    def __init__(self, loop=None, disconnect_callback=None, packet_callback: Callable = None ) -> None:
        """Initialize class."""
        if loop:
            self.loop = loop
        else:
            self.loop = asyncio.get_event_loop()

        # 2 callback handlers: packets and disconnections
        self.packet_callback = packet_callback
        self.disconnect_callback = disconnect_callback
        
        self.transport = None  # type: asyncio.Transport

        ###############################################################
        # Variables that are only used in handle_received_message function
        ###############################################################
        self.pmVarLenMsg = False       # Are we expecting a variable length message from the panel
        self.pmIncomingPduLen = 0      # The length of the incoming message
        self.pmCrcErrorCount = 0       # The CRC Error Count for Received Messages
        self.pmMsgType = None          # The current receiving message type

        ###############################################################
        # Variables that are only used in this class and not subclasses
        ###############################################################

        # a list of message types we are expecting from the panel
        self.pmExpectedResponse = []

        # The last sent message
        self.pmLastSentMessage = None

        # keep alive counter for the timer 
        self.keep_alive_counter = 0    # only used in keep_alive_and_watchdog_timer

        # this is the watchdog counter (in seconds)
        self.watchdog_counter = 0
        
        # sendlock is used in pmSendPdu to provide a mutex on sending to the panel, this ensures that a message is sent before another message
        self.sendlock = None
        
        # commandlock is used in SendCommand to provide a mutex on sending to the panel, this ensures that a message is sent before another message
        self.commandlock = None
        
        # The receive byte array for receiving a message
        self.ReceiveData = bytearray()

        # A queue of messages to send
        self.SendList = []
        
        self.myDownloadList = []
        
        # This is the time stamp of the last Send
        self.pmLastTransactionTime = self.getTimeFunction() - timedelta(seconds=1)  # take off 1 second so the first command goes through immediately

        self.giveupTrying = False

        self.expectedResponseTimeout = 0

        ###################################################################
        # Variables that are used and modified throughout derived classes
        ###################################################################

        ############## Variables that are set in this class and only read in derived classes ############
        self.ForceStandardMode = False    # until defined by HA or force standard
        self.suspendAllOperations = False # There has been a communication exception, when true do not send/receive(process) data

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
        #         the last command from us was MSG_START
        #    Set to False when we achieve powerlink i.e. self.pmPowerlinkMode is set to True
        self.pmPowerlinkModePending = False
        
        # When we are downloading the EPROM settings and finished parsing them and setting up the system.
        #   There should be no user (from Home Assistant for example) interaction when self.pmDownloadMode is True
        self.pmDownloadMode = False
        
        # Set when we receive a STOP from the panel, indicating that the EPROM data has finished downloading
        self.pmDownloadComplete = False

        # Set when the EPROM has been downloaded and we have extracted a user pin code
        self.pmGotUserCode = False
        
        # When trying to connect in powerlink from the timer loop, this allows the receipt of a powerlink ack to trigger a MSG_RESTORE
        self.allowAckToTriggerRestore = False

        # Set the Download Code 
        self.DownloadCode = '56 50'
        #DownloadCode = AA AA'
        tmpDLCode = PanelSettings["DownloadCode"]                 # INTERFACE : Get the download code
        if tmpDLCode is not None:
            if len(tmpDLCode) == 4 and type(tmpDLCode) is str:
                self.DownloadCode = tmpDLCode[0:2] + " " + tmpDLCode[2:4]

    # Convert byte array to a string of hex values
    def toString(self, array_alpha: bytearray):
        return "".join("%02x " % b for b in array_alpha)

    # get the current date and time
    def getTimeFunction(self) -> datetime:
        return datetime.now()

    # This is called from the loop handler when the connection to the transport is made
    def connection_made(self, transport):
        """Make the protocol connection to the Panel."""
        self.transport = transport
        log.info('[Connection] Connected to local Protocol handler and Transport Layer')

        # get the value for Force standard mode (i.e. do not attempt to go to powerlink)
        self.ForceStandardMode = PanelSettings["ForceStandard"] # INTERFACE : Get user variable from HA to force standard mode or try for PowerLink
        self.Initialise()
        self.ArmWithoutCode = PanelSettings["ArmWithoutCode"] # INTERFACE : Get user variable from HA to arm without user code

    # when the connection has problems then call the disconnect_callback when available, 
    #     otherwise try to reinitialise the connection from here
    def PerformDisconnect(self, exc = None):
        """Log when connection is closed, if needed call callback."""
        self.suspendAllOperations = True
        
        if exc is not None:
            #log.exception("ERROR Connection Lost : disconnected due to exception  <{0}>".format(exc))
            log.error("ERROR Connection Lost : disconnected due to exception {0}".format(exc))
        else:
            log.error('ERROR Connection Lost : disconnected because of close/abort.')
        
        sleep(5.0) # a bit of time for the watchdog timers and keep alive loops to self terminate
        if self.disconnect_callback:
            log.error('                        Calling Exception handler.')
            self.disconnect_callback(exc)
        else:
            log.error('                        No Exception handler to call, terminating Component......')

    # This is called by the asyncio parent when the connection is lost
    def connection_lost(self, exc):        
        """Log when connection is closed, if needed call callback."""
        self.PerformDisconnect(exc)


    # Initialise the parameters for the comms and set everything ready
    #    This is called when the connection is first made and then after any comms exceptions
    def Initialise(self):
        if self.suspendAllOperations:
            log.info('[Connection] Suspended. Sorry but all operations have been suspended, please recreate connection')
            return
        # Define powerlink seconds timer and start it for PowerLink communication
        self.reset_watchdog_timeout()
        self.reset_keep_alive_messages()
        #self.ClearList()
        #self.pmExpectedResponse = []
        self.sendInitCommand()
        if not self.ForceStandardMode:
            self.Start_Download()
        else:
            PanelStatus["Mode"] = "Standard"
        asyncio.ensure_future(self.keep_alive_and_watchdog_timer(), loop=self.loop)


    # This function performs a "soft" reset to the send comms, it resets the queues, clears expected responses, 
    #     resets watchdog timers and asks the panel for a status
    def triggerRestoreStatus(self):
        # Reset Send state (clear queue and reset flags)
        self.ClearList()
        self.pmExpectedResponse = []
        self.expectedResponseTimeout = 0
        # restart the counter
        self.reset_watchdog_timeout()
        self.reset_keep_alive_messages()
        if self.pmPowerlinkMode:
            # Send RESTORE to the panel
            self.SendCommand("MSG_RESTORE") # also gives status
        else:
            self.SendCommand("MSG_STATUS")


    # Function to send I'm Alive and status request messages to the panel
    # This is also a timeout function for a watchdog. If we are in powerlink, we should get a AB 03 message every 20 to 30 seconds
    #    If we haven't got one in the timeout period then reset the send queues and state and then call a MSG_RESTORE
    # In standard mode, this command asks the panel for a status
    async def keep_alive_and_watchdog_timer(self):
        self.reset_watchdog_timeout()
        self.reset_keep_alive_messages()
        status_counter = 0  # don't trigger first time!
        watchdog_events = 0
        download_counter = 0
        powerlink_counter = 230 # set to 3 minutes 50 seconds so first time it does it after 10 seconds
        downloadDuration = 0
        
        while not self.suspendAllOperations:
    
            # Watchdog functionality
            self.watchdog_counter = self.watchdog_counter + 1

            # Keep alive functionality
            self.keep_alive_counter = self.keep_alive_counter + 1

            # The Expected Response Timer
            self.expectedResponseTimeout = self.expectedResponseTimeout + 1
            
            if not self.pmDownloadMode:
                downloadDuration = 0
            
            #log.debug("[Controller] is {0}".format(self.watchdog_counter))
            
            # First, during actual download keep resetting the watchdogs and timers to prevent any other comms to the panel, check for download timeout
            # Second, if not actually doing download but download is incomplete then try every 4 minutes
            # Third, when download had completed successfully, and not ForceStandard from the user, then attempt to connect in powerlink
            # Fourth, check to see if the watchdog timer has expired
            if not self.giveupTrying and self.pmDownloadMode:
                # Disable watchdog and keep-alive during download (and keep flushing send queue)
                self.reset_watchdog_timeout()
                self.reset_keep_alive_messages()
                # count in seconds that we've been in download mode
                downloadDuration = downloadDuration + 1
                if downloadDuration > DOWNLOAD_TIMEOUT:
                    log.warning("[Controller] ********************** Download Timer has Expired, Download has taken too long *********************")
                    log.warning("[Controller] ************************************* Going to standard mode ***************************************")
                    # Stop download mode
                    self.pmDownloadMode = False
                    self.ClearList()
                    # goto standard mode
                    self.gotoStandardMode()
                    PanelStatus["Download Timeout"] = PanelStatus["Download Timeout"] + 1
                    self.sendResponseEvent ( 7 )  # download timer expired
                    
            elif not self.giveupTrying and not self.pmDownloadComplete and not self.ForceStandardMode and not self.pmDownloadMode:
                self.reset_watchdog_timeout()
                download_counter = download_counter + 1
                if download_counter >= DOWNLOAD_RETRY_DELAY:  # 4 Minutes
                    download_counter = 0
                    # trigger a download
                    log.info("[Controller] Trigger Panel Download Attempt")
                    self.Start_Download()
                    
            elif not self.giveupTrying and self.pmPowerlinkModePending and not self.ForceStandardMode and self.pmDownloadComplete and not self.pmPowerlinkMode:
                # Attempt to enter powerlink mode
                self.reset_watchdog_timeout()
                powerlink_counter = powerlink_counter + 1
                log.debug("[Controller] Powerlink Counter {0}".format(powerlink_counter))
                if powerlink_counter >= POWERLINK_RETRY_DELAY:  # 4 minutes
                    powerlink_counter = 0
                    log.info("[Controller] Trigger Powerlink Attempt")
                    # Allow the receipt of a powerlink ack to then send a MSG_RESTORE to the panel, 
                    #      this should kick it in to powerlink after we just enrolled
                    self.allowAckToTriggerRestore = True
                    # Send enroll to the panel to try powerlink
                    self.SendCommand("MSG_ENROLL",  options = [4, bytearray.fromhex(self.DownloadCode)])
                    #self.SendCommand("MSG_BYPASSTAT") # just experimenting here
                    #self.SendCommand("MSG_X10NAMES")
                    #self.SendCommand("MSG_U1")
                    #self.SendCommand("MSG_U2")
                    #self.SendCommand("MSG_U3")   #  to here
                    #self.SendCommand("MSG_RESTORE")
                    
            elif self.watchdog_counter >= WATCHDOG_TIMEOUT:   #  the clock runs at 1 second
                # watchdog timeout
                log.info("[Controller] ****************************** WatchDog Timer Expired ********************************")
                status_counter = 0  # delay status requests
                self.reset_watchdog_timeout()
                self.reset_keep_alive_messages()
                watchdog_events = watchdog_events + 1
                PanelStatus["Watchdog Timeout"] = PanelStatus["Watchdog Timeout"] + 1
                if watchdog_events >= WATCHDOG_MAXIMUM_EVENTS:
                    log.info("[Controller]               **************** Going to Standard Mode ***************")
                    watchdog_events = 0
                    self.gotoStandardMode()
                    self.sendResponseEvent ( 8 )  # watchdog timer expired, going to standard mode
                else:
                    log.info("[Controller]               **************** Trigger Restore Status ***************")
                    self.triggerRestoreStatus()
                    self.sendResponseEvent ( 9 )  # watchdog timer expired, going to try again

            elif len(self.pmExpectedResponse) > 0 and self.expectedResponseTimeout >= RESPONSE_TIMEOUT:
                log.debug("[Controller] ****************************** Response Timer Expired ********************************")
                self.triggerRestoreStatus()
                
            
            # Fifth, is it time to send an I'm Alive message to the panel
            # Sixth, flush the send queue, send all buffered messages to the panel
            if len(self.SendList) == 0 and not self.pmDownloadMode and self.keep_alive_counter >= KEEP_ALIVE_PERIOD:   #
                # Every KEEP_ALIVE_PERIOD seconds, unless watchdog has been reset
                #log.debug("[Controller]   Send list is empty so sending I'm alive message")
                # reset counter
                self.reset_keep_alive_messages()
                # Send I'm Alive and request status
                self.SendCommand("MSG_ALIVE")
                # When is standard mode, sending this asks the panel to send us the status so we know that the panel is ok.
                # When in powerlink mode, it makes no difference as we get the AB messages from the panel, but this also keeps our status updated
                status_counter = status_counter + 1
                if status_counter >= 3:  # every twice around the loop i.e every KEEP_ALIVE_PERIOD * 3 seconds
                    status_counter = 0
                    self.SendCommand("MSG_STATUS")  # Asks the panel to send us the A5 message set
            else:
                # Every 1.0 seconds, try to flush the send queue
                self.SendCommand(None)  # check send queue
            
            # sleep, doesn't need to be highly accurate so just count each second
            await asyncio.sleep(1.0)


    def reset_keep_alive_messages(self):
        self.keep_alive_counter = 0

    # This function needs to be called within the timeout to reset the timer period
    def reset_watchdog_timeout(self):
        self.watchdog_counter = 0
        
    def sendInitCommand(self):   # This should re-initialise the panel, most of the time it works!
        self.ClearList()
        self.pmExpectedResponse = []
        self.SendCommand("MSG_EXIT")
        self.SendCommand("MSG_STOP")
        while not self.suspendAllOperations and len(self.SendList) > 0:
            log.debug("[ResetPanel]       Waiting")
            self.SendCommand(None)  # check send queue

        self.pmExpectedResponse = []
        self.SendCommand("MSG_INIT")
        
    def gotoStandardMode(self):
        #self.ClearList()
        if self.pmDownloadComplete and self.pmGotUserCode:
            log.info("[Standard Mode] Entering Standard Plus Mode as we got the pin codes from the EPROM")
            PanelStatus["Mode"] = "Standard Plus"
        else:
            log.info("[Standard Mode] Entering Standard Mode")
            PanelStatus["Mode"] = "Standard"
            self.ForceStandardMode = True
        self.giveupTrying = True
        self.pmPowerlinkMode = False
        self.sendInitCommand()
        self.SendCommand("MSG_STATUS")

    # Process any received bytes (in data as a bytearray)            
    def data_received(self, data):
        """Add incoming data to ReceiveData."""
        if self.suspendAllOperations:
            return
        #log.debug('[data receiver] received data: %s', self.toString(data))
        for databyte in data:
            #log.debug("[data receiver] Processing " + hex(databyte).upper())
            # process a single byte at a time
            self.handle_received_byte(databyte)

    # Process one received byte at a time to build up the received messages
    #       pmIncomingPduLen is only used in this function
    #       pmVarLenMsg is only used in this function
    #       msgType_t is only used in this function
    #       pmCrcErrorCount is only used in this function
    def handle_received_byte(self, data):
        """Process a single byte as incoming data."""
        # Length of the received data so far
        if self.suspendAllOperations:
            return
            
        pdu_len = len(self.ReceiveData)

        # If we were expecting a message of a particular length and what we have is already greater then that length then dump the message and resynchronise.
        if 0 < self.pmIncomingPduLen <= pdu_len:   # waiting for pmIncomingPduLen bytes but got more and haven't been able to validate a PDU
            log.debug("[data receiver] Building PDU: Dumping Current PDU " + self.toString(self.ReceiveData))
            # Reset the incoming data to 0 length and clear the receive buffer
            self.ReceiveData = bytearray(b'')
            pdu_len = len(self.ReceiveData)

        # If the length is 4 bytes and we're receiving a variable length message, the panel tells us the length we're expecting to receive
        if pdu_len == 4 and self.pmVarLenMsg:
            # Determine length of variable size message
            # The message type is in the second byte
            #msgType = self.ReceiveData[1]
            self.pmIncomingPduLen = 7 + int(data) # (((int(self.ReceiveData[2]) * 0x100) + int(self.ReceiveData[3])))
            #if self.pmIncomingPduLen >= 0xB0:  # then more than one message
            #    self.pmIncomingPduLen = 0 # set it to 0 for this loop around
            #log.debug("[data receiver] Variable length Message Being Receive  Message {0}     pmIncomingPduLen {1}".format(hex(msgType).upper(), self.pmIncomingPduLen))

        # If this is the start of a new message, then check to ensure it is a 0x0D (message header)
        if pdu_len == 0:
            self.pmMsgType = None
            if data == 0x0D:  # preamble
                #log.debug("[data receiver] Start of new PDU detected, expecting response " + response)
                #log.debug("[data receiver] Start of new PDU detected")
                # reset the message and add the received header
                self.ReceiveData = bytearray(b'')
                self.ReceiveData.append(data)
                #log.debug("[data receiver] Starting PDU " + self.toString(self.ReceiveData))
        elif pdu_len == 1:
            # The second byte is the message type
            msgType = data
            #log.debug("[data receiver] Received message Type %d", data)
            # Is it a message type that we know about
            if msgType in pmReceiveMsg_t:
                self.pmMsgType = pmReceiveMsg_t[msgType]
                self.pmIncomingPduLen = (self.pmMsgType is not None) and self.pmMsgType.length or 0
                self.pmVarLenMsg = pmReceiveMsg_t[msgType].variablelength
            else:
                log.warning("[data receiver] Warning : Construction of incoming packet unknown - Message Type {0}".format(hex(msgType).upper()))
            #log.debug("[data receiver] Building PDU: It's a message %02X; pmIncomingPduLen = %d", data, self.pmIncomingPduLen)
            # Add on the message type to the buffer
            self.ReceiveData.append(data)
        elif (self.pmIncomingPduLen == 0 and data == 0x0A) or (pdu_len + 1 == self.pmIncomingPduLen): # postamble   (standard length message and data terminator) OR (actual length == calculated length)
            # add to the message buffer
            self.ReceiveData.append(data)
            #log.debug("[data receiver] Building PDU: Checking it " + self.toString(self.ReceiveData))
            msgType = self.ReceiveData[1]
            if (data != 0x0A) and (self.ReceiveData[pdu_len] == 0x43):
                log.info("[data receiver] Building PDU: Special Case 0x43 ********************************************************************************************")
                self.pmIncomingPduLen = self.pmIncomingPduLen + 1 # for 0x43
            elif self.validatePDU(self.ReceiveData):
                # We've got a validated message
                #log.debug("[data receiver] Building PDU: Got Validated PDU type 0x%02x   full data %s", int(msgType), self.toString(self.ReceiveData))
                # Reset some variable ready for next time
                self.pmVarLenMsg = False

                # Unknown Message has been received
                if self.pmMsgType is None:
                    log.warning("[data receiver] Unhandled message {0}".format(hex(msgType)))
                    self.SendAck()
                else:
                    #log.info("[data receiver] *** Received message " + hex(msgType).upper() + "   data " + self.toString(self.ReceiveData))
                    # Send an ACK if needed
                    if self.pmMsgType.ackneeded:
                        #log.debug("[data receiver] Sending an ack as needed by last panel status message " + hex(msgType).upper())
                        self.SendAck(data = self.ReceiveData)

                    # Check response
                    tmplength = len(self.pmExpectedResponse)
                    if len(self.pmExpectedResponse) > 0: # and msgType != 2:   # 2 is a simple acknowledge from the panel so ignore those
                        # We've sent something and are waiting for a reponse - this is it
                        #log.debug("[data receiver] msgType {0}  expected one of {1}".format(hex(msgType).upper(), [hex(no).upper() for no in self.pmExpectedResponse]))
                        if (msgType in self.pmExpectedResponse):
                            #while msgType in self.pmExpectedResponse:
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
                        self.packet_callback(self.ReceiveData)
                    
                # clear our buffer again so we can receive a new packet.
                self.ReceiveData = bytearray(b'')
                
            else: # CRC check failed. However, it could be an 0x0A in the middle of the data packet and not the terminator of the message
                if len(self.ReceiveData) > 0xB0:
                    log.info("[data receiver] PDU with CRC error %s", self.toString(self.ReceiveData))
                    self.ReceiveData = bytearray(b'')
                    if msgType != 0xF1:        # ignore CRC errors on F1 message
                        self.pmCrcErrorCount = self.pmCrcErrorCount + 1
                    if (self.pmCrcErrorCount > MAX_CRC_ERROR):
                        self.pmCrcErrorCount = 0
                        self.PerformDisconnect("CRC errors")
                elif msgType != 0xF1:        # ignore CRC errors on F1 message
                    a = self.calculate_crc(self.ReceiveData[1:-2])[0]
                    log.debug("[data receiver] Building PDU: Length is now %d bytes (apparently PDU not complete)    %s    checksum calcs %02x", len(self.ReceiveData), self.toString(self.ReceiveData), a)
        elif pdu_len <= 0xC0:
            #log.debug("[data receiver] Current PDU " + self.toString(self.ReceiveData) + "    adding " + str(hex(data).upper()))
            self.ReceiveData.append(data)
        else:
            log.debug("[data receiver] Dumping Current PDU " + self.toString(self.ReceiveData))
            self.ReceiveData = bytearray(b'') # messages should never be longer than 0xC0
        #log.debug("[data receiver] Building PDU " + self.toString(self.ReceiveData))


    # Send an achnowledge back to the panel
    def SendAck(self, data = bytearray(b'')):
        """ Send ACK if packet is valid """
        
        ispm = len(data) > 3 and (data[1] >= 0xA5 or (data[1] < 0x10 and data[-2] == 0x43))
        
        #---------------------- debug only start ----------------
        lastType = 0
        if len(data) > 2:
            lastType = data[1]
        log.debug("[Sending ack] PowerlinkMode={0}    Is PM Ack Reqd={1}    This is an Ack for message={2}".format(self.pmPowerlinkMode, ispm, hex(lastType).upper()))
        #---------------------- debug only end ------------------

        # There are 2 types of acknowledge that we can send to the panel
        #    Normal    : For a normal message
        #    Powerlink : For when we are in powerlink mode
        if not self.ForceStandardMode and ispm:
            message = pmSendMsg["MSG_ACKLONG"]
        else:
            message = pmSendMsg["MSG_ACK"]
        assert(message is not None)
        e = VisonicListEntry(command = message, options = None)
        t = asyncio.ensure_future(self.pmSendPdu(e), loop=self.loop)
        asyncio.wait_for(t, None)

    # check the checksum of received messages
    def validatePDU(self, packet : bytearray) -> bool:
        """Verify if packet is valid.
            >>> Packets start with a preamble (\x0D) and end with postamble (\x0A)
        """
        # Validate a received message
        # Does it start with a header
        if packet[:1] != b'\x0D':
            return False
        # Does it end with a footer
        if packet[-1:] != b'\x0A':
            return False

        if packet[-2:-1][0] == self.calculate_crc(packet[1:-2])[0] + 1:
            log.info("[validatePDU] Validated a Packet with a checksum that is 1 different to the actual checksum!!!!")
            return True

        # Check the CRC
        if packet[-2:-1] == self.calculate_crc(packet[1:-2]):
            #log.debug("[validatePDU] VALID PACKET!")
            return True

        log.info("[validatePDU] Not valid packet, CRC failed, may be ongoing and not final 0A")
        return False

    # calculate the checksum for sending and receiving messages
    def calculate_crc(self, msg: bytearray):
        """ Calculate CRC Checksum """
        #log.debug("[calculate_crc] Calculating for: %s", self.toString(msg))
        # Calculate the checksum
        checksum = 0
        for char in msg[0:len(msg)]:
            checksum += char
        checksum = 0xFF - (checksum % 0xFF)
        if checksum == 0xFF:
            checksum = 0x00
#            log.debug("[calculate_crc] Checksum was 0xFF, forsing to 0x00")
        #log.debug("[calculate_crc] Calculating for: %s     calculated CRC is: %s", self.toString(msg), self.toString(bytearray([checksum])))
        return bytearray([checksum])

    # Function to send all PDU messages to the panel, using a mutex lock to combine acknowledges and other message sends
    async def pmSendPdu(self, instruction : VisonicListEntry):
        """Encode and put packet string onto write buffer."""

        if self.suspendAllOperations:
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
                #log.debug("[pmSendPdu] Options {0} {1}".format(instruction.options, op))
                for o in range(0, op):
                    s = instruction.options[o * 2]      # bit offset as an integer
                    a = instruction.options[o * 2 + 1]  # the bytearray to insert
                    if isinstance(a, int):
                        #log.debug("[pmSendPdu] Options {0} {1} {2} {3}".format(type(s), type(a), s, a))
                        data[s] = a;                
                    else:
                        #log.debug("[pmSendPdu] Options {0} {1} {2} {3} {4}".format(type(s), type(a), s, a, len(a)))
                        for i in range(0, len(a)):
                            data[s + i] = a[i]
                            #log.debug("[pmSendPdu]        Inserting at {0}".format(s+i))

            #log.debug('[pmSendPdu] input data: %s', self.toString(packet))
            # First add header (0x0D), then the packet, then crc and footer (0x0A)
            sData = b'\x0D'
            sData += data
            sData += self.calculate_crc(data)
            sData += b'\x0A'

            interval = self.getTimeFunction() - self.pmLastTransactionTime
            sleepytime = timedelta(milliseconds=150) - interval
            if sleepytime > timedelta(milliseconds=0):
                #log.debug("[pmSendPdu] Speepytime {0}".format(sleepytime.total_seconds()))
                await asyncio.sleep(sleepytime.total_seconds())
            
            # no need to send i'm alive message for a while as we're about to send a command anyway
            self.reset_keep_alive_messages()   

            # Log some useful information in debug mode
            self.transport.write(sData)
            #log.debug("[pmSendPdu]      waiting for message response {}".format([hex(no).upper() for no in self.pmExpectedResponse]))
            
            if sData[1] != 0x02:   # the message is not an acknowledge back to the panel
                self.pmLastSentMessage = instruction

            self.pmLastTransactionTime = self.getTimeFunction()
            log.debug("[pmSendPdu] Sending Command ({0})    raw data {1}   waiting for message response {2}".format(command.msg, self.toString(sData), [hex(no).upper() for no in self.pmExpectedResponse]))
            if command.waittime > 0.0:
                log.debug("[pmSendPdu]          Command has a wait time after transmission {0}".format(command.waittime))
                await asyncio.sleep(command.waittime)
           

    # This is called to queue a command.
    def SendCommand(self, message_type, **kwargs):
        t = asyncio.ensure_future(self.SendCommandAsync(message_type, kwargs), loop=self.loop)
        asyncio.wait_for(t, None)

    # This is called to queue a command.
    # If it is possible, then also send the message
    async def SendCommandAsync(self, message_type, kwargs):
        """ Add a command to the send List 
            The List is needed to prevent sending messages too quickly normally it requires 500msec between messages """
        
        if self.suspendAllOperations:
            log.info("[SendCommand] Suspended all operations, not sending PDU")
            return
        
        if self.commandlock is None:
            self.commandlock = asyncio.Lock()
            
        #log.info("[SendCommand] kwargs  {0}  {1}".format(type(kwargs), kwargs))

        async with self.commandlock:            
            interval = self.getTimeFunction() - self.pmLastTransactionTime
            timeout = (interval > RESEND_MESSAGE_TIMEOUT)

            # command may be set to None on entry
            # Always add the command to the list
            if message_type is not None:
                message = pmSendMsg[message_type]
                assert(message is not None)
                options = kwargs.get('options', None)
                e = VisonicListEntry(command = message, options = options)
                self.SendList.append(e)
                #log.info("[SendCommand] %s", message.msg)

            # self.pmExpectedResponse will prevent us sending another message to the panel
            #   If the panel is lazy or we've got the timing wrong........
            #   If there's a timeout then resend the previous message. If that doesn't work then do a reset using triggerRestoreStatus function
            #  Do not resend during startup or download as the timing is critical anyway
            if not self.pmDownloadMode and self.pmLastSentMessage != None and timeout and len(self.pmExpectedResponse) > 0:
                if not self.pmLastSentMessage.triedResendingMessage:
                    # resend the last message
                    log.info("[SendCommand] Re-Sending last message  {0}".format(self.pmLastSentMessage.command.msg))
                    self.ClearList()
                    self.pmExpectedResponse = []
                    t = asyncio.ensure_future(self.pmSendPdu(self.pmLastSentMessage), loop=self.loop)
                    asyncio.wait_for(t, None)
                    self.pmLastSentMessage.triedResendingMessage = True
                else:
                    # tried resending once, no point in trying again so reset settings, start from scratch
                    log.info("[SendCommand] Tried Re-Sending last message but didn't work. Assume a powerlink timeout state and reset")
                    self.ClearList()
                    self.triggerRestoreStatus()
            elif len(self.SendList) > 0 and len(self.pmExpectedResponse) == 0: # we are ready to send
                # pop the oldest item from the list, this could be the only item.
                instruction = self.SendList.pop(0)

                if len(instruction.response) > 0:
                    log.debug("[pmSendPdu] Resetting expected response counter, it got to {0}   Response list before {1}  after {2}".format(self.expectedResponseTimeout, len(self.pmExpectedResponse), len(self.pmExpectedResponse) + len(instruction.response)))
                    self.expectedResponseTimeout = 0
                    # update the expected response list straight away (without having to wait for it to be actually sent) to make sure protocol is followed
                    self.pmExpectedResponse.extend(instruction.response) # if an ack is needed it will already be in this list

                t = asyncio.ensure_future(self.pmSendPdu(instruction), loop=self.loop)
                asyncio.wait_for(t, None)

    # Clear the send queue and reset the associated parameters
    def ClearList(self):
        """ Clear the List, preventing any retry causing issue. """
        # Clear the List
        log.debug("[ClearList] Setting queue empty")
        self.SendList = []
        self.pmLastSentMessage = None

    def getLastSentMessage(self):
        return self.pmLastSentMessage

    # This puts the panel in to download mode. It is the start of determining powerlink access
    def Start_Download(self):
        """ Start download mode """
        PanelStatus["Mode"] = "Download"
        if not self.pmDownloadComplete and not self.pmDownloadMode:
            #self.pmWaitingForAckFromPanel = False
            self.pmExpectedResponse = []
            log.info("[Start_Download] Starting download mode")
            self.SendCommand("MSG_DOWNLOAD", options = [3, bytearray.fromhex(self.DownloadCode)]) #
            self.pmDownloadMode = True
        elif self.pmDownloadComplete:
            log.debug("[Start_Download] Download has already completed (so not doing anything)")
        else:
            log.debug("[Start_Download] Already in Download Mode (so not doing anything)")

    # Attempt to enroll with the panel in the same was as a powerlink module would inside the panel
    def pmReadPanelSettings(self, isPowerMaster):
        """ Attempt to Enroll as a Powerlink """
        log.info("[Panel Settings] Reading panel settings")
        
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
        
        self.SendCommand("MSG_DL", options = [1, self.myDownloadList.pop(0)] )     # Read the names of the zones


# This class performs transactions based on messages (ProtocolBase is the raw data)
class PacketHandling(ProtocolBase):
    """Handle decoding of Visonic packets."""

    def __init__(self, *args, event_callback: Callable = None, **kwargs) -> None:
        """ Perform transactions based on messages (and not bytes) """
        super().__init__(*args, packet_callback=self.handle_packet, **kwargs)
        
        self.powerMasterDownloadCounter = 0
        
        # set the event callback handler
        self.event_callback = event_callback

        self.pmPhoneNr_t = {}
        self.pmEventLogDictionary = {}
        # We do not put these pin codes in to the panel status
        self.pmPincode_t = [ ]  # allow maximum of 48 user pin codes

        self.lastSendOfDownloadEprom = self.getTimeFunction() - timedelta(seconds=100)  # take off 100 seconds so the first command goes through immediately
        
        # Variables to manage the PowerMAster B0 message and the triggering of Motion
        self.lastRecvOfMasterMotionData = self.getTimeFunction() - timedelta(seconds=100)  # take off 100 seconds so the first command goes through immediately
        self.firstRecvOfMasterMotionData = self.getTimeFunction() - timedelta(seconds=100)  # take off 100 seconds so the first command goes through immediately
        self.zoneNumberMasterMotion = 0
        self.zoneDataMasterMotion = bytearray(b'')
        
        # Store the sensor details
        self.pmSensorDev_t = {}

        # Store the X10 details
        self.pmX10Dev_t = {}
        
        self.pmLang = PanelSettings["PluginLanguage"]             # INTERFACE : Get the plugin language from HA, either "EN" or "NL"
        self.pmRemoteArm = PanelSettings["EnableRemoteArm"]       # INTERFACE : Does the user allow remote setting of the alarm
        self.pmRemoteDisArm = PanelSettings["EnableRemoteDisArm"] # INTERFACE : Does the user allow remote disarming of the alarm
        self.pmSensorBypass = PanelSettings["EnableSensorBypass"] # INTERFACE : Does the user allow sensor bypass, True or False
        self.MotionOffDelay = PanelSettings["MotionOffDelay"]     # INTERFACE : Get the motion sensor off delay time (between subsequent triggers)
        self.OverrideCode = PanelSettings["OverrideCode"]         # INTERFACE : Get the override code (must be set if forced standard and not powerlink)
        self.ForceNumericKeypad = PanelSettings["ForceKeypad"]    # INTERFACE : Force the display and use of the keypad, even if downloaded EEPROM

        PanelStatus["Comm Exception Count"] = PanelSettings["ResetCounter"]
        
        # Save the EPROM data when downloaded
        self.pmRawSettings = {}
        # Save the sirens
        self.pmSirenDev_t = {}
        # Status in "Starting" mode
        PanelStatus["Mode"] = "Starting"
        
        self.pmSirenActive = None
        
        # These are used in the A5 message to reduce processing but mainly to reduce the amount of callbacks in to HA when nothing changes
        self.lowbatt_old = -1
        self.tamper_old = -1
        self.enrolled_old = 0   # means nothing enrolled
        self.status_old = -1
        self.bypass_old = -1
        self.zonealarm_old = -1
        self.zonetamper_old = -1

        self.pmBypassOff = False         # Do we allow the user to bypass the sensors
        self.pmSilentPanic = False
        self.lastPacket = None
        self.lastPacketCounter = 0
        # Whether its a powermax or powermaster
        self.PowerMaster = True
        
        # determine when MSG_ENROLL is sent to the panel
        self.doneAutoEnroll = False

        asyncio.ensure_future(self.reset_triggered_state_timer(), loop=self.loop)
    
    
    async def reset_triggered_state_timer(self):
        """ reset triggered state"""
        while not self.suspendAllOperations:
            # cycle through the sensors and set the triggered value back to False after the timeout duration
            for key in self.pmSensorDev_t:
                if self.pmSensorDev_t[key].triggered:
                    interval = self.getTimeFunction() - self.pmSensorDev_t[key].triggertime
                    td = timedelta(seconds=self.MotionOffDelay)  # at least self.MotionOffDelay seconds as it also depends on the frequency the panel sends messages
                    if interval > td:
                        self.pmSensorDev_t[key].triggered = False
                        self.pmSensorDev_t[key].pushChange()
            # check every 1 second
            await asyncio.sleep(1.0)  # must be less than 5 seconds for self.suspendAllOperations:

    def sendResponseEvent(self, ev):
        if self.event_callback is not None:
            self.event_callback(ev)        

    # We can only use this function when the panel has sent a "installing powerlink" message i.e. AB 0A 00 01
    #   We need to clear the send queue and reset the send parameters to immediately send an MSG_ENROLL
    def SendMsg_ENROLL(self):
        """ Auto enroll the PowerMax/Master unit """
        if not self.doneAutoEnroll:
            self.doneAutoEnroll = True
            self.SendCommand("MSG_ENROLL",  options = [4, bytearray.fromhex(self.DownloadCode)])
            self.Start_Download()
        elif self.DownloadCode == "DF FD":
            log.warning("[SendMsg_ENROLL] Warning: Trying to re enroll, already tried DFFD and still not successful")
        else:
            log.info("[SendMsg_ENROLL] Warning: Trying to re enroll but not triggering download")
            self.DownloadCode = "DF FD" # Force the Download code to be something different and try again ?????
            self.SendCommand("MSG_ENROLL",  options = [4, bytearray.fromhex(self.DownloadCode)])

    # pmWriteSettings: add a certain setting to the settings table
    #  So, to explain
    #      When we send a MSG_DL and insert the 4 bytes from pmDownloadItem_t, what we're doing is setting the page, index and len
    # This function stores the downloaded status and EPROM data
    def pmWriteSettings(self, page, index, setting):
        settings_len = len(setting)
        wrap = (index + settings_len - 0x100)
        sett = [bytearray(b''), bytearray(b'')]

        #log.debug("[Write Settings]   Entering Function  page {0}   index {1}    length {2}".format(page, index, settings_len)) 
        if settings_len > 0xB1:
            log.info("[Write Settings] ********************* Write Settings too long ********************")
            return

        if wrap > 0:
            #log.debug("[Write Settings] The write settings data is Split across 2 pages")
            sett[0] = setting[ : settings_len - wrap]  # bug fix in 0.0.6, removed the -1
            sett[1] = setting[settings_len - wrap : ]
            #log.debug("[Write Settings]         Wrapping  original len {0}   left len {1}   right len {2}".format(len(setting), len(sett[0]), len(sett[1]))) 
            wrap = 1
        else:
            sett[0] = setting
            wrap = 0

        for i in range(0, wrap+1):
            if (page + i) not in self.pmRawSettings:
                self.pmRawSettings[page + i] = bytearray()
                for v in range(0, 256):
                    self.pmRawSettings[page + i].append(255)
                if len(self.pmRawSettings[page + i]) != 256:
                    log.info("[Write Settings] The EPROM settings is incorrect for page {0}".format(page+i))
                #else:
                #    log.debug("[Write Settings] WHOOOPEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE")

            settings_len = len(sett[i])
            if i == 1:
                index = 0
            #log.debug("[Write Settings]         Writing settings page {0}  index {1}    length {2}".format(page+i, index, settings_len))
            self.pmRawSettings[page + i] = self.pmRawSettings[page + i][0:index] + sett[i] + self.pmRawSettings[page + i][index + settings_len :]
            if len(self.pmRawSettings[page + i]) != 256:
                log.info("[Write Settings] OOOOOOOOOOOOOOOOOOOO len = {0}".format(len(self.pmRawSettings[page + i])))
            #else:
            #    log.debug("[Write Settings] Page {0} is now {1}".format(page+i, self.toString(self.pmRawSettings[page + i])))

    # pmReadSettings
    # This function retrieves the downloaded status and EPROM data
    def pmReadSettingsA(self, page, index, settings_len):
        retlen = settings_len
        retval = bytearray()
        #log.debug("[Read Settings]    Entering Function  page {0}   index {1}    length {2}".format(page, index, settings_len)) 
        while page in self.pmRawSettings and retlen > 0:
            fred = self.pmRawSettings[page][index : index + retlen]
            retval = retval + fred
            page = page + 1
            retlen = retlen - len(fred)
            index = 0
        if settings_len == len(retval):
            #log.debug("[Read Settings]       Length " + str(settings_len) + " returning (just the 1st value) " + self.toString(retval[:1]))
            return retval
        # return a bytearray filled with 0xFF values
        log.info("[Read Settings]     Sorry but you havent downloaded that part of the EPROM data     page={0} index={1} length={2}".format(hex(page), hex(index), settings_len))
        retval = bytearray()
        for i in range(0, settings_len):
            retval.append(255)
        return retval

    # this can be called from an entry in pmDownloadItem_t such as
    #       for example "MSG_DL_PANELFW"      : bytearray.fromhex('00 04 20 00'),
    #            this defines the index, page and 2 bytes for length
    #            in this example page 4   index 0   length 32
    def pmReadSettings(self, item):
        return self.pmReadSettingsA(item[1], item[0], item[2] + (0x100 * item[3]))

    # This function was going to save the settings (including EPROM) to a file
    def dump_settings(self):
        log.debug("Dumping EPROM Settings")
        for p in range(0, 0x100):   ## assume page can go from 0 to 255
            if p in self.pmRawSettings:
                for j in range(0, 0x100, 0x10):   ## assume that each page can be 256 bytes long, step by 16 bytes
                    # do not display the rows with pin numbers
                    #if not (( p == 1 and j == 240 ) or (p == 2 and j == 0) or (p == 10 and j >= 140)):
                    if ( p != 1 or j != 240 ) and (p != 2 or j != 0) and (p != 10 or j <= 140):
                        if j <= len(self.pmRawSettings[p]):
                            s = self.toString(self.pmRawSettings[p][j : j + 0x10])
                            log.debug("{0:3}:{1:3}  {2}".format(p,j,s))


    def calcBool(self, val, mask):
        return True if val & mask != 0 else False

    #SettingsCommand = collections.namedtuple('SettingsCommand', 'count type size poff psize pstep pbitoff name values')
    def lookupEprom(self, val : SettingsCommand):
        retval = []
        
        if val is None:
            retval.append('Not Found')
            retval.append('Not Found As Well')
            return retval
        
        for ctr in range(0, val.count):
            addr = val.poff + (ctr * val.pstep)
            page = math.floor(addr / 0x100); 
            pos  = addr % 0x100
            
            myvalue = ''
            
            if val.type == "BYTE":
                v = self.pmReadSettingsA(page, pos, val.count)
                if val.psize == 8:
                    myvalue = str(v[0])
                else:
                    mask = (1 << val.psize) - 1
                    offset = val.pbitoff | 0
                    myvalue = str((v[0] >> offset) & mask) 
            elif val.type == "PHONE":
                for j in range(0, math.floor(val.psize / 8)):
                    nr = self.pmReadSettingsA(page, pos + j, 1)
                    if nr[0] != 0xFF:
                        myvalue = myvalue + "".join("%02x" % b for b in nr)
            elif val.type == "TIME":
                t = self.pmReadSettingsA(page, pos, 2)
                myvalue = "".join("%02d:" % b for b in t)[:-1]  # miss the last character off, which will be a colon :
            elif val.type == "CODE" or val.type == "ACCOUNT":
                nr = self.pmReadSettingsA(page, pos, math.floor(val.psize / 8))
                myvalue = "".join("%02x" % b for b in nr).upper()
                myvalue = myvalue.replace("FF", ".")
                #if val.type == "CODE" and val.size == 16:
                #    myvalue = pmEncryptPIN(val);
                #    myvalue = [ this.rawSettings[page][pos], this.rawSettings[page][pos + 1] ];
            elif val.type == "STRING":
                for j in range(0, math.floor(val.psize / 8)):
                    nr = self.pmReadSettingsA(page, pos + j, 1)
                    if nr[0] != 0xFF:
                        myvalue = myvalue + chr(nr[0]);
            else:
                myvalue = "Not Set"
            
            if len(val.values) > 0 and myvalue in val.values:
                retval.append(val.values[str(myvalue)])
            else:
                retval.append(myvalue)
            
        return retval
        
    def lookupEpromSingle(self, key):
        v = self.lookupEprom(DecodePanelSettings[key])
        if len(v) >= 1:
            return v[0]
        return ''
    
    # ProcessSettings
    #    Decode the EPROM and the various settings to determine 
    #       The general state of the panel
    #       The zones and the sensors
    #       The X10 devices
    #       The phone numbers
    #       The user pin codes
    def ProcessSettings(self):
        """Process Settings from the downloaded EPROM data from the panel"""
        log.info("[Process Settings] Process Settings from EPROM")

        # Process settings
        x10_t = {}
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
        pmPanelTypeNrStr = self.lookupEpromSingle("panelSerialCode")
        if pmPanelTypeNrStr is not None and len(pmPanelTypeNrStr) > 0:
            pmPanelTypeNr = int(pmPanelTypeNrStr)
            PanelStatus["Model"] = pmPanelType_t[pmPanelTypeNr] if pmPanelTypeNr in pmPanelType_t else "UNKNOWN"   # INTERFACE : PanelType set to model
            #self.dump_settings()
            log.info("[Process Settings] pmPanelTypeNr {0}    model {1}".format(pmPanelTypeNr, PanelStatus["Model"]))

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Need the panel type to be valid so we can decode some of the remaining downloaded data correctly
        if pmPanelTypeNr is not None and 0 <= pmPanelTypeNr <= 8:
            #log.debug("[Process Settings] Panel Type Number " + str(pmPanelTypeNr) + "    serial string " + self.toString(panelSerialType))
            zoneCnt = pmPanelConfig_t["CFG_WIRELESS"][pmPanelTypeNr] + pmPanelConfig_t["CFG_WIRED"][pmPanelTypeNr]
            customCnt = pmPanelConfig_t["CFG_ZONECUSTOM"][pmPanelTypeNr]
            userCnt = pmPanelConfig_t["CFG_USERCODES"][pmPanelTypeNr]
            partitionCnt = pmPanelConfig_t["CFG_PARTITIONS"][pmPanelTypeNr]
            sirenCnt = pmPanelConfig_t["CFG_SIRENS"][pmPanelTypeNr]
            keypad1wCnt = pmPanelConfig_t["CFG_1WKEYPADS"][pmPanelTypeNr]
            keypad2wCnt = pmPanelConfig_t["CFG_2WKEYPADS"][pmPanelTypeNr]
            self.pmPincode_t = [ bytearray.fromhex("00 00") ] * userCnt              # allow maximum of userCnt user pin codes

            devices = ""

            if self.pmDownloadComplete:
                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Check if time sync was OK
                #  if (pmSyncTimeCheck ~= nil) then
                #     setting = pmReadSettings(pmDownloadItem_t.MSG_DL_TIME)
                #     local timeRead = os.time({ day = string.byte(setting, 4), month = string.byte(setting, 5), year = string.byte(setting, 6) + 2000, 
                #        hour = string.byte(setting, 3), min = string.byte(setting, 2), sec = string.byte(setting, 1) })
                #     local timeSet = os.time(pmSyncTimeCheck)
                #     if (timeRead == timeSet) or (timeRead == timeSet + 1) then
                #        debug("Time sync OK (" .. os.date("%d/%m/%Y %H:%M:%S", timeRead) .. ")")
                #     else
                #        debug("Time sync FAILED (got " .. os.date("%d/%m/%Y %H:%M:%S", timeRead) .. "; expected " .. os.date("%d/%m/%Y %H:%M:%S", timeSet))
                #     end
                #  end

                log.debug("[Process Settings] Processing settings information")

                visonic_devices = defaultdict(list)

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process panel type and serial
                
                pmPanelTypeCodeStr = self.lookupEpromSingle("panelTypeCode")
                idx = "{0:0>2}{1:0>2}".format(hex(pmPanelTypeNr).upper()[2:], hex(int(pmPanelTypeCodeStr)).upper()[2:])
                pmPanelName = pmPanelName_t[idx] if idx in pmPanelName_t else "Unknown"

                #  INTERFACE : Add this param to the status panel first
                PanelStatus["Panel Name"] = pmPanelName

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process Panel Settings to display in the user interface
                for key in DecodePanelSettings:
                    val = DecodePanelSettings[key]
                    if val.show:
                        result = self.lookupEprom(val)
                        if result is not None:
                            if (type(DecodePanelSettings[key].name) is str):
                                #log.debug( "[Process Settings]      {0:<18}  {1:<40}  {2}".format(key, DecodePanelSettings[key].name, result[0]))
                                if len(result[0]) > 0:
                                    PanelStatus[DecodePanelSettings[key].name] = result[0]
                            else:
                                #log.debug( "[Process Settings]      {0:<18}  {1}  {2}".format(key, DecodePanelSettings[key].name, result))
                                for i in range (0, len(DecodePanelSettings[key].name)):
                                    if len(result[i]) > 0:
                                        PanelStatus[DecodePanelSettings[key].name[i]] = result[i]

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process alarm settings
                self.pmSilentPanic = self.lookupEpromSingle("panicAlarm") == "Silent Panic"    # special
                self.pmBypassOff = self.lookupEpromSingle("bypass") == "No Bypass"             # special   '2':"Manual Bypass", '0':"No Bypass", '1':"Force Arm"}
                
                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process user pin codes
                if self.PowerMaster:
                    setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_MR_PINCODES"])
                else:
                    setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_PINCODES"])
                # DON'T SAVE THE USER CODES TO THE LOG
                #log.debug("[Process Settings] User Codes:")
                for i in range (0, userCnt):
                    code = setting[2 * i : 2 * i + 2]
                    self.pmPincode_t[i] = code
                    if i == 0:
                        self.pmGotUserCode = True
                    #log.debug("[Process Settings]      User {0} has code {1}".format(i, self.toString(code)))

                #if not self.PowerMaster:
                #    setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_INSTPIN"])
                #    log.debug("[Process Settings]      Installer Code {0}".format(self.toString(setting)))
                #    setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_DOWNLOADPIN"])
                #    log.debug("[Process Settings]      Download  Code {0}".format(self.toString(setting)))

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Store partition info & check if partitions are on
                if partitionCnt > 1:          # Could the panel have more than 1 partition?
                    # If that panel type can have more than 1 partition, then check to see if the panel has defined more than 1
                    partition = self.pmReadSettings(pmDownloadItem_t["MSG_DL_PARTITIONS"])
                    if partition is None or partition[0] == 255:
                        partitionCnt = 1
                    #else:    
                    #    log.debug("[Process Settings] Partition settings " + self.toString(partition))

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process zone settings
                zoneNames = bytearray()
                settingMr = bytearray()
                if not self.PowerMaster:
                    zoneNames = self.pmReadSettings(pmDownloadItem_t["MSG_DL_ZONENAMES"])
                else: # PowerMaster models
                    zoneNames = self.pmReadSettings(pmDownloadItem_t["MSG_DL_MR_ZONENAMES"])
                    settingMr = self.pmReadSettings(pmDownloadItem_t["MSG_DL_MR_ZONES"])
                    #log.debug("[Process Settings] MSG_DL_MR_ZONES Buffer " + self.toString(settingMr))

                setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_ZONES"])
                
                #zonesignalstrength = self.pmReadSettings(pmDownloadItem_t["MSG_DL_ZONESIGNAL"])
                #log.debug("[Process Settings] ZoneSignal " + self.toString(zonesignalstrength))
                #log.debug("[Process Settings] DL Zone settings " + self.toString(setting))
                #log.debug("[Process Settings] Zones Names Buffer :  {0}".format(self.toString(zoneNames)))

                if len(setting) > 0 and len(zoneNames) > 0:
                    #log.debug("[Process Settings] Zones:    len settings {0}     len zoneNames {1}    zoneCnt {2}".format(len(setting), len(zoneNames), zoneCnt))
                    for i in range(0, zoneCnt):
                        # data in the setting bytearray is in blocks of 4
                        zoneName = pmZoneName_t[zoneNames[i]]
                        zoneEnrolled = False
                        if not self.PowerMaster: # PowerMax models
                            zoneEnrolled = setting[i * 4 : i * 4 + 3] != bytearray.fromhex("00 00 00")
                            #log.debug("[Process Settings]       Zone Slice is " + self.toString(setting[i * 4 : i * 4 + 4]))
                        else: # PowerMaster models (check only 5 of 10 bytes)
                            zoneEnrolled = settingMr[i * 10 + 4 : i * 10 + 9] != bytearray.fromhex("00 00 00 00 00")
                        
                        if zoneEnrolled:
                            zoneInfo = 0
                            sensorID_c = 0
                            sensorTypeStr = ""

                            if not self.PowerMaster: #  PowerMax models
                                zoneInfo = int(setting[i * 4 + 3])            # extract the zoneType and zoneChime settings
                                sensorID_c = int(setting[i * 4 + 2])          # extract the sensorType
                                tmpid = sensorID_c & 0x0F
                                sensorTypeStr = "UNKNOWN " + str(tmpid)
                                if tmpid in pmZoneSensor_t:
                                    sensorTypeStr = pmZoneSensor_t[tmpid]
                                else:
                                    log.info("[Process Settings] Found unknown sensor type " + str(sensorID_c))

                            else: # PowerMaster models
                                zoneInfo = int(setting[i])
                                sensorID_c = int(settingMr[i * 10 + 5])
                                sensorTypeStr = "UNKNOWN " + str(sensorID_c)
                                if sensorID_c in pmZoneSensorMaster_t:
                                    sensorTypeStr = pmZoneSensorMaster_t[sensorID_c].func
                                else:
                                    log.info("[Process Settings] Found unknown sensor type " + str(sensorID_c))

                            zoneType = (zoneInfo & 0x0F)
                            zoneChime = ((zoneInfo >> 4) & 0x03)

                            part = []
                            if partitionCnt > 1:
                                for j in range (0, partitionCnt):
                                    if (partition[0x11 + i] & (1 << j)) > 0:
                                        #log.debug("[Process Settings]     Adding to partition list - ref {0}  Z{1:0>2}   Partition {2}".format(i, i+1, j+1))
                                        part.append(j+1)
                            else:
                                part = [1]

                            log.debug("[Process Settings]      i={0} :    ZTypeName={1}   Chime={2}   SensorID={3}   sensorTypeStr=[{4}]  zoneName=[{5}]".format(
                                   i, pmZoneType_t[self.pmLang][zoneType], pmZoneChime_t[self.pmLang][zoneChime], sensorID_c, sensorTypeStr, zoneName))

                            if i in self.pmSensorDev_t:
                                self.pmSensorDev_t[i].stype = sensorTypeStr
                                self.pmSensorDev_t[i].sid = sensorID_c
                                self.pmSensorDev_t[i].ztype = zoneType
                                self.pmSensorDev_t[i].ztypeName = pmZoneType_t[self.pmLang][zoneType]
                                self.pmSensorDev_t[i].zname = zoneName
                                self.pmSensorDev_t[i].zchime = pmZoneChime_t[self.pmLang][zoneChime]
                                self.pmSensorDev_t[i].dname="Z{0:0>2}".format(i+1)
                                self.pmSensorDev_t[i].partition = part
                                self.pmSensorDev_t[i].id = i+1
                                self.pmSensorDev_t[i].enrolled = True
                            else:
                                self.pmSensorDev_t[i] = SensorDevice(stype = sensorTypeStr, sid = sensorID_c, ztype = zoneType,
                                             ztypeName = pmZoneType_t[self.pmLang][zoneType], zname = zoneName, zchime = pmZoneChime_t[self.pmLang][zoneChime],
                                             dname="Z{0:0>2}".format(i+1), partition = part, id=i+1, enrolled = True)
                                visonic_devices['sensor'].append(self.pmSensorDev_t[i])

                            if i in self.pmSensorDev_t:
                                if sensorTypeStr == "Magnet" or sensorTypeStr == "Wired":
                                    doorZoneStr = "{0},Z{1:0>2}".format(doorZoneStr, i+1)
                                elif sensorTypeStr == "Motion" or sensorTypeStr == "Camera":
                                    motionZoneStr = "{0},Z{1:0>2}".format(motionZoneStr, i+1)
                                elif sensorTypeStr == "Smoke" or sensorTypeStr == "Gas":
                                    smokeZoneStr = "{0},Z{1:0>2}".format(smokeZoneStr, i+1)
                                else:
                                    otherZoneStr = "{0},Z{1:0>2}".format(otherZoneStr, i+1)
                        else:
                            #log.debug("[Process Settings]       Removing sensor {0} as it is not enrolled".format(i+1))
                            if i in self.pmSensorDev_t:
                                del self.pmSensorDev_t[i]
                                #self.pmSensorDev_t[i] = None # remove zone if needed

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process PGM/X10 settings
                setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_PGMX10"])
                x10Names = self.pmReadSettings(pmDownloadItem_t["MSG_DL_X10NAMES"])
                for i in range (0, 16):
                    x10Enabled = False
                    x10Name = 0x1F
                    for j in range (0, 8):
                        x10Enabled = x10Enabled or setting[5 + i + (j * 0x10)] != 0
                    
                    if (i > 0):
                        x10Name = x10Names[i-1]
                    
                    x10Enabled = x10Enabled or x10Name != 0x1F
                    
                    if i == 0:
                        x10Location = "PGM"
                        x10DeviceName = "PGM"
                        x10Type = "onoff"
                    else:
                        x10Location = pmZoneName_t[x10Name]
                        x10DeviceName = "X{0:0>2}".format(i)
                        x10Type = "dim"

                    if x10Enabled:
                        deviceStr = "{0},{1}".format(deviceStr, x10DeviceName)

                    if i in self.pmX10Dev_t:
                        self.pmX10Dev_t[i].name = x10DeviceName
                        self.pmX10Dev_t[i].enabled = x10Enabled
                        self.pmX10Dev_t[i].type = x10Type
                        self.pmX10Dev_t[i].location = x10Location
                        self.pmX10Dev_t[i].id = i
                    else:
                        self.pmX10Dev_t[i] = X10Device(name = x10DeviceName, type = x10Type, location = x10Location, id=i, enabled=x10Enabled)
                        visonic_devices['switch'].append(self.pmX10Dev_t[i])
                        
                    #log.debug("[Process Settings] X10 device {0} {1}".format(i, deviceStr))
                    
                # ------------------------------------------------------------------------------------------------------------------------------------------------
                if not self.PowerMaster:
                    # Process keypad settings
                    setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_1WKEYPAD"])
                    for i in range(0, keypad1wCnt):
                        keypadEnrolled = setting[i * 4 : i * 4 + 2] != bytearray.fromhex("00 00")
                        if keypadEnrolled:
                            deviceStr = "{0},K1{1:0>2}".format(deviceStr, i)
                    setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_2WKEYPAD"])
                    for i in range (0, keypad2wCnt):
                        keypadEnrolled = setting[i * 4 : i * 4 + 3] != bytearray.fromhex("00 00 00")
                        if keypadEnrolled:
                            deviceStr = "{0},K2{1:0>2}".format(deviceStr, i)

                    # Process siren settings
                    setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_SIRENS"])
                    for i in range(0, sirenCnt):
                        sirenEnrolled = setting[i * 4 : i * 4 + 3] != bytearray.fromhex("00 00 00")
                        if sirenEnrolled:
                            deviceStr = "{0},S{1:0>2}".format(deviceStr, i)
                else: # PowerMaster
                    # Process keypad settings
                    setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_MR_KEYPADS"])
                    for i in range(0, keypad2wCnt):
                        keypadEnrolled = setting[i * 10 : i * 10 + 5] != bytearray.fromhex("00 00 00 00 00")
                        if keypadEnrolled:
                            deviceStr = "{0},K2{1:0>2}".format(deviceStr, i)
                    # Process siren settings
                    setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_MR_SIRENS"])
                    for i in range (0, sirenCnt):
                        sirenEnrolled = setting[i * 10 : i * 10 + 5] != bytearray.fromhex("00 00 00 00 00")
                        if sirenEnrolled:
                            deviceStr = "{0},S{1:0>2}".format(deviceStr, i)

                doorZones = doorZoneStr[1:]
                motionZones = motionZoneStr[1:]
                smokeZones = smokeZoneStr[1:]
                devices = deviceStr[1:]
                otherZones = otherZoneStr[1:]

                log.debug("[Process Settings] Adding zone devices")

                PanelStatus["Door Zones"] = doorZones
                PanelStatus["Motion Zones"] = motionZones
                PanelStatus["Smoke Zones"] = smokeZones
                PanelStatus["Other Zones"] = otherZones
                PanelStatus["Devices"] = devices

                #  INTERFACE : Add these self.pmSensorDev_t[i] params to the status panel
                self.sendResponseEvent ( visonic_devices )

                # INTERFACE : Create Partitions in the interface
                #for i in range(1, partitionCnt+1): # TODO

                log.info("[Process Settings] Ready for use")
            
            else:
                log.warning("[Process Settings] WARNING: Cannot process panel EPROM settings, download has not completed")
            
        elif pmPanelTypeNr is None or pmPanelTypeNr == 0xFF:
            log.warning("[Process Settings] WARNING: Cannot process panel EPROM settings, we're probably connected to the panel in standard mode")
        else:
            log.warning("[Process Settings] WARNING: Cannot process panel EPROM settings, the panel is too new")

        self.DumpSensorsToDisplay()
        
        if self.pmPowerlinkMode:
            PanelStatus["Mode"] = "Powerlink"
        elif self.pmDownloadComplete and self.pmGotUserCode:
            log.info("[Process Settings] Entering Standard Plus Mode as we got the pin codes from the EPROM")
            PanelStatus["Mode"] = "Standard Plus"
        else:
            PanelStatus["Mode"] = "Standard"

        if PanelSettings["AutoSyncTime"]:  # should we sync time between the HA and the Alarm Panel
            t = datetime.now()
            if t.year > 2000:
                year = t.year - 2000
                values = [t.second, t.minute, t.hour, t.day, t.month, year]
                timePdu = bytearray(values)
                #self.pmSyncTimeCheck = t
                self.SendCommand("MSG_SETTIME", options = [3, timePdu] )
            else:
                log.info("[Process Settings] Please correct your local time.")

    # This function handles a received message packet and processes it
    def handle_packet(self, packet):
        """Handle one raw incoming packet."""

        # Check the current packet against the last packet to determine if they are the same
        if self.lastPacket is not None:
            if self.lastPacket == packet and packet[1] == 0xA5:   # only consider A5 packets for consecutive error
                self.lastPacketCounter = self.lastPacketCounter + 1
            else:
                self.lastPacketCounter = 0        
        self.lastPacket = packet
        
        if self.lastPacketCounter == SAME_PACKET_ERROR:
            log.debug("[handle_packet] Had the same packet for 20 times in a row : %s", self.toString(packet))
            # PerformDisconnect
            self.PerformDisconnect("Same Packet for {0} times in a row".format(SAME_PACKET_ERROR))
        #else:
        #    log.debug("[handle_packet] Parsing complete valid packet: %s", self.toString(packet))

        if len(packet) < 4:  # there must at least be a header, command, checksum and footer
            log.warning("[handle_packet] Received invalid packet structure, not processing it " + self.toString(packet))
        elif packet[1] == 0x02: # ACK
            self.handle_msgtype02(packet[2:-2])  # remove the header and command bytes as the start. remove the footer and the checksum at the end
        elif packet[1] == 0x06: # Timeout
            self.handle_msgtype06(packet[2:-2])
        elif packet[1] == 0x08: # Access Denied
            self.handle_msgtype08(packet[2:-2])
        elif packet[1] == 0x0B: # Stopped
            self.handle_msgtype0B(packet[2:-2])
        elif packet[1] == 0x25: # Download retry
            self.handle_msgtype25(packet[2:-2])
        elif packet[1] == 0x33: # Settings send after a MSGV_START
            self.handle_msgtype33(packet[2:-2])
        elif packet[1] == 0x3c: # Message when start the download
            self.handle_msgtype3C(packet[2:-2])
        elif packet[1] == 0x3f: # Download information
            self.handle_msgtype3F(packet[2:-2])
        elif packet[1] == 0xa0: # Event log
            self.handle_msgtypeA0(packet[2:-2])
        elif packet[1] == 0xa3: # Event log
            self.handle_msgtypeA3(packet[2:-2])
        elif packet[1] == 0xa5: # General Event
            self.handle_msgtypeA5(packet[2:-2])
        elif packet[1] == 0xa6: # General Event
            self.handle_msgtypeA6(packet[2:-2])
        elif packet[1] == 0xa7: # General Event
            self.handle_msgtypeA7(packet[2:-2])
        elif packet[1] == 0xab and not self.ForceStandardMode: # PowerLink Event. Only process AB if not forced standard
            self.handle_msgtypeAB(packet[2:-2])
        elif packet[1] == 0xac: # X10 Names
            self.handle_msgtypeAC(packet[2:-2])
        elif packet[1] == 0xb0: # PowerMaster Event
            self.handle_msgtypeB0(packet[2:-2])
        else:
            log.info("[handle_packet] Unknown/Unhandled packet type {0}".format(packet[1:2]))

    def displayzonebin(self, bits):
        """ Display Zones in reverse binary format
          Zones are from e.g. 1-8, but it is stored in 87654321 order in binary format """
        return bin(bits)

    def handle_msgtype02(self, data): # ACK
        """ Handle Acknowledges from the panel """
        # Normal acknowledges have msgtype 0x02 but no data, when in powerlink the panel also sends data byte 0x43
        #    I have not found this on the internet, this is my hypothesis
        log.debug("[handle_msgtype02] Ack Received  data = {0}".format(self.toString(data)))
        if not self.pmPowerlinkMode and len(data) > 0:
            if data[0] == 0x43:  # Received a powerlink acknowledge
                log.info("[handle_msgtype02]    Received a powerlink acknowledge but I am in standard mode")
                if self.allowAckToTriggerRestore:
                    log.info("[handle_msgtype02]        and sending MSG_RESTORE")
                    self.SendCommand("MSG_RESTORE")
                    self.allowAckToTriggerRestore = False


    def handle_msgtype06(self, data):
        """ MsgType=06 - Time out
        Timeout message from the PM, most likely we are/were in download mode """
        log.info("[handle_msgtype06] Timeout Received  data {0}".format(self.toString(data)))
        
        if self.pmDownloadMode:
            self.pmDownloadMode = False
            # exit download mode and try again in DOWNLOAD_RETRY_DELAY seconds
            self.SendCommand("MSG_STOP")
            self.SendCommand("MSG_EXIT")
        else:
            self.SendAck()


    def handle_msgtype08(self, data):
        log.info("[handle_msgtype08] Access Denied  len {0} data {1}".format(len(data), self.toString(data)))

        if self.getLastSentMessage() is not None:
            lastCommandData = self.getLastSentMessage().command.data
            log.debug("[handle_msgtype08]                last command {0}".format( self.toString(lastCommandData)))
            self.reset_watchdog_timeout()
            if lastCommandData is not None:
                self.pmExpectedResponse = []  ## really we should look at the response from the last command and only remove the appropriate responses from this list
                if lastCommandData[0] != 0xAB and lastCommandData[0] & 0xA0 == 0xA0:  # this will match A0, A1, A2, A3 etc but not 0xAB
                    log.debug("[handle_msgtype08] Attempt to send a command message to the panel that has been denied, wrong pin code used")
                    # INTERFACE : tell user that wrong pin has been used
                    self.sendResponseEvent ( 5 )  # push changes through to the host, the pin has been rejected
                    
                elif lastCommandData[0] == 0x24:
                    log.debug("[handle_msgtype08] Got an Access Denied and we have sent a Download command to the Panel, so try to Enroll")
                    self.pmDownloadMode = False
                    self.SendMsg_ENROLL()


    def handle_msgtype0B(self, data): # STOP
        """ Handle STOP from the panel """
        log.info("[handle_msgtype0B] Stop    data is {0}".format(self.toString(data)))


    def handle_msgtype25(self, data): # Download retry
        """ MsgType=25 - Download retry. Unit is not ready to enter download mode """
        # Format: <MsgType> <?> <?> <delay in sec>
        iDelay = data[2]
        log.info("[handle_msgtype25] Download Retry, have to wait {0} seconds     data is {1}".format(iDelay, self.toString(data)))        
        # exit download mode
        self.pmDownloadMode = False
        #sleep(iDelay + 1)
        # exit download mode and try again in DOWNLOAD_RETRY_DELAY seconds
        self.SendCommand("MSG_STOP")
        self.SendCommand("MSG_EXIT")


    def handle_msgtype33(self, data):
        """ MsgType=33 - Settings
        Message sent after a MSG_START. We will store the information in an internal array/collection """

        if len(data) != 10:
            log.info("[handle_msgtype33] ERROR: MSGTYPE=0x33 Expected len=14, Received={0}".format(len(data)))
            log.info("[handle_msgtype33]                            " + self.toString(data))
            return

        # Data Format is: <index> <page> <8 data bytes>
        # Extract Page and Index information
        iIndex = data[0]
        iPage = data[1]

        #log.debug("[handle_msgtype33] Getting Data " + self.toString(data) + "   page " + hex(iPage) + "    index " + hex(iIndex))

        # Write to memory map structure, but remove the first 2 bytes from the data
        self.pmWriteSettings(iPage, iIndex, data[2:])


    def handle_msgtype3C(self, data): # Panel Info Messsage when start the download
        """ The panel information is in 4 & 5
           5=PanelType e.g. PowerMax, PowerMaster
           4=Sub model type of the panel - just informational, not used
           """
        self.ModelType = data[4]
        self.PanelType = data[5]

        self.PowerMaster = (self.PanelType >= 7)
        modelname = pmPanelType_t[self.PanelType] or "UNKNOWN"  # INTERFACE set this in the user interface

        PanelStatus["Model Type"] = self.ModelType
        PanelStatus["Power Master"] = 'Yes' if self.PowerMaster else 'No'

        log.debug("[handle_msgtype3C] PanelType={0} : {2} , Model={1}   Powermaster {3}".format(self.PanelType, self.ModelType, modelname, self.PowerMaster))

        # We got a first response, now we can Download the panel EPROM settings
        interval = self.getTimeFunction() - self.lastSendOfDownloadEprom
        td = timedelta(seconds=90)  # prevent multiple requests for the EPROM panel settings, at least 90 seconds 
        if interval > td:
            self.lastSendOfDownloadEprom = self.getTimeFunction()
            self.pmReadPanelSettings(self.PowerMaster)


    def handle_msgtype3F(self, data):
        """ MsgType=3F - Download information
        Multiple 3F can follow eachother, if we request more then &HFF bytes """

        log.info("[handle_msgtype3F]")
        # data format is normally: <index> <page> <length> <data ...>
        # If the <index> <page> = FF, then it is an additional PowerMaster MemoryMap
        iIndex = data[0]
        iPage = data[1]
        iLength = data[2]

        # Check length and data-length
        if iLength != len(data) - 3:  # 3 because -->   index & page & length
            log.info("[handle_msgtype3F] ERROR: Type=3F has an invalid length, Received: {0}, Expected: {1}".format(len(data)-3, iLength))
            log.info("[handle_msgtype3F]                            " + self.toString(data))
            return

        # Write to memory map structure, but remove the first 4 bytes (3F/index/page/length) from the data
        self.pmWriteSettings(iPage, iIndex, data[3:])

        if len(self.myDownloadList) > 0:
            self.SendCommand("MSG_DL", options = [1, self.myDownloadList.pop(0)] )     # Read the names of the zones
        else:
            # This is the message to tell us that the panel has finished download mode, so we too should stop download mode
            self.pmDownloadMode = False
            self.pmDownloadComplete = True
            if self.getLastSentMessage() is not None:
                lastCommandData = self.getLastSentMessage().command.data
                log.debug("[handle_msgtype3F]                last command {0}".format(self.toString(lastCommandData)))
                if lastCommandData is not None:
                    if lastCommandData[0] == 0x3E:  # Download Data
                        log.info("[handle_msgtype3F] We're almost in powerlink mode *****************************************")
                        self.pmPowerlinkModePending = True
            else:
                log.debug("[handle_msgtype3F]                no last command")
            self.SendCommand("MSG_EXIT")       # Exit download mode
            # We received a download exit message, restart timer
            self.reset_watchdog_timeout()
            self.ProcessSettings()


    def handle_msgtypeA0(self, data):
        """ MsgType=A0 - Event Log """
        log.info("[handle_MsgTypeA0] Packet = {0}".format(self.toString(data)))
        
        eventNum = data[1]
        # Check for the first entry, it only contains the number of events
        if eventNum == 0x01:
            log.debug("[handle_msgtypeA0] Eventlog received")
            self.eventCount = data[0]
        else:
            iSec = data[2]
            iMin = data[3]
            iHour = data[4]
            iDay = data[5]
            iMonth = data[6]
            iYear = int(data[7]) + 2000

            iEventZone = data[8]
            iLogEvent = data[9]
            zoneStr = pmLogUser_t[self.pmLang][iEventZone] or "UNKNOWN"
            eventStr = pmLogEvent_t[self.pmLang][iLogEvent] or "UNKNOWN"

            idx = eventNum - 1

            # Create an event log array
            self.pmEventLogDictionary[idx] = LogPanelEvent()
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
            #self.pmEventLogDictionary.items = idx
            #self.pmEventLogDictionary.done = (eventNum == self.eventCount)
            log.debug("[handle_msgtypeA0] Log Event {0}".format(self.pmEventLogDictionary[idx]))
            
            # Send the event log in to HA
            self.sendResponseEvent ( self.pmEventLogDictionary[idx] )

            
    def handle_msgtypeA3(self, data):
        """ MsgType=A3 - Zone Names """
        log.info("[handle_MsgTypeA3] Packet = {0}".format(self.toString(data)))
        if not self.pmPowerlinkMode:
            msgCnt = int(data[0])
            offset = 8 * (int(data[1]) - 1)
            for i in range(0, 8):
                zoneName = pmZoneName_t[int(data[2+i])]
                log.info("                        Zone name for zone {0} is {1}     Message Count is {2}".format( offset+i+1, zoneName, msgCnt ))
                if offset+i in self.pmSensorDev_t:
                    if not self.pmSensorDev_t[offset+i].zname:     # if not already set
                        self.pmSensorDev_t[offset+i].zname = zoneName
                        # self.pmSensorDev_t[offset+i].pushChange()
                        # log.info("                        Found Sensor")
        
#    def displaySensorBypass(self, sensor):
#        armed = False
#        if self.pmSensorShowBypass:
#            armed = sensor.bypass
#        else:
#            zoneType = sensor.ztype
#            mode = bitw.band(pmSysStatus, 0x0F) -- armed or not: 4=armed home; 5=armed away
#		 local alwaysOn = { [2] = "", [3] = "", [9] = "", [10] = "", [11] = "", [14] = "" }
		 # Show as armed if
		 #    a) the sensor type always triggers an alarm: (2)flood, (3)gas, (11)fire, (14)temp, (9/10)24h (silent/audible)
		 #    b) the system is armed away (mode = 4)
		 #    c) the system is armed home (mode = 5) and the zone is not interior(-follow) (6,12)
#         armed = ((zoneType > 0) and (sensor['bypass'] ~= true) and ((alwaysOn[zoneType] ~= nil) or (mode == 0x5) or ((mode == 0x4) and (zoneType % 6 ~= 0)))) and "1" or "0"


    def makeInt(self, data) -> int:
        if len(data) == 4:
            val = data[0]
            val = val + (0x100 * data[1])
            val = val + (0x10000 * data[2])
            val = val + (0x1000000 * data[3])
            return int(val)
        return 0

    # captured examples of A5 data
    #     0d a5 00 04 00 61 03 05 00 05 00 00 43 a4 0a
    def handle_msgtypeA5(self, data): # Status Message

        #msgTot = data[0]
        eventType = data[1]

        log.info("[handle_msgtypeA5] Parsing A5 packet " + self.toString(data))

        if eventType == 0x01: # Zone alarm status
            log.debug("[handle_msgtypeA5] Zone Alarm Status")
            val = self.makeInt(data[2:6])
            if val != self.zonealarm_old:
                self.zonealarm_old = val
                log.debug("[handle_msgtypeA5]      Zone Trip Alarm 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        self.pmSensorDev_t[i].ztrip = (val & (1 << i) != 0)
                        self.pmSensorDev_t[i].pushChange()
                
            val = self.makeInt(data[6:10])
            if val != self.zonetamper_old:
                self.zonetamper_old = val
                log.debug("[handle_msgtypeA5]      Zone Tamper Alarm 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        self.pmSensorDev_t[i].ztamper = (val & (1 << i) != 0)
                        self.pmSensorDev_t[i].pushChange()

        elif eventType == 0x02: # Status message - Zone Open Status
            # if in standard mode then use this A5 status message to reset the watchdog timer        
            if not self.pmPowerlinkMode:
                log.debug("[handle_msgtypeA5]      Got A5 02 message, resetting watchdog")
                self.reset_watchdog_timeout()

            val = self.makeInt(data[2:6])
            if val != self.status_old:
                self.status_old = val
                log.debug("[handle_msgtypeA5]      Open Door/Window Status Zones 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        alreadyset = self.pmSensorDev_t[i].status
                        self.pmSensorDev_t[i].status = (val & (1 << i) != 0)
                        if not alreadyset and self.pmSensorDev_t[i].status:
                            self.pmSensorDev_t[i].triggered = True
                            self.pmSensorDev_t[i].triggertime = self.getTimeFunction()
                        self.pmSensorDev_t[i].pushChange()

            val = self.makeInt(data[6:10])
            if val != self.lowbatt_old:
                self.lowbatt_old = val
                log.debug("[handle_msgtypeA5]      Battery Low Zones 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        self.pmSensorDev_t[i].lowbatt = (val & (1 << i) != 0)
                        self.pmSensorDev_t[i].pushChange()

        elif eventType == 0x03: # Tamper Event
            val = self.makeInt(data[2:6])
            log.debug("[handle_msgtypeA5]      Trigger (Inactive) Status Zones 32-01: {:032b}".format(val))
            # This status is different from the status in the 0x02 part above i.e they are different values.
            #    This one is wrong (I had a door open and this status had 0, the one above had 1)
            #       According to domotica forum, this represents "active" but what does that actually mean?
            #for i in range(0, 32):
            #    if i in self.pmSensorDev_t:
            #        self.pmSensorDev_t[i].status = (val & (1 << i) != 0)

            val = self.makeInt(data[6:10])
            if val != self.tamper_old:
                self.tamper_old = val
                log.debug("[handle_msgtypeA5]      Tamper Zones 32-01: {:032b}".format(val))
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        self.pmSensorDev_t[i].tamper = (val & (1 << i) != 0)
                        self.pmSensorDev_t[i].pushChange()

        elif eventType == 0x04: # Zone event
            sysStatus = data[2]
            sysFlags  = data[3]
            eventZone = data[4]
            eventType  = data[5]
            # dont know what 6 and 7 are
            x10stat1  = data[8]
            x10stat2  = data[9]
            x10status = x10stat1 + (x10stat2 * 0x100)
            
            log.debug("[handle_msgtypeA5]      Zone Event sysStatus {0}   sysFlags {1}   eventZone {2}   eventType {3}   x10status {4}".format(hex(sysStatus), hex(sysFlags), eventZone, eventType, hex(x10status)))

            # Examine zone tripped status
            if eventZone != 0:
                log.debug("[handle_msgtypeA5]      Event {0} in zone {1}".format(pmEventType_t[self.pmLang][eventType] or "UNKNOWN", eventZone))
                if eventZone in self.pmSensorDev_t:
                    sensor = self.pmSensorDev_t[eventZone]
                    if sensor is not None:
                        log.debug("[handle_msgtypeA5]      zone type {0} device tripped {1}".format(eventType, eventZone))
                    else:
                        log.debug("[handle_msgtypeA5]      unable to locate zone device " + str(eventZone))


            # Examine X10 status
            for i in range(0, 16):
                status = x10status & (1 << i)
                if i in self.pmX10Dev_t:
                    # INTERFACE : use this to set X10 status
                    oldstate = self.pmX10Dev_t[i].state
                    self.pmX10Dev_t[i].state = bool(status)
                    # Check to see if the state has changed
                    if ( oldstate and not self.pmX10Dev_t[i].state ) or (not oldstate and self.pmX10Dev_t[i].state):
                        log.debug("[handle_msgtypeA5]      X10 device {0} changed to {1}".format(i, status))
                        self.pmX10Dev_t[i].pushChange()


            slog = pmDetailedArmMode_t[sysStatus]
            sarm_detail = "Unknown"
            if 0 <= sysStatus < len(pmSysStatus_t[self.pmLang]):
                sarm_detail = pmSysStatus_t[self.pmLang][sysStatus]

            if sysStatus == 7 and self.pmDownloadComplete:  # sysStatus == 7 means panel "downloading"
                log.debug("[handle_msgtypeA5]      Sending a STOP and EXIT as we seem to be still in the downloading state and it should have finished")
                self.SendCommand("MSG_STOP")
                self.SendCommand("MSG_EXIT")


            # -1  Not yet defined
            # 0   Disarmed
            # 1   Exit Delay Arm Home
            # 2   Exit Delay Arm Away
            # 3   Entry Delay
            # 4   Armed Home
            # 5   Armed Away
            # 6   Special ("User Test", "Downloading", "Programming", "Installer")
			
            if sysStatus in [0x03]:
                sarm = "Armed"
                PanelStatus["Panel Status Code"] = 3   # Entry Delay
            elif sysStatus in [0x04, 0x0A, 0x13, 0x14]:
                sarm = "Armed"
                PanelStatus["Panel Status Code"] = 4   # Armed Home
            elif sysStatus in [0x05, 0x0B, 0x15]:
                sarm = "Armed"
                PanelStatus["Panel Status Code"] = 5   # Armed Away
            elif sysStatus in [0x01, 0x11]:
                sarm = "Arming"
                PanelStatus["Panel Status Code"] = 1   # Arming Home
            elif sysStatus in [0x02, 0x12]:
                sarm = "Arming"
                PanelStatus["Panel Status Code"] = 2   # Arming Away
            elif sysStatus in [0x06, 0x07, 0x08, 0x09]:
                sarm = "Disarmed"
                PanelStatus["Panel Status Code"] = 6   # Special ("User Test", "Downloading", "Programming", "Installer")
            elif sysStatus > 0x15:
                log.debug("[handle_msgtypeA5]      Unknown state, assuming Disarmed")
                sarm = "Disarmed"
                PanelStatus["Panel Status Code"] = 0   # Disarmed
            else:
                sarm = "Disarmed"
                PanelStatus["Panel Status Code"] = 0   # Disarmed

            log.debug("[handle_msgtypeA5]      log: {0}, arm: {1}".format(slog + "(" + sarm_detail + ")", sarm))

            #PanelStatus["Panel Status Code"]    = sysStatus
            PanelStatus["Panel Status"]        = sarm_detail
            PanelStatus["Panel Ready"]         = 'Yes' if sysFlags & 0x01 != 0 else 'No'
            PanelStatus["Panel Alert In Memory"] = 'Yes' if sysFlags & 0x02 != 0 else 'No'
            PanelStatus["Panel Trouble"]       = 'Yes' if sysFlags & 0x04 != 0 else 'No'
            PanelStatus["Panel Bypass"]        = 'Yes' if sysFlags & 0x08 != 0 else 'No'
            if sysFlags & 0x10 != 0:  # last 10 seconds of entry/exit
                PanelStatus["Panel Armed"] = 'Yes' if (sarm == "Arming") else 'No'
            else:
                PanelStatus["Panel Armed"] = 'Yes' if (sarm == "Armed") else 'No'
            PanelStatus["Panel Status Changed"] = 'Yes' if sysFlags & 0x40 != 0 else 'No'
            PanelStatus["Panel Alarm Event"]    = 'Yes' if sysFlags & 0x80 != 0 else 'No'

            #cond = ""
            #for i in range(0,8):
            #    if sysFlags & (1<<i) != 0:
            #        cond = cond + pmSysStatusFlags_t[self.pmLang][i] + ", "
            #if len(cond) > 0:
            #    cond = cond[:-2]
            #PanelStatus["PanelStatusText"] = cond

            if not self.pmPowerlinkMode:
                # if the system status has the panel armed and there has been an alarm event, assume that the alarm is sounding
                #   Normally this would only be directly available in Powerlink mode with A7 messages, but an assumption is made here
                if sarm == "Armed" and sysFlags & 0x80 != 0:
                    log.info("[handle_msgtypeA5]      Alarm Event Assumed while in Standard Mode")
                    # Alarm Event 
                    self.pmSirenActive = self.getTimeFunction()
                    self.sendResponseEvent ( 3 )   # Alarm Event

            # Clear any alarm event if the panel alarm has been triggered before (while armed) but now that the panel is disarmed (in all modes)
            if self.pmSirenActive is not None and sarm == "Disarmed":
                log.info("[handle_msgtypeA5] ******************** Alarm Not Sounding (Disarmed) ****************")
                self.pmSirenActive = None
            PanelStatus["Panel Siren Active"] = 'Yes' if self.pmSirenActive != None else 'No'

            if sysFlags & 0x20 != 0:
                sEventLog = pmEventType_t[self.pmLang][eventType]
                log.debug("[handle_msgtypeA5]      Bit 5 set, Zone Event")
                log.debug("[handle_msgtypeA5]            Zone: {0}, {1}".format(eventZone, sEventLog))
                for key, value in self.pmSensorDev_t.items():
                    if value.id == eventZone:      # look for the device name
                        if eventType == 1: # Tamper Alarm
                            self.pmSensorDev_t[key].tamper = True
                            self.pmSensorDev_t[key].pushChange()
                        elif eventType == 2: # Tamper Restore
                            self.pmSensorDev_t[key].tamper = False
                            self.pmSensorDev_t[key].pushChange()
                        elif eventType == 3: # Zone Open
                            self.pmSensorDev_t[key].triggered = True
                            self.pmSensorDev_t[key].status = True
                            self.pmSensorDev_t[key].triggertime = self.getTimeFunction()
                            self.pmSensorDev_t[key].pushChange()
                        elif eventType == 4: # Zone Closed
                            self.pmSensorDev_t[key].triggered = False
                            self.pmSensorDev_t[key].status = False
                            self.pmSensorDev_t[key].pushChange()
                        elif eventType == 5: # Zone Violated
                            self.pmSensorDev_t[key].triggered = True
                            self.pmSensorDev_t[key].triggertime = self.getTimeFunction()
                            self.pmSensorDev_t[key].pushChange()
                        #elif eventType == 6: # Panic Alarm
                        #elif eventType == 7: # RF Jamming
                        #elif eventType == 8: # Tamper Open
                        #    self.pmSensorDev_t[key].pushChange()
                        #elif eventType == 9: # Comms Failure
                        #elif eventType == 10: # Line Failure
                        #elif eventType == 11: # Fuse
                        #elif eventType == 12: # Not Active
                        #    self.pmSensorDev_t[key].triggered = False
                        #    self.pmSensorDev_t[key].status = False
                        #    self.pmSensorDev_t[key].pushChange()
                        elif eventType == 13: # Low Battery
                            self.pmSensorDev_t[key].lowbatt = True
                            self.pmSensorDev_t[key].pushChange()
                        #elif eventType == 14: # AC Failure
                        #elif eventType == 15: # Fire Alarm
                        #elif eventType == 16: # Emergency
                        #elif eventType == 17: # Siren Tamper
                        #    self.pmSensorDev_t[key].tamper = True
                        #    self.pmSensorDev_t[key].pushChange()
                        #elif eventType == 18: # Siren Tamper Restore
                        #    self.pmSensorDev_t[key].tamper = False
                        #    self.pmSensorDev_t[key].pushChange()
                        #elif eventType == 19: # Siren Low Battery
                        #    self.pmSensorDev_t[key].lowbatt = True
                        #    self.pmSensorDev_t[key].pushChange()
                        #elif eventType == 20: # Siren AC Fail

            self.sendResponseEvent ( 1 )   # push changes through to the host to get it to update
            
            #armModeNum = 1 if pmArmed_t[sysStatus] != None else 0
            #armMode = "Armed" if armModeNum == 1 else "Disarmed"
            
        elif eventType == 0x06: # Status message enrolled/bypassed
            # e.g. 00 06 7F 00 00 10 00 00 00 00 43
            val = self.makeInt(data[2:6])
            
            if val != self.enrolled_old:
                log.debug("[handle_msgtypeA5]      Enrolled Zones 32-01: {:032b}".format(val))
                send_zone_type_request = False
                visonic_devices = defaultdict(list)
                self.enrolled_old = val
                for i in range(0, 32):
                    # if the sensor is enrolled
                    if val & (1 << i) != 0:
                        # do we already know about the sensor from the EPROM decode
                        if i in self.pmSensorDev_t:
                            self.pmSensorDev_t[i].enrolled = True
                        else:
                            # we dont know about it so create it and make it enrolled
                            self.pmSensorDev_t[i] = SensorDevice(dname="Z{0:0>2}".format(i+1), id=i+1, enrolled = True)
                            visonic_devices['sensor'].append(self.pmSensorDev_t[i])
                            if not send_zone_type_request:
                                self.SendCommand("MSG_ZONENAME")
                                self.SendCommand("MSG_ZONETYPE")
                                send_zone_type_request = True
                            
                    elif i in self.pmSensorDev_t:
                        # it is not enrolled and we already know about it from the EPROM, set enrolled to False
                        self.pmSensorDev_t[i].enrolled = False

                self.sendResponseEvent ( visonic_devices )

            val = self.makeInt(data[6:10])
            if val != self.bypass_old:
                log.debug("[handle_msgtypeA5]      Bypassed Zones 32-01: {:032b}".format(val))
                self.bypass_old = val
                for i in range(0, 32):
                    if i in self.pmSensorDev_t:
                        self.pmSensorDev_t[i].bypass = (val & (1 << i) != 0)
                        self.pmSensorDev_t[i].pushChange()

            self.DumpSensorsToDisplay()
        #else:
        #    log.debug("[handle_msgtypeA5]      Unknown A5 Event: %s", hex(eventType))


    def handle_msgtypeA6(self, data):
        """ MsgType=A6 - Zone Types I think """
        log.info("[handle_MsgTypeA6] Packet = {0}".format(self.toString(data)))
        if not self.pmPowerlinkMode:
            msgCnt = int(data[0])
            offset = 8 * (int(data[1]) - 1)
            for i in range (0, 8):
                zoneType = ((int(data[2+i])) - 0x1E) & 0x0F
                log.debug("                        Zone type for {0} is {1} {2}".format( offset+i+1, hex(zoneType).upper(), pmZoneType_t[self.pmLang][zoneType] ))
                if (offset+i) in self.pmSensorDev_t:
                    self.pmSensorDev_t[offset+i].ztypeName = pmZoneType_t[self.pmLang][zoneType]
                    self.pmSensorDev_t[offset+i].ztype = zoneType
                    #self.pmSensorDev_t[offset+i].zchime = pmZoneChime_t[self.pmLang][zoneChime]
                    self.pmSensorDev_t[offset+i].pushChange()


    def handle_msgtypeA7(self, data):
        """ MsgType=A7 - Panel Status Change """
        log.info("[handle_msgtypeA7] Panel Status Change " + self.toString(data))
        # 
        #   In a log file I reveiced from pocket,    there was this A7 message 0d a7 ff fc 00 60 00 ff 00 0c 00 00 43 45 0a
        #   In a log file I reveiced from UffeNisse, there was this A7 message 0d a7 ff 64 00 60 00 ff 00 0c 00 00 43 45 0a     msgCnt is 0xFF and temp is 0x64 ????
        #                                                                          

        pmTamperActive = None
        msgCnt = int(data[0])

        # don't know what this is (It is 0x00 in test messages so could be the higher 8 bits for msgCnt)
        temp = int(data[1])
        
        if msgCnt <= 4:
            log.debug("[handle_msgtypeA7]      A7 message contains {0} messages".format(msgCnt))
            for i in range(0, msgCnt):
                eventZone = int(data[2 + (2 * i)])
                logEvent  = int(data[3 + (2 * i)])
                eventType = int(logEvent & 0x7F)
                s = (pmLogEvent_t[self.pmLang][eventType] or "UNKNOWN") + " / " + (pmLogUser_t[self.pmLang][eventZone] or "UNKNOWN")

                #---------------------------------------------------------------------------------------
                alarmStatus = None
                if eventType in pmPanelAlarmType_t:
                    alarmStatus = pmPanelAlarmType_t[eventType]
                troubleStatus = None
                if eventType in pmPanelTroubleType_t:
                    troubleStatus = pmPanelTroubleType_t[eventType]

                PanelStatus["Panel Last Event"]     = s
                PanelStatus["Panel Alarm Status"]   = "None" if alarmStatus is None else alarmStatus
                PanelStatus["Panel Trouble Status"] = "None" if troubleStatus is None else troubleStatus

                log.info("[handle_msgtypeA7]         System message " + s + "  alarmStatus " + PanelStatus["Panel Alarm Status"] + "   troubleStatus " + PanelStatus["Panel Trouble Status"])

                #---------------------------------------------------------------------------------------
                # Update tamper and siren status
                # 0x06 is "Tamper", 0x07 is "Control Panel Tamper", 0x08 is "Tamper Alarm", 0x09 is "Tamper Alarm"
                tamper = eventType == 0x06 or eventType == 0x07 or eventType == 0x08 or eventType == 0x09
                
                # 0x01 is "Interior Alarm", 0x02 is "Perimeter Alarm", 0x03 is "Delay Alarm", 0x05 is "24h Audible Alarm"
                # 0x04 is "24h Silent Alarm", 0x0B is "Panic From Keyfob", 0x0C is "Panic From Control Panel"
                siren = eventType == 0x01 or eventType == 0x02 or eventType == 0x03 or eventType == 0x05

                if tamper:
                    pmTamperActive = self.getTimeFunction()

                if not self.pmSilentPanic and siren:
                    self.pmSirenActive = self.getTimeFunction()
                    log.info("[handle_msgtypeA7] ******************** Alarm Active *******************")
                
                # 0x1B is a cancel alarm
                if eventType == 0x1B and self.pmSirenActive is not None: # Cancel Alarm
                    self.pmSirenActive = None
                    log.info("[handle_msgtypeA7] ******************** Alarm Cancelled ****************")
                
                # Siren has been active but it is no longer active (probably timed out and has then been disarmed)
                if not siren and self.pmSirenActive is not None: # Alarm Timed Out ????
                    self.pmSirenActive = None
                    log.info("[handle_msgtypeA7] ******************** Alarm Not Sounding ****************")
                
                # INTERFACE Indicate whether siren active
                PanelStatus["Panel Siren Active"] = 'Yes' if self.pmSirenActive != None else 'No'

                log.info("[handle_msgtypeA7]           self.pmSirenActive={0}   siren={1}   eventType={2}   self.pmSilentPanic={3}   tamper={4}".format(self.pmSirenActive, siren, hex(eventType), self.pmSilentPanic, tamper) )
                
                #---------------------------------------------------------------------------------------
                if eventType == 0x60: # system restart
                    log.warning("[handle_msgtypeA7]         Panel has been reset. Don't do anything and the comms might reconnect and magically continue")
                    self.sendResponseEvent ( 4 )   # push changes through to the host, the panel itself has been reset

            if pmTamperActive is not None:
                log.info("[handle_msgtypeA7] ******************** Tamper Triggered *******************")
                self.sendResponseEvent ( 6 )   # push changes through to the host to get it to update, tamper is active!
                    
            if self.pmSirenActive is not None:
                self.sendResponseEvent ( 3 )   # push changes through to the host to get it to update, alarm is active!!!!!!!!!
            else:
                self.sendResponseEvent ( 2 )   # push changes through to the host to get it to update
        else:  ## message count is more than 4
            log.warning("[handle_msgtypeA7]      A7 message contains too many messages to process : {0}   data={1}".format(msgCnt, self.toString(data)))


    # pmHandlePowerlink (0xAB)
    def handle_msgtypeAB(self, data): # PowerLink Message
        """ MsgType=AB - Panel Powerlink Messages """
        log.info("[handle_msgtypeAB]  data {0}".format(self.toString(data)))

        # Restart the timer
        self.reset_watchdog_timeout()

        subType = data[0]
        if subType == 3: # keepalive message
            # Example 0D AB 03 00 1E 00 31 2E 31 35 00 00 43 2A 0A
            log.info("[handle_msgtypeAB] ***************************** Got PowerLink Keep-Alive ****************************")
            # It is possible to receive this between enrolling (when the panel accepts the enroll successfully) and the EPROM download
            #     I suggest we simply ignore it
            if self.pmPowerlinkModePending:
                log.info("[handle_msgtypeAB]         Got alive message while Powerlink mode pending, going to full powerlink and calling Restore")
                self.pmPowerlinkMode = True
                self.pmPowerlinkModePending = False
                PanelStatus["Mode"] = "Powerlink"  # it is truly in powerlink now we are receiving powerlink alive messages from the panel
                self.triggerRestoreStatus()
                self.DumpSensorsToDisplay()
            elif not self.pmPowerlinkMode and not self.ForceStandardMode:
                if self.pmDownloadMode:
                    log.info("[handle_msgtypeAB]         Got alive message while not in Powerlink mode but we're in Download mode")
                else:
                    log.info("[handle_msgtypeAB]         Got alive message while not in Powerlink mode and not in Download mode")
            else:
                self.DumpSensorsToDisplay()
        elif subType == 5: # -- phone message
            action = data[2]
            if action == 1:
                log.debug("[handle_msgtypeAB] PowerLink Phone: Calling User")
                #pmMessage("Calling user " + pmUserCalling + " (" + pmPhoneNr_t[pmUserCalling] +  ").", 2)
                #pmUserCalling = pmUserCalling + 1
                #if (pmUserCalling > pmPhoneNr_t) then
                #    pmUserCalling = 1
            elif action == 2:
                log.debug("[handle_msgtypeAB] PowerLink Phone: User Acknowledged")
                #pmMessage("User " .. pmUserCalling .. " acknowledged by phone.", 2)
                #pmUserCalling = 1
            else:
                log.debug("[handle_msgtypeAB] PowerLink Phone: Unknown Action {0}".format(hex(data[1]).upper()))
        elif subType == 10 and data[2] == 0:
            log.debug("[handle_msgtypeAB] PowerLink telling us what the code is for downloads, currently commented out as I'm not certain of this")
            # data[3] data[4]
#        elif subType == 10 and data[2] == 1 and not self.doneAutoEnroll:
#            if not self.ForceStandardMode:
#                log.info("[handle_msgtypeAB] PowerLink most likely wants to auto-enroll, only doing auto enroll once")
#                self.SendMsg_ENROLL()
        elif subType == 10 and data[2] == 1 and self.pmPowerlinkModePending and not self.ForceStandardMode and self.pmDownloadComplete and not self.pmPowerlinkMode:
            log.info("[handle_msgtypeAB] ************************** PowerLink most likely wants to auto-enroll, lets gtive it a try **************************")
            self.doneAutoEnroll = False
            self.SendMsg_ENROLL()


    # X10 Names (0xAC)
    def handle_msgtypeAC(self, data): # PowerLink Message
        """ MsgType=AB - Panel Powerlink Messages """
        log.info("[handle_msgtypeAC]  data {0}".format(self.toString(data)))

    
    # Only Powermasters send this message
    def handle_msgtypeB0(self, data): # PowerMaster Message
        """ MsgType=B0 - Panel PowerMaster Message """
#       Sources of B0 Code
#                  https://github.com/nlrb/com.visonic.powermax/blob/master/node_modules/powermax-api/lib/handlers.js
#        msgSubTypes = [0x00, 0x01, 0x02, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x11, 0x12, 0x13, 0x14, 0x15, 
#                       0x16, 0x18, 0x19, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x21, 0x24, 0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x32, 0x33, 0x34, 0x38, 0x39, 0x3A ]
        # Format: <Type> <SubType> <Length> <Data>

        msgType = data[0]
        subType = data[1]
        msgLen  = data[2]
        log.info("[handle_msgtypeB0] Received {0} message {1}/{2} (len = {3})    full data = {4}".format(PanelStatus["Model"] or "UNKNOWN", msgType, subType, msgLen, self.toString(data)))
        #  Received PowerMaster30 message 3/36 (len = 26)   full data = 03 24 1a ff 08 ff 15 00 00 00 00 00 00 00 00 26 35 12 15 03 00 14 03 01 00 81 00 00 0a 43
        
        if not self.pmDownloadMode and msgType == 0x03 and subType == 0x39:
            # Movement detected (probably)
            #  Received PowerMaster10 message 3/57 (len = 6)    full data = 03 39 06 ff 08 ff 01 24 0b 43
            #  Received PowerMaster30 message 3/57 (len = 8)    full data = 03 39 08 ff 08 ff 03 18 24 4b 90 43
            log.debug("[handle_msgtypeB0]      Sending special {0} Commands to the panel".format(PanelStatus["Model"] or "UNKNOWN"))
            self.SendCommand("MSG_POWERMASTER", options = [2, pmSendMsgB0_t["ZONE_STAT1"]])    # This asks the panel to send 03 04 messages
            #self.SendCommand("MSG_POWERMASTER", options = [2, pmSendMsgB0_t["ZONE_STAT2"]])    # This asks the panel to send 03 07 messages
            #self.SendCommand("MSG_POWERMASTER", options = [2, pmSendMsgB0_t["ZONE_STAT3"]])    # This asks the panel to send 03 18 messages

        enable = PanelSettings["B0_Enable"]
        if enable and msgType == 0x03 and subType == 0x04:
            log.info("[handle_msgtypeB0]         Received {0} message, continue".format(PanelStatus["Model"] or "UNKNOWN"))
            min_interval = PanelSettings["B0_Min_Interval_Time"]
            max_wait_time = PanelSettings["B0_Max_Wait_Time"]
            # Zone information (probably)
            #  Received PowerMaster10 message 3/4 (len = 35)    full data = 03 04 23 ff 08 03 1e 26 00 00 01 00 00 <24 * 00> 0c 43
            #  Received PowerMaster30 message 3/4 (len = 69)    full data = 03 04 45 ff 08 03 40 11 08 08 04 08 08 <58 * 00> 89 43
            
            interval = self.getTimeFunction() - self.lastRecvOfMasterMotionData
            self.lastRecvOfMasterMotionData = self.getTimeFunction() 
            td = timedelta(seconds=min_interval)  # 
            if interval > td:
                # more than 30 seconds since the last B0 03 04 message so reset variables ready
                # also, it should enter here first time around as self.lastRecvOfMasterMotionData should be 100 seconds ago
                log.info("[handle_msgtypeB0]         03 04 Data Reset")
                self.zoneNumberMasterMotion = False
                self.firstRecvOfMasterMotionData = self.getTimeFunction() 
                self.zoneDataMasterMotion = data.copy()
            elif not self.zoneNumberMasterMotion: 
                log.info("[handle_msgtypeB0]         Checking if time delay is within {0} seconds".format(max_wait_time))
                interval = self.getTimeFunction() - self.firstRecvOfMasterMotionData
                td = timedelta(seconds=max_wait_time)  # 
                if interval <= td and len(data) == len(self.zoneDataMasterMotion):
                    # less than or equal to 5 seconds since the last valid trigger message, and the data messages are the same length
                    zoneLen = data[6] # The length of the zone data (64 for PM30, 30 for PM10)
                    log.info("[handle_msgtypeB0]         Received {0} message, zone length = {1}".format(PanelStatus["Model"] or "UNKNOWN", zoneLen))
                    for z in range(0, zoneLen):
                        # Check if the zone exists and it has to be a PIR
                        # do we already know about the sensor from the EPROM decode
                        if z in self.pmSensorDev_t:
                            #zone z
                            log.info("[handle_msgtypeB0]           Checking Zone {0}".format(z))
                            if self.pmSensorDev_t[z].stype == "Motion":
                                #log.info("[handle_msgtypeB0]             And its motion")
                                s1 = data[7 + z]
                                s2 = self.zoneDataMasterMotion[7 + z]
                                log.debug("[handle_msgtypeB0]             Zone {0}  Motion State Before {1}   After {2}".format(z, s2, s1))
                                if s1 != s2:
                                    log.debug("[handle_msgtypeB0]             Triggered Motion Detection")
                                    self.zoneNumberMasterMotion = True   # this means we wait at least 'min_interval' seconds for the next trigger
                                    self.pmSensorDev_t[z].triggered = True
                                    self.pmSensorDev_t[z].triggertime = self.getTimeFunction()
                                    self.pmSensorDev_t[z].pushChange()
                            #else:
                            #    s = data[7 + z]
                            #    log.debug("[handle_msgtypeB0]           Zone {0}  is not a motion stype   State = {1}".format(z, s))
                    
        if msgType == 0x03 and subType == 0x18:
            # Open/Close information (probably)
            zoneLen = data[6] # The length of the zone data (64 for PM30, 30 for PM10)
            log.info("[handle_msgtypeB0]       Received {0} message, open/close information (probably), zone length = {1}".format(PanelStatus["Model"] or "UNKNOWN", zoneLen))
            for z in range(0, zoneLen):
                if z in self.pmSensorDev_t:
                    s = data[7 + z]
                    log.debug("[handle_msgtypeB0]           Zone {0}  State {1}".format(z, s))

        if msgType == 0x03 and subType == 0x07:
            #  Received PowerMaster10 message 3/7 (len = 35)    full data = 03 07 23 ff 08 03 1e 03 00 00 03 00 00 <24 * 00> 0d 43
            #  Received PowerMaster30 message 3/7 (len = 69)    full data = 03 07 45 ff 08 03 40 03 03 03 03 03 03 <58 * 00> 92 43 
            # Unknown information
            zoneLen = data[6] # The length of the zone data (64 for PM30, 30 for PM10)
            log.info("[handle_msgtypeB0]       Received {0} message, 03 07 information, zone length = {1}".format(PanelStatus["Model"] or "UNKNOWN", zoneLen))
            for z in range(0, zoneLen):
                if z in self.pmSensorDev_t:
                    s = data[7 + z]
                    log.debug("[handle_msgtypeB0]           Zone {0}  State {1}".format(z, s))


    # pmGetPin: Convert a PIN given as 4 digit string in the PIN PDU format as used in messages to powermax
    def pmGetPin(self, pin):
        """ Get pin and convert to bytearray """
        if pin is None or pin == "" or len(pin) != 4:
            if self.ForceNumericKeypad:
                return False, bytearray.fromhex("00 00 00 00")
            elif self.OverrideCode is not None and len(self.OverrideCode) > 0:
                pin = self.OverrideCode
            elif self.pmGotUserCode:
                return True, self.pmPincode_t[0]   # if self.pmGotUserCode, then we downloaded the pin codes. Use the first one
            elif self.ArmWithoutCode:
                return False, bytearray.fromhex("00 00 00 00")
            else:
                log.warning("Warning: Valid 4 digit PIN needed and not got Pin from EPROM")
                return False, bytearray.fromhex("00 00 00 00")
        # insert a space character in between the 4 digits format will then be "XX XX"
        spin = pin[0:2] + " " + pin[2:4]
        return True, bytearray.fromhex(spin)


    # Get a sensor by the key reference (integer)
    #   Return : The SensorDevice class of the provided refernce in s  or None if not found
    #            I don't think it can be immutable in python but at least changes in either will not affect the other
    def GetSensor(self, s) -> SensorDevice:
        """ Return a deepcopy of sensor details """
        if s in self.pmSensorDev_t:
            return copy.deepcopy(self.pmSensorDev_t[s]) # return a deepcopy as we don't want anything else to change it
        return None

    #===================================================================================================================================================
    #===================================================================================================================================================
    #===================================================================================================================================================
    #========================= Functions below this are for testing purposes and should be removed eventually ==========================================
    #===================================================================================================================================================
    #===================================================================================================================================================
    #===================================================================================================================================================

#    def toYesNo(self, b):
#        return "Yes" if b else "No"
        
    def getStatus(self, s : str):
        return "Unknown" if s not in PanelStatus else PanelStatus[s]

    def DumpSensorsToDisplay(self):
        log.info("=============================================== Display Status ===============================================")
        for key, sensor in self.pmSensorDev_t.items():
            log.info("     key {0:<2} Sensor {1}".format(key, sensor))
        log.info("   Model {: <18}     PowerMaster {: <18}     LastEvent {: <18}     Ready   {: <13}".format(self.getStatus("Model"),
                                        self.getStatus("Power Master"), self.getStatus("Panel Last Event"), self.getStatus("Panel Ready")))
        log.info("   Mode  {: <18}     Status      {: <18}     Armed     {: <18}     Trouble {: <13}     AlarmStatus {: <12}".format(self.getStatus("Mode"), self.getStatus("Panel Status"),
                                        self.getStatus("Panel Armed"), self.getStatus("Panel Trouble Status"), self.getStatus("Panel Alarm Status")))
        log.info("==============================================================================================================")
        #for key in PanelStatus:
        #    log.info("Panel Status {0:22}  {1}".format(key, PanelStatus[key]))



class EventHandling(PacketHandling):
    """ Event Handling """

    def __init__(self, *args, command_queue = None, ignore: List[str] = None, **kwargs) -> None:
        """Add eventhandling specific initialization."""
        super().__init__(*args, **kwargs)

        if ignore:
            log.debug('[EventHandling]  ignoring: %s', ignore)
            self.ignore = ignore
        else:
            #log.debug("In __init__")
            self.ignore = []
        
        self.command_queue = command_queue
        if command_queue is not None:
            asyncio.ensure_future(self.process_command_queue(), loop=self.loop)
        
    # implement commands from the queue            
    async def process_command_queue(self):
        while not self.suspendAllOperations:
            command = await self.command_queue.get()
            if command[0] == "log":
                log.debug("[CommandQueue]  Calling event log")
                self.GetEventLog(command[1])
            elif command[0] == "bypass":
                log.debug("[CommandQueue]  Calling bypass for individual sensors")
                self.SetSensorArmedState(command[1], command[2], command[3])
            elif command[0] == "x10":
                log.debug("[CommandQueue]  Calling x10 command")
                self.SendX10Command(command[1], command[2])
            elif command[0] in pmArmMode_t:
                self.RequestArm(command[0], command[1])
            else:
                log.warning("[CommandQueue]  Processing unknown queue command {0}".format(command))
                

    # RequestArm
    #       state is one of: "Disarmed", "Stay", "Armed", "UserTest", "StayInstant", "ArmedInstant", "Night", "NightInstant"
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink

    def RequestArm(self, state, pin = ""):
        """ Send a request to the panel to Arm/Disarm """
        if not self.pmDownloadMode:
            isValidPL, bpin = self.pmGetPin(pin)
            
            armCode = None
            armCodeA = bytearray()
            if state in pmArmMode_t:
                armCode = pmArmMode_t[state]
                armCodeA.append(armCode)

            log.debug("[RequestArm]  RequestArmMode " + (state or "N/A"))     # + "  using pin " + self.toString(bpin))
            
            if armCode is not None:
    
                if not isValidPL and self.ArmWithoutCode and state != "Disarmed" and self.pmRemoteArm:
                    # if we dont have pin codes and we can arm without a code and we're arming and arming is allowed
                    isValidPL = True
                    bpin = bytearray.fromhex("00 00 00 00")

                if isValidPL:
                    if (state == "Disarmed" and self.pmRemoteDisArm) or (state != "Disarmed" and self.pmRemoteArm):
                        self.SendCommand("MSG_ARM", options = [3, armCodeA, 4, bpin])    #
                        return True
                    else:
                        log.info("[RequestArm]  Panel Access Not allowed, user setting prevent access")
                else:
                    log.info("[RequestArm]  Panel Access Not allowed without pin")
            else:
                log.info("[RequestArm]  RequestArmMode invalid state requested " + (state or "N/A"))
        else:
            log.info("[RequestArm]  Request Arm and Disarm only supported when not downloading EPROM.")
        return False

    # Individually arm/disarm the sensors
    #   This sets/clears the bypass for each sensor
    #       zone is the zone number 1 to 31
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
    def SetSensorArmedState(self, zone, bypassValue, pin = "") -> bool:  # was sensor instead of zone (zone in range 1 to 32).
        """ Set or Clear Sensor Bypass """
        if not self.pmDownloadMode:
            if not self.pmBypassOff:
                isValidPL, bpin = self.pmGetPin(pin)

                if isValidPL:
                    bypassint = 1 << (zone - 1)
                    log.info("[SensorArmState]  SetSensorArmedState A " + hex(bypassint))
                    # is it big or little endian, i'm not sure, needs testing
                    y1, y2, y3, y4 = (bypassint & 0xFFFFFFFF).to_bytes(4, 'little')
                    # These could be the wrong way around, needs testing
                    bypass = bytearray([y1, y2, y3, y4])
                    log.info("[SensorArmState]  SetSensorArmedState B " + self.toString(bypass))
                    if len(bpin) == 2 and len(bypass) == 4:
                        if bypassValue:
                            self.SendCommand("MSG_BYPASSEN", options = [1, bpin, 3, bypass]) 
                        else:
                            self.SendCommand("MSG_BYPASSDI", options = [1, bpin, 7, bypass])
                        self.SendCommand("MSG_BYPASSTAT") # request status to check success and update sensor variable
                        return True
                else:
                    log.info("[SensorArmState]  Bypass option not allowed, invalid pin")
            else:
                log.info("[SensorArmState]  Bypass option not enabled in panel settings.")
        else:
            log.info("[SensorArmState]  Bypass setting only supported when not downloading EPROM.")
        return False

    # Get the Event Log
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    def GetEventLog(self, pin = ""):
        """ Get Panel Event Log """
        log.info("GetEventLog")
        if not self.pmDownloadMode:
            isValidPL, bpin = self.pmGetPin(pin)
            if isValidPL:
                self.SendCommand("MSG_EVENTLOG", options=[4, bpin])
                return True
            else:
                log.info("Get Event Log not allowed, invalid pin")
        else:
            log.info("Get Event Log only supported when not downloading EPROM.")
        return False

    def SendX10Command(self, dev, state):
        # This is untested
        # "MSG_X10PGM"      : VisonicCommand(bytearray.fromhex('A4 00 00 00 00 00 99 99 00 00 00 43'), None  , False, "X10 Data" ),
        if not self.pmDownloadMode:
            if dev >= 0 and dev <= 15:
                log.debug("[SendX10Command]  Py Visonic : Send X10 Command : id = " + str(dev) + "   state = " + state)
                calc = (1 << dev)
                byteA = calc & 0xFF
                byteB = (calc >> 8) & 0xFF
                if state in pmX10State_t:
                    what = pmX10State_t[state]
                    self.SendCommand("MSG_X10PGM", options = [6, what, 7, byteA, 8, byteB])
                    return True
                else:
                    log.info("[SendX10Command]  Send X10 Command : state not in pmX10State_t " + state)
            else:
                log.info("[SendX10Command]  Send X10 Command : Device ID not in valid range")
        else:
            log.info("[SendX10Command]  Get Event Log only supported when not downloading EPROM.")
        return False

#PowerMax.prototype.writeMessage = function(item, val) {
#	var len = val.length;
#	var addr;
#	if (typeof item == 'string') {
#		addr = pm.download[item].slice(0, 1);
#	} else {
#		addr = [item % 0x100, math.floor(item / 0x100)];
#	}
#	while (len > 0) {
#		var s = val.slice(0, 0xAF);
#		var l = (len > 0xB0) ? 0xB0 : len;
#		this.debug('addr = ' + addr + ', len = ' + l);
#		this.sendMessage("MSG_WRITE", { addr: addr, len: l, val: s });
#		var page = string.byte(addr, 2)
#		var index = string.byte(addr)
#		//pmWriteSettings(page, index, s) // also update internal table
#		len -= 0xB0;
#		val = val.slice(0xB0);
#		var a = 0x100 * page + index + 0xB0;
#		addr = [a % 0x100, math.floor(a / 0x100)];
#    }
#}
        

class VisonicProtocol(EventHandling):
    """Combine preferred abstractions that form complete Rflink interface."""


    #===================================================================================================================================================
    #===================================================================================================================================================
    #===================================================================================================================================================
    #========================= Functions below this are to be called from Home Assistant ===============================================================
    #============================= These functions are to be used to configure and setup the connection to the panel ===================================
    #===================================================================================================================================================
    #===================================================================================================================================================

def setConfig(key, val):
    if key in PanelSettings:
        if val is not None:
            log.info("Setting key {0} to value {1}".format(key, val))
            PanelSettings[key] = val
    else:
        log.warning("ERROR: ************************ Cannot find key {0} in panel settings".format(key))

# Create a connection using asyncio using an ip and port
def create_tcp_visonic_connection(address, port, protocol=VisonicProtocol, command_queue = None, event_callback=None, disconnect_callback=None, loop=None):
    """Create Visonic manager class, returns tcp transport coroutine."""

    # use default protocol if not specified
    protocol = partial(
        protocol,
        loop=loop if loop else asyncio.get_event_loop(),
        event_callback=event_callback,
        disconnect_callback=disconnect_callback,
        command_queue = command_queue, 
#        ignore=ignore if ignore else [],
    )

    address = address
    port = port
    conn = loop.create_connection(protocol, address, port)

    return conn


# Create a connection using asyncio through a linux port (usb or rs232)
def create_usb_visonic_connection(port, baud=9600, protocol=VisonicProtocol, command_queue = None, event_callback=None, disconnect_callback=None, loop=None):
    """Create Visonic manager class, returns rs232 transport coroutine."""
    from serial_asyncio import create_serial_connection
    # use default protocol if not specified
    protocol = partial(
        protocol,
        loop=loop if loop else asyncio.get_event_loop(),
        event_callback=event_callback,
        disconnect_callback=disconnect_callback,
        command_queue = command_queue, 
 #        ignore=ignore if ignore else [],
    )

    # setup serial connection
    port = port
    baud = baud
    conn = create_serial_connection(loop, protocol, port, baud)

    return conn

def setupLocalLogger():
    #add custom formatter to root logger
    formatter = ElapsedFormatter()
    shandler = logging.StreamHandler(stream=sys.stdout)
    shandler.setFormatter(formatter)
    fhandler = logging.FileHandler('log.txt', mode='w')
    fhandler.setFormatter(formatter)

    log.propagate = False

    log.addHandler(fhandler)
    log.addHandler(shandler)

    #level = logging.getLevelName('INFO')
    #if PanelSettings["PluginDebug"]:
    level = logging.getLevelName('DEBUG')  # INFO, DEBUG
    log.setLevel(level)
