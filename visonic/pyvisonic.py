"""Asyncio protocol implementation of Visonic PowerMaster/PowerMax.
  Based on the DomotiGa and Vera implementation:

  Credits:
    Initial setup by Wouter Wolkers and Alexander Kuiper.
    Thanks to everyone who helped decode the data.

  Converted to Python module by Wouter Wolkers and David Field
  
  The Component now follows the new HA file structure
"""

########################################################
# PowerMax/Master send messages
########################################################

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

HOMEASSISTANT = True

PLUGIN_VERSION = "0.1.3"

MAX_CRC_ERROR = 5
POWERLINK_RETRIES = 4

# If we are waiting on a message back from the panel or we are explicitly waiting for an acknowledge,
#    then wait this time before resending the message.
#  Note that not all messages will get a resend, only ones waiting for a specific response and/or are blocking on an ack
RESEND_MESSAGE_TIMEOUT = timedelta(seconds=10)

# We must get specific messages from the panel, if we do not in this time period then trigger a restore/status request
WATCHDOG_TIMEOUT = 60

WATCHDOG_MAXIMUM_EVENTS = 10

# When we send a download command wait for DownloadMode to become false.
#   If this timesout then I'm not sure what to do, we really need to just start again
#     In Vera, if we timeout we just assume we're in Standard mode by default
DOWNLOAD_TIMEOUT = 120

DownloadCode = bytearray.fromhex('56 50')

PanelSettings = {
   "MotionOffDelay"      : 120,
   "OverrideCode"        : "",
   "PluginLanguage"      : "EN",
   "PluginDebug"         : False,
   "ForceStandard"       : False,
#   "AutoCreate"          : True,
   "ResetCounter"        : 0,
   "AutoSyncTime"        : True,
   "EnableRemoteArm"     : False,
   "EnableRemoteDisArm"  : False,  #
   "EnableSensorBypass"  : False   # Does user allow sensor bypass / arming
}

PanelStatus = {
   "PluginVersion"         : PLUGIN_VERSION,
   "Comm Exception Count"  : 0,
   "Mode"                  : "Unknown",

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
   "Power Master"       : 'No'
}

# use a named tuple for data and acknowledge
#    replytype is a message type from the Panel that we should get in response
#    waitforack, if True means that we should wait for the acknowledge from the Panel before progressing
VisonicCommand = collections.namedtuple('VisonicCommand', 'data replytype waitforack msg')
pmSendMsg = {
   "MSG_EVENTLOG"    : VisonicCommand(bytearray.fromhex('A0 00 00 00 99 99 00 00 00 00 00 43'), [0xA0], False, "Retrieving Event Log" ),  # replytype is 0xA0
   "MSG_ARM"         : VisonicCommand(bytearray.fromhex('A1 00 00 00 99 99 00 00 00 00 00 43'), None  , False, "(Dis)Arming System" ),
   "MSG_STATUS"      : VisonicCommand(bytearray.fromhex('A2 00 00 00 00 00 00 00 00 00 00 43'), [0xA5], False, "Getting Status" ),
   "MSG_BYPASSTAT"   : VisonicCommand(bytearray.fromhex('A2 00 00 20 00 00 00 00 00 00 00 43'), [0xA5], False, "Bypassing" ),
   "MSG_ZONENAME"    : VisonicCommand(bytearray.fromhex('A3 00 00 00 00 00 00 00 00 00 00 43'), [0xA3], False, "Requesting Zone Names" ),
   "MSG_X10PGM"      : VisonicCommand(bytearray.fromhex('A4 00 00 00 00 00 99 99 99 00 00 43'), None  , False, "X10 Data" ),
   "MSG_ZONETYPE"    : VisonicCommand(bytearray.fromhex('A6 00 00 00 00 00 00 00 00 00 00 43'), [0xA6], False, "Requesting Zone Types" ),
   "MSG_BYPASSEN"    : VisonicCommand(bytearray.fromhex('AA 99 99 12 34 56 78 00 00 00 00 43'), None  , False, "BYPASS Enable" ),
   "MSG_BYPASSDI"    : VisonicCommand(bytearray.fromhex('AA 99 99 00 00 00 00 12 34 56 78 43'), None  , False, "BYPASS Disable" ),
   "MSG_ALIVE"       : VisonicCommand(bytearray.fromhex('AB 03 00 00 00 00 00 00 00 00 00 43'), None  , False, "I'm Alive Message To Panel" ),
   "MSG_RESTORE"     : VisonicCommand(bytearray.fromhex('AB 06 00 00 00 00 00 00 00 00 00 43'), [0xA5], False, "Restore PowerMax/Master Connection" ), # It can take multiple of these to put the panel back in to powerlink
   "MSG_ENROLL"      : VisonicCommand(bytearray.fromhex('AB 0A 00 00 99 99 00 00 00 00 00 43'), [0xAB], False, "Auto-Enroll of the PowerMax/Master" ),
   "MSG_INIT"        : VisonicCommand(bytearray.fromhex('AB 0A 00 01 00 00 00 00 00 00 00 43'), None  ,  True, "Initializing PowerMax/Master PowerLink Connection" ),
   "MSG_X10NAMES"    : VisonicCommand(bytearray.fromhex('AC 00 00 00 00 00 00 00 00 00 00 43'), [0xAC], False, "Requesting X10 Names" ),
   # Command codes (powerlink) do not have the 0x43 on the end and are only 11 values
   "MSG_DOWNLOAD"    : VisonicCommand(bytearray.fromhex('24 00 00 99 99 00 00 00 00 00 00')   , None  ,  True, "Start Download Mode" ),  #[0x3C]
   "MSG_WRITE"       : VisonicCommand(bytearray.fromhex('3D 00 00 00 00 00 00 00 00 00 00')   , None  , False, "Write Data Set" ),
   "MSG_DL"          : VisonicCommand(bytearray.fromhex('3E 00 00 00 00 B0 00 00 00 00 00')   , [0x3F], False, "Download Data Set" ),
   "MSG_SETTIME"     : VisonicCommand(bytearray.fromhex('46 F8 00 01 02 03 04 05 06 FF FF')   , None  , False, "Setting Time" ),   # may not need an ack
   "MSG_SER_TYPE"    : VisonicCommand(bytearray.fromhex('5A 30 04 01 00 00 00 00 00 00 00')   , [0x33], False, "Get Serial Type" ),
   # quick command codes to start and stop download/powerlink are a single value
   "MSG_START"       : VisonicCommand(bytearray.fromhex('0A')                                 , [0x0B], False, "Start" ),    # waiting for download complete from panel
   "MSG_STOP"        : VisonicCommand(bytearray.fromhex('0B')                                 , None  , False, "Stop" ),     #
   "MSG_EXIT"        : VisonicCommand(bytearray.fromhex('0F')                                 , None  , False, "Exit" ),
   "MSG_ACK"         : VisonicCommand(bytearray.fromhex('02')                                 , None  , False, "Ack" ),
   "MSG_ACKLONG"     : VisonicCommand(bytearray.fromhex('02 43')                              , None  , False, "Ack Long" ),
   # PowerMaster specific
   "MSG_POWERMASTER" : VisonicCommand(bytearray.fromhex('B0 01 00 00 00 00 00 00 00 00 43')   , [0xB0], False, "Powermaster Command" )
}

pmSendMsgB0_t = {
   "ZONE_STAT1" : bytearray.fromhex('04 06 02 FF 08 03 00 00'),
   "ZONE_STAT2" : bytearray.fromhex('07 06 02 FF 08 03 00 00')
   #"ZONE_NAME"  : bytearray.fromhex('21 02 05 00'),   # not used in Vera Lua Script
   #"ZONE_TYPE"  : bytearray.fromhex('2D 02 05 00')    # not used in Vera Lua Script
}

# To use the following, use  "MSG_DL" above and replace bytes 1 to 4 with the following
#    index page lenlow lenhigh
pmDownloadItem_t = {
   "MSG_DL_TIME"         : bytearray.fromhex('F8 00 06 00'),   # could be F8 00 20 00
   "MSG_DL_COMMDEF"      : bytearray.fromhex('01 01 1E 00'),
   "MSG_DL_PHONENRS"     : bytearray.fromhex('36 01 20 00'),
   "MSG_DL_PINCODES"     : bytearray.fromhex('FA 01 10 00'),
   "MSG_DL_PGMX10"       : bytearray.fromhex('14 02 D5 00'),
   "MSG_DL_PARTITIONS"   : bytearray.fromhex('00 03 F0 00'),
   "MSG_DL_PANELFW"      : bytearray.fromhex('00 04 20 00'),
   "MSG_DL_SERIAL"       : bytearray.fromhex('30 04 08 00'),
   "MSG_DL_ZONES"        : bytearray.fromhex('00 09 78 00'),
   "MSG_DL_KEYFOBS"      : bytearray.fromhex('78 09 40 00'),
   "MSG_DL_2WKEYPAD"     : bytearray.fromhex('00 0A 08 00'),
   "MSG_DL_1WKEYPAD"     : bytearray.fromhex('20 0A 40 00'),
   "MSG_DL_SIRENS"       : bytearray.fromhex('60 0A 08 00'),
   "MSG_DL_X10NAMES"     : bytearray.fromhex('30 0B 10 00'),
   "MSG_DL_ZONENAMES"    : bytearray.fromhex('40 0B 1E 00'),
   "MSG_DL_EVENTLOG"     : bytearray.fromhex('DF 04 28 03'),
   "MSG_DL_ZONESTR"      : bytearray.fromhex('00 19 00 02'),
   "MSG_DL_ZONESIGNAL"   : bytearray.fromhex('DA 09 1C 00'),    # zone signal strength
   "MSL_DL_ZONECUSTOM"   : bytearray.fromhex('A0 1A 50 00'),
   "MSG_DL_MR_ZONENAMES" : bytearray.fromhex('60 09 40 00'),
   "MSG_DL_MR_PINCODES"  : bytearray.fromhex('98 0A 60 00'),
   "MSG_DL_MR_SIRENS"    : bytearray.fromhex('E2 B6 50 00'),
   "MSG_DL_MR_KEYPADS"   : bytearray.fromhex('32 B7 40 01'),
   "MSG_DL_MR_ZONES"     : bytearray.fromhex('72 B8 80 02'),
   "MSG_DL_MR_SIRKEYZON" : bytearray.fromhex('E2 B6 10 04'), # Combines Sirens keypads and sensors
   "MSG_DL_ALL"          : bytearray.fromhex('00 00 00 FF')
}

