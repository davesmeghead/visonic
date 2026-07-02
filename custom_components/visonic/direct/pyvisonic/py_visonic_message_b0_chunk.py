"""Process the B0 message chunk."""

# ruff: noqa: G004, C901

from datetime import datetime, timedelta
import logging
from textwrap import wrap

from .py_const import OBFUS, notknown
from .py_enum import (
    CFG,
    EVENT_TYPE,
    PANEL_STATUS,
    RAW,
    SEQUENCE,
    AlPanelMode,
    B0SubType,
    DataType,
    IndexName,
    MessagePriority,
    PanelSetting,
)
from .py_generic_device import AlGenericDeviceHelper, GenericDeviceType
from .py_panel_settings import pmMapZoneType, pmPanelSettingCodes
from .py_panel_type_data import pmPanelConfig, pmPanelType
from .py_sensor_types import ZoneFunctions
from .py_types_receiving import Chunky, pmPanelSettingsB0_35, pmPanelSettingsB0_42
from .py_types_sending import pmSendMsgB0, pmSendMsgB0_reverseLookup
from .py_utils import b2i, get_local_time, get_utc_time, hexify, toString
from .py_visonic_message_base import MessageHandlingBase

log = logging.getLogger(__name__)


class MessageHandlingB0Data(MessageHandlingBase):
    """Message Handling. Process the B0 message chunk."""

    def __init__(self, loop, force_standard_mode : bool, disable_all_commands : bool, download_code : str, user_code_slot: int, logger = None) -> None:
        """Initialize class."""
        super().__init__(loop=loop, force_standard_mode=force_standard_mode, disable_all_commands=disable_all_commands, download_code=download_code, user_code_slot=user_code_slot, logger=logger)

    def _settings_data_type_formatter( self, data_type: int, data: bytes, data_item_size: int = 16, byte_size: int = 1, no_of_entries: int = 1 ) -> int | str | bytearray | list:
        """Format data for 35 and 42 data."""

        match data_type:
            case DataType.ZERO_PADDED_STRING:
                return data.decode("ascii", errors="ignore").rstrip("\x00")     # \x00 padded string

            case DataType.DIRECT_MAP_STRING:
                datalen = int(len(data) / no_of_entries)
                return data.hex() if no_of_entries == 1 else [ data[i:i+datalen].hex() for i in range(0, no_of_entries, datalen) ]

            case DataType.FF_PADDED_STRING:
                return data.hex().replace("ff", "")

            case DataType.DOUBLE_LE_INT:  # 2 byte int
                return [b2i(data[i : i + 2]) for i in range(0, len(data), 2)] if len(data) > 2 else b2i(data[0:2])

            case DataType.INTEGER:  # 1 byte int?
                # Assume 1 byte int list
                return b2i(data) if len(data) == byte_size else [ b2i(data[i : i + byte_size]) for i in range(0, len(data), byte_size) ]

            case DataType.STRING:
                return data.decode("ascii", errors="ignore")

            case DataType.SPACE_PADDED_STRING: # Space padded string
                return data.decode("ascii", errors="ignore").rstrip(" ")

            case DataType.SPACE_PADDED_STRING_LIST: # Space paddeded string list - seems all 16 chars
                # Cmd 35 0d 00 can include a \x00 instead of \x20 (space)
                # Remove any \x00 also when decoding.
                names = wrap(data.decode("ascii", errors="ignore"), data_item_size)
                return [ name.replace("\x00", "").rstrip(" ") for name in names if name.replace("\x00", "").rstrip(" ") != "" ]

        return data.hex(" ")

    def _extract_35_data(self, ch: Chunky):
        #03 35 0b ff 08 ff 06 00 00 01 00 00 00 02 43
        #data_content = b2i(data[0:2], big_endian=False)
        data_content_a = ch.data[0]
        data_content_b = ch.data[1]
        data_content = (data_content_b << 8) | data_content_a
        datatype = ch.data[2]        # 6 is a String
        datalen = ch.length - 3
        data = ch.data[3:]
        log.debug("[_extract_35_data]     ***************************** Panel Settings ********************************")
        dat = self._settings_data_type_formatter(datatype, data)

        if not OBFUS:
            log.debug(f"[_extract_35_data]           data_content={hex(data_content)} panel setting   { DataType(datatype) }  {datalen=}    data={toString(data)}")
            log.debug(f"[_extract_35_data]               dat type = {type(dat)}   dat = {dat}")

        processed_data = False

        building = False
        if dat is not None and (d := pmPanelSettingsB0_35.get(data_content)) is not None:
            if d.processinstandard or not self.ForceStandardMode: #  self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
                if d.length in (0, d.length) and datatype == d.datatype:
                    # Check the PanelSettings to see if there's one that refers to this data_content
                    for key, value in pmPanelSettingCodes.items():
                        if value.PMasterB035Panel == data_content:
                            log.debug(f"[_extract_35_data]          Matched it {key=}")
                            processed_data = True
                            if d.sequence is not None and isinstance(d.sequence, list): # We have a list of sequence identifiers e.g. [1,2,255]
                                # TODO: We should really check to make sure that we get all messages in the list but not yet
                                # I'm assuming that 0x35 messages (and 0x42 maybe) are the only messages with sequences
                                if ch.datasize == RAW.BYTE.value and datatype == DataType.SPACE_PADDED_STRING_LIST:
                                    if key not in self.builderData:
                                        self.builderData[key] = []                    # empty the data list to concatenate the sequenced message data
                                        self.builderMessage[key] = d.sequence         # get the list of sequences there needs to be to complete the data
                                    if ch.sequence in self.builderMessage[key]:
                                        self.builderData[key].extend(dat)             # Add actual data to the end of the list, we assume that the panel sends the data in order and we don't need to check the order
                                        self.builderMessage[key].remove(ch.sequence)  # Got this sequence data so remove from the list
                                    else:
                                        log.debug(f"[_extract_35_data]                        building {key}   Unexpected data sequence {ch.sequence} or sequence sent more than once")
                                    if ch.sequence == 255:
                                        if len(self.builderMessage[key]) > 0:
                                            # Received the sequence end but we are missing some of the sequence
                                            log.debug(f"[_extract_35_data]                        building {key}   We have the sequence terminator message but we still have missing sequenced messages {self.builderMessage[key]}.  Dumping all message data and not using it")
                                        else:
                                            # copy across to use it
                                            self.PanelSettings[key] = self.builderData[key]
                                    if d.display:
                                        log.debug(f"[_extract_35_data]                        building {key}   {self.builderData[key]}")
                                    building = ch.sequence != 255
                            else:
                                self._update_panel_setting(key = key, length = ch.length, datasize = ch.datasize, data = data, display = d.display, msg = d.msg)
                            break
                else:
                    log.debug(f"[_extract_35_data]               {d.msg} data lengths differ: {ch.length=} {d.length=}   type: {datatype=} {d.datatype=}")
            else:
                log.debug(f"[_extract_35_data]               {d.msg} not processed as this specifically prevented in standard mode")
        else:
            log.debug(f"[_extract_35_data]               data_content={hex(data_content)} panel setting unknown      {datatype=}  {datalen=}    data={toString(ch.data[3:])}")

        if not building:
            # remove it from the dictionary
            self.builderMessage = {}
            self.builderData = {}

        if data_content == 0x000F and datalen == 2 and isinstance(dat,str):
            processed_data = True
            if not self.DownloadCodeUserSet and len(dat) == 4:
                self.DownloadCode = dat
                self.DownloadCodeUserSet = True    # Set to True as the download code has been obtained directly from the panel so it mist be correct
                self.PanelSettings[PanelSetting.PanelDownload] = self.DownloadCode
                log.debug(f"[_extract_35_data]               Setting Download Code : {self.DownloadCode}")

        elif data_content == 0x003C and datalen == 15 and isinstance(dat,str): # 8 is a string
            processed_data = True
            if not self.pmGotPanelDetails:
                name = dat.replace("-"," ")
                log.debug(f"[_extract_35_data] Panel Name {name}.  Not got panel details so trying to reconcile:")
                for p,v in pmPanelType.items():
                    log.debug(f"[_extract_35_data]     Checking: {p} {v}")
                    if name == v:
                        log.debug(f"[_extract_35_data] Fount it: {v}")
                        self.ModelType = 0xDA7E  # No idea what model type it is so just set it to a valid number, DAVE
                        if not self._set_data_from_panel_type(p, self.pmForceDownloadByEPROM):
                            log.debug(f"[_extract_35_data] Panel Type {data[5]} Unknown")
                        else:
                            log.debug(f"[_extract_35_data] PanelType={self.PanelType} : {self.PanelModel} , Model={hexify(self.ModelType)}   Powermaster {self.PowerMaster}")
                            self.pmGotPanelDetails = True
                        break
            else:
                log.debug("[_extract_35_data] Not Processed as already got Panel Details")

        elif data_content == 0x0030 and datalen == 1 and isinstance(dat,int):
            processed_data = True
            if dat == 0:
                log.debug("[_extract_35_data] Seems to indicate that partitions are disabled in the panel")
            else:
                log.debug(f"[_extract_35_data] Seems to indicate that partitions are enabled in the panel, but nothing done with this data, dat={dat}")
            self.PanelSettings[PanelSetting.PartitionEnabled] = [dat]   # this just stops it being mandatory again, it is not used

        if not processed_data and dat is not None:
#            if not OBFUS:
            log.debug(f"[_extract_35_data]               NOT PROCESSED dat = {dat}")
        log.debug("[_extract_35_data]     ***************************** Panel Settings Exit ***************************")

    def _extract_42_data(self, ch: Chunky): # -> tuple[int, str | int | list[str | int]]:
        """Format a command 42 message.

        This has many parameter options to retrieve EPROM settings.
        bytes 0 & 1 are the parameter
        bytes 2 & 3 is the max number of data items
        bytes 4 & 5 is the size of each data item (in bits)
        bytes 6 & 7 don't know
        bytes 8     is the data type
        bytes 9     byte_size
        bytes 10 & 11 is the start index of data item
        bytes 12 & 13 is the number of data items
        bytes 14 to end is data

        """

        #def chunk_bytearray(data: bytearray, size: int) -> None | list[bytes]:
        #    """Split bytearray into sized chunks."""
        #    if data:
        #        return [data[i : i + size] for i in range(0, len(data), size)]
        #    return None

        data_content = b2i(ch.data[0:2])
        max_data_items = b2i(ch.data[2:4])
        data_item_size = max(1, int(b2i(ch.data[4:6]) / 8))
        _not_known = b2i(ch.data[6:8])
        datatype = ch.data[8]  # This is actually 2 bytes, what is second byte??
        byte_size = 2 if ch.data[9] == 0 else 1
        start_entry = b2i(ch.data[10:12])
        no_of_entries = b2i(ch.data[12:14])

        log.debug(f"[_extract_42_data]               {data_content=}   {max_data_items=}   {data_item_size=}   {start_entry=}   { DataType(datatype) if datatype in DataType else "DataType is UNDEFINED" }   {byte_size=}   {no_of_entries=}")

        ###################################################################################
        dat = self._settings_data_type_formatter(datatype, ch.data[14:], data_item_size=data_item_size, byte_size=byte_size, no_of_entries=no_of_entries)
        if dat is None:
            log.debug("[_extract_42_data]               dat is NONE")
        elif OBFUS:
            log.debug(f"[_extract_42_data]               dat type = {type(dat)}   dat = OBFUSCATED")
        else:
            log.debug(f"[_extract_42_data]               dat type = {type(dat)}   dat = {dat}")
        ###################################################################################

        processed_data = False

        if dat is not None and (d := pmPanelSettingsB0_42.get(data_content)) is not None:
            #log.debug(f"[_extract_42_data]                  DataContent {d=}")
            if d.processinstandard or not self.ForceStandardMode: #  self.PanelMode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
                if d.length in (0, d.length) and datatype == d.datatype:
                    # Check the PanelSettings to see if there's one that refers to this data_content
                    for key, value in pmPanelSettingCodes.items():
                        if value.PMasterB042Panel == data_content:
                            log.debug(f"[_extract_42_data]          Matched it {key=}")
                            processed_data = True
                            if ch.datasize == RAW.BYTE.value and datatype == DataType.SPACE_PADDED_STRING_LIST:
                                s = f"{self.PanelSettings[key]}"              # Save the data before the update
                                log.debug(f"[_extract_42_data]               dat {dat}")
                                if len(self.PanelSettings[key]) <= start_entry:
                                    aa = [f"Undefined{i}" for i in range(len(self.PanelSettings[key]), start_entry+1)]
                                    self.PanelSettings[key].extend(aa)
                                if not isinstance(dat, int):
                                    self.PanelSettings[key][start_entry:start_entry+no_of_entries] = dat[0:no_of_entries]
                                log.debug(f"[_extract_42_data]               before {s}")
                                log.debug(f"[_extract_42_data]               after  {self.PanelSettings[key]}")
                            else:
                                self._update_panel_setting(key = key, length = ch.length, datasize = ch.datasize, data = ch.data[14:], display = d.display, msg = d.msg)
                else:
                    log.debug(f"[_extract_42_data]               {d.msg} data lengths differ: {ch.length=} {d.length=}   type: {datatype=} {d.datatype=}")
            else:
                log.debug(f"[_extract_42_data]               {d.msg} not processed as this specifically prevented in standard mode")
        else:
            log.debug(f"[_extract_42_data]               data_content={hex(data_content)} panel setting unknown      {datatype=}  data={toString(ch.data)}")

        if data_content == 0x000F and data_item_size == 2 and isinstance(dat,str) and len(dat) == 4:
            processed_data = True
            if not self.DownloadCodeUserSet and len(dat) == 4:
                self.DownloadCode = dat
                self.DownloadCodeUserSet = True    # Set to True as the download code has been obtained directly from the panel so it mist be correct
                self.PanelSettings[PanelSetting.PanelDownload] = self.DownloadCode
                log.debug(f"[_extract_42_data]               Setting Download Code : {self.DownloadCode}")

        elif data_content == 0x003C and data_item_size == 15 and max_data_items == 1 and no_of_entries == 1 and isinstance(dat,str):
            processed_data = True
            name = dat.replace("-"," ")
            self.PanelSettings[PanelSetting.PanelName] = name
            if not self.pmGotPanelDetails:
                log.debug(f"[_extract_42_data] Panel Name {name}.  Not got panel details so trying to reconcile:")
                for p,v in pmPanelType.items():
                    log.debug(f"[_extract_42_data]     Checking: {p} {v}")
                    if name.lower() == v.lower():
                        log.debug(f"[_extract_42_data] Fount it: {v}")
                        self.ModelType = 0xDA7E  # No idea what model type it is so just set it to a valid number, DAVE
                        if self._set_data_from_panel_type(p, self.pmForceDownloadByEPROM):
                            log.debug(f"[_extract_42_data] PanelType={self.PanelType} : {self.PanelModel} , Model={hexify(self.ModelType)}   Powermaster {self.PowerMaster}")
                            self.pmGotPanelDetails = True
                        else:
                            log.debug(f"[_extract_42_data] Panel Type {ch.data[5]} Unknown")
                        break
            else:
                log.debug("[_extract_42_data] Not Processed as already got Panel Details")

        elif data_content == 0x0030 and data_item_size == 1 and max_data_items == 1 and isinstance(dat,int):
            pd = self.PanelSettings.get(PanelSetting.PartitionData, "Undefined")
            log.debug(f"[_extract_42_data]     partitiondata set as {pd}")
            processed_data = True
            log.debug(f"[_extract_42_data]         processing 0x0030     {dat}")
            if dat == 0:
                log.debug("[_extract_42_data]          Seems to indicate that partitions are disabled in the panel")
            else:
                log.debug(f"[_extract_42_data]          Seems to indicate that partitions are enabled in the panel {dat}")
            self.PanelSettings[PanelSetting.PartitionEnabled] = [dat]   # this just stops it being mandatory again, it is not used

        if not processed_data and dat is not None:
#            if not OBFUS:
            log.debug(f"[_extract_42_data]               NOT PROCESSED dat = {dat}")

    def _decode_24(self, partitionCount : int, dateData: bytearray, unknownData : bytearray, partitionData : bytearray):

        i_sec = dateData[0]
        i_min = dateData[1]
        i_hour = dateData[2]
        i_day = dateData[3]
        i_month = dateData[4]
        i_year = dateData[5]

        # Attempt to check and correct time
        pt = datetime(2000 + i_year, i_month, i_day, i_hour, i_min, i_sec).astimezone()
        self._set_time_in_panel(pt)
        messagedate = f"{i_day:0>2}/{i_month:0>2}/{i_year}   {i_hour:0>2}:{i_min:0>2}:{i_sec:0>2}"

        unknown1 = unknownData[0]
        unknown2 = unknownData[1]
        self._check_unknown("[handle_msgtypeB0]              Decode 24 Message unknownData is different to last time", "handle_msgtypeB0_24", toString(unknownData))

        log.debug(f"[_decode_24]    Panel time is {pt}  date={messagedate}    data (hex) 14={hex(unknown1)}  15={hex(unknown2)}  PartitionCount={partitionCount}")

        for i in range(partitionCount):
            offset = i * 4
            # Repeat 4 bytes (17 to 20) for more than 1 partition, assume 19 and 20 are zone data
            self._updatePartitionStatus(i, partitionData[offset], partitionData[offset + 1], partitionData[offset + 2], partitionData[offset + 3])

    def _decode_4B(self, sensor_identifier, data):
        # Get local time
        t = get_local_time()
        # create an integer from the B0 data, this is the number of seconds since the epoch (00:00 on 1st Jan 1970)
        hs = b2i(data[0:4])
        # Make a datetime from it using the same timezone but subtract off the difference between local time and UTC
        offset = t.utcoffset()
        trigger = datetime.fromtimestamp(hs, tz=t.tzinfo) - (offset if offset is not None else timedelta(0))
        code = int(data[4])
        # 00 - Not a zone
        # 01 - Open (need to check timestamp)
        # 02 - Closed (need to check timestamp)
        # 03 - Motion (need to check timestamp)
        # 04 - CheckedIn?  As in device checked in.
        if sensor_identifier in self.SensorList:
            sensor = self.SensorList[sensor_identifier]
            if code in [0,4]:
                sensor.status_log = trigger
            elif code in [1,2,3]:
                triggered = False
                if self.Panel_Integration_Time_Difference is not None:  # Can only be True if AB messages are processed, therefore Std+ or Powerlink
                    tolerance = 4 # seconds
                    panel_time = t + self.Panel_Integration_Time_Difference
                    diff = abs((trigger - panel_time).total_seconds())
                    log.debug(f"[_decode_4B]           Sensor Updated = {sensor.id:>2}  timenow = {t}  self.Panel_Integration_Time_Difference {self.Panel_Integration_Time_Difference.total_seconds()}    diff {diff}     panel_time {panel_time}     trigger {trigger}")
                    triggered = diff <= tolerance

                else:
                    log.debug(f"[_decode_4B]           Sensor Updated = {sensor.id:>2}  trigger {trigger}")
                    triggered = sensor.status_log is None or (trigger - sensor.status_log) >= timedelta(milliseconds=500)

                if triggered:
                    log.debug(f"[_decode_4B]           Sensor Updated = {sensor.id:>2}  code {code}")
                    if code == 1:
                        sensor.do_status(True)
                    elif code == 2:
                        sensor.do_status(False)
                    elif code == 3:
                        sensor.do_trigger(True)
                    else:
                        log.debug("[_decode_4B]          ***************************** Sensor Updated with an unused code *****************************")
                    log.debug(f"[_decode_4B]                  my time {sensor.last_trigger_time}    panels time {trigger}")

                    sensor.status_log = trigger
                else:
                    log.debug(f"[_decode_4B]           Sensor {sensor.id:>2} Not Updated as Timestamp the same   code {code}     sensor time {trigger}     {sensor.status_log}")
            else:
                log.debug(f"[_decode_4B]           Abnormal: Sensor {sensor.id:>2} Not Updated as data code {code} not known")

    def _process_B0_log_entry(self, total, current, data):
        # PM10
        #    -- time ---          Ev Pt               Pt = Partition I think.  Partition 0 is System or the Panel itself.
        #    3f 71 02 67 03 00 00 5c 00 04            data[4] seems to always be 03, 06 or 0C.  device type - 0c - panel, 09 - plink, 03 - zones
        #    69 3a 01 67 0c 00 00 1c 00 53            data[5] if device type is zones, this is the zero based zone id
        #    69 3a 01 67 06 00 00 1b 01 52

        if self.onPanelLogHandler is not None:
            # There's no point in doing all of this if there's no handler to send it to!
            # extract the time as "epoch time" and convert to normal time
            hs = b2i(data[0:4])
            pmtime = datetime.fromtimestamp(hs)
            #log.debug(f"[handle_msgtypeA0]   Powermaster time {hs} as hex {hex(hs)} from epoch is {pmtime}")
            device_type = data[4]
            event_zone = 0
            if device_type == 3:          # device type =>  0c - panel, 09 - plink, 03 - zone
                event_zone = data[5] + 1  # if device type is zone, zero based zone id

            partition = data[8]
            # Send the event log in to HA
            #     Do not use timezone times as it was the log created on that day at that time
            self.onPanelLogHandler(total = total, current = current, partition = partition, dateandtime = pmtime, zone = event_zone, event = data[7])

    def process_chunk(self, ch : Chunky):
        """Process the B0 message chunk."""

        # Whether to process the experimental code (and associated B0 message data) or not
        beezerodebug = True
        #beezerodebug2 = True
        beezerodebug4 = True
        beezerodebug7 = True

        st = pmSendMsgB0_reverseLookup[ch.subtype].data if ch.subtype in pmSendMsgB0_reverseLookup else None

        if self.beezero_024B_sensorcount is not None and st != B0SubType.ZONE_LAST_EVENT:
            self.beezero_024B_sensorcount = None   # If theres a next time so they are coordinated
            log.debug(f"[handle_msgtypeB0]        Resetting beezero_024B_sensorcount st=<{st}>")

        if st is None:
            log.debug(f"[handle_msgtypeB0]     {notknown} chunk={ch.GetItAll()}")
            return

        ind = IndexName(ch.index) if ch.index in IndexName else IndexName.UNDEFINED
        datasize = RAW(ch.datasize) if ch.datasize in RAW else RAW.UNDEFINED
        seq_type = SEQUENCE(ch.type) if ch.type in SEQUENCE else SEQUENCE.UNDEFINED

        #log.debug(f"[handle_msgtypeB0]     st = {st}      chunky = {ch}      self.beezero_024B_sensorcount = {self.beezero_024B_sensorcount}") # [_process_chunk]                 chunky = sequence 255  datasize 40  index 3   length 140
        if datasize == RAW.UNDEFINED:
            log.debug(f"[handle_msgtypeB0]     {notknown} - datasize is undefined, chunk={ch.GetItAll()}")

        match (st, datasize, ind, ch.length):

            case (B0SubType.PANEL_SETTINGS_35, _    , _    ,  _ ):
                #log.debug(f"[handle_msgtypeB0]          Got PANEL_SETTINGS_35 {ch}")
                # I'm 100% sure this is correct
                self._extract_35_data(ch)
                self._update_all_sensors()

            case (B0SubType.PANEL_SETTINGS_42, _    , _    ,  _ ):
                #log.debug(f"[handle_msgtypeB0]          Got PANEL_SETTINGS_42 {ch}")
                self._extract_42_data(ch)
                self._update_all_sensors()

            case (B0SubType.PANEL_STATE_1,    RAW.BYTE, IndexName.MIXED,  20):
                # Panel state change, added just in case the panel abbreviates this message
                # 06 00 00 00 02 00 00 00 29 0b 10 08 0b 18 14 06 00 85 00 00
                #_decode_24(ch.data, 8, 14, 1, 17)
                #self.B0_LastPanelStateTime = get_utc_time()
                log.debug(f"[handle_msgtypeB0]              {notknown} Got a short Panel State Message but not processing it ch={ch}")

            case (B0SubType.PANEL_STATE_1,    RAW.BYTE, IndexName.MIXED,  21):
                # Panel state change, no idea what bytes 0 to 7 mean.
                # e.g. 06 00 00 00 02 00 00 00 29 0b 10 08 0b 18 14 06 01 00 85 00 00
                if ch.data[16] == 1:   # partition count set to 1
                    # We already know that the length of the ch.data is 21 so no need to check it
                    self._decode_24(1, ch.data[8:14], ch.data[14:16], ch.data[17:21])
                    self.B0_LastPanelStateTime = get_utc_time()
                else:
                    log.debug(f"[handle_msgtypeB0]              {notknown} Got a normal Panel State Message but not processing it because the partition count in the message is not 1 ch={ch}")
                self._check_unknown("[handle_msgtypeB0]              B0 Message PANEL_STATE_1 and the first 8 bytes", "handle_msgtypeB0_PANEL_STATE_1_21", toString(ch.data[0:8]))

            case (B0SubType.PANEL_STATE_1,    RAW.BYTE, IndexName.MIXED, 28):
                # Panel state change, no idea what bytes 0 to 7 mean. - the user that has the panel that sends this uses all 3 partitions
                # e.g. 0b 00 00 00 00 00 00 00 22 32 14 03 05 19 14 07 00 85 00 00 00 85 00 00 00 85 00 00
                if self.get_partitions_in_use() is not None:              # we have a 24 message that has extended data (for the partitions) and the panel has reported it has partitions in use
                    # We already know that the length of the ch.data is 28 so no need to check it
                    self._decode_24(3, ch.data[8:14], ch.data[14:16], ch.data[16:28])
                    self.B0_LastPanelStateTime = get_utc_time()
                else:
                    log.debug(f"[handle_msgtypeB0]              {notknown} Got a long Panel State Message but the partition count is 1, processing it anyway ch={ch}")
                    self._decode_24(3, ch.data[8:14], ch.data[14:16], ch.data[16:28])
                    self.B0_LastPanelStateTime = get_utc_time()
                self._check_unknown("[handle_msgtypeB0]              B0 Message PANEL_STATE_1 and the first 8 bytes", "handle_msgtypeB0_PANEL_STATE_1_28", toString(ch.data[0:8]))

            case (B0SubType.PANEL_STATE_1,    RAW.BYTE, IndexName.MIXED,  29):
                # Panel state change, no idea what bytes 0 to 7 mean.
                # e.g. 07 00 00 00 02 00 00 00 10 1d 0a 0a 0b 18 14 01 03 00 87 00 00 00 87 00 00 00 07 00 00
                if self.get_partitions_in_use() is not None:              # we have a 24 message that has extended data (for the partitions) and the panel has reported it has partitions in use
                    # We already know that the length of the ch.data is 29 so no need to check it
                    self._decode_24(ch.data[16], ch.data[8:14], ch.data[14:16], ch.data[17:29])
                    self.B0_LastPanelStateTime = get_utc_time()
                else:
                    log.debug(f"[handle_msgtypeB0]              {notknown} Got a really long Panel State Message but the partition count is 1, processing it anyway ch={ch}")
                    self._decode_24(ch.data[16], ch.data[8:14], ch.data[14:16], ch.data[17:29])
                    self.B0_LastPanelStateTime = get_utc_time()
                self._check_unknown("[handle_msgtypeB0]              B0 Message PANEL_STATE_1 and the first 8 bytes", "handle_msgtypeB0_PANEL_STATE_1_29", toString(ch.data[0:8]))

            case (B0SubType.PANEL_STATE_3, RAW.FIVE_BYTE,  IndexName.MIXED, 6 ):
                # e.g. 0c 00 00 03 10 14    ==>   stype.name='PANIC_PANEL'  partition=3  zone=1
                stype = EVENT_TYPE(ch.data[0]) if ch.data[0] in EVENT_TYPE else EVENT_TYPE.NOT_DEFINED
                sensor = ch.data[1]
                #zone = sensor + 1
                #partition = ch.data[3]
                partition = self._list_from_raw_bits(ch.data[3], 8)   # returns a list
                #log.debug(f"[handle_msgtypeB0]          Received message, Panel Stuff but not sure what, looks like status info  chunk = {ch}")
                log.debug(f"[handle_msgtypeB0]             {stype.name=}   {partition=} (not used)     {sensor=}     {self.get_partitions_in_use()=}")
                if ch.data[2] != 0:
                    log.debug(f"[handle_msgtypeB0]                ******************************************************* Data 2 is not zero   {ch.data[2]}")
                self._check_unknown("[handle_msgtypeB0]              B0 Message PANEL_STATE_3 data[2] is different to last time", "handle_msgtypeB0_PANEL_STATE_3", ch.data[2])

                # When Data[0] is 0x0C i.e. PANIC_PANEL and sensor is 0, then when Data[4] is
                panicmap = {
                    0x06 : EVENT_TYPE.PANIC_PANEL,
                    0x0F : EVENT_TYPE.FIRE,
                    0x10 : EVENT_TYPE.EMERGENCY
                }

                if stype == EVENT_TYPE.PANIC_PANEL: # and sensor == 0:
                    ptu = self.get_partitions_in_use() # returns a set()
                    #if ptu is not None:
                    #    for p in partition:    # already in 0 to X range
                    #        if p+1 in list(ptu) and ch.data[4] in panicmap:
                    #            self.PartitionState[p].UpdatePanelState(panicmap[ch.data[4]], None)
                    if ptu is not None:
                        if ch.data[4] in panicmap:
                            for p in ptu:
                                self.PartitionState[p-1].UpdatePanelState(panicmap[ch.data[4]])
                    elif ch.data[4] in panicmap:
                        self.PartitionState[0].UpdatePanelState(panicmap[ch.data[4]])

                # e.g. 03 36 00 03 05 a4
                # 2025-08-04 12:36:54.889 DEBUG (MainThread) [custom_components.visonic.pyvisonic] [handle_msgtypeB0]             stype.name='ALARM_DELAY'   partition=[0, 1]  sensor=54     self.get_partitions_in_use()={1, 2}

            case (B0SubType.PANEL_STATE_4, RAW.SIX_BYTE,  IndexName.MIXED, 1 ):
                b = -1
                if ch.datasize > 8 and ch.datasize % 8 == 0:  # if it's exactly divisible by 8 then
                    ds = ch.datasize // 8
                    if ch.length % ds == 0:  # If it's exactly divisible
                        b = ch.length // ds
                        for i in range(b):
                            log.debug(f"[handle_msgtypeB0]                     Got Unprocessed {st:<20}   MIXED     Block {i:<3}   {toString(ch.data[i*ds:(i+1)*ds])}")
                if b < 0:
                    log.debug(f"[handle_msgtypeB0]                     Got Unprocessed {st:<20}  MIXED   data = {toString(ch.data)}")
                self._check_unknown("[handle_msgtypeB0]              B0 Message PANEL_STATE_4 is different to last time", "handle_msgtypeB0_PANEL_STATE_4", toString(ch.data))

            case (B0SubType.SYSTEM_CAP, RAW.WORD, IndexName.MIXED, _ ):
                # System capabilities
                ds = 2 # each entry is 2 words
                b = ch.length // ds
                # Set / Update panel capabilities
                self.PanelCapabilities : dict[IndexName,int]= {}
                for i in range(b):
                    d = ch.data[(i*ds)+1] * 256 + ch.data[i*ds]
                    t = IndexName(i).name if i in IndexName else f'Type {i}'
                    log.debug(f"[handle_msgtypeB0]              Got {st.name:<20}   {t:<16}   {toString(ch.data[i*ds:(i+1)*ds])}    decimal {d:>4}")
                    if i in IndexName:
                        if IndexName(i) == IndexName.ZONES:
                            self.PanelCapabilities[IndexName.ZONES] = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
                            if self.PanelCapabilities[IndexName.ZONES] != d:
                                log.debug(f"[handle_msgtypeB0]                  Not updating ZONES as it may be wrong, reverting back to default value for panel {self.PanelCapabilities[IndexName.ZONES]}")
                        else:
                            self.PanelCapabilities[IndexName(i)] = d
                            # make sure any mandatory capabilities are recorded as complete
                            if IndexName(i) == IndexName.PGM:
                                self.PanelSettings[PanelSetting.HasPGM] = [ d >= 1 ]
                log.debug(f"[handle_msgtypeB0]             Panel Capabilities = {self.PanelCapabilities}")

            case (B0SubType.TRIGGERED_ZONE, RAW.BITS,  IndexName.ZONES, _ ):
                zone_len = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, zone trigger information, zone length = {zone_len}")
                self._do_sensor_update(ch.data[0:4], ZoneFunctions.DO_TRIGGER, "[handle_msgtypeB0]             Zone Trigger 32-01")
                device_triggers = self._list_from_raw_bits(b2i(ch.data[0:4]), 32)   # Sensor numbers for Zones 1 to 32 (sensors 0 to 31)
                if zone_len >= 33:
                    self._do_sensor_update(ch.data[4:8], ZoneFunctions.DO_TRIGGER, f"[handle_msgtypeB0]             Zone Trigger {zone_len}-33", 32, zone_len)
                    tmp = self._list_from_raw_bits(b2i(ch.data[4:8]), 32)
                    device_triggers.extend([32 + i for i in tmp])                # Add 32 on to all sensor numbers i.e. sensors 32 to 63, zones 33 to 64

                if len(device_triggers) > 0:
                    # siren triggered?
                    log.debug(f"[handle_msgtypeB0]      ********************** B0 siren Triggered ************************ {device_triggers=}")
                    ptu = self.get_partitions_in_use()
                    for dev in device_triggers:
                        # go through list of sensors that triggered
                        if dev in self.SensorList:
                            ev = EVENT_TYPE.NONE
                            if (zt := self.SensorList[dev].zone_type) in pmMapZoneType:
                                ev = pmMapZoneType[zt]
                            if ptu is not None:
                                # partitions in use
                                    # Get the partitions that this sensor belongs to
                                    #     Remember that dev is the sensor, the zone is sensor+1
                                part = self.SensorList[dev].partition
                                for p in part:
                                    log.debug(f"[handle_msgtypeB0]             Zone {dev+1}  partition,{p=}    {ev.name=}")
                                    self.PartitionState[p-1].UpdatePanelState(ev, self.SensorList[dev])
                            else:
                                # partitions not in use
                                log.debug(f"[handle_msgtypeB0]             Zone {dev+1}   {ev.name=}")
                                self.PartitionState[0].UpdatePanelState(ev, self.SensorList[dev])
                        else:
                            log.debug(f"[handle_msgtypeB0]               Device {dev}   Zone {dev+1}   not in the current sensor list")

            case (B0SubType.ZONE_OPENCLOSE, RAW.BITS,  IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                zone_len = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, open/close information, zone length = {zone_len}")
                self._do_sensor_update(ch.data[0:4], ZoneFunctions.DO_STATUS, "[handle_msgtypeB0]             Zone Status 32-01")
                if zone_len >= 33:
                    self._do_sensor_update(ch.data[4:8], ZoneFunctions.DO_STATUS, f"[handle_msgtypeB0]             Zone Status {zone_len}-33", 32, zone_len)

            case (B0SubType.ZONE_BYPASS,    RAW.BITS,  IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                zone_len = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, bypass information, zone length = {zone_len}")
                self._do_sensor_update(ch.data[0:4], ZoneFunctions.DO_BYPASS, "[handle_msgtypeB0]             Zone Bypass 32-01")
                if zone_len >= 33:
                    self._do_sensor_update(ch.data[4:8], ZoneFunctions.DO_BYPASS, f"[handle_msgtypeB0]             Zone Bypass {zone_len}-33", 32, zone_len)

            case (B0SubType.TAMPER_ALERT,   RAW.BITS,  IndexName.ZONES,  _ ):
                # I'm 50% sure this is correct
                zone_len = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, tamper alert, zone length = {zone_len}   --> Not yet processed as not 100% sure")
                #self._do_sensor_update(ch.data[0:4], ZoneFunctions.DO_TAMPER, "[handle_msgtypeB0]             Zone Tamper 32-01")
                #if zone_len >= 33:
                #    self._do_sensor_update(ch.data[4:8], ZoneFunctions.DO_TAMPER, f"[handle_msgtypeB0]             Zone Tamper {zone_len}-33", 32, zone_len)

            case (B0SubType.TAMPER_ACTIVITY,   RAW.BITS,  IndexName.ZONES,  _ ):
                # I'm 50% sure this is correct
                zone_len = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, tamper activity, zone length = {zone_len}   --> Not yet processed as not 100% sure")
                #self._do_sensor_update(ch.data[0:4], ZoneFunctions.DO_TAMPER, "[handle_msgtypeB0]             Zone Tamper 32-01")
                #if zone_len >= 33:
                #    self._do_sensor_update(ch.data[4:8], ZoneFunctions.DO_TAMPER, f"[handle_msgtypeB0]             Zone Tamper {zone_len}-33", 32, zone_len)

            case (B0SubType.ASSIGNED_PARTITION, RAW.BYTE, _    ,  _ ):   # paged
                if ch.index in IndexName:
                    log.debug(f"[handle_msgtypeB0]          Got Assigned Partition, {IndexName(ch.index).name:<14}  chunk = {ch}")
                else:
                    log.debug(f"[handle_msgtypeB0]          Got Assigned Partition, {notknown} Index unknown    chunk = {ch}")

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                self._update_all_sensors()

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.SIRENS, _ ):
                count = pmPanelConfig[CFG.SIRENS][self.PanelType]
                self.PanelSettings[PanelSetting.SirenEnrolled] = [(ch.data[0] >> i) & 0x01 == 1 for i in range(min(ch.length * 8, count))]
                self.PanelStatus[PANEL_STATUS.SIRENS] = self._string_from_raw_bits(ch.data[0], min(ch.length * 8, count), "[_process_chunk] Found an Enrolled PowerMaster siren")
                self._update_all_sirens()

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.REPEATERS, _ ):
                count = pmPanelConfig[CFG.REPEATERS][self.PanelType]
                self.PanelStatus[PANEL_STATUS.PANIC_BUTTONS] = self._string_from_raw_bits(ch.data[0], min(ch.length * 8, count), "[_process_chunk] Found an Enrolled PowerMaster repeater")

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.PANIC_BUTTONS, _ ):
                #count = pmPanelConfig[CFG.PANIC_BUTTONS][self.PanelType]
                self.PanelStatus[PANEL_STATUS.PANIC_BUTTONS] = self._string_from_raw_bits(b2i(ch.data), ch.length * 8, "[_process_chunk] Found an Enrolled PowerMaster panic-button")

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.KEYPADS_TWO_WAY, _ ):
                count = pmPanelConfig[CFG.TWO_WKEYPADS][self.PanelType]
                enrolled_keypads = b2i(ch.data)
                count = min(ch.length * 8, count)
                self.PanelStatus[PANEL_STATUS.KEYPADS] = self._string_from_raw_bits(enrolled_keypads, count, "[_process_chunk] Found an Enrolled PowerMaster keypad")
                for i in range(count):
                    f = enrolled_keypads & (1 << i)
                    device_id = AlGenericDeviceHelper.make_key(GenericDeviceType.KEYPAD2, i)
                    if f != 0 and device_id not in self.DeviceList:
                        log.info("[Process Settings]        keypad 2 way needs enrolling")
                        if self.onNewDeviceHandler is not None:
                            self.DeviceList[device_id] = AlGenericDeviceHelper(GenericDeviceType.KEYPAD2, id = i, enabled = True)
                            self.onNewDeviceHandler(True, self.DeviceList[device_id])

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.KEYFOBS, _ ):
                count = pmPanelConfig[CFG.KEYFOBS][self.PanelType]
                enrolled_keyfobs = b2i(ch.data)
                count = min(ch.length * 8, count)
                self.PanelStatus[PANEL_STATUS.KEYFOBS] = self._string_from_raw_bits(enrolled_keyfobs, count, "[_process_chunk] Found an Enrolled PowerMaster keyfob")
                # CREATE KEYFOB
                for i in range(count):
                    f = enrolled_keyfobs & (1 << i)
                    device_id = AlGenericDeviceHelper.make_key(GenericDeviceType.KEYFOB, i)
                    if f != 0 and device_id not in self.DeviceList:
                        log.info("[Process Settings]        keyfob needs enrolling")
                        if self.onNewDeviceHandler is not None:
                            self.DeviceList[device_id] = AlGenericDeviceHelper(GenericDeviceType.KEYFOB, id = i, enabled = True)
                            self.onNewDeviceHandler(True, self.DeviceList[device_id])

            case (B0SubType.SENSOR_ENROL,   RAW.BITS,  IndexName.PROXTAGS, _ ):
                count = pmPanelConfig[CFG.PROXTAGS][self.PanelType]
                self.PanelStatus[PANEL_STATUS.PROXTAGS] = self._string_from_raw_bits(b2i(ch.data), min(ch.length * 8, count), "[_process_chunk] Found an Enrolled PowerMaster proxtag")

            case (B0SubType.DEVICE_TYPES,   RAW.BYTE, IndexName.SIRENS,  _ ):
                # I'm 1% sure this is correct ie. it might be wrong
                self._update_all_sirens()

            case (B0SubType.DEVICE_TYPES,   RAW.BYTE, IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                self._update_all_sensors()

            case (B0SubType.ZONE_NAMES,     RAW.BYTE, IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                self._update_all_sensors()

            case (B0SubType.ZONE_TYPES,     RAW.BYTE, IndexName.ZONES,  _ ):
                # I'm 100% sure this is correct
                self._update_all_sensors()

            case (B0SubType.ZONE_TEMPERATURE, RAW.BYTE, IndexName.ZONES,  _ ):
                #log.debug(f"[handle_msgtypeB0]          Got Zone Temperatures Chunk {ch}")
                #zone_count = self._get_panel_capability(IndexName.ZONES)
                zone_count = self._get_panel_capability(IndexName.ZONES)
                if ch.length >= zone_count:
                    for i in range(zone_count):
                        if i in self.SensorList and ch.data[i] != 255:
                            temp = (ch.data[i] / 2) - 40.5
                            log.debug(f"[handle_msgtypeB0]            Zone {i+1} has temperature raw value {ch.data[i]}     temp={temp}")
                            self.SensorList[i].temperature = temp

            case (B0SubType.ZONE_LUX  ,     RAW.BYTE, IndexName.ZONES,  _ ):
                #log.debug(f"[handle_msgtypeB0]          Got Zone Luminance Chunk {ch}")
                #zone_count = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
                zone_count = self._get_panel_capability(IndexName.ZONES)
                if ch.length >= zone_count:
                    for i in range(zone_count):
                        if i in self.SensorList and ch.data[i] != 255:
                            log.debug(f"[handle_msgtypeB0]               Zone {i+1} has luminance value {ch.data[i]} --> not sure what the value means")
                            self.SensorList[i].lux = ch.data[i]

            case (B0SubType.ASK_ME_1,       RAW.BYTE, IndexName.MIXED,  _ ):
                log.debug(f"[handle_msgtypeB0]          Received ASK_ME_1 pop message   {ch}")
                if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD_PLUS, AlPanelMode.STANDARD]:
                    if ch.length > 0:
                        s = self._create_B0_Data_Request(taglist = set(ch.data))
                        self.add_message_to_send_queue(s, priority = MessagePriority.URGENT)
                    else:
                        log.debug(f"[handle_msgtypeB0]                   Empty ASK_ME_1 chunk={ch.GetItAll()}")

            case (B0SubType.ASK_ME_2,       RAW.BYTE, IndexName.MIXED,  _ ):
                log.debug(f"[handle_msgtypeB0]          Received ASK_ME_2 pop message   {ch}")
                if self.PanelMode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD_PLUS, AlPanelMode.STANDARD]:
                    if ch.length > 0:
                        s = self._create_B0_Data_Request(taglist = set(ch.data))
                        self.add_message_to_send_queue(s, priority = MessagePriority.URGENT)
                    else:
                        #log.debug(f"[handle_msgtypeB0]                   Empty ASK_ME_2 chunk={ch.GetItAll()}   so asking for PANEL_STATE_1 and ZONE_LAST_EVENT")
                        #s = self._create_B0_Data_Request(taglist = {pmSendMsgB0[B0SubType.PANEL_STATE_1].data, pmSendMsgB0[B0SubType.ZONE_LAST_EVENT].data} )
                        log.debug(f"[handle_msgtypeB0]                   Empty ASK_ME_2 chunk={ch.GetItAll()}   so asking for PANEL_STATE_1")
                        s = self._create_B0_Data_Request(taglist = {pmSendMsgB0[B0SubType.PANEL_STATE_1].data} )
                        self.add_message_to_send_queue(s, priority = MessagePriority.URGENT)

            case (B0SubType.ZONE_LAST_EVENT, RAW.FIVE_BYTE, IndexName.ZONES,  _ ):  # Each entry is ch.datasize=40 bits (or 5 bytes)
                if seq_type == SEQUENCE.SUB:
                    # Zone Last Event
                    # PM10: I assume this does not get sent by the panel.
                    # PM30: This represents sensors Z01 to Z36.  Each sensor is 5 bytes.
                    #       For the PM30 with 64 sensors this comes out as 180 / 5 = 36
                    #log.debug(f"[handle_msgtypeB0] ZONE_LAST_EVENT sub   self.beezero_024B_sensorcount = {self.beezero_024B_sensorcount}")
                    if self.beezero_024B_sensorcount is None and ch.length % 5 == 0:             # Divisible by 5, each sensors data is 5 bytes
                        self.beezero_024B_sensorcount = int(ch.length / 5)
                        for i in range(self.beezero_024B_sensorcount):
                            o = i * 5
                            self._decode_4B(sensor_identifier = i, data = ch.data[o:o+5])
                elif seq_type == SEQUENCE.MAIN:
                    # Zone Last Event
                    # PM10: This represents sensors Z01 to Z30.
                    #       For the PM10 with 30 sensors this comes out as 150 / 5 = 30
                    # PM30: This represents sensors Z37 to Z64.  Each sensor is 5 bytes
                    #       For the PM30 with 64 sensors this comes out as 140 / 5 = 28     (64-36=28)
                    #log.debug(f"[handle_msgtypeB0] ZONE_LAST_EVENT main   self.beezero_024B_sensorcount = {self.beezero_024B_sensorcount}")
                    if ch.length % 5 == 0:         # Divisible by 5, each sensors data is 5 bytes
                        if self.beezero_024B_sensorcount is not None:
                            sensorcount = int(ch.length / 5)
                            for i in range(sensorcount):
                                o = i * 5
                                self._decode_4B(sensor_identifier = i + self.beezero_024B_sensorcount, data = ch.data[o:o+5])
                        else: # Assume PM10
                            # Assume that when the PowerMaster panel has less than 32 sensors then it just sends this and not msgType == 0x02, subType == pmSendMsgB0[B0SubType.ZONE_LAST_EVENT]
                            sensorcount = int(ch.length / 5)
                            for i in range(sensorcount):
                                o = i * 5
                                self._decode_4B(sensor_identifier = i, data = ch.data[o:o+5])
                    self.beezero_024B_sensorcount = None   # If theres a next time so they are coordinated

            case (B0SubType.LEGACY_EVENT_LOG, RAW.TEN_BYTE, IndexName.MIXED,  _ ):
                log.debug(f"[handle_msgtypeB0]       Got Legacy Event Log Chunk {ch}")
                self._process_B0_log_entry(1, 1, ch.data)

            case (B0SubType.EVENT_LOG,        RAW.TEN_BYTE, IndexName.MIXED,  _ ):
                if seq_type == SEQUENCE.SUB:
                    log.debug(f"[handle_msgtypeB0]          Got Sub Event Log Chunk {ch}")
                    event_total = pmPanelConfig[CFG.EVENTS][self.PanelType]
                    # Got Event Log Chunk sequence 6  datasize 80  index 255   length 170    data 92 73 00 67 0c 00 00 1c 00 63 92 73 00 67 06 00 00 1b 01 62 92 73 00 67 06 00 00 55 01 61 83 73 00 67 03 00 00 01 01 6a 7c 73 00 67 06 00 00 52 01 60 64 72 00 67 0c 00 00 1c 00 5f 64 72 00 67 06 00 00 1b 01 5e 56 72 00 67 0c 00 00 20 00 5d 2d 70 00 67 0c 00 00 1c 00 5c 26 70 00 67 0c 00 00 23 00 5b 0f 6e 00 67 0c 00 00 1c 00 5a 0f 6e 00 67 06 00 00 1b 01 59 fd 6d 00 67 0c 00 00 0c 00 58 a6 69 00 67 0c 00 00 1c 00 57 a6 69 00 67 06 00 00 1b 01 56 8e 69 00 67 0c 00 00 0c 00 55 24 69 00 67 0c 00 00 1c 00 54
                    datalength = 10 # We know this as we check datasize to be 80 above       ch.datasize // 8 # 8 bits in a byte
                    entries = ch.length // datalength
                    offset = (ch.sequence-1) * entries       # This assumes that all previous messages in the sequence had the same number of entries
                    if ch.length % datalength == 0:  # is the length divisible by datalength exactly
                        for i in range(0, ch.length, datalength):
                            logentry = offset + (i // datalength)
                            self.B0_PANEL_LOG_Counter = max(self.B0_PANEL_LOG_Counter, logentry)
                            log.debug(f"[handle_msgtypeB0]            Processing log entry {logentry}     data = {toString(ch.data[i:i+datalength])}")
                            self._process_B0_log_entry(event_total, logentry + 1, ch.data[i:i+datalength])
                elif seq_type == SEQUENCE.MAIN:
                    log.debug(f"[handle_msgtypeB0]          Got Main Event Log Chunk {ch}")
                    event_total = pmPanelConfig[CFG.EVENTS][self.PanelType]
                    datalength = 10 # We know this as we check datasize to be 80 above       ch.datasize // 8 # 8 bits in a byte
                    offset = self.B0_PANEL_LOG_Counter + 1  # self.B0_PANEL_LOG_Counter is the maximum value from the 0x02 sequence so start from here + 1
                    if ch.length % datalength == 0:  # is the length divisible by datalength exactly
                        for i in range(0, ch.length, datalength):
                            logentry = offset + (i // datalength)
                            log.debug(f"[handle_msgtypeB0]               Processing log entry {logentry}     data = {toString(ch.data[i:i+datalength])}")
                            self._process_B0_log_entry(event_total, logentry + 1, ch.data[i:i+datalength])

            case (B0SubType.WIRELESS_DEV_MISSING,    RAW.BITS, IndexName.ZONES,  _ ):
                # I'm 80% sure of this but all it does is set some attributes of the sensor
                log.debug(f"[handle_msgtypeB0]          Received message, 03 02 information (WIRELESS_DEV_MISSING), zone length = {ch.length}")
                zone_len = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, zone missing or wireless issues, zone length = {zone_len}")
                self._do_sensor_update(ch.data[0:4], ZoneFunctions.DO_MISSING, "[handle_msgtypeB0]             Zone Missing 32-01")
                if zone_len >= 33:
                    self._do_sensor_update(ch.data[4:8], ZoneFunctions.DO_MISSING, f"[handle_msgtypeB0]             Zone Missing {zone_len}-33", 32, zone_len)

            case (B0SubType.WIRELESS_DEV_INACTIVE,   RAW.BITS, IndexName.ZONES,  _ ):
                # I'm 80% sure of this but all it does is set some attributes of the sensor
                log.debug(f"[handle_msgtypeB0]          Received message, 03 09 information (WIRELESS_DEV_INACTIVE), zone length = {ch.length}")
                zone_len = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, zone inactive or wireless issues, zone length = {zone_len}")
                self._do_sensor_update(ch.data[0:4], ZoneFunctions.DO_INACTIVE, "[handle_msgtypeB0]             Zone Inactive 32-01")
                if zone_len >= 33:
                    self._do_sensor_update(ch.data[4:8], ZoneFunctions.DO_INACTIVE, f"[handle_msgtypeB0]             Zone Inactive {zone_len}-33", 32, zone_len)

            case (B0SubType.WIRELESS_DEV_ONEWAY,   RAW.BITS, IndexName.ZONES,  _ ):
                # I'm 80% sure of this but all it does is set some attributes of the sensor
                log.debug(f"[handle_msgtypeB0]          Received message, 03 0E information (WIRELESS_DEV_ONEWAY), zone length = {ch.length}")
                zone_len = ch.length * 8     # 8 bits in a byte
                log.debug(f"[handle_msgtypeB0]          Received message, zone one way or wireless issues, zone length = {zone_len}")
                self._do_sensor_update(ch.data[0:4], ZoneFunctions.DO_ONEWAY, "[handle_msgtypeB0]             Zone One Way 32-01")
                if zone_len >= 33:
                    self._do_sensor_update(ch.data[4:8], ZoneFunctions.DO_ONEWAY, f"[handle_msgtypeB0]             Zone One Way {zone_len}-33", 32, zone_len)

            case (B0SubType.WIRELESS_DEV_CHANNEL,    RAW.BYTE, IndexName.ZONES,  _ ):
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
                    for z in range(ch.length):
                        if z in self.SensorList:
                            s = int(ch.data[z])
                            log.debug(f"[handle_msgtypeB0]             Zone {z}  State(hex) {hex(s)}")

            case (B0SubType.ZONE_STAT07,    RAW.BYTE, IndexName.ZONES,  _ ):
                #  Received PowerMaster10 message 3/7 (len = 35)    data = 03 07 23 ff 08 03 1e 03 00 00 03 00 00 <24 * 00> 0d 43
                #  Received PowerMaster30 message 3/7 (len = 69)    data = 03 07 45 ff 08 03 40 03 03 03 03 03 03 <58 * 00> 92 43
                #  My PM30:  data = 03 07 45 ff 08 03 40 00 00 00 00 00 03 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 03 00 00 03 00 1d 43
                # Unknown information
                log.debug(f"[handle_msgtypeB0]          Received message, 03 07 information, zone length = {ch.length}")
                if beezerodebug7:
                    for z in range(ch.length):
                        #if z in self.SensorList:
                        if ch.data[z] != 0:
                            s = int(ch.data[z])
                            log.debug(f"[handle_msgtypeB0]             Zone {z}  State {s}")

            case _:
                if beezerodebug:
                    #log.debug(f"@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@")
                    log.debug(f"[handle_msgtypeB0]        Received message chunk for  {st}, dont know what this is, chunk = {ch!s}")
                    if ch.index == IndexName.MIXED: # Some kind of panel settings
                        b = -1
                        if ch.datasize > 8 and ch.datasize % 8 == 0:  # if it's exactly divisible by 8 then
                            ds = ch.datasize // 8
                            if ch.length % ds == 0:  # If it's exactly divisible
                                b = ch.length // ds
                                for i in range(b):
                                    log.debug(f"[handle_msgtypeB0]                     Got Unprocessed {st:<20}   MIXED     Block {i:<3}   {toString(ch.data[i*ds:(i+1)*ds])}")
                        if b < 0:
                            log.debug(f"[handle_msgtypeB0]                     Got Unprocessed {st:<20}  MIXED   data = {toString(ch.data)}")
                    else:
                        t = IndexName(ch.index).name if ch.index in IndexName else f"Unknown Index {ch.index}"
                        log.debug(f"[handle_msgtypeB0]                     Got Unprocessed {st:<20} {t:<18}  data = {toString(ch.data)}")
                    #log.debug(f"@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@")
