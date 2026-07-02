"""EPROM Decode for PowerMax and PowerMaster Security Systems."""

# Make sure Ruff ignores f-strings
# ruff: noqa: G004

from dataclasses import dataclass
from enum import Enum, auto
import logging
import math
from math import ceil
from typing import Any, NamedTuple

from .py_const import DISABLE_TEXT, EPROM_DOWNLOAD_ALL, NOBYPASSSTR
from .py_enum import EPROM, PanelTypeEnum
from .py_utils import b2i, convert_bytearray, toString

log = logging.getLogger(__name__)

###################################################################################
##########################  EPROM Decode  #########################################
###################################################################################

# Set 1 of the following but not both, depending on the panel type
Dumpy = False

class PDT(Enum):
    """Panel Data Type."""
    BYTE = auto()
    INT = auto()
    PHONE = auto()
    TIME = auto()
    CODE = auto()
    ACCOUNT = auto()
    STRING = auto()
    DATE = auto()

class PSC(Enum):
    """Panel settings command. Determines which Eprom settings are valid for the panel type."""
    BOTH = auto()   # All panel types
    MAS = auto()    # PowerMaster only
    MAX = auto()    # PowerMax only

class SettingsCommand(NamedTuple):
    """Configuration for PMAX EPROM settings."""
    panel: PSC
    show: bool
    count: int
    type: PDT
    poff: int
    psize: int
    pstep: int
    pbitoff: int
    name: str | list[str]
    values: dict[str, str]

# fmt: off

