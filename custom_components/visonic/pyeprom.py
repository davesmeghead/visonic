
import os
# import requests

# The defaults are set for use in Home Assistant.
#    If using MicroPython / CircuitPython then set these values in the environment
MicroPython = os.getenv("MICRO_PYTHON")

if MicroPython is not None:
    import adafruit_logging as logging
    import binascii
#   from adafruit_datetime import datetime as datetime 
#   from adafruit_datetime import timedelta as timedelta
#   IntEnum = object
#   List = object
#   ABC = object
#   Callable = object

#   class ABC:
#       pass

#   def abstractmethod(f):
#       return f

    def convertByteArray(s) -> bytearray:
        b = binascii.unhexlify(s.replace(' ', ''))
        return bytearray(b)

    mylog = logging.getLogger(__name__)
    mylog.setLevel(logging.DEBUG)

else:
#   import inspect as ipt  
    import logging
#   import datetime as dt
#   from datetime import datetime, timedelta, timezone
#   from typing import Callable, List
#   import copy
#   from abc import abstractmethod

    def convertByteArray(s) -> bytearray:
        return bytearray.fromhex(s)

    mylog = logging.getLogger(__name__)

log = mylog

import collections
#import time
import math
from collections import namedtuple

try:
    from .pyenum import (EPROM, PanelTypeEnum)
    from .pyconst import (PanelConfig, EPROM_DOWNLOAD_ALL, NOBYPASSSTR, DISABLE_TEXT)
    from .pyhelper import (toString)
except:
    from pyenum import (EPROM, PanelTypeEnum)
    from pyconst import (PanelConfig, EPROM_DOWNLOAD_ALL, NOBYPASSSTR, DISABLE_TEXT)
    from pyhelper import (toString)

##############################################################################################################################################################################################################################################
##########################  EPROM Decode  ###################################################################################################################################################################################################
##############################################################################################################################################################################################################################################

# Set 1 of the following but not both, depending on the panel type
XDumpy = False # True     # Used to dump PowerMax Data to the log file
SDumpy = False # False    # Used to dump PowerMaster Data to the log file
Dumpy = XDumpy or SDumpy

# PMAX EPROM CONFIGURATION version 1_2
SettingsCommand = collections.namedtuple('SettingsCommand', 'show count type poff psize pstep pbitoff name values')

