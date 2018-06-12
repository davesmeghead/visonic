"""Asyncio protocol implementation of Visonic PowerMaster/PowerMax.
  Based on the DomotiGa and Vera implementation:

  Credits:
    Initial setup by Wouter Wolkers and Alexander Kuiper.
    Thanks to everyone who helped decode the data.

  Converted to Python module by Wouter Wolkers and David Field
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

from concurrent.futures import ProcessPoolExecutor
from collections import defaultdict
from datetime import datetime
from time import sleep
from datetime import timedelta
from dateutil.relativedelta import *
from functools import partial
from typing import Callable, List
from serial_asyncio import create_serial_connection
from collections import namedtuple

HOMEASSISTANT = True

PLUGIN_VERSION = "0.0.1"

MAX_CRC_ERROR = 5
POWERLINK_RETRIES = 4

# If we are waiting on a message back from the panel or we are explicitly waiting for an acknowledge,
#    then wait this time before resending the message.
#  Note that not all messages will get a resend, only ones waiting for a specific response and/or are blocking on an ack
RESEND_MESSAGE_TIMEOUT = timedelta(seconds=10)

# We must get specific messages from the panel, if we do not in this time period then trigger a restore/status request
WATCHDOG_TIMEOUT = 60

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
   "PluginVersion"      : PLUGIN_VERSION,
   "CommExceptionCount" : 0,
   "Mode"               : "Unknown",

# A7 message decode
   "PanelLastEvent"     : "None",
   "PanelAlarmStatus"   : "None",
   "PanelTroubleStatus" : "None",
   "PanelSirenActive"   : False,

# A5 message decode
   "PanelStatus"        : "Unknown",
   "PanelStatusCode"    : 0,
   "PanelReady"         : False,
   "PanelAlertInMemory" : False,
   "PanelTrouble"       : False,
   "PanelBypass"        : False,
   "PanelStatusChanged" : False,
   "PanelAlarmEvent"    : False,

# from the EPROM download
   "Model"              : "",
   "ModelType"          : "Unknown",
   "PanelArmed"         : False,
   "PowerMaster"        : False,
   "PanelSoftware"      : "Unknown",
   "PanelName"          : "Unknown",
   "PanelSerial"        : "Unknown",
   "DoorZones"          : "Unknown",
   "MotionZones"        : "Unknown",
   "SmokeZones"         : "Unknown",
   "OtherZones"         : "Unknown",
   "Devices"            : "Unknown",
   "PhoneNumbers"       : "",
   "EntryTime1"         : 0,
   "EntryTime2"         : 0,
   "ExitTime"           : 0,
   "BellTime"           : 0,
   "SilentPanic"        : False,
   "QuickArm"           : False,
   "BypassOff"          : False
}

# use a named tuple for data and acknowledge
#    replytype is a message type from the Panel that we should get in response
#    waitforack, if True means that we should wait for the acknowledge from the Panel before progressing
VisonicCommand = collections.namedtuple('VisonicCommand', 'data replytype waitforack msg')
pmSendMsg = {
   "MSG_INIT"        : VisonicCommand(bytearray.fromhex('AB 0A 00 01 00 00 00 00 00 00 00 43'), None  ,  True, "Initializing PowerMax/Master PowerLink Connection" ),
   "MSG_ALIVE"       : VisonicCommand(bytearray.fromhex('AB 03 00 00 00 00 00 00 00 00 00 43'), None  , False, "I'm Alive Message To Panel" ),
   "MSG_ZONENAME"    : VisonicCommand(bytearray.fromhex('A3 00 00 00 00 00 00 00 00 00 00 43'), [0xA3], False, "Requesting Zone Names" ),
   "MSG_ZONETYPE"    : VisonicCommand(bytearray.fromhex('A6 00 00 00 00 00 00 00 00 00 00 43'), [0xA6], False, "Requesting Zone Types" ),
   "MSG_X10NAMES"    : VisonicCommand(bytearray.fromhex('AC 00 00 00 00 00 00 00 00 00 00 43'), None  , False, "Requesting X10 Names" ),
   "MSG_RESTORE"     : VisonicCommand(bytearray.fromhex('AB 06 00 00 00 00 00 00 00 00 00 43'), [0xA5], False, "Restore PowerMax/Master Connection" ), # It can take multiple of these to put the panel back in to powerlink
   "MSG_ENROLL"      : VisonicCommand(bytearray.fromhex('AB 0A 00 00 99 99 00 00 00 00 00 43'), [0xAB], False, "Auto-Enroll of the PowerMax/Master" ),
   "MSG_EVENTLOG"    : VisonicCommand(bytearray.fromhex('A0 00 00 00 99 99 00 00 00 00 00 43'), None  , False, "Retrieving Event Log" ),  # replytype is 0xA0
   "MSG_ARM"         : VisonicCommand(bytearray.fromhex('A1 00 00 00 99 99 00 00 00 00 00 43'), None  , False, "(Dis)Arming System" ),
   "MSG_STATUS"      : VisonicCommand(bytearray.fromhex('A2 00 00 00 00 00 00 00 00 00 00 43'), [0xA5], False, "Getting Status" ),
   "MSG_BYPASSTAT"   : VisonicCommand(bytearray.fromhex('A2 00 00 20 00 00 00 00 00 00 00 43'), [0xA5], False, "Bypassing" ),
   "MSG_X10PGM"      : VisonicCommand(bytearray.fromhex('A4 00 00 00 00 00 99 99 00 00 00 43'), None  , False, "X10 Data" ),
   "MSG_BYPASSEN"    : VisonicCommand(bytearray.fromhex('AA 99 99 00 00 00 00 00 00 00 00 43'), None  , False, "BYPASS Enable" ),
   "MSG_BYPASSDIS"   : VisonicCommand(bytearray.fromhex('AA 99 99 00 00 00 00 00 00 00 00 43'), None  , False, "BYPASS Disable" ),
   # Command codes (powerlink) do not have the 0x43 on the end and are only 11 values
   "MSG_DOWNLOAD"    : VisonicCommand(bytearray.fromhex('24 00 00 99 99 00 00 00 00 00 00')   , None  ,  True, "Start Download Mode" ),  #[0x3C]
   "MSG_SETTIME"     : VisonicCommand(bytearray.fromhex('46 F8 00 01 02 03 04 05 06 FF FF')   , None  , False, "Setting Time" ),   # may not need an ack
   "MSG_DL"          : VisonicCommand(bytearray.fromhex('3E 00 00 00 00 B0 00 00 00 00 00')   , [0x3F], False, "Download Data Set" ),
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

pmLogUser_t = [
   "System ", "Zone 01", "Zone 02", "Zone 03", "Zone 04", "Zone 05", "Zone 06", "Zone 07", "Zone 08",
   "Zone 09", "Zone 10", "Zone 11", "Zone 12", "Zone 13", "Zone 14", "Zone 15", "Zone 16", "Zone 17", "Zone 18",
   "Zone 19", "Zone 20", "Zone 21", "Zone 22", "Zone 23", "Zone 24", "Zone 25", "Zone 26", "Zone 27", "Zone 28",
   "Zone 29", "Zone 30", "Fob  01", "Fob  02", "Fob  03", "Fob  04", "Fob  05", "Fob  06", "Fob  07", "Fob  08",
   "User 01", "User 02", "User 03", "User 04", "User 05", "User 06", "User 07", "User 08", "Pad  01", "Pad  02",
   "Pad  03", "Pad  04", "Pad  05", "Pad  06", "Pad  07", "Pad  08", "Sir  01", "Sir  02", "2Pad 01", "2Pad 02",
   "2Pad 03", "2Pad 04", "X10  01", "X10  02", "X10  03", "X10  04", "X10  05", "X10  06", "X10  07", "X10  08",
   "X10  09", "X10  10", "X10  11", "X10  12", "X10  13", "X10  14", "X10  15", "PGM    ", "GSM    ", "P-LINK ",
   "PTag 01", "PTag 02", "PTag 03", "PTag 04", "PTag 05", "PTag 06", "PTag 07", "PTag 08"
]

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

pmArmed_t = {
   0x03 : "", 0x04 : "", 0x05 : "", 0x0A : "", 0x0B : "", 0x13 : "", 0x14 : "", 0x15 : ""
}

pmArmMode_t = {
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

pmZoneChime_t = ("Chime Off", "Melody Chime", "Zone Name Chime")

# Note: names need to match to VAR_xxx
pmZoneSensor_t = {
   0x3 : "Motion", 0x4 : "Motion", 0x5 : "Magnet", 0x6 : "Magnet", 0x7 : "Magnet", 0xA : "Smoke", 0xB : "Gas", 0xC : "Motion", 0xF : "Wired"
} # unknown to date: Push Button, Flood, Universal

ZoneSensorMaster = collections.namedtuple("ZoneSensorMaster", 'name func' )
pmZoneSensorMaster_t = {
   0x01 : ZoneSensorMaster("Next PG2", "Motion" ),
   0x04 : ZoneSensorMaster("Next CAM PG2", "Camera" ),
   0x16 : ZoneSensorMaster("SMD-426 PG2", "Smoke" ),
   0x1A : ZoneSensorMaster("TMD-560 PG2", "Temperature" ),
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

class LogEvent:
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

    transport = None  # type: asyncio.Transport

    # Are we expecting a variable length message from the panel
    pmVarLenMsg = False
    pmIncomingPduLen = 0
    pmSendMsgRetries = 0

    # The CRC Error Count for Received Messages
    pmCrcErrorCount = 0
    # Whether its a powermax or powermaster
    PowerMaster = False
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

    CommExceptionCount = 0

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
                self.triggerRestoreStatus()
                status_counter = 0  # delay status requests
                self.reset_keep_alive_messages()

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
        log.info('[Connection] Connected. Please wait up to 5 minutes for Sensors and X10 Devices to Appear')

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
        self.resetPanelSequence()
        self.SendCommand("MSG_STATUS")


    # during initialisation we need to ignore all incoming data to establish a known state in the panel
    # I attempted to coordinate it through variables and state and it was tricky
    #   having an async function to coordinate it works better
    async def coordinate_powerlink_startup(self, cyclecount):
        self.coordinate_powerlink_startup_count = self.coordinate_powerlink_startup_count + 1
        if self.coordinate_powerlink_startup_count > POWERLINK_RETRIES:
            # just go in to standard mode
            self.coordinating_powerlink = False
            self.pmExpectedResponse = []
            self.reset_keep_alive_messages()
            self.reset_watchdog_timeout()
            self.gotoStandardMode()
        elif self.coordinate_powerlink_startup_count <= POWERLINK_RETRIES:
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
                    #log.debug("[data receiver] Received message " + hex(msgType).upper())
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
            #self.transport.write(bytearray.fromhex('0D 02 43 BA 0A'))
            message = pmSendMsg["MSG_ACKLONG"]
            assert(message is not None)
            e = VisonicListEntry(command = message, options = None)
            self.pmSendPdu(e)
        else:
            #self.transport.write(bytearray.fromhex('0D 02 FD 0A'))
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
            log.info("[QueueMessage] %s" % message.msg)

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
                td = timedelta(milliseconds=1000)
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

    # This is called by the parent when the connection is lost
    def connection_lost(self, exc):
        """Log when connection is closed, if needed call callback."""
        if exc:
            log.exception("ERROR Connection Lost : disconnected due to exception  <{0}>".format(exc))
        else:
            log.debug('ERROR Connection Lost : disconnected because of close/abort.')
        self.suspendAllOperations = True
        sleep(5.0) # i bit of time for the watchdog timers and keep alive loops to self terminate
        if self.disconnect_callback:
            self.disconnect_callback(exc)

    async def download_timer(self):
        # sleep for the duration that download is supposed to take
        await asyncio.sleep(DOWNLOAD_TIMEOUT)
        # if we're still doing download then do something
        if self.DownloadMode:
            log.warning("********************** Download Timer has Expired, Download has taken too long *********************")
            log.warning("********************** Not sure what to do for a download timeout so do nothing ********************")
            # what to do here??????????????

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
        #outf:write(string.format("Exception %s occurred at %s\n", what, when))
        self.CommExceptionCount = self.CommExceptionCount + 1
        PanelStatus["CommExceptionCount"] = self.CommExceptionCount
        #outf:write("Exception count is now " + exceptions + "\n")
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
        #self.SendCommand("MSG_DL", options = [1, pmDownloadItem_t["MSG_DL_ZONESIGNAL"]] )  # Read Signal Strength of the wireless zones
        if self.PowerMaster:
            self.SendCommand("MSG_DL", options = [1, pmDownloadItem_t["MSG_DL_MR_SIRKEYZON"]] )
        self.SendCommand("MSG_START")      # Start sending all relevant settings please
        self.SendCommand("MSG_EXIT")       # Exit download mode

    # We can only use this function when the panel has sent a "installing powerlink" message i.e. AB 0A 00 01
    #   We need to clear the send queue ans reset the send parameters to immediately send an MSG_ENROLL
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
    pmBellTime = 1
    pmEntryDelay1 = 0
    pmEntryDelay2 = 0
    pmExitDelay = 0
    pmSilentPanic = False
    pmQuickArm = False
    pmForcedDisarmCode = bytearray.fromhex("00 00")

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

        self.pmLang = PanelSettings["PluginLanguage"]             # INTERFACE : Get the plugin language from HA, either "EN" or "NL"
        self.pmRemoteArm = PanelSettings["EnableRemoteArm"]       # INTERFACE : Does the user allow remote setting of the alarm
        self.pmRemoteDisArm = PanelSettings["EnableRemoteDisArm"] # INTERFACE : Does the user allow remote disarming of the alarm
        self.pmSensorBypass = PanelSettings["EnableSensorBypass"] # INTERFACE : Does the user allow sensor bypass, True or False
        self.MotionOffDelay = PanelSettings["MotionOffDelay"]     # INTERFACE : Get the motion sensor off delay time (between subsequent triggers)
        self.pmAutoCreate = True # What else can we do????? # PanelSettings["AutoCreate"]         # INTERFACE : Whether to automatically create devices
        self.OverrideCode = PanelSettings["OverrideCode"]         # INTERFACE : Get the override code (must be set if forced standard and not powerlink)

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
            sett[0] = setting[ : settings_len - wrap - 1]
            sett[1] = setting[settings_len - wrap : ]
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
                log.info("[Write Settings] OOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOO len = {0}".format(len(self.pmRawSettings[page + i])))
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
        panelSerialType = self.pmReadSettings(pmDownloadItem_t["MSG_DL_SERIAL"])
        if panelSerialType is not None:
            pmPanelTypeNr = panelSerialType[7]
            if pmPanelTypeNr in pmPanelType_t:
                model = pmPanelType_t[pmPanelTypeNr]
            else:
                model = "UNKNOWN"   # INTERFACE : PanelType set to model
            PanelStatus["Model"] = model
            self.dump_settings()

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

                # Process zone names of this panel
                setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_ZONESTR"])
                log.debug("[Process Settings] Zone Type Names")
                for i in range(0, 26 + customCnt):
                    s = setting[i * 0x10 : i * 0x10 + 0x0F]
                    #log.debug("  Zone name slice is " + self.toString(s))
                    if s[0] != 0xFF:
                        log.debug("[Process Settings]     Zone Type Names   {0}  name {1}   downloaded name ({2})".format(i,
                                                                                        pmZoneName_t[i], s.decode().strip()))
                        # Following line commented out as "TypeError: 'tuple' object does not support item assignment" exception. I'm not sure that we should override these anyway
                        #pmZoneName_t[i] = s.decode().strip()  # Update predefined list with the proper downloaded values

                # Process communication settings
                setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_PHONENRS"])
                log.debug("[Process Settings] Phone Numbers")
                phoneStr = ""
                for i in range(0, 4):
                    if i not in self.pmPhoneNr_t:
                        self.pmPhoneNr_t[i] = bytearray()
                    for j in range(0, 8):
                        #log.debug("[Process Settings] pos " + str((8 * i) + j))
                        nr = setting[(8 * i) + j]
                        if nr is not None and nr != 0xFF:
                            #if j == 0:
                            #    self.pmPhoneNr_t[i] = bytearray()
                            #if self.pmPhoneNr_t[i] != None:
                            self.pmPhoneNr_t[i] = self.pmPhoneNr_t[i].append(nr)
                    if len(self.pmPhoneNr_t[i]) > 0:
                        phoneStr = phonestr + self.toString(self.pmPhoneNr_t[i]) + ", "
                        log.debug("[Process Settings]      Phone nr " + str(i) + " = " + self.toString(self.pmPhoneNr_t[i]))
                
                # INTERFACE : Add these phone numbers to the status panel
                PanelStatus["PhoneNumbers"] = "{0}".format("" if len(phoneStr) == 0 else phoneStr[:-2])

                # Process alarm settings
                setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_COMMDEF"])
                self.pmEntryDelay1 = setting[0x00]
                self.pmEntryDelay2 = setting[0x01]
                self.pmExitDelay = setting[0x02]
                self.pmBellTime = setting[0x03]
                self.pmSilentPanic = self.calcBool(setting[0x19], 0x10)
                self.pmQuickArm = self.calcBool(setting[0x1A], 0x08)
                self.pmBypassOff = self.calcBool(setting[0x1B], 0xC0)
                self.pmForcedDisarmCode = setting[0x10 : 0x12]

                PanelStatus["EntryTime1"] = self.pmEntryDelay1
                PanelStatus["EntryTime2"] = self.pmEntryDelay2
                PanelStatus["ExitTime"] = self.pmExitDelay
                PanelStatus["BellTime"] = str(self.pmBellTime) + " Minutes"
                PanelStatus["SilentPanic"] = self.pmSilentPanic
                PanelStatus["QuickArm"] = self.pmQuickArm
                PanelStatus["BypassOff"] = self.pmBypassOff

                log.debug("[Process Settings] Alarm Settings pmBellTime {0} minutes     pmSilentPanic {1}   pmQuickArm {2}    pmBypassOff {3}  pmForcedDisarmCode {4}".format(self.pmBellTime,
                          self.pmSilentPanic, self.pmQuickArm, self.pmBypassOff, self.toString(self.pmForcedDisarmCode)))

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

                # Process software information
                setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_PANELFW"])
                panelEprom = setting[0x00 : 0x10]
                panelSoftware = setting[0x10 : 0x21]
                log.debug("[Process Settings] EPROM: {0}; SW: {1}".format(panelEprom.decode(), panelSoftware.decode()))
                #PanelStatus["PanelEprom"] = panelEprom.decode()
                PanelStatus["PanelSoftware"] = panelSoftware.decode()

                # Process panel type and serial
                idx = "{0:0>2}{1:0>2}".format(hex(panelSerialType[7]).upper()[2:], hex(panelSerialType[6]).upper()[2:])
                if idx in pmPanelName_t:
                    pmPanelName = pmPanelName_t[idx]
                else:
                    pmPanelName = "Unknown"

                pmPanelSerial = ""
                for i in range(0, 6):
                    nr = panelSerialType[i]
                    if nr == 0xFF:
                        s = "."
                    else:
                        s = "{0:0>2}".format(hex(nr).upper()[2:])
                    pmPanelSerial = pmPanelSerial + s
                log.debug("[Process Settings] Panel Name {0} with serial <{1}>".format(pmPanelName, pmPanelSerial))

                #  INTERFACE : Add these 2 params to the status panel
                PanelStatus["PanelName"] = pmPanelName
                PanelStatus["PanelSerial"] = pmPanelSerial

                # Store partition info & check if partitions are on
                partition = self.pmReadSettings(pmDownloadItem_t["MSG_DL_PARTITIONS"])
                if partition is None or partition[0] == 0:
                    partitionCnt = 1

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
                #log.debug("[Process Settings] DL Zone settings " + self.toString(setting))
                #log.debug("[Process Settings] Zones Names Buffer :  {0}".format(self.toString(zoneNames)))

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

                            else: # PowerMaster models
                                zoneInfo = int(setting[i])
                                sensorID_c = int(settingMr[i * 10 + 5])
                                sensorTypeStr = "UNKNOWN " + str(sensorID_c)
                                if sensorID_c in pmZoneSensorMaster_t:
                                    sensorTypeStr = pmZoneSensorMaster_t[sensorID_c].func

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
                                   i, pmZoneType_t[self.pmLang][zoneType], pmZoneChime_t[zoneChime], sensorID_c, sensorTypeStr, zoneName))

                            if i in self.pmSensorDev_t:
                                self.pmSensorDev_t[i].stype = sensorTypeStr
                                self.pmSensorDev_t[i].sid = sensorID_c
                                self.pmSensorDev_t[i].ztype = zoneType
                                self.pmSensorDev_t[i].ztypeName = pmZoneType_t[self.pmLang][zoneType]
                                self.pmSensorDev_t[i].zname = zoneName
                                self.pmSensorDev_t[i].zchime = pmZoneChime_t[zoneChime]
                                self.pmSensorDev_t[i].dname="Z{0:0>2}".format(i+1)
                                self.pmSensorDev_t[i].partition = part
                                self.pmSensorDev_t[i].id=i+1
                            elif (i+1) not in self.exclude_sensor_list:
                                self.pmSensorDev_t[i] = SensorDevice(stype = sensorTypeStr, sid = sensorID_c, ztype = zoneType,
                                             ztypeName = pmZoneType_t[self.pmLang][zoneType], zname = zoneName, zchime = pmZoneChime_t[zoneChime],
                                             dname="Z{0:0>2}".format(i+1), partition = part, id=i+1)
                                visonic_devices['sensor'].append(self.pmSensorDev_t[i])

                            if i in self.pmSensorDev_t:
                                if sensorTypeStr == "Magnet" or sensorTypeStr == "Wired" or sensorTypeStr == "Temperature":
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

                # Process PGM/X10 settings
                setting = self.pmReadSettings(pmDownloadItem_t["MSG_DL_PGMX10"])
                x10Names = self.pmReadSettings(pmDownloadItem_t["MSG_DL_X10NAMES"])
                for i in range (0, 16):
                    enabled = False
                    x10Name = 0x1F
                    for j in range (0, 9):
                        enabled = enabled or setting[5 + i + j * 0x10] != 0
                    if (i > 0):
                        x10Name = x10Names[i]
                        x10_t[i] = pmZoneName_t[x10Name]
                    if enabled or x10Name != 0x1F:
                        if i == 0:
                            deviceStr = deviceStr + "PGM"
                        else:
                            deviceStr = "{0},X{1:0>2}".format(deviceStr, i)

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
                PanelStatus["DoorZones"] = doorZones
                PanelStatus["MotionZones"] = motionZones
                PanelStatus["SmokeZones"] = smokeZones
                PanelStatus["OtherZones"] = otherZones
                PanelStatus["Devices"] = devices

                if self.event_callback is not None:
                    self.event_callback( visonic_devices )

            # INTERFACE : Create Partitions in the interface
            #for i in range(1, partitionCnt+1): # TODO

            # Add PGM and X10 devices
#            for i in range (0, 15):
#                if i == 0:
#                    s = "PGM"
#                    if s in devices:
#                        log.debug("[Process Settings] Creating Device")
#                        self.pmCreateDevice(s, "Switch", s)
#                else:
#                    s = "X%02d".format(i)
#                    #string.find(devices, s)
#                    if s in devices:
#                        if devices[i + 3 : i + 4] == "d":
#                            log.debug("[Process Settings] Creating Device")
#                            self.pmCreateDevice(s, "Dim", x10_t[i], s)
#                        else:
#                            log.debug("[Process Settings] Creating Device")
#                            self.pmCreateDevice(s, "Switch", x10_t[i], s)

            # Add sirens
            #for i in range (1, sirenCnt):
            #    sirenEnrolled = (string.find(devices, string.format("S%02d", i)) ~= nil)
            #    if (sirenEnrolled == true) then
            #        id = self.pmCreateDevice(i, "Siren")
            #        if (id ~= nil) then
            #           pmSirenDev_t[i] = findChild(pmPanelDev, id)

            # Add keypads
            #for i in range(1, keypad1wCnt):
            #    keypadEnrolled = (string.find(devices, string.format("K1%d", i)) ~= nil)
            #    if (keypadEnrolled == true) then
            #        self.pmCreateDevice(i, "Keypad", nil, "1-way")

            #for i in range (1, keypad2wCnt):
            #    keypadEnrolled = (string.find(devices, string.format("K2%d", i)) ~= nil)
            #    if (keypadEnrolled == true) then
            #        self.pmCreateDevice(i, "Keypad", nil, "2-way")
            #=================================================================================================================================
            #=================================================================================================================================
            #=================================================================================================================================
            #=================================================================================================================================
            #=================================================================================================================================
            #=================================================================================================================================
            #=================================================================================================================================
            #=================================================================================================================================
        elif pmPanelTypeNr is None or pmPanelTypeNr == 0xFF:
            log.info("WARNING: Cannot process settings, we're probably connected to the panel in standard mode")
        else:
            log.info("WARNING: Cannot process settings, the panel is too new")

        if self.pmPowerlinkMode:
            PanelStatus["Mode"] = "Powerlink"
            self.SendCommand("MSG_RESTORE") # also gives status
        else:
            PanelStatus["Mode"] = "Standard"
            self.SendCommand("MSG_STATUS")
        log.info("[Process Settings] Ready for use")
        self.DumpSensorsToDisplay()

    def handle_packet(self, packet):
        """Handle one raw incoming packet."""

        # during early initialisation we need to ignore all incoming data to establish a known state in the panel
        if self.coordinating_powerlink:
            self.receive_log.append(packet)
            return

        #log.debug("[handle_packet] Parsing complete valid packet: %s", self.toString(packet))

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
        if self.DownloadMode:
            self.DownloadMode = False
            self.SendCommand("MSG_EXIT")
        self.pmSendAck()
        if self.pmPowerlinkMode:
            self.SendCommand("MSG_RESTORE")
        else:
            self.SendCommand("MSG_STATUS")

    def handle_msgtype08(self, data):
        log.info("[handle_msgtype08] Access Denied  len {0} data {1}".format(len(data), self.toString(data)))

#        if self.pmLastSentMessage is not None:
#            lastCommandData = self.pmLastSentMessage.command.data
#            log.debug("[handle_msgtype08]                last command {0}".format( self.toString(lastCommandData)))
#            if lastCommandData is not None:
#                if lastCommandData[0] == 0x24:
#                    self.pmPowerlinkMode = False   # INTERFACE : Assume panel is going in to Standard mode
#                    #self.ProcessSettings()
#                elif lastCommandData[0] & 0xA0 == 0xA0:  # this will match A0, A1, A2, A3 etc
#                    log.debug("[handle_msgtype08] Attempt to send a command message to the panel that has been denied, probably wrong pin code used")
#                    # INTERFACE : tell user that wrong pin has been used

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
                    log.info("[handle_msgtype0B] We're in powerlink mode *****************************************")
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

        PanelStatus["ModelType"] = self.ModelType
        PanelStatus["PowerMaster"] = self.PowerMaster

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
            zoneStr = pmLogUser_t[iEventZone] or "UNKNOWN"
            eventStr = pmLogEvent_t[self.pmLang][iLogEvent] or "UNKNOWN"

            idx = eventNum - 1

            # Create an event log array
            self.pmEventLogDictionary[idx] = LogEvent()
            if pmPanelConfig_t["CFG_PARTITIONS"][self.PanelType] > 1:
                part = 0
                for i in range(1, 4):
                    part = (iSec % (2 * i) >= i) and i or part
                self.pmEventLogDictionary[idx].partition = (part == 0) and "Panel" or part
                self.pmEventLogDictionary[idx].time = "{0:0>2}:{1:0>2}".format(iHour, iMin)
            else:
                # This alarm panel only has a single partition so it must either be panal or partition 1
                self.pmEventLogDictionary[idx].partition = (iEventZone == 0) and "Panel" or "1"
                self.pmEventLogDictionary[idx].time = "{0:0>2}:{1:0>2}:{2:0>2}".format(iHour, iMin, iSec)
            self.pmEventLogDictionary[idx].date = "{0:0>2}/{1:0>2}/{2}".format(iDay, iMonth, iYear)
            self.pmEventLogDictionary[idx].zone = zoneStr
            self.pmEventLogDictionary[idx].event = eventStr
            #self.pmEventLogDictionary.items = idx
            #self.pmEventLogDictionary.done = (eventNum == self.eventCount)
            log.debug("Log Event {0}".format(self.pmEventLogDictionary[idx]))

            
    def handle_msgtypeA3(self, data):
        """ MsgType=A3 - Zone Names """
        log.info("[handle_MsgTypeA3] Wibble Packet = {0}".format(self.toString(data)))
        msgCnt = int(data[0])
        offset = 8 * (int(data[1]) - 1)
        for i in range(0, 8):
            zoneName = pmZoneName_t[int(data[2+i])]
            log.info("                        Zone name for {0} is {1}".format( offset+i+1, zoneName ))
            if offset+i in self.pmSensorDev_t:
                if not self.pmSensorDev_t[offset+i].zname:     # if not already set
                    self.pmSensorDev_t[offset+i].zname = zoneName
                    self.pmSensorDev_t[offset+i].pushChange()
                    log.info("                        Found Sensor")
        
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
        #if not self.pmPowerlinkMode:
        #    msgCnt = int(data[0])
        #    offset = 8 * (int(data[1]) - 1)
        #    for i in range (0, 8):
        #        zoneInfo = int(data[2+i])
        #        zoneType = (zoneInfo & 0x0F)
        #        zoneChime = ((zoneInfo >> 4) & 0x03)
        #        log.debug("Zone type for {0} is {1}   chime {2}".format( offset+i+1, pmZoneType_t[self.pmLang][zoneType], pmZoneChime_t[zoneChime]))
        #        if offset+i in self.pmSensorDev_t:
        #            self.pmSensorDev_t[offset+i].ztype = zoneType
        #            self.pmSensorDev_t[offset+i].ztypeName = pmZoneType_t[self.pmLang][zoneType]
        #            self.pmSensorDev_t[offset+i].zchime = pmZoneChime_t[zoneChime]
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

        if eventType == 0x01: # Log event print
            log.debug("[handle_msgtypeA5] Log Event Print")
        elif eventType == 0x02: # Status message zones
            # if in standard mode then use this A5 status message to reset the watchdog timer        
            if not self.pmPowerlinkMode:
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
            log.debug("[handle_msgtypeA5]      Trigger Status Zones 32-01: {:032b}".format(val))
            # This status is different from the status in the 0x02 part above i.e they are different values.
            #    This one is wrong (I had a door open and this status had 0, the one above had 1)
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
                if i == 0:
                   s = "PGM"
                else:
                   s = "X{:0>2}".format(i)
                #child = findChild(pmPanelDev, s)
                status = x10status & (1 << i)
                # INTERFACE : use this to set X10 status

            slog = pmDetailedArmMode_t[sysStatus]
            sarm_detail = "Unknown"
            if 0 <= sysStatus < len(pmSysStatus_t[self.pmLang]):
                sarm_detail = pmSysStatus_t[self.pmLang][sysStatus]
            
            if sysStatus in [0x03, 0x04, 0x05, 0x0A, 0x0B, 0x14, 0x15]:
                sarm = "Armed"
            elif sysStatus > 0x15:
                log.debug("[handle_msgtypeA5]      Unknown state, assuming Disarmed")
                sarm = "Disarmed"
            else:
                sarm = "Disarmed"

            log.debug("[handle_msgtypeA5]      log: {0}, arm: {1}".format(slog + "(" + sarm_detail + ")", sarm))

            PanelStatus["PanelStatusCode"]    = sysStatus
            PanelStatus["PanelStatus"]        = sarm_detail
            PanelStatus["PanelReady"]         = sysFlags & 0x01 != 0
            PanelStatus["PanelAlertInMemory"] = sysFlags & 0x02 != 0
            PanelStatus["PanelTrouble"]       = sysFlags & 0x04 != 0
            PanelStatus["PanelBypass"]        = sysFlags & 0x08 != 0
            if sysFlags & 0x10 != 0:  # last 10 seconds of entry/exit
                PanelStatus["PanelArmed"] = (sarm == "Arming")
            else:
                PanelStatus["PanelArmed"] = (sarm == "Armed")
            PanelStatus["PanelStatusChanged"] = sysFlags & 0x40 != 0
            PanelStatus["PanelAlarmEvent"]    = sysFlags & 0x80 != 0

            #cond = ""
            #for i in range(0,8):
            #    if sysFlags & (1<<i) != 0:
            #        cond = cond + pmSysStatusFlags_t[self.pmLang][i] + ", "
            #if len(cond) > 0:
            #    cond = cond[:-2]
            #PanelStatus["PanelStatusText"] = cond
            
            if sysFlags & 0x20 != 0:
                sEventLog = pmEventType_t[self.pmLang][eventType]
                log.debug("[handle_msgtypeA5]      Bit 5 set, Zone Event")
                log.debug("[handle_msgtypeA5]            Zone: {0}, {1}".format(eventZone, sEventLog))
                for key, value in self.pmSensorDev_t.items():
                    if value.id == eventZone:      # look for the device name
                        if eventType == 3: # Zone Open
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

            if self.event_callback is not None:
                self.event_callback( 1 )   # push changes through to the host to get it to update
            
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

                if self.event_callback is not None:
                    self.event_callback(visonic_devices)

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
    def handle_msgtypeA7(self, data):
        """ MsgType=A7 - Panel Status Change """
        log.info("[handle_msgtypeA7] Panel Status Change " + self.toString(data))

        msgCnt = int(data[0])
        temp = int(data[1])  # don't know what this is (It is 0x00 in test messages so could be the higher 8 bits for msgCnt)
        log.debug("[handle_msgtypeA7]      A7 message contains {0} messages".format(msgCnt))
        for i in range(0, msgCnt):
            eventZone = int(data[2 + (2 * i)])
            logEvent  = int(data[3 + (2 * i)])
            eventType = int(logEvent & 0x7F)
            s = (pmLogEvent_t[self.pmLang][eventType] or "UNKNOWN") + " / " + (pmLogUser_t[eventZone] or "UNKNOWN")
            alarmStatus = None
            if eventType in pmPanelAlarmType_t:
                alarmStatus = pmPanelAlarmType_t[eventType]
            troubleStatus = None
            if eventType in pmPanelTroubleType_t:
                troubleStatus = pmPanelTroubleType_t[eventType]

            PanelStatus["PanelLastEvent"]     = s
            PanelStatus["PanelAlarmStatus"]   = "None" if alarmStatus is None else alarmStatus
            PanelStatus["PanelTroubleStatus"] = "None" if troubleStatus is None else troubleStatus

            log.info("[handle_msgtypeA7]         System message " + s + "  alarmStatus " + PanelStatus["PanelAlarmStatus"] + "   troubleStatus " + PanelStatus["PanelTroubleStatus"])

            # Update siren status
            noSiren = ((eventType == 0x0B) or (eventType == 0x0C)) and self.pmSilentPanic

            if (alarmStatus is not None) and (eventType != 0x04) and (not noSiren):
                self.pmSirenActive = self.getTimeFunction()      # + timedelta(seconds = 60 * self.pmBellTime)
                log.debug("Alarm Active")
            if eventType == 0x1B and self.pmSirenActive is not None: # Cancel Alarm
                self.pmSirenActive = None
                log.debug("Alarm Cancelled")
            # INTERFACE Indicate whether siren active
            PanelStatus["PanelSirenActive"] = self.pmSirenActive != None

            log.debug("[handle_msgtypeA7]                self.pmSirenActive={0}   noSiren={1}   eventType={2}   self.pmSilentPanic={3}".format(self.pmSirenActive, noSiren, hex(eventType), self.pmSilentPanic) )
            
            if eventType == 0x60: # system restart
                log.warning("handle_msgtypeA7:         Panel has been reset")
                self.Start_Download()
                self.event_callback( 4 )   # push changes through to the host, the panel itself has been reset

        if self.event_callback is not None:
            if self.pmSirenActive is not None:
                self.event_callback( 3 )   # push changes through to the host to get it to update, alarm is active!!!!!!!!!
            else:
                self.event_callback( 2 )   # push changes through to the host to get it to update


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
        msgSubTypes = [0x00, 0x01, 0x02, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x18, 0x19, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x21, 0x24, 0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x32, 0x33, 0x34, 0x38, 0x39, 0x3A ]
        msgType = data[0] # 00, 01, 04: req; 03: reply, so expecting 0x03
        subType = data[1]
        msgLen  = data[2]
        log.info("[handle_msgtypeB0] Received PowerMaster message {0}/{1} (len = {2})".format(msgType, subType, msgLen))
        if msgType == 0x03 and subType == 0x39:
            log.debug("[handle_msgtypeB0]      Sending special PowerMaster Commands to the panel")
            self.SendCommand("MSG_POWERMASTER", options = [2, pmSendMsgB0_t["ZONE_STAT1"]])    #
            self.SendCommand("MSG_POWERMASTER", options = [2, pmSendMsgB0_t["ZONE_STAT2"]])    #

    # pmGetPin: Convert a PIN given as 4 digit string in the PIN PDU format as used in messages to powermax
    def pmGetPin(self, pin):
        """ Get pin and convert to bytearray """
        if pin == "" or pin is None or len(pin) != 4:
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

    def toYesNo(self, b):
        return "Yes" if b else "No"

    def DumpSensorsToDisplay(self):
        log.info("=============================================== Display Status ===============================================")
        for key, sensor in self.pmSensorDev_t.items():
            log.info("     key {0:<2} Sensor {1}".format(key, sensor))
        log.info("   Model {: <18}     PowerMaster {: <18}     LastEvent {: <18}     Ready   {: <13}".format(PanelStatus["Model"],
                                        self.toYesNo(PanelStatus["PowerMaster"]), PanelStatus["PanelLastEvent"], self.toYesNo(PanelStatus["PanelReady"])))
        log.info("   Mode  {: <18}     Status      {: <18}     Armed     {: <18}     Trouble {: <13}     AlarmStatus {: <12}".format(PanelStatus["Mode"], PanelStatus["PanelStatus"],
                                        self.toYesNo(PanelStatus["PanelArmed"]), PanelStatus["PanelTroubleStatus"], PanelStatus["PanelAlarmStatus"]))
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
                log.debug("Calling log is not yet implemented (because I don't know what to do with it when it downloads")
            elif command[0] == "bypass":
                log.debug("Calling bypass for individual sensors is not yet implemented")
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

        log.info("RequestArmMode " + (state or "N/A"))
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
    #   the MSG_BYPASSEN and MSG_BYPASSDIS commands are the same i.e. command is A1
    #      byte 0 is the command A1
    #      bytes 1 and 2 are the pin
    #      bytes 3 to 6 are the Enable bits for the 32 zones
    #      bytes 7 to 10 are the Disable bits for the 32 zones 
    #      byte 11 is 0x43
    def SetSensorArmedState(self, zone, armedValue, pin = "") -> bool:  # was sensor instead of zone (zone in range 1 to 32).
        """ Set or Clear Sensor Bypass """
        if self.pmPowerlinkMode:
            if not self.pmBypassOff:
                isValidPL, bpin = self.pmGetPin(pin)

                if isValidPL:
                    bypassint = int(2 ^ (zone - 1))
                    # is it big or little endian, i'm not sure, needs testing
                    y1, y2, y3, y4 = (bypassint & 0xFFFFFFFF).to_bytes(4, 'big')
                    # These could be the wrong way around, needs testing
                    bypass = bytearray([y1, y2, y3, y4])
                    if len(bpin) == 2 and len(bypass) == 4:
                        if armedValue:
                            self.SendCommand("MSG_BYPASSDIS", options = [1, bpin, 7, bypass])
                        else:
                            self.SendCommand("MSG_BYPASSEN", options = [1, bpin, 3, bypass]) # { pin = pmPincode_t[1], bypass = bypassStr })
                        self.SendCommand("MSG_BYPASSTAT") # request status to check success and update sensor variable
                        return True
                else:
                    log.info("Bypass option not allowed, invalid pin")
            else:
                log.info("Bypass option not enabled in panel settings.")
        else:
            log.info("Bypass setting only supported in Powerlink mode.")
        return False

    # Get the Event Log
    #       optional pin, if not provided then try to use the EPROM downloaded pin if in powerlink
    def GetEventLog(self, pin = ""):
        """ Get Panel Event Log """
        log.info("GetEventLog")
        isValidPL, bpin = self.pmGetPin(pin)
        if isValidPL:
            self.SendCommand("MSG_EVENTLOG", options=[4, bpin])
        else:
            log.info("Get Event Log not allowed, invalid pin")


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
        log.warning("Setting key {0} to value {1}".format(key, val))
        PanelSettings[key] = val
    else:
        log.warning("ERROR: ************************ Cannot find key {0} in panel settings".format(key))
    if key == "PluginDebug":
        log.debug("Setting Logger Debug to {0}".format(val))
        if val == True:
            level = logging.getLevelName('DEBUG')  # INFO, DEBUG
            log.setLevel(level)
        else:
            level = logging.getLevelName('INFO')  # INFO, DEBUG
            log.setLevel(level)

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


# # Do not call this directly, it is the thread that creates and keeps going the asyncio. It repackages an asyncio in to a task
# def visonicworker(tcp, address, port, event_callback=None, disconnect_callback=None, excludes=None):
    # mynewloop = asyncio.new_event_loop()
    # asyncio.set_event_loop(mynewloop)

    # log.debug("visonic worker")
    # try:
        # if tcp:
            # conn = create_tcp_visonic_connection(address=address, port=port, event_callback=event_callback, disconnect_callback=disconnect_callback, loop=mynewloop, excludes=excludes)
        # else:
            # conn = create_usb_visonic_connection(port=port, event_callback=event_callback, disconnect_callback=disconnect_callback, loop=mynewloop, excludes=excludes)
        # mynewloop.create_task(conn)
        # mynewloop.run_forever()

    # except KeyboardInterrupt:
        # # cleanup connection
        # conn.close()
        # mynewloop.run_forever()
    # finally:
        # mynewloop.close()

# # Create a task and start it
# def create_tcp_visonic_connection_task(address, port, event_callback=None, disconnect_callback=None, excludes=None):
    # #pool = ProcessPoolExecutor(1)
    # #future = pool.submit(visonicworker, True, address, port, event_callback, disconnect_callback)
    # #return pool

    # t = threading.Thread(target=visonicworker, args=(True, address, port, event_callback, disconnect_callback, excludes))
    # t.start()
    # return t

# # Create a task and start it
# def create_usb_visonic_connection_task(port, event_callback=None, disconnect_callback=None, excludes=None):
    # t = threading.Thread(target=visonicworker, args=(False, "dummy", port, event_callback, disconnect_callback, excludes))
    # t.start()
    # return t