pmDecodePanelSettings: dict[str | EPROM, SettingsCommand] = \
{                                           #  PSC      show count  type          poff psize pstep pbitoff name                                   values
    "jamDetect"            : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   256,   8,   0,    -1,  "Jamming Detection",                  { '1':"UL 20/20", '2':"EN 30/60", '3':"Class 6", '4':"Other", '0':"Disable"} ),
    "entryDelays"          : SettingsCommand( PSC.BOTH,  True,  2, PDT.BYTE   ,   257,   8,   1,     2,  ["Entry Delay 1","Entry Delay 2"],    { '0':"None", '15':"15 Seconds", '30':"30 Seconds", '45':"45 Seconds", '60':"1 Minute", '180':"3 Minutes", '240':"4 Minutes"}),  # 257, 258
    "exitDelay"            : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   259,   8,   0,    -1,  "Exit Delay",                         { '30':"30 Seconds", '60':"60 Seconds", '90':"90 Seconds", '120':"2 Minutes", '180':"3 Minutes", '240':"4 Minutes"}),
    "bellTime"             : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   260,   8,   0,    -1,  "Bell Time",                          {} ), #{ '1':"1 Minute", '3':"3 Minutes", '4':"4 Minutes", '8':"8 Minutes", '10':"10 Minutes", '15':"15 Minutes", '20':"20 Minutes"}),
    "piezoBeeps"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   261,   8,   0,    -1,  "Piezo Beeps",                        { '3':"Enable (off when home)", '2':"Enable", '1':"Off when Home", '0':"Disable"} ),
    "swingerStop"          : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   262,   8,   0,    -1,  "Swinger Stop",                       { '1':"After 1 Time", '2':"After 2 Times", '3':"After 3 Times", '0':"No Shutdown"} ),
    "fobAux"               : SettingsCommand( PSC.BOTH,  True,  2, PDT.BYTE   ,   263,   8,  14,    -1,  ["Aux Key 1","Aux Key 2"],            { '1':"System Status", '2':"Instant Arm", '3':"Cancel Exit Delay", '4':"PGM/X-10"} ), # 263, 277
    "supervision"          : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   264,   8,   0,    -1,  "Supervision Interval",               { '1':"1 Hour", '2':"2 Hours", '4':"4 Hours", '8':"8 Hours", '12':"12 Hours", '0':"Disable"} ),
    "noActivity"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   265,   8,   0,    -1,  "No Activity Time",                   { '3':"3 Hours", '6':"6 Hours",'12':"12 Hours", '24':"24 Hours", '48':"48 Hours", '72':"72 Hours", '0':"Disable"} ),
    "cancelTime"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   266,   8,   0,    -1,  "Alarm Cancel Time",                  { '0':"Inactive", '1':"1 Minute", '5':"5 Minutes", '15':"15 Minutes", '60':"60 Minutes", '240':"4 Hours"}),
    "abortTime"            : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   267,   8,   0,    -1,  "Abort Time",                         { '0':"None", '15':"15 Seconds", '30':"30 Seconds", '45':"45 Seconds", '60':"1 Minute", '120':"2 Minutes", '180':"3 Minutes", '240':"4 Minutes"} ),
    "confirmAlarm"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   268,   8,   0,    -1,  "Confirm Alarm Timer",                { '0':"None", '30':"30 Minutes", '45':"45 Minutes", '60':"60 Minutes", '90':"90 Minutes"} ),
    "screenSaver"          : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   269,   8,   0,    -1,  "Screen Saver",                       { '2':"Reset By Key", '1':"Reset By Code", '0':"Off"} ),
    "resetOption"          : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   270,   8,   0,    -1,  "Reset Option",                       { '1':"Engineer Reset", '0':"User Reset"}  ),
    "duress"               : SettingsCommand( PSC.BOTH,  True,  1, PDT.CODE   ,   273,  16,   0,    -1,  "Duress",                             {  } ),
    "acFailure"            : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   275,   8,   0,    -1,  "AC Failure Report",                  { '0':"None", '5':"5 Minutes", '30':"30 Minutes", '60':"60 Minutes", '180':"180 Minutes"} ),
    "userPermit"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   276,   8,   0,    -1,  "User Permit",                        { '1':"Enable", '0':"Disable"} ),
    "zoneRestore"          : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   280,   1,   0,     0,  "Zone Restore",                       { '0':"Report Restore", '1':"Don't Report"} ),
    "tamperOption"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   280,   1,   0,     1,  "Tamper Option",                      { '1':"On", '0':"Off"} ),
    "pgmByLineFail"        : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   280,   1,   0,     2,  "PGM By Line Fail",                   { '1':"Yes", '0':"No"} ),
    "usrArmOption"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   280,   1,   0,     5,  "Auto Arm Option",                    { '1':"Enable", '0':"Disable"} ),
    "send2wv"              : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   280,   1,   0,     6,  "Send 2wv Code",                      { '1':"Send", '0':"Don't Send"} ),
    "memoryPrompt"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   281,   1,   0,     0,  "Memory Prompt",                      { '1':"Enable", '0':"Disable" } ),
    "usrTimeFormat"        : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   281,   1,   0,     1,  "Time Format",                        { '0':"USA - 12H", '1':"Europe - 24H"}),
    "usrDateFormat"        : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   281,   1,   0,     2,  "Date Format",                        { '0':"USA MM/DD/YYYY", '1':"Europe DD/MM/YYYY"}),
    "lowBattery"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   281,   1,   0,     3,  "Low Battery Acknowledge",            { '1':"On", '0':"Off"} ),
    "notReady"             : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   281,   1,   0,     4,  "Not Ready",                          { '0':"Normal", '1':"In Supervision"}  ),
    "switch_Flash"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   281,   1,   0,     5,  "Switch Flash On Alarm",              { '0':"No Flash", '1':"All Lights Flash" } ),
    "disarmOption"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   281,   2,   0,     6,  "Disarm Option",                      { '0':"Any Time", '1':"On Entry All", '2':"On Entry Wireless", '3':"Entry + Away KP"} ),
    "sirenOnLine"          : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   282,   1,   0,     1,  "Siren On Line",                      { '0':"Disable on Fail", '1':"Enable on Fail" }  ),
    "uploadOption"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   282,   1,   0,     2,  "Upload Option",                      { '0':"When System Off", '1':"Any Time"} ),
    "panicAlarm"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   282,   2,   0,     4,  "Panic Alarm",                        { '1':"Silent Panic", '2':"Audible Panic", '0':"Disable Panic"}  ),
    "exitMode"             : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   282,   2,   0,     6,  "Exit Mode",                          { '1':"Restart Exit", '2':"Off by Door", '0':"Normal"} ),
    "bellReport"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   283,   1,   0,     0,  "Bell Report Option",                 { '1':"EN Standard", '0':"Others"}  ),
    "intStrobe"            : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   283,   1,   0,     1,  "Internal/Strobe Siren",              { '0':"Internal Siren", '1':"Strobe"} ),
    "quickArm"             : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   283,   1,   0,     3,  "Quick Arm",                          { '1':"On", '0':"Off"} ),
    "backLight"            : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   283,   1,   0,     5,  "Back Light Time",                    { '1':"Allways On", '0':"Off After 10 Seconds"} ),
    "voice2Private"        : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   283,   1,   0,     6,  "Two-Way Voice - Private",            { '0':"Disable", '1':"Enable"} ),
    "latchKey"             : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   283,   1,   0,     7,  "Latchkey Arming",                    { '1':"On", '0':"Off"} ),
    EPROM.PANEL_BYPASS     : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   284,   2,   0,     6,  "Panel Global Bypass",                { '2':"Manual Bypass", '0':NOBYPASSSTR, '1':"Force Arm"} ),
    "troubleBeeps"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   284,   2,   0,     1,  "Trouble Beeps",                      { '3':"Enable", '1':"Off at Night", '0':"Disable"} ),
    "crossZoning"          : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   284,   1,   0,     0,  "Cross Zoning",                       { '1':"On", '0':"Off"} ),
    "recentClose"          : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   284,   1,   0,     3,  "Recent Close Report",                { '1':"On", '0':"Off"} ),
    "piezoSiren"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   284,   1,   0,     5,  "Piezo Siren",                        { '1':"On", '0':"Off"} ),
    "dialMethod"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   285,   1,   0,     0,  "Dialing Method",                     { '0':"Tone (DTMF)", '1':"Pulse"} ),
    "privateAck"           : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   285,   1,   0,     1,  "Private Telephone Acknowledge",      { '0':"Single Acknowledge", '1':"All Acknowledge"} ),
    "remoteAccess"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   285,   1,   0,     2,  "Remote Access",                      { '1':"On", '0':"Off"}),
    "reportConfirm"        : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   285,   2,   0,     6,  "Report Confirmed Alarm",             { '0':"Disable Report", '1':"Enable Report", '2':"Enable + Bypass"} ),
    "centralStation"       : SettingsCommand( PSC.BOTH,  True,  2, PDT.PHONE  ,   288,  64,  11,    -1,  ["1st Central Tel", "2nd Central Tel"], {} ), # 288, 299
    "accountNo"            : SettingsCommand( PSC.BOTH,  True,  2, PDT.ACCOUNT,   296,  24,  11,    -1,  ["1st Account No","2nd Account No"],  {} ), # 296, 307
    "usePhoneNrs"          : SettingsCommand( PSC.BOTH, Dumpy,  4, PDT.PHONE  ,   310,  64,   8,    -1,  ["1st Private Tel","2nd Private Tel","3rd Private Tel","4th Private Tel"],  {} ),  # 310, 318, 326, 334
    "pagerNr"              : SettingsCommand( PSC.BOTH,  True,  1, PDT.PHONE  ,   342,  64,   0,    -1,  "Pager Tel Number",                   {} ),
    "pagerPIN"             : SettingsCommand( PSC.BOTH,  True,  1, PDT.PHONE  ,   350,  64,   0,    -1,  "Pager PIN #",                        {} ),
    "ringbackTime"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   358,   8,   0,    -1,  "Ringback Time",                      { '1':"1 Minute", '3':"3 Minutes", '5':"5 Minutes", '10':"10 Minutes"} ),
    "reportCentral"        : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   359,   8,   0,    -1,  "Report to Central Station",          { '15':"All * Backup", '7':"All but Open/Close * Backup", '255':"All * All", '119':"All but Open/Close * All but Open/Close", '135':"All but Alert * Alert", '45':"Alarms * All but Alarms", '0':"Disable"} ),
    "pagerReport"          : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   360,   8,   0,    -1,  "Report To Pager",                    { '15':"All", '3':"All + Alerts", '7':"All but Open/Close", '12':"Troubles+Open/Close", '4':"Troubles", '8':"Open/Close", '0':"Disable Report"}  ),
    "privateReport"        : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   361,   8,   0,    -1,  "Reporting To Private Tel",           { '15':"All", '7':"All but Open/Close", '13':"All but Alerts", '1':"Alarms", '2':"Alerts", '8':"Open/Close", '0':"Disable Report"} ),
    "csDialAttempt"        : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   362,   8,   0,    -1,  "Central Station Dialing Attempts",   { '2':"2", '4':"4", '8':"8", '12':"12", '16':"16"} ),
    "reportFormat"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   363,   8,   0,    -1,  "Report Format",                      { '0':"Contact ID", '1':"SIA", '2':"4/2 1900/1400", '3':"4/2 1800/2300", '4':"Scancom"}  ),
    "pulseRate"            : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   364,   8,   0,    -1,  "4/2 Pulse Rate",                     { '0':"10 pps", '1':"20 pps", '2':"33 pps", '3':"40 pps"} ),
    "privateAttempt"       : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   365,   8,   0,    -1,  "Private Telephone Dialing Attempts", { '1':"1 Attempt", '2':"2 Attempts", '3':"3 Attempts", '4':"4 Attempts"} ),
    "voice2Central"        : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   366,   8,   0,    -1,  "Two-Way Voice To Central Stations",  { '10':"Time-out 10 Seconds", '45':"Time-out 45 Seconds", '60':"Time-out 60 Seconds", '90':"Time-out 90 Seconds", '120':"Time-out 2 Minutes", '1':"Ring Back", '0':"Disable"} ),
    "autotestTime"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.TIME   ,   367,  16,   0,    -1,  "Autotest Time",                      {} ),
    "autotestCycle"        : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   369,   8,   0,    -1,  "Autotest Cycle",                     { '1':"1 Day", '4':"5 Days", '2':"7 Days", '3':"30 Days", '0':"Disable"}  ),
    "areaCode"             : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.CODE   ,   371,  24,   0,    -1,  "Area Code",                          {} ),
    "outAccessNr"          : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.CODE   ,   374,   8,   0,    -1,  "Out Access Number",                  {} ),
    "lineFailure"          : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   375,   8,   0,    -1,  "Line Failure Report",                { '0':"Don't Report", '1':"Immediately", '5':"5 Minutes", '30':"30 Minutes", '60':"60 Minutes", '180':"180 Minutes"} ),
    "remoteProgNr"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.PHONE  ,   376,  64,   0,    -1,  "Remote Programmer Tel. No.",         {} ),
    "inactiveReport"       : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   384,   8,   0,    -1,  "System Inactive Report",             { '0':"Disable", '180':"7 Days", '14':"14 Days", '30':"30 Days", '90':"90 Days"} ),
    "ambientLevel"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   388,   8,   0,    -1,  "Ambient Level",                      { '0':"High Level", '1':"Low Level"} ),
    "plFailure"            : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   391,   8,   0,    -1,  "PowerLink Failure",                  { '1':"Report", '0':"Disable Report"} ),
    "gsmPurpose"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   392,   8,   0,    -1,  "GSM Line Purpose",                   { '1':"GSM is Backup", '2':"GSM is Primary", '3':"GSM Only", '0':"SMS Only" } ),
    "gsmSmsReport"         : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   393,   8,   0,    -1,  "GSM Report to SMS",                  { '15':"All", '7':"All but Open/Close", '13':"All but Alerts", '1':"Alarms", '2':"Alerts", '8':"Open/Close", '0':"Disable Report"} ),
    "gsmFailure"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   394,   8,   0,    -1,  "GSM Line Failure",                   { '0':"Don't Report", '2':"2 Minutes", '5':"5 Minutes", '15':"15 Minutes", '30':"30 Minutes"} ),
    "gsmInstall"           : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   395,   8,   0,    -1,  "GSM Install",                        { '1':"Installed", '0':"Not Installed"} ),
    "gsmSmsNrs"            : SettingsCommand( PSC.BOTH, Dumpy,  4, PDT.PHONE  ,   396,  64,   8,    -1,  ["1st SMS Tel","2nd SMS Tel","3rd SMS Tel","4th SMS Tel"], {} ),  #  396,404,412,420
    EPROM.DISPLAY_NAME     : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.STRING ,   428, 128,   0,    -1,  "Displayed String Panel Name",        {} ),   # This is shown on the display as it is centred in the string.  360 shows "SECURITY SYSTEM" for example
    "gsmAntenna"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.BYTE   ,   447,   8,   0,    -1,  "GSM Select Antenna",                 { '0':"Internal antenna", '1':"External antenna", '2':"Auto detect"} ),
    EPROM.USERCODE_MAX     : SettingsCommand( PSC.MAX , Dumpy, 16, PDT.BYTE   ,   506,   8,   1,    -1,  "PowerMax User Codes",                {} ),
    EPROM.MASTERCODE       : SettingsCommand( PSC.MAS , Dumpy,  1, PDT.BYTE   ,   522,  16,   0,    -1,  "Master Code",                        {} ),
    EPROM.INSTALLERCODE    : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   524,  16,   0,    -1,  "Installer Code",                     {} ),
    EPROM.MASTERDLCODE     : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   526,  16,   0,    -1,  "Master Download Code",               {} ),
    EPROM.INSTALDLCODE     : SettingsCommand( PSC.MAS , Dumpy,  1, PDT.BYTE   ,   528,  16,   0,    -1,  "Installer Download Code",            {} ),
    EPROM.SWITCH_LOCKOUT   : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.TIME   ,   532,  16,   0,    -1,  "Switch Lockout Time (start HH:MM)",  {} ),
    EPROM.SWITCH_HOUSECODE : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   536,   8,   0,    -1,  "Switch House Code",                  { '0':"A", '1':"B", '2':"C", '3':"D", '4':"E", '5':"F", '6':"G", '7':"H", '8':"I", '9':"J", '10':"K", '11':"L", '12':"M", '13':"N", '14':"O", '15':"P"}  ),
    EPROM.SWITCH_BYARMAWAY : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,   537,   8,   1,    -1,  "Switch By Arm Away",                 { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.SWITCH_BYARMHOME : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,   553,   8,   1,    -1,  "Switch By Arm Home",                 { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.SWITCH_BYDISARM  : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,   569,   8,   1,    -1,  "Switch By Disarm",                   { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.SWITCH_BYDELAY   : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,   585,   8,   1,    -1,  "Switch By Delay",                    { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.SWITCH_BYMEMORY  : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,   601,   8,   1,    -1,  "Switch By Memory",                   { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.SWITCH_BYKEYFOB  : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,   617,   8,   1,    -1,  "Switch By Keyfob",                   { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.SWITCH_ACTZONEA  : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,   633,   8,   1,    -1,  "Switch Act Zone A",                  { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.SWITCH_ACTZONEB  : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,   649,   8,   1,    -1,  "Switch Act Zone B",                  { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.SWITCH_ACTZONEC  : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,   665,   8,   1,    -1,  "Switch Act Zone C",                  { '255':DISABLE_TEXT, '0':DISABLE_TEXT, '1':"Turn Off", '2':"Turn On", '3':"Pulse Active", '4':"Toggle"} ),
    EPROM.SWITCH_PULSETIME : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,   681,   8,   1,    -1,  "Switch Pulse Time",                  { '255':DISABLE_TEXT, '0':"Unknown", '2':"2 Seconds", '30':"30 Seconds", '120':"2 Minutes", '240':"4 Minutes"} ),
    EPROM.SWITCH_ZONE      : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,   697,  24,   3,    -1,  "Switch Zone Data",                   {} ),
    "switch_Unknown"       : SettingsCommand( PSC.BOTH, Dumpy,  2, PDT.BYTE   ,   745,   8,   1,    -1,  "Switch Unknown",                     {} ),
    "switch_Trouble"       : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   747,   8,   0,    -1,  "Switch Trouble Indication",          { '1':"Enable", '0':"Disable"} ),
    "switch_Phase"         : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   748,   8,   0,    -1,  "Switch Phase and frequency",         { '0':"Disable", '1':"50 Hz", '2':"60 Hz"} ),
    "switch_ReportCs1"     : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   749,   1,   0,     0,  "Switch Report on Fail to Central 1", { '1':"Enable", '0':"Disable"} ),
    "switch_ReportCs2"     : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   749,   1,   0,     1,  "Switch Report on Fail to Central 2", { '1':"Enable", '0':"Disable"} ),
    "switch_ReportPagr"    : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   749,   1,   0,     2,  "Switch Report on Fail to Pager",     { '1':"Enable", '0':"Disable"} ),
    "switch_ReportPriv"    : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   749,   1,   0,     3,  "Switch Report on Fail to Private",   { '1':"Enable", '0':"Disable"} ),
    "switch_ReportSMS"     : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   749,   1,   0,     4,  "Switch Report on Fail to SMS",       { '1':"Enable", '0':"Disable"} ),
    "usrVoice"             : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   763,   8,   0,    -1,  "Set Voice Option",                   { '0':"Disable Voice", '1':"Enable Voice"} ),
    "usrSquawk"            : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   764,   8,   0,    -1,  "Squawk Option",                      { '0':"Disable", '1':"Low Level", '2':"Medium Level", '3':"High Level"}),
    "usrArmTime"           : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.TIME   ,   765,  16,   0,    -1,  "Auto Arm Time",                      {} ),
    EPROM.PART_ENABLED     : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,   768,   8,   1,    -1,  "Partition Enabled",                  {} ),   # This byte seems to be non-zero when partitions are enabled
    # Fairly sure that the intermediate 16 bytes are partition data but not sure what for, could be KeyFobs, panic buttons, repeaters etc
    EPROM.PART_ZONE_DATA   : SettingsCommand( PSC.BOTH, Dumpy, 64, PDT.BYTE   ,   785,   8,   1,    -1,  "Partition Zone Data",                {} ),   # I'm 99% sure these are the zone partition data in binary, 1 = partition 1, 2 = partition 2 and 4 = partition 3, ORd together so a sensor can be in multiple partitions
    "panelEprom"           : SettingsCommand( PSC.BOTH,  True,  1, PDT.STRING ,  1024, 128,   0,    -1,  "Panel Eprom",                        {} ),
    "panelSoftware"        : SettingsCommand( PSC.BOTH,  True,  1, PDT.STRING ,  1040, 144,   0,    -1,  "Panel Software",                     {} ),
    EPROM.PANEL_SERIAL     : SettingsCommand( PSC.BOTH,  True,  1, PDT.CODE   ,  1072,  48,   0,    -1,  "Panel Serial",                       {} ),   # page 4 offset 48
    EPROM.PANEL_MODEL_CODE : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,  1078,   8,   0,    -1,  "Panel Model Code",                   {} ),   # page 4 offset 54 and 55 ->> Panel model code
    EPROM.PANEL_TYPE_CODE  : SettingsCommand( PSC.BOTH, Dumpy,  1, PDT.BYTE   ,  1079,   8,   0,    -1,  "Panel Type Code",                    {} ),   # page 4 offset 55
    EPROM.USERCODE_MAS     : SettingsCommand( PSC.MAS , Dumpy, 96, PDT.BYTE   ,  2712,   8,   1,    -1,  "PowerMaster User Codes",             {} ),
    EPROM.SWITCH_ZONENAMES : SettingsCommand( PSC.BOTH, Dumpy, 16, PDT.BYTE   ,  2863,   8,   1,    -1,  "Switch Location Name references",    {} ),
    EPROM.ZONE_STR_NAM     : SettingsCommand( PSC.BOTH, Dumpy, 21, PDT.STRING ,  6400, 128,  16,    -1,  "Zone String Names Standard",         {} ),   # Zone String Names e.g "Attic", "Back door", "Basement", "Bathroom" etc 21 strings of 16 characters each
    EPROM.ZONE_STR_EXT     : SettingsCommand( PSC.BOTH, Dumpy, 10, PDT.STRING ,  6736, 128,  16,    -1,  "Zone String Names Custom",           {} ),   # Zone String Names Custom, 10 strings of 16 characters each
    EPROM.ZONEDATA_MAX     : SettingsCommand( PSC.MAX , Dumpy, 30, PDT.BYTE   ,  2304,  32,   4,    -1,  "Zone Data, PowerMax",                {} ),   # 4 bytes each, 30 zones --> 120 bytes
    EPROM.KEYFOB_MAX       : SettingsCommand( PSC.MAX , Dumpy,  8, PDT.INT    ,  2424,  32,   4,    -1,  "Maybe KeyFob Data PowerMax",         {} ),   # Structure not known
    "ZoneSignalPMax"       : SettingsCommand( PSC.MAX , Dumpy, 28, PDT.BYTE   ,  2522,   8,   1,    -1,  "Zone Signal Strength, PowerMax",     {} ),   # 28 wireless zones
    EPROM.KEYPAD_2_MAX     : SettingsCommand( PSC.MAX , Dumpy,  2, PDT.BYTE   ,  2560,  32,   4,    -1,  "Keypad2 Data, PowerMax",             {} ),   # 4 bytes each, 2 keypads
    EPROM.KEYPAD_1_MAX     : SettingsCommand( PSC.MAX , Dumpy,  8, PDT.BYTE   ,  2592,  32,   4,    -1,  "Keypad1 Data, PowerMax",             {} ),   # 4 bytes each, 8 keypads        THIS TOTALS 32 BYTES BUT IN OTHER SYSTEMS IVE SEEN 64 BYTES
    EPROM.SIRENS_MAX       : SettingsCommand( PSC.MAX , Dumpy,  2, PDT.BYTE   ,  2656,  32,   4,    -1,  "Siren Data, PowerMax",               {} ),   # 4 bytes each, 2 sirens
    EPROM.ZONENAME_MAX     : SettingsCommand( PSC.MAX , Dumpy, 30, PDT.BYTE   ,  2880,   8,   1,    -1,  "Zone Names, PowerMax",               {} ),
    EPROM.ZONEDATA_MAS     : SettingsCommand( PSC.MAS , Dumpy, 64, PDT.BYTE   ,  2304,   8,   1,    -1,  "Zone Data, PowerMaster",             {} ),   # 1 bytes each, 64 zones --> 64 bytes
    EPROM.ZONENAME_MAS     : SettingsCommand( PSC.MAS , Dumpy, 64, PDT.BYTE   ,  2400,   8,   1,    -1,  "Zone Names, PowerMaster",            {} ),
    EPROM.SIRENS_MAS       : SettingsCommand( PSC.MAS , Dumpy,  8, PDT.BYTE   , 46818,  80,  10,    -1,  "Siren Data, PowerMaster",            {} ),   # 10 bytes each, 8 sirens
    EPROM.KEYPAD_MAS       : SettingsCommand( PSC.MAS , Dumpy, 32, PDT.BYTE   , 46898,  80,  10,    -1,  "Keypad Data, PowerMaster",           {} ),   # 10 bytes each, 32 keypads
    EPROM.ZONEEXT_MAS      : SettingsCommand( PSC.MAS , Dumpy, 64, PDT.BYTE   , 47218,  80,  10,    -1,  "Zone Extended Data, PowerMaster",    {} ),   # 10 bytes each, 64 zones
    "AlarmLED10"           : SettingsCommand( PSC.MAS , Dumpy, 64, PDT.BYTE   , 49250,   8,   1,    -1,  "Alarm LED, PowerMaster 10",          {} ),   # This is the Alarm LED On/OFF settings for Motion Sensors -> Dev Settings --> Alarm LED
    "AlarmLED30"           : SettingsCommand( PSC.MAS , Dumpy, 64, PDT.BYTE   , 49735,   8,   1,    -1,  "Alarm LED, PowerMaster 30",          {} ),   # This is the Alarm LED On/OFF settings for Motion Sensors -> Dev Settings --> Alarm LED
    EPROM.ZONE_DEL_MAS     : SettingsCommand( PSC.MAS , Dumpy, 64, PDT.BYTE   , 49542,  16,   2,    -1,  "Zone Delay, PowerMaster",            {} )    # This is the Zone Delay settings for Motion Sensors -> Dev Settings --> Disarm Activity

