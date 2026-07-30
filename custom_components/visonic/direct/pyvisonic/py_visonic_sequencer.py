"""Visonic Sequencer - Managet the interaction with the panel at a low level, fast response."""

# ruff: noqa: G004, C901, BLE001, FURB171

import asyncio
from datetime import timedelta
from enum import IntEnum
import logging
import random
import traceback

from .py_const import (
    DISABLE_TEXT,
    DOWNLOAD_RETRY_COUNT,
    DOWNLOAD_RETRY_DELAY,
    DOWNLOAD_TIMEOUT,
    EPROM_DOWNLOAD_ALL,
    LAST_RECEIVE_DATA_TIMEOUT,
    MAX_TIME_BETWEEN_POWERLINK_ALIVE,
    NO_RECEIVE_DATA_TIMEOUT,
    OBFUS,
    POWERLINK_IMALIVE_RETRY_DELAY,
    IMAGE_TRANSFER_TIMEOUT,
    POWERMASTER_CHECK_TIME_INTERVAL,
    POWERMAX_CHECK_TIME_INTERVAL,
    STANDARD_STATUS_RETRY_DELAY,
    THREE_SECONDS,
    WATCHDOG_MAXIMUM_EVENTS,
    WATCHDOG_TIMEOUT,
)
from .py_enum import (
    CFG,
    EPROM,
    PANEL_STATUS,
    AlCondition,
    AlPanelMode,
    AlPanelStatus,
    AlTerminationType,
    B0SubType,
    IndexName,
    MessagePriority,
    Packet,
    PanelErrorStates,
    PanelSetting,
    Receive,
    Send,
)
from .py_panel_settings import pmPanelSettingCodes, pmZoneName
from .py_panel_type_data import pmPanelConfig
from .py_sensor_image import AlImageManager
from .py_switch import AlSwitchDeviceHelper
from .py_types_sending import (
    B0_SendMessageTuple,
    VisonicListEntry,
    pmSendMsgB0,
    pmSendMsgB0_reverseLookup,
)
from .py_utils import convert_bytearray, get_local_time, get_utc_time, hexify, toString
from .py_visonic_despatcher import Despatcher

log = logging.getLogger(__name__)


class SequencerType(IntEnum):
    """These are the sequencer states."""
    Invalid                 = -1
    Reset                   = 1
    LookForPowerlinkBridge  = 2
    InitialisePanel         = 3
    WaitingForPanelDetails  = 4
    AimingForStandard       = 5
    DoingStandard           = 6
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
        """Convert to string."""
        return str(self.name)

