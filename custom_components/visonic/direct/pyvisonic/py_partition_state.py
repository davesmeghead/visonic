"""Partition State."""

# ruff: noqa: G004

import asyncio
from collections.abc import Callable
from enum import StrEnum
import logging
from typing import Any, NamedTuple

from .py_enum import EVENT_TYPE, AlAlarmType, AlPanelMode, AlPanelStatus, AlTroubleType
from .py_sensor import AlSensorDeviceHelper
from .py_types import AlPanelEventData
from .py_utils import hexify

log = logging.getLogger(__name__)


# These dictionaries are subsets of EVENT_TYPE
pmPanelAlarmType_t: dict[EVENT_TYPE, AlAlarmType] = {      # Alarm Triggers (except Intruder which is separate)
   EVENT_TYPE.NONE        : AlAlarmType.NONE,
   EVENT_TYPE.PANIC_KEYFOB: AlAlarmType.PANIC,
   EVENT_TYPE.PANIC_PANEL : AlAlarmType.PANIC,
   EVENT_TYPE.DURESS      : AlAlarmType.PANIC,
   EVENT_TYPE.FIRE        : AlAlarmType.FIRE,
   EVENT_TYPE.EMERGENCY   : AlAlarmType.EMERGENCY,
   EVENT_TYPE.GAS_ALERT   : AlAlarmType.GAS,
   EVENT_TYPE.FLOOD_ALERT : AlAlarmType.FLOOD,
}

pmPanelAlarmType_r: frozenset[EVENT_TYPE] = {       # Alarm Restore (except Intruder which is separate)
   EVENT_TYPE.GENERAL_RESTORE,
   EVENT_TYPE.FIRE_RESTORE,
   EVENT_TYPE.EMERGENCY_RESTORE,
   EVENT_TYPE.GAS_ALERT_RESTORE,
   EVENT_TYPE.FLOOD_ALERT_RESTORE,
   EVENT_TYPE.ALARM_CANCEL
   # PANIC_KEYFOB and PANIC_PANEL from pmPanelAlarmType_t are not covered, are they covered by the more general ALARM_CANCEL
}

pmPanelIntruderType_t: frozenset[EVENT_TYPE] = {     # Intruder Alarm State Triggers
   EVENT_TYPE.ALARM_INTERIOR,
   EVENT_TYPE.ALARM_PERIMETER,
   EVENT_TYPE.ALARM_DELAY,
   EVENT_TYPE.ALARM_SILENT_24H,
   EVENT_TYPE.ALARM_AUDIBLE_24H
}

pmPanelIntruderType_r: frozenset[EVENT_TYPE] = {     # Intruder Alarm State Restore
   EVENT_TYPE.GENERAL_RESTORE,
   EVENT_TYPE.DISARM,
   EVENT_TYPE.ALARM_CANCEL,
   EVENT_TYPE.ALARM_INTERIOR_RESTORE,
   EVENT_TYPE.ALARM_PERIMETER_RESTORE,
   EVENT_TYPE.ALARM_DELAY_RESTORE,
   EVENT_TYPE.ALARM_SILENT_24H_RESTORE,
   EVENT_TYPE.ALARM_AUDIBLE_24H_RESTORE
}

pmPanelTroubleType_t: dict[EVENT_TYPE, AlTroubleType] = {    # Trouble Indications
   EVENT_TYPE.NONE                  : AlTroubleType.NONE,
   EVENT_TYPE.SWITCH_TROUBLE        : AlTroubleType.GENERAL,         # Should we add SWITCH to AlTroubleType, and add it to the language translations
   EVENT_TYPE.GAS_TROUBLE           : AlTroubleType.GENERAL,         # Should we add GAS to AlTroubleType, and add it to the language translations
   EVENT_TYPE.COMMUNICATION_LOSS    : AlTroubleType.COMMUNICATION,
   EVENT_TYPE.GENERAL_TROUBLE       : AlTroubleType.GENERAL,
   EVENT_TYPE.LOW_BATTERY           : AlTroubleType.BATTERY,
   EVENT_TYPE.AC_FAIL               : AlTroubleType.POWER,
   EVENT_TYPE.RF_JAMMING            : AlTroubleType.JAMMING,
   EVENT_TYPE.COMMUNICATION_FAILURE : AlTroubleType.COMMUNICATION,
   EVENT_TYPE.TELEPHONE_LINE_FAILURE: AlTroubleType.TELEPHONE,
   EVENT_TYPE.FUSE_FAILURE          : AlTroubleType.POWER,
   EVENT_TYPE.KEYFOB_LOW_BATTERY    : AlTroubleType.BATTERY,
   EVENT_TYPE.KEYPAD_LOW_BATTERY    : AlTroubleType.BATTERY,
}