#    EPROM.ZONE_STRING      : SettingsCommand( PSC.BOTH, Dumpy, 32, PDT.STRING ,  6400, 128,  16,    -1,  "Zone String Names",                  {} ),   # Zone String Names e.g "Attic", "Back door", "Basement", "Bathroom" etc 32 strings of 16 characters each
#    EPROM.PART_ZONE_DATA        : SettingsCommand( PSC.BOTH, Dumpy, 255, PDT.BYTE   ,    768,   8,   1,    -1,  "Partition Data",                     {} ),   # I'm not sure how many bytes this is or what they mean, i get all 255 bytes to the next entry so they can be displayed
    #"MaybeScreenSaver":SettingsCommand( PSC.BOTH, Dumpy, 75, PDT.BYTE   ,   5888,   8,   1,    -1,  "Maybe the screen saver",             {} ),   # Structure not known
    #"MaybeEventLog"        : SettingsCommand( PSC.BOTH,  Dumpy, 256, PDT.BYTE   ,   1247,   8,   1,    -1,  "Maybe the event log",                {} ),   # Structure not known   was length 808 but cut to 256 to see what data we get
    #"ZoneStrType1X"    : SettingsCommand(  PSC.MAX, Dumpy, 16,PDT.STRING , 22568, 120,  16,    -1,  "PowerMax Zone Type String",          {} ),   # Zone String Types e.g