# Message types we can receive with their length (None=unknown) and whether they need an ACK
PanelCallBack = collections.namedtuple("PanelCallBack", 'length ackneeded variablelength' )
pmReceiveMsg_t = {
   0x02 : PanelCallBack( None, False, False ),   # Ack
   0x06 : PanelCallBack( None, False, False ),   # Timeout. See the receiver function for ACK handling
   0x08 : PanelCallBack( None,  True, False ),   # Access Denied
   0x0B : PanelCallBack( None,  True, False ),   # Stop --> Download Complete
   0x25 : PanelCallBack(   14,  True, False ),   # 14 Download Retry
   0x33 : PanelCallBack(   14,  True, False ),   # 14 Download Settings   Do not acknowledge 0x33 back to panel
   0x3C : PanelCallBack(   14,  True, False ),   # 14 Panel Info
   0x3F : PanelCallBack( None,  True,  True ),   # Download Info
   0xA0 : PanelCallBack(   15,  True, False ),   # 15 Event Log
   0xA3 : PanelCallBack(   15,  True, False ),   # 15 Zone Names
   0xA5 : PanelCallBack(   15,  True, False ),   # 15 Status Update       Length was 15 but panel seems to send different lengths
   0xA6 : PanelCallBack(   15,  True, False ),   # 15 Zone Types I think!!!!
   0xA7 : PanelCallBack(   15,  True, False ),   # 15 Panel Status Change
   0xAB : PanelCallBack(   15, False, False ),   # 15 Enroll Request 0x0A  OR Ping 0x03      Length was 15 but panel seems to send different lengths
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
           "Log verzenden", "Systeem reset", "Installateur programmeert", "Foutieve code", "Overbruggen" )
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
           "Direct Totaal uitloopvertraging", "Direct inloopvertraging", "Direct Deel", "Direct Totaal" )
}

pmSysStatusFlags_t = {
   "EN" : ( "Ready", "Alert in memory", "Trouble", "Bypass on", "Last 10 seconds", "Zone event", "Status changed", "Alarm event" ),
   "NL" : ( "Klaar", "Alarm in geheugen", "Probleem", "Overbruggen aan", "Laatste 10 seconden", "Zone verstoord", "Status gewijzigd", "Alarm actief")
}

#pmArmed_t = {
#   0x03 : "", 0x04 : "", 0x05 : "", 0x0A : "", 0x0B : "", 0x13 : "", 0x14 : "", 0x15 : ""
#}

pmArmMode_t = {
# "Alarm" : 0x07, 
   "Disarmed" : 0x00, "Stay" : 0x04, "Armed" : 0x05, "UserTest" : 0x06, "StayInstant" : 0x14, "ArmedInstant" : 0x15, "Night" : 0x04, "NightInstant" : 0x14
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
           "Brandalarm", "Noodoproep", "Sirene sabotage", "Sirene sabotage herstel", "Sirene lage batterij", "Sirene AC probleem" )
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

# Config for each panel type (1-9)
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
           "24 uurs stil", "24 uurs luid", "Brand", "Interieur", "Thuis vertraagd", "Temperatuur", "Buiten", "16" )
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
   "NL" : ("Uit", "Muziek", "Zone", "Invalid")
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

if not HOMEASSISTANT:
    #add custom formatter to root logger
    formatter = ElapsedFormatter()
    shandler = logging.StreamHandler(stream=sys.stdout)
    shandler.setFormatter(formatter)
    fhandler = logging.FileHandler('log.txt', mode='w')
    fhandler.setFormatter(formatter)

    log.propagate = False

    log.addHandler(fhandler)
    log.addHandler(shandler)

    level = logging.getLevelName('INFO')
    if PanelSettings["PluginDebug"]:
        level = logging.getLevelName('DEBUG')  # INFO, DEBUG
    log.setLevel(level)

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
        log.info("Installing update handler for device {}".format(self.id))
        self._change_handler = ch

    def pushChange(self):
        if self._change_handler is not None:
            #log.info("Calling update handler for device")
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
        log.info("Installing update handler for device {}".format(self.id))
        self._change_handler = ch

    def pushChange(self):
        if self._change_handler is not None:
            log.info("Calling update handler for X10 device")
            self._change_handler()


class VisonicListEntry:
    def __init__(self, **kwargs):
        #self.message = kwargs.get('message', None)
        self.command = kwargs.get('command', None)
        #self.receive = kwargs.get('receive', None)
        #self.receivecount = kwargs.get('receivecount', None)
        #self.receivecountfixed = kwargs.get('receivecountfixed', None) # Need to store it extra, because with a re-List it can get lost
        #self.receiveretries = kwargs.get('receiveretries', None)
        self.options = kwargs.get('options', None)
        if self.command.replytype is None:
            self.response = []
        else:
            self.response = self.command.replytype  # list of message reply needed
        # are we waiting for an acknowledge from the panel (do not send a message until we get it)
        if self.command.waitforack:
            self.response.append(0x02)              # add an acknowledge to the list
        self.triedResendingMessage = False

    def __str__(self):
        return "Command:{0}    Options:{1}".format(self.command.msg, self.options)