pmPanelTroubleType_r: frozenset[EVENT_TYPE] = {    # Trouble Restore
   EVENT_TYPE.GAS_TROUBLE_RESTORE,
   EVENT_TYPE.SWITCH_TROUBLE_RESTORE,
   EVENT_TYPE.GENERAL_RESTORE,
   EVENT_TYPE.COMMUNICATION_LOSS_RESTORE,
   EVENT_TYPE.GENERAL_TROUBLE_RESTORE,
   EVENT_TYPE.LOW_BATTERY_RESTORE,
   EVENT_TYPE.AC_FAIL_RESTORE,
   EVENT_TYPE.RF_JAMMING_RESTORE,
   EVENT_TYPE.COMMUNICATION_FAILURE_RESTORE,
   EVENT_TYPE.TELEPHONE_LINE_FAILURE_RESTORE,
   EVENT_TYPE.FUSE_FAILURE_RESTORE,
   EVENT_TYPE.KEYFOB_LOW_BATTERY_RESTORE,
   EVENT_TYPE.KEYPAD_LOW_BATTERY_RESTORE,
}

pmPanelTamperType_t: frozenset[EVENT_TYPE] = {                       # Panel Tamper
   EVENT_TYPE.NONE,
   EVENT_TYPE.TAMPER_SENSOR,
   EVENT_TYPE.TAMPER_ALARM_A,
   EVENT_TYPE.TAMPER_ALARM_B,
   EVENT_TYPE.TAMPER_PANEL
}

pmPanelTamperType_r: frozenset[EVENT_TYPE] = {                       # Panel Tamper Restore
   EVENT_TYPE.GENERAL_RESTORE,
   EVENT_TYPE.TAMPER_SENSOR_RESTORE,
   EVENT_TYPE.TAMPER_ALARM_A_RESTORE,
   EVENT_TYPE.TAMPER_ALARM_B_RESTORE,
   EVENT_TYPE.TAMPER_PANEL_RESTORE
}

pmPanelBatteryType_t: frozenset[EVENT_TYPE] = {                      # Panel Low Battery
   EVENT_TYPE.PANEL_LOW_BATTERY
}

pmPanelBatteryType_r: frozenset[EVENT_TYPE] = {                      # Panel Low Battery Restore
   EVENT_TYPE.GENERAL_RESTORE,
   EVENT_TYPE.PANEL_LOW_BATTERY_RESTORE
}


class PanelArmedStatusCollection(NamedTuple):
    """Collection for Visonic Panel Armed status mapping."""
    disarmed: bool | None
    armed: bool | None
    entry: bool
    state: AlPanelStatus
    eventmapping: EVENT_TYPE

# fmt: off