#    "ZoneStrType1X"    : SettingsCommand(  PSC.MAX, Dumpy, 16,PDT.STRING , 22571,  96,  16,    -1,  "PowerMax Zone Type String",          {} ),   # Zone String Types e.g This starts 3 bytes later as it misses the "1. " and the strings are only 12 characters
#    "ZoneStrType2X"    : SettingsCommand(  PSC.MAX, Dumpy, 16,  PDT.BYTE   , 22583,   8,  16,    -1,  "PowerMax Zone Type Reference",       {} ),   # Zone String Types e.g
#    "ZoneChimeType1X"  : SettingsCommand(  PSC.MAX, Dumpy,  3,PDT.STRING ,0x64D8, 120,  16,    -1,  "PowerMax Zone Chime Type String",    {} ),   # Zone String Types e.g
#    "ZoneChimeType2X"  : SettingsCommand(  PSC.MAX, Dumpy,  3,  PDT.BYTE   ,0x64E7,   8,  16,    -1,  "PowerMax Zone Chime Type Ref",       {} ),   # Zone String Types e.g
    #"Test2"            : SettingsCommand( PSC.BOTH, Dumpy,128, PDT.BYTE   ,  2816,   8,   1,    -1,  "Test 2 String, PowerMax",            {} ),   # 0xB00
    #"Test1"            : SettingsCommand( PSC.BOTH, Dumpy,128, PDT.BYTE   ,  2944,   8,   1,    -1,  "Test 1 String, PowerMax",            {} ),   # 0xB80
    #"ZoneStrType1S"    : SettingsCommand(  PSC.MAS, Dumpy, 16,PDT.STRING , 33024, 120,  16,    -1,  "PowerMaster Zone Type String",       {} ),   # Zone String Types e.g