pmDecodePanelSettings = {                   #  show count  type     poff psize pstep pbitoff name                                   values
    "jamDetect"            : SettingsCommand(   True,  1, "BYTE",    256,   8,   0,    -1,  "Jamming Detection",                  { '1':"UL 20/20", '2':"EN 30/60", '3':"Class 6", '4':"Other", '0':"Disable"} ),
    "entryDelays"          : SettingsCommand(   True,  2, "BYTE",    257,   8,   1,     2,  ["Entry Delay 1","Entry Delay 2"],    { '0':"None", '15':"15 Seconds", '30':"30 Seconds", '45':"45 Seconds", '60':"1 Minute", '180':"3 Minutes", '240':"4 Minutes"}),  # 257, 258
    "exitDelay"            : SettingsCommand(   True,  1, "BYTE",    259,   8,   0,    -1,  "Exit Delay",                         { '30':"30 Seconds", '60':"60 Seconds", '90':"90 Seconds", '120':"2 Minutes", '180':"3 Minutes", '240':"4 Minutes"}),
    "bellTime"             : SettingsCommand(   True,  1, "BYTE",    260,   8,   0,    -1,  "Bell Time",                          { '1':"1 Minute", '3':"3 Minutes", '4':"4 Minutes", '8':"8 Minutes", '10':"10 Minutes", '15':"15 Minutes", '20':"20 Minutes"}),
    "piezoBeeps"           : SettingsCommand(   True,  1, "BYTE",    261,   8,   0,    -1,  "Piezo Beeps",                        { '3':"Enable (off when home)", '2':"Enable", '1':"Off when Home", '0':"Disable"} ),
    "swingerStop"          : SettingsCommand(   True,  1, "BYTE",    262,   8,   0,    -1,  "Swinger Stop",                       { '1':"After 1 Time", '2':"After 2 Times", '3':"After 3 Times", '0':"No Shutdown"} ),
    "fobAux"               : SettingsCommand(   True,  2, "BYTE",    263,   8,  14,    -1,  ["Aux Key 1","Aux Key 2"],            { '1':"System Status", '2':"Instant Arm", '3':"Cancel Exit Delay", '4':"PGM/X-10"} ), # 263, 277
    "supervision"          : SettingsCommand(   True,  1, "BYTE",    264,   8,   0,    -1,  "Supervision Interval",               { '1':"1 Hour", '2':"2 Hours", '4':"4 Hours", '8':"8 Hours", '12':"12 Hours", '0':"Disable"} ),
    "noActivity"           : SettingsCommand(   True,  1, "BYTE",    265,   8,   0,    -1,  "No Activity Time",                   { '3':"3 Hours", '6':"6 Hours",'12':"12 Hours", '24':"24 Hours", '48':"48 Hours", '72':"72 Hours", '0':"Disable"} ),
    "cancelTime"           : SettingsCommand(   True,  1, "BYTE",    266,   8,   0,    -1,  "Alarm Cancel Time",                  { '0':"Inactive", '1':"1 Minute", '5':"5 Minutes", '15':"15 Minutes", '60':"60 Minutes", '240':"4 Hours"}),
    "abortTime"            : SettingsCommand(   True,  1, "BYTE",    267,   8,   0,    -1,  "Abort Time",                         { '0':"None", '15':"15 Seconds", '30':"30 Seconds", '45':"45 Seconds", '60':"1 Minute", '120':"2 Minutes", '180':"3 Minutes", '240':"4 Minutes"} ),
    "confirmAlarm"         : SettingsCommand(   True,  1, "BYTE",    268,   8,   0,    -1,  "Confirm Alarm Timer",                { '0':"None", '30':"30 Minutes", '45':"45 Minutes", '60':"60 Minutes", '90':"90 Minutes"} ),
    "screenSaver"          : SettingsCommand(   True,  1, "BYTE",    269,   8,   0,    -1,  "Screen Saver",                       { '2':"Reset By Key", '1':"Reset By Code", '0':"Off"} ),
    "resetOption"          : SettingsCommand(   True,  1, "BYTE",    270,   8,   0,    -1,  "Reset Option",                       { '1':"Engineer Reset", '0':"User Reset"}  ),
    "duress"               : SettingsCommand(   True,  1, "CODE",    273,  16,   0,    -1,  "Duress",                             {  } ),
    "acFailure"            : SettingsCommand(   True,  1, "BYTE",    275,   8,   0,    -1,  "AC Failure Report",                  { '0':"None", '5':"5 Minutes", '30':"30 Minutes", '60':"60 Minutes", '180':"180 Minutes"} ),
    "userPermit"           : SettingsCommand(   True,  1, "BYTE",    276,   8,   0,    -1,  "User Permit",                        { '1':"Enable", '0':"Disable"} ),
    "zoneRestore"          : SettingsCommand(   True,  1, "BYTE",    280,   1,   0,     0,  "Zone Restore",                       { '0':"Report Restore", '1':"Don't Report"} ),
    "tamperOption"         : SettingsCommand(   True,  1, "BYTE",    280,   1,   0,     1,  "Tamper Option",                      { '1':"On", '0':"Off"} ),
    "pgmByLineFail"        : SettingsCommand(   True,  1, "BYTE",    280,   1,   0,     2,  "PGM By Line Fail",                   { '1':"Yes", '0':"No"} ),
    "usrArmOption"         : SettingsCommand(   True,  1, "BYTE",    280,   1,   0,     5,  "Auto Arm Option",                    { '1':"Enable", '0':"Disable"} ),
    "send2wv"              : SettingsCommand(   True,  1, "BYTE",    280,   1,   0,     6,  "Send 2wv Code",                      { '1':"Send", '0':"Don't Send"} ),
    "memoryPrompt"         : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     0,  "Memory Prompt",                      { '1':"Enable", '0':"Disable" } ),
    "usrTimeFormat"        : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     1,  "Time Format",                        { '0':"USA - 12H", '1':"Europe - 24H"}),
    "usrDateFormat"        : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     2,  "Date Format",                        { '0':"USA MM/DD/YYYY", '1':"Europe DD/MM/YYYY"}),
    "lowBattery"           : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     3,  "Low Battery Acknowledge",            { '1':"On", '0':"Off"} ),
    "notReady"             : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     4,  "Not Ready",                          { '0':"Normal", '1':"In Supervision"}  ),
    "x10Flash"             : SettingsCommand(   True,  1, "BYTE",    281,   1,   0,     5,  "X10 Flash On Alarm",                 { '0':"No Flash", '1':"All Lights Flash" } ),
    "disarmOption"         : SettingsCommand(   True,  1, "BYTE",    281,   2,   0,     6,  "Disarm Option",                      { '0':"Any Time", '1':"On Entry All", '2':"On Entry Wireless", '3':"Entry + Away KP"} ),
    "sirenOnLine"          : SettingsCommand(   True,  1, "BYTE",    282,   1,   0,     1,  "Siren On Line",                      { '0':"Disable on Fail", '1':"Enable on Fail" }  ),
    "uploadOption"         : SettingsCommand(   True,  1, "BYTE",    282,   1,   0,     2,  "Upload Option",                      { '0':"When System Off", '1':"Any Time"} ),
    "panicAlarm"           : SettingsCommand(   True,  1, "BYTE",    282,   2,   0,     4,  "Panic Alarm",                        { '1':"Silent Panic", '2':"Audible Panic", '0':"Disable Panic"}  ),
    "exitMode"             : SettingsCommand(   True,  1, "BYTE",    282,   2,   0,     6,  "Exit Mode",                          { '1':"Restart Exit", '2':"Off by Door", '0':"Normal"} ),
    "bellReport"           : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     0,  "Bell Report Option",                 { '1':"EN Standard", '0':"Others"}  ),
    "intStrobe"            : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     1,  "Internal/Strobe Siren",              { '0':"Internal Siren", '1':"Strobe"} ),
    "quickArm"             : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     3,  "Quick Arm",                          { '1':"On", '0':"Off"} ),
    "backLight"            : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     5,  "Back Light Time",                    { '1':"Allways On", '0':"Off After 10 Seconds"} ),
    "voice2Private"        : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     6,  "Two-Way Voice - Private",            { '0':"Disable", '1':"Enable"} ),
    "latchKey"             : SettingsCommand(   True,  1, "BYTE",    283,   1,   0,     7,  "Latchkey Arming",                    { '1':"On", '0':"Off"} ),
    EPROM.PANEL_BYPASS     : SettingsCommand(   True,  1, "BYTE",    284,   2,   0,     6,  "Panel Global Bypass",                { '2':"Manual Bypass", '0':NOBYPASSSTR, '1':"Force Arm"} ),
    "troubleBeeps"         : SettingsCommand(   True,  1, "BYTE",    284,   2,   0,     1,  "Trouble Beeps",                      { '3':"Enable", '1':"Off at Night", '0':"Disable"} ),
    "crossZoning"          : SettingsCommand(   True,  1, "BYTE",    284,   1,   0,     0,  "Cross Zoning",                       { '1':"On", '0':"Off"} ),
    "recentClose"          : SettingsCommand(   True,  1, "BYTE",    284,   1,   0,     3,  "Recent Close Report",                { '1':"On", '0':"Off"} ),
    "piezoSiren"           : SettingsCommand(   True,  1, "BYTE",    284,   1,   0,     5,  "Piezo Siren",                        { '1':"On", '0':"Off"} ),
    "dialMethod"           : SettingsCommand(   True,  1, "BYTE",    285,   1,   0,     0,  "Dialing Method",                     { '0':"Tone (DTMF)", '1':"Pulse"} ),
    "privateAck"           : SettingsCommand(  Dumpy,  1, "BYTE",    285,   1,   0,     1,  "Private Telephone Acknowledge",      { '0':"Single Acknowledge", '1':"All Acknowledge"} ),
    "remoteAccess"         : SettingsCommand(   True,  1, "BYTE",    285,   1,   0,     2,  "Remote Access",                      { '1':"On", '0':"Off"}),
    "reportConfirm"        : SettingsCommand(   True,  1, "BYTE",    285,   2,   0,     6,  "Report Confirmed Alarm",             { '0':"Disable Report", '1':"Enable Report", '2':"Enable + Bypass"} ),
    "centralStation"       : SettingsCommand(   True,  2, "PHONE",   288,  64,  11,    -1,  ["1st Central Tel", "2nd Central Tel"], {} ), # 288, 299
    "accountNo"            : SettingsCommand(   True,  2, "ACCOUNT", 296,  24,  11,    -1,  ["1st Account No","2nd Account No"],  {} ), # 296, 307
    "usePhoneNrs"          : SettingsCommand(  Dumpy,  4, "PHONE",   310,  64,   8,    -1,  ["1st Private Tel","2nd Private Tel","3rd Private Tel","4th Private Tel"],  {} ),  # 310, 318, 326, 334
    "pagerNr"              : SettingsCommand(   True,  1, "PHONE",   342,  64,   0,    -1,  "Pager Tel Number",                   {} ),
    "pagerPIN"             : SettingsCommand(   True,  1, "PHONE",   350,  64,   0,    -1,  "Pager PIN #",                        {} ),
    "ringbackTime"         : SettingsCommand(   True,  1, "BYTE",    358,   8,   0,    -1,  "Ringback Time",                      { '1':"1 Minute", '3':"3 Minutes", '5':"5 Minutes", '10':"10 Minutes"} ),
    "reportCentral"        : SettingsCommand(   True,  1, "BYTE",    359,   8,   0,    -1,  "Report to Central Station",          { '15':"All * Backup", '7':"All but Open/Close * Backup", '255':"All * All", '119':"All but Open/Close * All but Open/Close", '135':"All but Alert * Alert", '45':"Alarms * All but Alarms", '0':"Disable"} ),
    "pagerReport"          : SettingsCommand(   True,  1, "BYTE",    360,   8,   0,    -1,  "Report To Pager",                    { '15':"All", '3':"All + Alerts", '7':"All but Open/Close", '12':"Troubles+Open/Close", '4':"Troubles", '8':"Open/Close", '0':"Disable Report"}  ),
    "privateReport"        : SettingsCommand(   True,  1, "BYTE",    361,   8,   0,    -1,  "Reporting To Private Tel",           { '15':"All", '7':"All but Open/Close", '13':"All but Alerts", '1':"Alarms", '2':"Alerts", '8':"Open/Close", '0':"Disable Report"} ),
    "csDialAttempt"        : SettingsCommand(   True,  1, "BYTE",    362,   8,   0,    -1,  "Central Station Dialing Attempts",   { '2':"2", '4':"4", '8':"8", '12':"12", '16':"16"} ),
    "reportFormat"         : SettingsCommand(   True,  1, "BYTE",    363,   8,   0,    -1,  "Report Format",                      { '0':"Contact ID", '1':"SIA", '2':"4/2 1900/1400", '3':"4/2 1800/2300", '4':"Scancom"}  ),
    "pulseRate"            : SettingsCommand(   True,  1, "BYTE",    364,   8,   0,    -1,  "4/2 Pulse Rate",                     { '0':"10 pps", '1':"20 pps", '2':"33 pps", '3':"40 pps"} ),
    "privateAttempt"       : SettingsCommand(  Dumpy,  1, "BYTE",    365,   8,   0,    -1,  "Private Telephone Dialing Attempts", { '1':"1 Attempt", '2':"2 Attempts", '3':"3 Attempts", '4':"4 Attempts"} ),
    "voice2Central"        : SettingsCommand(   True,  1, "BYTE",    366,   8,   0,    -1,  "Two-Way Voice To Central Stations",  { '10':"Time-out 10 Seconds", '45':"Time-out 45 Seconds", '60':"Time-out 60 Seconds", '90':"Time-out 90 Seconds", '120':"Time-out 2 Minutes", '1':"Ring Back", '0':"Disable"} ),
    "autotestTime"         : SettingsCommand(   True,  1, "TIME",    367,  16,   0,    -1,  "Autotest Time",                      {} ),
    "autotestCycle"        : SettingsCommand(   True,  1, "BYTE",    369,   8,   0,    -1,  "Autotest Cycle",                     { '1':"1 Day", '4':"5 Days", '2':"7 Days", '3':"30 Days", '0':"Disable"}  ),
    "areaCode"             : SettingsCommand(  Dumpy,  1, "CODE",    371,  24,   0,    -1,  "Area Code",                          {} ),
    "outAccessNr"          : SettingsCommand(  Dumpy,  1, "CODE",    374,   8,   0,    -1,  "Out Access Number",                  {} ),
    "lineFailure"          : SettingsCommand(   True,  1, "BYTE",    375,   8,   0,    -1,  "Line Failure Report",                { '0':"Don't Report", '1':"Immediately", '5':"5 Minutes", '30':"30 Minutes", '60':"60 Minutes", '180':"180 Minutes"} ),
    "remoteProgNr"         : SettingsCommand(   True,  1, "PHONE",   376,  64,   0,    -1,  "Remote Programmer Tel. No.",         {} ),
    "inactiveReport"       : SettingsCommand(   True,  1, "BYTE",    384,   8,   0,    -1,  "System Inactive Report",             { '0':"Disable", '180':"7 Days", '14':"14 Days", '30':"30 Days", '90':"90 Days"} ),
    "ambientLevel"         : SettingsCommand(   True,  1, "BYTE",    388,   8,   0,    -1,  "Ambient Level",                      { '0':"High Level", '1':"Low Level"} ),
    "plFailure"            : SettingsCommand(   True,  1, "BYTE",    391,   8,   0,    -1,  "PowerLink Failure",                  { '1':"Report", '0':"Disable Report"} ),
    "gsmPurpose"           : SettingsCommand(   True,  1, "BYTE",    392,   8,   0,    -1,  "GSM Line Purpose",                   { '1':"GSM is Backup", '2':"GSM is Primary", '3':"GSM Only", '0':"SMS Only" } ),
    "gsmSmsReport"         : SettingsCommand(   True,  1, "BYTE",    393,   8,   0,    -1,  "GSM Report to SMS",                  { '15':"All", '7':"All but Open/Close", '13':"All but Alerts", '1':"Alarms", '2':"Alerts", '8':"Open/Close", '0':"Disable Report"} ),
    "gsmFailure"           : SettingsCommand(   True,  1, "BYTE",    394,   8,   0,    -1,  "GSM Line Failure",                   { '0':"Don't Report", '2':"2 Minutes", '5':"5 Minutes", '15':"15 Minutes", '30':"30 Minutes"} ),
    "gsmInstall"           : SettingsCommand(  Dumpy,  1, "BYTE",    395,   8,   0,    -1,  "GSM Install",                        { '1':"Installed", '0':"Not Installed"} ),
    "gsmSmsNrs"            : SettingsCommand(  Dumpy,  4, "PHONE",   396,  64,   8,    -1,  ["1st SMS Tel","2nd SMS Tel","3rd SMS Tel","4th SMS Tel"], {} ),  #  396,404,412,420
    EPROM.DISPLAY_NAME     : SettingsCommand(  Dumpy,  1,"STRING",   428, 128,   0,    -1,  "Displayed String Panel Name",        {} ),   # This is shown on the display as it is centred in the string.  360 shows "SECURITY SYSTEM" for example
    "gsmAntenna"           : SettingsCommand(   True,  1, "BYTE",    447,   8,   0,    -1,  "GSM Select Antenna",                 { '0':"Internal antenna", '1':"External antenna", '2':"Auto detect"} ),
    EPROM.USERCODE_MAX     : SettingsCommand( XDumpy, 16, "BYTE",    506,   8,   1,    -1,  "PowerMax User Codes",                {} ),
    EPROM.USERCODE_MAS     : SettingsCommand( SDumpy, 96, "BYTE",   2712,   8,   1,    -1,  "PowerMaster User Codes",             {} ),
    EPROM.MASTERCODE       : SettingsCommand( SDumpy,  1, "BYTE",    522,  16,   0,    -1,  "Master Code",                        {} ),
    EPROM.INSTALLERCODE    : SettingsCommand(  Dumpy,  1, "BYTE",    524,  16,   0,    -1,  "Installer Code",                     {} ),
    EPROM.MASTERDLCODE     : SettingsCommand(  Dumpy,  1, "BYTE",    526,  16,   0,    -1,  "Master Download Code",               {} ),
    EPROM.INSTALDLCODE     : SettingsCommand( SDumpy,  1, "BYTE",    528,  16,   0,    -1,  "Installer Download Code",            {} ),
    EPROM.X10_LOCKOUT      : SettingsCommand(  Dumpy,  1, "TIME",    532,  16,   0,    -1,  "X10 Lockout Time (start HH:MM)",     {} ),
    EPROM.X10_HOUSECODE    : SettingsCommand(  Dumpy,  1, "BYTE",    536,   8,   0,    -1,  "X10 House Code",                     { '0':"A", '1':"B", '2':"C", '3':"D", '4':"E", '5':"F", '6':"G", '7':"H", '8':"I", '9':"J", '10':"K", '11':"L", '12':"M", '13':"N", '14':"O", '15':"P"}  ),
    EPROM.X10_BYARMAWAY    : SettingsCommand(  Dumpy, 16, "BYTE",    537,   8,   1,    -1,  "X10 By Arm Away",                    { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.X10_BYARMHOME    : SettingsCommand(  Dumpy, 16, "BYTE",    553,   8,   1,    -1,  "X10 By Arm Home",                    { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.X10_BYDISARM     : SettingsCommand(  Dumpy, 16, "BYTE",    569,   8,   1,    -1,  "X10 By Disarm",                      { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.X10_BYDELAY      : SettingsCommand(  Dumpy, 16, "BYTE",    585,   8,   1,    -1,  "X10 By Delay",                       { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.X10_BYMEMORY     : SettingsCommand(  Dumpy, 16, "BYTE",    601,   8,   1,    -1,  "X10 By Memory",                      { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.X10_BYKEYFOB     : SettingsCommand(  Dumpy, 16, "BYTE",    617,   8,   1,    -1,  "X10 By Keyfob",                      { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.X10_ACTZONEA     : SettingsCommand(  Dumpy, 16, "BYTE",    633,   8,   1,    -1,  "X10 Act Zone A",                     { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.X10_ACTZONEB     : SettingsCommand(  Dumpy, 16, "BYTE",    649,   8,   1,    -1,  "X10 Act Zone B",                     { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.X10_ACTZONEC     : SettingsCommand(  Dumpy, 16, "BYTE",    665,   8,   1,    -1,  "X10 Act Zone C",                     { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.X10_PULSETIME    : SettingsCommand(  Dumpy, 16, "BYTE",    681,   8,   1,    -1,  "X10 Pulse Time",                     { '255':DISABLE_TEXT, '0':"Unknown", '2':"2 Seconds", '30':"30 Seconds", '120':"2 Minutes", '240':"4 Minutes"} ),
    EPROM.X10_ZONE         : SettingsCommand(  Dumpy, 16, "BYTE",    697,  24,   3,    -1,  "X10 Zone Data",                      {} ),
    "x10Unknown"           : SettingsCommand(  Dumpy,  2, "BYTE",    745,   8,   1,    -1,  "X10 Unknown",                        {} ),
    "x10Trouble"           : SettingsCommand(  Dumpy,  1, "BYTE",    747,   8,   0,    -1,  "X10 Trouble Indication",             { '1':"Enable", '0':"Disable"} ),
    "x10Phase"             : SettingsCommand(  Dumpy,  1, "BYTE",    748,   8,   0,    -1,  "X10 Phase and frequency",            { '0':"Disable", '1':"50 Hz", '2':"60 Hz"} ),
    "x10ReportCs1"         : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     0,  "X10 Report on Fail to Central 1",    { '1':"Enable", '0':"Disable"} ),
    "x10ReportCs2"         : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     1,  "X10 Report on Fail to Central 2",    { '1':"Enable", '0':"Disable"} ),
    "x10ReportPagr"        : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     2,  "X10 Report on Fail to Pager",        { '1':"Enable", '0':"Disable"} ),
    "x10ReportPriv"        : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     3,  "X10 Report on Fail to Private",      { '1':"Enable", '0':"Disable"} ),
    "x10ReportSMS"         : SettingsCommand(  Dumpy,  1, "BYTE",    749,   1,   0,     4,  "X10 Report on Fail to SMS",          { '1':"Enable", '0':"Disable"} ),
    "usrVoice"             : SettingsCommand(  Dumpy,  1, "BYTE",    763,   8,   0,    -1,  "Set Voice Option",                   { '0':"Disable Voice", '1':"Enable Voice"} ),
    "usrSquawk"            : SettingsCommand(  Dumpy,  1, "BYTE",    764,   8,   0,    -1,  "Squawk Option",                      { '0':"Disable", '1':"Low Level", '2':"Medium Level", '3':"High Level"}),
    "usrArmTime"           : SettingsCommand(  Dumpy,  1, "TIME",    765,  16,   0,    -1,  "Auto Arm Time",                      {} ),

#    EPROM.PART_ZONE_DATA        : SettingsCommand(  Dumpy,255, "BYTE",    768,   8,   1,    -1,  "Partition Data",                     {} ),   # I'm not sure how many bytes this is or what they mean, i get all 255 bytes to the next entry so they can be displayed

    EPROM.PART_ENABLED     : SettingsCommand(  Dumpy,  1, "BYTE",    768,   8,   1,    -1,  "Partition Enabled",                  {} ),   # This byte seems to be non-zero when partitions are enabled
    # Fairly sure that the intermediate 16 bytes are partition data but not sure what for, could be KeyFobs, panic buttons, repeaters etc
    EPROM.PART_ZONE_DATA   : SettingsCommand(  Dumpy, 64, "BYTE",    785,   8,   1,    -1,  "Partition Zone Data",                {} ),   # I'm 99% sure these are the zone partition data in binary, 1 = partition 1, 2 = partition 2 and 4 = partition 3, ORd together so a sensor can be in multiple partitions

    "panelEprom"           : SettingsCommand(   True,  1,"STRING",  1024, 128,   0,    -1,  "Panel Eprom",                        {} ),
    "panelSoftware"        : SettingsCommand(   True,  1,"STRING",  1040, 144,   0,    -1,  "Panel Software",                     {} ),
    EPROM.PANEL_SERIAL     : SettingsCommand(   True,  1, "CODE",   1072,  48,   0,    -1,  "Panel Serial",                       {} ),   # page 4 offset 48
    EPROM.PANEL_MODEL_CODE : SettingsCommand(  Dumpy,  1, "BYTE",   1078,   8,   0,    -1,  "Panel Model Code",                   {} ),   # page 4 offset 54 and 55 ->> Panel model code
    EPROM.PANEL_TYPE_CODE  : SettingsCommand(  Dumpy,  1, "BYTE",   1079,   8,   0,    -1,  "Panel Type Code",                    {} ),   # page 4 offset 55
    EPROM.X10_ZONENAMES    : SettingsCommand(  Dumpy, 16, "BYTE",   2863,   8,   1,    -1,  "X10 Location Name references",       {} ),   # 
#    EPROM.ZONE_STRING      : SettingsCommand(  Dumpy, 32,"STRING",  6400, 128,  16,    -1,  "Zone String Names",                  {} ),   # Zone String Names e.g "Attic", "Back door", "Basement", "Bathroom" etc 32 strings of 16 characters each
    EPROM.ZONE_STR_NAM     : SettingsCommand(  Dumpy, 21,"STRING",  6400, 128,  16,    -1,  "Zone String Names Standard",         {} ),   # Zone String Names e.g "Attic", "Back door", "Basement", "Bathroom" etc 21 strings of 16 characters each
    EPROM.ZONE_STR_EXT     : SettingsCommand(  Dumpy, 10,"STRING",  6736, 128,  16,    -1,  "Zone String Names Custom",           {} ),   # Zone String Names Custom, 10 strings of 16 characters each

    #"MaybeScreenSaver":SettingsCommand(  Dumpy, 75, "BYTE",   5888,   8,   1,    -1,  "Maybe the screen saver",             {} ),   # Structure not known 
    #"MaybeEventLog"        : SettingsCommand(  Dumpy,256, "BYTE",   1247,   8,   1,    -1,  "Maybe the event log",                {} ),   # Structure not known   was length 808 but cut to 256 to see what data we get

#PowerMax Only
    EPROM.ZONEDATA_MAX     : SettingsCommand( XDumpy, 30, "BYTE",   2304,  32,   4,    -1,  "Zone Data, PowerMax",                {} ),   # 4 bytes each, 30 zones --> 120 bytes
    "KeyFobsPMax"          : SettingsCommand( XDumpy, 16, "BYTE",   2424,  32,   4,    -1,  "Maybe KeyFob Data PowerMax",         {} ),   # Structure not known

    "ZoneSignalPMax"       : SettingsCommand( XDumpy, 28, "BYTE",   2522,   8,   1,    -1,  "Zone Signal Strength, PowerMax",     {} ),   # 28 wireless zones
    EPROM.KEYPAD_2_MAX     : SettingsCommand( XDumpy,  2, "BYTE",   2560,  32,   4,    -1,  "Keypad2 Data, PowerMax",             {} ),   # 4 bytes each, 2 keypads 
    EPROM.KEYPAD_1_MAX     : SettingsCommand( XDumpy,  8, "BYTE",   2592,  32,   4,    -1,  "Keypad1 Data, PowerMax",             {} ),   # 4 bytes each, 8 keypads        THIS TOTALS 32 BYTES BUT IN OTHER SYSTEMS IVE SEEN 64 BYTES
    EPROM.SIRENS_MAX       : SettingsCommand( XDumpy,  2, "BYTE",   2656,  32,   4,    -1,  "Siren Data, PowerMax",               {} ),   # 4 bytes each, 2 sirens 
    EPROM.ZONENAME_MAX     : SettingsCommand( XDumpy, 30, "BYTE",   2880,   8,   1,    -1,  "Zone Names, PowerMax",               {} ),

    #"ZoneStrType1X"    : SettingsCommand( XDumpy, 16,"STRING", 22568, 120,  16,    -1,  "PowerMax Zone Type String",          {} ),   # Zone String Types e.g 
#    "ZoneStrType1X"    : SettingsCommand( XDumpy, 16,"STRING", 22571,  96,  16,    -1,  "PowerMax Zone Type String",          {} ),   # Zone String Types e.g This starts 3 bytes later as it misses the "1. " and the strings are only 12 characters
#    "ZoneStrType2X"    : SettingsCommand( XDumpy, 16,  "BYTE", 22583,   8,  16,    -1,  "PowerMax Zone Type Reference",       {} ),   # Zone String Types e.g 

#    "ZoneChimeType1X"  : SettingsCommand( XDumpy,  3,"STRING",0x64D8, 120,  16,    -1,  "PowerMax Zone Chime Type String",    {} ),   # Zone String Types e.g 
#    "ZoneChimeType2X"  : SettingsCommand( XDumpy,  3,  "BYTE",0x64E7,   8,  16,    -1,  "PowerMax Zone Chime Type Ref",       {} ),   # Zone String Types e.g 

    #"Test2"            : SettingsCommand(  Dumpy,128, "BYTE",   2816,   8,   1,    -1,  "Test 2 String, PowerMax",            {} ),   # 0xB00
    #"Test1"            : SettingsCommand(  Dumpy,128, "BYTE",   2944,   8,   1,    -1,  "Test 1 String, PowerMax",            {} ),   # 0xB80

#PowerMaster only
    EPROM.ZONEDATA_MAS     : SettingsCommand( SDumpy, 64, "BYTE",   2304,   8,   1,    -1,  "Zone Data, PowerMaster",             {} ),   # 1 bytes each, 64 zones --> 64 bytes
    EPROM.ZONENAME_MAS     : SettingsCommand( SDumpy, 64, "BYTE",   2400,   8,   1,    -1,  "Zone Names, PowerMaster",            {} ),   # 
    EPROM.SIRENS_MAS       : SettingsCommand( SDumpy,  8, "BYTE",  46818,  80,  10,    -1,  "Siren Data, PowerMaster",            {} ),   # 10 bytes each, 8 sirens
    EPROM.KEYPAD_MAS       : SettingsCommand( SDumpy, 32, "BYTE",  46898,  80,  10,    -1,  "Keypad Data, PowerMaster",           {} ),   # 10 bytes each, 32 keypads 
    EPROM.ZONEEXT_MAS      : SettingsCommand( SDumpy, 64, "BYTE",  47218,  80,  10,    -1,  "Zone Extended Data, PowerMaster",    {} ),   # 10 bytes each, 64 zones 

    #"ZoneStrType1S"    : SettingsCommand( SDumpy, 16,"STRING", 33024, 120,  16,    -1,  "PowerMaster Zone Type String",       {} ),   # Zone String Types e.g 
#    "ZoneStrType1S"    : SettingsCommand( SDumpy, 16,"STRING", 33027,  96,  16,    -1,  "PowerMaster Zone Type String",       {} ),   # Zone String Types e.g  This starts 3 bytes later as it misses the "1. " and the strings are only 12 characters
#    "ZoneStrType2S"    : SettingsCommand( SDumpy, 16,  "BYTE", 33039,   8,  16,    -1,  "PowerMaster Zone Type Reference",    {} ),   # Zone String Types e.g 

#    "ZoneChimeType1S"  : SettingsCommand( SDumpy,  3,"STRING",0x8EB0, 120,  16,    -1,  "PowerMaster Zone Chime Type String", {} ),   # Zone String Types e.g 
#    "ZoneChimeType2S"  : SettingsCommand( SDumpy,  3,  "BYTE",0x8EBF,   8,  16,    -1,  "PowerMaster Zone Chime Type Ref",    {} ),   # Zone String Types e.g 

#    "LogEventStr"      : SettingsCommand( SDumpy,160,"STRING",0xED00, 128,  16,    -1,  "Log Event Strings",                  {} ),   # Zone String Types e.g 

    "AlarmLED"             : SettingsCommand( SDumpy, 64, "BYTE",  49250,   8,   1,    -1,  "Alarm LED, PowerMaster",             {} ),   # This is the Alarm LED On/OFF settings for Motion Sensors -> Dev Settings --> Alarm LED
    EPROM.ZONE_DEL_MAS     : SettingsCommand( SDumpy, 64, "BYTE",  49542,  16,   2,    -1,  "Zone Delay, PowerMaster",            {} )    # This is the Zone Delay settings for Motion Sensors -> Dev Settings --> Disarm Activity  
}
# 'show count type poff psize pstep pbitoff name values'


##############################################################################################################################################################################################################################################
##########################  EPROM Blocks to download #########################################################################################################################################################################################
##############################################################################################################################################################################################################################################

# These blocks are not value specific, they are used to download blocks of EPROM data that we need without reference to what the data means
#    They are used when EPROM_DOWNLOAD_ALL is False
#    We have to do it like this as the max message size is 176 (0xB0) bytes.

pmBlockDownload_Short = {
    PanelTypeEnum.POWER_MAX : ( 
              ( 0x0100, 0x0500 ),
              ( 0x0900, 0x0C00 ),
              ( 0x1900, 0x1B00 ),      # ZoneStringNames 0x1900 = 6400 Decimal
#              ( 0x5800, 0x5A00 ),      # pmZoneType_t starts at 0x5828
#              ( 0x64D8, 0x6510 )       # pmZoneChime starts at 0x64D8
    ),
    PanelTypeEnum.POWER_MASTER : (
              ( 0x0100, 0x0500 ),
              ( 0x0900, 0x0C00 ),
              ( 0x1900, 0x1B00 ),      # ZoneStringNames 0x1900 = 6400 Decimal
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


class EPROMManager:

    def __init__(self):
        # Save the EPROM data when downloaded
        self.reset()

    def reset(self):  
        self.pmRawSettings = {}
        self.pmDownloadComplete = False

    def findLength(self, page, index) -> int | None:
        for b in pmBlockDownload[PanelTypeEnum.POWER_MAX]:
            if b[0] == index and b[1] == page:
                return b[2]
        for b in pmBlockDownload[PanelTypeEnum.POWER_MASTER]:
            if b[0] == index and b[1] == page:
                return b[2]
        return None

    def _validatEPROMSettingsBlock(self, block) -> bool:
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
        log.debug(f"[_validatEPROMSettingsBlock]    page {block[1]:>3}   index {block[0]:>3}   length {block[2]:>3}     {'Already Got It' if settings_len == len(retval) else 'Not Got It'}")
        return settings_len == len(retval)

    def populatEPROMDownload(self, isPowerMaster):
        """ Populate the EPROM Download List """

        # Empty list and start at the beginning
        myDownloadList = []
        self.pmDownloadComplete = False

        if EPROM_DOWNLOAD_ALL:
            for page in range(0, 256):
                mystr = '00 ' + format(page, '02x').upper() + ' 80 00'
                if not self._validatEPROMSettingsBlock(convertByteArray(mystr)):
                    myDownloadList.append(convertByteArray(mystr))
                mystr = '80 ' + format(page, '02x').upper() + ' 80 00'
                if not self._validatEPROMSettingsBlock(convertByteArray(mystr)):
                    myDownloadList.append(convertByteArray(mystr))
        else:
            lenMax = len(pmBlockDownload[PanelTypeEnum.POWER_MAX])
            lenMaster = len(pmBlockDownload[PanelTypeEnum.POWER_MASTER])

            # log.debug(f"lenMax = {lenMax}   lenMaster = {lenMaster}")

            for block in pmBlockDownload[PanelTypeEnum.POWER_MAX]:
                if not self._validatEPROMSettingsBlock(block):
                    myDownloadList.append(block)

            if isPowerMaster:
                for block in pmBlockDownload[PanelTypeEnum.POWER_MASTER]:
                    if not self._validatEPROMSettingsBlock(block):
                        myDownloadList.append(block)
        self.pmDownloadComplete = len(myDownloadList) == 0
        return myDownloadList

    # _saveEPROMSettings: add a certain setting to the settings table
    #      When we send a MSG_DL and insert the 4 bytes from pmDownloadItem_t, what we're doing is setting the page, index and len
    # This function stores the downloaded status and EPROM data
    def saveEPROMSettings(self, page, index, setting):
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
                    log.debug(f"[Write Settings] the EPROM settings is incorrect for page {page + i}")
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
    # This function retrieves the downloaded status and EPROM data
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
        log.debug(f"[_readEPROMSettingsPageIndex]     Sorry but you havent downloaded that part of the EPROM data     page={hex(page)} index={hex(index)} length={settings_len}")

        # return a bytearray filled with 0xFF values
        retval = bytearray()
        for dummy in range(0, settings_len):
            retval.append(255)
        return retval

    # this can be called from an entry in pmDownloadItem_t such as
    #      page index lenhigh lenlow
    def readEPROMSettings(self, item):
        return self._readEPROMSettingsPageIndex(item[0], item[1], item[3] + (0x100 * item[2]))

    # This function was going to save the settings (including EPROM) to a file
    def _dumpEPROMSettings(self):
        log.debug("Dumping EPROM Settings")
        for p in range(0, 0x100):  ## assume page can go from 0 to 255
            if p in self.pmRawSettings:
                for j in range(0, 0x100, 0x10):  ## assume that each page can be 256 bytes long, step by 16 bytes
                    # do not display the rows with pin numbers
                    # if not (( p == 1 and j == 240 ) or (p == 2 and j == 0) or (p == 10 and j >= 140)):
                    if EPROM_DOWNLOAD_ALL or ((p != 1 or j != 240) and (p != 2 or j != 0) and (p != 10 or j <= 140)):
                        if j <= len(self.pmRawSettings[p]):
                            s = toString(self.pmRawSettings[p][j : j + 0x10])
                            log.debug(f"{p:3}:{j:3}  {s}")

    def _calcBoolFromIntMask(self, val, mask) -> bool:
        return True if val & mask != 0 else False

    # SettingsCommand = collections.namedtuple('SettingsCommand', 'show count type poff psize pstep pbitoff name values')
    def lookupEprom(self, ref : EPROM | SettingsCommand | str , expected_size : int = -1 ):
        
        val : SettingsCommand = None 
        
        if isinstance(ref, SettingsCommand):
            val = ref
        elif isinstance(ref, EPROM) and ref in pmDecodePanelSettings:
            val = pmDecodePanelSettings[ref]
        elif isinstance(ref, str) and ref in pmDecodePanelSettings:
            val = pmDecodePanelSettings[ref]

        retval = []

        if val is None:
            log.warning("EPROM Lookup Error: cannot find EPROM setting in the download")
            retval.append("Not Found")
            retval.append("Not Found As Well")
            return retval
        
        if expected_size >= 0 and val.count != expected_size:
            log.warning(f"EPROM Lookup Error: expected size is not found, should be {expected_size}  but it is {val.count}")
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
                #log.debug(f"[lookupEprom] A {val}")
                v = self._readEPROMSettingsPageIndex(page, pos, size)
                #log.debug(f"[lookupEprom] B {v}")
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
                    #log.debug(f"[lookupEprom] {page} {pos+j}  character {nr}   {chr(nr[0])}")
                    if nr[0] != 0xFF:
                        myvalue = myvalue + chr(nr[0])
                #log.debug(f"[lookupEprom] myvalue  <{myvalue}>")
                myvalue = myvalue.strip()
                #log.debug(f"[lookupEprom] myvalue stripped <{myvalue}>")
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

    def lookupEpromSingle(self, key : EPROM | SettingsCommand ):
        v = self.lookupEprom(key)
        if len(v) >= 1:
            return v[0]
        return None

    def processEPROMData(self) -> dict:
        # If val.show is True but addToLog is False then:
        #      Add the "True" values to the self.Panelstatus
        # If val.show is True and addToLog is True then:
        #      Add all (either PowerMax / PowerMaster) values to the self.Panelstatus and the log file
        PanelStatus : dict = {}
        addToLog = False
        for key in pmDecodePanelSettings:
            val = pmDecodePanelSettings[key]
            if val.show:
                result = self.lookupEprom(val)
                if result is not None:
                    if type(val.name) is str and len(result) == 1:
                        if isinstance(result[0], (bytes, bytearray)):
                            tmpdata = toString(result[0])
                            if addToLog:
                                log.debug(f"[processEPROMData]      {key:<18}  {val.name:<40}  {tmpdata}")
                            PanelStatus[val.name] = tmpdata
                        else:
                            if addToLog:
                                log.debug(f"[processEPROMData]      {key:<18}  {val.name:<40}  {result[0]}")
                            PanelStatus[val.name] = result[0]

                    elif type(val.name) is list and len(result) == len(val.name):
                        for i in range(0, len(result)):
                            if isinstance(result[0], (bytes, bytearray)):
                                tmpdata = toString(result[i])
                                if addToLog:
                                    log.debug(f"[processEPROMData]      {key:<18}  {val.name[i]:<40}  {tmpdata}")
                                PanelStatus[val.name[i]] = tmpdata
                            else:
                                if addToLog:
                                    log.debug(f"[processEPROMData]      {key:<18}  {val.name[i]:<40}  {result[i]}")
                                PanelStatus[val.name[i]] = result[i]

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
                            log.debug(f"[processEPROMData]      {key:<18}  {val.name:<40}  {tmpdata}")
                        PanelStatus[val.name] = tmpdata

                    else:
                        log.debug(f"[processEPROMData]   ************************** NOTHING DONE ************************     {key:<18}  {val.name}  {result}")
        return PanelStatus