pmPanelArmedStatus: dict[int, PanelArmedStatusCollection] = {
                                     # disarmed armed entry         state
   0x00: PanelArmedStatusCollection(  True, False, False, AlPanelStatus.DISARMED           , EVENT_TYPE.DISARM),                 # Disarmed
   0x01: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_HOME        , EVENT_TYPE.NOT_DEFINED),            # Arming Home
   0x02: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_AWAY        , EVENT_TYPE.NOT_DEFINED),            # Arming Away
   0x03: PanelArmedStatusCollection( False,  True,  True, AlPanelStatus.ENTRY_DELAY        , EVENT_TYPE.NOT_DEFINED),            # Entry Delay
   0x04: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_HOME         , EVENT_TYPE.ARMED_HOME),             # Armed Home
   0x05: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_AWAY         , EVENT_TYPE.ARMED_AWAY),             # Armed Away
   0x06: PanelArmedStatusCollection(  True, False, False, AlPanelStatus.USER_TEST          , EVENT_TYPE.NOT_DEFINED),            # User Test  (assume can only be done when panel is disarmed)

   0x07: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.DOWNLOADING        , EVENT_TYPE.NOT_DEFINED),            # Downloading
   0x08: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.INSTALLER          , EVENT_TYPE.INSTALLER_PROGRAMMING),  # Programming
   0x09: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.INSTALLER          , EVENT_TYPE.INSTALLER_PROGRAMMING),  # Installer
   0x0A: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_HOME         , EVENT_TYPE.ARMED_HOME),             # Armed Home Bypass   AlPanelStatus.ARMED_HOME_BYPASS
   0x0B: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_AWAY         , EVENT_TYPE.ARMED_AWAY),             # Armed Away Bypass   AlPanelStatus.ARMED_AWAY_BYPASS
   0x0C: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.DISARMED           , EVENT_TYPE.DISARM),                 # Ready
   0x0D: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.DISARMED           , EVENT_TYPE.DISARM),                 # Not Ready  (assume can only be done when panel is disarmed)
   0x0E: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.UNKNOWN            , EVENT_TYPE.DISARM),                 # ?
   0x0F: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.UNKNOWN            , EVENT_TYPE.DISARM),                 # ?
   # I don't think that the B0 message can command higher than 15
   0x10: PanelArmedStatusCollection(  True, False, False, AlPanelStatus.DISARMED           , EVENT_TYPE.DISARM),                 # Disarmed Instant
   0x11: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_HOME        , EVENT_TYPE.NOT_DEFINED),            # Arming Home Last 10 Seconds             ####### armed was False
   0x12: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_AWAY        , EVENT_TYPE.NOT_DEFINED),            # Arming Away Last 10 Seconds             ####### armed was False
   0x13: PanelArmedStatusCollection( False,  True,  True, AlPanelStatus.ENTRY_DELAY_INSTANT, EVENT_TYPE.NOT_DEFINED),            # Entry Delay Instant
   0x14: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_HOME_INSTANT , EVENT_TYPE.ARMED_HOME),             # Armed Home Instant
   0x15: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_AWAY_INSTANT , EVENT_TYPE.ARMED_AWAY)              # Armed Away Instant
}
# fmt: on