#    "ZoneStrType1S"    : SettingsCommand(  PSC.MAS, Dumpy, 16,PDT.STRING , 33027,  96,  16,    -1,  "PowerMaster Zone Type String",       {} ),   # Zone String Types e.g  This starts 3 bytes later as it misses the "1. " and the strings are only 12 characters
#    "ZoneStrType2S"    : SettingsCommand(  PSC.MAS, Dumpy, 16,  PDT.BYTE   , 33039,   8,  16,    -1,  "PowerMaster Zone Type Reference",    {} ),   # Zone String Types e.g
#    "ZoneChimeType1S"  : SettingsCommand(  PSC.MAS, Dumpy,  3,PDT.STRING ,0x8EB0, 120,  16,    -1,  "PowerMaster Zone Chime Type String", {} ),   # Zone String Types e.g
#    "ZoneChimeType2S"  : SettingsCommand(  PSC.MAS, Dumpy,  3,  PDT.BYTE   ,0x8EBF,   8,  16,    -1,  "PowerMaster Zone Chime Type Ref",    {} ),   # Zone String Types e.g
#    "LogEventStr"      : SettingsCommand(  PSC.MAS, Dumpy,160,PDT.STRING ,0xED00, 128,  16,    -1,  "Log Event Strings",                  {} ),   # Zone String Types e.g
}