# This class handles the detailed low level interface to the panel.
#    It sends the messages
#    It builds and received messages        
class ProtocolBase(asyncio.Protocol):
    """Manage low level Visonic protocol."""

    PanelStatus["Comm Exception Count"] = PanelSettings["ResetCounter"]
    
    transport = None  # type: asyncio.Transport

    # Are we expecting a variable length message from the panel
    pmVarLenMsg = False
    pmIncomingPduLen = 0
    pmSendMsgRetries = 0

    # The CRC Error Count for Received Messages
    pmCrcErrorCount = 0
    # Whether its a powermax or powermaster
    PowerMaster = True
    # the current receiving message type
    msgType_t = None
    # The last sent message
    pmLastSentMessage = None
    # keep alive counter for the timer 
    keep_alive_counter = 0    # only used in keep_alive_and_watchdog_timer
    # a list of message types we are expecting from the panel
    pmExpectedResponse = []
    # whether we are in powerlink state
    pmPowerlinkMode = False
    # When we are downloading the EPROM settings and finished parsing them and setting up the system.
    #   There should be no user (from Home Assistant for example) interaction when this is True
    DownloadMode = False

    doneAutoEnroll = False

    coordinating_powerlink = True

    receive_log = []

    watchdog_counter = 0

    log.info("Initialising Protocol")

    def __init__(self, loop=None, disconnect_callback=None, event_callback: Callable = None ) -> None:
        """Initialize class."""
        if loop:
            self.loop = loop
        else:
            self.loop = asyncio.get_event_loop()
        self.event_callback = event_callback
        # The receive byte array for receiving a message
        self.ReceiveData = bytearray()
        self.disconnect_callback = disconnect_callback
        # A queue of messages to send
        self.SendList = []
        # This is the time stamp of the last Send or Receive
        self.pmLastTransactionTime = self.getTimeFunction() - timedelta(seconds=1)  # take off 1 second so the first command goes through immediately
        self.ForceStandardMode = False # until defined by HA
        self.coordinate_powerlink_startup_count = 0
        self.suspendAllOperations = False
        self.pmLastPDU = bytearray()

    def sendResponseEvent(self, ev):
        if self.event_callback is not None:
            self.event_callback(ev)        

    def toString(self, array_alpha: bytearray):
        return "".join("%02x " % b for b in array_alpha)

    # get the current date and time
    def getTimeFunction(self) -> datetime:
        return datetime.now()

    def triggerRestoreStatus(self):
        # Reset Send state (clear queue and reset flags)
        self.ClearList()
        #self.pmWaitingForAckFromPanel = False
        self.pmExpectedResponse = []
        # restart the counter
        self.reset_watchdog_timeout()
        self.reset_keep_alive_messages()
        if self.pmPowerlinkMode:
            # Send RESTORE to the panel
            self.SendCommand("MSG_EXIT")    # exit any ongoing download sequence
            self.SendCommand("MSG_STOP")        
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
        status_counter = 1000  # trigger first time!
        watchdog_events = 0
        
        while not self.suspendAllOperations:
        
            # Disable watchdog and keep-alive during download
            if self.DownloadMode:
                self.reset_watchdog_timeout()
                self.reset_keep_alive_messages()

            # Watchdog functionality
            self.watchdog_counter = self.watchdog_counter + 1
            #log.debug("[WatchDogTimeout] is {0}".format(self.watchdog_counter))
            if self.watchdog_counter >= WATCHDOG_TIMEOUT:   #  the clock runs at 1 second
                log.info("[WatchDogTimeout] ****************************** WatchDog Timer Expired ********************************")
                status_counter = 0  # delay status requests
                self.reset_watchdog_timeout()
                self.reset_keep_alive_messages()
                watchdog_events = watchdog_events + 1
                if watchdog_events >= WATCHDOG_MAXIMUM_EVENTS:
                    watchdog_events = 0
                    self.gotoStandardMode()
                else:
                    self.triggerRestoreStatus()

            # Keep alive functionality
            self.keep_alive_counter = self.keep_alive_counter + 1
            #log.debug("[KeepaliveTimeout] is {0}   DownloadMode {1}".format(self.keep_alive_counter, self.DownloadMode))
            if len(self.SendList) == 0 and self.keep_alive_counter >= 20:   #
                # Every 20 seconds, unless watchdog has been reset
                #log.debug("Send list is empty so sending I'm alive message")
                # reset counter
                self.reset_keep_alive_messages()
                # Send I'm Alive and request status
                self.SendCommand("MSG_ALIVE")
                # When is standard mode, sending this asks the panel to send us the status so we know that the panel is ok.
                # When in powerlink mode, it makes no difference as we get the AB messages from the panel, but this also keeps our status updated
                if status_counter > 4:
                    status_counter = 0
                    self.SendCommand("MSG_STATUS")  # Asks the panel to send us the A5 message set
                status_counter = status_counter + 1
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

        
    # This is called from the loop handler when the connection to the transport is made
    def connection_made(self, transport):
        """Make the protocol connection to the Panel."""
        self.transport = transport
        log.info('[Connection] Connected to local Protocol handler and Transport Layer')

        # Force standard mode (i.e. do not attempt to go to powerlink)
        self.ForceStandardMode = PanelSettings["ForceStandard"] # INTERFACE : Get user variable from HA to force standard mode or try for PowerLink
        self.Initialise()

    def Initialise(self):
        if self.suspendAllOperations:
            log.info('[Connection] Suspended. Sorry but all operations have been suspended, please recreate connection')
            return

        # Define powerlink seconds timer and start it for PowerLink communication
        self.reset_watchdog_timeout()
        self.reset_keep_alive_messages()

        self.pmExpectedResponse = []
        self.pmSendMsgRetries = 0

        # Send the download command, this should initiate the communication
        # Only skip it, if we force standard mode
        if not self.ForceStandardMode:
            # attempt to coordinate powerlink connectivity
            #     during early initialisation we need to ignore all incoming data to establish a known state in the panel
            #     the first time, set the counter as 1 as we can assume that it's going to be OK!!!!
            asyncio.ensure_future(self.coordinate_powerlink_startup(1), loop=self.loop)
        else:
            self.gotoStandardMode()

        asyncio.ensure_future(self.keep_alive_and_watchdog_timer(), loop=self.loop)

        
    def resetPanelSequence(self):   # This should re-initialise the panel, most of the time it works!
        self.ClearList()
        
        self.pmExpectedResponse = []
        self.SendCommand("MSG_EXIT")
        sleep(1.0)
        
        self.pmExpectedResponse = []
        self.SendCommand("MSG_STOP")
        sleep(1.0)

        while not self.suspendAllOperations and len(self.SendList) > 0:
            log.debug("[ResetPanel]       Waiting")
            self.SendCommand(None)  # check send queue

        self.pmExpectedResponse = []
        self.SendCommand("MSG_INIT")
        sleep(1.0)
        
        
    def gotoStandardMode(self):
        PanelStatus["Mode"] = "Standard"
        self.pmPowerlinkMode = False
        self.coordinating_powerlink = False
        self.resetPanelSequence()
        self.SendCommand("MSG_STATUS")


    # during initialisation we need to ignore all incoming data to establish a known state in the panel
    # I attempted to coordinate it through variables and state and it was tricky
    #   having an async function to coordinate it works better
    async def coordinate_powerlink_startup(self, cyclecount):
        if not self.ForceStandardMode:
            self.coordinate_powerlink_startup_count = self.coordinate_powerlink_startup_count + 1
            if self.coordinate_powerlink_startup_count > POWERLINK_RETRIES:
                # just go in to standard mode
                self.coordinating_powerlink = False
                self.pmExpectedResponse = []
                self.reset_keep_alive_messages()
                self.reset_watchdog_timeout()
                self.gotoStandardMode()
            else: # self.coordinate_powerlink_startup_count <= POWERLINK_RETRIES:
                # TRY POWERLINK MODE
                # by setting this, we do not process incoming data, 
                #     all we do is collect it in self.receive_log
                self.coordinating_powerlink = True

                # walk through sending INIT and waiting for just an ack, we are trying to establish quiet time with the panel
                count = 0
                while not self.suspendAllOperations and count < cyclecount:
                    log.info("[Startup] Trying to initialise panel")
                    self.reset_keep_alive_messages()
                    self.reset_watchdog_timeout()

                    # send EXIT and INIT and then wait to make certain they have been sent
                    self.receive_log = []
                    self.resetPanelSequence()
                    self.pmExpectedResponse = []
                    # Wait to gather any panel responses
                    await asyncio.sleep(4.0)

                    # check that all received messages are either 02 (ack) or A5 (status).
                    #   status is sent when in standard or powerlink modes but not in download mode
                    #   status is sent by the panel approx every 15 seconds.
                    #   sometimes between init and download we can get an A5 message
                    rec_ok = True
                    for p in self.receive_log:
                        if p[1] != 0x02 and p[1] != 0xA5:
                            log.info("[Startup]    Got at least 1 unexpected message, so starting count again from zero")
                            log.info("[Startup]        " + self.toString(p))
                            rec_ok = False
                    if rec_ok:
                        log.debug("[Startup]       Success: Got only the required messages")
                        count = count + 1
                    else:
                        count = 0
                    # can the damn panel be quiet!!!  If not then try again
                    log.info("[Startup]   count is " + str(count))

                log.debug("[Startup] Sending Download Start")
                # allow the processing of incoming data packets as normal
                self.coordinating_powerlink = False
                self.receive_log = []
                self.pmExpectedResponse = []
                self.reset_keep_alive_messages()
                self.reset_watchdog_timeout()
                self.Start_Download()

    # Process any received bytes (in data as a bytearray)            
    def data_received(self, data):
        if self.suspendAllOperations:
            return
        """Add incoming data to ReceiveData."""
        #log.debug('[data receiver] received data: %s', self.toString(data))
        for databyte in data:
            #log.debug("[data receiver] Processing " + hex(databyte).upper())
            # process a single byte at a time
            self.handle_received_byte(databyte)

    # Process one received byte at a time to build up the received messages
    #       pmIncomingPduLen is only used in this function
    def handle_received_byte(self, data):
        """Process a single byte as incoming data."""
        # Length of the received data so far
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
            msgType = self.ReceiveData[1]
            self.pmIncomingPduLen = 7 + int(data) # (((int(self.ReceiveData[2]) * 0x100) + int(self.ReceiveData[3])))
            #if self.pmIncomingPduLen >= 0xB0:  # then more than one message
            #    self.pmIncomingPduLen = 0 # set it to 0 for this loop around
            #log.debug("[data receiver] Variable length Message Being Receive  Message {0}     pmIncomingPduLen {1}".format(hex(msgType).upper(), self.pmIncomingPduLen))

        # If this is the start of a new message, then check to ensure it is a 0x0D (message header)
        if pdu_len == 0:
            self.msgType_t = None
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
                self.msgType_t = pmReceiveMsg_t[msgType]
                self.pmIncomingPduLen = (self.msgType_t is not None) and self.msgType_t.length or 0
                self.pmVarLenMsg = pmReceiveMsg_t[msgType].variablelength
                #if msgType == 0x3F: # and self.msgType_t != None and self.pmIncomingPduLen == 0:
                #    self.pmVarLenMsg = True
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
                log.info("[data receiver] Building PDU: Special Case 42 ********************************************************************************************")
                self.pmIncomingPduLen = self.pmIncomingPduLen + 1 # for 0x43
            elif self.validatePDU(self.ReceiveData):
                # We've got a validated message
                #log.debug("[data receiver] Building PDU: Got Validated PDU type 0x%02x   full data %s", int(msgType), self.toString(self.ReceiveData))
                # Reset some variable ready for next time
                self.pmVarLenMsg = False
                self.pmLastPDU = self.ReceiveData
                self.pmLogPdu(self.ReceiveData, "<-PM  ")
                # Record the transaction time with the panel
                #pmLastTransactionTime = self.getTimeFunction()  # get time now to track how long it takes for a reply

                # Unknown Message has been received
                if self.msgType_t is None:
                    log.info("[data receiver] Unhandled message {0}".format(hex(msgType)))
                    self.pmSendAck()
                else:
                    # Send an ACK if needed
                    if not self.coordinating_powerlink and self.msgType_t.ackneeded:
                        #log.debug("[data receiver] Sending an ack as needed by last panel status message " + hex(msgType).upper())
                        self.pmSendAck()
                    # Handle the message
                    log.debug("[data receiver] Received message " + hex(msgType).upper() + "   data " + self.toString(self.ReceiveData))
                    self.handle_packet(self.ReceiveData)
                    # Check response
                    if len(self.pmExpectedResponse) > 0 and msgType != 2:   # 2 is a simple acknowledge from the panel so ignore those
                        # We've sent something and are waiting for a reponse - this is it
                        #log.debug("[data receiver] msgType {0}  expected one of {1}".format(hex(msgType).upper(), [hex(no).upper() for no in self.pmExpectedResponse]))
                        if (msgType in self.pmExpectedResponse):
                            self.pmExpectedResponse.remove(msgType)
                            log.debug("[data receiver] msgType {0} got it so removed from list, list is now {1}".format(hex(msgType).upper(), [hex(no).upper() for no in self.pmExpectedResponse]))
                            self.pmSendMsgRetries = 0
                        else:
                            log.debug("[data receiver] msgType not in self.pmExpectedResponse   Waiting for next PDU :  expected {0}   got {1}".format([hex(no).upper() for no in self.pmExpectedResponse], hex(msgType).upper()))
                self.ReceiveData = bytearray(b'')
            else: # CRC check failed. However, it could be an 0x0A in the middle of the data packet and not the terminator of the message
                if len(self.ReceiveData) > 0xB0:
                    log.info("[data receiver] PDU with CRC error %s", self.toString(self.ReceiveData))
                    self.pmLogPdu(self.ReceiveData, "<-PM   PDU with CRC error")
                    #pmLastTransactionTime = self.getTimeFunction() - timedelta(seconds=1)
                    self.ReceiveData = bytearray(b'')
                    if msgType != 0xF1:        # ignore CRC errors on F1 message
                        self.pmCrcErrorCount = self.pmCrcErrorCount + 1
                    if (self.pmCrcErrorCount > MAX_CRC_ERROR):
                        self.pmCrcErrorCount = 0
                        self.pmHandleCommException("CRC errors")
                else:
                    a = self.calculate_crc(self.ReceiveData[1:-2])[0]
                    log.debug("[data receiver] Building PDU: Length is now %d bytes (apparently PDU not complete)    %s    checksum calcs %02x", len(self.ReceiveData), self.toString(self.ReceiveData), a)
        elif pdu_len <= 0xC0:
            #log.debug("[data receiver] Current PDU " + self.toString(self.ReceiveData) + "    adding " + str(hex(data).upper()))
            self.ReceiveData.append(data)
        else:
            log.debug("[data receiver] Dumping Current PDU " + self.toString(self.ReceiveData))
            self.ReceiveData = bytearray(b'') # messages should never be longer than 0xC0
        #log.debug("[data receiver] Building PDU " + self.toString(self.ReceiveData))

    # PDUs can be logged in a file. We use this to discover new never before seen
    #      PowerMax messages we need to decode and make sense of in long evenings...
    def pmLogPdu(self, PDU, message):
        a = 0
        #log.debug("Logging PDU " + message + "   :    " + self.toString(PDU))