class PartitionStateClass:
    """Partition State Class with improved async timer management."""

    class _TimerName(StrEnum):
        ALARM = "AlarmTimeTask"
        INTRUDER = "IntruderTimeTask"

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        """Initialize class."""
        self.loop = loop
        self.bellTime: int = 20 * 60  # 20 minutes
        self._timers: dict[PartitionStateClass._TimerName, asyncio.Task | None] = {
            self._TimerName.ALARM: None,
            self._TimerName.INTRUDER: None
        }
        self.Reset()

    def Reset(self):
        """Reset the partition state."""
        self._cancel_timer(self._TimerName.ALARM)
        self._cancel_timer(self._TimerName.INTRUDER)
        self.PanelStateData: AlPanelStatus = AlPanelStatus.UNKNOWN
        self.PanelStateSourceData: int | None = None
        self.PanelReady: bool = False
        self.PanelTamper: bool = False
        self.PanelAlertInMemory: bool = False
        self.PanelBypass: bool = False
        self.SirenActive: bool = False
        self.SirenActiveDeviceTrigger: AlSensorDeviceHelper | None = None
        self.PanelAlarmStatus: AlAlarmType = AlAlarmType.NONE
        self.PanelIntruderStatus: bool = False
        self.PanelTroubleStatus: AlTroubleType = AlTroubleType.NONE
        self.PartitionGeneralTrouble: bool = False
        self.PanelBatteryTrouble: bool = False

    def shutdownOperation(self):
        """Shutdown operation."""
        self._cancel_timer(self._TimerName.ALARM)
        self._cancel_timer(self._TimerName.INTRUDER)
        self.Reset()

    # -------------------------
    # Async Timer Management
    # -------------------------
    def _start_timer(self, name: _TimerName, duration: float, restart_if_running: bool = True, callback: Callable[..., None] | None = None, *args, **kwargs):
        task = self._timers.get(name)
        if task and not task.done():
            if restart_if_running:
                self._cancel_timer(name)
            else:
                return  # timer already running, no need to restart

        async def timer_coroutine():
            try:
                await asyncio.sleep(duration)
                if callback:
                    callback(*args, **kwargs)
            except asyncio.CancelledError:
                pass
            finally:
                self._timers[name] = None

        self._timers[name] = self.loop.create_task(timer_coroutine(), name="pyvisonic_event_timeout")

    def _cancel_timer(self, name: _TimerName):
        """Cancel a named async timer if running."""
        task = self._timers.get(name)
        if task:
            task.cancel()
            self._timers[name] = None

    def isTimerRunning(self, name: _TimerName) -> bool:
        """Check if a named timer is currently running."""
        task = self._timers.get(name)
        return task is not None and not task.done()

    # -------------------------
    # Alarm Timer Helpers
    # -------------------------
    def startAlarmTimer(self, restart_if_running: bool = True):
        """Start the panel alarm timer."""
        self._start_timer(self._TimerName.ALARM, self.bellTime, restart_if_running, self._resetAlarmStatus)

    def stopAlarmTimer(self):
        """Stop the panel alarm timer."""
        self._cancel_timer(self._TimerName.ALARM)
        self._resetAlarmStatus()

    def _resetAlarmStatus(self):
        self.PanelAlarmStatus = AlAlarmType.NONE

    # -------------------------
    # Intruder Timer Helpers
    # -------------------------
    def startIntruderTimer(self):
        """Start the intruder timer."""
        def callback():
            self.PanelIntruderStatus = False
            self.SirenActive = False
            self.SirenActiveDeviceTrigger = None

        self._start_timer(self._TimerName.INTRUDER, self.bellTime, True, callback)
        self.SirenActive = True

    def stopIntruderTimer(self):
        """Stop the intruder timer."""
        self._cancel_timer(self._TimerName.INTRUDER)
        self.PanelIntruderStatus = False
        self.SirenActive = False
        self.SirenActiveDeviceTrigger = None

    def statelist(self) -> list[Any]:
        """Get the list of partition state values."""
        # These are the values that are used to determine if the panel state has been changed
        return [self.SirenActive, self.PanelStateData, self.PanelReady,
                self.PanelTroubleStatus, self.PanelAlarmStatus,
                self.PanelIntruderStatus, self.PanelBypass, self.PartitionGeneralTrouble]

    def setBellTime(self, bt):
        """Set the bell time."""
        log.debug("[setBellTime]   Setting bell time to %s", bt)
        self.bellTime = bt

    def getPartitionData(self) -> dict[str, Any]:
        """Get the partition data."""
        datadict = {}
        datadict["state"]  = AlPanelStatus.TRIGGERED.name.lower() if self.SirenActive else self.PanelStateData.name.lower()
        datadict["ready"]  = self.PanelReady
        datadict["memory"] = self.PanelAlertInMemory
        datadict["bypass"] = self.PanelBypass
        datadict["alarm"]  = AlAlarmType.INTRUDER.name.lower() if self.PanelIntruderStatus else self.PanelAlarmStatus.name.lower()
        return datadict

    def determineTrouble(self) -> str:
        """Determine the trouble status."""
        if self.PanelTroubleStatus != AlTroubleType.NONE:
            return self.PanelTroubleStatus.name.lower()
        if self.PartitionGeneralTrouble:
            return AlTroubleType.GENERAL.name.lower()
        return AlTroubleType.NONE.name.lower()

    def getPanelData(self) -> dict[str, Any]:
        """Get the panel data."""
        datadict = {}
        datadict["trouble"] = self.determineTrouble()
        datadict["tamper"]  = self.PanelTamper
        datadict["battery_level"] = 0 if self.PanelBatteryTrouble else 100
        return datadict

    def UpdatePanelState(self, et: EVENT_TYPE, sensor: AlSensorDeviceHelper | None = None):
        """Update the panel state based on an event type."""
        old_alarm_status = self.PanelAlarmStatus
        # I have split the known EventTypes in to those that affect Tamper, Intruder, Panel Alarm, Panel Battery and Panel Trouble
        # So just because the panel sets battery trouble, that doesnt mean it could also set others as well.
        #     These can be set by subsequent messages
        #     Each has a dict of Events to set it "_t" and a list of Events to reset it "_r"

        # Update tamper status
        #self.PanelTamper = (et == EVENT_TYPE.TAMPER_PANEL)
        if et in pmPanelTamperType_t:
            self.PanelTamper = True      # event in trigger set
        if et in pmPanelTamperType_r:
            self.PanelTamper = False     # event in restore set

        # Update intruder status
        #self.PanelIntruderStatus = bool(et in pmPanelIntruderType_t)
        if et in pmPanelIntruderType_t:
            self.PanelIntruderStatus = True      # event in trigger set
        if et in pmPanelIntruderType_r:
            self.PanelIntruderStatus = False     # event in restore set

        # Update alarm status
        #self.PanelAlarmStatus = pmPanelAlarmType_t[et] if et in pmPanelAlarmType_t else AlAlarmType.NONE
        if et in pmPanelAlarmType_t:
            self.PanelAlarmStatus = pmPanelAlarmType_t[et]      # event in trigger set
        if et in pmPanelAlarmType_r:
            self.PanelAlarmStatus = AlAlarmType.NONE            # event in restore set. When the panel is disarmed and it is FIRE etc, then it sends an ALARM_CANCEL Event

        # Update alarm panel battery state
        if et in pmPanelBatteryType_t:
            self.PanelBatteryTrouble = True             # event in trigger set
        if et in pmPanelBatteryType_r:
            self.PanelBatteryTrouble = False            # event in restore set

        # Update trouble status
        #self.PanelTroubleStatus = pmPanelTroubleType_t[et] if et in pmPanelTroubleType_t else AlTroubleType.NONE
        if et in pmPanelTroubleType_t:
            self.PanelTroubleStatus = pmPanelTroubleType_t[et]       # event in trigger set
        if et in pmPanelTroubleType_r:
            self.PanelTroubleStatus = AlTroubleType.NONE                                       # event in restore set

        # no clauses as if siren gets true again then keep updating self.SirenActive sensor
        if self.SirenActive:
            if not self.PanelIntruderStatus:  # Cancel Alarm
                # Intruder: cancel siren and the siren has been triggered
                self.SirenActive = False
                self.SirenActiveDeviceTrigger = None
                if self.isTimerRunning(self._TimerName.INTRUDER):
                    self.stopIntruderTimer()
                log.debug("[UpdatePanelState]            ******************** Intruder Cancelled ****************")

            #elif et not in pmPanelIgnoreSet:  # Alarm Timed Out ????
            #    # Siren has been active but it is no longer active (probably timed out and has then been disarmed)
            #    self.SirenActive = False
            #    self.SirenActiveDeviceTrigger = None
            #    self.PanelIntruderStatus = False
            #    log.debug("[UpdatePanelState]            ******************** Event not in Ignore Set, Cancelling Alarm Indication ****************")
        elif self.PanelStateSourceData is not None and self.PanelIntruderStatus:
            armed = pmPanelArmedStatus[self.PanelStateSourceData].armed
            entry = pmPanelArmedStatus[self.PanelStateSourceData].entry
            if armed is not None and armed and not entry:
                self.SirenActive = True
                self.SirenActiveDeviceTrigger = None if sensor is None else sensor
                self.startIntruderTimer()
                log.debug("[UpdatePanelState]            ******************** Intruder Active *******************")

        alarm_timer_list = [AlAlarmType.FIRE, AlAlarmType.EMERGENCY, AlAlarmType.PANIC, AlAlarmType.GAS, AlAlarmType.FLOOD]

        if self.PanelAlarmStatus in alarm_timer_list:
            if old_alarm_status != self.PanelAlarmStatus:
                self.startAlarmTimer(restart_if_running=True)
        else:
            self.stopAlarmTimer()