# PMAX EPROM CONFIGURATION version 1_2
# 'show count type poff psize pstep pbitoff name values'


# fmt: on


###################################################################################
##########################  EPROM Blocks to download ##############################
###################################################################################

# These blocks are not value specific, they are used to download blocks of EPROM data that we need without reference to what the data means
#    They are used when EPROM_DOWNLOAD_ALL is False
#    We have to do it like this as the max message size is 176 (0xB0) bytes.

@dataclass
class EpromBlock:
    """Eprom block of data."""
    start: int
    end: int   # exclusive

    @property
    def size(self) -> int:
        """Size of the block."""
        return self.end - self.start


MAX_DOWNLOAD_BLOCK_SIZE = 0x80
#MAX_DOWNLOAD_BLOCK_SIZE = 0xB0
PAGE_SIZE = 0x100
MAX_SETTING_LEN = MAX_DOWNLOAD_BLOCK_SIZE + 1
EMPTY_BYTE = 0xFF

class EPROMManager:
    """Manages the EPROM data download and storage."""

    def __init__(self) -> None:
        """Initialize the EPROM Manager."""
        # Save the EPROM data when downloaded
        self.pmRawSettings = {}
        self.pmDownloadComplete = False
        self.lastSavedPages = set()

        # Identify the eprom blocks to download for a PowerMax and PowerMaster
        #   Only download the settings that have "EPROM" enumerations
        #     and also split the list in to PowerMax and PowerMaster using "MAX" and "MAS" at the end of the EPROM enumerations
        #     I know this is cheating
        max_blocks, mas_blocks = self.collate_eprom_blocks_to_download(pmDecodePanelSettings)

        #log.warning("POWERMAX")
        #for b in max_blocks:
        #    log.warning(f"{b.start:5} -> {b.end - 1:5}   size={b.size}")
        #log.warning("POWERMASTER")
        #for b in mas_blocks:
        #    log.warning(f"{b.start:5} -> {b.end - 1:5}   size={b.size}")

        download_dedicated: dict[PanelTypeEnum, list[EpromBlock]] = {
            PanelTypeEnum.POWER_MAX : max_blocks,
            PanelTypeEnum.POWER_MASTER : mas_blocks
        }

        # And now split the blocks that cross a page boundary
        self.pmBlockDownload: dict[PanelTypeEnum, list[bytearray]] = {}
        for blk, data in download_dedicated.items():
            lst = []
            for ed in data:
                s = ed.start
                e = ed.end
                while s < e:
                    lst.append(bytearray([s & 0xFF, (s >> 8) & 0xFF, min(MAX_DOWNLOAD_BLOCK_SIZE, e - s), 0]))
                    s = s + MAX_DOWNLOAD_BLOCK_SIZE
            self.pmBlockDownload[blk] = lst
            #log.warning(f"{blk}")
            #for i, b in enumerate(lst, start=1):
            #    log.warning(f"{i}  {toString(b)}")


    def reset(self) -> None:
        """Reset the EPROM Manager."""
        self.pmRawSettings = {}
        self.pmDownloadComplete = False
        self.lastSavedPages = set()

    def findLength(self, is_power_master : bool, page : int, index : int) -> int | None:
        """Find the length of a block to download based on page and index."""
        p = PanelTypeEnum.POWER_MASTER if is_power_master else PanelTypeEnum.POWER_MAX
        for b in self.pmBlockDownload[p]:
            if b[0] == index and b[1] == page:
                return b[2]
        return None

    def _merge_ranges(
        self,
        ranges: list[tuple[int, int]],
        max_gap: int = 0
    ) -> list[EpromBlock]:

        if not ranges:
            return []

        ranges.sort(key=lambda r: r[0])
        merged: list[EpromBlock] = []

        for start, end in ranges:
            if not merged:
                merged.append(EpromBlock(start, end))
                continue
            last = merged[-1]
            gap = start - last.end
            # overlap, contiguous, or within allowed gap
            if gap <= max_gap:
                last.end = max(last.end, end)
            else:
                merged.append(EpromBlock(start, end))

        return merged


    def collate_eprom_blocks_to_download(
        self,
        settings: dict[str | EPROM, SettingsCommand],
        merge_gap: int = 20                           # is the start of a block is within X of the end of previous then merge them
    ) -> tuple[list[EpromBlock], list[EpromBlock]]:
        """Build raw ranges from EPROM entries only."""

        max_ranges: list[tuple[int, int]] = []
        mas_ranges: list[tuple[int, int]] = []

        for key, setting in settings.items():
            # ignore string keys
            if not isinstance(key, EPROM):
                continue
            # size of each item in bytes
            item_size_bytes = ceil(setting.psize / 8)
            # offset of final item
            last_item_offset = (
                setting.poff
                + ((setting.count - 1) * setting.pstep)
            )
            start = setting.poff
            end = last_item_offset + item_size_bytes
            # PowerMax list
            #if name.endswith("MAX") or not name.endswith("MAS"):
            if setting.panel in (PSC.BOTH, PSC.MAX):
                max_ranges.append((start, end))
            # PowerMaster list
            #if name.endswith("MAS") or not name.endswith("MAX"):
            if setting.panel in (PSC.BOTH, PSC.MAS):
                mas_ranges.append((start, end))

        return (
            self._merge_ranges(max_ranges, merge_gap),
            self._merge_ranges(mas_ranges, merge_gap),
        )

    def _validatEPROMSettingsBlock(self, block : bytearray) -> bool:
        page = block[1]
        index = block[0]
        settings_len = block[2]

        retlen = settings_len
        retval = bytearray()
        while page in self.pmRawSettings and retlen > 0:
            rawset = self.pmRawSettings[page][index : index + retlen]
            retval = retval + rawset
            page += 1
            retlen = retlen - len(rawset)
            index = 0
        log.debug(f"[_validatEPROMSettingsBlock]    page {block[1]:>3}   index {block[0]:>3}   length {block[2]:>3}     {'Already Got It' if settings_len == len(retval) else 'Not Got It'}")
        return settings_len == len(retval)

    def populatEPROMDownload(self, is_power_master : bool) -> list[bytearray]:
        """Populate the EPROM Download List."""

        # Empty list and start at the beginning
        download_list = []
        self.pmDownloadComplete = False

        if EPROM_DOWNLOAD_ALL:
            for page in range(256):
                mystr = '00 ' + format(page, '02x').upper() + ' 80 00'
                if not self._validatEPROMSettingsBlock(convert_bytearray(mystr)):
                    download_list.append(convert_bytearray(mystr))
                mystr = '80 ' + format(page, '02x').upper() + ' 80 00'
                if not self._validatEPROMSettingsBlock(convert_bytearray(mystr)):
                    download_list.append(convert_bytearray(mystr))
        elif is_power_master:
            for block in self.pmBlockDownload[PanelTypeEnum.POWER_MASTER]:
                if not self._validatEPROMSettingsBlock(block):
                    download_list.append(block)  # noqa: PERF401
        else:
            for block in self.pmBlockDownload[PanelTypeEnum.POWER_MAX]:
                if not self._validatEPROMSettingsBlock(block):
                    download_list.append(block)  # noqa: PERF401

        self.pmDownloadComplete = len(download_list) == 0
        return download_list

    # _saveEPROMSettings: add a certain setting to the settings table
    #      When we send a MSG_DL and insert the 4 bytes from pmDownloadItem_t, what we're doing is setting the page, index and len
    # This function stores the downloaded status and EPROM data
    def saveEPROMSettings(self, page : int, index : int, setting : bytearray) -> None:
        """Save the EPROM settings block."""
        settings_len = len(setting)
        wrappoint = index + settings_len - PAGE_SIZE
        sett = [bytearray(b""), bytearray(b"")]
        saved = set()  # empty set

        #log.debug(f"[Write Settings]   Entering Function  page {page}   index {index}    length {settings_len}")
        if settings_len > MAX_SETTING_LEN:
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

        for i in range(wrappoint + 1):
            if (page + i) not in self.pmRawSettings:
                self.pmRawSettings[page + i] = bytearray()
                for _dummy in range(256):
                    self.pmRawSettings[page + i].append(255)
                if len(self.pmRawSettings[page + i]) != 256:
                    log.debug("[Write Settings] the EPROM settings is incorrect for page %s", str(page + i))
                # else:
                #    log.debug("[Write Settings] WHOOOPEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE")

            settings_len = len(sett[i])
            if i == 1:
                index = 0
            #log.debug(f"[Write Settings]         Writing settings page {page+i}  index {index}    length {settings_len}")
            self.pmRawSettings[page + i] = self.pmRawSettings[page + i][0:index] + sett[i] + self.pmRawSettings[page + i][index + settings_len :]
            saved.add(page + i)
            #if len(self.pmRawSettings[page + i]) != 256:
            #    log.debug(f"[Write Settings] OOOOOOOOOOOOOOOOOOOO len = {len(self.pmRawSettings[page + i])}")
            # else:
            #    log.debug(f"[Write Settings] Page {page+i} is now {toString(self.pmRawSettings[page + i])}")

        self.lastSavedPages = saved   # The last set of saved pages
        #log.debug(f"[Write Settings]    The last set of saved pages {self.lastSavedPages}")

    def removeLastSaved(self) -> None:
        """Remove the last saved EPROM settings pages."""
        if len(self.lastSavedPages) > 0:
            self.pmDownloadComplete = False
            for sp in self.lastSavedPages:
                if sp in self.pmRawSettings:   # just to make sure
                    del self.pmRawSettings[sp]
            self.lastSavedPages = set()       # just in case this function is called again

    # _readEPROMSettingsPageIndex
    # This function retrieves the downloaded status and EPROM data
    def _readEPROMSettingsPageIndex(self, page : int, index : int, settings_len : int) -> bytearray:
        retlen = settings_len
        retval = bytearray()
        while index > 255:
            page += 1
            index = index - 256

        if self.pmDownloadComplete:
            #log.debug(f"[_readEPROMSettingsPageIndex]    Entering Function  page {page}   index {index}    length {settings_len}")
            while page in self.pmRawSettings and retlen > 0:
                rawset = self.pmRawSettings[page][index : index + retlen]
                retval = retval + rawset
                page += 1
                retlen = retlen - len(rawset)
                index = 0
            if settings_len == len(retval):
                return retval
        log.debug(f"[_readEPROMSettingsPageIndex]     Sorry but you havent downloaded that part of the EPROM data     page={hex(page)} index={hex(index)} length={settings_len}")

        # return a bytearray filled with 0xFF values
        retval = bytearray()
        for _dummy in range(settings_len):
            retval.append(EMPTY_BYTE)
        return retval

    # this can be called from an entry in pmDownloadItem_t such as
    #      page index lenhigh lenlow