#        if pmLogDebug:
#            logfile = pmLogFilename
#            outf = io.open(logfile , 'a')
#            if outf == None:
#                log.debug("Cannot write to debug file.")
#                return
#        filesize = outf:seek("end")
#        outf:close()
#        # empty file if it reaches 500 kb
#        if filesize > 500 * 1024:
#            outf = io.open(logfile , 'w')
#            outf:write('')
#            outf:close()
#        outf = io.open(logfile, 'a')
#        now = self.getTimeFunction()
#        outf:write(string.format("%s%s %s %s\n", os.date("%F %X", now), string.gsub(string.format("%.3f", now), "([^%.]*)", "", 1), direction, PDU))
#        outf:close()

    # Send an achnowledge back to the panel
    def pmSendAck(self, type_of_ack = False):
        """ Send ACK if packet is valid """
        lastType = self.pmLastPDU[1]
        #normalMode = (lastType >= 0x80) or ((lastType < 0x10) and (self.pmLastPDU[len(self.pmLastPDU) - 2] == 0x43))

        log.debug("[sending ack] Sending an ack back to Alarm powerlink = {0}{1}".format(self.pmPowerlinkMode, type_of_ack))
        # There are 2 types of acknowledge that we can send to the panel
        #    Normal    : For a normal message
        #    Powerlink : For when we are in powerlink mode
        if self.pmPowerlinkMode or type_of_ack:   # type_of_ack is True when panel has sent us an AB message
            message = pmSendMsg["MSG_ACKLONG"]
            assert(message is not None)
            e = VisonicListEntry(command = message, options = None)
            self.pmSendPdu(e)
        else:
            message = pmSendMsg["MSG_ACK"]
            assert(message is not None)
            e = VisonicListEntry(command = message, options = None)
            self.pmSendPdu(e)
        #yield from asyncio.sleep(0.25)
        sleep(0.1)

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

        if self.PowerMaster and packet[-2:-1][0] == self.calculate_crc(packet[1:-2])[0] + 1:
            log.info("[validatePDU] Validated a Packet with a checksum that is 1 different to the actual checksum, the powermaster 10 seems to do this!!!!")
            return True

        # Check the CRC
        if packet[-2:-1] == self.calculate_crc(packet[1:-2]):
            #log.debug("[validatePDU] VALID PACKET!")
            return True

        log.info("[validatePDU] Not valid packet, CRC failed, may be ongoing and not final 0A")
        return False

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

    def pmSendPdu(self, instruction : VisonicListEntry):
        """Encode and put packet string onto write buffer."""

        if self.suspendAllOperations:
            return
            
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
                    log.debug("[pmSendPdu] Options {0} {1} {2} {3}".format(type(s), type(a), s, a))
                    data[s] = a;                
                else:
                    log.debug("[pmSendPdu] Options {0} {1} {2} {3} {4}".format(type(s), type(a), s, a, len(a)))
                    for i in range(0, len(a)):
                        data[s + i] = a[i]
                        #log.debug("[pmSendPdu]        Inserting at {0}".format(s+i))

        #log.debug('[pmSendPdu] input data: %s', self.toString(packet))
        # First add header (0x0D), then the packet, then crc and footer (0x0A)
        sData = b'\x0D'
        sData += data
        sData += self.calculate_crc(data)
        sData += b'\x0A'

        # Log some usefull information in debug mode
        log.info("[pmSendPdu] Sending Command ({0})    raw data {1}".format(command.msg, self.toString(sData)))
        self.transport.write(sData)
        log.debug("[pmSendPdu]      waiting for message response {}".format([hex(no).upper() for no in self.pmExpectedResponse]))
        #yield from asyncio.sleep(0.25)
        #sleep(0.1)

    # This is called to queue a command.
    # If it is possible, then also send the message
    def SendCommand(self, message_type, **kwargs):
        """ Add a command to the send List 
            The List is needed to prevent sending messages too quickly normally it requires 500msec between messages """
        
        if self.suspendAllOperations:
            log.info("[SendCommand] Suspended all operations, not sending PDU")
            return
        
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
            log.info("[SendCommand] %s" % message.msg)

        # self.pmExpectedResponse will prevent us sending another message to the panel
        #   If the panel is lazy or we've got the timing wrong........
        #   If there's a timeout then resend the previous message. If that doesn't work then do a reset using triggerRestoreStatus function
        #  Do not resend during startup or download as the timing is critical anyway
        if not self.coordinating_powerlink and not self.DownloadMode and self.pmLastSentMessage != None and timeout and len(self.pmExpectedResponse) > 0:
            if not self.pmLastSentMessage.triedResendingMessage:
                # resend the last message
                log.info("[SendCommand] Re-Sending last message  {0}".format(self.pmLastSentMessage.command.msg))
                self.pmSendPdu(self.pmLastSentMessage)
                self.pmLastTransactionTime = self.getTimeFunction()
                self.pmLastSentMessage.triedResendingMessage = True
            else:
                # tried resending once, no point in trying again so reset settings, start from scratch
                log.info("[SendCommand] Tried Re-Sending last message but didn't work. Assume a powerlink timeout state and reset")
                self.triggerRestoreStatus() # this will call this function recursivelly to send the MSG_RESTORE.
                return
        elif len(self.SendList) > 0:    # This will send commands from the list, oldest first
            if interval is not None and len(self.pmExpectedResponse) == 0: # we are ready to send
                # check if the last command was sent at least 500 ms ago
                td = timedelta(milliseconds=800)
                ok_to_send = (interval > td) # pmMsgTiming_t[pmTiming].wait)
                #log.debug("[SendCommand]        ok_to_send {0}    {1}  {2}".format(ok_to_send, interval, td))
                if ok_to_send:
                    # pop the oldest item from the list, this could be the only item.
                    instruction = self.SendList.pop(0)
                    # Do we have to receive an acknowledge from the panel before we sent more messages
                    #self.pmWaitingForAckFromPanel = instruction.command.waitforack
                    self.reset_keep_alive_messages()   # no need to send i'm alive message for a while as we're about to send a command anyway
                    self.pmLastTransactionTime = self.getTimeFunction()
                    self.pmLastSentMessage = instruction
                    self.pmExpectedResponse.extend(instruction.response) # if an ack is needed it will already be in this list
                    self.pmSendPdu(instruction)

    # Clear the send queue and reset the associated parameters
    def ClearList(self):
        """ Clear the List, preventing any retry causing issue. """
        # Clear the List
        log.debug("[ClearList] Setting queue empty")
        self.SendList = []
        self.pmLastSentMessage = None

    # This is called by the asyncio parent when the connection is lost
    def connection_lost(self, exc):
        """Log when connection is closed, if needed call callback."""
        self.suspendAllOperations = True
        
        if exc:
            #log.exception("ERROR Connection Lost : disconnected due to exception  <{0}>".format(exc))
            log.error("ERROR Connection Lost : disconnected due to exception")
        else:
            log.error('ERROR Connection Lost : disconnected because of close/abort.')
        
        sleep(5.0) # a bit of time for the watchdog timers and keep alive loops to self terminate
        if self.disconnect_callback:
            log.error('                        Calling Exception handler.')
            self.disconnect_callback(exc)
        else:
            log.error('                        No Exception handler to call, terminating Component......')

    async def download_timer(self):
        # sleep for the duration that download is supposed to take
        await asyncio.sleep(DOWNLOAD_TIMEOUT)
        # if we're still doing download then do something
        if self.DownloadMode:
            log.warning("********************** Download Timer has Expired, Download has taken too long *********************")
            #log.warning("********************** Not sure what to do for a download timeout so do nothing ********************")
            # what to do here??????????????
            # Stop download mode
            self.DownloadMode = False
            # reset the receiving message data
            self.ReceiveData = bytearray(b'')
            # goto standard mode
            self.gotoStandardMode()

    # This puts the panel in to download mode. It is the start of determining powerlink access
    def Start_Download(self):
        """ Start download mode """
        PanelStatus["Mode"] = "Download"
        if not self.DownloadMode:
            #self.pmWaitingForAckFromPanel = False
            self.pmExpectedResponse = []
            log.info("[Start_Download] Starting download mode")
            self.SendCommand("MSG_DOWNLOAD", options = [3, DownloadCode]) #
            self.DownloadMode = True
            asyncio.ensure_future(self.download_timer(), loop = self.loop)
        else:
            log.debug("[Start_Download] Already in Download Mode (so not doing anything)")

    # pmHandleCommException: we have run into a communication error
    #   This currently doesn't do much as I assume a perfect connection.
    #   It just resets various variables and calls self.Initialise()
    def pmHandleCommException(self, what):
        """ Handle a Communication Exception, we've got out of sync """
        #outf = open(pmCrashFilename , 'a')
        #if (outf == None):
        #    log.debug("ERROR Exception : Cannot write to crash file.")
        #    return
        when = self.getTimeFunction()
        log.warning("*** There is a communication problem (" + what + ")! Executing a reload. ***")
        # initiate reload
        self.Initialise()

    # pmPowerlinkEnrolled
    # Attempt to enroll with the panel in the same was as a powerlink module would inside the panel
    def pmPowerlinkEnrolled(self):
        """ Attempt to Enroll as a Powerlink """
        log.info("[Enrolling Powerlink] Reading panel settings")
        self.SendCommand("MSG_DL", options = [1, pmDownloadItem_t["MSG_DL_PANELFW"]] )     # Request the panel FW
        self.SendCommand("MSG_DL", options = [1, pmDownloadItem_t["MSG_DL_SERIAL"]] )      # Request serial & type (not always sent by default)
        self.SendCommand("MSG_DL", options = [1, pmDownloadItem_t["MSG_DL_ZONESTR"]] )     # Read the names of the zones
        #fred = bytearray.fromhex('03 00 03 00 03')
        #self.SendCommand("MSG_DL", options = [1, pmDownloadItem_t["MSG_DL_ZONESIGNAL"], 6, fred] )  # Read Signal Strength of the wireless zones
        if self.PowerMaster:
            self.SendCommand("MSG_DL", options = [1, pmDownloadItem_t["MSG_DL_MR_SIRKEYZON"]] )
        self.SendCommand("MSG_START")      # Start sending all relevant settings please
        self.SendCommand("MSG_EXIT")       # Exit download mode

    # We can only use this function when the panel has sent a "installing powerlink" message i.e. AB 0A 00 01
    #   We need to clear the send queue and reset the send parameters to immediately send an MSG_ENROLL
    def SendMsg_ENROLL(self):
        """ Auto enroll the PowerMax/Master unit """
        if not self.doneAutoEnroll:
            self.doneAutoEnroll = True
            #yield from asyncio.sleep(1.0)
            sleep(1.0)
            log.info("[SendMsg_ENROLL]  download pin will be " + self.toString(DownloadCode))
            # Remove anything else from the List, we need to restart
            self.pmExpectedResponse = []
            # Clear the list
            self.ClearList()

            # The 3 and 4 ignore 0x0D header. Is this 3 or 4.  4 according to Lua plugin but 3 according to https://www.domoticaforum.eu/viewtopic.php?f=68&t=6581
            self.SendCommand("MSG_ENROLL",  options = [4, DownloadCode])

            # We are doing an auto-enrollment, most likely the download failed. Lets restart the download stage.
            if self.DownloadMode:
                log.debug("[SendMsg_ENROLL] Resetting download mode to 'Off' in order to retrigger it")
                self.DownloadMode = False
            self.Start_Download()
        else:
            log.warning("Warning: Trying to re enroll and it is only allowed once at the start")

        