#        if self.isTimerRunning(ALARM) and self.PanelAlarmStatus in alarm_timer_list and old_alarm_status in alarm_timer_list and old_alarm_status != self.PanelAlarmStatus:
#            # timer already running, oldalarm and newalarm in the list and they are different, there's been a change
#            # kill and restart the timer task
#            self.startAlarmTimer(True)
#        elif not self.isTimerRunning(ALARM) and self.PanelAlarmStatus in alarm_timer_list:
#            self.startAlarmTimer()
#        elif self.isTimerRunning(ALARM) and self.PanelAlarmStatus == AlAlarmType.NONE:
#            self.stopAlarmTimer()

        log.debug(
            "[UpdatePanelState] eventType=%s %s %s %s %s %s %s",
            et,
            self.PanelTamper,
            self.PanelAlarmStatus,
            self.PanelTroubleStatus,
            self.SirenActive,
            self.PanelIntruderStatus,
            self.PartitionGeneralTrouble,
        )

    def UpdatePartition(self, sysStatus: int, sysFlags: int, PanelMode: AlPanelMode) -> AlPanelEventData | None:
        """Update the partition state based on system status and flags."""

        retval = None

        if sysStatus in pmPanelArmedStatus:
            self.PanelStateSourceData = sysStatus
            disarmed = pmPanelArmedStatus[self.PanelStateSourceData].disarmed
            armed    = pmPanelArmedStatus[self.PanelStateSourceData].armed
            entry    = pmPanelArmedStatus[self.PanelStateSourceData].entry
            self.PanelStateData = pmPanelArmedStatus[self.PanelStateSourceData].state
            if pmPanelArmedStatus[self.PanelStateSourceData].eventmapping != EVENT_TYPE.NOT_DEFINED:
                #log.debug(f"[UpdatePartition]             self.PanelStateData is {self.PanelStateData}      using event mapping {pmPanelArmedStatus[sysStatus].eventmapping} for event data")
                retval = AlPanelEventData(name = 0, action = pmPanelArmedStatus[self.PanelStateSourceData].eventmapping.value) # use partiton set to -1 as a dummy
        else:
            log.debug("[UpdatePartition]             Unknown state 0x%s, assuming Panel state of Unknown", hexify(sysStatus))
            disarmed = None
            armed = None
            entry = False
            self.PanelStateData = AlPanelStatus.UNKNOWN  # UNKNOWN
            self.PanelStateSourceData = None

        if PanelMode == AlPanelMode.DOWNLOAD:
            self.PanelStateData = AlPanelStatus.DOWNLOADING  # Downloading
            self.PanelStateSourceData = None

        log.debug(f"[UpdatePartition]             sysFlags=0x{hexify(sysFlags)}    sysStatus=0x{hexify(sysStatus)}    log: {self.PanelStateData.name}, {disarmed=}  {armed=}")

        self.PanelReady = sysFlags & 0x01 != 0
        self.PanelAlertInMemory = sysFlags & 0x02 != 0

        if sysFlags & 0x04 != 0:                   # Trouble
            if not self.PartitionGeneralTrouble:
                log.debug("[UpdatePartition]                 Panel Indicates Trouble Set")
                self.PartitionGeneralTrouble = True
        else:
            if self.PartitionGeneralTrouble:
                log.debug("[UpdatePartition]                 Panel Indicates Trouble Cleared")
            self.PartitionGeneralTrouble = False

        self.PanelBypass = sysFlags & 0x08 != 0

        if sysFlags & 0x10 != 0:
            log.debug("[UpdatePartition]                 sysFlags bit 4 set --> Should be last 10 seconds of entry/exit")

        #if sysFlags & 0x20 != 0:
        #    log.debug(f"[UpdatePartition]                 sysFlags bit 5 set --> Should be Zone Event")

        if sysFlags & 0x40 != 0:
            log.debug("[UpdatePartition]                 sysFlags bit 6 set --> Should be Status Changed")

        #if sysFlags & 0x10 != 0:  # last 10 seconds of entry/exit
        #    self.PanelArmed = sarm == "Arming"
        #else:
        #     self.PanelArmed = sarm == "Armed"
        panel_alarm_event = sysFlags & 0x80 != 0

        if PanelMode not in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
            # if the system status has the panel armed and there has been an alarm event, assume that the alarm is sounding
            #        and that the sensor that triggered it isn't an entry delay
            #   Normally this would only be directly available in Powerlink mode with A7 messages, but an assumption is made here
            if armed is not None and armed and not entry and panel_alarm_event:
                log.debug("[UpdatePartition]                  Alarm Event Assumed while in Standard Mode")
                log.debug("[UpdatePartition]            ******************** Alarm Active *******************")
                # Alarm Event
                self.SirenActive = True
                self.SirenActiveDeviceTrigger = None

        # Clear any alarm event if the panel alarm has been triggered before (while armed) but now that the panel is disarmed (in all modes)
        if disarmed is not None and disarmed:
            if self.SirenActive:
                log.debug("[UpdatePartition] ******************** Alarm Not Sounding (Disarmed) ****************")
                self.SirenActive = False
                self.SirenActiveDeviceTrigger = None
                self.PanelIntruderStatus = False

        return retval