#    def readEPROMSettings(self, item):
#        """Read the EPROM settings based on the item."""
#        return self._readEPROMSettingsPageIndex(item[0], item[1], item[3] + (PAGE_SIZE * item[2]))

    # This function was going to save the settings (including EPROM) to a file
    def _dumpEPROMSettings(self) -> None:
        log.debug("Dumping EPROM Settings")
        for p in range(PAGE_SIZE):  ## assume page can go from 0 to 255
            if p in self.pmRawSettings:
                for j in range(0, PAGE_SIZE, 0x10):  ## assume that each page can be 256 bytes long, step by 16 bytes
                    # do not display the rows with pin numbers
                    # if not (( p == 1 and j == 240 ) or (p == 2 and j == 0) or (p == 10 and j >= 140)):
                    if EPROM_DOWNLOAD_ALL or ((p != 1 or j != 240) and (p != 2 or j != 0) and (p != 10 or j <= 140)):
                        if j <= len(self.pmRawSettings[p]):
                            sr = toString(self.pmRawSettings[p][j : j + 0x10])
                            log.debug(f"{p:3}:{j:3}  {sr}")

#    def _calcBoolFromIntMask(self, val, mask) -> bool:
#        return val & mask != 0

    # SettingsCommand = collections.namedtuple('SettingsCommand', 'show count type poff psize pstep pbitoff name values')
    def lookupEprom(self, ref : EPROM | SettingsCommand | str , expected_size : int = -1 ) -> list:
        """Lookup EPROM settings based on reference."""
        val : SettingsCommand | None = None

        if isinstance(ref, SettingsCommand):
            val = ref
        elif ref in pmDecodePanelSettings:
            if isinstance(ref, (EPROM, str)):
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

        for ctr in range(val.count):
            addr = val.poff + (ctr * val.pstep)
            page = math.floor(addr / PAGE_SIZE)
            pos = addr % PAGE_SIZE

            myvalue = ""

            size = 1 + ((val.psize - 1) // 8)

            if val.type in {PDT.BYTE, PDT.INT}:
                #log.debug(f"[lookupEprom] A {val}")
                v = self._readEPROMSettingsPageIndex(page, pos, size)
                #log.debug(f"[lookupEprom] B {v}")
                if val.psize > 8:
                    myvalue = v
                    if val.type == PDT.INT:
                        myvalue=b2i(myvalue)
                elif val.psize == 8:
                    myvalue = v[0]
                    if val.type == PDT.INT:
                        myvalue=b2i(myvalue)
                else:
                    mask = (1 << val.psize) - 1
                    offset = val.pbitoff | 0
                    myvalue = str((v[0] >> offset) & mask)

            elif val.type == PDT.PHONE:
                for j in range(size):
                    nr = self._readEPROMSettingsPageIndex(page, pos + j, 1)
                    if nr[0] != EMPTY_BYTE:
                        myvalue += "".join(f"{b:02x}" for b in nr)
            elif val.type == PDT.TIME:
                t = self._readEPROMSettingsPageIndex(page, pos, size)
                myvalue = ":".join(f"{b:02d}" for b in t)  # miss the last character off, which will be a colon :
            elif val.type in {PDT.CODE, PDT.ACCOUNT}:
                nr = self._readEPROMSettingsPageIndex(page, pos, size)
                myvalue = "".join(f"{b:02x}" for b in nr).upper()
                myvalue = myvalue.replace("FF", ".")
            elif val.type in {PDT.STRING, PDT.DATE}:
                for j in range(size):
                    nr = self._readEPROMSettingsPageIndex(page, pos + j, 1)
                    #log.debug(f"[lookupEprom] {page} {pos+j}  character {nr}   {chr(nr[0])}")
                    if nr[0] != EMPTY_BYTE:
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
                            retval.extend(val.values[v])
            else:
                retval.append(myvalue)

        return retval

    def lookupEpromSingle(self, key : EPROM | SettingsCommand | str ) -> Any | None:
        """Lookup a single EPROM setting based on reference."""
        v = self.lookupEprom(key)
        if len(v) >= 1:
            return v[0]
        return None

    def processEPROMData(self, is_power_master : bool) -> dict[str,Any]:
        """Process the EPROM data into a dictionary of panel status."""
        # If val.show is True but add_to_log is False then:
        #      Add the "True" values to the self.Panelstatus
        # If val.show is True and add_to_log is True then:
        #      Add all (either PowerMax / PowerMaster) values to the self.Panelstatus and the log file
        panel_status : dict[str,Any] = {}
        add_to_log = False
        for key, val in pmDecodePanelSettings.items():
            #val = pmDecodePanelSettings[key]
            panel_test = val.panel in (PSC.BOTH, PSC.MAS) if is_power_master else val.panel in (PSC.BOTH, PSC.MAX)
            if panel_test and val.show:
                result = self.lookupEprom(val)
                if result is not None:
                    if isinstance(val.name, str) and len(result) == 1:
                        if isinstance(result[0], (bytes, bytearray)):
                            packet = bytearray(result[0])
                            tmpdata = toString(packet)
                            if add_to_log:
                                log.debug(f"[processEPROMData]      {key:<18}  {val.name:<40}  {tmpdata}")
                            panel_status[val.name] = tmpdata
                        else:
                            if add_to_log:
                                log.debug(f"[processEPROMData]      {key:<18}  {val.name:<40}  {result[0]}")
                            panel_status[val.name] = result[0]

                    elif isinstance(val.name, list) and len(result) == len(val.name):
                        for i, item in enumerate(result):
                            if isinstance(result[0], (bytes, bytearray)):
                                tmpdata = toString(item)
                                if add_to_log:
                                    log.debug(f"[processEPROMData]      {key:<18}  {val.name[i]:<40}  {tmpdata}")
                                panel_status[val.name[i]] = tmpdata
                            else:
                                if add_to_log:
                                    log.debug(f"[processEPROMData]      {key:<18}  {val.name[i]:<40}  {item}")
                                panel_status[val.name[i]] = item

                    elif len(result) > 1 and isinstance(val.name, str):
                        tmpdata = ""
                        for _i, item in enumerate(result):
                            if isinstance(result[0], (bytes, bytearray)):
                                tmpdata = tmpdata + toString(item) + ", "
                            else:
                                tmpdata = tmpdata + str(item) + ", "
                        # there's at least 2 so this will not exception
                        tmpdata = tmpdata[:-2]
                        if add_to_log:
                            log.debug(f"[processEPROMData]      {key:<18}  {val.name:<40}  {tmpdata}")
                        panel_status[val.name] = tmpdata

                    #else:
                    #    log.debug(f"[processEPROMData]   ************************** NOTHING DONE ************************     {key:<18}  {val.name}  {result}")
        return panel_status
