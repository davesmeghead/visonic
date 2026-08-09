"""Process the messages from the panel."""

# ruff: noqa: G004

from datetime import datetime
import logging
from typing import Any

from .py_const import TIME_INTERVAL_ERROR, notknown
from .py_enum import (
    RAW,
    AlPanelStatus,
    B0SubType,
    IndexName,
    MessagePriority,
    PanelSetting,
    Send,
)
from .py_panel_settings import pmPanelSettingCodes
from .py_utils import b2i, convert_bytearray, get_local_time, hexify, toString
from .py_visonic_sequencer import Sequencer

log = logging.getLogger(__name__)

###################################################################################
##########################  Data Driven Message Decode ############################
###################################################################################


class MessageHandlingBase(Sequencer):
    """Message Handling. These are the helper functions and variables."""

    def __init__(self, loop, force_standard_mode : bool, disable_all_commands : bool, download_code : str, user_code_slot: int, logger = None) -> None:
        """Initialize class."""
        super().__init__(loop=loop, force_standard_mode=force_standard_mode, disable_all_commands=disable_all_commands, download_code=download_code, user_code_slot=user_code_slot, logger=logger)
        self.loopbackCounter : int = 0
        self._unknown_message_log : dict[str, Any] = {}

    def _reset_full(self):
        """Reset all non-permanent variables."""
        super()._reset_full()
        self.ModelType : int | None = None
        # Download block retry count (this is for individual 3F download failures)
        self.pmDownloadRetryCount = 0
        self.eventCount = 0
        self.enrolled_old : int = 0  # means nothing enrolled
        # These are used in the A5 message to reduce processing but mainly to reduce the amount of callbacks in to HA when nothing changes

        self.pmForceArmSetInPanel : bool = False          # If the Panel is using "Force Arm" then sensors may be automatically armed and bypassed by the panel when it is armed and disarmed
        self.B0_PANEL_LOG_Counter : int = 0
        self.logEventList : list[str] = []
        self.B0_temp : dict[int | B0SubType, bytearray] = {}
        self.beezero_024B_sensorcount : int | None = None
        self.builderMessage : dict[PanelSetting, Any] = {}  # Temporary variable
        self.builderData : dict[PanelSetting, Any] = {}     # Temporary variable
        self._reset_powerlink_counter()

    def _checkallsame(self, val, b: bytearray) -> list[int]:
        return [i for i, byte in enumerate(b) if byte != val]

    def _do_sensor_update(self, data : bytearray, func : str, msg : str, startzone : int = 0, endzone : int = 32):
        endzone_min = min(endzone, self._get_panel_capability(IndexName.ZONES))
        no_of_bytes = (1 + ((endzone_min - startzone - 1) // 8)) if endzone_min > startzone else None
        if no_of_bytes is not None and len(data) >= no_of_bytes:
            val = b2i(data)
            log.debug(f"{msg} : {val:032b}       startzone={startzone}    {f'corrected endzone={endzone_min-1}' if endzone_min != endzone else f'endzone={endzone_min-1}'}      {no_of_bytes=}")
            for i in range(startzone, endzone_min):
                if i in self.SensorList:
                    sf = getattr(self.SensorList[i], func)
                    if sf is not None:
                        sf(bool(val & (1 << (i-startzone)) != 0))
        else:
            log.debug(f"{msg} : len(data)={len(data)}  data={toString(data)} not processed    {startzone=}    {endzone=}   {endzone_min=}   {no_of_bytes=}")

    def _check_unknown(self, message : str, key : str, value):
        # {notknown}
        if key in self._unknown_message_log:
            if self._unknown_message_log[key] != value:
                log.debug(f"{notknown} {message} {key=}  old={self._unknown_message_log[key]}   new={value}")
                self._unknown_message_log[key] = value
        else:
            self._unknown_message_log[key] = value

    def _set_time_in_panel(self, paneltime: datetime):
        """Set the time in the panel."""
        # To set the time in the panel we need to be in DOWNLOAD Mode
        #     One user has lots of sensors and the messages can get a bit backed up, causing delays in getting and sending messages
        #     This is not a problem, apart from getting and setting the time, so:
        #          I have maded the messages to set the time IMMEDIATE so they jump the queue
        #          I have put a counter on the diff test, the time difference has to exceed the TIME_INTERVAL_ERROR for 3 times in a row.
        t = get_local_time()
        settime = self.AutoSyncTime  # should we sync time between the HA and the Alarm Panel
        if paneltime is not None and t.year > 2000:
            # Regardless of whether we autosync time, calculate the time difference
            self.Panel_Integration_Time_Difference = t - paneltime
            d = self.Panel_Integration_Time_Difference.total_seconds()
            log.debug(f"[_set_time_in_panel]      Local time is {t}      time difference {d} seconds")
            if abs(d) < TIME_INTERVAL_ERROR:
                log.debug(f"[_set_time_in_panel]      Not Correcting Time in Panel as less than {TIME_INTERVAL_ERROR} seconds difference.")
                settime = False
                self.Panel_Integration_Time_Counter = 0
            #else:
            #    log.debug("[_set_time_in_panel]      Correcting Time in Panel.")
        if settime:
            self.Panel_Integration_Time_Counter += 1
            if self.Panel_Integration_Time_Counter > 2:  # The time difference has to exceed the TIME_INTERVAL_ERROR for 3 times in a row.
                self.Panel_Integration_Time_Counter = 0
                log.debug(f"[_set_time_in_panel]      Setting time in panel to {t}     paneltime is currently {paneltime}")
                time_pdu = bytearray([t.second + 1, t.minute, t.hour, t.day, t.month, t.year - 2000])   # add about 1 seconds on as it takes over 1 to get to the panel to set it
                # Set these as IMMEDIATE to get them to the panel asap (so the time is set asap to synchronise panel and local time)
                self.add_message_to_send_queue(Send.DOWNLOAD_TIME, priority = MessagePriority.IMMEDIATE, options=[ [3, convert_bytearray(self.DownloadCode)] ])
                self.add_message_to_send_queue(Send.SETTIME, priority = MessagePriority.IMMEDIATE, options=[ [3, time_pdu] ])
                self.add_message_to_send_queue(Send.EXIT, priority = MessagePriority.IMMEDIATE)
            else:
                log.debug(f"[_set_time_in_panel]      Not Correcting Time in Panel as only exceeded {TIME_INTERVAL_ERROR} seconds difference for {self.Panel_Integration_Time_Counter} times in a row.")

    def _update_panel_setting(self, key, length, datasize, data, display : bool = False, msg : str = "") -> bool:

        psc = pmPanelSettingCodes[key]
        s = psc.tostring(self.PanelSettings[key])              # Save the data before the update

        if psc.item is not None:
            if len(data) > psc.item:
                self.PanelSettings[key] = data[psc.item]
        elif datasize == RAW.BITS.value:
            if len(self.PanelSettings[key]) < length * 8 :
                # replace as current length less than the new data
                #log.debug(f"[_update_panel_setting]              {key=}  replace")
                self.PanelSettings[key] = []
                for i in range(length):
                    for j in range(8):  # 8 bits in a byte
                        self.PanelSettings[key].append((data[i] & (1 << j)) != 0)
            else:
                # overwrite as current length is same as or more than new data
                #log.debug(f"[_update_panel_setting]              {key=}  overwrite")
                for i in range(length):
                    for j in range(8):  # 8 bits in a byte
                        self.PanelSettings[key][(i*8)+j] = (data[i] & (1 << j)) != 0
        else:
            self.PanelSettings[key] = data

        v = pmPanelSettingCodes[key].tostring(self.PanelSettings[key])
        if display:
            if len(s) > 100 or len(v) > 100:
                if s != v:
                    log.debug(f"[_update_panel_setting]              changed=True      {key=}   ({msg})")
                    log.debug(f"[_update_panel_setting]                        replacing {s}")
                    log.debug(f"[_update_panel_setting]                        with      {v}")
                else:
                    log.debug(f"[_update_panel_setting]              changed=False     {key=}   ({msg})    data is {v}")
            elif s != v:
                log.debug(f"[_update_panel_setting]              changed=True      {key=}   ({msg})    replacing {s}  with {v}")
            else:
                log.debug(f"[_update_panel_setting]              changed=False     {key=}   ({msg})    data is {v}")
        else:
            log.debug(f"[_update_panel_setting]              changed={s != v}     {key=}   ({msg})")
        return s != v


    def _updatePartitionStatus(self, partition: int, sys_status: int, sysFlags: int, sys_status_2: int, unknown4: int):
        """This function updates the panel state when B0 message data is received."""
        piu = self.get_partitions_in_use()
        if sysFlags & 0x80 != 0:  # This seems to be a "partition enabled" indication
            log.debug(f"[_updatePartitionStatus]        Partition={partition} with data sys_status=0x{hexify(sys_status)}  sysFlags=0x{hexify(sysFlags)}  X=0x{hexify(sys_status_2)}  Y=0x{hexify(unknown4)}")
            for i in range(4,8):
                v = bool((sys_status & (0x01 << i)) != 0)
                if v:
                    log.debug(f"[_updatePartitionStatus]             {notknown}: sys_status bit {i} is set and I don't know what it means")

            # I believe that bit 0 of sys_status_2 represents the "Instant" indication for armed home and armed away (and maybe disarm etc) i.e. all the PanelStateData values above 0x0F
            sys_status = (sys_status & 0x0F) | (( sys_status_2 << 4 ) & 0x10 )

            old_panel_state = self.PartitionState[partition].PanelStateData
            # Mask off the top bit as seems to be used to indicate overall validity
            s = self.PartitionState[partition].UpdatePartition(sysStatus=sys_status, sysFlags=sysFlags & 0x7F, PanelMode=self.PanelMode)  # does not set partition in return value
            if s is not None:
                if self.get_partitions_in_use() is not None:   # we have partitions so add it in as an attribute
                    s.set_partition(partition)
                self.add_panel_event_data(s)

            new_panel_state = self.PartitionState[partition].PanelStateData
            if new_panel_state == AlPanelStatus.DISARMED and new_panel_state != old_panel_state:
                # Panel state is Disarmed and it has just changed
                self.B0_Wanted.add(B0SubType.ZONE_BYPASS)
                #self.add_message_to_send_queue(Send.BYPASSTAT)

            if sysFlags & 0x20 != 0:  # Zone Event
                log.debug(f"[_updatePartitionStatus]                 It also claims to have a zone event with data (hex) {hex(sys_status_2)} possibly with this data {hex(unknown4)}")
            if sysFlags & 0x40 != 0:
                log.debug(f"[_updatePartitionStatus]                 It also claims to have a status changed event with data (hex) {hex(sys_status_2)} possibly with this data {hex(unknown4)}")
                #self._process_zone_event(eventDevice=eventDevice, event_type=event_type)
        elif piu is not None and partition in piu:
            log.debug(f"[_updatePartitionStatus]        Partition={partition}  Not Enabled but it is in the current Partition set {piu}, that's a problem")
        else:
            log.debug(f"[_updatePartitionStatus]        Partition={partition}  Not Enabled")

    def _process_switch_state_update(self, switch_status, total = 16):
        # Examine Switch status
        for i in range(total):
            status = switch_status & (1 << i)
            if i in self.SwitchList:
                # INTERFACE : use this to set Switch status
                oldstate = self.SwitchList[i].state
                self.SwitchList[i].state = bool(status)
                # Check to see if the state has changed
                if (oldstate and not self.SwitchList[i].state) or (not oldstate and self.SwitchList[i].state):
                    log.debug(f"[_process_switch_state_update]      Switch device {i} changed to {self.SwitchList[i].state} ({status})")
                    self.SwitchList[i].notify()

    def _string_from_raw_bits(self, d, s, m) -> str:
        device_str = ""
        for i in range(s):
            v = (d >> i) & 0x01
            if v == 1:
                log.debug(f"{m} {i}")
                device_str = f"{device_str},{i:0>2}"
        return device_str[1:]   # miss the first comma

    def _list_from_raw_bits(self, d, s) -> list:
        retval = []
        for i in range(s):
            v = (d >> i) & 0x01
            if v == 1:
                #log.debug(f"[_process_chunk] Found an Enrolled PowerMaster {m} {i}")
                retval.append(i)
        return retval   # miss the first comma