class Sequencer(Despatcher):
    """Sequencer. Coordinate the interaction with the panel at a low level."""

    def __init__(self, loop, force_standard_mode : bool, disable_all_commands : bool, download_code : str, user_code_slot: int, logger = None) -> None:
        """Initialize class."""
        super().__init__(loop=loop, force_standard_mode=force_standard_mode, disable_all_commands=disable_all_commands, download_code=download_code, user_code_slot=user_code_slot, logger=logger)
        self._sequencer_task = None
        self.WatchdogTimeoutCounter : int = 0
        self.WatchdogTimeoutPastDay : int = 0
        # Loopback capability added. Connect Rx and Tx together without connecting to the panel
        self.loopbackTest : bool = False
        self.pmForceDownloadByEPROM : bool = False     # For PowerMaster panels, try the B0 messages first and if they dont work in 20 seconds then force EPROM download
         # take off X seconds so the first command goes through immediately
        self._last_send_download_eprom = get_utc_time() - timedelta(seconds=DOWNLOAD_RETRY_DELAY + 100)
        self._first_send_download_eprom = get_utc_time()
        self._paused_state_save = AlPanelMode.UNKNOWN
        self.image_manager: AlImageManager = AlImageManager()
        self.ignoreF4DataMessages : bool = True
        self.image_ignore: set[int] = set()

    def _reset_full(self):
        """Reset all non-permanent variables."""
        super()._reset_full()
        ########################################################################
        # Global Variables that define the overall panel status
        ########################################################################

        # Set when the panel details have been received i.e. a 3C message
        self.pmGotPanelDetails : bool = False

        # Time difference between Panel and Integration
        self.Panel_Integration_Time_Difference = None
        self.Panel_Integration_Time_Counter = 0

        # When we are downloading the EPROM settings and finished parsing them and setting up the system.
        #   There should be no user (from Home Assistant for example) interaction when self.pmDownloadMode is True
        self.pmDownloadInProgress = False
        self.myDownloadList = []
        self.DownloadCounter = 0
        self.powerlink_counter = 0

        # Set when we receive a STOP from the panel, indicating that the EPROM data has finished downloading
        self.pmDownloadComplete = False

        # When to stop trying to download the EPROM
        self._stop_trying_download = False

        # When trying to connect in powerlink from the timer loop, this allows the receipt of a powerlink ack to trigger a MSG_RESTORE
        self.allowAckToTriggerRestore : bool = False
        self.receivedPowerlinkAcknowledge : bool = False

        # keep alive counter for the timer
        self._reset_keep_alive_messages()  # only used in _sequencer
        # The last sent message
        self._clear_receive_response_list()

    def _reset_connection(self):
        """Reset the variables needed to make a new connection."""
        super()._reset_connection()
        self._panel_reset_event : bool = False
        self.PanelWantsToEnrol : bool = False
        self.UnexpectedPanelKeepAlive : bool = False
        self.TimeoutReceived : bool = False
        self.ExitReceived : bool = False
        self.DownloadRetryReceived : bool = False
        self.AccessDeniedReceived : bool = False
        self.AccessDeniedMessage: VisonicListEntry | None = None
        self.gotBeeZeroInvalidCommand : bool = False
        # Current F4 jpg image
        #    Leave these here for the time being as they might be needed in the sequencer
        self.image_manager: AlImageManager = AlImageManager()
        self.ignoreF4DataMessages : bool = True
        self.image_ignore: set[int] = set()

    def _shutdown(self):
        """Shutdown the connection to the panel."""
        super()._shutdown()
        # Set that the transport connection to the panel is invalid.
        self._stop_sequencer()

    def _stop_sequencer(self):
        """Stop the sequencer."""
        if self._sequencer_task is not None:
            try:
                log.debug("[_stop_sequencer] Cancelling _sequencer")
                self._sequencer_task.cancel()
            except Exception as ex:
                tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                log.error("[_stop_sequencer] Visonic Executor loop has caused an exception\n%s", tb_str)
            self._sequencer_task = None

    def _start_sequencer(self):
        if self._sequencer_task is None:
            # Start sequencer the first time the transport is set, after that don't
            self._sequencer_task = self.loop.create_task(self._sequencer(), name="pyvisonic_sequencer")
            log.debug(f"[Connection]  Start Sequencer: main loop is {self._on_main_loop()}")

    def start(self):
        """Start the internal processing e.g. despatcher/sequencer."""
        self._start_sequencer()

    def pause(self):
        """Pause the internal processing e.g. despatcher/sequencer."""
        # save state
        self._paused_state_save = self.PanelMode
        self.PanelMode = AlPanelMode.PAUSED
        self._clearPanelErrorMessages()
        self._pause_event.clear()
        log.debug("[Connection]  Protocol Paused")

    def resume(self):
        """Resume the internal processing e.g. despatcher/sequencer."""
        # Do a few resets before resuming the event
        self.PanelMode = self._paused_state_save
        self._reset_watchdog_timeout()
        self._reset_keep_alive_messages()
        self._clear_receive_response_list()
        self._empty_send_queue(priority = MessagePriority.DELETE_ALL) # empty the list
        # Send Exit and Stop to the panel. This should quit download mode.
        log.debug("[Connection]  Protocol Resumed")
        self._pause_event.set()

    def _clearPanelErrorMessages(self):
        self.AccessDeniedReceived = False
        self.AccessDeniedMessage: VisonicListEntry | None = None
        self.ExitReceived = False
        self.DownloadRetryReceived = False
        self.TimeoutReceived = False
        self.gotBeeZeroInvalidCommand = False

    # This function needs to be called within the timeout to reset the timer period
    def _reset_powerlink_counter(self):
        """Reset the powerlink counter."""
        self.powerlink_counter = 0

    # There are 2 Tasks that manage the panel (despatcher and sequencer):
    #  This is the sequencer, it manages the state of the connection with the panel
    # Function to send I'm Alive and status request messages to the panel
    # This is also a timeout function for a watchdog. If we are in powerlink, we should get a AB 03 message every 20 to 30 seconds
    #    If we haven't got one in the timeout period then reset the send queues and state and then call a MSG_RESTORE
    # In standard mode, this command asks the panel for a status
    async def _sequencer(self):

        # Variables declared here so that the functions get the reference to them
        self.checkAllPanelData = True

        _sequencer_state = SequencerType.LookForPowerlinkBridge
        _sequencer_state_previous = SequencerType.Invalid

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
        _sent_startup_success = False
        log_sensor_state_counter = 0
        _last_b0_wanted_request_time = get_local_time()
        lastrecv = None
        delay_loops = 0
        a_day = 24 * 60 * 60  # seconds in a day

        old_panel_mode = self.PanelMode
        # Note that PANEL_STATE_1 is in all of them, to get it every time
        b0_periodic_update_list: list[set[B0SubType]] = [
             { B0SubType.SENSOR_ENROL, B0SubType.ZONE_NAMES, B0SubType.ZONE_TYPES, B0SubType.DEVICE_TYPES, B0SubType.PANEL_STATE_1, B0SubType.PANEL_STATE_4 },
             { B0SubType.TAMPER_ALERT, B0SubType.WIRELESS_DEV_MISSING, B0SubType.WIRELESS_DEV_INACTIVE, B0SubType.WIRELESS_DEV_ONEWAY, B0SubType.PANEL_STATE_1, B0SubType.PANEL_STATE_5 },
             { B0SubType.ZONE_TEMPERATURE, B0SubType.TAMPER_ACTIVITY, B0SubType.ZONE_OPENCLOSE, B0SubType.PANEL_STATE_1, B0SubType.PANEL_STATE_4 },                  # B0SubType.ZONE_LUX,  LUX seems to cause problems with my PM30
             { B0SubType.WIRED_STATUS_1, B0SubType.WIRED_STATUS_2, B0SubType.WIRED_DEVICES, B0SubType.PANEL_STATE_1, B0SubType.PANEL_STATE_5 }
        ]

        def _resetPanelInterface():
            """This should re-initialise the panel."""
            log.debug(f"[_resetPanelInterface]   ************************************* Reset Panel Interface **************************************  {self.PanelMode=}")

            # Clear the send list and empty the expected response list
            self._clear_receive_response_list()
            self._empty_send_queue(priority = MessagePriority.DELETE_ALL) # empty the list

            # Send Exit and Stop to the panel. This should quit download mode.
            self.add_message_to_send_queue(Send.EXIT)
            self.add_message_to_send_queue(Send.STOP)

            if not self.PowerLinkBridgeConnected and self.pmInitSupportedByPanel:
                self.add_message_to_send_queue(Send.INIT)

        def _requestMissingPanelConfig(missing : set[PanelSetting]):
            m = [
                pmPanelSettingCodes[a].PMasterB035Panel
                for a in missing
                if a in pmPanelSettingCodes and pmPanelSettingCodes[a].PMasterB035Panel is not None
            ]
            if len(m) > 0:
                log.debug(f"[_requestMissingPanelConfig]      Type 35 Wanting {m}")
                tmp = bytearray()
                for a in m:
                    y1, y2 = (a & 0xFFFF).to_bytes(2, "little")
                    tmp = tmp + bytearray([y1, y2])
                s = self._create_B0_35_Data_Request(strlist = toString(tmp))
                self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)
            else:
                m = [
                    pmPanelSettingCodes[a].PMasterB042Panel
                    for a in missing
                    if a in pmPanelSettingCodes and pmPanelSettingCodes[a].PMasterB042Panel is not None
                ]
                if len(m) > 0:
                    log.debug(f"[_requestMissingPanelConfig]      Type 42 Wanting {m}")
                    tmp = bytearray()
                    for a in m:
                        y1, y2 = (a & 0xFFFF).to_bytes(2, "little")
                        tmp = tmp + bytearray([y1, y2])
                    s = self._create_B0_42_Data_Request(strlist = toString(tmp))
                    self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)
                else:
                    m = [
                        pmPanelSettingCodes[a].PMasterB0Mess
                        for a in missing
                        if a in pmPanelSettingCodes and pmPanelSettingCodes[a].PMasterB0Mess is not None
                    ]
                    if len(m) > 0:
                        log.debug(f"[_requestMissingPanelConfig]      Wanting {m}")
                        tmp = [pmSendMsgB0[i].data if i in pmSendMsgB0 else i for i in m]      # m can contain State enumerations or the integer of the message subtype
                        s = self._create_B0_Data_Request(taglist = set(tmp))
                        self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)

        def _gotoStandardModeStopDownload():
            if not self.PowerLinkBridgeConnected:  # Should not be in this function when this is True but use it anyway
                if self.DisableAllCommands:
                    log.debug("[Standard Mode] Entering MINIMAL ONLY Mode")
                    self.PanelMode = AlPanelMode.MINIMAL_ONLY
                elif self.pmDownloadComplete and not self.ForceStandardMode and self._is_valid_user_code():
                    log.debug("[Standard Mode] Entering Standard Plus Mode as we got the pin codes from the EPROM (You can still manually Enrol your Panel)")
                    self.PanelMode = AlPanelMode.STANDARD_PLUS
                else:
                    log.debug("[Standard Mode] Entering Standard Mode")
                    self.PanelMode = AlPanelMode.STANDARD
                    self.ForceStandardMode = True
            # Stop download mode
            self.pmDownloadComplete = False
            self.pmDownloadMode = False
            self._triggered_download = False
            self._stop_trying_download = True
            self.send_panel_update(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
            if not self.PowerLinkBridgeConnected and self.DisableAllCommands:
                # Clear the send list and empty the expected response list
                self._clear_receive_response_list()
                self._empty_send_queue(priority = MessagePriority.ACK)
            else:
                _resetPanelInterface()
            self.add_message_to_send_queue(Send.STATUS_SEN)

        # Process the panel error messages, in order: Access Denied, Exit, DownloadRetry and Timeout
        def processPanelErrorMessages() -> PanelErrorStates:

            if self._despatcher_exception:
                self._despatcher_exception = False
                return PanelErrorStates.DespatcherException

            # Make sure that the Access Denied is processed first
            if self.AccessDeniedReceived:
                log.debug("[_sequencer] Access Denied")
                self.AccessDeniedReceived = False
                if self.AccessDeniedMessage is not None and self.AccessDeniedMessage.command is not None:
                    last_command_data = self.AccessDeniedMessage.command.data
                    self.AccessDeniedMessage = None
                    if last_command_data is not None:
                        log.debug(f"[_sequencer]     AccessDenied last command {toString(last_command_data[:3] if OBFUS else last_command_data)}")
                        # Check download first, then pin, then stop
                        if last_command_data[0] == 0x24 or last_command_data[0] == 0x09:
                            log.debug("[_sequencer]           Got an Access Denied and we have sent a Bump or a Download command to the Panel")
                            return PanelErrorStates.AccessDeniedDownload
                        if last_command_data[0] != 0xAB and last_command_data[0] != 0xA5 and last_command_data[0] & 0xA0 == 0xA0:  # this will match A0, A1, A2, A3 etc but not Receive.POWERLINK or A5
                            log.debug("[_sequencer]           Attempt to send a command message to the panel that has been denied, wrong pin code used")
                            # INTERFACE : tell user that wrong pin has been used
                            return PanelErrorStates.AccessDeniedPin
                        if last_command_data[0] == 0x0B:  # Stop
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

        def toStringList(ll) -> list[B0_SendMessageTuple]:
            return {pmSendMsgB0_reverseLookup[i].data if isinstance(i, int) and i in pmSendMsgB0_reverseLookup else i for i in ll}

        def reset_local():

            self.checkAllPanelData = True    # pylint: disable=unused-variable

            _sequencer_state = SequencerType.InitialisePanel
            _sequencer_state_previous = SequencerType.Invalid

            _last_b0_wanted_request_time = get_local_time()
            _my_panel_state_trigger_count = 5
            _sent_startup_success = False
            # declare a list and fill it with zeroes
            watchdog_list = [0] * WATCHDOG_MAXIMUM_EVENTS     # noqa: F841  pylint: disable=unused-variable
            # The starting point doesn't really matter
            watchdog_pos = WATCHDOG_MAXIMUM_EVENTS - 1        # noqa: F841  pylint: disable=unused-variable

            # create a generic counter that gets reset every state change, so it can be used in a single state
            counter = 0                                       # noqa: F841  pylint: disable=unused-variable
            no_data_received_counter = 0                      # noqa: F841  pylint: disable=unused-variable
            no_packet_received_counter = 0                    # noqa: F841  pylint: disable=unused-variable
            log_sensor_state_counter = 0                      # noqa: F841  pylint: disable=unused-variable
            lastrecv = None                                   # noqa: F841  pylint: disable=unused-variable
            delay_loops = 0                                   # noqa: F841  pylint: disable=unused-variable

        def updateSensorNamesAndTypes(force = False) -> bool:
            """Retrieve Zone Names and Zone Types if needed."""
            # This function checks to determine if the Zone Names and Zone Types have been retrieved and if not it gets them
            retval = None
            if self.PanelType is not None and 0 <= self.PanelType <= 16:
                retval = False
                #zone_count = self._get_panel_capability(IndexName.ZONES)
                zone_count = self._get_panel_capability(IndexName.ZONES)
                if self.is_power_master():
                    if force or len(self.PanelSettings[PanelSetting.ZoneNames]) < zone_count:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone names, zone count = {zone_count}  I've only got {len(self.PanelSettings[PanelSetting.ZoneNames])} zone names")
                        self.B0_Wanted.add(B0SubType.ZONE_NAMES)
                    if force or len(self.PanelSettings[PanelSetting.ZoneTypes]) < zone_count:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone types, zone count = {zone_count}  I've only got {len(self.PanelSettings[PanelSetting.ZoneTypes])} zone types")
                        self.B0_Wanted.add(B0SubType.ZONE_TYPES)
                else:
                    if force or len(self.PanelSettings[PanelSetting.ZoneNames]) < zone_count:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone names again zone count = {zone_count}  I've only got {len(self.PanelSettings[PanelSetting.ZoneNames])} zone names")
                        self.add_message_to_send_queue(Send.ZONENAME)
                    if force or len(self.PanelSettings[PanelSetting.ZoneTypes]) < zone_count:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone types again zone count = {zone_count}  I've only got {len(self.PanelSettings[PanelSetting.ZoneTypes])} zone types")
                        self.add_message_to_send_queue(Send.ZONETYPE)
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
            """Auto enrol the PowerMax/Master unit."""
            # Only attempt to auto enrol powerlink for newer panels but not the 360 or 360R.
            #       Older panels need the user to manually enrol
            #       360 and 360R can get to Standard Plus but not Powerlink as (I assume that) they already have this hardware and panel will not support 2 powerlink connections
            if self.ABMessageSupported and not self.PowerLinkBridgeConnected:
                if force or (self.PanelMode == AlPanelMode.STANDARD_PLUS):
                    if force or (self.PanelType is not None and self.AutoEnrol):
                        # Only attempt to auto enrol powerlink for newer panels. Older panels need the user to manually enrol, we should be in Standard Plus by now.
                        log.debug("[sendMsgENROL] Trigger Powerlink Attempt, sending ENROL request to the panel")
                        # Allow the receipt of a powerlink ack to then send a MSG_RESTORE to the panel,
                        #      this should kick it in to powerlink after we just enrolled
                        self.allowAckToTriggerRestore = True
                        # Send enrol to the panel to try powerlink
                        self.add_message_to_send_queue(Send.ENROL, priority = MessagePriority.IMMEDIATE, options=[ [4, convert_bytearray(self.DownloadCode)] ])
                    elif self.PanelType is not None and self.PanelType >= 1:
                        # Powermax+ or Powermax Pro, attempt to just send a MSG_RESTORE to prompt the panel in to taking action if it is able to
                        log.debug("[sendMsgENROL] Trigger Powerlink Prompt attempt to a Powermax+ or Powermax Pro panel")
                        # Prevent the receipt of a powerlink ack to then send a MSG_RESTORE to the panel,
                        self.allowAckToTriggerRestore = False
                        # Send a MSG_RESTORE, if it sends back a powerlink acknowledge then another MSG_RESTORE will be sent,
                        #      hopefully this will be enough to kick the panel in to sending Receive.POWERLINK Keep-Alive
                        self._trigger_restore_status()     # Clear message buffers and send a Restore (if in Powerlink or standard plus) or Status (not in Powerlink) to the Panel
                        #self.add_message_to_send_queue(Send.RESTORE)

        def update_time_check_power_master(counter : int, interval : int) -> int:
            for u in range(4):
                offset : int = int((u * interval) / 4)
                update_check = ((counter + offset) % interval) == 0
                if update_check:
                    log.debug(f"[_sequencer] Checking Panel and Sensor State requests - {u=}  {offset=}  {update_check=}")
                    return u
            return -1

        def do_got_panel_details() -> SequencerType:
            log.debug(f"[_sequencer] Got panel details, I am a {self.PanelModel}")
            # ignore all possible errors etc, call the function and ignore the return value
            self._clearPanelErrorMessages()
            if self.ForceStandardMode:
                if self.PanelType is not None and (self.PowerLinkBridgeConnected or self.is_power_master()):
                    return SequencerType.GettingB0SensorMessages
                self.add_message_to_send_queue(Send.EXIT)  # when we receive a 3C we know that the panel is in download mode, so exit download mode
                return SequencerType.AimingForStandard
            self._first_send_download_eprom = get_utc_time()
            if self.pmDownloadByEPROM:
                return SequencerType.EPROMInitialiseDownload  # This is the same as default for PowerMax so should be OK
            if self.PanelType is not None and (self.PowerLinkBridgeConnected or self.is_power_master()):
                return SequencerType.GettingB0SensorMessages
            log.warning("[_sequencer] Abnormal: Should not get here!  Got panel details and downloading by EPROM, tell the author of this integration by reporting an issue on Github")
            return SequencerType.EPROMInitialiseDownload

        def do_getting_b0_sensor_messages(counter : int) -> tuple[SequencerType | None, int]:
            self.EnableB0ReceiveProcessing = True

            mandatory, optional = self._check_panel_data_present(forceall = self.checkAllPanelData, output_to_log = True)
            missing = mandatory | optional

            log.debug(f"[_sequencer]   _check_panel_data_present {self.checkAllPanelData=}    missing items {mandatory=}  {optional=}")
            self.checkAllPanelData = False

            zone_count = self._get_panel_capability(IndexName.ZONES)
            if len(mandatory) == 0 and len(self.PanelSettings[PanelSetting.ZoneEnrolled]) >= zone_count:
                # Include a check to make certain we have the sensor enrolled data
                self.B0_Wanted = set()
                # We can create the sensors with just the mandatory data and progress the sequencer
                self._clearPanelErrorMessages()
                return SequencerType.CreateSensors, 0

            if counter >= 20: # 20 seconds
                # timeout. My PM panels both take about 7 to 8 seconds so if we get to 20 seconds then EPROMInitialiseDownload
                self.pmForceDownloadByEPROM = True
                self.pmDownloadByEPROM = True
                return SequencerType.InitialisePanel, 2

            if counter != THREE_SECONDS and counter % THREE_SECONDS == 0: # every 3 seconds (or so). This is a compromise delay, not too often so the panel starts sending back "wait" messages.
                self._clearPanelErrorMessages()
                _requestMissingPanelConfig(missing)
            elif counter > 2 and (s := processPanelErrorMessages()) != PanelErrorStates.AllGood:
                self._clearPanelErrorMessages()
                if s in [PanelErrorStates.BeeZeroInvalidCommand]:
                    # We've tried to get B0 messages to get Panel Data but it's replied with an InvalidCommand
                    self.pmForceDownloadByEPROM = True
                    self.pmDownloadByEPROM = True
                    return SequencerType.InitialisePanel, 2
            elif self.PartitionState[0].PanelStateData == AlPanelStatus.DOWNLOADING:
                self._triggered_download = False
                self.pmDownloadInProgress = False
                self.pmDownloadMode = False
                self.pmDownloadComplete = True
                if counter == 0:                             # First time just get the panel status
                    log.debug("[_sequencer]   Panel status is DOWNLOADING so updating panel status")
                    self._fetch_panel_status(priority = MessagePriority.IMMEDIATE)               # This should update .PanelStateData
                elif counter % 5 == 0:
                    log.debug("[_sequencer]   Panel status is DOWNLOADING so trying to kick it out")
                    self._clear_receive_response_list()
                    self._empty_send_queue(priority = MessagePriority.ACK)
                    self.add_message_to_send_queue(Send.EXIT)    # Kick the panel out of downloading, and wait for 1.5 seconds
                    self.add_message_to_send_queue(Send.STOP)    # Kick the panel out of downloading, and wait for 1.5 seconds
                    self._fetch_panel_status(priority = MessagePriority.NORMAL)                  # This should update .PanelStateData
            return None, 0  # stay in the same state

        def do_eeprom_initialise_download() -> SequencerType | None:
            self.EnableB0ReceiveProcessing = False
            # Clear all downloaded EPROM and empty all saved data
            self.epromManager.reset()
            # Populate the full list of EPROM blocks
            self.myDownloadList = self.epromManager.populatEPROMDownload(self.is_power_master())
            if not self.PowerLinkBridgeConnected or self.PowerLinkBridgeStealth:
                # If not using bridge, or using bridge and already in stealth mode
                return SequencerType.EPROMTriggerDownload
            log.debug("[_sequencer] Sending command to Bridge - Turn Stealth ON")
            command = 2   # Stealth command
            param = 1     # Enter it
            self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.IMMEDIATE, options=[ [1, command], [2, param] ])  # Tell the Bridge to go in to exclusive mode
            command = 1   # Get Status command
            param = 0     # Irrelevant
            self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.IMMEDIATE, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
            # Continue in this SequencerType until the bridge is in stealth
            return None

        def do_eprom_trigger_download() -> tuple[SequencerType, bool]:
            interval = get_utc_time() - self._first_send_download_eprom
            if self.DownloadCounter >= DOWNLOAD_RETRY_COUNT or (not EPROM_DOWNLOAD_ALL and interval > timedelta(seconds=DOWNLOAD_TIMEOUT)):
                # Give it DOWNLOAD_RETRY_COUNT attempts start the download
                # Give it DOWNLOAD_TIMEOUT seconds to complete the download
                log.warning("[_sequencer] Abnormal: ********************** Download Timer has Expired, Download has taken too long *********************")
                self.send_panel_update(AlCondition.DOWNLOAD_TIMEOUT)                 # download timer expired
                if self.PowerLinkBridgeConnected:
                    log.debug("[_sequencer] ***************************** Bridge connected so start again ***************************************")
                    # Reset download counter to check for the number of attempts
                    self.DownloadCounter = 0
                    # Delete all existing EPROM data
                    self.epromManager.reset()
                    return SequencerType.Reset, False
                log.debug("[_sequencer] ************************************* Going to standard mode ***************************************")
                return SequencerType.AimingForStandard, False
            log.debug("[_sequencer] Asking for panel EPROM")
            self._clear_receive_response_list()
            self._empty_send_queue(priority = MessagePriority.ACK)
            self.DownloadCounter += 1
            self.add_message_to_send_queue(Send.DOWNLOAD_DL, options=[ [3, convert_bytearray(self.DownloadCode)] ])
            # We got a first response, now we can Download the panel EPROM settings
            self._last_send_download_eprom = get_utc_time()
            # Kick off the download sequence and set associated variables
            self.pmExpectedResponse = set()
            self.PanelMode = AlPanelMode.DOWNLOAD
            self.PartitionState[0].PanelStateData = AlPanelStatus.DOWNLOADING  # Downloading
            self.send_panel_update(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
            self._triggered_download = True
            self.pmDownloadInProgress = True
            self.add_message_to_send_queue(Send.DL, options=[ [1, self.myDownloadList.pop(0)] ])  # Read the next block of EPROM data
            return SequencerType.EPROMStartedDownload, True

        def do_eeprom_started_download(lastrecv) -> SequencerType | None:
            # We got a first response, now we can Download the panel EPROM settings
            if (s := processPanelErrorMessages()) != PanelErrorStates.AllGood:
                if s in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.DownloadRetryReceived, PanelErrorStates.TimeoutReceived]:
                    self.pmExpectedResponse = set()
                    return SequencerType.EPROMInitialiseDownload
                if s == PanelErrorStates.DespatcherException:
                    # start again, restart the despatcher task
                    return SequencerType.Reset
                return SequencerType.InitialisePanel
            interval = get_utc_time() - self._last_send_download_eprom
            log.debug(f"[_sequencer] interval={interval}  td={DOWNLOAD_RETRY_DELAY}   self._last_send_download_eprom(UTC)={self._last_send_download_eprom}    timenow(UTC)={get_utc_time()}")

            if interval > timedelta(seconds=DOWNLOAD_RETRY_DELAY):            # Give it this number of seconds to start the downloading
                return SequencerType.EPROMInitialiseDownload
            if lastrecv != self._last_recv_time_panel_data and (self.pmDownloadInProgress or self.pmDownloadComplete):
                return SequencerType.EPROMDoingDownload
            return None

        def do_eeprom_doing_download() -> SequencerType | None:
            if (s := processPanelErrorMessages()) != PanelErrorStates.AllGood:
                # Handle error messages from the panel
                if s in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.DownloadRetryReceived, PanelErrorStates.TimeoutReceived]:
                    self.pmExpectedResponse = set()
                    return SequencerType.EPROMInitialiseDownload
                if s == PanelErrorStates.DespatcherException:
                    # start again, restart the despatcher task
                    return SequencerType.Reset
                return SequencerType.InitialisePanel
            if self.pmDownloadComplete:
                # Download Complete
                return SequencerType.EPROMDownloadComplete
            # Handle timeouts
            timenow = get_utc_time()
            interval_start = timenow - self._last_send_download_eprom
            interval_last_receive = timenow - self._last_recv_time_panel_data
            #log.debug(f"[_sequencer] timenow={get_utc_time()}   interval_start={interval_start}  self._last_send_download_eprom={self._last_send_download_eprom}")
            #log.debug(f"[_sequencer]                                        interval_last_receive={interval_last_receive}  self._last_recv_time_panel_data={self._last_recv_time_panel_data}")
            if interval_start > timedelta(seconds=DOWNLOAD_TIMEOUT):
                # The whole Download sequence hasn't finished in this timeout
                return SequencerType.InitialisePanel
            if interval_last_receive >= timedelta(seconds=8):
                # 8 seconds since we last received a byte of data from the panel
                log.debug("[_sequencer] ****************************** Assume Download Failed, go back to initialise panel and start again ********************************")
                return SequencerType.InitialisePanel
            if interval_last_receive >= timedelta(seconds=3):
                # 3 seconds since we last received a byte of data from the panel and the last command to the panel was a download EPROM command
                log.debug("[_sequencer] ****************************** Recreating Download list and triggering Download ********************************")
                # Make sure that the last saved block is removed in case it has been corrupted
                self.epromManager.removeLastSaved()
                # Recreate the list of blocks to download
                self.myDownloadList = self.epromManager.populatEPROMDownload(self.is_power_master())
                # Resent the Download command to the panel and try to get the blocks
                return SequencerType.EPROMTriggerDownload
            return None

        def do_eeprom_download_complete() -> SequencerType:
            # Check the panel type from EPROM against the panel type from the 3C message to give a basic test of the EPROM download
            panel_type_eprom = self.epromManager.lookupEpromSingle(EPROM.PANEL_TYPE_CODE)          # pyright: ignore[reportUnusedVariable]
            if panel_type_eprom in (None, 0xFF):
                self._triggered_download = False
                self.pmDownloadInProgress = False
                self.pmDownloadMode = False
                self.pmDownloadComplete = False
                log.error("[_sequencer] Lookup of panel type string and model from the EPROM failed, assuming EPROM download failed [panel_type_eprom=%s], retrying", panel_type_eprom)
                self._first_send_download_eprom = get_utc_time()
                return SequencerType.EPROMInitialiseDownload
            if self.PanelType is not None and self.PanelType != panel_type_eprom:
                self._triggered_download = False
                self.pmDownloadInProgress = False
                self.pmDownloadMode = False
                self.pmDownloadComplete = False
                log.error(f"[_sequencer] Panel Type not set from EPROM, assuming EPROM download failed {panel_type_eprom=}, retrying")
                self._first_send_download_eprom = get_utc_time()
                return SequencerType.EPROMInitialiseDownload
            # Process the EPROM data
            try:
                log.debug("[_sequencer] Process Settings from EPROM")
                if self.pmDownloadComplete:
                    self._process_EPROM_settings()
                    self.PanelStatus[PANEL_STATUS.DEVICES] = self._process_EPROM_keypads_sirens()
                    self._update_all_sirens()
                    self._process_switch_settings()
                    log.debug("[_sequencer] EPROM Processing Complete")
            except Exception as ex:
                tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                log.error("[_sequencer] EPROM Processing failed, caused an exception\n%s", tb_str)
                return SequencerType.Reset
            if self.is_power_master(): # PowerMaster so get any remaining B0 data
                self.checkAllPanelData = False                                    # We've downloaded the EPROM so no need to get all B0 panel data
                self.EnableB0ReceiveProcessing = True
                return SequencerType.GettingB0SensorMessages
            return SequencerType.CreateSensors

        def do_eeprom_exit_download(counter: int) -> tuple[SequencerType | None, int]:
            if self.PartitionState[0].PanelStateData != AlPanelStatus.DOWNLOADING:
                self._triggered_download = False
                self.pmDownloadInProgress = False
                self.pmDownloadMode = False
                self.pmDownloadComplete = True
                return SequencerType.EnrollingPowerlink, 0

            if (s := processPanelErrorMessages()) != PanelErrorStates.AllGood: # An error state from the panel so process it
                self._clear_receive_response_list()
                self._clearPanelErrorMessages()
                if s == PanelErrorStates.DespatcherException:
                    # start again, restart the despatcher task
                    return SequencerType.LookForPowerlinkBridge, 4
                if s not in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.AccessDeniedPin, PanelErrorStates.AccessDeniedStop, PanelErrorStates.AccessDeniedCommand]:
                    log.debug(f"[_sequencer]    Error Message: {s}")
                    return SequencerType.InitialisePanel, 4
                self._clear_receive_response_list()
                self._empty_send_queue(priority = MessagePriority.ACK)
                self.add_message_to_send_queue(Send.EXIT, priority = MessagePriority.URGENT)
                self.add_message_to_send_queue(Send.STOP, priority = MessagePriority.URGENT)    # Kick the panel out of downloading
                self._fetch_panel_status(priority = MessagePriority.URGENT)
                return None, 4
            if counter % 3 == 0:
                self._clear_receive_response_list()
                self._empty_send_queue(priority = MessagePriority.ACK)                      # Empty the URGENT amd NORMAL priority queue (retain the IMMEDIATE and ACK queues)
                self.add_message_to_send_queue(Send.EXIT, priority = MessagePriority.URGENT)  # Kick the panel out of download
                self._fetch_panel_status(priority = MessagePriority.URGENT)
            return None, 0

        def do_create_sensors() -> SequencerType:
            self._update_all_sensors()
            self._dumpAllDevicesToLogFile(False, False)
            self._create_PGM_switch()
            if not self.ForceStandardMode and self._is_valid_user_code():
                if self.PowerLinkBridgeConnected:
                    log.debug("[_sequencer] Sending command to Bridge - Stealth OFF")
                    command = 2   # Stealth command
                    param = 0     # Exit it
                    self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to exit exclusive mode
                    command = 1   # Get Status command
                    param = 0     # Irrelevant
                    self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                    self.PanelMode = AlPanelMode.POWERLINK_BRIDGED
                    retval = SequencerType.DoingPowerlinkBridge
                else:
                    self.PanelMode = AlPanelMode.STANDARD_PLUS
                    retval = SequencerType.EPROMExitDownload
                self.send_panel_update(AlCondition.DOWNLOAD_SUCCESS)   # download completed successfully, panel type matches and got usercode (so assume all sensors etc loaded)
                return retval
            self.send_panel_update(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
            return SequencerType.AimingForStandard

        reset_local()
        self._start_despatcher()
        await self.waitForTransport(200)
        while not self.suspendAllOperations:
            try:
                await self._pause_event.wait()  # pauses here if cleared
                changed_state = _sequencer_state != _sequencer_state_previous
                if changed_state:
                    # create a generic counter that gets reset every state change, so it can be used in a single state
                    log.debug(f"[_sequencer] Changed state from {_sequencer_state_previous} to {_sequencer_state}, I was in state {_sequencer_state_previous} for approx {counter} seconds")
                    counter = 0
                    if _sequencer_state in [SequencerType.DoingStandard, SequencerType.DoingStandardPlus, SequencerType.DoingPowerlink, SequencerType.DoingPowerlinkBridge]:
                        # if we're at the point of "doing" then give the client a chance to set everything up with all the async calls
                        await asyncio.sleep(1.0)
                    # If the state has changed then do it straight away, don't do the 1 second loop
                else:
                    # If the state has stayed the same then delay 1 second, if the state has changed then get on with it
                    await asyncio.sleep(1.0)
                    # increment the counter every loop
                    counter = counter + 1 if counter < a_day - 1 else 0  # reset the counter 24 hours (approx), has to be < so 4 hour delays are OK
                    #log.debug(f"[_sequencer] Current state {_sequencer_state}    {counter=}")

                _sequencer_state_previous = _sequencer_state

                # If the panel mode has changed then push an update through
                if old_panel_mode != self.PanelMode:
                    log.debug(f"[_sequencer] Panel Mode changed from {old_panel_mode.name} to {self.PanelMode.name}" )
                    self.send_panel_update(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
                old_panel_mode = self.PanelMode

                if not self.suspendAllOperations:  ## To make sure as it could have changed in the 1 second sleep

                    ######################################################################################
                    ####### Check the global connection state of the panel, have we received data ########
                    #######       These 3 tests take drastic action, they stop the integration    ########
                    ######################################################################################
                    if self._last_recv_time_panel_data is None:  # has any data been received from the panel yet, even just a single byte?
                        no_data_received_counter += 1
                        # log.debug(f"[_sequencer] no_data_received_counter {no_data_received_counter}")
                        if no_data_received_counter >= NO_RECEIVE_DATA_TIMEOUT:  ## lets assume approx 30 seconds
                            log.error("[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. no data has been received from the panel)" )
                            self._report_problem(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
                            no_data_received_counter = 0
                            continue   # just do the while loop, which will exit as self.suspendAllOperations will be True
                    elif self._last_packet is None: # have we been able to construct at least one full and crc checked message
                        no_packet_received_counter += 1
                        #log.debug(f"[_sequencer] no_packet_received_counter {no_packet_received_counter}")
                        if no_packet_received_counter >= NO_RECEIVE_DATA_TIMEOUT:  ## lets assume approx 30 seconds
                            log.error("[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. no valid packet has been received from the panel)" )
                            self._report_problem(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
                            no_packet_received_counter = 0
                            continue   # just do the while loop, which will exit as self.suspendAllOperations will be True
                    else:  # Data has been received from the panel but check when it was last received
                        # calc time difference between now and when data was last received
                        no_packet_received_counter = 0
                        no_data_received_counter = 0
                        # calculate the time interval back to the last receipt of any data
                        interval = get_utc_time() - self._last_recv_time_panel_data
                        if interval >= timedelta(seconds=LAST_RECEIVE_DATA_TIMEOUT):
                            log.error(f"[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. data has not been received from the panel in {interval})" )
                            self._report_problem(AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED)
                            continue   # just do the while loop, which will exit as self.suspendAllOperations will be True

                    #####################################
                    ####### Sequencer activities ########
                    #####################################

                    if (
                        _sequencer_state not in [SequencerType.DoingStandard, SequencerType.DoingStandardPlus, SequencerType.DoingPowerlink, SequencerType.DoingPowerlinkBridge]
                        or changed_state
                        or counter % 180 == 0
                    ):
                        # When we reach 1 of the 4 final states then stop logging it, but then output every 3 minutes
                        ps = [p.PanelStateData.name for p in self.PartitionState]
                        log.info(f"[_sequencer] SeqState={_sequencer_state}     Counter={counter}      PanelMode={self.PanelMode.name}     PanelStateData={ps if self.partitionsEnabled else ps[0]}     SendQueue={self._send_queue.qsize()}")

                    if self.loopbackTest:
                        # This supports the loopback test
                        #await asyncio.sleep(2.0)
                        self._clear_receive_response_list()
                        self._empty_send_queue(priority = MessagePriority.DELETE_ALL) # empty the list
                        self.add_message_to_send_queue(Send.STOP)
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.LookForPowerlinkBridge:   ################################################################ LookForPowerlinkBridge  ###################################################
                        self.PanelMode = AlPanelMode.STARTING
                        if not self.ForceStandardMode:
                            for i in range(2):
                                command = 1   # Get Status command
                                param = i     # Irrelevant, but it makes it a unique message
                                self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.IMMEDIATE, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                            # Make a 1 off request for the panel to send the Download Code and the panel name e.g. PowerMaster-10
                            self.EnableB0ReceiveProcessing = True
                            #s = self._create_B0_35_Data_Request(strlist = "3c 00 0f 00")
                            #s = self._create_B0_42_Data_Request(strlist = "3c 00 0f 00")
                            s = self._create_B0_42_Data_Request(strlist = "0f 00")
                            self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)

                            await asyncio.sleep(1.0)
                        _sequencer_state = SequencerType.Reset
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.Reset:                    ################################################################ Reset                   ###################################################
                        # log.debug(f"[_sequencer] In Reset state {self.PowerLinkBridgeConnected} and {self.PowerLinkBridgeAlarm}")
                        self.PanelMode = AlPanelMode.STARTING
                        if self.PowerLinkBridgeConnected and not self.PowerLinkBridgeAlarm:  # if bridge but the alarm panel is not connected then go no further
                            # This sequencer loop is once per second.  That is enough time between LookForPowerlinkBridge and here to make the connection and get a reply to set the variables
                            _sequencer_state = SequencerType.LookForPowerlinkBridge
                            log.debug("[_sequencer] Waiting for Alarm Panel to connect to the Bridge")
                            await asyncio.sleep(1.0)
                        else:
                            reset_local()
                            self._clear_despatcher() # No need to restart, just clear it out.
                            _sequencer_state = SequencerType.InitialisePanel
                        continue   # just do the while loop

                    if delay_loops > 0:                                           ################################################################ Delay Loop              ###################################################
                        no_data_received_counter = 0
                        no_packet_received_counter = 0
                        delay_loops = delay_loops - 1
                        self._clearPanelErrorMessages() # Clear all panel reported errors for the duration of the delay
                        continue   # do all the basic connection checks above and then just do the while loop

                    if (                                                          ################################################################ PanelWantsToEnrol       ###################################################
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

                    if self.UnexpectedPanelKeepAlive:                             ################################################################ PanelKeepAlive          ###################################################
                        self.UnexpectedPanelKeepAlive = False
                        if (
                           not self.pmDownloadMode
                           and not self.ForceStandardMode
                           and not self.allowAckToTriggerRestore
                           and self.PanelMode in [AlPanelMode.STOPPED]
                        ):
                            log.debug("[_sequencer] Unexpected Panel Powerlink Keep Alive, setting sequencer to LookForPowerlinkBridge")
                            _sequencer_state = SequencerType.LookForPowerlinkBridge
                            delay_loops = 2
                        else:
                            log.debug("[_sequencer] Unexpected Panel Powerlink Keep Alive, ignoring it")

                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.InitialisePanel:          ################################################################ Initialising            ###################################################
                        self.PanelMode = AlPanelMode.STARTING
                        await asyncio.sleep(1.0)
                        _resetPanelInterface()
                        self._clearPanelErrorMessages()
                        if not self.pmGotPanelDetails:
                            self.add_message_to_send_queue(Send.PANEL_DETAILS, options=[ [3, convert_bytearray(self.DownloadCode)] ])
                        _sequencer_state = SequencerType.WaitingForPanelDetails
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.WaitingForPanelDetails:   ################################################################ WaitingForPanelDetails  ###################################################

                        # Take care of the first part of initialisation
                        if self.pmGotPanelDetails:          # Got 3C panel data message
                            _sequencer_state = do_got_panel_details()
                        elif (s := processPanelErrorMessages()) != PanelErrorStates.AllGood:
                            self._clear_receive_response_list()
                            self._clearPanelErrorMessages()
                            delay_loops = 4
                            if s == PanelErrorStates.DespatcherException:
                                # start again, restart the despatcher task
                                _sequencer_state = SequencerType.LookForPowerlinkBridge
                            elif s in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.AccessDeniedStop]:
                                _sequencer_state = SequencerType.InitialisePanel
                                setNextDownloadCode(self.PanelType if self.PanelType is not None else 1)
                                log.debug("[_sequencer]    Abnormal: Moved on to next download code and going to init")
                            elif s == PanelErrorStates.Exit:
                                _sequencer_state = SequencerType.InitialisePanel
                            elif s == PanelErrorStates.TimeoutReceived:
                                _sequencer_state = SequencerType.InitialisePanel
                                log.debug("[_sequencer]    Abnormal: TimeoutReceived")
                            elif s == PanelErrorStates.DownloadRetryReceived:
                                delay_loops = 10
                                _sequencer_state = SequencerType.InitialisePanel
                                log.debug(f"[_sequencer]    Abnormal: DownloadRetryReceived loop = {delay_loops}")
                            # Ignore other errors
                        elif counter >= 7:     # up to 7 seconds to get panel data message (worst case to also allow for Bridge traffic)
                            log.debug("[_sequencer]    Abnormal: Taken too long, going to init")
                            _sequencer_state = SequencerType.InitialisePanel

                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.GettingB0SensorMessages:  ################################################################ GettingB0SensorMessages ###################################################
                        if not self.pmGotPanelDetails or self.PanelType is None: # This should never happen but just in case :)
                            _sequencer_state = SequencerType.LookForPowerlinkBridge
                            continue
                        next_state, delay_loops = do_getting_b0_sensor_messages(counter)
                        if next_state is not None:
                            _sequencer_state = next_state
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.EPROMInitialiseDownload:   ################################################################ EPROMInitialiseDownload  ###################################################
                        next_state: SequencerType = do_eeprom_initialise_download()
                        if next_state is not None:
                            _sequencer_state = next_state
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.EPROMTriggerDownload:     ################################################################ EPROMTriggerDownload    ###################################################
                        _sequencer_state, lr = do_eprom_trigger_download()
                        if lr:
                            lastrecv = self._last_recv_time_panel_data
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.EPROMStartedDownload:     ################################################################ EPROMStartedDownload    ###################################################
                        next_state: SequencerType = do_eeprom_started_download(lastrecv)
                        if next_state is not None:
                            _sequencer_state = next_state
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.EPROMDoingDownload:       ################################################################ EPROMDoingDownload      ###################################################
                        next_state: SequencerType = do_eeprom_doing_download()
                        if next_state is not None:
                            _sequencer_state = next_state
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.EPROMDownloadComplete:    ################################################################ EPROMDownloadComplete   ###################################################
                        _sequencer_state = do_eeprom_download_complete()
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.EPROMExitDownload:        ################################################################ EPROMExitDownload       ###################################################
                        next_state, delay_loops = do_eeprom_exit_download(counter)
                        if next_state is not None:
                            _sequencer_state = next_state
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.CreateSensors:            ################################################################ CreateSensors and PGM   ###################################################
                        _sequencer_state = do_create_sensors()
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.EnrollingPowerlink:       ################################################################ EnrollingPowerlink      ###################################################

                        if self.PanelMode in [AlPanelMode.POWERLINK]:
                            _sequencer_state = SequencerType.DoingPowerlink         # Very unlikely but possible
                        elif counter == 10:
                            self.PanelMode = AlPanelMode.STANDARD_PLUS                    # After 10 attempts to enrol, stay in StandardPlus Emulation Mode
                            _sequencer_state = SequencerType.DoingStandardPlus
                        else:
                            log.debug(f"[_sequencer] Try to enrol (panel {self.PanelModel})")
                            if self.PartitionState[0].PanelStateData == AlPanelStatus.DOWNLOADING:
                                log.debug(f"[_sequencer]       Partition 0 still thinks we're Downloading (panel {self.PanelModel})")
                                # ??????????????????????????? SHOULD WE GO BACK TO EPROMExitDownload ????????????????????
                            sendMsgENROL()  #  Try to enrol with the Download Code that worked for Downloading the EPROM
                            _sequencer_state = SequencerType.WaitingForEnrolSuccess
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.WaitingForEnrolSuccess:   ################################################################ WaitingForEnrolSuccess  ###################################################

                        self.keep_alive_counter += 1
                        log.debug(f"[_sequencer]     WaitingForEnrolSuccess {self._is_send_queue_empty()=} {self.pmDownloadMode=} {self.keep_alive_counter=}  threshold is 15")

                        if self.PanelType is not None and not self.AutoEnrol:
                            self.PanelMode = AlPanelMode.STANDARD_PLUS                    # Cannot AutoEnrol this panel so go straight to Std+ operation
                            log.debug("[_sequencer]     WaitingForEnrolSuccess        Panel does not support Auto Enrol, going to Standard Plus and waiting for manual enrol")
                            _sequencer_state = SequencerType.DoingStandardPlus
                        elif (s := processPanelErrorMessages()) == PanelErrorStates.DespatcherException:
                            # start again, restart the despatcher task
                            _sequencer_state = SequencerType.Reset
                        elif s != PanelErrorStates.AllGood:
                            self._clearPanelErrorMessages()
                            self.pmExpectedResponse = set()
                            self.PanelMode = AlPanelMode.STANDARD_PLUS
                            _sequencer_state = SequencerType.EPROMExitDownload
                        elif self.PanelMode == AlPanelMode.POWERLINK:
                            self._reset_keep_alive_messages()
                            _sequencer_state = SequencerType.DoingPowerlink
                        elif counter == (MAX_TIME_BETWEEN_POWERLINK_ALIVE if self.receivedPowerlinkAcknowledge else 3):
                            # once we receive a powerlink acknowledge then we wait for the I'm alive message (usually every 30 seconds from the panel)
                            self.PanelMode = AlPanelMode.STANDARD_PLUS
                            _sequencer_state = SequencerType.EPROMExitDownload
                        elif self._is_send_queue_empty() and not self.pmDownloadMode and self.keep_alive_counter >= 15:
                            self._reset_keep_alive_messages()
                            self.add_message_to_send_queue (Send.EXIT)
                            self.add_message_to_send_queue(Send.ALIVE if self.ABMessageSupported else Send.PM_KEEPALIVE)  # and not self.PowerLinkBridgeConnected
                            #self.add_message_to_send_queue (Send.ALIVE)

                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.AimingForStandard:        ################################################################ AimingForStandard       ###################################################

                        #self.PanelMode = AlPanelMode.STANDARD
                        # only if we meet the criteria do we move on to the next step.  Until then just do it
                        _gotoStandardModeStopDownload()
                        # Match _sequencer_state to the new self.PanelMode (that is set in _gotoStandardModeStopDownload)
                        if self.PanelMode == AlPanelMode.STANDARD_PLUS:
                            _sequencer_state = SequencerType.DoingStandardPlus
                        else:
                            _sequencer_state = SequencerType.DoingStandard

                        if self.PanelMode == AlPanelMode.STANDARD:    # Do not do this for MINIMAL and STANDARD_PLUS (for very different reasons)
                            if self.is_power_master(): # PowerMaster so get B0 data
                                # Powerlink panel so ask the panel for B0 data to get panel details, as these can be asked for and received within download mode we can do it straight away
                                #log.debug("[_sequencer] Adding lots of B0 requests to wanted list")
                                #self.B0_Wanted.update([0x20, 0x21, 0x2d, 0x1f, 0x07, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x11, 0x13, 0x14, 0x15, 0x18, 0x1a, 0x19, 0x1b, 0x1d, 0x2f, 0x31, 0x33, 0x1e, 0x24, 0x02, 0x23, 0x3a, 0x4b])

                                log.debug("[_sequencer] Aiming for Standard Mode - Adding B0 wanted data to list")
                                # Request Sensor Information and State
                                self.B0_Wanted.update(b0_periodic_update_list[0]) # WIRELESS_DEV_CHANNEL
                                self.B0_Wanted.update(b0_periodic_update_list[1])
                                self.B0_Wanted.update(b0_periodic_update_list[2])
                                self.B0_Wanted.update(b0_periodic_update_list[3])
                                self.B0_Wanted.update({B0SubType.ZONE_LAST_EVENT, B0SubType.SYSTEM_CAP})

                            else:    # PowerMax get ZONE_NAMES, ZONE_TYPES etc
                                self.add_message_to_send_queue(Send.ZONENAME)
                                self.add_message_to_send_queue(Send.ZONETYPE)
                        continue   # just do the while loop

                    if _sequencer_state == SequencerType.DoingStandard:            ################################################################ DoingStandard           ###################################################
                        # Put all the special standard mode things here
                        # Keep alive functionality
                        self.keep_alive_counter += 1
                        if self._is_send_queue_empty() and not self.pmDownloadMode and self.keep_alive_counter >= self.KeepAlivePeriod:
                            self._reset_keep_alive_messages()
                            self.add_message_to_send_queue (Send.STATUS_SEN)

                        # Do most of this for ALL Panel Types
                        # Only check these every 180 seconds
                        if (counter % 180) == 0:
                            if self.PartitionState[0].PanelStateData == AlPanelStatus.UNKNOWN:
                                log.debug("[_sequencer] ****************************** Getting Panel Status ********************************")
                                self.add_message_to_send_queue(Send.STATUS_SEN)
                            elif self.PartitionState[0].PanelStateData == AlPanelStatus.DOWNLOADING:
                                log.debug("[_sequencer] ****************************** Exit Download Kicker ********************************")
                                self.add_message_to_send_queue(Send.EXIT, priority = MessagePriority.URGENT)
                            elif not self.pmGotPanelDetails:
                                log.debug("[_sequencer] ****************************** Asking For Panel Details ****************************")
                                _sequencer_state = SequencerType.InitialisePanel
                            else:
                                # The first time this may create sensors (for PowerMaster, especially those in the range Z33 to Z64 as the A5 message will not have created them)
                                # Subsequent calls make sure we have all zone names, zone types and the sensor list
                                updateSensorNamesAndTypes()

                    elif _sequencer_state == SequencerType.DoingStandardPlus:        ################################################################ DoingStandardPlus       ###################################################

                        # Put all the special standard plus mode things here
                        # Keep alive functionality
                        self.keep_alive_counter += 1
                        if self._is_send_queue_empty() and not self.pmDownloadMode and self.keep_alive_counter >= self.KeepAlivePeriod:
                            self._reset_keep_alive_messages()
                            self.add_message_to_send_queue(Send.ALIVE if self.ABMessageSupported else Send.PM_KEEPALIVE) # and not self.PowerLinkBridgeConnected
                            #self.add_message_to_send_queue (Send.ALIVE)
                            #if self.PanelType is not None and not self.AutoEnrol:
                            #    self.PanelMode = AlPanelMode.STANDARD_PLUS             # should already be but just to make sure
                            #    _sequencer_state = SequencerType.EPROMExitDownload

                        if self.PanelMode in [AlPanelMode.POWERLINK]:
                            _sequencer_state = SequencerType.DoingPowerlink
                        elif self.PanelMode in [AlPanelMode.POWERLINK_BRIDGED]:  # This is only possible from EPROM Download so it's unlikely to happen, but just in case ....
                            _sequencer_state = SequencerType.DoingPowerlinkBridge

                    elif _sequencer_state == SequencerType.DoingPowerlink:           ################################################################ DoingPowerlink          ###################################################
                        # Put all the special powerlink mode things here
                        self.PanelMode = AlPanelMode.POWERLINK

                        # by here we should have all mandatory panel settings but maybe not all optional
                        (mandatory, optional) = self._check_panel_data_present(forceall = False, output_to_log = True)
                        missing = mandatory | optional

                        if len(mandatory) > 0:
                            log.debug(f"[_sequencer]   Mandatory should all be obtained by now but it isnt {mandatory=}")

                        if self.is_power_master() and len(missing) > 0 and counter != 3 and counter % 3 == 0: # every 3 seconds (or so). This is a compromise delay, not too often so the panel starts sending back "wait" messages.
                            log.debug(f"[_sequencer]   requesting missing panel data, missing items {mandatory=}  {optional=}")
                            # mandatory should be empty but add it just in case
                            _requestMissingPanelConfig(missing)

                        # Keep alive functionality
                        self.keep_alive_counter += 1    # This is for me sending to the panel
                        self.powerlink_counter += 1     # This gets reset to 0 when I receive I'm Alive from the panel

                        if self.powerlink_counter > POWERLINK_IMALIVE_RETRY_DELAY:
                            # Go back to Std+ and re-enrol
                            log.debug(f"[_sequencer] ****************************** Not Received I'm Alive From Panel for {POWERLINK_IMALIVE_RETRY_DELAY} Seconds, going to Std+ **************")
                            self.receivedPowerlinkAcknowledge = False
                            self.PanelMode = AlPanelMode.STANDARD_PLUS
                            _sequencer_state = SequencerType.EnrollingPowerlink
                            self._report_problem(AlTerminationType.NO_POWERLINK_FOR_PERIOD)
                            continue   # just do the while loop

                        if self._is_send_queue_empty() and not self.pmDownloadMode and self.keep_alive_counter >= self.KeepAlivePeriod:
                            # Every self.KeepAlivePeriod seconds, unless watchdog has been reset
                            self._reset_keep_alive_messages()
                            # Send I'm Alive to the panel so it knows we're still here
                            self.add_message_to_send_queue(Send.ALIVE if self.ABMessageSupported else Send.PM_KEEPALIVE) # and not self.PowerLinkBridgeConnected
                            #self.add_message_to_send_queue (Send.ALIVE)

                    elif _sequencer_state == SequencerType.DoingPowerlinkBridge:     ################################################################ DoingPowerlinkBridge    ###################################################

                        if self.PowerLinkBridgeConnected:
                            # Keep alive functionality
                            self.keep_alive_counter += 1    # This is for me sending to the panel
                            if self._is_send_queue_empty() and not self.pmDownloadMode and self.keep_alive_counter >= self.KeepAlivePeriod:
                                # Every self.KeepAlivePeriod seconds, unless watchdog has been reset
                                self._reset_keep_alive_messages()
                                # Send I'm Alive to the panel so it knows we're still here
                                self.add_message_to_send_queue(Send.ALIVE if self.ABMessageSupported else Send.PM_KEEPALIVE) # and not self.PowerLinkBridgeConnected
                                #self.add_message_to_send_queue (Send.ALIVE)

                            if self.PowerLinkBridgeStealth:
                                log.debug("[_sequencer] Sending commands to Bridge to exit stealth and get status")
                                command = 2   # Stealth command
                                param = 0     # Exit it
                                self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to exit exclusive mode
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status
                                self.PowerLinkBridgeStealth = False # To make certain it's disabled

                            elif counter % 30 == 0:  # approx every 30 seconds
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ [1, command], [2, param] ])  # Tell the Bridge to send me the status

                            interval = get_utc_time() - self.B0_LastPanelStateTime # make sure that we get the panel state at most every 45 seconds. If we get it for other reasons then OK
                            if interval >= timedelta(seconds=25):                              # every 25 seconds get the panel state
                                log.debug("[_sequencer] Adding Panel State request to B0 wanted due to timer")
                                self.B0_LastPanelStateTime = get_utc_time()        # to stop it retriggering (although its a set so it should not matter)
                                self.B0_Wanted.add(B0SubType.PANEL_STATE_1)                  # Remember that it's a set so if it's already there then it will only be in once

                    #############################################################################################################################################################
                    ####### Drop through to here to do generic code for DoingStandard, DoingStandardPlus, DoingPowerlinkBridge and DoingPowerlink ###############################
                    #############################################################################################################################################################

                    #if self.is_power_master() and counter % 4 == 0:
                        # Dump normal B0 data to the log file
                        #m = []
                        #m.append(myspecialcounter % 256)

                        #log.debug(f"[Process Settings]      myspecialcounter {myspecialcounter}   m={m}")
                        #tmp = [pmSendMsgB0[i].data if i in pmSendMsgB0 for i in m] # Theres only 1 thing in m but do it like this so it can do more than 1
                        #s = self._create_B0_Data_Request(taglist = tmp)
                        #self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)

                        # Dump 0x35 data to the log file
                        #high = myspecialcounter // 256
                        #low  = myspecialcounter % 256
                        #st = f"{low:0>2x} {high:0>2x}"
                        #s = self._create_B0_35_Data_Request(strlist = st)
                        #log.debug(f"[Process Settings]      myspecialcounter {myspecialcounter}   st={st}")
                        #myspecialcounter = (myspecialcounter + 1) % 256
                        #self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)

                        #myspecialcounter = myspecialcounter + 1


                    if self.PanelMode == AlPanelMode.POWERLINK_BRIDGED and self.PartitionState[0].PanelStateData == AlPanelStatus.DOWNLOADING:
                        _my_panel_state_trigger_count = _my_panel_state_trigger_count - 1
                        log.debug(f"[_sequencer] By here we should be in normal operation, we are in {self.PanelMode.name} panel mode"
                                  f" and status is {self.PartitionState[0].PanelStateData}    {_my_panel_state_trigger_count=}")
                        if _my_panel_state_trigger_count < 0:
                            _my_panel_state_trigger_count = 10
                            self._reset_keep_alive_messages()
                            self._reset_watchdog_timeout()
                            _resetPanelInterface()
                            self._clearPanelErrorMessages()
                        continue   # just do the while loop
                    if not (self.PowerLinkBridgeConnected and self.PowerLinkBridgeProxy) and \
                        (self.PartitionState[0].PanelStateData == AlPanelStatus.DOWNLOADING or self.PanelMode == AlPanelMode.DOWNLOAD):
                        # We may still be in the downloading state or the panel is in the downloading state
                        _my_panel_state_trigger_count = _my_panel_state_trigger_count - 1
                        log.debug(f"[_sequencer] By here we should be in normal operation, we are in {self.PanelMode.name} panel mode"
                                  f" and status is {self.PartitionState[0].PanelStateData}    {_my_panel_state_trigger_count=}")
                        if _my_panel_state_trigger_count < 0:
                            if self.PanelMode in [AlPanelMode.POWERLINK_BRIDGED]:
                                # Restart the sequence from the beginning
                                _sequencer_state = SequencerType.Reset
                            else:
                                _my_panel_state_trigger_count = 10
                                self._reset_keep_alive_messages()
                                self._reset_watchdog_timeout()
                                _resetPanelInterface()
                                self._clearPanelErrorMessages()
                                if self.pmDownloadComplete or self.ForceStandardMode:
                                    self._trigger_restore_status()     # Clear message buffers and send a Restore (if in Powerlink or standard plus) or Status (not in Powerlink) to the Panel
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
                            _sequencer_state = SequencerType.Reset
                        continue   # just do the while loop

                    _my_panel_state_trigger_count = 5

                    if self._panel_reset_event:
                        # If the user has been in to the installer settings there may have been changes that are relevant to this integration.
                        self._panel_reset_event = False
                        log.debug("[_sequencer] Performing a System Reset so reloading Panel Data")
                        self.send_panel_update (AlCondition.PANEL_RESET)   # push changes through to the host, the panel itself has been reset. Let user decide what action to take.
                        # Restart the sequence from Reset.
                        reset_local()
                        self._clear_despatcher()
                        _sequencer_state = SequencerType.Reset
                        continue   # just do the while loop

                    if not _sent_startup_success:
                        _sent_startup_success = True
                        self.send_panel_update(AlCondition.STARTUP_SUCCESS)   # startup completed successfully (in whatever mode)

                    self.EnableB0ReceiveProcessing = True

                    # If Std+ or PL then periodically check and then maybe update the time in the panel
                    if self.AutoSyncTime and self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:

                        int_diff_interval = (counter % 10 == 0
                                                and (self.Panel_Integration_Time_Difference is None
                                                    or (self.Panel_Integration_Time_Difference is not None
                                                        and abs(self.Panel_Integration_Time_Difference.total_seconds()) > 5)
                                                    )
                                                )

                        if self.is_power_master():
                            # PowerMaster Panels
                            if int_diff_interval:
                                log.debug("[_sequencer] Adding Panel and Sensor State requests - Set 0 due to int_diff_interval")
                                self.B0_Wanted.update(b0_periodic_update_list[0])  # the objective is to get the panel time, B0SubType.PANEL_STATE_1, so could use any of the lists as they all have it
                            elif (u := update_time_check_power_master(counter, POWERMASTER_CHECK_TIME_INTERVAL)) >= 0:
                                log.debug(f"[_sequencer] Adding Panel and Sensor State requests - Set {u}")
                                self.B0_Wanted.update(b0_periodic_update_list[u])

                        elif counter % POWERMAX_CHECK_TIME_INTERVAL == 0 or int_diff_interval: # counter % POWERMAX_CHECK_TIME_INTERVAL
                            # PowerMax Panels
                            # We set the time and then check it periodically, and then set it again if different by more than 5 seconds
                            #     every 4 hours (approx) or if not set yet or a big difference (set from B0 data)
                            # Get the time from the panel (this will compare to local time and set the panel time if different)
                            self.add_message_to_send_queue(Send.GETTIME, priority = MessagePriority.URGENT)

                    elif self.PanelMode in [AlPanelMode.STANDARD]:
                        if self.is_power_master():
                            # PowerMaster Panels
                            if (u := update_time_check_power_master(counter, STANDARD_STATUS_RETRY_DELAY)) >= 0:
                                log.debug(f"[_sequencer] Adding Panel and Sensor State requests - Set {u}")
                                self.B0_Wanted.update(b0_periodic_update_list[u])

                        elif (counter % STANDARD_STATUS_RETRY_DELAY) == 0:
                            # PowerMax Panels
                            log.debug("[_sequencer] Adding Panel for sensor status")
                            self.add_message_to_send_queue(Send.STATUS_SEN)

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
                                self.send_panel_update(AlCondition.PIN_REJECTED)  # push changes through to the host, the pin has been rejected
                            case PanelErrorStates.AccessDeniedCommand:
                                log.debug("[_sequencer] Attempt to send a command message to the panel that has been rejected")
                                self._reset_watchdog_timeout()
                                self.send_panel_update(AlCondition.COMMAND_REJECTED)  # push changes through to the host, something has been rejected (other than the pin)
                            case PanelErrorStates.Exit:
                                log.debug("[_sequencer] Received a Exit state, we assume that DOWNLOAD was called and rejected by the panel")
                                if Receive.PANEL_INFO in self.pmExpectedResponse:    # We sent DOWNLOAD to the panel (probably to set the time) and it has responded with EXIT
                                    self.pmExpectedResponse.remove(Receive.PANEL_INFO)
                            case PanelErrorStates.TimeoutReceived:
                                log.debug("[_sequencer] Received a Panel state Timeout")
                                # Reset Send state (clear queue and reset flags)
                                self._clear_receive_response_list()
                                #self._empty_send_queue(priority = MessagePriority.ACK)
                                dotrigger = True
                            case PanelErrorStates.DownloadRetryReceived:
                                log.debug("[_sequencer] Received a Download Retry and dont know why")
                                dotrigger = True
                            case PanelErrorStates.DespatcherException:
                                # restart the despatcher task
                                self._start_despatcher()
                            case PanelErrorStates.BeeZeroInvalidCommand:
                                log.debug("[_sequencer] Received a BeeZeroInvalidCommand")
                            case _:
                                log.debug(f"[_sequencer] Received an unexpected panel error state and dont know why {s}")
                                dotrigger = True

                    # Do the Watchdog functionality
                    self._watchdog_counter += 1
                    # every iteration, decrement all WATCHDOG_MAXIMUM_EVENTS watchdog counters (loop time is 1 second approx, doesn't have to be accurate)
                    watchdog_list = [x - 1 if x > 0 else 0 for x in watchdog_list]

                    if self._watchdog_counter >= WATCHDOG_TIMEOUT:  #  the loop runs at 1 second
                        # Check to see if the watchdog timer has expired
                        # watchdog timeout
                        log.debug("[_sequencer] ****************************** WatchDog Timer Expired ********************************")
                        self._reset_watchdog_timeout()
                        self._reset_keep_alive_messages()

                        # Total Watchdog timeouts
                        self.WatchdogTimeoutCounter += 1
                        # Total Watchdog timeouts in last 24 hours. Total up the entries > 0
                        self.WatchdogTimeoutPastDay = 1 + sum(1 if x > 0 else 0 for x in watchdog_list)    # in range 1 to 11

                        # move to the next position which is the oldest entry in the list
                        watchdog_pos = (watchdog_pos + 1) % WATCHDOG_MAXIMUM_EVENTS

                        # When watchdog_list[watchdog_pos] > 0 then the 24 hour period from the timeout WATCHDOG_MAXIMUM_EVENTS times ago hasn't decremented to 0.
                        #    So it's been less than 1 day for the previous WATCHDOG_MAXIMUM_EVENTS timeouts
                        self._clear_receive_response_list()
                        if not self._stop_trying_download and watchdog_list[watchdog_pos] > 0:
                            if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
                                log.debug("[_sequencer]               **************** Too many Timeouts in 24 hours and we're in Powerlink mode, going to re-establish panel connection *******************")
                                _sequencer_state = SequencerType.InitialisePanel
                                self.send_panel_update(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired
                                continue
                            if self.PanelMode in [AlPanelMode.STANDARD_PLUS]:
                                log.debug("[_sequencer]               **************** Too many Timeouts in 24 hours, but we're in Std+ so just Trigger Restore Status *******************")
                                self.send_panel_update(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired
                                # Reset Send state (clear queue and reset flags)
                                self._empty_send_queue(priority = MessagePriority.ACK)
                                dotrigger = True
                            else:
                                log.debug("[_sequencer]               **************** Too many Timeouts in 24 hours, giving up and going to Standard Mode *******************")
                                _gotoStandardModeStopDownload()
                                # Match _sequencer_state to the new self.PanelMode (that is set in _gotoStandardModeStopDownload)
                                if self.PanelMode == AlPanelMode.STANDARD_PLUS:
                                    _sequencer_state = SequencerType.DoingStandardPlus
                                else:
                                    _sequencer_state = SequencerType.DoingStandard
                                self.send_panel_update(AlCondition.WATCHDOG_TIMEOUT_GIVINGUP)   # watchdog timer expired, going to standard (plus) mode
                        else:
                            log.debug("[_sequencer]               **************** Trigger Restore Status *******************")
                            self.send_panel_update(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired, going to try again
                            # Reset Send state (clear queue and reset flags)
                            self._empty_send_queue(priority = MessagePriority.ACK)
                            dotrigger = True

                        # Overwrite the oldest entry and set it to 1 day in seconds. Keep the stats going in all modes for the statistics
                        #    Note that the asyncio 1 second sleep does not create an accurate time and this may be slightly more (but probably not less) than 24 hours.
                        watchdog_list[watchdog_pos] = 60 * 60 * 24  # seconds in 1 day
                        log.debug(f"[_sequencer]               Watchdog counter array, current={watchdog_pos}")
                        log.debug(f"[_sequencer]                       {watchdog_list}")

                    if dotrigger:
                        self._trigger_restore_status()     # Clear message buffers and send a Restore (if in Powerlink or standard plus) or Status (not in Powerlink) to the Panel

                    if self.image_manager.hasStartedSequence():
                        # Release an image the panel stopped sending part way through. Without this
                        # the record stays in progress for ever, and create() refuses every later
                        # request - for every camera, not just this one - until HA restarts.
                        if (dropped := self.image_manager.terminateIfExceededTimeout(IMAGE_TRANSFER_TIMEOUT)) is not None:
                            # The panel went quiet part way through. Nothing else reports this, so
                            # without it the user waits for a capture that is never coming.
                            _zone, _image_id = dropped
                            self.send_panel_update(AlCondition.IMAGE_UPDATE,
                                                   {"finished": True, "state": "failed", "zone": _zone,
                                                    "message": f"no image data for {IMAGE_TRANSFER_TIMEOUT} seconds, abandoned during image {_image_id}"})

                    # log.debug(f"[_sequencer] is {self._watchdog_counter}")

                    # We create a B0 message to request other B0 messages from a PowerMaster panel.
                    #    Wait 1 second per B0 request between sending again to give the panel a chance to send them
                    if self.is_power_master() and self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]: # not AlPanelMode.MINIMAL_ONLY
                        tnow = get_local_time()
                        diff = (tnow - _last_b0_wanted_request_time).total_seconds()
                        if self.image_manager.isSequenceActive():
                            # A Camera PIR download is underway. Sending B0 requests part way through makes the
                            # panel break off the F4 stream and answer the B0 instead, so hold off. B0_Wanted
                            # accumulates and goes out once the images have finished.
                            log.debug("[_sequencer] Deferring B0 requests, camera image download in progress")
                        elif self._is_send_queue_empty() and diff >= 10: # There must be at least 10 seconds between subsequent requests
                            if len(self.B0_Waiting) > 0:  # have we received the data that we last asked for last time
                                log.debug(f"[_sequencer] ****************************** Waiting For B0_Waiting **************************** {toStringList(self.B0_Waiting)}")
                                self.B0_Wanted.update(self.B0_Waiting) # ask again for them
                            if len(self.B0_Wanted) > 0:
                                log.debug(f"[_sequencer] ****************************** Asking For B0_Wanted **************************** {toStringList(self.B0_Wanted)}     timediff={diff}")
                                tmp = [pmSendMsgB0[i].data if i in pmSendMsgB0 else i for i in self.B0_Wanted]  # self.B0_Wanted can contain State enumerations or the integer of the message subtype
                                self.B0_Wanted = set()
                                s = self._create_B0_Data_Request(taglist = set(tmp))
                                self.add_message_to_send_queue(s)
                                self.B0_Waiting.update(set(tmp))
                                _last_b0_wanted_request_time = tnow

                    # Dump all sensors to the file every 60 seconds (1 minute)
                    log_sensor_state_counter = log_sensor_state_counter + 1
                    if log_sensor_state_counter >= 60:
                        log_sensor_state_counter = 0
                        self._dumpAllDevicesToLogFile()

            except Exception as ex:
                tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                log.error(f"[_sequencer] Visonic Executor loop has caused an exception \n\n{tb_str}")
                reset_local()
                self._start_despatcher()


######################################################################################################################################
#################  Support functions for the sequencer  ##############################################################################
######################################################################################################################################

    def _create_B0_35_Data_Request(self, taglist : list | None = None, strlist : str | None = None) -> bytearray:
        if taglist is None and strlist is None:
            return bytearray()
        if taglist is not None:
            pmaster_request_data = bytearray(taglist)
        elif strlist is not None:
            log.debug(f"[_create_B0_35_Data_request] {strlist}")
            pmaster_request_data = convert_bytearray(strlist)
        else:
            log.debug("[_create_B0_35_Data_request] Error not sending anything as both params set")
            return bytearray()

        pmaster_request_start = convert_bytearray('b0 01 35 99 02 ff 08 ff 99')  # The 2 means that each data parameter is 2 bytes
        pmaster_request_end   = bytearray([Packet.POWERLINK_TERMINAL])          # create from a list

        pmaster_data = pmaster_request_start + pmaster_request_data + pmaster_request_end

        pmaster_data[3] = len(pmaster_request_data) + 5
        pmaster_data[8] = len(pmaster_request_data)

        checksum = self._calculateCRC(pmaster_data)   # returns a bytearray with a single byte
        to_send = bytearray([Packet.HEADER]) + pmaster_data + checksum + bytearray([Packet.FOOTER])

        log.debug(f"[_create_B0_35_Data_request] Returning {toString(to_send)}")
        return to_send

    def _create_B0_42_Data_Request(self, taglist : list | None = None, strlist : str | None = None) -> bytearray:
        if taglist is None and strlist is None:
            return bytearray()
        if taglist is not None:
            pmaster_request_data = bytearray(taglist)
        elif strlist is not None:
            #log.debug(f"[_create_B0_42_Data_request] {strlist}")
            pmaster_request_data = convert_bytearray(strlist)
        else:
            log.debug("[_create_B0_42_Data_request] Error not sending anything as both params set incorrectly")
            return bytearray()

        if len(pmaster_request_data) % 2 == 1:  # its an odd number of bytes
            log.debug(f"[_create_B0_42_Data_request] Error not sending anything as its an odd number of bytes {toString(pmaster_request_data)}")
            return bytearray()
        if len(pmaster_request_data) == 2:
            pmaster_request_data.extend(convert_bytearray("00 00 ff ff"))
            special = 2
        elif len(pmaster_request_data) == 4:
            pmaster_request_data.extend(convert_bytearray("00 00"))
            special = 6
        else:
            special = 6

        pmaster_request_start = convert_bytearray(f'b0 01 42 99 0{special} ff 08 0c 99')
        pmaster_request_end   = bytearray([Packet.POWERLINK_TERMINAL])

        pmaster_data = pmaster_request_start + pmaster_request_data + pmaster_request_end

        pmaster_data[3] = len(pmaster_request_data) + 5
        pmaster_data[8] = len(pmaster_request_data)

        checksum = self._calculateCRC(pmaster_data)   # returns a bytearray with a single byte
        return bytearray([Packet.HEADER]) + pmaster_data + checksum + bytearray([Packet.FOOTER])

    def _create_B0_Data_Request(self, taglist : set | None = None) -> bytearray:

        if taglist is None or len(taglist) == 0:
            taglist = {pmSendMsgB0[B0SubType.PANEL_STATE_1].data}
            log.debug("[_create_B0_Data_Request] Taglist is empty so asking for PANEL_STATE_1")

        pmaster_request_data = bytearray(set(taglist))                          # just to make sure there are no duplicates
        pmaster_request_start = convert_bytearray('b0 01 17 99 01 ff 08 ff 99')
        pmaster_request_end   = bytearray([Packet.POWERLINK_TERMINAL])

        pmaster_data = pmaster_request_start + pmaster_request_data + pmaster_request_end

        pmaster_data[3] = len(pmaster_request_data) + 5   # Was 6 but removed counter at the end!!!!!!
        pmaster_data[8] = len(pmaster_request_data)

        checksum = self._calculateCRC(pmaster_data)   # returns a bytearray with a single byte
        to_send = bytearray([Packet.HEADER]) + pmaster_data + checksum + bytearray([Packet.FOOTER])

        log.debug(f"[_create_B0_Data_Request] Returning {toString(to_send)}")
        return to_send

    def _process_switch_settings(self):
        # Process Switch settings

        switch_device_max = self._get_panel_capability(IndexName.SWITCHES)

        if switch_device_max > 0:

            log.debug(f"[Process Settings]     Processing switch devices     Panel Type supports up to {switch_device_max} switch devices plus a PGM")

            data = [EPROM.SWITCH_BYARMAWAY, EPROM.SWITCH_BYARMHOME, EPROM.SWITCH_BYDISARM, EPROM.SWITCH_BYDELAY, EPROM.SWITCH_BYMEMORY, EPROM.SWITCH_BYKEYFOB, EPROM.SWITCH_ACTZONEA, EPROM.SWITCH_ACTZONEB, EPROM.SWITCH_ACTZONEC ]

            expected_len = switch_device_max + 1
            s = []
            for eprom_key in data:
                e = self.epromManager.lookupEprom(eprom_key, expected_len)
                log.debug(f"[Process Settings]             Processing switch devices e={e}")
                if len(e) == expected_len:
                    s.append(e)

            log.debug(f"[Process Settings]             Processing switch devices s={s}")

            switch_name_list = self.epromManager.lookupEprom(EPROM.SWITCH_ZONENAMES, switch_device_max + 1)  # 0 = PGM, 1 = X01

            if len(data) != len(s) or len(switch_name_list) != switch_device_max + 1:
                log.debug(f"[Process Settings]              There has been a problem loading EPROM switch data {len(data)} != {len(s)}  or  {len(switch_name_list)} != {switch_device_max + 1}")
            elif isinstance(switch_name_list, bytearray):
                log.debug(f"[Process Settings]            switch device EPROM Name Data {toString(switch_name_list)}")
            else:
                log.debug(f"[Process Settings]            switch device EPROM Name Data {switch_name_list}")

                # Start at 1 to exclude the PGM, we always create the PGM
                for i in range(1, len(s[0])):
                    # Check if any row at column i is not DISABLE_TEXT
                    switch_enabled = any(row[i] != DISABLE_TEXT for row in s)

                    switch_name = switch_name_list[i] & 0x1F   # Ensure in range 0 to 31

                    if switch_enabled or switch_name != 0x1F:
                        switch_location = pmZoneName[switch_name]
                        switch_type = "onoff"            # Assume PGM is onoff switch, also make other devices onoff Switches
                        if i in self.SwitchList:
                            self.SwitchList[i].switch_type = switch_type
                            self.SwitchList[i].location = switch_location
                            self.SwitchList[i].state = False
                        else:
                            self.SwitchList[i] = AlSwitchDeviceHelper(switch_type=switch_type, location=switch_location, id=i, enabled=True)
                            self.SwitchList[i].add_callback(self.switch_change_handler)
                            if self.onNewSwitchHandler is not None:
                                self.onNewSwitchHandler(True, self.SwitchList[i])

                log.debug(f"[Process Settings]     Processed switch devices, you have {len(self.SwitchList)} switch devices")
        else:
            log.debug("[Process Settings]     Panel Type does not support switch devices")

    def _create_PGM_switch(self):
        # Process PGM settings
        #has_pgm = pmPanelConfig[CFG.PGM][self.PanelType]
        if self._get_panel_capability(IndexName.PGM) > 0:
            location = "PGM"
            stype = "onoff"             # Assume PGM is onoff switch, all other devices are dimmer Switches
            if 0 in self.SwitchList:
                self.SwitchList[0].switch_type = stype
                self.SwitchList[0].location = location
                self.SwitchList[0].state = False
            else:
                self.SwitchList[0] = AlSwitchDeviceHelper(switch_type=stype, location=location, id=0, enabled=True)
                self.SwitchList[0].add_callback(self.switch_change_handler)
                log.debug("[Process Settings]             Creating PGM Switch")
                if self.onNewSwitchHandler is not None:
                    self.onNewSwitchHandler(True, self.SwitchList[0])

    def _fetch_panel_status(self, priority : MessagePriority):
        if self.is_power_master():
            s = self._create_B0_Data_Request(taglist = {pmSendMsgB0[B0SubType.PANEL_STATE_1].data} )
            self.add_message_to_send_queue(s, priority = priority)
        else:
            self.add_message_to_send_queue(Send.STATUS_SEN, priority = priority)