# This class performs transactions based on messages
class PacketHandling(ProtocolBase):
    """Handle decoding of Visonic packets."""

    pmBypassOff = False         # Do we allow the user to bypass the sensors
    pmSilentPanic = False

    def __init__(self, *args, packet_callback: Callable=None, excludes=None, **kwargs) -> None:
        """Add packethandling specific initialization.

        packet_callback: called with every complete/valid packet
        received.
        """
        super().__init__(*args, **kwargs)
        if packet_callback:
            self.packet_callback = packet_callback
        self.exclude_sensor_list = []
        if excludes is not None:
            self.exclude_sensor_list = excludes
        self.pmPhoneNr_t = {}
        self.pmEventLogDictionary = {}
        # We do not put these pin codes in to the panel status
        self.pmPincode_t = [ ]  # allow maximum of 48 user pin codes

        self.lastSendOfDownloadEprom = self.getTimeFunction() - timedelta(seconds=100)  # take off 100 seconds so the first command goes through immediately
        
        # Store the sensor details
        self.pmSensorDev_t = {}
        # Used to deepcopy to see if anything has changed with the sensors
        self.pmSensorDevOld_t = {}

        # Store the X10 details
        self.pmX10Dev_t = {}
        
        self.pmLang = PanelSettings["PluginLanguage"]             # INTERFACE : Get the plugin language from HA, either "EN" or "NL"
        self.pmRemoteArm = PanelSettings["EnableRemoteArm"]       # INTERFACE : Does the user allow remote setting of the alarm
        self.pmRemoteDisArm = PanelSettings["EnableRemoteDisArm"] # INTERFACE : Does the user allow remote disarming of the alarm
        self.pmSensorBypass = PanelSettings["EnableSensorBypass"] # INTERFACE : Does the user allow sensor bypass, True or False
        self.MotionOffDelay = PanelSettings["MotionOffDelay"]     # INTERFACE : Get the motion sensor off delay time (between subsequent triggers)
        self.pmAutoCreate = True # What else can we do????? # PanelSettings["AutoCreate"]         # INTERFACE : Whether to automatically create devices
        self.OverrideCode = PanelSettings["OverrideCode"]         # INTERFACE : Get the override code (must be set if forced standard and not powerlink)

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
            # check every 2 seconds
            await asyncio.sleep(2.0)  # must be less than 5 seconds for self.suspendAllOperations:

            
    # pmWriteSettings: add a certain setting to the settings table
    #  So, to explain
    #      When we send a MSG_DL and insert the 4 bytes from pmDownloadItem_t, what we're doing is setting the page, index and len
    # This function stores the downloaded status and EPROM data
    def pmWriteSettings(self, page, index, setting):
        settings_len = len(setting)
        wrap = (index + settings_len - 0x100)
        sett = [bytearray(b''), bytearray(b'')]

        if settings_len > 0xB1:
            log.info("[Write Settings] Write Settings too long *****************")
            return
        if wrap > 0:
            #log.debug("[Write Settings] The write settings data is Split across 2 pages")
            sett[0] = setting[ : settings_len - wrap]  # bug fix in 0.0.6, removed the -1
            sett[1] = setting[settings_len - wrap : ]
            #log.debug("[Write Settings]         original len {0}   left len {1}   right len {2}".format(len(setting), len(sett[0]), len(sett[1]))) 
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
            #log.debug("[Write Settings] Writing settings page {0}  index {1}    setting {2}".format(page+i, index, self.toString(sett[i])))
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
        while page in self.pmRawSettings and retlen > 0:
            fred = self.pmRawSettings[page][index : index + retlen]
            retval = retval + fred
            page = page + 1
            retlen = retlen - len(fred)
            index = 0
        if settings_len == len(retval):
            #log.debug("[Read Settings]  len " + str(settings_len) + " returning " + self.toString(retval))
            return retval
        # return a bytearray filled with 0xFF values
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
#        if pmLogDebug:
#            dumpfile = pmSettingsFilename
#            outf = open(dumpfile , 'w')
#            if outf == None:
#                log.debug("Cannot write to debug file.")
#                return
#            log.debug("Dumping PowerMax settings to file")
#            outf.write("PowerMax settings on %s\n", os.date('%Y-%m-%d %H:%M:%S'))
#            for i in range(0, 0xFF):
#                if pmRawSettings[i] != None:
#                    for j in range(0, 0x0F):
#                        s = ""
#                        outf.write(string.format("%08X: ", i * 0x100 + j * 0x10))
#                        for k in range (1, 0x10):
#                            byte = string.byte(pmRawSettings[i], j * 0x10 + k)
#                            outf.write(string.format(" %02X", byte))
#                            s = (byte < 0x20 or (byte >= 0x80 and byte < 0xA0)) and (s + ".") or (s + string.char(byte))
#                        outf.write("  " + s + "\n")
#            outf.close()

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
            log.info("pmPanelTypeNr {0}    model {1}".format(pmPanelTypeNr, PanelStatus["Model"]))

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

            if self.pmPowerlinkMode:
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
                                #log.info( "{0:<18}  {1:<40}  {2}".format(key, DecodePanelSettings[key].name, result[0]))
                                if len(result[0]) > 0:
                                    PanelStatus[DecodePanelSettings[key].name] = result[0]
                            else:
                                #log.info( "{0:<18}  {1}  {2}".format(key, DecodePanelSettings[key].name, result))
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
                #    log.debug("[Process Settings]      User {0} has code {1}".format(i, self.toString(code)))

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Store partition info & check if partitions are on
                partition = self.pmReadSettings(pmDownloadItem_t["MSG_DL_PARTITIONS"])
                if partition is None or partition[0] == 0:
                    partitionCnt = 1

                # ------------------------------------------------------------------------------------------------------------------------------------------------
                # Process zone settings
                zoneNames = bytearray()
                settingMr = bytearray()
                if not self.PowerMaster:
                    zoneNames = self.pmReadSettings(pmDownloadItem_t["MSG_DL_ZONENAMES"])
                else: # PowerMaster models
                    zoneNames = self.pmReadSettings(pmDownloadItem_t["MSG_DL_MR_ZONENAMES"])
                    settingMr = self.pmReadSettings(pmDownloadItem_t["MSG_DL_MR_ZONES"])

                setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_ZONES"])
                
                #zonesignalstrength = self.pmReadSettings(pmDownloadItem_t["MSG_DL_ZONESIGNAL"])
                #log.debug("ZoneSignal " + self.toString(zonesignalstrength))
                log.debug("[Process Settings] DL Zone settings " + self.toString(setting))
                log.debug("[Process Settings] Zones Names Buffer :  {0}".format(self.toString(zoneNames)))

                if len(setting) > 0 and len(zoneNames) > 0:
                    log.debug("[Process Settings] Zones:    len settings {0}     len zoneNames {1}    zoneCnt {2}".format(len(setting), len(zoneNames), zoneCnt))
                    for i in range(0, zoneCnt):
                        # data in the setting bytearray is in blocks of 4
                        zoneName = pmZoneName_t[zoneNames[i]]
                        zoneEnrolled = False
                        if not self.PowerMaster: # PowerMax models
                            zoneEnrolled = setting[i * 4 : i * 4 + 3] != bytearray.fromhex("00 00 00")
                            #log.debug("      Zone Slice is " + self.toString(setting[i * 4 : i * 4 + 4]))
                        else: # PowerMaster models (check only 5 of 10 bytes)
                            zoneEnrolled = settingMr[i * 10 : i * 10 + 5] != bytearray.fromhex("00 00 00 00 00")
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
                                    log.info("Found unknown sensor type " + str(sensorID_c))

                            else: # PowerMaster models
                                zoneInfo = int(setting[i])
                                sensorID_c = int(settingMr[i * 10 + 5])
                                sensorTypeStr = "UNKNOWN " + str(sensorID_c)
                                if sensorID_c in pmZoneSensorMaster_t:
                                    sensorTypeStr = pmZoneSensorMaster_t[sensorID_c].func
                                else:
                                    log.info("Found unknown sensor type " + str(sensorID_c))

                            zoneType = (zoneInfo & 0x0F)
                            zoneChime = ((zoneInfo >> 4) & 0x03)

                            part = []
                            if partitionCnt > 1:
                                for j in range (1, partitionCnt):
                                    if partition[0x11 + i] & (1 << (j - 1)) > 0:
                                        #log.debug("[Process Settings] Adding to partition list")
                                        part.append(j)
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
                                self.pmSensorDev_t[i].id=i+1
                            elif (i+1) not in self.exclude_sensor_list:
                                self.pmSensorDev_t[i] = SensorDevice(stype = sensorTypeStr, sid = sensorID_c, ztype = zoneType,
                                             ztypeName = pmZoneType_t[self.pmLang][zoneType], zname = zoneName, zchime = pmZoneChime_t[self.pmLang][zoneChime],
                                             dname="Z{0:0>2}".format(i+1), partition = part, id=i+1)
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
                        
                    #log.debug("X10 device {0} {1}".format(i, deviceStr))
                    
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

                #  INTERFACE : Add these self.pmSensorDev_t[i] params to the status panel
                PanelStatus["Door Zones"] = doorZones
                PanelStatus["Motion Zones"] = motionZones
                PanelStatus["Smoke Zones"] = smokeZones
                PanelStatus["Other Zones"] = otherZones
                PanelStatus["Devices"] = devices

                self.sendResponseEvent ( visonic_devices )

            # INTERFACE : Create Partitions in the interface
            #for i in range(1, partitionCnt+1): # TODO

        elif pmPanelTypeNr is None or pmPanelTypeNr == 0xFF:
            log.info("WARNING: Cannot process settings, we're probably connected to the panel in standard mode")
        else:
            log.info("WARNING: Cannot process settings, the panel is too new")

        log.info("[Process Settings] Ready for use")
        self.DumpSensorsToDisplay()
        
        if self.pmPowerlinkMode:
            PanelStatus["Mode"] = "Powerlink"
            #self.SendCommand("MSG_RESTORE") # also gives status
            self.triggerRestoreStatus()
        else:
            PanelStatus["Mode"] = "Standard"
            self.SendCommand("MSG_STATUS")

        if PanelSettings["AutoSyncTime"]:  # should we sync time between the HA and the Alarm Panel
            t = datetime.now()
            if t.year > 2000:
                year = t.year - 2000
                values = [t.second, t.minute, t.hour, t.day, t.month, year]
                timePdu = bytearray(values)
                #self.pmSyncTimeCheck = t
                self.SendCommand("MSG_SETTIME", options = [3, timePdu] )
            else:
                log.info("[Enrolling Powerlink] Please correct your local time.")

    def handle_packet(self, packet):
        """Handle one raw incoming packet."""

        # during early initialisation we need to ignore all incoming data to establish a known state in the panel
        if self.coordinating_powerlink:
            self.receive_log.append(packet)
            return

        log.debug("[handle_packet] Parsing complete valid packet: %s", self.toString(packet))

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
        elif packet[1] == 0xb0: # PowerMaster Event
            self.handle_msgtypeB0(packet[2:-2])
        else:
            log.info("[handle_packet] Unknown/Unhandled packet type {0}".format(packet[1:2]))
        # clear our buffer again so we can receive a new packet.
        self.ReceiveData = bytearray(b'')

    def displayzonebin(self, bits):
        """ Display Zones in reverse binary format
          Zones are from e.g. 1-8, but it is stored in 87654321 order in binary format """
        return bin(bits)

    def handle_msgtype02(self, data): # ACK
        """ Handle Acknowledges from the panel """
        # Normal acknowledges have msgtype 0x02 but no data, when in powerlink the panel also sends data byte 0x43
        #    I have not found this on the internet, this is my hypothesis
        log.debug("[handle_msgtype02] Ack Received  data = {0}".format(self.toString(data)))
        while 0x02 in self.pmExpectedResponse:
            self.pmExpectedResponse.remove(0x02)
        #self.pmWaitingForAckFromPanel = False

    def handle_msgtype06(self, data):
        """ MsgType=06 - Time out
        Timeout message from the PM, most likely we are/were in download mode """
        log.info("[handle_msgtype06] Timeout Received  data {0}".format(self.toString(data)))
        self.pmExpectedResponse = []
        self.DownloadMode = False
        self.ClearList()
        self.pmSendAck()
        self.SendCommand("MSG_EXIT")
        self.SendCommand("MSG_STOP")        
        if self.pmPowerlinkMode:
            self.SendCommand("MSG_RESTORE")
        else:
            self.SendCommand("MSG_STATUS")

    def handle_msgtype08(self, data):
        log.info("[handle_msgtype08] Access Denied  len {0} data {1}".format(len(data), self.toString(data)))

        if self.pmLastSentMessage is not None:
            lastCommandData = self.pmLastSentMessage.command.data
            log.debug("[handle_msgtype08]                last command {0}".format( self.toString(lastCommandData)))
            self.reset_watchdog_timeout()
            if lastCommandData is not None:
                if lastCommandData[0] != 0xAB and lastCommandData[0] & 0xA0 == 0xA0:  # this will match A0, A1, A2, A3 etc but not 0xAB
                    log.debug("[handle_msgtype08] Attempt to send a command message to the panel that has been denied, wrong pin code used")
                    # INTERFACE : tell user that wrong pin has been used
                    self.sendResponseEvent ( 5 )  # push changes through to the host, the pin has been rejected
                    
                elif lastCommandData[0] == 0x24:
                    log.debug("[handle_msgtype08] Got an Access Denied and we have sent a Download command to the Panel")

    def handle_msgtype0B(self, data): # STOP
        """ Handle STOP from the panel """
        log.info("[handle_msgtype0B] Stop    data is {0}".format(self.toString(data)))
        # This is the message to tell us that the panel has finished download mode, so we too should stop download mode
        self.DownloadMode = False
        self.pmExpectedResponse = []
        #self.pmWaitingForAckFromPanel = False
        if self.pmLastSentMessage is not None:
            lastCommandData = self.pmLastSentMessage.command.data
            log.debug("[handle_msgtype0B]                last command {0}".format(self.toString(lastCommandData)))
            if lastCommandData is not None:
                if lastCommandData[0] == 0x0A:
                    log.info("[handle_msgtype0B] We're almost in powerlink mode *****************************************")
                    self.pmPowerlinkMode = True  # INTERFACE set State to "PowerLink"
                    # We received a download exit message, restart timer
                    self.reset_watchdog_timeout()
                    self.ProcessSettings()

    def handle_msgtype25(self, data): # Download retry
        """ MsgType=25 - Download retry
        Unit is not ready to enter download mode
        """
        # Format: <MsgType> <?> <?> <delay in sec>
        iDelay = data[2]
        log.info("[handle_msgtype25] Download Retry, have to wait {0} seconds     data is {1}".format(iDelay, self.toString(data)))        
        # self.loop.call_later(int(iDelay), self.download_retry())
        self.DownloadMode = False
        self.doneAutoEnroll = False
        sleep(iDelay)
        ## dont bother with another download attemp as they never work, attempt to start again
        asyncio.ensure_future(self.coordinate_powerlink_startup(4), loop = self.loop)

    def handle_msgtype33(self, data):
        """ MsgType=33 - Settings
        Message send after a MSG_START. We will store the information in an internal array/collection """

        if len(data) != 10:
            log.info("[handle_msgtype33] ERROR: MSGTYPE=0x33 Expected len=14, Received={0}".format(len(self.ReceiveData)))
            log.info("[handle_msgtype33]                            " + self.toString(self.ReceiveData))
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

        if not self.doneAutoEnroll:
            # when here, the first download did not get denied 
            #     we did not get an 08 message back from the panel
            #     we did not get an AB 00 01 request from the panel to auto enroll
            # Remove anything else from the List, we need to restart
            self.pmExpectedResponse = []
            # Clear the list
            self.ClearList()

            if not self.ForceStandardMode:
                self.doneAutoEnroll = False
                log.info("[handle_msgtype3C] Attempt to auto-enroll")
                self.DownloadMode = False
                self.SendMsg_ENROLL()
            else:
                self.SendCommand("MSG_STATUS")
        
        # We got a first response, now we can continue enrollment the PowerMax/Master PowerLink
        interval = self.getTimeFunction() - self.lastSendOfDownloadEprom
        td = timedelta(seconds=90)  # prevent multiple requests for the EPROM panel settings, at least 90 seconds 
        if interval > td:
            self.lastSendOfDownloadEprom = self.getTimeFunction()
            self.pmPowerlinkEnrolled()

#            if PanelSettings["AutoSyncTime"]:  # should we sync time between the HA and the Alarm Panel
#                t = datetime.now()
#                if t.year > 2000:
#                    year = t.year - 2000
#                    values = [t.second, t.minute, t.hour, t.day, t.month, year]
#                    timePdu = bytearray(values)
#                    #self.pmSyncTimeCheck = t
#                    self.SendCommand("MSG_SETTIME", options = [3, timePdu] )
#                else:
#                    log.info("[Enrolling Powerlink] Please correct your local time.")

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
            log.info("[handle_msgtype3F]                            " + self.toString(self.ReceiveData))
            return

        # Write to memory map structure, but remove the first 4 bytes (3F/index/page/length) from the data
        self.pmWriteSettings(iPage, iIndex, data[3:])

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
            log.debug("Log Event {0}".format(self.pmEventLogDictionary[idx]))
            
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
        
    def handle_msgtypeA6(self, data):
        """ MsgType=A6 - Zone Types I think """
        log.info("[handle_MsgTypeA6] Packet = {0}".format(self.toString(data)))
        # Commented Out
        #   I assumed that the 5 A6 messages were similar to the 5 A3 messages, giving the type and chime info (as per the EPROM download)
        #      It doesn't look like it so it's commented out until I can figure out what they are
        #     Example data streams from my alarm, 5 data packets (header, mgstype, checksum and footer removed)
        #              04 01 2a 2a 2a 25 25 25 25 25 43     # This is supposed to tell us the total message count i.e. 4
        #              04 01 2a 2a 2a 25 25 25 25 25 43     # Then this is message 1 of 4  (zones 1 to 8)
        #              04 02 25 25 24 24 25 25 24 25 43     # Then this is message 2 of 4  (zones 9 to 16)
        #              04 03 25 25 25 29 29 1f 1f 27 43     # Then this is message 3 of 4  (zones 17 to 24)
        #              04 04 27 28 28 1e 22 28 00 00 43     # Then this is message 4 of 4  (zones 25 to 32)
        #        e.g. If we decoded the same as the EPROM zone type for zone 1, 2 and 3 (showing 2a in my examples above):
        #                   2a & 0x0F would give  0x0A for the type "24 Hours Audible" which is wrong, mine should be "Interior" as they are PIRs
        if not self.pmPowerlinkMode:
            msgCnt = int(data[0])
            offset = 8 * (int(data[1]) - 1)
            for i in range (0, 8):
                zoneInfo = int(data[2+i]) - 0x1E        #  in other code data[2+i] - 0x1E;
                zoneType = (zoneInfo & 0x0F) + 1        #  in other code add one
                zoneChime = ((zoneInfo >> 4) & 0x03)
                log.debug("                        Zone type for {0} is {1}   chime {2}".format( offset+i+1, pmZoneType_t[self.pmLang][zoneType], pmZoneChime_t[self.pmLang][zoneChime]))
        #        if offset+i in self.pmSensorDev_t:
        #            self.pmSensorDev_t[offset+i].ztype = zoneType
        #            self.pmSensorDev_t[offset+i].ztypeName = pmZoneType_t[self.pmLang][zoneType]
        #            self.pmSensorDev_t[offset+i].zchime = pmZoneChime_t[self.pmLang][zoneChime]
        #            self.pmSensorDev_t[offset+i].pushChange()

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
            #if not self.pmPowerlinkMode:
            log.debug("Got A5 02 message, resetting watchdog")
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
            visonic_devices = defaultdict(list)
            for i in range(0, 16):
                status = x10status & (1 << i)
                if i in self.pmX10Dev_t:
                    # INTERFACE : use this to set X10 status
                    oldstate = self.pmX10Dev_t[i].state
                    self.pmX10Dev_t[i].state = bool(status)
                    # Check to see if the state has changed
                    if ( oldstate and not self.pmX10Dev_t[i].state ) or (not oldstate and self.pmX10Dev_t[i].state):
                        log.debug("X10 device {0} changed to {1}".format(i, status))
                        self.pmX10Dev_t[i].pushChange()
                else:
                    if i == 0:
                        x10Location = "PGM"
                        x10DeviceName = "PGM"
                        x10Type = "onoff"
                    else:
                        x10Location = "Unknown"
                        x10DeviceName = "X{0:0>2}".format(i)
                        x10Type = "dim"
                    self.pmX10Dev_t[i] = X10Device(name = x10DeviceName, type = x10Type, location = x10Location, id=i, enabled=True)
                    self.pmX10Dev_t[i].state = bool(status)
                    visonic_devices['switch'].append(self.pmX10Dev_t[i])
             
            if len(visonic_devices) > 0:
                self.sendResponseEvent ( visonic_devices )
                    
            slog = pmDetailedArmMode_t[sysStatus]
            sarm_detail = "Unknown"
            if 0 <= sysStatus < len(pmSysStatus_t[self.pmLang]):
                sarm_detail = pmSysStatus_t[self.pmLang][sysStatus]

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
                    # Alarm Event 
                    self.pmSirenActive = self.getTimeFunction()
                    self.sendResponseEvent ( 3 )   # Alarm Event
                if self.pmSirenActive is not None and sarm == "Disarmed":
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
                        elif (i+1) not in self.exclude_sensor_list:
                            # we dont know about it so create it and make it enrolled
                            self.pmSensorDev_t[i] = SensorDevice(dname="Z{0:0>2}".format(i+1), id=i+1, enrolled = True)
                            visonic_devices['sensor'].append(self.pmSensorDev_t[i])
                            if not send_zone_type_request:
                                self.SendCommand("MSG_ZONENAME")
                                #self.SendCommand("MSG_ZONETYPE")   # The panel reples back with the correct number of A3 messages but I can't decode them
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

    # 01 00 21 52 01 ff 00 01 00 00 43
    # 01 00 21 55 01 ff 00 01 00 00 43
    
    # 01 00 1f 52 01 ff 00 00 00 00 43  # crashed just after 050818    [handle_msgtypeA7]      A7 message contains 1 messages
    def handle_msgtypeA7(self, data):
        """ MsgType=A7 - Panel Status Change """
        log.info("[handle_msgtypeA7] Panel Status Change " + self.toString(data))

        pmTamperActive = None
        msgCnt = int(data[0])
        # don't know what this is (It is 0x00 in test messages so could be the higher 8 bits for msgCnt)
        #   In a log file I reveiced from UffeNisse, there was this A7 message 0d a7 ff 64 00 60 00 ff 00 0c 00 00 43 45 0a
        #                                                                          msgCnt is 0xFF and temp is 0x64 ????
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
                
                # INTERFACE Indicate whether siren active
                PanelStatus["Panel Siren Active"] = 'Yes' if self.pmSirenActive != None else 'No'

                log.info("[handle_msgtypeA7]           self.pmSirenActive={0}   siren={1}   eventType={2}   self.pmSilentPanic={3}   tamper={4}".format(self.pmSirenActive, siren, hex(eventType), self.pmSilentPanic, tamper) )
                
                #---------------------------------------------------------------------------------------
                if eventType == 0x60: # system restart
                    log.warning("[handle_msgtypeA7]         Panel has been reset. Don't do anything and the comms will fail and then we'll reconnect")
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
        self.pmSendAck(True)

        # Restart the timer
        self.reset_watchdog_timeout()

        subType = self.ReceiveData[2]
        if subType == 3: # keepalive message
            # Example 0D AB 03 00 1E 00 31 2E 31 35 00 00 43 2A 0A
            log.info("[handle_msgtypeAB] ***************************** Got PowerLink Keep-Alive ****************************")
            # set downloading to False, if we are getting keep alive messages from the panel then we are not downloading
            # It is possible to receive this between enrolling (when the panel accepts the enroll successfully) and the EPROM download
            #     I suggest we simply ignore it
            if not self.pmPowerlinkMode:
                if self.DownloadMode:
                    log.info("[handle_msgtypeAB]         Got alive message while not in Powerlink mode but we're in Download mode")
                else:
                    log.info("[handle_msgtypeAB]         Got alive message while not in Powerlink mode and not in Download mode")
                #self.SendCommand("MSG_RESTORE") # also gives status
            else:
                self.DumpSensorsToDisplay()
        elif subType == 5: # -- phone message
            action = self.ReceiveData[4]
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
                log.debug("[handle_msgtypeAB] PowerLink Phone: Unknown Action {0}".format(hex(self.ReceiveData[3]).upper()))
        elif subType == 10 and self.ReceiveData[4] == 0:
            log.debug("[handle_msgtypeAB] PowerLink telling us what the code is for downloads, currently commented out as I'm not certain of this")
            #DownloadCode[0] = self.ReceiveData[5]
            #DownloadCode[1] = self.ReceiveData[6]
        elif subType == 10 and self.ReceiveData[4] == 1 and not self.doneAutoEnroll:
            if not self.ForceStandardMode:
                log.info("[handle_msgtypeAB] PowerLink most likely wants to auto-enroll, only doing auto enroll once")
                self.DownloadMode = False
                self.SendMsg_ENROLL()
        elif subType == 10 and self.ReceiveData[4] == 1:
            self.DownloadMode = False
            self.doneAutoEnroll = False
            asyncio.ensure_future(self.coordinate_powerlink_startup(4), loop = self.loop)

    def handle_msgtypeB0(self, data): # PowerMaster Message
        """ MsgType=B0 - Panel PowerMaster Message """
#        msgSubTypes = [0x00, 0x01, 0x02, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x18, 0x19, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x21, 0x24, 0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x32, 0x33, 0x34, 0x38, 0x39, 0x3A ]
        msgType = data[0] # 00, 01, 04: req; 03: reply, so expecting 0x03
        subType = data[1]
        msgLen  = data[2]
        log.info("[handle_msgtypeB0] Received PowerMaster message {0}/{1} (len = {2})".format(msgType, subType, msgLen))
        if msgType == 0x03 and subType == 0x39:
            log.debug("[handle_msgtypeB0]      Sending special PowerMaster Commands to the panel")
            self.SendCommand("MSG_POWERMASTER", options = [2, pmSendMsgB0_t["ZONE_STAT1"]])    #
            self.SendCommand("MSG_POWERMASTER", options = [2, pmSendMsgB0_t["ZONE_STAT2"]])    #
        if msgType == 0x03 and subType == 0x04:
            # Zone information (probably)
            log.info("[handle_msgtypeB0] Received PowerMaster message, zone information")
            #for o in range(1, data[6]):
                # zone o 
        if msgType == 0x03 and subType == 0x18:
            # Open/Close information (probably)
            log.info("[handle_msgtypeB0] Received PowerMaster message, open/close information")

    # pmGetPin: Convert a PIN given as 4 digit string in the PIN PDU format as used in messages to powermax
    def pmGetPin(self, pin):
        """ Get pin and convert to bytearray """
        if pin is None or pin == "" or len(pin) != 4:
            if self.OverrideCode is not None and len(self.OverrideCode) > 0:
                pin = self.OverrideCode
            elif self.pmPowerlinkMode:
                return True, self.pmPincode_t[0]   # if powerlink, then we downloaded the pin codes. Use the first one
            else:
                log.warning("Warning: Valid 4 digit PIN needed and not in Powerlink mode")
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

    def __init__(self, *args, command_queue = None, event_callback: Callable = None,
                 ignore: List[str] = None, **kwargs) -> None:
        """Add eventhandling specific initialization."""
        super().__init__(*args, **kwargs)
        self.event_callback = event_callback
        if ignore:
            log.debug('ignoring: %s', ignore)
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
                log.debug("Calling event log")
                self.GetEventLog(command[1])
            elif command[0] == "bypass":
                log.debug("Calling bypass for individual sensors")
                self.SetSensorArmedState(command[1], command[2], command[3])
            elif command[0] == "x10":
                log.debug("Calling x10 command")
                self.SendX10Command(command[1], command[2])
            elif command[0] in pmArmMode_t:
                self.RequestArm(command[0], command[1])
            else:
                log.warning("Processing unknown queue command {0}".format(command))
                

    # RequestArm
    #       state is one of: "Disarmed", "Stay", "Armed", "UserTest", "StayInstant", "ArmedInstant", "Night", "NightInstant"
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink

    def RequestArm(self, state, pin = ""):
        """ Send a request to the panel to Arm/Disarm """
        isValidPL, bpin = self.pmGetPin(pin)
        armCode = None
        armCodeA = bytearray()
        if state in pmArmMode_t:
            armCode = pmArmMode_t[state]
            armCodeA.append(armCode)

        log.debug("RequestArmMode " + (state or "N/A"))     # + "  using pin " + self.toString(bpin))
        if armCode is not None:
            if isValidPL:
                if (state == "Disarmed" and self.pmRemoteDisArm) or (state != "Disarmed" and self.pmRemoteArm):
                    self.SendCommand("MSG_ARM", options = [3, armCodeA, 4, bpin])    #
                else:
                    log.info("Panel Access Not allowed, user setting prevent access")
            else:
                log.info("Panel Access Not allowed without pin")
        else:
            log.info("RequestArmMode invalid state requested " + (state or "N/A"))

    # Individually arm/disarm the sensors
    #   This sets/clears the bypass for each sensor
    #       zone is the zone number 1 to 31
    #       armedValue is a boolean ( False then Bypass, True then Arm )
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    #   Return : success or not
    #
    #   the MSG_BYPASSEN and MSG_BYPASSDI commands are the same i.e. command is A1
    #      byte 0 is the command A1
    #      bytes 1 and 2 are the pin
    #      bytes 3 to 6 are the Enable bits for the 32 zones
    #      bytes 7 to 10 are the Disable bits for the 32 zones 
    #      byte 11 is 0x43
    def SetSensorArmedState(self, zone, armedValue, pin = "") -> bool:  # was sensor instead of zone (zone in range 1 to 32).
        """ Set or Clear Sensor Bypass """
#        if self.pmPowerlinkMode:
        if not self.pmBypassOff:
            isValidPL, bpin = self.pmGetPin(pin)

            if isValidPL:
                bypassint = 1 << (zone - 1)
                log.info("SetSensorArmedState A " + hex(bypassint))
                # is it big or little endian, i'm not sure, needs testing
                y1, y2, y3, y4 = (bypassint & 0xFFFFFFFF).to_bytes(4, 'little')
                # These could be the wrong way around, needs testing
                bypass = bytearray([y1, y2, y3, y4])
                log.info("SetSensorArmedState B " + self.toString(bypass))
                if len(bpin) == 2 and len(bypass) == 4:
                    if armedValue:
                        self.SendCommand("MSG_BYPASSDI", options = [1, bpin, 7, bypass])
                    else:
                        self.SendCommand("MSG_BYPASSEN", options = [1, bpin, 3, bypass]) 
                    self.SendCommand("MSG_BYPASSTAT") # request status to check success and update sensor variable
                    return True
            else:
                log.info("Bypass option not allowed, invalid pin")
        else:
            log.info("Bypass option not enabled in panel settings.")
#        else:
#            log.info("Bypass setting only supported in Powerlink mode.")
        return False

    # Get the Event Log
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    def GetEventLog(self, pin = ""):
        """ Get Panel Event Log """
        log.info("GetEventLog")
        if not self.DownloadMode:
            isValidPL, bpin = self.pmGetPin(pin)
            if isValidPL:
                self.SendCommand("MSG_EVENTLOG", options=[4, bpin])
            else:
                log.info("Get Event Log not allowed, invalid pin")

    def SendX10Command(self, dev, state):
        # This is untested
        # "MSG_X10PGM"      : VisonicCommand(bytearray.fromhex('A4 00 00 00 00 00 99 99 00 00 00 43'), None  , False, "X10 Data" ),
        if not self.DownloadMode and dev >= 0 and dev <= 15:
            log.debug("Py Visonic : Send X10 Command : id = " + str(dev) + "   state = " + state)
            calc = (1 << dev)
            byteA = calc & 0xFF
            byteB = (calc >> 8) & 0xFF
            if state in pmX10State_t:
                what = pmX10State_t[state]
                self.SendCommand("MSG_X10PGM", options = [6, what, 7, byteA, 8, byteB])
                #self.SendCommand("MSG_STATUS")
            else:
                log.info("Send X10 Command : state not in pmX10State_t " + state)

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
#    if key == "PluginDebug":
#        log.debug("Setting Logger Debug to {0}".format(val))
#        if val == True:
#            level = logging.getLevelName('DEBUG')  # INFO, DEBUG
#            log.setLevel(level)
#        else:
#            level = logging.getLevelName('INFO')  # INFO, DEBUG
#            log.setLevel(level)

# Create a connection using asyncio using an ip and port
def create_tcp_visonic_connection(address, port, protocol=VisonicProtocol, command_queue = None, event_callback=None, disconnect_callback=None, loop=None, excludes=None):
    """Create Visonic manager class, returns tcp transport coroutine."""

    # use default protocol if not specified
    protocol = partial(
        protocol,
        loop=loop if loop else asyncio.get_event_loop(),
        event_callback=event_callback,
        disconnect_callback=disconnect_callback,
        excludes=excludes,
        command_queue = command_queue, 
#        ignore=ignore if ignore else [],
    )

    address = address
    port = port
    conn = loop.create_connection(protocol, address, port)

    return conn


# Create a connection using asyncio through a linux port (usb or rs232)
def create_usb_visonic_connection(port, baud=9600, protocol=VisonicProtocol, command_queue = None, event_callback=None, disconnect_callback=None, loop=None, excludes=None):
    """Create Visonic manager class, returns rs232 transport coroutine."""
    from serial_asyncio import create_serial_connection
    # use default protocol if not specified
    protocol = partial(
        protocol,
        loop=loop if loop else asyncio.get_event_loop(),
        event_callback=event_callback,
        disconnect_callback=disconnect_callback,
        excludes=excludes,
        command_queue = command_queue, 
 #        ignore=ignore if ignore else [],
    )

    # setup serial connection
    port = port
    baud = baud
    conn = create_serial_connection(loop, protocol, port, baud)

    return conn
